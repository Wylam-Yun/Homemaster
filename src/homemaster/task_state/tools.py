"""Generic task-state tools — task_planner and task_progress_check.

These tools are model-owned: the model submits explicit plan/progress data.
No hidden state inference.
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.task_state.models import SubtaskStatus, TaskProgressUpdate, TaskStatus
from homemaster.task_state.store import TaskStateStore, TaskStateStoreError
from homemaster.tools.contracts import ToolExecutionResult
from homemaster.tools.spec import ToolSpec


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


def _evidence_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def task_progress_check_executor(
    *, arguments: dict[str, Any], run_context: RunContext,
) -> ToolResultMessage | ToolExecutionResult:
    store = _store(run_context)
    raw_task_status = arguments.get("task_status")
    task_status = (
        store.validate_status_transition(TaskStatus(raw_task_status))
        if raw_task_status is not None
        else None
    )
    raw_updates = arguments.get("updates", [])
    updates = [
        TaskProgressUpdate(
            subtask_id=item["subtask_id"],
            status=SubtaskStatus(item["status"]),
            evidence=_evidence_list(item.get("evidence")),
        )
        for item in raw_updates
    ]
    snapshot = store.apply_progress_updates(
        updates,
        current_subtask=arguments.get("current_subtask"),
        next_focus=arguments.get("next_focus"),
        updated_at_iteration=run_context.turn_index,
    )
    if task_status is not None:
        if task_status is TaskStatus.COMPLETED:
            completion_guard = run_context.deps.get("task_completion_guard")
            if completion_guard is not None:
                if not callable(completion_guard):
                    raise TaskStateStoreError("task_completion_guard must be callable")
                blocked = completion_guard()
                if blocked is not None:
                    if not isinstance(blocked, ToolExecutionResult):
                        raise TaskStateStoreError(
                            "task_completion_guard must return ToolExecutionResult or None"
                        )
                    return blocked
            snapshot = store.mark_completed(
                final_summary=arguments.get("completion_summary") or "Task completed.",
                updated_at_iteration=run_context.turn_index,
            )
        else:
            snapshot = store.update_status(
                task_status,
                updated_at_iteration=run_context.turn_index,
            )
    return ToolResultMessage(
        tool_call_id="",
        name="task_progress_check",
        content=[ContentBlock(text=snapshot.model_dump_json(indent=2))],
        data=snapshot.to_model_visible_dict(),
    )


def make_task_planner_tool() -> ToolSpec:
    return ToolSpec(
        name="task_planner",
        description="Create or replace the model-owned task plan snapshot.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The task goal."},
                "subtasks": {
                    "type": "array",
                    "description": (
                        "Concise subtask list. Use stable string ids and refer to those "
                        "ids from current_subtask and next_focus."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable subtask id, such as '1' or 'find_mug'.",
                            },
                            "description": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "blocked",
                                    "cancelled",
                                    "uncertain",
                                ],
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Model-visible evidence supporting the subtask status."
                                ),
                            },
                        },
                        "required": ["id", "description"],
                    },
                },
                "current_subtask": {
                    "type": "string",
                    "description": "Subtask id currently being worked on.",
                },
                "next_focus": {
                    "type": "string",
                    "description": "Subtask id or short focus statement for the next action.",
                },
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal", "subtasks"],
        },
        executor_mode="programmatic",
        executor=task_planner_executor,
    )


def make_task_progress_check_tool() -> ToolSpec:
    return ToolSpec(
        name="task_progress_check",
        description="Update progress on subtasks with explicit status and evidence.",
        input_schema={
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "description": "Explicit progress updates for known subtask ids.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subtask_id": {
                                "type": "string",
                                "description": "Existing subtask id from the task plan.",
                            },
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "blocked",
                                    "cancelled",
                                    "uncertain",
                                ],
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Model-visible evidence. If there is one item, still "
                                    "pass it as an array."
                                ),
                            },
                        },
                        "required": ["subtask_id", "status"],
                    },
                },
                "current_subtask": {
                    "type": "string",
                    "description": "Existing subtask id currently being worked on.",
                },
                "next_focus": {
                    "type": "string",
                    "description": (
                        "Existing subtask id or short focus statement for the next action."
                    ),
                },
                "task_status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed", "failed", "cancelled"],
                },
                "completion_summary": {"type": "string"},
            },
            "required": ["updates"],
        },
        executor_mode="programmatic",
        executor=task_progress_check_executor,
    )
