"""Assert current scenario results match the committed frozen snapshot.

This test does NOT generate or modify the snapshot. It reads the committed
baseline and verifies that running the same scenarios produces the same
stable outputs (final_status, stage pass/fail, key model_boundary fields).

To update the snapshot, run: python scripts/capture_scenario_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from homemaster.runtime import REPO_ROOT
from homemaster.scenario_runner import STAGE_07_SCENARIOS
from homemaster.task_runner import run_homemaster_task

SNAPSHOT_PATH = REPO_ROOT / "plan" / "V1.2" / "baselines" / "scenario_snapshot_v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Only compare these model_boundary keys — they are stable across runs
STABLE_MODEL_BOUNDARY_KEYS = [
    "stage02",
    "stage05_plan",
    "real_robot",
    "real_vla",
    "real_vlm",
]


@pytest.fixture(scope="module")
def snapshot() -> dict:
    """Load the committed snapshot. FAIL if missing."""
    if not SNAPSHOT_PATH.is_file():
        pytest.fail(
            f"Baseline snapshot not found: {SNAPSHOT_PATH.relative_to(REPO_ROOT)}\n"
            "Run: python scripts/capture_scenario_snapshot.py"
        )
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "scenario_name",
    list(STAGE_07_SCENARIOS.keys()),
    ids=list(STAGE_07_SCENARIOS.keys()),
)
def test_scenario_matches_snapshot(
    scenario_name: str,
    snapshot: dict,
    tmp_path: Path,
) -> None:
    """Run one scenario and assert stable fields match the snapshot."""
    expected_entry = snapshot["scenarios"].get(scenario_name)
    assert expected_entry is not None, f"Scenario {scenario_name!r} not in snapshot"

    utterance = STAGE_07_SCENARIOS[scenario_name]
    result = run_homemaster_task(
        utterance=utterance,
        scenario=scenario_name,
        runtime_memory_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        run_id=f"snapshot-check-{scenario_name}",
        live_models=False,
    )

    # 1. final_status must match
    assert result.final_status == expected_entry["actual_final_status"], (
        f"{scenario_name}: final_status mismatch — "
        f"expected {expected_entry['actual_final_status']!r}, got {result.final_status!r}"
    )

    # 2. Each stage pass/fail must match
    expected_stages = expected_entry["stage_pass_fail"]
    for stage, expected_status in expected_stages.items():
        actual_info = result.stage_statuses.get(stage)
        assert actual_info is not None, f"{scenario_name}: stage {stage!r} missing from result"
        actual_status = actual_info.get("status")
        assert actual_status == expected_status, (
            f"{scenario_name}: stage {stage!r} status mismatch — "
            f"expected {expected_status!r}, got {actual_status!r}"
        )

    # 3. Key model_boundary fields must match
    expected_boundary = expected_entry["model_boundary"]
    for key in STABLE_MODEL_BOUNDARY_KEYS:
        if key in expected_boundary:
            assert result.model_boundary.get(key) == expected_boundary[key], (
                f"{scenario_name}: model_boundary[{key!r}] mismatch — "
                f"expected {expected_boundary[key]!r}, got {result.model_boundary.get(key)!r}"
            )


def test_snapshot_scenario_set_matches_code(snapshot: dict) -> None:
    """The snapshot must cover exactly the same 5 scenarios as the code."""
    snapshot_names = set(snapshot["scenarios"].keys())
    code_names = set(STAGE_07_SCENARIOS.keys())
    assert snapshot_names == code_names, (
        f"Scenario set mismatch.\n"
        f"  In snapshot but not code: {snapshot_names - code_names}\n"
        f"  In code but not snapshot: {code_names - snapshot_names}"
    )


def test_source_file_hashes_match(snapshot: dict) -> None:
    """All source files recorded in the snapshot must still match on disk.

    If this fails, it means a source file changed since the snapshot was
    captured. Re-run: python scripts/capture_scenario_snapshot.py
    """
    expected_hashes: dict[str, str] = snapshot.get("source_file_hashes", {})
    assert expected_hashes, "Snapshot missing source_file_hashes"

    mismatches: list[str] = []
    for rel_path, expected_hash in expected_hashes.items():
        actual_path = REPO_ROOT / rel_path
        if not actual_path.is_file():
            mismatches.append(f"  MISSING: {rel_path}")
            continue
        actual_hash = _sha256(actual_path)
        if actual_hash != expected_hash:
            mismatches.append(f"  CHANGED: {rel_path}")

    assert not mismatches, (
        "Source files changed since snapshot was captured.\n"
        "Re-run: python scripts/capture_scenario_snapshot.py\n"
        + "\n".join(mismatches)
    )
