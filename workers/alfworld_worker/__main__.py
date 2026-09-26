"""Run the isolated ALFWorld worker protocol loop."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from collections.abc import Iterator

from protocol import MAX_LINE_BYTES, OPERATIONS, PROTOCOL, emit, response
from thor_backend import ThorBackend


def _module_origin(name: str) -> str:
    import importlib.util

    spec = importlib.util.find_spec(name)
    return str(spec.origin) if spec and spec.origin else ""


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
    except Exception:
        return "unknown"
    return str(getattr(module, "__version__", "unknown"))


@contextlib.contextmanager
def _redirect_external_output() -> Iterator[None]:
    """Keep third-party/Unity stdout out of the worker's NDJSON channel."""

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


def main() -> int:
    backend: ThorBackend | None = None
    closed = False
    seen: set[str] = set()
    emit(
        response(
            "ready",
            status="ready",
            result={
                "worker_version": "3.5.0",
                "python_executable": sys.executable,
                "alfworld_origin": _module_origin("alfworld"),
                "ai2thor_version": _module_version("ai2thor"),
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
            if operation == "reset":
                backend = ThorBackend(payload)
                with _redirect_external_output():
                    result = backend.reset()
                result.update(backend.identity())
                emit(response(request_id, status="ok", result=result, backend_attempted=True))
            elif operation == "observe":
                if backend is None:
                    raise RuntimeError("backend has not been reset")
                with _redirect_external_output():
                    result = backend.observe()
                emit(response(request_id, status="ok", result=result))
            elif operation == "set_task":
                if backend is None:
                    raise RuntimeError("backend has not been reset")
                with _redirect_external_output():
                    result = backend.set_task(payload)
                emit(
                    response(
                        request_id,
                        status="ok",
                        result=result,
                        external_return_code=0,
                        backend_attempted=True,
                    )
                )
            elif operation == "act":
                if backend is None:
                    raise RuntimeError("backend has not been reset")
                with _redirect_external_output():
                    result = backend.act(payload)
                return_code = int(result.pop("external_return_code", 0))
                emit(
                    response(
                        request_id,
                        status="ok",
                        result=result,
                        external_return_code=return_code,
                        backend_attempted=True,
                    )
                )
            else:
                with _redirect_external_output():
                    result = (
                        backend.close()
                        if backend is not None
                        else {"closed": True, "cleanup_status": "succeeded"}
                    )
                emit(response(request_id, status="closed", result=result))
                closed = True
                break
        except Exception as exc:
            if isinstance(exc, ValueError) and operation in {"act", "set_task"}:
                # Expected action/control rejection is already represented by
                # the typed error response. Keep stderr diagnostic without a
                # traceback so a verified failure black box can still close
                # cleanly; unexpected faults retain the full traceback.
                print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            else:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
            emit(
                response(
                    request_id,
                    status="error",
                    external_return_code=1,
                    backend_attempted=operation in {"reset", "act", "close"},
                    error={"code": "backend_failure", "message": f"{type(exc).__name__}: {exc}"},
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
