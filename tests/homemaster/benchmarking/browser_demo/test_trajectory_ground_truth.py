from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from homemaster.benchmarking.browser_demo.trajectory import (
    render_trajectory_markdown,
    validate_trajectory_ground_truth,
)

CASE_ROOT = Path("data/browser_demo/case_02")
GT_PATH = CASE_ROOT / "agent_trajectory_ground_truth.yaml"


def _load() -> dict:
    return yaml.safe_load(GT_PATH.read_text())


def test_full_normal_and_rollback_trajectories_are_locked() -> None:
    dag = _load()
    validate_trajectory_ground_truth(dag, source_path=GT_PATH)

    assert dag["scenarios"] == {
        "normal": {"required_node_count": 26},
        "post_change_anomaly": {"required_node_count": 23},
    }
    assert dag["nodes"][0]["node_id"] == "SKILL_LOADED"
    assert dag["nodes"][0]["argument_predicates"] == {"name": "change-ticket-executor"}
    assert dag["nodes"][-1]["node_id"] == "ROLLBACK_COMPLETE"


def test_gt_uses_generic_browser_semantics_and_requires_review_backfill() -> None:
    dag = _load()
    serialized = yaml.safe_dump(dag, sort_keys=True)

    assert "bid:" not in serialized
    assert "route:" not in serialized
    assert "terminal_execute" not in serialized
    assert "sop_decide" not in serialized
    required = set(dag["review_policy"]["required_evidence"])
    for node in dag["nodes"]:
        if node.get("review_step"):
            assert required <= set(node["required_evidence"]), node["node_id"]


def test_ticket_digest_is_verified_against_source_bytes() -> None:
    dag = copy.deepcopy(_load())
    dag["ticket_source_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_trajectory_ground_truth(dag, source_path=GT_PATH)


def test_generated_review_snapshot_matches_machine_truth() -> None:
    dag = _load()
    rendered = render_trajectory_markdown(dag, source_path=GT_PATH)

    assert rendered == (CASE_ROOT / "agent_trajectory_ground_truth.md").read_text()
    for node in dag["nodes"]:
        assert rendered.count(f"`{node['node_id']}`") == 1
