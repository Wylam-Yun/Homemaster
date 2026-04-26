"""Runtime object memory store for Stage 06 writes."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.contracts import MemoryCommitPlan, ObjectMemoryUpdate
from homemaster.trace import sanitize_for_log


class RuntimeMemoryStoreError(RuntimeError):
    """Raised when runtime memory cannot be updated safely."""


class RuntimeMemoryStore:
    """Persist object memory overlays outside tracked scenario fixtures."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.object_memory_path = self.root / "object_memory.json"

    def load_runtime_or_base(self, base_memory_path: Path) -> dict[str, object]:
        if self.object_memory_path.exists():
            return _load_json(self.object_memory_path)
        return _load_json(base_memory_path)

    def apply_commit_plan(
        self,
        *,
        base_memory_path: Path,
        plan: MemoryCommitPlan,
    ) -> Path:
        payload = self.load_runtime_or_base(base_memory_path)
        raw_memories = payload.get("object_memory")
        if not isinstance(raw_memories, list):
            raise RuntimeMemoryStoreError("memory payload must contain object_memory list")

        memories = [dict(item) for item in raw_memories if isinstance(item, dict)]
        by_id = {
            str(item.get("memory_id")): item
            for item in memories
            if isinstance(item.get("memory_id"), str)
        }
        for update in plan.object_memory_updates:
            target = by_id.get(update.memory_id)
            if target is None:
                continue
            _apply_object_memory_update(target, update)

        updated_payload = dict(payload)
        updated_payload["object_memory"] = memories
        self.root.mkdir(parents=True, exist_ok=True)
        self.object_memory_path.write_text(
            json.dumps(
                sanitize_for_log(updated_payload),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return self.object_memory_path


def _apply_object_memory_update(
    memory: dict[str, object],
    update: ObjectMemoryUpdate,
) -> None:
    memory.update(update.updated_fields)
    if update.update_type == "mark_stale":
        memory["belief_state"] = "stale"
    elif update.update_type == "mark_contradicted":
        memory["belief_state"] = "contradicted"


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeMemoryStoreError(f"invalid memory JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeMemoryStoreError(f"memory payload must be an object: {path}")
    return payload
