"""Tests for GenericAgentRuntime — the new message/tool-call/tool-result loop.

Uses fake transport and tool executor to verify runtime behavior without
importing home domain modules.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homemaster.agent.generic_runtime import GenericAgentRuntime, GenericRunResult
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
    ToolResultMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.providers.transport import LLMTransport, TransportDelta


class FakeTransport(LLMTransport):
    """Fake transport that returns pre-configured responses."""

    def __init__(self) -> None:
        self._responses: list[AssistantMessage] = []
        self._call_count = 0

    def queue_text(self, text: str, finish_reason: str = "stop") -> None:
        self._responses.append(
            AssistantMessage(
                content=[ContentBlock(text=text)],
                finish_reason=finish_reason,
            )
        )

    def queue_tool_call(
        self, name: str, arguments: dict[str, Any], call_id: str = "call_1"
    ) -> None:
        self._responses.append(
            AssistantMessage(
                content=[],
                tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                finish_reason="tool_calls",
            )
        )

    def queue_tool_calls(self, calls: list[tuple[str, str, dict[str, Any]]]) -> None:
        self._responses.append(
            AssistantMessage(
                content=[],
                tool_calls=[
                    ToolCall(id=cid, name=name, arguments=args)
                    for cid, name, args in calls
                ],
                finish_reason="tool_calls",
            )
        )

    def queue_repeating_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        """Queue a tool call that repeats forever (for budget tests)."""
        self._repeat_response = AssistantMessage(
            content=[],
            tool_calls=[ToolCall(id="call_repeat", name=name, arguments=arguments)],
            finish_reason="tool_calls",
        )

    @property
    def call_count(self) -> int:
        return self._call_count

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
        iteration: int | None = None,
    ) -> Iterator[TransportDelta]:
        msg = self._get_next_response()
        if msg.content:
            for block in msg.content:
                yield TransportDelta(type="transport.delta", text_delta=block.text)
        for tc in msg.tool_calls:
            yield TransportDelta(type="transport.delta", tool_call_delta=tc)
        yield TransportDelta(
            type="transport.delta",
            finish_reason=msg.finish_reason,
        )

    def _get_next_response(self) -> AssistantMessage:
        if hasattr(self, "_repeat_response"):
            self._call_count += 1
            return self._repeat_response
        if self._call_count < len(self._responses):
            msg = self._responses[self._call_count]
            self._call_count += 1
            return msg
        return AssistantMessage(
            content=[ContentBlock(text="no more responses")],
            finish_reason="stop",
        )


class FakeToolExecutor:
    """Fake tool executor that returns success results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        self.calls.append((name, arguments))
        return ToolResultMessage(
            tool_call_id="",  # Will be filled by runtime
            name=name,
            content=[ContentBlock(text='{"success": true}')],
        )

    def __call__(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        return self.dispatch(name, arguments)


class FailingToolExecutor:
    """Fake tool executor that always fails."""

    def __init__(self) -> None:
        self.call_count = 0

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        self.call_count += 1
        return ToolResultMessage(
            tool_call_id="",
            name=name,
            content=[ContentBlock(text='{"error": "tool failed"}')],
            is_error=True,
        )

    def __call__(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        return self.dispatch(name, arguments)


class MismatchedToolExecutor:
    """Fake tool executor that returns wrong tool_call_id."""

    def queue_result(self, tool_call_id: str, name: str) -> None:
        self._result = ToolResultMessage(
            tool_call_id=tool_call_id,
            name=name,
            content=[ContentBlock(text='{"ok": true}')],
        )

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        return self._result

    def __call__(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        return self.dispatch(name, arguments)


def _run(utterance: str, **kwargs: Any) -> GenericRunResult:
    transport = kwargs.pop("transport", FakeTransport())
    executor = kwargs.pop("dispatcher", FakeToolExecutor())
    max_iter = kwargs.pop("max_tool_iterations", 12)
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=executor,
        max_tool_iterations=max_iter,
    )
    session = AgentSession(session_id="test")
    return runtime.run(session, utterance, **kwargs)


# ---------------------------------------------------------------------------
# Greeting: no tools
# ---------------------------------------------------------------------------


def test_greeting_returns_text_without_tools() -> None:
    transport = FakeTransport()
    transport.queue_text("你好，我在。")
    result = _run("你好", transport=transport)
    assert result.final_reply == "你好，我在。"
    assert result.status == "replied"


# ---------------------------------------------------------------------------
# Tool failure appended as tool message
# ---------------------------------------------------------------------------


def test_tool_failure_is_appended_as_tool_message() -> None:
    transport = FakeTransport()
    transport.queue_tool_call("memory_retriever", {"query": "水杯"})
    transport.queue_text("我没有找到相关记忆。")
    executor = FailingToolExecutor()
    result = _run("帮我找水杯", transport=transport, dispatcher=executor)
    assert result.session.messages[-2].role == "tool"
    assert result.session.messages[-2].is_error is True
    assert result.final_reply == "我没有找到相关记忆。"


# ---------------------------------------------------------------------------
# Parallel tool calls preserved
# ---------------------------------------------------------------------------


def test_parallel_tool_call_ids_are_preserved() -> None:
    transport = FakeTransport()
    transport.queue_tool_calls([
        ("call_1", "memory_retriever", {"query": "水杯"}),
        ("call_2", "skill_view", {"skill": "fetch_object"}),
    ])
    transport.queue_text("我查到了两个结果。")
    result = _run("帮我拿水杯", transport=transport)
    tool_messages = [msg for msg in result.session.messages if msg.role == "tool"]
    assert [msg.tool_call_id for msg in tool_messages] == ["call_1", "call_2"]


# ---------------------------------------------------------------------------
# Stop on no tool calls
# ---------------------------------------------------------------------------


def test_runtime_stops_on_no_tool_calls_and_stop_reason() -> None:
    transport = FakeTransport()
    transport.queue_text("你好，我在。", finish_reason="stop")
    result = _run("你好", transport=transport)
    assert result.status == "replied"
    assert transport.call_count == 1


# ---------------------------------------------------------------------------
# Max iterations exceeded
# ---------------------------------------------------------------------------


def test_runtime_stops_when_max_iterations_exceeded() -> None:
    transport = FakeTransport()
    transport.queue_repeating_tool_call("memory_retriever", {"query": "水杯"})
    executor = FakeToolExecutor()
    result = _run(
        "帮我找水杯",
        transport=transport,
        dispatcher=executor,
        max_tool_iterations=1,
    )
    assert result.status == "failed"
    assert result.error_code == "max_tool_iterations_exceeded"
    assert any(event.type == "runtime.budget_exhausted" for event in result.events)


# ---------------------------------------------------------------------------
# Tool result ID mismatch
# ---------------------------------------------------------------------------


def test_runtime_fails_when_tool_result_id_mismatch() -> None:
    transport = FakeTransport()
    transport.queue_tool_call("memory_retriever", {"query": "水杯"}, call_id="call_1")
    executor = MismatchedToolExecutor()
    executor.queue_result(tool_call_id="call_other", name="memory_retriever")
    result = _run("帮我找水杯", transport=transport, dispatcher=executor)
    assert result.status == "failed"
    assert result.error_code == "tool_result_id_mismatch"
    assert any(event.type == "runtime.turn_failed" for event in result.events)


# ---------------------------------------------------------------------------
# Finish reason "length" treated as failure
# ---------------------------------------------------------------------------


def test_runtime_handles_finish_reason_length_as_failure() -> None:
    transport = FakeTransport()
    transport.queue_text("截断回复", finish_reason="length")
    result = _run("你好", transport=transport)
    assert result.status == "failed"
    assert result.error_code == "model_output_truncated"
