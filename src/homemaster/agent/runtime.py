"""AgentRuntime — re-exports GenericAgentRuntime as the public runtime.

The old Mimo-driven tool loop runtime (with ContextBuilder, MimoDecisionClient,
StateUpdater, etc.) has been replaced by the generic message/tool-call/tool-result
loop in GenericAgentRuntime. Old runtime code is scheduled for deletion in Batch 3.
"""

from __future__ import annotations

from homemaster.agent.generic_runtime import (
    GenericAgentRuntime,
    GenericRunResult,
)

# Backward-compatible aliases
AgentRuntime = GenericAgentRuntime
AgentRunResult = GenericRunResult

__all__ = [
    "AgentRuntime",
    "AgentRunResult",
    "GenericAgentRuntime",
    "GenericRunResult",
]
