"""Generic task-state tools — task_planner and task_progress_check.

These tools are model-owned: the model submits explicit plan/progress data.
No hidden state inference.
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.task_state.models import SubtaskStatus, TaskProgressUpdate
from homemaster.task_state.store import TaskStateStore, TaskStateStoreError


def _store(run_context: RunContext) -> TaskStateStore:
    value = run_context.deps.get("task_state_store")
    if not isinstance(value, TaskStateStore):
        raise TaskStateStoreError("no task_state_store in run_context.deps")
    return value


def task_planner_executor(
    *, arguments: dict[str, Any], run_context: RunContext,
) -> ToolResultMessage:
    store = _store(run_context)
    snapshot = store.create_or_replace_plan(
        goal=str(arguments["goal"]),
        subtasks=list(arguments["subtasks"]),
        current_subtask=arguments.get("current_subtask"),
        next_focus=arguments.get("next_focus"),
        open_questions=arguments.get("open_questions") or [],
        constraints=arguments.get("constraints") or [],
        updated_at_iteration=run_context.turn_index,
    )
    return ToolResultMessage(
        tool_call_id="",
        name="task_planner",
        content=[ContentBlock(text=snapshot.model_dump_json(indent=2))],
        data=snapshot.to_model_visible_dict(),
    )


def task_progress_check_executor(
    *, arguments: dict[str, Any], run_context: RunContext,
) -> ToolResultMessage:
    store = _store(run_context)
    raw_updates = arguments.get("updates", [])
    updates = [
        TaskProgressUpdate(
            subtask_id=item["subtask_id"],
            status=SubtaskStatus(item["status"]),
            evidence=item.get("evidence", []),
        )
        for item in raw_updates
    ]
    snapshot = store.apply_progress_updates(
        updates,
        current_subtask=arguments.get("current_subtask"),
        next_focus=arguments.get("next_focus"),
        updated_at_iteration=run_context.turn_index,
    )
    return ToolResultMessage(
        tool_call_id="",
        name="task_progress_check",
        content=[ContentBlock(text=snapshot.model_dump_json(indent=2))],
        data=snapshot.to_model_visible_dict(),
    )
