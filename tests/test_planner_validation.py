from __future__ import annotations

import pytest

from task_brain.capabilities import default_capability_registry
from task_brain.context import TaskContext, build_task_context
from task_brain.domain import (
    FailureAnalysis,
    FailureType,
    HighLevelPlan,
    ParsedTask,
    Predicate,
    RuntimeState,
    Subgoal,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
    TaskRequest,
)
from task_brain.planner import DeterministicHighLevelPlanner, PlannerService, PlanValidator


def test_planner_uses_top_candidate_for_check_presence() -> None:
    planner = DeterministicHighLevelPlanner()
    context = _build_context(
        parsed_task=_check_parsed_task(),
        ranked_candidates=[
            {"memory_id": "mem-medicine-1", "score": 10.0},
            {"memory_id": "mem-medicine-2", "score": 8.0},
        ],
    )

    plan = planner.generate(context)

    assert plan.intent == TaskIntent.CHECK_OBJECT_PRESENCE
    assert plan.memory_grounding == ["mem-medicine-1"]
    assert plan.candidate_grounding == ["mem-medicine-1"]
    assert [item.subgoal_type for item in plan.subgoals] == [
        SubgoalType.NAVIGATE,
        SubgoalType.OBSERVE,
        SubgoalType.VERIFY_OBJECT_PRESENCE,
    ]
    assert all(item.target_memory_id == "mem-medicine-1" for item in plan.subgoals)


def test_fetch_plan_contains_required_high_level_subgoals() -> None:
    planner = DeterministicHighLevelPlanner()
    context = _build_context(
        parsed_task=_fetch_parsed_task(),
        ranked_candidates=[{"memory_id": "mem-cup-1", "score": 9.5}],
    )

    plan = planner.generate(context)
    assert plan.intent == TaskIntent.FETCH_OBJECT
    assert [item.subgoal_type for item in plan.subgoals] == [
        SubgoalType.NAVIGATE,
        SubgoalType.OBSERVE,
        SubgoalType.VERIFY_OBJECT_PRESENCE,
        SubgoalType.EMBODIED_MANIPULATION,
        SubgoalType.RETURN_TO_USER,
    ]
    assert any(
        condition.name == "task_goal_satisfied"
        for subgoal in plan.subgoals
        for condition in subgoal.success_conditions
    )


def test_planner_does_not_emit_atomic_actions() -> None:
    planner = DeterministicHighLevelPlanner()
    context = _build_context(
        parsed_task=_fetch_parsed_task(),
        ranked_candidates=[{"memory_id": "mem-cup-1", "score": 9.5}],
    )

    plan = planner.generate(context)
    forbidden = {"move_arm_to_pregrasp", "close_gripper", "open_gripper", "lift"}
    for subgoal in plan.subgoals:
        description = (subgoal.description or "").lower()
        assert all(keyword not in description for keyword in forbidden)


def test_validator_rejects_manipulation_before_presence_verification() -> None:
    validator = PlanValidator()
    context = _build_context(
        parsed_task=_fetch_parsed_task(),
        ranked_candidates=[{"memory_id": "mem-cup-1", "score": 9.5}],
    )
    invalid_plan = HighLevelPlan(
        plan_id="plan-invalid-order",
        intent=TaskIntent.FETCH_OBJECT,
        memory_grounding=["mem-cup-1"],
        candidate_grounding=["mem-cup-1"],
        subgoals=[
            Subgoal(
                subgoal_id="sg-1",
                subgoal_type=SubgoalType.NAVIGATE,
                description="Navigate to candidate location.",
                success_conditions=[
                    Predicate(name="arrived_at_candidate_anchor", args=["mem-cup-1"])
                ],
            ),
            Subgoal(
                subgoal_id="sg-2",
                subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
                description="Manipulate target object.",
                success_conditions=[Predicate(name="object_secured", args=["cup"])],
            ),
            Subgoal(
                subgoal_id="sg-3",
                subgoal_type=SubgoalType.VERIFY_OBJECT_PRESENCE,
                description="Verify object is present.",
                success_conditions=[Predicate(name="object_presence_verified", args=["mem-cup-1"])],
            ),
            Subgoal(
                subgoal_id="sg-4",
                subgoal_type=SubgoalType.RETURN_TO_USER,
                description="Return to user with object.",
                success_conditions=[Predicate(name="task_goal_satisfied", args=["cup"])],
            ),
        ],
    )

    with pytest.raises(ValueError, match="before verify_object_presence"):
        validator.validate(invalid_plan, context)


