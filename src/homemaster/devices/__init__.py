"""Physical-device ownership, connection, lease, and stop contracts."""

from homemaster.devices.audit import DeviceAuditLog
from homemaster.devices.connection_pool import (
    DeviceConnectionBinding,
    DeviceConnectionCleanupError,
    DeviceConnectionHandle,
    DeviceConnectionPool,
)
from homemaster.devices.contracts import (
    DeviceAuditFailure,
    DeviceControlReceipt,
    DeviceEvent,
    DeviceIdentity,
    DeviceState,
    DeviceStateObservation,
    EmergencyStopResult,
    InMemoryDeviceEventStore,
)
from homemaster.devices.lease_manager import (
    DeviceLeaseError,
    DeviceLeaseManager,
)

__all__ = [
    "DeviceAuditLog",
    "DeviceAuditFailure",
    "DeviceConnectionBinding",
    "DeviceConnectionCleanupError",
    "DeviceConnectionHandle",
    "DeviceConnectionPool",
    "DeviceControlReceipt",
    "DeviceEvent",
    "DeviceIdentity",
    "DeviceLeaseError",
    "DeviceLeaseManager",
    "DeviceState",
    "DeviceStateObservation",
    "EmergencyStopResult",
    "InMemoryDeviceEventStore",
]
