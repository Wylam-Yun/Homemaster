from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.v19_release._common import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from scripts.v19_release.run_coworker import append_attempt_record, run_coworker_release
from scripts.v19_release.verify_coworker_release import (
    INDEX_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    SCHEMA_VERSION,
    ZERO_HASH,
    verify_coworker_release,
)

CANDIDATE = "a" * 40


def test_runner_rejects_nested_pointer_before_preflight_or_attempt_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = tmp_path / "evidence"
    preflight_called = False

    def preflight(*_args, **_kwargs):
        nonlocal preflight_called
        preflight_called = True
        return {"pass": True}

    monkeypatch.setattr("scripts.v19_release.run_coworker.run_preflight", preflight)
    monkeypatch.setattr(
        "scripts.v19_release.run_coworker._candidate_sha",
        lambda _root: CANDIDATE,
    )

    with pytest.raises(ValueError, match="directly inside evidence root"):
        run_coworker_release(
            repo_root=Path(__file__).resolve().parents[3],
            coworker_config=tmp_path / "coworker.yaml",
            provider_config=tmp_path / "provider.yaml",
            evidence_root=evidence,
            pointer_path=evidence / "nested/accepted.json",
        )

    assert preflight_called is False
    assert not (evidence / "coworker-attempts.jsonl").exists()


def test_verifier_reports_all_failed_and_accepted_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer, data_root = _release_fixture(tmp_path)
    monkeypatch.setattr(
        "scripts.v19_release.verify_coworker_release.verify_run_bundle",
        lambda *_args, **_kwargs: {"pass": True, "run_id": "run-accepted"},
    )

    report = verify_coworker_release(
        pointer,
        data_root=data_root,
        expected_sha=CANDIDATE,
    )

    assert report["status"] == "PASS"
    assert report["attempted"] == 2
    assert report["failed"] == 1
    assert report["accepted"] == 1
    assert [row["status"] for row in report["attempts"]] == ["failed", "accepted"]


def test_verifier_rejects_hidden_earlier_attempt_even_with_refreshed_ledger_hash(
    tmp_path: Path,
) -> None:
    pointer, data_root = _release_fixture(tmp_path)
    payload = _read(pointer)
    ledger = pointer.parent / payload["ledger_ref"]
    rows = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text(rows[1] + "\n", encoding="utf-8")
    payload["ledger_sha256"] = sha256_file(ledger)
    payload["ledger_record_count"] = 1
    write_canonical_json(pointer, payload)

    with pytest.raises(ValueError, match="index is not contiguous"):
        verify_coworker_release(pointer, data_root=data_root, expected_sha=CANDIDATE)


