from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.memory.add_queue import MemoryAddQueue, MemoryAddQueueClosed
from homemaster.memory.models import FactRecord, Subject


def _record(name: str) -> FactRecord:
    return FactRecord(
        subject=Subject(type="object", name=name),
        predicate="location",
        value="shelf",
        source="environment_observation",
    )


def _context(request_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        request_id=request_id,
        account_id="tenant-a",
        project_id="tenant-a",
        user_id="tenant-a",
        session_id="session-a",
    )


def _states(path: Path) -> list[dict[str, object]]:
    return [json.loads(line)["payload"] for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_enqueue_returns_before_terminal_add_and_close_waits_for_it(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Store:
        async def add_record(self, record, *, provenance_seq, context):
            assert record.subject.name == "apple"
            assert provenance_seq == 7
            assert context.request_id == "request-a"
            entered.set()
            await release.wait()
            return {"memory_id": "memory-a", "verified_terminal_state": True}

    audit_path = tmp_path / "jobs.jsonl"
    queue = MemoryAddQueue(Store(), audit_path=audit_path)
    await queue.start()

    receipt = await queue.enqueue(
        record=_record("apple"),
        provenance_seq=7,
        context=_context("request-a"),
    )

    assert receipt.status == "accepted"
    assert receipt.job_id
    await asyncio.wait_for(entered.wait(), timeout=1)
    close_task = asyncio.create_task(queue.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()

    release.set()
    await asyncio.wait_for(close_task, timeout=1)
    states = _states(audit_path)
    assert [item["status"] for item in states] == ["queued", "processing", "completed"]
    assert states[-1]["memory_id"] == "memory-a"


@pytest.mark.asyncio
async def test_jobs_run_fifo_one_at_a_time_and_failure_does_not_stop_worker(
    tmp_path: Path,
) -> None:
    active = 0
    maximum_active = 0
    calls: list[str] = []

    class Store:
        async def add_record(self, record, *, provenance_seq, context):
            nonlocal active, maximum_active
            del provenance_seq, context
            active += 1
            maximum_active = max(maximum_active, active)
            calls.append(record.subject.name)
            await asyncio.sleep(0)
            active -= 1
            if record.subject.name == "first":
                raise RuntimeError("first failed")
            return {"memory_id": f"memory-{record.subject.name}"}

    audit_path = tmp_path / "jobs.jsonl"
    queue = MemoryAddQueue(Store(), audit_path=audit_path)
    await queue.start()
    first = await queue.enqueue(
        record=_record("first"), provenance_seq=1, context=_context("request-1")
    )
    second = await queue.enqueue(
        record=_record("second"), provenance_seq=2, context=_context("request-2")
    )

    await queue.wait_idle()
    await queue.aclose()

    assert first.job_id != second.job_id
    assert calls == ["first", "second"]
    assert maximum_active == 1
    terminal = {
        item["job_id"]: item
        for item in _states(audit_path)
        if item["status"] in {"completed", "failed"}
    }
    assert terminal[first.job_id]["status"] == "failed"
    assert terminal[first.job_id]["error"] == "RuntimeError: first failed"
    assert terminal[second.job_id]["status"] == "completed"


@pytest.mark.asyncio
async def test_structured_adds_and_session_work_share_one_fifo(tmp_path: Path) -> None:
    active = 0
    maximum_active = 0
    calls: list[str] = []

    async def enter(name: str) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        calls.append(name)
        await asyncio.sleep(0)
        active -= 1

    class Store:
        async def add_record(self, record, *, provenance_seq, context):
            del provenance_seq, context
            await enter(f"add:{record.subject.name}")
            return {"memory_id": f"memory-{record.subject.name}"}

    audit_path = tmp_path / "jobs.jsonl"
    queue = MemoryAddQueue(Store(), audit_path=audit_path)
    await queue.start()
    await queue.enqueue(
        record=_record("before"), provenance_seq=1, context=_context("request-before")
    )
    receipt = queue.enqueue_work(
        job_type="session_finalization",
        session_id="session-a",
        work=lambda: enter("finalize:session-a"),
    )
    await queue.enqueue(
        record=_record("after"), provenance_seq=2, context=_context("request-after")
    )

    await queue.wait_idle()
    await queue.aclose()

    assert receipt.status == "accepted"
    assert calls == ["add:before", "finalize:session-a", "add:after"]
    assert maximum_active == 1
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    work_states = [
        row["payload"]["status"]
        for row in rows
        if row["event"] == "memory_work_job"
    ]
    assert work_states == ["queued", "processing", "completed"]


@pytest.mark.asyncio
async def test_failed_session_work_does_not_stop_later_add(tmp_path: Path) -> None:
    calls: list[str] = []

    class Store:
        async def add_record(self, record, *, provenance_seq, context):
            del provenance_seq, context
            calls.append(f"add:{record.subject.name}")
            return {"memory_id": f"memory-{record.subject.name}"}

    async def fail_finalization() -> None:
        calls.append("finalize:failed")
        raise RuntimeError("finalization failed")

    audit_path = tmp_path / "jobs.jsonl"
    queue = MemoryAddQueue(Store(), audit_path=audit_path)
    await queue.start()
    receipt = queue.enqueue_work(
        job_type="session_finalization",
        session_id="session-failed",
        work=fail_finalization,
    )
    await queue.enqueue(
        record=_record("after-failure"),
        provenance_seq=1,
        context=_context("request-after-failure"),
    )

    await queue.wait_idle()
    await queue.aclose()

    assert calls == ["finalize:failed", "add:after-failure"]
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    failed = [
        row["payload"]
        for row in rows
        if row["event"] == "memory_work_job"
        and row["payload"]["status"] == "failed"
    ]
    assert failed == [
        {
            "duration_ms": failed[0]["duration_ms"],
            "error": "RuntimeError: finalization failed",
            "job_id": receipt.job_id,
            "job_type": "session_finalization",
            "session_id": "session-failed",
            "status": "failed",
        }
    ]


@pytest.mark.asyncio
async def test_close_waits_for_queued_session_work(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def finalize_session() -> None:
        entered.set()
        await release.wait()
        completed.set()

    queue = MemoryAddQueue(SimpleNamespace(), audit_path=tmp_path / "jobs.jsonl")
    await queue.start()
    queue.enqueue_work(
        job_type="session_finalization",
        session_id="session-close",
        work=finalize_session,
    )

    close_task = asyncio.create_task(queue.aclose())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not close_task.done()
    release.set()
    await asyncio.wait_for(close_task, timeout=1)

    assert completed.is_set()


@pytest.mark.asyncio
async def test_accepted_job_outlives_submitting_task_cancellation(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class Store:
        async def add_record(self, record, *, provenance_seq, context):
            del record, provenance_seq, context
            started.set()
            await release.wait()
            completed.set()
            return {"memory_id": "memory-after-cancel"}

    queue = MemoryAddQueue(Store(), audit_path=tmp_path / "jobs.jsonl")
    await queue.start()
    accepted = asyncio.Event()
    receipts = []

    async def submit():
        receipts.append(
            await queue.enqueue(
                record=_record("cancelled-submitter"),
                provenance_seq=1,
                context=_context("request-cancelled"),
                run_id="run-cancelled",
            )
        )
        accepted.set()
        await asyncio.Event().wait()

    submitter = asyncio.create_task(submit())
    await accepted.wait()
    await started.wait()
    submitter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submitter
    release.set()
    await queue.aclose()

    assert receipts[0].job_id
    assert completed.is_set()


@pytest.mark.asyncio
async def test_closed_queue_rejects_new_jobs(tmp_path: Path) -> None:
    queue = MemoryAddQueue(SimpleNamespace(), audit_path=tmp_path / "jobs.jsonl")
    await queue.start()
    await queue.aclose()

    with pytest.raises(MemoryAddQueueClosed):
        await queue.enqueue(
            record=_record("late"), provenance_seq=1, context=_context("request-late")
        )
