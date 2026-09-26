"""Deterministic scene snapshot and grounding helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SceneObjectRef:
    label: str
    object_id: str
    object_type: str | None = None


class SceneGroundingError(ValueError):
    """The model label cannot be resolved to exactly one current object."""


class SceneSnapshot:
    def __init__(self, observation: dict[str, Any], *, generation: int, sequence: int) -> None:
        self.observation = dict(observation)
        self.generation = generation
        self.sequence = sequence
        self._objects = self._index(self.observation.get("objects"))

    @staticmethod
    def _index(value: Any) -> dict[str, tuple[dict[str, Any], ...]]:
        if not isinstance(value, list):
            return {}
        objects_by_id: dict[str, dict[str, Any]] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            object_id = item.get("objectId") or item.get("object_id")
            if isinstance(object_id, str) and object_id:
                objects_by_id.setdefault(object_id, dict(item))

        indexed: dict[str, list[dict[str, Any]]] = {}

        def add(name: str, item: dict[str, Any]) -> None:
            key = name.strip().casefold()
            if not key:
                return
            bucket = indexed.setdefault(key, [])
            object_id = item.get("objectId") or item.get("object_id")
            if not any(
                (existing.get("objectId") or existing.get("object_id")) == object_id
                for existing in bucket
            ):
                bucket.append(item)

        by_type: dict[str, list[dict[str, Any]]] = {}
        for item in objects_by_id.values():
            object_type = item.get("objectType") or item.get("object_type")
            if isinstance(object_type, str) and object_type.strip():
                type_key = "".join(char for char in object_type.casefold() if char.isalnum())
                by_type.setdefault(type_key, []).append(item)
        for type_key, items in by_type.items():
            ordered = sorted(
                items,
                key=lambda item: str(item.get("objectId") or item.get("object_id") or ""),
            )
            # Match the in-process adapter: an unnumbered type is a stable
            # first instance, while numbered labels address every instance.
            add(type_key, ordered[0])
            for index, item in enumerate(ordered, start=1):
                add(f"{type_key} {index}", item)

        for item in objects_by_id.values():
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            object_type = item.get("objectType") or item.get("object_type")
            type_key = (
                "".join(char for char in object_type.casefold() if char.isalnum())
                if isinstance(object_type, str)
                else ""
            )
            if name.casefold().strip() != type_key:
                add(name, item)
        return {key: tuple(items) for key, items in indexed.items()}

    def ground(self, label: str) -> SceneObjectRef:
        key = label.strip().casefold()
        matches = self._objects.get(key, ())
        if len(matches) != 1:
            raise SceneGroundingError(
                f"expected one object for {label!r}, found {len(matches)} "
                f"in scene generation {self.generation}"
            )
        item = matches[0]
        return SceneObjectRef(
            label=label.strip(),
            object_id=str(item.get("objectId") or item.get("object_id")),
            object_type=str(item.get("objectType") or item.get("object_type") or "") or None,
        )
