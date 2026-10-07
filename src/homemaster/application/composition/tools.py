"""Tool registry composition for application profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from homemaster.adapters.profiles import build_tool_registry
from homemaster.tools.base import ToolRegistry


def compose_tool_registry(
    *,
    environment: Literal["local_robot", "alfworld", "browser"] | None,
    world_path: Path | None,
    memory_path: Path | None,
    runtime_memory_root: Path,
    memory_enabled: bool,
    memory_tier: Literal["files", "full"] = "full",
) -> ToolRegistry:
    return build_tool_registry(
        environment=environment,
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
        memory_enabled=memory_enabled,
        memory_tier=memory_tier,
    )


__all__ = ["compose_tool_registry"]
