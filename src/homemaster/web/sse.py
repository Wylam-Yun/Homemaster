"""Session event stream over Server-Sent Events (SSE).

``GET /api/sessions/{session_id}/stream`` is the single live event channel —
it replaces the retired ``/api/events`` WebSocket for every consumer (browser
``EventSource``, the CLI thin client, tests). The semantics are unchanged:
live-only frames, reconnect = hydrate via the REST read APIs.

Bare ``StreamingResponse`` is used instead of ``sse-starlette`` deliberately:
the wire protocol here is two frame shapes (``id:``/``data:`` events and a
``:`` comment heartbeat), so a dedicated library buys nothing while adding a
dependency; hand-rolling also keeps the subscribe-before-headers ordering —
the ``POST /messages`` ``has_subscriber`` gate depends on it — explicit in
this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from homemaster.web.event_hub import WebEventHub
from homemaster.web.schemas import WebEvent

logger = logging.getLogger(__name__)

# Proxy/browser idle timeouts commonly land near 30-60s; a comment heartbeat
# every ~15s keeps the connection warm and doubles as the disconnect probe.
_HEARTBEAT_INTERVAL_S = 15.0
# ``request.is_disconnected()`` poll cadence — the WS handler observed
# disconnects immediately; a 1s poll keeps SSE close to that latency.
_DISCONNECT_POLL_S = 1.0

_STREAM_HEADERS = {
    # Intermediaries must never buffer or cache the live stream.
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def mount_sse_stream(
    app: FastAPI,
    *,
    hub: WebEventHub,
    session_ids: Callable[[], set[str]],
    error: Callable[..., Response],
) -> None:
    """Register ``GET /api/sessions/{session_id}/stream`` on ``app``.

    ``session_ids``/``error`` are injected so this module stays independent of
    ``app.py`` (which owns both helpers) while sharing the exact session-set
    semantics and the ``{code, message, retryable}`` error envelope.
    """

    @app.get("/api/sessions/{session_id}/stream")
    async def stream_session_events(session_id: str, request: Request) -> Response:
        if session_id not in session_ids():
            # SSE has no close codes — an unknown session must fail as a plain
            # HTTP 404 before the stream starts so clients can map it onto the
            # "session is gone" branch (WS close code 4404's old role).
            return error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        # Register BEFORE returning the response: response headers flush the
        # moment StreamingResponse starts, and the POST /messages
        # ``has_subscriber`` gate must already see this subscriber by then.
        queue = await hub.subscribe(session_id)
        # Live-only phase (P0-4): Last-Event-ID is accepted and logged but not
        # honoured — replay belongs to P1's event_store.
        # TODO(P1): parse ``?last_seq=``/Last-Event-ID here and replay missed
        # frames once the event store lands.
        last_event_id = request.headers.get("last-event-id") or request.query_params.get("last_seq")
        if last_event_id is not None:
            logger.info(
                "sse_last_event_id_ignored",
                extra={"session_id": session_id, "last_event_id": last_event_id},
            )
        return StreamingResponse(
            _disconnect_aware_stream(request, hub, session_id, queue),
            media_type="text/event-stream",
            headers=_STREAM_HEADERS,
        )


async def _disconnect_aware_stream(
    request: Request,
    hub: WebEventHub,
    session_id: str,
    queue: asyncio.Queue[object],
) -> AsyncIterator[str]:
    """Wrap the frame stream so the subscriber leaves the hub exactly once."""

    try:
        async for frame in _sse_frames(request, queue):
            yield frame
    finally:
        try:
            # Shield: a cancellation landing while unsubscribe awaits the hub
            # lock would otherwise abandon the removal mid-flight, leaving a
            # dead subscriber that keeps has_subscriber true until queue-full.
            await asyncio.shield(hub.unsubscribe(session_id, queue))
        except asyncio.CancelledError:
            # Hosting-scope teardown (test-client shutdown, ASGI close) can
            # cancel the handler right as the generator finalizes; the
            # unsubscribe still matters, so swallow the cancellation like the
            # retired WS handler did — pending approvals are durable state.
            pass


async def _sse_frames(
    request: Request,
    queue: asyncio.Queue[object],
    *,
    heartbeat_interval_s: float | None = None,
    disconnect_poll_s: float | None = None,
) -> AsyncIterator[str]:
    """Forward hub events as SSE frames while watching for client disconnect.

    Mirrors the retired ``_stream_events``: one FIRST_COMPLETED wait over the
    subscriber queue plus a poll timer. The timer drives both the heartbeat
    (``: ping`` every ``_HEARTBEAT_INTERVAL_S``) and a per-tick
    ``request.is_disconnected()`` probe — the SSE counterpart of the WS
    ``receive()`` disconnect watch.
    """

    heartbeat = _HEARTBEAT_INTERVAL_S if heartbeat_interval_s is None else heartbeat_interval_s
    poll = _DISCONNECT_POLL_S if disconnect_poll_s is None else disconnect_poll_s
    ticks_per_heartbeat = max(1, round(heartbeat / poll))

    event_task = asyncio.create_task(queue.get())
    poll_task = asyncio.create_task(asyncio.sleep(poll))
    ticks = 0
    try:
        while True:
            done, _pending = await asyncio.wait(
                (event_task, poll_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if poll_task in done:
                if await request.is_disconnected():
                    return
                ticks += 1
                if ticks >= ticks_per_heartbeat:
                    yield ": ping\n\n"
                    ticks = 0
                poll_task = asyncio.create_task(asyncio.sleep(poll))
            if event_task in done:
                event = event_task.result()
                if not isinstance(event, WebEvent):
                    # The hub dropped this subscriber after its bounded queue
                    # filled; ending the stream lets the client reconnect and
                    # resync — same recovery contract as the WS close.
                    return
                yield _format_frame(event)
                event_task = asyncio.create_task(queue.get())
    finally:
        for task in (event_task, poll_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(event_task, poll_task, return_exceptions=True)


def _format_frame(event: WebEvent) -> str:
    """One SSE frame: ``id:`` carries the envelope seq (Last-Event-ID feed)."""

    # Identical dumps settings to the retired ``websocket.send_json`` so the
    # payload bytes match the old WS frames exactly.
    data = json.dumps(event.to_dict(), separators=(",", ":"), ensure_ascii=False)
    return f"id: {event.seq}\ndata: {data}\n\n"


__all__ = ["mount_sse_stream"]
