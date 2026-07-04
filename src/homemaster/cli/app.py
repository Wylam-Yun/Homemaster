"""Typer CLI entrypoint for HomeMaster."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from homemaster.benchmarking.alfworld.tracing import split_trace_bucket
from homemaster.cli.benchmark_alfworld import handle_benchmark_alfworld
from homemaster.cli.doctor import doctor_report_to_json, render_doctor_text, run_doctor
from homemaster.cli.errors import render_error_and_exit
from homemaster.cli.interactive_shell import run_interactive_shell
from homemaster.cli.run_command import handle_run
from homemaster.cli.session_command import session_app
from homemaster.events.logger import setup_logging

app = typer.Typer(
    add_completion=False,
    help="HomeMaster V1.6 — Generic agent loop CLI.",
)
app.add_typer(session_app, name="session")


@app.command("run")
def run_command(
    utterance: Annotated[
        str | None,
        typer.Option("--utterance", help="Chinese user instruction to execute."),
    ] = None,
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
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show full thinking and tool result output on stderr."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            help="Suppress console event output; stdout still prints the result.",
        ),
    ] = False,
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session id."),
    ] = None,
    continue_latest: Annotated[
        bool,
        typer.Option("--continue", help="Resume the latest persisted session."),
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
            verbose=verbose,
            quiet=quiet,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
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
def shell_command(
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session id."),
    ] = None,
) -> None:
    """Launch the interactive HomeMaster shell."""
    run_interactive_shell(resume_session_id=resume_session_id)


@app.command("benchmark-alfworld")
def benchmark_alfworld_command(
    alfworld_root: Annotated[
        Path,
        typer.Option("--alfworld-root", help="Path to the local ALFWorld repository."),
    ],
    alfworld_config: Annotated[
        Path,
        typer.Option("--alfworld-config", help="Path to ALFWorld YAML config."),
    ],
    trace_root: Annotated[
        Path,
        typer.Option("--trace-root", help="Output directory for benchmark traces."),
    ] = Path("/tmp/homemaster/alfworld"),
    env_type: Annotated[
        str,
        typer.Option("--env-type", help="ALFWorld environment type."),
    ] = "AlfredTWEnv",
    split: Annotated[
        str,
        typer.Option("--split", help="train, valid_seen, or valid_unseen."),
    ] = "valid_seen",
    episodes: Annotated[
        int,
        typer.Option("--episodes", help="Number of episodes to run."),
    ] = 1,
    memory_mode: Annotated[
        str,
        typer.Option("--memory-mode", help="disabled, readonly, or full."),
    ] = "disabled",
    max_invalid_actions: Annotated[
        int,
        typer.Option("--max-invalid-actions", help="Fail after this many invalid actions."),
    ] = 100,
    max_env_steps: Annotated[
        int,
        typer.Option(
            "--max-env-steps",
            help="Fail after this many ALFWorld environment action steps.",
        ),
    ] = 50,
    max_tool_iterations: Annotated[
        int,
        typer.Option("--max-tool-iterations", help="Maximum HomeMaster tool iterations."),
    ] = 1000,
    provider_config: Annotated[
        Path | None,
        typer.Option("--api-config", help="Optional provider config JSON override."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option(
            "--provider-name",
            help="Optional provider name override; defaults to the API config default.",
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable benchmark run id."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
    observation_mode: Annotated[
        str,
        typer.Option("--observation-mode", help="visual_eval or textual_debug."),
    ] = "visual_eval",
) -> None:
    """Run HomeMaster on ALFWorld benchmark episodes."""
    try:
        summary = handle_benchmark_alfworld(
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            trace_root=trace_root,
            env_type=env_type,
            split=split,
            episodes=episodes,
            memory_mode=memory_mode,
            max_invalid_actions=max_invalid_actions,
            max_env_steps=max_env_steps,
            max_tool_iterations=max_tool_iterations,
            provider_config=provider_config,
            provider_name=provider_name,
            run_id=run_id,
            log_level=log_level,
            observation_mode=observation_mode,
        )
        typer.echo(f"run_id: {summary.run_id}")
        typer.echo(f"episodes: {len(summary.episodes)}")
        typer.echo(f"success_rate: {summary.success_rate:.3f}")
        typer.echo(
            f"trace_root: {trace_root / split_trace_bucket(split) / summary.run_id}"
        )
    except Exception as exc:
        render_error_and_exit(exc)


if __name__ == "__main__":
    app()
