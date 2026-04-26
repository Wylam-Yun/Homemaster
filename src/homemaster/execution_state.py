"""Small helpers for Stage 05 runtime state bookkeeping."""

from __future__ import annotations

from typing import Any

from homemaster.contracts import ExecutionState, VerificationResult


def next_ready_subtasks(state: ExecutionState) -> list[str]:
    """Return pending subtasks whose dependencies are verified."""

    verified = set(state.completed_subtask_ids)
    verified.update(
        subtask.subtask_id for subtask in state.subtasks if subtask.status == "verified"
    )
    ready: list[str] = []
    for subtask in state.subtasks:
        if subtask.status != "pending":
            continue
        if all(dependency in verified for dependency in subtask.depends_on):
            ready.append(subtask.subtask_id)
    return ready


def mark_subtask_verified(
    state: ExecutionState,
    subtask_id: str,
    verification_result: VerificationResult,
    *,
    observation: dict[str, Any] | None = None,
) -> ExecutionState:
    """Return a copy of state with one subtask marked verified."""

    updated = state.model_copy(deep=True)
    for subtask in updated.subtasks:
        if subtask.subtask_id != subtask_id:
            continue
        subtask.status = "verified"
        subtask.last_verification_result = verification_result
        subtask.last_observation = observation
        break

    if subtask_id not in updated.completed_subtask_ids:
        updated.completed_subtask_ids.append(subtask_id)
    updated.current_subtask_id = subtask_id
    updated.last_verification_result = verification_result
    updated.last_observation = observation
    if observation:
        if "target_object_visible" in observation:
            updated.target_object_visible = bool(observation["target_object_visible"])
        if "target_object_location" in observation:
            location = observation["target_object_location"]
            updated.target_object_location = str(location) if location is not None else None
        if "held_object" in observation:
            held_object = observation["held_object"]
            updated.held_object = str(held_object) if held_object is not None else None
        if "current_location" in observation:
            current_location = observation["current_location"]
            updated.current_location = (
                str(current_location) if current_location is not None else None
            )
        if "user_location" in observation:
            user_location = observation["user_location"]
            updated.user_location = str(user_location) if user_location is not None else None
    return updated


def append_failure_record_id(
    state: ExecutionState, subtask_id: str, failure_id: str
) -> ExecutionState:
    """Return a copy of state with failure id attached to task and subtask state."""

    updated = state.model_copy(deep=True)
    if failure_id not in updated.failure_record_ids:
        updated.failure_record_ids.append(failure_id)
    for subtask in updated.subtasks:
        if subtask.subtask_id != subtask_id:
            continue
        if failure_id not in subtask.failure_record_ids:
            subtask.failure_record_ids.append(failure_id)
        subtask.attempt_count += 1
        updated.retry_counts[subtask_id] = subtask.attempt_count
        break
    return updated
