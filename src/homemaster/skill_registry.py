"""Stage 05 skill registry: manifest, validation, and execution dispatch.

SkillRegistry is the single source of truth for Stage 05 skills.
Skills are registered with manifest + validator + optional executor.
The default registry (build_default_skill_registry) registers
navigation, operation, and verification with their mock handlers.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from homemaster.contracts import (
    ExecutionState,
    ModuleExecutionResult,
    StepDecision,
    Subtask,
)


# ---------------------------------------------------------------------------
# SkillManifest
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


# ---------------------------------------------------------------------------
# SkillExecutor type alias
# ---------------------------------------------------------------------------

SkillExecutor = Callable[
    [StepDecision, Subtask, ExecutionState],
    ModuleExecutionResult,
]


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


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
        """Register a skill with manifest, validator, and optional executor."""
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
        """Return manifests for skills selectable by Mimo."""
        return [m for m in self._manifests.values() if m.selectable_by_mimo]

    def get_action_names(self) -> list[str]:
        """Return names of action skills (selectable by Mimo)."""
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


# ---------------------------------------------------------------------------
# Mock skill handlers (migrated from executor.py)
# ---------------------------------------------------------------------------


def _run_mock_navigation(
    decision: StepDecision,
    subtask: Subtask,
    state: ExecutionState,
) -> ModuleExecutionResult:
    """Mock navigation executor: simulates finding objects or moving to locations."""
    skill_input = decision.skill_input
    goal_type = skill_input.get("goal_type")
    observation: dict[str, object] = {}
    if goal_type == "find_object":
        target_object = str(skill_input.get("target_object") or subtask.target_object or "")
        if skill_input.get("force_no_object"):
            observation.update(
                {
                    "target_object_visible": False,
                    "visible_objects": [],
                    "current_location": state.current_location or subtask.room_hint,
                }
            )
        else:
            observation.update(
                {
                    "target_object_visible": True,
                    "visible_objects": [target_object],
                    "target_object_location": subtask.anchor_hint
                    or subtask.room_hint
                    or skill_input.get("room_hint")
                    or "mock_visible_location",
                    "current_location": subtask.room_hint or state.current_location,
                }
            )
    elif goal_type == "go_to_location":
        target_location = str(skill_input.get("target_location") or state.user_location or "")
        observation.update({"current_location": target_location})
        if target_location:
            observation["user_location"] = state.user_location
    return ModuleExecutionResult(
        skill="navigation",
        status="success",
        skill_output={
            "goal_type": goal_type,
            "navigated": True,
        },
        observation=observation,
    )


def _run_mock_operation(
    decision: StepDecision,
    subtask: Subtask,
    state: ExecutionState,
) -> ModuleExecutionResult:
    """Mock operation executor: simulates pick-up, put-down, delivery actions."""
    target = str(decision.skill_input.get("target_object") or subtask.target_object or "")
    intent = subtask.intent
    observation: dict[str, object] = {}
    planned_actions: list[str] = []
    if any(term in intent for term in ("拿", "取", "抓", "拾")):
        observation["held_object"] = target
        planned_actions = ["approach", "grasp", "lift"]
    elif any(term in intent for term in ("放", "交付", "递", "给")):
        observation["held_object"] = None
        observation["delivered_object"] = target
        observation["delivery_complete"] = True
        planned_actions = ["approach_recipient", "release"]
    else:
        planned_actions = ["operate"]
    return ModuleExecutionResult(
        skill="operation",
        status="success",
        skill_output={
            "vla_instruction": f"根据当前观察执行：{intent}",
            "planned_atomic_actions": planned_actions,
        },
        observation=observation,
    )


# ---------------------------------------------------------------------------
# Default registry factory
# ---------------------------------------------------------------------------


def build_default_skill_registry() -> SkillRegistry:
    """Build the default SkillRegistry with navigation, operation, verification.

    Navigation and operation have mock executors registered.
    Verification has manifest + validator only (auto-invoked by verifier.py).
    """
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
        # no executor — verification is auto-invoked by verifier.py
    )

    return registry


_default_registry: SkillRegistry | None = None


def get_default_skill_registry() -> SkillRegistry:
    """Return the module-level default SkillRegistry (lazy init)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_skill_registry()
    return _default_registry


# ---------------------------------------------------------------------------
# Legacy wrapper functions (backward compatibility)
# ---------------------------------------------------------------------------


def get_stage_05_skill_manifests() -> dict[str, SkillManifest]:
    return get_default_skill_registry().get_all_manifests()


def get_stage_05_mimo_action_manifests() -> list[SkillManifest]:
    return get_default_skill_registry().get_action_manifests()


def get_stage_05_skill_prompt_payload(*, action_only: bool = True) -> list[dict[str, Any]]:
    return get_default_skill_registry().get_prompt_payload(action_only=action_only)


def validate_skill_input(skill_name: str, skill_input: dict[str, Any]) -> dict[str, Any]:
    """Validate only the stable first-version Stage 05 skill input shape."""
    return get_default_skill_registry().validate_input(skill_name, skill_input)


# ---------------------------------------------------------------------------
# Validators (used during registry registration)
# ---------------------------------------------------------------------------


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
