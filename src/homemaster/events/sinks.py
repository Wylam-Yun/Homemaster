"""Event sinks — JsonlEventSink, NullEventSink, ConsoleProgressEventSink, FanoutEventSink."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload


class JsonlEventSink:
    """Append-only event sink that writes to a JSONL file.

    Serializes ALL RuntimeEvent fields (never omits keys).
    Sanitizes payload via sanitize_event_payload.
    """

    def __init__(self, output_dir: Path, *, filename: str = "runtime_events.jsonl") -> None:
        self._output_dir = output_dir
        self._filename = filename
        self._events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        """Append event to in-memory list and write to JSONL file."""
        self._events.append(event)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / self._filename
        entry = _event_to_dict(event)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @property
    def events(self) -> list[RuntimeEvent]:
        return list(self._events)


class NullEventSink:
    """No-op sink. Accepts events silently, returns empty list."""

    def emit(self, event: RuntimeEvent) -> None:
        pass

    @property
    def events(self) -> list[RuntimeEvent]:
        return []


# High-level event types shown by ConsoleProgressEventSink.
_PROGRESS_EVENT_TYPES: frozenset[str] = frozenset({
    "runtime.turn_started",
    "runtime.turn_completed",
    "runtime.turn_failed",
    "runtime.budget_exhausted",
    "transport.request_started",
    "transport.response_completed",
    "transport.request_failed",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
})


class ConsoleProgressEventSink:
    """Wraps another sink, prints high-level events to stderr.

    Only prints events whose type is in _PROGRESS_EVENT_TYPES
    or whose type ends with '_failed'. Output goes to stderr
    so it does not interfere with stdout piping.
    """

    def __init__(self) -> None:
        self._console: Any = None

    def _get_console(self) -> Any:
        if self._console is None:
            from rich.console import Console
            self._console = Console(file=sys.stderr, highlight=False)
        return self._console

    def emit(self, event: RuntimeEvent) -> None:
        if event.type in _PROGRESS_EVENT_TYPES or event.type.endswith("_failed"):
            console = self._get_console()
            parts = [f"[bold]{event.type}[/bold]"]
            if event.name:
                parts.append(f"name={event.name}")
            if event.tool_call_id:
                parts.append(f"tool_call_id={event.tool_call_id}")
            if event.duration_ms is not None:
                parts.append(f"{event.duration_ms:.0f}ms")
            console.print(" ".join(parts))

    @property
    def events(self) -> list[RuntimeEvent]:
        return []


class FanoutEventSink:
    """Forwards emit() to all wrapped sinks. events from first sink."""

    def __init__(self, sinks: list[Any]) -> None:
        self._sinks = sinks

    def emit(self, event: RuntimeEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)

    @property
    def events(self) -> list[RuntimeEvent]:
        if self._sinks:
            return self._sinks[0].events
        return []


def _event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    """Serialize RuntimeEvent to dict with all fields and sanitized payload."""
    entry = asdict(event)
    entry["payload"] = sanitize_event_payload(event.payload)
    return entry
