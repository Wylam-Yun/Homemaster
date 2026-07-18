from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.trial_selection import (
    TrialSelectionEntry,
    build_trial_selection_entry,
    load_trial_selection_manifest,
    load_verified_trial_data,
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _trial_payload(*, scene: str = "FloorPlan10") -> bytes:
    return json.dumps(
        {
            "task_type": "pick_and_place_simple",
            "pddl_params": {
                "object_target": "Cup",
                "parent_target": "Cabinet",
                "toggle_target": None,
                "mrecep_target": None,
                "object_sliced": False,
            },
            "scene": {"floor_plan": scene},
        },
        sort_keys=True,
    ).encode("utf-8")


def _entry_payload(entry: TrialSelectionEntry) -> dict[str, str]:
    return {
        "trial_id": entry.trial_id,
        "trial_sha256": entry.trial_sha256,
        "expected_logical_scene": entry.expected_logical_scene,
        "goal_identity": entry.goal_identity,
        "goal_fingerprint": entry.goal_fingerprint,
        "identity_status": entry.identity_status,
    }


def test_trial_selection_is_portable_across_roots(tmp_path: Path) -> None:
    payload = _trial_payload()
    roots = [tmp_path / "left", tmp_path / "right"]
    manifests = []
    for root in roots:
        trial = root / "data/valid_unseen/trial-1/traj_data.json"
        trial.parent.mkdir(parents=True)
        trial.write_bytes(payload)
        entry = build_trial_selection_entry(
            trial,
            trial_root=root,
            expected_logical_scene="FloorPlan10",
            identity_status="historical_exact",
        )
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "alfworld-trial-selection-v1",
                    "entries": [_entry_payload(entry)],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        manifests.append(load_trial_selection_manifest(manifest_path, trial_root=root))

    assert manifests[0].entries[0].portable_fingerprint == (
        manifests[1].entries[0].portable_fingerprint
    )
    assert load_verified_trial_data(
        manifests[0].entries[0],
        trial_root=roots[0],
    ) == json.loads(payload)


def test_trial_selection_rejects_goal_or_scene_drift(tmp_path: Path) -> None:
    trial = tmp_path / "trial/traj_data.json"
    trial.parent.mkdir()
    trial.write_bytes(_trial_payload())
    entry = build_trial_selection_entry(
        trial,
        trial_root=tmp_path,
        expected_logical_scene="FloorPlan10",
        identity_status="taskset_declared",
    )

    trial.write_bytes(_trial_payload(scene="FloorPlan11"))

    with pytest.raises(ValueError, match="trial bytes hash mismatch"):
        load_verified_trial_data(entry, trial_root=tmp_path)


@pytest.mark.parametrize(
    "trial_id",
    ["/absolute/trial.json", "../trial.json", "a/../trial.json", "a\\trial.json", "a\x00b"],
)
def test_trial_selection_rejects_unsafe_ids(trial_id: str) -> None:
    with pytest.raises(ValueError):
        TrialSelectionEntry(
            trial_id=trial_id,
            trial_sha256="0" * 64,
            expected_logical_scene="FloorPlan10",
            goal_identity="goal",
            goal_fingerprint="1" * 64,
            identity_status="historical_exact",
        )


def test_manifest_rejects_gate_case_fields_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "escape.json").symlink_to(outside)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "alfworld-trial-selection-v1",
                "entries": [
                    {
                        "trial_id": "escape.json",
                        "trial_sha256": _sha(b"{}"),
                        "expected_logical_scene": "FloorPlan10",
                        "goal_identity": "goal",
                        "goal_fingerprint": "1" * 64,
                        "identity_status": "historical_exact",
                        "pose": {"x": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_trial_selection_manifest(manifest, trial_root=root)


def test_committed_v18_regression_manifest_pins_ten_trials() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    payload = json.loads(
        (repo_root / "config/alfworld_v18_regression_trials.json").read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == "alfworld-trial-selection-v1"
    entries = payload["entries"]
    assert [entry["identity_status"] for entry in entries] == [
        "deterministic_replacement",
        "historical_exact",
        "deterministic_replacement",
        "historical_exact",
        "historical_exact",
        "historical_exact",
        "deterministic_replacement",
        "historical_exact",
        "deterministic_replacement",
        "historical_exact",
    ]
    assert [Path(entry["trial_id"]).parent.name for entry in entries] == [
        "trial_T20190908_041333_727215",
        "trial_T20190909_103553_077615",
        "trial_T20190907_083346_800823",
        "trial_T20190909_021728_339782",
        "trial_T20190909_032721_511027",
        "trial_T20190918_154424_844749",
        "trial_T20190907_183715_299073",
        "trial_T20190908_201421_021646",
        "trial_T20190907_161210_531813",
        "trial_T20190908_121952_610012",
    ]
    allowed_keys = {
        "expected_logical_scene",
        "goal_fingerprint",
        "goal_identity",
        "identity_status",
        "trial_id",
        "trial_sha256",
    }
    assert all(set(entry) == allowed_keys for entry in entries)
