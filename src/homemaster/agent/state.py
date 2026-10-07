"""AgentState — mutable runtime bookkeeping for the generic context architecture.

AgentState holds generic runtime/session counters. Home-domain state
(task_card, memory_hits, current_location, etc.) lives in domain-specific
objects passed through RunContext.deps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from homemaster.agent.messages import Message

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


class CompactionArtifact(BaseModel):
    """Durable record of a summary compaction fold.

    The canonical transcript (session.messages / engine context) stays
    append-only. The artifact stores the folded head verbatim — the newest
    summary message followed by the protected prefix — plus where the live
    tail starts in the canonical list and a hash of the folded prefix for
    staleness detection. Each prepare projects ``head + canonical[first_kept:]``.
    """

    kind: str = "summary"
    first_kept_index: int = 0
    prefix_hash: str = ""
    head_messages: list[dict[str, Any]] = Field(default_factory=list)

    def head_as_messages(self) -> list[Message]:
        from homemaster.agent.messages import (
            AssistantMessage,
            ToolResultMessage,
            UserMessage,
        )

        role_map = {
            "user": UserMessage,
            "assistant": AssistantMessage,
            "tool": ToolResultMessage,
        }
        restored: list[Message] = []
        for item in self.head_messages:
            cls = role_map.get(str(item.get("role")))
            if cls is None:
                continue
            restored.append(cls.model_validate(item))
        return restored


class UsageAnchor(BaseModel):
    """Usage-anchored incremental context estimation state.

    Instead of re-estimating the whole projected view every prepare, the
    assembler records which canonical span the *sent* view covered
    (``pending_view``); when the provider's real input-token count lands,
    it is promoted to ``usage_anchor``. The next prepare then costs only
    ``anchor.input_tokens`` plus a heuristic estimate of the canonical
    messages appended since — the model opencode/pi use. ``artifact_key``
    and ``tools_key`` invalidate the anchor when the fold shape or the
    projection environment changed; ``tail_key`` catches the last canonical
    message growing in place (AgentScope merged-Msg). ``fixed_est`` records
    the heuristic share of non-conversation input (system prompt + prelude
    + tool schemas) so a changed prelude/prompt is delta-adjusted rather
    than silently absorbed by the stale anchor.
    """

    input_tokens: int = 0
    canonical_len: int = 0
    artifact_key: str = ""
    tail_key: str = ""
    fixed_est: int = 0
    tools_key: str = ""
    # Fingerprint of the whole covered prefix EXCLUDING the tail message —
    # hermes' base_prefix_fp equivalent. tail_key handles the last covered
    # message separately so merged-Msg growth recounts as a delta instead
    # of busting the anchor into a full re-estimate; any mid-prefix rewrite
    # (strip, splice, rewind, external edit) fails the anchor closed.
    prefix_key: str = ""


class AgentState(BaseModel):
    """Mutable runtime state for an AgentRuntime execution."""

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
    compaction: CompactionArtifact | None = None
    estimated_context_tokens: int = 0
    provider_usage: ProviderUsage | None = None
    usage_anchor: UsageAnchor | None = None
    pending_view: UsageAnchor | None = None

    def note_view_usage(self, input_tokens: int) -> None:
        """Promote the pending view coverage into a usage anchor.

        Consumes ``pending_view`` — it describes exactly one sent request,
        so a second usage event must not re-promote it."""
        if self.pending_view is not None:
            self.usage_anchor = self.pending_view.model_copy(
                update={"input_tokens": input_tokens}
            )
            self.pending_view = None
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
