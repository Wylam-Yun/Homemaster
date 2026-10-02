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
from typing import Any, AsyncGenerator, Callable

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
        try:
            return await next_handler(messages=messages, tools=tools)
        except Exception as exc:
            if not _is_context_length_error(str(exc)):
                raise
            retries = 0
            while retries < self._max_reactive_retries:
                retries += 1
                await self._handle.emit(
                    "runtime.reactive_compact_started",
                    payload={"attempt": retries},
                )
                metrics = await self._compact()
                if metrics is None:
                    raise
                await self._handle.emit(
                    "context.compaction",
                    payload={"metrics": _metrics_payload(metrics)},
                )
                try:
                    return await next_handler(messages=messages, tools=tools)
                except Exception as retry_exc:
                    if not _is_context_length_error(str(retry_exc)):
                        raise
                    if retries >= self._max_reactive_retries:
                        raise
            raise

    async def _assemble(self, input_kwargs: dict) -> tuple[list[Any], Any]:
        from homemaster.substrate.messages import to_agent_scope

        handle = self._handle
        force = self._pending_force_compact
        self._pending_force_compact = None
        prepared = self._assembler.prepare(
            session=handle.session,
            agent_state=handle.agent_state,
            task_state_store=handle.task_state_store,
            tools=input_kwargs.get("tools"),
            force_compact=force,
        )
        handle.agent_state.estimated_context_tokens = getattr(
            getattr(prepared, "metrics", None), "estimated_input_tokens", 0
        )
        as_messages = to_agent_scope(list(prepared.messages))
        return as_messages, input_kwargs.get("tools")

    async def _compact(self) -> Any:
        handle = self._handle
        prepared = self._assembler.prepare(
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
        return getattr(prepared, "metrics", None)


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
            self._record_attempt(
                request_sha=request_sha,
                status="failed",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=str(exc),
                usage={},
                output_sha256=None,
                stream_error=str(exc),
            )
            raise

        if hasattr(response, "__aiter__"):
            return self._wrap_stream(
                response,
                attempt_index=attempt_index,
                request_sha=request_sha,
                started=started,
            )
        await self._complete(
            response,
            attempt_index=attempt_index,
            request_sha=request_sha,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return response

    async def _wrap_stream(
        self,
        stream: Any,
        *,
        attempt_index: int,
        request_sha: str,
        started: float,
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
            self._record_attempt(
                request_sha=request_sha,
                status="failed",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=str(exc),
                usage={},
                output_sha256=None,
                stream_error=str(exc),
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
        self._record_attempt(
            request_sha=request_sha,
            status="success",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=None,
            usage=usage,
            output_sha256=None,
            stream_error=None,
        )

    async def _complete(
        self,
        response: Any,
        *,
        attempt_index: int,
        request_sha: str,
        latency_ms: float,
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
        self._record_attempt(
            request_sha=request_sha,
            status="success",
            latency_ms=latency_ms,
            error=None,
            usage=usage,
            output_sha256=_output_sha256(response),
            stream_error=None,
        )

    def _record_attempt(self, **fields: Any) -> None:
        sink = self._attempt_sink
        if sink is None:
            return
        record = _make_attempt_record(fields)
        record_attempt = getattr(sink, "record_attempt", None)
        if callable(record_attempt):
            record_attempt(record)


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
                name=observe_name if barrier is not None else None,
                payload={"tool_call_id": consumed},
            )
        return await next_handler(tools=tools)

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
        round_calls = _round_tool_calls(agent)

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

        if final is None or getattr(final, "state", None) is None:
            return
        barrier = handle.agent_state.pending_model_observation

        if (
            barrier is None
            and action_requires_model_observation(handle.tool_registry, call_name)
            and _is_success_state(final.state)
        ):
            await self._automatic_observe(
                agent, source_call=tool_call, response=final
            )
        elif (
            barrier is not None
            and call_name == barrier.observe_tool_name
            and _is_success_state(final.state)
        ):
            await self._validate_manual_observe(call_id=call_id, response=final)

    async def _automatic_observe(
        self, agent: Any, *, source_call: Any, response: Any
    ) -> None:
        handle = self._handle
        observe_name = self._observe_name(_round_schemas(handle))
        source = ToolCall(
            id=getattr(source_call, "id", "") or "",
            name=getattr(source_call, "name", "") or "",
            arguments=dict(getattr(source_call, "input", {}) or {}),
        )
        observe_call = automatic_observation_call(
            source, attempt=1, tool_name=observe_name
        )
        await handle.emit(
            "model_observation.automatic_started",
            tool_call_id=observe_call.id,
            name=observe_name,
            payload={"source_tool_call_id": source.id},
        )
        try:
            observe_response = await self._run_observe_tool(
                agent, observe_call
            )
            evidence = self._validate_chunk_as_observation(observe_response)
        except Exception as exc:
            await handle.emit(
                "model_observation.automatic_failed",
                tool_call_id=observe_call.id,
                name=observe_name,
                payload={
                    "error": str(exc),
                    "error_code": "automatic_observation_failed",
                    "source_tool_call_id": source.id,
                },
            )
            handle.observation_fatal = "automatic_observation_failed"
            return
        self._attach_observation(
            response=response,
            observe_response=observe_response,
            observe_call_id=observe_call.id,
            evidence=evidence,
            source_call_id=source.id,
        )
        handle.agent_state.unconsumed_observation_tool_call_id = observe_call.id
        await handle.emit(
            "model_observation.automatic_completed",
            tool_call_id=observe_call.id,
            name=observe_name,
            payload={
                "source_tool_call_id": source.id,
                "content_sha256": evidence.content_sha256,
                "pixel_sha256": evidence.pixel_sha256,
            },
        )

    async def _run_observe_tool(self, agent: Any, observe_call: Any) -> Any:
        """Execute the observe tool through the same adapter path (real HM
        executor) rather than asking the model — runtime-owned call."""
        from agentscope.message import ToolCallBlock
        from agentscope.tool import ToolResponse

        block = ToolCallBlock(
            id=observe_call.id,
            name=observe_call.name,
            input=dict(observe_call.arguments),
        )
        final: Any = None
        async for chunk in agent.toolkit.call_tool(block, agent.state):
            final = chunk
        if not isinstance(final, ToolResponse):
            raise RuntimeError("automatic observe produced no response")
        return final

    async def _validate_manual_observe(
        self, *, call_id: str, response: Any
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
                    "source_tool_call_id": barrier.source_tool_call_id,
                    "error": str(exc),
                },
            )
            if barrier.observe_failures >= MAX_OBSERVE_FAILURES:
                handle.observation_fatal = "model_observation_failed"
            return
        handle.agent_state.pending_model_observation = None
        handle.agent_state.unconsumed_observation_tool_call_id = call_id
        await handle.emit(
            "model_observation.barrier_cleared",
            tool_call_id=call_id,
            name=barrier.observe_tool_name,
            payload={
                "source_tool_call_id": barrier.source_tool_call_id,
                "content_sha256": evidence.content_sha256,
                "pixel_sha256": evidence.pixel_sha256,
            },
        )

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
        *, response: Any, observe_response: Any, observe_call_id: str,
        evidence: Any, source_call_id: str,
    ) -> None:
        """Attach the validated observation image + machine fields to the
        action's ToolResponse — mirrors ``attach_automatic_observation``."""
        images = [
            block
            for block in getattr(observe_response, "content", []) or []
            if getattr(block, "type", None) in {"image", "data"}
        ]
        if images and hasattr(response, "content"):
            response.content.extend(images)
        metadata = dict(getattr(response, "metadata", None) or {})
        hm = dict(metadata.get("hm") or {})
        data = dict(hm.get("data") or {})
        data["automatic_observation"] = {
            "status": "success",
            "source_tool_call_id": source_call_id,
            "observation_tool_call_id": observe_call_id,
            "content_sha256": evidence.content_sha256,
            "pixel_sha256": evidence.pixel_sha256,
        }
        hm["data"] = data
        metadata["hm"] = hm
        if hasattr(response, "metadata"):
            response.metadata = metadata

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


def _make_attempt_record(fields: dict[str, Any]) -> Any:
    try:
        from homemaster.providers.attempts import ProviderAttemptRecord

        return ProviderAttemptRecord(**fields)
    except Exception:
        return dict(fields)


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
