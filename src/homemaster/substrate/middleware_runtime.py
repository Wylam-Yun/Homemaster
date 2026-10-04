"""Phase-2 HM middlewares for the AgentScope reasoning loop.

Three middlewares port the HomeMaster runtime contract onto AS hooks:

- ``ContextAssemblyMiddleware`` — ``on_model_call`` rewrites ``messages``
  through the HM ``ContextAssembler`` (memory, compaction, tail projection),
  and performs reactive compaction retries on context-length errors.
- ``ProviderObservabilityMiddleware`` — ``on_model_call`` emits the HM
  transport.* request/response events and writes ``ProviderAttemptRecord``.
- ``ObservationBarrierMiddleware`` — ports the model-observation barrier:
  tool trimming + system-prompt augmentation while a barrier is pending,
  batch/protocol fences via ``on_check_permission``, and automatic
  observation via the ``on_acting`` post-hook.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionBehavior, PermissionDecision
from homemaster.agent.messages import ToolCall
from homemaster.agent.model_observation import (
    MAX_OBSERVE_FAILURES,
    MAX_PROTOCOL_FAILURES,
    action_requires_model_observation,
    append_model_observation_prompt,
    automatic_observation_call,
    observation_tool_name,
    validate_observation_result,
)

_PROVIDER_MAX_ATTEMPTS = 8
_PROVIDER_RETRY_BASE_DELAY_S = 3.0

_CONTEXT_LENGTH_KEYWORDS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "context window",
    "prompt is too long",
    "request_too_large",
    "string too long",
    "exceed",
)


def _is_context_length_error(error_msg: str) -> bool:
    lowered = error_msg.lower()
    return any(keyword in lowered for keyword in _CONTEXT_LENGTH_KEYWORDS)


def _assembler_prepare(assembler: Any) -> Callable[..., Any]:
    """Prefer the async ``aprepare`` (its summary path awaits coroutine
    ``complete``); fall back to sync ``prepare`` for test doubles."""
    return getattr(assembler, "aprepare", None) or assembler.prepare


async def _maybe_async(fn: Callable[..., Any], **kwargs: Any) -> Any:
    value = fn(**kwargs)
    if inspect.isawaitable(value):
        return await value
    return value


def _stable_msg_view(msg: Any) -> dict[str, Any]:
    """Strip volatile fields so fingerprints are stable across retries."""
    data = msg.model_dump(mode="json") if hasattr(msg, "model_dump") else dict(msg)
    for key in ("id", "created_at", "finished_at"):
        data.pop(key, None)
    return data


def _request_sha256(messages: list[Any], tools: Any, tool_choice: Any) -> str:
    payload = {
        "messages": [_stable_msg_view(m) for m in messages or []],
        "tools": tools or [],
        "tool_choice": _jsonable(tool_choice),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


class ContextAssemblyMiddleware(MiddlewareBase):
    """Runs the HM ContextAssembler inside ``on_model_call`` so the model
    sees the assembled (compacted/memory-injected) context rather than the
    raw engine transcript. Reactive compaction retries a context-length
    failure once the assembler has compacted."""

    def __init__(
        self,
        *,
        handle: Any,
        assembler: Any = None,
        force_compact: str | bool | None = None,
        on_compaction: Callable[[Any], Any] | None = None,
        max_reactive_retries: int = 2,
    ) -> None:
        self._handle = handle
        self._assembler = assembler
        self._pending_force_compact = force_compact
        self._on_compaction = on_compaction
        self._max_reactive_retries = max_reactive_retries

    async def on_compress_context(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> None:
        """Fence: HM's ContextAssembler is the sole context authority.
        Short-circuiting here (never calling ``next_handler``) prevents
        AgentScope's native ``_compress_context_impl`` — including its
        image limiter and the extra ``generate_structured_output`` model
        call — from silently mutating canonical history once the token
        count crosses ``trigger_ratio``. Covers both the per-iteration
        auto-compression and the CompressContext tool paths."""
        return None

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> Any:
        # Live engine-state/context references for the shell's projections
        # (the assembled Msg lands in agent.state.context). Keep both: state
        # survives `state.context` rebinding, the list is the fast path.
        self._handle.engine_state = agent.state
        self._handle.engine_context = agent.state.context

        if self._assembler is None:
            return await next_handler()

        messages, tools = await self._assemble(input_kwargs)
        retries = 0
        while True:
            try:
                resp = await next_handler(messages=messages, tools=tools)
            except Exception as exc:
                if not _is_context_length_error(str(exc)) or retries >= self._max_reactive_retries:
                    raise
                retries += 1
                messages, tools = await self._recompact(input_kwargs, retries)
                continue
            break
        if not hasattr(resp, "__aiter__"):
            return resp
        return self._stream_with_reactive_retry(
            resp, messages, tools, input_kwargs, next_handler, retries
        )

    async def _stream_with_reactive_retry(
        self,
        stream: Any,
        messages: list[Any],
        tools: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
        retries: int,
    ) -> AsyncGenerator:
        """Consume the model stream, retrying a context-length failure that
        surfaces on the first ``__anext__`` (streaming calls raise lazily).
        Safe only while zero chunks have been yielded — a mid-stream failure
        cannot be retried without re-delivering deltas."""
        while True:
            first_chunk = True
            try:
                async for chunk in stream:
                    first_chunk = False
                    yield chunk
            except Exception as exc:
                if (
                    first_chunk
                    and _is_context_length_error(str(exc))
                    and retries < self._max_reactive_retries
                ):
                    retries += 1
                    messages, tools = await self._recompact(input_kwargs, retries)
                    stream = await next_handler(messages=messages, tools=tools)
                    continue
                raise
            return

    async def _recompact(self, input_kwargs: dict, attempt: int) -> tuple[list[Any], Any]:
        await self._handle.emit(
            "runtime.reactive_compact_started",
            payload={"attempt": attempt},
        )
        prepared = await self._compact()
        if getattr(prepared, "metrics", None) is None:
            raise RuntimeError("reactive compaction produced no metrics")
        return self._render(input_kwargs, prepared)

    async def _assemble(self, input_kwargs: dict) -> tuple[list[Any], Any]:
        handle = self._handle
        # The session mirror is only as fresh as the last sync — re-sync so
        # post-tool/post-observation mutations (auto-observe attachments,
        # barrier clears) reach the assembler's canonical history. No-op
        # when the runtime did not wire the hook.
        if callable(handle.sync_session):
            handle.sync_session()
        force = self._pending_force_compact
        self._pending_force_compact = None
        prepared = await _maybe_async(
            _assembler_prepare(self._assembler),
            session=handle.session,
            agent_state=handle.agent_state,
            task_state_store=handle.task_state_store,
            tools=input_kwargs.get("tools"),
            force_compact=force,
        )
        handle.agent_state.estimated_context_tokens = getattr(
            getattr(prepared, "metrics", None), "estimated_input_tokens", 0
        )
        # Threshold and manual compactions surface here (the reactive path
        # notifies via ``_compact``). Legacy fires the event + callback for
        # every compaction kind — same-parity requirement, otherwise
        # ``require_recall`` is silently never re-armed on the AS path.
        metrics = getattr(prepared, "metrics", None)
        if metrics is not None and getattr(metrics, "compaction_triggered", False):
            await self._notify_compaction(metrics)
        return self._render(input_kwargs, prepared)

    async def _notify_compaction(self, metrics: Any) -> None:
        """Emit the legacy-shaped ``context.compaction`` event then invoke
        the ``on_compaction`` callback (order matches generic_runtime)."""
        kind = getattr(metrics, "compaction_kind", "") or ""
        trigger = (
            "manual"
            if kind.startswith("manual")
            else "reactive"
            if kind in {"reactive", "emergency"}
            else "auto"
        )
        await self._handle.emit(
            "context.compaction",
            payload={
                "trigger": trigger,
                "after_tokens": getattr(metrics, "estimated_tokens", 0) or 0,
                "kind": kind,
            },
        )
        if self._on_compaction is not None:
            result = self._on_compaction(metrics)
            if inspect.isawaitable(result):
                await result

    def _render(self, input_kwargs: dict, prepared: Any) -> tuple[list[Any], Any]:
        """Rebuild the model input: HM system prompt (assembler is the
        authority — it carries workspace/frozen-memory context the bare AS
        prompt lacks) + observation-barrier suffix + assembled messages.
        ``_prepare_model_input``'s messages[0] SystemMsg is replaced because
        AS's own ``_system_prompt`` is a strict subset."""
        from agentscope.message import SystemMsg
        from homemaster.substrate.messages import to_agent_scope

        prompt = getattr(prepared, "system_prompt", "") or ""
        barrier = self._handle.agent_state.pending_model_observation
        if barrier is not None:
            prompt = append_model_observation_prompt(prompt, tool_name=barrier.observe_tool_name)
        # Frozen copy of the canonical request messages — consumed by the
        # provider_attempt_context_binder (mindmemos_feedback) exactly like
        # legacy's ``frozen_messages`` snapshot before dispatch.
        self._handle.last_frozen_messages = [m.model_copy(deep=True) for m in prepared.messages]
        messages = [SystemMsg(name="system", content=prompt)] + to_agent_scope(
            list(prepared.messages)
        )
        return messages, input_kwargs.get("tools")

    async def _compact(self) -> Any:
        handle = self._handle
        if callable(handle.sync_session):
            handle.sync_session()
        prepared = await _maybe_async(
            _assembler_prepare(self._assembler),
            session=handle.session,
            agent_state=handle.agent_state,
            task_state_store=handle.task_state_store,
            tools=handle.all_tool_schemas or None,
            # Legacy passes "aggressive" for reactive compaction
            # (generic_runtime.py: `pending_compaction = "aggressive"`) —
            # a bare True would string-coerce into neither the aggressive
            # nor manual branch and silently no-op on small histories.
            force_compact="aggressive",
        )
        metrics = getattr(prepared, "metrics", None)
        if metrics is not None and getattr(metrics, "compaction_triggered", False):
            await self._notify_compaction(metrics)
        return prepared


