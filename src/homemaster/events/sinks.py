"""JsonlEventSink — append-only redacted events to JSONL file."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload


class JsonlEventSink:
    """Append-only event sink that writes to a JSONL file."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        """Append event to in-memory list and write to JSONL file."""
        self._events.append(event)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / "agent_runtime_events.jsonl"
        sanitized_payload = sanitize_event_payload(event.payload)
        entry = {
            "run_id": event.run_id,
            "event_id": event.event_id,
            "turn_index": event.turn_index,
            "event_type": event.event_type,
            "phase_label": event.phase_label,
            "status": event.status,
            "payload": sanitized_payload,
            "timestamp": event.timestamp,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @property
    def events(self) -> list[RuntimeEvent]:
        return list(self._events)
