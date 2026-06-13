"""Tests for TaskStateStore."""

from __future__ import annotations

import pytest

from homemaster.task_state.models import TaskProgressUpdate, TaskStatus
from homemaster.task_state.store import TaskStateStore, TaskStateStoreError


def test_create_plan_normalizes_snapshot() -> None:
    store = TaskStateStore(run_id="r1")

    snapshot = store.create_or_replace_plan(
        goal="put a hot apple in fridge",
        subtasks=[
            {"id": "find_apple", "description": "Find an apple."},
            {"id": "heat_apple", "description": "Heat the apple.", "status": "in_progress"},
        ],
        current_subtask="heat_apple",
        next_focus="Use the microwave.",
    )

    assert snapshot.status == TaskStatus.ACTIVE
    assert snapshot.current_subtask == "heat_apple"
    assert snapshot.subtasks[0].status == "pending"
    assert snapshot.subtasks[1].status == "in_progress"


def test_duplicate_subtask_ids_fail() -> None:
    store = TaskStateStore(run_id="r1")

    with pytest.raises(TaskStateStoreError, match="duplicate subtask id"):
        store.create_or_replace_plan(
            goal="goal",
            subtasks=[
                {"id": "a", "description": "A"},
                {"id": "a", "description": "B"},
            ],
        )


def test_progress_update_requires_existing_subtask() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(goal="goal", subtasks=[{"id": "a", "description": "A"}])

    with pytest.raises(TaskStateStoreError, match="unknown subtask"):
        store.apply_progress_updates([
            TaskProgressUpdate(subtask_id="missing", status="completed", evidence=["done"])
        ])


def test_completed_snapshot_model_view_is_bounded() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A", "evidence": ["e1", "e2", "e3"]}],
    )
    snapshot = store.mark_completed(final_summary="completed goal")

    visible = snapshot.to_model_visible_dict(max_evidence_per_subtask=2)

    assert visible["status"] == "completed"
    assert visible["completion_summary"] == "completed goal"
    assert visible["subtasks"][0]["evidence"] == ["e2", "e3"]


def test_progress_update_applies_status_and_evidence() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[
            {"id": "a", "description": "A"},
            {"id": "b", "description": "B"},
        ],
    )
    snapshot = store.apply_progress_updates([
        TaskProgressUpdate(subtask_id="a", status="in_progress", evidence=["started"]),
    ])

    assert snapshot.subtasks[0].status == "in_progress"
    assert snapshot.subtasks[0].evidence == ["started"]


def test_empty_subtasks_fail() -> None:
    store = TaskStateStore(run_id="r1")
    with pytest.raises(TaskStateStoreError, match="subtasks must not be empty"):
        store.create_or_replace_plan(goal="goal", subtasks=[])
