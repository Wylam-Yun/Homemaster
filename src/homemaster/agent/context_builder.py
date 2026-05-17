"""ContextBuilder — builds compact context for Mimo decision from AgentState.

Output is a three-layer dict:
  stable_context:      runtime constraints, tool manifest, skill summaries, snapshots
  task_state_context:  user request, task card, candidates, embodied state
  recent_dynamics:     actions, observations, verifications, failures

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
            "loaded_skill_contexts": state.loaded_skill_contexts,
            "memory_snapshot": state.memory_context_snapshot,
            "user_snapshot": state.user_context_snapshot,
        }

    def _build_task_state(self, state: AgentState) -> dict[str, Any]:
        return {
            "user_request": state.user_request,
            "task_card": state.task_card,
            "target_candidates": state.target_candidates,
            "current_location": state.current_location,
            "current_object": state.current_object,
            "holding_object": state.holding_object,
            "memory_hits_summary": [
                {"memory_id": h.get("memory_id"), "object_category": h.get("object_category")}
                for h in state.memory_hits[:10]
            ],
        }

    def _build_dynamics(self, state: AgentState, max_turns: int) -> dict[str, Any]:
        return {
            "recent_actions": state.actions[-5:],
            "recent_observations": state.observations[-5:],
            "recent_verifications": state.verifications[-5:],
            "failures": state.failures[-3:],
            "turn_index": state.turn_index,
            "max_turns": max_turns,
        }
