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
            state.failures.append({
                "turn": state.turn_index,
                "tool": result.tool_name,
                "error": result.failure_reason or "unknown error",
            })
            return state

        # Dispatch by tool name for state-specific updates.
        # spec.state_effects declares which fields the tool modifies.
        handler = _STATE_HANDLERS.get(result.tool_name)
        if handler:
            handler(state, result)

        return state


def _apply_understand_task(state: AgentState, result: ToolResult) -> None:
    task_card = result.data.get("task_card")
    if task_card is not None:
        state.task_card = task_card


def _apply_retrieve_memory(state: AgentState, result: ToolResult) -> None:
    state.memory_hits = result.data.get("hits", [])


def _apply_ground_target(state: AgentState, result: ToolResult) -> None:
    state.target_candidates = result.data.get("candidates", [])
    state.selected_target = result.data.get("selected_target")


def _apply_get_skill(state: AgentState, result: ToolResult) -> None:
    skill_name = result.data.get("name", "")
    if skill_name:
        state.loaded_skill_contexts[skill_name] = result.data


def _apply_navigate(state: AgentState, result: ToolResult) -> None:
    state.current_location = result.data.get("location")
    state.actions.append({
        "turn": state.turn_index,
        "tool": "navigate",
        "result": result.data,
    })


def _apply_observe(state: AgentState, result: ToolResult) -> None:
    state.current_object = result.data.get("object")
    state.observations.append({
        "turn": state.turn_index,
        "tool": "observe",
        "result": result.data,
    })


def _apply_manipulate(state: AgentState, result: ToolResult) -> None:
    state.holding_object = result.data.get("holding")
    state.actions.append({
        "turn": state.turn_index,
        "tool": "manipulate",
        "result": result.data,
    })


def _apply_verify(state: AgentState, result: ToolResult) -> None:
    state.verifications.append({
        "turn": state.turn_index,
        "tool": "verify",
        "result": result.data,
    })


def _apply_update_memory(state: AgentState, result: ToolResult) -> None:
    state.actions.append({
        "turn": state.turn_index,
        "tool": "update_memory",
        "result": result.data,
    })


def _apply_update_user_profile(state: AgentState, result: ToolResult) -> None:
    state.actions.append({
        "turn": state.turn_index,
        "tool": "update_user_profile",
        "result": result.data,
    })


_STATE_HANDLERS = {
    "understand_task": _apply_understand_task,
    "retrieve_memory": _apply_retrieve_memory,
    "ground_target": _apply_ground_target,
    "get_skill": _apply_get_skill,
    "navigate": _apply_navigate,
    "observe": _apply_observe,
    "manipulate": _apply_manipulate,
    "verify": _apply_verify,
    "update_memory": _apply_update_memory,
    "update_user_profile": _apply_update_user_profile,
}
