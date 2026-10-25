"""RoboCasa trajectory memory records (parallel to the ALFWorld trajectory memory)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from homemaster.events.runtime_events import RuntimeEvent

RoboCasaOutcome = Literal["success", "failure", "unknown"]

_FORBIDDEN_PUBLIC_KEYS = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
}


class RoboCasaFinalEnvironmentState(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    success: bool | None = None
    step_index: int | None = Field(default=None, ge=0)
    invalid_action_count: int | None = Field(default=None, ge=0)
    task_progress: Any = None

    @property
    def won(self) -> bool | None:
        return self.success


class RoboCasaTrajectoryStep(BaseModel):
    """A lossless JSON step retained under the trajectory ACL boundary."""

    model_config = ConfigDict(extra="allow", frozen=True)

    index: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class RoboCasaTrajectoryRecord(BaseModel):
    """Canonical source record for one RoboCasa benchmark cell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    domain: Literal["robocasa"] = "robocasa"
    record_kind: Literal["trajectory"] = "trajectory"
    trajectory_id: str = Field(min_length=1)
    source_session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    task_name: str = Field(min_length=1)
    split: str = Field(min_length=1)
    seed: int = Field(ge=0)
    task: str = Field(min_length=1)
    outcome: RoboCasaOutcome
    classification: str = Field(min_length=1)
    failure_reason: str | None = None
    is_executable: Literal[False] = False
    source_trace_path: str = Field(min_length=1)
    source_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_environment_state: RoboCasaFinalEnvironmentState
    steps: tuple[RoboCasaTrajectoryStep, ...] = ()
    failure_summary: tuple[str, ...] = ()
    failure_advice: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_outcome(self) -> RoboCasaTrajectoryRecord:
        if self.outcome == "success":
            if self.final_environment_state.won is not True:
                raise ValueError("success requires final_environment_state.success=true")
            if self.failure_reason is not None:
                raise ValueError("success cannot contain failure_reason")
        elif self.outcome == "failure":
            if self.final_environment_state.won is not False:
                raise ValueError("failure requires final_environment_state.success=false")
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
) -> tuple[RoboCasaOutcome, str | None]:
    """Apply the closed three-state rule to an adapter snapshot."""

    if infrastructure_failure or state_contradictory:
        return (
            "unknown",
            "infrastructure_error" if infrastructure_failure else "execution_state_uncertain",
        )
    success = final_state.get("success")
    if not isinstance(success, bool):
        return "unknown", "execution_state_uncertain"
    if success:
        return "success", None
    reason = final_state.get("failure_reason")
    return "failure", str(reason) if isinstance(reason, str) and reason else "not_success"


def canonical_trajectory_payload(record: RoboCasaTrajectoryRecord) -> bytes:
    payload = record.model_dump(mode="json", exclude={"source_trace_sha256"})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def trajectory_sha256(record: RoboCasaTrajectoryRecord) -> str:
    return hashlib.sha256(canonical_trajectory_payload(record)).hexdigest()


def extract_failure_guidance(
    steps: tuple[RoboCasaTrajectoryStep, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    summary: list[str] = []
    advice: list[str] = []
    errors = {
        str(step.payload.get("result", {}).get("error"))
        for step in steps
        if isinstance(step.payload.get("result"), dict) and step.payload["result"].get("error")
    }
    if any("grasp" in error for error in errors):
        summary.append("grasp_failure")
        advice.append(
            "Reposition the base closer to the target or localize it with "
            "world_map/back_project before retrying scripted_grasp."
        )
    if any("navigate" in error or "unreachable" in error for error in errors):
        summary.append("navigation_failure")
        advice.append("Navigate closer to the target before invoking arm-level skills.")
    if any("rldx" in error or "chunk" in error for error in errors):
        summary.append("vla_stall")
        advice.append(
            "When rldx_skill hits its chunk cap, reposition and retry rather than "
            "repeating the same invocation."
        )
    return tuple(dict.fromkeys(summary)), tuple(dict.fromkeys(advice))


def provider_projection(record: RoboCasaTrajectoryRecord) -> dict[str, Any]:
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
        "domain": "RoboCasa 执行轨迹",
        "result": {"success": "成功", "failure": "失败", "unknown": "未知"}[record.outcome],
        "executable": False,
    }
    return projected


class RoboCasaTrajectoryWriter:
    """Admit RoboCasa trajectory records to the application-owned memory queue."""

    def __init__(
        self,
        mindmemos: Any,
        queue: Any,
        *,
        event_sink: Any,
        tenant_id: str = "local",
    ) -> None:
        self._mindmemos = mindmemos
        self._queue = queue
        self._event_sink = event_sink
        self._tenant_id = tenant_id

    def enqueue(self, record: RoboCasaTrajectoryRecord) -> Any:
        if not isinstance(record, RoboCasaTrajectoryRecord):
            raise TypeError("record must be a RoboCasaTrajectoryRecord")
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
            result = await self._mindmemos.add_robocasa_trajectory_memory(record, context=context)
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
            job_type="robocasa_trajectory_memory",
            session_id=record.source_session_id,
            work=work,
        )

    def _event(
        self, event_type: str, record: RoboCasaTrajectoryRecord, payload: dict[str, Any]
    ) -> RuntimeEvent:
        return RuntimeEvent(
            type=event_type,
            session_id=record.source_session_id,
            run_id=record.run_id,
            turn_index=None,
            payload={
                "trajectory_id": record.trajectory_id,
                "episode_id": record.episode_id,
                "task_name": record.task_name,
                "seed": record.seed,
                "outcome": record.outcome,
                "classification": record.classification,
                "source_trace_sha256": record.source_trace_sha256,
                **payload,
            },
        )

    def _emit(self, event: str, record: RoboCasaTrajectoryRecord, payload: dict[str, Any]) -> None:
        sink = self._event_sink
        if sink is None:
            return
        emitter = getattr(sink, "emit", None)
        if callable(emitter):
            emitter(self._event(event, record, payload))

    async def _aemit(
        self, event: str, record: RoboCasaTrajectoryRecord, payload: dict[str, Any]
    ) -> None:
        sink = self._event_sink
        if sink is None:
            return
        built = self._event(event, record, payload)
        aemitter = getattr(sink, "aemit", None)
        if callable(aemitter):
            output = aemitter(built)
            if hasattr(output, "__await__"):
                await output
            return
        emitter = getattr(sink, "emit", None)
        if callable(emitter):
            emitter(built)


__all__ = [
    "RoboCasaFinalEnvironmentState",
    "RoboCasaOutcome",
    "RoboCasaTrajectoryRecord",
    "RoboCasaTrajectoryStep",
    "RoboCasaTrajectoryWriter",
    "canonical_trajectory_payload",
    "determine_outcome",
    "extract_failure_guidance",
    "provider_projection",
    "trajectory_sha256",
]
