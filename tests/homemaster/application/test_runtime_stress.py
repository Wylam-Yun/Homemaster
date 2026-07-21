from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

import pytest

from homemaster.adapters.thread_owned_sync import ThreadOwnedSyncBackendAdapter
from homemaster.agent.messages import UserMessage
from homemaster.application.resources import RunResourceScope
from homemaster.application.runtime import _GenerationFencedEventSink
from homemaster.application.session import SessionGenerationError, SessionManager
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent


def _event(index: int, *, session_id: str = "stress") -> RuntimeEvent:
    return RuntimeEvent(
        type="runtime.progress",
        session_id=session_id,
        run_id=f"run-{index}",
        turn_index=0,
        payload={"index": index},
    )


async def _spin_until(predicate: Callable[[], bool], *, limit: int = 10_000) -> None:
    for _ in range(limit):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true within the scheduler budget")


def _backend_threads() -> set[int | None]:
    return {
        thread.ident
        for thread in threading.enumerate()
        if thread.name.startswith("homemaster-sync-")
    }


async def _assert_no_leaks(
    baseline_tasks: set[asyncio.Task[object]],
    baseline_threads: set[int | None],
    *,
    bus: EventBus,
    active_leases: int,
) -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    current = asyncio.current_task()
    pending = {
        task
        for task in asyncio.all_tasks()
        if task is not current and task not in baseline_tasks and not task.done()
    }
    assert pending == set()
    assert bus.subscriber_count == 0
    assert bus.pending_producer_count == 0
    assert active_leases == 0
    assert _backend_threads() == baseline_threads


