"""Tests for AgentRuntime — the new message/tool-call/tool-result loop.

Uses fake transport and tool executor to verify runtime behavior without
importing home domain modules.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.agent.generic_runtime import AgentRuntime, GenericRunResult
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
    ToolResultMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.config.observability import ObservabilityConfig
from homemaster.providers.attempts import (
    ListProviderAttemptSink,
    ProviderAttemptRecord,
)
from homemaster.providers.errors import (
    LLMAuthError,
    LLMNetworkError,
    LLMProviderError,
)
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

    async def stream(
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
    ) -> AsyncIterator[TransportDelta]:
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

    async def stream(
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
    ) -> AsyncIterator[TransportDelta]:
        self.call_count += 1
        raise RuntimeError("context_length_exceeded")
        yield


class AuditedRetryTransport:
    def __init__(
        self,
        *,
        first_error: Exception,
        partial_delta: bool = False,
        change_retry_hash: bool = False,
    ) -> None:
        self.first_error = first_error
        self.partial_delta = partial_delta
        self.change_retry_hash = change_retry_hash
        self.call_count = 0
        self.key_indices: list[int] = []
        self.attempt_ids: list[str] = []
        self.request_hashes: list[str] = []

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        attempt_sink: Any,
        model_attempt_id: str,
        provider_key_index: int,
        **_kwargs: Any,
    ) -> AsyncIterator[TransportDelta]:
        payload = {
            "messages": [message.model_dump(mode="json") for message in messages],
            "system_prompt": system_prompt,
            "tools": tools,
        }
        if self.change_retry_hash and self.call_count == 1:
            payload["retry_mutation"] = True
        request_sha256 = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.key_indices.append(provider_key_index)
        self.attempt_ids.append(model_attempt_id)
        self.request_hashes.append(request_sha256)
        call_index = self.call_count
        self.call_count += 1
        if call_index == 0:
            if self.partial_delta:
                yield TransportDelta(type="transport.delta", text_delta="partial")
            assert isinstance(self.first_error, Exception)
            error_type = getattr(self.first_error, "error_type", None)
            cause_code = getattr(self.first_error, "cause_code", None)
            attempt_sink.record_attempt(
                ProviderAttemptRecord(
                    model_attempt_id=model_attempt_id,
                    request_sha256=request_sha256,
                    outbound_images=(),
                    stripped_images=False,
                    response_completed=False,
                    error_type=error_type,
                    cause_code=cause_code,
                )
            )
            raise self.first_error
        attempt_sink.record_attempt(
            ProviderAttemptRecord(
                model_attempt_id=model_attempt_id,
                request_sha256=request_sha256,
                outbound_images=(),
                stripped_images=False,
                response_completed=True,
                error_type=None,
                cause_code=None,
            )
        )
        yield TransportDelta(type="transport.delta", text_delta="done")
        yield TransportDelta(type="transport.delta", finish_reason="stop")


class FakeToolExecutor:
    """Fake tool executor that returns success results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, *, tool_calls, run_context) -> list[ToolResultMessage]:
        del run_context
        results = []
        for call in tool_calls:
            self.calls.append((call.name, call.arguments))
            results.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    content=[ContentBlock(text='{"success": true}')],
                )
            )
        return results


class FailingToolExecutor:
    """Fake tool executor that always fails."""

    def __init__(self) -> None:
        self.call_count = 0

    def dispatch(self, *, tool_calls, run_context) -> list[ToolResultMessage]:
        del run_context
        self.call_count += len(tool_calls)
        return [
            ToolResultMessage(
                tool_call_id=call.id,
                name=call.name,
                content=[ContentBlock(text='{"error": "tool failed"}')],
                is_error=True,
            )
            for call in tool_calls
        ]


class MismatchedToolExecutor:
    """Fake tool executor that returns wrong tool_call_id."""

    def queue_result(self, tool_call_id: str, name: str) -> None:
        self._result = ToolResultMessage(
            tool_call_id=tool_call_id,
            name=name,
            content=[ContentBlock(text='{"ok": true}')],
        )

    def dispatch(self, *, tool_calls, run_context) -> list[ToolResultMessage]:
        del tool_calls, run_context
        return [self._result]


