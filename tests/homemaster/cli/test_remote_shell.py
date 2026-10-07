"""Black-box tests for the remote (thin-client) shell and its commands."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from typing import Any

import pytest
from rich.console import Console

from homemaster.cli import remote_shell as remote_shell_module
from homemaster.cli.client import SessionBusyError
from homemaster.cli.event_renderer import EventRenderer, terminal_status
from homemaster.cli.remote_commands import (
    RemoteShellContext,
    dispatch_remote_command,
    resolve_resume_session,
)
from homemaster.cli.remote_shell import (
    _drain_interactions,
    _flush_dock,
    _pump_events,
    run_remote_shell,
)
from homemaster.cli.renderers import OutputFormat
from homemaster.cli.run_command import _remote_run_events, remote_result_envelope


class FakeClient:
    """In-memory HomeServerClient stand-in with a controllable event queue."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.state = "connected"
        self.generation = 1
        self.created: list[str | None] = []
        self.sent: list[dict[str, Any]] = []
        self.modes: list[str] = []
        self.compacted: list[str] = []
        self.answered: list[tuple[str, str]] = []
        self.submissions: list[dict[str, Any]] = []
        self.subscribed: list[str] = []
        self.sessions: list[dict[str, Any]] = [
            {"session_id": "sess-1", "title": "first", "message_count": 2, "updated_at": ""},
            {"session_id": "sess-2", "title": "second", "message_count": 1, "updated_at": ""},
        ]
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False
        self.reply_text = "server reply"
        self.status_payload: dict[str, Any] = {
            "status": "idle",
            "active": False,
            "ui_mode": "act",
        }
        self.providers_payload = {
            "providers": [
                {"name": "Mimo", "kind": "chat", "model": "mimo-v2", "api_key_configured": True},
                {"name": "Zhipu", "kind": "chat", "model": "glm", "api_key_configured": True},
            ]
        }

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._closed = True

    async def aclose(self) -> None:
        self._closed = True

    async def meta(self) -> dict[str, Any]:
        return {"version": "9.9", "memory_mode": "files", "environment": "home"}

    async def create_session(self, session_id: str | None = None) -> str:
        self.created.append(session_id)
        return session_id or "sess-1"

    async def list_sessions(self) -> list[dict[str, Any]]:
        return list(self.sessions)

    async def subscribe(self, session_id: str) -> None:
        self.subscribed.append(session_id)

    async def status(self, session_id: str | None = None) -> dict[str, Any]:
        return {**self.status_payload, "session_id": session_id}

    async def history(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return [{"role": "assistant", "text": "earlier answer"}]

    async def providers(self) -> list[dict[str, Any]]:
        return list(self.providers_payload["providers"])

    async def compact(self, session_id: str | None = None) -> dict[str, Any]:
        self.compacted.append(session_id or "")
        return {"triggered": True, "kind": "manual_summary", "revision": 2}

    async def set_mode(self, ui_mode: str, session_id: str | None = None) -> dict[str, Any]:
        self.modes.append(ui_mode)
        self._queue.put_nowait(
            {
                "type": "session.mode_changed",
                "session_id": session_id,
                "run_id": "",
                "request_id": "",
                "payload": {"ui_mode": ui_mode},
            }
        )
        return {"ui_mode": ui_mode}

    async def cancel(self, session_id: str | None = None) -> dict[str, Any]:
        return {"cancelled": True}

    async def send(self, text: str, **kwargs: Any) -> dict[str, Any]:
        self.sent.append({"text": text, **kwargs})
        sid = kwargs.get("session_id") or "sess-1"
        for event in (
            {"type": "run.started", "payload": {}},
            {"type": "answer.delta", "payload": {"text": self.reply_text}},
            {
                "type": "run.completed",
                "payload": {"status": "replied", "final_reply": self.reply_text},
            },
        ):
            self._queue.put_nowait(
                {
                    "session_id": sid,
                    "run_id": "r-1",
                    "request_id": "q-1",
                    **event,
                }
            )
        return {"accepted": True}

    async def resolve_skill(self, text: str) -> dict[str, Any]:
        return {"kind": "plain"}

    async def list_approvals(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return []

    async def list_questions(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return []

    async def get_approval(self, approval_id: str) -> dict[str, Any]:
        return {"approval_id": approval_id, "revision": 1}

    async def submit_approval(self, approval_id: str, **kwargs: Any) -> dict[str, Any]:
        self.submissions.append({"approval_id": approval_id, **kwargs})
        return {"request_status": "resolved"}

    async def answer_question(
        self, question_id: str, text: str, session_id: str | None = None
    ) -> dict[str, Any]:
        self.answered.append((question_id, text))
        return {"answered": True}

    def push(self, event: dict[str, Any]) -> None:
        self._queue.put_nowait(event)

    async def events(self):
        while True:
            yield await self._queue.get()


def _inputs(*lines: str) -> Callable[[str], str]:
    """A scripted ``input()`` — raises EOFError when exhausted."""

    queue = list(lines)

    def read(prompt: str = "") -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read


def _run_shell(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lines: list[str],
    client: FakeClient | None = None,
) -> tuple[FakeClient, int, str]:
    captured_client = client or FakeClient("http://fake.test")

    def fake_ctor(base_url: str) -> FakeClient:
        return captured_client

    monkeypatch.setattr(remote_shell_module, "HomeServerClient", fake_ctor)
    monkeypatch.setattr(
        remote_shell_module,
        "ensure_server",
        lambda server=None, **kwargs: ("http://fake.test", None),
    )
    monkeypatch.setattr(
        "homemaster.cli.prompt_loop.interactive_prompt_supported",
        lambda: False,
    )
    buffer = io.StringIO()
    monkeypatch.setattr(
        remote_shell_module,
        "Console",
        lambda: Console(file=buffer, width=120, color_system=None),
    )
    code = run_remote_shell(input_fn=_inputs(*lines))
    return captured_client, code, buffer.getvalue()


def test_shell_sends_messages_and_prints_reply(monkeypatch, capsys) -> None:
    client, code, out = _run_shell(monkeypatch, lines=["hello there", "/exit"])

    assert code == 0
    assert client.sent[0]["text"] == "hello there"
    assert client.sent[0]["session_id"] == "sess-1"
    assert "server reply" in out


def test_shell_bang_runs_local_command_without_send(monkeypatch, capsys) -> None:
    client, code, _out = _run_shell(monkeypatch, lines=["!echo local-out", "/exit"])

    assert code == 0
    assert client.sent == []
    assert "local-out" in capsys.readouterr().out


def test_shell_unknown_slash_prints_hint_and_no_send(monkeypatch) -> None:
    client, code, out = _run_shell(monkeypatch, lines=["/nope", "/exit"])

    assert code == 0
    assert "Unknown command /nope" in out
    assert client.sent == []


def test_shell_exit_command_terminates(monkeypatch) -> None:
    _client, code, out = _run_shell(monkeypatch, lines=["/exit"])
    assert code == 0
    assert "Goodbye" in out


@pytest.mark.asyncio
async def test_flush_dock_sends_queued_messages_in_order() -> None:
    client = FakeClient("http://fake.test")
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=io.StringIO()),
    )
    ctx.provider_name = "Mimo"
    ctx.model = "mimo-x"
    ctx.dock = ["one", "two"]

    await _flush_dock(ctx)

    # One queued message per turn — the flushed send makes the session busy,
    # so "two" stays docked until that turn terminates.
    assert [m["text"] for m in client.sent] == ["one"]
    assert ctx.dock == ["two"]
    assert all(m["provider_name"] == "Mimo" for m in client.sent)
    assert ctx.busy is True  # last send started a new turn

    ctx.busy = False
    await _flush_dock(ctx)
    assert [m["text"] for m in client.sent] == ["one", "two"]
    assert ctx.dock == []


@pytest.mark.asyncio
async def test_flush_dock_requeues_on_busy() -> None:
    class BusyClient(FakeClient):
        async def send(self, text: str, **kwargs: Any) -> dict[str, Any]:
            raise SessionBusyError(409, "session_busy", "busy", True)

    client = BusyClient("http://fake.test")
    ctx = RemoteShellContext(client=client, session_id="s1", console=Console(file=io.StringIO()))
    ctx.dock = ["one"]

    await _flush_dock(ctx)

    assert ctx.dock == ["one"]
    assert client.sent == []


@pytest.mark.asyncio
async def test_command_status_compact_mode_and_new() -> None:
    client = FakeClient("http://fake.test")
    out = io.StringIO()
    ctx = RemoteShellContext(client=client, session_id="s1", console=Console(file=out))
    switched: list[str] = []
    ctx.switch_session = lambda sid: switched.append(sid) or asyncio.sleep(0)

    assert await dispatch_remote_command("/status", ctx)
    assert await dispatch_remote_command("/compact", ctx)
    assert await dispatch_remote_command("/mode plan", ctx)
    assert await dispatch_remote_command("/mode", ctx)  # toggles back to act
    assert await dispatch_remote_command("/new", ctx)

    rendered = out.getvalue()
    assert "busy=" in rendered
    assert "manual_summary" in rendered
    assert client.compacted == ["s1"]
    assert client.modes == ["plan", "act"]
    assert switched == ["sess-1"]  # /new creates + switches
    assert client.created == [None]


@pytest.mark.asyncio
async def test_model_command_exact_term_and_picker(monkeypatch) -> None:
    client = FakeClient("http://fake.test")
    out = io.StringIO()
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=out),
        input_fn=_inputs("2", "custom-model-9"),
    )

    # Exact provider name applies immediately.
    await dispatch_remote_command("/model Mimo", ctx)
    assert ctx.provider_name == "Mimo"
    assert ctx.model is None

    # Bare /model lists providers and accepts a numeric pick (Zhipu).
    ctx.provider_name = None
    await dispatch_remote_command("/model", ctx)
    assert ctx.provider_name == "Zhipu"
    rendered = out.getvalue()
    assert "Providers:" in rendered
    assert "api_key" not in rendered  # key material never surfaces

    # Free-text model id flows through as the model override.
    ctx.provider_name = None
    await dispatch_remote_command("/model does-not-match", ctx)
    assert ctx.model == "custom-model-9"


