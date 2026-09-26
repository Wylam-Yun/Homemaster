"""HomeMaster-side client for the isolated ALFWorld NDJSON worker."""

from __future__ import annotations

import hashlib
import io
import json
import os
import select
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from homemaster.alfworld.backend import BackendReceipt, ThorObservation
from homemaster.alfworld.types import (
    AlfworldEnvState,
    AlfworldExecutionFeedback,
    AlfworldStepResult,
)
from homemaster.alfworld.worker_protocol import (
    MAX_LINE_BYTES,
    WorkerResponse,
    request_frame,
)


class AlfworldWorkerError(RuntimeError):
    """The isolated worker violated its lifecycle or receipt contract."""


class WorkerThorBackend:
    """Adapt one worker client to the Harness' narrow THOR backend contract."""

    def __init__(self, client: AlfworldWorkerClient) -> None:
        self.client = client

    def reset(self, trial: Any) -> BackendReceipt:
        return self.client.reset(trial)

    def observe(self) -> ThorObservation:
        return self.client.observe()

    def act(self, action: dict[str, Any]) -> BackendReceipt:
        payload = dict(action)
        object_id = payload.get("objectId")
        label = self._label_for_id(object_id)
        if payload.get("action") == "TeleportFull":
            return self.client.act(
                {"action": payload["action"], "target": label or str(object_id or "")}
            )
        if label and "object" not in payload:
            payload["object"] = label
        if label and payload.get("target") == object_id:
            payload["target"] = label
        return self.client.act(payload)

    def close(self) -> BackendReceipt:
        return self.client.close()

    def _label_for_id(self, object_id: Any) -> str | None:
        if not isinstance(object_id, str):
            return None
        for item in self.client.raw_state.get("objects", ()):
            if isinstance(item, dict) and item.get("objectId") == object_id:
                value = item.get("objectType") or item.get("name")
                return value if isinstance(value, str) else None
        return None


