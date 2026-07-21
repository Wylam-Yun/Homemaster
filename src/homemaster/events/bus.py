"""Application-owned bounded fanout for runtime and public stream events."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.stream_events import StreamEvent

_CLOSED = object()

PublicEventProjector = Callable[[RuntimeEvent], StreamEvent | None]


@dataclass(eq=False)
class _StreamChannel:
    queue: asyncio.Queue[RuntimeEvent | object]
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class EventBusClosedError(RuntimeError):
    """A producer was rejected or released because the bus is closing."""


class EventBus:
    """Thread-safe runtime sink with bounded async stream subscriptions.

    AgentRuntime remains on a worker thread until CL-16b, so ``emit`` is the
    explicit compatibility producer. Async producers use ``aemit`` and all
    stream delivery shares one close-aware backpressure path.
    """

    def __init__(
        self,
        *,
        capacity: int = 256,
        public_projector: PublicEventProjector | None = None,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("event bus capacity must be a positive integer")
        if public_projector is not None and not callable(public_projector):
            raise TypeError("public event projector must be callable or None")
        self._capacity = capacity
        self._public_projector = public_projector
        self._events: list[RuntimeEvent] = []
        self._subscribers: list[Callable[[RuntimeEvent], Any]] = []
        self._streams: set[_StreamChannel] = set()
        self._producer_tasks: set[asyncio.Task[None]] = set()
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed_signal: asyncio.Event | None = None
        self._closed = False

    @property
    def events(self) -> list[RuntimeEvent]:
        with self._lock:
            return list(self._events)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers) + len(self._streams)

    @property
    def pending_producer_count(self) -> int:
        with self._lock:
            return len(self._producer_tasks)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise EventBusClosedError("event bus is closed")
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("event bus is already owned by another event loop")
            if self._loop is None:
                self._loop = loop
                self._closed_signal = asyncio.Event()

    def subscribe(self, callback: Callable[[RuntimeEvent], Any]) -> Callable[[], None]:
        """Register a synchronous compatibility sink."""
        if not callable(callback):
            raise TypeError("event subscriber must be callable")
        with self._lock:
            if self._closed:
                raise EventBusClosedError("event bus is closed")
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, event: RuntimeEvent) -> None:
        """Produce from a non-owner thread, preserving bounded backpressure."""
        loop, streams = self._producer_snapshot(event)
        if streams and loop is None:
            raise RuntimeError("event bus must be started before async stream delivery")
        if loop is not None and _running_loop() is loop:
            raise RuntimeError("owner-loop event producers must await EventBus.aemit")
        self._record_and_notify(event)
        if streams:
            assert loop is not None
            future = asyncio.run_coroutine_threadsafe(
                self._enqueue(event, streams),
                loop,
            )
            future.result()

    async def aemit(self, event: RuntimeEvent) -> None:
        """Produce asynchronously with close-aware bounded backpressure."""
        loop, streams = self._producer_snapshot(event)
        if streams and loop is None:
            raise RuntimeError("event bus must be started before async stream delivery")
        self._record_and_notify(event)
        if not streams:
            return
        assert loop is not None
        if _running_loop() is loop:
            await self._enqueue(event, streams)
            return
        future = asyncio.run_coroutine_threadsafe(self._enqueue(event, streams), loop)
        await asyncio.wrap_future(future)

    def _producer_snapshot(
        self,
        event: RuntimeEvent,
    ) -> tuple[asyncio.AbstractEventLoop | None, tuple[_StreamChannel, ...]]:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("event bus accepts RuntimeEvent values")
        with self._lock:
            if self._closed:
                raise EventBusClosedError("event bus is closed")
            return self._loop, tuple(self._streams)

    def _record_and_notify(self, event: RuntimeEvent) -> None:
        with self._lock:
            if self._closed:
                raise EventBusClosedError("event bus is closed")
            self._events.append(event)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            value = callback(event)
            if inspect.isawaitable(value):
                close = getattr(value, "close", None)
                if callable(close):
                    close()
                raise TypeError("async event consumers must use EventBus.stream")
            if value is not None:
                raise TypeError("synchronous event subscribers must return None")

    async def _enqueue(
        self,
        event: RuntimeEvent,
        streams: tuple[_StreamChannel, ...],
    ) -> None:
        current = asyncio.current_task()
        assert current is not None
        with self._lock:
            signal = self._closed_signal
            if self._closed or signal is None:
                raise EventBusClosedError("event bus is closed")
            self._producer_tasks.add(current)
        try:
            for channel in streams:
                put_task = asyncio.create_task(channel.queue.put(event))
                close_task = asyncio.create_task(signal.wait())
                stream_close_task = asyncio.create_task(channel.closed.wait())
                done, _ = await asyncio.wait(
                    {put_task, close_task, stream_close_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if close_task in done or stream_close_task in done:
                    put_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await put_task
                    raise EventBusClosedError("event stream closed during publish")
                for task in (close_task, stream_close_task):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                await put_task
        finally:
            with self._lock:
                self._producer_tasks.discard(current)

    async def stream(self) -> AsyncIterator[RuntimeEvent]:
        """Yield private RuntimeEvents until the bus closes or the consumer exits."""
        await self.start()
        channel = _StreamChannel(asyncio.Queue(maxsize=self._capacity))
        with self._lock:
            if self._closed:
                raise EventBusClosedError("event bus is closed")
            self._streams.add(channel)
        try:
            while True:
                item = await channel.queue.get()
                if item is _CLOSED:
                    return
                assert isinstance(item, RuntimeEvent)
                yield item
        finally:
            channel.closed.set()
            with self._lock:
                self._streams.discard(channel)

    async def public_stream(self) -> AsyncIterator[StreamEvent]:
        """Project events only through an explicitly configured trust boundary."""
        projector = self._public_projector
        if projector is None:
            raise RuntimeError("public event stream requires an explicit projector")
        async for event in self.stream():
            try:
                projected = projector(event)
            except Exception:
                continue
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
        await self.aemit(event)

    async def aclose(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            signal = self._closed_signal
            streams = tuple(self._streams)
            producers = tuple(self._producer_tasks)
            self._streams.clear()
            self._subscribers.clear()
        if signal is not None:
            signal.set()
        current = asyncio.current_task()
        pending = tuple(task for task in producers if task is not current)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for channel in streams:
            channel.closed.set()
            while channel.queue.full():
                try:
                    channel.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            channel.queue.put_nowait(_CLOSED)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


__all__ = ["EventBus", "EventBusClosedError", "PublicEventProjector"]
