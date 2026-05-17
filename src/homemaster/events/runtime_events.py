"""RuntimeEvent — structured event for AgentRuntime tracing.

Events are emitted per-turn and per-stage: lifecycle, decision,
tool_call, tool_result, state_transition, LLM/embedding boundary,
Stage05 execution, and recovery. EventSink is the append-only writer.
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
    # AgentRuntime lifecycle
    "run_started", "run_completed", "run_failed",
    # AgentRuntime per-turn
    "turn_started", "context_built",
    # AgentRuntime decision
    "decision_started", "decision_completed", "decision_failed",
    # AgentRuntime tool call lifecycle
    "tool_call_validated", "tool_call_rejected",
    "tool_call_started", "tool_call_completed", "tool_call_failed",
    # AgentRuntime state
    "state_transitioned", "finish_decision_received", "max_turns_exceeded",
    # Pipeline / stage lifecycle
    "stage_started", "stage_completed", "stage_failed",
    # LLM boundary
    "llm_call_started", "llm_call_completed", "llm_call_failed",
    # Embedding boundary
    "embedding_call_started", "embedding_call_completed", "embedding_call_failed",
    # Stage05 execution
    "planning_completed", "step_decision_generated",
    "subtask_started", "subtask_completed", "subtask_failed",
    "skill_call_started", "skill_call_completed", "skill_call_failed",
    "verification_started", "verification_completed", "verification_failed",
    # Recovery
    "recovery_started", "recovery_decision_generated",
    "recovery_completed", "recovery_failed",
    # Legacy compat (5 original event types)
    "decision", "tool_call", "tool_result", "state_transition", "error",
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
    phase_label: str = ""
    status: str = ""
    # Phase 8 additions — structural metadata
    duration_ms: float | None = None
    source: str = ""  # "agent_runtime" or "pipeline" to distinguish run_* events
    stage: str = ""  # stage name for pipeline events
    subtask_id: str = ""
    skill_name: str = ""
    provider_name: str = ""
    attempt: int | None = None
    parent_event_id: str = ""
    tool_name: str = ""
    executor_mode: str = ""
    state_status: str = ""
    failure_record_id: str = ""


class EventSink(Protocol):
    """Protocol for append-only event writing."""

    def emit(self, event: RuntimeEvent) -> None: ...

    @property
    def events(self) -> list[RuntimeEvent]: ...
