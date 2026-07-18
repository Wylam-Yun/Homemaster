"""Task state models — snapshot, subtask, progress update, statuses."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class TaskSubtask(BaseModel):
    id: str
    description: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    evidence: list[str] = Field(default_factory=list)


class TaskSnapshot(BaseModel):
    type: str = "task_state_snapshot"
    snapshot_id: str
    status: TaskStatus = TaskStatus.ACTIVE
    goal: str
    current_subtask: str | None = None
    next_focus: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    subtasks: list[TaskSubtask] = Field(default_factory=list)
    updated_at_iteration: int = 0
    completion_summary: str | None = None

    def to_model_visible_dict(self, *, max_evidence_per_subtask: int = 2) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["subtasks"] = [
            {
                "id": subtask.id,
                "description": subtask.description,
                "status": subtask.status.value,
                "evidence": subtask.evidence[-max_evidence_per_subtask:],
            }
            for subtask in self.subtasks
        ]
        return payload

    def to_completed_model_summary_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "snapshot_id": self.snapshot_id,
            "status": self.status.value,
            "goal": self.goal,
            "completion_summary": self.completion_summary or "Task completed.",
            "updated_at_iteration": self.updated_at_iteration,
        }


class TaskProgressUpdate(BaseModel):
    subtask_id: str
    status: SubtaskStatus
    evidence: list[str]