@pytest.mark.asyncio
async def test_session_command_lists_and_resumes() -> None:
    client = FakeClient("http://fake.test")
    out = io.StringIO()
    ctx = RemoteShellContext(client=client, session_id="sess-1", console=Console(file=out))
    switched: list[str] = []
    ctx.switch_session = lambda sid: switched.append(sid) or asyncio.sleep(0)

    await dispatch_remote_command("/session sess-2", ctx)
    assert switched == ["sess-2"]

    out.truncate(0)
    out.seek(0)
    ctx.input = _inputs("1")
    await dispatch_remote_command("/session", ctx)
    assert switched[-1] == "sess-1"  # picker selection resumes it
    assert "earlier answer" in out.getvalue()  # last-message preview


def test_resolve_resume_session_exact_substring_fuzzy() -> None:
    sessions = [
        {"session_id": "sess-abc", "title": "alpha task", "message_count": 1},
        {"session_id": "sess-def", "title": "beta task", "message_count": 1},
        {"session_id": "sess-xyz", "title": "gamma notes", "message_count": 1},
    ]
    assert resolve_resume_session("sess-abc", sessions)["session_id"] == "sess-abc"
    assert resolve_resume_session("beta", sessions)["session_id"] == "sess-def"
    assert resolve_resume_session("gam", sessions)["session_id"] == "sess-xyz"
    # Ambiguous substring returns None (caller falls back to the picker).
    assert resolve_resume_session("sess-", sessions) is None


