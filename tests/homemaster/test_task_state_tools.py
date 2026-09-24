"""Tests for generic model-owned task-state tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from homemaster.task_state.store import TaskStateStore, TaskStateStoreError
from homemaster.task_state.tools import (
    make_task_planner_tool,
    make_task_progress_check_tool,
)


def _context(store: TaskStateStore, *, turn_index: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        turn_index=turn_index,
        services={"task_state_store": store},
        metadata={},
    )


@pytest.mark.asyncio
async def test_task_planner_stores_model_submitted_plan() -> None:
    store = TaskStateStore(run_id="r1")

    result = await make_task_planner_tool().executor.execute(
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
        context=_context(store),
    )

    assert result.success
    assert result.data["goal"] == "put a hot apple in fridge"
    assert result.data["updated_at_iteration"] == 3
    assert store.snapshot is not None
    assert store.snapshot.current_subtask == "heat_apple"


@pytest.mark.asyncio
async def test_task_progress_check_updates_only_explicit_subtasks() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[
            {"id": "a", "description": "A"},
            {"id": "b", "description": "B"},
        ],
    )

    result = await make_task_progress_check_tool().executor.execute(
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
        context=_context(store),
    )

    assert result.success
    assert result.data["subtasks"][0]["status"] == "completed"
    assert result.data["subtasks"][1]["status"] == "pending"
    assert store.snapshot is not None
    assert store.snapshot.current_subtask == "b"


@pytest.mark.asyncio
async def test_task_progress_check_can_mark_task_completed_explicitly() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    result = await make_task_progress_check_tool().executor.execute(
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
        context=_context(store),
    )

    assert result.data is not None
    assert result.data["status"] == "completed"
    assert result.data["completion_summary"] == "Goal completed."
    assert store.snapshot is not None
    assert store.snapshot.current_subtask is None


@pytest.mark.asyncio
async def test_task_progress_check_requires_store_in_run_context() -> None:

    with pytest.raises(TaskStateStoreError, match="no task_state_store"):
        await make_task_progress_check_tool().executor.execute(
            arguments={"updates": []},
            context=_context(None),
        )


@pytest.mark.asyncio
async def test_task_progress_check_accepts_string_evidence_for_model_recovery() -> None:
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    result = await make_task_progress_check_tool().executor.execute(
        arguments={
            "updates": [
                {
                    "subtask_id": "a",
                    "status": "completed",
                    "evidence": "single evidence item",
                }
            ],
        },
        context=_context(store),
    )

    assert result.data is not None
    assert result.data["subtasks"][0]["evidence"] == ["single evidence item"]


def test_task_state_tool_schemas_describe_nested_fields() -> None:
    planner = make_task_planner_tool()
    progress = make_task_progress_check_tool()

    assert planner.description
    assert progress.description

    for property_schema in planner.input_schema["properties"].values():
        assert property_schema.get("description")
    for property_schema in progress.input_schema["properties"].values():
        assert property_schema.get("description")

    subtask_schema = planner.input_schema["properties"]["subtasks"]["items"]
    assert {"id", "description"}.issubset(set(subtask_schema["required"]))
    assert "status" in subtask_schema["properties"]
    assert subtask_schema["properties"]["evidence"]["items"]["type"] == "string"
    for property_schema in subtask_schema["properties"].values():
        assert property_schema.get("description")

    update_schema = progress.input_schema["properties"]["updates"]["items"]
    assert {"subtask_id", "status"}.issubset(set(update_schema["required"]))
    assert update_schema["properties"]["evidence"]["type"] == "array"
    for property_schema in update_schema["properties"].values():
        assert property_schema.get("description")
