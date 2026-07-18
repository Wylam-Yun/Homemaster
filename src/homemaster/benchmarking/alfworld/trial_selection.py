"""Strict, portable ALFWorld trial-selection manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = "alfworld-trial-selection-v1"
_ENTRY_KEYS = {
    "trial_id",
    "trial_sha256",
    "expected_logical_scene",
    "goal_identity",
    "goal_fingerprint",
    "identity_status",
}


@dataclass(frozen=True)
class TrialSelectionEntry:
    trial_id: str
    trial_sha256: str
    expected_logical_scene: str
    goal_identity: str
    goal_fingerprint: str
    identity_status: str
    portable_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_trial_id(self.trial_id)
        _validate_sha256("trial_sha256", self.trial_sha256)
        _validate_sha256("goal_fingerprint", self.goal_fingerprint)
        for name in ("expected_logical_scene", "goal_identity", "identity_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        object.__setattr__(
            self,
            "portable_fingerprint",
            _portable_fingerprint(self.trial_id, bytes.fromhex(self.trial_sha256)),
        )


@dataclass(frozen=True)
class TrialSelectionManifest:
    schema_version: str
    entries: tuple[TrialSelectionEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"unsupported trial-selection schema: {self.schema_version}")
        if not self.entries:
            raise ValueError("trial-selection manifest has no entries")
        trial_ids = [entry.trial_id for entry in self.entries]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("trial-selection manifest contains duplicate trial IDs")


def load_trial_selection_manifest(
    path: Path,
    *,
    trial_root: Path,
) -> TrialSelectionManifest:
    payload = _load_json_object(path)
    _require_exact_keys(payload, {"schema_version", "entries"}, context="manifest")
    entries_payload = payload["entries"]
    if not isinstance(entries_payload, list):
        raise ValueError("manifest entries must be an array")

    root = trial_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("trial_root must be a directory")
    entries: list[TrialSelectionEntry] = []
    for index, raw_entry in enumerate(entries_payload):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"manifest entry {index} must be an object")
        _require_exact_keys(raw_entry, _ENTRY_KEYS, context=f"manifest entry {index}")
        entry = TrialSelectionEntry(**raw_entry)
        trial_path = (root / entry.trial_id).resolve(strict=True)
        try:
            trial_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"trial ID escapes trial_root: {entry.trial_id}") from exc
        if not trial_path.is_file():
            raise ValueError(f"trial is not a regular file: {entry.trial_id}")
        trial_bytes = trial_path.read_bytes()
        if hashlib.sha256(trial_bytes).hexdigest() != entry.trial_sha256:
            raise ValueError(f"trial bytes hash mismatch: {entry.trial_id}")
        trial_data = _decode_trial_data(trial_bytes, entry.trial_id)
        _validate_entry_goal(entry, trial_data)
        object.__setattr__(
            entry,
            "portable_fingerprint",
            _portable_fingerprint(entry.trial_id, trial_bytes),
        )
        entries.append(entry)

    schema_version = payload["schema_version"]
    if not isinstance(schema_version, str):
        raise ValueError("manifest schema_version must be a string")
    return TrialSelectionManifest(schema_version=schema_version, entries=tuple(entries))


def build_trial_selection_entry(
    trial_path: Path,
    *,
    trial_root: Path,
    expected_logical_scene: str,
    identity_status: str,
) -> TrialSelectionEntry:
    """Build a portable entry from verified trial bytes without absolute-path identity."""

    root = trial_root.resolve(strict=True)
    resolved = trial_path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"trial path escapes trial_root: {trial_path}") from exc
    if not resolved.is_file():
        raise ValueError(f"trial is not a regular file: {relative.as_posix()}")

    trial_bytes = resolved.read_bytes()
    trial_data = _decode_trial_data(trial_bytes, relative.as_posix())
    logical_scene = trial_logical_scene(trial_data)
    if logical_scene != expected_logical_scene:
        raise ValueError(
            f"trial logical scene mismatch: expected {expected_logical_scene}, got {logical_scene}"
        )
    trial_id = relative.as_posix()
    trial_sha256 = hashlib.sha256(trial_bytes).hexdigest()
    goal_identity = trial_goal_identity(trial_data)
    return TrialSelectionEntry(
        trial_id=trial_id,
        trial_sha256=trial_sha256,
        expected_logical_scene=expected_logical_scene,
        goal_identity=goal_identity,
        goal_fingerprint=_goal_fingerprint(trial_id, trial_sha256, goal_identity),
        identity_status=identity_status,
    )


def load_verified_trial_data(
    entry: TrialSelectionEntry,
    *,
    trial_root: Path,
) -> dict[str, Any]:
    """Reload one selection through its relative ID and verify all declared identity."""

    root = trial_root.resolve(strict=True)
    trial_path = (root / entry.trial_id).resolve(strict=True)
    try:
        trial_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"trial ID escapes trial_root: {entry.trial_id}") from exc
    if not trial_path.is_file():
        raise ValueError(f"trial is not a regular file: {entry.trial_id}")
    trial_bytes = trial_path.read_bytes()
    if hashlib.sha256(trial_bytes).hexdigest() != entry.trial_sha256:
        raise ValueError(f"trial bytes hash mismatch: {entry.trial_id}")
    trial_data = _decode_trial_data(trial_bytes, entry.trial_id)
    _validate_entry_goal(entry, trial_data)
    return trial_data


def trial_logical_scene(trial_data: dict[str, Any]) -> str:
    scene = trial_data.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("trial scene must be an object")
    floor_plan = scene.get("floor_plan")
    if not isinstance(floor_plan, str) or not floor_plan.startswith("FloorPlan"):
        raise ValueError("trial logical scene is unreadable")
    return floor_plan


def trial_goal_identity(trial_data: dict[str, Any]) -> str:
    task_type = trial_data.get("task_type")
    pddl_params = trial_data.get("pddl_params")
    if not isinstance(task_type, str) or not task_type or not isinstance(pddl_params, dict):
        raise ValueError("trial goal identity is unreadable")
    keys = (
        "object_target",
        "parent_target",
        "toggle_target",
        "mrecep_target",
        "object_sliced",
    )
    goal = {key: pddl_params.get(key) for key in keys}
    if not isinstance(goal["object_target"], str) or not goal["object_target"]:
        raise ValueError("trial goal object identity is unreadable")
    return json.dumps(
        {"pddl_params": goal, "task_type": task_type},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_trial_id(trial_id: str) -> None:
    if not isinstance(trial_id, str) or not trial_id:
        raise ValueError("trial_id must be a non-empty string")
    if "\x00" in trial_id or "\\" in trial_id or trial_id.startswith("/"):
        raise ValueError("trial_id must be a canonical POSIX-relative path")
    segments = trial_id.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("trial_id contains an unsafe path segment")
    if Path(trial_id).is_absolute():
        raise ValueError("trial_id must be relative")


def _portable_fingerprint(trial_id: str, trial_bytes: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(trial_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(trial_bytes)
    return digest.hexdigest()


def _goal_fingerprint(trial_id: str, trial_sha256: str, goal_identity: str) -> str:
    payload = {
        "goal_identity": goal_identity,
        "trial_id": trial_id,
        "trial_sha256": trial_sha256,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decode_trial_data(trial_bytes: bytes, trial_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(trial_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"trial JSON is unreadable: {trial_id}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"trial JSON must be an object: {trial_id}")
    return payload


def _validate_entry_goal(entry: TrialSelectionEntry, trial_data: dict[str, Any]) -> None:
    logical_scene = trial_logical_scene(trial_data)
    if logical_scene != entry.expected_logical_scene:
        raise ValueError(f"trial logical scene mismatch: {entry.trial_id}")
    goal_identity = trial_goal_identity(trial_data)
    if goal_identity != entry.goal_identity:
        raise ValueError(f"trial goal identity mismatch: {entry.trial_id}")
    expected_fingerprint = _goal_fingerprint(
        entry.trial_id,
        entry.trial_sha256,
        goal_identity,
    )
    if entry.goal_fingerprint != expected_fingerprint:
        raise ValueError(f"trial goal fingerprint mismatch: {entry.trial_id}")


def _validate_sha256(name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read trial-selection manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("trial-selection manifest must be a JSON object")
    return payload


def _require_exact_keys(value: dict[str, Any], expected: set[str], *, context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{context} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
