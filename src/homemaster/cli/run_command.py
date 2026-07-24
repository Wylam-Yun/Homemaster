"""Home one-shot execution and the legacy ``run --utterance`` wrapper."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import typer

from homemaster.application import RunPolicy, RunRequest, RunResult
from homemaster.cli.composition import HomeCliBackend, create_home_application
from homemaster.cli.live_output import StreamJsonEventSink, TextStreamEventSink
from homemaster.cli.renderers import (
    OutputFormat,
    render_run_result,
    result_exit_code,
    run_result_envelope,
)
from homemaster.config import load_config
from homemaster.events.logger import setup_logging
from homemaster.events.public_projection import PublicEventProjection
from homemaster.skills.commands import resolve_skill_command


class _PublicCliError(RuntimeError):
    """An exception whose message is already safe for public CLI rendering."""


@dataclass(frozen=True)
class OneShotExecution:
    result: RunResult
    trace_path: Path
    run_dir: Path
    live_rendered: bool = False


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
    model: str | None = None,
    output_format: OutputFormat | None = None,
    config_path: Path | None = None,
) -> OneShotExecution:
    if not prompt.strip():
        raise ValueError("a non-empty prompt is required")
    if continue_latest and resume_session_id is not None:
        raise ValueError("--continue cannot be combined with --resume")
    overrides = (
        {f"providers.{provider_name or 'default'}.model": model} if model is not None else None
    )
    config = load_config(config_path=config_path, cli_overrides=overrides)
    live_sink = None
    if output_format is OutputFormat.TEXT:
        import sys

        live_sink = TextStreamEventSink(file=sys.stdout)
    elif output_format is OutputFormat.STREAM_JSON:
        import sys

        live_sink = StreamJsonEventSink(file=sys.stdout)
    projection = PublicEventProjection()
    try:
        bundle = create_home_application(
            config=config,
            world_path=world_path,
            memory_path=memory_path,
            run_label=run_label,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
            event_sink=live_sink,
        )
    except Exception as exc:
        raise _PublicCliError(projection.project_content(str(exc))) from exc

    async def execute() -> RunResult:
        try:
            session_id = resume_session_id
            if continue_latest:
                session_ids = bundle.application.session_manager.list_session_ids()
                if not session_ids:
                    raise FileNotFoundError("no persisted session is available to continue")
                session_id = session_ids[0]
            resolved_skill = resolve_skill_command(
                prompt,
                bundle.skill_registry,
                session_id=session_id,
            )
            return await bundle.application.run(
                RunRequest(
                    text=resolved_skill.prompt if resolved_skill is not None else prompt,
                    session_id=session_id,
                    profile="home",
                    provider_name=provider_name,
                    model_override=(
                        resolved_skill.model_override if resolved_skill is not None else None
                    ),
                    resume=session_id is not None,
                    run_policy=RunPolicy(
                        max_tool_iterations=config.runtime.max_tool_iterations,
                    ),
                    dependencies={"skill_registry": bundle.skill_registry},
                    environment=HomeCliBackend(
                        world_path=world_path,
                        memory_path=memory_path,
                    ),
                )
            )
        finally:
            await bundle.application.aclose()

    try:
        result = asyncio.run(execute())
    except Exception as exc:
        raise _PublicCliError(projection.project_content(str(exc))) from exc
    if isinstance(live_sink, TextStreamEventSink):
        live_sink.finish(result.final_reply)
    elif isinstance(live_sink, StreamJsonEventSink):
        live_sink.write_envelope(run_result_envelope(result))
    return OneShotExecution(
        result=result,
        trace_path=bundle.trace_path,
        run_dir=bundle.run_dir,
        live_rendered=live_sink is not None or getattr(bundle, "live_rendered", False),
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
    provider_name: str | None = None,
    model: str | None = None,
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
        provider_name=provider_name,
        model=model,
    )
    result = execution.result
    typer.echo(f"run_id: {result.run_id}")
    if not execution.live_rendered:
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
    provider_name: str | None = None,
    model: str | None = None,
) -> None:
    try:
        execution = execute_one_shot(
            prompt=prompt,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            provider_name=provider_name,
            model=model,
            quiet=True,
            output_format=output_format,
        )
    except Exception as exc:
        if output_format is OutputFormat.STREAM_JSON:
            message = PublicEventProjection().project_content(str(exc))
            typer.echo(
                json.dumps(
                    {"type": "error", "message": message, "recoverable": False},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            raise typer.Exit(code=1) from exc
        raise
    if not execution.live_rendered:
        typer.echo(
            render_run_result(execution.result, output_format)
        )
    code = result_exit_code(execution.result)
    if code:
        raise typer.Exit(code=code)


__all__ = ["OneShotExecution", "execute_one_shot", "handle_print", "handle_run"]
