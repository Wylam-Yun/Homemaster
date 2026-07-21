"""Home one-shot execution and the legacy ``run --utterance`` wrapper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import typer

from homemaster.application import RunRequest, RunResult
from homemaster.cli.composition import HomeCliBackend, create_home_application
from homemaster.cli.renderers import OutputFormat, render_run_result, result_exit_code
from homemaster.config import load_config
from homemaster.events.logger import setup_logging


@dataclass(frozen=True)
class OneShotExecution:
    result: RunResult
    trace_path: Path
    run_dir: Path


def execute_one_shot(
    *,
    prompt: str,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    run_label: str | None = None,
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    provider_name: str | None = None,
) -> OneShotExecution:
    if not prompt.strip():
        raise ValueError("a non-empty prompt is required")
    if continue_latest and resume_session_id is not None:
        raise ValueError("--continue cannot be combined with --resume")
    config = load_config()
    bundle = create_home_application(
        config=config,
        world_path=world_path,
        memory_path=memory_path,
        run_label=run_label,
        progress=progress,
        verbose=verbose,
        quiet=quiet,
    )

    async def execute() -> RunResult:
        try:
            session_id = resume_session_id
            if continue_latest:
                session_ids = bundle.application.session_manager.list_session_ids()
                if not session_ids:
                    raise FileNotFoundError("no persisted session is available to continue")
                session_id = session_ids[0]
            return await bundle.application.run(
                RunRequest(
                    text=prompt,
                    session_id=session_id,
                    profile="home",
                    provider_name=provider_name,
                    resume=session_id is not None,
                    environment=HomeCliBackend(
                        world_path=world_path,
                        memory_path=memory_path,
                    ),
                )
            )
        finally:
            await bundle.application.aclose()

    return OneShotExecution(
        result=asyncio.run(execute()),
        trace_path=bundle.trace_path,
        run_dir=bundle.run_dir,
    )


def handle_run(
    *,
    utterance: str | None,
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
    """Compatibility renderer for the historical ``run`` subcommand."""

    setup_logging(level=log_level)
    if not utterance:
        raise ValueError("--utterance is required")
    execution = execute_one_shot(
        prompt=utterance,
        world_path=world_path,
        memory_path=memory_path,
        run_label=run_id,
        progress=progress,
        verbose=verbose,
        quiet=quiet,
        resume_session_id=resume_session_id,
        continue_latest=continue_latest,
    )
    result = execution.result
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"assistant: {result.final_reply}")
    typer.echo(f"status: {result.status}")
    typer.echo(f"trace: {execution.trace_path}")
    typer.echo(f"run_dir: {execution.run_dir}")
    code = result_exit_code(result)
    if code:
        raise typer.Exit(code=code)


def handle_print(
    *,
    prompt: str,
    output_format: OutputFormat,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
) -> None:
    execution = execute_one_shot(
        prompt=prompt,
        resume_session_id=resume_session_id,
        continue_latest=continue_latest,
        quiet=True,
    )
    typer.echo(render_run_result(execution.result, output_format))
    code = result_exit_code(execution.result)
    if code:
        raise typer.Exit(code=code)


__all__ = ["OneShotExecution", "execute_one_shot", "handle_print", "handle_run"]