class ProviderObservabilityMiddleware(MiddlewareBase):
    """Emits HM transport.* events and ProviderAttemptRecord entries around
    each AS model call — the same three-phase commit vocabulary the legacy
    ``LLMClient`` produces. For streaming calls the completion event fires
    only after the returned generator is fully consumed."""

    def __init__(self, *, handle: Any, attempt_sink: Any = None) -> None:
        self._handle = handle
        self._attempt_sink = attempt_sink
        self._attempt_index = 0
        self._bound_call_ids: set[str] = set()

    async def on_check_permission(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> PermissionDecision:
        """Bind provider-attempt feedback context once per tool-call round.

        This hook is the deterministic pre-dispatch point: by the time it
        runs, the round's assistant Msg (with its tool_call blocks) is in
        ``agent.state.context`` and the tool body has not executed. Legacy
        binds right after stream aggregation, before dispatch — AS's
        Msg-to-context append lands too late for the shell's watermark
        scan, so the permission boundary carries the equivalent.
        ``mindmemos_feedback`` reads the bound context via
        ``run_context.deps`` at call time."""
        round_calls = _round_tool_calls(agent, self._handle)
        if any(c.id not in self._bound_call_ids for c in round_calls):
            self._bound_call_ids.update(getattr(c, "id", "") or "" for c in round_calls)
            handle = self._handle
            scope = getattr(handle, "scope", None)
            services = getattr(scope, "services", None)
            run_context = services.get("run_context") if hasattr(services, "get") else None
            deps = getattr(run_context, "deps", None)
            binder = deps.get("provider_attempt_context_binder") if hasattr(deps, "get") else None
            if callable(binder):
                binder(
                    tool_calls=[
                        ToolCall(
                            id=getattr(c, "id", "") or "",
                            name=getattr(c, "name", "") or "",
                            arguments=_call_arguments(c),
                        )
                        for c in round_calls
                    ],
                    frozen_messages=list(handle.last_frozen_messages),
                    deps=deps,
                )
        return await next_handler()

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> Any:
        self._attempt_index += 1
        attempt_index = self._attempt_index
        request_sha = _request_sha256(
            input_kwargs.get("messages"),
            input_kwargs.get("tools"),
            input_kwargs.get("tool_choice"),
        )
        emit = self._handle.emit
        started = time.perf_counter()
        await emit(
            "transport.request_started",
            payload={
                "attempt": attempt_index,
                "request_sha256": request_sha,
                "model": getattr(self._handle, "model_name", ""),
                "api_format": getattr(self._handle, "model_api_format", ""),
                "transport": "agentscope",
                "iteration": self._handle.normal_iterations,
                "key_index": 0,
                "stripped_images": False,
            },
        )
        try:
            response = await next_handler()
        except Exception as exc:
            await emit(
                "transport.request_failed",
                payload={
                    "attempt": attempt_index,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "request_sha256": request_sha,
                },
            )
            await self._record_attempt(
                attempt_index=attempt_index,
                request_sha256=request_sha,
                messages=input_kwargs.get("messages"),
                response_completed=False,
                error=exc,
            )
            raise

        if hasattr(response, "__aiter__"):
            return self._wrap_stream(
                response,
                attempt_index=attempt_index,
                request_sha=request_sha,
                started=started,
                messages=input_kwargs.get("messages"),
            )
        await self._complete(
            response,
            attempt_index=attempt_index,
            request_sha=request_sha,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            messages=input_kwargs.get("messages"),
        )
        return response

    async def _wrap_stream(
        self,
        stream: Any,
        *,
        attempt_index: int,
        request_sha: str,
        started: float,
        messages: Any,
    ) -> AsyncGenerator:
        last_chunk: Any = None
        emitted: list[Any] = []
        try:
            async for chunk in stream:
                emitted.append(chunk)
                last_chunk = chunk
                yield chunk
        except Exception as exc:
            await self._handle.emit(
                "transport.request_failed",
                payload={
                    "attempt": attempt_index,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "request_sha256": request_sha,
                },
            )
            await self._record_attempt(
                attempt_index=attempt_index,
                request_sha256=request_sha,
                messages=messages,
                response_completed=False,
                error=exc,
            )
            raise
        usage = _usage_from_chunk(last_chunk)
        await self._handle.emit(
            "transport.response_completed",
            payload={
                "attempt": attempt_index,
                "latency_ms": (time.perf_counter() - started) * 1000.0,
                "request_sha256": request_sha,
                "output_sha256": _output_sha256(emitted),
                "usage": usage,
            },
        )
        await self._record_attempt(
            attempt_index=attempt_index,
            request_sha256=request_sha,
            messages=messages,
            response_completed=True,
            error=None,
        )

    async def _complete(
        self,
        response: Any,
        *,
        attempt_index: int,
        request_sha: str,
        latency_ms: float,
        messages: Any,
    ) -> None:
        usage = _usage_from_response(response)
        await self._handle.emit(
            "transport.response_completed",
            payload={
                "attempt": attempt_index,
                "latency_ms": latency_ms,
                "request_sha256": request_sha,
                "output_sha256": _output_sha256(response),
                "usage": usage,
            },
        )
        await self._record_attempt(
            attempt_index=attempt_index,
            request_sha256=request_sha,
            messages=messages,
            response_completed=True,
            error=None,
        )

    async def _record_attempt(
        self,
        *,
        attempt_index: int,
        request_sha256: str,
        messages: Any,
        response_completed: bool,
        error: Any,
    ) -> None:
        """Write a real ``ProviderAttemptRecord`` — the frozen dataclass
        rejects unknown kwargs, so anything looser would crash the JSONL
        sink's ``asdict`` and fail every model call."""
        sink = self._attempt_sink
        if sink is None:
            return
        from homemaster.providers.attempts import ProviderAttemptRecord

        error_type = getattr(error, "error_type", None) or (
            type(error).__name__ if error is not None else None
        )
        cause_code = getattr(error, "cause_code", None)
        record = ProviderAttemptRecord(
            model_attempt_id=(f"{self._handle.run_id}:attempt-{attempt_index:04d}"),
            request_sha256=request_sha256,
            outbound_images=_outbound_image_bindings(messages),
            stripped_images=False,
            response_completed=response_completed,
            error_type=error_type,
            cause_code=cause_code,
        )
        arecord = getattr(sink, "arecord_attempt", None)
        if callable(arecord):
            await arecord(record)
        else:
            sink.record_attempt(record)


class ProviderRetryMiddleware(MiddlewareBase):
    """Ports the legacy engine's pre-commit provider retry onto
    ``on_model_call``: the frozen call is retried only while every attempt
    failed before the response committed — ``response_completed=False`` on
    the recorded attempt, a matching error signature, and at most
    reasoning-only stream chunks observed. Ordering: this middleware must
    sit *outside* ``ProviderObservabilityMiddleware`` so every attempt gets
    its own ``transport.request_*`` event pair + ``ProviderAttemptRecord``,
    and *inside* ``ContextAssemblyMiddleware`` so a context-length failure
    propagates outward to the reactive-compaction retry instead of being
    re-issued against the same oversized request."""

    def __init__(self, *, handle: Any, attempt_sink: Any = None) -> None:
        self._handle = handle
        self._attempt_sink = attempt_sink
        self._first_attempt_id: str | None = None

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> Any:
        self._first_attempt_id = None
        attempt_index = 0
        while True:
            try:
                response = await next_handler()
            except Exception as exc:
                if attempt_index < _PROVIDER_MAX_ATTEMPTS - 1 and self._retry_allowed(exc):
                    delay_s = self._delay(attempt_index)
                    attempt_index += 1
                    await self._emit_retrying(attempt_index, delay_s)
                    await self._sleep(delay_s)
                    continue
                raise
            if hasattr(response, "__aiter__"):
                return self._stream_with_retry(
                    response,
                    next_handler,
                    attempt_index,
                )
            return response

    async def _stream_with_retry(
        self,
        stream: Any,
        next_handler: Callable[..., Any],
        attempt_index: int,
    ) -> AsyncGenerator:
        """Consume the model stream; a failure after only reasoning chunks
        is still pre-commit (nothing user-visible landed) and may retry with
        the same frozen call — mirroring the legacy ``_reasoning_only_delta``
        guard."""
        while True:
            reasoning_only = True
            try:
                async for chunk in stream:
                    if not _reasoning_only_chunk(chunk):
                        reasoning_only = False
                    yield chunk
            except Exception as exc:
                if (
                    reasoning_only
                    and attempt_index < _PROVIDER_MAX_ATTEMPTS - 1
                    and self._retry_allowed(exc)
                ):
                    delay_s = self._delay(attempt_index)
                    attempt_index += 1
                    await self._emit_retrying(attempt_index, delay_s)
                    await self._sleep(delay_s)
                    stream = await next_handler()
                    continue
                raise
            return

    def _retry_allowed(self, error: Exception) -> bool:
        # Marked ``_hm_propagate`` failures (session fences, teardown errors)
        # and run-deadline expiries must never be retried; context-length
        # errors belong to the outer reactive-compaction retry.
        if getattr(error, "_hm_propagate", False):
            return False
        if isinstance(error, (asyncio.CancelledError, TimeoutError)):
            return False
        if _is_context_length_error(str(error)):
            return False
        # Legacy gated on ``isinstance(error, LLMClientError)`` — transports
        # mapped raw SDK failures before raising. Under AS the model raises
        # raw SDK exceptions, so normalize first: typed provider errors keep
        # their legacy retry scope; raw exceptions retry only when they map
        # to a transient class (network/rate-limit), never programming bugs.
        from homemaster.providers._shared import map_sdk_error
        from homemaster.providers.errors import (
            LLMClientError,
            LLMNetworkError,
            LLMRateLimitError,
        )

        if not (
            isinstance(error, LLMClientError)
            or isinstance(map_sdk_error(error), (LLMNetworkError, LLMRateLimitError))
        ):
            return False
        attempt = _last_provider_attempt(self._attempt_sink)
        if attempt is None or attempt.response_completed:
            return False
        # The recorded attempt must describe this failure — a stale or
        # mismatched record means the failure happened outside the provider
        # call boundary (middleware/tool chain), where retry is unsafe.
        error_type = getattr(error, "error_type", None) or type(error).__name__
        cause_code = getattr(error, "cause_code", None)
        if (attempt.error_type, attempt.cause_code) != (error_type, cause_code):
            return False
        if self._first_attempt_id is None:
            self._first_attempt_id = attempt.model_attempt_id
        return True

    async def _emit_retrying(self, attempt_index: int, delay_s: float) -> None:
        await self._handle.emit(
            "transport.request_retrying",
            payload={
                "attempt": attempt_index + 1,
                "max_attempts": _PROVIDER_MAX_ATTEMPTS,
                "delay_seconds": delay_s,
                "cause_code": (
                    getattr(
                        _last_provider_attempt(self._attempt_sink),
                        "cause_code",
                        None,
                    )
                ),
                "first_model_attempt_id": self._first_attempt_id,
            },
        )

    @staticmethod
    def _delay(attempt_index: int) -> float:
        if attempt_index <= 0:
            return 0.0
        return _PROVIDER_RETRY_BASE_DELAY_S * (2 ** (attempt_index - 1))

    async def _sleep(self, delay_s: float) -> None:
        deadline = getattr(getattr(self._handle, "scope", None), "deadline", None)
        remaining = deadline.remaining_s() if deadline is not None else None
        if remaining is None:
            await asyncio.sleep(delay_s)
            return
        if remaining <= 0:
            raise TimeoutError("provider retry deadline expired")
        await asyncio.sleep(min(delay_s, remaining))


def _reasoning_only_chunk(chunk: Any) -> bool:
    """``ChatResponse`` chunk carrying nothing but thinking blocks — the AS
    analogue of the legacy ``_reasoning_only_delta`` guard (a finish chunk
    or any text/tool block commits the response)."""
    content = getattr(chunk, "content", None) or []
    if getattr(chunk, "is_last", False):
        return False
    if getattr(chunk, "metadata", None) and chunk.metadata.get("stop_reason"):
        return False
    return all(getattr(block, "type", None) == "thinking" for block in content)


def _last_provider_attempt(sink: Any) -> Any:
    if sink is None:
        return None
    from homemaster.providers.attempts import ProviderAttemptRecord

    record = getattr(sink, "last_record", None)
    return record if isinstance(record, ProviderAttemptRecord) else None


def _usage_from_chunk(chunk: Any) -> dict[str, int]:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return {}
    return {
        key: int(getattr(usage, key) or 0)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_input_tokens",
            "cache_creation_input_tokens",
        )
    }


