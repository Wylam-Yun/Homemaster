"""Typer CLI entrypoint for HomeMaster V1.2."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from homemaster.doctor import doctor_report_to_json, render_doctor_text, run_doctor
from homemaster.frontdoor import understand_task
from homemaster.interactive_shell import run_interactive_shell
from homemaster.pipeline import DEFAULT_STAGE_01_UTTERANCE, run_stage_01_contract_smoke
from homemaster.runtime import DEFAULT_CONFIG_PATH, DEFAULT_PROVIDER_NAME
from homemaster.task_runner import (
    DEFAULT_STAGE_07_RUNTIME_ROOT,
    HomeMasterRunError,
    run_homemaster_task,
)

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="HomeMaster V1.2 LLM-first task brain CLI.",
)


@app.callback()
def main(ctx: typer.Context) -> None:
    """HomeMaster command group."""
    if ctx.invoked_subcommand is None:
        run_interactive_shell()


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

    report = run_doctor(live=live)
    if json_output:
        typer.echo(doctor_report_to_json(report))
    else:
        typer.echo(render_doctor_text(report))
    if report.has_failures:
        raise typer.Exit(code=1)


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
        Path,
        typer.Option("--runtime-memory-root", help="Root for isolated runtime memory."),
    ] = DEFAULT_STAGE_07_RUNTIME_ROOT,
    debug_root: Annotated[
        Path,
        typer.Option("--debug-root", help="Root for debug case reports."),
    ] = Path("tests/homemaster/llm_cases"),
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable run id for traces and runtime memory."),
    ] = None,
    live_models: Annotated[
        bool,
        typer.Option(
            "--live-models/--no-live-models",
            help="Call real Mimo/BGE-M3 providers instead of deterministic test doubles.",
        ),
    ] = False,
    mock_skills: Annotated[
        bool,
        typer.Option("--mock-skills/--no-mock-skills", help="Stage07 uses mock skills."),
    ] = True,
) -> None:
    """Run one HomeMaster task through Stage02-Stage06."""

    if not scenario:
        typer.echo("run_failed: --scenario is required for Stage07 runs", err=True)
        raise typer.Exit(code=2)
    if not mock_skills:
        typer.echo("run_failed: Stage07 only supports --mock-skills", err=True)
        raise typer.Exit(code=2)
    try:
        result = run_homemaster_task(
            utterance=utterance,
            scenario=scenario,
            world_path=world_path,
            memory_path=memory_path,
            runtime_memory_root=runtime_memory_root,
            debug_root=debug_root,
            run_id=run_id,
            live_models=live_models,
            mock_skills=mock_skills,
        )
    except (HomeMasterRunError, Exception) as exc:
        typer.echo(f"run_failed: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"scenario: {result.scenario}")
    typer.echo(f"final_status: {result.final_status}")
    typer.echo(f"debug_path: {result.case_dir / 'result.md'}")
    typer.echo(f"runtime_memory_root: {result.runtime_memory_root}")
    if result.final_status == "failed":
        typer.echo("run_result: task failed safely; see debug_path for failure details")


@app.command("contract-smoke")
def contract_smoke(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to convert into TaskCard."),
    ] = DEFAULT_STAGE_01_UTTERANCE,
    config_path: Annotated[
        Path,
        typer.Option("--config", help="Provider config path."),
    ] = DEFAULT_CONFIG_PATH,
    provider_name: Annotated[
        str,
        typer.Option("--provider", help="Provider name in the config file."),
    ] = DEFAULT_PROVIDER_NAME,
) -> None:
    """Run the Stage 01 real LLM TaskCard contract smoke."""

    try:
        result = run_stage_01_contract_smoke(
            utterance=utterance,
            config_path=config_path,
            provider_name=provider_name,
        )
    except Exception as exc:
        typer.echo(f"contract_smoke_failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("contract_smoke: PASS")
    typer.echo(f"provider: {result.provider['name']}")
    typer.echo(f"model: {result.provider['model']}")
    typer.echo(f"task_type: {result.task_card.task_type}")
    typer.echo(f"target: {result.task_card.target}")
    typer.echo(f"case_dir: {result.case_dir}")
    typer.echo(f"results_dir: {result.results_dir}")


@app.command("understand")
def understand_command(
    utterance: Annotated[
        str,
        typer.Option("--utterance", help="Chinese user instruction to convert into TaskCard."),
    ],
    config_path: Annotated[
        Path,
        typer.Option("--config", help="Provider config path."),
    ] = DEFAULT_CONFIG_PATH,
    provider_name: Annotated[
        str,
        typer.Option("--provider", help="Provider name in the config file."),
    ] = DEFAULT_PROVIDER_NAME,
) -> None:
    """Run Stage 02 task understanding and print the validated TaskCard."""

    try:
        result = understand_task(
            utterance,
            config_path=config_path,
            provider_name=provider_name,
        )
    except Exception as exc:
        typer.echo(f"understand_failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

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


if __name__ == "__main__":
    app()
