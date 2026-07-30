"""Validation and review rendering for generic-browser trajectory GT."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def validate_trajectory_ground_truth(dag: Mapping[str, Any], *, source_path: Path) -> None:
    if dag.get("schema_version") != "browser-change-ticket-trajectory-v1":
        raise ValueError("unsupported browser trajectory schema")
    scenarios = dag.get("scenarios")
    nodes = dag.get("nodes")
    policy = dag.get("review_policy")
    if not isinstance(scenarios, Mapping) or not isinstance(nodes, list):
        raise ValueError("browser trajectory requires scenarios and nodes")
    if not isinstance(policy, Mapping) or not isinstance(policy.get("required_evidence"), list):
        raise ValueError("browser trajectory requires review evidence policy")
    scenario_ids = set(scenarios)
    required_review_evidence = set(policy["required_evidence"])
    seen: set[str] = set()
    counts = {scenario_id: 0 for scenario_id in scenario_ids}
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("trajectory node must be an object")
        node_id = node.get("node_id")
        tool_name = node.get("tool_name")
        if not isinstance(node_id, str) or not node_id or node_id in seen:
            raise ValueError(f"invalid or duplicate trajectory node: {node_id!r}")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError(f"trajectory node {node_id} has no tool")
        preconditions = node.get("preconditions", [])
        if not isinstance(preconditions, list) or not set(preconditions) <= seen:
            raise ValueError(f"trajectory node {node_id} has forward/unknown precondition")
        required_in = node.get("required_in_scenarios", [])
        if not isinstance(required_in, list) or not set(required_in) <= scenario_ids:
            raise ValueError(f"trajectory node {node_id} has unknown scenario")
        evidence = node.get("required_evidence", [])
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"trajectory node {node_id} has no evidence")
        if node.get("review_step") and not required_review_evidence <= set(evidence):
            raise ValueError(f"trajectory node {node_id} lacks review/backfill evidence")
        for scenario_id in required_in:
            counts[scenario_id] += 1
        seen.add(node_id)
    for scenario_id, count in counts.items():
        declared = scenarios[scenario_id].get("required_node_count")
        if count != declared:
            raise ValueError(f"trajectory count mismatch for {scenario_id}: {count} != {declared}")
    _validate_ticket_provenance(dag, source_path=source_path, node_ids=seen)


def _validate_ticket_provenance(
    dag: Mapping[str, Any], *, source_path: Path, node_ids: set[str]
) -> None:
    ticket_ref = dag.get("ticket_source")
    expected_digest = dag.get("ticket_source_sha256")
    if not isinstance(ticket_ref, str) or not isinstance(expected_digest, str):
        raise ValueError("trajectory requires ticket source and SHA-256")
    ticket_path = (source_path.parent / ticket_ref).resolve()
    if not ticket_path.is_file():
        raise ValueError("trajectory ticket source does not exist")
    ticket_bytes = ticket_path.read_bytes()
    if hashlib.sha256(ticket_bytes).hexdigest() != expected_digest:
        raise ValueError("trajectory ticket source SHA-256 mismatch")
    ticket = json.loads(ticket_bytes)
    sections = (
        "check_before_change",
        "change_implement",
        "change_verified",
        "change_rollback",
    )
    actual_steps = {
        section: {
            item["sop_step_id"]
            for item in ticket.get(section, [])
            if isinstance(item, Mapping) and isinstance(item.get("sop_step_id"), str)
        }
        for section in sections
    }
    coverage = dag.get("ticket_step_coverage")
    if not isinstance(coverage, Mapping) or set(coverage) != set(sections):
        raise ValueError("ticket step coverage must contain every ticket section")
    covered_nodes: set[str] = set()
    for section in sections:
        section_coverage = coverage[section]
        if not isinstance(section_coverage, Mapping):
            raise ValueError(f"ticket coverage section {section} must be an object")
        if set(section_coverage) != actual_steps[section]:
            raise ValueError(f"ticket SOP step coverage mismatch for {section}")
        for step_id, step_nodes in section_coverage.items():
            if (
                not isinstance(step_nodes, list)
                or not step_nodes
                or not set(step_nodes) <= node_ids
            ):
                raise ValueError(f"invalid GT node coverage for ticket step {step_id}")
            overlap = covered_nodes.intersection(step_nodes)
            if overlap:
                raise ValueError(f"GT nodes have ambiguous ticket provenance: {overlap}")
            covered_nodes.update(step_nodes)
    framework_nodes = dag.get("framework_nodes")
    if (
        not isinstance(framework_nodes, list)
        or not set(framework_nodes) <= node_ids
        or covered_nodes.intersection(framework_nodes)
        or covered_nodes.union(framework_nodes) != node_ids
    ):
        raise ValueError("ticket and framework provenance must partition every GT node")


def render_trajectory_markdown(dag: Mapping[str, Any], *, source_path: Path) -> str:
    validate_trajectory_ground_truth(dag, source_path=source_path)
    statuses = dag["external_execution_status"]
    lines = [
        "# Generic Browser Change-Ticket Trajectory GT",
        "",
        "Generated from `agent_trajectory_ground_truth.yaml`; do not edit by hand.",
        "",
        f"Ticket SHA-256: `{dag['ticket_source_sha256']}`",
        "",
        "Execution status: "
        f"normal implementation `{statuses['normal_implementation']}`; "
        f"full normal `{statuses['full_normal']}`; "
        "post-change anomaly/rollback "
        f"`{statuses['post_change_anomaly_rollback']}`.",
        "",
        "| Node | Tool | Scenarios | Preconditions | Review backfill |",
        "|---|---|---|---|---|",
    ]
    for node in dag["nodes"]:
        scenarios = ", ".join(node.get("required_in_scenarios", []))
        preconditions = ", ".join(node.get("preconditions", []))
        review = "required" if node.get("review_step") else "-"
        lines.append(
            f"| `{node['node_id']}` | `{node['tool_name']}` | {scenarios} | "
            f"{preconditions} | {review} |"
        )
    return "\n".join(lines) + "\n"


__all__ = ["render_trajectory_markdown", "validate_trajectory_ground_truth"]
