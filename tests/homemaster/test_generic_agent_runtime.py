"""Tests for GenericAgentRuntime — the new message/tool-call/tool-result loop.

Uses fake transport and tool executor to verify runtime behavior without
importing home domain modules.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
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
from homemaster.config.observability import ObservabilityConfig
from types import SimpleNamespace
from homemaster.providers.transports import TransportDelta


class FakeTransport:
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
        system_prompt: str = "",
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
        iteration: int | None = None,
    ) -> Iterator[TransportDelta]:
        self.last_system_prompt = system_prompt
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


class ContextLengthFailingTransport:
    def __init__(self) -> None:
        self.call_count = 0

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
        self.call_count += 1
        raise RuntimeError("context_length_exceeded")
        yield


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


def test_stop_condition_can_end_run_after_tool_results() -> None:
    transport = FakeTransport()
    transport.queue_tool_call("robot_verify", {}, call_id="call_1")
    transport.queue_text("This response must not be requested.")

    def stop_condition(session: AgentSession, tool_results: list[ToolResultMessage]):
        assert session.messages[-1].role == "tool"
        assert tool_results[0].name == "robot_verify"
        from homemaster.agent.generic_runtime import RuntimeStopDecision

        return RuntimeStopDecision(
            status="failed",
            error_code="benchmark_invalid_action_limit",
            final_reply="",
            payload={"reason": "invalid action limit reached"},
        )

    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=12,
        stop_condition=stop_condition,
    )
    session = AgentSession(session_id="test-stop")
    result = runtime.run(session, "run benchmark")

    assert result.status == "failed"
    assert result.error_code == "benchmark_invalid_action_limit"
    assert transport.call_count == 1


def test_runtime_passes_system_prompt_to_transport() -> None:
    transport = FakeTransport()
    transport.queue_text("done")
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        system_prompt="You are HomeMaster.",
    )
    session = AgentSession(session_id="test-sp")

    runtime.run(session, "hello", tools=[])

    assert transport.last_system_prompt == "You are HomeMaster."


def test_runtime_writes_session_persistence_artifacts(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport.queue_tool_call("memory_retriever", {"query": "水杯"}, call_id="call_1")
    transport.queue_text("我找到了水杯。")
    settings = SimpleNamespace(
        run_id="persist-run",
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
        provider_name="Mimo",
        observability=ObservabilityConfig(session_dir=str(tmp_path / "sessions")),
    )
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=3,
        system_prompt="system prompt",
    )
    session = AgentSession(session_id="persist-session")

    result = runtime.run(session, "帮我找水杯", settings=settings)

    assert result.status == "replied"
    session_dir = tmp_path / "sessions" / "persist-session"
    assert (session_dir / "trace.jsonl").exists()
    assert (session_dir / "messages.jsonl").exists()
    assert (session_dir / "session.json").exists()

    snapshot = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    assert snapshot["session_id"] == "persist-session"
    assert snapshot["model"] == "Mimo"
    assert snapshot["system_prompt"] == "system prompt"
    assert snapshot["agent_state"]["status"] == "replied"
    assert [message["role"] for message in snapshot["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    message_entries = [
        json.loads(line)
        for line in (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [(entry["role"], entry["content"]) for entry in message_entries] == [
        ("user", "帮我找水杯"),
        ("assistant", "我找到了水杯。"),
    ]
    trace_types = [
        json.loads(line)["type"]
        for line in (session_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "runtime.turn_started" in trace_types
    assert "tool.call_completed" in trace_types
    assert "runtime.turn_completed" in trace_types


def test_runtime_supports_unbounded_iterations() -> None:
    call_count = {"n": 0}

    def stop_after_3(session, tool_results):
        call_count["n"] += 1
        if call_count["n"] >= 3:
            from homemaster.agent.generic_runtime import RuntimeStopDecision
            return RuntimeStopDecision(status="replied", final_reply="done")
        return None

    transport = FakeTransport()
    transport.queue_tool_call("t", {}, call_id="c1")
    transport.queue_tool_call("t", {}, call_id="c2")
    transport.queue_tool_call("t", {}, call_id="c3")
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=None,
        stop_condition=stop_after_3,
    )
    session = AgentSession(session_id="test-unbounded")
    result = runtime.run(session, "go", tools=[])

    assert result.status == "replied"
    assert result.final_reply == "done"


def test_reactive_compact_retries_context_length_error_once() -> None:
    from homemaster.agent.context import ContextAssembler
    from homemaster.config import ContextPolicyConfig, ProviderProfileConfig

    transport = ContextLengthFailingTransport()
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="mimo",
            protocol="anthropic",
            base_url="https://mimo.example",
            model="m",
            api_keys=["secret"],
            context_window_tokens=100_000,
            max_output_tokens=4096,
        ),
        policy=ContextPolicyConfig(),
        system_prompt="system",
    )
    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        context_assembler=assembler,
    )

    result = runtime.run(AgentSession(session_id="reactive"), "hello", tools=[])

    assert transport.call_count == 2
    assert result.status == "failed"
    assert result.error_code == "context_length_exceeded_after_compact"