def _run(utterance: str, **kwargs: Any) -> GenericRunResult:
    transport = kwargs.pop("transport", FakeTransport())
    executor = kwargs.pop("dispatcher", FakeToolExecutor())
    max_iter = kwargs.pop("max_tool_iterations", 12)
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=executor,
        max_tool_iterations=max_iter,
    )
    session = AgentSession(session_id="test")
    return asyncio.run(runtime.run(session, utterance, **kwargs))


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
        from homemaster.application import RuntimeStopDecision

        return RuntimeStopDecision(
            status="failed",
            error_code="benchmark_invalid_action_limit",
            final_reply="",
            payload={"reason": "invalid action limit reached"},
        )

    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=12,
        stop_condition=stop_condition,
    )
    session = AgentSession(session_id="test-stop")
    result = asyncio.run(runtime.run(session, "run benchmark"))

    assert result.status == "failed"
    assert result.error_code == "benchmark_invalid_action_limit"
    assert transport.call_count == 1


def test_runtime_passes_system_prompt_to_transport() -> None:
    transport = FakeTransport()
    transport.queue_text("done")
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        system_prompt="You are HomeMaster.",
    )
    session = AgentSession(session_id="test-sp")

    asyncio.run(runtime.run(session, "hello"))

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
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=3,
        system_prompt="system prompt",
    )
    session = AgentSession(session_id="persist-session")

    result = asyncio.run(runtime.run(session, "帮我找水杯", settings=settings))

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
            from homemaster.application import RuntimeStopDecision
            return RuntimeStopDecision(status="replied", final_reply="done")
        return None

    transport = FakeTransport()
    transport.queue_tool_call("t", {}, call_id="c1")
    transport.queue_tool_call("t", {}, call_id="c2")
    transport.queue_tool_call("t", {}, call_id="c3")
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=None,
        stop_condition=stop_after_3,
    )
    session = AgentSession(session_id="test-unbounded")
    result = asyncio.run(runtime.run(session, "go"))

    assert result.status == "replied"
    assert result.final_reply == "done"


def test_reactive_compact_retries_context_length_error_twice_by_default() -> None:
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
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        context_assembler=assembler,
    )

    result = asyncio.run(runtime.run(AgentSession(session_id="reactive"), "hello"))

    assert transport.call_count == 3
    assert result.status == "failed"
    assert result.error_code == "context_length_exceeded_after_compact"


def test_runtime_retries_one_frozen_retryable_provider_attempt() -> None:
    transport = AuditedRetryTransport(
        first_error=LLMNetworkError(
            error_type="network_error",
            message="connection reset",
            cause_code="transient_network",
        )
    )
    sinks: list[ListProviderAttemptSink] = []

    def sink_factory() -> ListProviderAttemptSink:
        sink = ListProviderAttemptSink()
        sinks.append(sink)
        return sink

    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        provider_attempt_sink_factory=sink_factory,
    )

    result = asyncio.run(runtime.run(AgentSession(session_id="retry"), "hello"))

    assert result.status == "replied"
    assert result.final_reply == "done"
    assert transport.call_count == 2
    assert transport.key_indices == [0, 1]
    assert len(set(transport.attempt_ids)) == 2
    assert transport.request_hashes[0] == transport.request_hashes[1]
    assert len(sinks) == 2
    assert sinks[0].records[0].response_completed is False
    assert sinks[1].records[0].response_completed is True
    assert sum(event.type == "transport.request_retrying" for event in result.events) == 1


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthError(
            error_type="auth_error",
            message="invalid key",
            cause_code="authentication_rejected",
        ),
        LLMProviderError(
            error_type="provider_error",
            message="provider rejected request",
            cause_code="provider_error",
        ),
    ],
    ids=["auth", "generic-provider"],
)
def test_runtime_does_not_retry_closed_nonretryable_provider_errors(
    error: Exception,
) -> None:
    transport = AuditedRetryTransport(first_error=error)
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        provider_attempt_sink_factory=ListProviderAttemptSink,
    )

    result = asyncio.run(runtime.run(AgentSession(session_id="no-retry"), "hello"))

    assert result.status == "failed"
    assert result.error_code == "transport_error"
    assert transport.call_count == 1


