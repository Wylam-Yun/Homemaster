"""Provider helpers shared by the legacy client and the AgentScope substrate.

These functions were extracted from ``providers/llm_client.py`` so the
substrate path does not depend on the legacy client module it replaces.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from homemaster.agent.messages import Message
from homemaster.providers.attempts import (
    OutboundImageBinding,
    ProviderAttemptRecord,
)
from homemaster.providers.errors import (
    LLMAuthError,
    LLMClientError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)


@dataclass(frozen=True)
class LLMJsonResponse:
    provider_name: str
    model: str
    protocol: str
    content: str
    payload: dict[str, Any]
    elapsed_ms: float
    attempts: tuple[dict[str, Any], ...]
    finish_reason: str | None = None

    @property
    def json_payload(self) -> dict[str, Any]:
        return self.payload

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "model": self.model,
            "protocol": self.protocol,
            "elapsed_ms": self.elapsed_ms,
            "attempts": list(self.attempts),
            "finish_reason": self.finish_reason,
        }


def map_sdk_error(exc: Exception) -> LLMClientError:
    if isinstance(exc, LLMClientError):
        return exc
    name = type(exc).__name__.lower()
    message = _extract_error_message(exc)
    if "authentication" in name or "permission" in name or "unauthorized" in message.lower():
        return LLMAuthError(
            error_type="auth_error", message=message, cause_code="authentication_rejected"
        )
    if "ratelimit" in name or "rate_limit" in name:
        return LLMRateLimitError(error_type="rate_limit", message=message, cause_code="rate_limit")
    if "timeout" in name or "network" in name or "connection" in name:
        return LLMNetworkError(
            error_type="network_error", message=message, cause_code="transient_network"
        )
    return LLMProviderError(
        error_type="provider_error",
        message=message,
        raw_content=message,
        cause_code="provider_error",
    )


def _extract_error_message(exc: Exception) -> str:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if response is not None:
            try:
                payload = response.json()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    if error["message"].strip():
                        return error["message"]
                if isinstance(error, str) and error.strip():
                    return error
                message = payload.get("message")
                if isinstance(message, str) and message.strip():
                    return message
        message = getattr(current, "message", None)
        if isinstance(message, str) and message.strip():
            return message
        rendered = str(current)
        if rendered.strip():
            return rendered
        current = current.__cause__ or current.__context__
    return type(exc).__name__


def request_sha256(kwargs: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            kwargs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def default_attempt_id(run_id: str, iteration: int | None) -> str:
    return f"{run_id or 'provider'}:attempt-{(iteration or 0) + 1:04d}"


def attempt_record(
    *,
    messages: list[Message],
    request_body: dict[str, Any],
    model_attempt_id: str,
    request_sha256: str,
    stripped_images: bool,
    response_completed: bool,
    error: LLMClientError | None,
) -> ProviderAttemptRecord:
    candidates: list[tuple[str, OutboundImageBinding]] = []
    for message_index, message in enumerate(messages):
        for block_index, block in enumerate(message.content):
            if block.type != "image" or not isinstance(block.source, dict):
                continue
            data = block.source.get("data")
            if not isinstance(data, str):
                continue
            try:
                content = base64.b64decode(data, validate=True)
            except ValueError:
                content = data.encode("ascii", errors="replace")
            content_sha256 = hashlib.sha256(content).hexdigest()
            candidates.append(
                (
                    content_sha256,
                    OutboundImageBinding(
                        message_index=message_index,
                        block_index=block_index,
                        content_sha256=content_sha256,
                    ),
                )
            )
    serialized_counts = Counter(
        hashlib.sha256(content).hexdigest() for content in _serialized_image_contents(request_body)
    )
    bindings: list[OutboundImageBinding] = []
    for content_sha256, binding in reversed(candidates):
        if serialized_counts[content_sha256] <= 0:
            continue
        serialized_counts[content_sha256] -= 1
        bindings.append(binding)
    bindings.reverse()
    return ProviderAttemptRecord(
        model_attempt_id=model_attempt_id,
        request_sha256=request_sha256,
        outbound_images=tuple(bindings),
        stripped_images=stripped_images or len(bindings) != len(candidates),
        response_completed=response_completed,
        error_type=error.error_type if error is not None else None,
        cause_code=error.cause_code if error is not None else None,
    )


def _serialized_image_contents(value: Any) -> list[bytes]:
    contents: list[bytes] = []
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            image_url = value.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if isinstance(url, str) and ";base64," in url:
                contents.append(_image_bytes(url.split(";base64,", 1)[1]))
                return contents
        source = value.get("source")
        if value.get("type") == "image" and isinstance(source, dict):
            data = source.get("data")
            if isinstance(data, str):
                contents.append(_image_bytes(data))
                return contents
        for item in value.values():
            contents.extend(_serialized_image_contents(item))
    elif isinstance(value, list | tuple):
        for item in value:
            contents.extend(_serialized_image_contents(item))
    return contents


def _image_bytes(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except ValueError:
        return data.encode("ascii", errors="replace")


async def emit_event(
    event_sink: Any,
    event_type: str,
    *,
    session_id: str,
    run_id: str,
    turn_index: int | None,
    payload: dict[str, Any],
) -> None:
    if event_sink is None:
        return
    from homemaster.events.runtime_events import RuntimeEvent

    event = RuntimeEvent(
        type=event_type,
        session_id=session_id,
        run_id=run_id,
        turn_index=turn_index,
        payload=payload,
    )
    aemit = getattr(event_sink, "aemit", None)
    if callable(aemit):
        await aemit(event)
        return
    value = event_sink.emit(event)
    if inspect.isawaitable(value):
        await value


__all__ = [
    "LLMJsonResponse",
    "attempt_record",
    "default_attempt_id",
    "emit_event",
    "map_sdk_error",
    "request_sha256",
]
