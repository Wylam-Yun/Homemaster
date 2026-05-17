"""RuntimeEvent — structured event for AgentRuntime tracing.

Events are emitted per-turn: decision, tool_call, tool_result,
state_transition, error. EventSink is the append-only writer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol


@dataclass
class RuntimeEvent:
    """A single runtime event for tracing."""

    turn_index: int
    event_type: Literal[
        "decision", "tool_call", "tool_result", "state_transition", "error"
    ]
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    phase_label: str = ""
    status: str = ""


class EventSink(Protocol):
    """Protocol for append-only event writing."""

    def emit(self, event: RuntimeEvent) -> None: ...

    @property
    def events(self) -> list[RuntimeEvent]: ...
