"""Application-owned bounded fanout for runtime and public stream events."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.stream_events import StreamEvent, project_stream_event

_CLOSED = object()


class EventBus:
    """Thread-safe runtime sink with bounded async stream subscriptions.

    AgentRuntime remains on a worker thread until CL-16b, so ``emit`` is the
    explicit compatibility producer. Async consumers use ``stream`` or
    ``public_stream`` and are backpressured by a bounded per-consumer queue.
    """

    def __init__(self, *, capacity: int = 256) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("event bus capacity must be a positive integer")
        self._capacity = capacity
        self._events: list[RuntimeEvent] = []
        self._subscribers: list[Callable[[RuntimeEvent], Any]] = []
        self._streams: set[asyncio.Queue[RuntimeEvent | object]] = set()
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    @property
    def events(self) -> list[RuntimeEvent]:
        with self._lock:
            return list(self._events)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers) + len(self._streams)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise RuntimeError("event bus is closed")
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("event bus is already owned by another event loop")
            self._loop = loop

    def subscribe(self, callback: Callable[[RuntimeEvent], Any]) -> Callable[[], None]:
        """Register a synchronous compatibility sink."""
        if not callable(callback):
            raise TypeError("event subscriber must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("event bus is closed")
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, event: RuntimeEvent) -> None:
        """Persist locally, deliver compatibility sinks, then enqueue streams."""
        if not isinstance(event, RuntimeEvent):
            raise TypeError("event bus accepts RuntimeEvent values")
        with self._lock:
            if self._closed:
                raise RuntimeError("event bus is closed")
            self._events.append(event)
            subscribers = tuple(self._subscribers)
            streams = tuple(self._streams)
            loop = self._loop
        for callback in subscribers:
            value = callback(event)
            if inspect.isawaitable(value):
                close = getattr(value, "close", None)
                if callable(close):
                    close()
                raise TypeError("async event consumers must use EventBus.stream")
            if value is not None:
                raise TypeError("synchronous event subscribers must return None")
        if not streams:
            return
        if loop is None:
            raise RuntimeError("event bus must be started before async stream delivery")
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            for queue in streams:
                queue.put_nowait(event)
            return
        for queue in streams:
            asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()

    async def stream(self) -> AsyncIterator[RuntimeEvent]:
        """Yield private RuntimeEvents until the bus closes or the consumer exits."""
        await self.start()
        queue: asyncio.Queue[RuntimeEvent | object] = asyncio.Queue(maxsize=self._capacity)
        with self._lock:
            if self._closed:
                raise RuntimeError("event bus is closed")
            self._streams.add(queue)
        try:
            while True:
                item = await queue.get()
                if item is _CLOSED:
                    return
                assert isinstance(item, RuntimeEvent)
                yield item
        finally:
            with self._lock:
                self._streams.discard(queue)

    async def public_stream(self) -> AsyncIterator[StreamEvent]:
        """Project private RuntimeEvents through the public stream allowlist."""
        async for event in self.stream():
            projected = project_stream_event(event)
            if projected is not None:
                yield projected

    async def publish(self, tool_call, result, context, attempt_index: int) -> None:
        event = RuntimeEvent(
            type="tool.execution_published",
            session_id=context.session_id,
            run_id=context.run_id,
            turn_index=context.turn_index,
            tool_call_id=tool_call.id,
            name=tool_call.name,
            payload={
                "attempt_index": attempt_index,
                "result": result.to_dict(),
                "tool_view_id": context.tool_view.view_id,
            },
        )
        await asyncio.to_thread(self.emit, event)

    async def aclose(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            streams = tuple(self._streams)
            self._streams.clear()
            self._subscribers.clear()
        for queue in streams:
            while queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queue.put_nowait(_CLOSED)


__all__ = ["EventBus"]
