"""CLI handler for ALFWorld benchmark runs."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldSummary,
    EnvType,
    MemoryMode,
    SplitName,
)
from homemaster.logger import setup_logging


def handle_benchmark_alfworld(
    *,
    alfworld_root: Path,
    alfworld_config: Path,
    trace_root: Path,
    env_type: str = "AlfredTWEnv",
    split: str = "valid_seen",
    episodes: int = 1,
    memory_mode: str = "disabled",
    max_invalid_actions: int = 100,
    max_env_steps: int = 50,
    max_tool_iterations: int = 1000,
    provider_config: Path | None = None,
    provider_name: str = "Mimo",
    run_id: str | None = None,
    log_level: str = "INFO",
) -> AlfworldSummary:
    setup_logging(level=log_level)
    config = AlfworldBenchmarkConfig(
        alfworld_root=alfworld_root,
        alfworld_config=alfworld_config,
        trace_root=trace_root,
        env_type=cast(EnvType, env_type),
        split=cast(SplitName, split),
        episodes=episodes,
        memory_mode=cast(MemoryMode, memory_mode),
        max_invalid_actions=max_invalid_actions,
        max_env_steps=max_env_steps,
        max_tool_iterations=max_tool_iterations,
        provider_config=provider_config,
        provider_name=provider_name,
        run_id=run_id,
    )
    return AlfworldBenchmarkRunner(config=config).run()
