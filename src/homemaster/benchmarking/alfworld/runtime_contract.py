"""Pinned ALFWorld runtime identity contract loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SliceIdentityMode = Literal["preserved", "replaced_unique"]

_SCHEMA_VERSION = "alfworld-v18-runtime-contract-v1"
_IDENTITY_KEYS = {
    "python_version",
    "alfworld_version",
    "ai2thor_version",
    "unity_build_sha256",
    "logical_runtime_scene_rule",
    "slice_identity_mode",
}
_CONTRACT_KEYS = {"schema_version", "gate_evidence_sha256", *_IDENTITY_KEYS}


@dataclass(frozen=True)
class AlfworldRuntimeIdentity:
    python_version: str
    alfworld_version: str
    ai2thor_version: str
    unity_build_sha256: str
    logical_runtime_scene_rule: str
    slice_identity_mode: SliceIdentityMode

    def __post_init__(self) -> None:
        for name in (
            "python_version",
            "alfworld_version",
            "ai2thor_version",
            "logical_runtime_scene_rule",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        _validate_sha256("unity_build_sha256", self.unity_build_sha256)
        if self.slice_identity_mode not in {"preserved", "replaced_unique"}:
            raise ValueError(f"unsupported Slice identity mode: {self.slice_identity_mode}")

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in sorted(_IDENTITY_KEYS)}


@dataclass(frozen=True)
class AlfworldRuntimeContract:
    schema_version: str
    python_version: str
    alfworld_version: str
    ai2thor_version: str
    unity_build_sha256: str
    logical_runtime_scene_rule: str
    slice_identity_mode: SliceIdentityMode
    gate_evidence_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"unsupported ALFWorld runtime contract schema: {self.schema_version}")
        AlfworldRuntimeIdentity(
            python_version=self.python_version,
            alfworld_version=self.alfworld_version,
            ai2thor_version=self.ai2thor_version,
            unity_build_sha256=self.unity_build_sha256,
            logical_runtime_scene_rule=self.logical_runtime_scene_rule,
            slice_identity_mode=self.slice_identity_mode,
        )
        _validate_sha256("gate_evidence_sha256", self.gate_evidence_sha256)

    @property
    def runtime_identity(self) -> AlfworldRuntimeIdentity:
        return AlfworldRuntimeIdentity(
            python_version=self.python_version,
            alfworld_version=self.alfworld_version,
            ai2thor_version=self.ai2thor_version,
            unity_build_sha256=self.unity_build_sha256,
            logical_runtime_scene_rule=self.logical_runtime_scene_rule,
            slice_identity_mode=self.slice_identity_mode,
        )


def load_runtime_contract(
    path: Path,
    *,
    runtime_identity: AlfworldRuntimeIdentity,
) -> AlfworldRuntimeContract:
    payload = _load_json_object(path)
    actual_keys = set(payload)
    if actual_keys != _CONTRACT_KEYS:
        raise ValueError(
            "runtime contract keys differ: "
            f"missing={sorted(_CONTRACT_KEYS - actual_keys)}, "
            f"unknown={sorted(actual_keys - _CONTRACT_KEYS)}"
        )
    try:
        contract = AlfworldRuntimeContract(**payload)
    except TypeError as exc:
        raise ValueError("runtime contract fields have invalid types") from exc
    if contract.runtime_identity != runtime_identity:
        raise ValueError("runtime identity does not match the pinned contract")
    return contract


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read runtime contract: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("runtime contract must be a JSON object")
    return payload


def _validate_sha256(name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
