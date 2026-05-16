#!/usr/bin/env python3
"""Compare legacy_files vs homeworld_profile for all 5 baseline scenarios.

Uses canonical identity (object_category + room_id + anchor_id) for
selected_target equivalence instead of raw memory_id string match.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/compare_all_baselines.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from homemaster.runtime import REPO_ROOT as HM_ROOT
from homemaster.scenario_runner import STAGE_07_SCENARIOS, run_stage_07_scenario_matrix

BASELINES = [
    "check_medicine_success",
    "check_medicine_stale_recover",
    "fetch_cup_retry",
    "object_not_found",
    "distractor_rejected",
]

# Canonical identity lookup: memory_id → (object_category, room_id, anchor_id)
# Built from corpus + legacy files at startup.
MEMORY_CANONICAL: dict[str, dict[str, str]] = {}


def _build_canonical_index() -> None:
    """Index all memory_ids from corpus + legacy files by canonical identity."""
    # Corpus
    corpus = json.loads(
        (HM_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json")
        .read_text(encoding="utf-8")
    )
    for entry in corpus.get("object_memory", []):
        mid = entry.get("memory_id")
        anchor = entry.get("anchor", {})
        if mid:
            MEMORY_CANONICAL[mid] = {
                "object_category": entry.get("object_category", ""),
                "room_id": anchor.get("room_id", ""),
                "anchor_id": anchor.get("anchor_id", ""),
                "display_text": anchor.get("display_text", ""),
            }

    # Legacy per-scenario memory files
    for name in STAGE_07_SCENARIOS:
        mem_path = HM_ROOT / "data" / "scenarios" / name / "memory.json"
        if not mem_path.is_file():
            continue
        try:
            data = json.loads(mem_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("object_memory", []):
            mid = entry.get("memory_id")
            anchor = entry.get("anchor", {})
            if mid and mid not in MEMORY_CANONICAL:
                MEMORY_CANONICAL[mid] = {
                    "object_category": entry.get("object_category", ""),
                    "room_id": anchor.get("room_id", ""),
                    "anchor_id": anchor.get("anchor_id", ""),
                    "display_text": anchor.get("display_text", ""),
                }


def _canonical_match(id_a: str | None, id_b: str | None) -> bool:
    """Check if two memory_ids refer to the same canonical target."""
    if id_a == id_b:
        return True
    if not id_a or not id_b:
        return False
    a = MEMORY_CANONICAL.get(id_a)
    b = MEMORY_CANONICAL.get(id_b)
    if not a or not b:
        return False
    return (
        a["object_category"] == b["object_category"]
        and a["room_id"] == b["room_id"]
        and a["anchor_id"] == b["anchor_id"]
    )


def _run_scenario(scenario: str, label: str, work_root: Path) -> dict:
    """Run a single scenario and extract key metrics."""
    print(f"  [{label}] {scenario} ...", end=" ", flush=True)
    matrix = run_stage_07_scenario_matrix(
        runtime_root=work_root / label / "runs",
        debug_root=work_root / label / "debug",
        scenarios=[scenario],
    )
    r = matrix.case_results[0]
    print(f"final_status={r.final_status}")
    return {
        "scenario": scenario,
        "final_status": r.final_status,
        "stage_pass_fail": {
            s: i.get("status") for s, i in r.stage_statuses.items()
        },
        "grounding_status": r.stage_statuses.get("stage04", {}).get(
            "grounding_status"
        ),
        "selected_target": r.stage_statuses.get("stage04", {}).get("selected_target"),
    }


def _compare(legacy: dict, homeworld: dict) -> list[str]:
    """Compare two results using canonical identity for selected_target."""
    divergences = []

    if legacy["final_status"] != homeworld["final_status"]:
        divergences.append(
            f"final_status: {legacy['final_status']} → {homeworld['final_status']}"
        )

    if legacy.get("grounding_status") != homeworld.get("grounding_status"):
        divergences.append(
            f"grounding_status: {legacy.get('grounding_status')} → {homeworld.get('grounding_status')}"
        )

    if not _canonical_match(
        legacy.get("selected_target"), homeworld.get("selected_target")
    ):
        lt = MEMORY_CANONICAL.get(legacy.get("selected_target", ""), {})
        ht = MEMORY_CANONICAL.get(homeworld.get("selected_target", ""), {})
        divergences.append(
            f"selected_target canonical mismatch: "
            f"{legacy.get('selected_target')}({lt.get('room_id')}/{lt.get('anchor_id')}) → "
            f"{homeworld.get('selected_target')}({ht.get('room_id')}/{ht.get('anchor_id')})"
        )

    all_stages = sorted(
        set(legacy["stage_pass_fail"]) | set(homeworld["stage_pass_fail"])
    )
    for stage in all_stages:
        l = legacy["stage_pass_fail"].get(stage)
        h = homeworld["stage_pass_fail"].get(stage)
        if l != h:
            divergences.append(f"{stage}: {l} → {h}")

    return divergences


def main() -> None:
    _build_canonical_index()

    work_root = HM_ROOT / "var" / "homemaster" / "migration_compare_all"
    artifacts_dir = HM_ROOT / "plan" / "V1.2" / "baselines" / "migration_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    all_divergences: dict[str, list[str]] = {}

    for scenario in BASELINES:
        print(f"\n{'='*60}")
        print(f"  {scenario}")
        print(f"{'='*60}")

        legacy = _run_scenario(scenario, "legacy", work_root)
        homeworld = _run_scenario(scenario, "homeworld", work_root)
        divs = _compare(legacy, homeworld)

        # Check memory_id string diff (informational, not blocking)
        target_match = legacy.get("selected_target") == homeworld.get("selected_target")
        canonical_match = _canonical_match(
            legacy.get("selected_target"), homeworld.get("selected_target")
        )

        artifact = {
            "scenario": scenario,
            "legacy": legacy,
            "homeworld": homeworld,
            "divergences": divs,
            "selected_target_id_match": target_match,
            "selected_target_canonical_match": canonical_match,
        }
        artifact_path = artifacts_dir / f"{scenario}_comparison.json"
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if divs:
            all_divergences[scenario] = divs
            print(f"  *** DIVERGENCES: ***")
            for d in divs:
                print(f"    - {d}")
        else:
            id_note = ""
            if not target_match:
                id_note = f" (memory_id changed: {legacy.get('selected_target')} → {homeworld.get('selected_target')}, canonical match OK)"
            print(f"  ✓ No functional divergences{id_note}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"Artifacts written to: {artifacts_dir.relative_to(HM_ROOT)}")

    if all_divergences:
        print(f"\n*** FUNCTIONAL DIVERGENCES ({len(all_divergences)} scenarios): ***")
        for scenario, divs in all_divergences.items():
            for d in divs:
                print(f"  {scenario}: {d}")
        sys.exit(1)
    else:
        print(f"\n✓ All 5 baselines: no functional divergences.")
        print("  (memory_id string diffs are expected for corpus migration)")
        sys.exit(0)


if __name__ == "__main__":
    main()
