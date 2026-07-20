"""SDK-backed LLM client for HomeMaster providers."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
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
from homemaster.providers.attempts import (
    OutboundImageBinding,
    ProviderAttemptRecord,
    ProviderAttemptSink,
)
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
        attempt_sink: ProviderAttemptSink | None = None,
        model_attempt_id: str | None = None,
        provider_key_index: int = 0,
    ) -> Iterator[TransportDelta]:
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
                    "stripped_images": False,
                },
            )
            yield from self._stream_once(api_key=api_key, kwargs=kwargs)
            _emit(
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
                attempt_sink.record_attempt(
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
            _emit(
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
                attempt_sink.record_attempt(
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
                attempt_sink.record_attempt(
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
                    "max_retries": 0,
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
                "max_retries": 0,
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


def _request_sha256(kwargs: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            kwargs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _default_attempt_id(run_id: str, iteration: int | None) -> str:
    return f"{run_id or 'provider'}:attempt-{(iteration or 0) + 1:04d}"


def _attempt_record(
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
            frame_binding_id = block.metadata.get("frame_binding_id")
            observation_id = _optional_str(block.metadata.get("observation_id"))
            observation_content_sha256 = _optional_str(
                block.metadata.get("observation_content_sha256")
            )
            if observation_id is not None and observation_content_sha256 is None:
                observation_content_sha256 = _optional_str(block.metadata.get("content_sha256"))
            if (
                observation_content_sha256 is not None
                and observation_content_sha256 != content_sha256
            ):
                raise ValueError("outbound observation bytes do not match observation content hash")
            candidates.append(
                (
                    content_sha256,
                    OutboundImageBinding(
                        message_index=message_index,
                        block_index=block_index,
                        frame_binding_id=(
                            frame_binding_id if isinstance(frame_binding_id, str) else None
                        ),
                        content_sha256=content_sha256,
                        observation_id=observation_id,
                        observation_content_sha256=observation_content_sha256,
                        observation_pixel_sha256=_optional_str(
                            block.metadata.get("observation_pixel_sha256")
                            or block.metadata.get("pixel_sha256")
                        ),
                        observation_backend_id=_optional_str(
                            block.metadata.get("observation_backend_id")
                            or block.metadata.get("backend_id")
                        ),
                        observation_run_id=_optional_str(
                            block.metadata.get("observation_run_id")
                            or block.metadata.get("run_id")
                        ),
                        observation_generation=_optional_int(
                            block.metadata.get("observation_generation")
                            if "observation_generation" in block.metadata
                            else block.metadata.get("generation")
                        ),
                        observation_state_sequence=_optional_int(
                            block.metadata.get("observation_state_sequence")
                            if "observation_state_sequence" in block.metadata
                            else block.metadata.get("state_sequence")
                        ),
                        observation_capture_event_sequence=_optional_int(
                            block.metadata.get("observation_capture_event_sequence")
                            if "observation_capture_event_sequence" in block.metadata
                            else block.metadata.get("capture_event_sequence")
                        ),
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
        stripped_images=stripped_images,
        response_completed=response_completed,
        error_type=error.error_type if error is not None else None,
        cause_code=error.cause_code if error is not None else None,
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _serialized_image_contents(value: Any) -> list[bytes]:
    contents: list[bytes] = []
    if isinstance(value, dict):
        source = value.get("source")
        if value.get("type") == "image" and isinstance(source, dict):
            data = source.get("data")
            if isinstance(data, str):
                try:
                    contents.append(base64.b64decode(data, validate=True))
                except ValueError:
                    contents.append(data.encode("ascii", errors="replace"))
                return contents
        for item in value.values():
            contents.extend(_serialized_image_contents(item))
    elif isinstance(value, list | tuple):
        for item in value:
            contents.extend(_serialized_image_contents(item))
    return contents


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
