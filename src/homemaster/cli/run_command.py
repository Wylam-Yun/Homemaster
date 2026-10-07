"""Home one-shot execution — thin-client default, ``--local`` escape hatch."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TextIO

import typer

from homemaster.agent.session import new_session_id
from homemaster.application import RunPolicy, RunRequest, RunResult
from homemaster.application.composition import HomeCliBackend, compose_application
from homemaster.cli.event_renderer import (
    EventRenderer,
    is_terminal_event,
    terminal_exit_code,
    terminal_status,
)
from homemaster.cli.live_output import StreamJsonEventSink, TextStreamEventSink
from homemaster.cli.renderers import (
    OutputFormat,
    render_run_result,
    result_exit_code,
    run_result_envelope,
)
from homemaster.cli.server_process import ensure_server, stop_server
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
    tool_environment: Literal["browser"] | None = None,
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
        bundle = compose_application(
            config=config,
            world_path=world_path,
            memory_path=memory_path,
            run_label=run_label,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
            event_sink=live_sink,
            tool_environment=tool_environment or "local_robot",
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
            actual_session_id = session_id or new_session_id()
            async with bundle.application.session(actual_session_id, exit_reason="one_shot_end"):
                return await bundle.application.run(
                    RunRequest(
                        text=(resolved_skill.prompt if resolved_skill is not None else prompt),
                        session_id=actual_session_id,
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
    tool_environment: Literal["browser"] | None = None,
    config_path: Path | None = None,
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
        tool_environment=tool_environment,
        config_path=config_path,
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
    config_path: Path | None = None,
    tool_environment: Literal["browser"] | None = None,
    run_label: str | None = None,
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
            config_path=config_path,
            tool_environment=tool_environment,
            run_label=run_label,
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
        typer.echo(render_run_result(execution.result, output_format))
    code = result_exit_code(execution.result)
    if code:
        raise typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Remote (thin-client) one-shot path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteExecution:
    """Terminal outcome of one server-mediated run."""

    run_id: str
    session_id: str
    status: str
    final_reply: str
    terminal_event: dict[str, Any] | None


def remote_event_envelope(event: dict[str, Any]) -> dict[str, Any] | None:
    """Translate a WebEvent dict into the ``stream-json`` envelope vocabulary."""

    etype = event.get("type")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if etype == "answer.delta":
        return {"type": "assistant_delta", "text": str(payload.get("text") or "")}
    if etype == "answer.snapshot":
        return {"type": "assistant_snapshot", "text": str(payload.get("text") or "")}
    if etype in ("thinking.delta", "thinking.snapshot"):
        return {
            "type": "thinking_delta" if etype.endswith("delta") else "thinking_snapshot",
            "text": str(payload.get("text") or ""),
        }
    if etype == "tool.started":
        return {
            "type": "tool_started",
            "tool_name": str(payload.get("name") or ""),
            "tool_input": payload.get("arguments") or {},
        }
    if etype in ("tool.completed", "tool.failed"):
        failed = etype == "tool.failed"
        return {
            "type": "tool_completed",
            "tool_name": str(payload.get("name") or ""),
            "output": str(payload.get("output") or ""),
            "is_error": failed,
            "metadata": {},
        }
    if etype == "run.failed":
        return {
            "type": "error",
            "message": str(payload.get("message") or payload.get("code") or "run failed"),
            "recoverable": bool(payload.get("retryable")),
        }
    if etype == "approval.requested":
        return {
            "type": "approval_requested",
            "approval_id": str(payload.get("approval_id") or ""),
            "intent_summary": str(payload.get("intent_summary") or ""),
            "items": payload.get("items") or [],
        }
    if etype == "question.asked":
        return {
            "type": "question_asked",
            "question_id": str(payload.get("question_id") or ""),
            "question": str(payload.get("question") or ""),
        }
    if etype == "session.mode_changed":
        return {
            "type": "session_mode_changed",
            "ui_mode": str(payload.get("ui_mode") or ""),
        }
    if etype == "context.compacted":
        return {
            "type": "compact_progress",
            "phase": "compact_end",
            "trigger": payload.get("trigger"),
        }
    if etype == "client.resync":
        return {"type": "resync", "payload": payload}
    return None


def remote_result_envelope(
    terminal: dict[str, Any] | None,
    *,
    session_id: str,
) -> dict[str, Any]:
    payload = (
        terminal.get("payload")
        if terminal and isinstance(terminal.get("payload"), dict)
        else {}
    )
    status = terminal_status(terminal) if terminal is not None else "failed"
    return {
        "type": "result",
        "run_id": str(terminal.get("run_id") or "") if terminal else "",
        "session_id": session_id,
        "status": status,
        "final_reply": str(payload.get("final_reply") or ""),
        "error_code": (str(payload.get("code")) if payload.get("code") else None)
        if status == "failed"
        else None,
        # Local parity: the local envelope carries ``metadata`` — the web
        # projection does not, so emit an empty object for shape parity.
        "metadata": {},
    }


async def _remote_run_events(
    *,
    client: Any,
    session_id: str,
    output_format: OutputFormat,
    renderer: EventRenderer | None,
    out: TextIO,
    input_fn: Callable[[str], str],
) -> dict[str, Any] | None:
    """Consume the event stream until the run's terminal event.

    A ``client.resync`` marker means the socket dropped and reconnected:
    pending approvals/questions are re-injected as synthetic interactive
    events, and when the session already went idle a truthful synthetic
    terminal event closes the loop — the real terminal event was lost with
    the dropped connection and would otherwise hang the run forever.
    """

    from homemaster.cli.client import RESYNC_EVENT_TYPE
    from homemaster.cli.remote_commands import (
        RemoteShellContext,
        handle_approval_event,
        handle_question_event,
    )

    ctx = RemoteShellContext(
        client=client,
        session_id=session_id,
        console=renderer.console if renderer is not None else None,
        input_fn=input_fn,
    )
    terminal: dict[str, Any] | None = None
    async for event in client.events():
        batch = [event]
        if event.get("type") == RESYNC_EVENT_TYPE:
            batch.extend(await _resync_run_events(ctx=ctx, event=event))
        for item in batch:
            if output_format is OutputFormat.STREAM_JSON:
                envelope = remote_event_envelope(item)
                if envelope is not None:
                    line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
                    out.write(line + "\n")
                    out.flush()
            elif renderer is not None:
                renderer.render(item)
            if item.get("type") == "approval.requested":
                await handle_approval_event(ctx, item)
            elif item.get("type") == "question.asked":
                await handle_question_event(ctx, item)
            if item.get("run_id"):
                ctx.last_run_id = str(item["run_id"])
            if is_terminal_event(item):
                terminal = item
                break
        if terminal is not None:
            break
    return terminal


async def _resync_run_events(
    *,
    ctx: Any,
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expand a ``client.resync`` marker into follow-up events for the loop.

    Pending approvals/questions come back as synthetic interactive events
    (deduped against ones this client already decided); when the session is
    already idle the run ended during the disconnect, so a truthful synthetic
    terminal event is appended last to release the loop.
    """

    from homemaster.cli.remote_commands import (
        interaction_needs_prompt,
        resync_interaction_events,
        resync_session_status,
        session_status_idle,
    )

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    events = [
        synthetic
        for synthetic in resync_interaction_events(payload, session_id=ctx.session_id)
        if interaction_needs_prompt(ctx, synthetic)
    ]
    status = await resync_session_status(ctx)
    if status is None or not session_status_idle(status):
        return events  # still running (or unknown): keep waiting on real events
    events.append(_resync_terminal_event(ctx, payload=payload, status=status))
    return events


