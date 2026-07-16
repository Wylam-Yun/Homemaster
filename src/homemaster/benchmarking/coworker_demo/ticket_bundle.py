"""Validated, hash-locked access to the case_02 dataset bundle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml


class BundleValidationError(ValueError):
    """The requested ticket cannot be bound to a trusted dataset bundle."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root: Path, candidate: Path, label: str) -> Path:
    root = root.resolve()
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise BundleValidationError(f"{label} escapes case root")
    return resolved


def _read_json(path: Path) -> dict[str, Any] | list[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"invalid JSON at {path}: {exc}") from exc


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BundleValidationError(f"invalid YAML at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BundleValidationError(f"YAML root must be a mapping: {path}")
    return payload


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CaseBundle:
    case_root: Path
    ticket_path: Path
    manifest_path: Path
    scenario_path: Path
    dag_path: Path
    scenario_id: str
    ticket: Mapping[str, Any]
    scenario: Mapping[str, Any]
    dag: Mapping[str, Any]
    required_nodes: tuple[Mapping[str, Any], ...]
    locked_hashes: Mapping[str, str]


class CaseRepository:
    """Resolve only the manifest-declared ticket and stable scenario tokens."""

    def __init__(self, case_root: Path | str) -> None:
        self.case_root = Path(case_root).resolve()
        self.manifest_path = _inside(
            self.case_root, self.case_root / "dataset_manifest.json", "manifest"
        )
        manifest = _read_json(self.manifest_path)
        if not isinstance(manifest, dict):
            raise BundleValidationError("dataset manifest root must be an object")
        self._manifest = manifest
        raw_ticket = manifest.get("input_ticket")
        if not isinstance(raw_ticket, str) or not raw_ticket:
            raise BundleValidationError("manifest input_ticket is missing")
        self.ticket_path = _inside(self.case_root, self.case_root / raw_ticket, "input ticket")

    def resolve(self, ticket_path: Path | str, scenario_id: str = "normal") -> CaseBundle:
        supplied = _inside(self.case_root, Path(ticket_path), "ticket")
        if supplied != self.ticket_path:
            raise BundleValidationError("ticket does not match manifest input_ticket")
        self._verify_source_hashes()
        ticket = _read_json(self.ticket_path)
        if not isinstance(ticket, dict) or ticket.get("sop_type") != "CHANGE_SOP":
            raise BundleValidationError("ticket sop_type must be CHANGE_SOP")

        coworker = self._manifest.get("coworker_demo")
        if not isinstance(coworker, dict) or coworker.get("schema_version") != 1:
            raise BundleValidationError("manifest coworker_demo contract is missing")
        scenarios = coworker.get("supported_scenarios")
        if not isinstance(scenarios, dict) or scenario_id not in scenarios:
            raise BundleValidationError(f"unsupported scenario: {scenario_id}")
        scenario_entry = scenarios[scenario_id]
        scenario_path = self._locked_path(scenario_entry, f"scenario {scenario_id}")
        scenario = _read_yaml(scenario_path)
        if scenario.get("schema_version") != 1 or scenario.get("scenario_id") != scenario_id:
            raise BundleValidationError(f"scenario identity mismatch: {scenario_id}")

        dag_entry = coworker.get("trajectory_dag")
        dag_path = self._locked_path(dag_entry, "trajectory DAG")
        dag = _read_yaml(dag_path)
        if dag.get("schema_version") != 1 or not isinstance(dag.get("nodes"), list):
            raise BundleValidationError("trajectory DAG schema is invalid")
        required = tuple(
            node
            for node in dag["nodes"]
            if isinstance(node, dict) and scenario_id in node.get("required_in_scenarios", [])
        )
        expected_count = dag.get("scenarios", {}).get(scenario_id, {}).get("required_node_count")
        if len(required) != expected_count:
            raise BundleValidationError(
                f"trajectory DAG count mismatch for {scenario_id}: "
                f"{len(required)} != {expected_count}"
            )
        hashes = {
            "manifest": _sha256(self.manifest_path),
            "ticket": _sha256(self.ticket_path),
            "scenario": _sha256(scenario_path),
            "trajectory_dag": _sha256(dag_path),
        }
        frozen_dag = _freeze(dag)
        return CaseBundle(
            case_root=self.case_root,
            ticket_path=self.ticket_path,
            manifest_path=self.manifest_path,
            scenario_path=scenario_path,
            dag_path=dag_path,
            scenario_id=scenario_id,
            ticket=_freeze(ticket),
            scenario=_freeze(scenario),
            dag=frozen_dag,
            required_nodes=tuple(
                node for node in frozen_dag["nodes"] if scenario_id in node["required_in_scenarios"]
            ),
            locked_hashes=MappingProxyType(hashes),
        )

    def _verify_source_hashes(self) -> None:
        contract = self._manifest.get("contract")
        declared = contract.get("file_sha256") if isinstance(contract, dict) else None
        if not isinstance(declared, dict) or not declared:
            raise BundleValidationError("manifest source hashes are missing")
        for relative, expected in declared.items():
            path = _inside(self.case_root, self.case_root / relative, f"source {relative}")
            if not path.is_file():
                raise BundleValidationError(f"declared source is missing: {relative}")
            actual = _sha256(path)
            if actual != expected:
                raise BundleValidationError(f"hash mismatch for {relative}")

    def _locked_path(self, entry: Any, label: str) -> Path:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BundleValidationError(f"{label} manifest entry is missing")
        path = _inside(self.case_root, self.case_root / entry["path"], label)
        if not path.is_file():
            raise BundleValidationError(f"{label} file is missing")
        if _sha256(path) != entry.get("sha256"):
            raise BundleValidationError(f"{label} hash mismatch")
        return path


def render_dag_markdown(dag: Mapping[str, Any]) -> str:
    """Render the machine DAG as a deterministic review snapshot."""

    lines = [
        "# Coworker Demo Trajectory DAG",
        "",
        "Generated from `agent_trajectory_ground_truth.yaml`; do not edit by hand.",
        "",
        "| Node | Tool | Scenarios | Preconditions |",
        "|---|---|---|---|",
    ]
    for node in dag.get("nodes", []):
        scenarios = ", ".join(node.get("required_in_scenarios", []))
        preconditions = ", ".join(node.get("preconditions", []))
        lines.append(
            f"| `{node['node_id']}` | `{node['tool_name']}` | {scenarios} | {preconditions} |"
        )
    return "\n".join(lines) + "\n"