@pytest.mark.asyncio
async def test_approval_flow_maps_terminal_choices_to_protocol_v2() -> None:
    from homemaster.cli.remote_commands import handle_approval_event

    client = FakeClient("http://fake.test")
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=io.StringIO()),
        input_fn=_inputs("2", "3", "y", "4", "not today"),
    )
    event = {
        "type": "approval.requested",
        "payload": {
            "approval_id": "a-1",
            "revision": 4,
            "intent_summary": "run commands",
            "items": [
                {"item_id": "i1", "action_label": "exec", "display_name": "terminal"},
                {"item_id": "i2", "action_label": "exec", "display_name": "fetch"},
                {"item_id": "i3", "action_label": "exec", "display_name": "danger"},
            ],
        },
    }
    await handle_approval_event(ctx, event)

    assert len(client.submissions) == 1
    submission = client.submissions[0]
    assert submission["request_revision"] == 4
    decisions = {d["item_id"]: d["choice"] for d in submission["decisions"]}
    # session → server-side allow_session; always → allow_always;
    # deny → reject (the optional reason stays terminal-local).
    assert decisions == {"i1": "allow_session", "i2": "allow_always", "i3": "reject"}


@pytest.mark.asyncio
async def test_question_flow_answers_via_endpoint() -> None:
    from homemaster.cli.remote_commands import handle_question_event

    client = FakeClient("http://fake.test")
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=io.StringIO()),
        input_fn=_inputs("the answer"),
    )
    await handle_question_event(
        ctx,
        {"type": "question.asked", "payload": {"question_id": "q9", "question": "?"}},
    )
    assert client.answered == [("q9", "the answer")]


