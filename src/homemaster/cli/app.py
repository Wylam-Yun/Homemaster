"""Typer CLI entrypoint for HomeMaster V1.4."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from homemaster.cli.doctor import doctor_report_to_json, render_doctor_text, run_doctor
from homemaster.cli.errors import render_error_and_exit
from homemaster.cli.interactive_shell import run_interactive_shell
from homemaster.cli.run_command import handle_run
from homemaster.logger import setup_logging

app = typer.Typer(
    add_completion=False,
    help="HomeMaster V1.4 — Generic agent loop CLI.",
)


@app.command("run")
def run_command(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to execute."),
    ],
    world_path: Annotated[
        Path | None,
        typer.Option("--world", help="Optional world.json override."),
    ] = None,
    memory_path: Annotated[
        Path | None,
        typer.Option("--memory", help="Optional base memory.json override."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable run id for traces."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level (DEBUG/INFO/WARNING/ERROR)."),
    ] = "INFO",
    progress: Annotated[
        bool,
        typer.Option("--progress/--no-progress", help="Show high-level progress events on stderr."),
    ] = False,
) -> None:
    """Run one HomeMaster task with the generic agent loop."""
    try:
        handle_run(
            utterance=utterance,
            world_path=world_path,
            memory_path=memory_path,
            run_id=run_id,
            log_level=log_level,
            progress=progress,
        )
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("doctor")
def doctor_command(
    live: Annotated[
        bool,
        typer.Option("--live", help="Run live Mimo and BGE-M3 provider smoke checks."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Check HomeMaster local environment and optional live providers."""
    try:
        setup_logging()
        report = run_doctor(live=live)
        if json_output:
            typer.echo(doctor_report_to_json(report))
        else:
            typer.echo(render_doctor_text(report))
        if report.has_failures:
            raise typer.Exit(code=1)
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("shell")
def shell_command() -> None:
    """Launch the interactive HomeMaster shell."""
    run_interactive_shell()


if __name__ == "__main__":
    app()
