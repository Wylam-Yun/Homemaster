"""Failure analysis and recovery policy for Phase A."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from task_brain.domain import (
    Anchor,
    FailureAnalysis,
    FailureType,
    RecoveryAction,
    RecoveryDecision,
    RuntimeState,
    SubgoalType,
    TaskNegativeEvidence,
)
from task_brain.memory import anchor_to_location_key
from task_brain.verification import VerificationResult

_NAVIGATION_FAILURE_PREDICATES = {"arrived_at_candidate_anchor", "at"}
_OBJECT_PRESENCE_FAILURE_PREDICATES = {
    "object_presence_verified",
    "visible_category",
    "reachable_category",
}
_MANIPULATION_FAILURE_PREDICATES = {
    "object_secured",
    "holding_category",
    "returned_to_user",
}
_FINAL_TASK_FAILURE_PREDICATES = {"task_goal_satisfied"}
_RUNTIME_CHANGE_REASONS = {"target_dropped", "target_location_changed"}
_UNKNOWN_TARGET_TOKENS = {"", "unknown", "unknown_object", "unknown_target"}


def analyze_failure(
    *,
    verification_result: VerificationResult,
    subgoal_type: SubgoalType | str,
    runtime_state: RuntimeState,
    target_category: str | None = None,
) -> FailureAnalysis:
    """Classify verification failure into one failure type with explainable reason."""
    normalized_subgoal = _normalize_subgoal_type(subgoal_type)
    failed_conditions = [
        item.model_copy(deep=True) for item in verification_result.failed_conditions
    ]
    failed_names = {item.name for item in failed_conditions}

    if _is_final_failure(normalized_subgoal, failed_names):
        return FailureAnalysis(
            failure_type=FailureType.FINAL_TASK_FAILURE,
            failed_conditions=failed_conditions,
            reason="task_goal_not_satisfied",
            selected_candidate_id=runtime_state.selected_candidate_id,
        )

    if _is_navigation_failure(normalized_subgoal, failed_names):
        return FailureAnalysis(
            failure_type=FailureType.NAVIGATION_FAILURE,
            failed_conditions=failed_conditions,
            reason="navigation_not_arrived",
            selected_candidate_id=runtime_state.selected_candidate_id,
        )

    if _is_object_presence_failure(normalized_subgoal, failed_names):
        return FailureAnalysis(
            failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
            failed_conditions=failed_conditions,
            reason="object_presence_not_verified",
            selected_candidate_id=runtime_state.selected_candidate_id,
        )

    reason = "manipulation_target_not_visible"
    if _has_runtime_state_change_signal(runtime_state, verification_result):
        reason = "manipulation_target_state_changed"
    elif _target_visible(runtime_state, target_category):
        reason = "manipulation_target_still_visible"

    return FailureAnalysis(
        failure_type=FailureType.MANIPULATION_FAILURE,
        failed_conditions=failed_conditions,
        reason=reason,
        selected_candidate_id=runtime_state.selected_candidate_id,
    )


def decide_recovery(
    *,
    failure_analysis: FailureAnalysis,
    runtime_state: RuntimeState,
    ranked_candidates: Sequence[Mapping[str, Any]],
    target_category: str | None = None,
) -> RecoveryDecision:
    """Decide recovery action from failure analysis and runtime state."""
    next_candidate_id = _select_next_candidate_id(
        ranked_candidates=ranked_candidates,
        runtime_state=runtime_state,
        selected_candidate_id=failure_analysis.selected_candidate_id,
    )

    if failure_analysis.failure_type == FailureType.OBJECT_PRESENCE_FAILURE:
        if next_candidate_id is not None:
            return RecoveryDecision(
                action=RecoveryAction.SWITCH_CANDIDATE,
                reason="object_presence_failure_switch_candidate",
                next_candidate_id=next_candidate_id,
            )
        if _target_category_unknown(target_category):
            return RecoveryDecision(
                action=RecoveryAction.ASK_CLARIFICATION,
                reason="candidate_exhausted_and_target_unknown",
            )
        return RecoveryDecision(
            action=RecoveryAction.REPORT_FAILURE,
            reason="candidate_exhausted_report_failure",
        )

    if failure_analysis.failure_type == FailureType.MANIPULATION_FAILURE:
        if "state_changed" in (failure_analysis.reason or "") or _has_runtime_state_change_signal(
            runtime_state,
            None,
        ):
            if _recent_state_changed_manipulation_failure(runtime_state):
                return RecoveryDecision(
                    action=RecoveryAction.REPLAN,
                    reason="manipulation_state_changed_replan",
                )
            return RecoveryDecision(
                action=RecoveryAction.RE_OBSERVE,
                reason="manipulation_state_changed_reobserve",
            )

        if _target_visible(runtime_state, target_category):
            if runtime_state.retry_budget < 1:
                return RecoveryDecision(
                    action=RecoveryAction.RETRY_SAME_SUBGOAL,
                    reason="manipulation_target_visible_retry_once",
                )
            return RecoveryDecision(
                action=RecoveryAction.REPLAN,
                reason="manipulation_retry_exhausted_replan",
            )

        return RecoveryDecision(
            action=RecoveryAction.RE_OBSERVE,
            reason="manipulation_target_missing_reobserve",
        )

    if failure_analysis.failure_type == FailureType.NAVIGATION_FAILURE:
        if next_candidate_id is not None:
            return RecoveryDecision(
                action=RecoveryAction.SWITCH_CANDIDATE,
                reason="navigation_failure_switch_candidate",
                next_candidate_id=next_candidate_id,
            )
        return RecoveryDecision(
            action=RecoveryAction.REPLAN,
            reason="navigation_failure_replan",
        )

    if failure_analysis.failure_type == FailureType.FINAL_TASK_FAILURE:
        if runtime_state.retry_budget >= 1:
            return RecoveryDecision(
                action=RecoveryAction.REPORT_FAILURE,
                reason="final_task_failure_retry_exhausted",
            )
        return RecoveryDecision(
            action=RecoveryAction.REPLAN,
            reason="final_task_failure_replan",
        )

    return RecoveryDecision(
        action=RecoveryAction.CONTINUE,
        reason="no_recovery_needed",
    )


def apply_recovery_state_updates(
    *,
    runtime_state: RuntimeState,
    failure_analysis: FailureAnalysis,
    recovery_decision: RecoveryDecision,
    ranked_candidates: Sequence[Mapping[str, Any]] | None = None,
    target_category: str | None = None,
) -> RuntimeState:
    """Apply recovery decision into task-scoped RuntimeState only."""
    updated = runtime_state.model_copy(deep=True)
    updated.recent_failure_analysis = failure_analysis.model_copy(deep=True)

    if recovery_decision.action == RecoveryAction.RETRY_SAME_SUBGOAL:
        updated.retry_budget += 1

    if (
        failure_analysis.failure_type == FailureType.OBJECT_PRESENCE_FAILURE
        and ranked_candidates is not None
    ):
        negative_evidence = _build_task_negative_evidence(
            runtime_state=runtime_state,
            failure_analysis=failure_analysis,
            ranked_candidates=ranked_candidates,
            target_category=target_category,
        )
        _append_negative_evidence_if_new(updated, negative_evidence)
        updated.candidate_exclusion_state[negative_evidence.location_key] = negative_evidence.status

    if (
        recovery_decision.action == RecoveryAction.SWITCH_CANDIDATE
        and recovery_decision.next_candidate_id is not None
    ):
        updated.selected_candidate_id = recovery_decision.next_candidate_id

    return updated


def _is_final_failure(subgoal_type: SubgoalType | None, failed_names: set[str]) -> bool:
    if failed_names & _FINAL_TASK_FAILURE_PREDICATES:
        return True
    return subgoal_type == SubgoalType.RETURN_TO_USER and "returned_to_user" in failed_names


def _is_navigation_failure(subgoal_type: SubgoalType | None, failed_names: set[str]) -> bool:
    if subgoal_type == SubgoalType.NAVIGATE:
        return True
    return bool(failed_names & _NAVIGATION_FAILURE_PREDICATES)


def _is_object_presence_failure(subgoal_type: SubgoalType | None, failed_names: set[str]) -> bool:
    if subgoal_type == SubgoalType.VERIFY_OBJECT_PRESENCE:
        return True
    if subgoal_type == SubgoalType.OBSERVE and "observation_captured" in failed_names:
        return True
    return bool(failed_names & _OBJECT_PRESENCE_FAILURE_PREDICATES)


def _normalize_subgoal_type(value: SubgoalType | str) -> SubgoalType | None:
    if isinstance(value, SubgoalType):
        return value
    if not isinstance(value, str):
        return None
    try:
        return SubgoalType(value)
    except ValueError:
        return None


def _has_runtime_state_change_signal(
    runtime_state: RuntimeState,
    verification_result: VerificationResult | None,
) -> bool:
    for update in runtime_state.runtime_object_updates:
        if update.reason in _RUNTIME_CHANGE_REASONS:
            return True

    progress = runtime_state.embodied_action_progress
    if progress is not None:
        for key in _RUNTIME_CHANGE_REASONS:
            if progress.local_world_state_flags.get(key):
                return True

    if verification_result is not None:
        blocked_by = verification_result.evidence.get("blocked_by_runtime_updates")
        if isinstance(blocked_by, list):
            for item in blocked_by:
                if isinstance(item, str) and item in _RUNTIME_CHANGE_REASONS:
                    return True

    return False


def _target_visible(runtime_state: RuntimeState, target_category: str | None) -> bool:
    if runtime_state.current_observation is None:
        return False

    normalized_target = _normalize_text(target_category)
    if normalized_target in _UNKNOWN_TARGET_TOKENS:
        return bool(runtime_state.current_observation.visible_objects)

    for obj in runtime_state.current_observation.visible_objects:
        if _normalize_text(obj.category) == normalized_target:
            return True
    return False


def _recent_state_changed_manipulation_failure(runtime_state: RuntimeState) -> bool:
    recent = runtime_state.recent_failure_analysis
    if recent is None:
        return False
    if recent.failure_type != FailureType.MANIPULATION_FAILURE:
        return False
    return "state_changed" in (recent.reason or "")


def _target_category_unknown(target_category: str | None) -> bool:
    return _normalize_text(target_category) in _UNKNOWN_TARGET_TOKENS


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _select_next_candidate_id(
    *,
    ranked_candidates: Sequence[Mapping[str, Any]],
    runtime_state: RuntimeState,
    selected_candidate_id: str | None,
) -> str | None:
    current_id = selected_candidate_id or runtime_state.selected_candidate_id

    for candidate in ranked_candidates:
        candidate_id = _candidate_id(candidate)
        if candidate_id is None:
            continue
        if candidate_id == current_id:
            continue
        if _candidate_excluded(candidate, runtime_state):
            continue
        return candidate_id

    return None


def _candidate_excluded(candidate: Mapping[str, Any], runtime_state: RuntimeState) -> bool:
    candidate_id = _candidate_id(candidate)
    if candidate_id and candidate_id in runtime_state.candidate_exclusion_state:
        return True

    location_key = _candidate_location_key(candidate)
    if location_key is None:
        return False
    status = runtime_state.candidate_exclusion_state.get(location_key)
    return status == "searched_not_found"


def _candidate_id(candidate: Mapping[str, Any]) -> str | None:
    for key in ("memory_id", "candidate_id", "id"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _candidate_location_key(candidate: Mapping[str, Any]) -> str | None:
    anchor_payload = candidate.get("anchor")
    if not isinstance(anchor_payload, Mapping):
        return None

    room_id = anchor_payload.get("room_id")
    anchor_id = anchor_payload.get("anchor_id")
    if not isinstance(room_id, str) or not isinstance(anchor_id, str):
        return None
    return f"{room_id}:{anchor_id}"


def _build_task_negative_evidence(
    *,
    runtime_state: RuntimeState,
    failure_analysis: FailureAnalysis,
    ranked_candidates: Sequence[Mapping[str, Any]],
    target_category: str | None,
) -> TaskNegativeEvidence:
    selected_candidate_id = (
        failure_analysis.selected_candidate_id or runtime_state.selected_candidate_id
    )
    selected_candidate = _find_candidate_by_id(ranked_candidates, selected_candidate_id)
    anchor = _candidate_anchor(selected_candidate)

    if anchor is not None:
        location_key = anchor_to_location_key(anchor)
    else:
        location_key = "unknown:unknown"

    return TaskNegativeEvidence(
        location_key=location_key,
        status="searched_not_found",
        reason="searched_not_found",
        object_category=target_category,
        anchor=anchor,
        evidence={
            "failure_type": failure_analysis.failure_type.value,
            "failure_reason": failure_analysis.reason,
            "selected_candidate_id": selected_candidate_id,
        },
    )


def _find_candidate_by_id(
    ranked_candidates: Sequence[Mapping[str, Any]],
    candidate_id: str | None,
) -> Mapping[str, Any] | None:
    if candidate_id is None:
        return None
    for candidate in ranked_candidates:
        if _candidate_id(candidate) == candidate_id:
            return candidate
    return None


def _candidate_anchor(candidate: Mapping[str, Any] | None) -> Anchor | None:
    if candidate is None:
        return None

    payload = candidate.get("anchor")
    if not isinstance(payload, Mapping):
        return None

    room_id = payload.get("room_id")
    anchor_id = payload.get("anchor_id")
    if not isinstance(room_id, str) or not isinstance(anchor_id, str):
        return None

    anchor_type = payload.get("anchor_type")
    return Anchor(
        room_id=room_id,
        anchor_id=anchor_id,
        anchor_type=anchor_type if isinstance(anchor_type, str) else "unknown",
        viewpoint_id=payload.get("viewpoint_id")
        if isinstance(payload.get("viewpoint_id"), str)
        else None,
        display_text=payload.get("display_text")
        if isinstance(payload.get("display_text"), str)
        else None,
    )


def _append_negative_evidence_if_new(
    runtime_state: RuntimeState,
    negative_evidence: TaskNegativeEvidence,
) -> None:
    for item in runtime_state.task_negative_evidence:
        if (
            item.location_key == negative_evidence.location_key
            and item.status == negative_evidence.status
            and item.object_category == negative_evidence.object_category
        ):
            return
    runtime_state.task_negative_evidence.append(negative_evidence)


__all__ = [
    "analyze_failure",
    "apply_recovery_state_updates",
    "decide_recovery",
]