def _resync_terminal_event(
    ctx: Any,
    *,
    payload: Mapping[str, Any],
    status: Mapping[str, Any],
) -> dict[str, Any]:
    """Synthesize the terminal event the disconnect swallowed — truthfully.

    The event type and ``payload.status`` mirror what the server reports;
    ``final_reply`` is recovered from the resynced history.  Unknown states
    fail closed (``run.failed``) — never fabricate a success.
    """

    reported = str(status.get("status") or "").lower()
    if reported == "cancelled":
        etype = "run.cancelled"
        body: dict[str, Any] = {"status": "cancelled"}
    elif reported in {"replied", "completed", "waiting_user"}:
        etype = "run.completed"
        body = {"status": reported}
    else:
        etype = "run.failed"
        body = {
            "status": "failed",
            "code": "run_failed" if reported == "failed" else "terminal_event_lost",
            "message": (
                "The run failed while the client was disconnected."
                if reported == "failed"
                else "The run ended while the client was disconnected; "
                f"the server reports status={reported or 'unknown'}."
            ),
            "retryable": False,
        }
    body["final_reply"] = _resync_final_reply(payload)
    return {
        "type": etype,
        "session_id": ctx.session_id,
        "run_id": ctx.last_run_id or "",
        "request_id": "",
        "payload": body,
    }


def _resync_final_reply(payload: Mapping[str, Any]) -> str:
    """Last assistant text in the resynced history — the reply we missed."""

    history = payload.get("history")
    if isinstance(history, list):
        for message in reversed(history):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text = str(message.get("text") or "")
            if text.strip():
                return text
    return ""


