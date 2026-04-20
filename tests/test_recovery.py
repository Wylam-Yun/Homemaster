from __future__ import annotations

from copy import deepcopy

from task_brain.domain import (
    Anchor,
    EmbodiedActionProgress,
    FailureAnalysis,
    FailureType,
    ObjectMemory,
    Observation,
    ObservationSource,
    ObservedObject,
    RecoveryAction,
    RecoveryDecision,
    RuntimeObjectUpdate,
    RuntimeState,
    SubgoalType,
)
from task_brain.recovery import (
    analyze_failure,
    apply_recovery_state_updates,
    decide_recovery,
)
from task_brain.verification import VerificationResult


def test_object_presence_failure_switches_candidate_and_writes_negative_evidence() -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-medicine-1")
    ranked_candidates = [
        {
            "memory_id": "mem-medicine-1",
            "anchor": {
                "room_id": "kitchen",
                "anchor_id": "kitchen_cabinet_1",
                "anchor_type": "cabinet",
            },
        },
        {
            "memory_id": "mem-medicine-2",
            "anchor": {
                "room_id": "living_room",
                "anchor_id": "living_side_table_1",
                "anchor_type": "table",
            },
        },
    ]
    failure_analysis = FailureAnalysis(
        failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
        reason="object_presence_not_verified",
        selected_candidate_id="mem-medicine-1",
    )

    decision = decide_recovery(
        failure_analysis=failure_analysis,
        runtime_state=runtime_state,
        ranked_candidates=ranked_candidates,
        target_category="medicine_box",
    )
    updated_state = apply_recovery_state_updates(
        runtime_state=runtime_state,
        failure_analysis=failure_analysis,
        recovery_decision=decision,
        ranked_candidates=ranked_candidates,
        target_category="medicine_box",
    )

    assert decision.action == RecoveryAction.SWITCH_CANDIDATE
    assert decision.next_candidate_id == "mem-medicine-2"

    assert updated_state.selected_candidate_id == "mem-medicine-2"
    assert updated_state.recent_failure_analysis == failure_analysis
    assert len(updated_state.task_negative_evidence) == 1
    assert updated_state.task_negative_evidence[0].location_key == "kitchen:kitchen_cabinet_1"
    assert (
        updated_state.candidate_exclusion_state["kitchen:kitchen_cabinet_1"]
        == "searched_not_found"
    )


def test_manipulation_failure_with_visible_target_retries_once() -> None:
    runtime_state = RuntimeState(
        retry_budget=0,
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
    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[],
        evidence={},
    )

    analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
        runtime_state=runtime_state,
        target_category="cup",
    )
    decision_first = decide_recovery(
        failure_analysis=analysis,
        runtime_state=runtime_state,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )
    state_after_first = apply_recovery_state_updates(
        runtime_state=runtime_state,
        failure_analysis=analysis,
        recovery_decision=decision_first,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )

    decision_second = decide_recovery(
        failure_analysis=analysis,
        runtime_state=state_after_first,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )

    assert decision_first.action == RecoveryAction.RETRY_SAME_SUBGOAL
    assert state_after_first.retry_budget == 1
    assert decision_second.action == RecoveryAction.REPLAN


def test_manipulation_failure_with_missing_target_reobserves_then_replans() -> None:
    runtime_state = RuntimeState(
        selected_candidate_id="mem-cup-1",
        embodied_action_progress=EmbodiedActionProgress(
            local_world_state_flags={"target_location_changed": True}
        ),
        runtime_object_updates=[
            RuntimeObjectUpdate(
                object_ref="mem-cup-1",
                source="mock_atomic_executor",
                reason="target_location_changed",
            )
        ],
    )
    verification_result = VerificationResult(
        passed=False,
        failed_conditions=[],
        evidence={"blocked_by_runtime_updates": ["target_location_changed"]},
    )

    analysis = analyze_failure(
        verification_result=verification_result,
        subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
        runtime_state=runtime_state,
        target_category="cup",
    )
    first_decision = decide_recovery(
        failure_analysis=analysis,
        runtime_state=runtime_state,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )
    state_after_first = apply_recovery_state_updates(
        runtime_state=runtime_state,
        failure_analysis=analysis,
        recovery_decision=first_decision,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )
    second_decision = decide_recovery(
        failure_analysis=analysis,
        runtime_state=state_after_first,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )

    assert first_decision.action == RecoveryAction.RE_OBSERVE
    assert second_decision.action == RecoveryAction.REPLAN


def test_candidate_exhausted_reports_failure() -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-cup-1")
    failure_analysis = FailureAnalysis(
        failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
        reason="object_presence_not_verified",
        selected_candidate_id="mem-cup-1",
    )

    decision = decide_recovery(
        failure_analysis=failure_analysis,
        runtime_state=runtime_state,
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
        target_category="cup",
    )

    assert decision.action == RecoveryAction.REPORT_FAILURE


def test_final_task_failure_requires_replan_or_failure_path() -> None:
    low_retry_runtime = RuntimeState(retry_budget=0)
    high_retry_runtime = RuntimeState(retry_budget=2)
    analysis = FailureAnalysis(
        failure_type=FailureType.FINAL_TASK_FAILURE,
        reason="task_goal_not_satisfied",
    )

    low_retry_decision = decide_recovery(
        failure_analysis=analysis,
        runtime_state=low_retry_runtime,
        ranked_candidates=[],
        target_category="cup",
    )
    high_retry_decision = decide_recovery(
        failure_analysis=analysis,
        runtime_state=high_retry_runtime,
        ranked_candidates=[],
        target_category="cup",
    )

    assert low_retry_decision.action == RecoveryAction.REPLAN
    assert high_retry_decision.action == RecoveryAction.REPORT_FAILURE


def test_recovery_updates_runtime_state_only_without_long_term_memory_side_effect() -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-medicine-1")
    runtime_snapshot = deepcopy(runtime_state.model_dump())
    memory_entries = [
        ObjectMemory(
            memory_id="mem-medicine-1",
            object_category="medicine_box",
            anchor=Anchor(
                room_id="kitchen",
                anchor_id="kitchen_cabinet_1",
                anchor_type="cabinet",
            ),
        )
    ]
    memory_snapshot = deepcopy([entry.model_dump() for entry in memory_entries])

    failure_analysis = FailureAnalysis(
        failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
        reason="object_presence_not_verified",
        selected_candidate_id="mem-medicine-1",
    )
    decision = RecoveryDecision(
        action=RecoveryAction.SWITCH_CANDIDATE,
        reason="switch_to_next_candidate",
        next_candidate_id="mem-medicine-2",
    )

    updated = apply_recovery_state_updates(
        runtime_state=runtime_state,
        failure_analysis=failure_analysis,
        recovery_decision=decision,
        ranked_candidates=[
            {
                "memory_id": "mem-medicine-1",
                "anchor": {
                    "room_id": "kitchen",
                    "anchor_id": "kitchen_cabinet_1",
                    "anchor_type": "cabinet",
                },
            },
            {
                "memory_id": "mem-medicine-2",
                "anchor": {
                    "room_id": "living_room",
                    "anchor_id": "living_side_table_1",
                    "anchor_type": "table",
                },
            },
        ],
        target_category="medicine_box",
    )

    assert runtime_state.model_dump() == runtime_snapshot
    assert updated.selected_candidate_id == "mem-medicine-2"
    assert [entry.model_dump() for entry in memory_entries] == memory_snapshot
