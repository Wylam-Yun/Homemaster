from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.config import ProviderProfileConfig
from homemaster.events.trace import json_compatible_copy
from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.providers.errors import LLMNetworkError, LLMProviderError, LLMRateLimitError
from homemaster.providers.llm_client import (
    LLMClient,
    LLMProviderResponseError,
    _map_sdk_error,
    extract_json_payload,
)


class FakeAnthropicStream:
    def __init__(
        self,
        events: list[dict[str, Any]],
        *,
        enter_error: Exception | None = None,
        final_message: dict[str, Any] | None = None,
    ) -> None:
        self._events = events
        self._enter_error = enter_error
        self._final_message = final_message

    def __enter__(self) -> FakeAnthropicStream:
        if self._enter_error is not None:
            raise self._enter_error
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def __iter__(self):
        return iter(self._events)

    async def get_final_message(self) -> Any:
        if self._final_message is not None:
            return self._final_message
        content: list[dict[str, Any]] = []
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tools: dict[int, dict[str, Any]] = {}
        stop_reason = None
        usage: dict[str, int] = {}
        for event in self._events:
            event_type = event.get("type")
            if event_type == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    tools[event.get("index", 0)] = {
                        "type": "tool_use",
                        "id": block.get("id", "call_0"),
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                        "partial_json": "",
                    }
            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_parts.append(delta.get("text", ""))
                elif delta.get("type") == "thinking_delta":
                    thinking_parts.append(delta.get("thinking", ""))
                elif delta.get("type") == "input_json_delta":
                    tools[event.get("index", 0)]["partial_json"] += delta.get("partial_json", "")
            elif event_type == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason")
                usage.update(event.get("usage", {}))
        if thinking_parts:
            content.append({"type": "thinking", "thinking": "".join(thinking_parts)})
        if text_parts:
            content.append({"type": "text", "text": "".join(text_parts)})
        for index in sorted(tools):
            tool = tools[index]
            partial_json = tool.pop("partial_json")
            if partial_json:
                tool["input"] = json.loads(partial_json)
            content.append(tool)
        return {"content": content, "stop_reason": stop_reason, "usage": usage}


class FakeAnthropicMessages:
    def __init__(
        self,
        events: list[dict[str, Any]],
        requests: list[dict[str, Any]],
        enter_error: Exception | None = None,
        final_message: dict[str, Any] | None = None,
    ) -> None:
        self._events = events
        self._requests = requests
        self._enter_error = enter_error
        self._final_message = final_message

    def stream(self, **kwargs: Any) -> FakeAnthropicStream:
        self._requests.append(kwargs)
        return FakeAnthropicStream(
            self._events,
            enter_error=self._enter_error,
            final_message=self._final_message,
        )


class FakeAnthropicClient:
    def __init__(
        self,
        events: list[dict[str, Any]],
        requests: list[dict[str, Any]],
        enter_error: Exception | None = None,
        final_message: dict[str, Any] | None = None,
    ) -> None:
        self.messages = FakeAnthropicMessages(events, requests, enter_error, final_message)


def _provider(
    *,
    api_keys: list[str] | None = None,
    auth_type: str = "api_key",
) -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="Mimo",
        kind="chat",
        api_format="anthropic",
        transport="anthropic_sdk",
        base_url="https://mimo.example/anthropic",
        model="mimo-v2-pro",
        api_keys=api_keys or ["secret-one"],
        auth_type=auth_type,
        context_window_tokens=1_000_000,
        max_output_tokens=None,
    )


def _anthropic_factory(
    events: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    constructions: list[dict[str, Any]],
    enter_errors: list[Exception | None] | None = None,
    final_message: dict[str, Any] | None = None,
) -> Any:
    def factory(**kwargs: Any) -> FakeAnthropicClient:
        constructions.append(kwargs)
        enter_error = enter_errors.pop(0) if enter_errors else None
        return FakeAnthropicClient(events, requests, enter_error, final_message)

    return factory


@pytest.mark.asyncio
async def test_anthropic_tool_arguments_come_from_sdk_final_message() -> None:
    requests: list[dict[str, Any]] = []
    constructions: list[dict[str, Any]] = []
    events = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "text_delta",
                "text": (
                    "<tool_call><function=memory>"
                    "<parameter=target>user</parameter></function></tool_call>"
                ),
            },
        },
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    ]
    final_message = {
        "content": [
            {
                "type": "tool_use",
                "id": "call_memory",
                "name": "memory",
                "input": {
                    "target": "user",
                    "action": "add",
                    "content": "用户喜欢傍晚散步",
                },
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 7},
    }
    client = LLMClient(
        _provider(),
        anthropic_client_factory=_anthropic_factory(
            events, requests, constructions, final_message=final_message
        ),
    )

    message = await client.complete([UserMessage.from_text("记住我的散步习惯")])

    assert message.text.startswith("<tool_call>")
    assert len(message.tool_calls) == 1
    assert message.tool_calls[0].arguments == {
        "target": "user",
        "action": "add",
        "content": "用户喜欢傍晚散步",
    }


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


@pytest.mark.asyncio
async def test_anthropic_auth_token_is_passed_as_bearer_credential() -> None:
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
        _provider(auth_type="auth_token"),
        anthropic_client_factory=_anthropic_factory(events, requests, constructions),
    )

    await client.complete([UserMessage.from_text("hello")])

    assert constructions == [
        {
            "auth_token": "secret-one",
            "base_url": "https://mimo.example/anthropic",
            "timeout": 60.0,
            "max_retries": 0,
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


def test_json_compatible_copy_preserves_secret_shaped_fields() -> None:
    copied = json_compatible_copy(
        {
            "api_key": "secret-one",
            "headers": {
                "Authorization": "Bearer secret-one",
                "x-api-key": "secret-one",
            },
            "safe": "visible",
        }
    )
    encoded = json.dumps(copied, ensure_ascii=False)

    assert encoded.count("secret-one") == 3
    assert copied["safe"] == "visible"


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
    image_block = ContentBlock.from_image_path(image_path)
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
    assert (
        record.outbound_images[0].content_sha256 == hashlib.sha256(b"exact-image-bytes").hexdigest()
    )


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


def test_sdk_error_uses_nonempty_message_from_exception_chain() -> None:
    outer = ConnectionError()
    outer.__cause__ = TimeoutError("upstream stream read timed out")

    mapped = _map_sdk_error(outer)

    assert isinstance(mapped, LLMNetworkError)
    assert mapped.message == "upstream stream read timed out"


def test_sdk_error_uses_exception_type_when_chain_messages_are_empty() -> None:
    mapped = _map_sdk_error(TimeoutError())

    assert isinstance(mapped, LLMNetworkError)
    assert mapped.message == "TimeoutError"
