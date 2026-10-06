"""ProviderRetryMiddleware contract — mid-stream transport failures retry
through the real observability layer (attempt rows + transport.* events),
exactly as the production middleware stack composes them."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.substrate.middleware_runtime import (
    ProviderObservabilityMiddleware,
    ProviderRetryMiddleware,
)


class _FakeHandle:
    """Minimal AS-runtime handle surface the middlewares touch."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.run_id = "run-test"
        self.model_name = "mimo-x"
        self.model_api_format = "anthropic"
        self.normal_iterations = 0
        self.scope = None

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class _Block:
    def __init__(self, type_: str) -> None:
        self.type = type_


class _Chunk:
    def __init__(
        self,
        blocks: tuple[_Block, ...] = (),
        *,
        is_last: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = list(blocks)
        self.is_last = is_last
        self.metadata = metadata or {}
        self.usage = None
        self.id = "chunk-1"


def _remote_protocol_error() -> httpx.RemoteProtocolError:
    return httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)",
        request=httpx.Request("POST", "https://provider.example/v1/messages"),
    )


async def _stream_of(items: list[Any]) -> Any:
    async def _gen() -> Any:
        for item in items:
            if isinstance(item, Exception):
                raise item
            yield item

    return _gen()


def _stack(
    sink: ListProviderAttemptSink,
) -> tuple[
    _FakeHandle, ProviderRetryMiddleware, ProviderObservabilityMiddleware
]:
    handle = _FakeHandle()
    observability = ProviderObservabilityMiddleware(handle=handle, attempt_sink=sink)
    retry = ProviderRetryMiddleware(handle=handle, attempt_sink=sink)
    return handle, retry, observability


@pytest.mark.asyncio
async def test_mid_stream_transport_error_retries_while_reasoning_only() -> None:
    """attempt-1 dies mid-stream on RemoteProtocolError after only thinking
    chunks (pre-commit); the same frozen call is re-issued and completes."""
    sink = ListProviderAttemptSink()
    handle, retry, observability = _stack(sink)

    calls: list[int] = []

    async def model_call() -> Any:
        calls.append(len(calls))
        if len(calls) == 1:
            return await _stream_of([_Chunk((_Block("thinking"),)), _remote_protocol_error()])
        return await _stream_of(
            [_Chunk((_Block("text"),)), _Chunk((), is_last=True)]
        )

    async def next_handler() -> Any:
        return await observability.on_model_call(None, {}, model_call)

    result = await retry.on_model_call(None, {"messages": []}, next_handler)
    assert hasattr(result, "__aiter__")
    emitted = [chunk async for chunk in result]

    assert len(calls) == 2
    # One reasoning chunk from the dead attempt, then the full retried stream.
    assert [b.type for c in emitted for b in c.content if not c.is_last] == [
        "thinking",
        "text",
    ]
    assert any(e[0] == "transport.request_retrying" for e in handle.events)
    records = sink.records
    assert len(records) == 2
    assert records[0].response_completed is False
    assert records[0].error_type == "RemoteProtocolError"
    assert records[1].response_completed is True


@pytest.mark.asyncio
async def test_post_commit_stream_failure_is_not_retried() -> None:
    """A text chunk already reached the consumer — the response committed —
    so a later transport drop must propagate, not silently re-issue."""
    sink = ListProviderAttemptSink()
    handle, retry, observability = _stack(sink)

    async def model_call() -> Any:
        return await _stream_of(
            [_Chunk((_Block("text"),)), _remote_protocol_error()]
        )

    async def next_handler() -> Any:
        return await observability.on_model_call(None, {}, model_call)

    result = await retry.on_model_call(None, {"messages": []}, next_handler)
    with pytest.raises(httpx.RemoteProtocolError):
        _ = [chunk async for chunk in result]

    assert not any(e[0] == "transport.request_retrying" for e in handle.events)
    assert len(sink.records) == 1


@pytest.mark.asyncio
async def test_call_phase_transport_error_retries_fresh_request() -> None:
    """Failure before any stream object exists is the classic retry case."""
    sink = ListProviderAttemptSink()
    handle, retry, observability = _stack(sink)

    calls: list[int] = []

    async def model_call() -> Any:
        calls.append(len(calls))
        if len(calls) == 1:
            raise _remote_protocol_error()
        return await _stream_of([_Chunk((_Block("text"),), is_last=False)])

    async def next_handler() -> Any:
        return await observability.on_model_call(None, {}, model_call)

    result = await retry.on_model_call(None, {"messages": []}, next_handler)
    emitted = [chunk async for chunk in result]
    assert len(calls) == 2
    assert len(emitted) == 1
    assert len(sink.records) == 2


@pytest.mark.asyncio
async def test_deadline_cancellation_is_never_retried() -> None:
    sink = ListProviderAttemptSink()
    handle, retry, observability = _stack(sink)

    async def model_call() -> Any:
        return await _stream_of([_Chunk((_Block("thinking"),)), TimeoutError()])

    async def next_handler() -> Any:
        return await observability.on_model_call(None, {}, model_call)

    result = await retry.on_model_call(None, {"messages": []}, next_handler)
    with pytest.raises(TimeoutError):
        _ = [chunk async for chunk in result]
    assert not any(e[0] == "transport.request_retrying" for e in handle.events)
