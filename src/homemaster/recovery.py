"""Stage 05 recovery decision generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from homemaster.contracts import ExecutionState, FailureRecord, RecoveryDecision
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.runtime import ProviderConfig
from homemaster.token_budget import MAX_LLM_ATTEMPTS, initial_max_tokens, max_tokens_for_attempt

RECOVERY_RETRY_INSTRUCTION = """上一次输出没有通过 RecoveryDecision 校验。
请修正为严格 JSON object。
只包含 action、reason、failure_record_ids、should_retrieve_again、should_replan、
ask_user_question、final_failed_reason。
action 只能是 retry_step、reobserve、retrieve_again、replan、ask_user、finish_failed。
不要输出 switch_target 或 next_target_id。"""


class RecoveryDecisionGenerationError(RuntimeError):
    """Raised when Mimo cannot produce a valid recovery decision."""

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
class RecoveryDecisionGenerationResult:
    decision: RecoveryDecision
    prompt: str
    raw_response: str
    parsed_json: dict[str, Any]
    provider: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]


def build_recovery_prompt(
    state: ExecutionState,
    failure_records: list[FailureRecord],
    *,
    retry_feedback: str | None = None,
) -> str:
    state_json = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)
    failure_json = json.dumps(
        [record.model_dump(mode="json") for record in failure_records],
        ensure_ascii=False,
        indent=2,
    )
    retry_section = f"\n\n{retry_feedback}" if retry_feedback else ""
    return f"""你是 HomeMaster V1.2 的 Stage05 恢复决策组件。

目标：根据 ExecutionState 和 FailureRecord 列表，决定下一步恢复动作。

必须只输出一个 JSON object。
不要输出 Markdown、解释、代码块或思考过程。

RecoveryDecision schema:
{{
  "action": "retry_step | reobserve | retrieve_again | replan | ask_user | finish_failed",
  "reason": "字符串或 null",
  "failure_record_ids": ["相关 failure id"],
  "should_retrieve_again": false,
  "should_replan": false,
  "ask_user_question": "字符串或 null",
  "final_failed_reason": "字符串或 null"
}}

边界:
- 如果当前 grounded target 已经验证失败，需要换目标时，action 使用 retrieve_again。
- 不允许直接编造新的目标 id 或目标切换字段。
- replan 必须考虑 FailureRecord 和 negative evidence，避免重复失败动作。

ExecutionState:
{state_json}

FailureRecord list:
{failure_json}

只输出 JSON object:{retry_section}"""


def generate_recovery_decision(
    state: ExecutionState,
    failure_records: list[FailureRecord],
    provider: ProviderConfig,
    *,
    client: httpx.Client | None = None,
    max_tokens: int = initial_max_tokens("stage_05_recovery"),
) -> RecoveryDecisionGenerationResult:
    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    try:
        for attempt_index in range(1, MAX_LLM_ATTEMPTS + 1):
            prompt = build_recovery_prompt(
                state,
                failure_records,
                retry_feedback=RECOVERY_RETRY_INSTRUCTION if attempt_index > 1 else None,
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
                decision = RecoveryDecision.model_validate(response.json_payload)
            except (LLMClientError, ValidationError, ValueError) as exc:
                attempt.update(
                    {
                        "passed": False,
                        "error_type": "recovery_schema_error"
                        if isinstance(exc, ValidationError)
                        else getattr(exc, "error_type", type(exc).__name__),
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
            return RecoveryDecisionGenerationResult(
                decision=decision,
                prompt=prompt,
                raw_response=response.content,
                parsed_json=response.json_payload,
                provider=response.public_summary(),
                attempts=tuple(attempts),
            )
    finally:
        llm_client.close()

    raise RecoveryDecisionGenerationError(
        error_type=str(attempts[-1].get("error_type", "recovery_generation_failed"))
        if attempts
        else "recovery_generation_failed",
        message=str(attempts[-1].get("message", "Mimo failed to generate recovery")),
        attempts=attempts,
    )
