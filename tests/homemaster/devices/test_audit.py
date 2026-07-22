from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

from homemaster.devices import (
    DeviceAuditLog,
    DeviceControlReceipt,
    DeviceIdentity,
    DeviceLeaseManager,
    DeviceState,
    DeviceStateObservation,
    InMemoryDeviceEventStore,
)
from homemaster.tools.contracts import PermissionSubject


@pytest.mark.asyncio
async def test_device_events_are_persisted_as_mode_0600_jsonl(tmp_path) -> None:
    path = tmp_path / "audit" / "device_audit.jsonl"
    store = InMemoryDeviceEventStore(DeviceAuditLog(path))
    manager = DeviceLeaseManager(event_store=store)
    identity = DeviceIdentity("tenant", "device", "backend")
    context = SimpleNamespace(
        backend=SimpleNamespace(device_identity=identity, generation=0),
        session_id="session",
        run_id="run",
        tool_call_id="call",
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="gateway",
            tenant_id="tenant",
            capabilities=("device.control",),
        ),
    )

    assert not path.exists()
    async with manager.acquire("home:backend", context):
        pass

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event_type"] for row in rows] == [
        "device.lease.acquired",
        "device.lease.released",
    ]
    assert all(row["tenant_id"] == "tenant" for row in rows)
    assert all(row["device_id"] == "device" for row in rows)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_emergency_stop_audit_preserves_both_normalized_return_codes(tmp_path) -> None:
    path = tmp_path / "audit" / "device_audit.jsonl"
    store = InMemoryDeviceEventStore(DeviceAuditLog(path))
    manager = DeviceLeaseManager(event_store=store)

    class Backend:
        device_identity = DeviceIdentity("tenant", "device", "backend")
        generation = 0

        async def emergency_stop(self, *, reason: str, generation: int):
            del reason, generation
            return DeviceControlReceipt(succeeded=True, return_code="control-ok")

        async def read_device_state(self):
            return DeviceStateObservation(
                query_succeeded=True,
                state=DeviceState.STOPPED,
                return_code="query-ok",
            )

    subject = PermissionSubject(
        subject_id="operator",
        channel="gateway",
        tenant_id="tenant",
        capabilities=("device.control",),
    )

    result = await manager.emergency_stop(
        Backend(),
        permission_subject=subject,
        reason="operator requested",
    )

    assert result.succeeded is True
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    completed = rows[-1]
    assert completed["event_type"] == "device.emergency_stop.completed"
    assert completed["state"] == "stopped"
    assert completed["control_return_code"] == "control-ok"
    assert completed["state_return_code"] == "query-ok"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
