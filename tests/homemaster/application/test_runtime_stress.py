from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress
from types import SimpleNamespace

import pytest

from homemaster.adapters.thread_owned_sync import ThreadOwnedSyncBackendAdapter
from homemaster.agent.messages import ToolCall, UserMessage
from homemaster.application.resources import ApplicationResourceManager, RunResourceScope
from homemaster.application.runtime import _GenerationFencedEventSink
from homemaster.application.session import SessionGenerationError, SessionManager
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.pipeline import ToolExecutionPipeline


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


@pytest.mark.stress
@pytest.mark.asyncio
async def test_32_fake_sessions_reach_the_same_barrier_without_leaks() -> None:
    baseline_tasks = set(asyncio.all_tasks())
    baseline_threads = _backend_threads()
    manager = SessionManager()
    bus = EventBus(capacity=32)
    await bus.start()
    resources = ApplicationResourceManager()
    barrier = asyncio.Barrier(32)
    entered = 0
    backend = SimpleNamespace(backend_id="shared-physical-backend")

    class Executor:
        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.max_active = 0
            self.first_entered = asyncio.Event()
            self.release_first = asyncio.Event()

        async def execute(self, arguments, context):
            del arguments, context
            self.calls += 1
            call_index = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if call_index == 1:
                    self.first_entered.set()
                    await self.release_first.wait()
                return ToolExecutionResult(
                    status=ToolExecutionStatus.SUCCESS,
                    backend_attempted=True,
                )
            finally:
                self.active -= 1

    executor = Executor()
    definition = ToolDefinition(
        internal_id="stress.mutate.v1",
        model_alias="mutate",
        description="Mutate one shared stress backend.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference="stress"),
        version="1.9.0",
        concurrency_policy=ConcurrencyPolicy.RESOURCE_KEY,
        resource_key="stress:backend",
        state_effects=("backend.advance",),
    )
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(definition=definition, executor=executor))
    view = catalog.freeze((definition.internal_id,))
    pipeline = ToolExecutionPipeline(catalog, resource_manager=resources)

    for index in range(32):
        await manager.open_or_resume(f"session-{index}")

    async def run_session(index: int) -> None:
        nonlocal entered
        session_id = f"session-{index}"
        async with manager.turn(session_id) as (runtime, generation, cancellation):
            await barrier.wait()
            entered += 1
            context = ToolExecutionContext(
                session_id=session_id,
                run_id=f"run-{index}",
                turn_index=0,
                tool_call_id=f"call-{index}",
                internal_tool_id=definition.internal_id,
                tool_view=view,
                permission_subject=PermissionSubject(subject_id="stress", channel="test"),
                backend=backend,
                deadline=None,
                cancellation=cancellation,
                observation=None,
                domain_observer=None,
            )
            result = await pipeline.execute(
                ToolCall(id=f"call-{index}", name="mutate", arguments={}),
                context,
            )
            assert result.status is ToolExecutionStatus.SUCCESS
            manager.append_message(
                session_id,
                generation,
                UserMessage.from_text(f"message-{index}"),
            )
            sink = _GenerationFencedEventSink(runtime, generation, bus)
            await sink.aemit(_event(index, session_id=session_id))

    tasks = [asyncio.create_task(run_session(index)) for index in range(32)]
    await executor.first_entered.wait()
    await _spin_until(lambda: resources.waiting_count == 31)
    assert resources.active_lease_count == 1
    executor.release_first.set()
    await asyncio.gather(*tasks)
    await bus.aclose()

    assert entered == 32
    assert executor.calls == 32
    assert executor.max_active == 1
    assert resources.resource_count == 0
    assert len(bus.events) == 32
    assert all(len(runtime.session.messages) == 1 for runtime in manager.sessions)
    await _assert_no_leaks(
        baseline_tasks,
        baseline_threads,
        bus=bus,
        active_leases=resources.active_lease_count,
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
    resources = ApplicationResourceManager()
    backend = SimpleNamespace(backend_id="generation-backend")

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
        late_release = asyncio.Event()
        late_started = asyncio.Event()
        late_workers: list[asyncio.Task[None]] = []

        async def late_backend_completion(
            race_index: int,
            generation: int,
            sink: _GenerationFencedEventSink,
            backend_started: asyncio.Event,
            backend_release: asyncio.Event,
        ) -> None:
            context = SimpleNamespace(
                backend=backend,
                session_id="generation-race",
            )
            async with resources.acquire("stress:backend", context):
                backend_started.set()
                await backend_release.wait()
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

        async def stale_run(
            race_index: int,
            race_entered: asyncio.Event,
            backend_started: asyncio.Event,
            backend_release: asyncio.Event,
            workers: list[asyncio.Task[None]],
        ) -> None:
            async with manager.turn("generation-race") as (current, generation, _):
                sink = _GenerationFencedEventSink(current, generation, bus)
                workers.append(
                    asyncio.create_task(
                        late_backend_completion(
                            race_index,
                            generation,
                            sink,
                            backend_started,
                            backend_release,
                        )
                    )
                )
                await backend_started.wait()
                race_entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            stale_run(index, entered, late_started, late_release, late_workers)
        )
        await entered.wait()
        assert manager.cancel("generation-race") is True
        with suppress(asyncio.CancelledError):
            await task

        async with manager.turn("generation-race") as (current, generation, _):
            context = SimpleNamespace(
                backend=backend,
                session_id="generation-race",
            )

            async def current_generation_commit(
                race_index: int,
                race_generation: int,
                race_context: SimpleNamespace,
            ) -> None:
                async with resources.acquire("stress:backend", race_context):
                    manager.append_message(
                        "generation-race",
                        race_generation,
                        UserMessage.from_text(f"current-{race_index}"),
                    )
                    manager.apply(
                        "generation-race",
                        race_generation,
                        lambda value: value.agent_state.metadata.update(
                            {"domain": race_index}
                        ),
                    )
                    manager.commit_final_result(
                        "generation-race",
                        race_generation,
                        f"current-{race_index}",
                    )
                    await manager.save(
                        "generation-race",
                        generation=race_generation,
                    )
                    sink = _GenerationFencedEventSink(current, race_generation, bus)
                    await sink.aemit(_event(race_index))

            current_commit = asyncio.create_task(
                current_generation_commit(index, generation, context)
            )
            await _spin_until(lambda: resources.waiting_count == 1)
            late_release.set()
            await late_workers[0]
            await current_commit

    await bus.aclose()

    assert stale_attempts == stale_rejections == 600
    assert [message.content[0].text for message in runtime.session.messages] == [
        f"current-{index}" for index in range(100)
    ]
    assert runtime.agent_state.metadata == {"domain": 99}
    assert runtime.last_result == "current-99"
    assert runtime.revision == 100
    assert [event.payload["index"] for event in bus.events] == list(range(100))
    assert resources.waiting_count == resources.resource_count == 0
    await _assert_no_leaks(
        baseline_tasks,
        baseline_threads,
        bus=bus,
        active_leases=resources.active_lease_count,
    )
