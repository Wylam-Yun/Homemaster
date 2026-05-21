"""Simulated tool executors for AgentRuntime.

These executors simulate robot actions (navigate, observe, manipulate, verify)
when real VLA/VLN/VLM executors are not integrated. Each executor has signature:
    def executor(*, arguments: dict, run_context: RunContext) -> ToolResult
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec

# ---------------------------------------------------------------------------
# Executor implementations
# ---------------------------------------------------------------------------


def exec_navigate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Simulated navigation: move robot to target location."""
    goal_type = arguments.get("goal_type", "go_to")
    room_hint = arguments.get("room_hint", arguments.get("target_room", "kitchen"))
    return ToolResult(
        success=True,
        tool_name="navigate",
        executor_mode="simulated_skill",
        data={
            "location": room_hint,
            "observation": f"navigated to {room_hint}",
            "goal_type": goal_type,
        },
    )


def exec_observe(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Simulated observation."""
    target = arguments.get("target_object", "unknown")
    return ToolResult(
        success=True,
        tool_name="observe",
        executor_mode="simulated_skill",
        data={
            "object": target,
            "visible": True,
            "observation": f"observed {target}",
        },
    )


def exec_manipulate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Simulated manipulation: pick up, put down, etc."""
    action = arguments.get("action", "pick_up")
    target = arguments.get("target_object", "unknown")
    return ToolResult(
        success=True,
        tool_name="manipulate",
        executor_mode="simulated_skill",
        data={
            "holding": target,
            "action": action,
            "action_result": f"{action} {target} successfully",
        },
    )


def exec_verify(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    """Simulated symbolic verification: check task objective achieved."""
    target = arguments.get("target_object", "unknown")
    expected_state = arguments.get("expected_state", "delivered")

    # In generic mode, verification always succeeds for simulated tools
    verified = True
    reason = f"simulated verification passed for {target}"
    return ToolResult(
        success=verified,
        tool_name="verify",
        executor_mode="simulated_verification",
        failure_reason=None if verified else reason,
        data={
            "verified": verified,
            "target_object": target,
            "expected_state": expected_state,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# ToolSpec factories
# ---------------------------------------------------------------------------


def make_navigate_spec() -> ToolSpec:
    return ToolSpec(
        name="navigate",
        description=(
            "Simulated skill: navigate robot to a target location. "
            "Accepts room_hint or target_room. "
            "Success updates current_location and appends to actions. "
            "Failure produces a FailureRecord — no automatic recovery."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal_type": {"type": "string", "description": "Navigation goal type."},
                "room_hint": {"type": "string", "description": "Target room."},
                "target_room": {"type": "string", "description": "Target room (alternative)."},
            },
        },
        executor_mode="simulated_skill",
        selectable_by_model=True,
        requires_verification=True,
        state_effects=["current_location", "actions"],
        executor=exec_navigate,
    )


def make_observe_spec() -> ToolSpec:
    return ToolSpec(
        name="observe",
        description=(
            "Simulated skill: observe environment at current location. "
            "Accepts target_object. Success appends observations and scene_evidence. "
            "Object not found is negative evidence — Mimo decides next action."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to look for."},
            },
        },
        executor_mode="simulated_skill",
        selectable_by_model=True,
        requires_verification=True,
        state_effects=["current_object", "observations"],
        executor=exec_observe,
    )


def make_manipulate_spec() -> ToolSpec:
    return ToolSpec(
        name="manipulate",
        description=(
            "Simulated skill: manipulate an object (pick up, put down, etc.). "
            "Requires action and target_object. "
            "Success may update holding_object and object state. "
            "Failure returns failure_reason — no automatic retry."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action to perform."},
                "target_object": {"type": "string", "description": "Object to manipulate."},
            },
            "required": ["action", "target_object"],
        },
        executor_mode="simulated_skill",
        selectable_by_model=True,
        requires_verification=True,
        state_effects=["holding_object", "actions"],
        executor=exec_manipulate,
    )


def make_verify_spec() -> ToolSpec:
    return ToolSpec(
        name="verify",
        description=(
            "Simulated verification: check whether a task objective is achieved. "
            "Accepts target_object and expected_state. "
            "Success appends to verifications. Failure writes to failures — "
            "Mimo decides recovery or escalation on next turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to verify."},
                "expected_state": {"type": "string", "description": "Expected state."},
            },
        },
        executor_mode="simulated_verification",
        selectable_by_model=True,
        requires_verification=False,
        state_effects=["verifications"],
        executor=exec_verify,
    )


# ---------------------------------------------------------------------------
# Aggregated list for registry builders
# ---------------------------------------------------------------------------

SIMULATED_TOOL_MAKERS = [
    make_navigate_spec,
    make_observe_spec,
    make_manipulate_spec,
    make_verify_spec,
]