def test_validator_rejects_missing_memory_or_candidate_grounding() -> None:
    planner = DeterministicHighLevelPlanner()
    validator = PlanValidator()
    context = _build_context(
        parsed_task=_fetch_parsed_task(),
        ranked_candidates=[{"memory_id": "mem-cup-1", "score": 9.5}],
    )
    plan = planner.generate(context)
    invalid_plan = plan.model_copy(update={"memory_grounding": [], "candidate_grounding": []})

    with pytest.raises(
        ValueError,
        match="must include both memory_grounding and candidate_grounding",
    ):
        validator.validate(invalid_plan, context)


def test_validator_rejects_unsupported_capability_requirements() -> None:
    planner = DeterministicHighLevelPlanner()
    validator = PlanValidator()
    limited_registry = default_capability_registry()
    limited_registry.pop("mock_vln.navigate")

    context = _build_context(
        parsed_task=_check_parsed_task(),
        ranked_candidates=[{"memory_id": "mem-medicine-1", "score": 10.0}],
        capability_registry=limited_registry,
    )
    plan = planner.generate(context)

    with pytest.raises(ValueError, match="missing capability 'mock_vln.navigate'"):
        validator.validate(plan, context)


def test_plan_from_request_falls_back_to_ask_clarification_on_parse_error() -> None:
    service = PlannerService()
    request = TaskRequest(source="cli", user_id="u-parse-fallback", utterance="今天天气怎么样")

    plan = service.plan_from_request(
        request=request,
        runtime_state=RuntimeState(),
    )

    assert plan.intent == TaskIntent.CHECK_OBJECT_PRESENCE
    assert len(plan.subgoals) == 1
    assert plan.subgoals[0].subgoal_type == SubgoalType.ASK_CLARIFICATION
    assert plan.memory_grounding == []
    assert plan.candidate_grounding == []
    assert plan.notes is not None
    assert "parse_error=" in plan.notes


def test_replan_context_includes_negative_evidence_and_recent_failure() -> None:
    service = PlannerService()
    runtime_state = RuntimeState(
        retry_budget=1,
        recent_failure_analysis=FailureAnalysis(
            failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
            reason="searched_not_found",
            selected_candidate_id="mem-medicine-1",
        ),
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-1",
                location_key="kitchen:kitchen_cabinet_1",
                status="searched_not_found",
                object_category="medicine_box",
            )
        ],
        candidate_exclusion_state={"kitchen:kitchen_cabinet_1": "searched_not_found"},
    )

    plan = service.plan_from_request(
        request=TaskRequest(
            source="cli",
            user_id="u-replan",
            utterance="帮我看看药盒还在不在",
        ),
        runtime_state=runtime_state,
        ranked_candidates=[],
    )

    assert len(plan.subgoals) == 1
    assert plan.subgoals[0].subgoal_type == SubgoalType.ASK_CLARIFICATION
    assert plan.notes is not None
    assert "negative_evidence=1" in plan.notes
    assert "candidate_exclusions=1" in plan.notes
    assert "recent_failure=object_presence_failure" in plan.notes
    assert "retry_budget=1" in plan.notes


def _check_parsed_task() -> ParsedTask:
    return ParsedTask(
        intent=TaskIntent.CHECK_OBJECT_PRESENCE,
        target_object=TargetObject(category="medicine_box", aliases=["药盒"]),
        requires_navigation=True,
        requires_manipulation=False,
    )


def _fetch_parsed_task() -> ParsedTask:
    return ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"]),
        explicit_location_hint="厨房",
        delivery_target="user",
        requires_navigation=True,
        requires_manipulation=True,
    )


def _build_context(
    *,
    parsed_task: ParsedTask,
    ranked_candidates: list[dict[str, object]],
    capability_registry: dict[str, object] | None = None,
) -> TaskContext:
    return build_task_context(
        request=TaskRequest(source="cli", user_id="u-test", utterance="placeholder"),
        parsed_task=parsed_task,
        runtime_state=RuntimeState(),
        ranked_candidates=ranked_candidates,
        capability_registry=capability_registry,
    )
