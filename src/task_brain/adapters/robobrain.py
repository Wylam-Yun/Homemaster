"""Fake RoboBrain adapter for embodied subgoal to atomic-plan conversion."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EmbodiedSubgoalRequest(BaseModel):
    """Structured request accepted by fake RoboBrain."""

    subgoal: dict[str, Any]
    target_object: dict[str, Any] | None = None
    current_observation: dict[str, Any] | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    success_conditions: list[dict[str, Any] | list[str] | str] = Field(default_factory=list)


class AtomicPlanResponse(BaseModel):
    """Structured atomic plan with evidence."""

    atomic_plan: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class FakeRoboBrainClient:
    """Deterministic fake RoboBrain that emits atomic action templates."""

    def plan(self, request: EmbodiedSubgoalRequest | dict[str, Any]) -> AtomicPlanResponse:
        """Generate an atomic plan without deciding task success."""
        parsed = (
            request.model_copy(deep=True)
            if isinstance(request, EmbodiedSubgoalRequest)
            else EmbodiedSubgoalRequest.model_validate(request)
        )
        subgoal_type = _extract_subgoal_type(parsed.subgoal)
        atomic_plan = _atomic_template_for_subgoal(subgoal_type)

        return AtomicPlanResponse(
            atomic_plan=atomic_plan,
            evidence={
                "source": "fake_robobrain",
                "planned_for_subgoal": subgoal_type,
                "atomic_action_count": len(atomic_plan),
            },
        )


def _extract_subgoal_type(subgoal: dict[str, Any]) -> str:
    raw = subgoal.get("subgoal_type")
    if not isinstance(raw, str) or not raw.strip():
        return "unknown_subgoal"
    return raw.strip()


def _atomic_template_for_subgoal(subgoal_type: str) -> list[dict[str, Any]]:
    if subgoal_type == "embodied_manipulation":
        return [
            {"action": "move_to_pregrasp", "params": {"speed": "slow"}},
            {"action": "close_gripper", "params": {}},
            {"action": "lift", "params": {"height_cm": 10}},
        ]
    if subgoal_type == "navigate":
        return [{"action": "move_base", "params": {"mode": "vln_path_follow"}}]
    if subgoal_type == "observe":
        return [{"action": "scan_scene", "params": {"pattern": "left_to_right"}}]
    if subgoal_type == "return_to_user":
        return [
            {"action": "move_base", "params": {"mode": "return_to_user"}},
            {"action": "open_gripper", "params": {"handover": True}},
        ]
    return [{"action": "hold_position", "params": {"duration_s": 1}}]


__all__ = ["AtomicPlanResponse", "EmbodiedSubgoalRequest", "FakeRoboBrainClient"]
