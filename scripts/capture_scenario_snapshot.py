#!/usr/bin/env python3
"""Capture a frozen snapshot of the 5 baseline Stage07 scenarios.

Run explicitly to generate or update the committed baseline snapshot.
This script is NOT called by pytest — it produces a committed artifact.

Usage:
    python scripts/capture_scenario_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from homemaster.runtime import REPO_ROOT as HM_ROOT
from homemaster.scenario_catalog import load_catalog
from homemaster.scenario_runner import (
    EXPECTED_FINAL_STATUS,
    STAGE_07_SCENARIOS,
    run_stage_07_scenario_matrix,
)

SNAPSHOT_PATH = HM_ROOT / "plan" / "V1.2" / "baselines" / "scenario_snapshot_v1.json"

# Use isolated output dirs so we don't overwrite git-tracked test artifacts
# under tests/homemaster/llm_cases/stage_07/.
SNAPSHOT_WORK_ROOT = HM_ROOT / "var" / "homemaster" / "snapshots" / "p1_0"

# Build SOURCE_FILES based on each scenario's data_source
GLOBAL_HOMEWORLD = HM_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"
GLOBAL_CORPUS = HM_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"

_catalog = {e.name: e.data_source for e in load_catalog()}
SOURCE_FILES = []
_homeworld_profile_added = False
for _name in STAGE_07_SCENARIOS:
    _base = HM_ROOT / "data" / "scenarios" / _name
    _ds = _catalog.get(_name, "legacy_files")
    if _ds == "homeworld_profile":
        SOURCE_FILES.append(_base / "world_overlay.json")
        SOURCE_FILES.append(_base / "memory_profile.json")
        SOURCE_FILES.append(_base / "failures.json")
        if not _homeworld_profile_added:
            SOURCE_FILES.append(GLOBAL_HOMEWORLD)
            SOURCE_FILES.append(GLOBAL_CORPUS)
            _homeworld_profile_added = True
    else:
        SOURCE_FILES.append(_base / "world.json")
        SOURCE_FILES.append(_base / "memory.json")
        SOURCE_FILES.append(_base / "failures.json")
SOURCE_FILES.append(HM_ROOT / "data" / "scenarios" / "catalog.json")
SOURCE_FILES.append(HM_ROOT / "src" / "homemaster" / "scenario_runner.py")
SOURCE_FILES.append(HM_ROOT / "src" / "homemaster" / "task_runner.py")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=HM_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _extract_stage_pass_fail(stage_statuses: dict) -> dict[str, str]:
    return {stage: info.get("status", "UNKNOWN") for stage, info in stage_statuses.items()}


def main() -> None:
    print("=" * 60)
    print("HomeMaster Scenario Snapshot Capture")
    print("=" * 60)

    # Run all 5 scenarios in live mode (simulated skills).
    # Use isolated work dirs to avoid polluting git-tracked test artifacts.
    snapshot_runtime = SNAPSHOT_WORK_ROOT / "runs"
    snapshot_debug = SNAPSHOT_WORK_ROOT / "debug"
    print(f"\nWork directory: {SNAPSHOT_WORK_ROOT.relative_to(HM_ROOT)}")
    print("Running 5 baseline scenarios (live mode, simulated skills)...")
    matrix = run_stage_07_scenario_matrix(
        runtime_root=snapshot_runtime,
        debug_root=snapshot_debug,
        scenarios=STAGE_07_SCENARIOS,
    )

    # Build snapshot
    scenarios_snapshot: dict[str, dict] = {}
    for result in matrix.case_results:
        scenarios_snapshot[result.scenario] = {
            "utterance": result.utterance,
            "expected_final_status": sorted(EXPECTED_FINAL_STATUS.get(result.scenario, set())),
            "actual_final_status": result.final_status,
            "stage_pass_fail": _extract_stage_pass_fail(result.stage_statuses),
            "model_boundary": result.model_boundary,
        }

    source_file_hashes: dict[str, str] = {}
    for f in SOURCE_FILES:
        rel = f.relative_to(HM_ROOT)
        source_file_hashes[str(rel)] = _sha256(f)

    snapshot = {
        "snapshot_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_mode": "live_simulated",
        "homemaster_commit": _git_commit(),
        "source_file_hashes": source_file_hashes,
        "scenarios": scenarios_snapshot,
    }

    # Write snapshot
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Print summary
    print(f"\nSnapshot written to: {SNAPSHOT_PATH.relative_to(HM_ROOT)}")
    print(f"Git commit: {snapshot['homemaster_commit']}")
    print(f"\n{'Scenario':<35} {'Expected':<12} {'Actual':<12} {'All PASS?'}")
    print("-" * 75)
    for name, data in scenarios_snapshot.items():
        expected = ", ".join(data["expected_final_status"])
        actual = data["actual_final_status"]
        all_pass = all(v == "PASS" for v in data["stage_pass_fail"].values())
        status = "YES" if all_pass else "NO"
        print(f"{name:<35} {expected:<12} {actual:<12} {status}")

    print(f"\nDone. {len(scenarios_snapshot)} scenarios captured.")
    matrix_passed = matrix.passed
    print(f"Matrix passed: {matrix_passed}")
    if not matrix_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
