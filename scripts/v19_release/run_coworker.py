#!/usr/bin/env python3
"""Run and record the locked V1.9 Coworker normal release item."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from homemaster.benchmarking.coworker_demo.config import load_coworker_config
from homemaster.benchmarking.coworker_demo.turn import run_coworker_turn
from homemaster.benchmarking.coworker_demo.types import CoworkerAttemptError, ValidTicketRoute
from homemaster.cli.coworker_router import route_coworker_ticket
from homemaster.config import load_config
from scripts.coworker_demo.preflight import run_preflight
from scripts.coworker_demo.verify_run_bundle import verify as verify_run_bundle
from scripts.v19_release._common import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from scripts.v19_release.verify_coworker_release import (
    INDEX_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    SCHEMA_VERSION,
    ZERO_HASH,
    read_attempt_ledger,
    verify_coworker_release,
)


def run_coworker_release(
    *,
    repo_root: Path,
    coworker_config: Path,
    provider_config: Path,
    evidence_root: Path,
    pointer_path: Path,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    candidate_sha = _candidate_sha(repo_root)
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    pointer_path = pointer_path.resolve()
    if pointer_path.exists():
        raise ValueError("fresh Coworker release pointer already exists")
    if pointer_path.parent.resolve() != evidence_root:
        raise ValueError("release pointer must be directly inside evidence root")

    preflight = run_preflight(coworker_config.resolve(), provider_config.resolve())
    if preflight.get("pass") is not True:
        raise ValueError("Coworker preflight failed")
    config = load_coworker_config(coworker_config)
    data_root = config.paths.data_root.resolve(strict=True)
    ticket_path = data_root / "test_set/item_change_ticket.json"
    route = route_coworker_ticket(f"{ticket_path.resolve()} normal")
    if not isinstance(route, ValidTicketRoute) or route.scenario_id != "normal":
        raise ValueError("locked Coworker normal release item did not route")
    provider_home = load_config(provider_config)
    provider = provider_home.get_provider(
        provider_home.runtime_defaults.default_provider_name,
        kind="chat",
    )

    ledger_path = evidence_root / "coworker-attempts.jsonl"
    attempt_id = f"attempt-{uuid.uuid4().hex}"
    attempt_index = _next_attempt_index(ledger_path, candidate_sha=candidate_sha)
    run_root: Path | None = None
    run_id: str | None = None
    error_type: str | None = None
    formal_success = False
    status = "failed"
    try:
        result = run_coworker_turn(
            route,
            coworker_config_path=coworker_config,
            provider_config_path=provider_config,
        )
        run_root = result.artifact_path.resolve(strict=True)
        run_id = result.run_id
        bundle = verify_run_bundle(run_root, data_root, expected_model=provider.model)
        formal_success = result.formal_success is True and bundle.get("pass") is True
        status = "accepted" if formal_success else "rejected"
        if not formal_success:
            error_type = "FormalVerificationRejected"
    except CoworkerAttemptError as exc:
        run_root = exc.run_root.resolve()
        run_id = exc.run_id
        error_type = exc.error_type
    except Exception as exc:
        error_type = type(exc).__name__

    index_path = evidence_root / "coworker-attempt-indexes" / f"{attempt_id}.json"
    index = _build_index(
        candidate_sha=candidate_sha,
        attempt_id=attempt_id,
        attempt_index=attempt_index,
        status=status,
        formal_success=formal_success,
        error_type=error_type,
        run_id=run_id,
        run_root=run_root,
        data_root=data_root,
        expected_model=provider.model,
    )
    write_canonical_json(index_path, index)
    index_ref = index_path.relative_to(evidence_root).as_posix()
    record = append_attempt_record(
        ledger_path,
        {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "candidate_sha": candidate_sha,
            "attempt_id": attempt_id,
            "attempt_index": attempt_index,
            "status": status,
            "formal_success": formal_success,
            "error_type": error_type,
            "run_id": run_id,
            "run_root": str(run_root) if run_root is not None else None,
            "index_ref": index_ref,
            "index_sha256": sha256_file(index_path),
        },
        expected_sha=candidate_sha,
    )
    if status != "accepted" or run_root is None:
        raise ValueError(f"Coworker release attempt was {status}: {error_type}")

    pointer = {
        "schema_version": SCHEMA_VERSION,
        "candidate_sha": candidate_sha,
        "accepted_attempt_id": attempt_id,
        "accepted_run_root": str(run_root),
        "index_ref": index_ref,
        "index_sha256": record["index_sha256"],
        "ledger_ref": ledger_path.relative_to(evidence_root).as_posix(),
        "ledger_record_count": attempt_index + 1,
        "ledger_sha256": sha256_file(ledger_path),
    }
    write_canonical_json(pointer_path, pointer)
    return verify_coworker_release(
        pointer_path,
        data_root=data_root,
        expected_sha=candidate_sha,
    )


def append_attempt_record(
    ledger_path: Path,
    row: dict[str, Any],
    *,
    expected_sha: str,
) -> dict[str, Any]:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing_text = handle.read()
        if existing_text and not existing_text.endswith("\n"):
            raise ValueError("Coworker attempt ledger has a truncated final row")
        existing = (
            read_attempt_ledger(ledger_path, expected_sha=expected_sha)
            if existing_text
            else []
        )
        if any(record["status"] == "accepted" for record in existing):
            raise ValueError("candidate already has an accepted Coworker attempt")
        if row["attempt_index"] != len(existing):
            raise ValueError("attempt index changed while acquiring ledger lock")
        previous = existing[-1]["record_sha256"] if existing else ZERO_HASH
        unsigned = {**row, "previous_record_sha256": previous}
        record = {
            **unsigned,
            "record_sha256": sha256_bytes(canonical_json_bytes(unsigned)),
        }
        handle.seek(0, os.SEEK_END)
        handle.write(canonical_json_bytes(record).decode("ascii") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return record


def _next_attempt_index(ledger_path: Path, *, candidate_sha: str) -> int:
    if not ledger_path.exists():
        return 0
    records = read_attempt_ledger(ledger_path, expected_sha=candidate_sha)
    if any(record["status"] == "accepted" for record in records):
        raise ValueError("candidate already has an accepted Coworker attempt")
    return len(records)


def _build_index(
    *,
    candidate_sha: str,
    attempt_id: str,
    attempt_index: int,
    status: str,
    formal_success: bool,
    error_type: str | None,
    run_id: str | None,
    run_root: Path | None,
    data_root: Path,
    expected_model: str,
) -> dict[str, Any]:
    artifacts: dict[str, str] = {}
    if run_root is not None and run_root.is_dir():
        for relative in (
            "attempt_manifest.json",
            "run_manifest.json",
            "scores/summary.json",
            "agent/provider_identity.json",
        ):
            path = run_root / relative
            if path.is_file():
                artifacts[relative] = sha256_file(path)
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "candidate_sha": candidate_sha,
        "attempt_id": attempt_id,
        "attempt_index": attempt_index,
        "status": status,
        "formal_success": formal_success,
        "error_type": error_type,
        "run_id": run_id,
        "run_root": str(run_root) if run_root is not None else None,
        "dataset_manifest_sha256": sha256_file(data_root / "dataset_manifest.json"),
        "expected_model": expected_model,
        "artifacts": artifacts,
    }


def _candidate_sha(repo_root: Path) -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise ValueError("tracked candidate worktree must be clean")
    return sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--coworker-config", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--pointer", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_coworker_release(
            repo_root=args.repo_root,
            coworker_config=args.coworker_config,
            provider_config=args.provider_config,
            evidence_root=args.evidence_root,
            pointer_path=args.pointer,
        )
    except Exception as exc:
        print(f"Coworker release run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
