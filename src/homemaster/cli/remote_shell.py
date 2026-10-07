"""Interactive remote shell — a thin client over the HomeMaster server.

The shell talks REST+WS through :class:`HomeServerClient`; the server runtime
owns tools, memory, and the event stream.  This module drives the prompt loop
(prompt_toolkit on TTY, ``input()`` otherwise), renders WebEvents via
:class:`EventRenderer`, and answers ``approval.requested`` /
``question.asked`` interactively.  Messages sent while the session is busy
queue in a local dock and flush automatically on the next terminal event.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from collections.abc import Callable
from typing import Any

from rich.console import Console

from homemaster.cli.client import (
    RESYNC_EVENT_TYPE,
    HomeServerClient,
    ServerError,
    SessionBusyError,
)
from homemaster.cli.event_renderer import (
    EventRenderer,
    is_terminal_event,
    terminal_status,
)
from homemaster.cli.remote_commands import (
    REMOTE_REGISTRY,
    RemoteShellContext,
    dispatch_remote_command,
    handle_approval_event,
    handle_question_event,
    interaction_key,
    interaction_needs_prompt,
    resync_interaction_events,
    resync_session_status,
    session_status_idle,
    unknown_remote_hint,
)
from homemaster.cli.server_process import ensure_server, stop_server
from homemaster.cli.shell_commands import (
    interpolate_bang_output,
    run_local_command,
)


def _shell_version() -> str:
    try:
        return importlib.metadata.version("homemaster")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def run_remote_shell(
    *,
    server: str | None = None,
    config_path: Any | None = None,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    input_fn: Callable[[str], str] | None = None,
) -> int:
    """Entry point: locate/spawn the server, then drive the prompt loop."""

    base_url, proc = ensure_server(server, config_path=config_path)
    try:
        return asyncio.run(
            _shell_main(
                base_url,
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                input_fn=input_fn or input,
            )
        )
    finally:
        if proc is not None:
            stop_server(proc)


async def _shell_main(
    base_url: str,
    *,
    resume_session_id: str | None,
    continue_latest: bool,
    input_fn: Callable[[str], str],
) -> int:
    console = Console()
    renderer = EventRenderer(console=console)
    async with HomeServerClient(base_url) as client:
        try:
            meta = await client.meta()
        except ServerError:
            meta = {}
        session_id = await _resolve_session(
            client, resume_session_id=resume_session_id, continue_latest=continue_latest
        )
        ctx = RemoteShellContext(
            client=client,
            session_id=session_id,
            console=console,
            input_fn=input_fn,
        )
        ctx.loop = asyncio.get_running_loop()
        ctx.meta = meta
        ctx.switch_session = lambda sid: _switch_session(ctx, sid)
        ctx.on_toggle_mode = lambda: _request_mode_toggle(ctx)

        await client.subscribe(session_id)
        try:
            status = await client.status(session_id)
            if isinstance(status.get("ui_mode"), str):
                ctx.ui_mode = status["ui_mode"]
        except ServerError:
            pass

        pump = asyncio.create_task(_event_pump(ctx, renderer), name="homemaster-event-pump")
        try:
            await _seed_pending(ctx)
            version = str(meta.get("version") or _shell_version())
            ctx.echo(f"HomeMaster {version} — connected to {base_url}")
            ctx.echo(
                "Enter a task. Commands: /help, /new, /session, /model, /mode, "
                "/compact, /status, /exit."
            )
            return await _prompt_loop(ctx, renderer)
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)


async def _resolve_session(
    client: HomeServerClient,
    *,
    resume_session_id: str | None,
    continue_latest: bool,
) -> str:
    if resume_session_id:
        return await client.create_session(resume_session_id)
    if continue_latest:
        sessions = await client.list_sessions()
        if not sessions:
            raise ServerError("no persisted session is available to continue")
        return await client.create_session(str(sessions[0]["session_id"]))
    return await client.create_session()


async def _switch_session(ctx: RemoteShellContext, session_id: str) -> None:
    await ctx.client.subscribe(session_id)
    ctx.session_id = session_id
    ctx.dock.clear()
    ctx.busy = False
    ctx.decided_interactions.clear()
    ctx.inflight_interactions.clear()
    try:
        status = await ctx.client.status(session_id)
        if isinstance(status.get("ui_mode"), str):
            ctx.ui_mode = status["ui_mode"]
        else:
            ctx.ui_mode = None
    except ServerError:
        ctx.ui_mode = None


def _request_mode_toggle(ctx: RemoteShellContext) -> None:
    """Tab pressed on an empty prompt: schedule the mode flip on the loop."""

    target = "plan" if ctx.ui_mode != "plan" else "act"
    ctx.ui_mode = target  # optimistic; the WS echo reconciles

    async def flip() -> None:
        try:
            result = await ctx.client.set_mode(target, ctx.session_id)
            ctx.ui_mode = str(result.get("ui_mode") or target)
        except ServerError as exc:
            ctx.ui_mode = "plan" if target == "act" else "act"
            ctx.echo(f"mode switch failed: {exc}")

    loop = ctx.loop
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        running.create_task(flip())
    elif loop is not None and loop.is_running():
        # The key handler ran on a foreign thread — hop to the shell loop.
        asyncio.run_coroutine_threadsafe(flip(), loop)


async def _seed_pending(ctx: RemoteShellContext) -> None:
    """Enqueue pending approvals/questions discovered before we subscribed."""

    try:
        approvals = await ctx.client.list_approvals(ctx.session_id)
    except ServerError:
        approvals = []
    try:
        questions = await ctx.client.list_questions(ctx.session_id)
    except ServerError:
        questions = []
    for approval in approvals:
        _enqueue_interaction(ctx, {"type": "approval.requested", "payload": approval})
    for question in questions:
        _enqueue_interaction(ctx, {"type": "question.asked", "payload": question})


async def _event_pump(ctx: RemoteShellContext, renderer: EventRenderer) -> None:
    try:
        await _pump_events(ctx, renderer)
    finally:
        if ctx.exit_reason is None:
            ctx.echo(
                "[server connection closed — reconnect with `homemaster shell`]"
            )
            ctx.turn_done.set()  # release a prompt blocked in _wait_turn


async def _pump_events(ctx: RemoteShellContext, renderer: EventRenderer) -> None:
    async for event in ctx.client.events():
        sid = event.get("session_id")
        if sid and ctx.session_id and sid != ctx.session_id:
            continue  # stale frame from a previous subscription
        etype = event.get("type")
        if etype == RESYNC_EVENT_TYPE:
            # The socket dropped and reconnected; events emitted in between
            # are gone, so repopulate pending interactions and reconcile the
            # turn latch with the server's own status.
            renderer.render(event)
            await _consume_resync(ctx, event, renderer)
            continue
        if etype == "run.started":
            ctx.busy = True
        if etype == "session.mode_changed":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if isinstance(payload.get("ui_mode"), str):
                ctx.ui_mode = payload["ui_mode"]
        if etype == "usage.updated":
            renderer.render(event)
            ctx.last_usage = dict(renderer.last_usage)
            continue
        if etype in {"approval.requested", "question.asked"}:
            if _enqueue_interaction(ctx, event):
                renderer.render(event)
            continue
        renderer.render(event)
        if is_terminal_event(event):
            ctx.busy = False
            ctx.last_status = terminal_status(event)
            ctx.last_run_id = event.get("run_id") or ctx.last_run_id
            ctx.turn_done.set()
            await _flush_dock(ctx)


async def _consume_resync(
    ctx: RemoteShellContext,
    event: dict[str, Any],
    renderer: EventRenderer,
) -> None:
    """Consume a ``client.resync`` marker: re-prompt pendings, clear dead turns.

    Pending approvals/questions survived server-side but their WS events were
    lost with the dropped socket, so they come back as synthetic interactive
    events; dedup keeps ones already queued or decided from re-prompting.
    """

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    for synthetic in resync_interaction_events(payload, session_id=ctx.session_id):
        if _enqueue_interaction(ctx, synthetic):
            renderer.render(synthetic)
    status = await resync_session_status(ctx)
    if status is None:
        return  # server could not report — keep waiting on real events
    ui_mode = status.get("ui_mode")
    if isinstance(ui_mode, str):
        ctx.ui_mode = ui_mode
    if not (ctx.busy and session_status_idle(status)):
        return
    # The run reached a terminal state while the socket was down and no
    # run.* event will ever arrive — release the turn latch and flush the dock.
    ctx.busy = False
    ctx.last_status = str(status.get("status") or ctx.last_status)
    ctx.turn_done.set()
    await _flush_dock(ctx)


def _enqueue_interaction(ctx: RemoteShellContext, event: dict[str, Any]) -> bool:
    """Queue an interactive event once per (kind, id, revision)."""

    if not interaction_needs_prompt(ctx, event):
        return False
    key = interaction_key(event)
    assert key is not None
    ctx.inflight_interactions.add(key)
    ctx.interactions.put_nowait(event)
    return True


async def _flush_dock(ctx: RemoteShellContext) -> None:
    """QueueDock semantics: queued input auto-sends once the turn is terminal."""

    while ctx.dock and not ctx.busy:
        text = ctx.dock.pop(0)
        try:
            await ctx.client.send(
                text,
                session_id=ctx.session_id,
                provider_name=ctx.provider_name,
                model=ctx.model,
            )
        except SessionBusyError:
            ctx.dock.insert(0, text)
            return
        except ServerError as exc:
            ctx.echo(f"queued message failed: {exc}")
            continue
        ctx.busy = True
        ctx.echo("→ sent queued message")


async def _prompt_loop(ctx: RemoteShellContext, renderer: EventRenderer) -> int:
    prompt_reader = _build_prompt_reader(ctx)
    while True:
        await _drain_interactions(ctx)
        try:
            if prompt_reader is not None:
                line = await prompt_reader()
            else:
                line = await _read_plain(ctx, "homemaster> ")
        except EOFError:
            ctx.exit_reason = "eof"
            ctx.echo("Goodbye")
            return 0
        except KeyboardInterrupt:
            ctx.exit_reason = "interrupt"
            ctx.echo("\nGoodbye")
            return 0
        utterance = line.strip()
        if not utterance:
            continue
        if utterance.startswith("!"):
            command = utterance[1:].strip()
            if command:
                await asyncio.to_thread(run_local_command, command)
            continue
        interpolated = interpolate_bang_output(utterance)
        if interpolated is None:
            continue
        utterance = interpolated
        if await dispatch_remote_command(utterance, ctx):
            if ctx.exit_reason is not None:
                ctx.echo("Goodbye")
                return 0
            continue
        if utterance.startswith("/"):
            try:
                resolved = await ctx.client.resolve_skill(utterance)
            except ServerError:
                resolved = {"kind": "plain"}
            if isinstance(resolved, dict) and resolved.get("kind") == "skill":
                ctx.echo(
                    f"skill /{resolved.get('name')}: resolved server-side"
                )
            else:
                name = utterance[1:].partition(" ")[0].strip()
                unknown_remote_hint(name, ctx)
                continue
        if ctx.busy:
            ctx.dock.append(utterance)
            ctx.echo(f"queued ({len(ctx.dock)} pending — session busy)")
            continue
        # Clear the terminal latch BEFORE send — run.completed can beat the
        # POST response on a fast server and the flag must be armed already.
        ctx.turn_done.clear()
        try:
            await ctx.client.send(
                utterance,
                session_id=ctx.session_id,
                provider_name=ctx.provider_name,
                model=ctx.model,
            )
        except SessionBusyError:
            ctx.turn_done.set()
            ctx.dock.append(utterance)
            ctx.echo(f"queued ({len(ctx.dock)} pending — session busy)")
            continue
        except ServerError as exc:
            ctx.turn_done.set()
            ctx.last_status = "failed"
            ctx.echo(f"send failed: {exc}")
            continue
        try:
            await _wait_turn(ctx)
        except KeyboardInterrupt:
            try:
                await ctx.client.cancel(ctx.session_id)
            except ServerError:
                pass
            ctx.last_status = "cancelled"
            ctx.echo("Run cancelled.")


async def _wait_turn(ctx: RemoteShellContext) -> None:
    """Wait for the active turn's terminal event, answering interactions."""

    while not ctx.turn_done.is_set():
        try:
            item = await asyncio.wait_for(ctx.interactions.get(), timeout=0.1)
        except TimeoutError:
            continue
        await _handle_interaction(ctx, item)


