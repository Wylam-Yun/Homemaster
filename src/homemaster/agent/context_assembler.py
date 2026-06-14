"""ContextAssembler — orchestrate providers, budget, and compaction."""

from __future__ import annotations

from dataclasses import dataclass

from homemaster.agent.compact import (
    build_basic_summary,
    build_compaction_summary_message,
    split_preserving_recent_context,
)
from homemaster.agent.context_budget import BudgetDecision, ContextBudget, estimate_text_tokens
from homemaster.agent.context_items import ContextPlacement
from homemaster.agent.context_providers import (
    ContextProvider,
    ConversationProvider,
    FailureSummaryProvider,
    RuntimeBudgetStatusProvider,
    TaskStateSnapshotProvider,
)
from homemaster.agent.messages import ContentBlock, Message, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState, CompactionRecord
from homemaster.config.model_config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.config.model_profiles import resolve_context_window_tokens
from homemaster.task_state.store import TaskStateStore


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
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._system_prompt = system_prompt
        self.force_compact_next = False

    def _budget(self) -> ContextBudget:
        return ContextBudget(
            context_window_tokens=resolve_context_window_tokens(self._provider),
            output_reserve_tokens=self._policy.output_reserve_tokens,
            threshold_ratio=self._policy.compression_threshold_ratio,
            recent_tail_ratio=self._policy.recent_tail_ratio,
            safety_buffer_tokens=self._policy.safety_buffer_tokens,
            token_estimation_padding=self._policy.token_estimation_padding,
            image_token_estimate=self._policy.image_token_estimate,
        )

    def prepare(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore | None,
        tools: list[dict] | None,
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

        estimated = estimate_text_tokens(self._system_prompt)
        estimated += sum(item.token_estimate for item in items)
        estimated += estimate_tools_tokens(tools)
        budget = self._budget()
        padded = budget.padded(estimated)
        agent_state.estimated_context_tokens = padded

        compaction_triggered = False
        compaction_kind = "none"

        if (
            self._policy.auto_compact_enabled
            and (
                self.force_compact_next
                or budget.should_compact(padded) is BudgetDecision.COMPACT
            )
        ):
            before_tokens = padded
            compaction_triggered, compaction_kind, conversation_messages = self._compact(
                session=session,
                messages=conversation_messages,
                budget=budget,
            )
            if compaction_triggered:
                after_estimate = estimate_messages_tokens(conversation_messages)
                after_estimate += estimate_text_tokens(self._system_prompt)
                after_estimate += sum(estimate_text_tokens(text) for text in prelude_texts)
                after_estimate += estimate_tools_tokens(tools)
                agent_state.last_compaction = CompactionRecord(
                    kind="reactive" if self.force_compact_next else "summary",
                    before_tokens=before_tokens,
                    after_tokens=budget.padded(after_estimate),
                    reason="provider_context_length" if self.force_compact_next else "threshold",
                )
            self.force_compact_next = False

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
    ) -> tuple[bool, str, list[Message]]:
        preserve_count = self._policy.preserve_recent_agent_steps * 2
        older, recent = split_preserving_recent_context(
            messages,
            preserve_recent_messages=preserve_count,
            preserve_recent_user_turns=self._policy.preserve_recent_user_turns,
        )
        if not older:
            return False, "none", messages

        summary = build_basic_summary(older)
        compacted_messages = [build_compaction_summary_message(summary), *recent]

        session.replace_messages(compacted_messages)
        return True, "summary", compacted_messages


def estimate_messages_tokens(messages: list[Message]) -> int:
    return sum(
        estimate_text_tokens(block.text)
        for message in messages
        for block in message.content
        if block.text
    )


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    if not tools:
        return 0
    from homemaster.agent.context_budget import estimate_json_tokens

    return estimate_json_tokens(tools)
