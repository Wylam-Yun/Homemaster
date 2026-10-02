"""``AsLLMClient``: ``LLMClient``-compatible provider client on ChatModelBase.

Phase 1 dual-run seam (plan/V3.7 Phase 1): same ``stream``/``complete``/
``complete_json``/``token_estimator``/``aclose`` surface as
``providers.llm_client.LLMClient``, but the wire call is made by a vendored
AgentScope ``ChatModelBase`` instead of HM transports.

Invariants preserved (Phase-1 hard gates):
- frozen request fingerprint ``request_sha256`` is computed from the exact
  material sent to the model (formatted messages + tools + params), so
  provider retries re-hash identically;
- ``ProviderAttemptRecord`` three-phase commit stays the single record
  channel (``attempt_sink`` kwarg, ``model_attempt_id``);
- ``transport.request_started`` / ``response_completed`` /
  ``request_failed`` events keep the same payload shape;
- provider-native ``stop_reason`` (via vendored patch #2
  ``ChatResponse.metadata``) maps to HM ``finish_reason`` vocabulary
  (``max_tokens`` -> ``length`` etc.);
- ``provider_key_index`` selects ``api_keys[i]`` per attempt.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
    UserMessage,
)
from homemaster.config.config import ProviderProfileConfig
from homemaster.providers.attempts import ProviderAttemptSink
from homemaster.providers.errors import LLMClientError, LLMProviderError
from homemaster.providers.json_utils import extract_json_payload
from homemaster.providers.llm_client import (
    LLMJsonResponse,
    _attempt_record,
    _default_attempt_id,
    _emit,
    _map_sdk_error,
    _request_sha256,
)
from homemaster.providers.token_estimator import TokenEstimator, make_default_estimator
from homemaster.providers.transports.types import (
    TransportDelta,
    aggregate_deltas,
)
from homemaster.substrate.messages import to_agent_scope

_DEFAULT_TIMEOUT_S = 60.0


def _normalize_stop_reason(raw: Any) -> str | None:
    """Provider-native stop reason -> HM finish_reason vocabulary."""
    if raw is None:
        return None
    text = str(raw)
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
    }.get(text, text)


def _usage_to_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    out: dict[str, int] = {}
    for hm_key, as_key in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
        # HM vocabulary uses cache_read_input_tokens; AS calls the same
        # counter cache_input_tokens.
        ("cache_read_input_tokens", "cache_input_tokens"),
    ):
        value = getattr(usage, as_key, None)
        if not isinstance(value, int):
            continue
        # ChatUsage defaults cache counters to 0, conflating absent with
        # zero; HM only emits keys the provider actually sent.
        if hm_key.startswith("cache_") and not value:
            continue
        out[hm_key] = value
    return out or None


def _tool_envelope(tool: dict[str, Any]) -> dict[str, Any]:
    """HM tool dict -> OpenAI-style function envelope AS models expect."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema")
            or {"type": "object", "properties": {}},
        },
    }