def test_shell_remote_run_surfaces_send_reply_and_exits_clean(monkeypatch) -> None:
    """End-to-end through run_remote_shell: send → events → /exit → 0."""

    client, code, out = _run_shell(
        monkeypatch, lines=["task one", "task two", "/exit"]
    )
    assert code == 0
    assert [m["text"] for m in client.sent] == ["task one", "task two"]
    assert client.subscribed == ["sess-1"]
    assert out.count("server reply") >= 2


# ---------------------------------------------------------------------------
# Reconnect resync consumption
# ---------------------------------------------------------------------------


def _resync_event(
    session_id: str,
    *,
    approvals: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``client.resync`` marker the client emits after a reconnect."""

    return {
        "type": "client.resync",
        "session_id": session_id,
        "run_id": "",
        "request_id": "",
        "payload": {
            "resync": True,
            "generation": 2,
            "history": history or [],
            "approvals": approvals or [],
            "questions": questions or [],
        },
    }


def _usage_marker(session_id: str, tokens: int) -> dict[str, Any]:
    """A trailing event proving the pump consumed everything before it."""

    return {
        "type": "usage.updated",
        "session_id": session_id,
        "run_id": "",
        "request_id": "",
        "payload": {"total_tokens": tokens},
    }


async def _wait_pump(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll until the pump caught up (or fail the test via TimeoutError)."""

    waited = 0.0
    while not predicate():
        if waited >= timeout:
            raise TimeoutError("pump did not reach the expected state")
        await asyncio.sleep(0.01)
        waited += 0.01


@pytest.mark.asyncio
async def test_pump_resync_reprompts_approval_answers_question_and_clears_busy() -> None:
    """Reconnect with pending approval+question and an idle session.

    The resync payload is consumed, not just rendered: the pending approval and
    question are re-queued as interactive events, the answered decisions flow
    to the server endpoints, and the stale busy latch is released with the dock
    flushed so the shell does not hang.
    """

    client = FakeClient("http://fake.test")
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=io.StringIO()),
        input_fn=_inputs("1", "yes, proceed"),
    )
    ctx.busy = True
    ctx.turn_done.clear()
    ctx.dock = ["queued task"]
    renderer = EventRenderer(console=Console(file=io.StringIO()))
    pump = asyncio.create_task(_pump_events(ctx, renderer))
    try:
        client.push(
            _resync_event(
                "s1",
                approvals=[
                    {
                        "approval_id": "a-9",
                        "revision": 3,
                        "intent_summary": "run commands",
                        "items": [
                            {
                                "item_id": "i1",
                                "action_label": "exec",
                                "display_name": "terminal",
                                "location": "",
                            }
                        ],
                    }
                ],
                questions=[{"question_id": "q-7", "question": "proceed?"}],
                history=[{"role": "assistant", "text": "done"}],
            )
        )
        # turn_done is set only after the interactions are injected.
        await asyncio.wait_for(ctx.turn_done.wait(), timeout=2)
        await _drain_interactions(ctx)
        # The dock flush re-sends through the pump; its run.completed clears busy.
        await _wait_pump(lambda: bool(client.sent) and not ctx.busy)
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    assert client.submissions == [
        {
            "approval_id": "a-9",
            "request_revision": 3,
            "decisions": [{"item_id": "i1", "choice": "allow_once"}],
        }
    ]
    assert client.answered == [("q-7", "yes, proceed")]
    assert [m["text"] for m in client.sent] == ["queued task"]  # dock flushed
    assert ctx.busy is False


