from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import homemaster.contracts as contracts
from homemaster.contracts import (
    ExecutionState,
    FailureRecord,
    GroundedMemoryTarget,
    MemoryRetrievalHit,
    MemoryRetrievalQuery,
    MemoryRetrievalResult,
    ModuleExecutionResult,
    OrchestrationPlan,
    PlanningContext,
    RecoveryDecision,
    StepDecision,
    Subtask,
    SubtaskRuntimeState,
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


def test_stage_05_contracts_serialize_with_intent_skill_and_failure_state() -> None:
    subtask = Subtask(
        id="find_cup",
        intent="找到水杯",
        target_object="水杯",
        room_hint="厨房",
        anchor_hint="餐桌",
        success_criteria=["观察到水杯"],
    )
    plan = OrchestrationPlan(goal="取水杯", subtasks=[subtask], confidence=0.82)
    decision = StepDecision(
        subtask_id="find_cup",
        selected_skill="navigation",
        skill_input={"goal_type": "find_object", "target_object": "水杯"},
        expected_result="看到水杯",
        reason="先找到目标物",
    )
    result = ModuleExecutionResult(
        skill="operation",
        status="success",
        skill_output={"vla_instruction": "拿起水杯", "planned_atomic_actions": ["grasp"]},
        observation={"held_object": "水杯"},
    )
    verification = contracts.VerificationResult(
        scope="subtask",
        passed=False,
        missing_evidence=["未确认水杯已拿稳"],
        failed_reason="未拿起水杯",
        confidence=0.4,
    )
    failure = FailureRecord(
        failure_id="failure-1",
        subtask_id="pick_cup",
        subtask_intent="拿起水杯",
        skill="operation",
        failure_type="verification_failed",
        failed_reason="未拿起水杯",
        skill_input={"target_object": "水杯"},
        skill_output=result.skill_output,
        verification_result=verification,
        observation=result.observation,
        negative_evidence=[{"reason": "operation_not_verified"}],
        retry_count=1,
        created_at="2026-04-26T00:00:00Z",
        event_memory_candidate={"type": "failed_operation"},
    )
    state = ExecutionState(
        task_status="running",
        current_subtask_id="find_cup",
        subtasks=[
            SubtaskRuntimeState(subtask_id="find_cup", status="running"),
            SubtaskRuntimeState(
                subtask_id="pick_cup", status="pending", depends_on=["find_cup"]
            ),
        ],
        failure_record_ids=["failure-1"],
        negative_evidence=[{"memory_id": "mem-cup-1", "reason": "not_visible"}],
    )

    assert OrchestrationPlan.model_validate_json(plan.model_dump_json()) == plan
    assert StepDecision.model_validate_json(decision.model_dump_json()) == decision
    assert ModuleExecutionResult.model_validate_json(result.model_dump_json()) == result
    assert FailureRecord.model_validate_json(failure.model_dump_json()) == failure
    assert ExecutionState.model_validate_json(state.model_dump_json()) == state


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


def test_orchestration_plan_rejects_mimo_generated_selected_target() -> None:
    target = GroundedMemoryTarget(
        memory_id="mem-cup-1",
        room_id="kitchen",
        anchor_id="anchor_kitchen_table_1",
        viewpoint_id="kitchen_table_viewpoint",
    )

    with pytest.raises(ValidationError):
        OrchestrationPlan(goal="取水杯", selected_target=target)


def test_step_decision_rejects_legacy_module_fields_and_manual_verification() -> None:
    with pytest.raises(ValidationError):
        StepDecision(subtask_id="find_cup", module="navigate", module_input={})

    with pytest.raises(ValidationError):
        StepDecision(
            subtask_id="find_cup",
            selected_skill="navigation",
            skill_input={"goal_type": "find_object", "target_object": "水杯"},
            verify_after=False,
        )

    with pytest.raises(ValidationError):
        StepDecision(
            subtask_id="verify_cup",
            selected_skill="verification",
            skill_input={"scope": "subtask"},
        )


def test_module_execution_result_rejects_legacy_module_field() -> None:
    with pytest.raises(ValidationError):
        ModuleExecutionResult(module="operate", status="success")


def test_recovery_decision_rejects_switch_target_and_next_target_id() -> None:
    with pytest.raises(ValidationError):
        RecoveryDecision(action="switch_target")

    with pytest.raises(ValidationError):
        RecoveryDecision(action="retrieve_again", next_target_id="mem-cup-2")


def test_homemaster_contracts_do_not_expose_embodied_action_plan() -> None:
    assert not hasattr(contracts, "EmbodiedActionPlan")


def test_old_task_brain_candidate_baseline_still_imports() -> None:
    from task_brain.domain import RuntimeState

    state = RuntimeState(selected_candidate_id="mem-cup-1")

    assert state.selected_candidate_id == "mem-cup-1"
