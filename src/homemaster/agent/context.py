"""Context assembly — items, budget, providers, and assembler."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Protocol

from homemaster.agent.compact import (
    build_compaction_summary_message,
    microcompact_tool_results_by_type,
    sanitize_tool_pairs,
    split_preserving_recent_context,
    strip_old_images,
)
from homemaster.agent.messages import ContentBlock, Message, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState, CompactionRecord
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.providers.token_estimator import TokenEstimator, make_default_estimator
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
    cjk = sum(1 for char in text if "一" <= char <= "鿿")
    non_cjk = max(0, len(text) - cjk)
    return max(1, math.ceil(cjk / 2) + math.ceil(non_cjk / 4))


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
            self.context_window_tokens
            - self.output_reserve_tokens
            - self.safety_buffer_tokens
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
        errors = [
            r for r in self._state.last_tool_results_summary if r.get("is_error")
        ]
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
        total_text = sum(
            estimate_text_tokens(block.text)
            for msg in messages
            for block in msg.content
            if block.text
        )
        return [
            ContextItem(
                id="conversation",
                kind="conversation",
                priority=ContextPriority.REQUIRED,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONVERSATION,
                token_estimate=total_text,
                render=lambda _mode, msgs=messages: msgs,
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


class ContextAssembler:
    def __init__(
        self,
        *,
        provider: ProviderProfileConfig,
        policy: ContextPolicyConfig,
        system_prompt: str,
        summary_client: Any = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._system_prompt = system_prompt
        self._estimator = make_default_estimator(provider)
        self._summary_client = summary_client

    def _budget(self) -> ContextBudget:
        return ContextBudget(
            context_window_tokens=self._provider.context_window_tokens,
            output_reserve_tokens=self._policy.output_reserve_tokens,
            threshold_ratio=self._policy.compression_threshold_ratio,
            tail_token_ratio=self._policy.tail_token_ratio,
            safety_buffer_tokens=self._policy.safety_buffer_tokens,
            token_estimation_padding=self._policy.token_estimation_padding,
        )

    def prepare(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore | None,
        tools: list[dict] | None,
        force_compact: str | bool | None = None,
    ) -> ComposedContext:
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
        conversation_messages: list[Message] = session.messages
        for item in items:
            rendered = item.render(item.mode)
            if item.placement is ContextPlacement.CONTEXT_PRELUDE and isinstance(rendered, str):
                prelude_texts.append(rendered)
            elif (
                item.placement is ContextPlacement.CONVERSATION
                and isinstance(rendered, list)
            ):
                conversation_messages = rendered

        estimated = self._estimator.estimate_text(self._system_prompt)
        estimated += self._estimator.estimate_messages(conversation_messages)
        estimated += sum(self._estimator.estimate_text(text) for text in prelude_texts)
        estimated += estimate_tools_tokens(tools)
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
            compaction_triggered, compaction_kind, conversation_messages = self._compact(
                session=session,
                messages=conversation_messages,
                budget=budget,
                aggressive=force_mode in {"aggressive", "manual"},
                force_summary=force_mode == "manual",
            )
            if compaction_triggered:
                if force_mode == "manual":
                    record_kind = "manual"
                    record_reason = "manual"
                    compaction_kind = f"manual_{compaction_kind}"
                elif force_requested:
                    record_kind = "reactive"
                    record_reason = "provider_context_length"
                else:
                    record_kind = "summary"
                    record_reason = "threshold"
                after_estimate = estimate_messages_tokens(
                    conversation_messages,
                    estimator=self._estimator,
                )
                after_estimate += self._estimator.estimate_text(self._system_prompt)
                after_estimate += sum(
                    self._estimator.estimate_text(text) for text in prelude_texts
                )
                after_estimate += estimate_tools_tokens(tools)
                after_tokens = budget.padded(after_estimate)
                padded = after_tokens
                agent_state.last_compaction = CompactionRecord(
                    kind=record_kind,
                    before_tokens=before_tokens,
                    after_tokens=after_tokens,
                    reason=record_reason,
                )
        messages = self._render_messages(
            prelude_texts=prelude_texts,
            conversation_messages=conversation_messages,
        )

        return ComposedContext(
            messages=messages,
            system_prompt=self._system_prompt,
            tools=tools,
            metrics=ContextMetrics(
                estimated_tokens=padded,
                compaction_triggered=compaction_triggered,
                compaction_kind=compaction_kind,
            ),
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

    def _compact(
        self,
        *,
        session: AgentSession,
        messages: list[Message],
        budget: ContextBudget,
        aggressive: bool = False,
        force_summary: bool = False,
    ) -> tuple[bool, str, list[Message]]:
        stage1_messages, stripped_images = strip_old_images(
            messages,
            keep_recent_images=self._policy.keep_recent_images,
        )
        stage1_messages, saved_tool_tokens = microcompact_tool_results_by_type(
            stage1_messages,
            keep_recent_per_type=dict(self._policy.keep_recent_tool_results_per_type),
            default_keep_recent=self._policy.default_keep_recent_tool_results,
        )
        if stripped_images or saved_tool_tokens:
            stage1_messages = sanitize_tool_pairs(stage1_messages)
            stage1_estimate = estimate_messages_tokens(
                stage1_messages,
                estimator=self._estimator,
            )
            if (
                not force_summary
                and budget.should_compact(budget.padded(stage1_estimate))
                is not BudgetDecision.COMPACT
            ):
                session.replace_messages(stage1_messages)
                return True, "micro", stage1_messages
            messages = stage1_messages

        tail_ratio = (
            self._policy.aggressive_tail_token_ratio
            if aggressive
            else self._policy.tail_token_ratio
        )
        protect_first_n = (
            self._policy.aggressive_protect_first_n
            if aggressive
            else self._policy.protect_first_n
        )
        if force_summary:
            preserve_count = 1
        else:
            preserve_count = _tail_message_count_for_budget(
                messages,
                tail_token_budget=max(1, int(budget.compaction_threshold_tokens * tail_ratio)),
                estimator=self._estimator,
                min_messages=1,
            )
        older, recent = split_preserving_recent_context(
            messages,
            preserve_recent_messages=preserve_count,
            protect_first_n=protect_first_n,
        )
        if not older:
            if stripped_images or saved_tool_tokens:
                session.replace_messages(messages)
                return True, "micro", messages
            return False, "none", messages

        summary = self._build_summary(older=older, recent=recent)
        if summary is None:
            return False, "none", messages
        compacted_messages = sanitize_tool_pairs([
            build_compaction_summary_message(summary),
            *recent,
        ])
        compacted_messages, _stripped_final = strip_old_images(
            compacted_messages,
            keep_recent_images=self._policy.keep_recent_images,
        )

        session.replace_messages(compacted_messages)
        return True, "summary", compacted_messages

    def _build_summary(
        self,
        *,
        older: list[Message],
        recent: list[Message],
    ) -> str | None:
        if not self._policy.enable_llm_summary or self._summary_client is None:
            if self._policy.abort_on_summary_failure:
                return None
            return f"[Summary unavailable. {len(older)} messages omitted]"
        try:
            from homemaster.prompts.loader import PromptId, load_prompt

            prompt = _render_summary_source(older=older, recent=recent)
            message = self._summary_client_complete(
                prompt=prompt,
                system_prompt=load_prompt(PromptId.COMPACT_SUMMARY),
            )
        except Exception:
            if self._policy.abort_on_summary_failure:
                return None
            return f"[Summary unavailable. {len(older)} messages dropped]"
        if message.text.strip():
            return message.text.strip()
        if self._policy.abort_on_summary_failure:
            return None
        return f"[Summary unavailable. {len(older)} messages omitted]"

    def _summary_client_complete(self, *, prompt: str, system_prompt: str):
        try:
            return self._summary_client.complete(
                [UserMessage.from_text(prompt)],
                system_prompt=system_prompt,
                max_output_tokens=self._policy.output_reserve_tokens,
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
    return sum(
        estimate_text_tokens(block.text)
        for message in messages
        for block in message.content
        if block.text
    )


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    if not tools:
        return 0
    return estimate_json_tokens(tools)


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
