from __future__ import annotations

import asyncio

import pytest

from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    project_stream_event,
)


def _event(event_type: str, **values) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        session_id="session-a",
        run_id="run-a",
        turn_index=0,
        payload=values.pop("payload", {}),
        **values,
    )


def test_openharness_consumer_branches_map_from_allowlisted_runtime_events() -> None:
    events = [
        _event("assistant.reply", payload={"reply": "done"}),
        _event(
            "tool.call_started",
            name="observe",
            payload={"arguments": {"api_key": "secret", "safe": "value"}},
        ),
        _event(
            "tool.call_completed",
            name="observe",
            payload={"result": "visible", "data": {"state": "ready"}},
        ),
        _event("runtime.turn_completed", payload={"final_reply": "done", "usage": {"x": 1}}),
        _event("runtime.turn_failed", payload={"error_code": "failed"}),
        _event("context.compaction", payload={"message": "compacting"}),
    ]

    projected = [project_stream_event(event) for event in events]

    assert isinstance(projected[0], AssistantTextDelta)
    assert isinstance(projected[1], ToolExecutionStarted)
    assert projected[1].tool_input == {"api_key": "[REDACTED]", "safe": "value"}
    assert isinstance(projected[2], ToolExecutionCompleted)
    assert isinstance(projected[3], AssistantTurnComplete)
    assert isinstance(projected[4], ErrorEvent)
    assert isinstance(projected[5], StatusEvent)
    private = _event("assistant.thinking", payload={"thinking": "private"})
    assert project_stream_event(private) is None


@pytest.mark.asyncio
async def test_bounded_stream_backpressures_and_closes_without_subscribers() -> None:
    bus = EventBus(capacity=1)
    await bus.start()
    stream = bus.stream()
    first_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    await asyncio.to_thread(bus.emit, _event("assistant.reply", payload={"reply": "one"}))
    first = await asyncio.wait_for(first_wait, timeout=1)
    assert first.payload["reply"] == "one"

    await asyncio.to_thread(bus.emit, _event("assistant.reply", payload={"reply": "two"}))
    blocked = asyncio.create_task(
        asyncio.to_thread(bus.emit, _event("assistant.reply", payload={"reply": "three"}))
    )
    await asyncio.sleep(0.05)
    assert not blocked.done()

    second = await asyncio.wait_for(anext(stream), timeout=1)
    assert second.payload["reply"] == "two"
    await asyncio.wait_for(blocked, timeout=1)
    third = await asyncio.wait_for(anext(stream), timeout=1)
    assert third.payload["reply"] == "three"

    await stream.aclose()
    await bus.aclose()
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_public_stream_does_not_expose_private_events() -> None:
    bus = EventBus(capacity=2)
    await bus.start()
    stream = bus.public_stream()
    public_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    await asyncio.to_thread(
        bus.emit,
        _event("assistant.thinking", payload={"thinking": "private reasoning"}),
    )
    await asyncio.to_thread(
        bus.emit,
        _event("assistant.reply", payload={"reply": "public answer"}),
    )

    public = await asyncio.wait_for(public_wait, timeout=1)
    assert public == AssistantTextDelta(text="public answer")
    await stream.aclose()
    await bus.aclose()
