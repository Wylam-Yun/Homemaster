"""AgentState — mutable state for an AgentRuntime execution.

AgentState is the sole core state for the runtime. All tool results
flow through StateUpdater to update AgentState fields.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Mutable state for an AgentRuntime execution."""

    run_id: str = ""
    user_request: str = ""
    task_card: dict[str, Any] | None = None
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    target_candidates: list[dict[str, Any]] = Field(default_factory=list)
    current_location: str | None = None
    current_object: str | None = None
    holding_object: str | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    verifications: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    active_skills: list[str] = Field(default_factory=list)
    loaded_skill_contexts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    memory_context_snapshot: str | None = None
    user_context_snapshot: str | None = None
    status: Literal["running", "completed", "failed"] = "running"
    turn_index: int = 0
    runtime_settings: dict[str, Any] | None = None
