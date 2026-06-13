"""TaskStateStore — run-scoped store for model-owned task plans."""

from __future__ import annotations

from typing import Any

from homemaster.task_state.models import (
    TaskProgressUpdate,
    TaskSnapshot,
    TaskStatus,
    TaskSubtask,
)


class TaskStateStoreError(RuntimeError):
    pass


class TaskStateStore:
    def __init__(self, *, run_id: str) -> None:
        self._run_id = run_id
        self._snapshot_counter = 0
        self._snapshot: TaskSnapshot | None = None

    @property
    def snapshot(self) -> TaskSnapshot | None:
        return self._snapshot

    def create_or_replace_plan(
        self,
        *,
        goal: str,
        subtasks: list[dict[str, Any]],
        current_subtask: str | None = None,
        next_focus: str | None = None,
        open_questions: list[str] | None = None,
        constraints: list[str] | None = None,
        updated_at_iteration: int = 0,
    ) -> TaskSnapshot:
        if not subtasks:
            raise TaskStateStoreError("subtasks must not be empty")
        seen: set[str] = set()
        parsed: list[TaskSubtask] = []
        for item in subtasks:
            subtask = TaskSubtask.model_validate(item)
            if subtask.id in seen:
                raise TaskStateStoreError(f"duplicate subtask id: {subtask.id}")
            seen.add(subtask.id)
            parsed.append(subtask)
        if current_subtask is not None and current_subtask not in seen:
            raise TaskStateStoreError(f"unknown current_subtask: {current_subtask}")
        self._snapshot_counter += 1
        self._snapshot = TaskSnapshot(
            snapshot_id=f"task-state-{self._snapshot_counter:04d}",
            status=TaskStatus.ACTIVE,
            goal=goal,
            current_subtask=current_subtask,
            next_focus=next_focus,
            open_questions=open_questions or [],
            constraints=constraints or ["Only use model-visible observations and tool results."],
            subtasks=parsed,
            updated_at_iteration=updated_at_iteration,
        )
        return self._snapshot

    def apply_progress_updates(
        self,
        updates: list[TaskProgressUpdate],
        *,
        current_subtask: str | None = None,
        next_focus: str | None = None,
        updated_at_iteration: int = 0,
    ) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        by_id = {subtask.id: subtask for subtask in self._snapshot.subtasks}
        for update in updates:
            if update.subtask_id not in by_id:
                raise TaskStateStoreError(f"unknown subtask: {update.subtask_id}")
            subtask = by_id[update.subtask_id]
            subtask.status = update.status
            subtask.evidence.extend(update.evidence)
        if current_subtask is not None:
            if current_subtask not in by_id:
                raise TaskStateStoreError(f"unknown current_subtask: {current_subtask}")
            self._snapshot.current_subtask = current_subtask
        if next_focus is not None:
            self._snapshot.next_focus = next_focus
        self._snapshot.updated_at_iteration = updated_at_iteration
        return self._snapshot

    def mark_completed(self, *, final_summary: str, updated_at_iteration: int = 0) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        self._snapshot.status = TaskStatus.COMPLETED
        self._snapshot.completion_summary = final_summary
        self._snapshot.current_subtask = None
        self._snapshot.next_focus = None
        self._snapshot.updated_at_iteration = updated_at_iteration
        return self._snapshot
