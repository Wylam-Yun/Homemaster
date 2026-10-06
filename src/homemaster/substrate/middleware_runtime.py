"""Phase-2 HM middlewares for the AgentScope reasoning loop.

Three middlewares port the HomeMaster runtime contract onto AS hooks:

- ``ContextAssemblyMiddleware`` — ``on_model_call`` rewrites ``messages``
  through the HM ``ContextAssembler`` (memory, compaction, tail projection),
  and performs reactive compaction retries on context-length errors.
- ``ProviderObservabilityMiddleware`` — ``on_model_call`` emits the HM
  transport.* request/response events and writes ``ProviderAttemptRecord``.
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
        # post-tool mutations reach the assembler's canonical history. No-op
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
        prompt lacks) + assembled messages.
        ``_prepare_model_input``'s messages[0] SystemMsg is replaced because
        AS's own ``_system_prompt`` is a strict subset."""
        from agentscope.message import SystemMsg
        from homemaster.substrate.messages import to_agent_scope

        prompt = getattr(prepared, "system_prompt", "") or ""
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
    "ProtocolFenceMiddleware",
    "ProviderObservabilityMiddleware",
]
