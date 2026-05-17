"""Run command handler — extracted from app.py."""

from __future__ import annotations

from pathlib import Path

import typer

from homemaster.logger import setup_logging
from homemaster.task_runner import run_homemaster_task


def handle_run(
    *,
    utterance: str,
    scenario: str | None,
    world_path: Path | None,
    memory_path: Path | None,
    runtime_memory_root: Path | None = None,
    debug_root: Path | None = None,
    results_root: Path | None = None,
    run_id: str | None = None,
    log_level: str = "INFO",
    skill_mode: str = "simulated",
    progress: bool = False,
) -> None:
    """Execute a HomeMaster task run.

    No CLI-level policy checks — task_runner raises HomeMasterRunError /
    RuntimeConfigError which the CLI renders via render_error_and_exit().
    """
    setup_logging(level=log_level)
    result = run_homemaster_task(
        utterance=utterance,
        scenario=scenario,
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
        debug_root=debug_root,
        results_root=results_root,
        run_id=run_id,
        skill_mode=skill_mode,
        progress=progress,
    )
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"scenario: {result.scenario}")
    typer.echo(f"final_status: {result.final_status}")
    typer.echo(f"debug_path: {result.case_dir / 'result.md'}")
    typer.echo(f"event_trace: {result.case_dir / 'trace' / 'runtime_events.jsonl'}")
    typer.echo(f"runtime_memory_root: {result.runtime_memory_root}")
    if result.final_status == "failed":
        typer.echo("run_result: task failed safely; see debug_path for failure details")
