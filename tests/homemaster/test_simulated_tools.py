"""Tests for simulated tool boundary — Phase 6."""

from __future__ import annotations

from homemaster.tools.simulated import (
    SIMULATED_TOOL_MAKERS,
    make_manipulate_spec,
    make_navigate_spec,
    make_observe_spec,
    make_verify_spec,
)


class TestSimulatedToolSpecs:
    """Verify simulated tool specs meet Phase 6 acceptance criteria."""

    def test_navigate_spec_executor_mode(self):
        spec = make_navigate_spec()
        assert spec.executor_mode == "simulated_skill"
        assert spec.selectable_by_model is True
        assert spec.requires_verification is True

    def test_observe_spec_executor_mode(self):
        spec = make_observe_spec()
        assert spec.executor_mode == "simulated_skill"
        assert spec.selectable_by_model is True
        assert spec.requires_verification is True

    def test_manipulate_spec_executor_mode(self):
        spec = make_manipulate_spec()
        assert spec.executor_mode == "simulated_skill"
        assert spec.selectable_by_model is True
        assert spec.requires_verification is True

    def test_verify_spec_executor_mode(self):
        spec = make_verify_spec()
        assert spec.executor_mode == "simulated_verification"
        assert spec.selectable_by_model is True
        assert spec.requires_verification is False  # intentional: verify IS the verification

    def test_navigate_description_contains_simulated_marker(self):
        desc = make_navigate_spec().description
        assert "simulated" in desc.lower()
        assert "current_location" in desc

    def test_observe_description_contains_negative_evidence(self):
        desc = make_observe_spec().description
        assert "negative evidence" in desc.lower()

    def test_manipulate_description_contains_failure_reason(self):
        desc = make_manipulate_spec().description
        assert "failure_reason" in desc.lower()

    def test_verify_description_contains_simulated_verification(self):
        desc = make_verify_spec().description
        assert "simulated verification" in desc.lower()

    def test_simulated_tool_makers_count(self):
        assert len(SIMULATED_TOOL_MAKERS) == 4

    def test_all_specs_have_state_effects(self):
        for maker in SIMULATED_TOOL_MAKERS:
            spec = maker()
            assert len(spec.state_effects) > 0, f"{spec.name} has no state_effects"

    def test_mimo_manifest_excludes_executor(self):
        for maker in SIMULATED_TOOL_MAKERS:
            spec = maker()
            manifest = spec.to_mimo_manifest()
            assert "executor" not in manifest, f"{spec.name} leaks executor in manifest"

    def test_mimo_manifest_excludes_state_effects(self):
        for maker in SIMULATED_TOOL_MAKERS:
            spec = maker()
            manifest = spec.to_mimo_manifest()
            assert "state_effects" not in manifest, f"{spec.name} leaks state_effects"

    def test_mimo_manifest_excludes_failure_semantics(self):
        for maker in SIMULATED_TOOL_MAKERS:
            spec = maker()
            manifest = spec.to_mimo_manifest()
            assert "failure_semantics" not in manifest
