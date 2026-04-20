from __future__ import annotations

from datetime import UTC, datetime

from task_brain.domain import (
    FailureType,
    Observation,
    ObservationSource,
    ObservedObject,
    Predicate,
    RuntimeObjectUpdate,
    RuntimeState,
    SubgoalType,
)
from task_brain.recovery import analyze_failure, decide_recovery
from task_brain.verification import VerificationResult


def test_object_presence_failure_classification() -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-medicine-1")
    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[Predicate(name="visible_category", args=["medicine_box"])],
        evidence={},
    )

    analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.VERIFY_OBJECT_PRESENCE,
        runtime_state=runtime_state,
        target_category="medicine_box",
    )

    assert analysis.failure_type == FailureType.OBJECT_PRESENCE_FAILURE
    assert analysis.selected_candidate_id == "mem-medicine-1"
    assert analysis.failed_conditions[0].name == "visible_category"


def test_manipulation_failure_visible_vs_state_changed_classification() -> None:
    visible_runtime = RuntimeState(
        selected_candidate_id="mem-cup-1",
        current_observation=Observation(
            observation_id="obs-visible",
            source=ObservationSource.MOCK_WORLD,
            viewpoint_id="kitchen_table_viewpoint",
            room_id="kitchen",
            visible_objects=[
                ObservedObject(
                    observation_object_id="obs-cup-1",
                    category="cup",
                    memory_id="mem-cup-1",
                )
            ],
        ),
    )
    changed_runtime = RuntimeState(
        selected_candidate_id="mem-cup-1",
        runtime_object_updates=[
            RuntimeObjectUpdate(
                object_ref="mem-cup-1",
                source="mock_atomic_executor",
                reason="target_dropped",
            )
        ],
    )

    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[Predicate(name="object_secured", args=["cup"])],
        evidence={},
    )

    visible_analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
        runtime_state=visible_runtime,
        target_category="cup",
    )
    changed_analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
        runtime_state=changed_runtime,
        target_category="cup",
    )

    assert visible_analysis.failure_type == FailureType.MANIPULATION_FAILURE
    assert "target_still_visible" in (visible_analysis.reason or "")

    assert changed_analysis.failure_type == FailureType.MANIPULATION_FAILURE
    assert "state_changed" in (changed_analysis.reason or "")


def test_navigation_failure_classification() -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-cup-1")
    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[Predicate(name="arrived_at_candidate_anchor", args=["mem-cup-1"])],
        evidence={},
    )

    analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.NAVIGATE,
        runtime_state=runtime_state,
        target_category="cup",
    )

    assert analysis.failure_type == FailureType.NAVIGATION_FAILURE


def test_final_task_failure_classification() -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-cup-1")
    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[Predicate(name="task_goal_satisfied", args=["cup"])],
        evidence={"matched_signals": []},
    )

    analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.RETURN_TO_USER,
        runtime_state=runtime_state,
        target_category="cup",
    )

    assert analysis.failure_type == FailureType.FINAL_TASK_FAILURE


def test_same_failed_condition_maps_differently_with_different_evidence() -> None:
    ranked_candidates = [
        {
            "memory_id": "mem-cup-1",
            "anchor": {
                "room_id": "kitchen",
                "anchor_id": "kitchen_table_1",
                "anchor_type": "table",
            },
        }
    ]
    visible_runtime = RuntimeState(
        selected_candidate_id="mem-cup-1",
        current_observation=Observation(
            observation_id="obs-visible",
            source=ObservationSource.MOCK_WORLD,
            viewpoint_id="kitchen_table_viewpoint",
            room_id="kitchen",
            visible_objects=[
                ObservedObject(
                    observation_object_id="obs-cup-1",
                    category="cup",
                    memory_id="mem-cup-1",
                )
            ],
        ),
    )
    changed_runtime = RuntimeState(
        selected_candidate_id="mem-cup-1",
        runtime_object_updates=[
            RuntimeObjectUpdate(
                object_ref="mem-cup-1",
                source="mock_atomic_executor",
                reason="target_location_changed",
                timestamp=datetime(2026, 4, 20, tzinfo=UTC),
            )
        ],
    )
    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[Predicate(name="object_secured", args=["cup"])],
        evidence={},
    )

    visible_analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
        runtime_state=visible_runtime,
        target_category="cup",
    )
    changed_analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
        runtime_state=changed_runtime,
        target_category="cup",
    )

    visible_decision = decide_recovery(
        failure_analysis=visible_analysis,
        runtime_state=visible_runtime,
        ranked_candidates=ranked_candidates,
        target_category="cup",
    )
    changed_decision = decide_recovery(
        failure_analysis=changed_analysis,
        runtime_state=changed_runtime,
        ranked_candidates=ranked_candidates,
        target_category="cup",
    )

    assert visible_decision.action.value == "retry_same_subgoal"
    assert changed_decision.action.value == "re_observe"
