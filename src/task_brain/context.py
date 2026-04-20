"""Task context builder and model for planner-facing input."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from task_brain.capabilities import default_capability_registry, validate_capability_registry
from task_brain.domain import (
    CapabilitySpec,
    Observation,
    ParsedTask,
    RobotRuntimeState,
    RuntimeState,
    TaskNegativeEvidence,
    TaskRequest,
)


class TaskContext(BaseModel):
    """Single planner input model built from runtime and retrieval artifacts.

    Runtime progress is carried only by ``runtime_state`` to avoid a second task-state source.
    """

    model_config = ConfigDict(extra="forbid")

    request: TaskRequest
    parsed_task: ParsedTask
    ranked_candidates: list[dict[str, Any]] = Field(default_factory=list)
    object_memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    category_prior_hits: list[dict[str, Any]] = Field(default_factory=list)
    task_negative_evidence: list[TaskNegativeEvidence] = Field(default_factory=list)
    recent_episodic_summaries: list[dict[str, Any]] = Field(default_factory=list)
    current_observation: Observation | None = None
    robot_runtime_state: RobotRuntimeState | None = None
    capability_registry: dict[str, CapabilitySpec] = Field(default_factory=dict)
    adapter_status: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    runtime_state: RuntimeState


def build_task_context(
    *,
    request: TaskRequest,
    parsed_task: ParsedTask,
    runtime_state: RuntimeState,
    ranked_candidates: list[dict[str, Any]] | None = None,
    object_memory_hits: list[dict[str, Any]] | None = None,
    category_prior_hits: list[dict[str, Any]] | None = None,
    recent_episodic_summaries: list[dict[str, Any]] | None = None,
    capability_registry: Mapping[str, CapabilitySpec | dict[str, Any]] | None = None,
    adapter_status: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> TaskContext:
    """Build planner-facing task context.

    Runtime/task-scoped state is injected from ``runtime_state`` as the single source of truth.
    """
    normalized_registry = (
        default_capability_registry()
        if capability_registry is None
        else validate_capability_registry(capability_registry)
    )

    return TaskContext(
        request=request,
        parsed_task=parsed_task,
        ranked_candidates=ranked_candidates or [],
        object_memory_hits=object_memory_hits or [],
        category_prior_hits=category_prior_hits or [],
        task_negative_evidence=list(runtime_state.task_negative_evidence),
        recent_episodic_summaries=recent_episodic_summaries or [],
        current_observation=runtime_state.current_observation,
        robot_runtime_state=runtime_state.robot_runtime_state,
        capability_registry=normalized_registry,
        adapter_status=adapter_status or {},
        constraints=constraints or {},
        runtime_state=runtime_state,
    )


__all__ = ["TaskContext", "build_task_context"]
