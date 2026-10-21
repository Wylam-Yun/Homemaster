"""Async in-process client for testing ``GET /api/sessions/{id}/stream``.

Why not TestClient: starlette's ``_TestClientTransport`` (and likewise
``httpx.ASGITransport``) buffers the response body whole — an infinite SSE
stream never completes, so ``client.stream()`` hangs before headers return.
These helpers instead drive the ASGI app directly on the test's own event
loop: ``AsgiClient`` runs the lifespan, serves REST through
``httpx.ASGITransport`` (finite bodies — buffering is fine), and opens the
event stream by invoking ``app(scope, receive, send)`` as a task whose body
chunks land on an ``asyncio.Queue`` incrementally. Everything lives on one
loop, so ``hub.publish`` / confirmation waiters / plan-mode broadcasts stay
on the same loop as the app — no cross-loop queue wakeups needed.

Ordering: ``stream(...)`` yields once ``http.response.start`` arrived, and
the endpoint registers the hub subscriber *before* sending that message —
so a successfully opened stream is a valid precondition for POST /messages.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI


class SseEventReader:
    """Incrementally decodes SSE frames from streamed ASGI body chunks."""

    def __init__(self) -> None:
        self._buffer = ""
        # Most recent ``id:`` field — mirrors the envelope seq on the wire.
        self.last_id: str | None = None

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self._buffer += chunk.decode("utf-8")
        events: list[dict[str, Any]] = []
        while "\n\n" in self._buffer:
            block, self._buffer = self._buffer.split("\n\n", 1)
            data_parts: list[str] = []
            for line in block.split("\n"):
                if not line or line.startswith(":"):
                    continue  # blank fragment edges and heartbeat comments
                field, sep, value = line.partition(":")
                if sep and value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    data_parts.append(value)
                elif field == "id":
                    self.last_id = value
            if data_parts:
                events.append(json.loads("\n".join(data_parts)))
        return events


class AsgiSseStream:
    """One live SSE subscription driven through the ASGI interface itself."""

    def __init__(
        self,
        app: FastAPI,
        session_id: str,
        query: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        path = f"/api/sessions/{session_id}/stream"
        self._app = app
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader = SseEventReader()
        self._text_buffer = ""
        self._task: asyncio.Task[None] | None = None
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        raw_headers = [(b"accept", b"text/event-stream")]
        raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        self._scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "headers": raw_headers,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "root_path": "",
        }

    async def _receive(self) -> dict[str, Any]:
        return await self._inbox.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._outbox.put(message)

    async def open(self) -> AsgiSseStream:
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        # The endpoint awaits ``receive`` once for the request body — seed it
        # with the empty GET body; later receives wait for the disconnect.
        self._inbox.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        while True:
            message = await self._outbox.get()
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
                return self
            raise AssertionError(f"unexpected ASGI message before start: {message}")

    async def _next_block(self, timeout_s: float) -> str:
        """Return the next raw ``\\n\\n``-terminated SSE block as text."""

        while "\n\n" not in self._text_buffer:
            if self._task is not None and self._task.done() and self._outbox.empty():
                exc = self._task.exception()
                raise AssertionError(f"SSE stream ended before a complete frame: {exc!r}")
            try:
                message = await asyncio.wait_for(self._outbox.get(), timeout_s)
            except TimeoutError:
                exc = self._task.exception() if self._task and self._task.done() else None
                raise AssertionError(
                    f"timed out waiting for an SSE frame (app task: {exc!r})"
                ) from None
            if message["type"] != "http.response.body":
                raise AssertionError(f"unexpected ASGI message mid-stream: {message}")
            self._text_buffer += message.get("body", b"").decode("utf-8")
            if not message.get("more_body", False):
                # Final body chunk — the stream ended; loop relies on the
                # ``_task.done`` check above on the next pass.
                continue
        block, self._text_buffer = self._text_buffer.split("\n\n", 1)
        return block

    async def receive_block_lines(self, timeout_s: float = 10.0) -> list[str]:
        """Raw lines of the next SSE block — including ``:`` heartbeats."""

        return (await self._next_block(timeout_s)).split("\n")

    async def receive_frame_lines(self, timeout_s: float = 10.0) -> list[str]:
        """Raw lines of the next block carrying ``data:`` (skips heartbeats)."""

        while True:
            lines = await self.receive_block_lines(timeout_s)
            for line in lines:
                if line.startswith("id:"):
                    self._reader.last_id = line.partition(":")[2].lstrip(" ")
            if any(line.startswith("data:") for line in lines):
                return lines

    async def receive_json(self, timeout_s: float = 10.0) -> dict[str, Any]:
        """Block until one event frame; return its decoded ``data:`` payload.

        Mirrors the retired ``websocket.receive_json()`` ergonomics; the
        ``id:`` field updates ``last_id`` exactly like ``event.lastEventId``.
        """

        data_parts: list[str] = []
        for line in await self.receive_frame_lines(timeout_s):
            field, sep, value = line.partition(":")
            if sep and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_parts.append(value)
            elif field == "id":
                self._reader.last_id = value
        return json.loads("\n".join(data_parts))

    @property
    def last_id(self) -> str | None:
        return self._reader.last_id

    async def aclose(self) -> None:
        if self._task is None or self._task.done():
            return
        self._inbox.put_nowait({"type": "http.disconnect"})
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


class AsgiClient:
    """Lifespan + REST (httpx ASGI) + live SSE stream on one event loop."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self._lifespan: Any = None
        self._streams: list[AsgiSseStream] = []

    async def __aenter__(self) -> AsgiClient:
        self._lifespan = self._app.router.lifespan_context(self._app)
        await self._lifespan.__aenter__()
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        for stream in tuple(self._streams):
            await stream.aclose()
        await self._http.__aexit__(*exc_info)
        if self._lifespan is not None:
            await self._lifespan.__aexit__(*exc_info)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._http.request(method, path, **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._http.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._http.post(path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._http.delete(path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self._http.patch(path, **kwargs)

    @asynccontextmanager
    async def stream(
        self,
        session_id: str,
        query: str = "",
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[AsgiSseStream]:
        stream = AsgiSseStream(self._app, session_id, query, headers=headers)
        self._streams.append(stream)
        try:
            yield await stream.open()
        finally:
            await stream.aclose()
            self._streams.remove(stream)


def asgi_client(app: FastAPI) -> AsgiClient:
    return AsgiClient(app)


__all__ = ["AsgiClient", "AsgiSseStream", "SseEventReader", "asgi_client"]
