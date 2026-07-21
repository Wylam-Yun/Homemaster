from __future__ import annotations

import asyncio
from typing import Any

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.config import ProviderProfileConfig
from homemaster.providers.attempts import ProviderAttemptRecord
from homemaster.providers.llm_client import LLMClient


def _provider() -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="async-test",
        kind="chat",
        api_format="anthropic",
        transport="anthropic_sdk",
        base_url="https://provider.invalid/anthropic",
        model="test-model",
        api_keys=["test-key"],
        context_window_tokens=4096,
        max_output_tokens=256,
    )


class _AsyncStream:
    def __init__(self, entered: asyncio.Event | None, release: asyncio.Event | None) -> None:
        self.entered = entered
        self.release = release

    async def __aenter__(self):
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def __aiter__(self):
        return self._events().__aiter__()

    async def _events(self):
        yield {"type": "message_start", "message": {}}
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "ok"},
        }
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}


class _AsyncMessages:
    def __init__(self, entered: asyncio.Event | None, release: asyncio.Event | None) -> None:
        self.entered = entered
        self.release = release

    def stream(self, **kwargs: Any) -> _AsyncStream:
        del kwargs
        return _AsyncStream(self.entered, self.release)


class _AsyncClient:
    def __init__(self, entered: asyncio.Event | None, release: asyncio.Event | None) -> None:
        self.messages = _AsyncMessages(entered, release)

    async def aclose(self) -> None:
        return None


def _factory(entered: asyncio.Event | None = None, release: asyncio.Event | None = None):
    def build(**kwargs: Any) -> _AsyncClient:
        del kwargs
        return _AsyncClient(entered, release)

    return build


class _IncrementalStream(_AsyncStream):
    def __init__(self, release: asyncio.Event) -> None:
        super().__init__(None, None)
        self.release_after_text = release

    async def _events(self):
        yield {"type": "message_start", "message": {}}
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "first"},
        }
        await self.release_after_text.wait()
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}


class _IncrementalMessages:
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release

    def stream(self, **kwargs: Any) -> _IncrementalStream:
        del kwargs
        return _IncrementalStream(self.release)


class _IncrementalClient(_AsyncClient):
    def __init__(self, release: asyncio.Event) -> None:
        self.messages = _IncrementalMessages(release)


@pytest.mark.asyncio
async def test_provider_wait_does_not_block_another_session() -> None:
    slow_entered = asyncio.Event()
    slow_release = asyncio.Event()
    slow = LLMClient(
        _provider(),
        anthropic_client_factory=_factory(slow_entered, slow_release),
    )
    fast = LLMClient(_provider(), anthropic_client_factory=_factory())

    slow_task = asyncio.create_task(slow.complete([UserMessage.from_text("slow")]))
    await asyncio.wait_for(slow_entered.wait(), timeout=1)

    fast_result = await asyncio.wait_for(
        fast.complete([UserMessage.from_text("fast")]),
        timeout=1,
    )
    assert fast_result.text == "ok"
    assert not slow_task.done()

    slow_release.set()
    assert (await asyncio.wait_for(slow_task, timeout=1)).text == "ok"


@pytest.mark.asyncio
async def test_provider_yields_delta_before_stream_completion() -> None:
    release = asyncio.Event()
    client = LLMClient(
        _provider(),
        anthropic_client_factory=lambda **kwargs: _IncrementalClient(release),
    )
    stream = client.stream([UserMessage.from_text("hello")])

    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert first.text_delta == "first"

    next_delta = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    assert not next_delta.done()
    release.set()
    assert (await asyncio.wait_for(next_delta, timeout=1)).finish_reason == "stop"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_attempt_is_awaited_after_response_completion() -> None:
    order: list[str] = []

    class EventSink:
        def emit(self, event) -> None:
            order.append(event.type)

    class AttemptSink:
        async def arecord_attempt(self, record: ProviderAttemptRecord) -> None:
            assert record.response_completed is True
            await asyncio.sleep(0)
            order.append("attempt.persisted")

    client = LLMClient(
        _provider(),
        event_sink=EventSink(),
        anthropic_client_factory=_factory(),
    )
    deltas = [
        delta
        async for delta in client.stream(
            [UserMessage.from_text("hello")],
            attempt_sink=AttemptSink(),
            model_attempt_id="run-a:attempt-0001",
        )
    ]

    assert deltas
    assert order == [
        "transport.request_started",
        "transport.response_completed",
        "attempt.persisted",
    ]
