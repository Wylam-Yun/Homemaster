"""Typed validation for the isolated ALFWorld NDJSON protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROTOCOL = "homemaster-alfworld-v1"
OPERATIONS = frozenset({"reset", "set_task", "observe", "act", "close"})
MAX_LINE_BYTES = 1_048_576


class AlfworldWorkerProtocolError(ValueError):
    """The worker sent a malformed or unsafe protocol frame."""


@dataclass(frozen=True)
class WorkerResponse:
    request_id: str
    status: str
    external_return_code: int
    backend_attempted: bool
    result: dict[str, Any]
    error: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, value: object) -> WorkerResponse:
        if not isinstance(value, dict):
            raise AlfworldWorkerProtocolError("worker response must be an object")
        if value.get("protocol") != PROTOCOL:
            raise AlfworldWorkerProtocolError("worker response protocol mismatch")
        request_id = value.get("request_id")
        status = value.get("status")
        code = value.get("external_return_code")
        attempted = value.get("backend_attempted")
        result = value.get("result")
        if not isinstance(request_id, str) or not request_id:
            raise AlfworldWorkerProtocolError("worker response request_id is invalid")
        if status not in {"ready", "ok", "error", "closed"}:
            raise AlfworldWorkerProtocolError("worker response status is invalid")
        if isinstance(code, bool) or not isinstance(code, int):
            raise AlfworldWorkerProtocolError("worker external_return_code is invalid")
        if not isinstance(attempted, bool):
            raise AlfworldWorkerProtocolError("worker backend_attempted is invalid")
        if not isinstance(result, dict):
            raise AlfworldWorkerProtocolError("worker result must be an object")
        error = value.get("error")
        if error is not None and not isinstance(error, dict):
            raise AlfworldWorkerProtocolError("worker error must be an object or null")
        return cls(request_id, status, code, attempted, dict(result), error)


def request_frame(request_id: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not request_id:
        raise ValueError("request_id must be non-empty")
    if operation not in OPERATIONS:
        raise ValueError(f"unsupported ALFWorld operation: {operation}")
    if not isinstance(payload, dict):
        raise TypeError("worker payload must be an object")
    return {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "operation": operation,
        "payload": payload,
    }


__all__ = [
    "AlfworldWorkerProtocolError",
    "MAX_LINE_BYTES",
    "OPERATIONS",
    "PROTOCOL",
    "WorkerResponse",
    "request_frame",
]
