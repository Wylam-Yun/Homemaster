"""Failure record helpers for Stage 05."""

from __future__ import annotations

import time
from typing import Any

from homemaster.contracts import (
    FailureRecord,
    ModuleExecutionResult,
    StepDecision,
    Subtask,
    VerificationResult,
)


def make_failure_record(
    *,
    failure_index: int,
    subtask: Subtask | None,
    failure_type: str,
    failed_reason: str,
    decision: StepDecision | None = None,
    skill_result: ModuleExecutionResult | None = None,
    verification_result: VerificationResult | None = None,
    negative_evidence: list[dict[str, Any]] | None = None,
    retry_count: int = 0,
) -> FailureRecord:
    """Build a structured failure record without writing long-term memory."""

    return FailureRecord(
        failure_id=f"failure-{failure_index}",
        subtask_id=subtask.id if subtask else None,
        subtask_intent=subtask.intent if subtask else None,
        skill=decision.selected_skill if decision else skill_result.skill if skill_result else None,
        failure_type=failure_type,  # type: ignore[arg-type]
        failed_reason=failed_reason,
        skill_input=decision.skill_input if decision else {},
        skill_output=skill_result.skill_output if skill_result else {},
        verification_result=verification_result,
        observation=skill_result.observation if skill_result else None,
        negative_evidence=negative_evidence or [],
        retry_count=retry_count,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        event_memory_candidate={
            "type": failure_type,
            "subtask_id": subtask.id if subtask else None,
            "reason": failed_reason,
            "negative_evidence": negative_evidence or [],
        },
    )
