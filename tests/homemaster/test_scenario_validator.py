"""Tests for the scenario & memory validator."""

from __future__ import annotations

import json

from homemaster.contracts import ScenarioCatalogEntry, ScenarioManifest
from homemaster.scenario_validator import (
    ValidationResult,
    validate_all,
    validate_corpus,
    validate_failures,
    validate_home_world,
    validate_materialization,
    validate_memory_world_bridge,
    validate_scenario_memory,
    validate_scenario_metadata,
    validate_scenario_world,
    validate_target_coverage,
    validate_world_overlay,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _minimal_world() -> dict:
    """Minimal valid scenario world.json."""
    return {
        "rooms": [{"room_id": "kitchen", "display_text": "厨房"}],
        "viewpoints": {
            "kitchen_table_viewpoint": {
                "room_id": "kitchen",
                "visible_object_ids": ["obj_cup_1"],
                "visible_anchor_ids": ["anchor_kitchen_table_1"],
            },
        },
        "furniture": [
            {
                "anchor_id": "anchor_kitchen_table_1",
                "room_id": "kitchen",
                "anchor_type": "table",
                "viewpoint_id": "kitchen_table_viewpoint",
                "display_text": "厨房餐桌",
            },
        ],
        "objects": [
            {
                "object_id": "obj_cup_1",
                "category": "cup",
                "aliases": ["水杯", "杯子"],
                "memory_id": "mem-cup-1",
                "detector_id": "det-cup-1",
                "confidence_level": "high",
                "state_summary": "upright",
                "spatial_relation": "on_table",
            },
        ],
        "visibility": {
            "kitchen_table_viewpoint": {
                "objects": ["obj_cup_1"],
                "anchors": ["anchor_kitchen_table_1"],
                "scene_relations": [],
            },
        },
        "symbolic_predicates": [],
    }


def _minimal_memory() -> dict:
    """Minimal valid scenario memory.json."""
    return {
        "object_memory": [
            {
                "memory_id": "mem-cup-1",
                "object_category": "cup",
                "aliases": ["水杯", "杯子"],
                "anchor": {
                    "room_id": "kitchen",
                    "anchor_id": "anchor_kitchen_table_1",
                    "anchor_type": "table",
                    "viewpoint_id": "kitchen_table_viewpoint",
                    "display_text": "厨房餐桌",
                },
                "evidence_source": "direct_observation",
                "confidence_level": "high",
                "last_confirmed_at": "2026-04-19T09:00:00Z",
                "belief_state": "confirmed",
            },
        ],
    }


def _minimal_corpus() -> dict:
    """Minimal valid corpus."""
    return {
        "object_memory": [
            {
                "memory_id": "mem-cup-1",
                "object_category": "cup",
                "aliases": ["水杯", "杯子"],
                "anchor": {
                    "room_id": "kitchen",
                    "anchor_id": "anchor_kitchen_table_1",
                    "anchor_type": "table",
                    "viewpoint_id": "anchor_kitchen_table_1_vp",
                    "display_text": "厨房餐桌",
                },
            },
        ],
    }


def _minimal_home_world() -> dict:
    """Minimal valid HomeWorld with matching IDs to minimal corpus."""
    return {
        "rooms": [{"room_id": "kitchen", "display_text": "厨房"}],
        "viewpoints": {
            "anchor_kitchen_table_1_vp": {
                "room_id": "kitchen",
                "visible_object_ids": ["obj_cup_1"],
                "visible_anchor_ids": ["anchor_kitchen_table_1"],
            },
        },
        "furniture": [
            {
                "anchor_id": "anchor_kitchen_table_1",
                "room_id": "kitchen",
                "anchor_type": "table",
                "viewpoint_id": "anchor_kitchen_table_1_vp",
                "display_text": "厨房餐桌",
            },
        ],
        "objects": [
            {
                "object_id": "obj_cup_1",
                "category": "cup",
                "aliases": ["水杯", "杯子"],
                "memory_id": "mem-cup-1",
                "detector_id": "det-cup-1",
                "confidence_level": "high",
                "state_summary": "upright",
                "spatial_relation": "on_table",
            },
        ],
        "visibility": {
            "anchor_kitchen_table_1_vp": {
                "objects": ["obj_cup_1"],
                "anchors": ["anchor_kitchen_table_1"],
                "scene_relations": [],
            },
        },
        "symbolic_predicates": [],
    }


# ── Happy-path tests ─────────────────────────────────────────────────────────


def test_validate_home_world_passes():
    hw = _minimal_home_world()
    corpus = _minimal_corpus()
    issues = validate_home_world(hw, corpus)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_corpus_passes():
    hw = _minimal_home_world()
    corpus = _minimal_corpus()
    issues = validate_corpus(corpus, hw)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_scenario_world_passes():
    world = _minimal_world()
    issues = validate_scenario_world("test_scenario", world)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_scenario_memory_passes():
    world = _minimal_world()
    memory = _minimal_memory()
    issues = validate_scenario_memory("test_scenario", memory, world)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_memory_world_bridge_passes():
    world = _minimal_world()
    memory = _minimal_memory()
    issues = validate_memory_world_bridge("test_scenario", world, memory)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_scenario_metadata_passes():
    manifest = ScenarioManifest(
        name="test_scenario",
        home_id="elder_home_v1",
        utterance="去找水杯",
        expected_final_status="completed",
        tags=["cup"],
    )
    catalog_entry = ScenarioCatalogEntry(
        name="test_scenario",
        utterance="去找水杯",
        expected_final_status="completed",
        tags=["cup"],
    )
    issues = validate_scenario_metadata("test_scenario", manifest, catalog_entry)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_failures_legacy_passes():
    failures = {
        "failure_rules": [
            {
                "attempt": 1,
                "status": "failed",
                "reason": "object_slipped",
                "state_delta": {},
                "runtime_object_updates_candidate": [],
            },
        ],
    }
    issues = validate_failures("test_scenario", failures)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_failures_declarative_passes():
    failures = {
        "failure_rules": [
            {
                "rule_type": "force_no_object",
                "enabled": True,
                "target_category": "cup",
            },
        ],
    }
    issues = validate_failures("test_scenario", failures)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_all_runs_without_crash():
    """validate_all() completes without exception on real data."""
    result = validate_all()
    assert isinstance(result, ValidationResult)


def test_validate_target_coverage_passes():
    entry = ScenarioCatalogEntry(
        name="test_scenario",
        utterance="去厨房找水杯",
        expected_final_status="completed",
        tags=["fetch_object", "cup"],
    )
    memory = _minimal_memory()
    world = _minimal_world()
    issues = validate_target_coverage("test_scenario", entry, memory, world)
    assert issues == [], f"Unexpected issues: {issues}"


def test_validate_world_overlay_passes():
    hw = _minimal_home_world()
    overlay = {
        "present_object_ids": ["obj_cup_1"],
        "active_viewpoint_ids": ["anchor_kitchen_table_1_vp"],
        "visibility_overrides": {},
        "scene_relations": [],
    }
    issues = validate_world_overlay("test_scenario", overlay, hw)
    assert issues == [], f"Unexpected issues: {issues}"


# ── Fault injection tests ────────────────────────────────────────────────────


def test_fail_on_duplicate_corpus_memory_id():
    corpus = _minimal_corpus()
    corpus["object_memory"].append(dict(corpus["object_memory"][0]))  # duplicate
    hw = _minimal_home_world()
    issues = validate_corpus(corpus, hw)
    error_types = [i.error_type for i in issues]
    assert "duplicate_corpus_memory_id" in error_types


def test_fail_on_home_world_memory_id_missing():
    hw = _minimal_home_world()
    hw["objects"][0]["memory_id"] = "mem-nonexistent"
    corpus = _minimal_corpus()
    issues = validate_home_world(hw, corpus)
    error_types = [i.error_type for i in issues]
    assert "home_world_memory_id_not_in_corpus" in error_types


def test_fail_on_missing_anchor_room():
    memory = _minimal_memory()
    memory["object_memory"][0]["anchor"]["room_id"] = "nonexistent_room"
    world = _minimal_world()
    issues = validate_scenario_memory("test_scenario", memory, world)
    error_types = [i.error_type for i in issues]
    assert "memory_anchor_room_not_found" in error_types


def test_fail_on_missing_anchor():
    memory = _minimal_memory()
    memory["object_memory"][0]["anchor"]["anchor_id"] = "nonexistent_anchor"
    world = _minimal_world()
    issues = validate_scenario_memory("test_scenario", memory, world)
    error_types = [i.error_type for i in issues]
    assert "memory_anchor_not_found" in error_types


def test_fail_on_missing_viewpoint():
    memory = _minimal_memory()
    memory["object_memory"][0]["anchor"]["viewpoint_id"] = "nonexistent_vp"
    world = _minimal_world()
    issues = validate_scenario_memory("test_scenario", memory, world)
    error_types = [i.error_type for i in issues]
    assert "memory_viewpoint_not_found" in error_types


def test_fail_on_anchor_viewpoint_mismatch():
    world = _minimal_world()
    # Add a second furniture with different viewpoint
    world["viewpoints"]["other_viewpoint"] = {
        "room_id": "kitchen",
        "visible_object_ids": [],
        "visible_anchor_ids": [],
    }
    # Change furniture viewpoint but keep memory pointing to original
    world["furniture"][0]["viewpoint_id"] = "other_viewpoint"

    memory = _minimal_memory()
    issues = validate_scenario_memory("test_scenario", memory, world)
    error_types = [i.error_type for i in issues]
    assert "anchor_viewpoint_mismatch" in error_types


def test_fail_on_invalid_confidence():
    memory = _minimal_memory()
    memory["object_memory"][0]["confidence_level"] = "invalid"
    world = _minimal_world()
    issues = validate_scenario_memory("test_scenario", memory, world)
    error_types = [i.error_type for i in issues]
    assert "invalid_confidence_level" in error_types


def test_fail_on_world_memory_id_missing():
    """World object of target category has memory_id not in memory."""
    world = _minimal_world()
    world["objects"][0]["memory_id"] = "mem-different"
    memory = _minimal_memory()
    issues = validate_memory_world_bridge("test_scenario", world, memory)
    error_types = [i.error_type for i in issues]
    assert "world_memory_id_not_found" in error_types


def test_fail_on_metadata_name_mismatch():
    manifest = ScenarioManifest(
        name="wrong_name",
        home_id="elder_home_v1",
        utterance="去找水杯",
        expected_final_status="completed",
    )
    catalog_entry = ScenarioCatalogEntry(
        name="test_scenario",
        utterance="去找水杯",
        expected_final_status="completed",
    )
    issues = validate_scenario_metadata("test_scenario", manifest, catalog_entry)
    error_types = [i.error_type for i in issues]
    assert "metadata_name_mismatch" in error_types


def test_fail_on_unknown_failure_rule_type():
    failures = {
        "failure_rules": [
            {"rule_type": "unknown_type", "enabled": True},
        ],
    }
    issues = validate_failures("test_scenario", failures)
    error_types = [i.error_type for i in issues]
    assert "unknown_failure_rule_type" in error_types


def test_fail_on_overlay_object_not_in_home():
    hw = _minimal_home_world()
    overlay = {
        "present_object_ids": ["obj_nonexistent"],
        "active_viewpoint_ids": [],
        "visibility_overrides": {},
        "scene_relations": [],
    }
    issues = validate_world_overlay("test_scenario", overlay, hw)
    error_types = [i.error_type for i in issues]
    assert "overlay_object_not_found" in error_types


def test_fail_on_target_coverage_missing():
    entry = ScenarioCatalogEntry(
        name="test_scenario",
        utterance="去厨房找遥控器",  # says 遥控器, not 水杯
        expected_final_status="completed",
        tags=["fetch_object", "cup"],  # tag says cup
    )
    memory = _minimal_memory()
    world = _minimal_world()
    issues = validate_target_coverage("test_scenario", entry, memory, world)
    error_types = [i.error_type for i in issues]
    assert "target_coverage_missing" in error_types


# ── Materialization test ─────────────────────────────────────────────────────


def test_materialization_passes():
    from homemaster.contracts import MemoryProfile

    corpus = {
        "object_memory": [
            {"memory_id": "mem-1", "object_category": "cup"},
            {"memory_id": "mem-2", "object_category": "cup"},
        ],
    }
    profile = MemoryProfile(full_corpus=True)
    issues = validate_materialization("test_scenario", corpus, profile)
    assert issues == [], f"Unexpected issues: {issues}"


# ── New tests for P-1D.0 fixes ───────────────────────────────────────────────


def test_fail_on_required_file_missing(tmp_path):
    """Active scenario missing world.json/memory.json/etc produces issues."""
    from homemaster.scenario_validator import validate_all as _validate_all

    # Create a minimal catalog with one active scenario and no files
    scenario_dir = tmp_path / "empty_scenario"
    scenario_dir.mkdir()

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({
        "scenarios": [{
            "name": "empty_scenario",
            "utterance": "test",
            "expected_final_status": "completed",
            "status": "active",
            "data_source": "legacy_files",
        }],
    }), encoding="utf-8")

    # Need minimal corpus + world for global validation
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({"object_memory": []}), encoding="utf-8")
    world_path = tmp_path / "world.json"
    world_path.write_text(json.dumps({
        "rooms": [], "viewpoints": {}, "furniture": [],
        "objects": [], "visibility": {},
    }), encoding="utf-8")

    result = _validate_all(
        corpus_path=corpus_path,
        catalog_path=catalog_path,
        scenarios_root=tmp_path,
        world_path=world_path,
    )
    missing = [i for i in result.issues if i.error_type == "required_file_missing"]
    missing_names = [i.message for i in missing]
    assert len(missing) >= 4, f"Expected at least 4 missing file issues, got: {missing_names}"
    assert any("world.json" in m for m in missing_names)
    assert any("memory.json" in m for m in missing_names)
    assert any("failures.json" in m for m in missing_names)
    assert any("scenario.json" in m for m in missing_names)


