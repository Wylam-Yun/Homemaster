"""Atomic artifact publication and append-only evidence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def canonical_json(payload: Any) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temp.open("wb") as handle:
        handle.write(canonical_json(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json(payload))
        handle.flush()
        os.fsync(handle.fileno())


class ArtifactRegistry:
    def __init__(self, run_root: Path, run_id: str) -> None:
        self.run_root = run_root.resolve()
        self.run_id = run_id
        self.manifest_path = self.run_root / "run_manifest.json"
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}

    def resolve(self, relative_path: str) -> Path:
        path = (self.run_root / relative_path).resolve()
        if path == self.run_root or self.run_root not in path.parents:
            raise ValueError("artifact path escapes run root")
        return path

    def register(
        self, relative_path: str, *, producer: str, schema_version: int = 1, complete: bool = True
    ) -> dict[str, Any]:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        entry = {
            "path": relative_path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "producer": producer,
            "schema_version": schema_version,
            "complete": complete,
        }
        with self._lock:
            self._entries[relative_path] = entry
            atomic_write_json(
                self.manifest_path,
                {"schema_version": 1, "run_id": self.run_id, "artifacts": self._entries},
            )
        return entry

    def verify(self, *, required_paths: Iterable[str] = ()) -> list[str]:
        failures: list[str] = []
        with self._lock:
            for relative in required_paths:
                if relative not in self._entries:
                    failures.append(f"missing_manifest_entry:{relative}")
            for relative, entry in self._entries.items():
                path = self.resolve(relative)
                if not path.is_file():
                    failures.append(f"missing:{relative}")
                elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    failures.append(f"hash_drift:{relative}")
                elif not entry["complete"]:
                    failures.append(f"incomplete:{relative}")
        return failures
