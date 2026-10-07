"""W1 memory tiering: compose-level acceptance for the files tier.

The files tier (``memory.mode=files``) composes only local file memory —
FileMemoryStore, FrozenMemoryContextService and the dependency-free evidence
ledger — without MindMemOS/Neo4j/Qdrant/spaCy.  These tests assert external
terminal state (real files on disk, real tool execution, real sys.modules in a
subprocess), not just composition internals.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.application import RunRequest, RunStatus
from homemaster.application.composition import compose_application
from homemaster.config import HomeMasterConfig
from homemaster.providers.types import ToolCall, TransportDelta
from homemaster.tools.contracts import PermissionSubject
from tests.homemaster.as_testkit import as_provider
from tests.homemaster.tools.test_support import ToolExecutionContext

MINDMEMOS_TOOL_NAMES = {
    "mindmemos_add",
    "mindmemos_search",
    "mindmemos_history",
    "mindmemos_update",
    "mindmemos_delete",
    "mindmemos_feedback",
}
HEAVY_MODULES = ("mindmemos", "qdrant_client", "neo4j", "spacy")


def _files_config(tmp_path: Path, *, with_provider: bool = False) -> HomeMasterConfig:
    return HomeMasterConfig(
        providers=(
            {
                "default": "Mimo",
                "items": [
                    {
                        "name": "Mimo",
                        "kind": "chat",
                        "api_format": "anthropic",
                        "base_url": "https://chat.invalid/anthropic",
                        "model": "mimo-v2.5",
                        "api_keys": ["test-key"],
                    }
                ],
            }
            if with_provider
            else {}
        ),
        memory={"mode": "files", "data_root": tmp_path / "memory-data"},
        runtime={"runtime_root": tmp_path / "runs"},
        observability={
            "session_dir": str(tmp_path / "sessions"),
            "trace_dir": str(tmp_path / "traces"),
        },
    )


def test_files_tier_composes_only_file_memory_services(tmp_path: Path) -> None:
    bundle = compose_application(
        config=_files_config(tmp_path),
        run_label="files-tier-surface",
        tool_environment="local_robot",
    )
    try:
        # Full-tier objects are absent from the bundle.
        assert bundle.mindmemos is None
        assert bundle.memory_add_queue is None
        assert bundle.memory_enrichment_queue is None
        assert bundle.trajectory_writer is None
        assert bundle.alfworld_compile_jobs is None
        assert bundle.dreaming_coordinator is None
        assert bundle.session_finalization is None

        services = bundle.application.settings.application_services
        assert set(services) >= {
            "file_memory_store",
            "frozen_memory_context",
            "memory_evidence_ledger",
            "memory_audit_path",
        }
        for name in (
            "mindmemos",
            "memory_add_queue",
            "trajectory_writer",
            "alfworld_compile_jobs",
            "memory_enrichment_queue",
            "dreaming_coordinator",
            "memory_migration",
            "managed_neo4j",
        ):
            assert name not in services

        names = set(bundle.application.registry.all_names())
        assert "context_memory" in names
        assert not (names & MINDMEMOS_TOOL_NAMES)
    finally:
        asyncio.run(bundle.application.aclose())


@pytest.mark.asyncio
async def test_files_tier_file_memory_starts_and_writes_real_files(
    tmp_path: Path,
) -> None:
    bundle = compose_application(
        config=_files_config(tmp_path),
        run_label="files-tier-rw",
        tool_environment="local_robot",
    )
    try:
        await bundle.application.start()

        files_root = tmp_path / "memory-data" / "files"
        # External terminal state: the store really materialized its files.
        assert (files_root / "SOUL.md").is_file()
        assert (files_root / "USER.md").is_file()
        assert (files_root / "MEMORY.md").is_file()

        services = bundle.application.settings.application_services
        tool = bundle.application.registry.get("context_memory")
        assert tool is not None
        context = ToolExecutionContext(
            tmp_path,
            metadata={
                "services": services,
                "permission_subject": PermissionSubject(
                    subject_id="test",
                    channel="pytest",
                    capabilities=("tool.read", "tool.mutate"),
                ),
                "session_id": "files-tier-session",
                "run_id": "files-tier-run",
            },
        )
        result = await tool.execute(
            tool.input_model.model_validate(
                {"target": "user", "action": "add", "content": "偏好简洁回答"}
            ),
            context,
        )
        assert result.status.value == "success"
        # Blackbox readback: the content really landed in USER.md.
        user_md = (files_root / "USER.md").read_text(encoding="utf-8")
        assert "偏好简洁回答" in user_md
        # The evidence ledger is real SQLite under the configured data root.
        assert (tmp_path / "memory-data" / "evidence.sqlite3").is_file()
    finally:
        await bundle.application.aclose()


class _ScriptedTransport:
    """Minimal provider seam: streams one scripted delta list per call."""

    def __init__(self, responses: list[list[TransportDelta]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def stream(self, messages, *, tools=None, **kwargs):
        del kwargs
        self.calls.append({"messages": messages, "tools": tools})
        for delta in self._responses.pop(0):
            yield delta

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_files_tier_fake_provider_run_uses_only_file_memory(
    tmp_path: Path,
) -> None:
    """A full run() against a scripted provider must drive context_memory and
    produce real file writes, with no MindMemOS tools in the provider schema."""

    bundle = compose_application(
        config=_files_config(tmp_path, with_provider=True),
        run_label="files-tier-run",
        tool_environment="local_robot",
        session_root=tmp_path / "sessions",
    )
    transport = _ScriptedTransport(
        [
            [
                TransportDelta(
                    type="tool_call",
                    tool_call_delta=ToolCall(
                        id="call-1",
                        name="context_memory",
                        arguments={
                            "target": "user",
                            "action": "add",
                            "content": "用户偏好简短回答",
                        },
                    ),
                    finish_reason="tool_calls",
                )
            ],
            [TransportDelta(type="text", text_delta="已记住", finish_reason="stop")],
        ]
    )
    bundle.application.provider_factory = lambda request, run_id: as_provider(transport)
    try:
        result = await bundle.application.run(
            RunRequest(text="记住偏好", session_id="files-run")
        )
        assert result.status is RunStatus.REPLIED

        # External terminal state: the provider-visible tool set contains only
        # the files-tier tool, and USER.md really holds the written entry.
        offered = {
            str(
                schema.get("function", {}).get("name")
                if isinstance(schema, dict) and "function" in schema
                else schema.get("name", "")
            )
            for schema in (transport.calls[0]["tools"] or [])
        }
        assert "context_memory" in offered
        assert not (offered & MINDMEMOS_TOOL_NAMES)
        user_md = (tmp_path / "memory-data" / "files" / "USER.md").read_text(
            encoding="utf-8"
        )
        assert "用户偏好简短回答" in user_md
    finally:
        await bundle.application.aclose()


def test_files_tier_compose_never_imports_heavy_modules(tmp_path: Path) -> None:
    """In a wheel install without the ``memory`` extra, files-tier composition
    must succeed and leave the heavy modules out of ``sys.modules``."""

    src = Path(__file__).resolve().parents[3] / "src"
    script = """
