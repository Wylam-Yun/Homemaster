"""Context assembly — items, budget, providers, and assembler."""

from __future__ import annotations

import hashlib
import inspect
import json
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Protocol

from homemaster.agent.compact import (
    build_compaction_summary_message,
    microcompact_tool_results_by_type,
    repair_tool_pairs_indexed,
    split_preserving_recent_context,
    strip_old_images,
)
from homemaster.agent.context_projection import project_model_tool_context
from homemaster.agent.messages import ContentBlock, Message, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import (
    AgentState,
    CompactionArtifact,
    CompactionRecord,
    UsageAnchor,
)
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.providers.token_estimator import (
    TokenEstimator,
    estimate_text_tokens_rough,
    make_default_estimator,
)
from homemaster.task_state.store import TaskStateStore


class ContextPriority(StrEnum):
    REQUIRED = "required"
    IMPORTANT = "important"
    AUXILIARY = "auxiliary"
    TRACE_ONLY = "trace_only"


class ContextFreshness(StrEnum):
    CURRENT = "current"
    RECENT = "recent"
    OLD = "old"
    ARCHIVED = "archived"


class ContextPlacement(StrEnum):
    SYSTEM_PROMPT = "system_prompt"
    CONTEXT_PRELUDE = "context_prelude"
    CONVERSATION = "conversation"
    TOOL_SCHEMA = "tool_schema"
    TRACE_ONLY = "trace_only"


class RenderMode(StrEnum):
    FULL = "full"
    COMPACT = "compact"
    SUMMARY = "summary"
    POINTER = "pointer"


RenderedContext = str | list[Message]


@dataclass(frozen=True)
class ContextItem:
    id: str
    kind: str
    priority: ContextPriority
    freshness: ContextFreshness
    placement: ContextPlacement
    token_estimate: int
    render: Callable[[RenderMode], RenderedContext]
    group_id: str | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    mode: RenderMode = RenderMode.FULL


class BudgetDecision(Enum):
    NO_COMPACT = "no_compact"
    COMPACT = "compact"


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(
        1,
        estimate_text_tokens_rough(text),
    )


def estimate_json_tokens(value: object) -> int:
    return estimate_text_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True))


@dataclass(frozen=True)
class ContextBudget:
    context_window_tokens: int
    output_reserve_tokens: int
    threshold_ratio: float = 0.50
    tail_token_ratio: float = 0.10
    safety_buffer_tokens: int = 13_000
    token_estimation_padding: float = 4 / 3

    @property
    def compaction_threshold_tokens(self) -> int:
        ratio_threshold = int(self.context_window_tokens * self.threshold_ratio)
        hard_cap = (
            self.context_window_tokens - self.output_reserve_tokens - self.safety_buffer_tokens
        )
        return max(1, min(ratio_threshold, hard_cap))

    @property
    def recent_tail_budget_tokens(self) -> int:
        return max(1, int(self.compaction_threshold_tokens * self.tail_token_ratio))

    def padded(self, tokens: int) -> int:
        return int(tokens * self.token_estimation_padding)

    def should_compact(self, estimated_input_tokens: int) -> BudgetDecision:
        if estimated_input_tokens >= self.compaction_threshold_tokens:
            return BudgetDecision.COMPACT
        return BudgetDecision.NO_COMPACT


class ContextProvider(Protocol):
    name: str

    def collect(self) -> list[ContextItem]: ...


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class TaskStateSnapshotProvider:
    name = "task_state_snapshot"

    def __init__(self, store: TaskStateStore | None) -> None:
        self._store = store

    def collect(self) -> list[ContextItem]:
        snapshot = self._store.snapshot if self._store else None
        if snapshot is None:
            return []
        if snapshot.status.value == "completed":
            visible = snapshot.to_completed_model_summary_dict()
        else:
            visible = snapshot.to_model_visible_dict()
        text = "# Task State Snapshot\n" + _json_text(visible)
        priority = (
            ContextPriority.REQUIRED
            if visible.get("status") == "active"
            else ContextPriority.IMPORTANT
        )
        return [
            ContextItem(
                id="task_state_snapshot",
                kind="task_state_snapshot",
                priority=priority,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
                mode=RenderMode.FULL,
            )
        ]


