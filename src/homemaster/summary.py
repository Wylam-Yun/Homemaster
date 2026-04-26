"""Task summary generation for Stage 06."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from homemaster.contracts import EvidenceBundle, ExecutionState, TaskCard, TaskSummary
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.runtime import ProviderConfig

SUMMARY_RETRY_INSTRUCTION = """上一次输出没有通过 TaskSummary 校验。
请修正为严格 JSON object，只包含 result、confirmed_facts、unconfirmed_facts、
recovery_attempts、user_reply、failure_summary、evidence_refs。
不要编造 evidence_refs；只能使用输入中列出的 evidence_id。"""


class TaskSummaryGenerationError(RuntimeError):
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
class TaskSummaryGenerationResult:
    summary: TaskSummary
    prompt: str
    raw_response: str
    parsed_json: dict[str, Any]
    provider: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]


def build_task_summary_prompt(
    *,
    task_card: TaskCard,
    execution_state: ExecutionState,
    evidence_bundle: EvidenceBundle,
    retry_feedback: str | None = None,
) -> str:
    task_json = json.dumps(task_card.model_dump(mode="json"), ensure_ascii=False, indent=2)
    state_json = json.dumps(
        execution_state.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    evidence_json = json.dumps(
        evidence_bundle.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    retry_section = f"\n\n{retry_feedback}" if retry_feedback else ""
    return f"""你是 HomeMaster V1.2 的 Stage06 任务总结组件。

目标：只根据输入证据生成 TaskSummary JSON，给用户和开发者阅读。
你不负责长期记忆写回，不能决定 object_memory 或 fact_memory 写什么。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造没有证据的成功结果。
不要编造 evidence_refs，只能引用 EvidenceBundle 里已有的 evidence_id。

TaskSummary schema:
{{
  "result": "success | failed | needs_user",
  "confirmed_facts": ["由验证证据支持的事实"],
  "unconfirmed_facts": ["没有被验证或失败的事实"],
  "recovery_attempts": ["发生过的恢复尝试，没有则 []"],
  "user_reply": "给用户看的简短回复或 null",
  "failure_summary": "失败摘要或 null",
  "evidence_refs": ["引用 EvidenceBundle.evidence_refs 中的 evidence_id"]
}}

TaskCard:
{task_json}

ExecutionState:
{state_json}

EvidenceBundle:
{evidence_json}

只输出 JSON object:{retry_section}"""


def generate_task_summary(
    *,
    task_card: TaskCard,
    execution_state: ExecutionState,
    evidence_bundle: EvidenceBundle,
    provider: ProviderConfig,
    client: httpx.Client | None = None,
    max_tokens: int = 2048,
) -> TaskSummaryGenerationResult:
    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    first_prompt = build_task_summary_prompt(
        task_card=task_card,
        execution_state=execution_state,
        evidence_bundle=evidence_bundle,
    )
    try:
        for attempt_index in range(1, 4):
            prompt = build_task_summary_prompt(
                task_card=task_card,
                execution_state=execution_state,
                evidence_bundle=evidence_bundle,
                retry_feedback=SUMMARY_RETRY_INSTRUCTION if attempt_index > 1 else None,
            )
            attempt: dict[str, Any] = {"attempt": attempt_index, "prompt": prompt}
            try:
                response = llm_client.complete_json(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                summary = TaskSummary.model_validate(response.json_payload)
                _validate_summary_evidence_refs(summary, evidence_bundle)
            except (LLMClientError, ValueError) as exc:
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
                    "summary": summary.model_dump(mode="json"),
                    "provider": response.public_summary(),
                }
            )
            attempts.append(attempt)
            return TaskSummaryGenerationResult(
                summary=summary,
                prompt=prompt,
                raw_response=response.content,
                parsed_json=response.json_payload,
                provider=response.public_summary(),
                attempts=tuple(attempts),
            )
    finally:
        llm_client.close()

    raise TaskSummaryGenerationError(
        error_type=str(attempts[-1].get("error_type", "summary_generation_failed"))
        if attempts
        else "summary_generation_failed",
        message=str(attempts[-1].get("message", "Mimo failed to generate summary"))
        if attempts
        else "Mimo failed to generate summary",
        attempts=attempts or [{"attempt": 1, "prompt": first_prompt}],
    )


def _validate_summary_evidence_refs(
    summary: TaskSummary,
    evidence_bundle: EvidenceBundle,
) -> None:
    allowed = {ref.evidence_id for ref in evidence_bundle.evidence_refs}
    unknown = [item for item in summary.evidence_refs if item not in allowed]
    if unknown:
        raise ValueError(f"summary contains unknown evidence_refs: {unknown}")
