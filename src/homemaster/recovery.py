"""Stage 05 recovery decision generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from homemaster.contracts import ExecutionState, FailureRecord, RecoveryDecision
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.prompt_loader import render
from homemaster.runtime import ProviderConfig
from homemaster.token_budget import MAX_LLM_ATTEMPTS, initial_max_tokens, max_tokens_for_attempt

RECOVERY_RETRY_INSTRUCTION = render("stage_05_recovery_retry.txt")


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
    return render(
        "stage_05_recovery_prompt.txt",
        state_json=state_json,
        failure_json=failure_json,
        retry_section=retry_section,
    )


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
