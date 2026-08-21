"""Application composition for the isolated ALFWorld Gateway backend."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from homemaster.application.contracts import (
    ResourceBinding,
    ResourceLifetime,
    RunRequest,
    RunResult,
    RunStatus,
)
from homemaster.application.resources import RunResourceScope
from homemaster.benchmarking.alfworld.http_client import AlfworldHttpEnvironment
from homemaster.benchmarking.alfworld.tracing import (
    AlfworldToolDispatchObserver,
    AlfworldTraceWriter,
)
from homemaster.benchmarking.alfworld.translator import (
    AlfworldCommandTranslator,
    create_translator,
)
from homemaster.benchmarking.alfworld.trial_selection import (
    TrialSelectionEntry,
    load_trial_selection_manifest,
)
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    EpisodeOutcome,
)
from homemaster.config import AlfworldGatewayConfig


@dataclass(frozen=True)
class AlfworldGatewayBinding:
    adapter: AlfworldHttpEnvironment
    translator: AlfworldCommandTranslator
    terminal_owner: object
    dependencies: Mapping[str, object]
    config: AlfworldBenchmarkConfig
    selection: TrialSelectionEntry


class AlfworldSessionOwner:
    """Atomically retain one fixed episode for one Gateway session."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._sealed = False

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def claim(self, session_id: str) -> bool:
        async with self._lock:
            if self._sealed:
                return False
            if self._session_id is None:
                self._session_id = session_id
            return self._session_id == session_id

    async def seal(self) -> None:
        async with self._lock:
            self._sealed = True


class AlfworldGatewayApplication:
    """Bind ALFWorld resources after Gateway has produced a canonical request."""

    def __init__(
        self,
        application: Any,
        owner: AlfworldSessionOwner,
        binding: AlfworldGatewayBinding,
    ) -> None:
        self._application = application
        self._owner = owner
        self._binding = binding

    def __getattr__(self, name: str) -> Any:
        return getattr(self._application, name)

    async def run(self, request: RunRequest) -> RunResult:
        session_id = request.session_id
        if session_id is None:
            raise ValueError("ALFWorld Gateway requires an explicit session_id")
        if not await self._owner.claim(session_id):
            return RunResult(
                run_id=f"alfworld-busy-{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                status=RunStatus.FAILED,
                error_code="alfworld_session_busy",
                final_reply="ALFWorld demo is already owned by another session.",
            )
        dependencies = dict(request.dependencies)
        dependencies.update(self._binding.dependencies)
        bound_request = replace(
            request,
            profile="alfworld",
            environment=self._binding.adapter,
            dependencies=dependencies,
        )
        return await self._application.run(bound_request)

    def cancel(self, session_id: str) -> bool:
        return self._application.cancel(session_id)

    async def seal(self) -> None:
        await self._owner.seal()

    async def aclose(self) -> None:
        """Seal new ALFWorld claims before closing the shared application."""

        await self.seal()
        await self._application.aclose()


class _AlfworldTerminalOwner:
    def __init__(self, adapter: AlfworldHttpEnvironment) -> None:
        self._adapter = adapter

    @property
    def succeeded(self) -> bool:
        return self._adapter.current_state.won


