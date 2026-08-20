"""Own asynchronous Web requests and authoritative Runtime run correlation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homemaster.events.runtime_events import RuntimeEvent

_TERMINAL_EVENT_TYPES = frozenset(
    {
        "runtime.turn_completed",
        "runtime.turn_failed",
        "runtime.budget_exhausted",
        "runtime.cancelled",
    }
)

RunFactory = Callable[[], Awaitable[object]]


class SessionBusyError(RuntimeError):
    """The session already owns another active Web request."""


class RunCorrelationError(RuntimeError):
    """A Runtime event conflicts with immutable Web request ownership."""


@dataclass(frozen=True)
class RequestAcceptance:
    """Result returned when the adapter accepts or deduplicates a request."""

    session_id: str
    request_id: str
    created: bool


@dataclass
class _OwnedRequest:
    session_id: str
    request_id: str
    task: asyncio.Task[object]
    run_id: str | None = None


class WebRunRegistry:
    """Application-owned request task and Runtime run registry."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[tuple[str, str], _OwnedRequest] = {}
        self._active_by_session: dict[str, _OwnedRequest] = {}
        self._started_by_run: dict[str, _OwnedRequest] = {}
        self._closed = False

    @property
    def owned_task_count(self) -> int:
        return sum(not record.task.done() for record in self._records.values())

    async def is_accepted(self, session_id: str, request_id: str) -> bool:
        """Return whether this session/request pair was already accepted."""

        async with self._lock:
            return (session_id, request_id) in self._records

    async def accept(
        self,
        session_id: str,
        request_id: str,
        run_factory: RunFactory,
    ) -> RequestAcceptance:
        """Atomically reserve a session and start exactly one owned task."""

        _require_id(session_id, "session_id")
        _require_id(request_id, "request_id")
        if not callable(run_factory):
            raise TypeError("run_factory must be callable")
        key = (session_id, request_id)
        async with self._lock:
            if self._closed:
                raise RuntimeError("Web run registry is closed")
            if key in self._records:
                return RequestAcceptance(session_id, request_id, created=False)
            if session_id in self._active_by_session:
                raise SessionBusyError(f"session already has an active request: {session_id}")
            task = asyncio.create_task(run_factory())
            record = _OwnedRequest(session_id, request_id, task)
            self._records[key] = record
            self._active_by_session[session_id] = record
            return RequestAcceptance(session_id, request_id, created=True)

    async def correlate(self, event: RuntimeEvent) -> str:
        """Resolve one Runtime event to its immutable browser request id."""

        if not isinstance(event, RuntimeEvent):
            raise TypeError("event must be a RuntimeEvent")
        async with self._lock:
            if event.type == "runtime.turn_started":
                record = self._bind_started(event)
            else:
                record = self._started_by_run.get(event.run_id)
                if record is None:
                    raise RunCorrelationError(f"run has no Web request owner: {event.run_id}")
                if record.session_id != event.session_id:
                    raise RunCorrelationError("Runtime event session conflicts with run ownership")
            if event.type in _TERMINAL_EVENT_TYPES:
                self._active_by_session.pop(record.session_id, None)
                assert record.run_id is not None
                self._started_by_run.pop(record.run_id, None)
            return record.request_id

    async def fail_before_start(self, session_id: str, request_id: str) -> bool:
        """Release a matching request only when no authoritative run has started."""

        async with self._lock:
            record = self._active_by_session.get(session_id)
            if (
                record is None
                or record.request_id != request_id
                or record.run_id is not None
            ):
                return False
            self._active_by_session.pop(session_id, None)
            return True

    def _bind_started(self, event: RuntimeEvent) -> _OwnedRequest:
        if not event.run_id:
            raise RunCorrelationError("runtime.turn_started requires a run_id")
        record = self._active_by_session.get(event.session_id)
        if record is None:
            raise RunCorrelationError(
                f"session has no pending Web request: {event.session_id}"
            )
        existing = self._started_by_run.get(event.run_id)
        if existing is not None and existing is not record:
            raise RunCorrelationError("run_id is already owned by another request")
        if record.run_id is not None and record.run_id != event.run_id:
            raise RunCorrelationError("session request is already bound to another run")
        record.run_id = event.run_id
        self._started_by_run[event.run_id] = record
        return record

    async def aclose(self) -> None:
        """Cancel and join every task still owned by the Web process."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(record.task for record in self._records.values())
            self._active_by_session.clear()
            self._started_by_run.clear()
            self._records.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


__all__ = [
    "RequestAcceptance",
    "RunCorrelationError",
    "SessionBusyError",
    "WebRunRegistry",
]
