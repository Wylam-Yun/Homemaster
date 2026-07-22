"""Tests for runtime event sinks."""

from __future__ import annotations

import importlib
import importlib.util
import json
from io import StringIO

from rich.console import Console
from rich.markdown import Markdown

from homemaster.agent.messages import AssistantMessage, ContentBlock
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sinks import ConsoleEventSink, JsonlTraceSink, MessagesLogSink
from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)


def _load_module(name: str):
    assert importlib.util.find_spec(name) is not None
    return importlib.import_module(name)


class _FakeLive:
    instances = []

    def __init__(self, renderable, **kwargs) -> None:
        self.renderable = renderable
        self.kwargs = kwargs
        self.updates = []
        self.started = 0
        self.stopped = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def update(self, renderable, *, refresh: bool = False) -> None:
        self.renderable = renderable
        self.updates.append((renderable, refresh))

    def stop(self) -> None:
        self.stopped += 1


class _FakeStatus:
    instances = []

    def __init__(self, message: str) -> None:
        self.message = message
        self.started = 0
        self.stopped = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def test_jsonl_trace_sink_redacts_without_truncating(tmp_path) -> None:
    sink = JsonlTraceSink(tmp_path)
    long_prompt = "x" * 500
    sink.emit(
        RuntimeEvent(
            type="assistant.reply",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"reply": long_prompt, "api_key": "secret"},
        )
    )
    sink.close()

    entry = json.loads((tmp_path / "runtime_events.jsonl").read_text())

    assert entry["payload"]["reply"] == long_prompt
    assert entry["payload"]["api_key"] == "[REDACTED]"


def test_messages_log_sink_writes_user_and_assistant_messages(tmp_path) -> None:
    sink = MessagesLogSink(tmp_path)
    sink.emit(
        RuntimeEvent(
            type="runtime.turn_started",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"user_text": "hello"},
        )
    )
    sink.emit(
        RuntimeEvent(
            type="assistant.reply",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"reply": "hi"},
        )
    )

    rows = [json.loads(line) for line in (tmp_path / "messages.jsonl").read_text().splitlines()]

    assert [(row["role"], row["content"]) for row in rows] == [
        ("user", "hello"),
        ("assistant", "hi"),
    ]


def test_console_event_sink_renders_human_labels(capsys) -> None:
    sink = ConsoleEventSink()

    sink.emit(
        RuntimeEvent(
            type="runtime.turn_started",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"user_text": "hello"},
        )
    )
    sink.emit(
        RuntimeEvent(
            type="transport.request_started",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={},
        )
    )
    sink.emit(
        RuntimeEvent(
            type="assistant.thinking",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"thinking": "先找苹果\n再拿起"},
        )
    )
    sink.emit(
        RuntimeEvent(
            type="tool.call_started",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"arguments": {"object": "苹果"}},
            name="robot_observe",
        )
    )
    sink.emit(
        RuntimeEvent(
            type="tool.call_completed",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"result": '{"visible": true}'},
            name="robot_observe",
            duration_ms=12,
        )
    )
    sink.emit(
        RuntimeEvent(
            type="assistant.reply",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"reply": "苹果拿好了。"},
        )
    )

    stderr = capsys.readouterr().err

    assert "runtime.turn_started" not in stderr
    assert "transport.request_started" not in stderr
    assert "[模型思考] 先找苹果" in stderr
    assert '[工具调用] robot_observe: {"object": "苹果"}' in stderr
    assert '[工具结果] robot_observe (12ms): {"visible": true}' in stderr
    assert "[模型回复] 苹果拿好了。" in stderr


def test_console_event_sink_can_hide_assistant_replies(capsys) -> None:
    sink = ConsoleEventSink(show_replies=False)

    sink.emit(
        RuntimeEvent(
            type="assistant.reply",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"reply": "苹果拿好了。"},
        )
    )

    assert capsys.readouterr().err == ""


def test_console_event_sink_hides_internal_noise_but_shows_compaction(capsys) -> None:
    sink = ConsoleEventSink()

    for event_type in (
        "runtime.turn_completed",
        "transport.response_completed",
        "usage.update",
    ):
        sink.emit(
            RuntimeEvent(
                type=event_type,
                session_id="s1",
                run_id="r1",
                turn_index=0,
                payload={"total_tokens": 123, "final_reply": "done"},
            )
        )
    sink.emit(
        RuntimeEvent(
            type="context.compaction",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"trigger": "manual", "kind": "manual_summary", "after_tokens": 42},
        )
    )

    stderr = capsys.readouterr().err

    assert "runtime.turn_completed" not in stderr
    assert "transport.response_completed" not in stderr
    assert "usage.update" not in stderr
    assert "[上下文压缩] trigger=manual, kind=manual_summary, after_tokens=42" in stderr


