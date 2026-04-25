"""Stage 04 PlanningContext assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homemaster.contracts import MemoryRetrievalResult, PlanningContext, TaskCard
from homemaster.grounding import GroundingResult, select_grounded_target


@dataclass(frozen=True)
class PlanningContextBuildResult:
    context: PlanningContext
    grounding: GroundingResult


def build_planning_context(
    task_card: TaskCard,
    memory_result: MemoryRetrievalResult,
    world: dict[str, Any],
    *,
    runtime_state_summary: dict[str, Any] | None = None,
) -> PlanningContextBuildResult:
    grounding = select_grounded_target(task_card, memory_result, world)
    runtime_summary = dict(runtime_state_summary or {})
    runtime_summary.update(
        {
            "grounding_status": grounding.grounding_status,
            "grounding_reason": grounding.grounding_reason,
            "needs_exploratory_search": grounding.selected_target is None,
        }
    )
    planning_notes = _planning_notes(grounding)
    context = PlanningContext(
        task_card=task_card,
        retrieval_query=memory_result.retrieval_query,
        memory_evidence=memory_result,
        selected_target=grounding.selected_target,
        rejected_hits=grounding.rejected_hits,
        runtime_state_summary=runtime_summary,
        world_summary=build_world_summary(world),
        planning_notes=planning_notes,
    )
    return PlanningContextBuildResult(context=context, grounding=grounding)


def build_world_summary(world: dict[str, Any]) -> dict[str, Any]:
    viewpoints = world.get("viewpoints") if isinstance(world.get("viewpoints"), dict) else {}
    furniture = world.get("furniture") if isinstance(world.get("furniture"), list) else []
    rooms = world.get("rooms") if isinstance(world.get("rooms"), list) else []
    return {
        "room_ids": [
            item.get("room_id") for item in rooms if isinstance(item, dict) and item.get("room_id")
        ],
        "viewpoint_ids": sorted(str(key) for key in viewpoints),
        "anchors": [
            {
                "anchor_id": item.get("anchor_id"),
                "room_id": item.get("room_id"),
                "viewpoint_id": item.get("viewpoint_id"),
                "display_text": item.get("display_text"),
            }
            for item in furniture
            if isinstance(item, dict)
        ],
    }


def _planning_notes(grounding: GroundingResult) -> list[str]:
    if grounding.selected_target is not None:
        return ["grounded reliable memory target is available for Stage 05 planning"]
    notes = ["没有可靠执行记忆；Stage 05 should plan exploratory search/observe steps."]
    weak_hints = [
        assessment.suggested_search_hint
        for assessment in grounding.assessments
        if assessment.status == "weak_lead" and assessment.suggested_search_hint
    ]
    if weak_hints:
        notes.append("weak memory leads for exploration: " + "; ".join(dict.fromkeys(weak_hints)))
    return notes
