"""Application-owned physical-device connection pool."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homemaster.devices.contracts import DeviceIdentity, DeviceState
from homemaster.devices.lease_manager import (
    DeviceLeaseError,
    DeviceLeaseManager,
    device_identity_from_backend,
)


class DeviceConnectionCleanupError(RuntimeError):
    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        self.errors = errors
        super().__init__(
            "device connection cleanup failed: "
            + "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        )


class DeviceConnectionOwnership(StrEnum):
    OWNED = "owned"
    BORROWED = "borrowed"


@dataclass(frozen=True)
class DeviceConnectionBinding:
    identity: DeviceIdentity
    connection: Any
    generation: int = 0
    ownership: DeviceConnectionOwnership = DeviceConnectionOwnership.OWNED

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DeviceIdentity):
            raise TypeError("connection identity must be DeviceIdentity")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("connection generation must be non-negative")
        if not isinstance(self.ownership, DeviceConnectionOwnership):
            raise TypeError("connection ownership must be DeviceConnectionOwnership")


@dataclass
class _Entry:
    binding: DeviceConnectionBinding
    generation: int
    state: DeviceState = DeviceState.READY
    closed: bool = False


class DeviceConnectionHandle:
    """Tenant-pinned view of a connection registered in the application pool."""

    def __init__(self, entry: _Entry) -> None:
        self._entry = entry

    @property
    def actual_backend(self) -> Any:
        return self._entry.binding.connection

    @property
    def device_identity(self) -> DeviceIdentity:
        return self._entry.binding.identity

    @property
    def generation(self) -> int:
        return self._entry.generation

    @property
    def backend_generation(self) -> int:
        """Return the backend's run generation without weakening pool fencing."""
        return self.actual_backend.generation

    @property
    def backend_id(self) -> str:
        return self.device_identity.backend_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.actual_backend, name)


