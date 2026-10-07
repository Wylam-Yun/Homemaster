from __future__ import annotations

import asyncio

import pytest

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.web.run_registry import (
    RunCorrelationError,
    SessionBusyError,
    WebRunRegistry,
)


def _event(
    event_type: str,
    *,
    session_id: str = "session-01",
    run_id: str = "run-01",
) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        session_id=session_id,
        run_id=run_id,
        turn_index=0,
        payload={},
    )


@pytest.mark.asyncio
async def test_registry_owns_request_task_and_correlates_one_run_until_terminal() -> None:
    registry = WebRunRegistry()
    release = asyncio.Event()
    calls = 0

    async def run() -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    first = await registry.accept("session-01", "request-01", run)
    duplicate = await registry.accept("session-01", "request-01", run)

    assert first.created is True
    assert duplicate.created is False
    assert calls == 0
    await asyncio.sleep(0)
    assert calls == 1
    with pytest.raises(SessionBusyError):
        await registry.accept("session-01", "request-02", run)

    assert await registry.correlate(_event("runtime.turn_started")) == "request-01"
    assert await registry.correlate(_event("transport.delta")) == "request-01"
    with pytest.raises(RunCorrelationError):
        await registry.correlate(_event("assistant.reply", session_id="session-other"))

    assert await registry.correlate(_event("runtime.turn_completed")) == "request-01"
    second = await registry.accept("session-01", "request-02", run)
    assert second.created is True

    release.set()
    await registry.aclose()
    assert registry.owned_task_count == 0


@pytest.mark.asyncio
async def test_registry_releases_only_matching_request_that_failed_before_start() -> None:
    registry = WebRunRegistry()
    release = asyncio.Event()

    async def run() -> None:
        await release.wait()

    await registry.accept("session-01", "request-01", run)

    assert await registry.fail_before_start("session-01", "request-other") is False
    with pytest.raises(SessionBusyError):
        await registry.accept("session-01", "request-02", run)

    assert await registry.fail_before_start("session-01", "request-01") is True
    assert await registry.fail_before_start("session-01", "request-01") is False
    assert (await registry.accept("session-01", "request-02", run)).created is True

    release.set()
    await registry.aclose()


@pytest.mark.asyncio
async def test_registry_does_not_use_prestart_failure_for_bound_run() -> None:
    registry = WebRunRegistry()
    release = asyncio.Event()

    async def run() -> None:
        await release.wait()

    await registry.accept("session-01", "request-01", run)
    await registry.correlate(_event("runtime.turn_started"))

    assert await registry.fail_before_start("session-01", "request-01") is False
    with pytest.raises(SessionBusyError):
        await registry.accept("session-01", "request-02", run)

    release.set()
    await registry.aclose()


@pytest.mark.asyncio
async def test_release_finished_only_acts_on_done_owned_task() -> None:
    registry = WebRunRegistry()
    gate = asyncio.Event()

    async def run() -> None:
        await gate.wait()

    await registry.accept("session-01", "request-01", run)

    # A still-running task keeps its binding: a terminal may still arrive.
    assert await registry.release_finished("session-01", "request-01") is None
    assert await registry.is_busy("session-01") is True

    gate.set()
    for _ in range(100):
        if registry.owned_task_count == 0:
            break
        await asyncio.sleep(0.01)

    # An unbound finished task releases with "" and frees the session.
    assert await registry.release_finished("session-01", "request-01") == ""
    assert await registry.is_busy("session-01") is False
    assert await registry.release_finished("session-01", "request-01") is None
    assert (await registry.accept("session-01", "request-02", run)).created is True

    gate.set()
    for _ in range(100):
        if registry.owned_task_count == 0:
            break
        await asyncio.sleep(0.01)
    await registry.aclose()


@pytest.mark.asyncio
async def test_release_finished_returns_bound_run_id_once() -> None:
    registry = WebRunRegistry()

    async def run() -> None:
        return None

    await registry.accept("session-01", "request-01", run)
    await registry.correlate(_event("runtime.turn_started"))
    for _ in range(100):
        if registry.owned_task_count == 0:
            break
        await asyncio.sleep(0.01)

    # Wrong request id must not touch another record.
    assert await registry.release_finished("session-01", "request-other") is None
    assert await registry.is_busy("session-01") is True

    assert await registry.release_finished("session-01", "request-01") == "run-01"
    # After release, a stale late terminal no longer correlates.
    with pytest.raises(RunCorrelationError):
        await registry.correlate(_event("runtime.turn_completed"))
    await registry.aclose()


@pytest.mark.asyncio
async def test_release_finished_noops_after_real_terminal_correlated() -> None:
    registry = WebRunRegistry()

    async def run() -> None:
        return None

    await registry.accept("session-01", "request-01", run)
    await registry.correlate(_event("runtime.turn_started"))
    assert await registry.correlate(_event("runtime.turn_completed")) == "request-01"
    for _ in range(100):
        if registry.owned_task_count == 0:
            break
        await asyncio.sleep(0.01)

    # The real terminal already released the record — no synthetic terminal.
    assert await registry.release_finished("session-01", "request-01") is None
    await registry.aclose()
