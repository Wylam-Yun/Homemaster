"""Skill-compatible capability registry for Phase A."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from task_brain.domain import CapabilitySpec

ADAPTER_FACING_CAPABILITIES = (
    "mock_vln.navigate",
    "mock_perception.observe",
    "robobrain.plan",
    "mock_atomic_executor.execute",
)

_CAPABILITY_ALIASES = {
    "memory.update": "memory.reconcile",
}


def default_capability_registry() -> dict[str, CapabilitySpec]:
    """Return the default Phase A capability registry."""
    raw_registry = {
        "mock_vln.navigate": {
            "name": "mock_vln.navigate",
            "input_schema": {
                "type": "object",
                "properties": {"viewpoint_id": {"type": "string"}},
                "required": ["viewpoint_id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "arrived": {"type": "boolean"},
                    "evidence": {"type": "object"},
                },
                "required": ["status", "arrived", "evidence"],
            },
            "failure_modes": ["invalid_viewpoint", "navigation_failed", "timeout"],
            "timeout_s": 5.0,
            "returns_evidence": True,
        },
        "mock_perception.observe": {
            "name": "mock_perception.observe",
            "input_schema": {
                "type": "object",
                "properties": {
                    "viewpoint_id": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["viewpoint_id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "observation": {"type": "object"},
                    "evidence": {"type": "object"},
                },
                "required": ["observation", "evidence"],
            },
            "failure_modes": ["perception_failed", "timeout"],
            "timeout_s": 3.0,
            "returns_evidence": True,
        },
        "robobrain.plan": {
            "name": "robobrain.plan",
            "input_schema": {
                "type": "object",
                "properties": {
                    "subgoal": {"type": "object"},
                    "target_object": {"type": "object"},
                    "current_observation": {"type": "object"},
                    "constraints": {"type": "object"},
                    "success_conditions": {"type": "array"},
                    "runtime_state": {"type": "object"},
                },
                "required": ["subgoal"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "atomic_plan": {"type": "array"},
                    "evidence": {"type": "object"},
                },
                "required": ["atomic_plan", "evidence"],
            },
            "failure_modes": ["planning_failed", "invalid_subgoal", "timeout"],
            "timeout_s": 8.0,
            "returns_evidence": True,
        },
        "mock_atomic_executor.execute": {
            "name": "mock_atomic_executor.execute",
            "input_schema": {
                "type": "object",
                "properties": {"atomic_plan": {"type": "array"}},
                "required": ["atomic_plan"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "execution_result": {"type": "object"},
                    "evidence": {"type": "object"},
                },
                "required": ["status", "execution_result", "evidence"],
            },
            "failure_modes": ["execution_failed", "action_rejected", "timeout"],
            "timeout_s": 10.0,
            "returns_evidence": True,
        },
        "verification.evaluate": {
            "name": "verification.evaluate",
            "input_schema": {
                "type": "object",
                "properties": {
                    "success_conditions": {"type": "array"},
                    "evidence": {"type": "object"},
                },
                "required": ["success_conditions", "evidence"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "failed_conditions": {"type": "array"},
                    "evidence": {"type": "object"},
                },
                "required": ["passed", "failed_conditions", "evidence"],
            },
            "failure_modes": ["invalid_evidence", "unsupported_condition"],
            "timeout_s": 2.0,
            "returns_evidence": True,
        },
        "recovery.analyze_failure": {
            "name": "recovery.analyze_failure",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verification_result": {"type": "object"},
                    "runtime_state": {"type": "object"},
                },
                "required": ["verification_result", "runtime_state"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "failure_type": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "object"},
                },
                "required": ["failure_type", "reason", "evidence"],
            },
            "failure_modes": ["analysis_failed"],
            "timeout_s": 2.0,
            "returns_evidence": True,
        },
        "recovery.decide": {
            "name": "recovery.decide",
            "input_schema": {
                "type": "object",
                "properties": {
                    "failure_analysis": {"type": "object"},
                    "runtime_state": {"type": "object"},
                },
                "required": ["failure_analysis", "runtime_state"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "object"},
                },
                "required": ["action", "reason", "evidence"],
            },
            "failure_modes": ["decision_failed"],
            "timeout_s": 2.0,
            "returns_evidence": True,
        },
        "memory.reconcile": {
            "name": "memory.reconcile",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verified_evidence": {"type": "object"},
                    "memory_state": {"type": "object"},
                },
                "required": ["verified_evidence", "memory_state"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "updates": {"type": "array"},
                    "evidence": {"type": "object"},
                },
                "required": ["updates", "evidence"],
            },
            "failure_modes": ["reconcile_failed"],
            "timeout_s": 3.0,
            "returns_evidence": True,
        },
    }
    return validate_capability_registry(raw_registry)


def validate_capability_registry(
    registry: Mapping[str, CapabilitySpec | dict[str, Any]],
) -> dict[str, CapabilitySpec]:
    """Validate registry payload and normalize values to CapabilitySpec."""
    normalized: dict[str, CapabilitySpec] = {}
    for capability_name, payload in registry.items():
        canonical_key = _canonical_capability_name(capability_name)
        spec = (
            payload.model_copy(deep=True)
            if isinstance(payload, CapabilitySpec)
            else CapabilitySpec.model_validate(payload)
        )
        canonical_payload_name = _canonical_capability_name(spec.name)
        if canonical_payload_name != canonical_key:
            raise ValueError(
                f"capability name mismatch: key='{capability_name}' payload='{spec.name}'"
            )

        canonical_spec = spec.model_copy(update={"name": canonical_key})
        existing = normalized.get(canonical_key)
        if existing is not None:
            if existing.model_dump() != canonical_spec.model_dump():
                raise ValueError(
                    f"conflicting capability alias contract for '{canonical_key}'"
                )
            continue

        normalized[canonical_key] = canonical_spec
    return normalized


def _canonical_capability_name(name: str) -> str:
    return _CAPABILITY_ALIASES.get(name, name)


__all__ = [
    "ADAPTER_FACING_CAPABILITIES",
    "default_capability_registry",
    "validate_capability_registry",
]
