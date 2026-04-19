"""Mock perception adapter that outputs standard Observation schema."""

from __future__ import annotations

from typing import Any

from task_brain.domain import (
    ConfidenceLevel,
    Observation,
    ObservationSource,
    ObservedAnchor,
    ObservedObject,
    SceneRelation,
)
from task_brain.world import MockWorld


class MockPerceptionAdapter:
    """Adapter converting mock world truth to normalized perception output."""

    @staticmethod
    def observe(world: MockWorld, viewpoint_id: str) -> Observation:
        """Generate one observation from a viewpoint."""
        room_id = world.get_viewpoint_room(viewpoint_id)

        visible_objects = [
            ObservedObject(
                observation_object_id=f"{viewpoint_id}:{item['object_id']}",
                category=item["category"],
                aliases=_list_of_str(item.get("aliases")),
                attributes=_list_of_str(item.get("attributes")),
                detector_id=_detector_id(item),
                memory_id=_optional_str(item.get("memory_id")),
                confidence_level=_coerce_confidence(item.get("confidence_level")),
                state_summary=_optional_str(item.get("state_summary")),
                spatial_relation=_optional_str(item.get("spatial_relation")),
            )
            for item in world.get_visible_objects(viewpoint_id)
        ]

        visible_anchors = [
            ObservedAnchor(
                room_id=item["room_id"],
                anchor_id=item["anchor_id"],
                anchor_type=item["anchor_type"],
                viewpoint_id=_optional_str(item.get("viewpoint_id")),
                display_text=_optional_str(item.get("display_text")),
            )
            for item in world.get_visible_anchors(viewpoint_id)
        ]

        scene_relations = [
            SceneRelation(
                relation_type=item["relation_type"],
                subject_object_id=item["subject_object_id"],
                target_object_id=item["target_object_id"],
                text=_optional_str(item.get("text")),
            )
            for item in world.get_scene_relations(viewpoint_id)
        ]

        return Observation(
            observation_id=f"obs-{viewpoint_id}",
            source=ObservationSource.MOCK_WORLD,
            viewpoint_id=viewpoint_id,
            room_id=room_id,
            visible_objects=visible_objects,
            visible_anchors=visible_anchors,
            scene_relations=scene_relations,
            raw_ref=f"mock_world:{viewpoint_id}",
        )


def _detector_id(payload: dict[str, Any]) -> str:
    explicit = _optional_str(payload.get("detector_id"))
    if explicit:
        return explicit
    return f"det-{payload['object_id']}"


def _coerce_confidence(value: Any) -> ConfidenceLevel:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {item.value for item in ConfidenceLevel}:
            return ConfidenceLevel(normalized)
    return ConfidenceLevel.MEDIUM


def _list_of_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
