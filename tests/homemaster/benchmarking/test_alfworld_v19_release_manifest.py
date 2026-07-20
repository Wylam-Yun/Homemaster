from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.trial_selection import build_trial_selection_entry
from scripts.v19_release._common import write_canonical_json
from scripts.v19_release.alfworld_release_manifest import (
    ALGORITHM,
    SEED,
    build_release_manifest,
    verify_release_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "config/alfworld_v18_regression_trials.json"
RELEASE = REPO_ROOT / "config/alfworld_v19_release_trials.json"


def test_committed_release_manifest_is_deterministic_and_locked_before_results() -> None:
    report = verify_release_manifest(RELEASE, source_path=SOURCE)
    payload = json.loads(RELEASE.read_text(encoding="utf-8"))

    assert report == {
        "status": "PASS",
        "selected": 4,
        "source_entries": 10,
        "source_sha256": "88058c40461ac22240a1d8a887b9978349626ec9b69fa7d3fa20e9eecba154e1",
        "manifest_sha256": "64bb982a755a86d4c997cdaf7ff6898f17b2add1f6af1f095d7694845cf7c1ab",
        "algorithm": ALGORITHM,
        "seed": SEED,
        "dataset_bytes_verified": False,
    }
    assert [entry["source_rank"] for entry in payload["entries"]] == [1, 2, 3, 4]
    assert [Path(entry["trial_id"]).parent.name for entry in payload["entries"]] == [
        "trial_T20190907_161210_531813",
        "trial_T20190908_201421_021646",
        "trial_T20190909_032721_511027",
        "trial_T20190907_183715_299073",
    ]
    assert not ({"success", "classification", "score"} & _all_keys(payload))


def test_builder_output_is_byte_deterministic(tmp_path: Path) -> None:
    first = build_release_manifest(
        SOURCE, source_display_path="config/alfworld_v18_regression_trials.json"
    )
    second = build_release_manifest(
        SOURCE, source_display_path="config/alfworld_v18_regression_trials.json"
    )
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    write_canonical_json(first_path, first)
    write_canonical_json(second_path, second)
    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()


@pytest.mark.parametrize("count", [3, 5])
def test_verifier_rejects_any_count_other_than_four(tmp_path: Path, count: int) -> None:
    payload = json.loads(RELEASE.read_text(encoding="utf-8"))
    if count == 3:
        payload["entries"] = payload["entries"][:3]
    else:
        payload["entries"].append(payload["entries"][0])
    path = tmp_path / "release.json"
    write_canonical_json(path, payload)

    with pytest.raises(ValueError, match="exactly 4"):
        verify_release_manifest(path, source_path=SOURCE)


def test_verifier_rejects_source_hash_and_extra_field_drift(tmp_path: Path) -> None:
    drifted_source = tmp_path / "source.json"
    drifted_source.write_bytes(SOURCE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="source hash drifted"):
        verify_release_manifest(RELEASE, source_path=drifted_source)

    payload = json.loads(RELEASE.read_text(encoding="utf-8"))
    payload["entries"][0]["score"] = 1
    path = tmp_path / "release.json"
    write_canonical_json(path, payload)
    with pytest.raises(ValueError, match="keys differ"):
        verify_release_manifest(path, source_path=SOURCE)


def test_verifier_rejects_safe_but_incorrect_source_display_path(tmp_path: Path) -> None:
    payload = json.loads(RELEASE.read_text(encoding="utf-8"))
    payload["source"]["path"] = "other/safe.json"
    path = tmp_path / "release.json"
    write_canonical_json(path, payload)
    with pytest.raises(ValueError, match="source path drifted"):
        verify_release_manifest(path, source_path=SOURCE)


def test_builder_rejects_unsafe_source_trial_path(tmp_path: Path) -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    payload["entries"][0]["trial_id"] = "../escape/traj_data.json"
    path = tmp_path / "source.json"
    write_canonical_json(path, payload)
    with pytest.raises(ValueError, match="trial_id"):
        build_release_manifest(path)


def test_dataset_bytes_are_verified_through_v18_trial_validator(tmp_path: Path) -> None:
    trial_root = tmp_path / "dataset"
    source_entries = []
    for index in range(5):
        trial = trial_root / f"valid_unseen/task-{index}/trial-{index}/traj_data.json"
        trial.parent.mkdir(parents=True)
        trial.write_text(
            json.dumps(
                {
                    "task_type": "pick_and_place_simple",
                    "pddl_params": {
                        "object_target": f"Cup{index}",
                        "parent_target": "Cabinet",
                        "toggle_target": "",
                        "mrecep_target": "",
                        "object_sliced": False,
                    },
                    "scene": {"floor_plan": f"FloorPlan{index + 1}"},
                }
            ),
            encoding="utf-8",
        )
        entry = build_trial_selection_entry(
            trial,
            trial_root=trial_root,
            expected_logical_scene=f"FloorPlan{index + 1}",
            identity_status="test",
        )
        source_entries.append(
            {key: value for key, value in asdict(entry).items() if key != "portable_fingerprint"}
        )
    source = tmp_path / "source.json"
    write_canonical_json(
        source,
        {"schema_version": "alfworld-trial-selection-v1", "entries": source_entries},
    )
    release = tmp_path / "release.json"
    write_canonical_json(
        release,
        build_release_manifest(source, trial_root=trial_root),
    )

    report = verify_release_manifest(release, source_path=source, trial_root=trial_root)
    assert report["dataset_bytes_verified"] is True
    selected = json.loads(release.read_text(encoding="utf-8"))["entries"][0]
    (trial_root / selected["trial_id"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes hash mismatch"):
        verify_release_manifest(release, source_path=source, trial_root=trial_root)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()
