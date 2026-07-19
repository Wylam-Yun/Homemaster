from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.coworker_demo.verify_run_bundle import (
    _changed_pixels,
    _frame_stats,
    verify_external_end_state,
)


def test_verifier_has_no_product_imports() -> None:
    path = Path("scripts/coworker_demo/verify_run_bundle.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.startswith("case02_openenv") for name in imported)
    assert not any(name.startswith("homemaster.benchmarking.coworker_demo") for name in imported)


def test_verifier_rederives_required_nodes_from_raw_evidence() -> None:
    source = Path("scripts/coworker_demo/verify_run_bundle.py").read_text(encoding="utf-8")
    assert "raw_actions.jsonl" in source
    assert "required - observed" in source
    assert "ffprobe" in source
    assert "manifest_hash" in source
    assert "missing_manifest_entry" in source
    assert "manifest_incomplete" in source
    assert "unknown_evidence_ref" in source
    assert '"rawvideo"' in source
    assert "first_packet_not_proven" in source


def test_independent_frame_metrics_are_derived_from_raw_rgb() -> None:
    frame = bytes([0, 0, 0, 255, 255, 255])
    stats, grayscale = _frame_stats(frame, 2, 1)
    assert stats == {
        "nonblack_ratio": 0.5,
        "dark_ratio": 0.5,
        "variance": 16256.25,
    }
    assert grayscale == bytes([0, 255])
    assert _changed_pixels(grayscale, bytes([0, 254])) == 1


def _write_external_fixture(root: Path, scenario: str) -> None:
    variables = {
        "TenantId": "tenant-a",
        "ItemCode": "item-a",
        "SpecCode": "spec-a",
        "ExtensionName": "extension-a",
    }
    key = "tenant-a:item-a"
    expected = {
        "ExtensionName": "extension-a",
        "ItemCode": "item-a",
        "SpecCode": "spec-a",
        "TenantId": "tenant-a",
    }
    state_path = root / "environment/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "variables": variables,
                "terminal_outcome": "complete" if scenario == "normal" else "rolled_back",
            }
        ),
        encoding="utf-8",
    )
    config_path = (
        root / "environment/episode_root/service_layer/component/config/extension_item_mapping.json"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({key: expected} if scenario == "normal" else {}), encoding="utf-8"
    )
    raw = [
        {
            "sequence": 1,
            "kind": "automation_job_submitted",
            "status": "accepted",
            "arguments": {"operation": "add", "job_id": "job-add-aaaaaaaaaa", "run_id": "run-a"},
        },
        {
            "sequence": 2,
            "kind": "automation_job_status",
            "status": "succeeded",
            "arguments": {"operation": "add", "job_id": "job-add-aaaaaaaaaa", "return_code": 0},
        },
        {
            "sequence": 3,
            "kind": "command_completed",
            "node_id": "ADD_GREP",
            "status": "succeeded",
            "arguments": {},
        },
    ]
    if scenario == "normal":
        raw.extend(
            [
                {
                    "sequence": 4,
                    "kind": "automation_job_submitted",
                    "status": "accepted",
                    "arguments": {
                        "operation": "business_verify",
                        "job_id": "job-business_verify-aaaaaaaaaa",
                        "run_id": "run-a",
                    },
                },
                {
                    "sequence": 5,
                    "kind": "automation_job_status",
                    "status": "succeeded",
                    "arguments": {
                        "operation": "business_verify",
                        "job_id": "job-business_verify-aaaaaaaaaa",
                        "return_code": 0,
                    },
                },
            ]
        )
    else:
        raw.extend(
            [
                {
                    "sequence": 4,
                    "kind": "monitor_query",
                    "node_id": "ANOMALY_FOUND",
                    "status": "succeeded",
                    "arguments": {
                        "caused_by_current_change": True,
                        "causal_add_job_id": "job-add-aaaaaaaaaa",
                    },
                },
                {
                    "sequence": 5,
                    "kind": "sop_decision",
                    "node_id": "ROLLBACK_DECISION",
                    "status": "succeeded",
                    "arguments": {},
                },
                {
                    "sequence": 6,
                    "kind": "automation_job_submitted",
                    "status": "accepted",
                    "arguments": {
                        "operation": "remove",
                        "job_id": "job-remove-bbbbbbbbbb",
                        "run_id": "run-a",
                    },
                },
                {
                    "sequence": 7,
                    "kind": "automation_job_status",
                    "status": "succeeded",
                    "arguments": {
                        "operation": "remove",
                        "job_id": "job-remove-bbbbbbbbbb",
                        "return_code": 0,
                    },
                },
            ]
        )
    (root / "trajectory").mkdir(parents=True, exist_ok=True)
    (root / "trajectory/raw_actions.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in raw), encoding="utf-8"
    )
    commands = [
        {"exit_code": 0, "stdout_path": "terminal/add/stdout"},
    ]
    (root / "terminal/add").mkdir(parents=True)
    (root / "terminal/add/stdout").write_text(
        json.dumps({key: expected}, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if scenario == "post_change_anomaly":
        commands.append({"exit_code": 1, "stdout_path": "terminal/remove/stdout"})
        (root / "terminal/remove").mkdir(parents=True)
        (root / "terminal/remove/stdout").write_text("", encoding="utf-8")
    (root / "terminal").mkdir(parents=True, exist_ok=True)
    (root / "terminal/commands.jsonl").write_text(
        "".join(json.dumps(command) + "\n" for command in commands), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("scenario", "mutation", "expected"),
    [
        ("normal", "config", "normal_external_config"),
        ("normal", "business", "normal_business_job_return_code"),
        ("normal", "business_wrong_run", "normal_business_job_return_code"),
        ("normal", "grep", "add_grep_stdout"),
        ("post_change_anomaly", "alarm", "anomaly_causal_alarm_job"),
        ("post_change_anomaly", "order", "anomaly_rollback_order"),
        ("post_change_anomaly", "remove", "anomaly_remove_return_code"),
        ("post_change_anomaly", "remove_wrong_run", "anomaly_remove_return_code"),
        ("post_change_anomaly", "rollback_stdout", "anomaly_rollback_grep_stdout"),
    ],
)
def test_external_end_state_verifier_rejects_one_mutated_fact(
    tmp_path: Path, scenario: str, mutation: str, expected: str
) -> None:
    _write_external_fixture(tmp_path, scenario)
    config_path = (
        tmp_path
        / "environment/episode_root/service_layer/component/config/extension_item_mapping.json"
    )
    raw_path = tmp_path / "trajectory/raw_actions.jsonl"
    if mutation == "config":
        config_path.write_text("{}", encoding="utf-8")
    elif mutation == "business":
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        raw[-1]["arguments"]["return_code"] = 1
        raw_path.write_text("".join(json.dumps(event) + "\n" for event in raw))
    elif mutation == "business_wrong_run":
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        raw[-2]["arguments"]["run_id"] = "other-run"
        raw_path.write_text("".join(json.dumps(event) + "\n" for event in raw))
    elif mutation == "grep":
        (tmp_path / "terminal/add/stdout").write_text("wrong\n", encoding="utf-8")
    elif mutation == "alarm":
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        raw[3]["arguments"]["causal_add_job_id"] = "job-add-wrong00000"
        raw_path.write_text("".join(json.dumps(event) + "\n" for event in raw))
    elif mutation == "order":
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        raw[4]["sequence"], raw[5]["sequence"] = 7, 5
        raw_path.write_text("".join(json.dumps(event) + "\n" for event in raw))
    elif mutation == "remove":
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        raw[6]["arguments"]["return_code"] = 1
        raw_path.write_text("".join(json.dumps(event) + "\n" for event in raw))
    elif mutation == "remove_wrong_run":
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        raw[5]["arguments"]["run_id"] = "other-run"
        raw_path.write_text("".join(json.dumps(event) + "\n" for event in raw))
    elif mutation == "rollback_stdout":
        (tmp_path / "terminal/remove/stdout").write_text("unexpected\n", encoding="utf-8")
    else:
        raise AssertionError(mutation)

    failures = verify_external_end_state(tmp_path, scenario)

    assert expected in failures


@pytest.mark.parametrize("scenario", ["normal", "post_change_anomaly"])
def test_external_end_state_valid_fixture_passes_per_scenario(
    tmp_path: Path, scenario: str
) -> None:
    _write_external_fixture(tmp_path, scenario)

    assert verify_external_end_state(tmp_path, scenario) == []
