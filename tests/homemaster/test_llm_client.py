from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.config import ProviderProfileConfig
from homemaster.events.trace import sanitize_for_log
from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.providers.errors import LLMProviderError, LLMRateLimitError
from homemaster.providers.llm_client import (
    LLMClient,
    LLMProviderResponseError,
    extract_json_payload,
)


class FakeAnthropicStream:
    def __init__(
        self,
        events: list[dict[str, Any]],
        *,
        enter_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._enter_error = enter_error

    def __enter__(self) -> FakeAnthropicStream:
        if self._enter_error is not None:
            raise self._enter_error
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def __iter__(self):
        return iter(self._events)


class FakeAnthropicMessages:
    def __init__(
        self,
        events: list[dict[str, Any]],
        requests: list[dict[str, Any]],
        enter_error: Exception | None = None,
    ) -> None:
        self._events = events
        self._requests = requests
        self._enter_error = enter_error

    def stream(self, **kwargs: Any) -> FakeAnthropicStream:
        self._requests.append(kwargs)
        return FakeAnthropicStream(self._events, enter_error=self._enter_error)


class FakeAnthropicClient:
    def __init__(
        self,
        events: list[dict[str, Any]],
        requests: list[dict[str, Any]],
        enter_error: Exception | None = None,
    ) -> None:
        self.messages = FakeAnthropicMessages(events, requests, enter_error)


def _provider(*, api_keys: list[str] | None = None) -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="Mimo",
        kind="chat",
        api_format="anthropic",
        transport="anthropic_sdk",
        base_url="https://mimo.example/anthropic",
        model="mimo-v2-pro",
        api_keys=api_keys or ["secret-one"],
        context_window_tokens=1_000_000,
        max_output_tokens=None,
    )


def _anthropic_factory(
    events: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    constructions: list[dict[str, Any]],
    enter_errors: list[Exception | None] | None = None,
) -> Any:
    def factory(**kwargs: Any) -> FakeAnthropicClient:
        constructions.append(kwargs)
        enter_error = enter_errors.pop(0) if enter_errors else None
        return FakeAnthropicClient(events, requests, enter_error)

    return factory


@pytest.mark.asyncio
async def test_sdk_json_client_sends_anthropic_stream_request_and_parses_json() -> None:
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "```json\n"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "text_delta",
                "text": (
                    '{"task_type":"check_presence","target":"药盒",'
                    '"delivery_target":null,"location_hint":"桌子那边",'
                    '"success_criteria":["确认药盒是否在桌子附近"],'
                    '"needs_clarification":false,'
                    '"clarification_question":null,"confidence":0.9}'
                    "\n```"
                ),
            },
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"input_tokens": 12, "output_tokens": 8},
        },
    ]
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(events, requests, constructions),
    )

    response = await client.complete_json("prompt body", temperature=0.0)

    assert response.json_payload["task_type"] == "check_presence"
    assert response.finish_reason == "stop"
    assert constructions == [
        {
            "api_key": "secret-one",
            "base_url": "https://mimo.example/anthropic",
            "timeout": 60.0,
            "max_retries": 0,
        }
    ]
    assert requests == [
        {
            "model": "mimo-v2-pro",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "prompt body"}]}],
            "max_tokens": 4096,
            "temperature": 0.0,
        }
    ]


def test_extract_json_payload_accepts_plain_json() -> None:
    payload = extract_json_payload('{"task_type": "check_presence", "target": "药盒"}')

    assert payload == {"task_type": "check_presence", "target": "药盒"}


@pytest.mark.asyncio
async def test_successful_stream_emits_external_response_completed_event() -> None:
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "ok"},
        },
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
    ]
    emitted: list[Any] = []

    class Sink:
        def emit(self, event: Any) -> None:
            emitted.append(event)

    client = LLMClient(
        _provider(),
        event_sink=Sink(),
        run_id="run-a",
        anthropic_client_factory=_anthropic_factory(events, requests, constructions),
    )
    await client.complete([], session_id="session-a", turn_index=0, iteration=1)

    assert [event.type for event in emitted] == [
        "transport.request_started",
        "transport.response_completed",
    ]
    assert emitted[-1].payload["model"] == "mimo-v2-pro"
    assert emitted[-1].payload["status"] == "ok"


@pytest.mark.asyncio
async def test_llm_client_rejects_anthropic_thinking_without_text_for_json_parsing() -> None:
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "thinking_delta",
                "thinking": '{"query_text":"水杯","source_filter":["object_memory"]}',
            },
        },
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
    ]
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(events, requests, constructions),
    )

    with pytest.raises(LLMProviderResponseError) as exc_info:
        await client.complete_json("prompt body", temperature=0.0)

    assert exc_info.value.error_type == "provider_response_error"
    assert "response_missing_text" in exc_info.value.message
    assert "query_text" in (exc_info.value.raw_content or "")


