"""SDK-backed LLM client for HomeMaster providers."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import openai

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, UserMessage
from homemaster.config import ProviderProfileConfig
from homemaster.providers.errors import (
    LLMAuthError,
    LLMClientError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)
from homemaster.providers.json_utils import extract_json_payload
from homemaster.providers.token_estimator import TokenEstimator, make_default_estimator
from homemaster.providers.transports import (
    AnthropicTransport,
    OpenAIChatTransport,
    ProviderTransport,
    TransportDelta,
    aggregate_deltas,
)

_DEFAULT_TIMEOUT_S = 60.0


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


class LLMClient:
    """Provider client that owns SDK clients, key rotation, and retries."""

    def __init__(
        self,
        provider: ProviderProfileConfig,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        event_sink: Any = None,
        run_id: str = "",
        max_image_strip_attempts: int = 1,
        anthropic_client_factory: Any = None,
        openai_client_factory: Any = None,
    ) -> None:
        self._provider = provider
        self._timeout_s = timeout_s
        self._event_sink = event_sink
        self._run_id = run_id
        self._max_image_strip_attempts = max_image_strip_attempts
        self._transport = make_transport(provider)
        self._token_estimator = make_default_estimator(provider)
        self._anthropic_client_factory = anthropic_client_factory or anthropic.Anthropic
        self._openai_client_factory = openai.OpenAI
        if openai_client_factory is not None:
            self._openai_client_factory = openai_client_factory

    @property
    def token_estimator(self) -> TokenEstimator:
        return self._token_estimator

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
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AssistantMessage:
        deltas = list(
            self.stream(
                messages,
                tools,
                system_prompt=system_prompt,
                event_sink=event_sink,
                run_id=run_id,
                session_id=session_id,
                turn_index=turn_index,
                iteration=iteration,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
        )
        return aggregate_deltas(deltas)

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
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Iterator[TransportDelta]:
        sink = event_sink or self._event_sink
        effective_run_id = run_id or self._run_id
        attempts: list[dict[str, Any]] = []
        last_error: LLMClientError | None = None
        effective_messages = messages
        for strip_attempt in range(self._max_image_strip_attempts + 1):
            stripped_images = strip_attempt > 0
            retry_with_stripped_images = False
            for key_index, api_key in enumerate(self._provider.api_keys, start=1):
                started = time.perf_counter()
                try:
                    kwargs = self._transport.build_create_kwargs(
                        model=self._provider.model,
                        messages=effective_messages,
                        tools=tools,
                        system_prompt=system_prompt,
                        max_output_tokens=max_output_tokens or self._provider.max_output_tokens,
                        temperature=temperature,
                    )
                    _emit(
                        sink,
                        "transport.request_started",
                        session_id=session_id,
                        run_id=effective_run_id,
                        turn_index=turn_index,
                        payload={
                            "model": self._provider.model,
                            "api_format": self._provider.api_format,
                            "transport": self._provider.transport,
                            "iteration": iteration,
                            "key_index": key_index,
                            "stripped_images": stripped_images,
                        },
                    )
                    yield from self._stream_once(api_key=api_key, kwargs=kwargs)
                    attempts.append(
                        {
                            "key_index": key_index,
                            "status": "ok",
                            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                            "stripped_images": stripped_images,
                        }
                    )
                    return
                except LLMClientError as exc:
                    last_error = exc
                    attempts.append(
                        {
                            "key_index": key_index,
                            "status": exc.error_type,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                            "stripped_images": stripped_images,
                        }
                    )
                    _emit(
                        sink,
                        "transport.request_failed",
                        session_id=session_id,
                        run_id=effective_run_id,
                        turn_index=turn_index,
                        payload={
                            "error": exc.message,
                            "error_type": exc.error_type,
                            "key_index": key_index,
                            "stripped_images": stripped_images,
                        },
                    )
                    if isinstance(exc, (LLMAuthError, LLMRateLimitError, LLMNetworkError)):
                        continue
                    if (
                        strip_attempt < self._max_image_strip_attempts
                        and _is_multimodal_corruption(exc.message)
                    ):
                        effective_messages = _strip_message_images(messages)
                        retry_with_stripped_images = True
                        break
                    raise
            if retry_with_stripped_images:
                continue
            break
        raise last_error or LLMClientError(error_type="no_keys", message="no API keys configured")

    def complete_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
    ) -> LLMJsonResponse:
        started = time.perf_counter()
        message = self.complete([UserMessage.from_text(prompt)], temperature=temperature)
        content = message.text
        if message.finish_reason == "length":
            raw_content = content or message.reasoning_content
            raise LLMProviderError(
                error_type="provider_response_error",
                message="response_truncated: provider stopped before completing JSON output",
                raw_content=raw_content,
            )
        if not content and message.reasoning_content:
            raise LLMProviderError(
                error_type="provider_response_error",
                message="response_missing_text: provider response contained only reasoning content",
                raw_content=message.reasoning_content,
            )
        payload = extract_json_payload(content)
        return LLMJsonResponse(
            provider_name=self._provider.name,
            model=self._provider.model,
            protocol=self._provider.api_format,
            content=content,
            payload=payload,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            attempts=({"key_index": 1, "status": "ok"},),
            finish_reason=message.finish_reason,
        )

    def close(self) -> None:
        """SDK clients are scoped per call; no persistent handle to close."""

    def _stream_once(
        self,
        *,
        api_key: str,
        kwargs: dict[str, Any],
    ) -> Iterator[TransportDelta]:
        try:
            if self._provider.api_format == "anthropic":
                client_kwargs: dict[str, Any] = {
                    "api_key": api_key,
                    "base_url": self._provider.base_url,
                    "timeout": self._timeout_s,
                    "max_retries": 2,
                }
                if self._anthropic_client_factory is anthropic.Anthropic:
                    client_kwargs["http_client"] = httpx.Client(
                        timeout=self._timeout_s,
                        trust_env=False,
                    )
                client = self._anthropic_client_factory(**client_kwargs)
                try:
                    with client.messages.stream(**kwargs) as stream:
                        yield from self._transport.iter_stream_deltas(stream)
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
                return

            client_kwargs = {
                "api_key": api_key,
                "base_url": self._provider.base_url,
                "timeout": self._timeout_s,
                "max_retries": 2,
            }
            if self._openai_client_factory is openai.OpenAI:
                client_kwargs["http_client"] = httpx.Client(
                    timeout=self._timeout_s,
                    trust_env=False,
                )
            client = self._openai_client_factory(**client_kwargs)
            try:
                with client.chat.completions.create(
                    stream=True,
                    stream_options={"include_usage": True},
                    **kwargs,
                ) as stream:
                    yield from self._transport.iter_stream_deltas(stream)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            raise _map_sdk_error(exc) from exc


def make_transport(provider: ProviderProfileConfig) -> ProviderTransport:
    if provider.api_format == "anthropic":
        return AnthropicTransport()
    if provider.api_format == "openai":
        return OpenAIChatTransport()
    raise LLMClientError(
        error_type="unsupported_provider",
        message=f"unsupported provider api_format: {provider.api_format}",
    )


def _map_sdk_error(exc: Exception) -> LLMClientError:
    name = type(exc).__name__.lower()
    message = _extract_error_message(exc)
    if "authentication" in name or "permission" in name or "unauthorized" in message.lower():
        return LLMAuthError(error_type="auth_error", message=message)
    if "ratelimit" in name or "rate_limit" in name:
        return LLMRateLimitError(error_type="rate_limit", message=message)
    if "timeout" in name or "network" in name or "connection" in name:
        return LLMNetworkError(error_type="network_error", message=message)
    return LLMProviderError(error_type="provider_error", message=message, raw_content=message)


def _extract_error_message(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
            if isinstance(error, str):
                return error
            if isinstance(payload.get("message"), str):
                return payload["message"]
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message
    return str(exc)


def _is_multimodal_corruption(message: str) -> bool:
    lowered = message.lower()
    return "multimodal" in lowered and ("corrupt" in lowered or "process" in lowered)


def _strip_message_images(messages: list[Message]) -> list[Message]:
    stripped: list[Message] = []
    for message in messages:
        content = [
            (
                ContentBlock(text="[image omitted after provider rejected multimodal payload]")
                if block.type == "image"
                else block
            )
            for block in message.content
        ]
        stripped.append(message.model_copy(update={"content": content}))
    return stripped


def _emit(
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

    event_sink.emit(
        RuntimeEvent(
            type=event_type,
            session_id=session_id,
            run_id=run_id,
            turn_index=turn_index,
            payload=payload,
        )
    )


LLMProviderResponseError = LLMProviderError
