"""Immutable, provider-safe ALFWorld trajectory memory contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from homemaster.events.runtime_events import RuntimeEvent

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


class AlfworldTrajectoryWriter:
    """Admit ALFWorld trajectory records to the application-owned memory queue."""

    def __init__(
        self, mindmemos: Any, queue: Any, *, event_sink: Any, tenant_id: str = "local"
    ) -> None:
        self._mindmemos = mindmemos
        self._queue = queue
        self._event_sink = event_sink
        self._tenant_id = tenant_id

    def enqueue(self, record: AlfworldTrajectoryRecord) -> Any:
        if not isinstance(record, AlfworldTrajectoryRecord):
            raise TypeError("record must be an AlfworldTrajectoryRecord")
        self._emit(
            "memory.trajectory.queued",
            record,
            {"status": "queued", "external_return_status": "accepted"},
        )

        async def work() -> None:
            from homemaster.memory.automatic_recall import build_mindmemos_request_context

            context = build_mindmemos_request_context(
                request_id=f"trajectory-{record.trajectory_id}",
                tenant_id=self._tenant_id,
                session_id=record.source_session_id,
            )
            result = await self._mindmemos.add_trajectory_memory(record, context=context)
            payload = {
                "status": "stored",
                "memory_id": result.get("memory_id"),
                "external_return_status": "ok",
            }
            await self._aemit("memory.trajectory.stored", record, payload)
            if result.get("readback_verified") is not True:
                raise RuntimeError("trajectory memory readback was not verified")
            await self._aemit(
                "memory.trajectory.readback_verified",
                record,
                {
                    "status": "readback_verified",
                    "memory_id": result.get("memory_id"),
                    "external_return_status": "ok",
                    "readback_status": "verified",
                },
            )

        return self._queue.enqueue_work(
            job_type="alfworld_trajectory_memory",
            session_id=record.source_session_id,
            work=work,
        )

    def _event(
        self, event_type: str, record: AlfworldTrajectoryRecord, payload: dict[str, Any]
    ) -> RuntimeEvent:
        return RuntimeEvent(
            type=event_type,
            session_id=record.source_session_id,
            run_id=record.run_id,
            turn_index=None,
            payload={
                "trajectory_id": record.trajectory_id,
                "episode_id": record.episode_id,
                "taskset_id": record.taskset_id,
                "subtask_index": record.subtask_index,
                "outcome": record.outcome,
                "classification": record.classification,
                "source_trace_sha256": record.source_trace_sha256,
                **payload,
            },
        )

    def _emit(
        self, event_type: str, record: AlfworldTrajectoryRecord, payload: dict[str, Any]
    ) -> None:
        emit = getattr(self._event_sink, "emit", None)
        if callable(emit):
            emit(self._event(event_type, record, payload))

    async def _aemit(
        self, event_type: str, record: AlfworldTrajectoryRecord, payload: dict[str, Any]
    ) -> None:
        event = self._event(event_type, record, payload)
        aemit = getattr(self._event_sink, "aemit", None)
        if callable(aemit):
            await aemit(event)
            return
        emit = getattr(self._event_sink, "emit", None)
        if callable(emit):
            emit(event)



__all__ = [
    "AlfworldFinalEnvironmentState",
    "AlfworldOutcome",
    "AlfworldTrajectoryRecord",
    "AlfworldTrajectoryWriter",
    "AlfworldTrajectoryStep",
    "canonical_trajectory_payload",
    "determine_outcome",
    "provider_projection",
    "trajectory_sha256",
]
