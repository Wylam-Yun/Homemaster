"""MimoTransport — provider transport for MiMo/Anthropic-compatible APIs.

Normalizes Anthropic-style responses (content blocks with text/tool_use/thinking)
into AssistantMessage. Supports both complete and streaming modes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
)
from homemaster.providers.transport import LLMTransport, TransportDelta


class MimoTransport(LLMTransport):
    """Transport for MiMo/Anthropic-compatible APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        protocol: str = "anthropic",
        http_client: httpx.Client | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        max_output_tokens: int | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._protocol = protocol
        self._max_retries = max(0, max_retries)
        self._max_output_tokens = max_output_tokens
        self._owns_client = http_client is None
        timeout = httpx.Timeout(connect=10.0, read=timeout_s, write=15.0, pool=10.0)
        self._client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

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
        """Stream deltas from the MiMo/Anthropic API."""
        payload = self._build_request_payload(messages, tools, system_prompt=system_prompt)

        if event_sink is not None:
            from homemaster.events.runtime_events import RuntimeEvent

            event_sink.emit(RuntimeEvent(
                type="transport.request_started",
                session_id=session_id,
                run_id=run_id,
                turn_index=turn_index,
                payload={
                    "model": self._model,
                    "protocol": self._protocol,
                    "iteration": iteration,
                },
            ))

        t0 = time.perf_counter()

        if self._protocol == "anthropic":
            yield from self._stream_anthropic(
                payload, event_sink, run_id, session_id, turn_index, t0,
            )
        else:
            yield from self._stream_openai(
                payload, event_sink, run_id, session_id, turn_index, t0,
            )

    def complete(
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
    ) -> AssistantMessage:
        """Aggregate the primary streaming path into an AssistantMessage."""
        return super().complete(
            messages,
            tools,
            system_prompt=system_prompt,
            event_sink=event_sink,
            run_id=run_id,
            session_id=session_id,
            turn_index=turn_index,
            iteration=iteration,
        )

    def _stream_anthropic(
        self,
        payload: dict[str, Any],
        event_sink: Any,
        run_id: str,
        session_id: str,
        turn_index: int | None,
        t0: float,
    ) -> Iterator[TransportDelta]:
        url = f"{self._base_url}/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # For now, use non-streaming and convert to deltas.
        # Real SSE streaming can be added later.
        response, elapsed_ms = self._post_with_retries(
            url=url,
            headers=headers,
            payload=payload,
            event_sink=event_sink,
            run_id=run_id,
            session_id=session_id,
            turn_index=turn_index,
            t0=t0,
        )

        body = response.json()
        msg = self.parse_response_payload(body)

        # Yield deltas from the parsed message
        if msg.content:
            for block in msg.content:
                yield TransportDelta(type="transport.delta", text_delta=block.text)
                if event_sink is not None:
                    from homemaster.events.runtime_events import RuntimeEvent

                    event_sink.emit(RuntimeEvent(
                        type="transport.delta",
                        session_id=session_id,
                        run_id=run_id,
                        turn_index=turn_index,
                        payload={"text_delta": block.text},
                    ))

        if msg.reasoning_content:
            yield TransportDelta(type="transport.delta", reasoning_delta=msg.reasoning_content)

        for tc in msg.tool_calls:
            yield TransportDelta(type="transport.delta", tool_call_delta=tc)

        yield TransportDelta(
            type="transport.delta",
            finish_reason=msg.finish_reason,
            usage=msg.usage,
            provider_metadata=msg.provider_metadata,
        )

        if event_sink is not None:
            from homemaster.events.runtime_events import RuntimeEvent

            event_sink.emit(RuntimeEvent(
                type="transport.response_completed",
                session_id=session_id,
                run_id=run_id,
                turn_index=turn_index,
                payload={
                    "finish_reason": msg.finish_reason,
                    "tool_call_count": len(msg.tool_calls),
                },
                duration_ms=elapsed_ms,
            ))

    def _stream_openai(
        self,
        payload: dict[str, Any],
        event_sink: Any,
        run_id: str,
        session_id: str,
        turn_index: int | None,
        t0: float,
    ) -> Iterator[TransportDelta]:
        url = f"{self._base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        response, elapsed_ms = self._post_with_retries(
            url=url,
            headers=headers,
            payload=payload,
            event_sink=event_sink,
            run_id=run_id,
            session_id=session_id,
            turn_index=turn_index,
            t0=t0,
        )

        body = response.json()
        msg = self._parse_openai_response(body)

        if msg.content:
            for block in msg.content:
                yield TransportDelta(type="transport.delta", text_delta=block.text)

        if msg.reasoning_content:
            yield TransportDelta(type="transport.delta", reasoning_delta=msg.reasoning_content)

        for tc in msg.tool_calls:
            yield TransportDelta(type="transport.delta", tool_call_delta=tc)

        yield TransportDelta(
            type="transport.delta",
            finish_reason=msg.finish_reason,
            usage=msg.usage,
            provider_metadata=msg.provider_metadata,
        )

        if event_sink is not None:
            from homemaster.events.runtime_events import RuntimeEvent

            event_sink.emit(RuntimeEvent(
                type="transport.response_completed",
                session_id=session_id,
                run_id=run_id,
                turn_index=turn_index,
                payload={
                    "finish_reason": msg.finish_reason,
                    "tool_call_count": len(msg.tool_calls),
                },
                duration_ms=elapsed_ms,
            ))

    def _post_with_retries(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        event_sink: Any,
        run_id: str,
        session_id: str,
        turn_index: int | None,
        t0: float,
    ) -> tuple[httpx.Response, float]:
        request_payload = payload
        stripped_images = False
        attempts = self._max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.post(url, headers=headers, json=request_payload)
            except httpx.TimeoutException as exc:
                retryable = attempt < attempts
                _emit_transport_failure(
                    event_sink=event_sink,
                    run_id=run_id,
                    session_id=session_id,
                    turn_index=turn_index,
                    payload={
                        "attempt": attempt,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "retryable": retryable,
                    },
                )
                if retryable:
                    continue
                raise

            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            if response.status_code < 400:
                return response, elapsed_ms

            error_msg = _extract_response_error(response)
            retry_without_images = (
                attempt < attempts
                and not stripped_images
                and _is_multimodal_corruption(error_msg)
                and _payload_has_images(request_payload)
            )
            retryable = retry_without_images or (
                attempt < attempts and response.status_code >= 500
            )
            _emit_transport_failure(
                event_sink=event_sink,
                run_id=run_id,
                session_id=session_id,
                turn_index=turn_index,
                payload={
                    "attempt": attempt,
                    "status_code": response.status_code,
                    "error": error_msg,
                    "retryable": retryable,
                    "retry_without_images": retry_without_images,
                },
            )
            if retry_without_images:
                request_payload = _strip_image_blocks(payload)
                stripped_images = True
                continue
            if retryable:
                continue
            raise RuntimeError(f"Transport request failed: {error_msg}")

        raise RuntimeError("Transport request failed after retries")


    @staticmethod
    def parse_response_payload(body: dict[str, Any]) -> AssistantMessage:
        """Parse an Anthropic-style response body into AssistantMessage."""
        content_blocks = body.get("content", [])
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "thinking":
                reasoning_parts.append(block.get("thinking", ""))
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", f"call_{len(tool_calls)}"),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        content = [ContentBlock(text="".join(text_parts))] if text_parts else []
        reasoning = "".join(reasoning_parts) if reasoning_parts else None
        stop_reason = body.get("stop_reason")
        finish_reason = _normalize_finish_reason(stop_reason)

        usage = body.get("usage")
        usage_dict: dict[str, int] | None = None
        if isinstance(usage, dict):
            usage_dict = {
                k: v for k, v in usage.items()
                if isinstance(v, int)
            }

        return AssistantMessage(
            content=content,
            reasoning_content=reasoning,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage_dict,
            provider_metadata={"raw_stop_reason": stop_reason},
        )

    @staticmethod
    def _parse_openai_response(body: dict[str, Any]) -> AssistantMessage:
        """Parse an OpenAI-style response body into AssistantMessage."""
        choices = body.get("choices", [])
        if not choices:
            return AssistantMessage(finish_reason="error")

        choice = choices[0]
        message = choice.get("message", {})
        text = message.get("content", "")
        content = [ContentBlock(text=text)] if text else []

        tool_calls: list[ToolCall] = []
        for tc_data in message.get("tool_calls", []):
            fn = tc_data.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                id=tc_data.get("id", f"call_{len(tool_calls)}"),
                name=fn.get("name", ""),
                arguments=args,
            ))

        finish_reason = choice.get("finish_reason")

        usage = body.get("usage")
        usage_dict: dict[str, int] | None = None
        if isinstance(usage, dict):
            usage_dict = {k: v for k, v in usage.items() if isinstance(v, int)}

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage_dict,
        )

    def _build_request_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        """Build provider-specific request payload from normalized messages."""
        if self._protocol == "anthropic":
            return self._build_anthropic_payload(messages, tools, system_prompt=system_prompt)
        return self._build_openai_payload(messages, tools, system_prompt=system_prompt)

    def _build_anthropic_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        api_messages: list[dict[str, Any]] = []
        latest_image_message_index = _latest_image_message_index(messages)
        for index, msg in enumerate(messages):
            include_images = index == latest_image_message_index
            if hasattr(msg, "role") and msg.role == "user":
                api_messages.append({
                    "role": "user",
                    "content": _anthropic_content_blocks(
                        msg.content,
                        include_images=include_images,
                    ),
                })
            elif hasattr(msg, "role") and msg.role == "assistant":
                content_blocks: list[dict[str, Any]] = []
                if msg.reasoning_content:
                    content_blocks.append({
                        "type": "thinking",
                        "thinking": msg.reasoning_content,
                })
                if msg.content:
                    for b in msg.content:
                        block = _anthropic_content_block(
                            b,
                            include_images=include_images,
                        )
                        if block is not None:
                            content_blocks.append(block)
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content_blocks})
            elif hasattr(msg, "role") and msg.role == "tool":
                content_blocks: list[dict[str, Any]] = [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": "\n".join(b.text for b in msg.content if b.text),
                        "is_error": msg.is_error,
                }]
                for block in msg.content:
                    converted = _anthropic_content_block(
                        block,
                        include_images=include_images,
                    )
                    if converted is not None and converted.get("type") == "image":
                        content_blocks.append(converted)
                api_messages.append({"role": "user", "content": content_blocks})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
        }
        if system_prompt.strip():
            payload["system"] = system_prompt.strip()
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens
        if tools:
            payload["tools"] = tools
        return payload

    def _build_openai_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        api_messages: list[dict[str, Any]] = []
        if system_prompt.strip():
            api_messages.append({"role": "system", "content": system_prompt.strip()})
        for msg in messages:
            if hasattr(msg, "role") and msg.role == "user":
                api_messages.append({
                    "role": "user",
                    "content": msg.content[0].text if msg.content else "",
                })
            elif hasattr(msg, "role") and msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    entry["content"] = msg.content[0].text
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                api_messages.append(entry)
            elif hasattr(msg, "role") and msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content[0].text if msg.content else "",
                })

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
        }
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                }
                for t in tools
            ]
        return payload


