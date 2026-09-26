from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import (
    TaskStateStore,
    TaskStateTransitionError,
)
from homemaster.task_state.tools import make_task_progress_check_tool
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext


def _context(store: TaskStateStore) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=7,
        tool_call_id="task-progress-test",
        internal_tool_id="homemaster.task_progress_check.v1",
        permission_subject=PermissionSubject(subject_id="test", channel="pytest"),
        backend=None,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path.cwd(),
        services={"task_state_store": store},
    )


def _store() -> TaskStateStore:
    store = TaskStateStore(run_id="run")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )
    return store


def test_task_status_transition_table_executes_pause_resume_and_terminal_states() -> None:
    store = _store()

    assert store.update_status(TaskStatus.PAUSED).status is TaskStatus.PAUSED
    assert store.update_status(TaskStatus.ACTIVE).status is TaskStatus.ACTIVE
    assert store.update_status(TaskStatus.FAILED).status is TaskStatus.FAILED

    with pytest.raises(TaskStateTransitionError) as error:
        store.update_status(TaskStatus.ACTIVE)
    assert error.value.current is TaskStatus.FAILED
    assert error.value.requested is TaskStatus.ACTIVE


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_terminal_task_status_cannot_be_resumed(terminal: str) -> None:
    store = _store()
    if terminal == "completed":
        store.mark_completed(final_summary="done")
    else:
        store.update_status(TaskStatus(terminal))

    with pytest.raises(TaskStateTransitionError, match=f"{terminal} -> active"):
        store.update_status(TaskStatus.ACTIVE)


@pytest.mark.asyncio
async def test_task_tool_executes_every_declared_non_completion_status() -> None:
    store = _store()
    tool = make_task_progress_check_tool()

    paused = await tool.executor.execute({"updates": [], "task_status": "paused"}, _context(store))
    active = await tool.executor.execute({"updates": [], "task_status": "active"}, _context(store))
    cancelled = await tool.executor.execute(
        {"updates": [], "task_status": "cancelled"}, _context(store)
    )

    assert paused.data is not None and paused.data["status"] == "paused"
    assert active.data is not None and active.data["status"] == "active"
    assert cancelled.data is not None and cancelled.data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_invalid_task_status_rejects_before_progress_mutation() -> None:
    store = _store()
    store.update_status(TaskStatus.FAILED)
    tool = make_task_progress_check_tool()

    with pytest.raises(TaskStateTransitionError):
        await tool.executor.execute(
            {
                "updates": [{"subtask_id": "a", "status": "completed", "evidence": ["late"]}],
                "task_status": "active",
            },
            _context(store),
        )

    assert store.snapshot is not None
    assert store.snapshot.subtasks[0].status == "pending"
    assert store.snapshot.subtasks[0].evidence == []
