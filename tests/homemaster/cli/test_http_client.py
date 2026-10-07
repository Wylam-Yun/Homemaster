"""Tests for the thin-client HomeServerClient (REST + WS)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from homemaster.cli.client import (
    RESYNC_EVENT_TYPE,
    ApiError,
    HomeServerClient,
    ServerUnavailableError,
    SessionBusyError,
    SessionNotFoundError,
    StreamClosedError,
)


def _ok(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _error(
    status: int, code: str, message: str = "boom", retryable: bool = False
) -> httpx.Response:
    return httpx.Response(
        status,
        json={"code": code, "message": message, "retryable": retryable},
    )


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> HomeServerClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="http://server.test",
        transport=transport,
    )
    return HomeServerClient("http://server.test", http_client=http, **kwargs)


class FakeSocket:
    """Async-iterable fake websocket honouring the client connect contract."""

    def __init__(self, frames: list[Any] | None = None, error: BaseException | None = None) -> None:
        self._frames = list(frames or [])
        self._error = error
        self.entered = False

    async def __aenter__(self) -> FakeSocket:
        if self._error is not None:
            raise self._error
        self.entered = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        await asyncio.sleep(0)
        if not self._frames:
            raise StopAsyncIteration
        frame = self._frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame


def _ws_event(**overrides: Any) -> str:
    event = {
        "type": "answer.delta",
        "session_id": "s1",
        "run_id": "r1",
        "request_id": "q1",
        "payload": {"text": "hi"},
    }
    event.update(overrides)
    return json.dumps(event)


@pytest.mark.asyncio
async def test_rest_surface_hits_expected_paths() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/api/meta":
            return _ok({"version": "1.0", "memory_mode": "files", "environment": "home"})
        if path == "/api/providers":
            return _ok(
                {
                    "providers": [
                        {
                            "name": "Mimo",
                            "kind": "chat",
                            "model": "m",
                            "api_key_configured": True,
                        }
                    ]
                }
            )
        if path == "/api/sessions" and request.method == "POST":
            return _ok({"session_id": "sess-9"}, status=201)
        if path == "/api/sessions" and request.method == "GET":
            return _ok({"sessions": [{"session_id": "s1"}]})
        if path.endswith("/history"):
            return _ok({"messages": [{"role": "assistant", "text": "hi"}]})
        if path.endswith("/status"):
            return _ok({"status": "idle", "ui_mode": "act"})
        if path.endswith("/compact"):
            return _ok({"triggered": True, "kind": "manual_summary"})
        if path.endswith("/mode"):
            return _ok({"ui_mode": "plan"})
        if path.endswith("/approvals") and request.method == "GET":
            return _ok({"approvals": [{"approval_id": "a1"}]})
        if path.endswith("/questions") and request.method == "GET":
            return _ok({"questions": [{"question_id": "q1"}]})
        if path.endswith("/answer"):
            return _ok({"answered": True})
        if path == "/api/skills/resolve":
            return _ok({"kind": "plain"})
        return _error(404, "not_found")

    client = _client(handler)
    try:
        assert (await client.meta())["version"] == "1.0"
        assert (await client.providers())[0]["name"] == "Mimo"
        assert await client.create_session() == "sess-9"
        assert (await client.list_sessions())[0]["session_id"] == "s1"
        assert (await client.history("s1"))[0]["text"] == "hi"
        assert (await client.status("s1"))["ui_mode"] == "act"
        assert (await client.compact("s1"))["triggered"] is True
        assert (await client.set_mode("plan", "s1"))["ui_mode"] == "plan"
        assert (await client.list_approvals("s1"))[0]["approval_id"] == "a1"
        assert (await client.list_questions("s1"))[0]["question_id"] == "q1"
        assert (await client.answer_question("q1", "yes", "s1"))["answered"] is True
        assert (await client.resolve_skill("/x"))["kind"] == "plain"
    finally:
        await client.aclose()
    paths = [path for _method, path in calls]
    assert "/api/sessions/s1/status" in paths
    assert "/api/sessions/s1/compact" in paths
    assert "/api/sessions/s1/questions/q1/answer" in paths


@pytest.mark.asyncio
async def test_send_subscribes_stream_before_posting_message() -> None:
    order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        order.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/sessions":
            return _ok({"session_id": "s1"}, status=201)
        if request.url.path.endswith("/messages"):
            return _ok({"accepted": True}, status=202)
        return _error(404, "not_found")

    def ws_connect(url: str) -> FakeSocket:
        order.append("WS /api/events")
        return FakeSocket([_ws_event()])

    client = _client(handler, ws_connect=ws_connect)
    try:
        await client.send("hello", session_id="s1")
        assert order.index("WS /api/events") < order.index("POST /api/sessions/s1/messages")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_send_retries_event_stream_not_ready_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/messages"):
            attempts += 1
            if attempts < 3:
                return _error(409, "event_stream_not_ready", retryable=True)
            return _ok({"accepted": True}, status=202)
        return _error(404, "not_found")

    client = _client(handler, ws_connect=lambda url: FakeSocket([_ws_event()]))
    try:
        result = await client.send("hello", session_id="s1")
        assert result["accepted"] is True
        assert attempts == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_api_error_typing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages"):
            return _error(409, "session_busy", "busy")
        if "/sessions/" in path:
            return _error(404, "session_not_found", "gone")
        return _error(500, "internal", "broken")

    client = _client(handler, ws_connect=lambda url: FakeSocket())
    try:
        with pytest.raises(SessionBusyError):
            await client.send("hi", session_id="s1")
        with pytest.raises(SessionNotFoundError):
            await client.history("missing")
        with pytest.raises(ApiError) as exc_info:
            await client.meta()
        assert exc_info.value.code == "internal"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unreachable_server_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client(handler)
    try:
        with pytest.raises(ServerUnavailableError):
            await client.meta()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_events_reconnects_and_marks_resync() -> None:
    sockets = iter(
        [
            FakeSocket(
                [
                    _ws_event(payload={"text": "old"}),
                    ConnectionError("drop"),
                ]
            ),
            FakeSocket([_ws_event(payload={"text": "new"})]),
        ]
    )

    def ws_connect(url: str) -> FakeSocket:
        return next(sockets)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/history"):
            return _ok({"messages": [{"role": "assistant", "text": "hi"}]})
        if path.endswith("/approvals"):
            return _ok({"approvals": [{"approval_id": "a1"}]})
        if path.endswith("/questions"):
            return _ok({"questions": [{"question_id": "q1", "question": "proceed?"}]})
        return _error(404, "not_found")

    client = _client(
        handler,
        ws_connect=ws_connect,
        backoff_base_s=0.001,
        backoff_max_s=0.005,
        jitter=lambda: 0.5,
    )
    try:
        await client.subscribe("s1")
        seen: list[dict[str, Any]] = []
        async for event in client.events():
            seen.append(event)
            if event.get("type") == RESYNC_EVENT_TYPE:
                break
        assert client.generation == 2
        assert seen[0]["payload"]["text"] == "old"
        resync = seen[-1]
        assert resync["payload"]["resync"] is True
        assert resync["payload"]["history"][0]["text"] == "hi"
        assert resync["payload"]["approvals"][0]["approval_id"] == "a1"
        # Pending questions ride the resync payload so consumers can re-prompt.
        assert resync["payload"]["questions"][0]["question_id"] == "q1"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unknown_session_close_fails_subscribe_fast() -> None:
    class Reject(Exception):
        response = type("R", (), {"status_code": 404})()

    client = _client(
        lambda request: _error(404, "not_found"),
        ws_connect=lambda url: FakeSocket(error=Reject("no session")),
        backoff_base_s=0.001,
    )
    try:
        with pytest.raises(StreamClosedError):
            await client.subscribe("ghost")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_events_iterator_ends_when_stream_stops() -> None:
    client = _client(
        lambda request: _error(404, "not_found"),
        ws_connect=lambda url: FakeSocket([_ws_event()]),
        backoff_base_s=0.01,
    )
    try:
        await client.subscribe("s1")
        items = []
        # Socket exhausts its single frame; the loop will attempt reconnects
        # forever, so close once the first event landed.
        async for event in client.events():
            items.append(event)
            if len(items) == 1:
                await client.aclose()
        assert items[0]["payload"]["text"] == "hi"
    finally:
        await client.aclose()
