"""ContextBuilder — builds compact context for Mimo decision from AgentState.

Output is a three-layer dict:
  stable_context:      runtime constraints, tool manifest, skill summaries, snapshots
  task_state_context:  user request, metadata
  recent_dynamics:     tool results, metadata

Does NOT include full trace, raw prompt, secrets, or full SKILL.md.
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.state import AgentState


class ContextBuilder:
    """Builds compact three-layer context from AgentState for Mimo decisions."""

    def build(
        self,
        state: AgentState,
        tool_manifests: list[dict[str, Any]],
        skill_summaries: list[dict[str, Any]],
        max_turns: int,
    ) -> dict[str, Any]:
        """Build compact context dict from current AgentState."""
        return {
            "stable_context": self._build_stable(state, tool_manifests, skill_summaries, max_turns),
            "task_state_context": self._build_task_state(state),
            "recent_dynamics_context": self._build_dynamics(state, max_turns),
        }

    def _build_stable(
        self,
        state: AgentState,
        tool_manifests: list[dict[str, Any]],
        skill_summaries: list[dict[str, Any]],
        max_turns: int,
    ) -> dict[str, Any]:
        return {
            "runtime_constraints": {
                "max_turns": max_turns,
            },
            "tool_manifests": tool_manifests,
            "skill_summaries": skill_summaries,
        }

    def _build_task_state(self, state: AgentState) -> dict[str, Any]:
        return {
            "user_request": state.user_request,
            "metadata": state.metadata,
        }

    def _build_dynamics(self, state: AgentState, max_turns: int) -> dict[str, Any]:
        return {
            "recent_tool_results": state.tool_results[-5:],
            "failures": state.metadata.get("failures", [])[-3:],
            "turn_index": state.turn_index,
            "max_turns": max_turns,
        }
