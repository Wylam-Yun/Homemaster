"""World overlay materializer — applies world_overlay.json to a global HomeWorld."""

from __future__ import annotations

import json
from typing import Any


def apply_world_overlay(world: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Apply world_overlay.json to a global HomeWorld, producing a scenario-specific world.

    Filters all reference layers to prevent dangling refs:
    - objects → only ``present_object_ids``
    - viewpoints → only ``active_viewpoint_ids``
    - viewpoints[vp].visible_object_ids → intersect with present objects
    - viewpoints[vp].visible_anchor_ids → intersect with active anchors
    - furniture → only anchors referenced by active viewpoints
    - visibility[vp].objects → intersect with present objects
    - visibility[vp].anchors → intersect with active anchors
    - visibility[vp].scene_relations → subject/target must both be present
    - symbolic_predicates → drop predicates referencing removed objects
    - visibility_overrides → applied last, overrides above results

    Does **not** mutate the input *world* dict.
    """
    result = json.loads(json.dumps(world))  # deep copy

    present_ids = set(overlay.get("present_object_ids", []))
    active_vps = set(overlay.get("active_viewpoint_ids", []))
    vp_overrides = overlay.get("visibility_overrides", {})

    # 1. Filter objects
    result["objects"] = [o for o in result.get("objects", []) if o["object_id"] in present_ids]

    # 2. Filter viewpoints
    result["viewpoints"] = {
        vp_id: vp for vp_id, vp in result.get("viewpoints", {}).items() if vp_id in active_vps
    }

    # 3. Determine active anchors from filtered viewpoints + furniture
    active_anchor_ids: set[str] = set()
    for f in result.get("furniture", []):
        if f["viewpoint_id"] in active_vps:
            active_anchor_ids.add(f["anchor_id"])
    result["furniture"] = [
        f for f in result.get("furniture", []) if f["anchor_id"] in active_anchor_ids
    ]

    # 4. Filter viewpoints' visible_object_ids and visible_anchor_ids
    for vp in result["viewpoints"].values():
        vp["visible_object_ids"] = [
            oid for oid in vp.get("visible_object_ids", []) if oid in present_ids
        ]
        vp["visible_anchor_ids"] = [
            aid for aid in vp.get("visible_anchor_ids", []) if aid in active_anchor_ids
        ]

    # 5. Filter visibility
    filtered_visibility: dict[str, Any] = {}
    for vp_id, vis in result.get("visibility", {}).items():
        if vp_id not in active_vps:
            continue
        vis_objects = [oid for oid in vis.get("objects", []) if oid in present_ids]
        vis_anchors = [aid for aid in vis.get("anchors", []) if aid in active_anchor_ids]
        vis_relations = [
            rel for rel in vis.get("scene_relations", [])
            if rel.get("subject_object_id") in present_ids | active_anchor_ids
            and rel.get("target_object_id") in present_ids | active_anchor_ids
        ]
        filtered_visibility[vp_id] = {
            "objects": vis_objects,
            "anchors": vis_anchors,
            "scene_relations": vis_relations,
        }
    result["visibility"] = filtered_visibility

    # 6. Filter symbolic_predicates
    result["symbolic_predicates"] = [
        p for p in result.get("symbolic_predicates", [])
        if _predicate_refs_valid(p, present_ids, active_anchor_ids)
    ]

    # 7. Apply visibility_overrides (last, overrides above)
    # Filter override contents against present/active sets to prevent dangling refs
    for vp_id, override in vp_overrides.items():
        if vp_id not in result["visibility"]:
            continue
        filtered_override: dict[str, Any] = {}
        if "objects" in override:
            filtered_override["objects"] = [oid for oid in override["objects"] if oid in present_ids]
        if "anchors" in override:
            filtered_override["anchors"] = [aid for aid in override["anchors"] if aid in active_anchor_ids]
        if "scene_relations" in override:
            all_valid = present_ids | active_anchor_ids
            filtered_override["scene_relations"] = [
                rel for rel in override["scene_relations"]
                if _override_relation_valid(rel, all_valid)
            ]
        result["visibility"][vp_id].update(filtered_override)

    return result


def _override_relation_valid(rel: dict[str, Any], all_valid: set[str]) -> bool:
    """Check if an override scene_relation's references are valid.

    Relations without subject_object_id/target_object_id (custom format) pass through.
    """
    subj = rel.get("subject_object_id")
    tgt = rel.get("target_object_id")
    if subj is None and tgt is None:
        return True  # custom format, pass through
    if subj is not None and subj not in all_valid:
        return False
    if tgt is not None and tgt not in all_valid:
        return False
    return True


def _predicate_refs_valid(
    predicate: dict[str, Any],
    present_ids: set[str],
    active_anchor_ids: set[str],
) -> bool:
    """Return True if all object/anchor references in predicate are still valid."""
    all_valid = present_ids | active_anchor_ids
    args = predicate.get("args", [])
    # Only check args that look like object/anchor IDs
    for arg in args:
        if isinstance(arg, str) and (arg.startswith("obj_") or arg.startswith("anchor_")):
            if arg not in all_valid:
                return False
    return True
