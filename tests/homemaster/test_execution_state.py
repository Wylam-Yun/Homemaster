from __future__ import annotations

from homemaster.contracts import (
    ExecutionState,
    SubtaskRuntimeState,
    VerificationResult,
)
from homemaster.execution_state import (
    append_failure_record_id,
    mark_subtask_verified,
    next_ready_subtasks,
)


def test_grounded_fetch_cup_initial_state_serializes() -> None:
    state = ExecutionState(
        task_status="running",
        current_subtask_id="find_cup",
        user_location="客厅沙发旁",
        current_location="厨房门口",
        subtasks=[
            SubtaskRuntimeState(subtask_id="find_cup", status="pending"),
            SubtaskRuntimeState(
                subtask_id="pick_cup", status="pending", depends_on=["find_cup"]
            ),
            SubtaskRuntimeState(
                subtask_id="deliver_cup", status="pending", depends_on=["pick_cup"]
            ),
        ],
        negative_evidence=[
            {
                "memory_id": "mem-cup-2",
                "location_key": "living_room:side_table",
                "reason": "not_visible",
            }
        ],
    )

    decoded = ExecutionState.model_validate_json(state.model_dump_json())

    assert decoded == state
    assert decoded.user_location == "客厅沙发旁"
    assert decoded.negative_evidence[0]["location_key"] == "living_room:side_table"


def test_next_ready_subtasks_respects_dependencies() -> None:
    state = ExecutionState(
        task_status="running",
        subtasks=[
            SubtaskRuntimeState(subtask_id="find_cup", status="verified"),
            SubtaskRuntimeState(
                subtask_id="pick_cup", status="pending", depends_on=["find_cup"]
            ),
            SubtaskRuntimeState(
                subtask_id="deliver_cup", status="pending", depends_on=["pick_cup"]
            ),
        ],
    )

    assert next_ready_subtasks(state) == ["pick_cup"]


def test_mark_subtask_verified_records_visibility_for_operation_precondition() -> None:
    state = ExecutionState(
        task_status="running",
        current_subtask_id="find_cup",
        subtasks=[SubtaskRuntimeState(subtask_id="find_cup", status="running")],
    )
    verification = VerificationResult(
        scope="subtask",
        passed=True,
        verified_facts=["观察到水杯"],
        confidence=0.9,
    )

    updated = mark_subtask_verified(
        state,
        "find_cup",
        verification,
        observation={"target_object_visible": True, "target_object_location": "厨房餐桌"},
    )

    assert updated.subtasks[0].status == "verified"
    assert updated.target_object_visible is True
    assert updated.target_object_location == "厨房餐桌"
    assert updated.completed_subtask_ids == ["find_cup"]


def test_mark_subtask_verified_records_held_object_for_delivery_precondition() -> None:
    state = ExecutionState(
        task_status="running",
        current_subtask_id="pick_cup",
        subtasks=[SubtaskRuntimeState(subtask_id="pick_cup", status="running")],
    )
    verification = VerificationResult(scope="subtask", passed=True, confidence=0.85)

    updated = mark_subtask_verified(
        state,
        "pick_cup",
        verification,
        observation={"held_object": "水杯"},
    )

    assert updated.held_object == "水杯"
    assert updated.completed_subtask_ids == ["pick_cup"]


def test_append_failure_record_id_updates_subtask_and_task_state() -> None:
    state = ExecutionState(
        task_status="running",
        subtasks=[SubtaskRuntimeState(subtask_id="find_cup", status="running")],
    )

    updated = append_failure_record_id(state, "find_cup", "failure-1")

    assert updated.failure_record_ids == ["failure-1"]
    assert updated.subtasks[0].failure_record_ids == ["failure-1"]
    assert updated.subtasks[0].attempt_count == 1
