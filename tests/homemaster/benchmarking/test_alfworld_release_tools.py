from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.v19_release._common import sha256_file, write_canonical_json
from scripts.v19_release.run_alfworld import _provider_identity
from scripts.v19_release.verify_alfworld_release import (
    MANIFEST_ENTRY_KEYS,
    PROFILE,
    SCHEMA_VERSION,
    verify_alfworld_release,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCKED_MANIFEST = REPO_ROOT / "config/alfworld_v19_release_trials.json"
CANDIDATE = "a" * 40


def test_release_runner_resolves_provider_from_unified_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "HOMEMASTER_MIMO_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "homemaster.yaml"
    config.write_text(
        """
providers:
  default: Mimo
  items:
    - name: Mimo
      api_format: openai
      transport: raw_http
      base_url: https://provider.example/v1
      model: mimo-v2.5
      api_keys: [test-secret]
      kind: chat
""".lstrip(),
        encoding="utf-8",
    )

    assert _provider_identity(config, "mimo") == {
        "name": "Mimo",
        "model": "mimo-v2.5",
        "api_format": "openai",
        "transport": "raw_http",
    }


def test_migration_reports_task_failures_but_release_requires_four_successes(
    tmp_path: Path,
) -> None:
    report, manifest = _release_fixture(tmp_path, successes=2)

    migration = verify_alfworld_release(
        report,
        manifest_path=manifest,
        gate="migration",
        expected_sha=CANDIDATE,
        expect_selected=4,
        expect_attempted=4,
        expect_eligible=4,
    )

    assert migration["status"] == "PASS"
    assert migration["success"] == 2
    assert len(migration["task_failures"]) == 2
    with pytest.raises(ValueError, match="expected success=4, got 2"):
        verify_alfworld_release(
            report,
            manifest_path=manifest,
            gate="release",
            expected_sha=CANDIDATE,
            expect_success=4,
        )


def test_release_accepts_only_complete_four_of_four_bundle(tmp_path: Path) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)

    result = verify_alfworld_release(
        report,
        manifest_path=manifest,
        gate="release",
        expected_sha=CANDIDATE,
        expect_selected=4,
        expect_attempted=4,
        expect_eligible=4,
        expect_success=4,
    )

    assert result["success"] == 4
    assert result["formal_score_available"] is True


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_missing_attempt_ledger(tmp_path: Path, gate: str) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    ledger = report.parent / payload["episodes"][0]["provider_attempts_ref"]
    ledger.unlink()

    with pytest.raises(ValueError, match="artifact"):
        _verify(report, manifest, gate=gate)


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_artifact_hash_mismatch(tmp_path: Path, gate: str) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    trace = report.parent / payload["episodes"][0]["trace_ref"]
    trace.write_text('{"backend_action_count":2}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        _verify(report, manifest, gate=gate)


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_manifest_hash_or_order_mismatch(
    tmp_path: Path,
    gate: str,
) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    changed = _read(manifest)
    changed["entries"] = list(reversed(changed["entries"]))
    write_canonical_json(manifest, changed)

    with pytest.raises(ValueError, match="manifest hash mismatches"):
        _verify(report, manifest, gate=gate)


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_expected_sha_mismatch(tmp_path: Path, gate: str) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)

    with pytest.raises(ValueError, match="candidate SHA does not match"):
        verify_alfworld_release(
            report,
            manifest_path=manifest,
            gate=gate,  # type: ignore[arg-type]
            expected_sha="b" * 40,
        )


@pytest.mark.parametrize("field", ["name", "model"])
@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_provider_identity_mismatch(
    tmp_path: Path,
    gate: str,
    field: str,
) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    payload["provider"][field] = "different-provider-identity"
    write_canonical_json(report, payload)

    with pytest.raises(ValueError, match="provider"):
        _verify(report, manifest, gate=gate)


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_harness_invalid_even_when_report_is_self_consistent(
    tmp_path: Path,
    gate: str,
) -> None:
    report, manifest = _release_fixture(
        tmp_path,
        successes=3,
        harness_invalid_index=3,
    )

    with pytest.raises(ValueError, match="formal score is unavailable"):
        _verify(report, manifest, gate=gate)


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_action_count_contract_violation(
    tmp_path: Path,
    gate: str,
) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    summary_path = report.parent / payload["summary_ref"]
    summary = _read(summary_path)
    summary["episodes"][0]["model_backend_action_count"] = 2
    summary["episodes"][0]["backend_action_count"] = 2
    summary["episodes"][0]["total_backend_action_count"] = 4
    summary["episodes"][0]["total_external_request_count"] = 4
    write_canonical_json(summary_path, summary)
    payload["episodes"][0]["action_counts"].update(
        model_backend_actions=2,
        total_backend_actions=4,
        total_external_requests=4,
    )
    _refresh_hash(payload, report, summary_path)

    with pytest.raises(ValueError, match="model action count does not reconcile"):
        _verify(report, manifest, gate=gate)