class ObservationBarrierMiddleware(MiddlewareBase):
    """Ports the HM model-observation barrier onto AS hooks:

    - ``on_model_call`` / ``on_system_prompt``: while a barrier is pending,
      trim tools to the observation tool and append the observation prompt.
    - ``on_check_permission``: batch/protocol fences (single-observe under
      barrier, single action per batch, no mixed observe+action batches,
      budget fence) — enforced per call by reading the round's tool calls
      from the engine context tail.
    - ``on_acting`` post-hook: automatic observation after an action that
      requires it; manual-observe validation + barrier lifecycle.
    """

    _OBSERVE_NAMES = frozenset({"observe", "browser_screenshot"})

    def __init__(self, *, handle: Any) -> None:
        self._handle = handle
        self._protocol_rejected: dict[str, str] = {}

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> Any:
        handle = self._handle
        barrier = handle.agent_state.pending_model_observation
        tools = input_kwargs.get("tools")
        if barrier is not None and isinstance(tools, list):
            observe_name = barrier.observe_tool_name or self._observe_name(tools)
            tools = [schema for schema in tools if _schema_name(schema) == observe_name]
        consumed = handle.agent_state.unconsumed_observation_tool_call_id
        if consumed is not None:
            handle.agent_state.unconsumed_observation_tool_call_id = None
            await handle.emit(
                "model_observation.image_consumed",
                tool_call_id=consumed,
                name=self._safe_observe_name(),
                payload={"tool_call_id": consumed},
            )
        return await next_handler(tools=tools)

    def _safe_observe_name(self) -> str | None:
        try:
            return observation_tool_name(self._handle.all_tool_schemas or [])
        except RuntimeError:
            return None

    async def on_system_prompt(self, agent: Any, current_prompt: str) -> str:
        barrier = self._handle.agent_state.pending_model_observation
        if barrier is None:
            return current_prompt
        return append_model_observation_prompt(current_prompt, tool_name=barrier.observe_tool_name)

    async def on_check_permission(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> PermissionDecision:
        handle = self._handle
        tool_call = input_kwargs.get("tool_call")
        call_name = getattr(tool_call, "name", "") or ""
        call_id = getattr(tool_call, "id", "") or ""
        round_calls = _round_tool_calls(agent, handle)

        if call_id.startswith("auto-observe-"):
            # Runtime-owned automatic observation calls bypass the batch
            # fences — they are dispatched by the middleware itself, not
            # chosen by the model inside the current round.
            return await next_handler()

        barrier = handle.agent_state.pending_model_observation
        if barrier is not None:
            violation = None
            if len(round_calls) != 1 or call_name != barrier.observe_tool_name:
                violation = (
                    "model_observation_protocol_rejected",
                    "A pending environment action must be followed by one observe call.",
                )
            if violation is not None:
                barrier.protocol_failures += 1
                await handle.emit(
                    "model_observation.protocol_rejected",
                    tool_call_id=barrier.source_tool_call_id,
                    name=barrier.source_tool_name,
                    payload={
                        "protocol_failures": barrier.protocol_failures,
                        "source_tool_call_id": barrier.source_tool_call_id,
                    },
                )
                if barrier.protocol_failures >= MAX_PROTOCOL_FAILURES:
                    handle.observation_fatal = "model_observation_protocol_failed"
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=violation[1],
                )

        observation_actions = [
            name
            for name in (_call_name(c) for c in round_calls)
            if action_requires_model_observation(handle.tool_registry, name)
        ]
        explicit_observation = call_name in self._OBSERVE_NAMES or any(
            _call_name(c) in self._OBSERVE_NAMES for c in round_calls
        )
        mixed = explicit_observation and any(
            _call_name(c) not in self._OBSERVE_NAMES for c in round_calls
        )
        if (observation_actions and len(round_calls) != 1) or mixed:
            await handle.emit(
                "model_observation.batch_rejected",
                tool_call_id=getattr(tool_call, "id", None),
                name=call_name,
                payload={"error_code": "model_observation_batch_rejected"},
            )
            # Machine parity: the canonical result data carried the
            # rejection code — merge it into the denied block's hm pocket
            # (keeping status "denied" so it stays an error state).
            handle.denial_payloads[call_id] = {
                "backend_attempted": False,
                "error_code": "model_observation_batch_rejected",
                "rejected_tool": call_name,
            }
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=("A state-changing environment action must be the only call in its batch."),
            )
        return await next_handler()

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        tool_call = input_kwargs.get("tool_call")
        call_name = getattr(tool_call, "name", "") or ""
        call_id = getattr(tool_call, "id", "") or ""
        handle = self._handle
        final: Any = None
        async for item in next_handler():
            final = item
            yield item

        if (
            final is None
            or getattr(final, "state", None) is None
            or call_id.startswith("auto-observe-")
        ):
            return
        barrier = handle.agent_state.pending_model_observation

        if (
            barrier is None
            and action_requires_model_observation(handle.tool_registry, call_name)
            and _is_success_state(final.state)
            and _backend_attempted(final)
        ):
            await self._automatic_observe(agent, source_call=tool_call, response=final)
        elif barrier is not None and call_name == barrier.observe_tool_name:
            await self._validate_manual_observe(agent, call_id=call_id, response=final)
        elif (
            barrier is None and call_name in self._OBSERVE_NAMES and _is_success_state(final.state)
        ):
            await self._record_manual_observe(call_id=call_id, response=final)

    async def _automatic_observe(self, agent: Any, *, source_call: Any, response: Any) -> None:
        """Runtime-owned observation after an observed action — mirrors the
        ``MAX_OBSERVE_FAILURES`` retry loop of the legacy runtime. Events are
        attributed to the *source* action call, and the consumed-image marker
        records the source id, matching ``generic_runtime``."""
        handle = self._handle
        observe_name = observation_tool_name(handle.all_tool_schemas or [])
        source = ToolCall(
            id=getattr(source_call, "id", "") or "",
            name=getattr(source_call, "name", "") or "",
            arguments=_call_arguments(source_call),
        )
        await handle.emit(
            "model_observation.automatic_started",
            tool_call_id=source.id,
            name=source.name,
            payload={"source_tool_call_id": source.id},
        )
        failure_reason = "automatic observation did not run"
        for attempt in range(1, MAX_OBSERVE_FAILURES + 1):
            # Detached-worker fence: if the run terminated (cancel/aclose)
            # while this post-hook was in-flight, the owning task carries a
            # pending cancellation — the absorbed CancelledError inside
            # call_tool does not clear `cancelling()`. `handle.terminated`
            # additionally covers the path where the driver finished without
            # cancelling the gather (e.g. stream closed by the caller). A
            # terminated run must not keep issuing real observe calls
            # post-snapshot.
            task = asyncio.current_task()
            if handle.terminated or (task is not None and task.cancelling() > 0):
                failure_reason = "run terminated during automatic observation"
                break
            observe_call = automatic_observation_call(source, attempt, tool_name=observe_name)
            observe_response: Any = None
            try:
                observe_response = await self._run_observe_tool(agent, observe_call)
                evidence = self._validate_chunk_as_observation(observe_response)
            except Exception as exc:
                failure_reason = str(exc)
            else:
                # An observe that finished after terminal state must not
                # mutate persisted blocks or re-arm the unconsumed marker —
                # the snapshot is already taken; a late image would be
                # invisible on resume anyway.
                if handle.terminated:
                    return
                self._attach_observation(
                    agent,
                    response=response,
                    observe_response=observe_response,
                    observe_call_id=observe_call.id,
                    evidence=evidence,
                    source_call_id=source.id,
                )
                handle.agent_state.unconsumed_observation_tool_call_id = source.id
                await handle.emit(
                    "model_observation.automatic_completed",
                    tool_call_id=source.id,
                    name=source.name,
                    payload={
                        "attempt": attempt,
                        "content_sha256": evidence.content_sha256,
                        "pixel_sha256": evidence.pixel_sha256,
                        "source_tool_call_id": source.id,
                    },
                )
                return
            await handle.emit(
                "model_observation.automatic_attempt_failed",
                tool_call_id=source.id,
                name=source.name,
                payload={
                    "attempt": attempt,
                    "reason": failure_reason,
                    "source_tool_call_id": source.id,
                },
            )
        self._mark_observation_failed(
            agent,
            response,
            source_call_id=source.id,
            reason=failure_reason,
        )
        handle.observation_fatal = "automatic_observation_failed"
        handle.observation_fatal_reason = failure_reason

    async def _run_observe_tool(self, agent: Any, observe_call: Any) -> Any:
        """Execute the observe tool through the same adapter path (real HM
        executor) rather than asking the model — runtime-owned call. Emits the
        same tool.call_* lifecycle events a model-selected call would."""
        from agentscope.message import ToolCallBlock
        from agentscope.tool import ToolResponse

        handle = self._handle
        block = ToolCallBlock(
            id=observe_call.id,
            name=observe_call.name,
            input=json.dumps(dict(observe_call.arguments)),
        )
        await handle.emit(
            "tool.call_started",
            tool_call_id=observe_call.id,
            name=observe_call.name,
            payload={"arguments": dict(observe_call.arguments)},
        )
        # Runtime-owned call bypasses the on_acting middleware that binds
        # ``_current_tool_call_id`` — bind it here so the adapter/executor
        # attributes results to the observe call id, not a stale model call.
        from homemaster.substrate.toolkit import (
            _current_tool_call_id,
            _reset_call_id,
        )

        token = _current_tool_call_id.set(observe_call.id)
        try:
            final: Any = None
            async for chunk in agent.toolkit.call_tool(block, agent.state):
                final = chunk
        finally:
            _reset_call_id(token)
        if not isinstance(final, ToolResponse):
            await handle.emit(
                "tool.call_failed",
                tool_call_id=observe_call.id,
                name=observe_call.name,
                payload={"is_error": True, "result": "", "data": {}},
            )
            raise RuntimeError("automatic observe produced no response")
        hm = (getattr(final, "metadata", None) or {}).get("hm") or {}
        data = hm.get("data") if isinstance(hm.get("data"), dict) else {}
        is_error = not _is_success_state(getattr(final, "state", None))
        result_text = "\n".join(
            str(getattr(block, "text", "") or "")
            for block in (getattr(final, "content", None) or [])
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        )
        await handle.emit(
            "tool.call_failed" if is_error else "tool.call_completed",
            tool_call_id=observe_call.id,
            name=observe_call.name,
            payload={
                "is_error": is_error,
                "result": result_text,
                "data": data,
                "backend_attempted": hm.get("backend_attempted"),
                "status": hm.get("status"),
            },
        )
        return final

    async def _validate_manual_observe(self, agent: Any, *, call_id: str, response: Any) -> None:
        handle = self._handle
        barrier = handle.agent_state.pending_model_observation
        if barrier is None:
            return
        try:
            evidence = self._validate_chunk_as_observation(response)
        except Exception as exc:
            barrier.observe_failures += 1
            await handle.emit(
                "model_observation.observe_failed",
                tool_call_id=call_id,
                name=barrier.observe_tool_name,
                payload={
                    "observe_failures": barrier.observe_failures,
                    "reason": str(exc),
                    "source_tool_call_id": barrier.source_tool_call_id,
                },
            )
            if barrier.observe_failures >= MAX_OBSERVE_FAILURES:
                handle.observation_fatal = "model_observation_failed"
                handle.observation_fatal_reason = "model observation retry limit reached"
            return
        self._stamp_observation_of(agent, response, call_id, barrier.source_tool_call_id)
        handle.agent_state.pending_model_observation = None
        handle.agent_state.unconsumed_observation_tool_call_id = call_id
        await handle.emit(
            "model_observation.barrier_cleared",
            tool_call_id=call_id,
            name=barrier.observe_tool_name,
            payload={
                "content_sha256": evidence.content_sha256,
                "pixel_sha256": evidence.pixel_sha256,
                "source_tool_call_id": barrier.source_tool_call_id,
            },
        )

    async def _record_manual_observe(self, *, call_id: str, response: Any) -> None:
        """Model-initiated observe with no pending barrier — validate and
        record the consumed-image marker (``manual_completed``/``manual_failed``),
        matching the legacy runtime's post-batch manual-observation pass."""
        handle = self._handle
        try:
            evidence = self._validate_chunk_as_observation(response)
        except Exception as exc:
            await handle.emit(
                "model_observation.manual_failed",
                tool_call_id=call_id,
                name="observe",
                payload={"reason": str(exc)},
            )
            return
        handle.agent_state.unconsumed_observation_tool_call_id = call_id
        await handle.emit(
            "model_observation.manual_completed",
            tool_call_id=call_id,
            name="observe",
            payload={
                "content_sha256": evidence.content_sha256,
                "pixel_sha256": evidence.pixel_sha256,
            },
        )

    @staticmethod
    def _stamp_observation_of(
        agent: Any,
        response: Any,
        call_id: str,
        source_tool_call_id: str,
    ) -> None:
        def apply(metadata: Any) -> None:
            if not isinstance(metadata, dict):
                return
            hm = dict(metadata.get("hm") or {})
            data = dict(hm.get("data") or {})
            data["observation_of_tool_call_id"] = source_tool_call_id
            hm["data"] = data
            metadata["hm"] = hm

        apply(getattr(response, "metadata", None))
        apply(getattr(_find_result_block(agent, call_id), "metadata", None))

    @staticmethod
    def _mark_observation_failed(
        agent: Any, response: Any, *, source_call_id: str, reason: str
    ) -> None:
        record = {
            "status": "failed",
            "source_tool_call_id": source_call_id,
            "attempts": MAX_OBSERVE_FAILURES,
            "reason": reason,
        }

        def apply(metadata: Any) -> None:
            if not isinstance(metadata, dict):
                return
            hm = dict(metadata.get("hm") or {})
            data = dict(hm.get("data") or {})
            data["automatic_observation"] = record
            hm["data"] = data
            metadata["hm"] = hm

        apply(getattr(response, "metadata", None))
        apply(getattr(_find_result_block(agent, source_call_id), "metadata", None))

    @staticmethod
    def _validate_chunk_as_observation(response: Any) -> Any:
        """Run the HM image validator against an AS ToolResponse by
        projecting it into a canonical ToolResultMessage first."""
        from homemaster.agent.messages import (
            ContentBlock,
            ToolResultMessage,
        )

        content: list[ContentBlock] = []
        for block in getattr(response, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                content.append(ContentBlock(type="text", text=getattr(block, "text", "")))
            elif btype in {"image", "data"}:
                source = getattr(block, "source", None)
                content.append(
                    ContentBlock(
                        type="image",
                        source=(
                            source.model_dump(mode="json")
                            if hasattr(source, "model_dump")
                            else dict(source or {})
                        ),
                    )
                )
        state = getattr(response, "state", None)
        is_error = state in {"error", "denied", "interrupted"} or getattr(state, "value", "") in {
            "error",
            "denied",
            "interrupted",
        }
        result = ToolResultMessage(
            tool_call_id=getattr(response, "id", "") or "",
            name="observe",
            content=content,
            is_error=is_error,
            data=(getattr(response, "metadata", None) or {}).get("hm", {}).get("data", {})
            if isinstance((getattr(response, "metadata", None) or {}).get("hm"), dict)
            else {},
        )
        return validate_observation_result(result)

    @staticmethod
    def _attach_observation(
        agent: Any,
        *,
        response: Any,
        observe_response: Any,
        observe_call_id: str,
        evidence: Any,
        source_call_id: str,
    ) -> None:
        """Attach the validated observation image + machine fields to the
        action's result — mirrors ``attach_automatic_observation``.

        The post-hook runs *after* ``_execute_tool_call`` already built and
        persisted the ``ToolResultBlock``, so we must mutate BOTH the
        transient ``ToolResponse`` and the stored block in
        ``agent.state.context`` (canonical store → session mirror → v2
        snapshot)."""
        images = [
            block
            for block in getattr(observe_response, "content", []) or []
            if getattr(block, "type", None) in {"image", "data"}
        ]
        record = {
            "status": "success",
            "source_tool_call_id": source_call_id,
            "observation_tool_call_id": observe_call_id,
            "content_sha256": evidence.content_sha256,
            "pixel_sha256": evidence.pixel_sha256,
        }

        def apply(target: Any) -> None:
            if target is None:
                return
            content = getattr(target, "content", None)
            output = getattr(target, "output", None)
            sink = (
                content
                if isinstance(content, list)
                else output
                if isinstance(output, list)
                else None
            )
            if images and sink is not None:
                sink.extend(images)
            metadata = getattr(target, "metadata", None)
            if isinstance(metadata, dict):
                hm = dict(metadata.get("hm") or {})
                data = dict(hm.get("data") or {})
                data["automatic_observation"] = record
                hm["data"] = data
                metadata["hm"] = hm

        apply(response)
        apply(_find_result_block(agent, source_call_id))

    def _observe_name(self, schemas: list[Any]) -> str:
        names = {_schema_name(schema) for schema in schemas or [] if _schema_name(schema)}
        if "browser_screenshot" in names:
            return "browser_screenshot"
        if "observe" in names:
            return "observe"
        raise RuntimeError("model observation barrier requires an observation tool")


class ProtocolFenceMiddleware(MiddlewareBase):
    """Pre-dispatch batch fences ported from ``generic_runtime`` — the AS
    toolkit only errors per-call on unknown names, which would let a valid
    companion call execute before the batch's bad call fails. These fences
    preserve HM's atomic batch rejection:

    - **unavailable tool**: any round call whose name was not offered in the
      current model request denies the whole batch
      (``tool_not_available`` / ``tool_batch_contains_unavailable_call``).
    - **terminal allowlist**: when ``settings.permissions`` pins
      ``allowed_terminal_commands``, a non-matching ``terminal`` command
      denies the whole batch (``terminal_command_not_allowed`` /
      ``tool_batch_contains_disallowed_terminal_command``).

    Ordering: must run after ``ObservationBarrierMiddleware`` so the
    barrier's tool trimming is reflected in ``offered_tool_names``, and so
    barrier protocol violations keep firing first (legacy order).
    """

    def __init__(self, *, handle: Any) -> None:
        self._handle = handle

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> Any:
        tools = input_kwargs.get("tools")
        self._handle.offered_tool_names = (
            frozenset(_schema_name(schema) for schema in tools) if isinstance(tools, list) else None
        )
        return await next_handler()

    async def on_check_permission(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> PermissionDecision:
        handle = self._handle
        tool_call = input_kwargs.get("tool_call")
        call_name = _call_name(tool_call)
        call_id = getattr(tool_call, "id", "") or ""

        if call_id.startswith("auto-observe-"):
            return await next_handler()

        round_calls = _round_tool_calls(agent, handle)
        if not any(getattr(c, "id", None) == call_id for c in round_calls):
            # Not part of the current model batch (runtime-owned call) —
            # batch fences don't apply.
            return await next_handler()

        offered = handle.offered_tool_names
        if offered is not None:
            unavailable = sorted(
                {_call_name(call) for call in round_calls if _call_name(call) not in offered}
            )
            if unavailable:
                code = (
                    "tool_not_available"
                    if call_name in unavailable
                    else "tool_batch_contains_unavailable_call"
                )
                message = (
                    f"Tool {call_name!r} was not executed because it was "
                    "not offered in this model request. Use only a "
                    "currently available tool."
                    if call_name in unavailable
                    else "This tool was not executed because its batch also "
                    f"contained unavailable tool(s): {', '.join(unavailable)}. "
                    "Retry using only currently available tools."
                )
                payload = {
                    "status": "protocol_blocked",
                    "backend_attempted": False,
                    "error_code": code,
                    "rejected_tool": call_name,
                    "unavailable_tools": unavailable,
                    "message": message,
                }
                await handle.emit(
                    "tool.protocol_rejected",
                    tool_call_id=call_id,
                    name=call_name,
                    payload={
                        "error_code": code,
                        "backend_attempted": False,
                        "unavailable_tools": unavailable,
                    },
                )
                # Record the protocol payload by call id — the permission
                # chain deep-copies the call block, so ``_project_event``
                # merges this into the result metadata at END.
                handle.denial_payloads[call_id] = payload
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    # Legacy emitted the protocol payload as the result's
                    # JSON content — keep the same model-facing shape.
                    message=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )

        permissions = getattr(handle.settings, "permissions", None)
        allowed_commands = tuple(getattr(permissions, "allowed_terminal_commands", ()) or ())
        if allowed_commands:
            allowed = frozenset(allowed_commands)
            violating = {
                getattr(call, "id", None)
                for call in round_calls
                if _call_name(call) == "terminal"
                and _call_arguments(call).get("command") not in allowed
            }
            if violating:
                is_violator = call_id in violating
                code = (
                    "terminal_command_not_allowed"
                    if is_violator
                    else "tool_batch_contains_disallowed_terminal_command"
                )
                message = (
                    "This terminal call was not executed because its command "
                    "did not exactly match the configured allowlist. Re-read "
                    "the authoritative source and use only an explicitly "
                    "permitted command verbatim."
                    if is_violator
                    else "This tool was not executed because its batch also "
                    "contained a terminal command that failed the exact "
                    "allowlist. Retry the calls separately."
                )
                payload = {
                    "status": "protocol_blocked",
                    "backend_attempted": False,
                    "error_code": code,
                    "rejected_tool": call_name,
                    "message": message,
                }
                await handle.emit(
                    "terminal.command_protocol_rejected",
                    tool_call_id=call_id,
                    name=call_name,
                    payload={
                        "error_code": code,
                        "backend_attempted": False,
                    },
                )
                handle.denial_payloads[call_id] = payload
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
        return await next_handler()


def _find_result_block(agent: Any, tool_call_id: str) -> Any:
    """Locate the persisted ``ToolResultBlock`` for ``tool_call_id`` in the
    engine context (``agent.state.context``) — the canonical store mirrored
    into the HM session."""
    context = getattr(getattr(agent, "state", None), "context", []) or []
    for msg in reversed(context):
        for block in getattr(msg, "content", []) or []:
            if (
                getattr(block, "type", None) == "tool_result"
                and getattr(block, "id", None) == tool_call_id
            ):
                return block
    return None


def _backend_attempted(response: Any) -> bool:
    """Legacy gate: only a result whose backend actually ran may trigger
    automatic observation (``generic_runtime`` requires
    ``data.backend_attempted is True``)."""
    metadata = getattr(response, "metadata", None)
    hm = metadata.get("hm") if isinstance(metadata, dict) else None
    return isinstance(hm, dict) and hm.get("backend_attempted") is True


def _round_tool_calls(agent: Any, handle: Any = None) -> list[Any]:
    """Tool-call blocks issued by the *latest* reasoning round — whether
    finished, denied, or still pending.

    AgentScope merges every reasoning-acting round of one reply into a single
    assistant Msg, so scanning the tail Msg returns the whole reply's calls —
    a stale observed/unavailable/terminal call from an earlier round would
    poison later rounds' batch fences, and ``get_unfinished_tool_calls``
    alone is blind to siblings already decided (atomic-denial needs them).
    The round boundary is the ``round_floor`` watermark recorded by
    ``_project_event`` at each ``ModelCallStartEvent``; without it (test
    doubles, foreign agents) fall back to the whole tail Msg.
    """
    context = getattr(getattr(agent, "state", None), "context", []) or []
    if not context:
        return []
    last = context[-1]
    if getattr(last, "role", "") != "assistant":
        return []
    content = list(getattr(last, "content", []) or [])
    floor = 0
    if handle is not None:
        floor_id, floor_len = getattr(handle, "round_floor", ("", 0))
        if floor_id and floor_id == str(getattr(last, "id", "")):
            floor = min(floor_len, len(content))
    return [block for block in content[floor:] if getattr(block, "type", None) == "tool_call"]


def _call_name(call: Any) -> str:
    return getattr(call, "name", "") or ""


def _call_arguments(call: Any) -> dict[str, Any]:
    raw = getattr(call, "input", None)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _schema_name(schema: Any) -> str:
    if isinstance(schema, dict):
        function = schema.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(schema.get("name") or "")
    return str(getattr(schema, "name", "") or "")


def _is_success_state(state: Any) -> bool:
    value = getattr(state, "value", state)
    return value == "success"


def _usage_from_response(response: Any) -> dict[str, int]:
    chunks = getattr(response, "chunks", None)
    usage = getattr(response, "usage", None)
    if usage is None and chunks:
        usage = getattr(chunks[-1], "usage", None)
    if usage is None:
        return {}
    return {
        key: int(getattr(usage, key) or 0)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_input_tokens",
            "cache_creation_input_tokens",
        )
    }


