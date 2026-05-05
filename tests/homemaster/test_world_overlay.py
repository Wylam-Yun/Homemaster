"""Tests for world_overlay.apply_world_overlay."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from homemaster.world_overlay import apply_world_overlay


def _minimal_world() -> dict[str, Any]:
    """Minimal HomeWorld with 2 objects, 2 viewpoints, 2 furniture, full visibility."""
    return {
        "rooms": [{"room_id": "kitchen", "display_text": "厨房"}],
        "viewpoints": {
            "vp_1": {
                "room_id": "kitchen",
                "visible_object_ids": ["obj_cup_1", "obj_bowl_1"],
                "visible_anchor_ids": ["anchor_table_1"],
            },
            "vp_2": {
                "room_id": "kitchen",
                "visible_object_ids": ["obj_cup_1"],
                "visible_anchor_ids": ["anchor_counter_1"],
            },
        },
        "furniture": [
            {"anchor_id": "anchor_table_1", "room_id": "kitchen", "anchor_type": "table",
             "viewpoint_id": "vp_1", "display_text": "餐桌"},
            {"anchor_id": "anchor_counter_1", "room_id": "kitchen", "anchor_type": "counter",
             "viewpoint_id": "vp_2", "display_text": "操作台"},
        ],
        "objects": [
            {"object_id": "obj_cup_1", "category": "cup", "aliases": ["水杯"]},
            {"object_id": "obj_bowl_1", "category": "bowl", "aliases": ["碗"]},
        ],
        "visibility": {
            "vp_1": {
                "objects": ["obj_cup_1", "obj_bowl_1"],
                "anchors": ["anchor_table_1"],
                "scene_relations": [
                    {"subject_object_id": "obj_cup_1", "target_object_id": "anchor_table_1"},
                    {"subject_object_id": "obj_bowl_1", "target_object_id": "anchor_table_1"},
                ],
            },
            "vp_2": {
                "objects": ["obj_cup_1"],
                "anchors": ["anchor_counter_1"],
                "scene_relations": [],
            },
        },
        "symbolic_predicates": [
            {"name": "visible_category", "args": ["cup"]},
            {"name": "visible_category", "args": ["bowl"]},
        ],
    }


# ── Tests ────────────────────────────────────────────────────────────────────


def test_no_dangling_object_refs():
    """After filtering, viewpoints and visibility must not reference removed objects."""
    world = _minimal_world()
    overlay = {
        "present_object_ids": ["obj_cup_1"],  # bowl removed
        "active_viewpoint_ids": ["vp_1", "vp_2"],
        "visibility_overrides": {},
        "scene_relations": [],
    }
    result = apply_world_overlay(world, overlay)

    # viewpoints must not list obj_bowl_1
    for vp in result["viewpoints"].values():
        assert "obj_bowl_1" not in vp["visible_object_ids"]

    # visibility must not list obj_bowl_1
    for vis in result["visibility"].values():
        assert "obj_bowl_1" not in vis["objects"]


def test_no_dangling_anchor_refs():
    """After filtering, visibility must not reference anchors from removed viewpoints."""
    world = _minimal_world()
    overlay = {
        "present_object_ids": ["obj_cup_1"],
        "active_viewpoint_ids": ["vp_1"],  # vp_2 removed
        "visibility_overrides": {},
        "scene_relations": [],
    }
    result = apply_world_overlay(world, overlay)

    # vp_1 visibility should still reference anchor_table_1
    assert "anchor_table_1" in result["visibility"]["vp_1"]["anchors"]
    # vp_2 visibility should be gone
    assert "vp_2" not in result["visibility"]
    # anchor_counter_1 furniture should be gone
    anchor_ids = {f["anchor_id"] for f in result["furniture"]}
    assert "anchor_counter_1" not in anchor_ids


def test_visibility_overrides_applied():
    """visibility_overrides replace the filtered visibility entries."""
    world = _minimal_world()
    overlay = {
        "present_object_ids": ["obj_cup_1"],
        "active_viewpoint_ids": ["vp_1"],
        "visibility_overrides": {
            "vp_1": {
                "objects": ["obj_cup_1"],
                "anchors": ["anchor_table_1"],
                "scene_relations": [{"custom": True}],
            },
        },
        "scene_relations": [],
    }
    result = apply_world_overlay(world, overlay)
    assert result["visibility"]["vp_1"]["scene_relations"] == [{"custom": True}]


def test_scene_relations_filtered():
    """scene_relations referencing removed objects are dropped."""
    world = _minimal_world()
    overlay = {
        "present_object_ids": ["obj_cup_1"],  # bowl removed
        "active_viewpoint_ids": ["vp_1"],
        "visibility_overrides": {},
        "scene_relations": [],
    }
    result = apply_world_overlay(world, overlay)
    relations = result["visibility"]["vp_1"]["scene_relations"]
    # The relation with obj_bowl_1 should be gone
    for rel in relations:
        assert rel.get("subject_object_id") != "obj_bowl_1"
        assert rel.get("target_object_id") != "obj_bowl_1"


def test_empty_overlay_returns_filtered_world():
    """An empty overlay effectively filters nothing (all IDs present = all kept)."""
    world = _minimal_world()
    overlay = {
        "present_object_ids": ["obj_cup_1", "obj_bowl_1"],
        "active_viewpoint_ids": ["vp_1", "vp_2"],
        "visibility_overrides": {},
        "scene_relations": [],
    }
    original = copy.deepcopy(world)
    result = apply_world_overlay(world, overlay)

    assert len(result["objects"]) == len(original["objects"])
    assert len(result["viewpoints"]) == len(original["viewpoints"])
    assert len(result["furniture"]) == len(original["furniture"])


def test_does_not_mutate_input():
    """The input world dict must not be modified."""
    world = _minimal_world()
    original = copy.deepcopy(world)
    overlay = {
        "present_object_ids": ["obj_cup_1"],
        "active_viewpoint_ids": ["vp_1"],
        "visibility_overrides": {},
        "scene_relations": [],
    }
    apply_world_overlay(world, overlay)
    assert world == original


def test_override_filtered_against_present_ids():
    """Override objects referencing non-present IDs must be filtered out."""
    world = _minimal_world()
    overlay = {
        "present_object_ids": ["obj_cup_1"],  # bowl NOT present
        "active_viewpoint_ids": ["vp_1"],
        "visibility_overrides": {
            "vp_1": {
                "objects": ["obj_cup_1", "obj_bowl_1"],  # bowl is dangling
                "anchors": ["anchor_table_1"],
                "scene_relations": [],
            },
        },
        "scene_relations": [],
    }
    result = apply_world_overlay(world, overlay)
    assert "obj_bowl_1" not in result["visibility"]["vp_1"]["objects"]
