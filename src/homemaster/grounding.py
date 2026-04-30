"""Stage 04 grounding reliability checks for memory RAG hits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from homemaster.contracts import (
    GroundedMemoryTarget,
    MemoryRetrievalHit,
    MemoryRetrievalResult,
    TaskCard,
)

ReliabilityStatus = Literal["reliable", "weak_lead", "unreliable"]
GroundingStatus = Literal["grounded", "ungrounded"]


ROOM_HINTS: dict[str, tuple[str, ...]] = {
    "kitchen": ("厨房", "kitchen"),
    "living_room": ("客厅", "living room", "living_room"),
    "pantry": ("储物间", "pantry"),
    "bedroom": ("卧室", "bedroom"),
    "study": ("书房", "study"),
    "entryway": ("门口", "entryway"),
}

ANCHOR_HINTS: dict[str, tuple[str, ...]] = {
    "table": ("桌", "桌子", "table"),
    "cabinet": ("柜", "柜子", "药柜", "橱柜", "cabinet"),
    "shelf": ("搁架", "架子", "shelf"),
    "counter": ("台面", "柜台", "counter"),
    "sofa": ("沙发", "sofa"),
}

SPECIFIC_ANCHOR_WORDS: dict[str, tuple[str, ...]] = {
    "table": ("餐桌", "边桌", "茶几", "书桌", "床头柜", "梳妆台"),
    "shelf": ("置物架", "书架"),
    "counter": ("操作台",),
}


@dataclass(frozen=True)
class ReliabilityAssessment:
    memory_id: str | None
    document_id: str
    status: ReliabilityStatus
    reasons: list[str]
    needs_exploratory_search: bool
    suggested_search_hint: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "document_id": self.document_id,
            "status": self.status,
            "reasons": self.reasons,
            "needs_exploratory_search": self.needs_exploratory_search,
            "suggested_search_hint": self.suggested_search_hint,
        }


@dataclass(frozen=True)
class GroundingResult:
    selected_target: GroundedMemoryTarget | None
    rejected_hits: list[MemoryRetrievalHit]
    assessments: list[ReliabilityAssessment]
    grounding_status: GroundingStatus
    grounding_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_target": (
                self.selected_target.model_dump(mode="json") if self.selected_target else None
            ),
            "rejected_hits": [hit.model_dump(mode="json") for hit in self.rejected_hits],
            "assessments": [assessment.as_dict() for assessment in self.assessments],
            "grounding_status": self.grounding_status,
            "grounding_reason": self.grounding_reason,
        }


def assess_hit_reliability(
    task_card: TaskCard,
    hit: MemoryRetrievalHit,
    world: dict[str, Any],
    excluded_ids: set[str],
) -> ReliabilityAssessment:
    """Classify a memory hit as reliable, weak lead, or unreliable."""

    reasons: list[str] = []
    weak_reasons: list[str] = []
    unreliable_reasons: list[str] = []

    if hit.memory_id and hit.memory_id in excluded_ids:
        unreliable_reasons.append("excluded_by_negative_evidence")
    if hit.invalid_reason:
        unreliable_reasons.append(f"invalid_hit:{hit.invalid_reason}")
    if not hit.executable:
        unreliable_reasons.append("hit_not_executable")

    missing_fields = [
        field
        for field in ("memory_id", "room_id", "anchor_id", "viewpoint_id")
        if not getattr(hit, field)
    ]
    unreliable_reasons.extend(f"missing_{field}" for field in missing_fields)

    viewpoint_exists, anchor_exists, anchor_matches = _validate_world_grounding(hit, world)
    if hit.viewpoint_id and not viewpoint_exists:
        unreliable_reasons.append("viewpoint_not_found")
    if hit.anchor_id and not anchor_exists:
        unreliable_reasons.append("anchor_not_found")
    if hit.anchor_id and hit.viewpoint_id and anchor_exists and not anchor_matches:
        unreliable_reasons.append("anchor_viewpoint_mismatch")

    if not _target_matches(task_card, hit):
        unreliable_reasons.append("target_metadata_mismatch")

    if _location_conflicts(task_card, hit):
        weak_reasons.append("location_conflict")
    if _anchor_hint_conflicts(task_card, hit):
        weak_reasons.append("anchor_hint_conflict")

    if (hit.confidence_level or "").casefold() == "low":
        weak_reasons.append("low_confidence")
    if (hit.belief_state or "").casefold() == "stale":
        weak_reasons.append("stale_belief")

    if unreliable_reasons:
        status: ReliabilityStatus = "unreliable"
        reasons = unreliable_reasons + weak_reasons
    elif weak_reasons:
        status = "weak_lead"
        reasons = weak_reasons
    else:
        status = "reliable"
        reasons = ["reliable_execution_memory"]

    return ReliabilityAssessment(
        memory_id=hit.memory_id,
        document_id=hit.document_id,
        status=status,
        reasons=reasons,
        needs_exploratory_search=status != "reliable",
        suggested_search_hint=_suggested_search_hint(task_card, hit, status),
    )


def select_grounded_target(
    task_card: TaskCard,
    memory_result: MemoryRetrievalResult,
    world: dict[str, Any],
) -> GroundingResult:
    """Select the first reliable executable hit and turn it into a grounded target."""

    excluded_ids = {hit.memory_id for hit in memory_result.excluded if hit.memory_id}
    assessments: list[ReliabilityAssessment] = []
    rejected_hits: list[MemoryRetrievalHit] = []

    for hit in memory_result.hits:
        assessment = assess_hit_reliability(task_card, hit, world, excluded_ids)
        assessments.append(assessment)
        if assessment.status == "reliable":
            return GroundingResult(
                selected_target=_target_from_hit(hit, assessment),
                rejected_hits=rejected_hits,
                assessments=assessments,
                grounding_status="grounded",
                grounding_reason="selected first reliable executable memory hit",
            )
        rejected_hits.append(hit)

    if not memory_result.hits:
        reason = "no memory hits available; planner should explore/search"
    elif any(assessment.status == "weak_lead" for assessment in assessments):
        reason = "only weak memory leads available; planner should explore/search"
    else:
        reason = "no reliable executable memory hit; planner should explore/search"
    return GroundingResult(
        selected_target=None,
        rejected_hits=rejected_hits,
        assessments=assessments,
        grounding_status="ungrounded",
        grounding_reason=reason,
    )


def _target_from_hit(
    hit: MemoryRetrievalHit,
    assessment: ReliabilityAssessment,
) -> GroundedMemoryTarget:
    return GroundedMemoryTarget(
        memory_id=str(hit.memory_id),
        room_id=str(hit.room_id),
        anchor_id=str(hit.anchor_id),
        viewpoint_id=str(hit.viewpoint_id),
        display_text=hit.display_text,
        evidence={
            "source": "canonical_metadata",
            "document_id": hit.document_id,
            "final_score": hit.final_score,
            "ranking_reasons": hit.ranking_reasons,
            "reliability": assessment.as_dict(),
            "canonical_metadata": hit.canonical_metadata,
        },
        executable=True,
    )


def _validate_world_grounding(
    hit: MemoryRetrievalHit,
    world: dict[str, Any],
) -> tuple[bool, bool, bool]:
    viewpoints = world.get("viewpoints") if isinstance(world.get("viewpoints"), dict) else {}
    furniture = world.get("furniture") if isinstance(world.get("furniture"), list) else []
    viewpoint_exists = bool(hit.viewpoint_id and hit.viewpoint_id in viewpoints)
    matching_anchor = None
    for item in furniture:
        if isinstance(item, dict) and item.get("anchor_id") == hit.anchor_id:
            matching_anchor = item
            break
    anchor_exists = matching_anchor is not None
    anchor_matches = bool(
        matching_anchor is not None
        and hit.viewpoint_id
        and matching_anchor.get("viewpoint_id") == hit.viewpoint_id
    )
    return viewpoint_exists, anchor_exists, anchor_matches


def _target_matches(task_card: TaskCard, hit: MemoryRetrievalHit) -> bool:
    target = task_card.target.casefold()
    object_category = (hit.object_category or "").casefold()
    aliases = [alias.casefold() for alias in hit.aliases]
    ranking_reasons = set(hit.ranking_reasons)
    if "metadata_target_alias_match" in ranking_reasons:
        return True
    if "metadata_target_category_match" in ranking_reasons:
        return True
    if object_category and (object_category in target or target in object_category):
        return True
    return any(alias and (alias in target or target in alias) for alias in aliases)


def _location_conflicts(task_card: TaskCard, hit: MemoryRetrievalHit) -> bool:
    expected_room = _expected_room_from_hint(task_card.location_hint)
    if expected_room is None:
        return False
    return hit.room_id != expected_room


def _anchor_hint_conflicts(task_card: TaskCard, hit: MemoryRetrievalHit) -> bool:
    expected_anchor = _expected_anchor_from_hint(task_card.location_hint)
    if expected_anchor is None:
        return False
    hint = (task_card.location_hint or "").casefold()
    hit_anchor = _hit_anchor_text(hit)
    hit_display = _hit_display_text(hit)

    # If hint contains a specific furniture word (e.g. "茶几"), require that
    # word in the hit's display_text — broad anchor_type match is not enough.
    specific_words = SPECIFIC_ANCHOR_WORDS.get(expected_anchor, ())
    specific_in_hint = [w for w in specific_words if w.casefold() in hint]
    if specific_in_hint:
        return not any(w.casefold() in hit_display for w in specific_in_hint)

    # Otherwise fall back to broad anchor_type matching.
    hint_terms = ANCHOR_HINTS.get(expected_anchor, ())
    return not any(term.casefold() in hit_anchor for term in hint_terms)


def _expected_room_from_hint(location_hint: str | None) -> str | None:
    if not location_hint:
        return None
    hint = location_hint.casefold()
    for room_id, terms in ROOM_HINTS.items():
        if any(term.casefold() in hint for term in terms):
            return room_id
    return None


def _expected_anchor_from_hint(location_hint: str | None) -> str | None:
    if not location_hint:
        return None
    hint = location_hint.casefold()
    for anchor_type, terms in ANCHOR_HINTS.items():
        if any(term.casefold() in hint for term in terms):
            return anchor_type
    return None


def _hit_anchor_text(hit: MemoryRetrievalHit) -> str:
    return " ".join(
        str(value or "").casefold()
        for value in (
            hit.anchor_type,
            hit.anchor_id,
            hit.display_text,
            hit.canonical_metadata.get("anchor_type"),
            hit.canonical_metadata.get("display_text"),
        )
    )


def _hit_display_text(hit: MemoryRetrievalHit) -> str:
    """Extract display_text fields only (no anchor_type/anchor_id)."""
    return " ".join(
        str(value or "").casefold()
        for value in (
            hit.display_text,
            hit.canonical_metadata.get("display_text"),
        )
    )


def _suggested_search_hint(
    task_card: TaskCard,
    hit: MemoryRetrievalHit,
    status: ReliabilityStatus,
) -> str | None:
    if status == "reliable":
        return None
    parts = [task_card.target]
    if task_card.location_hint:
        parts.append(task_card.location_hint)
    elif hit.display_text:
        parts.append(hit.display_text)
    return " ".join(part for part in parts if part)