def test_verifier_rejects_attempt_appended_after_pointer(tmp_path: Path) -> None:
    pointer, data_root = _release_fixture(tmp_path)
    payload = _read(pointer)
    ledger = pointer.parent / payload["ledger_ref"]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    unsigned = {
        **{key: value for key, value in rows[-1].items() if key != "record_sha256"},
        "attempt_id": "attempt-late",
        "attempt_index": 2,
        "status": "failed",
        "formal_success": False,
        "previous_record_sha256": rows[-1]["record_sha256"],
    }
    late = {**unsigned, "record_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json_bytes(late).decode("ascii") + "\n")

    with pytest.raises(ValueError, match="ledger hash mismatch"):
        verify_coworker_release(pointer, data_root=data_root, expected_sha=CANDIDATE)


def test_verifier_rejects_cross_candidate_or_multiple_accepted(tmp_path: Path) -> None:
    pointer, data_root = _release_fixture(tmp_path)
    payload = _read(pointer)
    ledger = pointer.parent / payload["ledger_ref"]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[0]["candidate_sha"] = "b" * 40
    _rewrite_chain(ledger, rows)
    payload["ledger_sha256"] = sha256_file(ledger)
    write_canonical_json(pointer, payload)
    with pytest.raises(ValueError, match="crosses candidate SHA"):
        verify_coworker_release(pointer, data_root=data_root, expected_sha=CANDIDATE)

    pointer, data_root = _release_fixture(tmp_path / "multiple")
    payload = _read(pointer)
    ledger = pointer.parent / payload["ledger_ref"]
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[0]["status"] = "accepted"
    rows[0]["formal_success"] = True
    _rewrite_chain(ledger, rows)
    payload["ledger_sha256"] = sha256_file(ledger)
    write_canonical_json(pointer, payload)
    with pytest.raises(ValueError, match="exactly one accepted"):
        verify_coworker_release(pointer, data_root=data_root, expected_sha=CANDIDATE)


def test_verifier_rejects_index_tamper_and_formal_bundle_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer, data_root = _release_fixture(tmp_path)
    payload = _read(pointer)
    index = pointer.parent / payload["index_ref"]
    with index.open("a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="index hash mismatch"):
        verify_coworker_release(pointer, data_root=data_root, expected_sha=CANDIDATE)

    pointer, data_root = _release_fixture(tmp_path / "formal")
    monkeypatch.setattr(
        "scripts.v19_release.verify_coworker_release.verify_run_bundle",
        lambda *_args, **_kwargs: {"pass": False, "run_id": "run-accepted"},
    )
    with pytest.raises(ValueError, match="formal bundle verification failed"):
        verify_coworker_release(pointer, data_root=data_root, expected_sha=CANDIDATE)


def _release_fixture(tmp_path: Path) -> tuple[Path, Path]:
    evidence = tmp_path / "evidence"
    evidence.mkdir(parents=True)
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    write_canonical_json(data_root / "dataset_manifest.json", {"schema_version": 1})
    run_root = tmp_path / "run-accepted"
    (run_root / "scores").mkdir(parents=True)
    write_canonical_json(run_root / "scores/summary.json", {"run_id": "run-accepted"})
    ledger = evidence / "coworker-attempts.jsonl"

    rows = [
        _index_and_row(
            evidence,
            data_root,
            attempt_id="attempt-failed",
            attempt_index=0,
            status="failed",
            formal_success=False,
            run_id=None,
            run_root=None,
            artifacts={},
        ),
        _index_and_row(
            evidence,
            data_root,
            attempt_id="attempt-accepted",
            attempt_index=1,
            status="accepted",
            formal_success=True,
            run_id="run-accepted",
            run_root=run_root,
            artifacts={"scores/summary.json": sha256_file(run_root / "scores/summary.json")},
        ),
    ]
    for row in rows:
        append_attempt_record(ledger, row, expected_sha=CANDIDATE)

    accepted_index = evidence / rows[1]["index_ref"]
    pointer = evidence / "coworker-accepted.json"
    write_canonical_json(
        pointer,
        {
            "schema_version": SCHEMA_VERSION,
            "candidate_sha": CANDIDATE,
            "accepted_attempt_id": "attempt-accepted",
            "accepted_run_root": str(run_root.resolve()),
            "index_ref": rows[1]["index_ref"],
            "index_sha256": sha256_file(accepted_index),
            "ledger_ref": ledger.relative_to(evidence).as_posix(),
            "ledger_record_count": 2,
            "ledger_sha256": sha256_file(ledger),
        },
    )
    return pointer, data_root


def _index_and_row(
    evidence: Path,
    data_root: Path,
    *,
    attempt_id: str,
    attempt_index: int,
    status: str,
    formal_success: bool,
    run_id: str | None,
    run_root: Path | None,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    index_ref = f"coworker-attempt-indexes/{attempt_id}.json"
    index = evidence / index_ref
    write_canonical_json(
        index,
        {
            "schema_version": INDEX_SCHEMA_VERSION,
            "candidate_sha": CANDIDATE,
            "attempt_id": attempt_id,
            "attempt_index": attempt_index,
            "status": status,
            "formal_success": formal_success,
            "error_type": None if formal_success else "RuntimeError",
            "run_id": run_id,
            "run_root": str(run_root.resolve()) if run_root is not None else None,
            "dataset_manifest_sha256": sha256_file(data_root / "dataset_manifest.json"),
            "expected_model": "test-model",
            "artifacts": artifacts,
        },
    )
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "candidate_sha": CANDIDATE,
        "attempt_id": attempt_id,
        "attempt_index": attempt_index,
        "status": status,
        "formal_success": formal_success,
        "error_type": None if formal_success else "RuntimeError",
        "run_id": run_id,
        "run_root": str(run_root.resolve()) if run_root is not None else None,
        "index_ref": index_ref,
        "index_sha256": sha256_file(index),
    }


def _rewrite_chain(path: Path, rows: list[dict[str, Any]]) -> None:
    previous = ZERO_HASH
    encoded = []
    for offset, row in enumerate(rows):
        unsigned = {
            **{key: value for key, value in row.items() if key != "record_sha256"},
            "attempt_index": offset,
            "previous_record_sha256": previous,
        }
        record = {**unsigned, "record_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
        encoded.append(canonical_json_bytes(record).decode("ascii"))
        previous = record["record_sha256"]
    path.write_text("\n".join(encoded) + "\n", encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
