from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homemaster.devices import (
    DeviceControlReceipt,
    DeviceIdentity,
    DeviceLeaseError,
    DeviceLeaseManager,
    DeviceState,
    DeviceStateObservation,
    InMemoryDeviceEventStore,
)
from homemaster.tools.contracts import PermissionSubject


class Backend:
    def __init__(self, tenant: str, device: str, *, generation: int = 0) -> None:
        self.device_identity = DeviceIdentity(tenant, device, f"backend-{device}")
        self.backend_id = self.device_identity.backend_id
        self.device_id = device
        self.generation = generation
        self.stop_calls = 0
        self.observed_state = DeviceState.READY

    async def emergency_stop(self, *, reason: str, generation: int):
        assert reason
        assert generation > self.generation
        self.stop_calls += 1
        self.observed_state = DeviceState.STOPPED
        return DeviceControlReceipt(succeeded=True, return_code="ok")

    async def read_device_state(self):
        return DeviceStateObservation(
            query_succeeded=True,
            state=self.observed_state,
            return_code="ok",
        )


def context(backend: Backend, label: str, *, tenant: str | None = None):
    return SimpleNamespace(
        backend=backend,
        session_id=f"session-{label}",
        run_id=f"run-{label}",
        tool_call_id=f"call-{label}",
        permission_subject=PermissionSubject(
            subject_id=f"operator-{label}",
            channel="gateway",
            tenant_id=tenant or backend.device_identity.tenant_id,
            capabilities=("device.control",),
        ),
    )


@pytest.mark.asyncio
async def test_same_device_is_fifo_and_different_devices_run_concurrently() -> None:
    manager = DeviceLeaseManager()
    first_backend = Backend("tenant", "one")
    second_backend = Backend("tenant", "two")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def use(backend: Backend, label: str, *, hold: bool = False) -> None:
        async with manager.acquire("home:backend", context(backend, label)):
            order.append(label)
            if hold:
                first_entered.set()
                await release_first.wait()

    first = asyncio.create_task(use(first_backend, "first", hold=True))
    await first_entered.wait()
    second = asyncio.create_task(use(first_backend, "second"))
    third = asyncio.create_task(use(first_backend, "third"))
    other = asyncio.create_task(use(second_backend, "other"))
    while manager.waiting_count != 2:
        await asyncio.sleep(0)

    await other
    assert order == ["first", "other"]
    release_first.set()
    await asyncio.gather(first, second, third)

    assert order == ["first", "other", "second", "third"]
    assert manager.active_lease_count == manager.waiting_count == manager.resource_count == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed_without_releasing_active_lease() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with manager.acquire("home:backend", context(backend, "active")) as active:
            assert active.identity == backend.device_identity
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()

    async def wait() -> None:
        async with manager.acquire("home:backend", context(backend, "wait")):
            raise AssertionError("cancelled waiter entered")

    waiter = asyncio.create_task(wait())
    while manager.waiting_count != 1:
        await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert manager.active_lease_count == 1
    assert manager.waiting_count == 0
    release.set()
    await holder
    assert manager.active_lease_count == manager.resource_count == 0


@pytest.mark.asyncio
async def test_audit_sink_failure_cannot_retain_an_acquired_lease() -> None:
    def failing_audit(_payload: dict[str, object]) -> None:
        raise OSError("audit unavailable")

    store = InMemoryDeviceEventStore(failing_audit)
    manager = DeviceLeaseManager(event_store=store)
    backend = Backend("tenant", "one")

    async with manager.acquire("home:backend", context(backend, "audit-failure")):
        assert manager.active_lease_count == 1

    assert manager.active_lease_count == manager.resource_count == 0
    assert [event.event_type for event in store.events] == [
        "device.lease.acquired",
        "device.lease.released",
    ]
    assert [failure.event_type for failure in store.audit_failures] == [
        "device.lease.acquired",
        "device.lease.released",
    ]
    assert all(failure.error_type == "OSError" for failure in store.audit_failures)


@pytest.mark.asyncio
async def test_cross_tenant_and_stale_generation_are_fenced_before_backend() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant-a", "one", generation=1)
    with pytest.raises(DeviceLeaseError) as tenant_error:
        async with manager.acquire(
            "home:backend",
            context(backend, "cross", tenant="tenant-b"),
        ):
            pass
    assert tenant_error.value.error_code == "cross_tenant_device"

    await manager.fence(
        backend.device_identity,
        generation=2,
        state=DeviceState.STOPPED,
        requested_by="operator",
        reason="maintenance",
    )
    with pytest.raises(DeviceLeaseError) as stale_error:
        async with manager.acquire("home:backend", context(backend, "stale")):
            pass
    assert stale_error.value.error_code == "stale_generation"


