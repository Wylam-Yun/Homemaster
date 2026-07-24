from __future__ import annotations

import asyncio
import json
from typing import get_args

import pytest

from homemaster.adapters.coworker_entry import build_coworker_stream_projector
from homemaster.events.bus import EventBus, EventBusClosedError
from homemaster.events.public_projection import PublicEventProjection
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    project_stream_event,
)


def _event(event_type: str, **values) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        session_id="session-a",
        run_id="run-a",
        turn_index=0,
        payload=values.pop("payload", {}),
        **values,
    )


def test_public_stream_event_union_remains_exactly_the_locked_seven_classes() -> None:
    assert set(get_args(StreamEvent)) == {
        AssistantTextDelta,
        AssistantTurnComplete,
        ToolExecutionStarted,
        ToolExecutionCompleted,
        ErrorEvent,
        StatusEvent,
        CompactProgressEvent,
    }


def test_transport_text_delta_maps_immediately_and_private_delta_fields_are_rejected() -> None:
    public = project_stream_event(
        _event(
            "transport.delta",
            payload={
                "text_delta": "visible",
                "reasoning_delta": "private chain",
                "tool_json": '{"secret":',
                "provider_metadata": {"request_id": "private"},
            },
        )
    )

    assert public == AssistantTextDelta(text="visible")
    assert (
        project_stream_event(
            _event(
                "transport.delta",
                payload={
                    "reasoning_delta": "private chain",
                    "tool_json": '{"secret":',
                    "provider_metadata": {"request_id": "private"},
                },
            )
        )
        is None
    )


def test_successful_assistant_reply_maps_to_completion_not_another_delta() -> None:
    public = project_stream_event(
        _event(
            "assistant.reply",
            payload={
                "reply": "complete",
                "finish_reason": "stop",
                "usage": {"output_tokens": 2},
                "tool_calls": [],
                "reasoning_content": "private",
                "provider_metadata": {"request_id": "private"},
            },
        )
    )

    assert isinstance(public, AssistantTurnComplete)
    assert public.message.text == "complete"
    assert public.message.finish_reason == "stop"
    assert public.message.reasoning_content is None
    assert public.message.provider_metadata == {}
    assert public.usage == {"output_tokens": 2}


def test_runtime_terminal_does_not_duplicate_assistant_completion() -> None:
    assert (
        project_stream_event(_event("runtime.turn_completed", payload={"final_reply": "complete"}))
        is None
    )


def test_openharness_consumer_branches_map_from_allowlisted_runtime_events() -> None:
    events = [
        _event("transport.delta", payload={"text_delta": "done"}),
        _event(
            "tool.call_started",
            name="observe",
            payload={"arguments": {"api_key": "secret", "safe": "value"}},
        ),
        _event(
            "tool.call_completed",
            name="observe",
            payload={"result": "visible", "data": {"state": "ready"}},
        ),
        _event(
            "assistant.reply",
            payload={
                "reply": "done",
                "finish_reason": "stop",
                "usage": {"x": 1},
                "tool_calls": [],
            },
        ),
        _event("runtime.turn_failed", payload={"error_code": "failed"}),
        _event("context.compaction", payload={"message": "compacting"}),
    ]

    projected = [project_stream_event(event) for event in events]

    assert isinstance(projected[0], AssistantTextDelta)
    assert isinstance(projected[1], ToolExecutionStarted)
    assert projected[1].tool_input == {"api_key": "secret", "safe": "value"}
    assert isinstance(projected[2], ToolExecutionCompleted)
    assert isinstance(projected[3], AssistantTurnComplete)
    assert isinstance(projected[4], ErrorEvent)
    assert isinstance(projected[5], CompactProgressEvent)
    private = _event("assistant.thinking", payload={"thinking": "private"})
    assert project_stream_event(private) is None


def test_gateway_projection_preserves_secrets_queries_and_host_paths_in_free_text() -> None:
    projection = PublicEventProjection()

    content = projection.project_content(
        "token=raw-token configured-secret /home/operator/private.txt "
        "https://example.test/file?signature=raw"
    )

    assert content == (
        "token=raw-token configured-secret /home/operator/private.txt "
        "https://example.test/file?signature=raw"
    )


