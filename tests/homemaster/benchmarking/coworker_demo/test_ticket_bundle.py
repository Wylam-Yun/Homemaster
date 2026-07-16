from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from homemaster.benchmarking.coworker_demo.ticket_bundle import (
    BundleValidationError,
    CaseRepository,
    render_dag_markdown,
)

CASE_ROOT = Path("data/coworker_demo/case_02")


def test_repository_locks_complete_valid_bundle() -> None:
    bundle = CaseRepository(CASE_ROOT).resolve(
        CASE_ROOT / "test_set/item_change_ticket.json", "normal"
    )
    assert bundle.scenario_id == "normal"
    assert bundle.ticket["sop_type"] == "CHANGE_SOP"
    assert bundle.scenario["variables"]["ItemCode"] == "read"
    assert len(bundle.required_nodes) == 24
    assert (
        bundle.locked_hashes["ticket"]
        == hashlib.sha256(bundle.ticket_path.read_bytes()).hexdigest()
    )


def test_anomaly_is_an_exact_stable_token() -> None:
    repository = CaseRepository(CASE_ROOT)
    assert (
        len(repository.resolve(repository.ticket_path, "post_change_anomaly").required_nodes) == 22
    )
    with pytest.raises(BundleValidationError, match="unsupported scenario"):
        repository.resolve(repository.ticket_path, "please use post_change_anomaly")


def test_ticket_and_manifest_paths_are_contained(tmp_path: Path) -> None:
    root = tmp_path / "case"
    root.mkdir()
    (root / "dataset_manifest.json").write_text(
        json.dumps({"input_ticket": "../ticket.json", "contract": {"file_sha256": {}}}),
        encoding="utf-8",
    )
    with pytest.raises(BundleValidationError, match="escapes case root"):
        CaseRepository(root)


def test_source_hash_mutation_is_rejected(tmp_path: Path) -> None:
    import shutil

    copied = tmp_path / "case"
    shutil.copytree(CASE_ROOT, copied)
    ticket = copied / "test_set/item_change_ticket.json"
    ticket.write_bytes(ticket.read_bytes() + b" ")
    with pytest.raises(BundleValidationError, match="hash mismatch"):
        CaseRepository(copied).resolve(ticket, "normal")


def test_wrong_ticket_schema_is_rejected(tmp_path: Path) -> None:
    import shutil

    copied = tmp_path / "case"
    shutil.copytree(CASE_ROOT, copied)
    ticket = copied / "test_set/item_change_ticket.json"
    payload = json.loads(ticket.read_text(encoding="utf-8-sig"))
    payload["sop_type"] = "INCIDENT_SOP"
    ticket.write_text(json.dumps(payload), encoding="utf-8")
    manifest_path = copied / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract"]["file_sha256"]["test_set/item_change_ticket.json"] = hashlib.sha256(
        ticket.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="sop_type"):
        CaseRepository(copied).resolve(ticket, "normal")


def test_dag_snapshot_is_deterministic_and_complete() -> None:
    source = yaml.safe_load((CASE_ROOT / "agent_trajectory_ground_truth.yaml").read_text())
    rendered = render_dag_markdown(source)
    assert rendered == render_dag_markdown(source)
    for node in source["nodes"]:
        assert rendered.count(f"`{node['node_id']}`") == 1
    assert len(source["nodes"]) == 31
