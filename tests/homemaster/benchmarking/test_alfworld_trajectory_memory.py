from __future__ import annotations

from types import SimpleNamespace

import pytest

from homemaster.benchmarking.alfworld.trajectory_memory import (
    AlfworldFinalEnvironmentState,
    AlfworldTrajectoryRecord,
    AlfworldTrajectoryWriter,
    determine_outcome,
    provider_projection,
    trajectory_sha256,
)


def _record(**updates: object) -> AlfworldTrajectoryRecord:
    payload: dict[str, object] = {
        "trajectory_id": "traj-1",
        "source_session_id": "session-1",
        "run_id": "run-1",
        "episode_id": "episode-1",
        "task": "put mug in cabinet",
        "goal_type": "pick_and_place_simple",
        "outcome": "success",
        "classification": "agent_success",
        "source_trace_path": "/private/trace.jsonl",
        "source_trace_sha256": "a" * 64,
        "final_environment_state": {
            "won": True,
            "done": True,
            "step_index": 3,
            "invalid_action_count": 0,
            "goal_condition_success_rate": 1.0,
            "inventory": ["mug 1"],
        },
    }
    payload.update(updates)
    return AlfworldTrajectoryRecord.model_validate(payload)


@pytest.mark.parametrize(
    ("state", "kwargs", "expected"),
    [
        ({"won": True}, {}, ("success", None)),
        ({"won": False, "failure_reason": "not_won"}, {}, ("failure", "not_won")),
        ({"won": None}, {}, ("unknown", "execution_state_uncertain")),
        ({"won": False}, {"infrastructure_failure": True}, ("unknown", "runtime_failure")),
        ({"won": True}, {"state_contradictory": True}, ("unknown", "execution_state_uncertain")),
    ],
)
def test_determine_outcome_has_explicit_three_state_rule(state, kwargs, expected) -> None:
    assert determine_outcome(state, **kwargs) == expected


def test_success_cannot_be_claimed_without_authoritative_won() -> None:
    with pytest.raises(ValueError, match="won=true"):
        _record(
            outcome="success",
            final_environment_state=AlfworldFinalEnvironmentState(won=None, done=True),
        )


def test_failure_and_unknown_require_reasons() -> None:
    with pytest.raises(ValueError, match="failure_reason"):
        _record(
            outcome="failure",
            classification="agent_model_failure",
            failure_reason=None,
            final_environment_state={"won": False, "done": True},
        )
    with pytest.raises(ValueError, match="failure_reason"):
        _record(
            outcome="unknown",
            classification="runtime_failure",
            failure_reason=None,
            final_environment_state={"won": None, "done": None},
        )


def test_hash_is_stable_and_projection_is_provider_safe() -> None:
    record = _record(
        steps=(
            {
                "index": 0,
                "payload": {"action": "take", "objectId": "Mug|1", "pose": {"x": 1}},
            },
        )
    )
    assert trajectory_sha256(record) == trajectory_sha256(record)
    projected = provider_projection(record)
    text = str(projected).casefold()
    assert "objectid" not in text
    assert "source_trace_path" not in projected
    assert projected["content_label"] == {
        "domain": "ALFWorld 执行轨迹",
        "result": "成功",
        "executable": False,
    }


@pytest.mark.asyncio
async def test_trajectory_writer_admits_queue_work_and_emits_verified_lifecycle(
    tmp_path,
) -> None:
    del tmp_path
    record = _record()
    events = []
    calls = []

    class Store:
        async def add_trajectory_memory(self, received, *, context):
            calls.append((received, context))
            return {
                "memory_id": "trajectory-memory-1",
                "readback_verified": True,
            }

    class Queue:
        def enqueue_work(self, *, job_type, session_id, work):
            assert job_type == "alfworld_trajectory_memory"
            assert session_id == record.source_session_id
            self.work = work
            return SimpleNamespace(job_id="job-1", status="accepted")

    class Sink:
        def emit(self, event):
            events.append(event)

    queue = Queue()
    writer = AlfworldTrajectoryWriter(Store(), queue, event_sink=Sink(), tenant_id="tenant-1")
    receipt = writer.enqueue(record)
    await queue.work()

    assert receipt.job_id == "job-1"
    assert [event.type for event in events] == [
        "memory.trajectory.queued",
        "memory.trajectory.stored",
        "memory.trajectory.readback_verified",
    ]
    assert calls[0][0] is record
    assert calls[0][1].account_id == "tenant-1"
    assert calls[0][1].session_id == record.source_session_id
    assert events[-1].payload["readback_status"] == "verified"
