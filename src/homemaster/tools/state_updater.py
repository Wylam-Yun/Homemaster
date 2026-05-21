"""StateUpdater — sole AgentState writer.

Derives state transitions from ToolResult.data + ToolSpec.state_effects.
Does NOT read ToolResult.summary for state decisions.
ToolResult does NOT contain state_patch; StateUpdater is the only writer.
"""

from __future__ import annotations

from homemaster.agent.state import AgentState
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


class StateUpdater:
    """Applies tool results to AgentState. Sole AgentState writer."""

    def apply(self, *, state: AgentState, result: ToolResult, spec: ToolSpec) -> AgentState:
        """Apply a ToolResult to AgentState based on tool type and result data.

        Uses spec.state_effects to identify which state fields this tool affects.
        """
        if not result.success:
            state.metadata.setdefault("failures", []).append({
                "turn": state.turn_index,
                "tool": result.tool_name,
                "error": result.failure_reason or "unknown error",
            })
            return state

        # Record tool result in metadata
        state.tool_results.append({
            "turn": state.turn_index,
            "tool": result.tool_name,
            "success": result.success,
            "data": result.data,
        })

        return state