import importlib.util
import json
import sys

sys.path.insert(0, sys.argv[1])
import asyncio
from pathlib import Path
from homemaster.application.composition import compose_application
from homemaster.config import HomeMasterConfig

tmp = Path(sys.argv[2])
config = HomeMasterConfig(
    memory={"mode": "files", "data_root": tmp / "memory-data"},
    runtime={"runtime_root": tmp / "runs"},
    observability={"session_dir": str(tmp / "sessions"), "trace_dir": str(tmp / "traces")},
)
bundle = compose_application(
    config=config, run_label="files-tier-probe", tool_environment="local_robot"
)
asyncio.run(bundle.application.start())
asyncio.run(bundle.application.aclose())

heavy = ["mindmemos", "qdrant_client", "neo4j", "spacy"]
payload = {
    "imported_heavy": [name for name in heavy if name in sys.modules],
    "mindmemos_importable": importlib.util.find_spec("mindmemos") is not None,
    "files": sorted(p.name for p in (tmp / "memory-data" / "files").iterdir()),
    "names_has_context_memory": "context_memory" in set(bundle.application.registry.all_names()),
}
print(json.dumps(payload))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src)
    proc = subprocess.run(
        [sys.executable, "-c", script, str(src), str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["imported_heavy"] == []
    assert payload["names_has_context_memory"] is True
    assert {"SOUL.md", "USER.md", "MEMORY.md"} <= set(payload["files"])


def test_memory_off_composes_no_memory_surface_on_files_tier(tmp_path: Path) -> None:
    """``memory_off`` is the real kill-switch: a files-tier config must compose
    zero memory services and zero memory tools when the benchmark disables
    memory — clearing ``memory.enabled`` alone cannot do that."""

    bundle = compose_application(
        config=_files_config(tmp_path),
        run_label="memory-off-files",
        tool_environment="local_robot",
        memory_off=True,
    )
    try:
        names = set(bundle.application.registry.all_names())
        assert "context_memory" not in names
        assert not (names & MINDMEMOS_TOOL_NAMES)

        services = bundle.application.settings.application_services
        for name in (
            "file_memory_store",
            "frozen_memory_context",
            "memory_evidence_ledger",
            "memory_audit_path",
            "mindmemos",
            "memory_add_queue",
            "trajectory_writer",
        ):
            assert name not in services
        assert bundle.mindmemos is None
        assert bundle.memory_add_queue is None
    finally:
        asyncio.run(bundle.application.aclose())


def test_memory_off_composes_no_memory_surface_on_full_tier(tmp_path: Path) -> None:
    """The same kill-switch must also strip every memory surface from a
    full-tier config without mutating the config object."""

    config = HomeMasterConfig(
        memory={"mode": "full", "data_root": tmp_path / "memory-data"},
        runtime={"runtime_root": tmp_path / "runs"},
        observability={
            "session_dir": str(tmp_path / "sessions"),
            "trace_dir": str(tmp_path / "traces"),
        },
    )
    bundle = compose_application(
        config=config,
        run_label="memory-off-full",
        tool_environment="local_robot",
        memory_off=True,
    )
    try:
        names = set(bundle.application.registry.all_names())
        assert "context_memory" not in names
        assert not (names & MINDMEMOS_TOOL_NAMES)
        services = bundle.application.settings.application_services
        assert "frozen_memory_context" not in services
        assert "file_memory_store" not in services
        assert bundle.mindmemos is None
    finally:
        asyncio.run(bundle.application.aclose())
    # The caller's config object is untouched by the kill-switch.
    assert config.memory.mode == "full"
    assert config.memory.enabled is True


def test_benchmark_disabled_mode_forwards_memory_off_without_config_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BenchmarkApplicationLifecycle`` must forward ``memory_off`` for
    ``--memory-mode disabled`` isolated workers instead of mutating the
    config's projected ``enabled`` flag."""

    from homemaster.alfworld.benchmark import episode as episode_module
    from homemaster.application.composition import ApplicationCompositionRequest

    captured: dict[str, ApplicationCompositionRequest] = {}

    def fake_compose(request):
        captured["request"] = request
        return SimpleNamespace(
            application=SimpleNamespace(
                provider_factory=None,
                session=lambda *_args, **_kwargs: None,
            )
        )

    config = _files_config(tmp_path)
    monkeypatch.setattr(episode_module, "compose_application", fake_compose)

    episode_module.BenchmarkApplicationLifecycle(
        config=config,
        memory_mode="disabled",
        runtime_root=tmp_path / "runs" / "ep1",
        session_root=tmp_path / "sessions",
        transport_factory=None,
        event_sink=None,
        disable_memory_for_worker=True,
    )

    request = captured["request"]
    assert isinstance(request, ApplicationCompositionRequest)
    assert request.memory_off is True
    # The config object itself is never mutated for the kill-switch.
    assert config.memory.enabled is False  # projected False for the files tier
    assert config.memory.mode == "files"


def test_benchmark_memory_mode_full_fails_fast_on_files_tier(tmp_path: Path) -> None:
    """``benchmark-alfworld --memory-mode full`` must report a configuration
    error at startup when the resolved config selects the files tier."""

    from homemaster.alfworld.benchmark.runner import AlfworldBenchmarkRunner
    from homemaster.alfworld.types import AlfworldBenchmarkConfig
    from homemaster.config import ConfigError

    provider_config = tmp_path / "homemaster.yaml"
    provider_config.write_text(
        f"memory:\n  mode: files\n  data_root: {tmp_path / 'memory-data'}\n",
        encoding="utf-8",
    )
    config = AlfworldBenchmarkConfig(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
        provider_config=provider_config,
        memory_mode="full",
        episodes=1,
    )
    runner = AlfworldBenchmarkRunner(config=config)

    with pytest.raises(ConfigError, match="memory.mode='files'"):
        runner.run()
    # Startup failure must not leave run artifacts behind.
    assert not (tmp_path / "traces").exists()


def test_benchmark_memory_mode_full_gate_allows_full_and_other_modes(
    tmp_path: Path,
) -> None:
    from homemaster.alfworld.benchmark.runner import _require_full_memory_tier
    from homemaster.alfworld.types import AlfworldBenchmarkConfig

    files_config = tmp_path / "files.yaml"
    files_config.write_text("memory:\n  mode: files\n", encoding="utf-8")
    full_config = tmp_path / "full.yaml"
    full_config.write_text("memory:\n  mode: full\n", encoding="utf-8")

    base = dict(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
        episodes=1,
    )
    # readonly/disabled never touch the full-tier gate.
    for mode in ("readonly", "disabled"):
        _require_full_memory_tier(
            AlfworldBenchmarkConfig(
                **base, provider_config=files_config, memory_mode=mode
            )
        )
    # full memory_mode on a full-tier config passes the gate.
    _require_full_memory_tier(
        AlfworldBenchmarkConfig(
            **base, provider_config=full_config, memory_mode="full"
        )
    )