@pytest.mark.asyncio
async def test_pump_resync_keeps_busy_when_server_still_active() -> None:
    """A resync while the run continues re-prompts but does not end the turn."""

    client = FakeClient("http://fake.test")
    client.status_payload = {"status": "running", "active": True}
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=io.StringIO()),
        input_fn=_inputs("4", "no"),
    )
    ctx.busy = True
    ctx.turn_done.clear()
    ctx.dock = ["held task"]
    renderer = EventRenderer(console=Console(file=io.StringIO()))
    pump = asyncio.create_task(_pump_events(ctx, renderer))
    try:
        client.push(
            _resync_event(
                "s1",
                approvals=[
                    {
                        "approval_id": "a-9",
                        "revision": 3,
                        "intent_summary": "run commands",
                        "items": [
                            {
                                "item_id": "i1",
                                "action_label": "exec",
                                "display_name": "terminal",
                                "location": "",
                            }
                        ],
                    }
                ],
            )
        )
        client.push(_usage_marker("s1", 42))
        await _wait_pump(lambda: ctx.last_usage.get("total_tokens") == 42)
        await _drain_interactions(ctx)
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    # deny → reject submitted, yet the turn stays latched (still busy).
    assert client.submissions[0]["decisions"] == [{"item_id": "i1", "choice": "reject"}]
    assert ctx.busy is True
    assert not ctx.turn_done.is_set()
    assert client.sent == []  # dock is NOT flushed while the run is active


@pytest.mark.asyncio
async def test_pump_resync_dedups_queued_and_decided_interactions() -> None:
    """Same approval id+revision must not be re-prompted after a resync."""

    client = FakeClient("http://fake.test")
    ctx = RemoteShellContext(
        client=client,
        session_id="s1",
        console=Console(file=io.StringIO()),
        input_fn=_inputs("1", "the answer"),
    )
    renderer = EventRenderer(console=Console(file=io.StringIO()))
    # The WS event landed before the drop and is still queued unhandled.
    ws_event = {
        "type": "approval.requested",
        "session_id": "s1",
        "run_id": "r1",
        "request_id": "q1",
        "payload": {
            "approval_id": "a-9",
            "revision": 3,
            "intent_summary": "run commands",
            "items": [
                {
                    "item_id": "i1",
                    "action_label": "exec",
                    "display_name": "terminal",
                    "location": "",
                }
            ],
        },
    }
    pump = asyncio.create_task(_pump_events(ctx, renderer))
    try:
        client.push(ws_event)
        client.push(
            _resync_event(
                "s1",
                approvals=[dict(ws_event["payload"])],
                questions=[{"question_id": "q-7", "question": "proceed?"}],
            )
        )
        client.push(_usage_marker("s1", 7))
        await _wait_pump(lambda: ctx.last_usage.get("total_tokens") == 7)
        # Only one approval prompt despite the WS copy + the resynced listing.
        assert ctx.interactions.qsize() == 2  # one approval + one question
        await _drain_interactions(ctx)
        assert len(client.submissions) == 1
        assert client.answered == [("q-7", "the answer")]

        # A later resync listing the already-decided approval must not re-prompt.
        client.push(_resync_event("s1", approvals=[dict(ws_event["payload"])]))
        client.push(_usage_marker("s1", 8))
        await _wait_pump(lambda: ctx.last_usage.get("total_tokens") == 8)
        assert ctx.interactions.empty()
        assert len(client.submissions) == 1
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)


def test_shell_recovers_turn_when_run_finishes_during_disconnect(monkeypatch) -> None:
    """E2E: send → socket lost → resync sees idle → prompt returns (no hang)."""

    class DisconnectingClient(FakeClient):
        async def send(self, text: str, **kwargs: Any) -> dict[str, Any]:
            self.sent.append({"text": text, **kwargs})
            # run.started arrives but the terminal event is lost with the
            # dropped socket; the reconnect resync reports an idle session.
            self._queue.put_nowait(
                {
                    "type": "run.started",
                    "session_id": "sess-1",
                    "run_id": "r-1",
                    "request_id": "q-1",
                    "payload": {},
                }
            )
            self._queue.put_nowait(
                _resync_event(
                    "sess-1",
                    history=[{"role": "assistant", "text": "lost reply"}],
                )
            )
            return {"accepted": True}

    client = DisconnectingClient("http://fake.test")
    _client, code, out = _run_shell(
        monkeypatch, lines=["do work", "/exit"], client=client
    )
    assert code == 0
    assert "connection restored" in out


