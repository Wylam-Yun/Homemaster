"""GenericAgentRuntime — message/tool-call/tool-result agent loop."""

from __future__ import annotations

import signal
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homemaster.agent.interrupt import InterruptController
from homemaster.agent.messages import (
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import SessionPersistenceManager
from homemaster.agent.state import AgentState, ProviderUsage
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sinks import FanoutEventSink
from homemaster.providers.transports.types import aggregate_deltas
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore

if TYPE_CHECKING:
    from homemaster.agent.context import ContextAssembler

_CONTEXT_LENGTH_KEYWORDS = (
    "context_length_exceeded",
    "context length",
    "maximum context",
    "context window",
    "exceeds the available context size",
)


def _is_context_length_error(error_msg: str) -> bool:
    lowered = error_msg.lower()
    return any(keyword in lowered for keyword in _CONTEXT_LENGTH_KEYWORDS)


@dataclass
class ToolSpec:
    """Minimal tool spec for the generic runtime."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDispatcherResult:
    """Result from dispatching a single tool call."""

    tool_call_id: str
    name: str
    content: list[ContentBlock]
    is_error: bool = False
    data: dict[str, Any] | None = None


@dataclass
class GenericRunResult:
    """Result of a GenericAgentRuntime.run() execution."""

    run_id: str
    status: str
    session: AgentSession
    events: list[RuntimeEvent]
    final_reply: str = ""
    error_code: str | None = None


@dataclass(frozen=True)
class RuntimeStopDecision:
    """Optional generic decision to stop a run after tool results are appended."""

    status: str
    final_reply: str = ""
    error_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


StopCondition = Callable[
    [AgentSession, list[ToolResultMessage]],
    RuntimeStopDecision | None,
]


class GenericAgentRuntime:
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
    ) -> None:
        self._transport = transport
        self._tool_executor = tool_executor
        self._max_tool_iterations = max_tool_iterations
        self._stop_condition = stop_condition
        self._context_assembler = context_assembler
        self._system_prompt = system_prompt

    def run(
        self,
        session: AgentSession,
        user_text: str,
        tools: list[ToolSpec] | None = None,
        *,
        user_content: list[ContentBlock] | None = None,
        event_sink: Any = None,
        run_id: str | None = None,
        settings: Any = None,
        agent_state: AgentState | None = None,
        task_state_store: TaskStateStore | None = None,
    ) -> GenericRunResult:
        """Execute one agent run: user input -> model -> tool loop -> final reply."""

        run_id = run_id or uuid.uuid4().hex[:12]
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
                old_sigint_handler = signal.signal(signal.SIGINT, interrupt.handle_sigint)
                signal_registered = True
            except ValueError:
                signal_registered = False

        def emit(event_type: str, **kwargs: Any) -> None:
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
            if event_sink is not None:
                event_sink.emit(event)

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
        run_context = getattr(self._tool_executor, "_run_context", None)
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
                persistence
                if event_sink is None
                else FanoutEventSink([event_sink, persistence])
            )

        def save_snapshot(status: str | None = None) -> None:
            if persistence is None:
                return
            if status is not None:
                agent_state.status = status  # type: ignore[assignment]
            persistence.save_snapshot()

        emit(
            "runtime.turn_started",
            payload={
                "user_text": user_text,
                "content_block_types": [block.type for block in initial_content],
            },
        )

        tool_schemas = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in (tools or [])
        ]

        iteration = 0
        reactive_compact_retries = 0
        try:
            while self._max_tool_iterations is None or iteration < self._max_tool_iterations:
                if interrupt.cancelled:
                    return self._cancel_result(
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
                    task_state_store = None
                    run_context = getattr(self._tool_executor, "_run_context", None)
                    if run_context is not None:
                        task_state_store = run_context.deps.get("task_state_store")
                    composed = self._context_assembler.prepare(
                        session=session,
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        tools=context_tools,
                    )
                    context_messages = composed.messages
                    context_system_prompt = composed.system_prompt
                    context_tools = composed.tools
                    if composed.metrics.compaction_triggered:
                        emit(
                            "context.compaction",
                            payload={
                                "trigger": (
                                    "reactive"
                                    if composed.metrics.compaction_kind in {"reactive", "emergency"}
                                    else "auto"
                                ),
                                "after_tokens": composed.metrics.estimated_tokens,
                                "kind": composed.metrics.compaction_kind,
                            },
                        )
                        save_snapshot()

                try:
                    stream = self._transport.stream(
                        context_messages,
                        tools=context_tools,
                        system_prompt=context_system_prompt,
                        event_sink=event_sink,
                        run_id=run_id,
                        session_id=session.session_id,
                        turn_index=0,
                        iteration=iteration,
                    )
                    interrupt.set_stream(stream)
                    deltas = []
                    try:
                        for delta in stream:
                            if interrupt.cancelled:
                                break
                            deltas.append(delta)
                    finally:
                        interrupt.clear_stream()
                        close = getattr(stream, "close", None)
                        if callable(close):
                            close()
                    if interrupt.cancelled:
                        return self._cancel_result(
                            session,
                            run_id,
                            events,
                            emit=emit,
                            phase="llm_call",
                            agent_state=agent_state,
                            task_state_store=task_state_store,
                            persistence=persistence,
                        )
                    assistant_msg = aggregate_deltas(deltas)
                except Exception as exc:
                    if self._context_assembler is not None and _is_context_length_error(str(exc)):
                        max_retries = self._reactive_compact_max_retries(settings)
                        if reactive_compact_retries >= max_retries:
                            emit(
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
                        emit("runtime.reactive_compact_started", payload={"reason": str(exc)[:300]})
                        reactive_compact_retries += 1
                        self._context_assembler.force_compact_next = "aggressive"
                        continue
                    emit(
                        "transport.request_failed",
                        payload={"error": str(exc), "error_type": type(exc).__name__},
                    )
                    emit(
                        "runtime.turn_failed",
                        payload={"error": str(exc), "error_code": "transport_error"},
                    )
                    save_snapshot("failed")
                    return GenericRunResult(
                        run_id=run_id,
                        status="failed",
                        session=session,
                        events=events,
                        error_code="transport_error",
                    )

                model_elapsed_ms = round((time.perf_counter() - model_started) * 1000, 1)
                session.append(assistant_msg)
                if assistant_msg.reasoning_content:
                    emit("assistant.thinking", payload={"thinking": assistant_msg.reasoning_content})
                if assistant_msg.text:
                    emit("assistant.reply", payload={"reply": assistant_msg.text})
                    if persistence is not None:
                        persistence.append_message(assistant_msg)
                reactive_compact_retries = 0

                agent_state.last_assistant_text = assistant_msg.text
                if assistant_msg.usage:
                    self._record_usage(agent_state, assistant_msg.usage, emit=emit)

                if assistant_msg.finish_reason == "length":
                    emit(
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

                if not assistant_msg.tool_calls:
                    emit(
                        "runtime.turn_completed",
                        payload={"final_reply": assistant_msg.text, "duration_ms": model_elapsed_ms},
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
                for tool_call in tool_calls:
                    emit(
                        "tool.call_started",
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        payload={"arguments": tool_call.arguments},
                    )

                dispatch_started = time.perf_counter()
                tool_results = self._dispatch_tools(
                    tool_calls,
                    session,
                    run_id,
                    event_sink,
                    settings,
                    events,
                    cancellation_token=interrupt,
                )
                dispatch_ms = round((time.perf_counter() - dispatch_started) * 1000, 1)

                expected_ids = {tool_call.id for tool_call in tool_calls}
                actual_ids = {result.tool_call_id for result in tool_results}
                if expected_ids != actual_ids:
                    missing = expected_ids - actual_ids
                    emit(
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

                for result in tool_results:
                    session.append(result)

                agent_state.record_tool_results(
                    [
                        {
                            "tool_call_id": result.tool_call_id,
                            "name": result.name,
                            "is_error": result.is_error,
                            "text": "\n".join(
                                block.text for block in result.content if block.text
                            )[:500],
                        }
                        for result in tool_results
                    ]
                )

                call_args = {tool_call.id: tool_call.arguments for tool_call in tool_calls}
                for result in tool_results:
                    emit(
                        "tool.call_failed" if result.is_error else "tool.call_completed",
                        tool_call_id=result.tool_call_id,
                        name=result.name,
                        payload={
                            "is_error": result.is_error,
                            "args": call_args.get(result.tool_call_id, {}),
                            "result": "\n".join(
                                block.text for block in result.content if block.text
                            ),
                            "data": result.data,
                        },
                        duration_ms=dispatch_ms,
                    )

                if interrupt.cancelled:
                    return self._cancel_result(
                        session,
                        run_id,
                        events,
                        emit=emit,
                        phase="tool_execution",
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        persistence=persistence,
                    )

                save_snapshot()

                if self._stop_condition is not None:
                    decision = self._stop_condition(session, tool_results)
                    if decision is not None:
                        emit(
                            "runtime.turn_completed"
                            if decision.status == "replied"
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

                if settings is not None:
                    guard_result = self._check_loop_guards(agent_state, emit=emit, settings=settings)
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

            emit(
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
    def _record_usage(
        agent_state: AgentState,
        usage: dict[str, int],
        *,
        emit: Callable[..., None],
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
        emit(
            "usage.update",
            payload={
                "input_tokens": agent_state.provider_usage.input_tokens,
                "output_tokens": agent_state.provider_usage.output_tokens,
                "total_tokens": agent_state.provider_usage.total_tokens,
            },
        )

    def _cancel_result(
        self,
        session: AgentSession,
        run_id: str,
        events: list[RuntimeEvent],
        *,
        emit: Callable[..., None],
        phase: str,
        agent_state: AgentState | None = None,
        task_state_store: TaskStateStore | None = None,
        persistence: SessionPersistenceManager | None = None,
    ) -> GenericRunResult:
        emit("runtime.cancelled", payload={"phase": phase})
        if task_state_store is None:
            run_context = getattr(self._tool_executor, "_run_context", None)
            if run_context is not None:
                task_state_store = run_context.deps.get("task_state_store")
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
    def _check_loop_guards(
        agent_state: AgentState,
        *,
        emit: Callable[..., None],
        settings: Any,
    ) -> str | None:
        guards = getattr(settings, "runtime_guards", None)
        if guards is None:
            return None
        max_errors = getattr(guards, "max_consecutive_tool_errors", 5)
        if agent_state.consecutive_tool_errors >= max_errors:
            emit("runtime.guard_triggered", payload={"guard": "max_consecutive_tool_errors"})
            return "max_consecutive_tool_errors"
        max_no_progress = getattr(guards, "max_no_progress_iterations", 20)
        if agent_state.no_progress_iterations >= max_no_progress:
            emit("runtime.guard_triggered", payload={"guard": "max_no_progress_iterations"})
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

    def _dispatch_tools(
        self,
        tool_calls: list[ToolCall],
        session: AgentSession,
        run_id: str,
        event_sink: Any,
        settings: Any,
        events: list[RuntimeEvent],
        *,
        cancellation_token: Any = None,
    ) -> list[ToolResultMessage]:
        """Dispatch tool calls and return ToolResultMessages."""

        if hasattr(self._tool_executor, "dispatch"):
            try:
                from homemaster.agent.normalized import RunContext

                run_context = getattr(self._tool_executor, "_run_context", None)
                if run_context is None and settings is not None:
                    run_context = RunContext(
                        session_id=session.session_id,
                        run_id=run_id,
                        turn_index=0,
                        settings=settings,
                        event_sink=event_sink,
                    )
                if run_context is not None:
                    run_context.cancellation_token = cancellation_token
                    return self._tool_executor.dispatch(
                        tool_calls=tool_calls,
                        run_context=run_context,
                    )
            except TypeError:
                pass

        results: list[ToolResultMessage] = []
        for index, tool_call in enumerate(tool_calls):
            if getattr(cancellation_token, "cancelled", False):
                results.extend(
                    ToolResultMessage(
                        tool_call_id=pending.id,
                        name=pending.name,
                        content=[ContentBlock(text='{"error": "cancelled before tool execution"}')],
                        is_error=True,
                        data={
                            "success": False,
                            "error": "cancelled before tool execution",
                            "cancelled": True,
                        },
                    )
                    for pending in tool_calls[index:]
                )
                break
            try:
                if hasattr(cancellation_token, "enter_tool"):
                    cancellation_token.enter_tool()
                if callable(self._tool_executor):
                    raw_result = self._tool_executor(tool_call.name, tool_call.arguments)
                else:
                    raw_result = self._tool_executor.dispatch(tool_call.name, tool_call.arguments)
            except Exception as exc:
                results.append(
                    ToolResultMessage(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=[ContentBlock(text=f'{{"error": "{exc}"}}')],
                        is_error=True,
                    )
                )
                continue
            finally:
                if hasattr(cancellation_token, "exit_tool"):
                    cancellation_token.exit_tool()

            if isinstance(raw_result, ToolResultMessage):
                if not raw_result.tool_call_id:
                    raw_result = raw_result.model_copy(update={"tool_call_id": tool_call.id})
                results.append(raw_result)
            elif isinstance(raw_result, ToolDispatcherResult):
                results.append(
                    ToolResultMessage(
                        tool_call_id=raw_result.tool_call_id or tool_call.id,
                        name=raw_result.name or tool_call.name,
                        content=raw_result.content,
                        is_error=raw_result.is_error,
                        data=raw_result.data,
                    )
                )
            else:
                data = raw_result if isinstance(raw_result, dict) else {"result": str(raw_result)}
                results.append(
                    ToolResultMessage(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=[ContentBlock(text=str(data))],
                        data=data,
                    )
                )
        return results
