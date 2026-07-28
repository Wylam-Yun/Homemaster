"""Per-live-session frozen file-memory context."""

from __future__ import annotations

import threading

from homemaster.memory.file_store import FileMemoryStore


class FrozenMemoryContextService:
    def __init__(self, store: FileMemoryStore) -> None:
        self._store = store
        self._snapshots: dict[str, str] = {}
        self._lock = threading.Lock()

    def snapshot(self, session_id: str) -> str:
        with self._lock:
            existing = self._snapshots.get(session_id)
            if existing is not None:
                return existing
            blocks: list[str] = []
            soul = self._store.read_soul_for_prompt().strip()
            user = "\n§\n".join(self._store.entries_for_prompt("user")).strip()
            memory = "\n§\n".join(self._store.entries_for_prompt("memory")).strip()
            for title, content in (("SOUL.md", soul), ("USER.md", user), ("MEMORY.md", memory)):
                if content:
                    blocks.append(f"# {title}\n\n{content}")
            snapshot = "\n\n".join(blocks)
            self._snapshots[session_id] = snapshot
            return snapshot


__all__ = ["FrozenMemoryContextService"]
