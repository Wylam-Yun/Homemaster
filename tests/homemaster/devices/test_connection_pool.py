from __future__ import annotations

import asyncio

import pytest

from homemaster.devices.connection_pool import (
    DeviceConnectionBinding,
    DeviceConnectionCleanupError,
    DeviceConnectionOwnership,
    DeviceConnectionPool,
)
from homemaster.devices.contracts import (
    DeviceControlReceipt,
    DeviceIdentity,
    DeviceState,
    DeviceStateObservation,
)
from homemaster.devices.lease_manager import DeviceLeaseError, DeviceLeaseManager
from homemaster.tools.contracts import PermissionSubject


class Connection:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1
        self.events.append(self.name)
        if self.fail:
            raise RuntimeError(f"close failed: {self.name}")


@pytest.mark.asyncio
async def test_pool_enforces_tenant_disconnect_and_owned_borrowed_cleanup() -> None:
    events: list[str] = []
    manager = DeviceLeaseManager()
    pool = DeviceConnectionPool(manager)
    owned_id = DeviceIdentity("tenant", "owned", "backend-owned")
    bad_id = DeviceIdentity("tenant", "bad", "backend-bad")
    borrowed_id = DeviceIdentity("tenant", "borrowed", "backend-borrowed")
    owned = Connection("owned", events)
    bad = Connection("bad", events, fail=True)
    borrowed = Connection("borrowed", events)
    pool.register(DeviceConnectionBinding(owned_id, owned))
    pool.register(DeviceConnectionBinding(bad_id, bad))
    pool.register(
        DeviceConnectionBinding(
            borrowed_id,
            borrowed,
            ownership=DeviceConnectionOwnership.BORROWED,
        )
    )

    assert pool.get(owned_id, tenant_id="tenant") is owned
    with pytest.raises(PermissionError):
        pool.get(owned_id, tenant_id="other")
    generation = await pool.mark_disconnected(
        owned_id,
        requested_by="monitor",
        reason="transport lost",
    )
    assert generation == 1
    assert pool.state(owned_id) is DeviceState.DISCONNECTED
    with pytest.raises(RuntimeError, match="disconnected"):
        pool.get(owned_id, tenant_id="tenant")

    with pytest.raises(DeviceConnectionCleanupError) as error:
        await pool.aclose()
    assert len(error.value.errors) == 1
    assert events == ["bad", "owned"]
    assert owned.closed == bad.closed == 1
    assert borrowed.closed == 0
    await pool.aclose()
    assert owned.closed == bad.closed == 1


@pytest.mark.parametrize("generation", [True, -1, "1", 1.5])
def test_connection_generation_is_a_strict_non_negative_integer(generation) -> None:
    with pytest.raises(ValueError, match="generation must be non-negative"):
        DeviceConnectionBinding(
            DeviceIdentity("tenant", "device", "backend"),
            object(),
            generation=generation,
        )


def test_get_rejects_a_stale_expected_generation() -> None:
    pool = DeviceConnectionPool()
    identity = DeviceIdentity("tenant", "device", "backend")
    pool.register(DeviceConnectionBinding(identity, object(), generation=3))

    with pytest.raises(RuntimeError, match="expected 2, current 3"):
        pool.get(identity, tenant_id="tenant", expected_generation=2)


def test_pool_rejects_rebinding_to_a_different_lease_manager() -> None:
    first = DeviceLeaseManager()
    pool = DeviceConnectionPool(first)
    pool.bind_lease_manager(first)

    with pytest.raises(ValueError, match="another lease manager"):
        pool.bind_lease_manager(DeviceLeaseManager())


def test_borrowed_backend_is_pinned_to_one_authoritative_tenant() -> None:
    class UntypedBackend:
        backend_id = "shared-physical-backend"
        device_id = "shared-device"
        generation = 0

    pool = DeviceConnectionPool(DeviceLeaseManager())
    backend = UntypedBackend()

    bound = pool.bind_borrowed(backend, tenant_id="tenant-a")

    assert bound.actual_backend is backend
    assert bound.device_identity == DeviceIdentity(
        "tenant-a", "shared-device", "shared-physical-backend"
    )
    with pytest.raises(DeviceLeaseError) as error:
        pool.bind_borrowed(backend, tenant_id="tenant-b")
    assert error.value.error_code == "cross_tenant_device"


