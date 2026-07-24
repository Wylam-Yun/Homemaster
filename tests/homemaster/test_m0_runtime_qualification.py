from __future__ import annotations

import base64
import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.v19_release.run_m0_runtime_qualification import (
    _provider_identity,
)
from scripts.v19_release.verify_m0_runtime_qualification import (
    SCHEMA_VERSION,
    verify_qualification,
)

SHA = "a" * 64
COMMIT = "b" * 40


def test_m0_runner_resolves_provider_from_unified_config(
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
        "non_mock": True,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_path / "bundle"
    source = {
        "schema_version": "alfworld-trial-selection-v1",
        "entries": [{"trial_id": "valid_unseen/example/traj_data.json"}],
    }
    source_path = root / "inputs/source.json"
    _write_json(source_path, source)
    selected = {
        "expected_logical_scene": "FloorPlan308",
        "goal_fingerprint": "c" * 64,
        "trial_id": "valid_unseen/example/traj_data.json",
        "trial_sha256": "",
    }
    trial_path = root / "inputs/trial.json"
    _write_json(trial_path, {"task_type": "look_at_obj_in_light"})
    selected["trial_sha256"] = _sha(trial_path)
    release = {
        "schema_version": "alfworld-v19-release-trials-v1",
        "source": {"sha256": _sha(source_path)},
        "selection": {
            "algorithm": "sha256-rank-v1",
            "count": 4,
            "seed": "homemaster-v1.9-release",
        },
        "entries": [
            {**selected, "rank_digest": str(index) * 64, "source_rank": index}
            for index in range(1, 5)
        ],
    }
    release_path = root / "inputs/release.json"
    _write_json(release_path, release)
    config_path = root / "inputs/base_config.yaml"
    config_path.write_text("dataset: {}\n", encoding="utf-8")
    raw_frame = b"raw-rgb-frame-bytes"
    event_path = root / "run/events/0000-reset.json"
    _write_json(
        event_path,
        {
            "schema_version": "alfworld-external-event-v2",
            "raw_frame_base64": base64.b64encode(raw_frame).decode("ascii"),
        },
    )
    snapshot_path = root / "run/oracle-pose-snapshot.json"
    _write_json(snapshot_path, {"snapshot_sha256": "e" * 64})
    reset = {
        "schema_version": "alfworld-reset-transaction-v1",
        "ready": True,
        "scene_reset_fingerprint": "d" * 64,
        "goal_trial_fingerprint": selected["goal_fingerprint"],
        "snapshot_sha256": "e" * 64,
        "initial_event": {
            "event_ref": "events/0000-reset.json",
            "frame_sha256": hashlib.sha256(raw_frame).hexdigest(),
        },
        "event_files": ["events/0000-reset.json"],
        "snapshot_ref": "oracle-pose-snapshot.json",
        "actions": [
            {"request": {"payload": {"action": "GetReachablePositions"}}},
            {"request": {"payload": {"action": "TeleportFull"}}},
            {"request": {"payload": {"action": "ChangeTimeScale"}}},
        ],
    }
    reset_path = root / "run/reset-transaction.json"
    _write_json(reset_path, reset)
    frame_path = root / "run/frame.png"
    frame_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-frame-bytes")
    attempts_path = root / "run/provider_attempts.jsonl"
    attempts_path.write_text(
        json.dumps(
            {
                "cause_code": None,
                "error_type": None,
                "model_attempt_id": "attempt-1",
                "outbound_images": [
                    {
                        "block_index": 0,
                        "content_sha256": "9" * 64,
                        "message_index": 1,
                    }
                ],
                "request_sha256": "1" * 64,
                "response_completed": True,
                "stripped_images": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    summary_path = root / "run/summary.json"
    _write_json(
        summary_path,
        {
            "episode_count": 1,
            "formal_score_available": True,
            "config": {"env_type": "AlfredThorEnv", "observation_mode": "visual_eval"},
        },
    )
    paths = [
        source_path,
        release_path,
        config_path,
        trial_path,
        reset_path,
        event_path,
        snapshot_path,
        frame_path,
    ]
    paths.extend([attempts_path, summary_path])
    artifacts = {path.relative_to(root).as_posix(): _sha(path) for path in paths}
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "candidate": {
            "homemaster_commit": COMMIT,
            "openharness_commit": "c" * 40,
            "alfworld_commit": "d" * 40,
        },
        "environment": {
            "site": "hpc2",
            "hostname": "gpu3-9",
            "python_executable": "/candidate/.venv/bin/python",
            "python_version": "3.11.15",
            "conda": {"environment_name": None, "explicit_sha256": None, "prefix": None},
            "imports": {
                "homemaster": {
                    "status": "present",
                    "version": "0.1.0",
                    "origin": "/candidate/src/homemaster/__init__.py",
                    "origin_within_candidate": True,
                },
                "alfworld": {
                    "status": "present",
                    "version": "0.5.0",
                    "origin": "/locked/alfworld/__init__.py",
                    "origin_within_locked_source": True,
                },
                "ai2thor": {
                    "status": "present",
                    "version": "2.1.0",
                    "origin": "/candidate/.venv/ai2thor/__init__.py",
                },
            },
            "provider": {"name": "Mimo", "model": "deepseek-v4", "non_mock": True},
            "gpu": {"name": "NVIDIA A40", "driver_version": "575.57.08", "memory_mib": 49140},
            "display": {"name": ":99", "available": True},
            "unity": {
                "platform": "Linux",
                "version": "201909061227",
                "expected_sha256": SHA,
                "verified_sha256": SHA,
            },
        },
        "inputs": {
            "alfworld_config_ref": "inputs/base_config.yaml",
            "alfworld_config_sha256": artifacts["inputs/base_config.yaml"],
            "dataset_identity_sha256": "2" * 64,
            "release_manifest_ref": "inputs/release.json",
            "release_manifest_sha256": artifacts["inputs/release.json"],
            "selected_trial": selected,
            "source_manifest_ref": "inputs/source.json",
            "source_manifest_sha256": artifacts["inputs/source.json"],
            "trial_bytes_ref": "inputs/trial.json",
        },
        "canary": {
            "env_type": "AlfredThorEnv",
            "frame_ref": "run/frame.png",
            "fresh": True,
            "observation_mode": "visual_eval",
            "provider_attempt_count": 1,
            "provider_attempts_ref": "run/provider_attempts.jsonl",
            "reset_ref": "run/reset-transaction.json",
            "scorer_available": True,
            "split": "valid_unseen",
            "summary_ref": "run/summary.json",
        },
        "artifacts": artifacts,
    }
    report_path = root / "qualification.json"
    _write_json(report_path, report)
    return root, report_path, report


def test_independent_verifier_accepts_complete_m0_bundle(tmp_path: Path) -> None:
    root, report_path, _ = _bundle(tmp_path)
    result = verify_qualification(report_path, artifact_root=root, expected_candidate=COMMIT)
    assert result == {
        "status": "PASS",
        "candidate": COMMIT,
        "artifact_count": 10,
        "provider_attempt_count": 1,
        "selected_trial": "valid_unseen/example/traj_data.json",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda report: report.update(status="FAIL"), "status is not PASS"),
        (
            lambda report: report["candidate"].update(homemaster_commit="0" * 40),
            "expected commit",
        ),
        (
            lambda report: report["environment"]["display"].update(available=False),
            "display preflight",
        ),
        (
            lambda report: report["environment"]["unity"].update(verified_sha256="0" * 64),
            "Unity build hash",
        ),
        (
            lambda report: report["canary"].update(provider_attempt_count=0),
            "inconsistent or zero",
        ),
        (
            lambda report: report["canary"].update(scorer_available=False),
            "formal scorer",
        ),
    ],
)
def test_independent_verifier_rejects_false_qualification_claims(
    tmp_path: Path, mutation, message: str
) -> None:
    root, report_path, original = _bundle(tmp_path)
    report = deepcopy(original)
    mutation(report)
    _write_json(report_path, report)
    with pytest.raises(ValueError, match=message):
        verify_qualification(report_path, artifact_root=root, expected_candidate=COMMIT)


def test_independent_verifier_rejects_mutated_reset_frame_and_attempts(tmp_path: Path) -> None:
    root, report_path, report = _bundle(tmp_path)
    cases = (
        ("run/reset-transaction.json", b"{}\n", "artifact hash mismatch"),
        ("run/frame.png", b"not-a-frame", "artifact hash mismatch"),
        ("run/provider_attempts.jsonl", b"", "artifact hash mismatch"),
    )
    for relative, replacement, message in cases:
        path = root / relative
        original = path.read_bytes()
        path.write_bytes(replacement)
        with pytest.raises(ValueError, match=message):
            verify_qualification(report_path, artifact_root=root, expected_candidate=COMMIT)
        path.write_bytes(original)

    reset = json.loads((root / "run/reset-transaction.json").read_text(encoding="utf-8"))
    reset["ready"] = False
    _write_json(root / "run/reset-transaction.json", reset)
    report["artifacts"]["run/reset-transaction.json"] = _sha(
        root / "run/reset-transaction.json"
    )
    _write_json(report_path, report)
    with pytest.raises(ValueError, match="reset transaction did not pass"):
        verify_qualification(report_path, artifact_root=root, expected_candidate=COMMIT)


def test_independent_verifier_has_no_homemaster_product_imports() -> None:
    source = inspect.getsource(
        __import__(
            "scripts.v19_release.verify_m0_runtime_qualification",
            fromlist=["verify_qualification"],
        )
    )
    assert "from homemaster" not in source
    assert "import homemaster" not in source


def test_hkust4_report_requires_hm_alfworld_conda_identity(tmp_path: Path) -> None:
    root, report_path, original = _bundle(tmp_path)
    report = deepcopy(original)
    environment = report["environment"]
    environment["site"] = "hkust4"
    environment["conda"] = {
        "environment_name": "hm_alfworld",
        "explicit_sha256": "8" * 64,
        "prefix": "/conda/envs/hm_alfworld",
    }
    environment["imports"]["alfworld"].update(origin_within_conda_prefix=True)
    environment["imports"]["ai2thor"].update(origin_within_conda_prefix=True)
    _write_json(report_path, report)
    assert verify_qualification(report_path, artifact_root=root)["status"] == "PASS"

    environment["conda"]["environment_name"] = "base"
    _write_json(report_path, report)
    with pytest.raises(ValueError, match="not hm_alfworld"):
        verify_qualification(report_path, artifact_root=root)
