"""RuntimeEvent - structured event for AgentRuntime tracing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

# The benchmark environment currently runs Python 3.10, which lacks datetime.UTC.
_UTC = timezone.utc  # noqa: UP017

# All known event types. Used for documentation and test validation.
# RuntimeEvent.type is str for forward compatibility.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "runtime.turn_started",
        "runtime.turn_completed",
        "runtime.turn_failed",
        "runtime.budget_exhausted",
        "runtime.cancelled",
        "assistant.thinking",
        "assistant.reply",
        "context.compaction",
        "context.length_error",
        "usage.update",
        "transport.request_started",
        "transport.delta",
        "transport.response_completed",
        "transport.request_failed",
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "extension.hook_completed",
        "extension.cleanup_completed",
    }
)


@dataclass
class RuntimeEvent:
    """A single runtime event for tracing.

    When serializing to JSONL, include ALL fields — do not omit keys.
    Use null/"" for absent values per the "never omit" serialization rule.
    """

    type: str
    session_id: str
    run_id: str
    turn_index: int | None
    payload: dict[str, Any]
    tool_call_id: str | None = None
    name: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(_UTC).isoformat())
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    duration_ms: float | None = None
    gateway_generation: int | None = None


class EventSink(Protocol):
    """Protocol for append-only event writing."""

    def emit(self, event: RuntimeEvent) -> None: ...

    @property
    def events(self) -> list[RuntimeEvent]: ...
