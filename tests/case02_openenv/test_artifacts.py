from __future__ import annotations

import json
from pathlib import Path

import pytest
from case02_openenv.artifacts import ArtifactRegistry, atomic_write_json


def test_atomic_json_is_canonical_and_manifest_detects_drift(tmp_path: Path) -> None:
    root = tmp_path / "run"
    path = root / "scores/value.json"
    atomic_write_json(path, {"b": 2, "a": 1})
    assert path.read_text() == '{"a":1,"b":2}\n'
    registry = ArtifactRegistry(root, "run-1")
    registry.register("scores/value.json", producer="test")
    assert registry.verify() == []
    assert registry.verify(required_paths=["scores/missing.json"]) == [
        "missing_manifest_entry:scores/missing.json"
    ]
    path.write_text(json.dumps({"changed": True}), encoding="utf-8")
    assert registry.verify() == ["hash_drift:scores/value.json"]


def test_artifact_path_escape_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        ArtifactRegistry(tmp_path / "run", "run-1").resolve("../outside")
