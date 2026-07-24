from __future__ import annotations

import json
from pathlib import Path

from scripts.v19_release._common import sha256_file
from scripts.v19_release.capture_baseline import (
    ALFWORLD_CONTRACT_PATHS,
    COWORKER_CONTRACT_PATHS,
    HOMEMASTER_BASELINE_COMMIT,
    OPENHARNESS_BASELINE_COMMIT,
    _contract_hashes,
    _provider_attempt_contract,
    _tool_surfaces,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "plan/V1.9/baseline"


def test_committed_baseline_artifacts_match_current_cl01_contract_owners() -> None:
    sources = _read("source-commits.json")
    assert sources["homemaster_commit"] == HOMEMASTER_BASELINE_COMMIT
    assert sources["openharness_commit"] == OPENHARNESS_BASELINE_COMMIT
    assert sources["production_roots_match_homemaster_commit"] is True
    assert _read("tool-surfaces.json") == _tool_surfaces()
    assert _read("provider-attempt-contract.json") == _provider_attempt_contract()
    assert _read("alfworld-contract-hashes.json") == _contract_hashes(
        REPO_ROOT, "alfworld", ALFWORLD_CONTRACT_PATHS
    )
    assert _read("coworker-contract-hashes.json") == _contract_hashes(
        REPO_ROOT, "coworker", COWORKER_CONTRACT_PATHS
    )
    locks = _read("dependency-lock-hashes.json")["locks"]
    assert locks == {
        "uv.lock": sha256_file(REPO_ROOT / "uv.lock"),
        "apps/case02_openenv/uv.lock": sha256_file(
            REPO_ROOT / "apps/case02_openenv/uv.lock"
        ),
    }


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