def test_handle_separates_pool_fencing_from_backend_run_generation() -> None:
    class Backend:
        backend_id = "backend"
        device_id = "device"
        generation = 0

    pool = DeviceConnectionPool()
    backend = Backend()
    handle = pool.bind_borrowed(backend, tenant_id="tenant")

    backend.generation = 7

    assert handle.generation == 0
    assert handle.backend_generation == 7


@pytest.mark.asyncio
async def test_disconnect_generation_is_monotonic_across_stop_and_repeats() -> None:
    class Backend:
        def __init__(self) -> None:
            self.device_identity = DeviceIdentity("tenant", "device", "backend")
            self.generation = 0

        async def emergency_stop(self, *, reason: str, generation: int):
            del reason, generation
            return DeviceControlReceipt(succeeded=True, return_code="ok")

        async def read_device_state(self):
            return DeviceStateObservation(
                query_succeeded=True,
                state=DeviceState.STOPPED,
                return_code="ok",
            )

    manager = DeviceLeaseManager()
    pool = DeviceConnectionPool(manager)
    backend = Backend()
    pool.register(DeviceConnectionBinding(backend.device_identity, backend))
    subject = PermissionSubject(
        subject_id="operator",
        tenant_id="tenant",
        channel="gateway",
        capabilities=("device.control",),
    )

    first = await pool.disconnect(
        backend.device_identity,
        requested_by="monitor",
        reason="first",
    )
    second = await pool.disconnect(
        backend.device_identity,
        requested_by="monitor",
        reason="second",
    )
    stopped = await manager.emergency_stop(
        backend,
        permission_subject=subject,
        reason="stop",
    )
    after_stop = await pool.disconnect(
        backend.device_identity,
        requested_by="monitor",
        reason="after stop",
    )

    assert [first, second, stopped.generation, after_stop] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_application_close_fences_active_and_waiting_borrowed_connections() -> None:
    manager = DeviceLeaseManager()
    pool = DeviceConnectionPool(manager)
    identity = DeviceIdentity("tenant", "device", "backend")
    backend = type(
        "Backend",
        (),
        {"device_identity": identity, "backend_id": "backend", "generation": 0},
    )()
    pool.register(
        DeviceConnectionBinding(
            identity,
            backend,
            ownership=DeviceConnectionOwnership.BORROWED,
        )
    )
    subject = PermissionSubject(
        subject_id="operator",
        tenant_id="tenant",
        channel="gateway",
        capabilities=("device.control",),
    )

    def lease_context(label: str):
        return type(
            "Context",
            (),
            {
                "backend": backend,
                "session_id": "session",
                "run_id": "run",
                "tool_call_id": label,
                "permission_subject": subject,
            },
        )()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_action():
        with pytest.raises(DeviceLeaseError) as error:
            async with manager.acquire("home:backend", lease_context("active")):
                entered.set()
                await release.wait()
        return error.value

    async def waiting_action():
        with pytest.raises(DeviceLeaseError) as error:
            async with manager.acquire("home:backend", lease_context("waiting")):
                raise AssertionError("closed connection entered backend")
        return error.value

    active = asyncio.create_task(active_action())
    await entered.wait()
    waiting = asyncio.create_task(waiting_action())
    while manager.waiting_count != 1:
        await asyncio.sleep(0)

    await pool.aclose()
    try:
        waiting_error = await asyncio.wait_for(waiting, timeout=0.5)
    finally:
        release.set()
    active_error = await asyncio.wait_for(active, timeout=0.5)

    assert pool.state(identity) is DeviceState.CLOSED
    assert waiting_error.backend_attempted is False
    assert active_error.execution_status == "outcome_unknown"
    assert active_error.backend_attempted is True
