"""Deterministic compiler for verified ALFWorld trajectory memories."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from homemaster.benchmarking.alfworld.trajectory_memory import AlfworldTrajectoryRecord
from homemaster.memory.models import (
    ProcedureEntry,
    ProcedureExpect,
    ProcedureRecord,
    ProcedureStep,
    ProcedureSuccess,
    SemanticTarget,
)

COMPILER_VERSION = "alfworld-compiler-v1"


class AlfworldDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["failure", "unknown"]
    source_trajectory_id: str = Field(min_length=1)
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification: str = Field(min_length=1)
    failure_reason: str = Field(min_length=1)
    is_executable: Literal[False] = False


class AlfworldDerivedExperience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: Literal["alfworld"] = "alfworld"
    record_kind: Literal["experience"] = "experience"
    outcome: Literal["success", "failure", "unknown"]
    is_executable: bool
    source_trajectory_id: str = Field(min_length=1)
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str = Field(min_length=1)
    validation_status: Literal["verified", "diagnostic"]
    procedure: ProcedureRecord | None = None
    diagnostic: AlfworldDiagnostic | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> AlfworldDerivedExperience:
        if self.is_executable and (self.procedure is None or self.diagnostic is not None):
            raise ValueError("executable experience requires only a procedure")
        if not self.is_executable and (self.diagnostic is None or self.procedure is not None):
            raise ValueError("non-executable experience requires only a diagnostic")
        return self


def compile_alfworld_trajectory(
    record: AlfworldTrajectoryRecord,
    *,
    source_trace_sha256: str,
) -> AlfworldDerivedExperience:
    """Compile only a hash-verified source record into a safe derived result."""

    if source_trace_sha256 != record.source_trace_sha256:
        raise ValueError("source trace hash mismatch")
    if record.outcome != "success":
        diagnostic = AlfworldDiagnostic(
            outcome=record.outcome,
            source_trajectory_id=record.trajectory_id,
            source_trace_sha256=record.source_trace_sha256,
            classification=record.classification,
            failure_reason=record.failure_reason or "execution_state_uncertain",
        )
        return AlfworldDerivedExperience(
            outcome=record.outcome,
            is_executable=False,
            source_trajectory_id=record.trajectory_id,
            source_trace_sha256=record.source_trace_sha256,
            compiler_version=COMPILER_VERSION,
            validation_status="diagnostic",
            diagnostic=diagnostic,
        )
    if not record.steps:
        raise ValueError("successful trajectory has no compilable steps")
    steps = tuple(_compile_step(item) for item in record.steps)
    procedure = ProcedureRecord(
        name=f"ALFWorld: {record.goal_type}",
        sop_id=f"alfworld-{record.trajectory_id}",
        entry=ProcedureEntry(page_name="ALFWorld environment"),
        steps=steps,
        success=ProcedureSuccess(all_of=("environment.won == true",)),
    )
    result = AlfworldDerivedExperience(
        outcome="success",
        is_executable=True,
        source_trajectory_id=record.trajectory_id,
        source_trace_sha256=record.source_trace_sha256,
        compiler_version=COMPILER_VERSION,
        validation_status="verified",
        procedure=procedure,
    )
    return result


def _compile_step(step: Any) -> ProcedureStep:
    payload = dict(step.payload)
    action = _action_name(payload)
    target_value = payload.get("target") or payload.get("object") or payload.get("receptacle")
    safe_target = _safe_text(target_value)
    if target_value is not None and safe_target is None:
        raise ValueError("unsupported internal ALFWorld target")
    target = SemanticTarget(
        page_name="ALFWorld environment",
        name=safe_target or action,
    )
    raw_expect = payload.get("expect")
    if isinstance(raw_expect, dict):
        expect = ProcedureExpect.model_validate(raw_expect)
    else:
        expect = ProcedureExpect(visible_text=f"{action} completed")
    return ProcedureStep(
        order=step.index + 1,
        action=action,
        target=target,
        expect=expect,
        note="Compiled from a verified ALFWorld trajectory.",
    )


def _action_name(payload: dict[str, Any]) -> str:
    raw = payload.get("action") or payload.get("tool") or payload.get("name")
    value = _safe_text(raw)
    allowed = {
        "observe",
        "navigate",
        "take",
        "open",
        "close",
        "put",
        "use",
        "slice",
        "heat",
        "cool",
        "clean",
        "verify",
    }
    if value not in allowed:
        raise ValueError(f"unsupported or missing ALFWorld action: {value!r}")
    return value


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(
        token in value.casefold() for token in ("objectid", "pose", "snapshot", "/", "\\")
    ):
        return None
    return value


__all__ = [
    "AlfworldDiagnostic",
    "AlfworldDerivedExperience",
    "COMPILER_VERSION",
    "compile_alfworld_trajectory",
]
