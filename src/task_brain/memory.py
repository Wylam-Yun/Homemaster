"""Long-term object memory and task-scoped retrieval utilities for Phase A."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from task_brain.domain import (
    Anchor,
    BeliefState,
    ConfidenceLevel,
    EvidenceSource,
    ObjectMemory,
    ParsedTask,
    RelativeRelation,
    RuntimeState,
)


def anchor_to_location_key(anchor: Anchor) -> str:
    """Normalize anchor to canonical location key."""
    return f"{anchor.room_id}:{anchor.anchor_id}"


class MemoryStore:
    """In-memory store managing long-term object memory only."""

    def __init__(self, object_memories: list[ObjectMemory]) -> None:
        self._object_memories = object_memories
        self._by_id = {item.memory_id: item for item in object_memories}

    @classmethod
    def from_file(cls, path: str | Path) -> MemoryStore:
        """Load memory store from a JSON file."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MemoryStore:
        """Load memory store from dictionary payload."""
        raw_memories = payload.get("object_memory")
        if raw_memories is None:
            raise ValueError("memory payload must contain 'object_memory'")
        if not isinstance(raw_memories, list):
            raise ValueError("'object_memory' must be a list")
        memories = [ObjectMemory.model_validate(item) for item in raw_memories]
        return cls(memories)

    def dump_object_memory(self) -> list[ObjectMemory]:
        """Return a shallow copy of long-term object memory list."""
        return list(self._object_memories)

    def get_object_memory(self, memory_id: str) -> ObjectMemory | None:
        """Get one long-term memory by ID."""
        return self._by_id.get(memory_id)

    def retrieve_object_memory(
        self,
        object_category: str,
        aliases: list[str],
    ) -> list[ObjectMemory]:
        """Retrieve object memories with category filter then alias preference."""
        normalized_category = _normalize(object_category)
        normalized_aliases = {_normalize(item) for item in aliases if item}

        matched = [
            item
            for item in self._object_memories
            if _normalize(item.object_category) == normalized_category
        ]
        matched.sort(
            key=lambda item: (
                0 if _memory_alias_hit(item, normalized_aliases) else 1,
                item.memory_id,
            )
        )
        return matched

    def update_object_memory_from_verified_observation(
        self,
        memory_id: str,
        *,
        verified: bool,
        anchor: Anchor | None = None,
        relative_relation: RelativeRelation | None = None,
        last_observed_state: str | None = None,
        evidence_source: EvidenceSource = EvidenceSource.DIRECT_OBSERVATION,
        confidence_level: ConfidenceLevel | None = None,
        description: str | None = None,
    ) -> bool:
        """Update long-term memory only when evidence is verified."""
        if not verified:
            return False

        memory = self.get_object_memory(memory_id)
        if memory is None:
            return False

        if anchor is not None:
            memory.anchor = deepcopy(anchor)
        if relative_relation is not None:
            memory.relative_relation = deepcopy(relative_relation)
        if last_observed_state is not None:
            memory.last_observed_state = last_observed_state
        if description is not None:
            memory.description = description

        memory.evidence_source = evidence_source
        memory.confidence_level = (
            confidence_level or _default_confidence_for_source(evidence_source)
        )
        memory.last_confirmed_at = datetime.now(UTC)
        memory.belief_state = BeliefState.CONFIRMED
        return True

    def downgrade_stale_memory(self, memory_id: str) -> bool:
        """Downgrade one memory confidence and mark it stale."""
        memory = self.get_object_memory(memory_id)
        if memory is None:
            return False
        memory.confidence_level = _downgraded_confidence(memory.confidence_level)
        memory.belief_state = BeliefState.STALE
        return True

    def mark_object_memory_stale_or_contradicted(
        self,
        memory_id: str,
        contradicted: bool = False,
    ) -> bool:
        """Mark one memory stale/contradicted with conservative confidence downgrade."""
        memory = self.get_object_memory(memory_id)
        if memory is None:
            return False
        if contradicted:
            memory.belief_state = BeliefState.CONTRADICTED
            memory.confidence_level = ConfidenceLevel.LOW
            return True

        memory.belief_state = BeliefState.STALE
        memory.confidence_level = _downgraded_confidence(memory.confidence_level)
        return True


