"""Stage 05 skill manifest registry and input validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillManifest(BaseModel):
    """Small manifest shown to Mimo when choosing the next action skill."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["navigation", "operation", "verification"]
    description: str
    selectable_by_mimo: bool
    input_schema: dict[str, Any] = Field(default_factory=dict)


class SkillInputValidationError(RuntimeError):
    """Raised when a Stage 05 skill call is structurally invalid."""

    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def get_stage_05_skill_manifests() -> dict[str, SkillManifest]:
    return {
        "navigation": SkillManifest(
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
        "operation": SkillManifest(
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
        "verification": SkillManifest(
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
    }


def get_stage_05_mimo_action_manifests() -> list[SkillManifest]:
    manifests = get_stage_05_skill_manifests()
    return [manifests["navigation"], manifests["operation"]]


def get_stage_05_skill_prompt_payload(*, action_only: bool = True) -> list[dict[str, Any]]:
    manifests = (
        get_stage_05_mimo_action_manifests()
        if action_only
        else list(get_stage_05_skill_manifests().values())
    )
    return [manifest.model_dump(mode="json") for manifest in manifests]


def validate_skill_input(skill_name: str, skill_input: dict[str, Any]) -> dict[str, Any]:
    """Validate only the stable first-version Stage 05 skill input shape."""

    manifests = get_stage_05_skill_manifests()
    manifest = manifests.get(skill_name)
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
    if skill_name == "navigation":
        return _validate_navigation_input(skill_input)
    if skill_name == "operation":
        return _validate_operation_input(skill_input)
    raise SkillInputValidationError(
        error_type="skill_not_supported",
        message=f"skill {skill_name} is not supported as an action skill",
    )


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