@pytest.mark.asyncio
async def test_llm_client_treats_provider_token_stop_as_truncation() -> None:
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": '{"task_type":"check_presence"'},
        },
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
    ]
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(events, requests, constructions),
    )

    with pytest.raises(LLMProviderResponseError) as exc_info:
        await client.complete_json("prompt body", temperature=0.0)

    assert exc_info.value.error_type == "provider_response_error"
    assert "response_truncated" in exc_info.value.message
    assert exc_info.value.raw_content == '{"task_type":"check_presence"'


@pytest.mark.asyncio
async def test_llm_client_treats_thinking_only_provider_token_stop_as_truncation() -> None:
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "thinking_delta",
                "thinking": "reasoning budget consumed the response",
            },
        },
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
    ]
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(events, requests, constructions),
    )

    with pytest.raises(LLMProviderResponseError) as exc_info:
        await client.complete_json("prompt body", temperature=0.0)

    assert exc_info.value.error_type == "provider_response_error"
    assert "response_truncated" in exc_info.value.message
    assert "reasoning budget" in (exc_info.value.raw_content or "")


def test_sanitize_for_log_redacts_secret_fields() -> None:
    sanitized = sanitize_for_log(
        {
            "api_key": "secret-one",
            "headers": {
                "Authorization": "Bearer secret-one",
                "x-api-key": "secret-one",
            },
            "safe": "visible",
        }
    )
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert "secret-one" not in encoded
    assert sanitized["safe"] == "visible"


@pytest.mark.asyncio
async def test_llm_client_exposes_multimodal_corruption_without_stripping_images(
    tmp_path,
) -> None:
    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"fake-png")
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "ok"},
        },
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
    ]
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(
            events,
            requests,
            constructions,
            enter_errors=[
                RuntimeError("Multimodal data is corrupted or cannot be processed."),
                None,
            ],
        ),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        await client.complete(
            [
                UserMessage(
                    content=[
                        ContentBlock(text="Look"),
                        ContentBlock.from_image_path(image_path),
                    ]
                )
            ]
        )

    assert exc_info.value.error_type == "provider_error"
    assert "Multimodal data is corrupted" in exc_info.value.message
    assert len(requests) == 1
    assert requests[0]["messages"][0]["content"][1]["type"] == "image"


@pytest.mark.asyncio
async def test_llm_client_records_one_call_scoped_attempt_with_actual_image_hash(
    tmp_path,
) -> None:
    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"exact-image-bytes")
    image_block = ContentBlock.from_image_path(image_path).model_copy(
        update={
            "metadata": {
                "path": str(image_path),
                "frame_binding_id": "frame-bound-exact",
            }
        }
    )
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    sink = ListProviderAttemptSink()
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(
            [
                {"type": "message_start", "message": {}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "ok"},
                },
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            ],
            requests,
            constructions,
        ),
    )

    deltas = [
        delta
        async for delta in client.stream(
            [
                UserMessage(
                    content=[
                        ContentBlock(text="Look"),
                        image_block,
                    ]
                )
            ],
            attempt_sink=sink,
            model_attempt_id="run-1:attempt-0001",
        )
    ]

    assert deltas
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.model_attempt_id == "run-1:attempt-0001"
    assert record.response_completed is True
    assert record.stripped_images is False
    assert record.error_type is None
    assert (
        record.request_sha256
        == hashlib.sha256(
            json.dumps(
                requests[0],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    assert len(record.outbound_images) == 1
    assert record.outbound_images[0].frame_binding_id == "frame-bound-exact"
    assert (
        record.outbound_images[0].content_sha256 == hashlib.sha256(b"exact-image-bytes").hexdigest()
    )
    assert "frame-bound-exact" not in json.dumps(requests[0], ensure_ascii=False)


@pytest.mark.asyncio
async def test_llm_client_selects_one_requested_key_without_internal_rotation() -> None:
    class RateLimitError(RuntimeError):
        pass

    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    client = LLMClient(
        _provider(api_keys=["key-one", "key-two"]),
        anthropic_client_factory=_anthropic_factory(
            [],
            requests,
            constructions,
            enter_errors=[RateLimitError("rate limited")],
        ),
    )

    with pytest.raises(LLMRateLimitError):
        _ = [
            delta
            async for delta in client.stream(
                [UserMessage.from_text("hello")],
                provider_key_index=1,
            )
        ]

    assert len(constructions) == 1
    assert constructions[0]["api_key"] == "key-two"
    assert len(requests) == 1
