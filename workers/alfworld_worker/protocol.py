"""Dependency-free worker-side protocol helpers."""

from __future__ import annotations

import json
from typing import Any

PROTOCOL = "homemaster-alfworld-v1"
OPERATIONS = frozenset({"reset", "set_task", "observe", "act", "close"})
MAX_LINE_BYTES = 1_048_576


def response(
    request_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    external_return_code: int = 0,
    backend_attempted: bool = False,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "status": status,
        "external_return_code": external_return_code,
        "backend_attempted": backend_attempted,
        "result": dict(result or {}),
        "error": error,
    }


def emit(payload: dict[str, Any]) -> None:
    import sys

    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


__all__ = ["MAX_LINE_BYTES", "OPERATIONS", "PROTOCOL", "emit", "response"]
