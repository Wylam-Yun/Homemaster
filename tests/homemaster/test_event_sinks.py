"""Tests for runtime event sinks."""

from __future__ import annotations

import json

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sinks import ConsoleEventSink, JsonlTraceSink, MessagesLogSink


def test_jsonl_trace_sink_redacts_without_truncating(tmp_path) -> None:
    sink = JsonlTraceSink(tmp_path)
    long_prompt = "x" * 500
    sink.emit(RuntimeEvent(
        type="assistant.reply",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"reply": long_prompt, "api_key": "secret"},
    ))
    sink.close()

    entry = json.loads((tmp_path / "runtime_events.jsonl").read_text())

    assert entry["payload"]["reply"] == long_prompt
    assert entry["payload"]["api_key"] == "[REDACTED]"


def test_messages_log_sink_writes_user_and_assistant_messages(tmp_path) -> None:
    sink = MessagesLogSink(tmp_path)
    sink.emit(RuntimeEvent(
        type="runtime.turn_started",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"user_text": "hello"},
    ))
    sink.emit(RuntimeEvent(
        type="assistant.reply",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"reply": "hi"},
    ))

    rows = [
        json.loads(line)
        for line in (tmp_path / "messages.jsonl").read_text().splitlines()
    ]

    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]


def test_console_event_sink_renders_human_labels(capsys) -> None:
    sink = ConsoleEventSink()

    sink.emit(RuntimeEvent(
        type="runtime.turn_started",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"user_text": "hello"},
    ))
    sink.emit(RuntimeEvent(
        type="transport.request_started",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={},
    ))
    sink.emit(RuntimeEvent(
        type="assistant.thinking",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"thinking": "先找苹果\n再拿起"},
    ))
    sink.emit(RuntimeEvent(
        type="tool.call_started",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"arguments": {"object": "苹果"}},
        name="robot_observe",
    ))
    sink.emit(RuntimeEvent(
        type="tool.call_completed",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"result": '{"visible": true}'},
        name="robot_observe",
        duration_ms=12,
    ))
    sink.emit(RuntimeEvent(
        type="assistant.reply",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"reply": "苹果拿好了。"},
    ))

    stderr = capsys.readouterr().err

    assert "runtime.turn_started" not in stderr
    assert "transport.request_started" not in stderr
    assert "[模型思考] 先找苹果" in stderr
    assert '[工具调用] robot_observe: {"object": "苹果"}' in stderr
    assert '[工具结果] robot_observe (12ms): {"visible": true}' in stderr
    assert "[模型回复] 苹果拿好了。" in stderr


def test_console_event_sink_can_hide_assistant_replies(capsys) -> None:
    sink = ConsoleEventSink(show_replies=False)

    sink.emit(RuntimeEvent(
        type="assistant.reply",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"reply": "苹果拿好了。"},
    ))

    assert capsys.readouterr().err == ""


def test_console_event_sink_hides_internal_noise_but_shows_compaction(capsys) -> None:
    sink = ConsoleEventSink()

    for event_type in (
        "runtime.turn_completed",
        "transport.response_completed",
        "usage.update",
    ):
        sink.emit(RuntimeEvent(
            type=event_type,
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"total_tokens": 123, "final_reply": "done"},
        ))
    sink.emit(RuntimeEvent(
        type="context.compaction",
        session_id="s1",
        run_id="r1",
        turn_index=0,
        payload={"trigger": "manual", "kind": "manual_summary", "after_tokens": 42},
    ))

    stderr = capsys.readouterr().err

    assert "runtime.turn_completed" not in stderr
    assert "transport.response_completed" not in stderr
    assert "usage.update" not in stderr
    assert "[上下文压缩] trigger=manual, kind=manual_summary, after_tokens=42" in stderr
