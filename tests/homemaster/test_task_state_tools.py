"""Tests for generic model-owned task-state tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.agent.normalized import RunContext
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.task_state.store import TaskStateStore
from homemaster.task_state.tools import task_planner_executor, task_progress_check_executor


def _run_context(store: TaskStateStore) -> RunContext:
    settings = RuntimeSettings(
        run_id="r1",
        runtime_root=Path("/tmp/homemaster/runs"),
        debug_root=Path("/tmp/homemaster/debug"),
        results_root=Path("/tmp/homemaster/results"),
    )
    return RunContext(
        session_id="s1",
        run_id="r1",
        turn_index=3,
        settings=settings,
        event_sink=None,
        deps={"task_state_store": store},
    )


def test_task_planner_stores_model_submitted_plan() -> None:
    store = TaskStateStore(run_id="r1")

    result = task_planner_executor(
        arguments={
            "goal": "put a hot apple in fridge",
            "subtasks": [
                {"id": "find_apple", "description": "Find apple."},
                {
                    "id": "heat_apple",
                    "description": "Heat apple.",
                    "status": "in_progress",
                    "evidence": ["holding apple"],
                },
            ],
            "current_subtask": "heat_apple",
            "next_focus": "Use the microwave.",
        },
        run_context=_run_context(store),
    )

    assert result.name == "task_planner"
    assert result.data is not None
    assert result.data["goal"] == "put a hot apple in fridge"
    assert result.data["updated_at_iteration"] == 3
    assert store.snapshot is not None
    assert store.snapshot.current_subtask == "heat_apple"


def test_task_progress_check_updates_only_explicit_subtasks() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[
            {"id": "a", "description": "A"},
            {"id": "b", "description": "B"},
        ],
    )

    result = task_progress_check_executor(
        arguments={
            "updates": [
                {
                    "subtask_id": "a",
                    "status": "completed",
                    "evidence": ["observed model-visible success"],
                }
            ],
            "current_subtask": "b",
            "next_focus": "Work on B.",
        },
        run_context=_run_context(store),
    )

    assert result.name == "task_progress_check"
    assert result.data is not None
    assert result.data["subtasks"][0]["status"] == "completed"
    assert result.data["subtasks"][1]["status"] == "pending"
    assert store.snapshot is not None
    assert store.snapshot.current_subtask == "b"


def test_task_progress_check_can_mark_task_completed_explicitly() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    result = task_progress_check_executor(
        arguments={
            "updates": [
                {
                    "subtask_id": "a",
                    "status": "completed",
                    "evidence": ["done"],
                }
            ],
            "task_status": "completed",
            "completion_summary": "Goal completed.",
        },
        run_context=_run_context(store),
    )

    assert result.data is not None
    assert result.data["status"] == "completed"
    assert result.data["completion_summary"] == "Goal completed."
    assert store.snapshot is not None
    assert store.snapshot.current_subtask is None


def test_task_progress_check_requires_store_in_run_context() -> None:
    settings = RuntimeSettings(
        run_id="r1",
        runtime_root=Path("/tmp/homemaster/runs"),
        debug_root=Path("/tmp/homemaster/debug"),
        results_root=Path("/tmp/homemaster/results"),
    )
    run_context = RunContext(
        session_id="s1",
        run_id="r1",
        turn_index=0,
        settings=settings,
        event_sink=None,
        deps={},
    )

    with pytest.raises(RuntimeError, match="no task_state_store"):
        task_progress_check_executor(
            arguments={"updates": []},
            run_context=run_context,
        )
