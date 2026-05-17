"""Tests for FailureRuleProvider."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.failure_rule_provider import FailureRuleProvider

# ── should_force_no_object ───────────────────────────────────────────────────


def test_force_no_object_matches():
    provider = FailureRuleProvider(
        rules=[{"rule_type": "force_no_object", "enabled": True, "target_category": "cup"}],
        scenario="test",
    )
    assert provider.should_force_no_object(target_category="cup") is True


def test_force_no_object_no_match():
    provider = FailureRuleProvider(
        rules=[{"rule_type": "force_no_object", "enabled": True, "target_category": "cup"}],
        scenario="test",
    )
    assert provider.should_force_no_object(target_category="medicine_box") is False


def test_disabled_rule_ignored():
    provider = FailureRuleProvider(
        rules=[{"rule_type": "force_no_object", "enabled": False, "target_category": "cup"}],
        scenario="test",
    )
    assert provider.should_force_no_object(target_category="cup") is False


def test_empty_rules():
    provider = FailureRuleProvider(rules=[], scenario="test")
    assert provider.should_force_no_object(target_category="cup") is False


def test_legacy_rule_ignored():
    """Legacy format rules (with 'attempt' key, no 'rule_type') are skipped."""
    provider = FailureRuleProvider(
        rules=[{"attempt": 1, "status": "failed", "reason": "object_slipped"}],
        scenario="test",
    )
    assert provider.should_force_no_object(target_category="cup") is False


def test_force_no_object_no_category_does_not_match():
    """A force_no_object rule without target_category does NOT match (strict)."""
    provider = FailureRuleProvider(
        rules=[{"rule_type": "force_no_object", "enabled": True}],
        scenario="test",
    )
    assert provider.should_force_no_object(target_category="anything") is False


def test_force_no_object_none_target_category():
    """When target_category is None, rule does NOT fire (strict matching)."""
    provider = FailureRuleProvider(
        rules=[{"rule_type": "force_no_object", "enabled": True, "target_category": "cup"}],
        scenario="test",
    )
    assert provider.should_force_no_object(target_category=None) is False


# ── from_scenario ────────────────────────────────────────────────────────────


def test_from_scenario_loads(tmp_path: Path):
    scenario_dir = tmp_path / "test_scenario"
    scenario_dir.mkdir()
    (scenario_dir / "failures.json").write_text(
        json.dumps({
            "failure_rules": [
                {"rule_type": "force_no_object", "enabled": True, "target_category": "cup"},
            ],
        }),
        encoding="utf-8",
    )
    provider = FailureRuleProvider.from_scenario("test_scenario", scenario_dir)
    assert provider.scenario == "test_scenario"
    assert len(provider.rules) == 1
    assert provider.should_force_no_object(target_category="cup") is True


def test_from_scenario_missing_file(tmp_path: Path):
    provider = FailureRuleProvider.from_scenario("no_such_scenario", tmp_path)
    assert provider.rules == []
    assert provider.should_force_no_object(target_category="cup") is False


def test_from_scenario_malformed_json(tmp_path: Path):
    scenario_dir = tmp_path / "bad_scenario"
    scenario_dir.mkdir()
    (scenario_dir / "failures.json").write_text("not json", encoding="utf-8")
    provider = FailureRuleProvider.from_scenario("bad_scenario", scenario_dir)
    assert provider.rules == []


def test_from_scenario_mixed_rules(tmp_path: Path):
    """Legacy and declarative rules coexist; only declarative ones are evaluated."""
    scenario_dir = tmp_path / "mixed"
    scenario_dir.mkdir()
    (scenario_dir / "failures.json").write_text(
        json.dumps({
            "failure_rules": [
                {"attempt": 1, "status": "failed", "reason": "object_slipped"},
                {"rule_type": "force_no_object", "enabled": True, "target_category": "cup"},
            ],
        }),
        encoding="utf-8",
    )
    provider = FailureRuleProvider.from_scenario("mixed", scenario_dir)
    assert len(provider.rules) == 2
    assert provider.should_force_no_object(target_category="cup") is True