async def _remote_run_main(
    *,
    base_url: str,
    prompt: str,
    resume_session_id: str | None,
    continue_latest: bool,
    provider_name: str | None,
    model: str | None,
    output_format: OutputFormat | None,
    quiet: bool,
    verbose: bool,
    print_mode: bool,
    input_fn: Callable[[str], str],
) -> RemoteExecution:
    from homemaster.cli.client import HomeServerClient

    async with HomeServerClient(base_url) as client:
        if resume_session_id is not None:
            session_id = await client.create_session(resume_session_id)
        elif continue_latest:
            sessions = await client.list_sessions()
            if not sessions:
                raise FileNotFoundError("no persisted session is available to continue")
            session_id = await client.create_session(str(sessions[0]["session_id"]))
        else:
            session_id = await client.create_session()
        await client.subscribe(session_id)

        renderer: EventRenderer | None = None
        if output_format is OutputFormat.TEXT:
            from rich.console import Console

            if print_mode:
                # `-p` keeps stdout for answer text only; chrome → stderr.
                renderer = EventRenderer(
                    console=Console(file=sys.stderr),
                    answer_stream=sys.stdout,
                    show_thinking=verbose,
                    show_tool_lines=verbose,
                )
            else:
                renderer = EventRenderer(
                    console=Console(file=sys.stdout),
                    show_thinking=verbose,
                    show_tool_lines=not quiet,
                )

        await client.send(
            prompt,
            session_id=session_id,
            provider_name=provider_name,
            model=model,
        )
        terminal = await _remote_run_events(
            client=client,
            session_id=session_id,
            output_format=output_format or OutputFormat.TEXT,
            renderer=renderer,
            out=sys.stdout,
            input_fn=input_fn,
        )
        final_reply = ""
        if terminal is not None:
            payload = terminal.get("payload") if isinstance(terminal.get("payload"), dict) else {}
            final_reply = str(payload.get("final_reply") or "")
        if not final_reply:
            try:
                for message in reversed(await client.history(session_id)):
                    text = str(message.get("text") or "")
                    if message.get("role") == "assistant" and text.strip():
                        final_reply = text
                        break
            except Exception:
                pass
        if renderer is not None:
            renderer.finish(final_reply)
        execution = RemoteExecution(
            run_id=str(terminal.get("run_id") or "") if terminal else "",
            session_id=session_id,
            status=terminal_status(terminal) if terminal is not None else "failed",
            final_reply=final_reply,
            terminal_event=terminal,
        )
        if output_format is OutputFormat.JSON:
            sys.stdout.write(
                json.dumps(
                    remote_result_envelope(terminal, session_id=session_id),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            sys.stdout.flush()
        elif output_format is OutputFormat.STREAM_JSON:
            sys.stdout.write(
                json.dumps(
                    remote_result_envelope(terminal, session_id=session_id),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            sys.stdout.flush()
        return execution


def execute_remote_run(
    *,
    prompt: str,
    server: str | None = None,
    config_path: Path | None = None,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
    output_format: OutputFormat | None = None,
    quiet: bool = False,
    verbose: bool = False,
    print_mode: bool = False,
    input_fn: Callable[[str], str] = input,
) -> RemoteExecution:
    """Run one task through the thin client; returns the terminal outcome."""

    if not prompt.strip():
        raise ValueError("a non-empty prompt is required")
    if continue_latest and resume_session_id is not None:
        raise ValueError("--continue cannot be combined with --resume")
    base_url, proc = ensure_server(server, config_path=config_path)
    try:
        return asyncio.run(
            _remote_run_main(
                base_url=base_url,
                prompt=prompt,
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                provider_name=provider_name,
                model=model,
                output_format=output_format,
                quiet=quiet,
                verbose=verbose,
                print_mode=print_mode,
                input_fn=input_fn,
            )
        )
    finally:
        if proc is not None:
            stop_server(proc)


def handle_run_remote(
    *,
    utterance: str | None,
    server: str | None = None,
    config_path: Path | None = None,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
    output_format: OutputFormat | None = None,
    quiet: bool = False,
    verbose: bool = False,
) -> None:
    """Thin-client ``run``: stream events, print summary, exit by status."""

    if not utterance:
        raise ValueError("--utterance is required")
    execution = execute_remote_run(
        prompt=utterance,
        server=server,
        config_path=config_path,
        resume_session_id=resume_session_id,
        continue_latest=continue_latest,
        provider_name=provider_name,
        model=model,
        output_format=output_format or OutputFormat.TEXT,
        quiet=quiet,
        verbose=verbose,
    )
    if output_format is None or output_format is OutputFormat.TEXT:
        typer.echo(f"run_id: {execution.run_id}")
        typer.echo(f"status: {execution.status}")
    code = terminal_exit_code(execution.terminal_event)
    if code:
        raise typer.Exit(code=code)


def handle_print_remote(
    *,
    prompt: str,
    output_format: OutputFormat,
    server: str | None = None,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
    config_path: Path | None = None,
) -> None:
    """Thin-client ``-p``: stream the reply; emit the envelope on json modes."""

    try:
        execution = execute_remote_run(
            prompt=prompt,
            server=server,
            config_path=config_path,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            provider_name=provider_name,
            model=model,
            output_format=output_format,
            print_mode=True,
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
    if output_format is OutputFormat.TEXT:
        # Streaming already wrote the reply; the renderer emitted no chrome.
        pass
    code = terminal_exit_code(execution.terminal_event)
    if code:
        raise typer.Exit(code=code)


__all__ = [
    "OneShotExecution",
    "RemoteExecution",
    "execute_one_shot",
    "execute_remote_run",
    "handle_print",
    "handle_print_remote",
    "handle_run",
    "handle_run_remote",
    "remote_event_envelope",
    "remote_result_envelope",
]
