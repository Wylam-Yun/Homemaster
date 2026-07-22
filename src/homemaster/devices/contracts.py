"""Typed physical-device identity and authoritative control events."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class DeviceState(StrEnum):
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNCERTAIN = "uncertain"
    DISCONNECTED = "disconnected"
    CLOSED = "closed"


@dataclass(frozen=True)
class DeviceIdentity:
    tenant_id: str
    device_id: str
    backend_id: str

    def __post_init__(self) -> None:
        for label, value in (
            ("tenant_id", self.tenant_id),
            ("device_id", self.device_id),
            ("backend_id", self.backend_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"device {label} must be non-empty")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.device_id, self.backend_id)


@dataclass(frozen=True)
class DeviceEvent:
    event_id: str
    event_type: str
    identity: DeviceIdentity
    generation: int
    requested_by: str
    state: DeviceState
    reason: str
    timestamp: float
    control_return_code: str | None = None
    state_return_code: str | None = None


@dataclass(frozen=True)
class DeviceAuditFailure:
    event_id: str
    event_type: str
    error_type: str
    detail: str
    timestamp: float


class InMemoryDeviceEventStore:
    """Authoritative append-only device-event sink used by the control plane."""

    def __init__(self, audit_sink: Callable[[dict[str, object]], None] | None = None) -> None:
        self._events: list[DeviceEvent] = []
        self._audit_sink = audit_sink
        self._audit_failures: list[DeviceAuditFailure] = []

    def append(
        self,
        *,
        event_type: str,
        identity: DeviceIdentity,
        generation: int,
        requested_by: str,
        state: DeviceState,
        reason: str,
        control_return_code: str | None = None,
        state_return_code: str | None = None,
    ) -> DeviceEvent:
        event = DeviceEvent(
            event_id=f"device-event-{uuid.uuid4().hex}",
            event_type=event_type,
            identity=identity,
            generation=generation,
            requested_by=requested_by,
            state=state,
            reason=reason,
            timestamp=time.time(),
            control_return_code=control_return_code,
            state_return_code=state_return_code,
        )
        self._events.append(event)
        if self._audit_sink is not None:
            try:
                self._audit_sink(
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "tenant_id": event.identity.tenant_id,
                        "device_id": event.identity.device_id,
                        "backend_id": event.identity.backend_id,
                        "generation": event.generation,
                        "requested_by": event.requested_by,
                        "state": event.state.value,
                        "reason": event.reason,
                        "timestamp": event.timestamp,
                        "control_return_code": event.control_return_code,
                        "state_return_code": event.state_return_code,
                    }
                )
            except Exception as exc:
                self._audit_failures.append(
                    DeviceAuditFailure(
                        event_id=event.event_id,
                        event_type=event.event_type,
                        error_type=type(exc).__name__,
                        detail=str(exc)[:4000],
                        timestamp=time.time(),
                    )
                )
        return event

    @property
    def events(self) -> tuple[DeviceEvent, ...]:
        return tuple(self._events)

    @property
    def audit_failures(self) -> tuple[DeviceAuditFailure, ...]:
        return tuple(self._audit_failures)


@dataclass(frozen=True)
class DeviceControlReceipt:
    succeeded: bool
    return_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.succeeded, bool):
            raise TypeError("device control succeeded must be boolean")
        if not isinstance(self.return_code, str) or not self.return_code.strip():
            raise ValueError("device control return_code must be non-empty")


@dataclass(frozen=True)
class DeviceStateObservation:
    query_succeeded: bool
    state: DeviceState
    return_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.query_succeeded, bool):
            raise TypeError("device state query_succeeded must be boolean")
        if not isinstance(self.state, DeviceState):
            raise TypeError("device state observation must use DeviceState")
        if not isinstance(self.return_code, str) or not self.return_code.strip():
            raise ValueError("device state return_code must be non-empty")


@dataclass(frozen=True)
class EmergencyStopResult:
    identity: DeviceIdentity
    generation: int
    state: DeviceState
    return_succeeded: bool
    external_state_confirmed: bool
    control_return_code: str | None
    state_return_code: str | None
    event_ref: str

    @property
    def succeeded(self) -> bool:
        return (
            self.state is DeviceState.STOPPED
            and self.return_succeeded
            and self.external_state_confirmed
        )


__all__ = [
    "DeviceEvent",
    "DeviceAuditFailure",
    "DeviceControlReceipt",
    "DeviceIdentity",
    "DeviceState",
    "DeviceStateObservation",
    "EmergencyStopResult",
    "InMemoryDeviceEventStore",
]