class _LeaseCounter:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    @asynccontextmanager
    async def acquire(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            yield
        finally:
            self.active -= 1


@pytest.mark.stress
@pytest.mark.asyncio
async def test_32_fake_sessions_reach_the_same_barrier_without_leaks() -> None:
    baseline_tasks = set(asyncio.all_tasks())
    baseline_threads = _backend_threads()
    manager = SessionManager()
    bus = EventBus(capacity=32)
    await bus.start()
    leases = _LeaseCounter()
    barrier = asyncio.Barrier(32)
    entered = 0
    all_entered = asyncio.Event()

    for index in range(32):
        await manager.open_or_resume(f"session-{index}")

    async def run_session(index: int) -> None:
        nonlocal entered
        session_id = f"session-{index}"
        async with manager.turn(session_id) as (runtime, generation, _):
            async with leases.acquire():
                await barrier.wait()
                entered += 1
                if entered == 32:
                    all_entered.set()
                await all_entered.wait()
                manager.append_message(
                    session_id,
                    generation,
                    UserMessage.from_text(f"message-{index}"),
                )
                sink = _GenerationFencedEventSink(runtime, generation, bus)
                await sink.aemit(_event(index, session_id=session_id))

    await asyncio.gather(*(run_session(index) for index in range(32)))
    await bus.aclose()

    assert entered == 32
    assert leases.max_active == 32
    assert len(bus.events) == 32
    assert all(len(runtime.session.messages) == 1 for runtime in manager.sessions)
    await _assert_no_leaks(
        baseline_tasks,
        baseline_threads,
        bus=bus,
        active_leases=leases.active,
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_1000_progress_events_obey_bounded_backpressure_without_leaks() -> None:
    baseline_tasks = set(asyncio.all_tasks())
    baseline_threads = _backend_threads()
    bus = EventBus(capacity=8)
    await bus.start()
    stream = bus.stream()
    first_consumed = asyncio.Event()
    release_consumer = asyncio.Event()
    authoritative: list[int] = []
    published: list[int] = []
    consumed: list[int] = []

    async def consume() -> None:
        try:
            async for event in stream:
                index = int(event.payload["index"])
                consumed.append(index)
                if index == 0:
                    first_consumed.set()
                    await release_consumer.wait()
                if len(consumed) == 1000:
                    return
        finally:
            await stream.aclose()

    async def produce() -> None:
        for index in range(1000):
            authoritative.append(index)
            await bus.aemit(_event(index))
            published.append(index)

    consumer = asyncio.create_task(consume())
    await _spin_until(lambda: bus.subscriber_count == 1)
    producer = asyncio.create_task(produce())
    await first_consumed.wait()
    await _spin_until(
        lambda: len(authoritative) == 10 and len(published) == 9
    )

    assert authoritative == list(range(10))
    assert published == list(range(9))
    assert bus.pending_producer_count == 1
    release_consumer.set()
    await asyncio.gather(producer, consumer)
    await bus.aclose()

    assert authoritative == list(range(1000))
    assert published == authoritative
    assert consumed == authoritative
    await _assert_no_leaks(
        baseline_tasks,
        baseline_threads,
        bus=bus,
        active_leases=0,
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_1000_stream_and_owned_backend_open_close_cycles_do_not_leak() -> None:
    baseline_tasks = set(asyncio.all_tasks())
    baseline_threads = _backend_threads()
    bus = EventBus(capacity=1)
    await bus.start()
    active_resources = 0
    closed_resources = 0

    class OwnedWorker:
        def __init__(self, index: int) -> None:
            nonlocal active_resources
            self.index = index
            self.adapter = ThreadOwnedSyncBackendAdapter(name=f"stress-{index}")
            self.closed = False
            active_resources += 1

        async def ping(self) -> int:
            return await self.adapter.run(lambda: self.index)

        def close(self) -> None:
            nonlocal active_resources, closed_resources
            assert self.closed is False
            self.adapter.close()
            self.closed = True
            active_resources -= 1
            closed_resources += 1

    for index in range(1000):
        stream = bus.stream()
        next_event = asyncio.create_task(anext(stream))
        await _spin_until(lambda: bus.subscriber_count == 1)
        await bus.aemit(_event(index))
        assert (await next_event).payload == {"index": index}
        await stream.aclose()
        assert bus.subscriber_count == 0

        scope = RunResourceScope()
        worker = (
            await scope.acquire(
                "backend",
                lambda index=index: OwnedWorker(index),
            )
        ).resource
        assert await worker.ping() == index
        await scope.aclose()
        assert worker.closed is True
        assert worker.adapter.pending_count == 0
        assert worker.adapter.active_count == 0
        assert worker.adapter.alive is False

    await bus.aclose()
    assert closed_resources == 1000
    await _assert_no_leaks(
        baseline_tasks,
        baseline_threads,
        bus=bus,
        active_leases=active_resources,
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_100_cancel_restart_races_fence_every_stale_write(tmp_path) -> None:
    baseline_tasks = set(asyncio.all_tasks())
    baseline_threads = _backend_threads()
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("generation-race")
    bus = EventBus()
    await bus.start()
    stale_attempts = 0
    stale_rejections = 0

    async def reject_stale(operation) -> None:
        nonlocal stale_attempts, stale_rejections
        stale_attempts += 1
        try:
            value = operation()
            if asyncio.iscoroutine(value):
                await value
        except SessionGenerationError:
            stale_rejections += 1
        else:
            raise AssertionError("stale generation write was accepted")

    for index in range(100):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def stale_run(
            race_index: int,
            race_entered: asyncio.Event,
            race_release: asyncio.Event,
        ) -> None:
            async with manager.turn("generation-race") as (current, generation, _):
                sink = _GenerationFencedEventSink(current, generation, bus)
                race_entered.set()
                try:
                    await race_release.wait()
                except asyncio.CancelledError:
                    pass
                await reject_stale(
                    lambda: manager.append_message(
                        "generation-race",
                        generation,
                        UserMessage.from_text(f"stale-{race_index}"),
                    )
                )
                await reject_stale(
                    lambda: manager.apply(
                        "generation-race",
                        generation,
                        lambda value: value.agent_state.metadata.update(
                            {"domain": f"stale-{race_index}"}
                        ),
                    )
                )
                await reject_stale(
                    lambda: manager.apply(
                        "generation-race",
                        generation,
                        lambda value: value.task_state_store.clear(),
                    )
                )
                await reject_stale(
                    lambda: manager.commit_final_result(
                        "generation-race",
                        generation,
                        f"stale-{race_index}",
                    )
                )
                await reject_stale(
                    lambda: manager.save("generation-race", generation=generation)
                )
                await reject_stale(lambda: sink.aemit(_event(race_index)))

        task = asyncio.create_task(stale_run(index, entered, release))
        await entered.wait()
        assert manager.cancel("generation-race") is True
        release.set()
        await task

        async with manager.turn("generation-race") as (current, generation, _):
            manager.append_message(
                "generation-race",
                generation,
                UserMessage.from_text(f"current-{index}"),
            )
            manager.apply(
                "generation-race",
                generation,
                lambda value, index=index: value.agent_state.metadata.update(
                    {"domain": index}
                ),
            )
            manager.commit_final_result(
                "generation-race",
                generation,
                f"current-{index}",
            )
            await manager.save("generation-race", generation=generation)
            sink = _GenerationFencedEventSink(current, generation, bus)
            await sink.aemit(_event(index))

    await bus.aclose()

    assert stale_attempts == stale_rejections == 600
    assert [message.content[0].text for message in runtime.session.messages] == [
        f"current-{index}" for index in range(100)
    ]
    assert runtime.agent_state.metadata == {"domain": 99}
    assert runtime.last_result == "current-99"
    assert runtime.revision == 100
    assert [event.payload["index"] for event in bus.events] == list(range(100))
    await _assert_no_leaks(
        baseline_tasks,
        baseline_threads,
        bus=bus,
        active_leases=0,
    )
