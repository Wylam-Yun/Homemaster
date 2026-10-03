"""Agent — generic message/tool-call/tool-result runtime.

Contains AgentSession, normalized message types, context composition,
and shared run contracts. The AgentScope engine drives the model loop.
No home-domain schemas or domain logic.
"""

from homemaster.agent.context import (
    BudgetDecision,
    ComposedContext,
    ContextAssembler,
    ContextBudget,
    ContextFreshness,
    ContextItem,
    ContextMetrics,
    ContextPlacement,
    ContextPriority,
    ContextProvider,
    ConversationProvider,
    FailureSummaryProvider,
    RenderMode,
    RuntimeBudgetStatusProvider,
    TaskStateSnapshotProvider,
    estimate_json_tokens,
    estimate_messages_tokens,
    estimate_text_tokens,
    estimate_tools_tokens,
)

__all__ = [
    "BudgetDecision",
    "ComposedContext",
    "ContextAssembler",
    "ContextBudget",
    "ContextFreshness",
    "ContextItem",
    "ContextMetrics",
    "ContextPlacement",
    "ContextPriority",
    "ContextProvider",
    "ConversationProvider",
    "FailureSummaryProvider",
    "RenderMode",
    "RuntimeBudgetStatusProvider",
    "TaskStateSnapshotProvider",
    "estimate_json_tokens",
    "estimate_messages_tokens",
    "estimate_text_tokens",
    "estimate_tools_tokens",
]
