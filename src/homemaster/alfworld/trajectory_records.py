"""Immutable, provider-safe ALFWorld trajectory record contracts.

V3.8: these types model the trajectory artifact written to the episode trace
(``write_trajectory_memory``). The MindMemOS enqueue path
(``AlfworldTrajectoryWriter``) was removed with the embedded lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AlfworldOutcome = Literal["success", "failure", "unknown"]

_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "objectid",
        "object_id",
        "pose",
        "coordinate",
        "coordinates",
        "snapshot_id",
        "raw_event_ref",
        "evidence_ref",
        "source_trace_path",
        "internal_trace_ref",
    }
)


class AlfworldFinalEnvironmentState(BaseModel):
    """The adapter state observed at the moment the runner stops."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    won: bool | None
    done: bool | None
    step_index: int | None = Field(default=None, ge=0)
    invalid_action_count: int | None = Field(default=None, ge=0)
    goal_condition_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    inventory: tuple[str, ...] | None = None


class AlfworldTrajectoryStep(BaseModel):
    """A lossless JSON step retained under the trajectory ACL boundary."""

    model_config = ConfigDict(extra="allow", frozen=True)

    index: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class AlfworldTrajectoryRecord(BaseModel):
    """Canonical source record for every episode or taskset subtask."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    domain: Literal["alfworld"] = "alfworld"
    record_kind: Literal["trajectory"] = "trajectory"
    trajectory_id: str = Field(min_length=1)
    source_session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    taskset_id: str | None = None
    subtask_index: int | None = Field(default=None, ge=0)
    task: str = Field(min_length=1)
    goal_type: str = Field(min_length=1)
    outcome: AlfworldOutcome
    classification: str = Field(min_length=1)
    failure_reason: str | None = None
    is_executable: Literal[False] = False
    source_trace_path: str = Field(min_length=1)
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_environment_state: AlfworldFinalEnvironmentState
    steps: tuple[AlfworldTrajectoryStep, ...] = ()
    failure_summary: tuple[str, ...] = ()
    failure_advice: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_outcome(self) -> AlfworldTrajectoryRecord:
        if self.outcome == "success":
            if self.final_environment_state.won is not True:
                raise ValueError("success requires final_environment_state.won=true")
            if self.failure_reason is not None:
                raise ValueError("success cannot contain failure_reason")
        elif self.outcome == "failure":
            if self.final_environment_state.won is not False:
                raise ValueError("failure requires final_environment_state.won=false")
            if not self.failure_reason:
                raise ValueError("failure requires failure_reason")
        elif not self.failure_reason:
            raise ValueError("unknown requires failure_reason")
        return self


def determine_outcome(
    final_state: Mapping[str, Any],
    *,
    infrastructure_failure: bool = False,
    state_contradictory: bool = False,
) -> tuple[AlfworldOutcome, str | None]:
    """Apply the Spec's closed three-state rule to an adapter snapshot."""

    if infrastructure_failure or state_contradictory:
        return (
            "unknown",
            "runtime_failure" if infrastructure_failure else "execution_state_uncertain",
        )
    won = final_state.get("won")
    if not isinstance(won, bool):
        return "unknown", "execution_state_uncertain"
    if won:
        return "success", None
    reason = final_state.get("failure_reason")
    return "failure", str(reason) if isinstance(reason, str) and reason else "not_won"


def canonical_trajectory_payload(record: AlfworldTrajectoryRecord) -> bytes:
    """Serialize without the self-referential source hash for stable hashing."""

    payload = record.model_dump(mode="json", exclude={"source_trace_sha256"})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def trajectory_sha256(record: AlfworldTrajectoryRecord) -> str:
    return hashlib.sha256(canonical_trajectory_payload(record)).hexdigest()


def extract_failure_guidance(
    steps: tuple[AlfworldTrajectoryStep, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    summary: list[str] = []
    advice: list[str] = []
    errors = {str(step.payload.get("error")) for step in steps if step.payload.get("error")}
    if "target_not_found" in errors:
        summary.append("unsupported_target_or_alias")
        advice.append(
            "Use a scene-verified canonical target label instead of repeatedly guessing aliases."
        )
    if "navigation_required" in errors:
        summary.append("missing_navigation_precondition")
        advice.append("Navigate to the verified target before pickup or manipulation.")
    if "harness_operation_failure" in errors or any(
        "rejected" in str(step.payload.get("debug_feedback", "")).lower() for step in steps
    ):
        summary.append("external_action_rejected")
        advice.append(
            "Treat an externally rejected action with no state change as failure and "
            "recover before retrying."
        )
    return tuple(dict.fromkeys(summary)), tuple(dict.fromkeys(advice))


def provider_projection(record: AlfworldTrajectoryRecord) -> dict[str, Any]:
    """Expose outcome-labelled content while rejecting internal identity fields."""

    payload = record.model_dump(
        mode="json", exclude={"source_trace_path", "source_trace_sha256", "steps"}
    )

    def scrub(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): scrub(child)
                for key, child in value.items()
                if str(key).casefold() not in _FORBIDDEN_PUBLIC_KEYS
            }
        if isinstance(value, list):
            return [scrub(child) for child in value]
        return value

    projected = scrub(payload)
    projected["content_label"] = {
        "domain": "ALFWorld 执行轨迹",
        "result": {"success": "成功", "failure": "失败", "unknown": "未知"}[record.outcome],
        "executable": False,
    }
    return projected


