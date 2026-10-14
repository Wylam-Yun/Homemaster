"""RoboCasa environment adapter: borrowed-environment view of the worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemaster.robocasa.types import RoboCasaEnvState
from homemaster.robocasa.worker_client import RoboCasaWorkerClient, RoboCasaWorkerError


class RoboCasaEnvAdapter:
    """Present the isolated worker as the borrowed EnvironmentBackend."""

    def __init__(self, *, worker: RoboCasaWorkerClient) -> None:
        self.worker = worker

    # -- EnvironmentBackend surface ----------------------------------------

    @property
    def backend_id(self) -> str:
        return self.worker.backend_id

    @property
    def current_state(self) -> RoboCasaEnvState:
        return self.worker.current_state

    async def screenshot(self) -> bytes:
        """ScreenshotSource: one current agentview frame for generic observe."""
        return await self.worker.screenshot()

    def bind_application_run(self, run_id: str, generation: int) -> None:
        self.worker.bind_application_run(run_id, generation)

    def close(self) -> dict[str, Any]:
        try:
            return self.worker.close()
        except RoboCasaWorkerError:
            self.worker.terminate()
            raise

    def terminate(self) -> None:
        self.worker.terminate()

    # -- cell lifecycle -----------------------------------------------------

    def ensure_cell(
        self,
        *,
        rpent_root: Path,
        task_name: str,
        split: str,
        seed: int,
        cuda_device: str | None,
        hi_res: bool,
        workdir: Path,
    ) -> dict[str, Any]:
        """First cell spawns the runtime; later cells only swap env_server."""
        if not self.worker.runtime_started:
            return self.worker.start_runtime(
                rpent_root=rpent_root,
                task_name=task_name,
                split=split,
                seed=seed,
                cuda_device=cuda_device,
                hi_res=hi_res,
                workdir=workdir,
            )
        return self.worker.reconfigure(
            task_name=task_name, split=split, seed=seed, workdir=workdir
        )

    def start_cell(
        self,
        *,
        rpent_root: Path,
        task_name: str,
        split: str,
        seed: int,
        cuda_device: str | None,
        hi_res: bool,
        workdir: Path,
    ) -> dict[str, Any]:
        return self.worker.start_runtime(
            rpent_root=rpent_root,
            task_name=task_name,
            split=split,
            seed=seed,
            cuda_device=cuda_device,
            hi_res=hi_res,
            workdir=workdir,
        )

    def reconfigure(
        self, *, task_name: str, split: str, seed: int, workdir: Path | None = None
    ) -> dict[str, Any]:
        return self.worker.reconfigure(
            task_name=task_name, split=split, seed=seed, workdir=workdir
        )

    def observe(self, *, cameras: list[str] | None = None) -> dict[str, Any]:
        try:
            return self.worker.observe(cameras=cameras)
        except RoboCasaWorkerError as exc:
            self._kill_on_desync(exc)
            raise

    def invoke(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one mutating or read primitive op through the worker."""
        try:
            return self.worker.primitive(operation, payload or {})
        except RoboCasaWorkerError as exc:
            self._kill_on_desync(exc)
            raise

    def check_success(self) -> dict[str, Any]:
        try:
            return self.worker.check_success()
        except RoboCasaWorkerError as exc:
            self._kill_on_desync(exc)
            raise

    def _kill_on_desync(self, exc: RoboCasaWorkerError) -> None:
        """A transport-level failure leaves the pipe desynchronised — the late
        response will corrupt every later request, so the worker is dead.
        Terminate it now instead of letting the next cell inherit a poisoned
        runtime.  Worker error frames (response set) leave the stream clean.
        """
        if exc.response is None:
            self.worker.terminate()

    def image_artifacts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        return self.worker.image_artifacts(result)


__all__ = ["RoboCasaEnvAdapter"]
