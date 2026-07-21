"""Application-owned fanout for runtime and canonical tool events."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent


class EventBus:
    """Thread-safe application event sink with explicit subscriptions."""

    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []
        self._subscribers: list[Callable[[RuntimeEvent], Any]] = []
        self._lock = threading.RLock()

    @property
    def events(self) -> list[RuntimeEvent]:
        with self._lock:
            return list(self._events)

    def subscribe(self, callback: Callable[[RuntimeEvent], Any]) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("event subscriber must be callable")
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def emit(self, event: RuntimeEvent) -> None:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("event bus accepts RuntimeEvent values")
        with self._lock:
            self._events.append(event)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            value = callback(event)
            if inspect.isawaitable(value):
                raise TypeError("emit subscribers must be synchronous")

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
        self.emit(event)


__all__ = ["EventBus"]
