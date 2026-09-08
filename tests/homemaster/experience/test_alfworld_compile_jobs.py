from __future__ import annotations

from types import SimpleNamespace

import pytest

from homemaster.benchmarking.alfworld.trajectory_memory import AlfworldTrajectoryRecord
from homemaster.experience.alfworld_compile_jobs import AlfworldCompileJobService


def _record(trace_path: str) -> AlfworldTrajectoryRecord:
    return AlfworldTrajectoryRecord.model_validate({
        "trajectory_id": "traj-1",
        "source_session_id": "session-1",
        "run_id": "run-1",
        "episode_id": "episode-1",
        "task": "put mug in cabinet",
        "goal_type": "pick_and_place_simple",
        "outcome": "success",
        "classification": "agent_success",
        "source_trace_path": trace_path,
        "source_trace_sha256": "a" * 64,
        "final_environment_state": {"won": True, "done": True},
        "steps": [{"index": 0, "payload": {"action": "navigate", "target": "cabinet"}}],
    })


class Queue:
    def enqueue_work(self, *, job_type, session_id, work):
        assert job_type == "alfworld_compile"
        assert session_id == "web-memory-management"
        self.work = work
        return SimpleNamespace(job_id="queue-job", status="accepted")


@pytest.mark.asyncio
async def test_compile_job_writes_succeeded_receipt_and_derived_memory(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text("source\n", encoding="utf-8")
    record = _record(str(trace))
    # The job verifies the trace hash independently, so use the actual hash.
    import hashlib
    record = record.model_copy(
        update={"source_trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest()}
    )
    raw = SimpleNamespace(
        status="active",
        metadata={
            "homemaster_memory_type": "trajectory",
            "record_json": record.model_dump_json(),
        },
    )
    calls = []

    class Store:
        async def get_raw(self, memory_id, context):
            assert memory_id == "source-memory"
            return raw
        async def add_derived_experience(self, content, **kwargs):
            calls.append((content, kwargs))
            return {"memory_id": "derived-memory", "readback_verified": True}

    events = []
    service = AlfworldCompileJobService(
        Store(),
        Queue(),
        jobs_root=tmp_path / "jobs",
        event_sink=SimpleNamespace(emit=events.append),
    )
    admitted = service.enqueue("source-memory")
    await service._queue.work()

    receipt = service.get(admitted["job_id"])
    assert receipt["status"] == "succeeded"
    assert receipt["derived_memory_id"] == "derived-memory"
    assert calls and calls[0][1]["source_memory_id"] == "source-memory"
    assert [event.type for event in events] == [
        "memory.experience.compile.queued",
        "memory.experience.compile.started",
        "memory.experience.compile.completed",
    ]


@pytest.mark.asyncio
async def test_compile_job_hash_tamper_fails_without_derived_write(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text("changed\n", encoding="utf-8")
    record = _record(str(trace))
    raw = SimpleNamespace(
        status="active",
        metadata={
            "homemaster_memory_type": "trajectory",
            "record_json": record.model_dump_json(),
        },
    )
    calls = []

    class Store:
        async def get_raw(self, memory_id, context):
            return raw
        async def add_derived_experience(self, content, **kwargs):
            calls.append((content, kwargs))
            return {"memory_id": "derived-memory", "readback_verified": True}

    service = AlfworldCompileJobService(
        Store(),
        Queue(),
        jobs_root=tmp_path / "jobs",
        event_sink=SimpleNamespace(emit=lambda event: None),
    )
    admitted = service.enqueue("source-memory")
    with pytest.raises(ValueError, match="hash mismatch"):
        await service._queue.work()
    receipt = service.get(admitted["job_id"])
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "ValueError"
    assert calls == []
