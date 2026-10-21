"""Endpoint tests for ``GET /api/sessions/{id}/stream`` — the SSE channel that
replaced the ``/api/events`` WebSocket.

Covers: frame wire format (``id:``/``data:``), unknown-session 404 (the SSE
equivalent of the old 4404 close code), disconnect → ``unsubscribe`` cleanup,
the ``has_subscriber`` ordering gate for ``POST /messages``, and the ``:``
comment heartbeat.

Streams are driven through ``sse_support.asgi_client`` — TestClient /
``httpx.ASGITransport`` buffer an infinite SSE body and never return it.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sse_support import asgi_client
from test_app import _FakeApplication

import homemaster.web.sse as sse
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler


def _app(application: _FakeApplication) -> object:
    return create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )


def test_stream_unknown_session_is_http_404() -> None:
    """Finite response — plain TestClient is correct here."""

    app = _app(_FakeApplication())

    with TestClient(app) as client:
        response = client.get("/api/sessions/no-such-session/stream")

    assert response.status_code == 404
    assert response.json()["code"] == "session_not_found"
    assert response.json()["retryable"] is False


@pytest.mark.asyncio
async def test_stream_frame_format_id_matches_envelope_seq() -> None:
    application = _FakeApplication()
    app = _app(application)

    async with asgi_client(app) as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        # Read raw lines so the wire shape — not just the parsed payload — is
        # locked: every frame is ``id: {seq}`` + ``data: {json}`` + blank.
        async with client.stream(session_id) as events:
            assert events.status == 200
            assert events.headers["content-type"].startswith("text/event-stream")
            accepted = await client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "request-01", "text": "hello"},
            )
            assert accepted.status_code == 202

            for expected_type, expected_seq in [
                ("request.accepted", 1),
                ("run.started", 2),
            ]:
                lines = await events.receive_frame_lines()
                assert lines[0] == f"id: {expected_seq}"
                data_line = lines[1]
                frame = json.loads(data_line.removeprefix("data: "))
                assert frame["type"] == expected_type
                assert frame["seq"] == expected_seq
                assert frame["request_id"] == "request-01"


@pytest.mark.asyncio
async def test_stream_disconnect_unsubscribes_the_hub() -> None:
    application = _FakeApplication()
    app = _app(application)
    hub = app.state.event_hub

    async with asgi_client(app) as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        async with client.stream(session_id):
            assert await hub.has_subscriber(session_id) is True
        # Exiting the context sends http.disconnect and awaits the app task,
        # so the finally-block unsubscribe must already have run.
        assert await hub.has_subscriber(session_id) is False


@pytest.mark.asyncio
async def test_stream_subscription_gates_post_messages() -> None:
    """The subscribe-before-POST contract holds: inside the stream the POST
    is accepted; with no stream the same session is 409 event_stream_not_ready."""

    application = _FakeApplication()
    app = _app(application)

    async with asgi_client(app) as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]

        not_ready = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"request_id": "req-early", "text": "too early"},
        )
        assert not_ready.status_code == 409
        assert not_ready.json()["code"] == "event_stream_not_ready"

        async with client.stream(session_id) as events:
            accepted = await client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-live", "text": "hello"},
            )
            # Headers arrived ⇒ the subscriber is already registered — no
            # event_stream_not_ready race on the very first message.
            assert accepted.status_code == 202
            while (await events.receive_json())["type"] != "run.completed":
                pass


@pytest.mark.asyncio
async def test_stream_emits_comment_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sse, "_HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(sse, "_DISCONNECT_POLL_S", 0.01)
    application = _FakeApplication()
    app = _app(application)

    async with asgi_client(app) as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        async with client.stream(session_id) as events:

            async def _heartbeat() -> list[str]:
                while True:
                    lines = await events.receive_block_lines()
                    if any(line.startswith(":") for line in lines):
                        return lines

            lines = await asyncio.wait_for(_heartbeat(), timeout=5)
            assert lines == [": ping"]


@pytest.mark.asyncio
async def test_stream_ignores_last_event_id_without_replay() -> None:
    """P0-4 is live-only: Last-Event-ID/``?last_seq=`` are accepted (and
    logged) but do not change what the stream emits."""

    application = _FakeApplication()
    app = _app(application)

    async with asgi_client(app) as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        async with client.stream(
            session_id,
            query="last_seq=99",
            headers={"Last-Event-ID": "99"},
        ) as events:
            assert events.status == 200
            accepted = await client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "request-01", "text": "hello"},
            )
            assert accepted.status_code == 202
            # The first live frame still starts at seq 1 — nothing replayed.
            assert (await events.receive_frame_lines())[0] == "id: 1"
            assert events.last_id == "1"
