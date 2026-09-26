"""File-backed HomeWorld backend for the local robot profile."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class HomeBackendReceipt:
    ok: bool
    external_return_code: int
    code: str
    detail: str
    evidence_ref: str
    state: Mapping[str, object]


class HomeWorldBackend:
    """Authoritative file-backed state; every call rereads and writes the file."""

    def __init__(self, world_path: Path) -> None:
        self.world_path = world_path.expanduser().resolve()
        self._lock = RLock()

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.world_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read HomeWorld: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("HomeWorld root must be an object")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        self.world_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.world_path.name}.", dir=self.world_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.world_path)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _runtime(world: dict[str, Any]) -> dict[str, Any]:
        runtime = world.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise RuntimeError("HomeWorld runtime must be an object")
        runtime.setdefault("robot_room", None)
        runtime.setdefault("held_object_id", None)
        runtime.setdefault("object_locations", {})
        runtime.setdefault("revision", 0)
        locations = runtime["object_locations"]
        if not isinstance(locations, dict):
            raise RuntimeError("HomeWorld object_locations must be an object")
        if not locations:
            for relation in _relations(world):
                if relation.get("relation_type") in {"on", "in"}:
                    locations.setdefault(
                        str(relation["subject_object_id"]), relation["target_object_id"]
                    )
        return runtime

    @staticmethod
    def _resolve_room(world: Mapping[str, Any], target: str) -> str | None:
        needle = target.strip().lower()
        for room in world.get("rooms", []):
            if not isinstance(room, dict):
                continue
            values = [room.get("room_id"), room.get("display_name"), *room.get("aliases", [])]
            if any(needle == str(value).strip().lower() for value in values if value):
                return str(room.get("room_id"))
        for anchor in world.get("furniture", []):
            if not isinstance(anchor, dict):
                continue
            values = [anchor.get("anchor_id"), anchor.get("display_text")]
            if any(needle == str(value).strip().lower() for value in values if value):
                return str(anchor.get("room_id"))
        obj = _find_object(world, target)
        if obj is not None:
            return _anchor_room(world, _object_location(world, str(obj["object_id"])))
        return None

    @staticmethod
    def _resolve_anchor(world: Mapping[str, Any], target: str) -> dict[str, Any] | None:
        needle = target.strip().lower()
        for anchor in world.get("furniture", []):
            if not isinstance(anchor, dict):
                continue
            values = [
                anchor.get("anchor_id"),
                anchor.get("display_text"),
                anchor.get("anchor_type"),
            ]
            if any(needle == str(value).strip().lower() for value in values if value):
                return anchor
        return None

    def _receipt(
        self, world: dict[str, Any], *, ok: bool, code: str, detail: str, return_code: int
    ) -> HomeBackendReceipt:
        runtime = self._runtime(world)
        digest = hashlib.sha256(
            json.dumps(runtime, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        return HomeBackendReceipt(
            ok,
            return_code,
            code,
            detail,
            f"home-world/{digest}",
            json.loads(json.dumps(runtime, ensure_ascii=False)),
        )

    def go_to(self, target: str) -> HomeBackendReceipt:
        with self._lock:
            world = self._read()
            runtime = self._runtime(world)
            room = self._resolve_room(world, target)
            if room is None:
                return self._receipt(
                    world,
                    ok=False,
                    code="target_not_found",
                    detail=f"unknown home target: {target}",
                    return_code=2,
                )
            runtime["robot_room"] = room
            runtime["revision"] = int(runtime["revision"]) + 1
            self._write(world)
            return self._receipt(
                world, ok=True, code="home-go-to-ok", detail=f"robot moved to {room}", return_code=0
            )

    def manipulate(
        self, *, action: str, target: str, receptacle: str | None = None
    ) -> HomeBackendReceipt:
        with self._lock:
            world = self._read()
            runtime = self._runtime(world)
            obj = _find_object(world, target)
            if obj is None:
                return self._receipt(
                    world,
                    ok=False,
                    code="object_not_found",
                    detail=f"unknown object: {target}",
                    return_code=2,
                )
            object_id = str(obj["object_id"])
            normalized = action.strip().lower().replace("-", "_")
            object_room = _anchor_room(world, runtime["object_locations"].get(object_id))
            if normalized in {"take", "pick_up", "pickup", "get"}:
                if runtime["robot_room"] is not None and object_room != runtime["robot_room"]:
                    return self._receipt(
                        world,
                        ok=False,
                        code="wrong_room",
                        detail=f"object is in {object_room}",
                        return_code=3,
                    )
                runtime["held_object_id"] = object_id
                runtime["object_locations"][object_id] = "robot"
            elif normalized in {"put", "put_down", "place", "drop"}:
                if runtime["held_object_id"] != object_id:
                    return self._receipt(
                        world,
                        ok=False,
                        code="object_not_held",
                        detail=f"robot is not holding {object_id}",
                        return_code=4,
                    )
                anchor = self._resolve_anchor(world, receptacle or "")
                if anchor is None:
                    return self._receipt(
                        world,
                        ok=False,
                        code="receptacle_not_found",
                        detail=f"unknown receptacle: {receptacle}",
                        return_code=2,
                    )
                if (
                    runtime["robot_room"] is not None
                    and anchor.get("room_id") != runtime["robot_room"]
                ):
                    return self._receipt(
                        world,
                        ok=False,
                        code="wrong_room",
                        detail=f"receptacle is in {anchor.get('room_id')}",
                        return_code=3,
                    )
                runtime["held_object_id"] = None
                runtime["object_locations"][object_id] = str(anchor["anchor_id"])
            else:
                return self._receipt(
                    world,
                    ok=False,
                    code="unsupported_action",
                    detail=f"unsupported home action: {action}",
                    return_code=5,
                )
            runtime["revision"] = int(runtime["revision"]) + 1
            self._write(world)
            return self._receipt(
                world,
                ok=True,
                code="home-manipulate-ok",
                detail=f"{normalized} {object_id}",
                return_code=0,
            )


def _relations(world: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for viewpoint in (world.get("visibility", {}) or {}).values():
        if isinstance(viewpoint, dict):
            result.extend(
                item for item in viewpoint.get("scene_relations", []) if isinstance(item, Mapping)
            )
    return result


def _find_object(world: Mapping[str, Any], target: str) -> dict[str, Any] | None:
    needle = target.strip().lower()
    for obj in world.get("objects", []):
        if not isinstance(obj, dict):
            continue
        values = [obj.get("object_id"), obj.get("category"), *obj.get("aliases", [])]
        if any(needle == str(value).strip().lower() for value in values if value):
            return obj
    return None


def _object_location(world: Mapping[str, Any], object_id: str) -> str | None:
    for relation in _relations(world):
        if relation.get("subject_object_id") == object_id and relation.get("relation_type") in {
            "on",
            "in",
        }:
            return str(relation.get("target_object_id"))
    return None


def _anchor_room(world: Mapping[str, Any], location: object) -> str | None:
    if location in {None, "robot"}:
        return None
    for anchor in world.get("furniture", []):
        if isinstance(anchor, dict) and anchor.get("anchor_id") == location:
            return str(anchor.get("room_id"))
    return None


__all__ = ["HomeBackendReceipt", "HomeWorldBackend"]
