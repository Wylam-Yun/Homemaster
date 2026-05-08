"""Stage 05 mock execution loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from homemaster.contracts import (
    ExecutionState,
    FailureRecord,
    ModuleExecutionResult,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
    SubtaskRuntimeState,
    VerificationResult,
)
from homemaster.execution_state import (
    append_failure_record_id,
    mark_subtask_verified,
    next_ready_subtasks,
)
from homemaster.failure_log import make_failure_record
from homemaster.orchestration_validator import validate_orchestration_plan
from homemaster.runtime import (
    RuntimeConfigError,
    get_config_section,
    load_homemaster_config,
)
from homemaster.skill_registry import (
    SkillInputValidationError,
    SkillRegistry,
    get_default_skill_registry,
)
from homemaster.stages.verifier import build_verification_input, verify_skill_result


# P7: executor config with fail-fast validation
def _load_executor_config() -> tuple[int, int]:
    cfg = get_config_section(load_homemaster_config(), "executor")
    if cfg is None:
        return 3, 3
    sm = cfg.get("step_multiplier", 3)
    if not isinstance(sm, int) or sm < 1:
        raise RuntimeConfigError(
            f"executor.step_multiplier must be a positive int, got {sm!r}"
        )
    mms = cfg.get("minimum_max_steps", 3)
    if not isinstance(mms, int) or mms < 1:
        raise RuntimeConfigError(
            f"executor.minimum_max_steps must be a positive int, got {mms!r}"
        )
    return sm, mms


STEP_MULTIPLIER, MINIMUM_MAX_STEPS = _load_executor_config()


class Stage05ExecutionError(RuntimeError):
    """Raised when the Stage 05 executor cannot continue safely."""


class StepDecisionProvider(Protocol):
    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        """Return one action decision for the current subtask."""


class StaticStepDecisionProvider:
    """Small deterministic provider for tests."""

    def __init__(self, decisions: list[StepDecision]) -> None:
        self._decisions = list(decisions)

    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        if not self._decisions:
            raise Stage05ExecutionError("no StepDecision available for static provider")
        return self._decisions.pop(0)


@dataclass(frozen=True)
class Stage05ExecutionResult:
    final_state: ExecutionState
    plan: OrchestrationPlan
    skill_results: list[ModuleExecutionResult]
    verification_results: list[VerificationResult]
    verification_inputs: list[dict[str, object]]
    failure_records: list[FailureRecord]
    step_decisions: list[StepDecision]

    def as_debug_payload(self) -> dict[str, object]:
        return {
            "plan": self.plan.model_dump(mode="json"),
            "final_state": self.final_state.model_dump(mode="json"),
            "step_decisions": [
                decision.model_dump(mode="json") for decision in self.step_decisions
            ],
            "skill_results": [result.model_dump(mode="json") for result in self.skill_results],
            "verification_inputs": self.verification_inputs,
            "verification_results": [
                result.model_dump(mode="json") for result in self.verification_results
            ],
            "failure_records": [
                failure.model_dump(mode="json") for failure in self.failure_records
            ],
        }


def execute_stage_05_plan(
    context: PlanningContext,
    plan: OrchestrationPlan,
    *,
    decision_provider: StepDecisionProvider,
    skill_registry: SkillRegistry | None = None,
    initial_state: ExecutionState | None = None,
    max_steps: int | None = None,
) -> Stage05ExecutionResult:
    """Execute a Stage 05 plan with mock navigation/operation and auto verification."""

    registry = skill_registry or get_default_skill_registry()
    plan = validate_orchestration_plan(plan)
    state = _initial_state_from_plan(plan, initial_state)
    skill_results: list[ModuleExecutionResult] = []
    verification_inputs: list[dict[str, object]] = []
    verification_results: list[VerificationResult] = []
    failure_records: list[FailureRecord] = []
    step_decisions: list[StepDecision] = []
    subtask_by_id = {subtask.id: subtask for subtask in plan.subtasks}
    max_steps = max_steps or max(len(plan.subtasks) * STEP_MULTIPLIER, MINIMUM_MAX_STEPS)

    for _ in range(max_steps):
        if _all_subtasks_verified(state):
            state.task_status = "completed"
            break
        ready = next_ready_subtasks(state)
        if not ready:
            state.task_status = "failed"
            break
        subtask = subtask_by_id[ready[0]]
        _set_subtask_running(state, subtask.id)

        try:
            decision = decision_provider.next_decision(subtask, state, context)
            _validate_decision_for_subtask(decision, subtask, state, registry)
        except (Stage05ExecutionError, SkillInputValidationError) as exc:
            _append_failure(
                state=state,
                failure_records=failure_records,
                subtask=subtask,
                failure_type="precondition_failed",
                failed_reason=str(exc),
                retry_count=state.retry_counts.get(subtask.id, 0),
            )
            _mark_subtask_failed(state, subtask.id)
            state.task_status = "failed"
            break

        step_decisions.append(decision)
        try:
            skill_result = registry.execute(decision.selected_skill, decision, subtask, state)
        except (SkillInputValidationError, Exception) as exc:
            _append_failure(
                state=state,
                failure_records=failure_records,
                subtask=subtask,
                failure_type="skill_failed",
                failed_reason=f"{type(exc).__name__}: {exc}",
                decision=decision,
                retry_count=state.retry_counts.get(subtask.id, 0),
            )
            _mark_subtask_failed(state, subtask.id)
            state.task_status = "failed"
            break
        skill_results.append(skill_result)
        state.last_skill_call = decision.model_dump(mode="json")
        state.last_skill_result = skill_result

        verification_input = build_verification_input(subtask, skill_result)
        verification_inputs.append(verification_input)
        verification = verify_skill_result(subtask, skill_result)
        verification_results.append(verification)
        state.last_verification_result = verification

        if verification.passed:
            state = mark_subtask_verified(
                state,
                subtask.id,
                verification,
                observation=skill_result.observation,
            )
            continue

        negative_evidence = _negative_evidence_for_failure(context, subtask, skill_result)
        _append_failure(
            state=state,
            failure_records=failure_records,
            subtask=subtask,
            failure_type="verification_failed",
            failed_reason=verification.failed_reason or "verification failed",
            decision=decision,
            skill_result=skill_result,
            verification_result=verification,
            negative_evidence=negative_evidence,
            retry_count=state.retry_counts.get(subtask.id, 0),
        )
        _mark_subtask_failed(state, subtask.id)
        state.task_status = "failed"
        break
    else:
        state.task_status = "failed"

    return Stage05ExecutionResult(
        final_state=state,
        plan=plan,
        skill_results=skill_results,
        verification_inputs=verification_inputs,
        verification_results=verification_results,
        failure_records=failure_records,
        step_decisions=step_decisions,
    )


def _initial_state_from_plan(
    plan: OrchestrationPlan,
    initial_state: ExecutionState | None,
) -> ExecutionState:
    if initial_state is not None:
        state = initial_state.model_copy(deep=True)
    else:
        state = ExecutionState(task_status="running")
    if not state.subtasks:
        state.subtasks = [
            SubtaskRuntimeState(subtask_id=subtask.id, depends_on=subtask.depends_on)
            for subtask in plan.subtasks
        ]
    state.task_status = "running"
    return state


def _validate_decision_for_subtask(
    decision: StepDecision,
    subtask: Subtask,
    state: ExecutionState,
    registry: SkillRegistry,
) -> None:
    if decision.subtask_id != subtask.id:
        raise SkillInputValidationError(
            error_type="wrong_subtask_id",
            message=f"StepDecision points to {decision.subtask_id}, expected {subtask.id}",
        )
    registry.validate_input(decision.selected_skill, decision.skill_input)
    if decision.selected_skill == "operation":
        _validate_operation_preconditions(subtask, state)


def _validate_operation_preconditions(subtask: Subtask, state: ExecutionState) -> None:
    intent = subtask.intent
    if subtask.target_object and any(term in intent for term in ("拿", "取", "抓", "拾")):
        if not state.target_object_visible:
            raise SkillInputValidationError(
                error_type="operation_precondition_failed",
                message="operation requires target_object_visible=true before pickup",
            )
    if subtask.target_object and any(term in intent for term in ("放", "交付", "递", "给")):
        if state.held_object != subtask.target_object:
            raise SkillInputValidationError(
                error_type="operation_precondition_failed",
                message="operation requires held_object to match target before delivery",
            )


def _append_failure(
    *,
    state: ExecutionState,
    failure_records: list[FailureRecord],
    subtask: Subtask,
    failure_type: str,
    failed_reason: str,
    decision: StepDecision | None = None,
    skill_result: ModuleExecutionResult | None = None,
    verification_result: VerificationResult | None = None,
    negative_evidence: list[dict[str, object]] | None = None,
    retry_count: int = 0,
) -> FailureRecord:
    failure = make_failure_record(
        failure_index=len(failure_records) + 1,
        subtask=subtask,
        failure_type=failure_type,
        failed_reason=failed_reason,
        decision=decision,
        skill_result=skill_result,
        verification_result=verification_result,
        negative_evidence=list(negative_evidence or []),
        retry_count=retry_count,
    )
    failure_records.append(failure)
    updated = append_failure_record_id(state, subtask.id, failure.failure_id)
    state.failure_record_ids = updated.failure_record_ids
    state.retry_counts = updated.retry_counts
    state.negative_evidence.extend(failure.negative_evidence)
    for index, runtime_subtask in enumerate(updated.subtasks):
        state.subtasks[index] = runtime_subtask
    return failure


def _negative_evidence_for_failure(
    context: PlanningContext,
    subtask: Subtask,
    skill_result: ModuleExecutionResult,
) -> list[dict[str, object]]:
    evidence: dict[str, object] = {
        "subtask_id": subtask.id,
        "reason": "verification_failed",
    }
    if context.selected_target is not None:
        evidence["memory_id"] = context.selected_target.memory_id
        evidence["location_key"] = (
            f"{context.selected_target.room_id}:{context.selected_target.anchor_id}"
        )
    if skill_result.observation:
        evidence["observation"] = skill_result.observation
    return [evidence]


def _set_subtask_running(state: ExecutionState, subtask_id: str) -> None:
    state.current_subtask_id = subtask_id
    for runtime_subtask in state.subtasks:
        if runtime_subtask.subtask_id == subtask_id:
            runtime_subtask.status = "running"
            return


def _mark_subtask_failed(state: ExecutionState, subtask_id: str) -> None:
    for runtime_subtask in state.subtasks:
        if runtime_subtask.subtask_id == subtask_id:
            runtime_subtask.status = "failed"
            return


def _all_subtasks_verified(state: ExecutionState) -> bool:
    return bool(state.subtasks) and all(
        subtask.status == "verified" for subtask in state.subtasks
    )
