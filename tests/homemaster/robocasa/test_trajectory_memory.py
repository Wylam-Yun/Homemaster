"""Regression tests for the RoboCasa trajectory memory writer."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.robocasa.trajectory_memory import (
    RoboCasaFinalEnvironmentState,
    RoboCasaTrajectoryRecord,
    RoboCasaTrajectoryWriter,
)


def _record() -> RoboCasaTrajectoryRecord:
    return RoboCasaTrajectoryRecord(
        trajectory_id="traj-1",
        source_session_id="sess-1",
        run_id="run-1",
        episode_id="ep-1",
        task_name="OpenDrawer",
        split="target",
        seed=1,
        task="Open the left drawer.",
        outcome="failure",
        classification="agent_model_failure",
        failure_reason="deadline_exceeded",
        source_trace_path="/tmp/trace.jsonl",
        source_trace_sha256="0" * 64,
        final_environment_state=RoboCasaFinalEnvironmentState(
            success=False, step_index=5, invalid_action_count=0
        ),
    )


class _StrictSink:
    """Mimics the event bus contract: rejects non-RuntimeEvent payloads."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def emit(self, event: Any) -> None:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("event bus accepts RuntimeEvent values")
        self.events.append(event)


class _FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    @property
    def started(self) -> bool:
        return True

    def enqueue_work(self, *, job_type: str, session_id: str, work: Any) -> Any:
        self.jobs.append({"job_type": job_type, "session_id": session_id})
        return SimpleNamespace(job_id="job-1")


def test_enqueue_emits_runtime_events() -> None:
    sink = _StrictSink()
    queue = _FakeQueue()
    writer = RoboCasaTrajectoryWriter(
        mindmemos=SimpleNamespace(),
        queue=queue,
        event_sink=sink,
    )
    writer.enqueue(_record())
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.type == "memory.trajectory.queued"
    assert event.session_id == "sess-1"
    assert event.payload["trajectory_id"] == "traj-1"
    assert queue.jobs == [{"job_type": "robocasa_trajectory_memory", "session_id": "sess-1"}]
