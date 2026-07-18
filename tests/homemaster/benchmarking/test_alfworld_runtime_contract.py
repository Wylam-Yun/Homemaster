from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.runtime_contract import (
    AlfworldRuntimeIdentity,
    load_runtime_contract,
)


def test_runtime_contract_requires_exact_identity(tmp_path: Path) -> None:
    identity = AlfworldRuntimeIdentity(
        python_version="3.11.15",
        alfworld_version="0.5.0",
        ai2thor_version="2.1.0",
        unity_build_sha256="a" * 64,
        logical_runtime_scene_rule="ai2thor-2.1.0:FloorPlanN->FloorPlanN_physics",
        slice_identity_mode="preserved",
    )
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "alfworld-v18-runtime-contract-v1",
                **identity.to_dict(),
                "gate_evidence_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )

    contract = load_runtime_contract(path, runtime_identity=identity)
    assert contract.gate_evidence_sha256 == "b" * 64

    changed = AlfworldRuntimeIdentity(**{**identity.to_dict(), "ai2thor_version": "2.2.0"})
    with pytest.raises(ValueError, match="runtime identity"):
        load_runtime_contract(path, runtime_identity=changed)


def test_runtime_contract_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"schema_version": "x", "unexpected": True}), encoding="utf-8")
    identity = AlfworldRuntimeIdentity(
        python_version="3.11",
        alfworld_version="0.5.0",
        ai2thor_version="2.1.0",
        unity_build_sha256="a" * 64,
        logical_runtime_scene_rule="rule",
        slice_identity_mode="preserved",
    )
    with pytest.raises(ValueError):
        load_runtime_contract(path, runtime_identity=identity)
