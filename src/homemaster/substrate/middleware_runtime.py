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

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> Any:
        # Live engine-context reference for the shell's assistant-event
        # projection (the assembled Msg lands in agent.state.context).
        self._handle.engine_context = agent.state.context

        if self._assembler is None:
            return await next_handler()

        messages, tools = await self._assemble(input_kwargs)
        retries = 0
        while True:
            try:
                resp = await next_handler(messages=messages, tools=tools)
            except Exception as exc:
                if (
                    not _is_context_length_error(str(exc))
                    or retries >= self._max_reactive_retries
                ):
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
                    messages, tools = await self._recompact(
                        input_kwargs, retries
                    )
                    stream = await next_handler(
                        messages=messages, tools=tools
                    )
                    continue
                raise
            return

    async def _recompact(
        self, input_kwargs: dict, attempt: int
    ) -> tuple[list[Any], Any]:
        await self._handle.emit(
            "runtime.reactive_compact_started",
            payload={"attempt": attempt},
        )
        prepared = await self._compact()
        metrics = getattr(prepared, "metrics", None)
        if metrics is None:
            raise RuntimeError("reactive compaction produced no metrics")
        await self._handle.emit(
            "context.compaction",
            payload={"metrics": _metrics_payload(metrics)},
        )
        return self._render(input_kwargs, prepared)

    async def _assemble(self, input_kwargs: dict) -> tuple[list[Any], Any]:
        handle = self._handle
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
        return self._render(input_kwargs, prepared)

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
            prompt = append_model_observation_prompt(
                prompt, tool_name=barrier.observe_tool_name
            )
        messages = [SystemMsg(name="system", content=prompt)] + to_agent_scope(
            list(prepared.messages)
        )
        return messages, input_kwargs.get("tools")

    async def _compact(self) -> Any:
        handle = self._handle
        prepared = await _maybe_async(
            _assembler_prepare(self._assembler),
            session=handle.session,
            agent_state=handle.agent_state,
            task_state_store=handle.task_state_store,
            tools=handle.all_tool_schemas or None,
            force_compact=True,
        )
        if self._on_compaction is not None:
            result = self._on_compaction(getattr(prepared, "metrics", None))
            if inspect.isawaitable(result):
                await result
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
            model_attempt_id=(
                f"{self._handle.run_id}:attempt-{attempt_index:04d}"
            ),
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
            observe_name = barrier.observe_tool_name or self._observe_name(
                tools
            )
            tools = [
                schema for schema in tools if _schema_name(schema) == observe_name
            ]
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
        return append_model_observation_prompt(
            current_prompt, tool_name=barrier.observe_tool_name
        )

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
        round_calls = _round_tool_calls(agent)

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
                    "A pending environment action must be followed by one "
                    "observe call.",
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
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    "A state-changing environment action must be the only "
                    "call in its batch."
                ),
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
            await self._automatic_observe(
                agent, source_call=tool_call, response=final
            )
        elif (
            barrier is not None
            and call_name == barrier.observe_tool_name
        ):
            await self._validate_manual_observe(
                agent, call_id=call_id, response=final
            )
        elif (
            barrier is None
            and call_name in self._OBSERVE_NAMES
            and _is_success_state(final.state)
        ):
            await self._record_manual_observe(call_id=call_id, response=final)

    async def _automatic_observe(
        self, agent: Any, *, source_call: Any, response: Any
    ) -> None:
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
            observe_call = automatic_observation_call(
                source, attempt, tool_name=observe_name
            )
            observe_response: Any = None
            try:
                observe_response = await self._run_observe_tool(
                    agent, observe_call
                )
                evidence = self._validate_chunk_as_observation(observe_response)
            except Exception as exc:
                failure_reason = str(exc)
            else:
                self._attach_observation(
                    agent,
                    response=response,
                    observe_response=observe_response,
                    observe_call_id=observe_call.id,
                    evidence=evidence,
                    source_call_id=source.id,
                )
                handle.agent_state.unconsumed_observation_tool_call_id = (
                    source.id
                )
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
        final: Any = None
        async for chunk in agent.toolkit.call_tool(block, agent.state):
            final = chunk
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
        await handle.emit(
            "tool.call_failed" if is_error else "tool.call_completed",
            tool_call_id=observe_call.id,
            name=observe_call.name,
            payload={
                "is_error": is_error,
                "result": data.get("text", ""),
                "data": data,
                "backend_attempted": hm.get("backend_attempted"),
                "status": hm.get("status"),
            },
        )
        return final

    async def _validate_manual_observe(
        self, agent: Any, *, call_id: str, response: Any
    ) -> None:
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
                handle.observation_fatal_reason = (
                    "model observation retry limit reached"
                )
            return
        self._stamp_observation_of(
            agent, response, call_id, barrier.source_tool_call_id
        )
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

    async def _record_manual_observe(
        self, *, call_id: str, response: Any
    ) -> None:
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
                content.append(
                    ContentBlock(type="text", text=getattr(block, "text", ""))
                )
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
        is_error = state in {"error", "denied", "interrupted"} or getattr(
            state, "value", ""
        ) in {"error", "denied", "interrupted"}
        result = ToolResultMessage(
            tool_call_id=getattr(response, "id", "") or "",
            name="observe",
            content=content,
            is_error=is_error,
            data=(getattr(response, "metadata", None) or {}).get("hm", {}).get(
                "data", {}
            )
            if isinstance(
                (getattr(response, "metadata", None) or {}).get("hm"), dict
            )
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
                else output if isinstance(output, list) else None
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
        names = {
            _schema_name(schema) for schema in schemas or [] if _schema_name(schema)
        }
        if "browser_screenshot" in names:
            return "browser_screenshot"
        if "observe" in names:
            return "observe"
        raise RuntimeError(
            "model observation barrier requires an observation tool"
        )


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


def _round_tool_calls(agent: Any) -> list[Any]:
    """Tool-call blocks of the current reply round, read from the engine
    context tail (the assistant Msg produced by the last reasoning)."""
    context = getattr(getattr(agent, "state", None), "context", []) or []
    for msg in reversed(context):
        if getattr(msg, "role", "") != "assistant":
            break
        calls = [
            block
            for block in getattr(msg, "content", []) or []
            if getattr(block, "type", None) == "tool_call"
        ]
        if calls:
            return calls
    return []


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
            c.model_dump(mode="json") if hasattr(c, "model_dump") else str(c)
            for c in chunks
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

    bindings: list[Any] = []
    for message_index, msg in enumerate(messages or []):
        for block_index, block in enumerate(
            getattr(msg, "content", []) or []
        ):
            if not isinstance(block, DataBlock):
                continue
            source = getattr(block, "source", None)
            if getattr(source, "type", None) != "base64":
                continue
            data = getattr(source, "data", None)
            if not isinstance(data, str) or not data:
                continue
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


def _metrics_payload(metrics: Any) -> dict[str, Any]:
    if metrics is None:
        return {}
    if hasattr(metrics, "model_dump"):
        return metrics.model_dump(mode="json")
    if isinstance(metrics, dict):
        return dict(metrics)
    return {
        key: getattr(metrics, key)
        for key in (
            "estimated_input_tokens",
            "removed_messages",
            "retained_messages",
        )
        if hasattr(metrics, key)
    }


__all__ = [
    "ContextAssemblyMiddleware",
    "ObservationBarrierMiddleware",
    "ProviderObservabilityMiddleware",
]
