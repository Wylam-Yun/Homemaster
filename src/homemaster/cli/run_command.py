"""Run command handler — extracted from app.py."""

from __future__ import annotations

from pathlib import Path

import typer

from homemaster.agent.turn import run_single_turn
from homemaster.logger import setup_logging


def handle_run(
    *,
    utterance: str,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    run_id: str | None = None,
    log_level: str = "INFO",
    progress: bool = False,
) -> None:
    """Execute a single agent turn via the generic runtime."""
    setup_logging(level=log_level)
    result = run_single_turn(
        utterance=utterance,
        run_id=run_id,
        world_path=world_path,
        memory_path=memory_path,
        progress=progress,
    )
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"assistant: {result.final_reply}")
    typer.echo(f"status: {result.status}")
    if result.trace_path:
        typer.echo(f"trace: {result.trace_path}")
    if result.run_dir:
        typer.echo(f"run_dir: {result.run_dir}")
