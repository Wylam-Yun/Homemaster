from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homemaster.experience import FinalizeResult, SessionFinalizationController


class RecordingQueue:
    def __init__(self, *, started: bool = True) -> None:
        self.started = started
        self.jobs = []

    def enqueue_work(self, *, job_type, session_id, work):
        self.jobs.append((job_type, session_id, work))
        return SimpleNamespace(job_id=f"job-{len(self.jobs)}", status="accepted")


@pytest.mark.asyncio
async def test_controller_admits_without_waiting_and_exposes_result() -> None:
    release = asyncio.Event()

    class Finalizer:
        async def finalize(self, session_id, exit_reason):
            await release.wait()
            return FinalizeResult(session_id=session_id, status=exit_reason)

    queue = RecordingQueue()
    controller = SessionFinalizationController(Finalizer(), queue)

    receipt = controller.enqueue("session-one", "episode_end")

    assert receipt is not None
    assert [(kind, session_id) for kind, session_id, _ in queue.jobs] == [
        ("session_finalization", "session-one")
    ]
    work = asyncio.create_task(queue.jobs[0][2]())
    assert work.done() is False
    release.set()
    assert await controller.wait(receipt) == FinalizeResult(
        session_id="session-one", status="episode_end"
    )
    await work


def test_controller_skips_session_when_memory_queue_never_started() -> None:
    controller = SessionFinalizationController(object(), RecordingQueue(started=False))

    assert controller.enqueue("session-one", "startup_failed") is None


def test_controller_skips_session_when_memory_runtime_is_unavailable() -> None:
    controller = SessionFinalizationController(
        object(),
        RecordingQueue(),
        ready=lambda: False,
    )

    assert controller.enqueue("session-one", "memory_unavailable") is None


def test_controller_admission_does_not_require_a_running_event_loop() -> None:
    controller = SessionFinalizationController(object(), RecordingQueue())

    receipt = controller.enqueue("shell-session", "user_exit")

    assert receipt is not None
    assert receipt.status == "accepted"
