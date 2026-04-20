"""Long-term object memory and task-scoped retrieval utilities for Phase A."""

from __future__ import annotations

import json
import re
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

    def add_object_memory(self, memory: ObjectMemory) -> None:
        """Insert one long-term memory record."""
        self._object_memories.append(memory)
        self._by_id[memory.memory_id] = memory

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


def reconcile_memory_after_task(
    *,
    parsed_task: ParsedTask,
    runtime_state: RuntimeState,
    memory_store: MemoryStore,
    final_status: str,
    latest_execution_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile long-term object memory using verified task evidence only."""
    _ = latest_execution_result

    target_category = parsed_task.target_object.category
    normalized_target = _normalize(target_category)
    summary: dict[str, Any] = {
        "verified": False,
        "updated": 0,
        "created": 0,
        "stale": 0,
        "contradicted": 0,
        "skipped_runtime_updates": 0,
        "updated_memory_ids": [],
        "created_memory_ids": [],
        "stale_memory_ids": [],
        "contradicted_memory_ids": [],
        "skipped_reasons": [],
    }

    updated_ids: set[str] = set()
    created_ids: set[str] = set()
    stale_ids: set[str] = set()
    contradicted_ids: set[str] = set()
    skipped_reasons: list[str] = []

    observation = runtime_state.current_observation
    target_observed_objects = _target_observed_objects(observation, normalized_target)
    has_verified_positive = final_status == "success" and bool(target_observed_objects)
    summary["verified"] = has_verified_positive

    positive_memory_ids: set[str] = set()
    positive_location_keys: set[str] = set()
    positive_anchor = _observation_primary_anchor(observation)
    if positive_anchor is not None:
        positive_location_keys.add(anchor_to_location_key(positive_anchor))

    if has_verified_positive:
        for observed in target_observed_objects:
            matched_memory = _match_existing_memory(
                observed_memory_id=observed.get("memory_id"),
                normalized_target_category=normalized_target,
                memory_store=memory_store,
                anchor=positive_anchor,
            )
            if matched_memory is not None:
                resolved_anchor = positive_anchor or matched_memory.anchor
                updated = memory_store.update_object_memory_from_verified_observation(
                    matched_memory.memory_id,
                    verified=True,
                    anchor=resolved_anchor,
                    last_observed_state=observed.get("state_summary"),
                    evidence_source=EvidenceSource.DIRECT_OBSERVATION,
                    confidence_level=ConfidenceLevel.HIGH,
                )
                if updated:
                    updated_ids.add(matched_memory.memory_id)
                    positive_memory_ids.add(matched_memory.memory_id)
                    positive_location_keys.add(anchor_to_location_key(resolved_anchor))
                continue

            if positive_anchor is None:
                skipped_reasons.append("skip_create_missing_anchor")
                continue

            memory_id = _generate_memory_id(
                object_category=target_category,
                observation_object_id=observed.get("observation_object_id"),
                detector_id=observed.get("detector_id"),
                memory_store=memory_store,
            )
            aliases = _unique_nonempty(
                [target_category, *parsed_task.target_object.aliases, *observed.get("aliases", [])]
            )
            new_memory = ObjectMemory(
                memory_id=memory_id,
                object_category=target_category,
                aliases=aliases,
                anchor=positive_anchor,
                last_observed_state=observed.get("state_summary"),
                evidence_source=EvidenceSource.DIRECT_OBSERVATION,
                confidence_level=ConfidenceLevel.HIGH,
                last_confirmed_at=datetime.now(UTC),
                belief_state=BeliefState.CONFIRMED,
            )
            memory_store.add_object_memory(new_memory)
            created_ids.add(memory_id)
            positive_memory_ids.add(memory_id)
            positive_location_keys.add(anchor_to_location_key(positive_anchor))
    else:
        skipped_reasons.append("no_verified_positive_evidence")

    _reconcile_negative_evidence(
        normalized_target_category=normalized_target,
        runtime_state=runtime_state,
        memory_store=memory_store,
        positive_memory_ids=positive_memory_ids,
        positive_location_keys=positive_location_keys,
        stale_ids=stale_ids,
        contradicted_ids=contradicted_ids,
    )

    for runtime_update in runtime_state.runtime_object_updates:
        if runtime_update.object_ref not in positive_memory_ids:
            summary["skipped_runtime_updates"] += 1
            skipped_reasons.append(
                f"runtime_update_without_verified_support:{runtime_update.object_ref}"
            )
            continue
        memory = memory_store.get_object_memory(runtime_update.object_ref)
        if memory is None:
            summary["skipped_runtime_updates"] += 1
            skipped_reasons.append(f"runtime_update_unknown_memory:{runtime_update.object_ref}")
            continue

        updated = memory_store.update_object_memory_from_verified_observation(
            runtime_update.object_ref,
            verified=True,
            anchor=runtime_update.new_anchor or memory.anchor,
            relative_relation=runtime_update.new_relative_relation or memory.relative_relation,
            last_observed_state=runtime_update.reason or "runtime_update_verified",
            evidence_source=EvidenceSource.DIRECT_OBSERVATION,
        )
        if updated:
            updated_ids.add(runtime_update.object_ref)

    summary["updated_memory_ids"] = sorted(updated_ids)
    summary["created_memory_ids"] = sorted(created_ids)
    summary["stale_memory_ids"] = sorted(stale_ids)
    summary["contradicted_memory_ids"] = sorted(contradicted_ids)
    summary["updated"] = len(updated_ids)
    summary["created"] = len(created_ids)
    summary["stale"] = len(stale_ids)
    summary["contradicted"] = len(contradicted_ids)
    summary["skipped_reasons"] = skipped_reasons
    return summary


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


def _target_observed_objects(
    observation: Any,
    normalized_target_category: str,
) -> list[dict[str, Any]]:
    if observation is None:
        return []
    visible_objects = getattr(observation, "visible_objects", [])
    if not isinstance(visible_objects, list):
        return []

    matched: list[dict[str, Any]] = []
    for item in visible_objects:
        category = getattr(item, "category", None)
        if _normalize(category) != normalized_target_category:
            continue
        aliases = getattr(item, "aliases", [])
        matched.append(
            {
                "observation_object_id": getattr(item, "observation_object_id", None),
                "memory_id": getattr(item, "memory_id", None),
                "detector_id": getattr(item, "detector_id", None),
                "state_summary": getattr(item, "state_summary", None),
                "aliases": aliases if isinstance(aliases, list) else [],
            }
        )
    return matched


def _observation_primary_anchor(observation: Any) -> Anchor | None:
    if observation is None:
        return None
    visible_anchors = getattr(observation, "visible_anchors", [])
    if not isinstance(visible_anchors, list) or not visible_anchors:
        return None
    first_anchor = visible_anchors[0]
    return Anchor(
        room_id=str(first_anchor.room_id),
        anchor_id=str(first_anchor.anchor_id),
        anchor_type=str(first_anchor.anchor_type),
        viewpoint_id=first_anchor.viewpoint_id,
        display_text=first_anchor.display_text,
    )


def _match_existing_memory(
    *,
    observed_memory_id: str | None,
    normalized_target_category: str,
    memory_store: MemoryStore,
    anchor: Anchor | None,
) -> ObjectMemory | None:
    if observed_memory_id:
        candidate = memory_store.get_object_memory(observed_memory_id)
        if (
            candidate is not None
            and _normalize(candidate.object_category) == normalized_target_category
        ):
            return candidate

    if anchor is None:
        return None

    location_key = anchor_to_location_key(anchor)
    category_memories = memory_store.retrieve_object_memory(normalized_target_category, [])
    matched = [
        memory
        for memory in category_memories
        if anchor_to_location_key(memory.anchor) == location_key
    ]
    if len(matched) == 1:
        return matched[0]
    return None


def _generate_memory_id(
    *,
    object_category: str,
    observation_object_id: str | None,
    detector_id: str | None,
    memory_store: MemoryStore,
) -> str:
    category_token = _sanitize_memory_token(object_category)
    object_token = _sanitize_memory_token(observation_object_id) or "object"
    base_id = f"mem-{category_token}-{object_token}"
    existing_ids = {item.memory_id for item in memory_store.dump_object_memory()}
    candidate = base_id
    suffix = 2
    while candidate in existing_ids or (detector_id is not None and candidate == detector_id):
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


def _sanitize_memory_token(value: str | None) -> str:
    normalized = _normalize(value)
    if not normalized:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return normalized[:40]


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        output.append(stripped)
    return output


def _reconcile_negative_evidence(
    *,
    normalized_target_category: str,
    runtime_state: RuntimeState,
    memory_store: MemoryStore,
    positive_memory_ids: set[str],
    positive_location_keys: set[str],
    stale_ids: set[str],
    contradicted_ids: set[str],
) -> None:
    if not runtime_state.task_negative_evidence:
        return

    target_memories = memory_store.retrieve_object_memory(normalized_target_category, [])
    for evidence in runtime_state.task_negative_evidence:
        status = evidence.status or evidence.reason
        if status != "searched_not_found":
            continue
        if (
            evidence.object_category
            and _normalize(evidence.object_category) != normalized_target_category
        ):
            continue

        location_key = evidence.location_key
        if not location_key and evidence.anchor is not None:
            location_key = anchor_to_location_key(evidence.anchor)
        if not location_key:
            continue

        for memory in target_memories:
            if memory.memory_id in positive_memory_ids:
                continue
            if anchor_to_location_key(memory.anchor) != location_key:
                continue

            contradicted = bool(
                positive_location_keys and location_key not in positive_location_keys
            )
            updated = memory_store.mark_object_memory_stale_or_contradicted(
                memory.memory_id,
                contradicted=contradicted,
            )
            if not updated:
                continue
            if contradicted:
                contradicted_ids.add(memory.memory_id)
            else:
                stale_ids.add(memory.memory_id)


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
    "reconcile_memory_after_task",
    "retrieve_candidates",
]
