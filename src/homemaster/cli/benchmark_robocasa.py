"""CLI handlers for RoboCasa benchmark runs."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from homemaster.events.logger import setup_logging
from homemaster.robocasa.benchmark.runner import RoboCasaBenchmarkRunner
from homemaster.robocasa.types import MemoryMode, RoboCasaBenchmarkConfig, RoboCasaSummary


def handle_benchmark_robocasa(
    *,
    rpent_root: Path,
    task_manifest: Path,
    trace_root: Path,
    split: str = "target",
    episodes: int | None = None,
    memory_mode: str = "disabled",
    max_invalid_actions: int = 100,
    max_tool_iterations: int = 100,
    provider_config: Path | None = None,
    provider_name: str | None = None,
    run_id: str | None = None,
    log_level: str = "INFO",
    rpent_python: Path | None = None,
    vla_model_path: Path | None = None,
    cuda_device: str | None = None,
    hi_res: bool = False,
    tasks: tuple[str, ...] = (),
    seeds: tuple[int, ...] = (),
    task_splits: tuple[str, ...] = (),
) -> RoboCasaSummary:
    setup_logging(level=log_level)
    config = RoboCasaBenchmarkConfig(
        rpent_root=rpent_root,
        task_manifest=task_manifest,
        trace_root=trace_root,
        split=split,
        episodes=episodes,
        memory_mode=cast(MemoryMode, memory_mode),
        max_invalid_actions=max_invalid_actions,
        max_tool_iterations=max_tool_iterations,
        provider_config=provider_config,
        provider_name=provider_name,
        run_id=run_id,
        rpent_python=rpent_python,
        vla_model_path=vla_model_path,
        cuda_device=cuda_device,
        hi_res=hi_res,
        tasks=tasks,
        seeds=seeds,
        task_splits=task_splits,
    )
    return RoboCasaBenchmarkRunner(config=config).run()


__all__ = ["handle_benchmark_robocasa"]
