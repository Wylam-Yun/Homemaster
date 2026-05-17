"""Stage 05 mock execution loop."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from homemaster.stages.verifier import build_verification_input, verify_skill_result

# ---------------------------------------------------------------------------
# Legacy SkillRegistry (moved from root-level skill_registry.py)
# ---------------------------------------------------------------------------

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SkillManifest(BaseModel):
    """Small manifest shown to Mimo when choosing the next action skill."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    selectable_by_mimo: bool
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_skill_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("skill name must not be blank")
        if not _SKILL_NAME_RE.match(value):
            raise ValueError(
                f"skill name {value!r} must be lowercase alphanumeric with underscores "
                "(e.g. 'navigation', 'my_skill')"
            )
        return value


class SkillInputValidationError(RuntimeError):
    """Raised when a Stage 05 skill call is structurally invalid."""

    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


SkillExecutor = Callable[
    [StepDecision, Subtask, ExecutionState],
    ModuleExecutionResult,
]


class SkillRegistry:
    """Registry for Stage 05 skills: manifest + validator + executor."""

    def __init__(self) -> None:
        self._manifests: dict[str, SkillManifest] = {}
        self._validators: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._executors: dict[str, SkillExecutor] = {}

    def register(
        self,
        manifest: SkillManifest,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        executor: SkillExecutor | None = None,
    ) -> None:
        name = manifest.name
        if name in self._manifests:
            raise ValueError(f"skill {name!r} already registered")
        self._manifests[name] = manifest
        self._validators[name] = validator
        if executor is not None:
            self._executors[name] = executor

    def get_manifest(self, name: str) -> SkillManifest | None:
        return self._manifests.get(name)

    def get_all_manifests(self) -> dict[str, SkillManifest]:
        return dict(self._manifests)

    def get_action_manifests(self) -> list[SkillManifest]:
        return [m for m in self._manifests.values() if m.selectable_by_mimo]

    def get_action_names(self) -> list[str]:
        return [m.name for m in self._manifests.values() if m.selectable_by_mimo]

    def get_prompt_payload(self, *, action_only: bool = True) -> list[dict[str, Any]]:
        manifests = self.get_action_manifests() if action_only else list(self._manifests.values())
        return [m.model_dump(mode="json") for m in manifests]

    def validate_input(self, skill_name: str, skill_input: dict[str, Any]) -> dict[str, Any]:
        manifest = self._manifests.get(skill_name)
        if manifest is None:
            raise SkillInputValidationError(
                error_type="unknown_skill",
                message=f"unknown Stage 05 skill: {skill_name}",
            )
        if not manifest.selectable_by_mimo:
            raise SkillInputValidationError(
                error_type="skill_not_selectable",
                message=f"skill {skill_name} is not selectable by Mimo",
            )
        if not isinstance(skill_input, dict):
            raise SkillInputValidationError(
                error_type="skill_input_not_object",
                message="skill_input must be a JSON object",
            )
        validator = self._validators.get(skill_name)
        if validator is None:
            raise SkillInputValidationError(
                error_type="skill_not_supported",
                message=f"skill {skill_name} has no validator",
            )
        return validator(skill_input)

    def execute(
        self,
        skill_name: str,
        decision: StepDecision,
        subtask: Subtask,
        state: ExecutionState,
    ) -> ModuleExecutionResult:
        executor = self._executors.get(skill_name)
        if executor is None:
            raise SkillInputValidationError(
                error_type="skill_not_executable",
                message=f"skill {skill_name} has no executor registered",
            )
        return executor(decision, subtask, state)

    def has_executor(self, skill_name: str) -> bool:
        return skill_name in self._executors


