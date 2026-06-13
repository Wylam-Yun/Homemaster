"""ToolResult — typed execution outcome.

Does NOT contain state_patch. Does NOT prescribe AgentState mutations.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Outcome of a tool execution."""

    success: bool
    tool_name: str = ""
    executor_mode: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    retryable: bool = False
    summary: str | None = None
