"""GenericAgentRuntime — message/tool-call/tool-result agent loop.

This runtime does not import any domain-specific modules. It operates purely
on normalized messages, tool calls, and tool results.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homemaster.agent.messages import (
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState, ProviderUsage
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.providers.transport import LLMTransport

if TYPE_CHECKING:
    from homemaster.agent.context_assembler import ContextAssembler

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
    """Generic message/tool-call/tool-result agent loop.

    Dependencies are injected via constructor. Does not import domain tools.
    """

    def __init__(
        self,
        *,
        transport: LLMTransport,
        tool_executor: Any,  # ToolDispatcher or callable
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
    ) -> GenericRunResult:
        """Execute one agent run: user input → model → tool loop → final reply."""
        run_id = run_id or uuid.uuid4().hex[:12]
        events: list[RuntimeEvent] = []

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
        agent_state = AgentState(run_id=run_id, session_id=session.session_id)
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
        while self._max_tool_iterations is None or iteration < self._max_tool_iterations:
            agent_state.begin_iteration(iteration)
            t0 = time.perf_counter()

            # Prepare context before model call
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

            try:
                deltas = list(self._transport.stream(
                    context_messages,
                    tools=context_tools,
                    system_prompt=context_system_prompt,
                    event_sink=event_sink,
                    run_id=run_id,
                    session_id=session.session_id,
                    turn_index=0,
                    iteration=iteration,
                ))
                assistant_msg = LLMTransport._aggregate(deltas)
            except Exception as exc:
                # Reactive compact on context-length errors
                if self._context_assembler is not None and _is_context_length_error(str(exc)):
                    emit("runtime.reactive_compact_started", payload={"reason": str(exc)[:300]})
                    self._context_assembler.force_compact_next = True
                    continue

                emit("transport.request_failed", payload={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                })
                emit("runtime.turn_failed", payload={
                    "error": str(exc),
                    "error_code": "transport_error",
                })
                return GenericRunResult(
                    run_id=run_id,
                    status="failed",
                    session=session,
                    events=events,
                    error_code="transport_error",
                )

            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            session.append(assistant_msg)

            agent_state.last_assistant_text = assistant_msg.text
            if assistant_msg.usage:
                input_tokens = int(
                    assistant_msg.usage.get("input_tokens")
                    or assistant_msg.usage.get("prompt_tokens")
                    or 0
                )
                output_tokens = int(
                    assistant_msg.usage.get("output_tokens")
                    or assistant_msg.usage.get("completion_tokens")
                    or 0
                )
                agent_state.provider_usage = ProviderUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                )

            # Handle finish_reason == "length" as failure
            if assistant_msg.finish_reason == "length":
                emit("runtime.turn_failed", payload={
                    "error": "model output truncated",
                    "error_code": "model_output_truncated",
                })
                return GenericRunResult(
                    run_id=run_id,
                    status="failed",
                    session=session,
                    events=events,
                    final_reply=assistant_msg.text,
                    error_code="model_output_truncated",
                )

            # No tool calls → done
            if not assistant_msg.tool_calls:
                emit("runtime.turn_completed", payload={
                    "final_reply": assistant_msg.text,
                    "duration_ms": elapsed_ms,
                })
                return GenericRunResult(
                    run_id=run_id,
                    status="replied",
                    session=session,
                    events=events,
                    final_reply=assistant_msg.text,
                )

            # Dispatch tool calls
            tool_calls = assistant_msg.tool_calls
            for tc in tool_calls:
                emit(
                    "tool.call_started",
                    tool_call_id=tc.id,
                    name=tc.name,
                    payload={"arguments": tc.arguments},
                )

            dispatch_t0 = time.perf_counter()
            tool_results = self._dispatch_tools(
                tool_calls, session, run_id, event_sink, settings, events,
            )
            dispatch_ms = round((time.perf_counter() - dispatch_t0) * 1000, 1)

            # Validate tool result IDs
            expected_ids = {tc.id for tc in tool_calls}
            actual_ids = {tr.tool_call_id for tr in tool_results}
            if expected_ids != actual_ids:
                missing = expected_ids - actual_ids
                emit("runtime.turn_failed", payload={
                    "error": f"tool result ID mismatch: missing {missing}",
                    "error_code": "tool_result_id_mismatch",
                })
                return GenericRunResult(
                    run_id=run_id,
                    status="failed",
                    session=session,
                    events=events,
                    error_code="tool_result_id_mismatch",
                )

            for tr in tool_results:
                session.append(tr)

            agent_state.record_tool_results([
                {
                    "tool_call_id": tr.tool_call_id,
                    "name": tr.name,
                    "is_error": tr.is_error,
                    "text": "\n".join(block.text for block in tr.content if block.text)[:500],
                }
                for tr in tool_results
            ])

            for tr in tool_results:
                emit(
                    "tool.call_failed" if tr.is_error else "tool.call_completed",
                    tool_call_id=tr.tool_call_id,
                    name=tr.name,
                    payload={"is_error": tr.is_error},
                    duration_ms=dispatch_ms,
                )

            if self._stop_condition is not None:
                decision = self._stop_condition(session, tool_results)
                if decision is not None:
                    event_type = (
                        "runtime.turn_completed"
                        if decision.status == "replied"
                        else "runtime.turn_failed"
                    )
                    emit(event_type, payload={
                        "error_code": decision.error_code,
                        **decision.payload,
                    })
                    return GenericRunResult(
                        run_id=run_id,
                        status=decision.status,
                        session=session,
                        events=events,
                        final_reply=decision.final_reply,
                        error_code=decision.error_code,
                    )

            # Loop guards
            if settings is not None:
                guards = getattr(settings, "runtime_guards", None)
                if guards is not None:
                    max_errors = getattr(guards, "max_consecutive_tool_errors", 5)
                    if agent_state.consecutive_tool_errors >= max_errors:
                        emit("runtime.guard_triggered", payload={"guard": "max_consecutive_tool_errors"})
                        return GenericRunResult(
                            run_id=run_id,
                            status="failed",
                            session=session,
                            events=events,
                            error_code="max_consecutive_tool_errors",
                        )
                    max_no_progress = getattr(guards, "max_no_progress_iterations", 20)
                    if agent_state.no_progress_iterations >= max_no_progress:
                        emit("runtime.guard_triggered", payload={"guard": "max_no_progress_iterations"})
                        return GenericRunResult(
                            run_id=run_id,
                            status="failed",
                            session=session,
                            events=events,
                            error_code="max_no_progress_iterations",
                        )

            iteration += 1

        # Max iterations exceeded (only reachable when max_tool_iterations is not None)
        emit("runtime.budget_exhausted", payload={
            "max_tool_iterations": self._max_tool_iterations,
            "error_code": "max_tool_iterations_exceeded",
        })
        return GenericRunResult(
            run_id=run_id,
            status="failed",
            session=session,
            events=events,
            error_code="max_tool_iterations_exceeded",
        )

    def _dispatch_tools(
        self,
        tool_calls: list[ToolCall],
        session: AgentSession,
        run_id: str,
        event_sink: Any,
        settings: Any,
        events: list[RuntimeEvent],
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
                    return self._tool_executor.dispatch(
                        tool_calls=tool_calls,
                        run_context=run_context,
                    )
            except TypeError:
                pass

        results: list[ToolResultMessage] = []

        for tc in tool_calls:
            try:
                if callable(self._tool_executor):
                    result = self._tool_executor(tc.name, tc.arguments)
                else:
                    result = self._tool_executor.dispatch(tc.name, tc.arguments)

                if isinstance(result, ToolResultMessage):
                    # Backfill tool_call_id if executor left it empty
                    if not result.tool_call_id:
                        result = result.model_copy(update={"tool_call_id": tc.id})
                    results.append(result)
                elif isinstance(result, ToolDispatcherResult):
                    results.append(ToolResultMessage(
                        tool_call_id=result.tool_call_id or tc.id,
                        name=result.name or tc.name,
                        content=result.content,
                        is_error=result.is_error,
                        data=result.data,
                    ))
                else:
                    # Assume it's a dict-like result
                    data = result if isinstance(result, dict) else {"result": str(result)}
                    results.append(ToolResultMessage(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=[ContentBlock(text=str(data))],
                        data=data,
                    ))
            except Exception as exc:
                results.append(ToolResultMessage(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=[ContentBlock(text=f'{{"error": "{exc}"}}')],
                    is_error=True,
                ))

        return results
