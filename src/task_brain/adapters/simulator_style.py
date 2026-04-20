"""Simulator-style observation adapter.

This module converts simulator events into the standard Observation schema
without exposing raw simulator payloads to planning/verification layers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from task_brain.domain import (
    ConfidenceLevel,
    Observation,
    ObservationSource,
    ObservedAnchor,
    ObservedObject,
    SceneRelation,
)


class SimulatorPose(BaseModel):
    """Agent pose payload from simulator event streams."""

    viewpoint_id: str | None = None
    room_id: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None


class SimulatorVisibleObject(BaseModel):
    """Visible object payload in one simulator frame."""

    object_id: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    detector_id: str | None = None
    memory_id: str | None = None
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    state_summary: str | None = None
    spatial_relation: str | None = None


class SimulatorVisibleAnchor(BaseModel):
    """Visible anchor payload in one simulator frame."""

    room_id: str
    anchor_id: str
    anchor_type: str
    viewpoint_id: str | None = None
    display_text: str | None = None


class SimulatorSceneRelation(BaseModel):
    """Scene relation payload in one simulator frame."""

    relation_type: str
    subject_object_id: str
    target_object_id: str
    text: str | None = None


class SimulatorEvent(BaseModel):
    """Simulator-side event payload normalized by adapter."""

    event_id: str
    pose: SimulatorPose
    room_id: str | None = None
    visible_objects: list[SimulatorVisibleObject] = Field(default_factory=list)
    visible_anchors: list[SimulatorVisibleAnchor] = Field(default_factory=list)
    scene_relations: list[SimulatorSceneRelation] = Field(default_factory=list)
    object_states: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimulatorStyleAdapter:
    """Convert simulator events into the standard Observation schema."""

    @staticmethod
    def to_observation(event: SimulatorEvent | dict[str, Any]) -> Observation:
        """Normalize one simulator event into Observation."""
        simulator_event = (
            event.model_copy(deep=True)
            if isinstance(event, SimulatorEvent)
            else SimulatorEvent.model_validate(event)
        )
        viewpoint_id = _resolve_viewpoint_id(simulator_event)
        room_id = _resolve_room_id(simulator_event)

        visible_objects = [
            ObservedObject(
                observation_object_id=f"{simulator_event.event_id}:{item.object_id}",
                category=item.category,
                aliases=item.aliases,
                attributes=item.attributes,
                detector_id=item.detector_id or f"det-{item.object_id}",
                memory_id=item.memory_id,
                confidence_level=item.confidence_level,
                state_summary=item.state_summary
                or _state_summary(simulator_event.object_states, item.object_id),
                spatial_relation=item.spatial_relation,
            )
            for item in simulator_event.visible_objects
        ]

        visible_anchors = [
            ObservedAnchor(
                room_id=item.room_id,
                anchor_id=item.anchor_id,
                anchor_type=item.anchor_type,
                viewpoint_id=item.viewpoint_id,
                display_text=item.display_text,
            )
            for item in simulator_event.visible_anchors
        ]

        scene_relations = [
            SceneRelation(
                relation_type=item.relation_type,
                subject_object_id=item.subject_object_id,
                target_object_id=item.target_object_id,
                text=item.text,
            )
            for item in simulator_event.scene_relations
        ]

        return Observation(
            observation_id=f"sim-obs-{simulator_event.event_id}",
            source=ObservationSource.AI2_THOR,
            viewpoint_id=viewpoint_id,
            room_id=room_id,
            visible_objects=visible_objects,
            visible_anchors=visible_anchors,
            scene_relations=scene_relations,
            raw_ref=f"simulator:{simulator_event.event_id}",
        )


def _resolve_viewpoint_id(event: SimulatorEvent) -> str:
    if event.pose.viewpoint_id:
        return event.pose.viewpoint_id
    maybe = event.metadata.get("viewpoint_id")
    if isinstance(maybe, str) and maybe:
        return maybe
    return f"sim-viewpoint-{event.event_id}"


def _resolve_room_id(event: SimulatorEvent) -> str:
    if event.room_id:
        return event.room_id
    if event.pose.room_id:
        return event.pose.room_id
    maybe = event.metadata.get("room_id")
    if isinstance(maybe, str) and maybe:
        return maybe
    return "unknown_room"


def _state_summary(object_states: dict[str, Any], object_id: str) -> str | None:
    value = object_states.get(object_id)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value):
            item = value[key]
            if isinstance(item, (str, int, float, bool)):
                parts.append(f"{key}={item}")
        if parts:
            return "; ".join(parts)
    return None


__all__ = [
    "SimulatorEvent",
    "SimulatorPose",
    "SimulatorSceneRelation",
    "SimulatorStyleAdapter",
    "SimulatorVisibleAnchor",
    "SimulatorVisibleObject",
]
