"""Verification engine for evidence-based success evaluation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from task_brain.domain import Predicate, VerificationEvidence

_RUNTIME_BLOCK_REASONS = {"target_dropped", "target_location_changed"}
_RUNTIME_BLOCKED_PREDICATES = {"object_secured", "returned_to_user", "task_goal_satisfied"}


class VerificationResult(BaseModel):
    """Structured result returned by verification engine."""

    passed: bool
    failed_conditions: list[Predicate] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class VerificationEngine:
    """Evaluate success predicates against normalized evidence."""

    def evaluate(
        self,
        success_conditions: list[Predicate | dict[str, Any] | list[Any]],
        evidence: VerificationEvidence | dict[str, Any],
    ) -> VerificationResult:
        """Evaluate conditions and return pass/fail with explainable evidence."""
        normalized_conditions = [_normalize_condition(item) for item in success_conditions]
        verification_evidence = (
            evidence.model_copy(deep=True)
            if isinstance(evidence, VerificationEvidence)
            else VerificationEvidence.model_validate(evidence)
        )

        blocked_reasons = _runtime_blocked_reasons(verification_evidence.execution_result)
        matched_signals: list[str] = []
        failed_conditions: list[Predicate] = []

        for condition in normalized_conditions:
            if self._evaluate_predicate(condition, verification_evidence, blocked_reasons):
                matched_signals.append(condition.name)
            else:
                failed_conditions.append(condition)

        has_conditions = bool(normalized_conditions)
        passed = has_conditions and not failed_conditions
        return VerificationResult(
            passed=passed,
            failed_conditions=failed_conditions,
            evidence={
                "matched_signals": matched_signals,
                "blocked_by_runtime_updates": blocked_reasons,
                "condition_count": len(normalized_conditions),
                "no_conditions_provided": not has_conditions,
            },
        )

    def _evaluate_predicate(
        self,
        predicate: Predicate,
        evidence: VerificationEvidence,
        blocked_reasons: list[str],
    ) -> bool:
        name = predicate.name
        args = predicate.args
        if name in _RUNTIME_BLOCKED_PREDICATES and blocked_reasons:
            return False

        if name == "at":
            return _check_at(args, evidence)
        if name == "visible_category":
            return _check_visible_category(args, evidence)
        if name == "reachable_category":
            return _check_reachable_category(args, evidence)
        if name == "holding_category":
            return _check_holding_category(args, evidence)
        if name == "near":
            return _check_near(args, evidence)
        if name == "observation_captured":
            return evidence.observation is not None
        if name == "arrived_at_candidate_anchor":
            return _check_arrived_at_candidate_anchor(evidence)
        if name == "object_presence_verified":
            return _check_object_presence_verified(args, evidence)
        if name == "object_secured":
            return _check_object_secured(args, evidence)
        if name == "returned_to_user":
            return _check_returned_to_user(evidence)
        if name == "task_goal_satisfied":
            return _check_task_goal_satisfied(args, evidence)
        return False


def _normalize_condition(item: Predicate | dict[str, Any] | list[Any]) -> Predicate:
    if isinstance(item, Predicate):
        return item.model_copy(deep=True)

    if isinstance(item, dict):
        name = item.get("name")
        args = item.get("args", [])
        if isinstance(name, str) and name:
            return Predicate(name=name, args=_normalize_args(args))

    if isinstance(item, list):
        if item and isinstance(item[0], str) and item[0]:
            return Predicate(name=item[0], args=[str(arg) for arg in item[1:]])

    return Predicate(name="__invalid_predicate__", args=[str(item)])


def _normalize_args(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [raw]
    return []


def _check_at(args: list[str], evidence: VerificationEvidence) -> bool:
    runtime = evidence.robot_runtime_state
    if runtime is None or runtime.viewpoint_id is None:
        return False
    if len(args) >= 2:
        return runtime.viewpoint_id == args[1]
    if len(args) == 1:
        return runtime.viewpoint_id == args[0]
    return False


def _check_visible_category(args: list[str], evidence: VerificationEvidence) -> bool:
    if evidence.observation is None or not args:
        return False
    target_category = args[0]
    return any(item.category == target_category for item in evidence.observation.visible_objects)


def _check_reachable_category(args: list[str], evidence: VerificationEvidence) -> bool:
    if not _check_visible_category(args, evidence):
        return False
    if not args:
        return False

    category = args[0]
    reachable_hints = _reachable_hints(evidence.execution_result)
    if reachable_hints["explicit_false"]:
        return False
    if category in reachable_hints["reachable_categories"]:
        return True
    if reachable_hints["explicit_true"]:
        return True
    return True


def _check_holding_category(args: list[str], evidence: VerificationEvidence) -> bool:
    runtime = evidence.robot_runtime_state
    if runtime is None or not runtime.is_holding_object:
        return False
    if len(args) < 2:
        return True
    return runtime.holding_object_category == args[1]


def _check_near(args: list[str], evidence: VerificationEvidence) -> bool:
    _ = args
    return _near_user(evidence.execution_result)


def _check_arrived_at_candidate_anchor(evidence: VerificationEvidence) -> bool:
    return _execution_success(evidence.execution_result)


def _check_object_presence_verified(args: list[str], evidence: VerificationEvidence) -> bool:
    if evidence.observation is None:
        return False
    visible_categories = {item.category for item in evidence.observation.visible_objects}
    visible_memory_ids = {
        item.memory_id for item in evidence.observation.visible_objects if item.memory_id
    }

    if len(args) >= 2:
        category, memory_id = args[0], args[1]
        return category in visible_categories and memory_id in visible_memory_ids
    if len(args) == 1:
        token = args[0]
        return token in visible_categories or token in visible_memory_ids
    return bool(evidence.observation.visible_objects)


def _check_object_secured(args: list[str], evidence: VerificationEvidence) -> bool:
    if len(args) >= 1:
        category = args[0]
        return _check_holding_category(["robot", category], evidence)
    return _check_holding_category([], evidence)


def _check_returned_to_user(evidence: VerificationEvidence) -> bool:
    return _near_user(evidence.execution_result)


def _check_task_goal_satisfied(args: list[str], evidence: VerificationEvidence) -> bool:
    if not args:
        return False
    category = args[0]

    fetch_style = _check_holding_category(["robot", category], evidence) and _near_user(
        evidence.execution_result
    )
    check_presence_style = _check_visible_category([category], evidence)
    return fetch_style or check_presence_style


def _execution_success(execution_result: dict[str, Any] | None) -> bool:
    if not isinstance(execution_result, dict):
        return False
    status = execution_result.get("status")
    if status == "success":
        return True
    state_delta = execution_result.get("state_delta")
    return isinstance(state_delta, dict) and state_delta.get("executor_status") == "success"


def _near_user(execution_result: dict[str, Any] | None) -> bool:
    for value in _find_values_by_key(execution_result, "near_user"):
        if isinstance(value, bool):
            return value
    return False


def _reachable_hints(execution_result: dict[str, Any] | None) -> dict[str, Any]:
    reachable_categories: set[str] = set()
    explicit_true = False
    explicit_false = False

    for value in _find_values_by_key(execution_result, "reachable_categories"):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    reachable_categories.add(item)

    for value in _find_values_by_key(execution_result, "reachable"):
        if value is True:
            explicit_true = True
        if value is False:
            explicit_false = True

    return {
        "reachable_categories": reachable_categories,
        "explicit_true": explicit_true,
        "explicit_false": explicit_false,
    }


def _runtime_blocked_reasons(execution_result: dict[str, Any] | None) -> list[str]:
    reasons: list[str] = []

    for updates in _find_values_by_key(execution_result, "runtime_object_updates_candidate"):
        if not isinstance(updates, list):
            continue
        for item in updates:
            if not isinstance(item, dict):
                continue
            reason = item.get("reason")
            if isinstance(reason, str) and reason in _RUNTIME_BLOCK_REASONS:
                reasons.append(reason)

    for flags in _find_values_by_key(execution_result, "local_world_state_flags"):
        if not isinstance(flags, dict):
            continue
        for key in ("target_dropped", "target_location_changed"):
            if flags.get(key) is True:
                reasons.append(key)

    return _dedup_preserve_order(reasons)


def _find_values_by_key(payload: Any, key: str) -> list[Any]:
    found: list[Any] = []
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for item_key, value in current.items():
                if item_key == key:
                    found.append(value)
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


__all__ = ["VerificationEngine", "VerificationResult"]