def _run_mock_navigation(
    decision: StepDecision,
    subtask: Subtask,
    state: ExecutionState,
) -> ModuleExecutionResult:
    """Simulated navigation: returns location and visibility observation."""
    skill_input = decision.skill_input
    goal_type = skill_input.get("goal_type")
    observation: dict[str, object] = {}
    if goal_type == "find_object":
        target_object = str(skill_input.get("target_object") or subtask.target_object or "")
        if skill_input.get("force_no_object"):
            observation["target_object_visible"] = False
            observation["visible_objects"] = []
        else:
            observation["target_object_visible"] = True
            observation["visible_objects"] = [target_object]
        observation["current_location"] = subtask.room_hint or state.current_location
    elif goal_type == "go_to_location":
        target_location = str(skill_input.get("target_location") or state.user_location or "")
        observation["current_location"] = target_location
    return ModuleExecutionResult(
        skill="navigation",
        status="success",
        skill_output={"goal_type": goal_type, "navigated": True},
        observation=observation,
    )


def _run_mock_operation(
    decision: StepDecision,
    subtask: Subtask,
    state: ExecutionState,
) -> ModuleExecutionResult:
    """Simulated operation: returns manipulation result based on intent."""
    target = str(decision.skill_input.get("target_object") or subtask.target_object or "")
    intent = subtask.intent
    observation: dict[str, object] = {}
    if any(term in intent for term in ("拿", "取", "抓", "拾")):
        observation["held_object"] = target
    elif any(term in intent for term in ("放", "交付", "递", "给")):
        observation["held_object"] = None
        observation["delivered_object"] = target
        observation["delivery_complete"] = True
    return ModuleExecutionResult(
        skill="operation",
        status="success",
        skill_output={
            "vla_instruction": f"根据当前观察执行：{intent}",
            "planned_atomic_actions": ["operate"],
        },
        observation=observation,
    )


def build_default_skill_registry() -> SkillRegistry:
    """Build the default SkillRegistry with navigation, operation, verification."""
    registry = SkillRegistry()

    registry.register(
        manifest=SkillManifest(
            name="navigation",
            description="根据目标物名称寻找物体，或根据具体位置描述移动到该位置。",
            selectable_by_mimo=True,
            input_schema={
                "goal_type": "find_object | go_to_location",
                "target_object": "目标物名称；goal_type=find_object 时必填",
                "target_location": "位置描述；goal_type=go_to_location 时必填",
                "room_hint": "可选房间提示",
                "anchor_hint": "可选锚点提示",
                "subtask_id": "当前子任务 id",
                "subtask_intent": "当前子任务意图",
            },
        ),
        validator=_validate_navigation_input,
        executor=_run_mock_navigation,
    )

    registry.register(
        manifest=SkillManifest(
            name="operation",
            description="根据当前操作子任务和观察，生成 VLA 指令并执行拿起、放下或交付类操作。",
            selectable_by_mimo=True,
            input_schema={
                "subtask_id": "当前子任务 id",
                "subtask_intent": "当前操作意图",
                "target_object": "可选目标物",
                "recipient": "可选接收对象",
                "observation": "当前结构化观察",
            },
        ),
        validator=_validate_operation_input,
        executor=_run_mock_operation,
    )

    registry.register(
        manifest=SkillManifest(
            name="verification",
            description="由程序自动调用，验证当前子任务或整个任务是否完成。",
            selectable_by_mimo=False,
            input_schema={
                "scope": "subtask | task",
                "success_criteria": "需要验证的完成条件",
                "observation": "最近一次结构化观察",
                "image_input": "默认 disabled 的图片输入占位",
            },
        ),
        validator=lambda skill_input: dict(skill_input),
    )

    return registry


_default_registry: SkillRegistry | None = None


