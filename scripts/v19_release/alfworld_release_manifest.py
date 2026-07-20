"""Build and verify the deterministic V1.9 ALFWorld four-trial inventory."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from homemaster.benchmarking.alfworld.trial_selection import (
    TrialSelectionEntry,
    load_trial_selection_manifest,
    load_verified_trial_data,
)
from scripts.v19_release._common import (
    canonical_json_bytes,
    read_json_object,
    require_exact_keys,
    require_sha256,
    sha256_bytes,
    sha256_file,
)

SCHEMA_VERSION = "alfworld-v19-release-trials-v1"
ALGORITHM = "sha256-rank-v1"
SEED = "homemaster-v1.9-release"
SELECTION_COUNT = 4
SOURCE_SCHEMA_VERSION = "alfworld-trial-selection-v1"
SOURCE_DISPLAY_PATH = "config/alfworld_v18_regression_trials.json"
SOURCE_ENTRY_KEYS = {
    "expected_logical_scene",
    "goal_fingerprint",
    "goal_identity",
    "identity_status",
    "trial_id",
    "trial_sha256",
}
RELEASE_ENTRY_KEYS = SOURCE_ENTRY_KEYS | {"rank_digest", "source_rank"}
ROOT_KEYS = {"schema_version", "source", "selection", "entries"}
SOURCE_KEYS = {"path", "sha256", "schema_version", "entry_count"}
SELECTION_KEYS = {"algorithm", "seed", "count"}
RANK_KEYS = (
    "trial_id",
    "trial_sha256",
    "goal_fingerprint",
    "expected_logical_scene",
)


def build_release_manifest(
    source_path: Path,
    *,
    source_display_path: str = SOURCE_DISPLAY_PATH,
    trial_root: Path | None = None,
) -> dict[str, Any]:
    if _canonical_repo_path(source_display_path) != SOURCE_DISPLAY_PATH:
        raise ValueError(f"release source path must be {SOURCE_DISPLAY_PATH}")
    source = _load_source(source_path, trial_root=trial_root)
    ranked = sorted(
        ((_rank_digest(entry), entry) for entry in source),
        key=lambda item: (item[0], item[1].trial_id),
    )
    entries = []
    for rank, (digest, entry) in enumerate(ranked[:SELECTION_COUNT], start=1):
        payload = _entry_payload(entry)
        payload.update(rank_digest=digest, source_rank=rank)
        entries.append(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "path": _canonical_repo_path(source_display_path),
            "sha256": sha256_file(source_path),
            "schema_version": SOURCE_SCHEMA_VERSION,
            "entry_count": len(source),
        },
        "selection": {
            "algorithm": ALGORITHM,
            "seed": SEED,
            "count": SELECTION_COUNT,
        },
        "entries": entries,
    }


def verify_release_manifest(
    manifest_path: Path,
    *,
    source_path: Path,
    trial_root: Path | None = None,
) -> dict[str, Any]:
    payload = read_json_object(manifest_path, label="release manifest")
    require_exact_keys(payload, ROOT_KEYS, label="release manifest")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported release manifest schema")

    source_meta = payload["source"]
    selection = payload["selection"]
    entries = payload["entries"]
    if not isinstance(source_meta, dict):
        raise ValueError("release manifest source must be an object")
    if not isinstance(selection, dict):
        raise ValueError("release manifest selection must be an object")
    if not isinstance(entries, list):
        raise ValueError("release manifest entries must be an array")
    require_exact_keys(source_meta, SOURCE_KEYS, label="release source")
    require_exact_keys(selection, SELECTION_KEYS, label="release selection")
    if source_meta["schema_version"] != SOURCE_SCHEMA_VERSION:
        raise ValueError("release source schema drifted")
    if source_meta["sha256"] != sha256_file(source_path):
        raise ValueError("release source hash drifted")
    if _canonical_repo_path(source_meta["path"]) != SOURCE_DISPLAY_PATH:
        raise ValueError("release source path drifted")
    if selection != {"algorithm": ALGORITHM, "seed": SEED, "count": SELECTION_COUNT}:
        raise ValueError("release selection policy drifted")
    if len(entries) != SELECTION_COUNT:
        raise ValueError(f"release manifest must contain exactly {SELECTION_COUNT} trials")

    source = _load_source(source_path, trial_root=trial_root)
    if source_meta["entry_count"] != len(source):
        raise ValueError("release source entry count drifted")
    expected = build_release_manifest(
        source_path,
        source_display_path=source_meta["path"],
        trial_root=trial_root,
    )
    seen: set[str] = set()
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"release entry {index} must be an object")
        require_exact_keys(raw_entry, RELEASE_ENTRY_KEYS, label=f"release entry {index}")
        require_sha256(raw_entry["rank_digest"], label=f"release entry {index} rank_digest")
        TrialSelectionEntry(**{key: raw_entry[key] for key in SOURCE_ENTRY_KEYS})
        if raw_entry["trial_id"] in seen:
            raise ValueError("release manifest contains duplicate trial IDs")
        seen.add(raw_entry["trial_id"])
    if payload != expected:
        raise ValueError("release manifest does not match deterministic ranking")

    return {
        "status": "PASS",
        "selected": len(entries),
        "source_entries": len(source),
        "source_sha256": source_meta["sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "algorithm": ALGORITHM,
        "seed": SEED,
        "dataset_bytes_verified": trial_root is not None,
    }


def _load_source(source_path: Path, *, trial_root: Path | None) -> tuple[TrialSelectionEntry, ...]:
    payload = read_json_object(source_path, label="source trial manifest")
    require_exact_keys(payload, {"schema_version", "entries"}, label="source trial manifest")
    if payload["schema_version"] != SOURCE_SCHEMA_VERSION:
        raise ValueError("unsupported source trial manifest schema")
    raw_entries = payload["entries"]
    if not isinstance(raw_entries, list):
        raise ValueError("source trial manifest entries must be an array")
    if len(raw_entries) < SELECTION_COUNT:
        raise ValueError("source trial manifest has fewer than four entries")
    entries: list[TrialSelectionEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"source entry {index} must be an object")
        require_exact_keys(raw_entry, SOURCE_ENTRY_KEYS, label=f"source entry {index}")
        entry = TrialSelectionEntry(**raw_entry)
        expected_goal_fingerprint = sha256_bytes(
            canonical_json_bytes(
                {
                    "goal_identity": entry.goal_identity,
                    "trial_id": entry.trial_id,
                    "trial_sha256": entry.trial_sha256,
                }
            )
        )
        if entry.goal_fingerprint != expected_goal_fingerprint:
            raise ValueError(f"source goal fingerprint drifted: {entry.trial_id}")
        entries.append(entry)
    if len({entry.trial_id for entry in entries}) != len(entries):
        raise ValueError("source trial manifest contains duplicate trial IDs")
    if trial_root is not None:
        verified = load_trial_selection_manifest(source_path, trial_root=trial_root)
        for entry in verified.entries:
            load_verified_trial_data(entry, trial_root=trial_root)
    return tuple(entries)


def _rank_digest(entry: TrialSelectionEntry) -> str:
    payload = {key: getattr(entry, key) for key in RANK_KEYS}
    return sha256_bytes(SEED.encode("utf-8") + b"\x00" + canonical_json_bytes(payload))


def _entry_payload(entry: TrialSelectionEntry) -> dict[str, Any]:
    payload = asdict(entry)
    payload.pop("portable_fingerprint", None)
    return payload


def _canonical_repo_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("source path must be a canonical repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source path must be a canonical repository-relative path")
    return path.as_posix()
