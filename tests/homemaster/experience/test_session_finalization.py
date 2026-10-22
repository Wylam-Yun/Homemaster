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
        async def finalize(self, session_id, exit_reason, *, trace_end=None):
            del trace_end
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


def test_controller_merges_duplicate_enqueues_for_same_session() -> None:
    queue = RecordingQueue()
    controller = SessionFinalizationController(object(), queue)

    first = controller.enqueue("session-one", "archived")
    merged = controller.enqueue("session-one", "new_session")

    assert first is not None and merged is first
    assert [(kind, session_id) for kind, session_id, _ in queue.jobs] == [
        ("session_finalization", "session-one")
    ]

    other = controller.enqueue("session-two", "new_session")
    assert other is not None and other is not first
    assert len(queue.jobs) == 2


@pytest.mark.asyncio
async def test_controller_enqueue_after_completion_creates_new_job() -> None:
    class Finalizer:
        async def finalize(self, session_id, exit_reason, *, trace_end=None):
            del trace_end
            return FinalizeResult(session_id=session_id, status="empty")

    queue = RecordingQueue()
    controller = SessionFinalizationController(Finalizer(), queue)

    first = controller.enqueue("session-one", "archived")
    await queue.jobs[0][2]()

    second = controller.enqueue("session-one", "new_session")

    assert second is not None and second is not first
    assert len(queue.jobs) == 2


class _RecordingSink:
    def __init__(self) -> None:
        self.events = []

    async def aemit(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_controller_emits_work_lifecycle_events_on_success() -> None:
    class Finalizer:
        async def finalize(self, session_id, exit_reason, *, trace_end=None):
            del trace_end
            return FinalizeResult(
                session_id=session_id,
                status=exit_reason,
                collected_events=7,
                operations=(SimpleNamespace(), SimpleNamespace()),
                duration_ms=12.5,
            )

    queue = RecordingQueue()
    sink = _RecordingSink()
    controller = SessionFinalizationController(Finalizer(), queue, event_sink=sink)

    receipt = controller.enqueue("session-one", "archived")
    assert controller.status_for("session-one") == {
        "state": "queued",
        "job_id": receipt.job_id,
        "exit_reason": "archived",
    }

    await queue.jobs[0][2]()

    assert [event.type for event in sink.events] == [
        "memory.work.processing",
        "memory.work.completed",
    ]
    assert all(event.session_id == "session-one" for event in sink.events)
    assert sink.events[0].payload["job_id"] == receipt.job_id
    assert sink.events[1].payload["extracted_count"] == 2


@pytest.mark.asyncio
async def test_controller_emits_failed_event_for_failed_result() -> None:
    class Finalizer:
        async def finalize(self, session_id, exit_reason, *, trace_end=None):
            del trace_end
            return FinalizeResult(session_id=session_id, status="failed", error="boom")

    queue = RecordingQueue()
    sink = _RecordingSink()
    controller = SessionFinalizationController(Finalizer(), queue, event_sink=sink)
    controller.enqueue("session-one", "archived")

    with pytest.raises(RuntimeError, match="boom"):
        await queue.jobs[0][2]()

    assert [event.type for event in sink.events] == [
        "memory.work.processing",
        "memory.work.failed",
    ]
    assert sink.events[1].payload["error"] == "boom"


@pytest.mark.asyncio
async def test_controller_emits_failed_event_when_finalize_raises() -> None:
    class Finalizer:
        async def finalize(self, session_id, exit_reason, *, trace_end=None):
            del trace_end
            raise ValueError("kaput")

    queue = RecordingQueue()
    sink = _RecordingSink()
    controller = SessionFinalizationController(Finalizer(), queue, event_sink=sink)
    controller.enqueue("session-one", "archived")

    with pytest.raises(ValueError):
        await queue.jobs[0][2]()

    assert [event.type for event in sink.events] == [
        "memory.work.processing",
        "memory.work.failed",
    ]
    assert "kaput" in sink.events[1].payload["error"]


@pytest.mark.asyncio
async def test_status_for_reports_processing_then_merges_durable_receipt() -> None:
    release = asyncio.Event()

    class Finalizer:
        def job_index(self):
            return {
                "session-one": {
                    "status": "pending",
                    "job_id": "job-1",
                    "add": {"status": "processing"},
                }
            }

        async def finalize(self, session_id, exit_reason, *, trace_end=None):
            del trace_end
            await release.wait()
            return FinalizeResult(session_id=session_id, status="completed")

    queue = RecordingQueue()
    controller = SessionFinalizationController(Finalizer(), queue)
    controller.enqueue("session-one", "archived")

    work = asyncio.create_task(queue.jobs[0][2]())
    await asyncio.sleep(0)
    view = controller.status_for("session-one")
    assert view["state"] == "processing"
    assert view["stages"]["add"] == "processing"

    release.set()
    await work


def test_status_for_and_statuses_read_durable_receipts() -> None:
    class Finalizer:
        def job_index(self):
            return {
                "session-one": {
                    "status": "completed",
                    "job_id": "job-1",
                    "exit_reason": "archived",
                    "add": {"status": "completed", "operations": [{}, {}, {}]},
                    "implicit_feedback": {"status": "completed"},
                    "dreaming": {"status": "not_due"},
                },
                "session-two": {
                    "status": "failed",
                    "job_id": "job-2",
                    "failed_phase": "add",
                    "add": {"status": "failed", "error": "rate limited"},
                },
            }

    controller = SessionFinalizationController(Finalizer(), RecordingQueue())

    done = controller.status_for("session-one")
    assert done["state"] == "completed"
    assert done["extracted_count"] == 3
    assert done["stages"]["implicit_feedback"] == "completed"

    failed = controller.status_for("session-two")
    assert failed["state"] == "failed"
    assert failed["failed_phase"] == "add"
    assert failed["error"] == "rate limited"

    assert controller.status_for("session-three") is None

    statuses = controller.statuses()
    assert set(statuses) == {"session-one", "session-two"}
    assert statuses["session-two"]["state"] == "failed"
