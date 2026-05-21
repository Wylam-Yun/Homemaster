"""AgentState — mutable state for a GenericAgentRuntime execution.

AgentState holds only generic runtime/session fields. Home-domain state
(task_card, memory_hits, current_location, etc.) lives in domain-specific
objects passed through RunContext.deps.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Mutable state for a GenericAgentRuntime execution."""

    run_id: str = ""
    user_request: str = ""
    status: Literal["running", "replied", "tool_loop_completed", "failed"] = "running"
    turn_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    last_assistant_text: str | None = None
