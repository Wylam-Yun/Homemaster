from __future__ import annotations

import asyncio

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.application.session import SessionGenerationError, SessionManager
from homemaster.observations.service import ObservationLedger, ObservationState


@pytest.mark.asyncio
async def test_cancelled_run_that_swallows_cancellation_cannot_write_back(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("fenced")
    entered = asyncio.Event()
    continue_after_cancel = asyncio.Event()
    outcomes: list[str] = []

    async def stale_run() -> None:
        async with manager.turn("fenced") as (_, generation, cancellation):
            entered.set()
            try:
                await continue_after_cancel.wait()
            except asyncio.CancelledError:
                outcomes.append("cancelled")
            assert cancellation.cancelled is True
            with pytest.raises(SessionGenerationError):
                manager.append_message("fenced", generation, UserMessage.from_text("stale"))
            with pytest.raises(SessionGenerationError):
                manager.apply(
                    "fenced",
                    generation,
                    lambda current: current.agent_state.metadata.update({"domain": "stale"}),
                )
            with pytest.raises(SessionGenerationError):
                manager.commit_final_result("fenced", generation, "stale")
            with pytest.raises(SessionGenerationError):
                await manager.save("fenced", generation=generation)
            outcomes.append("fenced")

    task = asyncio.create_task(stale_run())
    await entered.wait()
    assert manager.cancel("fenced") is True
    continue_after_cancel.set()
    await task

    assert outcomes == ["cancelled", "fenced"]
    assert runtime.session.messages == []
    assert runtime.agent_state.metadata == {}
    assert runtime.last_result is None


@pytest.mark.asyncio
async def test_current_generation_can_commit_all_state(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("current")

    async with manager.turn("current") as (_, generation, _):
        manager.append_message("current", generation, UserMessage.from_text("current"))
        manager.apply(
            "current",
            generation,
            lambda current: current.agent_state.metadata.update({"domain": "current"}),
        )
        revision = await manager.save("current", generation=generation)
        manager.commit_final_result("current", generation, "done", status="completed")

    assert revision == 1
    assert runtime.session.messages[0].content[0].text == "current"
    assert runtime.agent_state.metadata["domain"] == "current"
    assert runtime.last_result == "done"


@pytest.mark.asyncio
async def test_rebind_and_resume_force_observation_ledger_to_needs_observe(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("observe", environment_ref="backend-a")
    ledger = ObservationLedger(run_id="run", backend_id="backend-a", generation=0)
    ledger.state = ObservationState.BOUND_READY
    runtime.set_observation_reset(ledger.invalidate)
    assert ledger.state is ObservationState.NEEDS_OBSERVE

    ledger.state = ObservationState.BOUND_READY
    runtime.rebind_environment("backend-b")
    assert ledger.state is ObservationState.NEEDS_OBSERVE
    await manager.save("observe")

    resumed_manager = SessionManager(session_root=tmp_path)
    resumed = await resumed_manager.resume("observe")
    resumed_ledger = ObservationLedger(run_id="run-2", backend_id="backend-b", generation=1)
    resumed_ledger.state = ObservationState.BOUND_READY
    resumed.set_observation_reset(resumed_ledger.invalidate)

    assert resumed_ledger.state is ObservationState.NEEDS_OBSERVE