async def _drain_interactions(ctx: RemoteShellContext) -> None:
    while True:
        try:
            item = ctx.interactions.get_nowait()
        except asyncio.QueueEmpty:
            return
        await _handle_interaction(ctx, item)


async def _handle_interaction(ctx: RemoteShellContext, event: dict[str, Any]) -> None:
    key = interaction_key(event)
    if key is not None and key in ctx.decided_interactions:
        ctx.inflight_interactions.discard(key)
        return
    try:
        etype = event.get("type")
        if etype == "approval.requested":
            await handle_approval_event(ctx, event)
        elif etype == "question.asked":
            await handle_question_event(ctx, event)
    finally:
        if key is not None:
            # Interactions that failed to submit stay pending server-side, so
            # they leave the inflight set and a later resync may re-prompt.
            ctx.inflight_interactions.discard(key)


def _build_prompt_reader(ctx: RemoteShellContext) -> Callable[[], Any] | None:
    """prompt_toolkit async reader on TTY, None for the input() fallback."""

    from homemaster.cli.prompt_loop import interactive_prompt_supported

    if not interactive_prompt_supported():
        return None
    from homemaster.cli.prompt_loop import ShellPrompt

    prompt = ShellPrompt(
        model_name=lambda: ctx.display_model(),
        context_usage=lambda: _context_usage(ctx),
        ui_mode=lambda: ctx.ui_mode,
        on_toggle_mode=ctx.on_toggle_mode,
        commands=REMOTE_REGISTRY,
    )
    return prompt.read_async


def _context_usage(ctx: RemoteShellContext) -> str | None:
    total = ctx.last_usage.get("total_tokens")
    if total:
        return f"{total}"
    return None


async def _read_plain(ctx: RemoteShellContext, prompt: str) -> str:
    """Non-TTY fallback — plain input() on a daemon thread."""

    import queue as _queue
    import threading

    done: _queue.Queue[tuple[str, Any]] = _queue.Queue()

    def reader() -> None:
        try:
            done.put(("ok", ctx.input(prompt)))
        except BaseException as exc:  # noqa: BLE001
            done.put(("err", exc))

    threading.Thread(target=reader, name="homemaster-prompt", daemon=True).start()
    while True:
        try:
            status, value = done.get_nowait()
        except _queue.Empty:
            await asyncio.sleep(0.02)
            continue
        if status == "ok":
            return str(value)
        raise value


__all__ = ["run_remote_shell"]
