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
from homemaster.prompt_loader import render
from homemaster.runtime import ProviderConfig
from homemaster.token_budget import MAX_LLM_ATTEMPTS, initial_max_tokens, max_tokens_for_attempt

ORCHESTRATION_RETRY_INSTRUCTION = render("stage_05_orchestration_retry.txt")


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
    return render(
        "stage_05_orchestration_prompt.txt",
        context_json=context_json,
        retry_section=retry_section,
    )


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
