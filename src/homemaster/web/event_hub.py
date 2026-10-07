"""Single Runtime event consumer with session-scoped WebSocket fanout."""

from __future__ import annotations

import asyncio

from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.run_registry import RunCorrelationError, WebRunRegistry
from homemaster.web.schemas import WebEvent

_RUN_FINISHED_EVENT_TYPE = "web.run_finished"


class _DroppedSubscriber:
    """Queue sentinel telling ``_stream_events`` its subscription was dropped."""


class WebEventHub:
    """Correlate each Runtime event once and fan out projected Web events."""

    def __init__(
        self,
        event_bus: EventBus,
        run_registry: WebRunRegistry,
        projection: WebEventProjection,
        *,
        capacity: int = 256,
    ) -> None:
        self._event_bus = event_bus
        self._run_registry = run_registry
        self._projection = projection
        self._capacity = capacity
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[asyncio.Queue[object]]] = {}
        self._pump_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        """Start and confirm the private EventBus subscription before commands."""

        if self._pump_task is not None:
            return
        baseline = self._event_bus.subscriber_count
        self._pump_task = asyncio.create_task(self._pump(baseline))
        await asyncio.wait_for(self._ready.wait(), timeout=2)

    async def subscribe(self, session_id: str) -> asyncio.Queue[object]:
        """Register one bounded session subscriber."""

        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._capacity)
        async with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[object],
    ) -> None:
        """Remove one session subscriber without affecting other connections."""

        async with self._lock:
            self._drop_subscriber(session_id, queue)

    def _drop_subscriber(self, session_id: str, queue: asyncio.Queue[object]) -> None:
        subscribers = self._subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(session_id, None)

    async def has_subscriber(self, session_id: str) -> bool:
        async with self._lock:
            return bool(self._subscribers.get(session_id))

    async def publish(self, event: WebEvent) -> None:
        """Deliver one event to every current subscriber for its session.

        Delivery is non-blocking: a connected client that stopped reading must
        never stall the shared pump. A subscriber whose bounded queue is full
        is dropped and queued the sentinel so the WebSocket handler exits and
        the client can reconnect and resync.
        """

        async with self._lock:
            subscribers = tuple(self._subscribers.get(event.session_id, ()))
        stalled: list[asyncio.Queue[object]] = []
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stalled.append(queue)
        if not stalled:
            return
        async with self._lock:
            for queue in stalled:
                self._drop_subscriber(event.session_id, queue)
        for queue in stalled:
            _queue_drop_sentinel(queue)

    async def note_task_finished(
        self,
        session_id: str,
        request_id: str,
        task: asyncio.Task[object],
    ) -> None:
        """Emit the in-order finish marker for one completed owned run task.

        ``EventBus.aemit`` lands strictly after every event the task already
        produced, so the pump consumes this marker only once the run's real
        terminal — if any — was correlated. It is the deterministic signal
        that lets the hub release a session whose run ended without a
        terminal event (e.g. a fenced ``runtime.cancelled``).

        Falls back to direct terminalization when the bus can no longer
        accept the marker (e.g. during close): the pump is gone then, so the
        honest terminal is delivered straight to subscribers.
        """

        if task.cancelled():
            outcome = "cancelled"
        elif task.exception() is not None:
            outcome = "failed"
        else:
            outcome = (
                "cancelled"
                if getattr(task.result(), "status", None) == "cancelled"
                else "failed"
            )
        try:
            await self._event_bus.aemit(
                RuntimeEvent(
                    type=_RUN_FINISHED_EVENT_TYPE,
                    session_id=session_id,
                    run_id="",
                    turn_index=None,
                    payload={"request_id": request_id, "outcome": outcome},
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.finish_run(session_id, request_id, outcome)

    async def finish_run(
        self,
        session_id: str,
        request_id: str,
        outcome: str,
    ) -> None:
        """Publish the honest terminal for a finished run that never emitted one.

        ``release_finished`` only acts when the record's task is actually done
        and no real terminal already correlated, so this never double-emits
        and never terminalizes a still-running task.
        """

        run_id = await self._run_registry.release_finished(session_id, request_id)
        if run_id is None:
            return
        if outcome == "cancelled":
            projected = WebEvent(
                type="run.cancelled",
                session_id=session_id,
                run_id=run_id,
                request_id=request_id,
                payload={},
            )
        else:
            projected = WebEvent(
                type="run.failed",
                session_id=session_id,
                run_id=run_id,
                request_id=request_id,
                payload={
                    "code": "run_failed",
                    "message": "The run ended without a terminal event.",
                    "retryable": False,
                },
            )
        await self.publish(projected)

    async def _pump(self, baseline_subscribers: int) -> None:
        stream = self._event_bus.stream()
        pending = asyncio.create_task(anext(stream))
        try:
            while self._event_bus.subscriber_count <= baseline_subscribers:
                await asyncio.sleep(0)
            self._ready.set()
            while True:
                try:
                    event = await pending
                except StopAsyncIteration:
                    return
                pending = asyncio.create_task(anext(stream))
                if event.type == _RUN_FINISHED_EVENT_TYPE:
                    await self._finish_marker(event)
                    continue
                try:
                    request_id = await self._run_registry.correlate(event)
                except RunCorrelationError:
                    continue
                for projected in self._projection.project(event, request_id=request_id):
                    await self.publish(projected)
        finally:
            self._ready.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()

    async def _finish_marker(self, event: RuntimeEvent) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        request_id = payload.get("request_id")
        outcome = payload.get("outcome")
        if (
            not isinstance(request_id, str)
            or not request_id
            or not isinstance(outcome, str)
        ):
            return
        await self.finish_run(event.session_id, request_id, outcome)

    async def aclose(self) -> None:
        """Stop the private stream consumer and release subscribers."""

        task = self._pump_task
        self._pump_task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            self._subscribers.clear()


def _queue_drop_sentinel(queue: asyncio.Queue[object]) -> None:
    """Evict the stalled backlog and wake the consumer so the stream exits."""

    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(_DroppedSubscriber())


__all__ = ["WebEventHub"]
