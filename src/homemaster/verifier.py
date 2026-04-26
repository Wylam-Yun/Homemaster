"""Mock Stage 05 verifier for structure-only execution tests."""

from __future__ import annotations

from typing import Any

from homemaster.contracts import ModuleExecutionResult, Subtask, VerificationResult


def build_verification_input(
    subtask: Subtask,
    skill_result: ModuleExecutionResult,
    *,
    scope: str = "subtask",
) -> dict[str, Any]:
    return {
        "scope": scope,
        "subtask_id": subtask.id,
        "subtask_intent": subtask.intent,
        "success_criteria": subtask.success_criteria,
        "observation": skill_result.observation,
        "image_input": skill_result.image_input.model_dump(mode="json"),
    }


def verify_skill_result(
    subtask: Subtask,
    skill_result: ModuleExecutionResult,
) -> VerificationResult:
    """Verify one mock skill result using structured observation only."""

    if skill_result.status != "success":
        return VerificationResult(
            scope="subtask",
            passed=False,
            failed_reason=skill_result.error or "skill did not succeed",
            confidence=0.9,
        )

    observation = skill_result.observation or {}
    target = subtask.target_object
    intent = subtask.intent
    if target and any(term in intent for term in ("找", "寻找", "观察", "查看", "确认", "看")):
        if _target_visible(observation, target):
            return _passed(f"观察到{target}")
        return _failed(f"没有观察到{target}")

    if target and any(term in intent for term in ("拿", "取", "抓", "拾")):
        if observation.get("held_object") == target:
            return _passed(f"已经拿起{target}")
        return _failed(f"未确认已经拿起{target}")

    if target and any(term in intent for term in ("放", "交付", "递", "给")):
        if observation.get("delivered_object") == target or observation.get("delivery_complete"):
            return _passed(f"已经交付{target}")
        return _failed(f"未确认已经交付{target}")

    if any(term in intent for term in ("回到", "到达", "去")):
        if observation.get("current_location"):
            return _passed("已到达目标位置")
        return _failed("未确认当前位置")

    return _passed("结构化 observation 支持该子任务完成")


def _target_visible(observation: dict[str, Any], target: str) -> bool:
    if observation.get("target_object_visible") is True:
        return True
    if observation.get("object_present") is True:
        return True
    visible_objects = observation.get("visible_objects")
    return isinstance(visible_objects, list) and target in visible_objects


def _passed(fact: str) -> VerificationResult:
    return VerificationResult(
        scope="subtask",
        passed=True,
        verified_facts=[fact],
        confidence=0.9,
    )


def _failed(reason: str) -> VerificationResult:
    return VerificationResult(
        scope="subtask",
        passed=False,
        missing_evidence=[reason],
        failed_reason=reason,
        confidence=0.8,
    )