def _output_sha256(response: Any) -> str | None:
    try:
        chunks = getattr(response, "chunks", None) or []
        payload = [
            c.model_dump(mode="json") if hasattr(c, "model_dump") else str(c) for c in chunks
        ]
        if not payload:
            return None
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    except Exception:
        return None


def _outbound_image_bindings(messages: Any) -> tuple[Any, ...]:
    """Locate base64 image blocks in the outbound AS messages; mirrors
    ``llm_client._attempt_record``'s binding semantics for the middleware
    layer, which sees AS ``Msg``s rather than HM messages + request body."""
    import base64

    from agentscope.message import DataBlock
    from homemaster.providers.attempts import OutboundImageBinding

    def _payloads(block: Any) -> Any:
        if isinstance(block, DataBlock):
            source = getattr(block, "source", None)
            if getattr(source, "type", None) == "base64":
                data = getattr(source, "data", None)
                if isinstance(data, str) and data:
                    yield data
        for item in getattr(block, "output", ()) or ():
            yield from _payloads(item)

    bindings: list[Any] = []
    for message_index, msg in enumerate(messages or []):
        for block_index, block in enumerate(getattr(msg, "content", []) or []):
            for data in _payloads(block):
                try:
                    content = base64.b64decode(data, validate=True)
                except ValueError:
                    content = data.encode("ascii", errors="replace")
                bindings.append(
                    OutboundImageBinding(
                        message_index=message_index,
                        block_index=block_index,
                        content_sha256=hashlib.sha256(content).hexdigest(),
                    )
                )
    return tuple(bindings)


__all__ = [
    "ContextAssemblyMiddleware",
    "ObservationBarrierMiddleware",
    "ProtocolFenceMiddleware",
    "ProviderObservabilityMiddleware",
]
