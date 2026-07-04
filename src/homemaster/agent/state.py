"""AgentState — mutable runtime bookkeeping for the generic context architecture.

AgentState holds generic runtime/session counters. Home-domain state
(task_card, memory_hits, current_location, etc.) lives in domain-specific
objects passed through RunContext.deps.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentRunStatus = Literal["running", "waiting_user", "replied", "completed", "failed", "cancelled"]
CompactionKind = Literal["none", "micro", "summary", "reactive", "emergency", "manual"]


class ProviderUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class CompactionRecord(BaseModel):
    kind: CompactionKind = "none"
    before_tokens: int = 0
    after_tokens: int = 0
    reason: str = ""


class AgentState(BaseModel):
    """Mutable runtime state for a GenericAgentRuntime execution."""

    run_id: str = ""
    session_id: str = ""
    status: AgentRunStatus = "running"
    turn_index: int = 0
    iteration_index: int = 0
    total_model_calls: int = 0
    total_tool_calls: int = 0
    max_tool_iterations: int | None = None
    active_task_snapshot_id: str | None = None
    last_assistant_text: str | None = None
    last_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    last_tool_results_summary: list[dict[str, Any]] = Field(default_factory=list)
    consecutive_tool_errors: int = 0
    no_progress_iterations: int = 0
    last_progress_marker: str | None = None
    last_compaction: CompactionRecord | None = None
    estimated_context_tokens: int = 0
    provider_usage: ProviderUsage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def begin_iteration(self, iteration: int) -> None:
        self.iteration_index = iteration
        self.total_model_calls += 1

    def record_tool_results(self, summaries: list[dict[str, Any]]) -> None:
        self.total_tool_calls += len(summaries)
        previous_signature = _tool_result_signature(self.last_tool_results_summary)
        next_signature = _tool_result_signature(summaries)
        self.last_tool_results_summary = summaries
        if summaries and all(item.get("is_error") for item in summaries):
            self.consecutive_tool_errors += len(summaries)
        else:
            self.consecutive_tool_errors = 0
        if summaries and previous_signature == next_signature:
            self.no_progress_iterations += 1
        else:
            self.no_progress_iterations = 0


def _tool_result_signature(summaries: list[dict[str, Any]]) -> tuple[tuple[str, bool, str], ...]:
    return tuple(
        (
            str(item.get("name", "")),
            bool(item.get("is_error")),
            str(item.get("text", ""))[:300],
        )
        for item in summaries
    )
