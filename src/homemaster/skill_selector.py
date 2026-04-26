"""Stage 05 execution-time skill selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from homemaster.contracts import ExecutionState, PlanningContext, StepDecision, Subtask
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.runtime import ProviderConfig
from homemaster.skill_registry import (
    SkillInputValidationError,
    get_stage_05_skill_prompt_payload,
    validate_skill_input,
)

STEP_DECISION_RETRY_INSTRUCTION = """上一次输出没有通过 StepDecision 校验。
请修正为严格 JSON object，只包含 subtask_id、selected_skill、skill_input、expected_result、reason。
selected_skill 只能是 navigation 或 operation。
不要选择 verification；verification 由程序自动后置调用。"""


class StepDecisionGenerationError(RuntimeError):
    """Raised when Mimo cannot select a valid next action skill."""

    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.error_type = error_type
        self.message = message
        self.attempts = attempts or []
        super().__init__(message)


@dataclass(frozen=True)
class StepDecisionGenerationResult:
    decision: StepDecision
    prompt: str
    raw_response: str
    parsed_json: dict[str, Any]
    provider: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]


def build_step_decision_prompt(
    subtask: Subtask,
    state: ExecutionState,
    context: PlanningContext,
    *,
    retry_feedback: str | None = None,
) -> str:
    subtask_json = json.dumps(subtask.model_dump(mode="json"), ensure_ascii=False, indent=2)
    state_json = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)
    context_json = json.dumps(
        {
            "task_card": context.task_card.model_dump(mode="json"),
            "selected_target": (
                context.selected_target.model_dump(mode="json")
                if context.selected_target
                else None
            ),
            "runtime_state_summary": context.runtime_state_summary,
            "planning_notes": context.planning_notes,
        },
        ensure_ascii=False,
        indent=2,
    )
    skills_json = json.dumps(
        get_stage_05_skill_prompt_payload(action_only=True),
        ensure_ascii=False,
        indent=2,
    )
    retry_section = f"\n\n{retry_feedback}" if retry_feedback else ""
    return f"""你是 HomeMaster V1.2 的 Stage05 单步 skill 选择组件。

目标：根据当前 subtask、ExecutionState 和可选 skill manifest，选择下一步 action skill。
只能选择 navigation 或 operation。
不能选择 verification；verification 由程序在 action skill 后自动调用。

必须只输出一个 JSON object。
不要输出 Markdown、解释、代码块或思考过程。

StepDecision schema:
{{
  "subtask_id": "当前 subtask id",
  "selected_skill": "navigation | operation",
  "skill_input": {{}},
  "expected_result": "字符串或 null",
  "reason": "字符串或 null"
}}

当前 subtask:
{subtask_json}

ExecutionState:
{state_json}

PlanningContext 摘要:
{context_json}

可选 action skill manifest:
{skills_json}

规则:
- 找目标物或移动到位置时选择 navigation。
- 拿起、放下、交付等操作时选择 operation。
- 找用户首版使用 ExecutionState.user_location，navigation goal_type 使用 go_to_location。
- operation 的 skill_input 只放当前子任务、目标、接收对象和当前观察；不要输出原子动作序列。

只输出 JSON object:{retry_section}"""


def generate_step_decision(
    subtask: Subtask,
    state: ExecutionState,
    context: PlanningContext,
    provider: ProviderConfig,
    *,
    client: httpx.Client | None = None,
    max_tokens: int = 2048,
) -> StepDecisionGenerationResult:
    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    try:
        for attempt_index in range(1, 4):
            prompt = build_step_decision_prompt(
                subtask,
                state,
                context,
                retry_feedback=STEP_DECISION_RETRY_INSTRUCTION if attempt_index > 1 else None,
            )
            attempt: dict[str, Any] = {"attempt": attempt_index, "prompt": prompt}
            try:
                response = llm_client.complete_json(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                decision = StepDecision.model_validate(response.json_payload)
                if decision.subtask_id != subtask.id:
                    raise SkillInputValidationError(
                        error_type="wrong_subtask_id",
                        message=(
                            f"StepDecision subtask_id {decision.subtask_id!r} "
                            f"does not match current subtask {subtask.id!r}"
                        ),
                    )
                validate_skill_input(decision.selected_skill, decision.skill_input)
            except (LLMClientError, ValidationError, SkillInputValidationError) as exc:
                attempt.update(
                    {
                        "passed": False,
                        "error_type": _decision_error_type(exc),
                        "message": str(exc),
                        "raw_response": getattr(exc, "raw_content", None),
                    }
                )
                attempts.append(attempt)
                continue

            attempt.update(
                {
                    "passed": True,
                    "raw_response": response.content,
                    "json_payload": response.json_payload,
                    "decision": decision.model_dump(mode="json"),
                    "provider": response.public_summary(),
                }
            )
            attempts.append(attempt)
            return StepDecisionGenerationResult(
                decision=decision,
                prompt=prompt,
                raw_response=response.content,
                parsed_json=response.json_payload,
                provider=response.public_summary(),
                attempts=tuple(attempts),
            )
    finally:
        llm_client.close()

    raise StepDecisionGenerationError(
        error_type=str(attempts[-1].get("error_type", "step_decision_failed"))
        if attempts
        else "step_decision_failed",
        message=str(attempts[-1].get("message", "Mimo failed to select a step")),
        attempts=attempts,
    )


def _decision_error_type(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        return "step_schema_error"
    return str(getattr(exc, "error_type", type(exc).__name__))
