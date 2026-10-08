"""Slash-command registry for the remote (thin-client) shell.

Commands run against the HomeMaster server through ``HomeServerClient`` — the
same protocol the web console drives.  Handlers receive a
``RemoteShellContext`` (client + session state) instead of the in-process
``ShellContext``; they may be sync or async — ``dispatch_remote_command``
awaits awaitables.
"""

from __future__ import annotations

import difflib
import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC
from typing import Any

from rich.console import Console

from homemaster.cli.client import (
    ApiError,
    HomeServerClient,
    ServerError,
    ServerUnavailableError,
)
from homemaster.cli.keymap import REMOTE_KEY_HELP_LINES
from homemaster.cli.shell_commands import (
    CommandSpec,
    echo_unknown_command_hint,
    find_command,
    render_help,
    slash_command,
)

REMOTE_REGISTRY: list[CommandSpec] = []

_remote_command = lambda name, **kwargs: slash_command(  # noqa: E731
    name, registry=REMOTE_REGISTRY, **kwargs
)

_HISTORY_PREVIEW_LIMIT = 15
_PREVIEW_CHARS = 60


class RemoteShellContext:
    """Mutable state shared between the remote prompt loop and handlers."""

    def __init__(
        self,
        *,
        client: HomeServerClient,
        session_id: str,
        console: Console | None = None,
        echo: Callable[[str], None] | None = None,
        input_fn: Callable[[str], str] = input,
    ) -> None:
        self.client = client
        self.session_id = session_id
        self.console = console or Console()
        self.echo = echo or (lambda text: self.console.print(text, markup=False))
        self.input = input_fn
        # Per-message provider/model override, set by /model; passed through
        # SendMessageRequest on every subsequent send.
        self.provider_name: str | None = None
        self.model: str | None = None
        # Server-owned session ui_mode (plan|act); kept in sync by
        # session.mode_changed events and POST /mode responses.
        self.ui_mode: str | None = None
        self.busy = False
        # QueueDock: messages submitted while the session was busy get flushed
        # in order once the active turn reaches a terminal event.
        self.dock: list[str] = []
        self.last_status = "idle"
        self.last_run_id: str | None = None
        self.last_usage: dict[str, int] = {}
        self.exit_reason: str | None = None
        self.meta: dict[str, Any] = {}
        # Session-scoped allows are server-side now (allow_session choice).
        # Interactive event queue + terminal latch driven by the event pump.
        import asyncio

        self.interactions: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.turn_done = asyncio.Event()
        self.turn_done.set()
        # Interaction dedup keyed by (kind, id, revision): an id moves from
        # ``inflight`` (queued/prompting) to ``decided`` once this client
        # submits it, so a post-reconnect resync never re-prompts a resolved
        # approval/question nor double-queues a pending one.
        self.decided_interactions: set[tuple[str, str, Any]] = set()
        self.inflight_interactions: set[tuple[str, str, Any]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        # Late-bound by remote_shell (session switching touches the event
        # subscription; Tab mode toggle must reach the running event loop).
        self.switch_session: Callable[[str], Awaitable[None]] | None = None
        self.on_toggle_mode: Callable[[], None] | None = None

    def display_model(self) -> str:
        if self.model:
            return self.model
        return "default"


async def dispatch_remote_command(utterance: str, ctx: RemoteShellContext) -> bool:
    """Run a registered ``/`` command; awaits async handlers."""

    if not utterance.startswith("/"):
        return False
    name, _, args = utterance[1:].partition(" ")
    name = name.strip()
    if not name:
        return False
    spec = find_command(name, REMOTE_REGISTRY)
    if spec is None:
        return False
    try:
        result = spec.run(ctx, args)
        if inspect.isawaitable(result):
            await result
    except ServerUnavailableError as exc:
        ctx.echo(f"Server unavailable: {exc}")
    except ApiError as exc:
        ctx.echo(f"{exc.code}: {exc}")
    return True


def _command_name_completions(prefix: str) -> Iterable[tuple[str, str]]:
    for spec in REMOTE_REGISTRY:
        for candidate in (spec.name, *spec.aliases):
            if candidate.startswith(prefix):
                yield candidate, spec.help


def _relative_time(updated_at: str | None) -> str:
    if not updated_at:
        return "?"
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        delta = datetime.now(UTC) - parsed
    except (ValueError, TypeError):
        return str(updated_at)
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _session_label(summary: dict[str, Any]) -> str:
    return f"{summary.get('session_id', '')} {summary.get('title', '')}"


def resolve_resume_session(
    term: str, sessions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Three-level match: exact id → unique substring → fuzzy (openclaw tui)."""

    needle = term.strip()
    if not needle:
        return None
    for summary in sessions:
        if str(summary.get("session_id")) == needle:
            return summary
    sub = [
        s
        for s in sessions
        if needle.casefold() in str(s.get("session_id", "")).casefold()
        or needle.casefold() in str(s.get("title", "")).casefold()
    ]
    if len(sub) == 1:
        return sub[0]
    if sub:
        return None  # ambiguous — caller falls back to the picker
    labels = {_session_label(s): s for s in sessions}
    match = difflib.get_close_matches(
        needle, list(labels), n=1, cutoff=0.4
    ) or difflib.get_close_matches(
        needle,
        [str(s.get("session_id", "")) for s in sessions],
        n=1,
        cutoff=0.5,
    )
    if not match:
        return None
    found = match[0]
    return labels.get(found) or next(
        (s for s in sessions if str(s.get("session_id")) == found), None
    )


async def _resume_session(ctx: RemoteShellContext, session_id: str) -> None:
    assert ctx.switch_session is not None
    await ctx.switch_session(session_id)


async def _list_sessions_with_preview(ctx: RemoteShellContext) -> list[dict[str, Any]]:
    sessions = await ctx.client.list_sessions()
    for summary in sessions[:_HISTORY_PREVIEW_LIMIT]:
        try:
            messages = await ctx.client.history(str(summary.get("session_id")))
        except ServerError:
            continue
        preview = ""
        for message in reversed(messages):
            text = str(message.get("text") or "").strip().splitlines()
            if text:
                preview = text[0][:_PREVIEW_CHARS]
                break
        summary["preview"] = preview
    return sessions


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@_remote_command("new", help="create a fresh session on the server.")
async def _cmd_new(ctx: RemoteShellContext, args: str) -> None:
    del args
    session_id = await ctx.client.create_session()
    await _resume_session(ctx, session_id)
    ctx.echo(f"New session: {session_id}")


@_remote_command("compact", help="persist an immediate context compaction.")
async def _cmd_compact(ctx: RemoteShellContext, args: str) -> None:
    del args
    result = await ctx.client.compact(ctx.session_id)
    status = result.get("status") or ("triggered" if result.get("triggered") else "noop")
    detail = ", ".join(
        f"{key}={result[key]}"
        for key in ("kind", "revision", "status", "triggered")
        if key in result
    )
    ctx.echo(f"Context compaction: {status}" + (f" ({detail})" if detail else ""))


@_remote_command("archive", help="archive this session into long-term memory.")
async def _cmd_archive(ctx: RemoteShellContext, args: str) -> None:
    del args
    try:
        result = await ctx.client.archive(ctx.session_id)
    except Exception as exc:
        ctx.echo(f"Archive failed: {exc}")
        return
    ctx.echo(
        f"Archival queued for session {result.get('session_id', ctx.session_id)}"
        + (f" (job {result['job_id']})" if result.get("job_id") else "")
        + " — memory consolidation runs in the background."
    )


@_remote_command("status", help="show session status (server).")
async def _cmd_status(ctx: RemoteShellContext, args: str) -> None:
    del args
    status = await ctx.client.status(ctx.session_id)
    fields = []
    for key in ("status", "generation", "revision", "active", "ui_mode"):
        if key in status:
            fields.append(f"{key}={status[key]}")
    extras = sorted(set(status) - {"status", "generation", "revision", "active", "ui_mode"})
    fields.extend(f"{key}={status[key]}" for key in extras[:6])
    ctx.echo(
        f"Status: {'; '.join(fields) if fields else status}"
        f"; busy={str(ctx.busy).lower()}; model={ctx.display_model()}"
    )


@_remote_command(
    "mode",
    usage="[plan|act]",
    help="switch Plan/Act mode (Tab on an empty prompt toggles too).",
)
async def _cmd_mode(ctx: RemoteShellContext, args: str) -> None:
    target = args.strip().lower()
    if target and target not in ("plan", "act"):
        ctx.echo("Usage: /mode [plan|act]")
        return
    if not target:
        target = "plan" if ctx.ui_mode != "plan" else "act"
    result = await ctx.client.set_mode(target, ctx.session_id)
    ctx.ui_mode = str(result.get("ui_mode") or target)
    ctx.echo(f"Mode: {ctx.ui_mode}")


@_remote_command(
    "model",
    usage="[provider-or-model]",
    help="pick provider/model; bare lists configured providers.",
)
async def _cmd_model(ctx: RemoteShellContext, args: str) -> None:
    term = args.strip()
    providers = await ctx.client.providers()
    if not term:
        await _model_picker(ctx, providers, prefill=None)
        return
    exact = [
        p
        for p in providers
        if str(p.get("name", "")).casefold() == term.casefold()
    ]
    if len(exact) == 1:
        _apply_provider(ctx, exact[0])
        return
    partial = [
        p
        for p in providers
        if term.casefold() in str(p.get("name", "")).casefold()
        or term.casefold() in str(p.get("model", "")).casefold()
    ]
    if len(partial) == 1:
        _apply_provider(ctx, partial[0])
        return
    if len(partial) > 1:
        ctx.echo(f"/model {term}: multiple matches — pick one:")
        await _model_picker(ctx, partial, prefill=term)
        return
    # Miss → free-text model id (cline ModelPickerWithManualEntry): the term
    # itself seeds the custom entry.
    await _model_picker(ctx, providers, prefill=term)


def _apply_provider(ctx: RemoteShellContext, provider: dict[str, Any]) -> None:
    ctx.provider_name = str(provider.get("name") or "") or None
    ctx.model = None  # provider's configured model applies server-side
    ctx.echo(
        f"Model: provider={ctx.provider_name} (model from server config "
        f"{provider.get('model', '?')})"
    )


async def _model_picker(
    ctx: RemoteShellContext,
    providers: list[dict[str, Any]],
    *,
    prefill: str | None,
) -> None:
    lines = ["Providers:"]
    for index, provider in enumerate(providers, start=1):
        key = "key ✓" if provider.get("api_key_configured") else "key ✗"
        current = " *current*" if provider.get("name") == ctx.provider_name else ""
        lines.append(
            f"  {index}. {provider.get('name')} ({provider.get('kind', '?')}) "
            f"model={provider.get('model', '?')} [{key}]{current}"
        )
    hint = prefill or ctx.model or ""
    lines.append(
        "Enter a number, a provider name, or a custom model id"
        + (f" [{hint}]" if hint else "")
        + ":"
    )
    ctx.echo("\n".join(lines))
    try:
        choice = (await _read(ctx, "model> ")).strip()
    except (EOFError, KeyboardInterrupt):
        ctx.echo("(model unchanged)")
        return
    if not choice:
        choice = hint
    if not choice:
        return
    if choice.isdigit() and 1 <= int(choice) <= len(providers):
        _apply_provider(ctx, providers[int(choice) - 1])
        return
    for provider in providers:
        if str(provider.get("name", "")).casefold() == choice.casefold():
            _apply_provider(ctx, provider)
            return
    # Custom model id — keep the selected/default provider.
    provider_name = ctx.provider_name or (
        str(providers[0].get("name")) if providers else None
    )
    ctx.provider_name = provider_name
    ctx.model = choice
    ctx.echo(f"Model: provider={provider_name or 'default'} model={choice}")


@_remote_command(
    "session",
    usage="[id-or-title]",
    help="list sessions / fuzzy-resume one.",
)
async def _cmd_session(ctx: RemoteShellContext, args: str) -> None:
    term = args.strip()
    sessions = await _list_sessions_with_preview(ctx)
    if not sessions:
        ctx.echo("No sessions on this server.")
        return
    if term:
        match = resolve_resume_session(term, sessions)
        if match is None:
            ctx.echo(f"/session {term}: no unique match.")
            await _session_picker(ctx, sessions)
            return
        await _resume_session(ctx, str(match["session_id"]))
        ctx.echo(f"Resumed session: {match['session_id']}")
        return
    await _session_picker(ctx, sessions)


async def _session_picker(
    ctx: RemoteShellContext, sessions: list[dict[str, Any]]
) -> None:
    lines = ["Sessions:"]
    for index, summary in enumerate(sessions, start=1):
        marker = " *current*" if summary.get("session_id") == ctx.session_id else ""
        preview = summary.get("preview") or ""
        tail = f" — {preview}" if preview else ""
        lines.append(
            f"  {index}. {summary.get('session_id')} "
            f"[{_relative_time(summary.get('updated_at'))}, "
            f"{summary.get('message_count', 0)} msgs] "
            f"{summary.get('title') or ''}{tail}{marker}"
        )
    ctx.echo("\n".join(lines))
    try:
        choice = (await _read(ctx, "session> ")).strip()
    except (EOFError, KeyboardInterrupt):
        ctx.echo("(unchanged)")
        return
    if not choice:
        return
    if choice.isdigit() and 1 <= int(choice) <= len(sessions):
        target = sessions[int(choice) - 1]
    else:
        target = resolve_resume_session(choice, sessions)
    if target is None:
        ctx.echo("No matching session.")
        return
    await _resume_session(ctx, str(target["session_id"]))
    ctx.echo(f"Resumed session: {target['session_id']}")


@_remote_command("cancel", help="cancel the active run on this session.")
async def _cmd_cancel(ctx: RemoteShellContext, args: str) -> None:
    del args
    result = await ctx.client.cancel(ctx.session_id)
    ctx.echo("Cancelled." if result.get("cancelled") else "Nothing to cancel.")


@_remote_command("events", help="show the connected server and session.")
def _cmd_events(ctx: RemoteShellContext, args: str) -> None:
    del args
    ctx.echo(f"Server: {ctx.client.base_url}  Session: {ctx.session_id}")


@_remote_command("doctor", help="check local configuration and dependencies.")
def _cmd_doctor(ctx: RemoteShellContext, args: str) -> None:
    del args
    from homemaster.cli.doctor import render_doctor_text, run_doctor

    ctx.echo(render_doctor_text(run_doctor(live=False)))


@_remote_command("debug", help="show the last run id and connection state.")
def _cmd_debug(ctx: RemoteShellContext, args: str) -> None:
    del args
    ctx.echo(
        f"Debug: run_id={ctx.last_run_id or 'none'} state={ctx.client.state} "
        f"gen={ctx.client.generation}"
    )


@_remote_command(
    "help",
    aliases=("?",),
    usage="[command]",
    help="show this help.",
    arg_completer=_command_name_completions,
)
def _cmd_help(ctx: RemoteShellContext, args: str) -> None:
    del args
    ctx.echo(render_help(REMOTE_REGISTRY, extra_key_lines=REMOTE_KEY_HELP_LINES))
    ctx.echo("Deferred: /undo, /tree, external editor (need server support).")


@_remote_command(
    "exit",
    aliases=("quit", "q"),
    help="leave the remote session and exit.",
)
def _cmd_exit(ctx: RemoteShellContext, args: str) -> None:
    del args
    ctx.exit_reason = "user_exit"


async def _read(ctx: RemoteShellContext, prompt: str) -> str:
    """Read one interactive answer on a daemon thread (never blocks exit)."""

    import asyncio
    import queue as _queue
    import threading

    done: _queue.Queue[tuple[str, Any]] = _queue.Queue()

    def reader() -> None:
        try:
            done.put(("ok", ctx.input(prompt)))
        except BaseException as exc:  # noqa: BLE001 — re-raised on the loop
            done.put(("err", exc))

    threading.Thread(target=reader, name="homemaster-input", daemon=True).start()
    while True:
        try:
            status, value = done.get_nowait()
        except _queue.Empty:
            await asyncio.sleep(0.02)
            continue
        if status == "ok":
            return str(value)
        raise value


# ---------------------------------------------------------------------------
# Interactive approval / question flows (shared by the shell and `run`)
# ---------------------------------------------------------------------------
_APPROVAL_CHOICES = {"1": "once", "2": "session", "3": "always", "4": "deny"}


async def handle_approval_event(
    ctx: RemoteShellContext, event: dict[str, Any]
) -> None:
    """Walk one ``approval.requested`` payload item by item and submit v2."""

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    approval_id = str(payload.get("approval_id") or "")
    if not approval_id:
        return
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if not items:
        return
    revision = payload.get("revision")
    decisions: list[dict[str, str]] = []
    try:
        for item in items:
            decisions.append(await _decide_item(ctx, item))
    except (EOFError, KeyboardInterrupt):
        # Input ended mid-prompt — fail closed: reject the whole card.
        decisions = [
            {"item_id": str(item.get("item_id")), "choice": "reject"}
            for item in items
        ]
        ctx.echo("(input ended — rejected)")
    if not decisions:
        return
    if not isinstance(revision, int):
        try:
            stored = await ctx.client.get_approval(approval_id)
            revision = stored.get("revision")
        except ServerError:
            revision = None
    if not isinstance(revision, int):
        ctx.echo("approval has no revision; cannot submit")
        return
    try:
        resolution = await ctx.client.submit_approval(
            approval_id,
            request_revision=revision,
            decisions=decisions,
        )
        ctx.decided_interactions.add(("approval", approval_id, revision))
        ctx.echo(
            f"approval {approval_id}: {resolution.get('request_status', 'submitted')}"
        )
    except ApiError as exc:
        ctx.echo(f"approval {approval_id}: {exc.code} — {exc}")


async def _decide_item(ctx: RemoteShellContext, item: dict[str, Any]) -> dict[str, str]:
    item_id = str(item.get("item_id") or "")
    ctx.echo(
        f"  • {item.get('action_label', 'action')}: "
        f"{item.get('display_name', item_id)} ({item.get('location', '')})\n"
        "    [1] once  [2] this session  [3] always  [4] deny"
    )
    while True:
        answer = (await _read(ctx, "approve> ")).strip().lower()
        choice = _APPROVAL_CHOICES.get(answer) or (
            answer if answer in ("once", "session", "always", "deny") else None
        )
        if choice == "always":
            confirm = (
                await _read(ctx, "    allow ALWAYS for future requests? [y/N] ")
            ).strip().lower()
            if confirm in ("y", "yes"):
                return {"item_id": item_id, "choice": "allow_always"}
            continue
        if choice == "session":
            return {"item_id": item_id, "choice": "allow_session"}
        if choice == "deny":
            try:
                reason = await _read(ctx, "    deny reason (optional): ")
            except (EOFError, KeyboardInterrupt):
                reason = ""
            # The v2 submission has no reason field — it stays a terminal
            # record only; the server sees the reject decision itself.
            if reason.strip():
                ctx.echo(f"    denied: {reason.strip()}")
            return {"item_id": item_id, "choice": "reject"}
        if choice == "once":
            return {"item_id": item_id, "choice": "allow_once"}
        ctx.echo("    answer 1/2/3/4 (once, session, always, deny)")


async def handle_question_event(
    ctx: RemoteShellContext, event: dict[str, Any]
) -> None:
    """Answer one ``question.asked`` payload via an ``ask>`` prompt."""

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    question_id = str(payload.get("question_id") or "")
    if not question_id:
        return
    try:
        answer = await _read(ctx, "ask> ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    try:
        await ctx.client.answer_question(question_id, answer)
    except ServerError as exc:
        ctx.echo(f"answer failed: {exc}")
    else:
        ctx.decided_interactions.add(("question", question_id, None))


# ---------------------------------------------------------------------------
# Reconnect-resync hydration (shared by the shell pump and `run`)
# ---------------------------------------------------------------------------
# Agent-side statuses that mean no turn is in flight on the session; used to
# detect that the active run reached a terminal state while the socket was
# down (its terminal event was lost with the dropped connection).
_IDLE_AGENT_STATUSES = frozenset(
    {"replied", "completed", "waiting_user", "failed", "cancelled", "idle"}
)


def interaction_key(event: Mapping[str, Any]) -> tuple[str, str, Any] | None:
    """Dedup identity for one interactive event: ``(kind, id, revision)``.

    Approvals key on ``approval_id`` + ``revision`` so a revised request
    re-prompts while a redelivery of the same revision does not; questions key
    on ``question_id`` (they carry no revision).
    """

    etype = event.get("type")
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    if etype == "approval.requested":
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            return None
        return ("approval", approval_id, payload.get("revision"))
    if etype == "question.asked":
        question_id = str(payload.get("question_id") or "")
        if not question_id:
            return None
        return ("question", question_id, None)
    return None


def interaction_needs_prompt(ctx: RemoteShellContext, event: Mapping[str, Any]) -> bool:
    """True unless the interaction was already decided or is already queued."""

    key = interaction_key(event)
    return (
        key is not None
        and key not in ctx.decided_interactions
        and key not in ctx.inflight_interactions
    )


def resync_interaction_events(
    payload: Mapping[str, Any], *, session_id: str
) -> list[dict[str, Any]]:
    """Rebuild interactive events from one ``client.resync`` payload.

    ``GET /{id}/approvals`` entries already carry the ``approval.requested``
    fields the handler reads (``approval_id``/``items``/``revision``), and
    ``GET /{id}/questions`` entries carry ``question_id``/``question`` —
    each dict becomes the synthetic event's payload verbatim.
    """

    events: list[dict[str, Any]] = []
    approvals = payload.get("approvals")
    if isinstance(approvals, list):
        for approval in approvals:
            if not isinstance(approval, dict) or not approval.get("approval_id"):
                continue
            events.append(
                {
                    "type": "approval.requested",
                    "session_id": session_id,
                    "run_id": str(approval.get("run_id") or ""),
                    "request_id": str(approval.get("request_id") or ""),
                    "payload": dict(approval),
                }
            )
    questions = payload.get("questions")
    if isinstance(questions, list):
        for question in questions:
            if not isinstance(question, dict) or not question.get("question_id"):
                continue
            events.append(
                {
                    "type": "question.asked",
                    "session_id": session_id,
                    "run_id": str(question.get("run_id") or ""),
                    "request_id": str(question.get("request_id") or ""),
                    "payload": dict(question),
                }
            )
    return events


async def resync_session_status(ctx: RemoteShellContext) -> dict[str, Any] | None:
    """GET the session status after a reconnect; ``None`` when unreportable."""

    try:
        status = await ctx.client.status(ctx.session_id)
    except ServerError:
        return None
    return dict(status) if isinstance(status, dict) else None


def session_status_idle(status: Mapping[str, Any]) -> bool:
    """True when the server reports no run in flight for the session."""

    active = status.get("active")
    if isinstance(active, bool):
        return not active
    # Older/partial payloads without ``active``: fall back to the agent status.
    return str(status.get("status") or "").lower() in _IDLE_AGENT_STATUSES


def unknown_remote_hint(name: str, ctx: RemoteShellContext) -> None:
    echo_unknown_command_hint(name, ctx, REMOTE_REGISTRY)


__all__ = [
    "REMOTE_REGISTRY",
    "RemoteShellContext",
    "dispatch_remote_command",
    "interaction_key",
    "interaction_needs_prompt",
    "resolve_resume_session",
    "resync_interaction_events",
    "resync_session_status",
    "session_status_idle",
    "unknown_remote_hint",
]
