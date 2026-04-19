from __future__ import annotations

from task_brain.domain import (
    ConfidenceLevel,
    EvidenceSource,
    ParsedTask,
    RuntimeState,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
)
from task_brain.memory import MemoryStore, retrieve_candidates


def _build_memory_store() -> MemoryStore:
    return MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-medicine-1",
                    "object_category": "medicine_box",
                    "aliases": ["药盒", "药箱"],
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "kitchen_cabinet_1",
                        "anchor_type": "cabinet",
                        "viewpoint_id": "kitchen_cabinet_viewpoint",
                        "display_text": "厨房药柜",
                    },
                    "evidence_source": "direct_observation",
                    "confidence_level": "high",
                    "last_confirmed_at": "2026-04-18T12:00:00Z",
                    "belief_state": "confirmed",
                },
                {
                    "memory_id": "mem-medicine-2",
                    "object_category": "medicine_box",
                    "aliases": ["药盒"],
                    "anchor": {
                        "room_id": "bedroom",
                        "anchor_id": "bedside_table_1",
                        "anchor_type": "table",
                        "viewpoint_id": "bedside_table_viewpoint",
                        "display_text": "卧室床头柜",
                    },
                    "evidence_source": "inferred_experience",
                    "confidence_level": "medium",
                    "last_confirmed_at": "2026-04-15T12:00:00Z",
                    "belief_state": "confirmed",
                },
                {
                    "memory_id": "mem-cup-1",
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "kitchen_table_1",
                        "anchor_type": "table",
                        "viewpoint_id": "kitchen_table_viewpoint",
                        "display_text": "厨房餐桌",
                    },
                    "evidence_source": "direct_observation",
                    "confidence_level": "high",
                    "last_confirmed_at": "2026-04-18T10:00:00Z",
                    "belief_state": "confirmed",
                },
            ]
        }
    )


def _build_parsed_task(*, location_hint: str | None = None) -> ParsedTask:
    return ParsedTask(
        intent=TaskIntent.CHECK_OBJECT_PRESENCE,
        target_object=TargetObject(
            category="medicine_box",
            aliases=["药盒"],
        ),
        explicit_location_hint=location_hint,
    )


def test_object_memory_uses_structured_anchor() -> None:
    store = _build_memory_store()
    memory = store.get_object_memory("mem-medicine-1")
    assert memory is not None
    assert memory.anchor.room_id == "kitchen"
    assert memory.anchor.anchor_id == "kitchen_cabinet_1"
    assert memory.anchor.anchor_type == "cabinet"


def test_object_category_filter_returns_candidates() -> None:
    store = _build_memory_store()
    candidates = retrieve_candidates(
        parsed_task=_build_parsed_task(location_hint="厨房"),
        memory_store=store,
        runtime_state=RuntimeState(),
    )

    assert [item["memory_id"] for item in candidates] == ["mem-medicine-1", "mem-medicine-2"]
    assert all(item["object_category"] == "medicine_box" for item in candidates)
    assert all("score" in item and "reasons" in item for item in candidates)
    assert "location_hint_match" in candidates[0]["reasons"]


def test_task_negative_evidence_excludes_searched_location() -> None:
    store = _build_memory_store()
    runtime_state = RuntimeState(
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-1",
                location_key="kitchen:kitchen_cabinet_1",
                status="searched_not_found",
                object_category="medicine_box",
            )
        ]
    )

    candidates = retrieve_candidates(
        parsed_task=_build_parsed_task(),
        memory_store=store,
        runtime_state=runtime_state,
    )
    assert [item["memory_id"] for item in candidates] == ["mem-medicine-2"]


def test_allow_revisit_re_includes_excluded_location() -> None:
    store = _build_memory_store()
    runtime_state = RuntimeState(
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-2",
                location_key="kitchen:kitchen_cabinet_1",
                status="searched_not_found",
                object_category="medicine_box",
            )
        ]
    )

    candidates = retrieve_candidates(
        parsed_task=_build_parsed_task(),
        memory_store=store,
        runtime_state=runtime_state,
        allow_revisit=True,
    )
    assert [item["memory_id"] for item in candidates] == ["mem-medicine-1", "mem-medicine-2"]


def test_negative_evidence_does_not_pollute_long_term_object_memory() -> None:
    store = _build_memory_store()
    before_ids = [item.memory_id for item in store.dump_object_memory()]

    retrieve_candidates(
        parsed_task=_build_parsed_task(),
        memory_store=store,
        runtime_state=RuntimeState(
            task_negative_evidence=[
                TaskNegativeEvidence(
                    task_request_id="req-3",
                    location_key="kitchen:kitchen_cabinet_1",
                    status="searched_not_found",
                    object_category="medicine_box",
                    evidence={"source": "verify_object_presence"},
                )
            ]
        ),
    )

    after = store.dump_object_memory()
    assert [item.memory_id for item in after] == before_ids
    assert store.get_object_memory("mem-medicine-1") is not None


def test_direct_observation_maps_to_high_confidence() -> None:
    store = _build_memory_store()
    target = store.get_object_memory("mem-medicine-2")
    assert target is not None
    target.confidence_level = ConfidenceLevel.LOW

    updated = store.update_object_memory_from_verified_observation(
        "mem-medicine-2",
        verified=True,
        evidence_source=EvidenceSource.DIRECT_OBSERVATION,
        last_observed_state="confirmed_visible",
    )

    refreshed = store.get_object_memory("mem-medicine-2")
    assert updated is True
    assert refreshed is not None
    assert refreshed.confidence_level == ConfidenceLevel.HIGH
    assert refreshed.last_observed_state == "confirmed_visible"
    assert refreshed.last_confirmed_at is not None


def test_only_verified_observation_can_update_object_memory() -> None:
    store = _build_memory_store()
    target = store.get_object_memory("mem-medicine-2")
    assert target is not None
    original_confirmed_at = target.last_confirmed_at
    original_confidence = target.confidence_level

    updated = store.update_object_memory_from_verified_observation(
        "mem-medicine-2",
        verified=False,
        evidence_source=EvidenceSource.DIRECT_OBSERVATION,
        last_observed_state="planner_guess",
    )

    refreshed = store.get_object_memory("mem-medicine-2")
    assert updated is False
    assert refreshed is not None
    assert refreshed.last_confirmed_at == original_confirmed_at
    assert refreshed.confidence_level == original_confidence
    assert refreshed.last_observed_state is None