@pytest.mark.asyncio
async def test_bounded_stream_backpressures_and_closes_without_subscribers() -> None:
    bus = EventBus(capacity=1)
    await bus.start()
    stream = bus.stream()
    first_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    await bus.aemit(_event("assistant.reply", payload={"reply": "one"}))
    first = await asyncio.wait_for(first_wait, timeout=1)
    assert first.payload["reply"] == "one"

    await bus.aemit(_event("assistant.reply", payload={"reply": "two"}))
    blocked = asyncio.create_task(bus.aemit(_event("assistant.reply", payload={"reply": "three"})))
    await asyncio.sleep(0.05)
    assert not blocked.done()

    second = await asyncio.wait_for(anext(stream), timeout=1)
    assert second.payload["reply"] == "two"
    await asyncio.wait_for(blocked, timeout=1)
    third = await asyncio.wait_for(anext(stream), timeout=1)
    assert third.payload["reply"] == "three"

    await stream.aclose()
    await bus.aclose()
    assert bus.subscriber_count == 0
    assert bus.pending_producer_count == 0


@pytest.mark.asyncio
async def test_close_releases_owner_loop_and_worker_thread_producers() -> None:
    bus = EventBus(capacity=1)
    await bus.start()
    stream = bus.stream()
    first_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await bus.aemit(_event("assistant.reply", payload={"reply": "first"}))
    await first_wait
    await bus.aemit(_event("assistant.reply", payload={"reply": "queued"}))

    owner_producer = asyncio.create_task(
        bus.aemit(_event("assistant.reply", payload={"reply": "owner-blocked"}))
    )
    worker_producer = asyncio.create_task(
        asyncio.to_thread(
            bus.emit,
            _event("assistant.reply", payload={"reply": "worker-blocked"}),
        )
    )
    await asyncio.sleep(0.05)
    assert not owner_producer.done()
    assert not worker_producer.done()

    await bus.aclose()
    with pytest.raises(EventBusClosedError):
        await asyncio.wait_for(owner_producer, timeout=1)
    with pytest.raises(EventBusClosedError):
        await asyncio.wait_for(worker_producer, timeout=1)
    assert bus.pending_producer_count == 0
    assert bus.subscriber_count == 0
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_consumer_close_releases_its_blocked_producer() -> None:
    bus = EventBus(capacity=1)
    await bus.start()
    stream = bus.stream()
    first_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await bus.aemit(_event("assistant.reply", payload={"reply": "first"}))
    await first_wait
    await bus.aemit(_event("assistant.reply", payload={"reply": "queued"}))
    blocked = asyncio.create_task(
        bus.aemit(_event("assistant.reply", payload={"reply": "blocked"}))
    )
    await asyncio.sleep(0.05)
    assert not blocked.done()

    await stream.aclose()
    with pytest.raises(EventBusClosedError):
        await asyncio.wait_for(blocked, timeout=1)
    assert bus.pending_producer_count == 0
    await bus.aclose()


@pytest.mark.asyncio
async def test_cancelled_blocked_producer_does_not_enqueue_after_cancellation() -> None:
    bus = EventBus(capacity=1)
    await bus.start()
    stream = bus.stream()
    first_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await bus.aemit(_event("assistant.reply", payload={"reply": "first"}))
    await first_wait
    await bus.aemit(_event("assistant.reply", payload={"reply": "queued"}))
    blocked = asyncio.create_task(
        bus.aemit(_event("assistant.reply", payload={"reply": "cancelled"}))
    )
    while bus.pending_producer_count == 0:
        await asyncio.sleep(0)

    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked
    assert bus.pending_producer_count == 0
    assert (await anext(stream)).payload["reply"] == "queued"

    late = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    assert late.done() is False
    late.cancel()
    with pytest.raises(asyncio.CancelledError):
        await late
    await stream.aclose()
    await bus.aclose()


