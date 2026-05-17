"""ContextSnapshot — generates MEMORY.md / USER.md prompt snapshots.

These snapshots are read-only context for the model. The structured
memory/profile stores remain the source of truth. Mimo cannot directly
write these files — it can only submit proposals via update_memory /
update_user_profile tools.

Refresh rules:
  - AgentRuntime start: load latest valid or regenerate
  - update_memory commit success: atomically regenerate MEMORY.md
  - update_user_profile commit success: atomically regenerate USER.md
  - proposal rejected: do not refresh
  - stale snapshot: regenerate before next ContextBuilder build
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homemaster.agent.state import AgentState


@dataclass
class SnapshotResult:
    """Result of generating a prompt snapshot."""

    content: str
    source_versions: dict[str, str]
    content_hash: str
    generated_at: str


class ContextSnapshot:
    """Generates and manages MEMORY.md / USER.md prompt snapshots."""

    def __init__(self) -> None:
        self._memory_snapshot: SnapshotResult | None = None
        self._user_snapshot: SnapshotResult | None = None
        self._memory_hits_at_snapshot: list[dict[str, Any]] | None = None

    def generate_memory_snapshot(
        self,
        object_memory_records: list[dict[str, Any]],
        fact_memory_records: list[dict[str, Any]] | None = None,
    ) -> SnapshotResult:
        """Generate MEMORY.md snapshot from structured memory records."""
        lines = ["# Memory Context\n"]
        for rec in object_memory_records:
            cat = rec.get("object_category", "unknown")
            room = rec.get("anchor", {}).get("room_id", "unknown")
            belief = rec.get("belief_state", "unknown")
            lines.append(f"- {cat} in {room} (belief: {belief})")
        if fact_memory_records:
            lines.append("\n## Facts\n")
            for rec in fact_memory_records:
                lines.append(f"- {rec.get('content', '')}")

        content = "\n".join(lines) + "\n"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        generated_at = datetime.now(UTC).isoformat()

        result = SnapshotResult(
            content=content,
            source_versions={
                "object_memory": str(len(object_memory_records)),
                "fact_memory": str(len(fact_memory_records or [])),
            },
            content_hash=content_hash,
            generated_at=generated_at,
        )
        self._memory_snapshot = result
        self._memory_hits_at_snapshot = list(object_memory_records)
        return result

    def generate_user_snapshot(
        self,
        user_profile_records: list[dict[str, Any]] | None = None,
    ) -> SnapshotResult:
        """Generate USER.md snapshot from user profile records."""
        lines = ["# User Context\n"]
        if user_profile_records:
            for rec in user_profile_records:
                key = rec.get("key", "")
                value = rec.get("value", "")
                lines.append(f"- {key}: {value}")
        else:
            lines.append("- No user profile data available.")

        content = "\n".join(lines) + "\n"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        generated_at = datetime.now(UTC).isoformat()

        result = SnapshotResult(
            content=content,
            source_versions={"user_profile": str(len(user_profile_records or []))},
            content_hash=content_hash,
            generated_at=generated_at,
        )
        self._user_snapshot = result
        return result

    def refresh_if_stale(self, state: AgentState) -> AgentState:
        """Check if snapshots are stale and regenerate if needed.

        A snapshot is stale if:
        - state has memory hits but no snapshot content
        - state has memory hits that differ from what was used to generate
          the current snapshot (data changed since last generation)
        Returns the (possibly updated) AgentState.
        """
        if state.memory_hits:
            needs_refresh = (
                state.memory_context_snapshot is None
                or state.memory_hits != self._memory_hits_at_snapshot
            )
            if needs_refresh:
                result = self.generate_memory_snapshot(state.memory_hits)
                state.memory_context_snapshot = result.content

        if state.user_context_snapshot is None:
            result = self.generate_user_snapshot()
            state.user_context_snapshot = result.content

        return state

    @property
    def last_memory_snapshot(self) -> SnapshotResult | None:
        return self._memory_snapshot

    @property
    def last_user_snapshot(self) -> SnapshotResult | None:
        return self._user_snapshot
