from __future__ import annotations

from homemaster.contracts import (
    ExecutionState,
    FailureRecord,
    GroundedMemoryTarget,
    ModuleExecutionResult,
    OrchestrationPlan,
    PlanningContext,
    Subtask,
    TaskCard,
    VerificationResult,
)
from homemaster.memory_commit import build_evidence_bundle, build_memory_commit_plan


def _task_card() -> TaskCard:
    return TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["水杯交付给用户"],
        needs_clarification=False,
        confidence=0.9,
    )


def _context() -> PlanningContext:
    return PlanningContext(
        task_card=_task_card(),
        selected_target=GroundedMemoryTarget(
            memory_id="mem-cup-1",
            room_id="kitchen",
            anchor_id="anchor_kitchen_table_1",
            viewpoint_id="kitchen_table_viewpoint",
            display_text="厨房餐桌",
        ),
    )


def _plan() -> OrchestrationPlan:
    return OrchestrationPlan(
        goal="取水杯并交付用户",
        subtasks=[
            Subtask(id="find_cup", intent="找到水杯", success_criteria=["看到水杯"]),
            Subtask(
                id="deliver_cup",
                intent="交付水杯给用户",
                target_object="水杯",
                recipient="user",
                depends_on=["find_cup"],
                success_criteria=["水杯已交付用户"],
            ),
        ],
        confidence=0.9,
    )


def test_success_commit_updates_existing_object_memory_and_fact_records() -> None:
    skill_results = [
        ModuleExecutionResult(
            skill="navigation",
            status="success",
            observation={
                "target_object_visible": True,
                "target_object_location": "厨房餐桌",
            },
        ),
        ModuleExecutionResult(
            skill="operation",
            status="success",
            observation={"delivered_object": "水杯", "delivery_complete": True},
        ),
    ]
    verification_results = [
        VerificationResult(
            scope="subtask",
            passed=True,
            verified_facts=["水杯在厨房餐桌被观察到"],
            confidence=0.95,
        ),
        VerificationResult(
            scope="task",
            passed=True,
            verified_facts=["水杯已交付给用户"],
            confidence=0.93,
        ),
    ]
    bundle = build_evidence_bundle(
        task_id="task-success",
        verification_results=verification_results,
        skill_results=skill_results,
        created_at="2026-04-26T00:00:00Z",
    )

    commit = build_memory_commit_plan(
        task_id="task-success",
        task_card=_task_card(),
        planning_context=_context(),
        orchestration_plan=_plan(),
        execution_state=ExecutionState(task_status="completed"),
        evidence_bundle=bundle,
        task_summary=None,
        completed_at="2026-04-26T00:01:00Z",
    )

    assert commit.object_memory_updates[0].memory_id == "mem-cup-1"
    assert commit.object_memory_updates[0].update_type == "confirm"
    assert commit.object_memory_updates[0].updated_fields["belief_state"] == "confirmed"
    assert "mem-cup-1" in commit.index_stale_memory_ids
    fact_types = {write.fact_type for write in commit.fact_memory_writes}
    assert {"object_seen", "delivery_verified"} <= fact_types
    assert all(write.searchable is False for write in commit.fact_memory_writes)
    assert commit.task_record is not None
    assert commit.task_record.result == "success"


def test_object_not_found_commit_writes_scoped_negative_fact_without_new_location() -> None:
    failure = FailureRecord(
        failure_id="failure-1",
        subtask_id="find_cup",
        subtask_intent="找到水杯",
        skill="navigation",
        failure_type="object_not_found",
        failed_reason="厨房餐桌没有观察到水杯",
        negative_evidence=[
            {
                "memory_id": "mem-cup-1",
                "location_key": "kitchen:anchor_kitchen_table_1",
                "reason": "not_visible",
            }
        ],
        created_at="2026-04-26T00:00:00Z",
    )
    bundle = build_evidence_bundle(
        task_id="task-not-found",
        failure_records=[failure],
        created_at="2026-04-26T00:00:00Z",
    )

    commit = build_memory_commit_plan(
        task_id="task-not-found",
        task_card=_task_card(),
        planning_context=_context(),
        orchestration_plan=_plan(),
        execution_state=ExecutionState(task_status="failed"),
        evidence_bundle=bundle,
        task_summary=None,
        completed_at="2026-04-26T00:01:00Z",
    )

    assert commit.object_memory_updates[0].update_type == "mark_stale"
    assert commit.fact_memory_writes[0].fact_type == "object_not_seen"
    assert commit.fact_memory_writes[0].polarity == "negative"
    assert "不能" not in commit.fact_memory_writes[0].text
    assert commit.negative_evidence[0]["failure_record_id"] == "failure-1"
    assert commit.negative_evidence[0]["stale_after"]


def test_operation_failure_writes_event_memory_without_object_update() -> None:
    failure = FailureRecord(
        failure_id="failure-op-1",
        subtask_id="pick_cup",
        subtask_intent="拿起水杯",
        skill="operation",
        failure_type="verification_failed",
        failed_reason="拿起水杯后未通过验证",
        created_at="2026-04-26T00:00:00Z",
    )
    bundle = build_evidence_bundle(
        task_id="task-op-failed",
        failure_records=[failure],
        created_at="2026-04-26T00:00:00Z",
    )

    commit = build_memory_commit_plan(
        task_id="task-op-failed",
        task_card=_task_card(),
        planning_context=_context(),
        orchestration_plan=_plan(),
        execution_state=ExecutionState(task_status="failed"),
        evidence_bundle=bundle,
        task_summary=None,
        completed_at="2026-04-26T00:01:00Z",
    )

    assert commit.object_memory_updates == []
    assert commit.fact_memory_writes[0].fact_type == "operation_failed"
    assert commit.fact_memory_writes[0].polarity == "negative"


def test_model_output_failure_only_creates_task_record() -> None:
    failure = FailureRecord(
        failure_id="failure-model-1",
        failure_type="model_output_invalid",
        failed_reason="Mimo 三次非 JSON",
        created_at="2026-04-26T00:00:00Z",
    )
    bundle = build_evidence_bundle(
        task_id="task-model-failed",
        failure_records=[failure],
        created_at="2026-04-26T00:00:00Z",
    )

    commit = build_memory_commit_plan(
        task_id="task-model-failed",
        task_card=_task_card(),
        planning_context=_context(),
        orchestration_plan=_plan(),
        execution_state=ExecutionState(task_status="failed"),
        evidence_bundle=bundle,
        task_summary=None,
        completed_at="2026-04-26T00:01:00Z",
    )

    assert commit.object_memory_updates == []
    assert commit.fact_memory_writes == []
    assert commit.task_record is not None
    assert commit.task_record.result == "failed"
    assert commit.task_record.failure_record_ids == ["failure-model-1"]
