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

from homemaster.agent.interrupt import InterruptController
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)
from homemaster.agent.normalized import RunContext
from homemaster.agent.runtime_contracts import (
    GenericRunResult,
    StopCondition,
    _cancelled,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import SessionPersistenceManager
from homemaster.agent.state import AgentState, ProviderUsage
from homemaster.events import FanoutEventSink
from homemaster.events.bus import EventBusClosedError
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
    tool_call_args: dict[str, Any] = field(default_factory=dict)
    reply_finished_reason: str | None = None
    reply_error: Any = None
    tool_registry: Any = None
    engine_context: list[Any] = field(default_factory=list)
    # Live AgentState reference — preferred over `engine_context` because
    # ``state.context`` may be rebound (e.g. native compression); resolve
    # through it fresh via ``handle_engine_context``.
    engine_state: Any = None
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
    # Mirrors the live engine context into the canonical session. Set once
    # per run by AsAgentRuntime.run — ContextAssemblyMiddleware calls it
    # before every prepare/compaction so post-tool and post-observation
    # mutations are visible to the assembler even when persistence is off.
    sync_session: Callable[[], None] | None = None
    # Canonical ToolResultMessages appended since the last model call —
    # stop_condition is evaluated over this batch only (legacy parity:
    # the condition saw the just-dispatched batch, not all history).
    round_result_ids: set[str] = field(default_factory=set)
    # Tool calls whose result event was already projected — results that
    # land inside a closing stream skip ``_project_event`` entirely, so the
    # teardown sweep reconciles them against this set.
    tool_event_emitted_ids: set[str] = field(default_factory=set)
    # (assistant msg id, content length) watermark recorded at each
    # model call — blocks appended past the floor are the current
    # reasoning round's calls; AgentScope merges all rounds into one Msg,
    # so position is the only reliable round boundary.
    round_floor: tuple[str, int] = ("", 0)
    # Protocol-fence denial payloads by call id. The permission chain
    # deep-copies the call block, so the payload rides the handle and is
    # merged into the result's hm.data at ToolResultEndEvent.
    denial_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Model identity for transport.request_* payload parity with the
    # legacy LLMClient events.
    model_name: str = ""
    model_api_format: str = ""
    # Set once the driver has committed to a terminal state (or taken the
    # stream down); detached post-hook workers check it before issuing
    # further real tool calls or mutating persisted blocks.
    terminated: bool = False


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
        model_api_format: str | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._tools = list(tools or [])
        self._extra_middlewares = list(middlewares or [])
        self._max_tool_iterations = max_tool_iterations
        self._stop_condition = stop_condition
        self._context_assembler = context_assembler
        self._provider_attempt_sink_factory = provider_attempt_sink_factory
        self._model_api_format = model_api_format

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
            abort_llm_stream=bool(getattr(observability, "interrupt_abort_llm_stream", True))
        )
        old_sigint_handler: Any = None
        signal_registered = False
        if bool(getattr(observability, "interrupt_enabled", True)):
            try:
                old_sigint_handler = signal.signal(signal.SIGINT, interrupt.handle_sigint)
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
                try:
                    if callable(aemit):
                        await aemit(event)
                    else:
                        value = event_sink.emit(event)
                        if inspect.isawaitable(value):
                            await value
                except EventBusClosedError:
                    # The bus only closes at application ``aclose()``, which
                    # already cancelled the runs — every later emit is teardown.
                    # The local ``events`` record above kept the event.
                    pass

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
                backend=(run_context.deps.get("backend") if run_context else None),
                domain_observer=(run_context.deps.get("domain_observer") if run_context else None),
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
                persistence if event_sink is None else FanoutEventSink([event_sink, persistence])
            )

        def save_snapshot(status: str | None = None) -> None:
            if status is not None:
                agent_state.status = status  # type: ignore[assignment]
            # The canonical session mirror must track the engine context on
            # every boundary — the assembler, stop_condition, and compaction
            # all read the session. Only disk persistence is optional.
            self._sync_session(session, engine_state)
            if persistence is not None:
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
        handle.sync_session = lambda: self._sync_session(session, engine_state)
        handle.model_name = str(getattr(self._model, "model", "") or "")
        handle.model_api_format = self._model_api_format or ""

        middlewares: list[Any] = [RunScopeMiddleware()]
        from homemaster.substrate.middleware_runtime import (
            ContextAssemblyMiddleware,
            ProtocolFenceMiddleware,
            ProviderObservabilityMiddleware,
            ProviderRetryMiddleware,
        )

        middlewares.append(
            ContextAssemblyMiddleware(
                handle=handle,
                assembler=self._context_assembler,
                force_compact=force_compact,
                on_compaction=on_compaction,
            )
        )
        middlewares.append(ProtocolFenceMiddleware(handle=handle))
        middlewares.append(
            ProviderRetryMiddleware(
                handle=handle,
                attempt_sink=attempt_sink,
            )
        )
        middlewares.append(
            ProviderObservabilityMiddleware(
                handle=handle,
                attempt_sink=attempt_sink,
            )
        )
        middlewares.extend(self._extra_middlewares)

        # AS counts every reasoning iteration toward max_iters; give AS the
        # HM budget plus small headroom — the authoritative budget check
        # stays on ``normal_iterations`` in ``_project_event``.
        from agentscope.agent import ReActConfig

        react_config = ReActConfig(
            # ``None`` is HM's "unlimited" — AS has no None sentinel (default
            # would silently clamp at 50). A huge value preserves the
            # contract; the authoritative budget check stays on
            # ``normal_iterations`` in ``_project_event``.
            max_iters=(
                (self._max_tool_iterations + 2)
                if self._max_tool_iterations is not None
                else 2**31 - 1
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
            # Request-owned references must not outlive the run: adapters,
            # the toolkit and any suspended middleware asyncgens all hold
            # ``scope``, and an unclosed asyncgen pins the whole object graph
            # past cyclic GC. The scope object itself may be caller-reused,
            # so only its request-scoped payload is dropped.
            scope.backend = None
            scope.domain_observer = None
            scope.services = {}

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

        async def _next_reply_item() -> tuple[Any, bool]:
            """Yield ``(item, True)`` — or ``(None, False)`` at stream end.

            A StopAsyncIteration stored on a completed Task keeps its
            traceback (and through it every suspended frame and the whole
            run graph) reachable until the event loop drains the task's
            done-callback handles. Returning a value instead lets one
            ``gc.collect()`` free a finished run."""
            try:
                return await stream.__anext__(), True
            except StopAsyncIteration:
                return None, False

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
                task = asyncio.ensure_future(_next_reply_item())
                pending_anext[0] = task
                try:
                    item, more = await _await_with_deadline(
                        task,
                        deadline=deadline,
                        operation="agentscope reply",
                    )
                finally:
                    pending_anext[0] = None
                if not more:
                    break
                if isinstance(item, Msg):
                    final_msg = item
                    continue
                stop = await self._project_event(
                    item,
                    agent=agent,
                    handle=handle,
                    emit=emit,
                    stream=stream,
                    session=session,
                    save_snapshot=save_snapshot,
                )
                if stop is not None:
                    return stop
        except TimeoutError as exc:
            await _close_stream(stream)
            await self._close_dangling_tool_calls(handle, emit)
            self._sync_session(session, engine_state)
            propagatable = _find_propagatable(exc, self._propagate_exceptions)
            if propagatable is not None:
                save_snapshot("failed")
                raise propagatable from None
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
            propagatable = _find_propagatable(exc, self._propagate_exceptions)
            if propagatable is not None:
                save_snapshot("failed")
                raise propagatable from None
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
            # Post-loop: every exit path (terminal, exception, cancel) means
            # detached post-hook workers must stop issuing real calls.
            handle.terminated = True
            # A never-aclosed reply asyncgen stays in ``loop._asyncgens`` and
            # anchors the whole run graph (frames → tasks → agent → scope),
            # leaking request-owned objects past cyclic GC. Error paths close
            # it inline; normal completion must close it here.
            await _close_stream(stream)
            # The last ``__anext__`` task keeps its exception (e.g.
            # StopAsyncIteration) whose traceback holds this frame — and this
            # frame holds the task: a self-referential cycle that survives a
            # single gc.collect(). Break the frame→task edge.
            pending_anext[0] = None
            task = None

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
                error_text = str(getattr(handle.reply_error, "message", handle.reply_error))
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
        if reason == ReplyFinishedReason.EXCEED_MAX_ITERS or reason == "exceed_max_iters":
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
        # Legacy parity: finish_reason "length" is a failed turn, not a
        # normal reply (generic_runtime emits model_output_truncated).
        if getattr(final_msg, "finish_reason", None) == "length":
            reply_text = _msg_text(final_msg)
            handle.agent_state.last_assistant_text = reply_text
            await emit(
                "runtime.turn_failed",
                payload={
                    "error": "model output truncated",
                    "error_code": "model_output_truncated",
                },
            )
            save_snapshot("failed")
            return GenericRunResult(
                run_id=run_id,
                status="failed",
                session=session,
                events=events,
                final_reply=reply_text,
                error_code="model_output_truncated",
            )

        reply_text = _msg_text(final_msg)
        handle.agent_state.last_assistant_text = reply_text
        save_snapshot("replied")
        # Legacy parity: assistant replies append to messages.jsonl (the
        # user-facing dialogue log), same as generic_runtime.
        if persistence is not None and reply_text:
            persistence.append_message(AssistantMessage(content=[ContentBlock(text=reply_text)]))
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
        agent: Any,
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
            await emit("transport.delta", payload={"reasoning_delta": item.delta})
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
            handle.tool_call_args[item.tool_call_id] = arguments
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
            from homemaster.substrate.middleware_runtime import _find_result_block

            await self._emit_assistant_events(handle, emit)
            hm = (item.metadata or {}).get("hm") or {}
            data = hm.get("data") if isinstance(hm.get("data"), dict) else {}
            # Protocol-fence denials carry their payload via the handle (the
            # permission chain deep-copies the call block) — merge it into
            # the persisted result block's hm.data so the canonical session
            # mirror sees status="protocol_blocked", not bare "denied".
            denial = handle.denial_payloads.pop(item.tool_call_id, None)
            result_block = _find_result_block(agent, item.tool_call_id)
            if denial is not None and item.state == "denied":
                merged = dict(data)
                merged.update(denial)
                data = merged
                if result_block is not None:
                    block_meta = dict(getattr(result_block, "metadata", {}) or {})
                    block_hm = dict(block_meta.get("hm") or {})
                    block_data = dict(block_hm.get("data") or {})
                    block_data.update(denial)
                    block_hm["data"] = block_data
                    block_meta["hm"] = block_hm
                    result_block.metadata = block_meta
            # Legacy parity: protocol-fence denials (terminal allowlist /
            # unknown-tool batch rejects) complete as protocol results —
            # ``call_completed`` + status "protocol_blocked", never error
            # state and never counted by the error/no-progress guards.
            protocol_blocked = (
                item.state == "denied" and str(data.get("status", "")) == "protocol_blocked"
            )
            is_error = item.state in _REASON_ERROR_STATES and not protocol_blocked
            # Legacy parity: ``result`` is the model-visible text of the
            # persisted ToolResultBlock — ``hm.data`` deliberately stays
            # the canonical machine pocket.
            block_output = getattr(result_block, "output", None)
            if isinstance(block_output, str):
                result_text = block_output
            else:
                result_text = "\n".join(
                    str(getattr(block, "text", "") or "")
                    for block in (block_output or [])
                    if getattr(block, "type", None) == "text"
                    and getattr(block, "text", None)
                )
            await emit(
                "tool.call_failed" if is_error else "tool.call_completed",
                tool_call_id=item.tool_call_id,
                name=(
                    handle.tool_call_names.get(item.tool_call_id)
                    or getattr(result_block, "name", None)
                ),
                payload={
                    "is_error": is_error,
                    "args": handle.tool_call_args.get(item.tool_call_id, {}),
                    "result": result_text,
                    "data": data,
                    "backend_attempted": hm.get("backend_attempted"),
                    "status": hm.get("status"),
                },
            )
            handle.round_result_ids.add(item.tool_call_id)
            handle.tool_event_emitted_ids.add(item.tool_call_id)
            agent_state.record_tool_results(
                [
                    {
                        "tool_call_id": item.tool_call_id,
                        "name": handle.tool_call_names.get(item.tool_call_id) or "",
                        "is_error": is_error,
                        "text": result_text,
                    }
                ]
            )
            save_snapshot()
            # Stop/guards only evaluate a *complete* current round — legacy
            # dispatched the whole batch, then evaluated once.
            if _unfinished_tool_calls(agent):
                return None
            decision = await self._evaluate_stop(handle)
            if decision is not None:
                await self._close_dangling_tool_calls(handle, emit)
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
            guard = _check_guards(handle)
            if guard is not None:
                await self._close_dangling_tool_calls(handle, emit)
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
            handle.normal_iterations += 1
            # New reasoning round — stop_condition sees only results
            # produced since this point (legacy batch semantics).
            handle.round_result_ids.clear()
            # Watermark the merged assistant Msg: blocks appended after this
            # point belong to the round that this model call produces.
            _ctx = handle_engine_context(handle)
            _last = _ctx[-1] if _ctx else None
            if _last is not None and getattr(_last, "role", "") == "assistant":
                handle.round_floor = (
                    str(getattr(_last, "id", "")),
                    len(getattr(_last, "content", []) or []),
                )
            else:
                handle.round_floor = ("", 0)
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
                    "cache_creation_input_tokens": (item.cache_creation_input_tokens),
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

    async def _emit_assistant_events(self, handle: AsRunHandle, emit: Callable[..., Any]) -> None:
        """Emit assistant.thinking / assistant.reply per reasoning round.

        AgentScope merges all rounds of a reply into ONE Msg (``reply_id``),
        so a message-count watermark can never see rounds 2+. Watermark by
        (msg id, content length) instead and project only the blocks appended
        since the last announcement — one assistant.reply per reasoning
        round, matching legacy's per-iteration emission. Ordering is
        event-agnostic: we rescan at MODEL_CALL_END / TOOL_RESULT_END /
        REPLY_END boundaries."""
        context = handle_engine_context(handle)
        announced_id, announced_len = getattr(handle, "assistant_watermark", ("", 0))
        # Only the tail assistant Msg can grow in place; earlier assistant
        # Msgs are complete rounds already announced (or pre-existing).
        tail = next(
            (m for m in reversed(context) if getattr(m, "role", "") == "assistant"),
            None,
        )
        if tail is None:
            return
        if tail.id != announced_id:
            announced_len = 0
        blocks = list(getattr(tail, "content", []) or [])
        if len(blocks) <= announced_len:
            return
        new_blocks = blocks[announced_len:]
        handle.assistant_watermark = (tail.id, len(blocks))

        thinking = "".join(
            getattr(block, "thinking", "") or ""
            for block in new_blocks
            if getattr(block, "type", None) == "thinking"
        )
        text = "".join(
            getattr(block, "text", "") or ""
            for block in new_blocks
            if getattr(block, "type", None) == "text"
        )
        tool_calls = []
        for block in new_blocks:
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
            await emit("assistant.thinking", payload={"thinking": thinking})
        if text or tool_calls:
            await emit(
                "assistant.reply",
                payload={
                    "reply": text,
                    "finish_reason": getattr(tail, "finish_reason", None) or "",
                    "usage": {},
                    "tool_calls": tool_calls,
                },
            )

    async def _evaluate_stop(self, handle: AsRunHandle) -> Any:
        if self._stop_condition is None:
            return None
        # Current-round scope only — a persisted waiting_user marker (or any
        # historical result) must not retrigger on later rounds or resumed
        # runs; legacy evaluated the just-dispatched batch.
        results = [
            m
            for m in handle.session.messages
            if isinstance(m, ToolResultMessage) and m.tool_call_id in handle.round_result_ids
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
        context = list(handle_engine_context(handle) or [])
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
        # Results persisted inside a closing stream never reached
        # ``_project_event`` — reconcile them so teardown produces the same
        # tool.call_* events a live projection would have.
        for block in getattr(last_msg, "content", []) or []:
            if not isinstance(block, ToolResultBlock):
                continue
            if block.id in handle.tool_event_emitted_ids:
                continue
            handle.tool_event_emitted_ids.add(block.id)
            hm = dict((getattr(block, "metadata", None) or {}).get("hm") or {})
            block_data = dict(hm.get("data") or {})
            is_error = block.state in {
                ToolResultState.ERROR,
                ToolResultState.DENIED,
                ToolResultState.INTERRUPTED,
            }
            output = block.output
            result_text = ""
            if isinstance(output, str):
                result_text = output
            elif isinstance(output, list):
                result_text = "".join(getattr(b, "text", "") or "" for b in output)
            payload_data = {"text": result_text, **block_data}
            await emit(
                "tool.call_failed" if is_error else "tool.call_completed",
                tool_call_id=block.id,
                name=handle.tool_call_names.get(block.id) or getattr(block, "name", None),
                payload={
                    "is_error": is_error,
                    "result": result_text,
                    "data": payload_data,
                    "backend_attempted": hm.get("backend_attempted"),
                    "status": hm.get("status"),
                },
            )
        if not dangling:
            return
        reminder = (
            "<system-reminder>The tool call has been interrupted by the user.</system-reminder>"
        )
        existing_result_ids = {
            b.id for b in getattr(last_msg, "content", []) or [] if isinstance(b, ToolResultBlock)
        }
        for block in dangling.values():
            # A still-alive concurrent worker can land its own interrupted
            # result between the scan above and this append (the emit awaits
            # below are yield points) — re-check per call so a tool_use never
            # ends up with two tool_result blocks.
            if block.id in existing_result_ids:
                continue
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
            existing_result_ids.add(block.id)
            handle.tool_event_emitted_ids.add(block.id)

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
        # The session generation may already have been bumped by the cancel
        # itself (``SessionRuntime._cancel_locked``) — fenced session/snapshot
        # writes then refuse with the propagate-listed generation error by
        # design. That must not abort the cancel teardown or swallow the
        # already-emitted events: the run is terminal either way.
        try:
            if engine_state is not None and handle is not None:
                await self._close_dangling_tool_calls(handle, emit)
                self._sync_session(session, engine_state)
            elif engine_state is not None:
                self._sync_session(session, engine_state)
        except BaseException as exc:
            if _find_propagatable(exc, self._propagate_exceptions) is None:
                raise
        try:
            await emit(
                "runtime.cancelled",
                payload={"phase": phase},
                local_only=local_only,
            )
        except BaseException as exc:
            # The fanned-out persistence sink can hit the same generation
            # fence — the local events list already recorded the event.
            if _find_propagatable(exc, self._propagate_exceptions) is None:
                raise
        if handle is not None:
            snapshot = getattr(handle.task_state_store, "snapshot", None)
            if snapshot is not None and snapshot.status == TaskStatus.ACTIVE:
                handle.task_state_store.update_status(TaskStatus.PAUSED)
            handle.agent_state.status = "cancelled"
        if persistence is not None:
            try:
                persistence.save_snapshot()
            except BaseException as exc:
                if _find_propagatable(exc, self._propagate_exceptions) is None:
                    raise
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
            session_root=Path(str(getattr(observability, "session_dir", "~/.homemaster/sessions"))),
            model=str(getattr(settings, "provider_name", "")),
            system_prompt=self._system_prompt,
            strip_images=bool(getattr(observability, "strip_images_in_snapshot", True)),
            trace_rotation_max_mb=int(getattr(observability, "trace_rotation_max_mb", 100)),
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
    try:
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
    finally:
        # A completed task keeps its exception; that exception's traceback
        # references this frame, which references the task (directly and via
        # the ``done`` set) — a cycle a single gc.collect() cannot break.
        task = None
        done = None


def handle_engine_context(handle: AsRunHandle) -> list[Any]:
    """Live engine context — resolved fresh through ``engine_state`` so a
    rebound ``state.context`` (e.g. native compression) can never leave the
    cached list stale. ``engine_context`` remains the fallback for tests that
    only seed the list."""
    state = getattr(handle, "engine_state", None)
    context = getattr(state, "context", None)
    if context is not None:
        return context
    return getattr(handle, "engine_context", [])


def _unfinished_tool_calls(agent: Any) -> list[Any]:
    """Current-round tool calls that still lack a result block — the live
    in-flight set. AgentScope merges all rounds of a reply into one Msg, so
    ``get_unfinished_tool_calls`` is the only reliable current-round view."""
    state = getattr(agent, "state", None)
    get_unfinished = getattr(state, "get_unfinished_tool_calls", None)
    if callable(get_unfinished):
        return list(get_unfinished(getattr(agent, "name", "")))
    return []


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


def _resolve_subject(run_context: RunContext | None, settings: Any) -> PermissionSubject:
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


def _find_propagatable(
    exc: BaseException,
    types: tuple[type[BaseException], ...],
) -> BaseException | None:
    """Find a propagate-listed member inside (possibly) an ExceptionGroup.

    ``_execute_concurrent_tool_calls`` collects worker exceptions via
    ``gather(return_exceptions=True)`` and re-raises them as an
    ``ExceptionGroup`` — a ``_hm_propagate`` exception raised inside a tool
    body (nearly all tools run on this path; ``concurrency_policy`` defaults
    to ``parallel``) would otherwise land here misclassified as
    ``transport_error``, defeating session-generation fencing and
    recall-deadline propagation. Re-raise the marked member raw so the
    caller sees the same exception the driver path produces.
    """
    if isinstance(exc, types):
        return exc
    children = getattr(exc, "exceptions", None)
    if children:
        for member in children:
            found = _find_propagatable(member, types)
            if found is not None:
                return found
    return None


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
    return "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves[:3])


def _msg_text(msg: Any) -> str:
    if msg is None:
        return ""
    return "".join(
        getattr(block, "text", "") or ""
        for block in getattr(msg, "content", []) or []
        if getattr(block, "type", None) == "text"
    )


__all__ = ["AsAgentRuntime", "AsRunHandle"]
