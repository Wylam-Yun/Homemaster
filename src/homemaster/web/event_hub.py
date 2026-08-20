"""Single Runtime event consumer with session-scoped WebSocket fanout."""

from __future__ import annotations

import asyncio

from homemaster.events.bus import EventBus
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.run_registry import RunCorrelationError, WebRunRegistry
from homemaster.web.schemas import WebEvent


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
        self._subscribers: dict[str, set[asyncio.Queue[WebEvent]]] = {}
        self._pump_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        """Start and confirm the private EventBus subscription before commands."""

        if self._pump_task is not None:
            return
        baseline = self._event_bus.subscriber_count
        self._pump_task = asyncio.create_task(self._pump(baseline))
        await asyncio.wait_for(self._ready.wait(), timeout=2)

    async def subscribe(self, session_id: str) -> asyncio.Queue[WebEvent]:
        """Register one bounded session subscriber."""

        queue: asyncio.Queue[WebEvent] = asyncio.Queue(maxsize=self._capacity)
        async with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[WebEvent],
    ) -> None:
        """Remove one session subscriber without affecting other connections."""

        async with self._lock:
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
        """Deliver one event to every current subscriber for its session."""

        async with self._lock:
            subscribers = tuple(self._subscribers.get(event.session_id, ()))
        for queue in subscribers:
            await queue.put(event)

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


__all__ = ["WebEventHub"]
