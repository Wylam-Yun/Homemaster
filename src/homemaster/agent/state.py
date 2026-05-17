"""AgentState placeholder — expanded in Phase 4 (AgentRuntime MVP).

Currently a minimal stub. The full AgentState will track turn count,
tool call history, evidence refs, and failure records.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Mutable state for an AgentRuntime execution."""

    turn: int = 0
    tool_calls: list[dict] = Field(default_factory=list)
    failures: list[dict] = Field(default_factory=list)
