"""Mock world model and query interface for Phase A."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


class MockWorld:
    """Read-only world facade for mock perception and future adapters."""

    _REQUIRED_KEYS = (
        "rooms",
        "viewpoints",
        "furniture",
        "objects",
        "visibility",
        "symbolic_predicates",
    )

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self._rooms = payload["rooms"]
        self._viewpoints = payload["viewpoints"]
        self._furniture = payload["furniture"]
        self._objects = payload["objects"]
        self._visibility = payload["visibility"]
        self._symbolic_predicates = payload["symbolic_predicates"]

        self._objects_by_id = {
            self._object_id(item): item
            for item in self._objects
            if isinstance(item, dict) and self._object_id(item) is not None
        }
        self._anchors_by_id = {
            self._anchor_id(item): item
            for item in self._furniture
            if isinstance(item, dict) and self._anchor_id(item) is not None
        }

    @classmethod
    def from_file(cls, path: str | Path) -> MockWorld:
        """Load a world fixture from JSON file."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MockWorld:
        """Create mock world from a dictionary payload."""
        missing = [key for key in cls._REQUIRED_KEYS if key not in payload]
        if missing:
            raise ValueError(f"world payload missing required keys: {missing}")
        return cls(deepcopy(payload))

    def get_viewpoint_room(self, viewpoint_id: str) -> str:
        """Return room ID that a viewpoint belongs to."""
        viewpoint = self._require_viewpoint(viewpoint_id)
        room_id = viewpoint.get("room_id")
        if not room_id:
            raise ValueError(f"viewpoint '{viewpoint_id}' does not define room_id")
        return room_id

    def get_visible_objects(self, viewpoint_id: str) -> list[dict[str, Any]]:
        """Return object records visible from given viewpoint."""
        self._require_viewpoint(viewpoint_id)
        ids = self._visible_object_ids(viewpoint_id)
        return [
            dict(self._objects_by_id[item_id])
            for item_id in ids
            if item_id in self._objects_by_id
        ]

    def get_visible_anchors(self, viewpoint_id: str) -> list[dict[str, Any]]:
        """Return furniture/anchor records visible from given viewpoint."""
        self._require_viewpoint(viewpoint_id)
        ids = self._visible_anchor_ids(viewpoint_id)
        return [
            dict(self._anchors_by_id[item_id])
            for item_id in ids
            if item_id in self._anchors_by_id
        ]

    def get_scene_relations(self, viewpoint_id: str) -> list[dict[str, Any]]:
        """Return scene relations visible from a viewpoint."""
        self._require_viewpoint(viewpoint_id)
        visibility = self._visibility_entry(viewpoint_id)
        relations = visibility.get("scene_relations", [])
        return [dict(item) for item in relations if isinstance(item, dict)]

    def query_predicates(self, predicate_name: str, **filters: Any) -> list[dict[str, Any]]:
        """Query symbolic predicates by name and optional exact-match filters."""
        matches: list[dict[str, Any]] = []
        for predicate in self._symbolic_predicates:
            if not isinstance(predicate, dict):
                continue
            if predicate.get("name") != predicate_name:
                continue
            if all(predicate.get(key) == value for key, value in filters.items()):
                matches.append(dict(predicate))
        return matches

    def _require_viewpoint(self, viewpoint_id: str) -> dict[str, Any]:
        if viewpoint_id not in self._viewpoints:
            raise ValueError(f"unknown viewpoint_id: {viewpoint_id}")
        viewpoint = self._viewpoints[viewpoint_id]
        if not isinstance(viewpoint, dict):
            raise ValueError(f"viewpoint '{viewpoint_id}' payload must be an object")
        return viewpoint

    def _visibility_entry(self, viewpoint_id: str) -> dict[str, Any]:
        visibility = self._visibility.get(viewpoint_id, {})
        if isinstance(visibility, dict):
            return visibility
        return {}

    def _visible_object_ids(self, viewpoint_id: str) -> list[str]:
        visibility = self._visibility_entry(viewpoint_id)
        ids = visibility.get("objects")
        if ids is None:
            ids = self._viewpoints[viewpoint_id].get("visible_object_ids", [])
        return [item for item in ids if isinstance(item, str)]

    def _visible_anchor_ids(self, viewpoint_id: str) -> list[str]:
        visibility = self._visibility_entry(viewpoint_id)
        ids = visibility.get("anchors")
        if ids is None:
            ids = self._viewpoints[viewpoint_id].get("visible_anchor_ids", [])
        return [item for item in ids if isinstance(item, str)]

    @staticmethod
    def _object_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("object_id")
        return value if isinstance(value, str) else None

    @staticmethod
    def _anchor_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("anchor_id")
        return value if isinstance(value, str) else None
