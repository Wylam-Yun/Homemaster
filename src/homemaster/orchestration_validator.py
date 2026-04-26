"""Lightweight validators for Stage 05 orchestration output."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from homemaster.contracts import OrchestrationPlan

FORBIDDEN_STAGE_05_KEYS = {
    "selected_target",
    "selected_candidate_id",
    "candidate_id",
    "switch_candidate",
    "CandidatePool",
    "CandidateSelection",
    "memory_id",
    "selected_memory_id",
    "switch_target",
    "next_target_id",
}


class Stage05ValidationError(RuntimeError):
    """Raised when Stage 05 model output violates structural boundaries."""

    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def validate_orchestration_payload(payload: dict[str, Any]) -> OrchestrationPlan:
    """Validate raw LLM JSON before it enters the executor."""

    forbidden = _find_forbidden_keys(payload)
    if forbidden:
        raise Stage05ValidationError(
            error_type="forbidden_orchestration_field",
            message="forbidden Stage 05 field(s): " + ", ".join(sorted(forbidden)),
        )
    try:
        plan = OrchestrationPlan.model_validate(payload)
    except ValidationError as exc:
        raise Stage05ValidationError(
            error_type="orchestration_schema_error",
            message=str(exc),
        ) from exc
    return validate_orchestration_plan(plan)


def validate_orchestration_plan(plan: OrchestrationPlan) -> OrchestrationPlan:
    """Validate Stage 05 plan relationships without judging semantic quality."""

    if not plan.subtasks:
        raise Stage05ValidationError(
            error_type="empty_orchestration_plan",
            message="OrchestrationPlan must contain at least one subtask",
        )

    seen: set[str] = set()
    for subtask in plan.subtasks:
        if subtask.id in seen:
            raise Stage05ValidationError(
                error_type="duplicate_subtask_id",
                message=f"duplicate subtask id: {subtask.id}",
            )
        seen.add(subtask.id)
        if not subtask.success_criteria:
            raise Stage05ValidationError(
                error_type="missing_success_criteria",
                message=f"subtask {subtask.id} must include success_criteria",
            )

    for subtask in plan.subtasks:
        for dependency in subtask.depends_on:
            if dependency not in seen:
                raise Stage05ValidationError(
                    error_type="unknown_dependency",
                    message=f"subtask {subtask.id} has unknown dependency {dependency}",
                )
    _assert_no_dependency_cycles(plan)
    return plan


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_STAGE_05_KEYS:
                found.add(key_text)
            found.update(_find_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_keys(item))
    return found


def _assert_no_dependency_cycles(plan: OrchestrationPlan) -> None:
    dependencies = {subtask.id: set(subtask.depends_on) for subtask in plan.subtasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(subtask_id: str) -> None:
        if subtask_id in visited:
            return
        if subtask_id in visiting:
            raise Stage05ValidationError(
                error_type="dependency_cycle",
                message=f"dependency cycle detected at {subtask_id}",
            )
        visiting.add(subtask_id)
        for dependency in dependencies[subtask_id]:
            visit(dependency)
        visiting.remove(subtask_id)
        visited.add(subtask_id)

    for subtask_id in dependencies:
        visit(subtask_id)
