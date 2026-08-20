"""Recoverable component migration into HomeMaster's single memory data root."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from homemaster.config import MemoryConfig

logger = logging.getLogger(__name__)

LEGACY_MIGRATION_SCHEMA = "homemaster-memory-migration-v1"
MIGRATION_SCHEMA = "homemaster-memory-migration-v2"
ComponentName = Literal["files", "evidence"]
LEGACY_COMPONENT_SETS = (
    frozenset({"files", "evidence"}),
    frozenset({"files", "qdrant", "history", "evidence"}),
)


class MemoryMigrationError(RuntimeError):
    def __init__(self, code: str, message: str, **details: object) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class MemoryMigrationInspection:
    status: Literal["ready", "migration_required", "conflict"]
    data_root: Path
    journal_path: Path
    manifest_path: Path
    legacy_fields: tuple[str, ...]
    reason: str


class MemoryMigrationCoordinator:
    """Own planning, locking, recovery, verification and publication for one data root."""

    def __init__(self, config: MemoryConfig) -> None:
        self.config = config
        self.data_root = config.data_root
        self.journal_path = self.data_root / "migration-journal.json"
        self.manifest_path = self.data_root / "migration-manifest.json"
        self.lock_path = self.data_root.parent / ".memory-migration.lock"

    def inspect(self) -> MemoryMigrationInspection:
        """Inspect migration state without creating, locking or opening any store."""

        legacy = self.config.migration_spec.explicit_legacy_fields
        if self.journal_path.exists():
            try:
                journal = _read_json(self.journal_path)
            except MemoryMigrationError as exc:
                return self._inspection("conflict", f"invalid migration journal: {exc}")
            if journal.get("status") != "completed":
                return self._inspection(
                    "migration_required", "an incomplete migration journal exists"
                )
        if self.manifest_path.exists():
            try:
                manifest = _read_json(self.manifest_path)
                if manifest.get("schema_version") == LEGACY_MIGRATION_SCHEMA:
                    self._validate_legacy_manifest(manifest, deep=False)
                    return self._inspection(
                        "migration_required", "legacy migration manifest requires upgrade"
                    )
                self._validate_completed_manifest(manifest, deep=False)
            except MemoryMigrationError as exc:
                return self._inspection("conflict", f"invalid migration manifest: {exc}")
            return self._inspection("ready", "completed migration manifest verified")

        files_source = self.config.migration_spec.files_source
        if files_source != self.config.files_root and files_source.exists():
            if self.config.files_root.exists() and not _same_inventory(
                files_source, self.config.files_root
            ):
                return self._inspection("conflict", "legacy and target file memories differ")
            return self._inspection("migration_required", "legacy file memory requires publication")
        if legacy:
            return self._inspection("migration_required", "legacy path fields require verification")
        return self._inspection("ready", "no legacy memory migration is required")

    def ensure_ready(self, *, auto_migrate: bool = True) -> dict[str, Any]:
        inspection = self.inspect()
        if inspection.status == "conflict":
            raise MemoryMigrationError("memory_migration_conflict", inspection.reason)
        if inspection.status == "migration_required" and not auto_migrate:
            raise MemoryMigrationError("memory_migration_required", inspection.reason)

        self.data_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._exclusive_lock():
            self.data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.data_root, 0o700)
            if self.manifest_path.exists():
                manifest = _read_json(self.manifest_path)
                if manifest.get("schema_version") == LEGACY_MIGRATION_SCHEMA:
                    return self._upgrade_legacy_manifest(manifest)
                self._validate_completed_manifest(manifest, deep=True)
                return manifest
            journal = self._load_or_plan_journal()
            for name in ("files", "evidence"):
                component = journal["components"][name]
                if component["status"] == "completed":
                    continue
                self._migrate_component(name, component, journal)
            journal["status"] = "completed"
            _atomic_json(self.journal_path, journal)
            manifest = {
                "schema_version": MIGRATION_SCHEMA,
                "status": "completed",
                "migration_id": journal["migration_id"],
                "data_root": str(self.data_root),
                "legacy_fields": list(self.config.migration_spec.explicit_legacy_fields),
                "components": journal["components"],
            }
            _atomic_json(self.manifest_path, manifest)
            self._validate_completed_manifest(manifest, deep=True)
            logger.info(
                json.dumps(
                    {
                        "event": "memory.migration.completed",
                        "migration_id": journal["migration_id"],
                        "data_root": str(self.data_root),
                    },
                    sort_keys=True,
                )
            )
            return manifest

    def _inspection(
        self, status: Literal["ready", "migration_required", "conflict"], reason: str
    ) -> MemoryMigrationInspection:
        return MemoryMigrationInspection(
            status,
            self.data_root,
            self.journal_path,
            self.manifest_path,
            self.config.migration_spec.explicit_legacy_fields,
            reason,
        )

    def _load_or_plan_journal(self) -> dict[str, Any]:
        expected = self._component_paths()
        if self.journal_path.exists():
            journal = _read_json(self.journal_path)
            self._validate_identity(journal)
            observed = {
                name: (item.get("source"), item.get("target"))
                for name, item in journal.get("components", {}).items()
            }
            planned = {
                name: (str(source), str(target)) for name, (source, target) in expected.items()
            }
            if set(observed) != set(planned) or any(
                not _path_pair_equivalent(observed[name], planned[name]) for name in planned
            ):
                raise MemoryMigrationError(
                    "memory_migration_plan_changed",
                    "migration sources or targets changed after the journal was created",
                )
            return journal

        migration_id = uuid.uuid4().hex
        journal = {
            "schema_version": MIGRATION_SCHEMA,
            "status": "in_progress",
            "migration_id": migration_id,
            "data_root": str(self.data_root),
            "components": {
                name: {
                    "source": str(source),
                    "target": str(target),
                    "status": "pending",
                    "publication": None,
                    "sha256": None,
                }
                for name, (source, target) in expected.items()
            },
        }
        _atomic_json(self.journal_path, journal)
        return journal

    def _component_paths(self) -> dict[ComponentName, tuple[Path, Path]]:
        spec = self.config.migration_spec
        return {
            "files": (spec.files_source, self.config.files_root),
            "evidence": (spec.evidence_source, self.config.evidence_db_path),
        }

    def _migrate_component(
        self, name: ComponentName, component: dict[str, Any], journal: dict[str, Any]
    ) -> None:
        source = Path(component["source"])
        target = Path(component["target"])
        explicit = self._component_is_explicit(name)
        if source == target:
            if source.exists():
                self._validate_component(name, source)
                digest = self._component_digest(name, source)
                publication = "verified_in_place"
            else:
                digest = None
                publication = "absent"
            self._complete_component(component, journal, publication, digest)
            return
        if not source.exists():
            if explicit:
                raise MemoryMigrationError(
                    "memory_migration_source_missing",
                    f"configured legacy {name} source does not exist",
                    source=str(source),
                )
            self._complete_component(component, journal, "absent", None)
            return

        with self._source_guard(name, source):
            self._validate_component(name, source)
            source_digest = self._component_digest(name, source)
            if target.exists():
                self._validate_component(name, target)
                if source_digest != self._component_digest(name, target):
                    raise MemoryMigrationError(
                        "memory_migration_target_conflict",
                        f"target {name} already exists with different data",
                        source=str(source),
                        target=str(target),
                    )
                self._complete_component(component, journal, "matched_existing", source_digest)
                return

            staging = self.data_root / ".staging" / f"{name}-{journal['migration_id']}"
            if staging.exists() and self._component_digest(name, staging) != source_digest:
                if staging.is_dir():
                    shutil.rmtree(staging)
                else:
                    staging.unlink()
            if not staging.exists():
                staging.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _copy_component(source, staging)
            self._validate_component(name, staging)
            if self._component_digest(name, staging) != source_digest:
                raise MemoryMigrationError(
                    "memory_migration_copy_mismatch", f"staged {name} checksum mismatch"
                )
            if self._component_digest(name, source) != source_digest:
                raise MemoryMigrationError(
                    "memory_migration_source_changed",
                    f"legacy {name} changed while its migration snapshot was copied",
                    source=str(source),
                )
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(staging, target)
            _fsync_directory(target.parent)
            self._validate_component(name, target)
            if self._component_digest(name, target) != source_digest:
                raise MemoryMigrationError(
                    "memory_migration_publish_mismatch", f"published {name} checksum mismatch"
                )
            self._complete_component(component, journal, "copied", source_digest)

    def _complete_component(
        self,
        component: dict[str, Any],
        journal: dict[str, Any],
        publication: str,
        digest: str | None,
    ) -> None:
        component.update(status="completed", publication=publication, sha256=digest)
        _atomic_json(self.journal_path, journal)

    def _component_is_explicit(self, name: ComponentName) -> bool:
        fields = set(self.config.migration_spec.explicit_legacy_fields)
        return {
            "files": "memory.root",
            "evidence": "memory.evidence_db_path",
        }[name] in fields

    def _validate_component(self, name: ComponentName, path: Path) -> None:
        if name == "files":
            if not path.is_dir():
                raise MemoryMigrationError(
                    "memory_migration_invalid_files", "file memory is not a directory"
                )
            for filename in (
                self.config.soul_file,
                self.config.user_file,
                self.config.memory_file,
            ):
                try:
                    (path / filename).read_bytes()
                except OSError as exc:
                    raise MemoryMigrationError(
                        "memory_migration_invalid_files", f"file memory is missing {filename}"
                    ) from exc
        elif name == "evidence":
            _validate_sqlite(path)

    def _validate_identity(self, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != MIGRATION_SCHEMA:
            raise MemoryMigrationError("memory_migration_schema", "unknown migration schema")
        if not _path_equivalent(payload.get("data_root"), self.data_root):
            raise MemoryMigrationError(
                "memory_migration_root_changed", "migration data root changed"
            )

    def _validate_legacy_manifest(self, payload: dict[str, Any], *, deep: bool) -> None:
        if payload.get("schema_version") != LEGACY_MIGRATION_SCHEMA:
            raise MemoryMigrationError("memory_migration_schema", "unknown migration schema")
        if not _path_equivalent(payload.get("data_root"), self.data_root):
            raise MemoryMigrationError(
                "memory_migration_root_changed", "migration data root changed"
            )
        if payload.get("status") != "completed":
            raise MemoryMigrationError(
                "memory_migration_manifest_incomplete", "migration manifest is not completed"
            )
        components = payload.get("components")
        if not isinstance(components, dict) or frozenset(components) not in LEGACY_COMPONENT_SETS:
            raise MemoryMigrationError(
                "memory_migration_manifest_components",
                "legacy migration manifest components are invalid",
            )
        for name, item in components.items():
            if not isinstance(item, dict) or item.get("status") != "completed":
                raise MemoryMigrationError(
                    "memory_migration_manifest_components",
                    f"legacy migration manifest component {name} is incomplete",
                )
            publication = item.get("publication")
            digest = item.get("sha256")
            if publication == "absent":
                if digest is not None:
                    raise MemoryMigrationError(
                        "memory_migration_manifest_digest", f"absent {name} has a digest"
                    )
                continue
            if publication not in {"verified_in_place", "matched_existing", "copied"}:
                raise MemoryMigrationError(
                    "memory_migration_manifest_components",
                    f"legacy migration manifest component {name} has an invalid publication",
                )
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise MemoryMigrationError(
                    "memory_migration_manifest_digest",
                    f"legacy published {name} has an invalid digest",
                )
            target = item.get("target")
            if not isinstance(target, str) or not Path(target).exists():
                raise MemoryMigrationError(
                    "memory_migration_published_target_missing",
                    f"legacy published {name} target is missing",
                    target=target,
                )
            if deep:
                _validate_legacy_component(name, Path(target))

    def _upgrade_legacy_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        self._validate_legacy_manifest(manifest, deep=True)
        migration_id = manifest.get("migration_id")
        if not isinstance(migration_id, str) or not migration_id:
            raise MemoryMigrationError(
                "memory_migration_manifest_identity", "legacy migration ID is invalid"
            )
        self._preserve_legacy_audit(self.manifest_path, manifest, migration_id)
        if self.journal_path.exists():
            journal = _read_json(self.journal_path)
            if journal.get("schema_version") == LEGACY_MIGRATION_SCHEMA:
                self._validate_legacy_manifest(journal, deep=True)
                if journal.get("migration_id") != migration_id:
                    raise MemoryMigrationError(
                        "memory_migration_manifest_identity",
                        "legacy migration journal and manifest IDs differ",
                    )
                self._preserve_legacy_audit(self.journal_path, journal, migration_id)

        upgraded_id = uuid.uuid4().hex
        components: dict[str, dict[str, Any]] = {}
        for name, (source, target) in self._component_paths().items():
            if target.exists():
                self._validate_component(name, target)
                publication = "verified_in_place"
                digest = self._component_digest(name, target)
            else:
                publication = "absent"
                digest = None
            components[name] = {
                "source": str(source),
                "target": str(target),
                "status": "completed",
                "publication": publication,
                "sha256": digest,
            }
        journal = {
            "schema_version": MIGRATION_SCHEMA,
            "status": "completed",
            "migration_id": upgraded_id,
            "data_root": str(self.data_root),
            "components": components,
            "upgraded_from": {
                "schema_version": LEGACY_MIGRATION_SCHEMA,
                "migration_id": migration_id,
            },
        }
        manifest = {
            **journal,
            "legacy_fields": list(self.config.migration_spec.explicit_legacy_fields),
        }
        _atomic_json(self.journal_path, journal)
        _atomic_json(self.manifest_path, manifest)
        self._validate_completed_manifest(manifest, deep=True)
        return manifest

    def _preserve_legacy_audit(
        self, path: Path, payload: dict[str, Any], migration_id: str
    ) -> None:
        stem = path.name.removesuffix(".json")
        backup = path.with_name(f"{stem}.v1.{migration_id}.json")
        if backup.exists():
            if _read_json(backup) != payload:
                raise MemoryMigrationError(
                    "memory_migration_audit_conflict", "legacy migration audit backup conflicts"
                )
            return
        _atomic_json(backup, payload)

    def _validate_completed_manifest(self, payload: dict[str, Any], *, deep: bool) -> None:
        self._validate_identity(payload)
        if payload.get("status") != "completed":
            raise MemoryMigrationError(
                "memory_migration_manifest_incomplete", "migration manifest is not completed"
            )
        components = payload.get("components")
        expected = self._component_paths()
        if not isinstance(components, dict) or set(components) != set(expected):
            raise MemoryMigrationError(
                "memory_migration_manifest_components", "migration manifest components are invalid"
            )
        for name, (source, target) in expected.items():
            item = components[name]
            if not isinstance(item, dict) or item.get("status") != "completed":
                raise MemoryMigrationError(
                    "memory_migration_manifest_components",
                    f"migration manifest component {name} is incomplete",
                )
            if not _path_pair_equivalent(
                (item.get("source"), item.get("target")), (str(source), str(target))
            ):
                raise MemoryMigrationError(
                    "memory_migration_plan_changed",
                    "migration sources or targets changed after publication",
                )
            publication = item.get("publication")
            digest = item.get("sha256")
            if publication == "absent":
                if digest is not None:
                    raise MemoryMigrationError(
                        "memory_migration_manifest_digest", f"absent {name} has a digest"
                    )
                continue
            if publication not in {"verified_in_place", "matched_existing", "copied"}:
                raise MemoryMigrationError(
                    "memory_migration_manifest_components",
                    f"migration manifest component {name} has an invalid publication",
                )
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise MemoryMigrationError(
                    "memory_migration_manifest_digest", f"published {name} has an invalid digest"
                )
            if not target.exists():
                raise MemoryMigrationError(
                    "memory_migration_published_target_missing",
                    f"published {name} target is missing",
                    target=str(target),
                )
            if deep:
                self._validate_component(name, target)
            else:
                self._validate_component_read_only(name, target)

    def _validate_component_read_only(self, name: ComponentName, path: Path) -> None:
        self._validate_component(name, path)

    def _component_digest(self, name: ComponentName, path: Path) -> str:
        if name == "evidence":
            return _sqlite_digest(path)
        return _inventory_digest(path)

    @contextmanager
    def _source_guard(self, name: ComponentName, source: Path):
        if name != "files":
            yield
            return
        descriptor = os.open(source / ".memory.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @contextmanager
    def _exclusive_lock(self):
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _validate_sqlite(path: Path) -> None:
    if not path.is_file():
        raise MemoryMigrationError(
            "memory_migration_invalid_sqlite", "SQLite component is not a file"
        )
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise MemoryMigrationError(
            "memory_migration_invalid_sqlite", "SQLite integrity check failed"
        ) from exc
    if result != ("ok",):
        raise MemoryMigrationError(
            "memory_migration_invalid_sqlite", f"SQLite integrity check returned {result!r}"
        )


def _sqlite_digest(path: Path) -> str:
    _validate_sqlite(path)
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            statements = "\n".join(connection.iterdump())
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise MemoryMigrationError(
            "memory_migration_invalid_sqlite", "SQLite snapshot digest failed"
        ) from exc
    return hashlib.sha256(statements.encode()).hexdigest()


def _validate_legacy_component(name: str, path: Path) -> None:
    if name == "qdrant":
        _legacy_qdrant_snapshot(path)
        return
    if name in {"history", "evidence"}:
        _validate_sqlite(path)
        return
    if name == "files":
        if not path.is_dir():
            raise MemoryMigrationError(
                "memory_migration_invalid_files", "file memory is not a directory"
            )
        _inventory_digest(path)
        return
    raise MemoryMigrationError(
        "memory_migration_manifest_components", f"unknown legacy component {name}"
    )


def _legacy_qdrant_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        raise MemoryMigrationError(
            "memory_migration_invalid_qdrant", "Qdrant component is not a directory"
        )
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(path))
    try:
        snapshot: dict[str, Any] = {}
        for collection in sorted(item.name for item in client.get_collections().collections):
            count = int(client.count(collection, exact=True).count)
            points: list[dict[str, Any]] = []
            offset = None
            while True:
                rows, offset = client.scroll(
                    collection,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                points.extend(
                    sorted(
                        (
                            {
                                "id": str(row.id),
                                "payload": row.payload,
                                "vectors": sorted(row.vector)
                                if isinstance(row.vector, dict)
                                else [""],
                            }
                            for row in rows
                        ),
                        key=lambda item: item["id"],
                    )
                )
                if offset is None:
                    break
            if len(points) != count:
                raise MemoryMigrationError(
                    "memory_migration_invalid_qdrant",
                    f"Qdrant collection {collection!r} count mismatch",
                )
            snapshot[collection] = {"count": count, "points": points}
        return snapshot
    except MemoryMigrationError:
        raise
    except Exception as exc:
        raise MemoryMigrationError(
            "memory_migration_invalid_qdrant",
            f"Qdrant inspection failed: {type(exc).__name__}",
        ) from exc
    finally:
        client.close()


def _copy_component(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, copy_function=shutil.copy2)
        for path in target.rglob("*"):
            if path.is_file():
                _fsync_file(path)
        _fsync_directory(target)
    else:
        try:
            source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            target_connection = sqlite3.connect(target)
            try:
                source_connection.backup(target_connection)
            finally:
                target_connection.close()
                source_connection.close()
        except sqlite3.Error as exc:
            raise MemoryMigrationError(
                "memory_migration_copy_failed", "SQLite snapshot copy failed"
            ) from exc
        _fsync_file(target)


def _inventory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise MemoryMigrationError(
                "memory_migration_path_unreadable", f"component path is unreadable: {path}"
            ) from exc
        digest.update(b"file\0")
        digest.update(content)
        return digest.hexdigest()
    if not path.is_dir():
        raise MemoryMigrationError(
            "memory_migration_path_missing", f"component path missing: {path}"
        )
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix()
        if relative == ".lock" or relative.endswith("/.lock"):
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        if item.is_symlink():
            raise MemoryMigrationError(
                "memory_migration_symlink", "memory components may not contain symlinks"
            )
        if item.is_file():
            try:
                digest.update(item.read_bytes())
            except OSError as exc:
                raise MemoryMigrationError(
                    "memory_migration_path_unreadable",
                    f"component path is unreadable: {item}",
                ) from exc
    return digest.hexdigest()


def _same_inventory(left: Path, right: Path) -> bool:
    try:
        return _inventory_digest(left) == _inventory_digest(right)
    except MemoryMigrationError:
        return False


def _path_equivalent(left: object, right: object) -> bool:
    if not isinstance(left, (str, os.PathLike)) or not isinstance(right, (str, os.PathLike)):
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(
            strict=False
        )
    except (OSError, RuntimeError):
        return False


def _path_pair_equivalent(
    left: tuple[object, object], right: tuple[object, object]
) -> bool:
    return _path_equivalent(left[0], right[0]) and _path_equivalent(left[1], right[1])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryMigrationError(
            "memory_migration_invalid_json", f"cannot read {path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise MemoryMigrationError("memory_migration_invalid_json", f"{path.name} is not an object")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "MemoryMigrationCoordinator",
    "MemoryMigrationError",
    "MemoryMigrationInspection",
]