class DeviceConnectionPool:
    """Hold one connection per exact tenant/device/backend identity."""

    def __init__(self, lease_manager: DeviceLeaseManager | None = None) -> None:
        self._entries: dict[tuple[str, str, str], _Entry] = {}
        self._physical_entries: dict[tuple[str, str], _Entry] = {}
        self._closed = False
        self._lease_manager = lease_manager

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def lease_manager(self) -> DeviceLeaseManager | None:
        return self._lease_manager

    def bind_lease_manager(self, lease_manager: DeviceLeaseManager) -> None:
        if not isinstance(lease_manager, DeviceLeaseManager):
            raise TypeError("lease_manager must be DeviceLeaseManager")
        if self._lease_manager is not None and self._lease_manager is not lease_manager:
            raise ValueError("device connection pool already uses another lease manager")
        self._lease_manager = lease_manager

    def register(self, binding: DeviceConnectionBinding) -> None:
        if self._closed:
            raise RuntimeError("device connection pool is closed")
        if not isinstance(binding, DeviceConnectionBinding):
            raise TypeError("binding must be DeviceConnectionBinding")
        if binding.identity.key in self._entries:
            raise ValueError("device connection identity is already registered")
        physical_key = (binding.identity.device_id, binding.identity.backend_id)
        existing = self._physical_entries.get(physical_key)
        if existing is not None:
            if existing.binding.identity.tenant_id != binding.identity.tenant_id:
                raise DeviceLeaseError(
                    "cross_tenant_device",
                    "physical device is already bound to another tenant",
                )
            raise ValueError("physical device connection is already registered")
        entry = _Entry(binding, generation=binding.generation)
        self._entries[binding.identity.key] = entry
        self._physical_entries[physical_key] = entry

    def bind_borrowed(self, connection: Any, *, tenant_id: str) -> DeviceConnectionHandle:
        if self._closed:
            raise RuntimeError("device connection pool is closed")
        if isinstance(connection, DeviceConnectionHandle):
            if connection.device_identity.tenant_id != tenant_id:
                raise DeviceLeaseError(
                    "cross_tenant_device",
                    "physical device is already bound to another tenant",
                )
            return connection
        identity = device_identity_from_backend(connection, tenant_id)
        physical_key = (identity.device_id, identity.backend_id)
        existing = self._physical_entries.get(physical_key)
        if existing is not None:
            if existing.binding.identity.tenant_id != tenant_id:
                raise DeviceLeaseError(
                    "cross_tenant_device",
                    "physical device is already bound to another tenant",
                )
            if existing.binding.connection is not connection:
                raise DeviceLeaseError(
                    "device_connection_conflict",
                    "physical device already has another active connection",
                )
            return DeviceConnectionHandle(existing)
        generation = getattr(connection, "generation", 0)
        self.register(
            DeviceConnectionBinding(
                identity=identity,
                connection=connection,
                generation=generation,
                ownership=DeviceConnectionOwnership.BORROWED,
            )
        )
        return DeviceConnectionHandle(self._entries[identity.key])

    def get(
        self,
        identity: DeviceIdentity,
        *,
        tenant_id: str,
        expected_generation: int | None = None,
    ) -> Any:
        if identity.tenant_id != tenant_id:
            raise PermissionError("cross-tenant device access denied")
        entry = self._entries.get(identity.key)
        if entry is None:
            raise KeyError("device connection is not registered")
        if entry.state is not DeviceState.READY:
            raise RuntimeError(f"device connection is {entry.state.value}")
        if expected_generation is not None and entry.generation != expected_generation:
            raise RuntimeError(
                "stale device connection generation: "
                f"expected {expected_generation}, current {entry.generation}"
            )
        return entry.binding.connection

    def state(self, identity: DeviceIdentity) -> DeviceState:
        entry = self._entries.get(identity.key)
        if entry is None:
            raise KeyError("device connection is not registered")
        return entry.state

    async def mark_disconnected(
        self,
        identity: DeviceIdentity,
        *,
        requested_by: str,
        reason: str,
        resource_key: str = "home:backend",
    ) -> int:
        return await self.disconnect(
            identity,
            requested_by=requested_by,
            reason=reason,
            resource_key=resource_key,
        )

    async def disconnect(
        self,
        identity: DeviceIdentity,
        *,
        requested_by: str,
        reason: str,
        resource_key: str = "home:backend",
    ) -> int:
        entry = self._entries.get(identity.key)
        if entry is None:
            raise KeyError("device connection is not registered")
        generation = entry.generation + 1
        if self._lease_manager is not None:
            generation = await self._lease_manager.fence_next(
                identity,
                generation_floor=entry.generation,
                state=DeviceState.DISCONNECTED,
                requested_by=requested_by,
                reason=reason,
                resource_key=resource_key,
            )
        entry.generation = generation
        entry.state = DeviceState.DISCONNECTED
        return generation

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        for entry in reversed(tuple(self._entries.values())):
            if entry.closed:
                continue
            try:
                if self._lease_manager is not None:
                    entry.generation = await self._lease_manager.fence_next(
                        entry.binding.identity,
                        generation_floor=entry.generation,
                        state=DeviceState.CLOSED,
                        requested_by="application",
                        reason="application connection pool closed",
                    )
                else:
                    entry.generation += 1
                if entry.binding.ownership is DeviceConnectionOwnership.OWNED:
                    await _close(entry.binding.connection)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                errors.append(exc)
            finally:
                entry.closed = True
                entry.state = DeviceState.CLOSED
        if errors:
            raise DeviceConnectionCleanupError(tuple(errors))


async def _close(connection: Any) -> None:
    closer = getattr(connection, "aclose", None) or getattr(connection, "close", None)
    if not callable(closer):
        return
    value = closer()
    if inspect.isawaitable(value):
        await value


__all__ = [
    "DeviceConnectionBinding",
    "DeviceConnectionCleanupError",
    "DeviceConnectionHandle",
    "DeviceConnectionOwnership",
    "DeviceConnectionPool",
]
