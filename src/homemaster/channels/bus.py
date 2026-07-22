"""Bounded priority channel bus adapted from OpenHarness MessageBus.

OpenHarness/nanobot uses two unbounded asyncio queues. HomeMaster preserves the
inbound/outbound split while adding quotas, progress coalescing, critical-event
retention, producer backpressure, and deadline-aware draining.
"""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from collections.abc import Callable
from typing import Generic, TypeVar

from homemaster.channels.contracts import ChannelEventKind, InboundMessage, OutboundMessage

T = TypeVar("T", InboundMessage, OutboundMessage)


class BusClosedError(RuntimeError):
    pass


class _BoundedQueue(Generic[T]):
    def __init__(
        self,
        capacity: int,
        per_tenant_capacity: int,
        per_session_capacity: int,
        *,
        is_progress: Callable[[T], bool],
        coalesce_key: Callable[[T], object | None],
    ) -> None:
        self.capacity = capacity
        self.per_tenant_capacity = per_tenant_capacity
        self.per_session_capacity = per_session_capacity
        self._is_progress = is_progress
        self._coalesce_key = coalesce_key
        self._items: deque[T] = deque()
        self._counts: Counter[tuple[str, str]] = Counter()
        self._tenant_counts: Counter[str] = Counter()
        self._condition = asyncio.Condition()
        self._accepting = True
        self._closed = False

    @property
    def size(self) -> int:
        return len(self._items)

    async def publish(self, item: T) -> bool:
        async with self._condition:
            if not self._accepting:
                raise BusClosedError("channel bus is closing")
            coalesce = self._coalesce_key(item)
            if coalesce is not None:
                for index, queued in enumerate(self._items):
                    if self._coalesce_key(queued) == coalesce:
                        self._items[index] = item
                        return True

            while not self._has_capacity(item):
                if self._evict_progress(item):
                    continue
                if self._is_progress(item):
                    return False
                await self._condition.wait()
                if not self._accepting:
                    raise BusClosedError("channel bus is closing")
            self._items.append(item)
            self._counts[item.queue_key] += 1
            self._tenant_counts[item.queue_key[0]] += 1
            self._condition.notify_all()
            return True

    async def receive(self) -> T:
        async with self._condition:
            while not self._items:
                if self._closed or not self._accepting:
                    raise BusClosedError("channel bus is closed")
                await self._condition.wait()
            item = self._items.popleft()
            self._counts[item.queue_key] -= 1
            self._tenant_counts[item.queue_key[0]] -= 1
            if self._counts[item.queue_key] <= 0:
                del self._counts[item.queue_key]
            if self._tenant_counts[item.queue_key[0]] <= 0:
                del self._tenant_counts[item.queue_key[0]]
            self._condition.notify_all()
            return item

    async def begin_close(self) -> None:
        async with self._condition:
            self._accepting = False
            self._condition.notify_all()

    async def wait_empty(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: not self._items)

    async def finish_close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _has_capacity(self, item: T) -> bool:
        return (
            len(self._items) < self.capacity
            and self._tenant_counts[item.queue_key[0]] < self.per_tenant_capacity
            and self._counts[item.queue_key] < self.per_session_capacity
        )

    def _evict_progress(self, incoming: T) -> bool:
        quota_full = self._counts[incoming.queue_key] >= self.per_session_capacity
        tenant_full = self._tenant_counts[incoming.queue_key[0]] >= self.per_tenant_capacity
        for index, queued in enumerate(self._items):
            if not self._is_progress(queued):
                continue
            if quota_full and queued.queue_key != incoming.queue_key:
                continue
            if tenant_full and queued.queue_key[0] != incoming.queue_key[0]:
                continue
            del self._items[index]
            self._counts[queued.queue_key] -= 1
            self._tenant_counts[queued.queue_key[0]] -= 1
            if self._counts[queued.queue_key] <= 0:
                del self._counts[queued.queue_key]
            if self._tenant_counts[queued.queue_key[0]] <= 0:
                del self._tenant_counts[queued.queue_key[0]]
            return True
        return False


class BoundedPriorityBus:
    def __init__(
        self,
        *,
        capacity: int = 128,
        per_tenant_capacity: int = 64,
        per_session_capacity: int = 32,
    ) -> None:
        if capacity < 1 or per_tenant_capacity < 1 or per_session_capacity < 1:
            raise ValueError("bus capacities must be positive")
        if per_tenant_capacity > capacity:
            per_tenant_capacity = capacity
        if per_session_capacity > capacity:
            per_session_capacity = capacity
        if per_session_capacity > per_tenant_capacity:
            per_session_capacity = per_tenant_capacity
        self._inbound = _BoundedQueue[InboundMessage](
            capacity,
            per_tenant_capacity,
            per_session_capacity,
            is_progress=lambda _item: False,
            coalesce_key=lambda _item: None,
        )
        self._outbound = _BoundedQueue[OutboundMessage](
            capacity,
            per_tenant_capacity,
            per_session_capacity,
            is_progress=lambda item: item.kind is ChannelEventKind.PROGRESS,
            coalesce_key=lambda item: (
                item.coalesce_key if item.kind is ChannelEventKind.PROGRESS else None
            ),
        )
        self._close_lock = asyncio.Lock()
        self._closed = False

    @property
    def inbound_size(self) -> int:
        return self._inbound.size

    @property
    def outbound_size(self) -> int:
        return self._outbound.size

    async def publish_inbound(self, message: InboundMessage) -> bool:
        return await self._inbound.publish(message)

    async def receive_inbound(self) -> InboundMessage:
        return await self._inbound.receive()

    async def publish_outbound(self, message: OutboundMessage) -> bool:
        return await self._outbound.publish(message)

    async def receive_outbound(self) -> OutboundMessage:
        return await self._outbound.receive()

    async def aclose(self, *, deadline_s: float = 5.0) -> bool:
        if deadline_s <= 0:
            raise ValueError("deadline_s must be positive")
        async with self._close_lock:
            if self._closed:
                return True
            await self._inbound.begin_close()
            await self._outbound.begin_close()
            try:
                async with asyncio.timeout(deadline_s):
                    await asyncio.gather(
                        self._inbound.wait_empty(),
                        self._outbound.wait_empty(),
                    )
            except TimeoutError:
                return False
            await asyncio.gather(self._inbound.finish_close(), self._outbound.finish_close())
            self._closed = True
            return True


__all__ = ["BoundedPriorityBus", "BusClosedError"]
