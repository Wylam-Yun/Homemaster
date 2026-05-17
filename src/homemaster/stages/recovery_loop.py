"""Stage 05 recovery loop — wraps executor with RecoveryDecision dispatch.

Extracted from Stage05Adapter for independent testability.
"""

from __future__ import annotations

from typing import Any

from homemaster.contracts import ExecutionState, OrchestrationPlan
from homemaster.execution_state import reset_subtask_to_pending
from homemaster.logger import get_logger
from homemaster.pipeline.core import PipelineContext
from homemaster.recovery_config import MAX_RECOVERY_ATTEMPTS
from homemaster.runtime import load_provider_config
from homemaster.stages.executor import (
    Stage05ExecutionResult,
    StepDecisionProvider,
    execute_stage_05_plan,
)
from homemaster.stages.recovery import (
    RecoveryDecisionGenerationError,
    generate_recovery_decision,
)

logger = get_logger()


def run_stage05_with_recovery(
    *,
    ctx: PipelineContext,
    plan: OrchestrationPlan,
    decision_provider: StepDecisionProvider,
    config_path: str,
    provider_name: str,
    max_recovery_attempts: int = MAX_RECOVERY_ATTEMPTS,
    event_sink: Any = None,  # EventSink | None
) -> tuple[Stage05ExecutionResult, list[dict[str, Any]]]:
    """Execute a Stage05 plan with recovery loop.

    Returns ``(execution_result, recovery_attempts)`` where
    ``recovery_attempts`` is a list of dicts describing each recovery round.
    """
    initial_state = ExecutionState(
        task_status="running",
        user_location="user_start",
        current_location="robot_start",
    )

    provider = load_provider_config(config_path, provider_name=provider_name)
    current_plan = plan
    current_ctx = ctx
    recovery_attempts: list[dict[str, Any]] = []
    execution_result: Stage05ExecutionResult | None = None
    current_state = initial_state

    def _emit(event_type: str, **kwargs: Any) -> None:
        if event_sink is None:
            return
        from homemaster.events.runtime_events import RuntimeEvent
        payload = kwargs.pop("payload", {})
        event_sink.emit(RuntimeEvent(
            turn_index=0, event_type=event_type, run_id=ctx.run_id, payload=payload, **kwargs,
        ))

    for recovery_round in range(max_recovery_attempts + 1):
        _emit("recovery_started", payload={"round": recovery_round})

        execution_result = execute_stage_05_plan(
            current_ctx.planning_context,
            current_plan,
            decision_provider=decision_provider,
            initial_state=current_state,
            event_sink=event_sink,
            run_id=ctx.run_id,
        )

        if execution_result.final_state.task_status != "failed":
            # Success or needs_user_input — no recovery needed
            if recovery_round > 0:
                logger.info(
                    "[%s] recovery succeeded after %d attempts  final_status=%s",
                    ctx.run_id,
                    recovery_round,
                    execution_result.final_state.task_status,
                )
            _emit("recovery_completed", payload={
                "round": recovery_round,
                "final_status": execution_result.final_state.task_status,
            })
            break

        # Failed — try recovery
        if recovery_round >= max_recovery_attempts:
            recovery_attempts.append({
                "round": recovery_round,
                "action": "finish_failed",
                "reason": "max recovery attempts exceeded",
            })
            logger.warning(
                "[%s] recovery exhausted after %d attempts  final_status=failed",
                ctx.run_id,
                recovery_round + 1,
            )
            break

        try:
            recovery_result = generate_recovery_decision(
                execution_result.final_state,
                execution_result.failure_records,
                provider,
            )
            decision = recovery_result.decision
        except RecoveryDecisionGenerationError as exc:
            recovery_attempts.append({
                "round": recovery_round,
                "action": "finish_failed",
                "reason": f"recovery decision generation failed: {exc}",
            })
            logger.warning(
                "[%s] recovery decision generation failed at round %d: %s",
                ctx.run_id,
                recovery_round,
                exc,
            )
            break

        recovery_attempts.append({
            "round": recovery_round,
            "action": decision.action,
            "reason": decision.reason,
        })
        _emit("recovery_decision_generated", payload={
            "round": recovery_round,
            "action": decision.action,
            "reason": decision.reason,
        })
        logger.info(
            "[%s] recovery round %d  action=%s  reason=%s",
            ctx.run_id,
            recovery_round,
            decision.action,
            decision.reason,
        )

        # Dispatch recovery action
        if decision.action == "finish_failed":
            break

        # legacy_compat_only: ask_user is a legacy recovery action.
        # AgentRuntime does not expose ask_user — Mimo must act or finish.
        if decision.action == "ask_user":
            execution_result.final_state.task_status = "needs_user_input"
            break

        if decision.action in ("retry_step", "reobserve"):
            # Reset the failed subtask and re-execute with the same plan
            failed_subtask_id = _find_failed_subtask_id(execution_result)
            if failed_subtask_id:
                current_state = reset_subtask_to_pending(
                    execution_result.final_state, failed_subtask_id
                )
            else:
                current_state = ExecutionState(
                    task_status="running",
                    user_location="user_start",
                    current_location="robot_start",
                )
            continue

        if decision.action == "retrieve_again":
            # Inject negative evidence and re-run Stage03 → Stage04 → replan
            neg_evidence = list(execution_result.final_state.negative_evidence)
            current_ctx = current_ctx.with_updates(negative_evidence=neg_evidence)
            registry = current_ctx.registry
            if registry is not None:
                current_ctx = registry.get_stage("stage03").execute(current_ctx)
                current_ctx = registry.get_stage("stage04").execute(current_ctx)
            current_plan = _replan(current_ctx, config_path, provider_name)
            current_state = ExecutionState(
                task_status="running",
                user_location="user_start",
                current_location="robot_start",
            )
            continue

        if decision.action == "replan":
            # Re-plan only (keep Stage03/04 outputs)
            current_plan = _replan(current_ctx, config_path, provider_name)
            current_state = ExecutionState(
                task_status="running",
                user_location="user_start",
                current_location="robot_start",
            )
            continue

    assert execution_result is not None
    if execution_result.final_state.task_status == "failed":
        _emit("recovery_failed", payload={
            "round": len(recovery_attempts),
            "final_status": "failed",
        })
    return execution_result, recovery_attempts


def _find_failed_subtask_id(result: Stage05ExecutionResult) -> str | None:
    """Return the subtask_id of the last failure record, or None."""
    if not result.failure_records:
        return None
    return result.failure_records[-1].subtask_id


def _replan(
    ctx: PipelineContext,
    config_path: str,
    provider_name: str,
) -> OrchestrationPlan:
    """Generate a new orchestration plan from the current planning context."""
    from homemaster.pipeline.stage_runtime import run_stage05_plan

    return run_stage05_plan(
        context=ctx.planning_context,
        config_path=config_path,
        provider_name=provider_name,
    )
