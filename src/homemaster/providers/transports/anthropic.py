"""Anthropic Messages API transport conversion."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, ToolCall
from homemaster.providers.errors import LLMProviderError
from homemaster.providers.transports.base import ProviderTransport
from homemaster.providers.transports.types import TransportDelta


class AnthropicTransport(ProviderTransport):
    """Convert normalized HomeMaster messages to/from Anthropic Messages shapes."""

    def build_create_kwargs(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _anthropic_messages(messages),
        }
        if system_prompt.strip():
            kwargs["system"] = system_prompt.strip()
        kwargs["max_tokens"] = max_output_tokens or 4096
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools
        return kwargs

    def normalize_response(self, response: Any) -> AssistantMessage:
        content_blocks = _get(response, "content", [])
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks or []:
            block_type = _get(block, "type", "")
            if block_type == "text":
                text_parts.append(str(_get(block, "text", "")))
            elif block_type == "thinking":
                reasoning_parts.append(str(_get(block, "thinking", "")))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(_get(block, "id", f"call_{len(tool_calls)}")),
                        name=str(_get(block, "name", "")),
                        arguments=dict(_get(block, "input", {}) or {}),
                    )
                )
        stop_reason = _get(response, "stop_reason", None)
        return AssistantMessage(
            content=[ContentBlock(text="".join(text_parts))] if text_parts else [],
            reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
            tool_calls=tool_calls,
            finish_reason=_normalize_anthropic_stop(stop_reason),
            usage=_usage_to_dict(_get(response, "usage", None)),
            provider_metadata={"raw_stop_reason": stop_reason},
        )

    def iter_stream_deltas(self, stream: Any) -> Iterator[TransportDelta]:
        tool_blocks: dict[int, dict[str, Any]] = {}
        message_started = False
        for event in stream:
            event_type = _get(event, "type", "")
            if event_type == "message_start":
                message_started = True
            elif event_type == "content_block_start":
                index = int(_get(event, "index", 0) or 0)
                block = _get(event, "content_block", None)
                if _get(block, "type", "") == "tool_use":
                    tool_blocks[index] = {
                        "id": _get(block, "id", f"call_{index}"),
                        "name": _get(block, "name", ""),
                        "json": "",
                        "input": _get(block, "input", None),
                    }
            elif event_type == "content_block_delta":
                index = int(_get(event, "index", 0) or 0)
                delta = _get(event, "delta", None)
                delta_type = _get(delta, "type", "")
                if delta_type == "text_delta":
                    yield TransportDelta(type="transport.delta", text_delta=_get(delta, "text", ""))
                elif delta_type == "thinking_delta":
                    yield TransportDelta(
                        type="transport.delta",
                        reasoning_delta=_get(delta, "thinking", ""),
                    )
                elif delta_type == "input_json_delta" and index in tool_blocks:
                    tool_blocks[index]["json"] += str(_get(delta, "partial_json", ""))
            elif event_type == "content_block_stop":
                index = int(_get(event, "index", 0) or 0)
                tool = tool_blocks.pop(index, None)
                if tool is not None:
                    partial_json = str(tool.get("json", ""))
                    if partial_json.strip():
                        arguments = _loads_json_object(partial_json)
                    else:
                        arguments = tool.get("input")
                    if not isinstance(arguments, dict):
                        arguments = {}
                    yield TransportDelta(
                        type="transport.delta",
                        tool_call_delta=ToolCall(
                            id=str(tool.get("id") or f"call_{index}"),
                            name=str(tool.get("name") or ""),
                            arguments=arguments,
                        ),
                    )
            elif event_type == "message_delta":
                if not message_started:
                    raise LLMProviderError(
                        error_type="stream_protocol_error",
                        message="provider sent message_delta before message_start",
                        cause_code="message_delta_before_message_start",
                    )
                delta = _get(event, "delta", None)
                stop_reason = _get(delta, "stop_reason", None)
                usage = _usage_to_dict(_get(event, "usage", None))
                if stop_reason or usage:
                    yield TransportDelta(
                        type="transport.delta",
                        finish_reason=_normalize_anthropic_stop(stop_reason),
                        usage=usage,
                        provider_metadata={"raw_stop_reason": stop_reason},
                    )
            elif event_type == "message_stop":
                usage = _usage_to_dict(_get(event, "usage", None))
                if usage:
                    yield TransportDelta(type="transport.delta", usage=usage)


def _anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    api_messages: list[dict[str, Any]] = []
    latest_image_index = _latest_image_message_index(messages)
    for index, msg in enumerate(messages):
        role = getattr(msg, "role", "")
        include_images = index == latest_image_index
        if role == "user":
            api_messages.append(
                {"role": "user", "content": _anthropic_content_blocks(msg.content, include_images)}
            )
        elif role == "assistant":
            content: list[dict[str, Any]] = []
            if msg.reasoning_content:
                content.append({"type": "thinking", "thinking": msg.reasoning_content})
            content.extend(_anthropic_content_blocks(msg.content, include_images))
            for tool_call in msg.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.arguments,
                    }
                )
            api_messages.append({"role": "assistant", "content": content})
        elif role == "tool":
            text = "\n".join(block.text for block in msg.content if block.text)
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": text,
                    "is_error": msg.is_error,
                }
            ]
            for block in msg.content:
                converted = _anthropic_content_block(block, include_images)
                if converted is not None and converted.get("type") == "image":
                    content.append(converted)
            api_messages.append({"role": "user", "content": content})
    return api_messages


def _anthropic_content_blocks(
    blocks: list[ContentBlock],
    include_images: bool,
) -> list[dict[str, Any]]:
    converted = [
        block
        for item in blocks
        if (block := _anthropic_content_block(item, include_images)) is not None
    ]
    return converted or [{"type": "text", "text": ""}]


def _anthropic_content_block(block: ContentBlock, include_images: bool) -> dict[str, Any] | None:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "image" and include_images and isinstance(block.source, dict):
        return {"type": "image", "source": block.source}
    if block.type == "image":
        return {"type": "text", "text": "[image omitted from older context]"}
    return None


def _latest_image_message_index(messages: list[Message]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if any(block.type == "image" for block in getattr(messages[index], "content", [])):
            return index
    return None


def _normalize_anthropic_stop(raw: Any) -> str | None:
    if raw is None:
        return None
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }.get(str(raw), str(raw))


def _usage_to_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    result: dict[str, int] = {}
    for key in keys:
        value = _get(usage, key, None)
        if isinstance(value, int):
            result[key] = value
    return result or None


def _loads_json_object(value: str) -> dict[str, Any]:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
