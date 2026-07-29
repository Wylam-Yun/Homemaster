"""HomeMaster-side proxy for the isolated ALFWorld HTTP worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import select
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from homemaster.benchmarking.alfworld.types import (
    AlfworldEnvState,
    AlfworldExecutionFeedback,
    AlfworldStepResult,
)

_PROTOCOL = "homemaster-alfworld-http-v1"
_TOKEN_ENV = "HOMEMASTER_ALFWORLD_HTTP_TOKEN"
_READY_FD_ENV = "HOMEMASTER_ALFWORLD_READY_FD"


class AlfworldWorkerError(RuntimeError):
    """The isolated environment failed its transport or terminal contract."""


class AlfworldHttpEnvironment:
    """Synchronous adapter facade backed by one serialized loopback worker."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        client: httpx.Client,
        ready: dict[str, Any],
        log_handle: Any,
        log_path: Path,
        request_timeout_s: float,
    ) -> None:
        self._process = process
        self._client = client
        self._ready = ready
        self._log_handle = log_handle
        self.log_path = log_path
        self._request_timeout_s = request_timeout_s
        self._lock = threading.Lock()
        self._closed = False
        self._state = _decode_state(_required_mapping(ready, "state"))
        self._state_sequence = 1
        self._run_id = "unbound"

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
        startup_timeout_s: float = 180.0,
        request_timeout_s: float = 120.0,
    ) -> AlfworldHttpEnvironment:
        token = secrets.token_urlsafe(32)
        ready_read_fd, ready_write_fd = os.pipe()
        environment = dict(os.environ)
        source_root = Path(__file__).resolve().parents[3]
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment.update(
            {
                "DISPLAY": display,
                _TOKEN_ENV: token,
                _READY_FD_ENV: str(ready_write_fd),
                "PYTHONPATH": (
                    str(source_root)
                    if not existing_pythonpath
                    else f"{source_root}{os.pathsep}{existing_pythonpath}"
                ),
            }
        )
        frame_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("wb")
        command = [
            str(python_executable),
            "-m",
            "homemaster.benchmarking.alfworld.http_worker",
            "--asset-root",
            str(asset_root),
            "--data-root",
            str(data_root),
            "--config-path",
            str(config_path),
            "--trial-manifest",
            str(trial_manifest),
            "--trial-index",
            str(trial_index),
            "--env-type",
            env_type,
            "--split",
            split,
            "--seed",
            str(seed),
            "--allow-offscreen-object-navigation",
            str(allow_offscreen_object_navigation).lower(),
            "--frame-dir",
            str(frame_dir),
        ]
        process = subprocess.Popen(
            command,
            env=environment,
            pass_fds=(ready_write_fd,),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        os.close(ready_write_fd)
        try:
            ready = _read_ready(
                ready_read_fd,
                process=process,
                timeout_s=startup_timeout_s,
                log_path=log_path,
            )
            if ready.get("protocol") != _PROTOCOL:
                raise AlfworldWorkerError(f"worker protocol mismatch: {ready.get('protocol')!r}")
            if (
                ready.get("allow_offscreen_object_navigation")
                is not allow_offscreen_object_navigation
            ):
                raise AlfworldWorkerError("worker navigation policy mismatch")
            host = ready.get("host")
            port = ready.get("port")
            if host != "127.0.0.1" or isinstance(port, bool) or not isinstance(port, int):
                raise AlfworldWorkerError("worker returned an invalid loopback address")
            client = httpx.Client(
                base_url=f"http://{host}:{port}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=request_timeout_s,
            )
            instance = cls(
                process=process,
                client=client,
                ready=ready,
                log_handle=log_handle,
                log_path=log_path,
                request_timeout_s=request_timeout_s,
            )
            instance._verify_health(
                python_executable=python_executable,
                asset_root=asset_root,
                allow_offscreen_object_navigation=allow_offscreen_object_navigation,
            )
            return instance
        except BaseException:
            _terminate_process(process)
            log_handle.close()
            raise

    @property
    def backend_id(self) -> str:
        return f"alfworld-http:{self._process.pid}"

    @property
    def worker_pid(self) -> int:
        return self._process.pid

    @property
    def current_state(self) -> AlfworldEnvState:
        return self._state

    @property
    def runtime_identity(self) -> dict[str, Any]:
        return dict(self._health)

    def bind_application_run(self, run_id: str, generation: int) -> None:
        del generation
        self._run_id = run_id

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        if tool_name != "robot_go_to":
            raise ValueError("HTTP ALFWorld navigation requires robot_go_to")
        return self._action(
            "/v1/go-to",
            {"target": target, "tool_args": tool_args},
        )

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        if tool_name != "robot_manipulate":
            raise ValueError("HTTP ALFWorld manipulation requires robot_manipulate")
        return self._action(
            "/v1/manipulate",
            {"action": action, "tool_args": tool_args},
        )

    async def screenshot(self) -> bytes:
        return await asyncio.to_thread(self._screenshot_sync)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            descendants = _descendants(self._process.pid)
            request_id = f"close-{uuid.uuid4().hex}"
            response = self._client.post(
                "/v1/close",
                json={"request_id": request_id},
                timeout=self._request_timeout_s,
            )
            payload = _successful_json(response)
            cleanup = _required_mapping(payload, "cleanup")
            if cleanup.get("status") != "succeeded":
                raise AlfworldWorkerError(f"worker cleanup failed: {cleanup.get('status')!r}")
            self._client.close()
            try:
                return_code = self._process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                _terminate_process(self._process)
                raise AlfworldWorkerError("worker did not exit after close") from exc
            self._log_handle.close()
            self._closed = True
            if return_code != 0:
                raise AlfworldWorkerError(f"worker exited with return code {return_code}")
            time.sleep(0.2)
            remaining = _alive(descendants)
            if remaining:
                raise AlfworldWorkerError(
                    f"worker descendants survived cleanup: {sorted(remaining)}"
                )

    def _verify_health(
        self,
        *,
        python_executable: Path,
        asset_root: Path,
        allow_offscreen_object_navigation: bool,
    ) -> None:
        response = self._client.get("/v1/health")
        payload = _successful_json(response)
        if payload.get("protocol") != _PROTOCOL:
            raise AlfworldWorkerError("health endpoint protocol mismatch")
        if (
            payload.get("allow_offscreen_object_navigation")
            is not allow_offscreen_object_navigation
        ):
            raise AlfworldWorkerError("worker health navigation policy mismatch")
        if payload.get("ai2thor_version") != "2.1.0":
            raise AlfworldWorkerError(
                f"AI2-THOR version mismatch: {payload.get('ai2thor_version')!r}"
            )
        reported_python = Path(str(payload.get("python_executable", ""))).resolve()
        configured_python = python_executable.resolve(strict=True)
        if reported_python != configured_python:
            raise AlfworldWorkerError(
                f"worker interpreter mismatch: {reported_python} != {configured_python}"
            )
        origin = Path(str(payload.get("alfworld_origin", ""))).resolve(strict=True)
        try:
            origin.relative_to(asset_root.resolve(strict=True))
        except ValueError:
            pass
        else:
            raise AlfworldWorkerError(
                "worker imported ALFWorld from the asset checkout instead of its environment"
            )
        digest = str(payload.get("alfworld_environment_sha256", ""))
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise AlfworldWorkerError("worker source digest is invalid")
        self._health = payload

    def _action(self, path: str, values: dict[str, Any]) -> AlfworldStepResult:
        with self._lock:
            self._ensure_open()
            request_id = f"{self._run_id}-{uuid.uuid4().hex}"
            response = self._client.post(
                path,
                json={"request_id": request_id, **values},
                timeout=self._request_timeout_s,
            )
            payload = _successful_json(response)
            if payload.get("request_id") != request_id:
                raise AlfworldWorkerError("worker response request_id mismatch")
            state_sequence = payload.get("state_sequence")
            if (
                isinstance(state_sequence, bool)
                or not isinstance(state_sequence, int)
                or state_sequence <= self._state_sequence
            ):
                raise AlfworldWorkerError("worker state sequence did not advance")
            result = _decode_step(_required_mapping(payload, "result"))
            backend_attempted = payload.get("backend_attempted")
            if not isinstance(backend_attempted, bool) or backend_attempted != (
                result.backend_action_count > 0
            ):
                raise AlfworldWorkerError("worker backend-attempted receipt mismatch")
            self._state_sequence = state_sequence
            self._state = result.state
            return result

    def _screenshot_sync(self) -> bytes:
        with self._lock:
            self._ensure_open()
            response = self._client.get(
                "/v1/screenshot",
                timeout=self._request_timeout_s,
            )
            response.raise_for_status()
            if response.headers.get("Content-Type") != "image/png":
                raise AlfworldWorkerError("worker screenshot is not image/png")
            content = response.content
            digest = hashlib.sha256(content).hexdigest()
            if not content or response.headers.get("X-Content-SHA256") != digest:
                raise AlfworldWorkerError("worker screenshot hash mismatch")
            try:
                with Image.open(__import__("io").BytesIO(content)) as image:
                    image.load()
                    if image.width <= 0 or image.height <= 0:
                        raise AlfworldWorkerError("worker screenshot is empty")
            except AlfworldWorkerError:
                raise
            except Exception as exc:
                raise AlfworldWorkerError("worker screenshot is not decodable") from exc
            return content

    def _ensure_open(self) -> None:
        if self._closed:
            raise AlfworldWorkerError("worker environment is closed")
        return_code = self._process.poll()
        if return_code is not None:
            raise AlfworldWorkerError(f"worker exited unexpectedly with return code {return_code}")


def _decode_state(payload: dict[str, Any]) -> AlfworldEnvState:
    values = dict(payload)
    values["admissible_commands"] = tuple(values.get("admissible_commands", ()))
    return AlfworldEnvState(**values)


def _decode_step(payload: dict[str, Any]) -> AlfworldStepResult:
    values = dict(payload)
    state = _decode_state(_required_mapping(values, "state"))
    feedback = dict(_required_mapping(values, "execution_feedback"))
    if isinstance(feedback.get("inventory"), list):
        feedback["inventory"] = tuple(feedback["inventory"])
    execution_feedback = AlfworldExecutionFeedback(**feedback)
    return AlfworldStepResult(
        tool_name=str(values["tool_name"]),
        tool_args=dict(values["tool_args"]),
        translated_command=values.get("translated_command"),
        success=bool(values["success"]),
        state=state,
        execution_feedback=execution_feedback,
        feedback=values.get("feedback"),
        backend_action_count=int(values.get("backend_action_count", 0)),
        trace_events=tuple(values.get("trace_events", ())),
    )


def _successful_json(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise AlfworldWorkerError(
            f"worker HTTP failure ({response.status_code}): {response.text[:500]}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise AlfworldWorkerError(f"worker returned failure: {payload!r}")
    return payload


def _required_mapping(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise AlfworldWorkerError(f"worker payload is missing object {name!r}")
    return value


def _read_ready(
    ready_fd: int,
    *,
    process: subprocess.Popen[bytes],
    timeout_s: float,
    log_path: Path,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AlfworldWorkerError("worker startup timed out")
            readable, _, _ = select.select([ready_fd], [], [], min(remaining, 0.2))
            if not readable:
                if process.poll() is not None:
                    detail = log_path.read_text(encoding="utf-8", errors="replace")
                    raise AlfworldWorkerError(
                        f"worker exited before readiness ({process.returncode}): {detail[-2000:]}"
                    )
                continue
            with os.fdopen(ready_fd, "rb", closefd=False) as reader:
                line = reader.readline()
            if not line:
                raise AlfworldWorkerError("worker closed readiness pipe without a record")
            payload = json.loads(line)
            if not isinstance(payload, dict) or payload.get("type") != "ready":
                raise AlfworldWorkerError("worker readiness record is invalid")
            return payload
    finally:
        os.close(ready_fd)


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _descendants(root_pid: int) -> set[int]:
    output = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True)
    children: dict[int, set[int]] = {}
    for line in output.splitlines():
        pid_text, parent_text = line.split()
        children.setdefault(int(parent_text), set()).add(int(pid_text))
    found: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, ()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def _alive(pids: set[int]) -> set[int]:
    alive: set[int] = set()
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        alive.add(pid)
    return alive


__all__ = ["AlfworldHttpEnvironment", "AlfworldWorkerError"]
