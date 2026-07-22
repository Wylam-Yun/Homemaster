from __future__ import annotations

import stat

import pytest

from homemaster.artifacts.tool_output_store import (
    ArtifactAccessDeniedError,
    ArtifactExpiredError,
    ArtifactQuotaExceededError,
    ToolOutputStore,
)


def test_store_partitions_raw_bytes_and_enforces_exact_acl(tmp_path) -> None:
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=1024, ttl_seconds=60)
    stored = store.write(
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        content=b"raw-secret-output",
        media_type="text/plain",
    )

    assert stored.handle.startswith("hm-artifact:")
    assert all(value not in stored.handle for value in ("tenant-a", "session-a", "run-a"))
    assert store.read(
        stored.handle,
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
    ) == b"raw-secret-output"
    for wrong in (
        {"tenant_id": "tenant-b", "session_id": "session-a", "run_id": "run-a"},
        {"tenant_id": "tenant-a", "session_id": "session-b", "run_id": "run-a"},
        {"tenant_id": "tenant-a", "session_id": "session-a", "run_id": "run-b"},
    ):
        with pytest.raises(ArtifactAccessDeniedError):
            store.read(stored.handle, **wrong)

    files = [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
    assert files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)


def test_store_rejects_quota_before_writing_new_bytes(tmp_path) -> None:
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=8, ttl_seconds=60)
    store.write(
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        content=b"12345678",
        media_type="application/octet-stream",
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(ArtifactQuotaExceededError):
        store.write(
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-a",
            content=b"9",
            media_type="application/octet-stream",
        )

    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_store_expires_and_removes_raw_artifact(tmp_path) -> None:
    now = [100.0]
    store = ToolOutputStore(
        tmp_path / "artifacts",
        quota_bytes=1024,
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    stored = store.write(
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        content=b"expires",
        media_type="text/plain",
    )
    now[0] = 111.0

    with pytest.raises(ArtifactExpiredError):
        store.read(
            stored.handle,
            tenant_id="tenant-a",
            session_id="session-a",
            run_id="run-a",
        )

    assert not any(path.suffix == ".blob" for path in (tmp_path / "artifacts").rglob("*"))