class RuntimeBudgetStatusProvider:
    name = "runtime_budget_status"

    def __init__(self, state: AgentState) -> None:
        self._state = state

    def collect(self) -> list[ContextItem]:
        payload = {
            "type": "runtime_budget_status",
            "iteration_index": self._state.iteration_index,
            "max_tool_iterations": self._state.max_tool_iterations,
            "consecutive_tool_errors": self._state.consecutive_tool_errors,
            "no_progress_iterations": self._state.no_progress_iterations,
            "estimated_context_tokens": self._state.estimated_context_tokens,
            "last_compaction": (
                self._state.last_compaction.kind
                if self._state.last_compaction is not None
                else "none"
            ),
        }
        text = "# Runtime Budget Status\n" + _json_text(payload)
        return [
            ContextItem(
                id="runtime_budget_status",
                kind="runtime_budget_status",
                priority=ContextPriority.IMPORTANT,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
                mode=RenderMode.FULL,
            )
        ]


class FailureSummaryProvider:
    name = "failure_summary"

    def __init__(self, state: AgentState) -> None:
        self._state = state

    def collect(self) -> list[ContextItem]:
        errors = [r for r in self._state.last_tool_results_summary if r.get("is_error")]
        if not errors:
            return []
        payload = {
            "type": "failure_summary",
            "active_failures": [
                {
                    "tool": r.get("name", "unknown"),
                    "reason": r.get("text", "")[:200],
                    "attempts": 1,
                }
                for r in errors[:3]
            ],
            "consecutive_tool_errors": self._state.consecutive_tool_errors,
        }
        text = "# Failure Summary\n" + _json_text(payload)
        return [
            ContextItem(
                id="failure_summary",
                kind="failure_summary",
                priority=ContextPriority.IMPORTANT,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
                mode=RenderMode.FULL,
            )
        ]


class ConversationProvider:
    name = "conversation"

    def __init__(self, session: AgentSession) -> None:
        self._session = session

    def collect(self) -> list[ContextItem]:
        messages = self._session.messages
        if not messages:
            return []
        return [
            ContextItem(
                id="conversation",
                kind="conversation",
                priority=ContextPriority.REQUIRED,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONVERSATION,
                # token_estimate is a write-only field — the real estimate
                # is computed once per prepare on the projected view, so an
                # O(history) scan here would be pure waste.
                token_estimate=0,
                render=lambda _mode, msgs=messages: msgs,
            )
        ]


class AvailableSkillsProvider:
    """Render the OpenHarness progressive-disclosure Skill index."""

    name = "skills"

    def __init__(self, registry: Any | None) -> None:
        self._registry = registry

    def collect(self) -> list[ContextItem]:
        if self._registry is None:
            return []
        refresh = getattr(self._registry, "refresh", None)
        if callable(refresh):
            refresh()
        list_skills = getattr(self._registry, "list_skills", None)
        if not callable(list_skills):
            return []
        skills = [skill for skill in list_skills() if not skill.disable_model_invocation]
        if not skills:
            return []
        lines = [
            "# Available Skills",
            "",
            "Use `load_skill(name=...)` to load a Skill's full instructions before acting on it.",
            "",
        ]
        for skill in skills:
            command_name = skill.command_name or skill.name
            display = f" ({skill.display_name})" if skill.display_name else ""
            lines.append(f"- **{command_name}**{display}: {skill.description}")
        text = "\n".join(lines)
        return [
            ContextItem(
                id="available_skills",
                kind="available_skills",
                priority=ContextPriority.IMPORTANT,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
            )
        ]


@dataclass
class ContextMetrics:
    estimated_tokens: int
    compaction_triggered: bool = False
    compaction_kind: str = "none"


@dataclass
class ComposedContext:
    messages: list[Message]
    system_prompt: str
    tools: list[dict] | None
    metrics: ContextMetrics
    automatic_recalled_memories: tuple[Any, ...] = ()


