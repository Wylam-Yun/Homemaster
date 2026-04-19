from __future__ import annotations

import pytest
from pydantic import ValidationError

from task_brain.capabilities import (
    ADAPTER_FACING_CAPABILITIES,
    default_capability_registry,
    validate_capability_registry,
)
from task_brain.domain import CapabilitySpec


def test_default_registry_contains_required_capabilities() -> None:
    registry = default_capability_registry()
    required = {
        "mock_vln.navigate",
        "mock_perception.observe",
        "robobrain.plan",
        "mock_atomic_executor.execute",
        "verification.evaluate",
        "recovery.analyze_failure",
        "recovery.decide",
        "memory.reconcile",
    }
    assert required.issubset(set(registry.keys()))


def test_each_capability_has_full_skill_compatible_contract() -> None:
    registry = default_capability_registry()
    for name, spec in registry.items():
        assert isinstance(spec, CapabilitySpec)
        assert spec.name == name
        assert isinstance(spec.input_schema, dict)
        assert isinstance(spec.output_schema, dict)
        assert len(spec.failure_modes) >= 1
        assert spec.timeout_s > 0
        assert isinstance(spec.returns_evidence, bool)


def test_adapter_facing_capabilities_return_evidence_true() -> None:
    registry = default_capability_registry()
    for capability_name in ADAPTER_FACING_CAPABILITIES:
        assert capability_name in registry
        assert registry[capability_name].returns_evidence is True


def test_registry_validation_rejects_name_mismatch_or_missing_fields() -> None:
    with pytest.raises(ValueError, match="capability name mismatch"):
        validate_capability_registry(
            {
                "mock_perception.observe": {
                    "name": "mock_perception.WRONG",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "failure_modes": ["timeout"],
                    "timeout_s": 1.0,
                    "returns_evidence": True,
                }
            }
        )

    with pytest.raises(ValidationError):
        validate_capability_registry(
            {
                "mock_perception.observe": {
                    "name": "mock_perception.observe",
                    "input_schema": {"type": "object"},
                    "failure_modes": ["timeout"],
                    "timeout_s": 1.0,
                    "returns_evidence": True,
                }
            }
        )
