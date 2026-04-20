"""Mock atomic executor that returns evidence-rich execution results."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

from task_brain.adapters.robobrain import AtomicPlanResponse
from task_brain.domain import RuntimeState
from task_brain.world import MockWorld


class ExecutionResult(BaseModel):
    """Normalized executor output."""

    status: str
    execution_result: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class MockAtomicExecutor:
    """Execute atomic plans with deterministic outputs and optional failure injection."""

    @staticmethod
    def execute(
        plan: AtomicPlanResponse | dict[str, Any],
        runtime_state: RuntimeState,
        world: MockWorld,
        attempt: int,
        failure_rules: list[dict[str, Any]] | None = None,
    ) -> ExecutionResult:
        """Execute one atomic plan attempt.

        The executor only reads ``runtime_state`` and never mutates it.
        """
        parsed_plan = (
            plan.model_copy(deep=True)
            if isinstance(plan, AtomicPlanResponse)
            else AtomicPlanResponse.model_validate(plan)
        )

        runtime_progress = {
            "active_skill_name": (
                runtime_state.embodied_action_progress.active_skill_name
                if runtime_state.embodied_action_progress is not None
                else None
            ),
            "current_action_phase": (
                runtime_state.embodied_action_progress.current_action_phase
                if runtime_state.embodied_action_progress is not None
                else None
            ),
            "local_world_state_flags": (
                deepcopy(runtime_state.embodied_action_progress.local_world_state_flags)
                if runtime_state.embodied_action_progress is not None
                else {}
            ),
        }

        rule = _match_failure_rule(failure_rules or [], attempt)
        if rule is not None:
            reason = _optional_str(rule.get("reason")) or "execution_failed"
            runtime_updates = _runtime_updates_candidate(rule)
            return ExecutionResult(
                status=str(rule.get("status", "failed")),
                execution_result={
                    "status": "failed",
                    "reason": reason,
                    "attempt": attempt,
                    "state_delta": _state_delta(rule, status="failed", attempt=attempt),
                    "runtime_object_updates_candidate": runtime_updates,
                },
                evidence={
                    "source": "mock_atomic_executor",
                    "failure_injected": True,
                    "reason": reason,
                    "runtime_progress": runtime_progress,
                    "world_hint": _world_hint(world),
                },
            )

        return ExecutionResult(
            status="success",
            execution_result={
                "status": "success",
                "attempt": attempt,
                "applied_action_count": len(parsed_plan.atomic_plan),
                "state_delta": _state_delta(None, status="success", attempt=attempt),
                "runtime_object_updates_candidate": [],
            },
            evidence={
                "source": "mock_atomic_executor",
                "failure_injected": False,
                "runtime_progress": runtime_progress,
                "world_hint": _world_hint(world),
            },
        )


def _match_failure_rule(rules: list[dict[str, Any]], attempt: int) -> dict[str, Any] | None:
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        candidate_attempt = rule.get("attempt")
        if candidate_attempt is None or candidate_attempt == attempt:
            return rule
    return None


def _state_delta(rule: dict[str, Any] | None, *, status: str, attempt: int) -> dict[str, Any]:
    if rule and isinstance(rule.get("state_delta"), dict):
        delta = dict(rule["state_delta"])
    else:
        delta = {}
    delta.setdefault("executor_status", status)
    delta.setdefault("last_attempt", attempt)
    return delta


def _runtime_updates_candidate(rule: dict[str, Any]) -> list[dict[str, Any]]:
    updates = rule.get("runtime_object_updates_candidate", [])
    if not isinstance(updates, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        object_ref = item.get("object_ref")
        source = item.get("source")
        if not isinstance(object_ref, str) or not isinstance(source, str):
            continue
        normalized.append(dict(item))
    return normalized


def _world_hint(world: MockWorld) -> dict[str, Any]:
    _ = world
    return {"world_type": "mock_world"}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = ["ExecutionResult", "MockAtomicExecutor"]
