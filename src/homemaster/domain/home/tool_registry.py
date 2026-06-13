"""Home domain tool registry builder."""

from __future__ import annotations

from pathlib import Path

from homemaster.domain.home.tools import (
    make_memory_retriever,
    make_memory_writer,
    make_robot_manipulate,
    make_robot_navigate,
    make_robot_observe,
    make_robot_verify,
    make_skill_view,
    make_target_grounder,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.task_state.tools import task_planner_executor, task_progress_check_executor
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.spec import ToolSpec


def _make_task_planner() -> ToolSpec:
    return ToolSpec(
        name="task_planner",
        description="Create or replace the model-owned task plan snapshot.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "subtasks": {"type": "array", "items": {"type": "object"}},
                "current_subtask": {"type": "string"},
                "next_focus": {"type": "string"},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal", "subtasks"],
        },
        executor_mode="programmatic",
        executor=task_planner_executor,
    )


def _make_task_progress_check() -> ToolSpec:
    return ToolSpec(
        name="task_progress_check",
        description="Update progress on subtasks with explicit status and evidence.",
        input_schema={
            "type": "object",
            "properties": {
                "updates": {"type": "array", "items": {"type": "object"}},
                "current_subtask": {"type": "string"},
                "next_focus": {"type": "string"},
            },
            "required": ["updates"],
        },
        executor_mode="programmatic",
        executor=task_progress_check_executor,
    )


def build_home_tool_registry(
    *,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> ToolRegistry:
    """Build a ToolRegistry with all home domain tools + task-state tools."""
    registry = ToolRegistry()
    for spec in (
        make_task_interpreter(),
        make_memory_retriever(memory_path=memory_path),
        make_target_grounder(world_path=world_path),
        make_skill_view(),
        make_robot_navigate(),
        make_robot_observe(),
        make_robot_manipulate(),
        make_robot_verify(),
        make_memory_writer(runtime_memory_root=runtime_memory_root),
        make_task_summarizer(),
        _make_task_planner(),
        _make_task_progress_check(),
    ):
        registry.register(spec)
    return registry