@pytest.mark.asyncio
async def test_remote_run_events_resync_idle_yields_truthful_terminal() -> None:
    """run: resync + idle session terminates with the server's real status."""

    client = FakeClient("http://fake.test")
    client.status_payload = {"status": "replied", "active": False}
    client.push(
        {
            "type": "run.started",
            "session_id": "s1",
            "run_id": "r-9",
            "request_id": "q1",
            "payload": {},
        }
    )
    client.push(
        _resync_event(
            "s1",
            history=[{"role": "assistant", "text": "final answer"}],
        )
    )

    terminal = await _remote_run_events(
        client=client,
        session_id="s1",
        output_format=OutputFormat.JSON,
        renderer=None,
        out=io.StringIO(),
        input_fn=_inputs(),
    )

    assert terminal is not None
    assert terminal["type"] == "run.completed"
    assert terminal["run_id"] == "r-9"  # recovered from the pre-drop run.started
    assert terminal_status(terminal) == "replied"  # server truth, not "completed"
    assert terminal["payload"]["final_reply"] == "final answer"
    envelope = remote_result_envelope(terminal, session_id="s1")
    assert envelope["status"] == "replied"
    assert envelope["final_reply"] == "final answer"


@pytest.mark.asyncio
async def test_remote_run_events_resync_cancelled_reports_cancelled() -> None:
    """A run cancelled during the disconnect surfaces as run.cancelled."""

    client = FakeClient("http://fake.test")
    client.status_payload = {"status": "cancelled", "active": False}
    client.push(_resync_event("s1"))

    terminal = await _remote_run_events(
        client=client,
        session_id="s1",
        output_format=OutputFormat.JSON,
        renderer=None,
        out=io.StringIO(),
        input_fn=_inputs(),
    )

    assert terminal is not None
    assert terminal["type"] == "run.cancelled"
    assert terminal_status(terminal) == "cancelled"


@pytest.mark.asyncio
async def test_remote_run_events_resync_busy_waits_and_answers_question() -> None:
    """run: resync while still active keeps waiting and re-prompts pendings."""

    client = FakeClient("http://fake.test")
    client.status_payload = {"status": "running", "active": True}
    client.push(
        _resync_event(
            "s1",
            questions=[{"question_id": "q-3", "question": "proceed?"}],
        )
    )
    client.push(
        {
            "type": "run.completed",
            "session_id": "s1",
            "run_id": "r-9",
            "request_id": "q1",
            "payload": {"status": "replied", "final_reply": "real reply"},
        }
    )

    terminal = await _remote_run_events(
        client=client,
        session_id="s1",
        output_format=OutputFormat.JSON,
        renderer=None,
        out=io.StringIO(),
        input_fn=_inputs("yes"),
    )

    # The resynced question reached the answer endpoint; the loop kept waiting
    # and exited on the REAL terminal event rather than a synthetic one.
    assert client.answered == [("q-3", "yes")]
    assert terminal is not None
    assert terminal["type"] == "run.completed"
    assert terminal["payload"]["final_reply"] == "real reply"


@pytest.mark.asyncio
async def test_remote_run_events_resync_skips_decided_approval() -> None:
    """run: an approval already decided before the drop is not re-prompted."""

    client = FakeClient("http://fake.test")
    client.status_payload = {"status": "running", "active": True}
    approval_payload = {
        "approval_id": "a-5",
        "revision": 2,
        "intent_summary": "run commands",
        "items": [
            {
                "item_id": "i1",
                "action_label": "exec",
                "display_name": "terminal",
                "location": "",
            }
        ],
    }
    client.push(
        {
            "type": "approval.requested",
            "session_id": "s1",
            "run_id": "r-9",
            "request_id": "q1",
            "payload": approval_payload,
        }
    )
    # The resync still lists it (stale read) — decided dedup must skip it.
    client.push(_resync_event("s1", approvals=[dict(approval_payload)]))
    client.push(
        {
            "type": "run.completed",
            "session_id": "s1",
            "run_id": "r-9",
            "request_id": "q1",
            "payload": {"status": "replied", "final_reply": "done"},
        }
    )

    terminal = await _remote_run_events(
        client=client,
        session_id="s1",
        output_format=OutputFormat.JSON,
        renderer=None,
        out=io.StringIO(),
        input_fn=_inputs("1"),
    )

    assert len(client.submissions) == 1  # prompted exactly once, before resync
    assert terminal is not None
    assert terminal["payload"]["final_reply"] == "done"