@pytest.mark.asyncio
async def test_public_stream_does_not_expose_private_events() -> None:
    bus = EventBus(capacity=2, public_projector=project_stream_event)
    await bus.start()
    stream = bus.public_stream()
    public_wait = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    await asyncio.to_thread(
        bus.emit,
        _event("assistant.thinking", payload={"thinking": "private reasoning"}),
    )
    await asyncio.to_thread(
        bus.emit,
        _event("transport.delta", payload={"text_delta": "public answer"}),
    )

    public = await asyncio.wait_for(public_wait, timeout=1)
    assert public == AssistantTextDelta(text="public answer")
    await stream.aclose()
    await bus.aclose()


@pytest.mark.asyncio
async def test_public_stream_requires_explicit_trust_boundary() -> None:
    bus = EventBus()
    await bus.start()
    stream = bus.public_stream()
    with pytest.raises(RuntimeError, match="explicit projector"):
        await anext(stream)
    await bus.aclose()


def test_coworker_projector_preserves_free_text_and_structurally_summarizes_tools() -> None:
    secret = "configured-provider-secret"
    project = build_coworker_stream_projector()

    assert project(
        _event("assistant.reply", payload={"reply": f"token={secret}"})
    ) == AssistantTextDelta(text=f"token={secret}")
    assert project(
        _event(
            "assistant.reply",
            payload={"reply": "https://example.invalid/file?X-Amz-Signature=deadbeef"},
        )
    ) == AssistantTextDelta(
        text="https://example.invalid/file?X-Amz-Signature=deadbeef"
    )

    terminal = project(
        _event(
            "tool.call_completed",
            name="terminal_execute",
            tool_call_id="call-terminal",
            payload={
                "result": f"stdout={secret}",
                "data": {"exit_code": 0, "stdout": secret, "stderr": ""},
            },
        )
    )
    assert isinstance(terminal, ToolExecutionCompleted)
    assert terminal.output == ""
    assert secret not in json.dumps(terminal.metadata)

    failure = project(
        _event("runtime.turn_failed", payload={"error": f"provider rejected {secret}"})
    )
    assert isinstance(failure, ErrorEvent)
    assert secret not in failure.message


def test_gateway_projection_is_allowlisted_correlated_and_preserves_values() -> None:
    secret = "configured-provider-secret"
    projection = PublicEventProjection()
    event = _event(
        "tool.call_completed",
        name="observe",
        tool_call_id="call-1",
        payload={
            "result": "done",
            "usage": {
                "nested": [
                    {
                        "api_key": "raw-key",
                        "token": secret,
                        "path": "/private/work/result.png",
                        "uri": "https://example.invalid/file?token=raw",
                        "safe": "ready",
                    }
                ]
            },
            "data": {
                "token": secret,
                "path": "/private/work/result.png",
                "uri": "https://example.invalid/file?token=raw",
            },
        },
    )

    public = projection.project(event)

    assert public is not None
    assert public.session_id == "session-a"
    assert public.run_id == "run-a"
    assert public.turn_index == 0
    assert public.correlation_id == "call-1"
    encoded = json.dumps(public.to_dict())
    assert secret in encoded
    assert "raw-key" in encoded
    assert "/private/work" in encoded
    assert "?token=" in encoded


def test_gateway_projection_rejects_private_and_unknown_events() -> None:
    projection = PublicEventProjection()
    assert projection.project(_event("assistant.thinking", payload={"thinking": "private"})) is None
    assert (
        projection.project(_event("provider.private_payload", payload={"raw": "private"})) is None
    )


def test_tool_completed_projects_only_valid_opaque_artifact_refs() -> None:
    valid = {
        "artifact_handle": f"hm-artifact:{'a' * 32}",
        "run_id": "run-media",
        "filename": "result.png",
        "media_type": "image/png",
        "content_sha256": "b" * 64,
    }
    event = RuntimeEvent(
        type="tool.call_completed",
        session_id="session-a",
        run_id="run-media",
        turn_index=1,
        tool_call_id="call-media",
        name="render",
        payload={
            "result": "must-not-be-public",
            "data": {
                "artifacts": [
                    valid,
                    {**valid, "artifact_handle": "/tmp/raw.png"},
                    {**valid, "filename": "../escape.png"},
                ]
            },
        },
        gateway_generation=3,
    )

    projected = PublicEventProjection().project(event)

    assert projected is not None
    assert projected.artifacts == (valid,)
    assert "must-not-be-public" not in projected.content
