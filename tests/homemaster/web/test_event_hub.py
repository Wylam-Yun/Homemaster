"""WebEventHub backpressure and last-resort terminalization tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homemaster.events.bus import EventBus
from homemaster.web.event_hub import WebEventHub, _DroppedSubscriber
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.run_registry import WebRunRegistry
from homemaster.web.schemas import WebEvent


def _event(session_id: str = "s1", request_id: str = "req-1") -> WebEvent:
    return WebEvent(
        type="answer.delta",
        session_id=session_id,
        run_id="run-01",
        request_id=request_id,
        payload={"text": "x"},
    )


def _hub(
    registry: WebRunRegistry | None = None,
    *,
    capacity: int = 2,
) -> WebEventHub:
    return WebEventHub(
        EventBus(),
        registry or WebRunRegistry(),
        WebEventProjection(),
        capacity=capacity,
    )


@pytest.mark.asyncio
async def test_publish_drops_stalled_subscriber_but_keeps_healthy_ones() -> None:
    hub = _hub(capacity=2)
    stalled = await hub.subscribe("s1")
    healthy = await hub.subscribe("s1")
    other_session = await hub.subscribe("s2")

    delivered: list[WebEvent] = []
    for _ in range(6):
        # The shared publish path must never block on one full queue.
        await asyncio.wait_for(hub.publish(_event()), timeout=1)
        delivered.append(healthy.get_nowait())  # a reading client keeps up
        await hub.publish(_event("s2", "req-9"))
        assert isinstance(other_session.get_nowait(), WebEvent)

    assert len(delivered) == 6
    # The stalled subscriber was dropped: its backlog is replaced by the
    # exit sentinel and it receives nothing further.
    assert isinstance(stalled.get_nowait(), _DroppedSubscriber)
    with pytest.raises(asyncio.QueueEmpty):
        stalled.get_nowait()
    assert await hub.has_subscriber("s1") is True
    assert await hub.has_subscriber("s2") is True

    await hub.publish(_event())
    assert isinstance(healthy.get_nowait(), WebEvent)
    with pytest.raises(asyncio.QueueEmpty):
        stalled.get_nowait()


@pytest.mark.asyncio
async def test_stream_exits_on_dropped_subscriber_sentinel() -> None:
    from homemaster.web.app import _stream_events

    queue: asyncio.Queue[object] = asyncio.Queue()
    queue.put_nowait(_DroppedSubscriber())

    async def _never_disconnect() -> dict[str, str]:
        await asyncio.get_running_loop().create_future()
        return {"type": "websocket.disconnect"}

    websocket = SimpleNamespace(
        receive=_never_disconnect,
        send_json=lambda _payload: _unexpected_send(),
    )

    await asyncio.wait_for(_stream_events(websocket, queue), timeout=1)


async def _unexpected_send() -> None:
    raise AssertionError("a dropped subscriber must not receive frames")


@pytest.mark.asyncio
async def test_finish_run_terminalizes_done_task_once_and_frees_session() -> None:
    registry = WebRunRegistry()
    hub = _hub(registry)
    queue = await hub.subscribe("s1")

    async def run() -> object:
        return SimpleNamespace(status="cancelled")

    await registry.accept("s1", "req-1", run)
    for _ in range(100):
        if registry.owned_task_count == 0:
            break
        await asyncio.sleep(0.01)
    await hub.finish_run("s1", "req-1", "cancelled")

    event = queue.get_nowait()
    assert event.to_dict() == {
        "type": "run.cancelled",
        "session_id": "s1",
        "run_id": "",
        "request_id": "req-1",
        "payload": {},
    }
    assert await registry.is_busy("s1") is False

    # Terminalization is idempotent — no double emit for the same request.
    await hub.finish_run("s1", "req-1", "cancelled")
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


@pytest.mark.asyncio
async def test_finish_run_never_terminalizes_a_running_task() -> None:
    registry = WebRunRegistry()
    hub = _hub(registry)
    queue = await hub.subscribe("s1")
    gate = asyncio.Event()

    async def run() -> None:
        await gate.wait()

    await registry.accept("s1", "req-1", run)
    await hub.finish_run("s1", "req-1", "failed")
    assert await registry.is_busy("s1") is True
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()

    gate.set()
    for _ in range(100):
        if registry.owned_task_count == 0:
            break
        await asyncio.sleep(0.01)

    await hub.finish_run("s1", "req-1", "failed")
    event = queue.get_nowait()
    assert event.type == "run.failed"
    assert event.payload == {
        "code": "run_failed",
        "message": "The run ended without a terminal event.",
        "retryable": False,
    }
    assert await registry.is_busy("s1") is False
    await registry.aclose()
