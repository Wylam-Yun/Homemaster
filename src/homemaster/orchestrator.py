"""Stage 05 high-level orchestration plan generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from homemaster.contracts import OrchestrationPlan, PlanningContext
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.orchestration_validator import (
    Stage05ValidationError,
    validate_orchestration_payload,
)
from homemaster.runtime import ProviderConfig
from homemaster.token_budget import MAX_LLM_ATTEMPTS, initial_max_tokens, max_tokens_for_attempt

ORCHESTRATION_RETRY_INSTRUCTION = """上一次输出没有通过 OrchestrationPlan 校验。
请修正为严格 JSON object，只包含 goal、subtasks、confidence。
不要输出 selected_target、memory_id、candidate 字段或 skill 名称。
每个 subtask 必须有 id、intent、success_criteria。
可选字段只有 target_object、recipient、room_hint、anchor_hint、depends_on。"""


class OrchestrationGenerationError(RuntimeError):
    """Raised when Mimo cannot produce a valid OrchestrationPlan."""

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
class OrchestrationGenerationResult:
    plan: OrchestrationPlan
    prompt: str
    raw_response: str
    parsed_json: dict[str, Any]
    provider: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]


def build_orchestration_prompt(
    context: PlanningContext,
    *,
    retry_feedback: str | None = None,
) -> str:
    context_json = json.dumps(context.model_dump(mode="json"), ensure_ascii=False, indent=2)
    retry_section = f"\n\n{retry_feedback}" if retry_feedback else ""
    return f"""你是 HomeMaster V1.2 的 Stage05 高层子任务编排组件。

目标：根据 PlanningContext 生成一个 OrchestrationPlan JSON。
你只负责拆成高层子任务 intent，不负责选择 skill，不负责导航或操作执行。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。

OrchestrationPlan schema:
{{
  "goal": "非空字符串，整体任务目标",
  "subtasks": [
    {{
      "id": "稳定、唯一、非空字符串",
      "intent": "高层子任务意图，例如 找到水杯 / 拿起水杯 / 回到用户位置 / 放下水杯",
      "target_object": "字符串或 null",
      "recipient": "字符串或 null",
      "room_hint": "字符串或 null",
      "anchor_hint": "字符串或 null",
      "success_criteria": ["至少一个可验证完成条件"],
      "depends_on": ["依赖的 subtask id"]
    }}
  ],
  "confidence": 0.0
}}

边界:
- 高层 subtask 不写 selected_skill、skill、module、navigation、operation 或 verification。
- 不输出 selected_target、memory_id、candidate_id、selected_candidate_id、switch_candidate。
- 不伪造 PlanningContext 中没有的 memory target。
- 如果 PlanningContext.selected_target 为 null，不要假装有可靠记忆；先规划探索/寻找/观察或追问。
- 如果任务是取物交付给用户，通常需要覆盖：
  找到目标物、拿起目标物、回到用户位置、放下/交付目标物、确认完成。
- 找用户首版使用 runtime 里记录的 user_location，不新增 find_user skill。
- 可以用自然语言描述子任务，不要把原子动作当作独立执行接口。

PlanningContext:
{context_json}

只输出 JSON object:{retry_section}"""


def generate_orchestration_plan(
    context: PlanningContext,
    provider: ProviderConfig,
    *,
    client: httpx.Client | None = None,
    max_tokens: int = initial_max_tokens("stage_05_orchestration"),
) -> OrchestrationGenerationResult:
    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    first_prompt = build_orchestration_prompt(context)
    try:
        for attempt_index in range(1, MAX_LLM_ATTEMPTS + 1):
            prompt = build_orchestration_prompt(
                context,
                retry_feedback=ORCHESTRATION_RETRY_INSTRUCTION
                if attempt_index > 1
                else None,
            )
            attempt_max_tokens = max_tokens_for_attempt(max_tokens, attempt_index)
            attempt: dict[str, Any] = {
                "attempt": attempt_index,
                "prompt": prompt,
                "max_tokens": attempt_max_tokens,
            }
            try:
                response = llm_client.complete_json(
                    prompt,
                    max_tokens=attempt_max_tokens,
                    temperature=0.0,
                )
                plan = validate_orchestration_payload(response.json_payload)
            except (LLMClientError, Stage05ValidationError, ValueError) as exc:
                attempt.update(
                    {
                        "passed": False,
                        "error_type": getattr(exc, "error_type", type(exc).__name__),
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
                    "plan": plan.model_dump(mode="json"),
                    "provider": response.public_summary(),
                }
            )
            attempts.append(attempt)
            return OrchestrationGenerationResult(
                plan=plan,
                prompt=prompt,
                raw_response=response.content,
                parsed_json=response.json_payload,
                provider=response.public_summary(),
                attempts=tuple(attempts),
            )
    finally:
        llm_client.close()

    raise OrchestrationGenerationError(
        error_type=str(attempts[-1].get("error_type", "orchestration_generation_failed"))
        if attempts
        else "orchestration_generation_failed",
        message=str(attempts[-1].get("message", "Mimo failed to generate a plan"))
        if attempts
        else "Mimo failed to generate a plan",
        attempts=attempts or [{"attempt": 1, "prompt": first_prompt}],
    )
