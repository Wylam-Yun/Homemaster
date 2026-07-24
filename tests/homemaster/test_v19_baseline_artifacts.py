from __future__ import annotations

import json
from pathlib import Path

from scripts.v19_release._common import canonical_json_bytes, sha256_bytes
from scripts.v19_release.capture_baseline import (
    ALFWORLD_CONTRACT_PATHS,
    COWORKER_CONTRACT_PATHS,
    HOMEMASTER_BASELINE_COMMIT,
    OPENHARNESS_BASELINE_COMMIT,
    _provider_attempt_contract,
    _tool_surfaces,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "plan/V1.9/baseline"


def test_committed_baseline_artifacts_are_internally_consistent() -> None:
    sources = _read("source-commits.json")
    assert sources["homemaster_commit"] == HOMEMASTER_BASELINE_COMMIT
    assert sources["openharness_commit"] == OPENHARNESS_BASELINE_COMMIT
    assert sources["production_roots_match_homemaster_commit"] is True
    assert _read("tool-surfaces.json") == _tool_surfaces()
    assert _read("provider-attempt-contract.json") == _provider_attempt_contract()
    _assert_contract_snapshot(
        _read("alfworld-contract-hashes.json"),
        schema="homemaster-v1.9-baseline-alfworld-contracts-v1",
        paths=ALFWORLD_CONTRACT_PATHS,
    )
    _assert_contract_snapshot(
        _read("coworker-contract-hashes.json"),
        schema="homemaster-v1.9-baseline-coworker-contracts-v1",
        paths=COWORKER_CONTRACT_PATHS,
    )
    locks = _read("dependency-lock-hashes.json")["locks"]
    assert set(locks) == {"uv.lock", "apps/case02_openenv/uv.lock"}
    assert all(_is_sha256(digest) for digest in locks.values())


def test_committed_baseline_inventory_contains_domain_authority_characterization() -> None:
    inventory = set((BASELINE / "test-inventory.txt").read_text(encoding="utf-8").splitlines())
    assert {
        "tests/homemaster/test_cli_run.py::test_run_command_status_field",
        "tests/homemaster/test_cli_interactive.py::test_shell_status_reports_last_turn_status",
        "tests/homemaster/benchmarking/test_alfworld_runtime_contract.py::test_runtime_contract_requires_exact_identity",
        "tests/homemaster/benchmarking/coworker_demo/test_registry.py::test_registry_contains_exactly_eleven_tools_in_stable_order",
    } <= inventory


def test_committed_baseline_test_evidence_is_a_passing_sanitized_run() -> None:
    evidence = (BASELINE / "pytest-nonlive.txt").read_text(encoding="utf-8")
    assert "exit_code: 0" in evidence
    assert " passed," in evidence
    assert str(REPO_ROOT) not in evidence


def _read(name: str) -> dict:
    return json.loads((BASELINE / name).read_text(encoding="utf-8"))


def _assert_contract_snapshot(
    snapshot: dict,
    *,
    schema: str,
    paths: tuple[str, ...],
) -> None:
    assert snapshot["schema_version"] == schema
    assert set(snapshot["files"]) == set(paths)
    assert all(_is_sha256(digest) for digest in snapshot["files"].values())
    assert snapshot["aggregate_sha256"] == sha256_bytes(
        canonical_json_bytes(snapshot["files"])
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
