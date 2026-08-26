"""Canonical AgentRuntime model loop."""

from __future__ import annotations

import asyncio
import inspect
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homemaster.agent.context import ContextMetrics
from homemaster.agent.context_projection import (
    project_model_tool_schemas,
    terminal_command_protocol_results,
    unavailable_tool_protocol_results,
)
from homemaster.agent.interrupt import InterruptController
from homemaster.agent.messages import (
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)
from homemaster.agent.model_observation import (
    MAX_OBSERVE_FAILURES,
    MAX_PROTOCOL_FAILURES,
    action_requires_model_observation,
    append_model_observation_prompt,
    attach_automatic_observation,
    automatic_observation_call,
    model_selectable_tool_schemas,
    observation_batch_error_results,
    observation_tool_name,
    observation_tool_schema,
    validate_observation_result,
)
from homemaster.agent.normalized import RunContext
from homemaster.agent.runtime_contracts import RuntimeStopDecision
from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import SessionPersistenceManager
from homemaster.agent.state import AgentState, ProviderUsage
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sinks import FanoutEventSink
from homemaster.providers.attempts import (
    AttemptCommitState,
    ProviderAttemptRecord,
)
from homemaster.providers.errors import LLMClientError
from homemaster.providers.transports.types import aggregate_deltas
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore

if TYPE_CHECKING:
    from homemaster.agent.context import ContextAssembler
    from homemaster.tools.base import ToolRegistry

_CONTEXT_LENGTH_KEYWORDS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "context window",
    "exceeds the available context size",
)
_PROVIDER_MAX_ATTEMPTS = 8
_PROVIDER_RETRY_BASE_DELAY_S = 3.0
_CANCEL_JOIN_GRACE_S = 1.0


def _is_context_length_error(error_msg: str) -> bool:
    lowered = error_msg.lower()
    return any(keyword in lowered for keyword in _CONTEXT_LENGTH_KEYWORDS)


@dataclass
class GenericRunResult:
    """Result of an AgentRuntime.run() execution."""

    run_id: str
    status: str
    session: AgentSession
    events: list[RuntimeEvent]
    final_reply: str = ""
    error_code: str | None = None


StopCondition = Callable[
    [AgentSession, list[ToolResultMessage]],
    RuntimeStopDecision | None | Awaitable[RuntimeStopDecision | None],
]


