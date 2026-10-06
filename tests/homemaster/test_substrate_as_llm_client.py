"""AsLLMClient contract tests — LLMClient-compatible surface on AS models."""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.model._model_response import FinishedReason
from homemaster.agent.messages import UserMessage
from homemaster.config.config import ProviderProfileConfig
from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.providers.errors import LLMClientError
from homemaster.substrate.as_llm_client import AsLLMClient


class _Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeModel:
    """Scripted stand-in for a ``ChatModelBase`` — returns canned chunks."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, messages: list, tools: list | None = None, **kwargs: Any
    ) -> Any:
        self.calls.append({"messages": messages, "tools": tools, **kwargs})
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item

        async def _gen():
            for chunk in item or []:
                yield chunk

        return _gen()


def _profile(**overrides) -> ProviderProfileConfig:
    values = {
        "name": "p1",
        "api_format": "anthropic",
        "transport": "anthropic_sdk",
        "base_url": "https://api.example.com",
        "model": "claude-x",
        "api_keys": ("sk-a", "sk-b"),
        "context_window_tokens": 12345,
        "max_output_tokens": 4096,
        "kind": "chat",
    }
    values.update(overrides)
    return ProviderProfileConfig(**values)


def _client(model: FakeModel, **overrides) -> AsLLMClient:
    return AsLLMClient(
        _profile(**overrides),
        model_factory=lambda *a, **kw: model,
    )


def _chunks(*items: Any) -> list[Any]:
    return list(items)


@pytest.mark.asyncio
async def test_stream_yields_text_tool_call_and_finish() -> None:
    model = FakeModel(
        [
            _chunks(
                ChatResponse(
                    content=[TextBlock(text="hel", id="t1")], is_last=False
                ),
                ChatResponse(
                    content=[TextBlock(text="lo", id="t1")], is_last=False
                ),
                ChatResponse(
                    content=[
                        TextBlock(text="hello", id="t1"),
                        ToolCallBlock(
                            id="tc1", name="echo", input='{"x": 1}'
                        ),
                    ],
                    is_last=True,
                    metadata={"stop_reason": "end_turn"},
                    usage=None,
                ),
            )
        ]
    )
    sink = _Sink()
    attempt_sink = ListProviderAttemptSink()
    deltas = [
        d
        async for d in _client(model).stream(
            [UserMessage.from_text("hi")],
            tools=[{"name": "echo", "description": "d", "input_schema": {}}],
            system_prompt="sys",
            event_sink=sink,
            run_id="r1",
            session_id="s1",
            iteration=0,
            attempt_sink=attempt_sink,
            model_attempt_id="r1:attempt-0001",
        )
    ]
    texts = [d.text_delta for d in deltas if d.text_delta]
    assert texts == ["hel", "lo"]
    calls = [d.tool_call_delta for d in deltas if d.tool_call_delta]
    assert len(calls) == 1 and calls[0].id == "tc1"
    assert calls[0].arguments == {"x": 1}
    finals = [d for d in deltas if d.finish_reason]
    assert finals[0].finish_reason == "stop"
    assert finals[0].provider_metadata["raw_stop_reason"] == "end_turn"
    # attempt recorded with a stable request fingerprint
    assert attempt_sink.records[-1].response_completed is True
    assert attempt_sink.records[-1].request_sha256
    # events emitted with HM payload shape
    kinds = [e.type for e in sink.events]
    assert "transport.request_started" in kinds
    assert "transport.response_completed" in kinds
    # tools converted to OpenAI envelope for AS
    assert model.calls[0]["tools"][0]["function"]["name"] == "echo"
    # system prompt prepended as Msg
    assert model.calls[0]["messages"][0].role == "system"


@pytest.mark.asyncio
async def test_max_tokens_maps_to_length() -> None:
    model = FakeModel(
        [
            _chunks(
                ChatResponse(
                    content=[TextBlock(text="x", id="t")],
                    is_last=True,
                    metadata={"stop_reason": "max_tokens"},
                )
            )
        ]
    )
    deltas = [d async for d in _client(model).stream([UserMessage.from_text("hi")])]
    assert deltas[-1].finish_reason == "length"


@pytest.mark.asyncio
async def test_retry_fingerprint_is_stable() -> None:
    """Same frozen input must hash identically across attempts."""
    model = FakeModel([_chunks(), _chunks()])
    client = _client(model)
    sink = ListProviderAttemptSink()
    msgs = [UserMessage.from_text("same input")]
    for _ in range(2):
        async for _ in client.stream(msgs, attempt_sink=sink):
            pass
    records = sink.records
    assert len(records) == 2
    assert records[0].request_sha256 == records[1].request_sha256


@pytest.mark.asyncio
async def test_provider_key_index_selects_key() -> None:
    seen_keys: list[str] = []

    def _factory(profile, *, api_key=None, timeout_s=None):
        seen_keys.append(api_key)
        return FakeModel([_chunks()])

    client = AsLLMClient(_profile(), model_factory=_factory)
    async for _ in client.stream(
        [UserMessage.from_text("x")], provider_key_index=1
    ):
        pass
    assert seen_keys == ["sk-b"]


@pytest.mark.asyncio
async def test_model_error_maps_and_records_failed_attempt() -> None:
    model = FakeModel([RuntimeError("APIConnectionError: refused")])
    sink = _Sink()
    attempt_sink = ListProviderAttemptSink()
    with pytest.raises(LLMClientError):
        async for _ in _client(model).stream(
            [UserMessage.from_text("hi")],
            event_sink=sink,
            attempt_sink=attempt_sink,
            run_id="r1",
        ):
            pass
    assert attempt_sink.records[-1].response_completed is False
    assert attempt_sink.records[-1].request_sha256
    kinds = [e.type for e in sink.events]
    assert "transport.request_failed" in kinds


@pytest.mark.asyncio
async def test_no_keys_fails_closed() -> None:
    model = FakeModel([])
    client = AsLLMClient(
        _profile(api_keys=()), model_factory=lambda *a, **kw: model
    )
    with pytest.raises(LLMClientError, match="no_keys|no API keys"):
        async for _ in client.stream([UserMessage.from_text("hi")]):
            pass


@pytest.mark.asyncio
async def test_interrupted_stream_raises_typed_error() -> None:
    model = FakeModel(
        [
            _chunks(
                ChatResponse(
                    content=[],
                    is_last=True,
                    finished_reason=FinishedReason.INTERRUPTED,
                )
            )
        ]
    )
    with pytest.raises(LLMClientError):
        async for _ in _client(model).stream([UserMessage.from_text("hi")]):
            pass


class _FakeSdkClient:
    """Stands in for ``AsyncAnthropic``/``AsyncOpenAI`` on ``model.client``."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _model_with_client(chunks: list[Any] | None = None) -> FakeModel:
    model = FakeModel(chunks or [])
    model.client = _FakeSdkClient()
    return model


