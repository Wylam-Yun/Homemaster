"""Evidence helpers for verification-facing interfaces."""

from __future__ import annotations

from typing import Any

from task_brain.domain import (
    Observation,
    RobotRuntimeState,
    TaskNegativeEvidence,
    VerificationEvidence,
)


def build_verification_evidence(
    observation: Observation | None,
    robot_runtime_state: RobotRuntimeState | None,
    execution_result: dict[str, Any] | None = None,
    task_negative_evidence: list[TaskNegativeEvidence] | None = None,
) -> VerificationEvidence:
    """Build verification evidence without exposing raw world payloads."""
    return VerificationEvidence(
        observation=observation,
        execution_result=execution_result,
        robot_runtime_state=robot_runtime_state,
        task_negative_evidence=task_negative_evidence or [],
    )
