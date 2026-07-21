from __future__ import annotations

import asyncio
import threading

import pytest

from homemaster.adapters.thread_owned_sync import (
    ThreadOwnedObservationBackend,
    ThreadOwnedSyncBackendAdapter,
)


@pytest.mark.asyncio
async def test_construction_calls_and_cleanup_share_one_owner_thread() -> None:
    adapter = ThreadOwnedSyncBackendAdapter(name="affinity")
    calls: list[tuple[int, int]] = []

    class Backend:
        def __init__(self) -> None:
            self.created_on = threading.get_ident()

        def call(self, value: int) -> int:
            calls.append((value, threading.get_ident()))
            return value * 2

        def close(self) -> int:
            return threading.get_ident()

    backend = adapter.call(Backend)
    values = await asyncio.gather(*(adapter.run(backend.call, value) for value in range(16)))
    closed_on = adapter.call(backend.close)
    adapter.close()

    assert values == [value * 2 for value in range(16)]
    assert backend.created_on == closed_on == adapter.owner_thread_id
    assert [value for value, _thread_id in calls] == list(range(16))
    assert {thread_id for _value, thread_id in calls} == {adapter.owner_thread_id}
    assert adapter.pending_count == 0
    assert adapter.active_count == 0
    assert adapter.alive is False


@pytest.mark.asyncio
async def test_cancelled_queued_call_never_reaches_backend() -> None:
    adapter = ThreadOwnedSyncBackendAdapter(name="queued-cancel")
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def block() -> str:
        entered.set()
        release.wait(timeout=2)
        calls.append("block")
        return "done"

    first = asyncio.create_task(adapter.run(block))
    assert await asyncio.to_thread(entered.wait, 1)
    queued = asyncio.create_task(adapter.run(calls.append, "queued"))
    await asyncio.sleep(0)

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    assert await first == "done"
    adapter.close()

    assert calls == ["block"]
    assert adapter.pending_count == 0
    assert adapter.alive is False


@pytest.mark.asyncio
async def test_worker_survives_backend_exception_and_closes_cleanly() -> None:
    adapter = ThreadOwnedSyncBackendAdapter(name="exception")

    def fail() -> None:
        raise ValueError("broken")

    with pytest.raises(ValueError, match="broken"):
        await adapter.run(fail)
    assert await adapter.run(lambda: "next") == "next"
    await adapter.aclose()

    assert adapter.closed is True
    assert adapter.alive is False


@pytest.mark.asyncio
async def test_close_waits_for_active_backend_before_stopping_thread() -> None:
    adapter = ThreadOwnedSyncBackendAdapter(name="close-drain")
    entered = threading.Event()
    release = threading.Event()

    def block() -> str:
        entered.set()
        release.wait(timeout=2)
        return "released"

    work = asyncio.create_task(adapter.run(block))
    assert await asyncio.to_thread(entered.wait, 1)
    closing = asyncio.create_task(asyncio.to_thread(adapter.close))
    await asyncio.sleep(0.01)

    assert closing.done() is False
    release.set()
    assert await work == "released"
    await closing

    assert adapter.pending_count == 0
    assert adapter.active_count == 0
    assert adapter.alive is False


@pytest.mark.asyncio
async def test_observation_facade_binds_and_captures_on_owner_thread() -> None:
    adapter = ThreadOwnedSyncBackendAdapter(name="observation")

    class Backend:
        backend_id = "coworker:test"
        generation = 0
        state_sequence = 0

        def __init__(self) -> None:
            self.thread_ids: list[int] = []
            self.run_id = ""

        def bind_application_run(self, run_id: str, generation: int) -> None:
            self.thread_ids.append(threading.get_ident())
            self.run_id = run_id
            self.generation = generation

        def capture(self) -> dict[str, object]:
            self.thread_ids.append(threading.get_ident())
            return {"run_id": self.run_id, "generation": self.generation}

    backend = Backend()
    facade = ThreadOwnedObservationBackend(backend, adapter)
    await facade.bind_application_run("run-1", 3)
    capture = await facade.capture()
    adapter.close()

    assert capture == {"run_id": "run-1", "generation": 3}
    assert backend.thread_ids == [adapter.owner_thread_id, adapter.owner_thread_id]
