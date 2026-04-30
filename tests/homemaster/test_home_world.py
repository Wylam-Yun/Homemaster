"""Validate the unified HomeWorld data file.

Ensures data/homes/elder_home_v1/world.json is well-formed, has consistent
references, and meets the minimum coverage requirements for P-1A.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.runtime import REPO_ROOT

WORLD_PATH = REPO_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"

VALID_ANCHOR_TYPES = {"table", "cabinet", "shelf", "counter", "sofa"}

CORE_CATEGORIES = {
    "cup",
    "medicine_box",
    "remote_control",
    "glasses",
    "keys",
    "tissue",
    "water_bottle",
    "book",
    "phone",
}


@pytest.fixture(scope="module")
def world() -> dict:
    assert WORLD_PATH.is_file(), f"HomeWorld not found: {WORLD_PATH}"
    return json.loads(WORLD_PATH.read_text(encoding="utf-8"))


# --- Metadata ---

def test_schema_version_present(world: dict):
    assert "schema_version" in world
    assert world["schema_version"] == "1.0"


def test_home_id_present(world: dict):
    assert "home_id" in world
    assert world["home_id"] == "elder_home_v1"


# --- Rooms ---

def test_rooms_exist(world: dict):
    rooms = world.get("rooms", [])
    assert len(rooms) >= 6, f"Expected at least 6 rooms, got {len(rooms)}"


def test_room_ids_unique(world: dict):
    ids = [r["room_id"] for r in world.get("rooms", [])]
    assert len(ids) == len(set(ids)), f"Duplicate room_id: {[x for x in ids if ids.count(x) > 1]}"


# --- Anchors (furniture) ---

def test_anchors_exist(world: dict):
    anchors = world.get("furniture", [])
    assert len(anchors) >= 14, f"Expected at least 14 anchors, got {len(anchors)}"


def test_anchor_ids_unique(world: dict):
    anchors = world.get("furniture", [])
    ids = [a["anchor_id"] for a in anchors]
    assert len(ids) == len(set(ids)), f"Duplicate anchor_id: {[x for x in ids if ids.count(x) > 1]}"


def test_anchor_ids_have_prefix(world: dict):
    for anchor in world.get("furniture", []):
        assert anchor["anchor_id"].startswith("anchor_"), (
            f"anchor_id {anchor['anchor_id']!r} should start with 'anchor_'"
        )


def test_anchor_room_references_valid(world: dict):
    room_ids = {r["room_id"] for r in world.get("rooms", [])}
    for anchor in world.get("furniture", []):
        assert anchor["room_id"] in room_ids, (
            f"Anchor {anchor['anchor_id']!r} references unknown room {anchor['room_id']!r}"
        )


def test_anchor_viewpoint_references_valid(world: dict):
    viewpoint_ids = set(world.get("viewpoints", {}).keys())
    for anchor in world.get("furniture", []):
        assert anchor["viewpoint_id"] in viewpoint_ids, (
            f"Anchor {anchor['anchor_id']!r} references unknown viewpoint {anchor['viewpoint_id']!r}"
        )


def test_anchor_types_valid(world: dict):
    for anchor in world.get("furniture", []):
        assert anchor["anchor_type"] in VALID_ANCHOR_TYPES, (
            f"Anchor {anchor['anchor_id']!r} has invalid type {anchor['anchor_type']!r}"
        )


# --- Viewpoints ---

def test_viewpoints_exist(world: dict):
    viewpoints = world.get("viewpoints", {})
    assert len(viewpoints) >= 14, f"Expected at least 14 viewpoints, got {len(viewpoints)}"


def test_viewpoint_ids_have_vp_suffix(world: dict):
    for vp_id in world.get("viewpoints", {}):
        assert vp_id.endswith("_vp"), (
            f"viewpoint_id {vp_id!r} should end with '_vp'"
        )


def test_viewpoint_room_references_valid(world: dict):
    room_ids = {r["room_id"] for r in world.get("rooms", [])}
    for vp_id, vp in world.get("viewpoints", {}).items():
        assert vp["room_id"] in room_ids, (
            f"Viewpoint {vp_id!r} references unknown room {vp['room_id']!r}"
        )


# --- Objects ---

def test_objects_exist(world: dict):
    objects = world.get("objects", [])
    assert len(objects) >= 20, f"Expected at least 20 objects, got {len(objects)}"


def test_object_ids_unique(world: dict):
    objects = world.get("objects", [])
    ids = [o["object_id"] for o in objects]
    assert len(ids) == len(set(ids)), f"Duplicate object_id: {[x for x in ids if ids.count(x) > 1]}"


def test_object_ids_have_prefix(world: dict):
    for obj in world.get("objects", []):
        assert obj["object_id"].startswith("obj_"), (
            f"object_id {obj['object_id']!r} should start with 'obj_'"
        )


def test_object_has_required_fields(world: dict):
    for obj in world.get("objects", []):
        for field in ("category", "aliases", "memory_id"):
            assert field in obj, (
                f"Object {obj.get('object_id', '?')!r} missing field {field!r}"
            )
        assert len(obj["aliases"]) > 0, (
            f"Object {obj['object_id']!r} has empty aliases"
        )


def test_object_memory_ids_unique(world: dict):
    objects = world.get("objects", [])
    mem_ids = [o["memory_id"] for o in objects]
    assert len(mem_ids) == len(set(mem_ids)), (
        f"Duplicate memory_id: {[x for x in mem_ids if mem_ids.count(x) > 1]}"
    )


def test_core_categories_present(world: dict):
    objects = world.get("objects", [])
    present = {o["category"] for o in objects}
    missing = CORE_CATEGORIES - present
    assert not missing, f"Missing core categories: {missing}"


# --- Visibility ---

def test_visibility_keys_match_viewpoints(world: dict):
    vp_ids = set(world.get("viewpoints", {}).keys())
    vis_ids = set(world.get("visibility", {}).keys())
    assert vp_ids == vis_ids, (
        f"Visibility keys mismatch: extra in visibility={vis_ids - vp_ids}, "
        f"missing from visibility={vp_ids - vis_ids}"
    )


def test_visibility_object_references_valid(world: dict):
    object_ids = {o["object_id"] for o in world.get("objects", [])}
    for vp_id, vis in world.get("visibility", {}).items():
        for obj_id in vis.get("objects", []):
            assert obj_id in object_ids, (
                f"Visibility {vp_id!r} references unknown object {obj_id!r}"
            )


def test_visibility_anchor_references_valid(world: dict):
    anchor_ids = {a["anchor_id"] for a in world.get("furniture", [])}
    for vp_id, vis in world.get("visibility", {}).items():
        for anchor_id in vis.get("anchors", []):
            assert anchor_id in anchor_ids, (
                f"Visibility {vp_id!r} references unknown anchor {anchor_id!r}"
            )


def test_scene_relations_reference_valid_ids(world: dict):
    object_ids = {o["object_id"] for o in world.get("objects", [])}
    anchor_ids = {a["anchor_id"] for a in world.get("furniture", [])}
    all_ids = object_ids | anchor_ids
    for vp_id, vis in world.get("visibility", {}).items():
        for rel in vis.get("scene_relations", []):
            assert rel["subject_object_id"] in all_ids, (
                f"scene_relation in {vp_id!r} references unknown subject {rel['subject_object_id']!r}"
            )
            assert rel["target_object_id"] in all_ids, (
                f"scene_relation in {vp_id!r} references unknown target {rel['target_object_id']!r}"
            )


# --- Physical instance constraint ---

def test_each_object_in_exactly_one_viewpoint(world: dict):
    """Each object should appear in exactly one viewpoint's visible_object_ids."""
    object_ids = {o["object_id"] for o in world.get("objects", [])}
    appearance: dict[str, list[str]] = {oid: [] for oid in object_ids}
    for vp_id, vp in world.get("viewpoints", {}).items():
        for obj_id in vp.get("visible_object_ids", []):
            appearance[obj_id].append(vp_id)

    duplicates = {oid: vps for oid, vps in appearance.items() if len(vps) > 1}
    assert not duplicates, (
        f"Objects appearing in multiple viewpoints: {duplicates}"
    )
