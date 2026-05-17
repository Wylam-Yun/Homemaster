"""Stage 05 execution-time skill selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from homemaster.contracts import ExecutionState, PlanningContext, StepDecision, Subtask
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.prompt_loader import render
from homemaster.runtime import ProviderConfig
from homemaster.stages.executor import (
    SkillInputValidationError,
    get_default_skill_registry,
    validate_skill_input,
)
from homemaster.token_budget import MAX_LLM_ATTEMPTS, initial_max_tokens, max_tokens_for_attempt


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
    registry = get_default_skill_registry()
    action_names = registry.get_action_names()
    names_str = " | ".join(action_names)

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
        registry.get_prompt_payload(action_only=True),
        ensure_ascii=False,
        indent=2,
    )
    retry_section = f"\n\n{retry_feedback}" if retry_feedback else ""
    return render(
        "stage_05_step_decision_prompt.txt",
        names_str=names_str,
        subtask_json=subtask_json,
        state_json=state_json,
        context_json=context_json,
        skills_json=skills_json,
        retry_section=retry_section,
    )


def build_retry_instruction() -> str:
    """Build dynamic retry instruction from registry action names."""
    registry = get_default_skill_registry()
    action_names = registry.get_action_names()
    names_str = "、".join(action_names)
    return render("stage_05_step_decision_retry.txt", names_str=names_str)


def generate_step_decision(
    subtask: Subtask,
    state: ExecutionState,
    context: PlanningContext,
    provider: ProviderConfig,
    *,
    client: httpx.Client | None = None,
    max_tokens: int = initial_max_tokens("stage_05_step_decision"),
) -> StepDecisionGenerationResult:
    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    try:
        for attempt_index in range(1, MAX_LLM_ATTEMPTS + 1):
            prompt = build_step_decision_prompt(
                subtask,
                state,
                context,
                retry_feedback=build_retry_instruction() if attempt_index > 1 else None,
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
