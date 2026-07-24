"""Runtime object memory store writes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from homemaster.events.trace import json_compatible_copy


@dataclass(frozen=True)
class ObjectMemoryUpdate:
    """One object-memory update applied to the per-run memory overlay."""

    memory_id: str
    update_type: Literal["confirm", "mark_stale", "mark_contradicted"] = "confirm"
    updated_fields: dict[str, Any] = field(default_factory=dict)


class RuntimeMemoryStoreError(RuntimeError):
    """Raised when runtime memory cannot be updated safely."""


class RuntimeMemoryStore:
    """Persist object memory overlays outside tracked fixtures."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.object_memory_path = self.root / "object_memory.json"

    def load_runtime_or_base(self, base_memory_path: Path) -> dict[str, object]:
        if self.object_memory_path.exists():
            return _load_json(self.object_memory_path)
        return _load_json(base_memory_path)

    def apply_updates(
        self,
        *,
        base_memory_path: Path,
        updates: Sequence[ObjectMemoryUpdate],
    ) -> Path:
        payload = self.load_runtime_or_base(base_memory_path)
        memory_key = _memory_collection_key(payload)
        raw_memories = payload.get(memory_key)
        if not isinstance(raw_memories, list):
            raise RuntimeMemoryStoreError(
                "memory payload must contain object_memory or objects list"
            )

        memories = [dict(item) for item in raw_memories if isinstance(item, dict)]
        for update in updates:
            for memory in memories:
                if _matches_memory(memory, update.memory_id):
                    _apply_object_memory_update(memory, update)

        updated_payload = dict(payload)
        updated_payload[memory_key] = memories
        self.root.mkdir(parents=True, exist_ok=True)
        self.object_memory_path.write_text(
            json.dumps(
                json_compatible_copy(updated_payload),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return self.object_memory_path


def _apply_object_memory_update(
    memory: dict[str, Any],
    update: ObjectMemoryUpdate,
) -> None:
    memory.update(update.updated_fields)
    if update.update_type == "mark_stale":
        memory["belief_state"] = "stale"
    elif update.update_type == "mark_contradicted":
        memory["belief_state"] = "contradicted"


def _memory_collection_key(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("object_memory"), list):
        return "object_memory"
    if isinstance(payload.get("objects"), list):
        return "objects"
    raise RuntimeMemoryStoreError("memory payload must contain object_memory or objects list")


def _matches_memory(memory: dict[str, Any], update_id: str) -> bool:
    if memory.get("memory_id") == update_id:
        return True
    anchor = memory.get("anchor")
    return isinstance(anchor, dict) and anchor.get("anchor_id") == update_id


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeMemoryStoreError(f"invalid memory JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeMemoryStoreError(f"memory payload must be an object: {path}")
    return payload
