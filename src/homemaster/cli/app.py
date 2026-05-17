"""Typer CLI entrypoint for HomeMaster V1.3."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from homemaster.cli.doctor import doctor_report_to_json, render_doctor_text, run_doctor
from homemaster.cli.errors import render_error_and_exit
from homemaster.cli.interactive_shell import run_interactive_shell
from homemaster.cli.run_command import handle_run
from homemaster.logger import setup_logging
from homemaster.pipeline import DEFAULT_STAGE_01_UTTERANCE, run_stage_01_contract_smoke
from homemaster.stages.task_understanding import understand_task

app = typer.Typer(
    add_completion=False,
    help="HomeMaster V1.3 — AgentRuntime task brain CLI.",
)

stage_app = typer.Typer(help="Run individual pipeline stages.")
smoke_app = typer.Typer(help="Run contract and smoke tests.")
app.add_typer(stage_app, name="stage")
app.add_typer(smoke_app, name="smoke")


@app.command("run")
def run_command(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to execute."),
    ],
    scenario: Annotated[
        str | None,
        typer.Option("--scenario", help="Scenario name under data/scenarios/."),
    ] = None,
    world_path: Annotated[
        Path | None,
        typer.Option("--world", help="Optional world.json override."),
    ] = None,
    memory_path: Annotated[
        Path | None,
        typer.Option("--memory", help="Optional base memory.json override."),
    ] = None,
    runtime_memory_root: Annotated[
        Path | None,
        typer.Option("--runtime-memory-root", help="Root for isolated runtime memory."),
    ] = None,
    debug_root: Annotated[
        Path | None,
        typer.Option("--debug-root", help="Root for debug case reports."),
    ] = None,
    results_root: Annotated[
        Path | None,
        typer.Option("--results-root", help="Root for Stage07 results (llm_samples, traces)."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable run id for traces and runtime memory."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level (DEBUG/INFO/WARNING/ERROR)."),
    ] = "INFO",
    skill_mode: Annotated[
        str,
        typer.Option("--skill-mode", help="Skill execution mode (simulated or real)."),
    ] = "simulated",
    progress: Annotated[
        bool,
        typer.Option("--progress/--no-progress", help="Show high-level progress events on stderr."),
    ] = False,
) -> None:
    """Run one HomeMaster task with AgentRuntime, live Mimo decisions, and simulated robot tools."""
    try:
        handle_run(
            utterance=utterance,
            scenario=scenario,
            world_path=world_path,
            memory_path=memory_path,
            runtime_memory_root=runtime_memory_root,
            debug_root=debug_root,
            results_root=results_root,
            run_id=run_id,
            log_level=log_level,
            skill_mode=skill_mode,
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


@smoke_app.command("contract")
def smoke_contract(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to convert into TaskCard."),
    ] = DEFAULT_STAGE_01_UTTERANCE,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Provider config path."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name in the config file."),
    ] = None,
) -> None:
    """Run the Stage 01 real LLM TaskCard contract smoke."""
    try:
        from homemaster.runtime import DEFAULT_CONFIG_PATH, DEFAULT_PROVIDER_NAME
        if config_path is None:
            config_path = DEFAULT_CONFIG_PATH
        if provider_name is None:
            provider_name = DEFAULT_PROVIDER_NAME
        setup_logging()
        result = run_stage_01_contract_smoke(
            utterance=utterance,
            config_path=config_path,
            provider_name=provider_name,
        )
    except Exception as exc:
        render_error_and_exit(exc)

    typer.echo("contract_smoke: PASS")
    typer.echo(f"provider: {result.provider['name']}")
    typer.echo(f"model: {result.provider['model']}")
    typer.echo(f"task_type: {result.task_card.task_type}")
    typer.echo(f"target: {result.task_card.target}")
    typer.echo(f"case_dir: {result.case_dir}")
    typer.echo(f"results_dir: {result.results_dir}")


@stage_app.command("understand")
def stage_understand(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to convert into TaskCard."),
    ],
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Provider config path."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name in the config file."),
    ] = None,
) -> None:
    """Run Stage 02 task understanding and print the validated TaskCard."""
    try:
        from homemaster.runtime import DEFAULT_CONFIG_PATH, DEFAULT_PROVIDER_NAME
        if config_path is None:
            config_path = DEFAULT_CONFIG_PATH
        if provider_name is None:
            provider_name = DEFAULT_PROVIDER_NAME
        setup_logging()
        result = understand_task(
            utterance,
            config_path=config_path,
            provider_name=provider_name,
        )
    except Exception as exc:
        render_error_and_exit(exc)

    task_card = result.task_card
    typer.echo("understand: PASS")
    typer.echo(f"provider: {result.provider['provider_name']}")
    typer.echo(f"model: {result.provider['model']}")
    typer.echo(f"task_type: {task_card.task_type}")
    typer.echo(f"target: {task_card.target}")
    typer.echo(f"location_hint: {task_card.location_hint}")
    typer.echo(f"delivery_target: {task_card.delivery_target}")
    typer.echo(f"needs_clarification: {task_card.needs_clarification}")
    typer.echo(f"case_dir: {result.case_dir}")


# ---------------------------------------------------------------------------
# Deprecated top-level commands (removal target: Phase 10)
# ---------------------------------------------------------------------------


@app.command("contract-smoke", deprecated=True)
def contract_smoke_deprecated(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to convert into TaskCard."),
    ] = DEFAULT_STAGE_01_UTTERANCE,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Provider config path."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name in the config file."),
    ] = None,
) -> None:
    """[deprecated: use 'homemaster smoke contract'] Run the Stage 01 contract smoke."""
    smoke_contract(utterance=utterance, config_path=config_path, provider_name=provider_name)


@app.command("understand", deprecated=True)
def understand_deprecated(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to convert into TaskCard."),
    ],
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Provider config path."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider", help="Provider name in the config file."),
    ] = None,
) -> None:
    """[deprecated: use 'homemaster stage understand'] Run Stage 02 task understanding."""
    stage_understand(utterance=utterance, config_path=config_path, provider_name=provider_name)


@app.command("shell")
def shell_command() -> None:
    """Launch the interactive HomeMaster shell."""
    run_interactive_shell()


if __name__ == "__main__":
    app()
