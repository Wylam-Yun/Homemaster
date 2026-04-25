from __future__ import annotations

import json
from pathlib import Path

from homemaster.contracts import MemoryRetrievalHit, MemoryRetrievalResult, TaskCard
from homemaster.grounding import assess_hit_reliability, select_grounded_target


def _stage_03_actual(case_name: str) -> dict[str, object]:
    path = Path("tests/homemaster/llm_cases/stage_03") / case_name / "actual.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _task_card_from_stage_03(case_name: str) -> TaskCard:
    return TaskCard.model_validate(_stage_03_actual(case_name)["task_card"])


def _memory_result_from_stage_03(case_name: str) -> MemoryRetrievalResult:
    return MemoryRetrievalResult.model_validate(_stage_03_actual(case_name)["memory_result"])


def _world(case_name: str) -> dict[str, object]:
    path = Path("data/scenarios") / case_name / "world.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_hit(hit: MemoryRetrievalHit, **updates: object) -> MemoryRetrievalHit:
    data = hit.model_dump(mode="json")
    data.update(updates)
    metadata = dict(data.get("canonical_metadata") or {})
    for key in (
        "memory_id",
        "room_id",
        "anchor_id",
        "anchor_type",
        "display_text",
        "viewpoint_id",
        "confidence_level",
        "belief_state",
    ):
        if key in updates:
            metadata[key] = updates[key]
    data["canonical_metadata"] = metadata
    return MemoryRetrievalHit.model_validate(data)


def test_ground_cup_target() -> None:
    task_card = _task_card_from_stage_03("cup_object_memory_rag")
    memory_result = _memory_result_from_stage_03("cup_object_memory_rag")

    result = select_grounded_target(task_card, memory_result, _world("fetch_cup_retry"))

    assert result.grounding_status == "grounded"
    assert result.selected_target is not None
    assert result.selected_target.memory_id == "mem-cup-1"
    assert result.selected_target.room_id == "kitchen"
    assert result.selected_target.anchor_id == "anchor_kitchen_table_1"
    assert result.selected_target.viewpoint_id == "kitchen_table_viewpoint"


def test_ground_medicine_target() -> None:
    task_card = _task_card_from_stage_03("medicine_object_memory_rag")
    memory_result = _memory_result_from_stage_03("medicine_object_memory_rag")

    result = select_grounded_target(
        task_card,
        memory_result,
        _world("check_medicine_success"),
    )

    assert result.grounding_status == "grounded"
    assert result.selected_target is not None
    assert result.selected_target.memory_id == "mem-medicine-1"
    assert result.selected_target.evidence["source"] == "canonical_metadata"


def test_skip_invalid_viewpoint_hit() -> None:
    task_card = _task_card_from_stage_03("cup_object_memory_rag")
    memory_result = _memory_result_from_stage_03("cup_object_memory_rag")
    invalid_first = _copy_hit(memory_result.hits[0], viewpoint_id="missing_viewpoint")
    valid_second = _copy_hit(
        memory_result.hits[1],
        room_id="kitchen",
        anchor_id="anchor_kitchen_table_1",
        viewpoint_id="kitchen_table_viewpoint",
        display_text="厨房餐桌",
        confidence_level="high",
        belief_state="confirmed",
    )
    patched_result = memory_result.model_copy(
        update={"hits": [invalid_first, valid_second]}
    )

    result = select_grounded_target(task_card, patched_result, _world("fetch_cup_retry"))

    assert result.selected_target is not None
    assert result.selected_target.memory_id == "mem-cup-2"
    assert result.rejected_hits[0].memory_id == "mem-cup-1"
    assert "viewpoint_not_found" in result.assessments[0].reasons


def test_reject_missing_execution_fields() -> None:
    task_card = _task_card_from_stage_03("cup_object_memory_rag")
    hit = _copy_hit(_memory_result_from_stage_03("cup_object_memory_rag").hits[0], anchor_id=None)

    assessment = assess_hit_reliability(task_card, hit, _world("fetch_cup_retry"), set())

    assert assessment.status == "unreliable"
    assert "missing_anchor_id" in assessment.reasons


def test_location_conflict_becomes_weak_lead() -> None:
    task_card = _task_card_from_stage_03("cup_object_memory_rag")
    hit = _copy_hit(
        _memory_result_from_stage_03("cup_object_memory_rag").hits[0],
        room_id="living_room",
        anchor_id="anchor_living_side_table_1",
        viewpoint_id="living_side_table_viewpoint",
        display_text="客厅边桌",
        confidence_level="medium",
        belief_state="confirmed",
    )

    assessment = assess_hit_reliability(task_card, hit, _world("object_not_found"), set())

    assert assessment.status == "weak_lead"
    assert "location_conflict" in assessment.reasons
    assert assessment.needs_exploratory_search is True


def test_low_confidence_or_stale_becomes_weak_lead() -> None:
    task_card = _task_card_from_stage_03("cup_object_memory_rag").model_copy(
        update={"location_hint": None}
    )
    base_hit = _memory_result_from_stage_03("cup_object_memory_rag").hits[0]
    low_confidence = _copy_hit(base_hit, confidence_level="low")
    stale = _copy_hit(base_hit, belief_state="stale")

    low_assessment = assess_hit_reliability(
        task_card,
        low_confidence,
        _world("fetch_cup_retry"),
        set(),
    )
    stale_assessment = assess_hit_reliability(task_card, stale, _world("fetch_cup_retry"), set())

    assert low_assessment.status == "weak_lead"
    assert "low_confidence" in low_assessment.reasons
    assert stale_assessment.status == "weak_lead"
    assert "stale_belief" in stale_assessment.reasons


def test_dense_only_without_metadata_target_match_is_unreliable() -> None:
    task_card = _task_card_from_stage_03("cup_object_memory_rag")
    hit = _copy_hit(
        _memory_result_from_stage_03("cup_object_memory_rag").hits[0],
        object_category="remote",
        aliases=["遥控器"],
        ranking_reasons=["dense_rank=1"],
        metadata_score=0.0,
    )

    assessment = assess_hit_reliability(task_card, hit, _world("fetch_cup_retry"), set())

    assert assessment.status == "unreliable"
    assert "target_metadata_mismatch" in assessment.reasons


def test_negative_evidence_excluded_hit_never_selected() -> None:
    task_card = _task_card_from_stage_03("negative_evidence_excludes_location")
    memory_result = _memory_result_from_stage_03("negative_evidence_excludes_location")

    result = select_grounded_target(task_card, memory_result, _world("object_not_found"))

    assert all(hit.memory_id != "mem-cup-1" for hit in result.rejected_hits)
    assert result.selected_target is None
    assert result.grounding_status == "ungrounded"
    assert any(assessment.status == "weak_lead" for assessment in result.assessments)