def _emit_transport_failure(
    *,
    event_sink: Any,
    run_id: str,
    session_id: str,
    turn_index: int | None,
    payload: dict[str, Any],
) -> None:
    if event_sink is None:
        return
    from homemaster.events.runtime_events import RuntimeEvent

    event_sink.emit(RuntimeEvent(
        type="transport.request_failed",
        session_id=session_id,
        run_id=run_id,
        turn_index=turn_index,
        payload=payload,
    ))


def _is_multimodal_corruption(error_msg: str) -> bool:
    lowered = error_msg.lower()
    return "multimodal" in lowered and (
        "corrupt" in lowered or "cannot be processed" in lowered
    )


def _payload_has_images(payload: dict[str, Any]) -> bool:
    return _contains_image_block(payload)


def _contains_image_block(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "image":
            return True
        return any(_contains_image_block(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_image_block(item) for item in value)
    return False


def _strip_image_blocks(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_image_blocks(item) for key, item in value.items()}
    if isinstance(value, list):
        stripped = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "image":
                continue
            stripped.append(_strip_image_blocks(item))
        return stripped
    return value


def _normalize_finish_reason(raw: str | None) -> str | None:
    if raw is None:
        return None
    mapping = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }
    return mapping.get(raw, raw)


def _anthropic_content_blocks(
    blocks: list[ContentBlock],
    *,
    include_images: bool = True,
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for block in blocks:
        item = _anthropic_content_block(block, include_images=include_images)
        if item is not None:
            converted.append(item)
    return converted


def _anthropic_content_block(
    block: ContentBlock,
    *,
    include_images: bool = True,
) -> dict[str, Any] | None:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if not include_images:
        return None
    if block.type == "image" and isinstance(block.source, dict):
        return {"type": "image", "source": block.source}
    return None


def _latest_image_message_index(messages: list[Message]) -> int | None:
    latest: int | None = None
    for index, message in enumerate(messages):
        if any(block.type == "image" for block in getattr(message, "content", [])):
            latest = index
    return latest


def _extract_response_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        if text:
            return f"HTTP {response.status_code}: {text[:300]}"
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f"HTTP {response.status_code}: {message[:300]}"
        if isinstance(error, str) and error:
            return f"HTTP {response.status_code}: {error[:300]}"
        message = body.get("message")
        if isinstance(message, str) and message:
            return f"HTTP {response.status_code}: {message[:300]}"
    return f"HTTP {response.status_code}"
