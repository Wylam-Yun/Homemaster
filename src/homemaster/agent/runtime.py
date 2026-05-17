"""AgentRuntime — Mimo-driven tool loop main runtime.

The runtime executes a tool loop: build context → Mimo decide → dispatch
tool → update state → repeat. It does NOT follow a fixed PLAN/ACT/VERIFY/
RECOVER pipeline. Mimo selects each tool via structured decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from homemaster.agent.context_builder import ContextBuilder
from homemaster.agent.decision import FinishDecision, ToolCallDecision
from homemaster.agent.state import AgentState
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.events.runtime_events import EventSink, RuntimeEvent
from homemaster.logger import get_logger
from homemaster.memory.context_snapshot import ContextSnapshot
from homemaster.providers.mimo_decision_client import MimoDecisionClient
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.state_updater import StateUpdater


@dataclass
class AgentRunResult:
    """Result of an AgentRuntime.run() execution."""

    run_id: str
    final_status: str
    state: AgentState
    events: list[RuntimeEvent]


class AgentRuntime:
    """Mimo-driven tool loop runtime.

    Dependencies are injected via constructor — no module-level config reads,
    no import-time singletons.
    """

    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        decision_client: MimoDecisionClient,
        tool_registry: ToolRegistry,
        skill_registry: Any,  # SkillRegistry
        event_sink: EventSink,
        context_builder: ContextBuilder,
        dispatcher: ToolDispatcher,
        state_updater: StateUpdater,
        context_snapshot: ContextSnapshot,
    ) -> None:
        self._settings = settings
        self._decision_client = decision_client
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry
        self._event_sink = event_sink
        self._context_builder = context_builder
        self._dispatcher = dispatcher
        self._state_updater = state_updater
        self._context_snapshot = context_snapshot

    def _emit(self, state: AgentState, event_type: str, **kwargs: Any) -> None:
        """Emit a RuntimeEvent with source="agent_runtime" and common fields."""
        self._event_sink.emit(RuntimeEvent(
            turn_index=state.turn_index,
            event_type=event_type,
            run_id=self._settings.run_id,
            source="agent_runtime",
            state_status=state.status,
            **kwargs,
        ))

    def run(
        self,
        user_request: str,
        initial_state: AgentState | None = None,
    ) -> AgentRunResult:
        """Execute the tool loop until finish, failure, or max_turns."""
        logger = get_logger()

        state = initial_state or AgentState(run_id=self._settings.run_id)
        state.user_request = user_request

        tool_manifests = self._tool_registry.tool_manifests()
        skill_summaries = self._skill_registry.candidate_summaries()
        max_turns = self._settings.max_turns

        logger.info(
            "[%s] AgentRuntime.run started  max_turns=%d  tools=%d",
            self._settings.run_id, max_turns, len(tool_manifests),
        )

        run_started_at = time.perf_counter()
        self._emit(state, "run_started", payload={
            "user_request": user_request, "max_turns": max_turns,
        })

        while state.status == "running" and state.turn_index < max_turns:
            turn_started_at = time.perf_counter()
            self._emit(state, "turn_started", payload={"turn_index": state.turn_index})

            # Refresh snapshots if stale
            state = self._context_snapshot.refresh_if_stale(state)

            # Build compact context
            ctx_started = time.perf_counter()
            context = self._context_builder.build(
                state, tool_manifests, skill_summaries, max_turns
            )
            self._emit(state, "context_built", payload={
                "duration_ms": round((time.perf_counter() - ctx_started) * 1000, 1),
            })

            # Ask Mimo for decision
            self._emit(state, "decision_started", payload={})
            dec_started = time.perf_counter()
            try:
                decision = self._decision_client.decide(
                    context=context,
                    tools=tool_manifests,
                    settings=self._settings,
                    turn_index=state.turn_index,
                )
            except Exception as exc:
                self._emit(state, "decision_failed", payload={
                    "error": str(exc), "error_type": type(exc).__name__,
                    "duration_ms": round((time.perf_counter() - dec_started) * 1000, 1),
                })
                state.status = "failed"
                self._emit(state, "run_failed", payload={
                    "final_status": "failed",
                    "error": str(exc), "error_type": type(exc).__name__,
                    "duration_ms": round(
                        (time.perf_counter() - run_started_at) * 1000, 1,
                    ),
                })
                raise
            dec_ms = round((time.perf_counter() - dec_started) * 1000, 1)
            self._emit(state, "decision_completed", payload={
                "decision_type": type(decision).__name__, "duration_ms": dec_ms,
            })

            # Legacy compat event
            self._event_sink.emit(RuntimeEvent(
                turn_index=state.turn_index,
                event_type="decision",
                payload={"decision_type": type(decision).__name__},
                run_id=self._settings.run_id,
                phase_label="decide",
                status="deciding",
            ))

            # FinishDecision → terminate
            if isinstance(decision, FinishDecision):
                state.status = "completed" if decision.status == "completed" else "failed"
                self._emit(state, "finish_decision_received", payload={
                    "decision_status": decision.status, "summary": decision.summary,
                    "duration_ms": round((time.perf_counter() - turn_started_at) * 1000, 1),
                })
                # Legacy compat event
                self._event_sink.emit(RuntimeEvent(
                    turn_index=state.turn_index,
                    event_type="state_transition",
                    payload={"status": state.status, "summary": decision.summary},
                    run_id=self._settings.run_id,
                    phase_label="finish",
                    status=state.status,
                ))
                logger.info(
                    "[%s] finish decision  status=%s  summary=%s",
                    self._settings.run_id, state.status, decision.summary,
                )
                break

            # ToolCallDecision
            assert isinstance(decision, ToolCallDecision)
            spec = self._tool_registry.get(decision.tool)

            # Rejection: tool not found or not selectable (includes finish_task)
            if spec is None or not spec.selectable_by_model:
                state.failures.append({
                    "turn": state.turn_index,
                    "tool": decision.tool,
                    "error": "invalid or non-selectable tool",
                })
                self._emit(state, "tool_call_rejected", payload={
                    "tool": decision.tool, "reason": "invalid or non-selectable",
                })
                # Legacy compat event
                self._event_sink.emit(RuntimeEvent(
                    turn_index=state.turn_index,
                    event_type="error",
                    payload={"tool": decision.tool, "error": "invalid or non-selectable"},
                    run_id=self._settings.run_id,
                    phase_label="reject",
                    status="error",
                ))
                logger.warning(
                    "[%s] rejected tool_call  tool=%s  reason=invalid_or_non_selectable",
                    self._settings.run_id, decision.tool,
                )
                state.turn_index += 1
                continue

            # Tool validated
            self._emit(state, "tool_call_validated", payload={
                "tool": decision.tool, "executor_mode": spec.executor_mode,
            })

            # Dispatch tool
            self._emit(state, "tool_call_started", payload={
                "tool": decision.tool, "arguments": decision.arguments,
            }, tool_name=decision.tool, executor_mode=spec.executor_mode)
            # Legacy compat event
            self._event_sink.emit(RuntimeEvent(
                turn_index=state.turn_index,
                event_type="tool_call",
                payload={"tool": decision.tool, "arguments": decision.arguments},
                run_id=self._settings.run_id,
                phase_label="dispatch",
                status="calling",
            ))

            dispatch_started = time.perf_counter()
            result = self._dispatcher.dispatch(
                spec=spec, arguments=decision.arguments, state=state, settings=self._settings
            )
            dispatch_ms = round((time.perf_counter() - dispatch_started) * 1000, 1)

            tool_event_type = "tool_call_completed" if result.success else "tool_call_failed"
            self._emit(state, tool_event_type, payload={
                "tool": result.tool_name, "success": result.success,
                "executor_mode": result.executor_mode, "failure_reason": result.failure_reason,
                "duration_ms": dispatch_ms,
            }, tool_name=result.tool_name, executor_mode=result.executor_mode,
               duration_ms=dispatch_ms)
            # Legacy compat event
            self._event_sink.emit(RuntimeEvent(
                turn_index=state.turn_index,
                event_type="tool_result",
                payload={
                    "tool": result.tool_name,
                    "success": result.success,
                    "executor_mode": result.executor_mode,
                    "failure_reason": result.failure_reason,
                },
                run_id=self._settings.run_id,
                phase_label="result",
                status="success" if result.success else "failure",
            ))

            # Update state
            state = self._state_updater.apply(state=state, result=result, spec=spec)
            self._emit(state, "state_transitioned", payload={
                "triggered_by": result.tool_name, "success": result.success,
                "duration_ms": round((time.perf_counter() - turn_started_at) * 1000, 1),
            }, tool_name=result.tool_name, executor_mode=result.executor_mode)
            # Legacy compat event
            self._event_sink.emit(RuntimeEvent(
                turn_index=state.turn_index,
                event_type="state_transition",
                payload={"triggered_by": result.tool_name, "success": result.success},
                run_id=self._settings.run_id,
                phase_label="update",
                status="updated",
            ))

            # Refresh snapshots after memory/profile updates
            if result.success and result.data.get("committed"):
                state = self._context_snapshot.refresh_if_stale(state)

            state.turn_index += 1

            logger.info(
                "[%s] turn %d  tool=%s  success=%s",
                self._settings.run_id, state.turn_index - 1, result.tool_name, result.success,
            )

        # max_turns exceeded → failed
        if state.status == "running":
            state.status = "failed"
            self._emit(state, "max_turns_exceeded", payload={
                "max_turns": max_turns,
                "duration_ms": round((time.perf_counter() - run_started_at) * 1000, 1),
            })
            # Legacy compat event
            self._event_sink.emit(RuntimeEvent(
                turn_index=state.turn_index,
                event_type="error",
                payload={"error": "max_turns_exceeded", "max_turns": max_turns},
                run_id=self._settings.run_id,
                phase_label="timeout",
                status="error",
            ))
            logger.warning("[%s] max_turns exceeded  status=failed", self._settings.run_id)

        total_ms = round((time.perf_counter() - run_started_at) * 1000, 1)
        final_event = "run_completed" if state.status == "completed" else "run_failed"
        self._emit(state, final_event, payload={
            "final_status": state.status, "duration_ms": total_ms,
        }, duration_ms=total_ms)

        logger.info(
            "[%s] AgentRuntime.run finished  status=%s",
            self._settings.run_id, state.status,
        )

        return AgentRunResult(
            run_id=self._settings.run_id,
            final_status=state.status,
            state=state,
            events=self._event_sink.events,
        )
