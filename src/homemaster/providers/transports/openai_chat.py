"""OpenAI Chat Completions transport conversion."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, ToolCall
from homemaster.providers.transports.base import ProviderTransport
from homemaster.providers.transports.types import TransportDelta


class OpenAIChatTransport(ProviderTransport):
    """Convert normalized HomeMaster messages to/from OpenAI chat completions."""

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
        api_messages: list[dict[str, Any]] = []
        if system_prompt.strip():
            api_messages.append({"role": "system", "content": system_prompt.strip()})
        for msg in messages:
            role = getattr(msg, "role", "")
            if role == "user":
                api_messages.append({"role": "user", "content": _content_value(msg.content)})
            elif role == "assistant":
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": _content_value(msg.content),
                }
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                            },
                        }
                        for tool_call in msg.tool_calls
                    ]
                api_messages.append(entry)
            elif role == "tool":
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": _content_value(msg.content),
                    }
                )
        kwargs: dict[str, Any] = {"model": model, "messages": api_messages}
        if max_output_tokens is not None:
            kwargs["max_tokens"] = max_output_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
                for tool in tools
            ]
        return kwargs

    def normalize_response(self, response: Any) -> AssistantMessage:
        choices = _get(response, "choices", []) or []
        if not choices:
            return AssistantMessage(finish_reason="error")
        choice = choices[0]
        message = _get(choice, "message", {})
        text = _get(message, "content", "") or ""
        tool_calls = [
            _openai_tool_call_to_normalized(item, index)
            for index, item in enumerate(_get(message, "tool_calls", []) or [])
        ]
        return AssistantMessage(
            content=[ContentBlock(text=text)] if text else [],
            tool_calls=tool_calls,
            finish_reason=_get(choice, "finish_reason", None),
            usage=_usage_to_dict(_get(response, "usage", None)),
        )

    def iter_stream_deltas(self, stream: Any) -> Iterator[TransportDelta]:
        decoder = _OpenAIStreamDecoder()
        for chunk in stream:
            yield from decoder.consume(chunk)

    async def aiter_stream_deltas(self, stream: Any) -> AsyncIterator[TransportDelta]:
        decoder = _OpenAIStreamDecoder()
        async for chunk in stream:
            for delta in decoder.consume(chunk):
                yield delta


class _OpenAIStreamDecoder:
    def __init__(self) -> None:
        self.tool_parts: dict[int, dict[str, str]] = {}

    def consume(self, chunk: Any) -> Iterator[TransportDelta]:
        usage = _usage_to_dict(_get(chunk, "usage", None))
        if usage:
            yield TransportDelta(type="transport.delta", usage=usage)
        choices = _get(chunk, "choices", []) or []
        if not choices:
            return
        choice = choices[0]
        delta = _get(choice, "delta", {})
        content = _get(delta, "content", None)
        if content:
            yield TransportDelta(type="transport.delta", text_delta=content)
        reasoning = _get(delta, "reasoning_content", None)
        if reasoning:
            yield TransportDelta(type="transport.delta", reasoning_delta=reasoning)
        for item in _get(delta, "tool_calls", []) or []:
            index = int(_get(item, "index", 0) or 0)
            function = _get(item, "function", {})
            part = self.tool_parts.setdefault(
                index,
                {"id": "", "name": "", "arguments": ""},
            )
            part["id"] = str(_get(item, "id", part["id"]) or part["id"])
            part["name"] += str(_get(function, "name", "") or "")
            part["arguments"] += str(_get(function, "arguments", "") or "")
        finish_reason = _get(choice, "finish_reason", None)
        if finish_reason:
            for index in sorted(self.tool_parts):
                part = self.tool_parts[index]
                yield TransportDelta(
                    type="transport.delta",
                    tool_call_delta=ToolCall(
                        id=part["id"] or f"call_{index}",
                        name=part["name"],
                        arguments=_loads_json_object(part["arguments"]),
                    ),
                )
            self.tool_parts.clear()
            yield TransportDelta(type="transport.delta", finish_reason=finish_reason)


def _text_content(blocks: list[ContentBlock]) -> str:
    return "\n".join(block.text for block in blocks if block.text)


def _content_value(blocks: list[ContentBlock]) -> str | list[dict[str, Any]]:
    """Keep image observations in the OpenAI request instead of dropping them."""

    if not any(block.type == "image" for block in blocks):
        return _text_content(blocks)
    content: list[dict[str, Any]] = []
    for block in blocks:
        if block.type == "text":
            content.append({"type": "text", "text": block.text})
            continue
        if not isinstance(block.source, dict):
            continue
        media_type = str(block.source.get("media_type") or "image/png")
        data = block.source.get("data")
        if isinstance(data, str):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                }
            )
    return content


def _openai_tool_call_to_normalized(item: Any, index: int) -> ToolCall:
    function = _get(item, "function", {})
    return ToolCall(
        id=str(_get(item, "id", f"call_{index}")),
        name=str(_get(function, "name", "")),
        arguments=_loads_json_object(str(_get(function, "arguments", "{}") or "{}")),
    )


def _loads_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage_to_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _get(usage, key, None)
        if isinstance(value, int):
            result[key] = value
    return result or None


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
