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


class TaskStateTransitionError(TaskStateStoreError):
    """Raised when a TaskStatus transition is not part of the lifecycle."""

    def __init__(self, current: TaskStatus, requested: TaskStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"invalid task status transition: {current.value} -> {requested.value}")


_ALLOWED_STATUS_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.ACTIVE: frozenset(
        {
            TaskStatus.PAUSED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.PAUSED: frozenset(
        {
            TaskStatus.ACTIVE,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.NONE: frozenset(),
}


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
        if current_subtask is not None and current_subtask not in by_id:
            raise TaskStateStoreError(f"unknown current_subtask: {current_subtask}")
        for update in updates:
            subtask = by_id[update.subtask_id]
            subtask.status = update.status
            subtask.evidence.extend(update.evidence)
        if current_subtask is not None:
            self._snapshot.current_subtask = current_subtask
        if next_focus is not None:
            self._snapshot.next_focus = next_focus
        self._snapshot.updated_at_iteration = updated_at_iteration
        return self._snapshot

    def mark_completed(self, *, final_summary: str, updated_at_iteration: int = 0) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        self.update_status(
            TaskStatus.COMPLETED,
            updated_at_iteration=updated_at_iteration,
        )
        self._snapshot.completion_summary = final_summary
        self._snapshot.current_subtask = None
        self._snapshot.next_focus = None
        return self._snapshot

    def update_status(
        self,
        status: TaskStatus,
        *,
        updated_at_iteration: int | None = None,
    ) -> TaskSnapshot:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        status = self.validate_status_transition(status)
        self._snapshot.status = status
        if updated_at_iteration is not None:
            self._snapshot.updated_at_iteration = updated_at_iteration
        return self._snapshot

    def validate_status_transition(self, status: TaskStatus) -> TaskStatus:
        if self._snapshot is None:
            raise TaskStateStoreError("no_active_task_plan")
        if not isinstance(status, TaskStatus):
            try:
                status = TaskStatus(status)
            except (TypeError, ValueError) as exc:
                raise TaskStateStoreError(f"unknown task status: {status!r}") from exc
        current = self._snapshot.status
        if status is not current and status not in _ALLOWED_STATUS_TRANSITIONS[current]:
            raise TaskStateTransitionError(current, status)
        return status

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "run_id": self._run_id,
            "snapshot_counter": self._snapshot_counter,
            "snapshot": self._snapshot.model_dump(mode="json") if self._snapshot else None,
        }

    @classmethod
    def from_snapshot_dict(cls, data: dict[str, Any]) -> TaskStateStore:
        run_id = str(data.get("run_id") or "")
        store = cls(run_id=run_id)
        store._snapshot_counter = int(data.get("snapshot_counter") or 0)
        raw_snapshot = data.get("snapshot")
        if isinstance(raw_snapshot, dict):
            store._snapshot = TaskSnapshot.model_validate(raw_snapshot)
        return store
