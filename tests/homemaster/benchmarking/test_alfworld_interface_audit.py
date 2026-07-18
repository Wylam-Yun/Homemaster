from __future__ import annotations

from homemaster.benchmarking.alfworld.env_adapter import _AdapterOracleBackend
from homemaster.benchmarking.alfworld.gateway import OracleExecutionBackend
from homemaster.benchmarking.alfworld.pose_snapshot import (
    FrozenOraclePoseStore,
    OraclePoseStore,
)


def test_adapter_backend_implements_complete_gateway_protocol() -> None:
    required = {
        name
        for name, value in OracleExecutionBackend.__dict__.items()
        if callable(value) and not name.startswith("_")
    }

    assert required == {"capture_event", "send", "close"}
    assert all(callable(getattr(_AdapterOracleBackend, name, None)) for name in required)


def test_frozen_pose_store_implements_atomic_lookup_protocol() -> None:
    required = {
        name
        for name, value in OraclePoseStore.__dict__.items()
        if callable(value) and not name.startswith("_")
    }

    assert required == {"get_pose"}
    assert all(callable(getattr(FrozenOraclePoseStore, name, None)) for name in required)