def test_release_verifier_ignores_thor_process_rows_when_counting_actions(
    tmp_path: Path,
) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    trace_path = report.parent / payload["episodes"][0]["trace_ref"]
    step = json.loads(trace_path.read_text(encoding="utf-8"))
    process_rows = [
        {"event": "context_created", "tool_call_id": "call-0"},
        {"event": "attempt_started", "attempt_id": "attempt-0"},
        {"event": "execution_terminal", "success": True},
    ]
    _write_jsonl(trace_path, [*process_rows, step])
    _refresh_hash(payload, report, trace_path)

    result = _verify(report, manifest, gate="release")

    assert result["success"] == 4


@pytest.mark.parametrize("gate", ["migration", "release"])
def test_both_gates_reject_persisted_formal_score_unavailable(
    tmp_path: Path,
    gate: str,
) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    summary_path = report.parent / payload["summary_ref"]
    summary = _read(summary_path)
    summary["formal_score_available"] = False
    write_canonical_json(summary_path, summary)
    _refresh_hash(payload, report, summary_path)

    with pytest.raises(ValueError, match="persisted formal score is unavailable"):
        _verify(report, manifest, gate=gate)


def test_terminal_call_id_and_trace_evidence_must_reconcile(tmp_path: Path) -> None:
    report, manifest = _release_fixture(tmp_path, successes=4)
    payload = _read(report)
    summary_path = report.parent / payload["summary_ref"]
    summary = _read(summary_path)
    summary["episodes"][0]["terminal_tool_call_id"] = "missing-call"
    summary["episodes"][0]["terminal_evidence_ref"] = (
        "trace.jsonl#sha256=" + "0" * 64
    )
    write_canonical_json(summary_path, summary)
    payload["episodes"][0]["terminal_tool_call_id"] = "missing-call"
    payload["episodes"][0]["terminal_evidence_ref"] = (
        "trace.jsonl#sha256=" + "0" * 64
    )
    _refresh_hash(payload, report, summary_path)

    with pytest.raises(ValueError, match="terminal call ID is absent"):
        _verify(report, manifest, gate="migration")


