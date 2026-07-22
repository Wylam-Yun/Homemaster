"""Generation-aware FIFO leases and independent emergency-stop control."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from homemaster.devices.contracts import (
    DeviceControlReceipt,
    DeviceIdentity,
    DeviceState,
    DeviceStateObservation,
    EmergencyStopResult,
    InMemoryDeviceEventStore,
)


class DeviceLeaseError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        execution_status: str = "denied",
        backend_attempted: bool = False,
    ) -> None:
        self.error_code = code
        self.execution_status = execution_status
        self.backend_attempted = backend_attempted
        super().__init__(message)


@dataclass(frozen=True)
class DeviceLease:
    lease_id: str
    identity: DeviceIdentity
    generation: int
    owner: str


@dataclass
class _Waiter:
    future: asyncio.Future[DeviceLease]
    lease: DeviceLease


@dataclass
class _DeviceSlot:
    generation: int
    state: DeviceState = DeviceState.READY
    active: DeviceLease | None = None
    waiters: deque[_Waiter] = field(default_factory=deque)


class DeviceLeaseManager:
    """Serialize one physical device while allowing different devices to run concurrently."""

    def __init__(self, *, event_store: InMemoryDeviceEventStore | None = None) -> None:
        self.event_store = event_store or InMemoryDeviceEventStore()
        self._slots: dict[tuple[str, str, str, str], _DeviceSlot] = {}
        self._registry_lock = asyncio.Lock()
        self._active_lease_count = 0
        self._waiting_count = 0

    @property
    def active_lease_count(self) -> int:
        return self._active_lease_count

    @property
    def waiting_count(self) -> int:
        return self._waiting_count

    @property
    def resource_count(self) -> int:
        return len(self._slots)

    @asynccontextmanager
    async def acquire(self, resource_key: str, context: Any):
        identity = device_identity_from_context(context)
        generation = _context_generation(context)
        key = (*identity.key, _lease_domain(resource_key))
        loop = asyncio.get_running_loop()
        lease = DeviceLease(
            lease_id=f"lease-{uuid.uuid4().hex}",
            identity=identity,
            generation=generation,
            owner=(
                f"{context.session_id}:"
                f"{getattr(context, 'run_id', 'run')}:"
                f"{getattr(context, 'tool_call_id', 'call')}"
            ),
        )
        waiter = _Waiter(loop.create_future(), lease)
        async with self._registry_lock:
            slot = self._slots.get(key)
            if slot is None:
                slot = _DeviceSlot(generation=generation)
                self._slots[key] = slot
            self._validate_generation(slot, generation)
            self._ensure_ready(slot)
            slot.waiters.append(waiter)
            self._waiting_count += 1
            self._grant_next(slot)
        try:
            acquired = await waiter.future
            async with self._registry_lock:
                if (
                    slot.active is None
                    or slot.active.lease_id != acquired.lease_id
                    or slot.generation != acquired.generation
                    or slot.state is not DeviceState.READY
                ):
                    raise DeviceLeaseError(
                        "device_fenced",
                        "device was fenced before backend execution",
                    )
        except BaseException:
            async with self._registry_lock:
                self._remove_waiter(slot, waiter)
                if slot.active is not None and slot.active.lease_id == waiter.lease.lease_id:
                    slot.active = None
                    self._active_lease_count -= 1
                    self._grant_next(slot)
                self._remove_idle_ready_slot(key, slot)
            raise
        self.event_store.append(
            event_type="device.lease.acquired",
            identity=acquired.identity,
            generation=acquired.generation,
            requested_by=acquired.owner,
            state=DeviceState.READY,
            reason=resource_key,
        )
        try:
            yield acquired
            async with self._registry_lock:
                if slot.generation != acquired.generation or slot.state is not DeviceState.READY:
                    raise DeviceLeaseError(
                        "device_generation_changed",
                        "device generation or state changed while the action was executing",
                        execution_status="outcome_unknown",
                        backend_attempted=True,
                    )
        finally:
            async with self._registry_lock:
                if slot.active is not None and slot.active.lease_id == acquired.lease_id:
                    slot.active = None
                    self._active_lease_count -= 1
                    self._grant_next(slot)
                self._remove_idle_ready_slot(key, slot)
            self.event_store.append(
                event_type="device.lease.released",
                identity=acquired.identity,
                generation=acquired.generation,
                requested_by=acquired.owner,
                state=slot.state,
                reason=resource_key,
            )

    async def fence(
        self,
        identity: DeviceIdentity,
        *,
        generation: int,
        state: DeviceState,
        requested_by: str,
        reason: str,
        resource_key: str = "home:backend",
    ) -> None:
        key = (*identity.key, _lease_domain(resource_key))
        async with self._registry_lock:
            slot = self._slots.setdefault(key, _DeviceSlot(generation=generation))
            if generation < slot.generation:
                raise DeviceLeaseError("stale_generation", "cannot fence a newer device generation")
            slot.generation = generation
            slot.state = state
            self._reject_waiters(slot, code="device_fenced", reason=reason)
        self.event_store.append(
            event_type="device.fenced",
            identity=identity,
            generation=generation,
            requested_by=requested_by,
            state=state,
            reason=reason,
        )

    async def fence_next(
        self,
        identity: DeviceIdentity,
        *,
        generation_floor: int,
        state: DeviceState,
        requested_by: str,
        reason: str,
        resource_key: str = "home:backend",
    ) -> int:
        if (
            isinstance(generation_floor, bool)
            or not isinstance(generation_floor, int)
            or generation_floor < 0
        ):
            raise ValueError("generation_floor must be a non-negative integer")
        key = (*identity.key, _lease_domain(resource_key))
        async with self._registry_lock:
            slot = self._slots.setdefault(key, _DeviceSlot(generation=generation_floor))
            generation = max(slot.generation, generation_floor) + 1
            slot.generation = generation
            slot.state = state
            self._reject_waiters(slot, code="device_fenced", reason=reason)
        self.event_store.append(
            event_type="device.fenced",
            identity=identity,
            generation=generation,
            requested_by=requested_by,
            state=state,
            reason=reason,
        )
        return generation

    async def emergency_stop(
        self,
        backend: Any,
        *,
        permission_subject: Any,
        reason: str,
        resource_key: str = "home:backend",
    ) -> EmergencyStopResult:
        capabilities = getattr(permission_subject, "capabilities", ())
        if "device.control" not in capabilities and "device.emergency_stop" not in capabilities:
            raise DeviceLeaseError(
                "permission_denied",
                "principal lacks required capability: device.control",
            )
        identity = device_identity_from_backend(backend, permission_subject.tenant_id)
        key = (*identity.key, _lease_domain(resource_key))
        async with self._registry_lock:
            slot = self._slots.setdefault(
                key,
                _DeviceSlot(generation=_backend_generation(backend)),
            )
            generation = max(slot.generation, _backend_generation(backend)) + 1
            slot.generation = generation
            slot.state = DeviceState.STOPPING
            self._reject_waiters(slot, code="emergency_stop", reason=reason)
        requested = self.event_store.append(
            event_type="device.emergency_stop.requested",
            identity=identity,
            generation=generation,
            requested_by=permission_subject.subject_id,
            state=DeviceState.STOPPING,
            reason=reason,
        )
        stop = getattr(backend, "emergency_stop", None)
        control_receipt: DeviceControlReceipt | None = None
        if callable(stop):
            try:
                value = stop(reason=reason, generation=generation)
                if inspect.isawaitable(value):
                    value = await value
                if isinstance(value, DeviceControlReceipt):
                    control_receipt = value
            except Exception:
                control_receipt = None
        state_observation = await _external_stop_observation(backend)
        return_succeeded = bool(control_receipt and control_receipt.succeeded)
        external_state_confirmed = bool(
            state_observation
            and state_observation.query_succeeded
            and state_observation.state is DeviceState.STOPPED
        )
        state = (
            DeviceState.STOPPED
            if return_succeeded and external_state_confirmed
            else DeviceState.UNCERTAIN
        )
        async with self._registry_lock:
            slot.state = state
        completed = self.event_store.append(
            event_type="device.emergency_stop.completed",
            identity=identity,
            generation=generation,
            requested_by=permission_subject.subject_id,
            state=state,
            reason=reason,
            control_return_code=(
                control_receipt.return_code if control_receipt is not None else None
            ),
            state_return_code=(
                state_observation.return_code if state_observation is not None else None
            ),
        )
        return EmergencyStopResult(
            identity=identity,
            generation=generation,
            state=state,
            return_succeeded=return_succeeded,
            external_state_confirmed=external_state_confirmed,
            control_return_code=(
                control_receipt.return_code if control_receipt is not None else None
            ),
            state_return_code=(
                state_observation.return_code if state_observation is not None else None
            ),
            event_ref=completed.event_id or requested.event_id,
        )

    def _validate_generation(self, slot: _DeviceSlot, generation: int) -> None:
        if generation < slot.generation:
            raise DeviceLeaseError("stale_generation", "device generation is stale")
        if generation > slot.generation:
            if slot.active is not None:
                raise DeviceLeaseError(
                    "generation_conflict",
                    "cannot advance generation while a device action is active",
                )
            slot.generation = generation
            slot.state = DeviceState.READY

    @staticmethod
    def _ensure_ready(slot: _DeviceSlot) -> None:
        if slot.state is not DeviceState.READY:
            raise DeviceLeaseError(
                "device_fenced",
                f"device is fenced in state {slot.state.value}",
            )

    def _grant_next(self, slot: _DeviceSlot) -> None:
        if slot.active is not None or slot.state is not DeviceState.READY:
            return
        while slot.waiters:
            waiter = slot.waiters.popleft()
            self._waiting_count -= 1
            if waiter.future.cancelled():
                continue
            slot.active = waiter.lease
            self._active_lease_count += 1
            waiter.future.set_result(waiter.lease)
            return

    def _remove_waiter(self, slot: _DeviceSlot, waiter: _Waiter) -> None:
        try:
            slot.waiters.remove(waiter)
        except ValueError:
            return
        self._waiting_count -= 1

    def _reject_waiters(self, slot: _DeviceSlot, *, code: str, reason: str) -> None:
        while slot.waiters:
            waiter = slot.waiters.popleft()
            self._waiting_count -= 1
            if not waiter.future.done():
                waiter.future.set_exception(DeviceLeaseError(code, reason))

    def _remove_idle_ready_slot(
        self,
        key: tuple[str, str, str, str],
        slot: _DeviceSlot,
    ) -> None:
        if slot.active is None and not slot.waiters and slot.state is DeviceState.READY:
            self._slots.pop(key, None)


def device_identity_from_context(context: Any) -> DeviceIdentity:
    subject = getattr(context, "permission_subject", None)
    tenant_id = getattr(subject, "tenant_id", "local")
    return device_identity_from_backend(context.backend, tenant_id, context.session_id)


def device_identity_from_backend(
    backend: Any,
    tenant_id: str,
    fallback: str = "unbound-device",
) -> DeviceIdentity:
    declared = getattr(backend, "device_identity", None)
    if isinstance(declared, DeviceIdentity):
        if declared.tenant_id != tenant_id:
            raise DeviceLeaseError("cross_tenant_device", "device belongs to another tenant")
        return declared
    actual = getattr(backend, "actual_backend", backend)
    declared = getattr(actual, "device_identity", None)
    if isinstance(declared, DeviceIdentity):
        if declared.tenant_id != tenant_id:
            raise DeviceLeaseError("cross_tenant_device", "device belongs to another tenant")
        return declared
    backend_id = str(getattr(actual, "backend_id", fallback))
    device_id = str(getattr(actual, "device_id", backend_id))
    return DeviceIdentity(tenant_id=tenant_id, device_id=device_id, backend_id=backend_id)


def _context_generation(context: Any) -> int:
    return _backend_generation(getattr(context, "backend", None))


def _backend_generation(backend: Any) -> int:
    value = getattr(backend, "generation", None)
    actual = getattr(backend, "actual_backend", backend)
    if value is None:
        value = getattr(actual, "generation", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeviceLeaseError("invalid_generation", "device generation must be non-negative")
    return value


async def _external_stop_observation(backend: Any) -> DeviceStateObservation | None:
    actual = getattr(backend, "actual_backend", backend)
    query = getattr(actual, "read_device_state", None)
    if not callable(query):
        return None
    value = query()
    if inspect.isawaitable(value):
        value = await value
    return value if isinstance(value, DeviceStateObservation) else None


def _lease_domain(resource_key: str) -> str:
    return "physical-device" if resource_key.endswith(":backend") else resource_key


__all__ = [
    "DeviceLease",
    "DeviceLeaseError",
    "DeviceLeaseManager",
    "device_identity_from_backend",
    "device_identity_from_context",
]
