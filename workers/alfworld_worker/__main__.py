"""Run the dependency-light ALFWorld worker protocol loop."""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL = "homemaster-alfworld-v1"
OPERATIONS = frozenset({"reset", "observe", "act", "close"})


def _frame(request_id: str, *, ok: bool, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok}
    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error
    return {"protocol": PROTOCOL, "request_id": request_id, "ok": ok, "payload": payload}


def _handle(frame: object, *, closed: bool) -> tuple[dict[str, Any], bool]:
    if not isinstance(frame, dict):
        return _frame("unknown", ok=False, error="frame must be an object"), closed
    request_id = frame.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return _frame("unknown", ok=False, error="request_id is required"), closed
    if frame.get("protocol") != PROTOCOL:
        return _frame(request_id, ok=False, error="protocol mismatch"), closed
    operation = frame.get("operation")
    if operation not in OPERATIONS:
        return _frame(request_id, ok=False, error="unsupported operation"), closed
    if closed:
        return _frame(request_id, ok=False, error="worker is closed"), closed
    if operation == "close":
        return _frame(request_id, ok=True, result={"closed": True}), True
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return _frame(request_id, ok=False, error="payload must be an object"), closed
    # The environment-specific backend is injected behind this process boundary.
    # Until it is attached, the worker reports a deterministic unavailable state.
    return _frame(
        request_id,
        ok=True,
        result={"operation": operation, "backend": "unavailable", "payload": payload},
    ), closed


def main() -> int:
    closed = False
    for line in sys.stdin:
        try:
            frame = json.loads(line)
            response, closed = _handle(frame, closed=closed)
        except json.JSONDecodeError:
            response, closed = _frame("unknown", ok=False, error="invalid JSON"), closed
        sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        if closed:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