class AsLLMClient:
    """Drop-in replacement for ``LLMClient`` backed by ``ChatModelBase``."""

    def __init__(
        self,
        provider: ProviderProfileConfig,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        event_sink: Any = None,
        run_id: str = "",
        model_factory: Any = None,
    ) -> None:
        self._provider = provider
        self._timeout_s = timeout_s
        self._event_sink = event_sink
        self._run_id = run_id
        # Test seam: ``model_factory(profile, api_key=…, timeout_s=…)``
        # defaults to ``chat_model_from_profile``.
        self._model_factory = model_factory
        self._token_estimator = make_default_estimator(provider)

    @property
    def token_estimator(self) -> TokenEstimator:
        return self._token_estimator

    def chat_model(self, *, provider_key_index: int = 0) -> Any:
        """Build the underlying ``ChatModelBase`` for the AgentScope-agent
        path (Phase 2): same profile resolution + key selection as
        ``stream``, without the LLMClient projection wrapper."""
        model_factory = self._model_factory
        if model_factory is None:
            from homemaster.substrate.models import chat_model_from_profile

            model_factory = chat_model_from_profile
        keyless = self._provider.api_format == "ollama"
        if not self._provider.api_keys and not keyless:
            from homemaster.providers.errors import LLMClientError

            raise LLMClientError(
                error_type="no_keys",
                message="no API keys configured",
                cause_code="no_keys",
            )
        api_key = (
            self._provider.api_keys[provider_key_index]
            if self._provider.api_keys
            else None
        )
        return model_factory(
            self._provider,
            api_key=api_key,
            timeout_s=self._timeout_s,
        )

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
        from agentscope.message import Msg, TextBlock
        from agentscope.model import FinishedReason

        model_factory = self._model_factory
        if model_factory is None:
            from homemaster.substrate.models import chat_model_from_profile

            model_factory = chat_model_from_profile

        sink = event_sink or self._event_sink
        effective_run_id = run_id or self._run_id
        keyless = self._provider.api_format == "ollama"
        if not self._provider.api_keys and not keyless:
            raise LLMClientError(
                error_type="no_keys",
                message="no API keys configured",
                cause_code="no_keys",
            )
        selected_key_index = min(
            max(provider_key_index, 0), max(len(self._provider.api_keys) - 1, 0)
        )
        key_index = selected_key_index + 1
        api_key = (
            self._provider.api_keys[selected_key_index]
            if self._provider.api_keys
            else None
        )

        request_sha256 = ""
        recorded = False
        request_body: dict[str, Any] = {}
        try:
            as_messages = to_agent_scope(messages)
            if system_prompt.strip():
                as_messages.insert(
                    0,
                    Msg(
                        name="system",
                        role="system",
                        content=[TextBlock(text=system_prompt.strip())],
                    ),
                )
            as_tools = [_tool_envelope(t) for t in tools] if tools else None
            call_kwargs: dict[str, Any] = {}
            if temperature is not None:
                call_kwargs["temperature"] = temperature
            effective_max_tokens = (
                max_output_tokens or self._provider.max_output_tokens
            )
            if effective_max_tokens is not None:
                token_key = (
                    "max_tokens"
                    if self._provider.api_format == "anthropic"
                    else "max_completion_tokens"
                )
                call_kwargs[token_key] = effective_max_tokens
            model = model_factory(
                self._provider,
                api_key=api_key,
                timeout_s=self._timeout_s,
            )
            request_body = {
                "model": self._provider.model,
                "messages": [_stable_msg_view(m) for m in as_messages],
                "tools": as_tools or [],
                **call_kwargs,
            }
            request_sha256 = _request_sha256(request_body)
            await _emit(
                sink,
                "transport.request_started",
                session_id=session_id,
                run_id=effective_run_id,
                turn_index=turn_index,
                payload={
                    "model": self._provider.model,
                    "api_format": self._provider.api_format,
                    "transport": "agentscope",
                    "iteration": iteration,
                    "key_index": key_index,
                    "stripped_images": False,
                },
            )

            last: Any = None
            interrupted = False
            streamed_text = ""
            streamed_thinking = ""
            try:
                stream = await model(
                    as_messages, tools=as_tools, **call_kwargs
                )
            except Exception as exc:
                raise _map_sdk_error(exc) from exc
            if not hasattr(stream, "__aiter__"):
                # stream=False path: a single complete ChatResponse.
                stream = _one_shot(stream)
            try:
                async for chunk in stream:
                    if chunk.finished_reason is FinishedReason.INTERRUPTED:
                        interrupted = True
                    if chunk.is_last:
                        last = chunk
                        continue
                    for block in chunk.content:
                        text = getattr(block, "text", None)
                        thinking = getattr(block, "thinking", None)
                        if text:
                            streamed_text += text
                            yield TransportDelta(
                                type="transport.delta", text_delta=text
                            )
                        elif thinking:
                            streamed_thinking += thinking
                            yield TransportDelta(
                                type="transport.delta",
                                reasoning_delta=thinking,
                            )
            except Exception as exc:
                raise _map_sdk_error(exc) from exc
            if interrupted:
                raise LLMProviderError(
                    error_type="interrupted",
                    message="provider stream interrupted",
                    cause_code="interrupted",
                )
            if last is not None:
                # The final AS chunk carries the *complete* response; emit
                # only the text/thinking suffix not already streamed, so
                # non-incremental (stream=False) replies are not dropped
                # and cumulative streams are not duplicated.
                tail_text = "".join(
                    getattr(block, "text", "") or ""
                    for block in last.content
                )
                if tail_text:
                    if tail_text.startswith(streamed_text):
                        tail_text = tail_text[len(streamed_text):]
                    if tail_text:
                        yield TransportDelta(
                            type="transport.delta", text_delta=tail_text
                        )
                tail_thinking = "".join(
                    getattr(block, "thinking", "") or ""
                    for block in last.content
                )
                if tail_thinking:
                    if tail_thinking.startswith(streamed_thinking):
                        tail_thinking = tail_thinking[len(streamed_thinking):]
                    if tail_thinking:
                        yield TransportDelta(
                            type="transport.delta",
                            reasoning_delta=tail_thinking,
                        )
                for block in last.content:
                    if getattr(block, "type", None) == "tool_call":
                        yield TransportDelta(
                            type="transport.delta",
                            tool_call_delta=ToolCall(
                                id=block.id,
                                name=block.name,
                                arguments=_parse_tool_input(block.input),
                            ),
                        )
                stop_reason = (last.metadata or {}).get("stop_reason")
                finish_reason = _normalize_stop_reason(stop_reason)
                usage = _usage_to_dict(last.usage)
                if finish_reason or usage or stop_reason:
                    yield TransportDelta(
                        type="transport.delta",
                        finish_reason=finish_reason,
                        usage=usage,
                        provider_metadata=(
                            {"raw_stop_reason": stop_reason}
                            if stop_reason
                            else {}
                        ),
                    )
            await _emit(
                sink,
                "transport.response_completed",
                session_id=session_id,
                run_id=effective_run_id,
                turn_index=turn_index,
                payload={
                    "model": self._provider.model,
                    "api_format": self._provider.api_format,
                    "transport": "agentscope",
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
                        request_body=request_body,
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
                        request_body=request_body,
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
                        request_body=request_body,
                        model_attempt_id=model_attempt_id
                        or _default_attempt_id(effective_run_id, iteration),
                        request_sha256=request_sha256,
                        stripped_images=False,
                        response_completed=False,
                        error=LLMProviderError(
                            error_type="stream_aborted",
                            message=(
                                "provider stream ended before a complete "
                                "response"
                            ),
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
        message = await self.complete(
            [UserMessage.from_text(prompt)], temperature=temperature
        )
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


async def _one_shot(response: Any) -> AsyncIterator[Any]:
    yield response


def _stable_msg_view(msg: Any) -> dict[str, Any]:
    """Deterministic serialization for the request fingerprint.

    ``to_agent_scope`` regenerates ``id``/``created_at`` per call, so the
    raw dump would hash differently on every retry — violating the
    frozen-request invariant. Strip volatile fields; keep semantics.
    """
    dump = msg.model_dump(mode="json")
    for key in ("id", "created_at", "finished_at"):
        dump.pop(key, None)
    for block in dump.get("content") or []:
        if isinstance(block, dict):
            for key in ("id", "created_at", "finished_at"):
                block.pop(key, None)
    return dump


def _parse_tool_input(raw: Any) -> dict[str, Any]:
    import json

    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return value if isinstance(value, dict) else {"_raw": value}
    return {}


__all__ = ["AsLLMClient"]