def test_runtime_does_not_retry_after_partial_provider_delta() -> None:
    transport = AuditedRetryTransport(
        first_error=LLMNetworkError(
            error_type="network_error",
            message="connection reset",
            cause_code="transient_network",
        ),
        partial_delta=True,
    )
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        provider_attempt_sink_factory=ListProviderAttemptSink,
    )

    result = asyncio.run(runtime.run(AgentSession(session_id="partial"), "hello"))

    assert result.status == "failed"
    assert result.error_code == "transport_error"
    assert transport.call_count == 1
    assert [message.role for message in result.session.messages] == ["user"]


def test_runtime_rejects_retry_request_hash_drift() -> None:
    transport = AuditedRetryTransport(
        first_error=LLMNetworkError(
            error_type="network_error",
            message="connection reset",
            cause_code="transient_network",
        ),
        change_retry_hash=True,
    )
    runtime = AgentRuntime(
        transport=transport,
        tool_executor=FakeToolExecutor(),
        max_tool_iterations=1,
        provider_attempt_sink_factory=ListProviderAttemptSink,
    )

    result = asyncio.run(runtime.run(AgentSession(session_id="hash-drift"), "hello"))

    assert result.status == "failed"
    assert result.error_code == "transport_error"
    assert transport.call_count == 2
    assert transport.request_hashes[0] != transport.request_hashes[1]
    assert [message.role for message in result.session.messages] == ["user"]


def test_runtime_commits_successful_attempt_before_tool_dispatch() -> None:
    order: list[str] = []
    committed_attempts: list[ProviderAttemptRecord] = []
    provider_commits: list[ProviderAttemptRecord] = []

    class ToolTransport:
        async def stream(
            self,
            _messages: list[Message],
            *,
            attempt_sink: Any,
            model_attempt_id: str,
            **_kwargs: Any,
        ) -> AsyncIterator[TransportDelta]:
            attempt_sink.record_attempt(
                ProviderAttemptRecord(
                    model_attempt_id=model_attempt_id,
                    request_sha256="d" * 64,
                    outbound_images=(),
                    stripped_images=False,
                    response_completed=True,
                    error_type=None,
                    cause_code=None,
                )
            )
            yield TransportDelta(
                type="transport.delta",
                tool_call_delta=ToolCall(id="call-1", name="test_tool", arguments={}),
            )
            yield TransportDelta(type="transport.delta", finish_reason="tool_calls")

    class Observer:
        def bind_messages(self, messages: list[Message]) -> list[Message]:
            return messages

        def commit_successful_response(self, *, attempt: ProviderAttemptRecord) -> None:
            order.append("model_view_commit")
            committed_attempts.append(attempt)

    class Executor(FakeToolExecutor):
        def dispatch(self, *, tool_calls, run_context) -> list[ToolResultMessage]:
            order.append("tool_dispatch")
            return super().dispatch(tool_calls=tool_calls, run_context=run_context)

    class ProviderObserver:
        def commit_successful_response(self, *, attempt: ProviderAttemptRecord) -> None:
            order.append("provider_commit")
            provider_commits.append(attempt)

    runtime = AgentRuntime(
        transport=ToolTransport(),
        tool_executor=Executor(),
        max_tool_iterations=1,
        model_view_observer=Observer(),
        provider_commit_observer=ProviderObserver(),
        provider_attempt_sink_factory=ListProviderAttemptSink,
    )

    result = asyncio.run(runtime.run(AgentSession(session_id="commit-order"), "hello"))

    assert result.error_code == "max_tool_iterations_exceeded"
    assert order == ["model_view_commit", "provider_commit", "tool_dispatch"]
    assert len(committed_attempts) == 1
    assert committed_attempts[0].request_sha256 == "d" * 64
    assert len(provider_commits) == 1
