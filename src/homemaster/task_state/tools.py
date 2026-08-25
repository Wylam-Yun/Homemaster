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
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
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
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
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
        description=(
            "Create or completely replace the model-owned TODO list for one multi-step task, "
            "then return the stored task-state snapshot. Use this before work begins or when "
            "the user's goal changes enough to require a new plan. Give every subtask a stable "
            "ID and an initial status. This tool writes planning state only: it does not observe "
            "the environment, execute work, verify evidence, or complete any subtask."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "The single overall outcome this TODO list is intended to achieve."
                    ),
                },
                "subtasks": {
                    "type": "array",
                    "description": (
                        "The complete ordered TODO list. Include every currently known subtask, "
                        "use stable string IDs, and refer to those IDs from current_subtask and "
                        "next_focus. Calling task_planner again replaces this entire list."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable subtask id, such as '1' or 'find_mug'.",
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Concrete work represented by this TODO item, including its "
                                    "target or completion condition when known."
                                ),
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
                                "description": (
                                    "Initial state of this TODO item. Use pending for work that "
                                    "has not started; never predict completion."
                                ),
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Evidence already visible in user input or tool results that "
                                    "supports the initial status. Do not add expected future proof."
                                ),
                            },
                        },
                        "required": ["id", "description"],
                    },
                },
                "current_subtask": {
                    "type": "string",
                    "description": (
                        "Stable subtask ID currently being worked on. Omit when work has not begun."
                    ),
                },
                "next_focus": {
                    "type": "string",
                    "description": (
                        "Stable subtask ID or concise focus for the next work, not a command that "
                        "must run in the next model response."
                    ),
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Unresolved inputs that may change execution. Use an empty array when none "
                        "are known."
                    ),
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Stable task constraints taken from authoritative instructions or "
                        "model-visible evidence."
                    ),
                },
            },
            "required": ["goal", "subtasks"],
        },
        executor_mode="programmatic",
        executor=task_planner_executor,
    )


def make_task_progress_check_tool() -> ToolSpec:
    return ToolSpec(
        name="task_progress_check",
        description=(
            "Update selected items in the existing model-owned TODO list and return the latest "
            "task-state snapshot. Despite the legacy name, this tool records explicit status, "
            "evidence, focus, and overall-task changes supplied by the model; it does not inspect "
            "the environment, execute work, or independently check whether evidence is true. "
            "Call it only after model-visible results justify a state change. It does not require "
            "any particular next tool or action."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "description": (
                        "Only the existing TODO items whose status or evidence must change. This "
                        "may be empty when updating only focus or overall task status."
                    ),
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
                                "description": (
                                    "New status to store for this TODO item. Mark completed only "
                                    "after model-visible terminal evidence supports completion."
                                ),
                            },
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Literal evidence already returned by the user or tools that "
                                    "supports this update. Do not use plans, predictions, or the "
                                    "TODO update itself as evidence. If there is one item, still "
                                    "pass an array."
                                ),
                            },
                        },
                        "required": ["subtask_id", "status"],
                    },
                },
                "current_subtask": {
                    "type": "string",
                    "description": (
                        "Existing stable subtask ID that is now being worked on. This changes "
                        "task-state focus only and does not start external work."
                    ),
                },
                "next_focus": {
                    "type": "string",
                    "description": (
                        "Existing subtask ID or concise future focus. It is TODO metadata, not a "
                        "requirement to call a particular tool in the next model response."
                    ),
                },
                "task_status": {
                    "type": "string",
                    "enum": ["active", "paused", "completed", "failed", "cancelled"],
                    "description": (
                        "Optional overall task status. completed is accepted only when the "
                        "runtime completion guard also permits completion; this tool does not "
                        "itself verify the external result."
                    ),
                },
                "completion_summary": {
                    "type": "string",
                    "description": (
                        "Concise final outcome to store when task_status is completed. Base it "
                        "only on already recorded terminal evidence."
                    ),
                },
            },
            "required": ["updates"],
        },
        executor_mode="programmatic",
        executor=task_progress_check_executor,
    )
