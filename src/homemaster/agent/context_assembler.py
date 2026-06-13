"""ContextAssembler — orchestrate providers, budget, and compaction."""

from __future__ import annotations

from dataclasses import dataclass

from homemaster.agent.context_budget import ContextBudget, BudgetDecision, estimate_text_tokens
from homemaster.agent.context_providers import (
    ConversationProvider,
    FailureSummaryProvider,
    RuntimeBudgetStatusProvider,
    TaskStateSnapshotProvider,
)
from homemaster.agent.compact import (
    build_compaction_summary_message,
    deterministic_summary,
    split_preserving_tool_pairs,
)
from homemaster.agent.messages import ContentBlock, Message, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config.model_config import ContextPolicyConfig, ProviderProfileConfig
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
            context_window_tokens=self._provider.context_window_tokens or 200_000,
            max_output_tokens=self._provider.max_output_tokens or 4096,
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
        prelude_texts: list[str] = []
        for provider in (
            TaskStateSnapshotProvider(task_state_store),
            RuntimeBudgetStatusProvider(agent_state),
            FailureSummaryProvider(agent_state),
        ):
            for item in provider.collect():
                rendered = item.render(item.mode)
                if isinstance(rendered, str):
                    prelude_texts.append(rendered)

        messages = session.messages
        if prelude_texts:
            messages = [
                UserMessage(
                    content=[
                        ContentBlock(
                            text="# Runtime Context\n"
                            + "\n\n".join(prelude_texts)
                            + "\n\nThis runtime context is not a new user request."
                        )
                    ]
                ),
                *messages,
            ]

        estimated = estimate_text_tokens(self._system_prompt)
        estimated += sum(
            estimate_text_tokens(block.text)
            for message in messages
            for block in message.content
            if block.text
        )
        budget = self._budget()
        padded = budget.padded(estimated)
        agent_state.estimated_context_tokens = padded

        compaction_triggered = False
        compaction_kind = "none"

        if self.force_compact_next or budget.should_compact(padded) is BudgetDecision.COMPACT:
            compaction_triggered, compaction_kind, messages = self._compact(
                session=session,
                messages=messages,
                budget=budget,
            )
            self.force_compact_next = False

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

    def _compact(
        self,
        *,
        session: AgentSession,
        messages: list[Message],
        budget: ContextBudget,
    ) -> tuple[bool, str, list[Message]]:
        preserve_count = self._policy.preserve_recent_agent_steps * 2
        older, recent = split_preserving_tool_pairs(
            messages,
            preserve_recent=preserve_count,
        )
        if not older:
            return False, "none", messages

        summary = deterministic_summary(older)
        compacted_messages = [build_compaction_summary_message(summary), *recent]

        session.replace_messages(compacted_messages)
        return True, "summary", compacted_messages
