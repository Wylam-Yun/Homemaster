"""Single-owner adapter for thread-affine synchronous backends."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_STOP = object()


@dataclass(frozen=True)
class _WorkItem:
    future: concurrent.futures.Future[Any]
    function: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class ThreadOwnedSyncBackendAdapter:
    """Run construction, calls, and cleanup on one dedicated owner thread."""

    def __init__(self, *, name: str, close_timeout_s: float = 30.0) -> None:
        if not name.strip():
            raise ValueError("thread-owned adapter name must be non-empty")
        if close_timeout_s <= 0:
            raise ValueError("close timeout must be positive")
        self._close_timeout_s = close_timeout_s
        self._queue: queue.Queue[_WorkItem | object] = queue.Queue()
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._closed = False
        self._pending = 0
        self._active = 0
        self._owner_thread_id: int | None = None
        self._thread = threading.Thread(
            target=self._worker,
            name=f"homemaster-sync-{name}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=close_timeout_s):
            raise RuntimeError("thread-owned backend worker did not start")

    @property
    def owner_thread_id(self) -> int:
        owner = self._owner_thread_id
        if owner is None:
            raise RuntimeError("thread-owned backend worker is not ready")
        return owner

    @property
    def thread_name(self) -> str:
        return self._thread.name

    @property
    def pending_count(self) -> int:
        with self._lock:
            return self._pending

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def submit(
        self,
        function: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> concurrent.futures.Future[Any]:
        if not callable(function):
            raise TypeError("thread-owned backend work must be callable")
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("thread-owned backend adapter is closed")
            self._pending += 1
            self._queue.put(_WorkItem(future, function, args, dict(kwargs)))
        return future

    def call(
        self,
        function: Callable[..., Any],
        /,
        *args: Any,
        wait_timeout_s: float | None = None,
        **kwargs: Any,
    ) -> Any:
        if threading.get_ident() == self.owner_thread_id:
            return function(*args, **kwargs)
        future = self.submit(function, *args, **kwargs)
        return future.result(timeout=wait_timeout_s)

    async def run(
        self,
        function: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        future = self.submit(function, *args, **kwargs)
        return await asyncio.wrap_future(future)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(_STOP)
        if threading.get_ident() == self.owner_thread_id:
            return
        self._thread.join(timeout=self._close_timeout_s)
        if self._thread.is_alive():
            raise TimeoutError("thread-owned backend worker did not stop")

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)

    def _worker(self) -> None:
        self._owner_thread_id = threading.get_ident()
        self._ready.set()
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            assert isinstance(item, _WorkItem)
            future = item.future
            with self._lock:
                self._pending -= 1
            if not future.set_running_or_notify_cancel():
                continue
            with self._lock:
                self._active += 1
            try:
                value = item.function(*item.args, **item.kwargs)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(value)
            finally:
                with self._lock:
                    self._active -= 1


class ThreadOwnedObservationBackend:
    """Async observation facade over a synchronous thread-affine backend."""

    def __init__(self, backend: Any, adapter: ThreadOwnedSyncBackendAdapter) -> None:
        self._backend = backend
        self._adapter = adapter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    async def capture(self) -> Any:
        return await self._adapter.run(self._backend.capture)

    async def bind_application_run(self, run_id: str, generation: int) -> None:
        await self._adapter.run(
            self._backend.bind_application_run,
            run_id,
            generation,
        )


__all__ = ["ThreadOwnedObservationBackend", "ThreadOwnedSyncBackendAdapter"]
