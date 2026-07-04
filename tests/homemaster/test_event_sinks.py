"""Tests for runtime event sinks."""

from __future__ import annotations

import json

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sinks import JsonlTraceSink, MessagesLogSink


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
