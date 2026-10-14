"""Run the isolated RoboCasa worker protocol loop."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from collections.abc import Iterator

from protocol import MAX_LINE_BYTES, OPERATIONS, PROTOCOL, emit, response


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
    except Exception:
        return "unknown"
    return str(getattr(module, "__version__", "unknown"))


def _git_commit(root: str) -> str:
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


@contextlib.contextmanager
def _redirect_external_output() -> Iterator[None]:
    """Keep third-party/simulator stdout out of the worker's NDJSON channel."""

    saved_fd = os.dup(1)
    try:
        sys.stdout.flush()
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 1)
        os.close(saved_fd)


_MUTATING = {
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


def main() -> int:
    backend = None
    closed = False
    seen: set[str] = set()
    emit(
        response(
            "ready",
            status="ready",
            result={
                "worker_version": "3.5.0",
                "python_executable": sys.executable,
                "capabilities": sorted(OPERATIONS),
            },
        )
    )
    for raw_line in sys.stdin.buffer:
        if len(raw_line) > MAX_LINE_BYTES:
            emit(
                response(
                    "unknown",
                    status="error",
                    external_return_code=64,
                    error={"code": "line_too_long", "message": "request exceeds protocol limit"},
                )
            )
            continue
        try:
            frame = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            emit(
                response(
                    "unknown",
                    status="error",
                    external_return_code=64,
                    error={"code": "invalid_json", "message": str(exc)},
                )
            )
            continue
        if not isinstance(frame, dict):
            emit(
                response(
                    "unknown",
                    status="error",
                    external_return_code=64,
                    error={"code": "invalid_frame", "message": "request must be an object"},
                )
            )
            continue
        request_id = frame.get("request_id")
        operation = frame.get("operation")
        payload = frame.get("payload")
        if not isinstance(request_id, str) or not request_id:
            emit(
                response(
                    "unknown",
                    status="error",
                    external_return_code=64,
                    error={"code": "missing_request_id", "message": "request_id is required"},
                )
            )
            continue
        if request_id in seen:
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=64,
                    error={
                        "code": "duplicate_request_id",
                        "message": "request_id was already used",
                    },
                )
            )
            continue
        seen.add(request_id)
        if frame.get("protocol") != PROTOCOL:
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=64,
                    error={"code": "protocol_mismatch", "message": "protocol mismatch"},
                )
            )
            continue
        if operation not in OPERATIONS:
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=64,
                    error={"code": "unsupported_operation", "message": "unsupported operation"},
                )
            )
            continue
        if not isinstance(payload, dict):
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=64,
                    error={"code": "invalid_payload", "message": "payload must be an object"},
                )
            )
            continue
        if closed:
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=64,
                    error={"code": "worker_closed", "message": "worker is closed"},
                )
            )
            continue
        try:
            if operation == "start":
                from rpent_backend import RPentBackend

                backend = RPentBackend()
                with _redirect_external_output():
                    result = backend.start(payload)
                    # Version probes may trigger first-time imports whose
                    # stdout must stay off the NDJSON channel.
                    result["worker"] = {
                        "python_executable": sys.executable,
                        "rpent_commit": _git_commit(
                            str(payload.get("rpent_root") or ".")
                        ),
                        "robocasa_version": _module_version("robocasa"),
                        "robosuite_version": _module_version("robosuite"),
                    }
                emit(response(request_id, status="ok", result=result, backend_attempted=True))
            elif operation == "close":
                with _redirect_external_output():
                    result = backend.close(payload) if backend is not None else {"closed": True}
                emit(response(request_id, status="closed", result=result))
                closed = True
                break
            else:
                if backend is None:
                    raise RuntimeError("backend has not been started")
                handler = getattr(backend, operation, None)
                if not callable(handler):
                    raise RuntimeError(f"unhandled operation: {operation}")
                with _redirect_external_output():
                    result = handler(payload)
                emit(
                    response(
                        request_id,
                        status="ok",
                        result=result,
                        backend_attempted=operation in _MUTATING
                        or operation in {"start", "reconfigure"},
                    )
                )
        except Exception as exc:
            if isinstance(exc, ValueError):
                print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            else:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=1,
                    backend_attempted=operation in _MUTATING
                    or operation in {"start", "reconfigure", "close"},
                    error={"code": "backend_failure", "message": f"{type(exc).__name__}: {exc}"},
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
