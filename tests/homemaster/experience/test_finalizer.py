from __future__ import annotations

import json
from datetime import datetime, timezone
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

    async def add_vanilla(self, messages, context, *, metadata):
        self.calls.append((messages, context, metadata))
        return SimpleNamespace(
            add_record_id="add-record-1",
            result=SimpleNamespace(
                status="ok",
                memories=[
                    SimpleNamespace(
                        operation="add",
                        memory_id="memory-1",
                        mem_type="experience",
                        content="启动服务后再次检查终态。",
                        related_memory_ids=[],
                    )
                ],
            ),
        )

    async def get_raw(self, memory_id, context):
        del context
        return SimpleNamespace(memory_id=memory_id, status="active")

    async def feedback_implicit(self, context):
        del context
        return SimpleNamespace(status="ok", message=None, actions=[])


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
async def test_finalizer_renders_selected_dialogue_without_internal_ids(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_semantic_events(trace)
    mindmemos = FakeMindMemOS()

    result = await SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=mindmemos,
    ).finalize("s1", "user_exit")

    messages, _, metadata = mindmemos.calls[0]
    rendered = "\n".join(message.content for message in messages)
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "system",
    ]
    assert messages[0].content == "修复 JSON"
    assert messages[1].content == "[thinking]\n先验证失败原因"
    assert "terminal" in messages[2].content
    assert "python parse.py" in messages[2].content
    assert "JSONDecodeError: missing brace" in messages[2].content
    assert messages[3].content == "已经修复"
    assert messages[4].content == "Session ended: user_exit"
    assert "SECRET-" not in rendered
    assert "SECRET-MODEL" not in rendered
    assert "input_tokens" not in rendered
    assert metadata["source_type"] == "homemaster_session_experience"
    assert "input_hash" in metadata
    assert "trace_hash" not in metadata
    assert result.rendered_messages == 5
    assert not list((tmp_path / "memory" / "experience_jobs").glob("*/task_trace.json"))


@pytest.mark.asyncio
async def test_finalizer_collects_session_and_persists_vanilla_result(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_events(trace)
    mindmemos = FakeMindMemOS()
    finalizer = SessionFinalizer(
        trace_path=trace,
        data_root=tmp_path / "memory",
        mindmemos=mindmemos,
    )

    result = await finalizer.finalize("s1", "user_exit")

    assert result.status == "completed"
    assert result.collected_events == 2
    assert result.excluded_transport_deltas == 1
    assert result.operations[0].memory_id == "memory-1"
    assert result.rendered_messages == 3
    assert not list((tmp_path / "memory" / "experience_jobs").glob("*/task_trace.json"))
    assert len(mindmemos.calls) == 1
    messages, _, _ = mindmemos.calls[0]
    assert [message.timestamp for message in messages[:2]] == [
        int(datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000),
        int(datetime(2026, 8, 12, 10, 0, 3, tzinfo=timezone.utc).timestamp() * 1000),
    ]

    jobs = list((tmp_path / "memory" / "experience_jobs").glob("*/job.json"))
    job = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert job["schema_version"] == 2
    assert job["add"]["status"] == "completed"
    assert job["implicit_feedback"]["status"] == "completed"

    repeated = await finalizer.finalize("s1", "user_exit")
    assert repeated.status == "already_completed"
    assert len(mindmemos.calls) == 1


@pytest.mark.asyncio
async def test_finalizer_failure_does_not_raise(tmp_path: Path) -> None:
    trace = tmp_path / "runtime_events.jsonl"
    _write_events(trace)

    class FailingMindMemOS:
        async def add_vanilla(self, *args, **kwargs):
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
