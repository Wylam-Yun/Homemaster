import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.experience.dreaming_state import DreamingCoordinator, DreamingStateStore


class RecordingEventSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def test_seven_memories_are_not_due_and_eighth_claims_exact_batch(tmp_path: Path) -> None:
    store = DreamingStateStore(tmp_path, threshold=8)
    for index in range(7):
        store.register(
            project_id="project-a",
            user_id="user-a",
            add_record_id=f"add-{index}",
            memory_ids=(f"memory-{index}",),
        )
    assert store.claim(project_id="project-a", user_id="user-a") is None

    store.register(
        project_id="project-a",
        user_id="user-a",
        add_record_id="add-7",
        memory_ids=("memory-7",),
    )
    batch = store.claim(project_id="project-a", user_id="user-a")

    assert batch is not None
    assert batch.add_record_ids == tuple(f"add-{index}" for index in range(8))
    assert store.claim(project_id="project-a", user_id="user-a") is None


def test_failure_restores_batch_and_success_preserves_new_arrivals(tmp_path: Path) -> None:
    store = DreamingStateStore(tmp_path, threshold=2)
    for index in range(2):
        store.register(
            project_id="project-a",
            user_id="user-a",
            add_record_id=f"add-{index}",
            memory_ids=(f"memory-{index}",),
        )
    batch = store.claim(project_id="project-a", user_id="user-a")
    assert batch is not None
    store.fail(batch, error="provider rejected")
    assert store.read(project_id="project-a", user_id="user-a")["pending"] is True

    retry = store.claim(project_id="project-a", user_id="user-a")
    assert retry is not None
    store.register(
        project_id="project-a",
        user_id="user-a",
        add_record_id="add-new",
        memory_ids=("memory-new",),
    )
    store.complete(retry, outcome="no_action")
    state = store.read(project_id="project-a", user_id="user-a")
    assert state["new_active_memory_count"] == 1
    assert state["pending_add_records"][0]["add_record_id"] == "add-new"


def test_scopes_are_isolated_and_duplicate_add_record_is_idempotent(tmp_path: Path) -> None:
    store = DreamingStateStore(tmp_path, threshold=1)
    for _ in range(2):
        store.register(
            project_id="project-a",
            user_id="user-a",
            add_record_id="same-add",
            memory_ids=("same-memory",),
        )
    other = store.read(project_id="project-b", user_id="user-b")

    assert store.read(project_id="project-a", user_id="user-a")[
        "new_active_memory_count"
    ] == 1
    assert other["new_active_memory_count"] == 0


def test_only_one_process_claims_live_inflight_batch(tmp_path: Path) -> None:
    store = DreamingStateStore(tmp_path, threshold=1)
    store.register(
        project_id="project-a",
        user_id="user-a",
        add_record_id="add-1",
        memory_ids=("memory-1",),
    )
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_claim_worker,
            args=(str(tmp_path), release, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    first = queue.get(timeout=10)
    second = queue.get(timeout=10)
    release.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted([first, second]) == [False, True]


@pytest.mark.asyncio
async def test_coordinator_consumes_verified_no_action_batch(tmp_path: Path) -> None:
    from mindmemos.typing import DreamingPipelineResult

    class FakeMindMemOS:
        async def dream(self, *, seed_add_record_ids, context):
            assert seed_add_record_ids == ["add-1"]
            assert context.session_id is None
            return DreamingPipelineResult(
                status="ok",
                outcome="no_action",
                reviewed_add_record_ids=["add-1"],
                completed_add_record_ids=["add-1"],
            )

        async def get_add_records(self, add_record_ids, context):
            del context
            return [
                SimpleNamespace(
                    point_id=add_record_ids[0],
                    payload={"consolidation_status": "done"},
                )
            ]

    store = DreamingStateStore(tmp_path, threshold=1)
    event_sink = RecordingEventSink()
    coordinator = DreamingCoordinator(
        store=store,
        mindmemos=FakeMindMemOS(),
        event_sink=event_sink,
    )
    outcome = await coordinator.register_and_run(
        context=_context(),
        add_record_id="add-1",
        memory_ids=("memory-1",),
    )

    assert outcome == "no_action"
    state = store.read(project_id="project-a", user_id="user-a")
    assert state["pending"] is False
    assert state["last_successful_watermark"]["add_record_ids"] == ["add-1"]
    assert [event.type for event in event_sink.events] == [
        "memory.dreaming.threshold_reached",
        "memory.dreaming.started",
        "memory.dreaming.no_action",
    ]
    assert event_sink.events[0].payload["threshold"] == 1
    assert event_sink.events[0].payload["count"] == 1
    assert event_sink.events[-1].payload["action_count"] == 0


@pytest.mark.asyncio
async def test_coordinator_keeps_pending_when_add_record_not_done(tmp_path: Path) -> None:
    from mindmemos.typing import DreamingPipelineResult

    class FakeMindMemOS:
        async def dream(self, *, seed_add_record_ids, context):
            del seed_add_record_ids, context
            return DreamingPipelineResult(status="ok", outcome="no_action")

        async def get_add_records(self, add_record_ids, context):
            del context
            return [SimpleNamespace(point_id=add_record_ids[0], payload={})]

    store = DreamingStateStore(tmp_path, threshold=1)
    event_sink = RecordingEventSink()
    coordinator = DreamingCoordinator(
        store=store,
        mindmemos=FakeMindMemOS(),
        event_sink=event_sink,
    )
    outcome = await coordinator.register_and_run(
        context=_context(),
        add_record_id="add-1",
        memory_ids=("memory-1",),
    )

    assert outcome == "failed"
    state = store.read(project_id="project-a", user_id="user-a")
    assert state["pending"] is True
    assert state["pending_add_records"][0]["add_record_id"] == "add-1"
    assert event_sink.events[-1].type == "memory.dreaming.failed"
    assert "not consolidated" in event_sink.events[-1].payload["error"]


def _context():
    return SimpleNamespace(
        request_id="request-1",
        account_id="account-a",
        project_id="project-a",
        api_key_uuid="key-a",
        user_id="user-a",
        app_id="homemaster",
        session_id="session-a",
        agent_id="homemaster",
    )


def _claim_worker(root: str, release, queue) -> None:
    store = DreamingStateStore(Path(root), threshold=1)
    batch = store.claim(project_id="project-a", user_id="user-a")
    queue.put(batch is not None)
    if batch is not None:
        release.wait(timeout=10)
