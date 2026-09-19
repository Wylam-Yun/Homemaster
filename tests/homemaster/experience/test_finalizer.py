from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.experience import SessionFinalizer


class RecordingEventSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class FakeMindMemOS:
    def __init__(self) -> None:
        self.calls = []

    async def add_schema_episode(self, episode, context, *, metadata):
        self.calls.append((episode, context, metadata))
        type_result = lambda **values: SimpleNamespace(
            model_dump=lambda **_kwargs: values,
            **values,
        )
        return SimpleNamespace(
            add_record_id="add-record-1",
            result=SimpleNamespace(
                status="ok",
                schema_episode=SimpleNamespace(
                    episode_id="episode-1",
                    write_status="completed",
                    types={
                        "object_location": type_result(
                            status="completed",
                            candidate_count=1,
                            memory_ids=["memory-1"],
                            memory_count=1,
                            error=None,
                            outcome_counts={},
                            executable_count=0,
                        ),
                        "search_observation": type_result(
                            status="not_detected",
                            candidate_count=0,
                            memory_ids=[],
                            memory_count=0,
                            error=None,
                            outcome_counts={},
                            executable_count=0,
                        ),
                        "task_procedure": type_result(
                            status="not_detected",
                            candidate_count=0,
                            memory_ids=[],
                            memory_count=0,
                            error=None,
                            outcome_counts={},
                            executable_count=0,
                        ),
                    },
                ),
                memories=[
                    SimpleNamespace(
                        operation="add",
                        memory_id="memory-1",
                        mem_type="fact",
                        content=json.dumps(
                            {
                                "type": "object_location",
                                "summary": "服务状态已观察。",
                            },
                            ensure_ascii=False,
                        ),
                        related_memory_ids=[],
                    )
                ],
            ),
        )

    async def get_raw(self, memory_id, context):
        del context
        return SimpleNamespace(
            memory_id=memory_id,
            status="active",
            mem_type="fact",
            content=json.dumps(
                {"type": "object_location", "summary": "服务状态已观察。"},
                ensure_ascii=False,
            ),
        )

    async def feedback_implicit(self, context):
        del context
        return SimpleNamespace(status="ok", message=None, actions=[])


def test_finalizer_rejects_memory_implementation_without_schema_episode_contract(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="add_schema_episode"):
        SessionFinalizer(
            trace_path=tmp_path / "runtime_events.jsonl",
            data_root=tmp_path / "memory",
            mindmemos=object(),
        )


def _write_events(path: Path) -> None:
    events = [
        {
            "type": "runtime.turn_started",
            "session_id": "s1",
            "run_id": "r1",
            "turn_index": 1,
            "timestamp": "2026-08-12T10:00:00Z",
            "payload": {"user_text": "检查服务"},
        },
        {
            "type": "transport.delta",
            "session_id": "s1",
            "run_id": "r1",
            "turn_index": 1,
            "timestamp": "2026-08-12T10:00:01Z",
            "payload": {"text": "碎片"},
        },
        {
            "type": "assistant.reply",
            "session_id": "s2",
            "run_id": "r2",
            "turn_index": 1,
            "timestamp": "2026-08-12T10:00:02Z",
            "payload": {"reply": "其他会话"},
        },
        {
            "type": "assistant.reply",
            "session_id": "s1",
            "run_id": "r1",
            "turn_index": 1,
            "timestamp": "2026-08-12T10:00:03Z",
            "payload": {"reply": "已恢复"},
        },
    ]
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
        encoding="utf-8",
    )


def _write_semantic_events(path: Path) -> None:
    common = {
        "session_id": "s1",
        "run_id": "SECRET-RUN-ID",
        "turn_index": 1,
        "timestamp": "2026-08-12T10:00:00Z",
        "event_id": "SECRET-EVENT-ID",
    }
    events = [
        {**common, "type": "runtime.turn_started", "payload": {"user_text": "修复 JSON"}},
        {**common, "type": "transport.request_started", "payload": {"model": "SECRET-MODEL"}},
        {**common, "type": "assistant.thinking", "payload": {"thinking": "先验证失败原因"}},
        {
            **common,
            "type": "assistant.reply",
            "payload": {
                "reply": "",
                "tool_calls": [{"id": "SECRET-CALL-ID", "name": "terminal"}],
                "usage": {"input_tokens": 999},
            },
        },
        {
            **common,
            "type": "tool.call_started",
            "name": "terminal",
            "tool_call_id": "SECRET-CALL-ID",
            "payload": {"arguments": {"command": "python parse.py"}},
        },
        {
            **common,
            "type": "tool.call_failed",
            "name": "terminal",
            "tool_call_id": "SECRET-CALL-ID",
            "payload": {
                "args": {"command": "python parse.py"},
                "result": "JSONDecodeError: missing brace",
                "data": {"status": "failed", "returncode": 1},
            },
        },
        {**common, "type": "assistant.reply", "payload": {"reply": "已经修复", "tool_calls": []}},
        {
            **common,
            "type": "runtime.turn_completed",
            "payload": {"final_reply": "已经修复", "duration_ms": 10},
        },
        {**common, "type": "usage.update", "payload": {"total_tokens": 999}},
    ]
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_finalizer_submits_normalized_episode_without_internal_metadata(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_semantic_events(trace)
    mindmemos = FakeMindMemOS()

    result = await SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=mindmemos,
    ).finalize("s1", "user_exit")

    episode, _, metadata = mindmemos.calls[0]
    serialized = json.dumps(episode, ensure_ascii=False)
    assert episode["schema_version"] == "homemaster.schema_episode.v1"
    assert episode["tool_steps"][0]["tool"] == "terminal"
    assert episode["tool_steps"][0]["arguments"] == {"command": "python parse.py"}
    assert episode["tool_steps"][0]["result"] == "JSONDecodeError: missing brace"
    assert episode["tool_steps"][0]["source_event_ids"] == [
        "SECRET-EVENT-ID",
        "SECRET-EVENT-ID",
    ]
    assert "SECRET-CALL-ID" in serialized
    assert "SECRET-MODEL" not in serialized
    assert "先验证失败原因" not in serialized
    assert "input_tokens" not in serialized
    assert metadata["source_type"] == "homemaster_schema_episode"
    assert "input_hash" in metadata
    assert metadata["domain_schema_version"] == "homemaster.schema_episode.v1"
    assert result.rendered_messages == 4
    assert not list((tmp_path / "memory" / "experience_jobs").glob("*/task_trace.json"))


