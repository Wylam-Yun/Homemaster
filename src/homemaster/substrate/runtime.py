"""``AsAgentRuntime`` — ``AgentRuntime``-compatible shell over AgentScope.

Phase 2 (plan/V3.7): the AS ``Agent`` owns the reasoning-acting loop; this
class preserves the HomeMaster runtime contract around it — deadlines,
SIGINT/cancellation, session mirror, schema-v2 snapshots, provider attempt
records, loop guards, stop conditions, and the HM event vocabulary.

Event projection (AgentEvent -> RuntimeEvent) is lossy by design for the
public stream: only fields HM's projection allowlist already exposes are
carried through.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homemaster.agent.generic_runtime import (
    GenericRunResult,
    StopCondition,
    _cancelled,
)
from homemaster.agent.interrupt import InterruptController
from homemaster.agent.messages import (
    ContentBlock,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)
from homemaster.agent.model_observation import (
    MAX_OBSERVE_FAILURES,
    MAX_PROTOCOL_FAILURES,
)
from homemaster.agent.normalized import RunContext
from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import SessionPersistenceManager
from homemaster.agent.state import AgentState, ProviderUsage
from homemaster.events import FanoutEventSink
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.substrate.messages import from_agent_scope, to_agent_scope
from homemaster.substrate.toolkit import RunScope, RunScopeMiddleware
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore
from homemaster.tools.contracts import PermissionSubject

_REASON_ERROR_STATES = frozenset({"error", "denied", "interrupted"})


@dataclass
class AsRunHandle:
    """Per-run state shared between the shell and the HM middlewares."""

    session: AgentSession
    agent_state: AgentState
    task_state_store: TaskStateStore
    run_id: str
    settings: Any
    scope: RunScope
    emit: Callable[..., Any]
    events: list[RuntimeEvent] = field(default_factory=list)
    all_tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    tool_call_names: dict[str, str] = field(default_factory=dict)
    pending_args: dict[str, str] = field(default_factory=dict)
    reply_finished_reason: str | None = None
    reply_error: Any = None
    tool_registry: Any = None
    engine_context: list[Any] = field(default_factory=list)
    observation_fatal: str | None = None
    observation_fatal_reason: str = ""
    normal_iterations: int = 0
    # Tool names offered to the model on the current reasoning round —
    # ProtocolFenceMiddleware records them in on_model_call and rejects
    # batches containing calls outside this set (legacy parity).
    offered_tool_names: frozenset[str] | None = None
    # Deep-copied canonical messages of the last rendered model input —
    # fed to the provider_attempt_context_binder (mindmemos_feedback)
    # alongside each new assistant tool_call batch, matching the legacy
    # ``frozen_messages`` contract.
    last_frozen_messages: list[Any] = field(default_factory=list)


class AsAgentRuntime:
    """Drives one user turn through a vendored AgentScope ``Agent``.

    Constructor dependencies mirror ``AgentRuntime``; the model is a
    vendored ``ChatModelBase`` (built by ``chat_model_from_profile``), and
    tools enter as ``HomeToolAdapter`` instances so the HM permission/
    physical chain stays authoritative inside the tool body.
    """

    def __init__(
        self,
        *,
        model: Any,
        system_prompt: str = "",
        tools: list[Any] | None = None,
        middlewares: list[Any] | None = None,
        max_tool_iterations: int | None = 12,
        stop_condition: StopCondition | None = None,
        context_assembler: Any = None,
        provider_attempt_sink_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._tools = list(tools or [])
        self._extra_middlewares = list(middlewares or [])
        self._max_tool_iterations = max_tool_iterations
        self._stop_condition = stop_condition
        self._context_assembler = context_assembler
        self._provider_attempt_sink_factory = provider_attempt_sink_factory

    async def run(
        self,
        session: AgentSession,
        user_text: str,
        run_context: RunContext | None = None,
        *,
        user_content: list[ContentBlock] | None = None,
        event_sink: Any = None,
        run_id: str | None = None,
        settings: Any = None,
        agent_state: AgentState | None = None,
        task_state_store: TaskStateStore | None = None,
        force_compact: str | bool | None = None,
        tool_registry: Any = None,
        cancellation_token: Any = None,
        deadline: Any = None,
        on_compaction: Callable[[Any], Any] | None = None,
        engine_state: Any = None,
        scope: RunScope | None = None,
        propagate_exceptions: tuple[type[BaseException], ...] = (),
    ) -> GenericRunResult:
        """Execute one agent run through the AgentScope reasoning loop.

        ``propagate_exceptions`` lists exception types that must surface raw
        out of this method (after stream/dangling-tool-call cleanup) instead
        of being classified into ``deadline_exceeded``/``transport_error``
        results — the application layer uses it for the session-generation
        fence and the recall deadline, matching the legacy engine contract."""
        from agentscope.agent import Agent
        from agentscope.tool import Toolkit

        run_id = run_id or uuid.uuid4().hex[:12]
        self._propagate_exceptions = tuple(propagate_exceptions)
        events: list[RuntimeEvent] = []
        observability = getattr(settings, "observability", None)
        interrupt = InterruptController(
            abort_llm_stream=bool(
                getattr(observability, "interrupt_abort_llm_stream", True)
            )
        )
        old_sigint_handler: Any = None
        signal_registered = False
        if bool(getattr(observability, "interrupt_enabled", True)):
            try:
                old_sigint_handler = signal.signal(
                    signal.SIGINT, interrupt.handle_sigint
                )
                signal_registered = True
            except ValueError:
                signal_registered = False

        async def emit(event_type: str, **kwargs: Any) -> None:
            local_only = bool(kwargs.pop("local_only", False))
            event = RuntimeEvent(
                type=event_type,
                session_id=session.session_id,
                run_id=run_id,
                turn_index=0,
                tool_call_id=kwargs.pop("tool_call_id", None),
                name=kwargs.pop("name", None),
                payload=kwargs.pop("payload", {}),
                **{k: v for k, v in kwargs.items() if k != "payload"},
            )
            events.append(event)
            if event_sink is not None and not local_only:
                aemit = getattr(event_sink, "aemit", None)
                if callable(aemit):
                    await aemit(event)
                else:
                    value = event_sink.emit(event)
                    if inspect.isawaitable(value):
                        await value

        initial_content = user_content or normalize_content(user_text)
        session.append(UserMessage(content=initial_content))
        if agent_state is None:
            agent_state = AgentState(
                run_id=run_id,
                session_id=session.session_id,
                max_tool_iterations=self._max_tool_iterations,
            )
        else:
            agent_state.run_id = run_id
            agent_state.session_id = session.session_id
            agent_state.max_tool_iterations = self._max_tool_iterations
        if task_state_store is None and run_context is not None:
            task_state_store = run_context.deps.get("task_state_store")
        if task_state_store is None:
            task_state_store = TaskStateStore(run_id=run_id)
        if run_context is not None:
            run_context.deps["task_state_store"] = task_state_store

        if scope is None:
            scope = RunScope(
                session_id=session.session_id,
                run_id=run_id,
                permission_subject=_resolve_subject(run_context, settings),
                working_directory=_resolve_workdir(run_context, settings),
                deadline=deadline,
                cancellation=cancellation_token or interrupt,
                backend=(
                    run_context.deps.get("backend") if run_context else None
                ),
                domain_observer=(
                    run_context.deps.get("domain_observer")
                    if run_context
                    else None
                ),
                services=dict(run_context.deps) if run_context else {},
                turn_index=agent_state.turn_index,
            )
        else:
            scope.run_id = run_id
            scope.turn_index = agent_state.turn_index
            if scope.deadline is None:
                scope.deadline = deadline
            if scope.cancellation is None:
                scope.cancellation = cancellation_token or interrupt
            if run_context is not None:
                merged = dict(scope.services)
                merged.update(run_context.deps)
                scope.services = merged

        # Engine state: provided (resume) or seeded from the session mirror.
        # The session mirror is projected from the engine context — never the
        # reverse — for the remainder of the run.
        if engine_state is None:
            from agentscope.state import AgentState as EngineState

            prior = list(session.messages[:-1])
            engine_state = EngineState(
                session_id=session.session_id,
                context=to_agent_scope(prior) if prior else [],
            )

        persistence = self._build_persistence_manager(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state_store,
            engine_state=engine_state,
            settings=settings,
        )
        if persistence is not None:
            persistence.append_message(session.messages[-1])
            event_sink = (
                persistence
                if event_sink is None
                else FanoutEventSink([event_sink, persistence])
            )

        def save_snapshot(status: str | None = None) -> None:
            if persistence is None:
                return
            if status is not None:
                agent_state.status = status  # type: ignore[assignment]
            self._sync_session(session, engine_state)
            persistence.save_snapshot()

        attempt_sink = (
            self._provider_attempt_sink_factory()
            if self._provider_attempt_sink_factory is not None
            else None
        )
        handle = AsRunHandle(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state_store,
            run_id=run_id,
            settings=settings,
            scope=scope,
            emit=emit,
            events=events,
            all_tool_schemas=_tool_schemas(tool_registry),
            tool_registry=tool_registry,
        )

        middlewares: list[Any] = [RunScopeMiddleware()]
        from homemaster.substrate.middleware_runtime import (
            ContextAssemblyMiddleware,
            ObservationBarrierMiddleware,
            ProtocolFenceMiddleware,
            ProviderObservabilityMiddleware,
        )

        middlewares.append(
            ContextAssemblyMiddleware(
                handle=handle,
                assembler=self._context_assembler,
                force_compact=force_compact,
                on_compaction=on_compaction,
            )
        )
        middlewares.append(ObservationBarrierMiddleware(handle=handle))
        middlewares.append(ProtocolFenceMiddleware(handle=handle))
        middlewares.append(
            ProviderObservabilityMiddleware(
                handle=handle,
                attempt_sink=attempt_sink,
            )
        )
        middlewares.extend(self._extra_middlewares)

        # AS counts every reasoning iteration toward max_iters; HM only counts
        # "normal" ones and grants free observation follow-up turns (bounded by
        # the protocol/observe failure caps). Give AS the HM budget plus that
        # grace headroom — the authoritative budget check still runs in
        # ``_project_event`` via ``normal_iterations``.
        react_config = None
        if self._max_tool_iterations is not None:
            from agentscope.agent import ReActConfig

            react_config = ReActConfig(
                max_iters=(
                    self._max_tool_iterations
                    + MAX_PROTOCOL_FAILURES
                    + MAX_OBSERVE_FAILURES
                    + 2
                )
            )

        # HM's ContextAssembler is the single context authority — disarm AS's
        # own context mutators so they cannot silently rewrite the canonical
        # transcript: no runtime-state HintBlocks, no image dropping, and
        # compression fails loudly (HM's assembler/reactive path owns it).
        from agentscope.agent import ContextConfig, InjectionConfig

        context_config = ContextConfig(
            trigger_ratio=0.9,
            max_image_num=10000,
            compression_fallback_to_truncation=False,
            compression_tool_enabled=False,
        )
        injection_config = InjectionConfig(inject_runtime_state=False)

        agent = Agent(
            name="homemaster",
            system_prompt=self._system_prompt,
            model=self._model,
            toolkit=Toolkit(tools=list(self._tools)),
            middlewares=middlewares,
            state=engine_state,
            react_config=react_config,
            context_config=context_config,
            injection_config=injection_config,
        )

        await emit(
            "runtime.turn_started",
            payload={
                "user_text": user_text,
                "content_block_types": [b.type for b in initial_content],
            },
        )

        input_msg = to_agent_scope([UserMessage(content=initial_content)])[0]
        try:
            result = await self._drive_reply(
                agent=agent,
                input_msg=input_msg,
                handle=handle,
                emit=emit,
                interrupt=interrupt,
                cancellation_token=cancellation_token,
                deadline=deadline,
                session=session,
                engine_state=engine_state,
                persistence=persistence,
                save_snapshot=save_snapshot,
            )
            result.engine_state = engine_state
            return result
        finally:
            if signal_registered:
                signal.signal(signal.SIGINT, old_sigint_handler)

    # ------------------------------------------------------------------
    # Reply driver
    # ------------------------------------------------------------------

    async def _drive_reply(
        self,
        *,
        agent: Any,
        input_msg: Any,
        handle: AsRunHandle,
        emit: Callable[..., Any],
        interrupt: InterruptController,
        cancellation_token: Any,
        deadline: Any,
        session: AgentSession,
        engine_state: Any,
        persistence: Any,
        save_snapshot: Callable[..., Any],
    ) -> GenericRunResult:
        from agentscope.event import ReplyFinishedReason
        from agentscope.message import Msg

        events = handle.events
        run_id = handle.run_id
        stream = agent.reply_stream(input_msg, yield_final_msg=True)
        pending_anext: list[asyncio.Task | None] = [None]

        class _StreamAbortShim:
            """InterruptController aborts via a synchronous ``close()``;
            the AS reply generator only exposes ``aclose()`` — cancel the
            pending ``__anext__`` task instead (same abort boundary)."""

            def close(self) -> None:
                task = pending_anext[0]
                if task is not None and not task.done():
                    task.cancel()

        interrupt.set_stream(_StreamAbortShim())
        final_msg: Any = None
        try:
            while True:
                if _cancelled(interrupt, cancellation_token):
                    await stream.aclose()
                    return await self._cancel_result(
                        session,
                        run_id,
                        events,
                        emit=emit,
                        phase="as_reply",
                        handle=handle,
                        persistence=persistence,
                        engine_state=engine_state,
                    )
                task = asyncio.ensure_future(stream.__anext__())
                pending_anext[0] = task
                try:
                    item = await _await_with_deadline(
                        task,
                        deadline=deadline,
                        operation="agentscope reply",
                    )
                except StopAsyncIteration:
                    break
                finally:
                    pending_anext[0] = None
                if isinstance(item, Msg):
                    final_msg = item
                    continue
                stop = await self._project_event(
                    item,
                    handle=handle,
                    emit=emit,
                    stream=stream,
                    session=session,
                    save_snapshot=save_snapshot,
                )
                if (
                    stop is None
                    and getattr(handle, "observation_fatal", None) is not None
                ):
                    # The fatal flag is set by the on_acting post-hook, which
                    # resumes after the action's ToolResultEndEvent — check at
                    # every event boundary so a text-only final reply cannot
                    # silently drop it.
                    stop = await self._fail_observation_fatal(
                        handle,
                        session=session,
                        emit=emit,
                        stream=stream,
                        save_snapshot=save_snapshot,
                    )
                if stop is not None:
                    return stop
        except TimeoutError as exc:
            await _close_stream(stream)
            await self._close_dangling_tool_calls(handle, emit)
            self._sync_session(session, engine_state)
            if isinstance(exc, self._propagate_exceptions):
                save_snapshot("failed")
                raise
            await emit(
                "runtime.turn_failed",
                payload={
                    "error": "agentscope reply exceeded the run deadline",
                    "error_code": "deadline_exceeded",
                },
            )
            save_snapshot("failed")
            return GenericRunResult(
                run_id=run_id,
                status="failed",
                session=session,
                events=events,
                error_code="deadline_exceeded",
            )
        except asyncio.CancelledError:
            await _close_stream(stream)
            return await self._cancel_result(
                session,
                run_id,
                events,
                emit=emit,
                phase="as_reply",
                handle=handle,
                persistence=persistence,
                engine_state=engine_state,
            )
        except Exception as exc:
            await _close_stream(stream)
            await self._close_dangling_tool_calls(handle, emit)
            self._sync_session(session, engine_state)
            if isinstance(exc, self._propagate_exceptions):
                save_snapshot("failed")
                raise
            await emit(
                "runtime.turn_failed",
                payload={
                    "error": _flatten_error(exc),
                    "error_code": "transport_error",
                },
            )
            save_snapshot("failed")
            return GenericRunResult(
                run_id=run_id,
                status="failed",
                session=session,
                events=events,
                error_code="transport_error",
            )
        finally:
            interrupt.clear_stream()

        self._sync_session(session, engine_state)
        reason = handle.reply_finished_reason
        if reason == ReplyFinishedReason.INTERRUPTED or reason == "interrupted":
            return await self._cancel_result(
                session,
                run_id,
                events,
                emit=emit,
                phase="as_reply",
                handle=handle,
                persistence=persistence,
                engine_state=engine_state,
            )
        if reason == ReplyFinishedReason.ERROR or reason == "error":
            error_text = ""
            if handle.reply_error is not None:
                error_text = str(
                    getattr(handle.reply_error, "message", handle.reply_error)
                )
            await emit(
                "runtime.turn_failed",
                payload={
                    "error": error_text or "agentscope reply failed",
                    "error_code": "reply_error",
                },
            )
            save_snapshot("failed")
            return GenericRunResult(
                run_id=run_id,
                status="failed",
                session=session,
                events=events,
                error_code="reply_error",
            )
        if (
            reason == ReplyFinishedReason.EXCEED_MAX_ITERS
            or reason == "exceed_max_iters"
        ):
            await emit(
                "runtime.budget_exhausted",
                payload={
                    "max_tool_iterations": self._max_tool_iterations,
                    "error_code": "max_tool_iterations_exceeded",
                },
            )
            save_snapshot("failed")
            return GenericRunResult(
                run_id=run_id,
                status="failed",
                session=session,
                events=events,
                error_code="max_tool_iterations_exceeded",
            )

        reply_text = _msg_text(final_msg)
        handle.agent_state.last_assistant_text = reply_text
        save_snapshot("replied")
        await emit("runtime.turn_completed", payload={"final_reply": reply_text})
        return GenericRunResult(
            run_id=run_id,
            status="replied",
            session=session,
            events=events,
            final_reply=reply_text,
        )

    # ------------------------------------------------------------------
    # Event projection
    # ------------------------------------------------------------------

    async def _project_event(
        self,
        item: Any,
        *,
        handle: AsRunHandle,
        emit: Callable[..., Any],
        stream: Any,
        session: AgentSession,
        save_snapshot: Callable[..., Any],
    ) -> GenericRunResult | None:
        """Project one AgentEvent into HM RuntimeEvents. Returns a terminal
        result when a boundary decision (stop_condition / guard / truncation)
        ends the run early."""
        from agentscope.event import (
            ModelCallEndEvent,
            ModelCallStartEvent,
            ReplyEndEvent,
            TextBlockDeltaEvent,
            ThinkingBlockDeltaEvent,
            ToolCallDeltaEvent,
            ToolCallEndEvent,
            ToolCallStartEvent,
            ToolResultEndEvent,
            ToolResultStartEvent,
            ToolResultTextDeltaEvent,
        )

        agent_state = handle.agent_state
        if isinstance(item, TextBlockDeltaEvent):
            await emit("transport.delta", payload={"text_delta": item.delta})
        elif isinstance(item, ThinkingBlockDeltaEvent):
            await emit(
                "transport.delta", payload={"reasoning_delta": item.delta}
            )
        elif isinstance(item, ToolCallStartEvent):
            handle.tool_call_names[item.tool_call_id] = item.tool_call_name
            handle.pending_args[item.tool_call_id] = ""
        elif isinstance(item, ToolCallDeltaEvent):
            handle.pending_args[item.tool_call_id] = (
                handle.pending_args.get(item.tool_call_id, "") + item.delta
            )
        elif isinstance(item, ToolCallEndEvent):
            raw = handle.pending_args.pop(item.tool_call_id, "")
            try:
                arguments = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                arguments = {"_raw": raw}
            await emit(
                "tool.call_started",
                tool_call_id=item.tool_call_id,
                name=handle.tool_call_names.get(item.tool_call_id),
                payload={"arguments": arguments},
            )
        elif isinstance(item, ToolResultStartEvent):
            handle.tool_call_names[item.tool_call_id] = item.tool_call_name
        elif isinstance(item, ToolResultTextDeltaEvent):
            pass  # result text rides on the metadata "hm" pocket at END
        elif isinstance(item, ToolResultEndEvent):
            await self._emit_assistant_events(handle, emit)
            hm = (item.metadata or {}).get("hm") or {}
            data = hm.get("data") if isinstance(hm.get("data"), dict) else {}
            is_error = item.state in _REASON_ERROR_STATES
            await emit(
                "tool.call_failed" if is_error else "tool.call_completed",
                tool_call_id=item.tool_call_id,
                name=handle.tool_call_names.get(item.tool_call_id),
                payload={
                    "is_error": is_error,
                    "result": data.get("text", ""),
                    "data": data,
                    "backend_attempted": hm.get("backend_attempted"),
                    "status": hm.get("status"),
                },
            )
            agent_state.record_tool_results(
                [
                    {
                        "tool_call_id": item.tool_call_id,
                        "name": handle.tool_call_names.get(item.tool_call_id)
                        or "",
                        "is_error": is_error,
                        "text": str(data.get("text", "")),
                    }
                ]
            )
            save_snapshot()
            decision = await self._evaluate_stop(handle)
            if decision is not None:
                await stream.aclose()
                await emit(
                    "runtime.turn_completed"
                    if decision.status in {"replied", "waiting_user"}
                    else "runtime.turn_failed",
                    payload={
                        "error_code": decision.error_code,
                        **decision.payload,
                    },
                )
                save_snapshot(decision.status)
                return GenericRunResult(
                    run_id=handle.run_id,
                    status=decision.status,
                    session=session,
                    events=handle.events,
                    final_reply=decision.final_reply,
                    error_code=decision.error_code,
                )
            fatal = getattr(handle, "observation_fatal", None)
            if fatal is not None:
                await stream.aclose()
                await emit(
                    "runtime.turn_failed",
                    payload={
                        "error": getattr(
                            handle, "observation_fatal_reason", ""
                        )
                        or "model observation protocol failed",
                        "error_code": fatal,
                    },
                )
                save_snapshot("failed")
                return GenericRunResult(
                    run_id=handle.run_id,
                    status="failed",
                    session=session,
                    events=handle.events,
                    error_code=fatal,
                )
            guard = _check_guards(handle)
            if guard is not None:
                await stream.aclose()
                await emit(
                    "runtime.guard_triggered",
                    payload={"guard": guard, "error_code": guard},
                )
                save_snapshot("failed")
                return GenericRunResult(
                    run_id=handle.run_id,
                    status="failed",
                    session=session,
                    events=handle.events,
                    error_code=guard,
                )
        elif isinstance(item, ModelCallStartEvent):
            # HM normal-iteration accounting: observation follow-up turns
            # (pending barrier or unconsumed image marker) do not consume the
            # max_tool_iterations budget — matching the legacy loop condition.
            followup = (
                agent_state.pending_model_observation is not None
                or agent_state.unconsumed_observation_tool_call_id is not None
            )
            if not followup:
                handle.normal_iterations += 1
            if (
                self._max_tool_iterations is not None
                and handle.normal_iterations > self._max_tool_iterations
            ):
                await stream.aclose()
                await emit(
                    "runtime.budget_exhausted",
                    payload={
                        "max_tool_iterations": self._max_tool_iterations,
                        "error_code": "max_tool_iterations_exceeded",
                    },
                )
                save_snapshot("failed")
                return GenericRunResult(
                    run_id=handle.run_id,
                    status="failed",
                    session=session,
                    events=handle.events,
                    error_code="max_tool_iterations_exceeded",
                )
            agent_state.begin_iteration(agent_state.iteration_index + 1)
        elif isinstance(item, ModelCallEndEvent):
            await self._record_usage(
                agent_state,
                {
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "cache_read_input_tokens": item.cache_input_tokens,
                    "cache_creation_input_tokens": (
                        item.cache_creation_input_tokens
                    ),
                },
                emit=emit,
            )
            await self._emit_assistant_events(handle, emit)
        elif isinstance(item, ReplyEndEvent):
            # The assistant Msg lands in the engine context possibly after
            # MODEL_CALL_END — rescan the tail so no round loses its
            # assistant.reply projection.
            await self._emit_assistant_events(handle, emit)
            handle.reply_finished_reason = (
                item.finished_reason.value
                if hasattr(item.finished_reason, "value")
                else item.finished_reason
            )
            handle.reply_error = item.error
        return None

    async def _emit_assistant_events(
        self, handle: AsRunHandle, emit: Callable[..., Any]
    ) -> None:
        """Emit assistant.thinking / assistant.reply for every assistant Msg
        in the engine-context tail past the announced watermark. Ordering is
        event-agnostic: AS appends the Msg around MODEL_CALL_END and both
        sides may race, so we scan at multiple boundaries."""
        context = handle_engine_context(handle)
        announced = getattr(handle, "assistant_watermark", 0)
        pending: list[Any] = []
        assistant_count = 0
        for msg in context:
            if getattr(msg, "role", "") == "assistant":
                assistant_count += 1
                if assistant_count > announced:
                    pending.append(msg)
        if not pending:
            return
        handle.assistant_watermark = assistant_count
        for msg in pending:
            thinking = "".join(
                getattr(block, "thinking", "") or ""
                for block in getattr(msg, "content", []) or []
                if getattr(block, "type", None) == "thinking"
            )
            text = "".join(
                getattr(block, "text", "") or ""
                for block in getattr(msg, "content", []) or []
                if getattr(block, "type", None) == "text"
            )
            tool_calls = []
            for block in getattr(msg, "content", []) or []:
                if getattr(block, "type", None) != "tool_call":
                    continue
                raw = getattr(block, "input", "")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        raw = {"_raw": raw}
                tool_calls.append(
                    {
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "arguments": raw if isinstance(raw, dict) else {},
                    }
                )
            if thinking:
                await emit(
                    "assistant.thinking", payload={"thinking": thinking}
                )
            await emit(
                "assistant.reply",
                payload={
                    "reply": text,
                    "finish_reason": getattr(msg, "finish_reason", None) or "",
                    "usage": {},
                    "tool_calls": tool_calls,
                },
            )

    async def _evaluate_stop(self, handle: AsRunHandle) -> Any:
        if self._stop_condition is None:
            return None
        results = [
            m
            for m in handle.session.messages
            if isinstance(m, ToolResultMessage)
        ]
        decision = self._stop_condition(handle.session, results)
        if inspect.isawaitable(decision):
            decision = await decision
        return decision

    # ------------------------------------------------------------------
    # Shared internals
    # ------------------------------------------------------------------

    def _sync_session(self, session: AgentSession, engine_state: Any) -> None:
        """Mirror the authoritative engine context into the HM session."""
        session.replace_messages(from_agent_scope(list(engine_state.context)))

    async def _fail_observation_fatal(
        self,
        handle: AsRunHandle,
        *,
        session: AgentSession,
        emit: Callable[..., Any],
        stream: Any,
        save_snapshot: Callable[..., Any],
    ) -> GenericRunResult:
        fatal = handle.observation_fatal
        await _close_stream(stream)
        await emit(
            "runtime.turn_failed",
            payload={
                "error": getattr(handle, "observation_fatal_reason", "")
                or "model observation protocol failed",
                "error_code": fatal,
            },
        )
        save_snapshot("failed")
        return GenericRunResult(
            run_id=handle.run_id,
            status="failed",
            session=session,
            events=handle.events,
            error_code=fatal,
        )

    async def _close_dangling_tool_calls(
        self, handle: AsRunHandle, emit: Callable[..., Any]
    ) -> None:
        """Append INTERRUPTED results for tool calls whose result block was
        never persisted — mirrors ``Agent._close_unfinished_tool_calls``.

        A cancel/deadline that lands inside tool execution (or an aclose that
        kills the ``finally`` mid-yield) leaves ALLOWED/PENDING
        ``ToolCallBlock``s without results; an unpaired ``tool_use`` is
        rejected by provider APIs on resume, so close them before the session
        mirror/snapshot is taken."""
        context = list(getattr(handle, "engine_context", []) or [])
        if not context:
            return
        from agentscope.message import (
            ToolCallBlock,
            ToolCallState,
            ToolResultBlock,
            ToolResultState,
        )

        last_msg = context[-1]
        if getattr(last_msg, "role", "") != "assistant":
            return
        dangling: dict[str, Any] = {}
        for block in getattr(last_msg, "content", []) or []:
            if isinstance(block, ToolCallBlock):
                dangling[block.id] = block
            elif isinstance(block, ToolResultBlock):
                dangling.pop(block.id, None)
        if not dangling:
            return
        reminder = (
            "<system-reminder>The tool call has been interrupted by "
            "the user.</system-reminder>"
        )
        for block in dangling.values():
            block.state = ToolCallState.FINISHED
            last_msg.content.append(
                ToolResultBlock(
                    id=block.id,
                    name=block.name,
                    output=reminder,
                    state=ToolResultState.INTERRUPTED,
                    metadata={
                        "hm": {
                            "data": {
                                "backend_attempted": False,
                                "status": "interrupted",
                            }
                        }
                    },
                )
            )
            await emit(
                "tool.call_failed",
                tool_call_id=block.id,
                name=block.name,
                payload={
                    "is_error": True,
                    "result": reminder,
                    "data": {
                        "backend_attempted": False,
                        "status": "interrupted",
                    },
                    "backend_attempted": False,
                    "status": "interrupted",
                },
            )

    async def _cancel_result(
        self,
        session: AgentSession,
        run_id: str,
        events: list[RuntimeEvent],
        *,
        emit: Callable[..., Any],
        phase: str,
        handle: AsRunHandle | None = None,
        persistence: Any = None,
        engine_state: Any = None,
        local_only: bool = False,
    ) -> GenericRunResult:
        if engine_state is not None and handle is not None:
            await self._close_dangling_tool_calls(handle, emit)
            self._sync_session(session, engine_state)
        elif engine_state is not None:
            self._sync_session(session, engine_state)
        await emit(
            "runtime.cancelled",
            payload={"phase": phase},
            local_only=local_only,
        )
        if handle is not None:
            snapshot = getattr(handle.task_state_store, "snapshot", None)
            if snapshot is not None and snapshot.status == TaskStatus.ACTIVE:
                handle.task_state_store.update_status(TaskStatus.PAUSED)
            handle.agent_state.status = "cancelled"
        if persistence is not None:
            persistence.save_snapshot()
        return GenericRunResult(
            run_id=run_id,
            status="cancelled",
            session=session,
            events=events,
            error_code="user_interrupted",
        )

    @staticmethod
    async def _record_usage(
        agent_state: AgentState,
        usage: dict[str, int],
        *,
        emit: Callable[..., Any],
    ) -> None:
        input_tokens = int(usage.get("input_tokens") or 0)
        input_tokens += int(usage.get("cache_read_input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        if not (input_tokens or output_tokens):
            return
        previous = agent_state.provider_usage or ProviderUsage()
        agent_state.provider_usage = ProviderUsage(
            input_tokens=previous.input_tokens + input_tokens,
            output_tokens=previous.output_tokens + output_tokens,
            total_tokens=previous.total_tokens + input_tokens + output_tokens,
        )
        await emit(
            "usage.update",
            payload={
                "input_tokens": agent_state.provider_usage.input_tokens,
                "output_tokens": agent_state.provider_usage.output_tokens,
                "total_tokens": agent_state.provider_usage.total_tokens,
            },
        )

    def _build_persistence_manager(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        engine_state: Any,
        settings: Any,
    ) -> SessionPersistenceManager | None:
        observability = getattr(settings, "observability", None)
        if observability is None:
            return None
        if not (
            bool(getattr(observability, "save_session_per_iteration", True))
            or bool(getattr(observability, "save_on_sigint", True))
        ):
            return None
        manager = SessionPersistenceManager(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state_store,
            session_root=Path(
                str(
                    getattr(
                        observability, "session_dir", "~/.homemaster/sessions"
                    )
                )
            ),
            model=str(getattr(settings, "provider_name", "")),
            system_prompt=self._system_prompt,
            strip_images=bool(
                getattr(observability, "strip_images_in_snapshot", True)
            ),
            trace_rotation_max_mb=int(
                getattr(observability, "trace_rotation_max_mb", 100)
            ),
        )
        manager.engine_state = engine_state
        return manager


async def _close_stream(stream: Any) -> None:
    """Best-effort ``aclose()`` on the reply generator — the deadline path
    cancels the pending ``__anext__`` task, which leaves the generator
    suspended (its ``finally`` cleanup never runs) until we close it."""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except BaseException:
        # GeneratorExit ignores, RuntimeError("ignored GeneratorExit"), or a
        # re-delivery of our own cancellation — all mean "closed enough".
        pass


async def _await_with_deadline(
    awaitable: Any,
    *,
    deadline: Any,
    operation: str,
) -> Any:
    if not inspect.isawaitable(awaitable):
        return awaitable
    remaining = deadline.remaining_s() if deadline is not None else None
    if remaining is None:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    if remaining > 0:
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        if task in done:
            return task.result()
    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    raise TimeoutError(f"{operation} exceeded the run deadline")


def handle_engine_context(handle: AsRunHandle) -> list[Any]:
    """Engine context list — populated by ContextAssemblyMiddleware which
    owns the live ``agent`` reference."""
    return getattr(handle, "engine_context", [])


def _check_guards(handle: AsRunHandle) -> str | None:
    """Mirror ``AgentRuntime._check_loop_guards`` — thresholds from
    ``settings.runtime_guards``."""
    agent_state = handle.agent_state
    guards = getattr(handle.settings, "runtime_guards", None)
    if guards is None:
        return None
    max_errors = getattr(guards, "max_consecutive_tool_errors", 5)
    if max_errors > 0 and agent_state.consecutive_tool_errors >= max_errors:
        return "max_consecutive_tool_errors"
    max_no_progress = getattr(guards, "max_no_progress_iterations", 20)
    if agent_state.no_progress_iterations >= max_no_progress:
        return "max_no_progress_iterations"
    return None


def _resolve_subject(
    run_context: RunContext | None, settings: Any
) -> PermissionSubject:
    subject = None
    if run_context is not None:
        subject = run_context.deps.get("permission_subject")
    if subject is None:
        subject = getattr(settings, "permission_subject", None)
    if isinstance(subject, PermissionSubject):
        return subject
    return PermissionSubject(
        subject_id="runtime",
        channel="internal",
        roles=(),
        tenant_id="runtime",
        capabilities=("tool.auto",),
    )


def _resolve_workdir(run_context: RunContext | None, settings: Any) -> Path:
    workdir = None
    if run_context is not None:
        workdir = run_context.deps.get("working_directory")
    if workdir is None:
        workdir = getattr(settings, "working_directory", None)
    return Path(workdir) if workdir else Path.cwd()


def _tool_schemas(tool_registry: Any) -> list[dict[str, Any]]:
    if tool_registry is None:
        return []
    to_api_schema = getattr(tool_registry, "to_api_schema", None)
    return list(to_api_schema()) if callable(to_api_schema) else []


def _flatten_error(exc: BaseException) -> str:
    """ExceptionGroups hide the real failure behind 'N sub-exceptions' —
    flatten the first leaf so turn_failed carries an actionable error."""
    leaves: list[BaseException] = []
    stack: list[BaseException] = [exc]
    while stack and len(leaves) < 8:
        current = stack.pop()
        children = getattr(current, "exceptions", None)
        if children:
            stack.extend(children)
        else:
            leaves.append(current)
    if not leaves:
        return str(exc)
    return "; ".join(
        f"{type(leaf).__name__}: {leaf}" for leaf in leaves[:3]
    )


def _msg_text(msg: Any) -> str:
    if msg is None:
        return ""
    return "".join(
        getattr(block, "text", "") or ""
        for block in getattr(msg, "content", []) or []
        if getattr(block, "type", None) == "text"
    )


__all__ = ["AsAgentRuntime", "AsRunHandle"]
