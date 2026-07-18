"""CLI handlers for ALFWorld benchmark runs."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner, AlfworldTasksetRunner
from homemaster.benchmarking.alfworld.taskset_loader import load_taskset_config
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldSummary,
    EnvType,
    MemoryMode,
    ObservationMode,
    SplitName,
    TasksetRunSummary,
)
from homemaster.events.logger import setup_logging


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
    provider_name: str | None = None,
    run_id: str | None = None,
    log_level: str = "INFO",
    observation_mode: str = "visual_eval",
    trial_manifest: Path | None = None,
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
        observation_mode=cast(ObservationMode, observation_mode),
        trial_manifest=trial_manifest,
    )
    return AlfworldBenchmarkRunner(config=config).run()


def handle_benchmark_alfworld_taskset(
    *,
    taskset_config: Path,
    alfworld_root: Path,
    alfworld_config: Path,
    log_level: str = "INFO",
) -> TasksetRunSummary:
    """Run long-horizon tasksets (a chain of subtasks in one persistent scene)."""
    setup_logging(level=log_level)
    config = load_taskset_config(
        taskset_config,
        alfworld_root=alfworld_root,
        alfworld_config=alfworld_config,
    )
    runner = AlfworldTasksetRunner(taskset_config=config)
    return runner.run()