def test_fail_on_overlay_internal_object_not_in_home():
    """Overlay visibility_overrides.vp.objects referencing unknown object."""
    hw = _minimal_home_world()
    overlay = {
        "present_object_ids": [],
        "active_viewpoint_ids": [],
        "visibility_overrides": {
            "anchor_kitchen_table_1_vp": {
                "objects": ["obj_nonexistent"],
                "anchors": [],
                "scene_relations": [],
            },
        },
    }
    issues = validate_world_overlay("test_scenario", overlay, hw)
    error_types = [i.error_type for i in issues]
    assert "overlay_object_not_found" in error_types
    assert any("anchor_kitchen_table_1_vp" in i.message for i in issues)


def test_fail_on_overlay_internal_scene_relation_not_in_home():
    """Overlay visibility_overrides.vp.scene_relations referencing unknown object."""
    hw = _minimal_home_world()
    overlay = {
        "present_object_ids": [],
        "active_viewpoint_ids": [],
        "visibility_overrides": {
            "anchor_kitchen_table_1_vp": {
                "objects": [],
                "anchors": [],
                "scene_relations": [
                    {"subject_object_id": "obj_cup_1", "target_object_id": "obj_missing"},
                ],
            },
        },
    }
    issues = validate_world_overlay("test_scenario", overlay, hw)
    error_types = [i.error_type for i in issues]
    assert "overlay_scene_relation_ref_not_found" in error_types


