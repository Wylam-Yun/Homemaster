"""Agent loop acceptance tests — verifies core behavior of the generic agent runtime."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homemaster.agent.generic_runtime import GenericAgentRuntime
from homemaster.agent.messages import Message, ToolCall
from homemaster.agent.session import AgentSession
from homemaster.providers.transport import LLMTransport, TransportDelta
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


class FakeTransport(LLMTransport):
    """Fake LLM transport that returns predefined responses via stream()."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._index = 0

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
        iteration: int | None = None,
    ) -> Iterator[TransportDelta]:
        if self._index >= len(self._responses):
            resp = {"content": "I'm here to help!", "stop_reason": "end_turn"}
        else:
            resp = self._responses[self._index]
            self._index += 1

        content = resp.get("content", "")
        stop_reason = resp.get("stop_reason", "end_turn")
        tool_calls_data = resp.get("tool_calls", [])

        if content:
            yield TransportDelta(type="text", text_delta=content)

        for tc in tool_calls_data:
            yield TransportDelta(
                type="tool_call",
                tool_call_delta=ToolCall(
                    id=tc.get("id", "call_0"),
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                ),
            )

        yield TransportDelta(type="finish", finish_reason=stop_reason)


def test_chat_turn_uses_zero_tools() -> None:
    """A simple greeting should get a direct reply with no tool calls."""
    transport = FakeTransport([
        {"content": "你好！有什么可以帮你的吗？", "stop_reason": "end_turn"},
    ])
    dispatcher = ToolDispatcher()
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
    )
    session = AgentSession(session_id="test-greeting")
    result = runtime.run(session, "你好")
    assert result.final_reply
    assert "你好" in result.final_reply


def test_task_turn_calls_tools() -> None:
    """A task request should trigger tool calls."""

    def echo_executor(*, arguments: dict[str, Any], run_context: Any) -> ToolResult:
        return ToolResult(success=True, tool_name="echo", data=arguments)

    spec = ToolSpec(
        name="echo",
        description="Echo input",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=echo_executor,
    )
    registry = ToolRegistry()
    registry.register(spec)

    dispatcher = ToolDispatcher()
    for name in registry.all_names():
        dispatcher.register(registry.get(name))

    transport = FakeTransport([
        {"content": "", "stop_reason": "tool_use", "tool_calls": [
            {"id": "call_1", "name": "echo", "arguments": {"text": "hello"}},
        ]},
        {"content": "Done!", "stop_reason": "end_turn"},
    ])
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
    )
    session = AgentSession(session_id="test-tools")
    result = runtime.run(session, "do something", tools=[spec])
    assert result.final_reply
