"""Canonical model-owned task-state tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from homemaster.task_state.models import SubtaskStatus, TaskProgressUpdate, TaskStatus
from homemaster.task_state.store import TaskStateStore, TaskStateStoreError
from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)


def _store(context: ToolExecutionContext) -> tuple[TaskStateStore, Any]:
    store = context.services.get("task_state_store")
    if not isinstance(store, TaskStateStore):
        raise TaskStateStoreError("no task_state_store in canonical context services")
    return store, context.services


def _success(data: Mapping[str, object]) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=json.dumps(dict(data), ensure_ascii=False, sort_keys=True),
        data=dict(data),
    )


async def _task_planner(
    arguments: Mapping[str, object], context: ToolExecutionContext
) -> ToolExecutionResult:
    store, services = _store(context)
    snapshot = store.create_or_replace_plan(
        goal=str(arguments["goal"]),
        subtasks=list(arguments["subtasks"]),
        current_subtask=arguments.get("current_subtask"),
        next_focus=arguments.get("next_focus"),
        open_questions=list(arguments.get("open_questions") or []),
        constraints=list(arguments.get("constraints") or []),
        updated_at_iteration=context.turn_index,
    )
    del services
    return _success(snapshot.to_model_visible_dict())


def _evidence_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


async def _task_progress_check(
    arguments: Mapping[str, object], context: ToolExecutionContext
) -> ToolExecutionResult:
    store, services = _store(context)
    raw_status = arguments.get("task_status")
    task_status = store.validate_status_transition(TaskStatus(raw_status)) if raw_status else None
    updates = [
        TaskProgressUpdate(
            subtask_id=str(item["subtask_id"]),
            status=SubtaskStatus(item["status"]),
            evidence=_evidence_list(item.get("evidence")),
        )
        for item in (arguments.get("updates") or [])
    ]
    snapshot = store.apply_progress_updates(
        updates,
        current_subtask=arguments.get("current_subtask"),
        next_focus=arguments.get("next_focus"),
        updated_at_iteration=context.turn_index,
    )
    if task_status is not None:
        if task_status is TaskStatus.COMPLETED:
            guard = services.get("task_completion_guard")
            if guard is not None:
                if not callable(guard):
                    raise TaskStateStoreError("task_completion_guard must be callable")
                blocked = guard()
                if blocked is not None:
                    if not isinstance(blocked, ToolExecutionResult):
                        raise TaskStateStoreError(
                            "task_completion_guard must return ToolExecutionResult or None"
                        )
                    return blocked
            snapshot = store.mark_completed(
                final_summary=str(arguments.get("completion_summary") or "Task completed."),
                updated_at_iteration=context.turn_index,
            )
        else:
            snapshot = store.update_status(task_status, updated_at_iteration=context.turn_index)
    return _success(snapshot.to_model_visible_dict())


class _TaskExecutor:
    def __init__(self, function: Any) -> None:
        self._function = function

    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        return await self._function(arguments, context)


def _registered(name: str, description: str, schema: Mapping[str, object], function: Any) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id=f"homemaster.{name}.v1",
            model_alias=name,
            description=description,
            input_schema=dict(schema),
            output_schema={"type": "object"},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source="homemaster.task_state", reference=f"task_state.{name}"),
            version="3.5.0",
        ),
        executor=_TaskExecutor(function),
    )


def make_task_planner_tool() -> RegisteredTool:
    return _registered(
        "task_planner",
        "Create or replace the model-owned TODO list for a multi-step task.",
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Overall task outcome."},
                "subtasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Stable subtask identifier."},
                            "description": {"type": "string", "description": "Concrete subtask work."},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "blocked", "cancelled", "uncertain"],
                                "description": "Initial subtask status.",
                            },
                            "evidence": {"type": "array", "items": {"type": "string"}, "description": "Existing evidence."},
                        },
                        "required": ["id", "description"],
                    },
                },
                "current_subtask": {"type": "string", "description": "Current subtask ID."},
                "next_focus": {"type": "string", "description": "Next focus."},
                "open_questions": {"type": "array", "items": {"type": "string"}, "description": "Unresolved inputs."},
                "constraints": {"type": "array", "items": {"type": "string"}, "description": "Stable constraints."},
            },
            "required": ["goal", "subtasks"],
        },
        _task_planner,
    )


def make_task_progress_check_tool() -> RegisteredTool:
    return _registered(
        "task_progress_check",
        "Update explicit task TODO status and return the latest task-state snapshot.",
        {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subtask_id": {"type": "string", "description": "Existing subtask ID."},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed", "blocked", "cancelled", "uncertain"],
                                "description": "Updated subtask status.",
                            },
                            "evidence": {"type": "array", "items": {"type": "string"}, "description": "Observed evidence."},
                        },
                        "required": ["subtask_id", "status"],
                    },
                },
                "current_subtask": {"type": "string", "description": "Current subtask ID."},
                "next_focus": {"type": "string", "description": "Next focus."},
                "task_status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed", "failed", "cancelled"],
                    "description": "Optional overall task status.",
                },
                "completion_summary": {"type": "string", "description": "Final evidence-based summary."},
            },
            "required": ["updates"],
        },
        _task_progress_check,
    )


__all__ = ["make_task_planner_tool", "make_task_progress_check_tool"]