class AgentRuntime:
    """Generic message/tool-call/tool-result agent loop."""

    def __init__(
        self,
        *,
        transport: Any,
        tool_executor: Any,
        max_tool_iterations: int | None = 12,
        stop_condition: StopCondition | None = None,
        context_assembler: ContextAssembler | None = None,
        system_prompt: str = "",
        provider_attempt_sink_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._transport = transport
        self._tool_executor = tool_executor
        self._max_tool_iterations = max_tool_iterations
        self._stop_condition = stop_condition
        self._context_assembler = context_assembler
        self._system_prompt = system_prompt
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
        tool_registry: ToolRegistry | None = None,
        cancellation_token: Any = None,
        deadline: Any = None,
        on_compaction: Callable[[ContextMetrics], Any] | None = None,
    ) -> GenericRunResult:
        """Execute one agent run: user input -> model -> tool loop -> final reply."""

        run_id = run_id or uuid.uuid4().hex[:12]
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

        persistence = self._build_persistence_manager(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state_store,
            settings=settings,
            system_prompt=self._system_prompt,
        )
        if persistence is not None:
            persistence.append_message(session.messages[-1])
            event_sink = (
                persistence if event_sink is None else FanoutEventSink([event_sink, persistence])
            )

        def save_snapshot(status: str | None = None) -> None:
            if persistence is None:
                return
            if status is not None:
                agent_state.status = status  # type: ignore[assignment]
            persistence.save_snapshot()

        await emit(
            "runtime.turn_started",
            payload={
                "user_text": user_text,
                "content_block_types": [block.type for block in initial_content],
            },
        )

        if tool_registry is not None:
            all_tool_schemas = list(tool_registry.to_api_schema())
            tool_schemas = model_selectable_tool_schemas(all_tool_schemas, tool_registry)
        else:
            all_tool_schemas = []
            tool_schemas = []

        iteration = 0
        normal_iterations = 0
        reactive_compact_retries = 0
        pending_compaction = force_compact
        try:
            while (
                self._max_tool_iterations is None
                or normal_iterations < self._max_tool_iterations
                or agent_state.pending_model_observation is not None
                or agent_state.unconsumed_observation_tool_call_id is not None
            ):
                observation_followup = (
                    agent_state.pending_model_observation is not None
                    or agent_state.unconsumed_observation_tool_call_id is not None
                )
                normal_budget_available = (
                    self._max_tool_iterations is None
                    or normal_iterations < self._max_tool_iterations
                )
                if _cancelled(interrupt, cancellation_token):
                    return await self._cancel_result(
                        session,
                        run_id,
                        events,
                        emit=emit,
                        phase="iteration_boundary",
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        persistence=persistence,
                    )

                agent_state.begin_iteration(iteration)
                model_started = time.perf_counter()
                context_messages = session.messages
                context_system_prompt = self._system_prompt
                context_tools = tool_schemas if tool_schemas else None
                if self._context_assembler is not None:
                    aprepare = getattr(self._context_assembler, "aprepare", None)
                    if callable(aprepare):
                        composed = await _await_with_deadline(
                            aprepare(
                                session=session,
                                agent_state=agent_state,
                                task_state_store=task_state_store,
                                tools=context_tools,
                                force_compact=pending_compaction,
                            ),
                            deadline=deadline,
                            operation="context preparation",
                        )
                    else:
                        composed = self._context_assembler.prepare(
                            session=session,
                            agent_state=agent_state,
                            task_state_store=task_state_store,
                            tools=context_tools,
                            force_compact=pending_compaction,
                        )
                    pending_compaction = None
                    context_messages = composed.messages
                    context_system_prompt = composed.system_prompt
                    context_tools = composed.tools
                    if composed.metrics.compaction_triggered:
                        compaction_trigger = (
                            "manual"
                            if composed.metrics.compaction_kind.startswith("manual")
                            else (
                                "reactive"
                                if composed.metrics.compaction_kind in {"reactive", "emergency"}
                                else "auto"
                            )
                        )
                        await emit(
                            "context.compaction",
                            payload={
                                "trigger": compaction_trigger,
                                "after_tokens": composed.metrics.estimated_tokens,
                                "kind": composed.metrics.compaction_kind,
                            },
                        )
                        if on_compaction is not None:
                            callback_result = on_compaction(composed.metrics)
                            if inspect.isawaitable(callback_result):
                                await callback_result
                        save_snapshot()

                context_tools = project_model_tool_schemas(
                    context_tools,
                    messages=context_messages,
                )
                if agent_state.pending_model_observation is not None:
                    context_tools = observation_tool_schema(all_tool_schemas)
                    context_system_prompt = append_model_observation_prompt(
                        context_system_prompt,
                        tool_name=observation_tool_name(all_tool_schemas),
                    )

                # Freeze provider inputs once so retry cannot recapture or rebuild media.
                frozen_messages = [message.model_copy(deep=True) for message in context_messages]
                frozen_tools = deepcopy(context_tools)
                frozen_system_prompt = context_system_prompt

                try:
                    attempt_index = 0
                    first_request_sha256: str | None = None
                    successful_attempt: ProviderAttemptRecord | None = None
                    attempt_commit_state = AttemptCommitState(
                        assistant_committed=False,
                        tool_dispatch_committed=False,
                        external_action_committed=False,
                    )
                    while True:
                        base_attempt_id = f"{run_id}:attempt-{iteration + 1:04d}"
                        model_attempt_id = (
                            base_attempt_id
                            if attempt_index == 0
                            else f"{base_attempt_id}:retry-{attempt_index:02d}"
                        )
                        attempt_sink = None
                        stream_kwargs = {
                            "tools": frozen_tools,
                            "system_prompt": frozen_system_prompt,
                            "event_sink": event_sink,
                            "run_id": run_id,
                            "session_id": session.session_id,
                            "turn_index": 0,
                            "iteration": iteration,
                        }
                        if (
                            self._provider_attempt_sink_factory is not None
                            and _accepts_attempt_sink(self._transport.stream)
                        ):
                            attempt_sink = self._provider_attempt_sink_factory()
                            stream_kwargs.update(
                                {
                                    "attempt_sink": attempt_sink,
                                    "model_attempt_id": model_attempt_id,
                                }
                            )
                        if _accepts_stream_parameter(
                            self._transport.stream,
                            "provider_key_index",
                        ):
                            stream_kwargs["provider_key_index"] = attempt_index

                        deltas = []
                        try:
                            stream = self._transport.stream(
                                [message.model_copy(deep=True) for message in frozen_messages],
                                **{**stream_kwargs, "tools": deepcopy(frozen_tools)},
                            )
                            interrupt.set_stream(stream)
                            try:
                                await _consume_stream(
                                    stream,
                                    deltas,
                                    interrupt=interrupt,
                                    cancellation_token=cancellation_token,
                                    deadline=deadline,
                                    on_delta=partial(
                                        self._publish_text_delta,
                                        emit=emit,
                                    ),
                                )
                            finally:
                                interrupt.clear_stream()
                                await _close_stream(stream, deadline=deadline)
                        except Exception as exc:
                            failed_attempt = _last_provider_attempt(attempt_sink)
                            if (
                                attempt_index < _PROVIDER_MAX_ATTEMPTS - 1
                                and _provider_retry_allowed(
                                    error=exc,
                                    deltas=deltas,
                                    commit_state=attempt_commit_state,
                                    attempt=failed_attempt,
                                )
                            ):
                                assert failed_attempt is not None
                                if first_request_sha256 is None:
                                    first_request_sha256 = failed_attempt.request_sha256
                                delay_s = _provider_retry_delay(attempt_index)
                                attempt_index += 1
                                await emit(
                                    "transport.request_retrying",
                                    payload={
                                        "attempt": attempt_index + 1,
                                        "max_attempts": _PROVIDER_MAX_ATTEMPTS,
                                        "delay_seconds": delay_s,
                                        "cause_code": failed_attempt.cause_code,
                                        "first_model_attempt_id": (failed_attempt.model_attempt_id),
                                    },
                                )
                                await _sleep_for_provider_retry(delay_s, deadline=deadline)
                                continue
                            raise

                        if _cancelled(interrupt, cancellation_token):
                            return await self._cancel_result(
                                session,
                                run_id,
                                events,
                                emit=emit,
                                phase="llm_call",
                                agent_state=agent_state,
                                task_state_store=task_state_store,
                                persistence=persistence,
                            )
                        successful_attempt = _last_provider_attempt(attempt_sink)
                        if first_request_sha256 is not None and (
                            successful_attempt is None
                            or successful_attempt.request_sha256 != first_request_sha256
                        ):
                            raise RuntimeError("provider retry changed the frozen request body")
                        assistant_msg = aggregate_deltas(deltas)
                        _bind_provider_attempt_contexts(
                            assistant_msg.tool_calls,
                            frozen_messages,
                            run_context,
                        )
                        break
                except Exception as exc:
                    if (
                        self._context_assembler is not None
                        and not deltas
                        and _is_context_length_error(str(exc))
                    ):
                        max_retries = self._reactive_compact_max_retries(settings)
                        if reactive_compact_retries >= max_retries:
                            await emit(
                                "runtime.turn_failed",
                                payload={
                                    "error": str(exc),
                                    "error_code": "context_length_exceeded_after_compact",
                                },
                            )
                            save_snapshot("failed")
                            return GenericRunResult(
                                run_id=run_id,
                                status="failed",
                                session=session,
                                events=events,
                                error_code="context_length_exceeded_after_compact",
                            )
                        await emit(
                            "runtime.reactive_compact_started",
                            payload={"reason": str(exc)[:300]},
                        )
                        reactive_compact_retries += 1
                        pending_compaction = "aggressive"
                        continue
                    error_code = (
                        "deadline_exceeded" if isinstance(exc, TimeoutError) else "transport_error"
                    )
                    await emit(
                        "transport.request_failed",
                        payload={"error": str(exc), "error_type": type(exc).__name__},
                    )
                    await emit(
                        "runtime.turn_failed",
                        payload={"error": str(exc), "error_code": error_code},
                    )
                    save_snapshot("failed")
                    return GenericRunResult(
                        run_id=run_id,
                        status="failed",
                        session=session,
                        events=events,
                        error_code=error_code,
                    )

                model_elapsed_ms = round((time.perf_counter() - model_started) * 1000, 1)
                if not observation_followup:
                    normal_iterations += 1
                session.append(assistant_msg)
                consumed_observation_call_id = agent_state.unconsumed_observation_tool_call_id
                if consumed_observation_call_id is not None:
                    agent_state.unconsumed_observation_tool_call_id = None
                    await emit(
                        "model_observation.image_consumed",
                        tool_call_id=consumed_observation_call_id,
                        name=observation_tool_name(all_tool_schemas),
                        payload={"tool_call_id": consumed_observation_call_id},
                    )
                attempt_commit_state = AttemptCommitState(
                    assistant_committed=True,
                    tool_dispatch_committed=False,
                    external_action_committed=False,
                )
                if assistant_msg.reasoning_content:
                    await emit(
                        "assistant.thinking",
                        payload={"thinking": assistant_msg.reasoning_content},
                    )
                await emit(
                    "assistant.reply",
                    payload={
                        "reply": assistant_msg.text,
                        "finish_reason": assistant_msg.finish_reason,
                        "usage": assistant_msg.usage or {},
                        "tool_calls": [
                            tool_call.model_dump(mode="json")
                            for tool_call in assistant_msg.tool_calls
                        ],
                    },
                )
                if assistant_msg.text:
                    if persistence is not None:
                        persistence.append_message(assistant_msg)
                reactive_compact_retries = 0

                agent_state.last_assistant_text = assistant_msg.text
                if assistant_msg.usage:
                    await self._record_usage(agent_state, assistant_msg.usage, emit=emit)

                if assistant_msg.finish_reason == "length":
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
                        final_reply=assistant_msg.text,
                        error_code="model_output_truncated",
                    )

                barrier = agent_state.pending_model_observation
                if barrier is not None and (
                    len(assistant_msg.tool_calls) != 1
                    or assistant_msg.tool_calls[0].name != barrier.observe_tool_name
                ):
                    barrier.protocol_failures += 1
                    await emit(
                        "model_observation.protocol_rejected",
                        tool_call_id=barrier.source_tool_call_id,
                        name=barrier.source_tool_name,
                        payload={
                            "protocol_failures": barrier.protocol_failures,
                            "source_tool_call_id": barrier.source_tool_call_id,
                        },
                    )
                    if assistant_msg.tool_calls:
                        rejected = observation_batch_error_results(
                            assistant_msg.tool_calls,
                            code="model_observation_protocol_rejected",
                            message=(
                                "A pending environment action must be followed by one observe call."
                            ),
                        )
                        for result in rejected:
                            session.append(result)
                        await self._publish_tool_results(
                            assistant_msg.tool_calls,
                            rejected,
                            dispatch_ms=0.0,
                            emit=emit,
                        )
                    save_snapshot()
                    if barrier.protocol_failures >= MAX_PROTOCOL_FAILURES:
                        await emit(
                            "runtime.turn_failed",
                            payload={
                                "error": "model observation protocol retry limit reached",
                                "error_code": "model_observation_protocol_failed",
                            },
                        )
                        save_snapshot("failed")
                        return GenericRunResult(
                            run_id=run_id,
                            status="failed",
                            session=session,
                            events=events,
                            error_code="model_observation_protocol_failed",
                        )
                    iteration += 1
                    continue

                if not assistant_msg.tool_calls:
                    await emit(
                        "runtime.turn_completed",
                        payload={
                            "final_reply": assistant_msg.text,
                            "duration_ms": model_elapsed_ms,
                        },
                    )
                    save_snapshot("replied")
                    return GenericRunResult(
                        run_id=run_id,
                        status="replied",
                        session=session,
                        events=events,
                        final_reply=assistant_msg.text,
                    )

                tool_calls = assistant_msg.tool_calls
                observation_actions = [
                    call
                    for call in tool_calls
                    if action_requires_model_observation(tool_registry, call.name)
                ]
                rejection_code: str | None = None
                rejection_message = ""
                if observation_actions and len(tool_calls) != 1:
                    rejection_code = "model_observation_batch_rejected"
                    rejection_message = (
                        "A state-changing environment action must be the only call in its batch."
                    )
                elif observation_followup and observation_actions:
                    if not normal_budget_available:
                        rejection_code = "model_observation_budget_exhausted"
                        rejection_message = (
                            "The normal tool-iteration budget cannot start another "
                            "environment action."
                        )
                    else:
                        normal_iterations += 1
                if rejection_code is not None:
                    rejected = observation_batch_error_results(
                        tool_calls,
                        code=rejection_code,
                        message=rejection_message,
                    )
                    for result in rejected:
                        session.append(result)
                    agent_state.record_tool_results(
                        [
                            {
                                "tool_call_id": result.tool_call_id,
                                "name": result.name,
                                "is_error": True,
                                "text": rejection_message,
                            }
                            for result in rejected
                        ]
                    )
                    await self._publish_tool_results(
                        tool_calls,
                        rejected,
                        dispatch_ms=0.0,
                        emit=emit,
                    )
                    save_snapshot()
                    iteration += 1
                    continue
                unavailable_rejected = unavailable_tool_protocol_results(
                    tool_calls,
                    tools=frozen_tools,
                )
                if unavailable_rejected is not None:
                    for result in unavailable_rejected:
                        session.append(result)
                    agent_state.record_tool_results(
                        [
                            {
                                "tool_call_id": result.tool_call_id,
                                "name": result.name,
                                "is_error": False,
                                "text": "\n".join(
                                    block.text for block in result.content if block.text
                                )[:500],
                            }
                            for result in unavailable_rejected
                        ]
                    )
                    for result in unavailable_rejected:
                        await emit(
                            "tool.protocol_rejected",
                            tool_call_id=result.tool_call_id,
                            name=result.name,
                            payload={
                                "error_code": (result.data or {}).get("error_code"),
                                "backend_attempted": False,
                                "unavailable_tools": (result.data or {}).get(
                                    "unavailable_tools", []
                                ),
                            },
                        )
                    await self._publish_tool_results(
                        tool_calls,
                        unavailable_rejected,
                        dispatch_ms=0.0,
                        emit=emit,
                    )
                    save_snapshot()
                    iteration += 1
                    continue
                permission_settings = getattr(settings, "permissions", None)
                terminal_rejected = terminal_command_protocol_results(
                    tool_calls,
                    allowed_commands=getattr(
                        permission_settings,
                        "allowed_terminal_commands",
                        (),
                    ),
                )
                if terminal_rejected is not None:
                    for result in terminal_rejected:
                        session.append(result)
                    agent_state.record_tool_results(
                        [
                            {
                                "tool_call_id": result.tool_call_id,
                                "name": result.name,
                                "is_error": False,
                                "text": "\n".join(
                                    block.text for block in result.content if block.text
                                )[:500],
                            }
                            for result in terminal_rejected
                        ]
                    )
                    for result in terminal_rejected:
                        await emit(
                            "terminal.command_protocol_rejected",
                            tool_call_id=result.tool_call_id,
                            name=result.name,
                            payload={
                                "error_code": (result.data or {}).get("error_code"),
                                "backend_attempted": False,
                            },
                        )
                    await self._publish_tool_results(
                        tool_calls,
                        terminal_rejected,
                        dispatch_ms=0.0,
                        emit=emit,
                    )
                    save_snapshot()
                    iteration += 1
                    continue
                for tool_call in tool_calls:
                    await emit(
                        "tool.call_started",
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        payload={"arguments": tool_call.arguments},
                    )

                dispatch_started = time.perf_counter()
                tool_results = await self._dispatch_tools(
                    tool_calls,
                    session,
                    run_id,
                    event_sink,
                    settings,
                    events,
                    run_context=run_context,
                    cancellation_token=interrupt,
                )
                dispatch_ms = round((time.perf_counter() - dispatch_started) * 1000, 1)

                expected_ids = {tool_call.id for tool_call in tool_calls}
                actual_ids = {result.tool_call_id for result in tool_results}
                if expected_ids != actual_ids:
                    missing = expected_ids - actual_ids
                    await emit(
                        "runtime.turn_failed",
                        payload={
                            "error": f"tool result ID mismatch: missing {missing}",
                            "error_code": "tool_result_id_mismatch",
                        },
                    )
                    save_snapshot("failed")
                    return GenericRunResult(
                        run_id=run_id,
                        status="failed",
                        session=session,
                        events=events,
                        error_code="tool_result_id_mismatch",
                    )

                if _cancelled(interrupt, cancellation_token):
                    await self._publish_tool_results(
                        tool_calls,
                        tool_results,
                        dispatch_ms=dispatch_ms,
                        emit=emit,
                        local_only=True,
                    )
                    return await self._cancel_result(
                        session,
                        run_id,
                        events,
                        emit=emit,
                        phase="tool_execution",
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        persistence=persistence,
                        local_only=True,
                    )

                automatic_observation_error: str | None = None
                if barrier is None and observation_actions:
                    action_call = observation_actions[0]
                    action_result = next(
                        result for result in tool_results if result.tool_call_id == action_call.id
                    )
                    action_data = action_result.data or {}
                    if action_data.get("backend_attempted") is True:
                        await emit(
                            "model_observation.automatic_started",
                            tool_call_id=action_call.id,
                            name=action_call.name,
                            payload={"source_tool_call_id": action_call.id},
                        )
                        failure_reason = "automatic observation did not run"
                        for attempt in range(1, MAX_OBSERVE_FAILURES + 1):
                            observe_call = automatic_observation_call(
                                action_call,
                                attempt,
                                tool_name=observation_tool_name(all_tool_schemas),
                            )
                            observe_results = await self._dispatch_tools(
                                [observe_call],
                                session,
                                run_id,
                                event_sink,
                                settings,
                                events,
                                run_context=run_context,
                                cancellation_token=interrupt,
                            )
                            if len(observe_results) != 1:
                                failure_reason = "automatic observe returned an invalid batch"
                            else:
                                observe_result = observe_results[0]
                                try:
                                    image_evidence = validate_observation_result(observe_result)
                                except ValueError as exc:
                                    failure_reason = str(exc)
                                else:
                                    attach_automatic_observation(
                                        action_result,
                                        observe_result,
                                        image_evidence,
                                    )
                                    agent_state.unconsumed_observation_tool_call_id = (
                                        action_result.tool_call_id
                                    )
                                    await emit(
                                        "model_observation.automatic_completed",
                                        tool_call_id=action_call.id,
                                        name=action_call.name,
                                        payload={
                                            "attempt": attempt,
                                            "content_sha256": image_evidence.content_sha256,
                                            "pixel_sha256": image_evidence.pixel_sha256,
                                            "source_tool_call_id": action_call.id,
                                        },
                                    )
                                    break
                            await emit(
                                "model_observation.automatic_attempt_failed",
                                tool_call_id=action_call.id,
                                name=action_call.name,
                                payload={
                                    "attempt": attempt,
                                    "reason": failure_reason,
                                    "source_tool_call_id": action_call.id,
                                },
                            )
                        else:
                            data = dict(action_result.data or {})
                            data["automatic_observation"] = {
                                "status": "failed",
                                "source_tool_call_id": action_call.id,
                                "attempts": MAX_OBSERVE_FAILURES,
                                "reason": failure_reason,
                            }
                            action_result.data = data
                            automatic_observation_error = failure_reason

                if barrier is None:
                    manual_observations = [
                        (call, result)
                        for call in tool_calls
                        for result in tool_results
                        if call.name in {"observe", "browser_screenshot"}
                        and result.tool_call_id == call.id
                    ]
                    if len(manual_observations) == 1:
                        manual_call, manual_result = manual_observations[0]
                        try:
                            image_evidence = validate_observation_result(manual_result)
                        except ValueError as exc:
                            await emit(
                                "model_observation.manual_failed",
                                tool_call_id=manual_call.id,
                                name=manual_call.name,
                                payload={"reason": str(exc)},
                            )
                        else:
                            agent_state.unconsumed_observation_tool_call_id = manual_call.id
                            await emit(
                                "model_observation.manual_completed",
                                tool_call_id=manual_call.id,
                                name=manual_call.name,
                                payload={
                                    "content_sha256": image_evidence.content_sha256,
                                    "pixel_sha256": image_evidence.pixel_sha256,
                                },
                            )

                for result in tool_results:
                    session.append(result)

                agent_state.record_tool_results(
                    [
                        {
                            "tool_call_id": result.tool_call_id,
                            "name": result.name,
                            "is_error": result.is_error,
                            "text": "\n".join(block.text for block in result.content if block.text)[
                                :500
                            ],
                        }
                        for result in tool_results
                    ]
                )

                if barrier is not None and len(tool_results) == 1:
                    observe_data = tool_results[0].data
                    if observe_data is None:
                        observe_data = {}
                        tool_results[0].data = observe_data
                    observe_data["observation_of_tool_call_id"] = barrier.source_tool_call_id

                await self._publish_tool_results(
                    tool_calls,
                    tool_results,
                    dispatch_ms=dispatch_ms,
                    emit=emit,
                )

                if automatic_observation_error is not None:
                    await emit(
                        "model_observation.automatic_failed",
                        tool_call_id=observation_actions[0].id,
                        name=observation_actions[0].name,
                        payload={
                            "error": automatic_observation_error,
                            "error_code": "automatic_observation_failed",
                            "source_tool_call_id": observation_actions[0].id,
                        },
                    )
                    await emit(
                        "runtime.turn_failed",
                        payload={
                            "error": automatic_observation_error,
                            "error_code": "automatic_observation_failed",
                        },
                    )
                    save_snapshot("failed")
                    return GenericRunResult(
                        run_id=run_id,
                        status="failed",
                        session=session,
                        events=events,
                        error_code="automatic_observation_failed",
                    )

                if barrier is not None:
                    observe_result = tool_results[0]
                    try:
                        image_evidence = validate_observation_result(observe_result)
                    except ValueError as exc:
                        barrier.observe_failures += 1
                        await emit(
                            "model_observation.observe_failed",
                            tool_call_id=observe_result.tool_call_id,
                            name=observe_result.name,
                            payload={
                                "observe_failures": barrier.observe_failures,
                                "reason": str(exc),
                                "source_tool_call_id": barrier.source_tool_call_id,
                            },
                        )
                        if barrier.observe_failures >= MAX_OBSERVE_FAILURES:
                            await emit(
                                "runtime.turn_failed",
                                payload={
                                    "error": "model observation retry limit reached",
                                    "error_code": "model_observation_failed",
                                },
                            )
                            save_snapshot("failed")
                            return GenericRunResult(
                                run_id=run_id,
                                status="failed",
                                session=session,
                                events=events,
                                error_code="model_observation_failed",
                            )
                    else:
                        agent_state.pending_model_observation = None
                        agent_state.unconsumed_observation_tool_call_id = (
                            observe_result.tool_call_id
                        )
                        await emit(
                            "model_observation.barrier_cleared",
                            tool_call_id=observe_result.tool_call_id,
                            name=observe_result.name,
                            payload={
                                "content_sha256": image_evidence.content_sha256,
                                "pixel_sha256": image_evidence.pixel_sha256,
                                "source_tool_call_id": barrier.source_tool_call_id,
                            },
                        )

                save_snapshot()

                if (
                    self._stop_condition is not None
                    and agent_state.pending_model_observation is None
                ):
                    decision = self._stop_condition(session, tool_results)
                    if inspect.isawaitable(decision):
                        decision = await decision
                    if decision is not None:
                        await emit(
                            "runtime.turn_completed"
                            if decision.status in {"replied", "waiting_user"}
                            else "runtime.turn_failed",
                            payload={"error_code": decision.error_code, **decision.payload},
                        )
                        save_snapshot(decision.status)
                        return GenericRunResult(
                            run_id=run_id,
                            status=decision.status,
                            session=session,
                            events=events,
                            final_reply=decision.final_reply,
                            error_code=decision.error_code,
                        )

                if (
                    settings is not None
                    and agent_state.pending_model_observation is None
                    and agent_state.unconsumed_observation_tool_call_id is None
                ):
                    guard_result = await self._check_loop_guards(
                        agent_state,
                        emit=emit,
                        settings=settings,
                    )
                    if guard_result is not None:
                        save_snapshot("failed")
                        return GenericRunResult(
                            run_id=run_id,
                            status="failed",
                            session=session,
                            events=events,
                            error_code=guard_result,
                        )

                iteration += 1

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
        finally:
            if signal_registered:
                signal.signal(signal.SIGINT, old_sigint_handler)

    @staticmethod
    def _build_persistence_manager(
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        settings: Any,
        system_prompt: str,
    ) -> SessionPersistenceManager | None:
        observability = getattr(settings, "observability", None)
        if observability is None:
            return None
        if not (
            bool(getattr(observability, "save_session_per_iteration", True))
            or bool(getattr(observability, "save_on_sigint", True))
        ):
            return None
        return SessionPersistenceManager(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state_store,
            session_root=Path(str(getattr(observability, "session_dir", "~/.homemaster/sessions"))),
            model=str(getattr(settings, "provider_name", "")),
            system_prompt=system_prompt,
            strip_images=bool(getattr(observability, "strip_images_in_snapshot", True)),
            trace_rotation_max_mb=int(getattr(observability, "trace_rotation_max_mb", 100)),
        )

    @staticmethod
    async def _publish_text_delta(
        delta: Any,
        *,
        emit: Callable[..., Awaitable[None]],
    ) -> None:
        payload = {}
        text = getattr(delta, "text_delta", None)
        if text:
            payload["text_delta"] = text
        reasoning = getattr(delta, "reasoning_delta", None)
        if reasoning:
            payload["reasoning_delta"] = reasoning
        if payload:
            await emit("transport.delta", payload=payload)

    @staticmethod
    async def _record_usage(
        agent_state: AgentState,
        usage: dict[str, int],
        *,
        emit: Callable[..., Awaitable[None]],
    ) -> None:
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        input_tokens += int(usage.get("cache_read_input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
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

    async def _cancel_result(
        self,
        session: AgentSession,
        run_id: str,
        events: list[RuntimeEvent],
        *,
        emit: Callable[..., Awaitable[None]],
        phase: str,
        agent_state: AgentState | None = None,
        task_state_store: TaskStateStore | None = None,
        persistence: SessionPersistenceManager | None = None,
        local_only: bool = False,
    ) -> GenericRunResult:
        await emit(
            "runtime.cancelled",
            payload={"phase": phase},
            local_only=local_only,
        )
        snapshot = getattr(task_state_store, "snapshot", None)
        if snapshot is not None and snapshot.status == TaskStatus.ACTIVE:
            task_state_store.update_status(TaskStatus.PAUSED)
        if agent_state is not None:
            agent_state.status = "cancelled"
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
    async def _publish_tool_results(
        tool_calls: list[ToolCall],
        tool_results: list[ToolResultMessage],
        *,
        dispatch_ms: float,
        emit: Callable[..., Awaitable[None]],
        local_only: bool = False,
    ) -> None:
        call_args = {tool_call.id: tool_call.arguments for tool_call in tool_calls}
        for result in tool_results:
            await emit(
                "tool.call_failed" if result.is_error else "tool.call_completed",
                tool_call_id=result.tool_call_id,
                name=result.name,
                payload={
                    "is_error": result.is_error,
                    "args": call_args.get(result.tool_call_id, {}),
                    "result": "\n".join(block.text for block in result.content if block.text),
                    "data": result.data,
                },
                duration_ms=dispatch_ms,
                local_only=local_only,
            )

    @staticmethod
    async def _check_loop_guards(
        agent_state: AgentState,
        *,
        emit: Callable[..., Awaitable[None]],
        settings: Any,
    ) -> str | None:
        guards = getattr(settings, "runtime_guards", None)
        if guards is None:
            return None
        max_errors = getattr(guards, "max_consecutive_tool_errors", 5)
        if agent_state.consecutive_tool_errors >= max_errors:
            await emit("runtime.guard_triggered", payload={"guard": "max_consecutive_tool_errors"})
            return "max_consecutive_tool_errors"
        max_no_progress = getattr(guards, "max_no_progress_iterations", 20)
        if agent_state.no_progress_iterations >= max_no_progress:
            await emit(
                "runtime.guard_triggered",
                payload={"guard": "max_no_progress_iterations"},
            )
            return "max_no_progress_iterations"
        return None

    @staticmethod
    def _reactive_compact_max_retries(settings: Any) -> int:
        context = getattr(settings, "context", None)
        value = getattr(context, "reactive_compact_max_retries", 2)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 2

    async def _dispatch_tools(
        self,
        tool_calls: list[ToolCall],
        session: AgentSession,
        run_id: str,
        event_sink: Any,
        settings: Any,
        events: list[RuntimeEvent],
        *,
        run_context: RunContext | None = None,
        cancellation_token: Any = None,
    ) -> list[ToolResultMessage]:
        """Dispatch tool calls and return ToolResultMessages."""

        if not hasattr(self._tool_executor, "dispatch"):
            raise TypeError("tool executor must provide explicit batch dispatch")
        if run_context is None:
            run_context = RunContext(
                session_id=session.session_id,
                run_id=run_id,
                turn_index=0,
                settings=settings,
                event_sink=event_sink,
            )
        run_context.cancellation_token = cancellation_token
        value = self._tool_executor.dispatch(
            tool_calls=tool_calls,
            run_context=run_context,
        )
        if inspect.isawaitable(value):
            value = await value
        return value


def _cancelled(interrupt: InterruptController, cancellation_token: Any) -> bool:
    return interrupt.cancelled or bool(getattr(cancellation_token, "cancelled", False))


def _bind_provider_attempt_contexts(
    tool_calls: list[ToolCall],
    frozen_messages: list[Any],
    run_context: RunContext | None,
) -> None:
    if run_context is None:
        return
    binder = run_context.deps.get("provider_attempt_context_binder")
    if not callable(binder):
        return
    binder(
        tool_calls=tool_calls,
        frozen_messages=frozen_messages,
        deps=run_context.deps,
    )


async def _consume_stream(
    stream: Any,
    deltas: list[Any],
    *,
    interrupt: InterruptController,
    cancellation_token: Any,
    deadline: Any,
    on_delta: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    async def consume() -> None:
        if not hasattr(stream, "__aiter__"):
            raise TypeError("provider stream must be an async iterator")
        async for delta in stream:
            if _cancelled(interrupt, cancellation_token):
                break
            deltas.append(delta)
            if on_delta is not None:
                await on_delta(delta)

    remaining = deadline.remaining_s() if deadline is not None else None
    if remaining is None:
        await consume()
        return
    if remaining <= 0:
        raise TimeoutError("provider deadline expired")
    try:
        async with asyncio.timeout(remaining):
            await consume()
    except TimeoutError as exc:
        raise TimeoutError("provider deadline expired") from exc


async def _close_stream(stream: Any, *, deadline: Any = None) -> None:
    aclose = getattr(stream, "aclose", None)
    if callable(aclose):
        await _await_with_deadline(
            aclose(),
            deadline=deadline,
            operation="provider stream close",
        )
        return
    close = getattr(stream, "close", None)
    if callable(close):
        value = close()
        if inspect.isawaitable(value):
            await _await_with_deadline(
                value,
                deadline=deadline,
                operation="provider stream close",
            )


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
            await _cancel_and_join(task, require_stopped=False)
            raise
        if task in done:
            return task.result()
    await _cancel_and_join(task, require_stopped=True)
    raise TimeoutError(f"{operation} exceeded the run deadline")


async def _cancel_and_join(
    task: asyncio.Future[Any],
    *,
    require_stopped: bool,
) -> None:
    if task.done():
        return
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=_CANCEL_JOIN_GRACE_S)
    if task not in done:
        task.add_done_callback(_consume_future_exception)
        if require_stopped:
            raise TimeoutError("cancelled async operation did not stop")
        return
    await asyncio.gather(task, return_exceptions=True)


def _consume_future_exception(task: asyncio.Future[Any]) -> None:
    if task.cancelled():
        return
    task.exception()


def _accepts_attempt_sink(stream: Any) -> bool:
    return _accepts_stream_parameter(stream, "attempt_sink")


def _accepts_stream_parameter(stream: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(stream).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _last_provider_attempt(sink: Any) -> ProviderAttemptRecord | None:
    record = getattr(sink, "last_record", None)
    return record if isinstance(record, ProviderAttemptRecord) else None


def _provider_retry_allowed(
    *,
    error: Exception,
    deltas: list[Any],
    commit_state: AttemptCommitState,
    attempt: ProviderAttemptRecord | None,
) -> bool:
    if (
        not isinstance(error, LLMClientError)
        or attempt is None
        or any(not _reasoning_only_delta(delta) for delta in deltas)
    ):
        return False
    if any(
        (
            commit_state.assistant_committed,
            commit_state.tool_dispatch_committed,
            commit_state.external_action_committed,
        )
    ):
        return False
    if attempt.response_completed:
        return False
    failure = (error.error_type, error.cause_code)
    return (attempt.error_type, attempt.cause_code) == failure


def _provider_retry_delay(attempt_index: int) -> float:
    """Return the delay before the next attempt after a failed attempt."""

    if attempt_index <= 0:
        return 0.0
    return _PROVIDER_RETRY_BASE_DELAY_S * (2 ** (attempt_index - 1))


async def _sleep_for_provider_retry(delay_s: float, *, deadline: Any = None) -> None:
    if deadline is None:
        await asyncio.sleep(delay_s)
        return
    remaining = deadline.remaining_s()
    if remaining is None:
        await asyncio.sleep(delay_s)
        return
    if remaining <= 0:
        raise TimeoutError("provider retry deadline expired")
    async with asyncio.timeout(remaining):
        await asyncio.sleep(delay_s)


def _reasoning_only_delta(delta: Any) -> bool:
    return bool(getattr(delta, "reasoning_delta", None)) and not any(
        (
            getattr(delta, "text_delta", None),
            getattr(delta, "tool_call_delta", None),
            getattr(delta, "finish_reason", None),
            getattr(delta, "usage", None),
            getattr(delta, "provider_metadata", None),
        )
    )