class AlfworldWorkerClient:
    """Serialized one-episode client over worker stdin/stdout."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        stderr_handle: Any,
        artifact_root: Path,
        ready: WorkerResponse,
        expected_identity: dict[str, str],
        request_timeout_s: float,
    ) -> None:
        self._process = process
        self._stderr_handle = stderr_handle
        self._artifact_root = artifact_root.resolve()
        self._ready = ready
        self._expected_identity = dict(expected_identity)
        self._request_timeout_s = request_timeout_s
        self._data_root: Path | None = None
        self._frame_dir = artifact_root / "frames"
        self._lock = threading.Lock()
        self._closed = False
        self._run_id = "unbound"
        self._state: AlfworldEnvState | None = None
        self._raw_state: dict[str, Any] = {}
        self._state_sequence = int(ready.result.get("state_sequence", 0))
        self._health = dict(ready.result)
        self._reset_payload: dict[str, Any] | None = None
        self._request_history: list[dict[str, Any]] = []
        self._close_sigint_count = 0

    @classmethod
    def start(
        cls,
        *,
        python_executable: Path,
        asset_root: Path,
        data_root: Path,
        config_path: Path,
        trial_manifest: Path,
        trial_index: int,
        env_type: str,
        split: str,
        seed: int,
        allow_offscreen_object_navigation: bool,
        display: str,
        frame_dir: Path,
        log_path: Path,
        reset_on_start: bool = True,
        startup_timeout_s: float = 180.0,
        request_timeout_s: float = 120.0,
    ) -> AlfworldWorkerClient:
        if env_type != "AlfredThorEnv":
            raise ValueError("the isolated worker only supports AlfredThorEnv")
        configured_root = os.environ.get("HOMEMASTER_REPO_ROOT", "").strip()
        source_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else Path(__file__).resolve().parents[3]
        )
        worker_entrypoint = source_root / "workers" / "alfworld_worker" / "main.py"
        if not worker_entrypoint.is_file():
            raise AlfworldWorkerError(
                f"isolated worker source is missing from checkout: {worker_entrypoint}"
            )
        frame_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_root = frame_dir.parent.resolve()
        trial_root = data_root / "json_2.1.1"
        manifest = json.loads(trial_manifest.read_text(encoding="utf-8"))
        entries = manifest.get("entries") if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or not 0 <= trial_index < len(entries):
            raise AlfworldWorkerError("trial manifest index is invalid")
        entry = entries[trial_index]
        if not isinstance(entry, dict):
            raise AlfworldWorkerError("trial manifest entry is invalid")
        expected_identity = {
            key: entry.get(key)
            for key in (
                "trial_id",
                "trial_sha256",
                "expected_logical_scene",
                "goal_identity",
                "goal_fingerprint",
            )
        }
        if any(not isinstance(value, str) or not value for value in expected_identity.values()):
            raise AlfworldWorkerError("trial manifest identity is incomplete")
        trial_id = entry.get("trial_id")
        if not isinstance(trial_id, str) or not trial_id:
            raise AlfworldWorkerError("trial manifest entry has no trial_id")
        trial_path = (trial_root / trial_id).resolve(strict=True)
        try:
            trial_path.relative_to(trial_root.resolve(strict=True))
        except ValueError as exc:
            raise AlfworldWorkerError("trial path escapes the data root") from exc
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.update({"DISPLAY": display})
        command = [
            str(python_executable),
            str(worker_entrypoint),
        ]
        log_handle = log_path.open("wb")
        process = subprocess.Popen(
            command,
            cwd=str(source_root / "workers" / "alfworld_worker"),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_handle,
        )
        try:
            ready = _read_response(process, timeout_s=startup_timeout_s)
            if ready.status != "ready":
                raise AlfworldWorkerError(f"worker did not become ready: {ready.status}")
            if ready.external_return_code != 0:
                raise AlfworldWorkerError("worker readiness returned a non-zero code")
            instance = cls(
                process=process,
                stderr_handle=log_handle,
                artifact_root=artifact_root,
                ready=ready,
                expected_identity=expected_identity,
                request_timeout_s=request_timeout_s,
            )
            instance._data_root = data_root.resolve(strict=True)
            instance._frame_dir = frame_dir.resolve()
            reset_payload = {
                "asset_root": str(asset_root),
                "data_root": str(data_root),
                "config_path": str(config_path),
                "trial_path": str(trial_path),
                "trial_id": trial_id,
                "split": split,
                "seed": seed,
                "frame_dir": str(frame_dir),
                "allow_offscreen_object_navigation": allow_offscreen_object_navigation,
            }
            instance._reset_payload = reset_payload
            if reset_on_start:
                reset = instance._request("reset", reset_payload)
                if reset.external_return_code != 0:
                    raise AlfworldWorkerError("worker reset returned a non-zero code")
                _validate_worker_trial_identity(reset.result, expected_identity)
                instance._health.update(reset.result)
                instance._update_state(reset.result)
            return instance
        except BaseException:
            _terminate_process(process)
            log_handle.close()
            raise

    @property
    def backend_id(self) -> str:
        return f"alfworld-worker:{self._process.pid}"

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
    def current_state(self) -> AlfworldEnvState:
        if self._state is None:
            raise AlfworldWorkerError("worker environment has not been reset")
        return self._state

    @property
    def env_type(self) -> str:
        return "AlfredThorEnv"

    @property
    def generation(self) -> int:
        return int(self._health.get("scene_generation", 1))

    @property
    def goal_generation(self) -> int:
        return int(self._health.get("goal_generation", 1))

    @property
    def state_sequence(self) -> int:
        return self._state_sequence

    @property
    def authoritative_object_index(self) -> Any | None:
        return None

    @property
    def runtime_identity(self) -> dict[str, Any]:
        return dict(self._health)

    @property
    def request_history(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._request_history)

    @property
    def close_sigint_count(self) -> int:
        """Number of SIGINT signals deferred while closing this worker."""

        return self._close_sigint_count

    def bind_application_run(self, run_id: str, generation: int) -> None:
        del generation
        self._run_id = run_id

    def set_frame_dir(self, frame_dir: Path) -> None:
        self._frame_dir = frame_dir.resolve()
        self._frame_dir.mkdir(parents=True, exist_ok=True)

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        if tool_name != "robot_go_to":
            raise ValueError("worker navigation requires robot_go_to")
        response = self._request(
            "act",
            {"kind": "go_to", "target": target, "tool_name": tool_name, "tool_args": tool_args},
        )
        return self._decode_step_response(response)

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        if tool_name != "robot_manipulate":
            raise ValueError("worker manipulation requires robot_manipulate")
        response = self._request(
            "act",
            {
                "kind": "manipulate",
                "action": action,
                "tool_name": tool_name,
                "tool_args": tool_args,
            },
        )
        return self._decode_step_response(response)

    async def screenshot(self) -> bytes:
        response = await __import__("asyncio").to_thread(self._request, "observe", {})
        state = response.result.get("state")
        artifact = state.get("frame_artifact") if isinstance(state, dict) else None
        if not isinstance(artifact, dict):
            raise AlfworldWorkerError("worker observe returned no frame artifact")
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
            raise AlfworldWorkerError("worker frame artifact metadata is invalid")
        path = _safe_artifact_path(frame_path, self._artifact_root)
        content = path.read_bytes()
        if len(content) != size or hashlib.sha256(content).hexdigest() != expected_sha:
            raise AlfworldWorkerError("worker frame artifact digest mismatch")
        try:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as image:
                image.verify()
                if image.format != "PNG":
                    raise ValueError("artifact is not PNG")
        except Exception as exc:
            raise AlfworldWorkerError("worker frame artifact is not a valid PNG") from exc
        return content

    @property
    def raw_state(self) -> dict[str, Any]:
        return dict(self._raw_state)

    def reset(self, trial: Any) -> BackendReceipt:
        del trial
        if self._reset_payload is None:
            return BackendReceipt(
                "reset",
                1,
                False,
                self.raw_state,
                error="worker reset payload is unavailable",
            )
        reset_payload = dict(self._reset_payload)
        reset_payload["frame_dir"] = str(self._frame_dir)
        response = self._request("reset", reset_payload)
        try:
            _validate_worker_trial_identity(response.result, self._expected_identity)
        except AlfworldWorkerError as exc:
            return BackendReceipt(
                "reset",
                1,
                response.backend_attempted,
                self.raw_state,
                error=str(exc),
            )
        self._health.update(response.result)
        self._update_state(response.result)
        return BackendReceipt(
            "reset",
            response.external_return_code,
            response.backend_attempted,
            self.raw_state,
            evidence_ref=f"worker:{self.worker_pid}:reset",
        )

    def set_task(self, selection: Any) -> BackendReceipt:
        """Advance to a verified goal while retaining the worker scene."""

        if self._data_root is None:
            return BackendReceipt("set_task", 1, False, error="worker data root is unavailable")
        trial_root = (self._data_root / "json_2.1.1").resolve(strict=True)
        trial_path = (trial_root / str(selection.trial_id)).resolve(strict=True)
        try:
            trial_path.relative_to(trial_root)
        except ValueError:
            return BackendReceipt("set_task", 64, False, error="trial path escapes data root")
        trial_sha256 = hashlib.sha256(trial_path.read_bytes()).hexdigest()
        if trial_sha256 != selection.trial_sha256:
            return BackendReceipt("set_task", 64, False, error="trial bytes hash mismatch")
        payload = {
            "trial_id": selection.trial_id,
            "trial_sha256": selection.trial_sha256,
            "expected_logical_scene": selection.expected_logical_scene,
            "goal_identity": selection.goal_identity,
            "goal_fingerprint": selection.goal_fingerprint,
            "frame_dir": str(self._frame_dir),
        }
        try:
            response = self._request("set_task", payload, allow_error=True)
        except AlfworldWorkerError as exc:
            return BackendReceipt("set_task", 1, False, self.raw_state, error=str(exc))
        if response.status == "error":
            return BackendReceipt(
                "set_task",
                response.external_return_code,
                response.backend_attempted,
                self.raw_state,
                error=str((response.error or {}).get("message", "worker set_task failed")),
            )
        try:
            _validate_worker_trial_identity(response.result, {
                key: payload[key]
                for key in (
                    "trial_id",
                    "trial_sha256",
                    "expected_logical_scene",
                    "goal_identity",
                    "goal_fingerprint",
                )
            })
        except AlfworldWorkerError as exc:
            return BackendReceipt(
                "set_task",
                1,
                response.backend_attempted,
                self.raw_state,
                error=str(exc),
            )
        self._health.update(response.result)
        self._update_state(response.result)
        return BackendReceipt(
            "set_task",
            response.external_return_code,
            response.backend_attempted,
            dict(response.result),
            evidence_ref=f"worker:{self.worker_pid}:set-task",
        )

    def observe(self) -> ThorObservation:
        response = self._request("observe", {})
        self._update_state(response.result)
        return ThorObservation(
            state=self.raw_state,
            scene_generation=int(response.result.get("scene_generation", self.generation)),
            state_sequence=int(response.result.get("state_sequence", self.state_sequence)),
        )

    def act(self, action: dict[str, Any]) -> BackendReceipt:
        kind = "go_to" if action.get("action") == "TeleportFull" else "manipulate"
        target = str(action.get("objectId") or action.get("target") or "")
        if kind == "go_to":
            step = self.go_to_target(target, tool_name="robot_go_to", tool_args={"target": target})
        else:
            step = self.manipulate_with_thor(
                action=str(action.get("action", "")),
                tool_name="robot_manipulate",
                tool_args=dict(action),
            )
        return BackendReceipt(
            "act",
            0 if step.success else 1,
            step.backend_action_count > 0,
            self.raw_state,
            evidence_ref=f"worker:{self.worker_pid}:state-{self.state_sequence}",
            error=None if step.success else step.failure_reason,
        )

    def close(self) -> BackendReceipt:
        with self._lock:
            if self._closed:
                return BackendReceipt("close", 0, False, {"already_closed": True})
            # A signal at this boundary must not interrupt reconciliation: the
            # worker may already have written an action receipt, and close must
            # still be sent and read before the process is considered stopped.
            deferred_sigints = [0]
            with _defer_sigint(deferred_sigints):
                # Consume a bounded late receipt before reading the close
                # receipt when an earlier action was interrupted.
                response = self._request_locked("close", {}, allow_prior_responses=True)
                if response.status not in {"ok", "closed"} or response.external_return_code != 0:
                    raise AlfworldWorkerError("worker close failed")
                try:
                    return_code = self._process.wait(timeout=15)
                except subprocess.TimeoutExpired as exc:
                    _terminate_process(self._process)
                    raise AlfworldWorkerError("worker did not exit after close") from exc
                stderr_path = Path(self._stderr_handle.name)
                self._stderr_handle.close()
                self._closed = True
                if return_code != 0:
                    raise AlfworldWorkerError(
                        f"worker exited with return code {return_code}: {stderr_path}"
                    )
                stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
                if "Traceback (most recent call last)" in stderr:
                    raise AlfworldWorkerError("worker stderr contains a traceback")
            self._close_sigint_count += deferred_sigints[0]
            return BackendReceipt(
                "close",
                0,
                True,
                {
                    "closed": True,
                    "worker_pid": self.worker_pid,
                    "sigint_count": self._close_sigint_count,
                },
                evidence_ref=f"worker:{self.worker_pid}:close",
            )

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
    ) -> WorkerResponse:
        self._ensure_open()
        request_id = f"{self._run_id}-{uuid.uuid4().hex}"
        frame = request_frame(request_id, operation, payload)
        assert self._process.stdin is not None
        self._process.stdin.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
        self._process.stdin.flush()
        response = _read_response(self._process, timeout_s=self._request_timeout_s)
        late_responses = 0
        while response.request_id != request_id:
            if not allow_prior_responses or operation != "close":
                raise AlfworldWorkerError("worker response request_id mismatch")
            late_responses += 1
            if late_responses > 8:
                raise AlfworldWorkerError("too many late worker responses before close")
            self._request_history.append(
                {
                    "request_id": response.request_id,
                    "operation": "late_response",
                    "requested_operation": operation,
                    "status": response.status,
                    "external_return_code": response.external_return_code,
                    "backend_attempted": response.backend_attempted,
                }
            )
            if response.status != "error":
                self._update_state(response.result)
            response = _read_response(self._process, timeout_s=self._request_timeout_s)
        self._request_history.append(
            {
                "request_id": request_id,
                "operation": operation,
                "status": response.status,
                "external_return_code": response.external_return_code,
                "backend_attempted": response.backend_attempted,
            }
        )
        if response.status == "error" and not allow_error:
            raise AlfworldWorkerError(str((response.error or {}).get("message", "worker error")))
        if operation != "observe":
            self._update_state(response.result)
        return response

    def _decode_step_response(self, response: WorkerResponse) -> AlfworldStepResult:
        step = response.result.get("step")
        if not isinstance(step, dict):
            raise AlfworldWorkerError("worker action response has no step")
        return _decode_step(step)

    def _update_state(self, result: dict[str, Any]) -> None:
        state = result.get("state")
        if isinstance(state, dict):
            self._raw_state = dict(state)
            self._state = _decode_state(state)
        sequence = result.get("state_sequence")
        if isinstance(sequence, int) and sequence > self._state_sequence:
            self._state_sequence = sequence

    def _ensure_open(self) -> None:
        if self._closed:
            raise AlfworldWorkerError("worker environment is closed")
        return_code = self._process.poll()
        if return_code is not None:
            raise AlfworldWorkerError(f"worker exited unexpectedly with return code {return_code}")


def _read_response(process: subprocess.Popen[bytes], *, timeout_s: float) -> WorkerResponse:
    if process.stdout is None:
        raise AlfworldWorkerError("worker stdout is unavailable")
    deadline = time.monotonic() + timeout_s
    while True:
        if time.monotonic() >= deadline:
            raise AlfworldWorkerError("worker response timed out")
        ready, _, _ = select.select([process.stdout], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            raise AlfworldWorkerError("worker response timed out")
        line = process.stdout.readline()
        if not line:
            detail = process.poll()
            raise AlfworldWorkerError(f"worker closed stdout (return code {detail})")
        if len(line) > MAX_LINE_BYTES:
            raise AlfworldWorkerError("worker response exceeds protocol line limit")
        try:
            return WorkerResponse.from_json(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise AlfworldWorkerError(f"invalid worker response: {exc}") from exc


def _decode_state(value: object) -> AlfworldEnvState:
    if not isinstance(value, dict):
        raise AlfworldWorkerError("worker state is missing")
    payload = dict(value)
    allowed = {
        "episode_id",
        "task",
        "observation",
        "inventory",
        "last_command",
        "last_feedback",
        "reward",
        "done",
        "won",
        "goal_condition_success_rate",
        "frame_path",
        "step_index",
        "invalid_action_count",
        "admissible_commands",
        "frame_artifact",
    }
    payload = {key: item for key, item in payload.items() if key in allowed}
    payload["admissible_commands"] = tuple(payload.get("admissible_commands", ()))
    return AlfworldEnvState(**payload)


def _decode_step(value: dict[str, Any]) -> AlfworldStepResult:
    feedback = dict(value.get("execution_feedback") or {})
    feedback["inventory"] = (
        tuple(feedback["inventory"])
        if isinstance(feedback.get("inventory"), list)
        else feedback.get("inventory")
    )
    error = feedback.get("error")
    if isinstance(error, dict):
        feedback["error"] = str(error.get("code") or "unclassified_execution_failure")
    return AlfworldStepResult(
        tool_name=str(value["tool_name"]),
        tool_args=dict(value.get("tool_args") or {}),
        translated_command=value.get("translated_command"),
        success=bool(value.get("success")),
        state=_decode_state(value.get("state")),
        execution_feedback=AlfworldExecutionFeedback(**feedback),
        feedback=value.get("feedback"),
        backend_action_count=int(value.get("backend_action_count", 0)),
        trace_events=tuple(value.get("trace_events") or ()),
    )


def _safe_artifact_path(value: str, root: Path) -> Path:
    path = Path(value).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise AlfworldWorkerError("worker artifact path escapes the artifact root") from exc
    return path


@contextmanager
def _defer_sigint(counter: list[int]) -> Iterator[None]:
    """Defer SIGINT while a worker close receipt is being reconciled.

    Python delivers signals to the main thread.  A worker client used from a
    background thread cannot install a handler, so it keeps the caller's
    normal behavior.  The main-thread close path temporarily owns SIGINT and
    records every signal until the external close and process wait finish.
    """

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    previous = signal.getsignal(signal.SIGINT)

    def handle_sigint(_signum: int, _frame: Any) -> None:
        counter[0] += 1

    signal.signal(signal.SIGINT, handle_sigint)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _validate_worker_trial_identity(
    result: dict[str, Any], expected: dict[str, str]
) -> None:
    mismatches = {
        key: (expected_value, result.get(key))
        for key, expected_value in expected.items()
        if result.get(key) != expected_value
    }
    if mismatches:
        details = ", ".join(
            f"{key}: expected {expected_value!r}, got {actual!r}"
            for key, (expected_value, actual) in sorted(mismatches.items())
        )
        raise AlfworldWorkerError(f"worker trial identity mismatch: {details}")


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


__all__ = ["AlfworldWorkerClient", "AlfworldWorkerError", "WorkerThorBackend"]
