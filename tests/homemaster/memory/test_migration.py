from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from qdrant_client import QdrantClient, models
from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.cli.composition import create_home_application
from homemaster.config import HomeMasterConfig, MemoryConfig
from homemaster.memory.migration import MemoryMigrationCoordinator, MemoryMigrationError


def _legacy_files(path: Path, marker: str = "legacy") -> None:
    path.mkdir(parents=True)
    (path / "SOUL.md").write_text(f"soul-{marker}", encoding="utf-8")
    (path / "USER.md").write_text(f"user-{marker}", encoding="utf-8")
    (path / "MEMORY.md").write_text(f"memory-{marker}", encoding="utf-8")


def _sqlite(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE item (value TEXT NOT NULL)")
    connection.execute("INSERT INTO item VALUES (?)", (value,))
    connection.commit()
    connection.close()


def test_inspect_is_read_only_and_finds_historical_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    old = tmp_path / ".homemaster" / "memories"
    _legacy_files(old)
    config = MemoryConfig.model_validate({})
    coordinator = MemoryMigrationCoordinator(config)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    inspection = coordinator.inspect()

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert inspection.status == "migration_required"
    assert before == after
    assert not coordinator.lock_path.exists()
    assert not config.data_root.exists()


def test_component_migration_preserves_source_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    old_files = tmp_path / "legacy-files"
    old_history = tmp_path / "legacy-history.sqlite3"
    _legacy_files(old_files)
    _sqlite(old_history, "history-terminal-state")
    config = MemoryConfig.model_validate(
        {
            "root": old_files,
            "mem0": {"history_db_path": old_history},
        }
    )
    coordinator = MemoryMigrationCoordinator(config)

    first = coordinator.ensure_ready(auto_migrate=True)
    second = coordinator.ensure_ready(auto_migrate=True)

    assert first == second
    assert first["status"] == "completed"
    assert first["components"]["files"]["publication"] == "copied"
    assert first["components"]["history"]["publication"] == "copied"
    assert old_files.joinpath("USER.md").read_text(encoding="utf-8") == "user-legacy"
    assert config.user_path.read_text(encoding="utf-8") == "user-legacy"
    connection = sqlite3.connect(f"file:{config.history_db_path}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT value FROM item").fetchone() == (
            "history-terminal-state",
        )
    finally:
        connection.close()
    assert (
        json.loads(coordinator.manifest_path.read_text())["migration_id"] == first["migration_id"]
    )
    assert not list(config.data_root.joinpath(".staging").glob("*"))


def test_completed_manifest_rejects_missing_published_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "legacy-files"
    _legacy_files(source, "published")
    config = MemoryConfig.model_validate({"root": source})
    coordinator = MemoryMigrationCoordinator(config)
    coordinator.ensure_ready(auto_migrate=True)
    shutil.rmtree(config.files_root)

    inspection = coordinator.inspect()

    assert inspection.status == "conflict"
    assert "published" in inspection.reason
    with pytest.raises(MemoryMigrationError) as caught:
        coordinator.ensure_ready(auto_migrate=True)
    assert caught.value.code == "memory_migration_conflict"
    assert source.joinpath("MEMORY.md").read_text(encoding="utf-8") == "memory-published"


def test_source_change_during_copy_is_rejected_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "legacy-files"
    _legacy_files(source, "before")
    config = MemoryConfig.model_validate({"root": source})
    from homemaster.memory import migration as migration_module

    original_copy = migration_module._copy_component

    def copy_then_change(copy_source: Path, target: Path) -> None:
        original_copy(copy_source, target)
        copy_source.joinpath("MEMORY.md").write_text("memory-after", encoding="utf-8")

    monkeypatch.setattr(migration_module, "_copy_component", copy_then_change)

    with pytest.raises(MemoryMigrationError) as caught:
        MemoryMigrationCoordinator(config).ensure_ready(auto_migrate=True)

    assert caught.value.code == "memory_migration_source_changed"
    assert not config.files_root.exists()
    assert source.joinpath("MEMORY.md").read_text(encoding="utf-8") == "memory-after"


def test_completed_journal_cannot_republish_when_target_was_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "legacy-files"
    _legacy_files(source, "journal")
    config = MemoryConfig.model_validate({"root": source})
    coordinator = MemoryMigrationCoordinator(config)
    coordinator.ensure_ready(auto_migrate=True)
    coordinator.manifest_path.unlink()
    shutil.rmtree(config.files_root)

    with pytest.raises(MemoryMigrationError) as caught:
        coordinator.ensure_ready(auto_migrate=True)

    assert caught.value.code == "memory_migration_published_target_missing"
    assert not config.files_root.exists()
    assert source.joinpath("MEMORY.md").read_text(encoding="utf-8") == "memory-journal"


def test_existing_different_target_fails_closed_without_changing_either_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "source"
    _legacy_files(source, "source")
    config = MemoryConfig.model_validate({"root": source})
    _legacy_files(config.files_root, "target")
    source_before = source.joinpath("MEMORY.md").read_bytes()
    target_before = config.memory_path.read_bytes()

    with pytest.raises(MemoryMigrationError) as caught:
        MemoryMigrationCoordinator(config).ensure_ready(auto_migrate=True)

    assert caught.value.code == "memory_migration_conflict"
    assert source.joinpath("MEMORY.md").read_bytes() == source_before
    assert config.memory_path.read_bytes() == target_before


def test_corrupt_sqlite_is_rejected_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")
    config = MemoryConfig.model_validate({"mem0": {"history_db_path": corrupt}})

    with pytest.raises(MemoryMigrationError) as caught:
        MemoryMigrationCoordinator(config).ensure_ready(auto_migrate=True)

    assert caught.value.code == "memory_migration_invalid_sqlite"
    assert not config.history_db_path.exists()
    assert corrupt.read_bytes() == b"not sqlite"


def test_real_qdrant_component_copy_preserves_ids_payloads_and_named_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "legacy-qdrant"
    client = QdrantClient(path=str(source))
    client.create_collection(
        "memory",
        vectors_config={"dense": models.VectorParams(size=2, distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams()},
    )
    client.upsert(
        "memory",
        [
            models.PointStruct(
                id=7,
                vector={
                    "dense": [0.5, 0.25],
                    "bm25": models.SparseVector(indices=[1, 4], values=[0.7, 0.3]),
                },
                payload={"kind": "fact", "text": "terminal-state"},
            )
        ],
    )
    client.close()
    config = MemoryConfig.model_validate({"mem0": {"qdrant_path": source}})

    manifest = MemoryMigrationCoordinator(config).ensure_ready(auto_migrate=True)

    target = QdrantClient(path=str(config.qdrant_path))
    try:
        points = target.retrieve("memory", [7], with_payload=True, with_vectors=True)
        assert target.count("memory", exact=True).count == 1
        assert points[0].id == 7
        assert points[0].payload == {"kind": "fact", "text": "terminal-state"}
        assert set(points[0].vector) == {"dense", "bm25"}
    finally:
        target.close()
    assert manifest["components"]["qdrant"]["publication"] == "copied"


def test_memory_migrate_cli_returns_typed_receipt_and_terminal_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / ".homemaster" / "memories"
    _legacy_files(source, "cli")
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text("memory:\n  enabled: true\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["memory", "migrate", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "PASS"
    target = tmp_path / ".homemaster" / "memory" / "files" / "MEMORY.md"
    assert target.read_text(encoding="utf-8") == "memory-cli"


def test_memory_migrate_cli_conflict_is_nonzero_and_preserves_old_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / ".homemaster" / "memories"
    target = tmp_path / ".homemaster" / "memory" / "files"
    _legacy_files(source, "source")
    _legacy_files(target, "target")
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text("memory:\n  enabled: true\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["memory", "migrate", "--config", str(config_path)])

    assert result.exit_code == 1
    receipt = json.loads(result.stderr)
    assert receipt["status"] == "FAIL"
    assert receipt["code"] == "memory_migration_conflict"
    assert source.joinpath("MEMORY.md").read_text(encoding="utf-8") == "memory-source"
    assert target.joinpath("MEMORY.md").read_text(encoding="utf-8") == "memory-target"


@pytest.mark.asyncio
async def test_application_start_migrates_before_opening_owned_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "legacy-files"
    _legacy_files(source, "application")
    config = HomeMasterConfig.model_validate(
        {
            "memory": {"root": source},
            "runtime": {"runtime_root": tmp_path / "runs"},
            "observability": {"session_dir": str(tmp_path / "sessions")},
        }
    )
    bundle = create_home_application(config=config, run_label="migration-entry")

    await bundle.application.start()
    try:
        assert config.memory.memory_path.read_text(encoding="utf-8") == "memory-application"
        assert config.memory.evidence_db_path.is_file()
        migration = bundle.application.settings.application_services["memory_migration"]
        assert migration.inspect().status == "ready"
    finally:
        await bundle.application.aclose()
