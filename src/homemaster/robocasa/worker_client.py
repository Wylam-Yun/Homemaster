"""Isolated RoboCasa worker process client (NDJSON over stdin/stdout)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import select
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from homemaster.robocasa.types import RoboCasaEnvState
from homemaster.robocasa.worker_protocol import (
    MAX_LINE_BYTES,
    WorkerResponse,
    request_frame,
)


class RoboCasaWorkerError(RuntimeError):
    """The worker process failed or violated the protocol.

    ``response`` is the worker's error frame when one was received; ``None``
    means a transport-level failure (timeout, closed pipe, protocol
    violation) where the external mutation's outcome is unknown and the
    stream may be desynchronised.
    """

    def __init__(self, message: str, *, response: WorkerResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


_PER_OP_TIMEOUT = {
    "start": 600.0,
    "reconfigure": 600.0,
    "observe": 120.0,
    "skill": 900.0,
    "arm": 900.0,
    "navigate": 300.0,
    "move_to": 120.0,
    "move_delta": 120.0,
    "rotate_pitch": 60.0,
    "set_gripper": 60.0,
    "release": 60.0,
    "scripted_grasp": 120.0,
    "move_base": 120.0,
    "back_project": 60.0,
    "world_map": 60.0,
    "check_success": 60.0,
    "close": 120.0,
}

_MUTATING_OPS = {
    "skill",
    "arm",
    "navigate",
    "move_to",
    "move_delta",
    "rotate_pitch",
    "set_gripper",
    "release",
    "scripted_grasp",
    "move_base",
}

_REQUEST_HISTORY_LIMIT = 200

_WORKER_ENV_BASE = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "NO_ALBUMENTATIONS_UPDATE": "1",
}


class RoboCasaWorkerClient:
    """Serialized client over worker stdin/stdout for one RoboCasa runtime."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        stderr_handle: Any,
        artifact_root: Path,
        ready: WorkerResponse,
        request_timeout_s: float,
    ) -> None:
        self._process = process
        self._stderr_handle = stderr_handle
        self._artifact_root = artifact_root.resolve()
        self._ready = ready
        self._request_timeout_s = request_timeout_s
        self._lock = threading.Lock()
        self._closed = False
        self._started = False
        self._run_id = "unbound"
        self._state: RoboCasaEnvState | None = None
        self._request_history: list[dict[str, Any]] = []
        self._dropped_artifacts = 0

    @classmethod
    def start(
        cls,
        *,
        python_executable: Path,
        worker_source: Path,
        artifact_root: Path,
        log_path: Path,
        env_overrides: dict[str, str] | None = None,
        startup_timeout_s: float = 600.0,
        request_timeout_s: float = 120.0,
    ) -> RoboCasaWorkerClient:
        """Spawn the worker process and wait for its ready frame.

        The caller must send a ``start`` request afterwards to launch the
        env/vla servers; ``start()`` only performs the protocol handshake.
        """
        if not python_executable.is_file():
            raise RoboCasaWorkerError(f"worker python not found: {python_executable}")
        if not worker_source.is_file():
            raise RoboCasaWorkerError(f"worker source not found: {worker_source}")
        artifact_root.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.update(_WORKER_ENV_BASE)
        environment.update(env_overrides or {})
        log_handle = log_path.open("wb")
        process = subprocess.Popen(
            [str(python_executable), str(worker_source)],
            cwd=str(worker_source.parent),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_handle,
            # Isolate the worker from terminal SIGINT: the client controls the
            # lifecycle via terminate/kill, and a stray signal must not kill the
            # worker mid-op without a response frame.
            start_new_session=True,
        )
        try:
            ready = _read_response(process, timeout_s=startup_timeout_s)
            if ready.status != "ready":
                raise RoboCasaWorkerError(f"worker did not become ready: {ready.status}")
            return cls(
                process=process,
                stderr_handle=log_handle,
                artifact_root=artifact_root,
                ready=ready,
                request_timeout_s=request_timeout_s,
            )
        except BaseException:
            _terminate_process(process)
            log_handle.close()
            raise

    # -- identity / state -------------------------------------------------

    @property
    def backend_id(self) -> str:
        return f"robocasa-worker:{self._process.pid}"

    @property
    def worker_pid(self) -> int:
        return self._process.pid

    @property
    def worker_exit_code(self) -> int | None:
        return self._process.poll()

    @property
    def stderr_path(self) -> Path:
        return Path(self._stderr_handle.name)

    @property
    def runtime_started(self) -> bool:
        """Whether the env/vla runtime has been brought up via ``start``."""

        return self._started

    @property
    def current_state(self) -> RoboCasaEnvState:
        if self._state is None:
            raise RoboCasaWorkerError("worker environment has not been started")
        return self._state

    @property
    def request_history(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._request_history)

    def bind_application_run(self, run_id: str, generation: int) -> None:
        del generation
        self._run_id = run_id

    # -- protocol ops -----------------------------------------------------

    def start_runtime(
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
        response = self._request(
            "start",
            {
                "rpent_root": str(rpent_root),
                "task_name": task_name,
                "split": split,
                "seed": seed,
                "cuda_device": cuda_device,
                "hi_res": hi_res,
                "workdir": str(workdir),
            },
        )
        self._started = True
        # Artifacts for this cell land under workdir/artifacts — track the
        # root so path containment stays correct across reconfigures.
        self._artifact_root = workdir.resolve()
        return dict(response.result)

    def reconfigure(
        self,
        *,
        task_name: str,
        split: str,
        seed: int,
        workdir: Path | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"task_name": task_name, "split": split, "seed": seed}
        if workdir is not None:
            payload["workdir"] = str(workdir)
        result = dict(self._request("reconfigure", payload).result)
        if workdir is not None:
            self._artifact_root = workdir.resolve()
        return result

    def observe(self, *, cameras: list[str] | None = None) -> dict[str, Any]:
        payload = {"cameras": cameras} if cameras else {}
        return dict(self._request("observe", payload).result)

    def primitive(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return dict(self._request(operation, payload or {}).result)

    def check_success(self) -> dict[str, Any]:
        return dict(self._request("check_success", {}).result)

    async def screenshot(self) -> bytes:
        import asyncio

        response = await asyncio.to_thread(self.observe, cameras=["agentview"])
        images = response.get("images")
        if not isinstance(images, list) or not images:
            raise RoboCasaWorkerError("worker observe returned no image artifacts")
        artifact = images[0]
        return self._read_image_artifact(artifact)

    def _read_image_artifact(self, artifact: Any) -> bytes:
        if not isinstance(artifact, dict):
            raise RoboCasaWorkerError("worker image artifact metadata is invalid")
        frame_path = artifact.get("path")
        size = artifact.get("size")
        expected_sha = artifact.get("sha256")
        if (
            not isinstance(frame_path, str)
            or not isinstance(size, int)
            or size < 1
            or not isinstance(expected_sha, str)
            or len(expected_sha) != 64
            or artifact.get("mime_type") != "image/png"
        ):
            raise RoboCasaWorkerError("worker image artifact metadata is invalid")
        path = _safe_artifact_path(frame_path, self._artifact_root)
        content = path.read_bytes()
        if len(content) != size or hashlib.sha256(content).hexdigest() != expected_sha:
            raise RoboCasaWorkerError("worker image artifact digest mismatch")
        try:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as image:
                image.verify()
                if image.format != "PNG":
                    raise ValueError("artifact is not PNG")
        except Exception as exc:
            raise RoboCasaWorkerError("worker image artifact is not a valid PNG") from exc
        return content

    @property
    def dropped_artifacts(self) -> int:
        """Count of image artifacts rejected during verification."""

        return self._dropped_artifacts

    def image_artifacts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        """Validate image artifacts from an observe/primitive result and return them."""
        images = result.get("images")
        if not isinstance(images, list):
            return []
        verified = []
        for artifact in images:
            if not isinstance(artifact, dict):
                continue
            try:
                content = self._read_image_artifact(artifact)
            except RoboCasaWorkerError:
                self._dropped_artifacts += 1
                continue
            verified.append({**artifact, "content": content})
        return verified

    # -- lifecycle --------------------------------------------------------

    def close(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {"closed": True, "already_closed": True}
            response = self._request_locked(
                "close", {}, allow_prior_responses=True, timeout_s=_PER_OP_TIMEOUT["close"]
            )
            if response.status not in {"ok", "closed"} or response.external_return_code != 0:
                raise RoboCasaWorkerError("worker close failed")
            try:
                return_code = self._process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                _terminate_process(self._process)
                raise RoboCasaWorkerError("worker did not exit after close") from exc
            self._stderr_handle.close()
            self._closed = True
            if return_code != 0:
                raise RoboCasaWorkerError(
                    f"worker exited with return code {return_code}: {self.stderr_path}"
                )
            return {
                "closed": True,
                "worker_pid": self.worker_pid,
                "stderr_path": str(self.stderr_path),
            }

    def terminate(self) -> None:
        """Hard-stop the worker (and, via --parent-watch, its env/vla servers).

        Deliberately does NOT take ``self._lock``: an in-flight request may
        hold it for up to a per-op timeout, and a wedged worker must not be
        allowed to delay its own teardown.  Process termination is
        idempotent, and ``_closed`` is only a fast-fail hint — the process
        exit check in ``_ensure_open`` is the real guard.
        """
        if self._closed:
            return
        _terminate_process(self._process)
        try:
            self._stderr_handle.close()
        except Exception:
            pass
        self._closed = True

    # -- request/response plumbing ---------------------------------------

    def _request(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        allow_error: bool = False,
    ) -> WorkerResponse:
        with self._lock:
            return self._request_locked(operation, payload, allow_error=allow_error)

    def _request_locked(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        allow_error: bool = False,
        allow_prior_responses: bool = False,
        timeout_s: float | None = None,
    ) -> WorkerResponse:
        self._ensure_open()
        request_id = f"{self._run_id}-{uuid.uuid4().hex}"
        frame = request_frame(request_id, operation, payload)
        assert self._process.stdin is not None
        try:
            self._process.stdin.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
            self._process.stdin.flush()
        except BrokenPipeError as exc:
            raise RoboCasaWorkerError(
                f"worker stdin is closed (return code {self._process.poll()})"
            ) from exc
        deadline = timeout_s or _PER_OP_TIMEOUT.get(operation, self._request_timeout_s)
        response = _read_response(self._process, timeout_s=deadline)
        late_responses = 0
        while response.request_id != request_id:
            if not allow_prior_responses or operation != "close":
                raise RoboCasaWorkerError("worker response request_id mismatch")
            late_responses += 1
            if late_responses > 8:
                raise RoboCasaWorkerError("too many late worker responses before close")
            self._record_history(
                {
                    "request_id": response.request_id,
                    "operation": "late_response",
                    "requested_operation": operation,
                    "status": response.status,
                    "external_return_code": response.external_return_code,
                    "backend_attempted": response.backend_attempted,
                }
            )
            response = _read_response(self._process, timeout_s=deadline)
        self._record_history(
            {
                "request_id": request_id,
                "operation": operation,
                "status": response.status,
                "external_return_code": response.external_return_code,
                "backend_attempted": response.backend_attempted,
            }
        )
        if response.status == "error" and not allow_error:
            raise RoboCasaWorkerError(
                str((response.error or {}).get("message", "worker error")),
                response=response,
            )
        self._update_state(response.result)
        return response

    def _update_state(self, result: dict[str, Any]) -> None:
        state = result.get("state")
        if isinstance(state, dict):
            self._state = _decode_state(state)

    def _record_history(self, entry: dict[str, Any]) -> None:
        self._request_history.append(entry)
        if len(self._request_history) > _REQUEST_HISTORY_LIMIT:
            del self._request_history[: len(self._request_history) - _REQUEST_HISTORY_LIMIT]

    def _ensure_open(self) -> None:
        if self._closed:
            raise RoboCasaWorkerError("worker environment is closed")
        return_code = self._process.poll()
        if return_code is not None:
            raise RoboCasaWorkerError(
                f"worker exited unexpectedly with return code {return_code}"
            )


def _decode_state(value: dict[str, Any]) -> RoboCasaEnvState:
    return RoboCasaEnvState(
        task_name=str(value.get("task_name") or ""),
        split=str(value.get("split") or ""),
        seed=int(value.get("seed") or 0),
        task_language=str(value.get("task_language") or ""),
        success=bool(value.get("success")),
        task_progress=value.get("task_progress"),
        proprio=value.get("proprio") if isinstance(value.get("proprio"), dict) else None,
        step_index=int(value.get("step_index") or 0),
        invalid_action_count=int(value.get("invalid_action_count") or 0),
    )


def _read_response(process: subprocess.Popen[bytes], *, timeout_s: float) -> WorkerResponse:
    if process.stdout is None:
        raise RoboCasaWorkerError("worker stdout is unavailable")
    deadline = time.monotonic() + timeout_s
    while True:
        if time.monotonic() >= deadline:
            raise RoboCasaWorkerError("worker response timed out")
        ready, _, _ = select.select([process.stdout], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            raise RoboCasaWorkerError("worker response timed out")
        line = process.stdout.readline()
        if not line:
            detail = process.poll()
            raise RoboCasaWorkerError(f"worker closed stdout (return code {detail})")
        if len(line) > MAX_LINE_BYTES:
            raise RoboCasaWorkerError("worker response exceeds protocol line limit")
        try:
            return WorkerResponse.from_json(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RoboCasaWorkerError(f"invalid worker response: {exc}") from exc


def _safe_artifact_path(frame_path: str, artifact_root: Path) -> Path:
    path = Path(frame_path).resolve()
    try:
        path.relative_to(artifact_root)
    except ValueError as exc:
        raise RoboCasaWorkerError("worker artifact path escapes the artifact root") from exc
    return path


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


__all__ = ["RoboCasaWorkerClient", "RoboCasaWorkerError"]