class ManagedXvfb:
    """One explicitly addressed Xvfb process with a real readiness probe."""

    def __init__(self, executable: Path, display: str) -> None:
        self.executable = executable
        self.display = display
        self.process: asyncio.subprocess.Process | None = None
        self._previous_display: str | None = None

    async def start(self) -> None:
        if not self.executable.is_file():
            raise ValueError(f"Xvfb executable does not exist: {self.executable}")
        self._previous_display = os.environ.get("DISPLAY")
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable),
            self.display,
            "-screen",
            "0",
            "1280x960x24",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await _wait_for_display(self.display, process=self.process)
        except BaseException:
            await self.aclose()
            raise
        os.environ["DISPLAY"] = self.display

    async def aclose(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._previous_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._previous_display


async def _wait_for_display(
    display: str,
    *,
    process: asyncio.subprocess.Process | None = None,
) -> None:
    last_detail = ""
    for _attempt in range(30):
        if process is not None and process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr is not None else b""
            raise RuntimeError(
                f"Xvfb exited before display became ready: {stderr.decode(errors='replace')}"
            )
        probe = await asyncio.create_subprocess_exec(
            "xdpyinfo",
            "-display",
            display,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await probe.communicate()
        if probe.returncode == 0:
            return
        last_detail = stderr.decode(errors="replace").strip()
        await asyncio.sleep(0.1)
    raise RuntimeError(f"display {display} is not usable: {last_detail}")


async def create_alfworld_gateway_binding(
    config: AlfworldGatewayConfig,
    *,
    run_dir: Path,
    resource_scope: RunResourceScope,
) -> tuple[AlfworldGatewayBinding, AlfworldSessionOwner]:
    """Start the isolated worker and bind it to application lifetime."""

    (
        asset_root,
        data_root,
        config_path,
        python_executable,
        manifest_path,
    ) = config.require_runtime_paths()
    for label, path in (
        ("asset_root", asset_root),
        ("data_root", data_root),
        ("config_path", config_path),
        ("python_executable", python_executable),
        ("trial_manifest", manifest_path),
    ):
        if not path.exists():
            raise ValueError(f"ALFWorld Gateway {label} does not exist: {path}")

    managed_xvfb: ManagedXvfb | None = None
    if config.manage_xvfb:
        managed_xvfb = ManagedXvfb(config.xvfb_executable, config.display)
        await managed_xvfb.start()
        resource_scope.bind(
            ResourceBinding.owned(
                "alfworld-xvfb",
                managed_xvfb,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
    else:
        await _wait_for_display(config.display)

    trial_root = data_root / "json_2.1.1"
    manifest = load_trial_selection_manifest(manifest_path, trial_root=trial_root)
    if config.trial_index >= len(manifest.entries):
        raise ValueError(
            f"ALFWorld trial_index {config.trial_index} exceeds "
            f"{len(manifest.entries)} manifest entries"
        )
    selection = manifest.entries[config.trial_index]
    benchmark_config = AlfworldBenchmarkConfig(
        alfworld_root=asset_root,
        alfworld_config=config_path,
        trace_root=run_dir,
        data_root=data_root,
        use_installed_alfworld=True,
        env_type=config.env_type,
        split=config.split,
        episodes=1,
        memory_mode="disabled",
        seed=config.seed,
        trial_manifest=manifest_path,
    )
    adapter = await asyncio.to_thread(
        AlfworldHttpEnvironment.start,
        python_executable=python_executable,
        asset_root=asset_root,
        data_root=data_root,
        config_path=config_path,
        trial_manifest=manifest_path,
        trial_index=config.trial_index,
        env_type=config.env_type,
        split=config.split,
        seed=config.seed,
        allow_offscreen_object_navigation=config.allow_offscreen_object_navigation,
        display=config.display,
        frame_dir=run_dir / "alfworld" / "frames",
        log_path=run_dir / "alfworld" / "worker.log",
        startup_timeout_s=config.startup_timeout_s,
        request_timeout_s=config.request_timeout_s,
    )
    resource_scope.bind(
        ResourceBinding.owned(
            "alfworld-http-environment",
            adapter,
            lifetime=ResourceLifetime.APPLICATION,
        )
    )

    translator = create_translator(config.env_type)
    terminal_owner = _AlfworldTerminalOwner(adapter)
    outcome = EpisodeOutcome()
    trace = AlfworldTraceWriter(run_dir / "alfworld" / "episode")
    dependencies: dict[str, object] = {
        "alfworld_translator": translator,
        "alfworld_trace": trace,
        "alfworld_config": benchmark_config,
        "alfworld_episode_outcome": outcome,
        "alfworld_tool_observer": AlfworldToolDispatchObserver(outcome),
        "external_terminal_owner": terminal_owner,
        "alfworld_semantic_judge_config": (asset_root / "configs" / "semantic_judge_agnes.yaml"),
    }
    return (
        AlfworldGatewayBinding(
            adapter=adapter,
            translator=translator,
            terminal_owner=terminal_owner,
            dependencies=dependencies,
            config=benchmark_config,
            selection=selection,
        ),
        AlfworldSessionOwner(),
    )


__all__ = [
    "AlfworldGatewayApplication",
    "AlfworldGatewayBinding",
    "AlfworldSessionOwner",
    "ManagedXvfb",
    "create_alfworld_gateway_binding",
]
