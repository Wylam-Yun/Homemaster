"""Local-first runtime event sink with best-effort HTTP mirroring."""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient
from homemaster.benchmarking.coworker_demo.presentation import (
    ProjectionError,
    project_runtime_event,
)
from homemaster.events.trace import json_compatible_copy

_PROJECTED_EVENTS = {
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
    "assistant.reply",
    "runtime.turn_completed",
    "runtime.turn_failed",
}
_MAX_MIRROR_FAILURES = 32


class CoworkerTraceSink:
    """Serialize local writes and mirror projected events in local append order."""

    def __init__(
        self,
        path: Path,
        client: EnvironmentClient,
        run_id: str,
        *,
        transcript_path: Path | None = None,
    ) -> None:
        self.path = path
        self.client = client
        self.run_id = run_id
        self.transcript_path = transcript_path
        self.mirror_failures: deque[str] = deque(maxlen=_MAX_MIRROR_FAILURES)
        self.mirror_failure_total = 0
        self._emit_lock = threading.RLock()
        self._mirror_order = threading.Condition()
        self._next_mirror_ticket = 0
        self._mirror_turn = 0
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Any) -> None:
        projected: dict[str, Any] | None = None
        mirror_ticket: int | None = None
        with self._emit_lock:
            payload = json_compatible_copy(asdict(event))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                handle.flush()
            if self.transcript_path is not None:
                self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
                with self.transcript_path.open("a", encoding="utf-8") as transcript:
                    transcript.write(self._transcript_line(payload) + "\n")
                    transcript.flush()
            try:
                if event.type in _PROJECTED_EVENTS and event.run_id != self.run_id:
                    raise ProjectionError("trace sink run identity mismatch")
                projected = project_runtime_event(event)
                if event.type == "assistant.reply" and projected is None:
                    raise ProjectionError("unsafe public reply rejected")
            except Exception as exc:
                self._record_mirror_failure(exc)
            else:
                if projected is not None:
                    mirror_ticket = self._next_mirror_ticket
                    self._next_mirror_ticket += 1
        if projected is not None and mirror_ticket is not None:
            self._mirror_presentation(mirror_ticket, projected)

    def _mirror_presentation(self, ticket: int, projected: dict[str, Any]) -> None:
        with self._mirror_order:
            while ticket != self._mirror_turn:
                self._mirror_order.wait()
        try:
            self.client.presentation_event(self.run_id, projected)
        except Exception as exc:
            self._record_mirror_failure(exc)
        finally:
            with self._mirror_order:
                self._mirror_turn += 1
                self._mirror_order.notify_all()

    def _record_mirror_failure(self, exc: Exception) -> None:
        with self._emit_lock:
            self.mirror_failure_total += 1
            failure_type = type(exc).__name__
            if not failure_type.isascii() or not failure_type.isidentifier():
                failure_type = "MirrorError"
            self.mirror_failures.append(f"{failure_type[:48]}: presentation mirror failed")

    @staticmethod
    def _transcript_line(payload: dict[str, Any]) -> str:
        event_type = str(payload.get("type") or "event")
        name = str(payload.get("name") or "")
        event_payload = payload.get("payload") or {}
        if event_type == "tool.call_started":
            return (
                f"TOOL {name}: {json.dumps(event_payload.get('arguments', {}), ensure_ascii=False)}"
            )
        if event_type in {"tool.call_completed", "tool.call_failed"}:
            return f"RESULT {name}: {str(event_payload.get('result') or '')[:800]}"
        if event_type == "assistant.reply":
            return f"MODEL: {event_payload.get('reply', '')}"
        if event_type == "assistant.thinking":
            return "MODEL: working through the change procedure"
        return event_type
