"""AgentRuntime — re-exports GenericAgentRuntime as the public runtime."""

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
