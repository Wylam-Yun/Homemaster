"""Run command handler — extracted from app.py."""

from __future__ import annotations

from pathlib import Path

import typer

from homemaster.agent.session_persistence import (
    find_latest_session_snapshot,
    resume_session,
    session_snapshot_path,
)
from homemaster.agent.turn import run_single_turn
from homemaster.agent.turn import run_agent_turn
from homemaster.config import load_config
from homemaster.events.logger import setup_logging


def handle_run(
    *,
    utterance: str,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    run_id: str | None = None,
    log_level: str = "INFO",
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
) -> None:
    """Execute a single agent turn via the generic runtime."""
    setup_logging(level=log_level)
    if continue_latest and resume_session_id is not None:
        raise ValueError("--continue cannot be combined with --resume")

    if resume_session_id is not None or continue_latest:
        config = load_config()
        session_root = Path(config.observability.session_dir)
        snapshot_path = (
            find_latest_session_snapshot(session_root)
            if continue_latest
            else session_snapshot_path(session_root, str(resume_session_id))
        )
        session, agent_state, task_state_store = resume_session(snapshot_path)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"session snapshot not found: {snapshot_path}")
        typer.echo(f"session: {session.session_id}")
        if not utterance:
            typer.echo("status: resumed")
            typer.echo("message: session restored; provide --utterance to continue.")
            return
        result = run_agent_turn(
            session,
            utterance,
            run_id=run_id or agent_state.run_id or None,
            world_path=world_path,
            memory_path=memory_path,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
            agent_state=agent_state,
            task_state_store=task_state_store,
        )
    else:
        if not utterance:
            raise ValueError("--utterance is required for a new run")
        result = run_single_turn(
            utterance=utterance,
            run_id=run_id,
            world_path=world_path,
            memory_path=memory_path,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
        )
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"assistant: {result.final_reply}")
    typer.echo(f"status: {result.status}")
    if result.trace_path:
        typer.echo(f"trace: {result.trace_path}")
    if result.run_dir:
        typer.echo(f"run_dir: {result.run_dir}")