def test_fail_on_declarative_rule_missing_target_category():
    """force_no_object without target_category produces issue."""
    failures = {
        "failure_rules": [
            {"rule_type": "force_no_object", "enabled": True},
        ],
    }
    issues = validate_failures("test_scenario", failures)
    error_types = [i.error_type for i in issues]
    assert "invalid_failure_rule" in error_types
    assert any("target_category" in i.message for i in issues)


def test_schema_error_becomes_issue_not_crash(tmp_path):
    """Invalid catalog entry becomes ScenarioValidationIssue, not an exception."""
    from homemaster.scenario_validator import _safe_load_catalog

    bad_catalog = tmp_path / "catalog.json"
    bad_catalog.write_text(json.dumps({
        "scenarios": [{
            "name": "bad",
            "utterance": "test",
            "expected_final_status": "done",  # invalid value
        }],
    }), encoding="utf-8")

    entries, issues = _safe_load_catalog(bad_catalog)
    assert entries == []
    assert len(issues) == 1
    assert issues[0].error_type == "catalog_parse_error"


def test_validate_failures_force_no_object_all_candidates_flagged():
    """force_no_object_all_candidates is not in P-1E minimal rules — flagged as unknown."""
    failures = {
        "failure_rules": [
            {
                "rule_type": "force_no_object_all_candidates",
                "enabled": True,
                "target_category": "cup",
            },
        ],
    }
    issues = validate_failures("test_scenario", failures)
    error_types = [i.error_type for i in issues]
    assert "unknown_failure_rule_type" in error_types