def test_atomic_json_writer_cleans_temporary_file_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "report.json"

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("scripts.v19_release._common.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_canonical_json(destination, {"status": "PASS"})

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def _verify(report: Path, manifest: Path, *, gate: str) -> dict[str, Any]:
    return verify_alfworld_release(
        report,
        manifest_path=manifest,
        gate=gate,  # type: ignore[arg-type]
        expected_sha=CANDIDATE,
    )


def _release_fixture(
    tmp_path: Path,
    *,
    successes: int,
    harness_invalid_index: int | None = None,
) -> tuple[Path, Path]:
    root = tmp_path / "bundle"
    root.mkdir()
    manifest = root / "locked-manifest.json"
    manifest.write_bytes(LOCKED_MANIFEST.read_bytes())
    locked = _read(manifest)
    entries = locked["entries"]
    execution = root / "inputs/execution.json"
    write_canonical_json(
        execution,
        {
            "schema_version": "alfworld-trial-selection-v1",
            "entries": [
                {
                    key: entry[key]
                    for key in (
                        "expected_logical_scene",
                        "goal_fingerprint",
                        "goal_identity",
                        "identity_status",
                        "trial_id",
                        "trial_sha256",
                    )
                }
                for entry in entries
            ],
        },
    )

    artifacts: dict[str, str] = {}
    report_episodes = []
    summary_episodes = []
    for index, entry in enumerate(entries):
        episode_dir = root / f"run/episode-{index + 1:04d}"
        episode_dir.mkdir(parents=True)
        is_harness_invalid = index == harness_invalid_index
        success = index < successes and not is_harness_invalid
        classification = (
            "harness_operation_failure"
            if is_harness_invalid
            else "agent_success" if success else "agent_model_failure"
        )
        score_eligible = not is_harness_invalid
        run_id = f"episode-run-{index}"
        trace_line = json.dumps(
            {
                "backend_action_count": 1,
                "tool_args": {},
                "tool_name": "robot_go_to",
                "tool_success": True,
            },
            sort_keys=True,
        )
        paths = {
            "trace_ref": episode_dir / "trace.jsonl",
            "model_trace_ref": episode_dir / "model_trace.jsonl",
            "runtime_events_ref": episode_dir / "runtime/runtime_events.jsonl",
            "provider_attempts_ref": episode_dir / "provider_attempts.jsonl",
            "reset_ref": episode_dir / "reset-transaction.json",
            "summary_ref": episode_dir / "summary.json",
        }
        paths["trace_ref"].write_text(trace_line + "\n", encoding="utf-8")
        _write_jsonl(paths["model_trace_ref"], [{"event": "episode_started"}])
        _write_jsonl(
            paths["runtime_events_ref"],
            [
                {
                    "type": "transport.request_started",
                    "payload": {"model": "mimo-v2.5"},
                    "tool_call_id": None,
                },
                {
                    "type": "tool.call_started",
                    "payload": {"arguments": {}},
                    "tool_call_id": f"call-{index}",
                },
            ],
        )
        _write_jsonl(
            paths["provider_attempts_ref"],
            [
                {
                    "cause_code": None,
                    "error_type": None,
                    "model_attempt_id": f"attempt-{index}",
                    "outbound_images": [],
                    "request_sha256": hashlib.sha256(f"request-{index}".encode()).hexdigest(),
                    "response_completed": True,
                    "stripped_images": False,
                }
            ],
        )
        write_canonical_json(
            paths["reset_ref"],
            {
                "schema_version": "alfworld-reset-transaction-v1",
                "ready": True,
                "goal_trial_fingerprint": entry["goal_fingerprint"],
                "setup_backend_action_count": 2,
            },
        )
        episode_summary = {
            "episode_id": f"episode-{index}",
            "failure_reason": None if success else classification,
            "goal_condition_success_rate": 1.0 if success else 0.0,
            "invalid_actions": 0,
            "run_id": run_id,
            "runtime_status": "replied" if success else "failed",
            "steps": 1,
            "success": success,
            "classification": classification,
            "score_eligible": score_eligible,
        }
        write_canonical_json(paths["summary_ref"], episode_summary)
        counts = {
            "agent_tool_calls": 1,
            "setup_backend_actions": 2,
            "control_backend_actions": 0,
            "model_backend_actions": 1,
            "total_backend_actions": 3,
            "total_external_requests": 3,
        }
        summary_episodes.append(
            {
                **episode_summary,
                "trace_path": str(paths["trace_ref"]),
                "agent_tool_call_count": 1,
                "backend_action_count": 1,
                "setup_backend_action_count": 2,
                "control_backend_action_count": 0,
                "model_backend_action_count": 1,
                "total_backend_action_count": 3,
                "total_external_request_count": 3,
                "terminal_tool_call_id": None,
                "terminal_evidence_ref": None,
            }
        )
        refs = {key: path.relative_to(root).as_posix() for key, path in paths.items()}
        report_episodes.append(
            {
                "index": index,
                "trial_id": entry["trial_id"],
                "run_id": run_id,
                **refs,
                "provider_attempt_count": 1,
                "successful_provider_attempt_count": 1,
                "classification": classification,
                "success": success,
                "score_eligible": score_eligible,
                "action_counts": counts,
                "terminal_tool_call_id": None,
                "terminal_evidence_ref": None,
            }
        )

    harness_invalid = int(harness_invalid_index is not None)
    persisted_summary = root / "run/summary.json"
    write_canonical_json(
        persisted_summary,
        {
            "run_id": "release-run",
            "config": {**PROFILE, "provider_name": "Mimo"},
            "episodes": summary_episodes,
            "formal_score_available": harness_invalid == 0,
            "harness_invalid_episodes": harness_invalid,
        },
    )
    for path in [execution, persisted_summary, *root.glob("run/episode-*/*")]:
        if path.is_file():
            artifacts[path.relative_to(root).as_posix()] = sha256_file(path)
    for path in root.glob("run/episode-*/runtime/*"):
        artifacts[path.relative_to(root).as_posix()] = sha256_file(path)

    task_failures = [row["trial_id"] for row in report_episodes if not row["success"]]
    report = root / "alfworld-run.json"
    write_canonical_json(
        report,
        {
            "schema_version": SCHEMA_VERSION,
            "candidate_sha": CANDIDATE,
            "run_id": "release-run",
            "profile": PROFILE,
            "provider": {
                "name": "Mimo",
                "model": "mimo-v2.5",
                "api_format": "openai",
                "transport": "raw_http",
            },
            "manifest": {
                "sha256": sha256_file(manifest),
                "entries": [
                    {key: entry[key] for key in MANIFEST_ENTRY_KEYS}
                    for entry in entries
                ],
            },
            "execution_manifest_ref": execution.relative_to(root).as_posix(),
            "summary_ref": persisted_summary.relative_to(root).as_posix(),
            "episodes": report_episodes,
            "summary": {
                "selected": 4,
                "attempted": 4,
                "eligible": 4 - harness_invalid,
                "success": successes,
                "formal_score_available": harness_invalid == 0,
                "harness_invalid": harness_invalid,
                "contract_violations": 0,
                "task_failures": task_failures,
            },
            "artifacts": artifacts,
        },
    )
    return report, manifest


def _refresh_hash(payload: dict[str, Any], report: Path, artifact: Path) -> None:
    ref = artifact.relative_to(report.parent).as_posix()
    payload["artifacts"][ref] = sha256_file(artifact)
    write_canonical_json(report, payload)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
