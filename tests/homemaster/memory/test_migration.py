from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from homemaster.application.composition import compose_application
from homemaster.cli.app import app
from homemaster.config import HomeMasterConfig, MemoryConfig
from homemaster.memory.evidence import MemoryEvidenceLedger
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.managed_neo4j import ManagedNeo4jRuntime
from homemaster.memory.migration import (
    LEGACY_MIGRATION_SCHEMA,
    MIGRATION_SCHEMA,
    MemoryMigrationCoordinator,
    MemoryMigrationError,
)
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS


def _legacy_files(path: Path, marker: str = "legacy") -> None:
    path.mkdir(parents=True)
    (path / "SOUL.md").write_text(f"soul-{marker}", encoding="utf-8")
    (path / "USER.md").write_text(f"user-{marker}", encoding="utf-8")
    (path / "MEMORY.md").write_text(f"memory-{marker}", encoding="utf-8")


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
    _legacy_files(old_files)
    config = MemoryConfig.model_validate({"root": old_files})
    coordinator = MemoryMigrationCoordinator(config)

    first = coordinator.ensure_ready(auto_migrate=True)
    second = coordinator.ensure_ready(auto_migrate=True)

    assert first == second
    assert first["status"] == "completed"
    assert first["components"]["files"]["publication"] == "copied"
    assert set(first["components"]) == {"files", "evidence"}
    assert old_files.joinpath("USER.md").read_text(encoding="utf-8") == "user-legacy"
    assert config.user_path.read_text(encoding="utf-8") == "user-legacy"
    assert (
        json.loads(coordinator.manifest_path.read_text())["migration_id"] == first["migration_id"]
    )
    assert not list(config.data_root.joinpath(".staging").glob("*"))


def test_legacy_manifest_upgrade_accepts_mount_alias_and_preserves_audit(
    tmp_path: Path,
) -> None:
    physical_root = tmp_path / "physical" / "memory"
    physical_root.mkdir(parents=True)
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(tmp_path / "physical", target_is_directory=True)
    config = MemoryConfig.model_validate({"data_root": alias_parent / "memory"})
    migration_id = "legacy-four-component"
    components = {
        name: {
            "source": str(physical_root / source),
            "target": str(physical_root / target),
            "status": "completed",
            "publication": "absent",
            "sha256": None,
        }
        for name, source, target in (
            ("files", "../memories", "files"),
            ("qdrant", "qdrant", "qdrant"),
            ("history", "history.sqlite3", "history.sqlite3"),
            ("evidence", "evidence.sqlite3", "evidence.sqlite3"),
        )
    }
    legacy = {
        "schema_version": LEGACY_MIGRATION_SCHEMA,
        "status": "completed",
        "migration_id": migration_id,
        "data_root": str(physical_root),
        "components": components,
    }
    for name in ("migration-manifest.json", "migration-journal.json"):
        physical_root.joinpath(name).write_text(json.dumps(legacy), encoding="utf-8")
    coordinator = MemoryMigrationCoordinator(config)

    inspection = coordinator.inspect()
    upgraded = coordinator.ensure_ready(auto_migrate=True)

    assert inspection.status == "migration_required"
    assert upgraded["schema_version"] == MIGRATION_SCHEMA
    assert upgraded["upgraded_from"] == {
        "schema_version": LEGACY_MIGRATION_SCHEMA,
        "migration_id": migration_id,
    }
    assert set(upgraded["components"]) == {"files", "evidence"}
    assert coordinator.inspect().status == "ready"
    for name in ("migration-manifest", "migration-journal"):
        audit = physical_root / f"{name}.v1.{migration_id}.json"
        assert json.loads(audit.read_text(encoding="utf-8")) == legacy


def test_legacy_manifest_upgrade_rejects_unknown_component_shape(tmp_path: Path) -> None:
    config = MemoryConfig.model_validate({"data_root": tmp_path / "memory"})
    config.data_root.mkdir()
    invalid = {
        "schema_version": LEGACY_MIGRATION_SCHEMA,
        "status": "completed",
        "migration_id": "unknown-shape",
        "data_root": str(config.data_root),
        "components": {"files": {}},
    }
    config.data_root.joinpath("migration-manifest.json").write_text(
        json.dumps(invalid), encoding="utf-8"
    )

    inspection = MemoryMigrationCoordinator(config).inspect()

    assert inspection.status == "conflict"
    assert "components are invalid" in inspection.reason


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

    # The contract under test is ordering — migration completes before owned
    # stores open — so isolate the full application-owned external resource
    # closure (managed Neo4j process, MindMemOS backends, mindmemos imports)
    # rather than requiring a live installation.
    @dataclasses.dataclass
    class _MemoryRequestContext:
        request_id: str
        account_id: str
        project_id: str
        api_key_uuid: str
        user_id: str
        app_id: str
        session_id: str | None
        agent_id: str

    mindmemos_pkg = types.ModuleType("mindmemos")
    mindmemos_typing = types.ModuleType("mindmemos.typing")
    mindmemos_typing.MemoryRequestContext = _MemoryRequestContext  # type: ignore[attr-defined]
    mindmemos_pkg.typing = mindmemos_typing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mindmemos", mindmemos_pkg)
    monkeypatch.setitem(sys.modules, "mindmemos.typing", mindmemos_typing)

    opened: list[str] = []
    real_ensure_ready = MemoryMigrationCoordinator.ensure_ready
    real_file_start = FileMemoryStore.start
    real_evidence_start = MemoryEvidenceLedger.start

    def _recording_ensure_ready(
        self: MemoryMigrationCoordinator, *, auto_migrate: bool = True
    ) -> dict:
        result = real_ensure_ready(self, auto_migrate=auto_migrate)
        opened.append("migration")
        return result

    def _recording_file_start(self: FileMemoryStore) -> None:
        opened.append("file_store")
        real_file_start(self)

    def _recording_evidence_start(self: MemoryEvidenceLedger) -> None:
        opened.append("evidence")
        real_evidence_start(self)

    async def _recording_neo4j_start(self: ManagedNeo4jRuntime) -> None:
        opened.append("neo4j")

    async def _recording_mindmemos_start(self: EmbeddedMindMemOS) -> None:
        opened.append("mindmemos")
        self._qdrant = object()

    async def _noop_close(self: object) -> None:
        return None

    monkeypatch.setattr(
        MemoryMigrationCoordinator, "ensure_ready", _recording_ensure_ready
    )
    monkeypatch.setattr(FileMemoryStore, "start", _recording_file_start)
    monkeypatch.setattr(MemoryEvidenceLedger, "start", _recording_evidence_start)
    monkeypatch.setattr(ManagedNeo4jRuntime, "start", _recording_neo4j_start)
    monkeypatch.setattr(ManagedNeo4jRuntime, "close", _noop_close)
    monkeypatch.setattr(EmbeddedMindMemOS, "start", _recording_mindmemos_start)
    monkeypatch.setattr(EmbeddedMindMemOS, "close", _noop_close)

    bundle = compose_application(config=config, run_label="migration-entry")

    await bundle.application.start()
    try:
        assert config.memory.memory_path.read_text(encoding="utf-8") == "memory-application"
        assert config.memory.evidence_db_path.is_file()
        migration = bundle.application.settings.application_services["memory_migration"]
        assert migration.inspect().status == "ready"
        assert opened[:4] == ["migration", "file_store", "evidence", "neo4j"]
        assert opened.index("mindmemos") > opened.index("neo4j")
    finally:
        await bundle.application.aclose()