def retrieve_candidates(
    parsed_task: ParsedTask,
    memory_store: MemoryStore,
    runtime_state: RuntimeState,
    allow_revisit: bool = False,
) -> list[dict[str, Any]]:
    """Structured retrieval first with task-negative-evidence exclusion."""
    target = parsed_task.target_object
    memories = memory_store.retrieve_object_memory(target.category, target.aliases)
    excluded = _excluded_location_keys(runtime_state, target.category)
    hint = _normalize(parsed_task.explicit_location_hint)
    normalized_aliases = {_normalize(item) for item in target.aliases if item}

    ranked: list[tuple[float, float, ObjectMemory, list[str]]] = []
    for memory in memories:
        location_key = anchor_to_location_key(memory.anchor)
        if not allow_revisit and location_key in excluded:
            continue

        score = 0.0
        reasons: list[str] = ["category_match"]

        if _memory_alias_hit(memory, normalized_aliases):
            score += 4.0
            reasons.append("alias_match")

        if hint and _location_matches_hint(memory.anchor, hint):
            score += 3.0
            reasons.append("location_hint_match")

        confidence_score = _confidence_score(memory.confidence_level)
        score += confidence_score
        reasons.append(f"confidence_{memory.confidence_level.value}")

        timestamp_score = (
            memory.last_confirmed_at.timestamp()
            if memory.last_confirmed_at
            else float("-inf")
        )
        ranked.append((score, timestamp_score, memory, reasons))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].memory_id))
    return [
        {
            "memory_id": memory.memory_id,
            "object_category": memory.object_category,
            "anchor": memory.anchor.model_dump(),
            "confidence_level": memory.confidence_level.value,
            "score": score,
            "reasons": reasons,
        }
        for score, _, memory, reasons in ranked
    ]


def _excluded_location_keys(runtime_state: RuntimeState, target_category: str) -> set[str]:
    normalized_target = _normalize(target_category)
    excluded: set[str] = set()
    for evidence in runtime_state.task_negative_evidence:
        status = evidence.status or evidence.reason
        if status != "searched_not_found":
            continue
        if evidence.object_category and _normalize(evidence.object_category) != normalized_target:
            continue
        if evidence.location_key:
            excluded.add(evidence.location_key)
            continue
        if evidence.anchor is not None:
            excluded.add(anchor_to_location_key(evidence.anchor))
    return excluded


def _confidence_score(confidence: ConfidenceLevel) -> float:
    mapping = {
        ConfidenceLevel.HIGH: 3.0,
        ConfidenceLevel.MEDIUM: 2.0,
        ConfidenceLevel.LOW: 1.0,
    }
    return mapping[confidence]


def _default_confidence_for_source(evidence_source: EvidenceSource) -> ConfidenceLevel:
    if evidence_source == EvidenceSource.DIRECT_OBSERVATION:
        return ConfidenceLevel.HIGH
    if evidence_source == EvidenceSource.USER_PROVIDED:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _downgraded_confidence(level: ConfidenceLevel) -> ConfidenceLevel:
    if level == ConfidenceLevel.HIGH:
        return ConfidenceLevel.MEDIUM
    if level == ConfidenceLevel.MEDIUM:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.LOW


def _location_matches_hint(anchor: Anchor, normalized_hint: str) -> bool:
    haystacks = [
        anchor.room_id,
        anchor.anchor_id,
        anchor.anchor_type,
        anchor.display_text or "",
    ]
    return any(normalized_hint in _normalize(item) for item in haystacks)


def _memory_alias_hit(memory: ObjectMemory, normalized_aliases: set[str]) -> bool:
    if not normalized_aliases:
        return False
    memory_terms = {_normalize(item) for item in memory.aliases}
    memory_terms.add(_normalize(memory.object_category))
    return not memory_terms.isdisjoint(normalized_aliases)


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


__all__ = [
    "MemoryStore",
    "anchor_to_location_key",
    "retrieve_candidates",
]