def test_chat_model_caches_per_key_index() -> None:
    built: list[FakeModel] = []

    def _factory(profile, *, api_key=None, timeout_s=None):
        model = _model_with_client()
        built.append(model)
        return model

    client = AsLLMClient(_profile(), model_factory=_factory)
    first = client.chat_model()
    assert client.chat_model() is first
    other = client.chat_model(provider_key_index=1)
    assert other is not first
    assert len(built) == 2


@pytest.mark.asyncio
async def test_stream_reuses_cached_model() -> None:
    built: list[FakeModel] = []

    def _factory(profile, *, api_key=None, timeout_s=None):
        model = _model_with_client(
            [_chunks(ChatResponse(content=[], is_last=True))]
        )
        built.append(model)
        return model

    client = AsLLMClient(_profile(), model_factory=_factory)
    for _ in range(3):
        async for _delta in client.stream([UserMessage.from_text("hi")]):
            pass
    assert len(built) == 1


@pytest.mark.asyncio
async def test_aclose_closes_every_cached_model_client_once() -> None:
    built: list[FakeModel] = []

    def _factory(profile, *, api_key=None, timeout_s=None):
        model = _model_with_client(
            [_chunks(ChatResponse(content=[], is_last=True))]
        )
        built.append(model)
        return model

    client = AsLLMClient(_profile(), model_factory=_factory)
    agent_model = client.chat_model()
    async for _delta in client.stream([UserMessage.from_text("hi")]):
        pass
    async for _delta in client.stream(
        [UserMessage.from_text("hi")], provider_key_index=1
    ):
        pass

    await client.aclose()
    assert [m.client.close_calls for m in built] == [1, 1]
    assert agent_model.client.close_calls == 1

    # Idempotent; models built after close are a fresh generation.
    await client.aclose()
    assert [m.client.close_calls for m in built] == [1, 1]
    replacement = client.chat_model()
    assert replacement is not agent_model


@pytest.mark.asyncio
async def test_aclose_tolerates_models_without_sdk_client() -> None:
    client = AsLLMClient(
        _profile(), model_factory=lambda *a, **kw: FakeModel([])
    )
    client.chat_model()
    await client.aclose()