def get_default_skill_registry() -> SkillRegistry:
    """Return the module-level default SkillRegistry (lazy init)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_skill_registry()
    return _default_registry


def get_stage_05_skill_manifests() -> dict[str, SkillManifest]:
    return get_default_skill_registry().get_all_manifests()


def get_stage_05_mimo_action_manifests() -> list[SkillManifest]:
    return get_default_skill_registry().get_action_manifests()


def get_stage_05_skill_prompt_payload(*, action_only: bool = True) -> list[dict[str, Any]]:
    return get_default_skill_registry().get_prompt_payload(action_only=action_only)


def validate_skill_input(skill_name: str, skill_input: dict[str, Any]) -> dict[str, Any]:
    """Validate only the stable first-version Stage 05 skill input shape."""
    return get_default_skill_registry().validate_input(skill_name, skill_input)


def _validate_navigation_input(skill_input: dict[str, Any]) -> dict[str, Any]:
    goal_type = _required_text(skill_input, "goal_type")
    if goal_type not in {"find_object", "go_to_location"}:
        raise SkillInputValidationError(
            error_type="invalid_navigation_goal_type",
            message="navigation.goal_type must be find_object or go_to_location",
        )
    if goal_type == "find_object":
        _required_text(skill_input, "target_object")
    if goal_type == "go_to_location":
        _required_text(skill_input, "target_location")
    return dict(skill_input)


def _validate_operation_input(skill_input: dict[str, Any]) -> dict[str, Any]:
    _required_text(skill_input, "subtask_intent")
    return dict(skill_input)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillInputValidationError(
            error_type="missing_skill_input_field",
            message=f"skill_input.{key} must be a non-empty string",
        )
    return value.strip()


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
    event_sink: Any = None,  # EventSink | None
    run_id: str = "",
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

    def _emit(event_type: str, **kwargs: Any) -> None:
        if event_sink is None:
            return
        from homemaster.events.runtime_events import RuntimeEvent
        payload = kwargs.pop("payload", {})
        event_sink.emit(RuntimeEvent(
            turn_index=0, event_type=event_type, run_id=run_id,
            payload=payload, **kwargs,
        ))

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
        _emit("subtask_started", subtask_id=subtask.id, payload={"subtask_id": subtask.id})

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
            _emit("subtask_failed", subtask_id=subtask.id,
                  payload={"reason": str(exc)})
            break

        step_decisions.append(decision)
        _emit("step_decision_generated", subtask_id=subtask.id,
              skill_name=decision.selected_skill,
              payload={"skill": decision.selected_skill})
        try:
            _emit("skill_call_started", subtask_id=subtask.id,
                  skill_name=decision.selected_skill,
                  payload={"skill": decision.selected_skill})
            skill_result = registry.execute(decision.selected_skill, decision, subtask, state)
        except (SkillInputValidationError, Exception) as exc:
            _emit("skill_call_failed", subtask_id=subtask.id,
                  skill_name=decision.selected_skill,
                  payload={"error": str(exc), "error_type": type(exc).__name__})
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
            _emit("subtask_failed", subtask_id=subtask.id,
                  payload={"reason": f"skill_failed: {exc}"})
            break
        _emit("skill_call_completed", subtask_id=subtask.id,
              skill_name=decision.selected_skill,
              payload={"success": True})
        skill_results.append(skill_result)
        state.last_skill_call = decision.model_dump(mode="json")
        state.last_skill_result = skill_result

        _emit("verification_started", subtask_id=subtask.id,
              payload={"skill": decision.selected_skill})
        verification_input = build_verification_input(subtask, skill_result)
        verification_inputs.append(verification_input)
        verification = verify_skill_result(subtask, skill_result)
        verification_results.append(verification)
        state.last_verification_result = verification

        if verification.passed:
            _emit("verification_completed", subtask_id=subtask.id,
                  payload={"passed": True})
            state = mark_subtask_verified(
                state,
                subtask.id,
                verification,
                observation=skill_result.observation,
            )
            _emit("subtask_completed", subtask_id=subtask.id,
                  payload={"verified": True})
            continue

        _emit("verification_failed", subtask_id=subtask.id,
              payload={"passed": False,
                       "reason": verification.failed_reason or "verification failed"})
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
        _emit("subtask_failed", subtask_id=subtask.id,
              payload={"reason": "verification_failed"})
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
