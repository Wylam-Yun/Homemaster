from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import homemaster.contracts as contracts
from homemaster.contracts import (
    GroundedMemoryTarget,
    MemoryRetrievalHit,
    MemoryRetrievalQuery,
    MemoryRetrievalResult,
    OrchestrationPlan,
    PlanningContext,
    RecoveryDecision,
    TaskCard,
    VLMImageInput,
)


def _valid_task_card() -> dict[str, object]:
    return {
        "task_type": "check_presence",
        "target": "药盒",
        "delivery_target": None,
        "location_hint": "桌子那边",
        "success_criteria": ["观察到药盒是否还在桌子附近"],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.88,
    }


def test_task_card_serializes_and_validates_json() -> None:
    task_card = TaskCard.model_validate(_valid_task_card())
    encoded = task_card.model_dump_json()

    decoded = TaskCard.model_validate_json(encoded)

    assert decoded == task_card
    assert json.loads(encoded)["target"] == "药盒"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_type", "inspect_object"),
        ("target", "  "),
        ("success_criteria", []),
        ("confidence", 1.2),
    ],
)
def test_task_card_rejects_invalid_contract_values(field: str, value: object) -> None:
    payload = _valid_task_card()
    payload[field] = value

    with pytest.raises(ValidationError):
        TaskCard.model_validate(payload)


def test_task_card_forbids_extra_fields() -> None:
    payload = _valid_task_card()
    payload["invented_field"] = "should fail"

    with pytest.raises(ValidationError):
        TaskCard.model_validate(payload)


def test_vlm_image_input_defaults_to_disabled_and_serializes() -> None:
    image_input = VLMImageInput()

    assert image_input.enabled is False
    assert VLMImageInput.model_validate_json(image_input.model_dump_json()) == image_input


def test_memory_retrieval_query_and_result_serialize() -> None:
    query = MemoryRetrievalQuery(
        query_text="厨房 水杯 杯子 cup",
        target_category="cup",
        target_aliases=["水杯", "杯子", "cup"],
        location_terms=["厨房", "kitchen"],
    )
    hit = MemoryRetrievalHit(
        document_id="object_memory:mem-cup-1",
        memory_id="mem-cup-1",
        object_category="cup",
        aliases=["水杯", "杯子"],
        room_id="kitchen",
        anchor_id="anchor_kitchen_table_1",
        anchor_type="table",
        display_text="厨房餐桌",
        viewpoint_id="kitchen_table_viewpoint",
        confidence_level="high",
        belief_state="confirmed",
        bm25_score=0.8,
        dense_score=0.7,
        metadata_score=0.2,
        final_score=0.9,
        ranking_reasons=["bm25_alias_match", "dense_match"],
        canonical_metadata={"memory_id": "mem-cup-1"},
        executable=True,
        ranking_stage="bm25_dense_fusion",
    )
    result = MemoryRetrievalResult(
        hits=[hit],
        retrieval_query=query,
        embedding_provider={"name": "MemoryEmbedding", "model": "BAAI/bge-m3"},
        index_snapshot={"document_count": 1},
    )

    encoded = result.model_dump_json()
    decoded = MemoryRetrievalResult.model_validate_json(encoded)

    assert decoded.hits[0].memory_id == "mem-cup-1"
    assert decoded.retrieval_query is not None
    assert decoded.retrieval_query.source_filter == ["object_memory"]


def test_memory_retrieval_query_rejects_non_object_memory_source() -> None:
    with pytest.raises(ValidationError):
        MemoryRetrievalQuery(query_text="水杯", source_filter=["episodic_memory"])


def test_grounded_target_and_planning_context_serialize() -> None:
    task_card = TaskCard.model_validate(_valid_task_card())
    query = MemoryRetrievalQuery(query_text="桌子那边 药盒 medicine_box")
    hit = MemoryRetrievalHit(
        document_id="object_memory:mem-medicine-1",
        memory_id="mem-medicine-1",
        viewpoint_id="living_side_table_viewpoint",
        final_score=0.91,
        executable=True,
    )
    target = GroundedMemoryTarget(
        memory_id="mem-medicine-1",
        room_id="living_room",
        anchor_id="anchor_living_side_table_1",
        viewpoint_id="living_side_table_viewpoint",
        display_text="客厅边桌",
        evidence={"final_score": 0.91},
    )
    context = PlanningContext(
        task_card=task_card,
        retrieval_query=query,
        memory_evidence=MemoryRetrievalResult(hits=[hit], retrieval_query=query),
        selected_target=target,
        world_summary={"rooms": ["living_room"]},
    )

    decoded = PlanningContext.model_validate_json(context.model_dump_json())

    assert decoded.selected_target is not None
    assert decoded.selected_target.viewpoint_id == "living_side_table_viewpoint"


def test_orchestration_and_recovery_accept_new_grounded_target_fields() -> None:
    target = GroundedMemoryTarget(
        memory_id="mem-cup-1",
        room_id="kitchen",
        anchor_id="anchor_kitchen_table_1",
        viewpoint_id="kitchen_table_viewpoint",
    )
    plan = OrchestrationPlan(goal="取水杯", selected_target=target)
    decision = RecoveryDecision(action="switch_target", next_target_id="mem-cup-2")

    assert OrchestrationPlan.model_validate_json(plan.model_dump_json()).selected_target == target
    decoded_decision = RecoveryDecision.model_validate_json(decision.model_dump_json())

    assert decoded_decision.action == "switch_target"


def test_homemaster_contracts_do_not_expose_legacy_candidate_contracts() -> None:
    for name in (
        "ObjectMemorySearchPlan",
        "ObjectMemoryEvidence",
        "Candidate",
        "CandidatePool",
        "CandidateSelection",
    ):
        assert not hasattr(contracts, name)


def test_orchestration_plan_rejects_selected_candidate_id() -> None:
    with pytest.raises(ValidationError):
        OrchestrationPlan(goal="取水杯", selected_candidate_id="candidate_cup_1")


def test_recovery_decision_rejects_switch_candidate() -> None:
    with pytest.raises(ValidationError):
        RecoveryDecision(action="switch_candidate", next_candidate_id="candidate_cup_2")


def test_old_task_brain_candidate_baseline_still_imports() -> None:
    from task_brain.domain import RuntimeState

    state = RuntimeState(selected_candidate_id="mem-cup-1")

    assert state.selected_candidate_id == "mem-cup-1"