def test_rich_renderer_uses_one_live_region_then_replaces_it_with_final_markdown() -> None:
    module = _load_module("homemaster.cli.rich_renderer")
    _FakeLive.instances.clear()
    _FakeStatus.instances.clear()
    output = StringIO()
    renderer = module.RichOutputRenderer(
        console=Console(file=output, force_terminal=False, color_system=None),
        live_factory=_FakeLive,
        status_factory=_FakeStatus,
    )

    renderer.model_request_started()
    renderer.render(AssistantTextDelta(text="**hel"))
    renderer.render(AssistantTextDelta(text="lo**"))
    renderer.render(
        AssistantTurnComplete(
            message=AssistantMessage(content=[ContentBlock(text="**hello**")]),
            usage={"output_tokens": 2},
        )
    )

    assert renderer.state == "idle"
    assert len(_FakeLive.instances) == 1
    live = _FakeLive.instances[0]
    assert live.started == live.stopped == 1
    assert isinstance(live.renderable, Markdown)
    assert _FakeStatus.instances[0].started == _FakeStatus.instances[0].stopped == 1


def test_rich_renderer_pairs_same_name_tools_fifo_and_cleans_every_active_state() -> None:
    module = _load_module("homemaster.cli.rich_renderer")
    _FakeLive.instances.clear()
    _FakeStatus.instances.clear()
    output = StringIO()
    renderer = module.RichOutputRenderer(
        console=Console(file=output, force_terminal=False, color_system=None),
        live_factory=_FakeLive,
        status_factory=_FakeStatus,
    )

    renderer.render(ToolExecutionStarted("lookup", {"query": "first"}))
    renderer.render(ToolExecutionStarted("lookup", {"query": "second"}))
    renderer.render(ToolExecutionCompleted("lookup", "one"))
    renderer.render(ToolExecutionCompleted("lookup", "two", is_error=True))
    renderer.close()

    rendered = output.getvalue()
    assert rendered.index("query=first") < rendered.index("query=second")
    assert "one" in rendered
    assert "two" in rendered
    assert renderer.state == "closed"
    assert all(status.stopped == 1 for status in _FakeStatus.instances)


def test_rich_renderer_error_status_and_close_are_idempotent() -> None:
    module = _load_module("homemaster.cli.rich_renderer")
    output = StringIO()
    renderer = module.RichOutputRenderer(
        console=Console(file=output, force_terminal=False, color_system=None),
        live_factory=_FakeLive,
        status_factory=_FakeStatus,
    )

    renderer.model_request_started()
    renderer.render(StatusEvent("retrying"))
    renderer.render(ErrorEvent("failed", recoverable=False))
    renderer.close()
    renderer.close()

    assert "retrying" in output.getvalue()
    assert "failed" in output.getvalue()
    assert renderer.state == "closed"


class _FlushBuffer(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def test_text_and_stream_json_sinks_flush_live_events_without_duplicate_completion() -> None:
    module = _load_module("homemaster.cli.live_output")
    text_output = _FlushBuffer()
    json_output = _FlushBuffer()
    text_sink = module.TextStreamEventSink(file=text_output)
    json_sink = module.StreamJsonEventSink(file=json_output)
    events = [
        RuntimeEvent(
            type="transport.delta",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={"text_delta": "hello"},
        ),
        RuntimeEvent(
            type="assistant.reply",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            payload={
                "reply": "hello",
                "finish_reason": "stop",
                "usage": {"output_tokens": 1},
                "tool_calls": [],
            },
        ),
    ]

    for event in events:
        text_sink.emit(event)
        json_sink.emit(event)

    rows = [json.loads(line) for line in json_output.getvalue().splitlines()]
    assert text_output.getvalue() == "hello"
    assert text_output.flush_count == 1
    assert rows == [
        {"type": "assistant_delta", "text": "hello"},
        {
            "type": "assistant_complete",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
                "tool_calls": [],
                "finish_reason": "stop",
            },
            "usage": {"output_tokens": 1},
        },
    ]
    assert json_output.flush_count == 2


def test_stream_json_sink_redacts_configured_secret_under_innocuous_key() -> None:
    module = _load_module("homemaster.cli.live_output")
    output = _FlushBuffer()
    sink = module.StreamJsonEventSink(
        file=output,
        sensitive_values=("configured-secret",),
    )

    sink.emit(
        RuntimeEvent(
            type="tool.call_started",
            session_id="s1",
            run_id="r1",
            turn_index=0,
            name="lookup",
            payload={"arguments": {"query": "configured-secret"}},
        )
    )

    assert "configured-secret" not in output.getvalue()
    assert json.loads(output.getvalue()) == {
        "type": "tool_started",
        "tool_name": "lookup",
        "tool_input": {"query": "[REDACTED]"},
    }