@pytest.mark.asyncio
async def test_emergency_stop_bypasses_active_lease_and_fences_its_result() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_action() -> DeviceLeaseError | None:
        try:
            async with manager.acquire("home:backend", context(backend, "active")):
                entered.set()
                await release.wait()
        except DeviceLeaseError as exc:
            return exc
        return None

    action = asyncio.create_task(active_action())
    await entered.wait()
    stopped = await manager.emergency_stop(
        backend,
        permission_subject=context(backend, "stop").permission_subject,
        reason="operator requested",
    )

    assert stopped.succeeded is True
    assert stopped.control_return_code == "ok"
    assert stopped.state_return_code == "ok"
    assert backend.stop_calls == 1
    assert manager.active_lease_count == 1
    release.set()
    error = await action

    assert error is not None
    assert error.error_code == "device_generation_changed"
    assert error.execution_status == "outcome_unknown"
    assert manager.active_lease_count == 0
    assert [event.event_type for event in manager.event_store.events] == [
        "device.lease.acquired",
        "device.emergency_stop.requested",
        "device.emergency_stop.completed",
        "device.lease.released",
    ]
    completed = manager.event_store.events[2]
    assert completed.control_return_code == "ok"
    assert completed.state_return_code == "ok"


@pytest.mark.asyncio
async def test_audit_sink_failure_cannot_block_emergency_stop() -> None:
    def failing_audit(_payload: dict[str, object]) -> None:
        raise RuntimeError("audit unavailable")

    store = InMemoryDeviceEventStore(failing_audit)
    manager = DeviceLeaseManager(event_store=store)
    backend = Backend("tenant", "one")

    result = await manager.emergency_stop(
        backend,
        permission_subject=context(backend, "stop").permission_subject,
        reason="operator requested",
    )

    assert result.succeeded is True
    assert backend.stop_calls == 1
    assert [event.event_type for event in store.events] == [
        "device.emergency_stop.requested",
        "device.emergency_stop.completed",
    ]
    assert len(store.audit_failures) == 2


@pytest.mark.asyncio
async def test_stop_requires_both_success_return_and_external_stopped_state() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")

    async def incomplete_stop(*, reason: str, generation: int):
        del reason, generation
        return DeviceControlReceipt(succeeded=True, return_code="ok")

    backend.emergency_stop = incomplete_stop
    result = await manager.emergency_stop(
        backend,
        permission_subject=context(backend, "stop").permission_subject,
        reason="test",
    )

    assert result.return_succeeded is True
    assert result.external_state_confirmed is False
    assert result.state is DeviceState.UNCERTAIN
    assert result.succeeded is False
    assert result.control_return_code == "ok"
    assert result.state_return_code == "ok"


@pytest.mark.asyncio
async def test_raw_external_status_strings_are_not_treated_as_verified_contracts() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")

    async def unverified_stop(*, reason: str, generation: int):
        del reason, generation
        return {"status": "success"}

    async def unverified_state():
        return "stopped"

    backend.emergency_stop = unverified_stop
    backend.read_device_state = unverified_state
    result = await manager.emergency_stop(
        backend,
        permission_subject=context(backend, "stop").permission_subject,
        reason="test",
    )

    assert result.return_succeeded is False
    assert result.external_state_confirmed is False
    assert result.state is DeviceState.UNCERTAIN
    assert result.control_return_code is None
    assert result.state_return_code is None


@pytest.mark.asyncio
async def test_stop_requires_a_control_capability_before_backend_lookup() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")
    reader = PermissionSubject(
        subject_id="reader",
        channel="gateway",
        tenant_id="tenant",
        capabilities=("device.read",),
    )

    with pytest.raises(DeviceLeaseError) as error:
        await manager.emergency_stop(
            backend,
            permission_subject=reader,
            reason="unauthorized",
        )

    assert error.value.error_code == "permission_denied"
    assert backend.stop_calls == 0
    assert manager.event_store.events == ()


@pytest.mark.asyncio
async def test_backend_resource_aliases_share_one_physical_device_domain() -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")
    entered = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def use(resource_key: str, label: str, *, hold: bool = False) -> None:
        async with manager.acquire(resource_key, context(backend, label)):
            order.append(label)
            if hold:
                entered.set()
                await release.wait()

    first = asyncio.create_task(use("home:backend", "first", hold=True))
    await entered.wait()
    second = asyncio.create_task(use("alfworld:backend", "second"))
    while manager.waiting_count != 1:
        await asyncio.sleep(0)

    assert order == ["first"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["first", "second"]


@pytest.mark.asyncio
async def test_granted_waiter_rechecks_fence_before_entering_backend(monkeypatch) -> None:
    manager = DeviceLeaseManager()
    backend = Backend("tenant", "one")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    waiter_entered = False
    grants = 0
    original_grant_next = manager._grant_next

    def grant_then_fence(slot):
        nonlocal grants
        active_before = slot.active
        original_grant_next(slot)
        if active_before is not None or slot.active is None:
            return
        grants += 1
        if grants == 2:
            slot.generation += 1
            slot.state = DeviceState.STOPPED

    monkeypatch.setattr(manager, "_grant_next", grant_then_fence)

    async def first_action() -> None:
        async with manager.acquire("home:backend", context(backend, "first")):
            first_entered.set()
            await release_first.wait()

    async def waiting_action() -> DeviceLeaseError | None:
        nonlocal waiter_entered
        try:
            async with manager.acquire("home:backend", context(backend, "waiting")):
                waiter_entered = True
        except DeviceLeaseError as exc:
            return exc
        return None

    first = asyncio.create_task(first_action())
    await first_entered.wait()
    waiting = asyncio.create_task(waiting_action())
    while manager.waiting_count != 1:
        await asyncio.sleep(0)

    release_first.set()
    await first
    error = await waiting

    assert waiter_entered is False
    assert error is not None
    assert error.error_code == "device_fenced"
    assert error.backend_attempted is False
