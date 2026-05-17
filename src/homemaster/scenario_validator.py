"""Dev/CI validator for scenario data integrity.

Checks cross-file references, schema compliance, and coverage across
catalog.json, per-scenario scenario.json / memory.json / world.json /
failures.json / memory_profile.json / world_overlay.json, and the
object memory corpus.

Does NOT enter the business pipeline. Does NOT call LLM or embedding.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from homemaster.contracts import ScenarioCatalogEntry, ScenarioManifest
from homemaster.memory_profile import materialize_memory
from homemaster.runtime import REPO_ROOT
from homemaster.scenario_catalog import (
    load_catalog,
    load_memory_profile,
    load_scenario_manifest,
)

# ── Paths ────────────────────────────────────────────────────────────────────

CORPUS_PATH = REPO_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"
WORLD_PATH = REPO_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"
CATALOG_PATH = REPO_ROOT / "data" / "scenarios" / "catalog.json"
SCENARIOS_ROOT = REPO_ROOT / "data" / "scenarios"

# ── Known object categories (union of world + corpus) ────────────────────────

KNOWN_OBJECT_CATEGORIES: set[str] = {
    "cup", "medicine_box", "remote_control", "glasses", "keys",
    "tissue", "water_bottle", "book", "phone", "fruit_bowl",
    "bowl", "mug", "plate", "umbrella", "bag",
}

VALID_CONFIDENCE_LEVELS: set[str] = {"high", "medium", "low"}
VALID_BELIEF_STATES: set[str] = {"confirmed", "stale"}
VALID_EVIDENCE_SOURCES: set[str] = {"direct_observation", "inferred_experience"}
VALID_EXPECTED_FINAL_STATUSES: set[str] = {"completed", "failed", "needs_user"}

# Declarative failure rule types (P-1E direction)
DECLARATIVE_RULE_TYPES: set[str] = {"force_no_object"}

# Per-type required fields for declarative rules
DECLARATIVE_REQUIRED_FIELDS: dict[str, set[str]] = {
    "force_no_object": {"enabled", "target_category"},
}

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioValidationIssue:
    """One validation failure with categorized type and location."""

    error_type: str
    scope: str  # "__global__" or scenario name
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Aggregated validation result."""

    issues: list[ScenarioValidationIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _issue(error_type: str, scope: str, message: str) -> ScenarioValidationIssue:
    return ScenarioValidationIssue(error_type=error_type, scope=scope, message=message)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_load_catalog(
    path: Path,
) -> tuple[list[ScenarioCatalogEntry], list[ScenarioValidationIssue]]:
    """Load catalog, converting parse/schema errors into issues."""
    try:
        entries = load_catalog(path)
        return entries, []
    except (PydanticValidationError, json.JSONDecodeError, ValueError) as exc:
        return [], [_issue("catalog_parse_error", "__global__", f"catalog.json: {exc}")]


def _safe_load_manifest(name: str) -> tuple[ScenarioManifest | None, list[ScenarioValidationIssue]]:
    """Load scenario.json, converting parse/schema errors into issues."""
    try:
        manifest = load_scenario_manifest(name)
        return manifest, []
    except (PydanticValidationError, json.JSONDecodeError, ValueError) as exc:
        return None, [_issue("manifest_parse_error", name, f"scenario.json: {exc}")]


def _safe_load_profile(name: str) -> tuple[Any | None, list[ScenarioValidationIssue]]:
    """Load memory_profile.json, converting parse/schema errors into issues."""
    try:
        profile = load_memory_profile(name)
        return profile, []
    except (PydanticValidationError, json.JSONDecodeError, ValueError) as exc:
        return None, [_issue("profile_parse_error", name, f"memory_profile.json: {exc}")]


# ── V0: HomeWorld reference integrity ────────────────────────────────────────


def validate_home_world(
    home_world: dict[str, Any],
    corpus: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Validate global HomeWorld internal consistency + corpus references."""
    issues: list[ScenarioValidationIssue] = []
    scope = "__global__"

    # Rooms
    rooms = home_world.get("rooms", [])
    room_ids = [r["room_id"] for r in rooms]
    if len(room_ids) != len(set(room_ids)):
        dupes = [x for x in room_ids if room_ids.count(x) > 1]
        issues.append(_issue("duplicate_room_id", scope, f"Duplicate room_id: {dupes}"))

    room_id_set = set(room_ids)

    # Furniture
    furniture = home_world.get("furniture", [])
    anchor_ids = [a["anchor_id"] for a in furniture]
    if len(anchor_ids) != len(set(anchor_ids)):
        dupes = [x for x in anchor_ids if anchor_ids.count(x) > 1]
        issues.append(_issue("duplicate_anchor_id", scope, f"Duplicate anchor_id: {dupes}"))

    anchor_id_set = set(anchor_ids)
    for anchor in furniture:
        aid = anchor["anchor_id"]
        if anchor.get("room_id") not in room_id_set:
            issues.append(_issue(
                "anchor_room_not_found", scope,
                f"Anchor {aid!r} references unknown room {anchor.get('room_id')!r}",
            ))
        if anchor.get("viewpoint_id") not in set(home_world.get("viewpoints", {})):
            issues.append(_issue(
                "anchor_viewpoint_not_found", scope,
                f"Anchor {aid!r} references unknown viewpoint {anchor.get('viewpoint_id')!r}",
            ))

    # Viewpoints
    viewpoints = home_world.get("viewpoints", {})
    vp_ids = set(viewpoints.keys())
    for vp_id, vp in viewpoints.items():
        if vp.get("room_id") not in room_id_set:
            issues.append(_issue(
                "viewpoint_room_not_found", scope,
                f"Viewpoint {vp_id!r} references unknown room {vp.get('room_id')!r}",
            ))

    # Objects
    objects = home_world.get("objects", [])
    object_ids = [o["object_id"] for o in objects]
    if len(object_ids) != len(set(object_ids)):
        dupes = [x for x in object_ids if object_ids.count(x) > 1]
        issues.append(_issue("duplicate_object_id", scope, f"Duplicate object_id: {dupes}"))

    object_id_set = set(object_ids)
    corpus_memory_ids = {
        e["memory_id"]
        for e in corpus.get("object_memory", [])
        if isinstance(e, dict) and "memory_id" in e
    }

    for obj in objects:
        oid = obj["object_id"]
        for required in ("category", "aliases", "memory_id"):
            if required not in obj:
                issues.append(_issue(
                    "object_missing_field", scope,
                    f"Object {oid!r} missing required field {required!r}",
                ))
        mid = obj.get("memory_id")
        if mid and mid not in corpus_memory_ids:
            issues.append(_issue(
                "home_world_memory_id_not_in_corpus", scope,
                f"Object {oid!r} memory_id {mid!r} not found in corpus",
            ))

    # Visibility
    visibility = home_world.get("visibility", {})
    if set(visibility.keys()) != vp_ids:
        extra = set(visibility.keys()) - vp_ids
        missing = vp_ids - set(visibility.keys())
        issues.append(_issue(
            "visibility_keys_mismatch", scope,
            f"Visibility keys mismatch: extra={extra}, missing={missing}",
        ))

    for vp_id, vis in visibility.items():
        for obj_id in vis.get("objects", []):
            if obj_id not in object_id_set:
                issues.append(_issue(
                    "visibility_object_not_found", scope,
                    f"Visibility {vp_id!r} references unknown object {obj_id!r}",
                ))
        for anchor_id in vis.get("anchors", []):
            if anchor_id not in anchor_id_set:
                issues.append(_issue(
                    "visibility_anchor_not_found", scope,
                    f"Visibility {vp_id!r} references unknown anchor {anchor_id!r}",
                ))
        all_ids = object_id_set | anchor_id_set
        for rel in vis.get("scene_relations", []):
            for key in ("subject_object_id", "target_object_id"):
                if rel.get(key) not in all_ids:
                    issues.append(_issue(
                        "scene_relation_ref_not_found", scope,
                        f"scene_relation in {vp_id!r} references unknown {key} {rel.get(key)!r}",
                    ))

    return issues


# ── V1: Corpus validation ────────────────────────────────────────────────────


def validate_corpus(
    corpus: dict[str, Any],
    home_world: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Validate corpus memory_id uniqueness + anchor references against HomeWorld."""
    issues: list[ScenarioValidationIssue] = []
    scope = "__global__"

    entries = corpus.get("object_memory", [])
    memory_ids = [e.get("memory_id") for e in entries if isinstance(e, dict)]
    if len(memory_ids) != len(set(memory_ids)):
        dupes = [x for x in memory_ids if memory_ids.count(x) > 1]
        issues.append(_issue("duplicate_corpus_memory_id", scope, f"Duplicate memory_id: {dupes}"))

    # Check anchor references against HomeWorld
    hw_room_ids = {r["room_id"] for r in home_world.get("rooms", [])}
    hw_anchor_ids = {a["anchor_id"] for a in home_world.get("furniture", [])}
    hw_vp_ids = set(home_world.get("viewpoints", {}).keys())

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("memory_id", "?")
        anchor = entry.get("anchor", {})
        if anchor.get("room_id") not in hw_room_ids:
            issues.append(_issue(
                "corpus_anchor_room_not_in_homeworld", scope,
                f"Corpus {mid}: anchor.room_id {anchor.get('room_id')!r} not in HomeWorld",
            ))
        if anchor.get("anchor_id") not in hw_anchor_ids:
            issues.append(_issue(
                "corpus_anchor_not_in_homeworld", scope,
                f"Corpus {mid}: anchor.anchor_id {anchor.get('anchor_id')!r} not in HomeWorld",
            ))
        if anchor.get("viewpoint_id") not in hw_vp_ids:
            issues.append(_issue(
                "corpus_viewpoint_not_in_homeworld", scope,
                f"Corpus {mid}: anchor.viewpoint_id "
                f"{anchor.get('viewpoint_id')!r} not in HomeWorld",
            ))

    return issues


# ── V2: Scenario world.json ──────────────────────────────────────────────────


def validate_scenario_world(
    scenario_name: str,
    world: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Validate per-scenario world.json internal consistency."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    rooms = world.get("rooms", [])
    room_ids = {r["room_id"] for r in rooms}

    viewpoints = world.get("viewpoints", {})
    vp_ids = set(viewpoints.keys())

    for vp_id, vp in viewpoints.items():
        if vp.get("room_id") not in room_ids:
            issues.append(_issue(
                "scenario_viewpoint_room_not_found", scope,
                f"Viewpoint {vp_id!r} references unknown room {vp.get('room_id')!r}",
            ))

    furniture = world.get("furniture", [])
    anchor_ids = [a["anchor_id"] for a in furniture]
    if len(anchor_ids) != len(set(anchor_ids)):
        dupes = [x for x in anchor_ids if anchor_ids.count(x) > 1]
        issues.append(_issue(
            "scenario_duplicate_anchor_id", scope,
            f"Duplicate anchor_id in scenario world: {dupes}",
        ))

    for anchor in furniture:
        aid = anchor["anchor_id"]
        if anchor.get("room_id") not in room_ids:
            issues.append(_issue(
                "scenario_anchor_room_not_found", scope,
                f"Anchor {aid!r} references unknown room {anchor.get('room_id')!r}",
            ))
        if anchor.get("viewpoint_id") not in vp_ids:
            issues.append(_issue(
                "scenario_anchor_viewpoint_not_found", scope,
                f"Anchor {aid!r} references unknown viewpoint {anchor.get('viewpoint_id')!r}",
            ))

    objects = world.get("objects", [])
    object_ids = [o["object_id"] for o in objects]
    if len(object_ids) != len(set(object_ids)):
        dupes = [x for x in object_ids if object_ids.count(x) > 1]
        issues.append(_issue(
            "scenario_duplicate_object_id", scope,
            f"Duplicate object_id in scenario world: {dupes}",
        ))

    for obj in objects:
        if not obj.get("memory_id"):
            issues.append(_issue(
                "scenario_object_empty_memory_id", scope,
                f"Object {obj.get('object_id')!r} has empty memory_id",
            ))

    return issues


# ── V3: Scenario memory.json ─────────────────────────────────────────────────


def validate_scenario_memory(
    scenario_name: str,
    memory: dict[str, Any],
    world: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Validate per-scenario memory.json against its world.json."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    entries = memory.get("object_memory", [])
    memory_ids = [e.get("memory_id") for e in entries if isinstance(e, dict)]
    if len(memory_ids) != len(set(memory_ids)):
        dupes = [x for x in memory_ids if memory_ids.count(x) > 1]
        issues.append(_issue(
            "scenario_duplicate_memory_id", scope,
            f"Duplicate memory_id in scenario memory: {dupes}",
        ))

    room_ids = {r["room_id"] for r in world.get("rooms", [])}
    furniture_by_id = {a["anchor_id"]: a for a in world.get("furniture", [])}
    vp_ids = set(world.get("viewpoints", {}).keys())

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("memory_id", "?")
        anchor = entry.get("anchor", {})

        if anchor.get("room_id") not in room_ids:
            issues.append(_issue(
                "memory_anchor_room_not_found", scope,
                f"Memory {mid}: anchor.room_id {anchor.get('room_id')!r} not in scenario world",
            ))

        anchor_id = anchor.get("anchor_id")
        if anchor_id not in furniture_by_id:
            issues.append(_issue(
                "memory_anchor_not_found", scope,
                f"Memory {mid}: anchor.anchor_id {anchor_id!r} not in scenario world furniture",
            ))
        else:
            furniture_vp = furniture_by_id[anchor_id].get("viewpoint_id")
            if anchor.get("viewpoint_id") != furniture_vp:
                issues.append(_issue(
                    "anchor_viewpoint_mismatch", scope,
                    f"Memory {mid}: anchor.viewpoint_id {anchor.get('viewpoint_id')!r} "
                    f"!= furniture viewpoint_id {furniture_vp!r}",
                ))

        if anchor.get("viewpoint_id") not in vp_ids:
            issues.append(_issue(
                "memory_viewpoint_not_found", scope,
                f"Memory {mid}: anchor.viewpoint_id "
                f"{anchor.get('viewpoint_id')!r} not in scenario world",
            ))

        cl = entry.get("confidence_level")
        if cl not in VALID_CONFIDENCE_LEVELS:
            issues.append(_issue(
                "invalid_confidence_level", scope,
                f"Memory {mid}: confidence_level {cl!r} not in {VALID_CONFIDENCE_LEVELS}",
            ))

        bs = entry.get("belief_state")
        if bs not in VALID_BELIEF_STATES:
            issues.append(_issue(
                "invalid_belief_state", scope,
                f"Memory {mid}: belief_state {bs!r} not in {VALID_BELIEF_STATES}",
            ))

        es = entry.get("evidence_source")
        if es not in VALID_EVIDENCE_SOURCES:
            issues.append(_issue(
                "invalid_evidence_source", scope,
                f"Memory {mid}: evidence_source {es!r} not in {VALID_EVIDENCE_SOURCES}",
            ))

    return issues


# ── V4: Memory-world bridge ──────────────────────────────────────────────────


def validate_memory_world_bridge(
    scenario_name: str,
    world: dict[str, Any],
    memory: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Check that world objects of target categories have matching memory entries."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    memory_entries = memory.get("object_memory", [])
    target_categories = {
        e.get("object_category")
        for e in memory_entries
        if isinstance(e, dict) and e.get("object_category")
    }
    memory_ids = {
        e.get("memory_id")
        for e in memory_entries
        if isinstance(e, dict) and e.get("memory_id")
    }

    for obj in world.get("objects", []):
        category = obj.get("category")
        if category not in target_categories:
            continue  # skip non-target categories
        mid = obj.get("memory_id")
        if mid not in memory_ids:
            issues.append(_issue(
                "world_memory_id_not_found", scope,
                f"World object {obj.get('object_id')!r} (category={category}) "
                f"memory_id {mid!r} not found in scenario memory",
            ))

    return issues


# ── V5: Scenario metadata ────────────────────────────────────────────────────


def validate_scenario_metadata(
    scenario_name: str,
    manifest: ScenarioManifest,
    catalog_entry: ScenarioCatalogEntry,
) -> list[ScenarioValidationIssue]:
    """Validate scenario.json metadata against catalog entry."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    if manifest.name != scenario_name:
        issues.append(_issue(
            "metadata_name_mismatch", scope,
            f"scenario.json name {manifest.name!r} != directory name {scenario_name!r}",
        ))

    if manifest.expected_final_status not in VALID_EXPECTED_FINAL_STATUSES:
        issues.append(_issue(
            "invalid_expected_final_status", scope,
            f"expected_final_status {manifest.expected_final_status!r} "
            f"not in {VALID_EXPECTED_FINAL_STATUSES}",
        ))

    if manifest.name != catalog_entry.name:
        issues.append(_issue(
            "catalog_manifest_name_mismatch", scope,
            f"scenario.json name {manifest.name!r} != catalog name {catalog_entry.name!r}",
        ))

    if manifest.utterance != catalog_entry.utterance:
        issues.append(_issue(
            "catalog_manifest_utterance_mismatch", scope,
            "scenario.json utterance != catalog utterance",
        ))

    if manifest.expected_final_status != catalog_entry.expected_final_status:
        issues.append(_issue(
            "catalog_manifest_status_mismatch", scope,
            "scenario.json expected_final_status != catalog expected_final_status",
        ))

    return issues


# ── V6: Target coverage ─────────────────────────────────────────────────────


def validate_target_coverage(
    scenario_name: str,
    catalog_entry: ScenarioCatalogEntry,
    memory: dict[str, Any],
    world: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Check utterance target is coverable by memory/world aliases."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    # Find object category tags
    category_tags = [
        t for t in catalog_entry.tags
        if t in KNOWN_OBJECT_CATEGORIES
    ]
    if not category_tags:
        return issues  # no object category tag found, skip

    utterance = catalog_entry.utterance

    # Collect aliases for the target categories from memory + world
    target_aliases: set[str] = set()
    for cat in category_tags:
        # From memory
        for entry in memory.get("object_memory", []):
            if isinstance(entry, dict) and entry.get("object_category") == cat:
                for alias in entry.get("aliases", []):
                    target_aliases.add(alias)
        # From world
        for obj in world.get("objects", []):
            if obj.get("category") == cat:
                for alias in obj.get("aliases", []):
                    target_aliases.add(alias)

    if not target_aliases:
        issues.append(_issue(
            "target_no_aliases_found", scope,
            f"Category tags {category_tags} found but no aliases in memory or world",
        ))
        return issues

    # Check utterance contains at least one alias substring
    if not any(alias in utterance for alias in target_aliases):
        issues.append(_issue(
            "target_coverage_missing", scope,
            f"Utterance {utterance!r} does not contain any alias for categories "
            f"{category_tags}: {sorted(target_aliases)}",
        ))

    return issues


# ── V7: Failures ─────────────────────────────────────────────────────────────


def validate_failures(
    scenario_name: str,
    failures: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Validate failures.json supports both legacy and declarative formats."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    rules = failures.get("failure_rules")
    if not isinstance(rules, list):
        issues.append(_issue(
            "failure_rules_not_list", scope,
            f"failure_rules must be a list, got {type(rules).__name__}",
        ))
        return issues

    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            issues.append(_issue(
                "failure_rule_not_dict", scope,
                f"failure_rules[{i}] must be a dict",
            ))
            continue

        # Detect format
        has_attempt = "attempt" in rule
        has_rule_type = "rule_type" in rule

        if has_attempt:
            # Legacy format: attempt/status/reason
            if "status" not in rule:
                issues.append(_issue(
                    "invalid_failure_rule", scope,
                    f"failure_rules[{i}]: legacy rule missing 'status'",
                ))
            if "reason" not in rule:
                issues.append(_issue(
                    "invalid_failure_rule", scope,
                    f"failure_rules[{i}]: legacy rule missing 'reason'",
                ))
        elif has_rule_type:
            # Declarative format: rule_type + per-type required fields
            rt = rule["rule_type"]
            if rt not in DECLARATIVE_RULE_TYPES:
                issues.append(_issue(
                    "unknown_failure_rule_type", scope,
                    f"failure_rules[{i}]: unknown rule_type {rt!r}, "
                    f"expected one of {DECLARATIVE_RULE_TYPES}",
                ))
            else:
                required = DECLARATIVE_REQUIRED_FIELDS.get(rt, set())
                for field_name in required:
                    if field_name not in rule:
                        issues.append(_issue(
                            "invalid_failure_rule", scope,
                            f"failure_rules[{i}]: rule_type {rt!r} "
                            f"missing required field {field_name!r}",
                        ))
        else:
            issues.append(_issue(
                "unknown_failure_rule_type", scope,
                f"failure_rules[{i}]: rule has neither 'attempt' (legacy) "
                f"nor 'rule_type' (declarative)",
            ))

    return issues


# ── V8: World overlay ────────────────────────────────────────────────────────


def validate_world_overlay(
    scenario_name: str,
    overlay: dict[str, Any],
    home_world: dict[str, Any],
) -> list[ScenarioValidationIssue]:
    """Validate world_overlay.json references against HomeWorld."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    hw_object_ids = {o["object_id"] for o in home_world.get("objects", [])}
    hw_vp_ids = set(home_world.get("viewpoints", {}).keys())
    hw_anchor_ids = {a["anchor_id"] for a in home_world.get("furniture", [])}
    all_hw_ids = hw_object_ids | hw_anchor_ids

    for obj_id in overlay.get("present_object_ids", []):
        if obj_id not in hw_object_ids:
            issues.append(_issue(
                "overlay_object_not_found", scope,
                f"overlay present_object_id {obj_id!r} not in HomeWorld",
            ))

    for vp_id in overlay.get("active_viewpoint_ids", []):
        if vp_id not in hw_vp_ids:
            issues.append(_issue(
                "overlay_viewpoint_not_found", scope,
                f"overlay active_viewpoint_id {vp_id!r} not in HomeWorld",
            ))

    for vp_id, vp_override in overlay.get("visibility_overrides", {}).items():
        if vp_id not in hw_vp_ids:
            issues.append(_issue(
                "overlay_visibility_override_viewpoint_not_found", scope,
                f"overlay visibility_overrides key {vp_id!r} not in HomeWorld",
            ))
        # Check internal references inside the override
        if isinstance(vp_override, dict):
            for obj_id in vp_override.get("objects", []):
                if obj_id not in hw_object_ids:
                    issues.append(_issue(
                        "overlay_object_not_found", scope,
                        f"overlay visibility_overrides.{vp_id}.objects references "
                        f"unknown object {obj_id!r}",
                    ))
            for anchor_id in vp_override.get("anchors", []):
                if anchor_id not in hw_anchor_ids:
                    issues.append(_issue(
                        "overlay_anchor_not_found", scope,
                        f"overlay visibility_overrides.{vp_id}.anchors references "
                        f"unknown anchor {anchor_id!r}",
                    ))
            for rel in vp_override.get("scene_relations", []):
                for key in ("subject_object_id", "target_object_id"):
                    if rel.get(key) not in all_hw_ids:
                        issues.append(_issue(
                            "overlay_scene_relation_ref_not_found", scope,
                            f"overlay visibility_overrides.{vp_id}.scene_relation "
                            f"{key} {rel.get(key)!r} not in HomeWorld",
                        ))

    # Top-level scene_relations (if present at overlay root level)
    for rel in overlay.get("scene_relations", []):
        for key in ("subject_object_id", "target_object_id"):
            if rel.get(key) not in all_hw_ids:
                issues.append(_issue(
                    "overlay_scene_relation_ref_not_found", scope,
                    f"overlay scene_relation {key} {rel.get(key)!r} not in HomeWorld",
                ))

    return issues


# ── V9: Materialization ──────────────────────────────────────────────────────


def validate_materialization(
    scenario_name: str,
    corpus: dict[str, Any],
    profile: Any,
    *,
    require_nonempty: bool = False,
) -> list[ScenarioValidationIssue]:
    """Validate memory_profile materialization."""
    issues: list[ScenarioValidationIssue] = []
    scope = scenario_name

    try:
        result = materialize_memory(corpus, profile)
    except Exception as exc:
        issues.append(_issue(
            "materialization_failed", scope,
            f"materialize_memory raised {type(exc).__name__}: {exc}",
        ))
        return issues

    if not isinstance(result, dict) or "object_memory" not in result:
        issues.append(_issue(
            "materialization_bad_structure", scope,
            "materialize_memory result missing 'object_memory' key",
        ))
        return issues

    if require_nonempty and not result["object_memory"]:
        issues.append(_issue(
            "materialization_empty", scope,
            "materialize_memory returned empty object_memory",
        ))

    return issues


# ── Main orchestrator ────────────────────────────────────────────────────────


def validate_all(
    corpus_path: Path = CORPUS_PATH,
    catalog_path: Path = CATALOG_PATH,
    scenarios_root: Path = SCENARIOS_ROOT,
    world_path: Path = WORLD_PATH,
    *,
    include_draft: bool = False,
) -> ValidationResult:
    """Run all validation checks and return aggregated result."""
    all_issues: list[ScenarioValidationIssue] = []

    # Load global data (safe)
    try:
        corpus = _load_json(corpus_path)
    except (json.JSONDecodeError, OSError) as exc:
        return ValidationResult(issues=[_issue("corpus_parse_error", "__global__", str(exc))])

    try:
        home_world = _load_json(world_path)
    except (json.JSONDecodeError, OSError) as exc:
        return ValidationResult(issues=[_issue("homeworld_parse_error", "__global__", str(exc))])

    catalog, catalog_issues = _safe_load_catalog(catalog_path)
    all_issues.extend(catalog_issues)

    # V0 + V1: Global validation
    all_issues.extend(validate_home_world(home_world, corpus))
    all_issues.extend(validate_corpus(corpus, home_world))

    # Per-scenario validation
    for entry in catalog:
        if entry.status == "draft":
            if not include_draft:
                continue
            scenario_dir = scenarios_root / entry.name
            # Draft: validate catalog entry + manifest if present
            manifest, manifest_issues = _safe_load_manifest(entry.name)
            all_issues.extend(manifest_issues)
            if manifest is not None:
                all_issues.extend(validate_scenario_metadata(entry.name, manifest, entry))
            # Validate memory_profile if present (P-1E.3)
            profile_path = scenario_dir / "memory_profile.json"
            if profile_path.is_file():
                profile, profile_issues = _safe_load_profile(entry.name)
                all_issues.extend(profile_issues)
                if profile is not None:
                    all_issues.extend(validate_materialization(
                        entry.name, corpus, profile, require_nonempty=False,
                    ))
            # Validate failures.json if present (P-1E fix)
            failures_path = scenario_dir / "failures.json"
            if failures_path.is_file():
                failures_data = _load_json(failures_path)
                if failures_data is not None:
                    all_issues.extend(validate_failures(entry.name, failures_data))
            continue

        # Active scenario: require files based on data_source
        scenario_dir = scenarios_root / entry.name
        scope = entry.name

        # Required files for all active scenarios
        required_files = ["failures.json", "scenario.json"]
        if entry.data_source == "homeworld_profile":
            required_files.extend(["memory_profile.json", "world_overlay.json"])
        else:
            required_files.extend(["world.json", "memory.json"])

        for fname in required_files:
            if not (scenario_dir / fname).is_file():
                all_issues.append(_issue(
                    "required_file_missing", scope,
                    f"Active scenario missing required file: {fname}",
                ))

        failures_path = scenario_dir / "failures.json"
        failures = _load_json(failures_path) if failures_path.is_file() else None

        # Load world + memory based on data_source
        if entry.data_source == "homeworld_profile":
            # Use global HomeWorld + overlay and corpus + profile
            overlay_path = scenario_dir / "world_overlay.json"
            overlay = _load_json(overlay_path) if overlay_path.is_file() else None
            world = home_world  # global HomeWorld
            memory = None
            profile, profile_issues = _safe_load_profile(entry.name)
            all_issues.extend(profile_issues)
            if profile is not None and overlay is not None:
                from homemaster.world_overlay import apply_world_overlay
                try:
                    world = apply_world_overlay(home_world, overlay)
                except Exception as exc:
                    all_issues.append(_issue(
                        "overlay_apply_error", scope,
                        f"Failed to apply world overlay: {exc}",
                    ))
                try:
                    materialized = materialize_memory(corpus, profile)
                    memory = materialized
                except Exception as exc:
                    all_issues.append(_issue(
                        "materialization_error", scope,
                        f"Failed to materialize memory: {exc}",
                    ))
        else:
            # Legacy: use per-scenario world.json and memory.json
            world_path_s = scenario_dir / "world.json"
            memory_path = scenario_dir / "memory.json"
            world = _load_json(world_path_s) if world_path_s.is_file() else None
            memory = _load_json(memory_path) if memory_path.is_file() else None
            overlay = None

        # V5: Metadata (safe)
        manifest, manifest_issues = _safe_load_manifest(entry.name)
        all_issues.extend(manifest_issues)
        if manifest is not None:
            all_issues.extend(validate_scenario_metadata(entry.name, manifest, entry))

        # V2: World
        if world is not None:
            all_issues.extend(validate_scenario_world(entry.name, world))

        # V3: Memory
        if memory is not None and world is not None:
            all_issues.extend(validate_scenario_memory(entry.name, memory, world))

        # V4: Bridge
        if world is not None and memory is not None:
            all_issues.extend(validate_memory_world_bridge(entry.name, world, memory))

        # V6: Target coverage
        if memory is not None and world is not None:
            all_issues.extend(validate_target_coverage(entry.name, entry, memory, world))

        # V7: Failures
        if failures is not None:
            all_issues.extend(validate_failures(entry.name, failures))

        # V8: Overlay
        if overlay is not None:
            all_issues.extend(validate_world_overlay(entry.name, overlay, home_world))

        # V9: Materialization (safe) — skip for homeworld_profile (already done above)
        if entry.data_source != "homeworld_profile":
            profile, profile_issues = _safe_load_profile(entry.name)
            all_issues.extend(profile_issues)
            if profile is not None:
                all_issues.extend(validate_materialization(
                    entry.name, corpus, profile, require_nonempty=False,
                ))

    return ValidationResult(issues=all_issues)


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scenario & Memory Validator")
    parser.add_argument(
        "--include-draft", action="store_true", help="Also validate draft scenarios",
    )
    args = parser.parse_args()

    result = validate_all(include_draft=args.include_draft)
    for issue in result.issues:
        print(f"[{issue.error_type}] {issue.scope}: {issue.message}")
    print(f"\n{'PASS' if result.passed else 'FAIL'} ({len(result.issues)} issues)")
    sys.exit(0 if result.passed else 1)
