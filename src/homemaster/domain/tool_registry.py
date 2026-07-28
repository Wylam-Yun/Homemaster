"""Home domain tool registry builder."""

from __future__ import annotations

from pathlib import Path

from homemaster.domain.tools import (
    make_load_skill,
    make_memory_retriever,
    make_memory_writer,
    make_robot_manipulate,
    make_robot_navigate,
    make_robot_verify,
    make_target_grounder,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.task_state.tools import make_task_planner_tool, make_task_progress_check_tool
from homemaster.tools.registry import ToolRegistry


def build_home_tool_registry(
    *,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> ToolRegistry:
    """Build a ToolRegistry with all home domain tools + task-state tools."""
    registry = ToolRegistry()
    specs = (
        make_task_interpreter(),
        make_memory_retriever(memory_path=memory_path),
        make_target_grounder(world_path=world_path),
        make_load_skill(),
        make_robot_navigate(),
        make_robot_manipulate(),
        make_robot_verify(),
        make_memory_writer(runtime_memory_root=runtime_memory_root),
        make_task_summarizer(),
        make_task_planner_tool(),
        make_task_progress_check_tool(),
    )
    for spec in specs:
        if spec.name == "robot_navigate":
            # Keep the historical name for execution-only traces while the
            # provider-facing surface uses the canonical CL-07 alias.
            registry.register(spec.model_copy(update={"selectable_by_model": False}))
            registry.register(spec.model_copy(update={"name": "robot_go_to"}))
        else:
            registry.register(spec)
    return registry
