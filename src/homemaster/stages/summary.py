"""Task summary generation for Stage 06."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from homemaster.contracts import EvidenceBundle, ExecutionState, TaskCard, TaskSummary
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.prompt_loader import render
from homemaster.runtime import ProviderConfig
from homemaster.token_budget import MAX_LLM_ATTEMPTS, initial_max_tokens, max_tokens_for_attempt

SUMMARY_RETRY_INSTRUCTION = render("stage_06_summary_retry.txt")


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
    return render(
        "stage_06_summary_prompt.txt",
        task_json=task_json,
        state_json=state_json,
        evidence_json=evidence_json,
        retry_section=retry_section,
    )


def generate_task_summary(
    *,
    task_card: TaskCard,
    execution_state: ExecutionState,
    evidence_bundle: EvidenceBundle,
    provider: ProviderConfig,
    client: httpx.Client | None = None,
    max_tokens: int = initial_max_tokens("stage_06_summary"),
) -> TaskSummaryGenerationResult:
    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    first_prompt = build_task_summary_prompt(
        task_card=task_card,
        execution_state=execution_state,
        evidence_bundle=evidence_bundle,
    )
    try:
        for attempt_index in range(1, MAX_LLM_ATTEMPTS + 1):
            prompt = build_task_summary_prompt(
                task_card=task_card,
                execution_state=execution_state,
                evidence_bundle=evidence_bundle,
                retry_feedback=SUMMARY_RETRY_INSTRUCTION if attempt_index > 1 else None,
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
