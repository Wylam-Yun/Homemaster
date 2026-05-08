"""Tests for reset_subtask_to_pending() added in P9."""

from __future__ import annotations

from homemaster.contracts import ExecutionState, SubtaskRuntimeState, VerificationResult
from homemaster.execution_state import reset_subtask_to_pending


def test_reset_subtask_to_pending_clears_status() -> None:
    state = ExecutionState(
        task_status="failed",
        subtasks=[
            SubtaskRuntimeState(
                subtask_id="find_cup",
                status="failed",
                last_verification_result=VerificationResult(
                    scope="subtask",
                    passed=False,
                    failed_reason="not found",
                ),
                last_observation={"target_object_visible": False},
            ),
        ],
        completed_subtask_ids=["find_cup"],
    )

    updated = reset_subtask_to_pending(state, "find_cup")

    assert updated.task_status == "running"
    assert updated.subtasks[0].status == "pending"
    assert updated.subtasks[0].last_verification_result is None
    assert updated.subtasks[0].last_observation is None


def test_reset_subtask_to_pending_removes_from_completed() -> None:
    state = ExecutionState(
        task_status="failed",
        subtasks=[
            SubtaskRuntimeState(subtask_id="find_cup", status="verified"),
            SubtaskRuntimeState(subtask_id="pick_cup", status="failed"),
        ],
        completed_subtask_ids=["find_cup"],
    )

    updated = reset_subtask_to_pending(state, "find_cup")

    assert "find_cup" not in updated.completed_subtask_ids
    # pick_cup is unaffected
    assert updated.subtasks[1].status == "failed"


def test_reset_subtask_to_pending_preserves_other_subtasks() -> None:
    state = ExecutionState(
        task_status="failed",
        subtasks=[
            SubtaskRuntimeState(subtask_id="find_cup", status="verified"),
            SubtaskRuntimeState(subtask_id="pick_cup", status="failed"),
        ],
        completed_subtask_ids=["find_cup"],
    )

    updated = reset_subtask_to_pending(state, "pick_cup")

    # find_cup remains verified
    assert updated.subtasks[0].status == "verified"
    assert "find_cup" in updated.completed_subtask_ids
    # pick_cup is reset
    assert updated.subtasks[1].status == "pending"
    assert updated.task_status == "running"
