"""RuntimeEvent — structured event for AgentRuntime tracing.

Events are emitted per-turn: lifecycle, transport, tool call, and budget.
EventSink is the append-only writer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

# All known event types. Used for documentation and test validation.
# RuntimeEvent.event_type is str for forward compatibility — new event
# types can be added without changing the type annotation.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset({
    # Generic agent loop events
    "runtime.turn_started",
    "runtime.turn_completed",
    "runtime.turn_failed",
    "runtime.budget_exhausted",
    "runtime.cancelled",
    "transport.request_started",
    "transport.delta",
    "transport.response_completed",
    "transport.request_failed",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
})


@dataclass
class RuntimeEvent:
    """A single runtime event for tracing.

    All fields have defaults so existing constructor calls are unaffected.
    When serializing to JSONL, include ALL fields — do not omit keys.
    Use null/"" for absent values per the "never omit" serialization rule.
    """

    turn_index: int
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    duration_ms: float | None = None
    tool_name: str = ""
    executor_mode: str = ""


class EventSink(Protocol):
    """Protocol for append-only event writing."""

    def emit(self, event: RuntimeEvent) -> None: ...

    @property
    def events(self) -> list[RuntimeEvent]: ...
