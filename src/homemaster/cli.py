"""Typer CLI entrypoint for HomeMaster V1.2."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from homemaster.frontdoor import understand_task
from homemaster.pipeline import DEFAULT_STAGE_01_UTTERANCE, run_stage_01_contract_smoke
from homemaster.runtime import DEFAULT_CONFIG_PATH, DEFAULT_PROVIDER_NAME

app = typer.Typer(add_completion=False, help="HomeMaster V1.2 LLM-first task brain CLI.")


@app.callback()
def main() -> None:
    """HomeMaster command group."""


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