@pytest.mark.asyncio
async def test_finalizer_collects_session_and_persists_schema_result(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_events(trace)
    mindmemos = FakeMindMemOS()
    finalizer = SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=mindmemos,
        memory_tenant_id="Caroline",
    )

    result = await finalizer.finalize("s1", "user_exit")

    assert result.status == "completed"
    assert result.collected_events == 2
    assert result.excluded_transport_deltas == 1
    assert result.operations[0].memory_id == "memory-1"
    assert result.rendered_messages == 1
    assert not list((tmp_path / "memory" / "experience_jobs").glob("*/task_trace.json"))
    assert len(mindmemos.calls) == 1
    episode, context, _ = mindmemos.calls[0]
    assert context.account_id == "Caroline"
    assert context.project_id == "Caroline"
    assert context.user_id == "Caroline"
    assert [event["timestamp"] for event in episode["events"]] == [
        "2026-08-12T10:00:00Z",
    ]

    jobs = list((tmp_path / "memory" / "experience_jobs").glob("*/job.json"))
    job = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert job["schema_version"] == 3
    assert job["add"]["status"] == "completed"
    assert job["add"]["algorithm"] == "schema_add_v1"
    assert job["add"]["types"]["object_location"]["memory_ids"] == ["memory-1"]
    assert job["add"]["types"]["search_observation"]["status"] == "not_detected"
    assert job["implicit_feedback"]["status"] == "completed"

    repeated = await finalizer.finalize("s1", "user_exit")
    assert repeated.status == "already_completed"
    assert len(mindmemos.calls) == 1


@pytest.mark.asyncio
async def test_finalizer_failure_does_not_raise(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_events(trace)

    class FailingMindMemOS:
        async def add_schema_episode(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    result = await SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=FailingMindMemOS(),
    ).finalize("s1", "eof")

    assert result.status == "failed"
    assert "provider unavailable" in result.error


@pytest.mark.asyncio
async def test_finalizer_retries_implicit_without_repeating_add(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_events(trace)

    class RetryMindMemOS(FakeMindMemOS):
        def __init__(self) -> None:
            super().__init__()
            self.implicit_calls = 0

        async def feedback_implicit(self, context):
            del context
            self.implicit_calls += 1
            if self.implicit_calls == 1:
                return SimpleNamespace(
                    status="error", message="provider rejected", actions=[]
                )
            return SimpleNamespace(status="ok", message=None, actions=[])

    mindmemos = RetryMindMemOS()
    event_sink = RecordingEventSink()
    finalizer = SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=mindmemos,
        event_sink=event_sink,
    )

    failed = await finalizer.finalize("s1", "user_exit")
    completed = await finalizer.finalize("s1", "user_exit")

    assert failed.status == "failed"
    assert completed.status == "completed"
    assert len(mindmemos.calls) == 1
    assert mindmemos.implicit_calls == 2
    assert [event.type for event in event_sink.events] == [
        "memory.feedback.implicit.started",
        "memory.feedback.implicit.failed",
        "memory.feedback.implicit.started",
        "memory.feedback.implicit.completed",
    ]
    jobs = list((tmp_path / "memory" / "experience_jobs").glob("*/job.json"))
    job = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert job["status"] == "completed"
    assert job["implicit_feedback"]["status"] == "completed"


@pytest.mark.asyncio
async def test_finalizer_persists_failed_phase_and_typed_event(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_events(trace)

    class FailingImplicit(FakeMindMemOS):
        async def feedback_implicit(self, context):
            del context
            return SimpleNamespace(
                status="error", message="planner rejected", actions=[]
            )

    event_sink = RecordingEventSink()
    result = await SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=FailingImplicit(),
        event_sink=event_sink,
    ).finalize("s1", "user_exit")

    assert result.status == "failed"
    jobs = list((tmp_path / "memory" / "experience_jobs").glob("*/job.json"))
    job = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert job["status"] == "failed"
    assert job["failed_phase"] == "implicit_feedback"
    assert job["implicit_feedback"]["status"] == "failed"
    assert "planner rejected" in job["implicit_feedback"]["error"]
    failed_event = event_sink.events[-1]
    assert failed_event.type == "memory.feedback.implicit.failed"
    assert failed_event.session_id == "s1"
    assert failed_event.payload["project_id"] == "local"
    assert failed_event.payload["user_id"] == "local"
    assert "planner rejected" in failed_event.payload["error"]
