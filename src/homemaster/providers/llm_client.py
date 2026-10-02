"""SDK-backed LLM client for HomeMaster providers."""

from __future__ import annotations

import contextlib
import inspect
import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx
import openai

from homemaster.agent.messages import (
    AssistantMessage,
    Message,
    UserMessage,
)
from homemaster.config import ProviderProfileConfig
from homemaster.providers._shared import (
    LLMJsonResponse,
)
from homemaster.providers._shared import (
    attempt_record as _attempt_record,
)
from homemaster.providers._shared import (
    default_attempt_id as _default_attempt_id,
)
from homemaster.providers._shared import (
    emit_event as _emit,
)
from homemaster.providers._shared import (
    map_sdk_error as _map_sdk_error,
)
from homemaster.providers._shared import (
    request_sha256 as _request_sha256,
)
from homemaster.providers.attempts import (
    ProviderAttemptSink,
)
from homemaster.providers.errors import (
    LLMClientError,
    LLMProviderError,
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


class LLMClient:
    """Provider client that owns SDK clients and sends one frozen request attempt."""

    def __init__(
        self,
        provider: ProviderProfileConfig,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        event_sink: Any = None,
        run_id: str = "",
        max_image_strip_attempts: int = 0,
        anthropic_client_factory: Any = None,
        openai_client_factory: Any = None,
    ) -> None:
        self._provider = provider
        self._timeout_s = timeout_s
        self._event_sink = event_sink
        self._run_id = run_id
        del max_image_strip_attempts
        self._transport = make_transport(provider)
        self._token_estimator = make_default_estimator(provider)
        self._anthropic_client_factory = anthropic_client_factory or anthropic.AsyncAnthropic
        self._openai_client_factory = openai.AsyncOpenAI
        if openai_client_factory is not None:
            self._openai_client_factory = openai_client_factory

    @property
    def token_estimator(self) -> TokenEstimator:
        return self._token_estimator

    async def complete(
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
        deltas = [
            delta
            async for delta in self.stream(
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
        ]
        return aggregate_deltas(deltas)

    async def stream(
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
        attempt_sink: ProviderAttemptSink | None = None,
        model_attempt_id: str | None = None,
        provider_key_index: int = 0,
    ) -> AsyncIterator[TransportDelta]:
        sink = event_sink or self._event_sink
        effective_run_id = run_id or self._run_id
        if not self._provider.api_keys:
            raise LLMClientError(
                error_type="no_keys",
                message="no API keys configured",
                cause_code="no_keys",
            )
        selected_key_index = min(max(provider_key_index, 0), len(self._provider.api_keys) - 1)
        key_index = selected_key_index + 1
        api_key = self._provider.api_keys[selected_key_index]
        request_sha256 = ""
        recorded = False
        kwargs: dict[str, Any] = {}
        try:
            kwargs = self._transport.build_create_kwargs(
                model=self._provider.model,
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                max_output_tokens=max_output_tokens or self._provider.max_output_tokens,
                temperature=temperature,
            )
            request_sha256 = _request_sha256(kwargs)
            await _emit(
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
                    "stripped_images": False,
                },
            )
            async for delta in self._stream_once(api_key=api_key, kwargs=kwargs):
                yield delta
            await _emit(
                sink,
                "transport.response_completed",
                session_id=session_id,
                run_id=effective_run_id,
                turn_index=turn_index,
                payload={
                    "model": self._provider.model,
                    "api_format": self._provider.api_format,
                    "transport": self._provider.transport,
                    "iteration": iteration,
                    "key_index": key_index,
                    "status": "ok",
                    "stripped_images": False,
                },
            )
            if attempt_sink is not None:
                await attempt_sink.arecord_attempt(
                    _attempt_record(
                        messages=messages,
                        request_body=kwargs,
                        model_attempt_id=model_attempt_id
                        or _default_attempt_id(effective_run_id, iteration),
                        request_sha256=request_sha256,
                        stripped_images=False,
                        response_completed=True,
                        error=None,
                    )
                )
                recorded = True
            return
        except LLMClientError as exc:
            await _emit(
                sink,
                "transport.request_failed",
                session_id=session_id,
                run_id=effective_run_id,
                turn_index=turn_index,
                payload={
                    "error": exc.message,
                    "error_type": exc.error_type,
                    "cause_code": exc.cause_code,
                    "key_index": key_index,
                    "stripped_images": False,
                },
            )
            if attempt_sink is not None and request_sha256:
                await attempt_sink.arecord_attempt(
                    _attempt_record(
                        messages=messages,
                        request_body=kwargs,
                        model_attempt_id=model_attempt_id
                        or _default_attempt_id(effective_run_id, iteration),
                        request_sha256=request_sha256,
                        stripped_images=False,
                        response_completed=False,
                        error=exc,
                    )
                )
                recorded = True
            raise
        finally:
            if attempt_sink is not None and request_sha256 and not recorded:
                await attempt_sink.arecord_attempt(
                    _attempt_record(
                        messages=messages,
                        request_body=kwargs,
                        model_attempt_id=model_attempt_id
                        or _default_attempt_id(effective_run_id, iteration),
                        request_sha256=request_sha256,
                        stripped_images=False,
                        response_completed=False,
                        error=LLMProviderError(
                            error_type="stream_aborted",
                            message="provider stream ended before a complete response",
                            cause_code="stream_aborted",
                        ),
                    )
                )

    async def complete_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
    ) -> LLMJsonResponse:
        started = time.perf_counter()
        message = await self.complete([UserMessage.from_text(prompt)], temperature=temperature)
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

    async def aclose(self) -> None:
        """SDK clients are scoped per call; no persistent handle to close."""

    async def _stream_once(
        self,
        *,
        api_key: str,
        kwargs: dict[str, Any],
    ) -> AsyncIterator[TransportDelta]:
        try:
            if self._provider.api_format == "anthropic":
                client_kwargs: dict[str, Any] = {
                    self._provider.auth_type: api_key,
                    "base_url": self._provider.base_url,
                    "timeout": self._timeout_s,
                    "max_retries": 0,
                }
                if self._anthropic_client_factory is anthropic.AsyncAnthropic:
                    client_kwargs["http_client"] = httpx.AsyncClient(
                        timeout=self._timeout_s,
                        trust_env=False,
                    )
                client = self._anthropic_client_factory(**client_kwargs)
                try:
                    stream_context = client.messages.stream(**kwargs)
                    async with _async_context(stream_context) as stream:
                        async for delta in self._transport.aiter_stream_deltas(_async_iter(stream)):
                            # Text and thinking stay live. Anthropic's SDK owns the
                            # authoritative assembly of tool_use.input at stream end.
                            if delta.text_delta or delta.reasoning_delta:
                                yield delta
                        final_message = await _maybe_await(stream.get_final_message())
                        normalized = self._transport.normalize_response(final_message)
                        for tool_call in normalized.tool_calls:
                            yield TransportDelta(
                                type="transport.delta",
                                tool_call_delta=tool_call,
                            )
                        if (
                            normalized.finish_reason
                            or normalized.usage
                            or normalized.provider_metadata
                        ):
                            yield TransportDelta(
                                type="transport.delta",
                                finish_reason=normalized.finish_reason,
                                usage=normalized.usage,
                                provider_metadata=normalized.provider_metadata,
                            )
                finally:
                    close = getattr(client, "aclose", None) or getattr(client, "close", None)
                    if callable(close):
                        await _maybe_await(close())
                return

            client_kwargs = {
                "api_key": api_key,
                "base_url": self._provider.base_url,
                "timeout": self._timeout_s,
                "max_retries": 0,
            }
            if self._openai_client_factory is openai.AsyncOpenAI:
                client_kwargs["http_client"] = httpx.AsyncClient(
                    timeout=self._timeout_s,
                    trust_env=False,
                )
            client = self._openai_client_factory(**client_kwargs)
            try:
                stream_context = client.chat.completions.create(
                    stream=True,
                    stream_options={"include_usage": True},
                    **kwargs,
                )
                stream_context = await _maybe_await(stream_context)
                async with _async_context(stream_context) as stream:
                    async for delta in self._transport.aiter_stream_deltas(_async_iter(stream)):
                        yield delta
            finally:
                close = getattr(client, "aclose", None) or getattr(client, "close", None)
                if callable(close):
                    await _maybe_await(close())
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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _async_iter(value: Any) -> AsyncIterator[Any]:
    if hasattr(value, "__aiter__"):
        async for item in value:
            yield item
        return
    for item in value:
        yield item


@contextlib.asynccontextmanager
async def _async_context(value: Any) -> AsyncIterator[Any]:
    if hasattr(value, "__aenter__"):
        async with value as entered:
            yield entered
        return
    with value as entered:
        yield entered


LLMProviderResponseError = LLMProviderError