class ContextAssembler:
    def __init__(
        self,
        *,
        provider: ProviderProfileConfig,
        policy: ContextPolicyConfig,
        system_prompt: str,
        summary_client: Any = None,
        skill_registry: Any | None = None,
        frozen_memory_context: Any | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._system_prompt = system_prompt
        self._estimator = make_default_estimator(provider)
        self._summary_client = summary_client
        self._skill_registry = skill_registry
        self._frozen_memory_context = frozen_memory_context
        self._automatic_memory_context: str | None = None
        self._automatic_recalled_memories: tuple[Any, ...] = ()
        self._working_directory: Path | None = None

    def bind_working_directory(self, working_directory: Path) -> None:
        resolved = working_directory.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("working directory must be a directory")
        self._working_directory = resolved

    def bind_automatic_memory_context(self, text: str | None) -> None:
        normalized = text.strip() if isinstance(text, str) else ""
        self._automatic_memory_context = normalized or None

    def bind_automatic_recalled_memories(self, memories: tuple[Any, ...]) -> None:
        self._automatic_recalled_memories = tuple(memories)

    def _session_system_prompt(self, session_id: str) -> str:
        parts = [self._system_prompt]
        if self._working_directory is not None:
            path = json.dumps(str(self._working_directory), ensure_ascii=False)
            parts.append(
                f"Current workspace: {path}.\n"
                "Relative paths passed to terminal and file tools resolve from this workspace "
                "unless a tool call explicitly supplies another working directory.\n"
                "A host, device, environment, or project named by the user is not automatically "
                "this workspace. Do not operate on the current workspace on behalf of that target "
                "unless model-visible evidence explicitly connects them."
            )
        if self._frozen_memory_context is not None:
            memory = self._frozen_memory_context.snapshot(session_id).strip()
            if memory:
                parts.append(memory)
        return "\n\n".join(parts)

    def _budget(self) -> ContextBudget:
        return ContextBudget(
            context_window_tokens=self._provider.context_window_tokens,
            output_reserve_tokens=self._policy.output_reserve_tokens,
            threshold_ratio=self._policy.compression_threshold_ratio,
            tail_token_ratio=self._policy.tail_token_ratio,
            safety_buffer_tokens=self._policy.safety_buffer_tokens,
            token_estimation_padding=self._policy.token_estimation_padding,
        )

    async def aprepare(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore | None,
        tools: list[dict] | None,
        force_compact: str | bool | None = None,
    ) -> ComposedContext:
        """Assemble context without blocking the application event loop."""
        system_prompt = self._session_system_prompt(session.session_id)

        providers = self._build_providers(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state_store,
        )
        items = [
            item
            for provider in providers
            for item in provider.collect()
            if item.placement is not ContextPlacement.TRACE_ONLY
        ]
        prelude_texts: list[str] = []
        if self._automatic_memory_context is not None:
            prelude_texts.append(self._automatic_memory_context)
        conversation_messages: list[Message] = session.messages
        for item in items:
            rendered = item.render(item.mode)
            if item.placement is ContextPlacement.CONTEXT_PRELUDE and isinstance(rendered, str):
                prelude_texts.append(rendered)
            elif item.placement is ContextPlacement.CONVERSATION and isinstance(rendered, list):
                conversation_messages = rendered

        canonical_messages = session.messages
        conversation_view, view_offset, view_head_len, coords_ok = (
            self._projected_conversation_view(
                agent_state=agent_state,
                rendered=conversation_messages,
                canonical=canonical_messages,
            )
        )
        conversation_view, view_kept = repair_tool_pairs_indexed(conversation_view)
        # repair may drop orphans — the head boundary and fold coordinates
        # must be expressed in post-repair positions. view_kept maps a
        # post-repair view index back to the pre-repair view, which
        # view_offset then maps onto canonical indices.
        repaired_head_len = bisect_left(view_kept, view_head_len)
        if repaired_head_len:
            # Head is the durable verbatim fold output — hygiene applies to
            # the raw canonical tail only. Re-running micro over the head
            # would re-summarize already-stubbed tool results (the
            # summarizers are not idempotent).
            head_view = conversation_view[:repaired_head_len]
            tail_view, _ = strip_old_images(
                conversation_view[repaired_head_len:],
                keep_recent_images=self._policy.keep_recent_images,
            )
            tail_view, _ = microcompact_tool_results_by_type(
                tail_view,
                keep_recent_per_type=dict(self._policy.keep_recent_tool_results_per_type),
                default_keep_recent=self._policy.default_keep_recent_tool_results,
            )
            conversation_view = [*head_view, *tail_view]
        conversation_messages = project_model_tool_context(
            conversation_view,
            tools=tools,
        )
        fixed_est = (
            self._estimator.estimate_text(system_prompt)
            + sum(self._estimator.estimate_text(text) for text in prelude_texts)
            + estimate_tools_tokens(tools)
        )
        estimated = self._estimate_input(
            agent_state=agent_state,
            fixed_est=fixed_est,
            conversation_messages=conversation_messages,
            canonical_messages=canonical_messages,
            tools=tools,
        )
        budget = self._budget()
        padded = budget.padded(estimated)
        agent_state.estimated_context_tokens = padded
        compaction_triggered = False
        compaction_kind = "none"

        force_requested = bool(force_compact)
        should_auto_compact = (
            self._policy.auto_compact_enabled
            and budget.should_compact(padded) is BudgetDecision.COMPACT
        )
        if force_requested or should_auto_compact:
            before_tokens = padded
            force_mode = str(force_compact) if force_compact else ""
            compaction_ran, kind, out_messages, fold_upto, head_len = await self._acompact(
                messages=conversation_view,
                budget=budget,
                aggressive=force_mode in {"aggressive", "manual"},
                force_summary=force_mode == "manual",
                view_head_len=repaired_head_len,
                view_presanitized=view_head_len > 0,
            )
            if compaction_ran:
                conversation_view = out_messages
                conversation_messages = project_model_tool_context(
                    conversation_view,
                    tools=tools,
                )
                compaction_kind = (
                    f"manual_{kind}"
                    if force_mode == "manual"
                    else f"reactive_{kind}"
                    if force_requested
                    else kind
                )
                after_estimate = estimate_messages_tokens(
                    conversation_messages,
                    estimator=self._estimator,
                )
                after_estimate += self._estimator.estimate_text(system_prompt)
                after_estimate += sum(self._estimator.estimate_text(text) for text in prelude_texts)
                after_estimate += estimate_tools_tokens(tools)
                after_tokens = budget.padded(after_estimate)
                padded = after_tokens
                if kind == "summary" and fold_upto is not None and coords_ok:
                    first_kept = (
                        view_kept[fold_upto] + view_offset
                        if fold_upto < len(view_kept)
                        else len(canonical_messages)
                    )
                    agent_state.compaction = CompactionArtifact(
                        first_kept_index=first_kept,
                        prefix_hash=_hash_messages(canonical_messages[:first_kept]),
                        head_messages=[
                            m.model_dump(mode="json") for m in out_messages[:head_len]
                        ],
                    )
                    compaction_triggered = True
                    if force_mode == "manual":
                        record_kind = "manual"
                        record_reason = "manual"
                    elif force_requested:
                        record_kind = "reactive"
                        record_reason = "provider_context_length"
                    else:
                        record_kind = "summary"
                        record_reason = "threshold"
                    agent_state.last_compaction = CompactionRecord(
                        kind=record_kind,
                        before_tokens=before_tokens,
                        after_tokens=after_tokens,
                        reason=record_reason,
                    )

        agent_state.pending_view = UsageAnchor(
            canonical_len=len(canonical_messages),
            artifact_key=_artifact_key(agent_state.compaction),
            tail_key=_tail_key(canonical_messages),
            fixed_est=fixed_est,
            tools_key=_tools_key(tools),
            prefix_key=_prefix_key(canonical_messages),
        )
        return ComposedContext(
            messages=self._render_messages(
                prelude_texts=prelude_texts,
                conversation_messages=conversation_messages,
            ),
            system_prompt=system_prompt,
            tools=tools,
            metrics=ContextMetrics(
                estimated_tokens=padded,
                compaction_triggered=compaction_triggered,
                compaction_kind=compaction_kind,
            ),
            automatic_recalled_memories=self._automatic_recalled_memories,
        )

    def _build_providers(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore | None,
    ) -> list[ContextProvider]:
        by_name: dict[str, ContextProvider] = {
            ConversationProvider.name: ConversationProvider(session),
            TaskStateSnapshotProvider.name: TaskStateSnapshotProvider(task_state_store),
            RuntimeBudgetStatusProvider.name: RuntimeBudgetStatusProvider(agent_state),
            FailureSummaryProvider.name: FailureSummaryProvider(agent_state),
            AvailableSkillsProvider.name: AvailableSkillsProvider(self._skill_registry),
        }
        return [
            provider
            for name in self._policy.enabled_providers
            if (provider := by_name.get(name)) is not None
        ]

    @staticmethod
    def _render_messages(
        *,
        prelude_texts: list[str],
        conversation_messages: list[Message],
    ) -> list[Message]:
        if not prelude_texts:
            return conversation_messages
        return [
            UserMessage(
                content=[
                    ContentBlock(
                        text="# Runtime Context\n"
                        + "\n\n".join(prelude_texts)
                        + "\n\nThis runtime context is not a new user request."
                    )
                ]
            ),
            *conversation_messages,
        ]

    def _estimate_input(
        self,
        *,
        agent_state: AgentState,
        fixed_est: int,
        conversation_messages: list[Message],
        canonical_messages: list[Message],
        tools: list[dict] | None,
    ) -> int:
        """Estimate the model-visible input for this prepare.

        Anchored path (opencode/pi model): the previous call's real
        ``input_tokens`` already covers system prompt, tools, and the whole
        covered canonical span — only the canonical messages appended since
        plus the fixed-share delta (system prompt + prelude + tool schemas)
        need a heuristic estimate. Falls back to a full-view heuristic when
        there is no anchor, the fold shape moved (a new artifact means the
        sent view no longer decomposes as anchor-plus-delta), or the tool
        set changed (tool availability re-projects covered messages too).
        ``prefix_key`` (hermes' ``base_prefix_fp`` equivalent) fingerprints
        every covered message except the tail, so a mid-list rewrite fails
        closed into the full estimate while ``tail_key`` still lets the
        merged-Msg tail reprice as a delta. ``fixed_est`` is the heuristic
        estimate of the non-conversation share of this prepare's input.
        """
        anchor = agent_state.usage_anchor
        anchored = (
            anchor is not None
            and anchor.input_tokens > 0
            and anchor.canonical_len <= len(canonical_messages)
            and anchor.artifact_key == _artifact_key(agent_state.compaction)
            and anchor.tools_key == _tools_key(tools)
            # O(N) fingerprint runs last — the cheap keys above short-circuit.
            and anchor.prefix_key
            == _prefix_key(canonical_messages[: anchor.canonical_len])
        )
        if anchored:
            delta_start = anchor.canonical_len
            if (
                delta_start > 0
                and anchor.tail_key
                and _hash_messages(canonical_messages[delta_start - 1 : delta_start])
                != anchor.tail_key
            ):
                # AgentScope merges reply rounds into the same Msg — the
                # anchored last message grew in place, so re-count it.
                delta_start -= 1
            estimated = (
                anchor.input_tokens
                + self._estimator.estimate_messages(canonical_messages[delta_start:])
                + fixed_est
                - anchor.fixed_est
            )
        else:
            estimated = fixed_est + self._estimator.estimate_messages(
                conversation_messages
            )
        return max(1, estimated)

    def _projected_conversation_view(
        self,
        *,
        agent_state: AgentState,
        rendered: list[Message],
        canonical: list[Message],
    ) -> tuple[list[Message], int, int, bool]:
        """Overlay the durable compaction artifact onto the conversation.

        Returns ``(view, view_offset, view_head_len, coords_ok)``. With an
        applied artifact the view is ``head_messages + canonical[first_kept:]``;
        ``view_offset`` maps view indices to canonical indices
        (``canonical_index = view_index + view_offset``) and ``view_head_len``
        is the head segment length. ``coords_ok`` is False when the provider's
        rendered conversation diverged from the canonical mirror — artifact
        reads AND writes are both unsafe in that case.
        """
        artifact = agent_state.compaction
        if len(rendered) != len(canonical):
            return list(rendered), 0, 0, False
        if artifact is None or not artifact.head_messages:
            return list(canonical), 0, 0, True
        first_kept = artifact.first_kept_index
        if first_kept <= 0 or first_kept > len(canonical):
            agent_state.compaction = None
            return list(canonical), 0, 0, True
        if _hash_messages(canonical[:first_kept]) != artifact.prefix_hash:
            agent_state.compaction = None
            return list(canonical), 0, 0, True
        try:
            head = artifact.head_as_messages()
        except Exception:
            # A corrupt/unmigratable head must degrade to full canonical —
            # artifacts are an optimisation, never a hard dependency.
            agent_state.compaction = None
            return list(canonical), 0, 0, True
        view = [*head, *canonical[first_kept:]]
        return view, first_kept - len(head), len(head), True

    def _accept_summary(self, message, *, fallback: str) -> str:
        """A tool-free summary call must end in ``stop`` — anything else
        (length/refusal/content_filter/stray tool_calls) produced a partial
        or off-task result that must not become a durable checkpoint
        (pi refuses stopReason=="length" the same way)."""
        finish = getattr(message, "finish_reason", None)
        if finish is not None and finish != "stop":
            if self._policy.abort_on_summary_failure:
                raise RuntimeError(
                    f"context compaction aborted: summary finish={finish}"
                )
            return fallback
        text = getattr(message, "text", "") or ""
        if text.strip():
            return text.strip()
        if self._policy.abort_on_summary_failure:
            raise RuntimeError("context compaction aborted: summary returned empty")
        return fallback

    async def _acompact(
        self,
        *,
        messages: list[Message],
        budget: ContextBudget,
        aggressive: bool = False,
        force_summary: bool = False,
        view_head_len: int = 0,
        view_presanitized: bool = False,
    ) -> tuple[bool, str, list[Message], int | None, int]:
        """Fold the oldest region of ``messages`` behind a summary head.

        Pure transform — the caller owns persistence (compaction artifact);
        the session mirror is never written. ``view_head_len`` is the head
        boundary expressed in ``messages`` coordinates: positions below it
        came from a prior compaction head, positions at/above it are live
        canonical tail. ``view_presanitized`` marks that the caller already
        applied stage-1 hygiene (strip/micro) to this view — the stage is
        skipped so stub text is never re-summarized. Returns
        ``(ran, kind, out_messages, fold_upto, head_len)`` where
        ``fold_upto`` is the ``messages`` index of the first surviving
        canonical-tail message (``len(messages)`` when the whole tail was
        dropped by repair) and ``head_len`` is the prefix of
        ``out_messages`` that forms the durable head segment. Both are
        meaningful only for ``kind == "summary"``.
        """
        view_len = len(messages)
        stage1_messages = messages
        stripped_images = 0
        saved_tool_tokens = 0
        if not view_presanitized:
            # The caller pre-sanitizes artifact-projected views — re-running
            # strip/micro here would double-wrap tool stubs into the durable
            # head (summarize_tool_result is not idempotent).
            stage1_messages, stripped_images = strip_old_images(
                messages,
                keep_recent_images=self._policy.keep_recent_images,
            )
            stage1_messages, saved_tool_tokens = microcompact_tool_results_by_type(
                stage1_messages,
                keep_recent_per_type=dict(self._policy.keep_recent_tool_results_per_type),
                default_keep_recent=self._policy.default_keep_recent_tool_results,
            )
        msgs_to_view: list[int] | None = None
        if stripped_images or saved_tool_tokens:
            stage1_messages, msgs_to_view = repair_tool_pairs_indexed(stage1_messages)
            stage1_estimate = estimate_messages_tokens(
                stage1_messages,
                estimator=self._estimator,
            )
            if (
                not force_summary
                and budget.should_compact(budget.padded(stage1_estimate))
                is not BudgetDecision.COMPACT
            ):
                return True, "micro", stage1_messages, None, 0
            messages = stage1_messages

        tail_ratio = (
            self._policy.aggressive_tail_token_ratio
            if aggressive
            else self._policy.tail_token_ratio
        )
        protect_first_n = (
            self._policy.aggressive_protect_first_n if aggressive else self._policy.protect_first_n
        )
        preserve_count = (
            1
            if force_summary
            else _tail_message_count_for_budget(
                messages,
                tail_token_budget=max(
                    1,
                    int(budget.compaction_threshold_tokens * tail_ratio),
                ),
                estimator=self._estimator,
                min_messages=1,
            )
        )
        older, recent = split_preserving_recent_context(
            messages,
            preserve_recent_messages=preserve_count,
            protect_first_n=protect_first_n,
        )
        if not older:
            if stripped_images or saved_tool_tokens:
                return True, "micro", messages, None, 0
            return False, "none", messages, None, 0

        summary = await self._abuild_summary(older=older, recent=recent)
        compacted_pre = [build_compaction_summary_message(summary), *recent]
        compacted_messages, kept = repair_tool_pairs_indexed(compacted_pre)
        compacted_messages, _ = strip_old_images(
            compacted_messages,
            keep_recent_images=self._policy.keep_recent_images,
        )
        split_index = len(messages) - len(recent) + protect_first_n
        fold_upto, head_len = _fold_geometry(
            split_index=split_index,
            msgs_len=len(messages),
            msgs_to_view=msgs_to_view,
            view_len=view_len,
            view_head_len=view_head_len,
            protect_first_n=protect_first_n,
            kept=kept,
            out_len=len(compacted_messages),
        )
        return True, "summary", compacted_messages, fold_upto, head_len

    async def _abuild_summary(
        self,
        *,
        older: list[Message],
        recent: list[Message],
    ) -> str:
        fallback = f"[Summary unavailable. {len(older)} messages omitted]"
        if not self._policy.enable_llm_summary or self._summary_client is None:
            if self._policy.abort_on_summary_failure:
                raise RuntimeError("context compaction aborted: summary client unavailable")
            return fallback
        try:
            from homemaster.prompts.loader import PromptId, load_prompt

            prompt = _render_summary_source(older=older, recent=recent)
            value = self._summary_client_complete(
                prompt=prompt,
                system_prompt=load_prompt(PromptId.COMPACT_SUMMARY),
            )
            message = await value if inspect.isawaitable(value) else value
        except Exception as exc:
            if self._policy.abort_on_summary_failure:
                raise RuntimeError("context compaction aborted: summary failed") from exc
            return fallback
        return self._accept_summary(message, fallback=fallback)

    def _summary_client_complete(self, *, prompt: str, system_prompt: str):
        budget = self._policy.summary_max_output_tokens
        provider_cap = getattr(self._provider, "max_output_tokens", None)
        if isinstance(provider_cap, int) and provider_cap > 0:
            # pi clamps the summary budget to the model's declared max —
            # asking the wire for more than the model can emit just errors.
            budget = min(budget, provider_cap)
        try:
            return self._summary_client.complete(
                [UserMessage.from_text(prompt)],
                system_prompt=system_prompt,
                max_output_tokens=budget,
                temperature=0.0,
            )
        except TypeError:
            return self._summary_client.complete(
                [UserMessage.from_text(prompt)],
                system_prompt=system_prompt,
            )


def estimate_messages_tokens(
    messages: list[Message],
    *,
    estimator: TokenEstimator | None = None,
) -> int:
    if estimator is not None:
        return estimator.estimate_messages(messages)
    total = sum(
        estimate_text_tokens(block.text)
        for message in messages
        for block in message.content
        if block.text
    )
    for message in messages:
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            total += estimate_text_tokens(reasoning)
        for call in getattr(message, "tool_calls", None) or ():
            total += estimate_text_tokens(call.id)
            total += estimate_text_tokens(call.name)
            total += estimate_json_tokens(call.arguments)
        name = getattr(message, "name", None)
        if name:
            total += estimate_text_tokens(name)
        call_id = getattr(message, "tool_call_id", None)
        if call_id:
            total += estimate_text_tokens(call_id)
    return total


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    if not tools:
        return 0
    return estimate_json_tokens(tools)


def _fold_geometry(
    *,
    split_index: int,
    msgs_len: int,
    msgs_to_view: list[int] | None,
    view_len: int,
    view_head_len: int,
    protect_first_n: int,
    kept: list[int],
    out_len: int,
) -> tuple[int, int]:
    """Translate the compaction split into input-view coordinates.

    ``split_index`` is where the contiguous preserved tail starts in the
    working list (post-stage1); ``msgs_to_view`` maps working-list positions
    back to view indices (None = identity, i.e. no repair drops). The durable
    head covers everything before the first view position that belongs to the
    canonical contiguous tail (view index >= view_head_len). ``kept`` maps
    each output position to its pre-final-repair index.

    Returns ``(fold_upto_view_index, head_len)``; ``fold_upto`` equals
    ``view_len`` when no canonical tail element survived the final repair.
    """

    def view_index(msgs_index: int) -> int:
        return msgs_index if msgs_to_view is None else msgs_to_view[msgs_index]

    canon_tail_msgs = split_index
    while canon_tail_msgs < msgs_len and view_index(canon_tail_msgs) < view_head_len:
        canon_tail_msgs += 1
    tail_boundary = 1 + protect_first_n + (canon_tail_msgs - split_index)

    fold_upto: int | None = None
    head_len = 0
    for src_index in kept:
        if src_index < tail_boundary:
            head_len += 1
        elif fold_upto is None:
            fold_upto = view_index(split_index + (src_index - 1 - protect_first_n))
    if fold_upto is None:
        return view_len, out_len
    return fold_upto, head_len


def _canonical_hash_view(message: Message) -> dict[str, Any]:
    """Drop volatile fields before hashing: merged-Msg bookkeeping
    (usage/finish_reason/provider_metadata) grows on every model call and
    must not invalidate the fold; ids/timestamps likewise."""
    data = message.model_dump(mode="json")
    for key in ("id", "created_at", "finished_at", "usage", "finish_reason", "provider_metadata"):
        data.pop(key, None)
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source")
            block["source"] = {"bytes": len(json.dumps(source, default=str)) if source else 0}
    return data


def _hash_messages(messages: list[Message]) -> str:
    payload = [_canonical_hash_view(message) for message in messages]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _artifact_key(artifact: CompactionArtifact | None) -> str:
    """Identity of the durable fold for usage-anchor validation — covers
    the fold boundary, the folded prefix hash, and the head content so a
    re-fold at the same boundary with a different summary still busts the
    anchor."""
    if artifact is None:
        return ""
    head_key = hashlib.sha256(
        json.dumps(artifact.head_messages, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()[:8]
    return f"{artifact.first_kept_index}:{artifact.prefix_hash[:16]}:{head_key}"


def _tail_key(canonical_messages: list[Message]) -> str:
    """Fingerprint of the last canonical message — detects in-place growth
    (merged-Msg bookkeeping appends to the reply Msg between model calls)."""
    if not canonical_messages:
        return ""
    return _hash_messages(canonical_messages[-1:])


def _prefix_key(canonical_messages: list[Message]) -> str:
    """Fingerprint of every message in the list EXCEPT the last (hermes'
    ``base_prefix_fp`` equivalent). The tail is guarded separately by
    ``_tail_key`` so in-place merged-Msg growth recounts as a delta; any
    mid-list rewrite fails the anchor closed into a full re-estimate."""
    if len(canonical_messages) <= 1:
        return ""
    fps = [
        hashlib.sha256(
            json.dumps(
                _canonical_hash_view(m), sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        for m in canonical_messages[:-1]
    ]
    return hashlib.sha256("".join(fps).encode("utf-8")).hexdigest()[:16]


def _tools_key(tools: list[dict] | None) -> str:
    """Fingerprint of the tool set — a changed tool list both costs
    different schema tokens and re-projects covered tool-call messages, so
    it hard-invalidates the usage anchor."""
    if not tools:
        return ""
    return hashlib.sha256(
        json.dumps(tools, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _render_summary_source(*, older: list[Message], recent: list[Message]) -> str:
    return (
        "# Messages To Compact\n"
        f"{_render_messages_for_summary(older)}\n\n"
        "# Recent Tail Reference\n"
        f"{_render_messages_for_summary(recent)}"
    )


def _render_messages_for_summary(messages: list[Message]) -> str:
    lines: list[str] = []
    for index, message in enumerate(messages):
        text = "\n".join(block.text for block in message.content if block.text)
        tool_calls = getattr(message, "tool_calls", [])
        if tool_calls:
            calls = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in tool_calls
            ]
            text = f"{text}\nTOOL_CALLS={calls}".strip()
        if isinstance(message, UserMessage) and text.startswith("[CONTEXT COMPACTION"):
            text = f"PREVIOUS_SUMMARY:\n{text}"
        lines.append(f"## {index}: {message.role}\n{text or '[no text]'}")
    return "\n\n".join(lines) or "[no messages]"


def _tail_message_count_for_budget(
    messages: list[Message],
    *,
    tail_token_budget: int,
    estimator: TokenEstimator,
    min_messages: int = 1,
) -> int:
    if not messages:
        return 0
    total = 0
    count = 0
    for message in reversed(messages):
        total += estimator.estimate_messages([message])
        count += 1
        if count >= min_messages and total >= tail_token_budget:
            break
    return min(len(messages), max(min_messages, count))
