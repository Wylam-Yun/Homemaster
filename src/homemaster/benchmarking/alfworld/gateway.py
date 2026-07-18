"""Single ordered boundary for ALFWorld external setup and execution requests."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from homemaster.benchmarking.alfworld.pose_snapshot import OraclePose, ScanPoseStep

ExternalReadStatus = Literal["ok", "absent", "malformed", "error"]
GatewayPhase = Literal[
    "setup_time_control",
    "setup_query",
    "setup_scan",
    "setup_pose_restore",
    "navigation",
    "manipulation",
]
CleanupReadStatus = Literal["succeeded", "unverified", "failed"]


@dataclass(frozen=True)
class ExternalEventRead:
    status: ExternalReadStatus
    returned_action: str | None
    action_success: bool | None
    pose: OraclePose | None
    world_sha256: str | None
    visibility_sha256: str | None
    frame_sha256: str | None
    objects: tuple[Any, ...] | None
    reachable_payload: bytes | None
    strict_visible_exact_ids: tuple[str, ...]
    bbox_areas: tuple[tuple[str, float], ...]
    raw_event_ref: str | None
    raw_event_sha256: str | None

    def __post_init__(self) -> None:
        if self.status not in {"ok", "absent", "malformed", "error"}:
            raise ValueError(f"unsupported external event read status: {self.status}")
        visible = tuple(sorted(set(self.strict_visible_exact_ids)))
        if visible != self.strict_visible_exact_ids:
            raise ValueError("strict-visible exact IDs must be sorted and unique")
        bbox_areas = tuple(sorted(self.bbox_areas))
        if bbox_areas != self.bbox_areas or len({item[0] for item in bbox_areas}) != len(
            bbox_areas
        ):
            raise ValueError("bbox areas must be sorted with one row per exact ID")
        if self.status == "ok":
            for name in ("world_sha256", "visibility_sha256", "frame_sha256"):
                value = getattr(self, name)
                if value is not None:
                    _validate_sha256(name, value)
        if self.raw_event_sha256 is not None:
            _validate_sha256("raw_event_sha256", self.raw_event_sha256)


@dataclass(frozen=True)
class ExternalActionRequest:
    phase: GatewayPhase
    sequence: int
    payload: dict[str, Any]
    request_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if self.phase not in {
            "setup_time_control",
            "setup_query",
            "setup_scan",
            "setup_pose_restore",
            "navigation",
            "manipulation",
        }:
            raise ValueError(f"unsupported gateway phase: {self.phase}")
        if self.sequence <= 0:
            raise ValueError("gateway sequence must be positive")
        payload = dict(self.payload)
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            raise ValueError("external action request requires an action")
        digest = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "request_sha256", digest)


@dataclass(frozen=True)
class GatewayActionResult:
    request: ExternalActionRequest
    event: ExternalEventRead
    duration_ms: float

    @property
    def success(self) -> bool:
        return (
            self.event.status == "ok"
            and self.event.returned_action == self.request.payload["action"]
            and self.event.action_success is True
        )


@dataclass(frozen=True)
class CleanupResult:
    status: CleanupReadStatus
    evidence_ref: str | None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "unverified", "failed"}:
            raise ValueError(f"unsupported cleanup status: {self.status}")


class OracleExecutionBackend(Protocol):
    def capture_event(self) -> ExternalEventRead: ...

    def send(self, request: ExternalActionRequest) -> ExternalEventRead: ...

    def close(self) -> CleanupResult: ...


class OracleActionGateway:
    def __init__(
        self,
        *,
        backend: OracleExecutionBackend,
        monotonic_ms: Any | None = None,
    ) -> None:
        self._backend = backend
        self._monotonic_ms = monotonic_ms or (lambda: time.perf_counter() * 1000.0)
        self._sequence = 0
        self._ledger: list[GatewayActionResult] = []

    @property
    def ledger(self) -> tuple[GatewayActionResult, ...]:
        return tuple(self._ledger)

    def capture_event(self) -> ExternalEventRead:
        return self._backend.capture_event()

    def execute_setup_time_control(self, value: float) -> GatewayActionResult:
        if value not in {0.01, 1.0}:
            raise ValueError("setup time control accepts only 0.01 or 1.0")
        return self._execute(
            "setup_time_control",
            {"action": "ChangeTimeScale", "timeScale": value},
        )

    def execute_setup_query(self) -> GatewayActionResult:
        return self._execute("setup_query", {"action": "GetReachablePositions"})

    def execute_setup_teleport(self, step: ScanPoseStep) -> GatewayActionResult:
        if not step.send_teleport or step.index == 0:
            raise ValueError("the zero-action initial scan step cannot be sent")
        return self._execute("setup_scan", _teleport_payload(step.pose))

    def execute_restore(self, pose: OraclePose) -> GatewayActionResult:
        return self._execute("setup_pose_restore", _teleport_payload(pose))

    def execute_navigation(self, payload: dict[str, Any]) -> GatewayActionResult:
        return self._execute("navigation", payload)

    def execute_manipulation(self, payload: dict[str, Any]) -> GatewayActionResult:
        return self._execute("manipulation", payload)

    def close(self) -> CleanupResult:
        return self._backend.close()

    def _execute(self, phase: GatewayPhase, payload: dict[str, Any]) -> GatewayActionResult:
        self._sequence += 1
        request = ExternalActionRequest(phase=phase, sequence=self._sequence, payload=payload)
        started = float(self._monotonic_ms())
        try:
            event = self._backend.send(request)
        except Exception:
            event = ExternalEventRead(
                status="error",
                returned_action=None,
                action_success=None,
                pose=None,
                world_sha256=None,
                visibility_sha256=None,
                frame_sha256=None,
                objects=None,
                reachable_payload=None,
                strict_visible_exact_ids=(),
                bbox_areas=(),
                raw_event_ref=None,
                raw_event_sha256=None,
            )
        result = GatewayActionResult(
            request=request,
            event=event,
            duration_ms=max(0.0, float(self._monotonic_ms()) - started),
        )
        self._ledger.append(result)
        return result


def _teleport_payload(pose: OraclePose) -> dict[str, Any]:
    return {
        "action": "TeleportFull",
        "x": pose.x,
        "y": pose.y,
        "z": pose.z,
        "rotateOnTeleport": True,
        "rotation": pose.rotation,
        "horizon": pose.horizon,
    }


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _validate_sha256(name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
