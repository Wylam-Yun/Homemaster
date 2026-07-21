#!/usr/bin/env python3
"""Independently verify one V1.9 Coworker release candidate pointer."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts.coworker_demo.verify_run_bundle import verify as verify_run_bundle
from scripts.v19_release._common import (
    canonical_json_bytes,
    read_json_object,
    require_exact_keys,
    require_sha256,
    sha256_bytes,
    sha256_file,
)

SCHEMA_VERSION = "homemaster-v1.9-coworker-release-v1"
INDEX_SCHEMA_VERSION = "homemaster-v1.9-coworker-attempt-index-v1"
LEDGER_SCHEMA_VERSION = "homemaster-v1.9-coworker-attempt-v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ZERO_HASH = "0" * 64
POINTER_KEYS = {
    "accepted_attempt_id",
    "accepted_run_root",
    "candidate_sha",
    "index_ref",
    "index_sha256",
    "ledger_ref",
    "ledger_record_count",
    "ledger_sha256",
    "schema_version",
}
LEDGER_KEYS = {
    "attempt_id",
    "attempt_index",
    "candidate_sha",
    "error_type",
    "formal_success",
    "index_ref",
    "index_sha256",
    "previous_record_sha256",
    "record_sha256",
    "run_id",
    "run_root",
    "schema_version",
    "status",
}
INDEX_KEYS = {
    "artifacts",
    "attempt_id",
    "attempt_index",
    "candidate_sha",
    "dataset_manifest_sha256",
    "error_type",
    "expected_model",
    "formal_success",
    "run_id",
    "run_root",
    "schema_version",
    "status",
}


def verify_coworker_release(
    pointer_path: Path,
    *,
    data_root: Path,
    expected_sha: str,
) -> dict[str, Any]:
    expected_sha = _commit(expected_sha)
    pointer_path = pointer_path.resolve(strict=True)
    root = pointer_path.parent
    pointer = read_json_object(pointer_path, label="Coworker release pointer")
    require_exact_keys(pointer, POINTER_KEYS, label="Coworker release pointer")
    if pointer["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported Coworker release pointer schema")
    if _commit(pointer["candidate_sha"]) != expected_sha:
        raise ValueError("candidate SHA does not match expected SHA")

    ledger_path = _contained(root, pointer["ledger_ref"], label="attempt ledger")
    if sha256_file(ledger_path) != require_sha256(
        pointer["ledger_sha256"], label="ledger SHA-256"
    ):
        raise ValueError("attempt ledger hash mismatch")
    records = read_attempt_ledger(ledger_path, expected_sha=expected_sha)
    if pointer["ledger_record_count"] != len(records):
        raise ValueError("attempt ledger record count mismatch")
    accepted = [record for record in records if record["status"] == "accepted"]
    if len(accepted) != 1:
        raise ValueError("attempt ledger must contain exactly one accepted attempt")
    accepted_record = accepted[0]
    if accepted_record["attempt_id"] != pointer["accepted_attempt_id"]:
        raise ValueError("accepted attempt identity mismatch")

    seen_attempt_ids: set[str] = set()
    seen_index_refs: set[str] = set()
    indexes: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["attempt_id"] in seen_attempt_ids:
            raise ValueError("attempt ledger contains duplicate attempt IDs")
        if record["index_ref"] in seen_index_refs:
            raise ValueError("attempt ledger reuses an attempt index")
        seen_attempt_ids.add(record["attempt_id"])
        seen_index_refs.add(record["index_ref"])
        record_index_path = _contained(root, record["index_ref"], label="attempt index")
        if sha256_file(record_index_path) != require_sha256(
            record["index_sha256"], label="index SHA-256"
        ):
            raise ValueError("attempt index hash mismatch")
        indexes[record["attempt_id"]] = _verify_index(
            record_index_path,
            record,
            expected_sha=expected_sha,
            data_root=data_root,
        )

    index_path = _contained(root, pointer["index_ref"], label="accepted attempt index")
    index_sha = require_sha256(pointer["index_sha256"], label="index SHA-256")
    if sha256_file(index_path) != index_sha:
        raise ValueError("accepted attempt index hash mismatch")
    if accepted_record["index_ref"] != pointer["index_ref"]:
        raise ValueError("pointer and ledger index references differ")
    if accepted_record["index_sha256"] != index_sha:
        raise ValueError("pointer and ledger index hashes differ")
    index = indexes[accepted_record["attempt_id"]]

    run_root = Path(str(index["run_root"])).resolve(strict=True)
    if str(run_root) != pointer["accepted_run_root"]:
        raise ValueError("accepted run root mismatch")
    bundle = verify_run_bundle(
        run_root,
        data_root.resolve(strict=True),
        expected_model=str(index["expected_model"]),
    )
    if bundle.get("pass") is not True:
        raise ValueError("accepted Coworker formal bundle verification failed")
    if bundle.get("run_id") != index["run_id"]:
        raise ValueError("formal bundle run identity mismatch")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "candidate_sha": expected_sha,
        "attempted": len(records),
        "accepted": 1,
        "rejected": sum(record["status"] == "rejected" for record in records),
        "failed": sum(record["status"] == "failed" for record in records),
        "attempts": [
            {
                "attempt_id": record["attempt_id"],
                "attempt_index": record["attempt_index"],
                "status": record["status"],
                "run_id": record["run_id"],
            }
            for record in records
        ],
        "run_id": index["run_id"],
        "formal_success": True,
    }


def read_attempt_ledger(path: Path, *, expected_sha: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("unable to read Coworker attempt ledger") from exc
    if not lines:
        raise ValueError("Coworker attempt ledger is empty")
    records: list[dict[str, Any]] = []
    previous = ZERO_HASH
    for offset, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Coworker attempt ledger contains invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError("Coworker attempt ledger rows must be objects")
        require_exact_keys(record, LEDGER_KEYS, label=f"attempt ledger row {offset}")
        if record["schema_version"] != LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported Coworker attempt ledger schema")
        if record["candidate_sha"] != expected_sha:
            raise ValueError("attempt ledger crosses candidate SHA")
        if record["attempt_index"] != offset:
            raise ValueError("attempt ledger index is not contiguous")
        if record["previous_record_sha256"] != previous:
            raise ValueError("attempt ledger hash chain is broken")
        declared = require_sha256(record["record_sha256"], label="attempt record SHA-256")
        unsigned = {key: value for key, value in record.items() if key != "record_sha256"}
        actual = sha256_bytes(canonical_json_bytes(unsigned))
        if actual != declared:
            raise ValueError("attempt ledger record hash mismatch")
        if record["status"] not in {"failed", "rejected", "accepted"}:
            raise ValueError("attempt ledger status is invalid")
        if (record["status"] == "accepted") is not (record["formal_success"] is True):
            raise ValueError("attempt ledger formal-success status mismatch")
        previous = declared
        records.append(record)
    return records


def _verify_index(
    path: Path,
    record: dict[str, Any],
    *,
    expected_sha: str,
    data_root: Path,
) -> dict[str, Any]:
    index = read_json_object(path, label="Coworker attempt index")
    require_exact_keys(index, INDEX_KEYS, label="Coworker attempt index")
    if index["schema_version"] != INDEX_SCHEMA_VERSION:
        raise ValueError("unsupported Coworker attempt index schema")
    for key in (
        "attempt_id",
        "attempt_index",
        "candidate_sha",
        "error_type",
        "formal_success",
        "run_id",
        "run_root",
        "status",
    ):
        if index[key] != record[key]:
            raise ValueError(f"attempt index mismatches ledger: {key}")
    if index["candidate_sha"] != expected_sha:
        raise ValueError("attempt index has invalid candidate")
    dataset_sha = require_sha256(
        index["dataset_manifest_sha256"], label="dataset manifest SHA-256"
    )
    if dataset_sha != sha256_file(data_root.resolve(strict=True) / "dataset_manifest.json"):
        raise ValueError("attempt index dataset manifest hash mismatch")
    if not isinstance(index["expected_model"], str) or not index["expected_model"]:
        raise ValueError("attempt index expected model is missing")
    artifacts = index["artifacts"]
    if not isinstance(artifacts, dict):
        raise ValueError("attempt index artifact hashes must be an object")
    if index["run_root"] is None:
        if artifacts or index["run_id"] is not None:
            raise ValueError("attempt without a run root cannot claim run artifacts")
        return index
    run_root = Path(str(index["run_root"])).resolve(strict=True)
    if index["status"] == "accepted" and not artifacts:
        raise ValueError("accepted attempt index artifact hashes are missing")
    for relative, digest in artifacts.items():
        artifact = _contained(run_root, relative, label="run artifact")
        if sha256_file(artifact) != require_sha256(digest, label="run artifact SHA-256"):
            raise ValueError(f"run artifact hash mismatch: {relative}")
    return index


def _contained(root: Path, ref: Any, *, label: str) -> Path:
    if not isinstance(ref, str) or not ref or Path(ref).is_absolute():
        raise ValueError(f"{label} reference must be relative")
    path = (root / ref).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError(f"{label} escapes its root")
    if not path.is_file():
        raise ValueError(f"{label} is not a file")
    return path


def _commit(value: Any) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise ValueError("candidate SHA must be a full lowercase Git commit")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pointer", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    try:
        result = verify_coworker_release(
            args.pointer,
            data_root=args.data_root,
            expected_sha=args.expected_sha,
        )
    except Exception as exc:
        print(f"Coworker release verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
