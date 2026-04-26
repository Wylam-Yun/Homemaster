from __future__ import annotations

from homemaster.contracts import (
    ExecutionState,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
    SubtaskRuntimeState,
    TaskCard,
)
from homemaster.executor import (
    StaticStepDecisionProvider,
    execute_stage_05_plan,
)


def _task_card() -> TaskCard:
    return TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["把水杯交给用户"],
        needs_clarification=False,
        clarification_question=None,
        confidence=0.9,
    )


def _context() -> PlanningContext:
    return PlanningContext(
        task_card=_task_card(),
        runtime_state_summary={"grounding_status": "grounded"},
        world_summary={"room_ids": ["kitchen", "living_room"]},
    )


def test_executor_runs_navigation_then_auto_verification_and_updates_state() -> None:
    plan = OrchestrationPlan(
        goal="找到水杯",
        subtasks=[
            Subtask(
                id="find_cup",
                intent="找到水杯",
                target_object="水杯",
                room_hint="厨房",
                success_criteria=["观察到水杯"],
            )
        ],
    )
    result = execute_stage_05_plan(
        _context(),
        plan,
        decision_provider=StaticStepDecisionProvider(
            [
                StepDecision(
                    subtask_id="find_cup",
                    selected_skill="navigation",
                    skill_input={
                        "goal_type": "find_object",
                        "target_object": "水杯",
                        "subtask_id": "find_cup",
                        "subtask_intent": "找到水杯",
                    },
                )
            ]
        ),
    )

    assert result.final_state.task_status == "completed"
    assert result.final_state.target_object_visible is True
    assert result.final_state.completed_subtask_ids == ["find_cup"]
    assert result.verification_results[0].scope == "subtask"
    assert result.failure_records == []


def test_executor_blocks_operation_when_target_not_visible() -> None:
    plan = OrchestrationPlan(
        goal="拿起水杯",
        subtasks=[
            Subtask(
                id="pick_cup",
                intent="拿起水杯",
                target_object="水杯",
                success_criteria=["确认水杯已拿起"],
            )
        ],
    )

    result = execute_stage_05_plan(
        _context(),
        plan,
        initial_state=ExecutionState(
            subtasks=[SubtaskRuntimeState(subtask_id="pick_cup")],
            target_object_visible=False,
        ),
        decision_provider=StaticStepDecisionProvider(
            [
                StepDecision(
                    subtask_id="pick_cup",
                    selected_skill="operation",
                    skill_input={
                        "subtask_id": "pick_cup",
                        "subtask_intent": "拿起水杯",
                        "target_object": "水杯",
                    },
                )
            ]
        ),
    )

    assert result.final_state.task_status == "failed"
    assert result.failure_records[0].failure_type == "precondition_failed"
    assert "target_object_visible" in result.failure_records[0].failed_reason


def test_executor_does_not_advance_when_verification_fails() -> None:
    plan = OrchestrationPlan(
        goal="找到水杯",
        subtasks=[
            Subtask(
                id="find_cup",
                intent="找到水杯",
                target_object="水杯",
                success_criteria=["观察到水杯"],
            )
        ],
    )

    result = execute_stage_05_plan(
        _context(),
        plan,
        decision_provider=StaticStepDecisionProvider(
            [
                StepDecision(
                    subtask_id="find_cup",
                    selected_skill="navigation",
                    skill_input={
                        "goal_type": "find_object",
                        "target_object": "水杯",
                        "subtask_id": "find_cup",
                        "subtask_intent": "找到水杯",
                        "force_no_object": True,
                    },
                )
            ]
        ),
    )

    assert result.final_state.task_status == "failed"
    assert result.final_state.completed_subtask_ids == []
    assert result.failure_records[0].failure_type == "verification_failed"
    assert result.failure_records[0].verification_result is not None


def test_missing_step_decision_is_recorded_as_failure() -> None:
    plan = OrchestrationPlan(
        goal="找到水杯",
        subtasks=[
            Subtask(id="find_cup", intent="找到水杯", success_criteria=["观察到水杯"])
        ],
    )

    result = execute_stage_05_plan(
        _context(),
        plan,
        decision_provider=StaticStepDecisionProvider([]),
    )

    assert result.final_state.task_status == "failed"
    assert result.failure_records[0].failure_type == "precondition_failed"
    assert "no StepDecision available" in result.failure_records[0].failed_reason
