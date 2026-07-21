"""Agent — generic message/tool-call/tool-result runtime.

Contains AgentRuntime, its temporary GenericAgentRuntime alias, AgentSession,
normalized message types, context composition, and the turn loop.
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
from homemaster.agent.generic_runtime import AgentRuntime, GenericAgentRuntime

__all__ = [
    "AgentRuntime",
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
    "GenericAgentRuntime",
    "RenderMode",
    "RuntimeBudgetStatusProvider",
    "TaskStateSnapshotProvider",
    "estimate_json_tokens",
    "estimate_messages_tokens",
    "estimate_text_tokens",
    "estimate_tools_tokens",
]
