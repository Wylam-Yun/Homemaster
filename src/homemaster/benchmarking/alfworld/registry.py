"""Tool registry builder for ALFWorld benchmark mode."""

from __future__ import annotations

from pathlib import Path

from homemaster.benchmarking.alfworld.tools import (
    make_alfworld_observe,
    make_alfworld_robot_go_to,
    make_alfworld_robot_manipulate,
    make_alfworld_robot_verify,
)
from homemaster.domain.tools import (
    make_memory_retriever,
    make_memory_writer,
)
from homemaster.task_state.tools import (
    make_task_planner_tool,
    make_task_progress_check_tool,
)
from homemaster.tools.registry import ToolRegistry


def build_alfworld_tool_registry(
    *,
    memory_mode: str = "disabled",
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> ToolRegistry:
    if memory_mode not in {"disabled", "readonly", "full"}:
        raise ValueError(f"unsupported memory_mode: {memory_mode}")

    registry = ToolRegistry()

    if memory_mode in {"readonly", "full"}:
        registry.register(make_memory_retriever(memory_path=memory_path))
    if memory_mode == "full":
        registry.register(make_memory_writer(runtime_memory_root=runtime_memory_root))

    registry.register(make_alfworld_observe())
    registry.register(make_alfworld_robot_go_to())
    registry.register(make_alfworld_robot_manipulate())
    registry.register(make_alfworld_robot_verify())
    registry.register(make_task_planner_tool())
    registry.register(make_task_progress_check_tool())
    return registry
