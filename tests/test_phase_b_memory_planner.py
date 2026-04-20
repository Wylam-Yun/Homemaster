from __future__ import annotations

from task_brain.context import build_task_context
from task_brain.domain import (
    EmbodiedActionProgress,
    FailureAnalysis,
    FailureType,
    HighLevelPlan,
    HighLevelProgress,
    ParsedTask,
    Predicate,
    RuntimeObjectUpdate,
    RuntimeState,
    Subgoal,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
    TaskRequest,
)
from task_brain.llm import KimiProviderSchemaError
from task_brain.memory import MemoryStore, retrieve_candidates
from task_brain.planner import LLMHighLevelPlanner, PlannerService


def test_category_prior_provides_candidate_when_instance_memory_missing() -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [],
            "category_prior_memory": [
                {
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "candidate_locations": [
                        {
                            "anchor": {
                                "room_id": "kitchen",
                                "anchor_id": "kitchen_table_1",
                                "anchor_type": "table",
                                "viewpoint_id": "kitchen_table_viewpoint",
                            },
                            "prior_rank": 0,
                            "confidence_level": "medium",
                            "reason": "user_habit",
                        }
                    ],
                }
            ],
        }
    )

    parsed_task = ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"]),
        explicit_location_hint="厨房",
        requires_manipulation=True,
    )
    candidates = retrieve_candidates(
        parsed_task=parsed_task,
        memory_store=store,
        runtime_state=RuntimeState(),
    )

    assert len(candidates) == 1
    assert candidates[0]["memory_id"].startswith("prior-cup-")
    assert "category_prior_fill_in" in candidates[0]["reasons"]


def test_episodic_retrieval_boosts_but_does_not_override_negative_evidence() -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-cup-1",
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "kitchen_table_1",
                        "anchor_type": "table",
                        "viewpoint_id": "kitchen_table_viewpoint",
                    },
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                }
            ],
            "category_prior_memory": [
                {
                    "object_category": "cup",
                    "aliases": ["水杯"],
                    "candidate_locations": [
                        {
                            "anchor": {
                                "room_id": "living_room",
                                "anchor_id": "sofa_table_1",
                                "anchor_type": "table",
                                "viewpoint_id": "sofa_table_viewpoint",
                            },
                            "prior_rank": 0,
                            "confidence_level": "low",
                        }
                    ],
                }
            ],
            "episodic_memory": [
                {
                    "object_category": "cup",
                    "location_key": "kitchen:kitchen_table_1",
                    "summary": "recent success in kitchen",
                    "boost": 2.0,
                    "success": True,
                }
            ],
        }
    )

    parsed_task = ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"]),
    )
    runtime_state = RuntimeState(
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-neg-1",
                location_key="kitchen:kitchen_table_1",
                status="searched_not_found",
                object_category="cup",
            )
        ]
    )

    candidates = retrieve_candidates(
        parsed_task=parsed_task,
        memory_store=store,
        runtime_state=runtime_state,
    )

    candidate_ids = [item["memory_id"] for item in candidates]
    assert "mem-cup-1" not in candidate_ids
    assert candidate_ids and candidate_ids[0].startswith("prior-cup-")


def test_invalid_llm_plan_falls_back_to_deterministic_plan() -> None:
    class FailingProvider:
        def generate_plan(self, *, prompt: str) -> HighLevelPlan:  # pragma: no cover - sanity
            raise KimiProviderSchemaError(
                error_type="schema_validation_error",
                message=f"invalid llm output for prompt={len(prompt)}",
            )

        def close(self) -> None:
            return None

    llm_planner = LLMHighLevelPlanner(provider_factory=lambda: FailingProvider())
    service = PlannerService(llm_planner=llm_planner, llm_first=True)

    context = _build_check_context(
        ranked_candidates=[{"memory_id": "mem-medicine-1", "score": 9.0}]
    )
    plan = service.plan(context)

    assert plan.notes == "deterministic_check_presence_plan"
    assert plan.memory_grounding == ["mem-medicine-1"]

    diagnostics = service.last_diagnostics
    assert diagnostics["llm_attempted"] is True
    assert diagnostics["fallback_used"] is True
    assert diagnostics["planner_error_type"] == "schema_validation_error"
    assert "invalid llm output" in diagnostics["planner_error_message"]


def test_llm_plan_cannot_ignore_task_negative_evidence() -> None:
    class IgnoreEvidenceProvider:
        def generate_plan(self, *, prompt: str) -> HighLevelPlan:
            _ = prompt
            return HighLevelPlan(
                plan_id="plan-llm-ignore-1",
                intent=TaskIntent.CHECK_OBJECT_PRESENCE,
                subgoals=[
                    Subgoal(
                        subgoal_id="sg-1",
                        subgoal_type=SubgoalType.NAVIGATE,
                        description="Navigate to excluded place.",
                        candidate_id="mem-excluded",
                        target_memory_id="mem-excluded",
                        success_conditions=[
                            Predicate(name="arrived_at_candidate_anchor", args=["mem-excluded"])
                        ],
                    ),
                    Subgoal(
                        subgoal_id="sg-2",
                        subgoal_type=SubgoalType.OBSERVE,
                        description="Observe excluded place.",
                        candidate_id="mem-excluded",
                        target_memory_id="mem-excluded",
                        success_conditions=[
                            Predicate(name="observation_captured", args=["mem-excluded"])
                        ],
                    ),
                    Subgoal(
                        subgoal_id="sg-3",
                        subgoal_type=SubgoalType.VERIFY_OBJECT_PRESENCE,
                        description="Verify excluded candidate.",
                        candidate_id="mem-excluded",
                        target_memory_id="mem-excluded",
                        success_conditions=[
                            Predicate(name="object_presence_verified", args=["cup", "mem-excluded"])
                        ],
                    ),
                ],
                memory_grounding=["mem-excluded"],
                candidate_grounding=["mem-excluded"],
                notes="llm_ignore_negative_evidence",
            )

        def close(self) -> None:
            return None

    llm_planner = LLMHighLevelPlanner(provider_factory=lambda: IgnoreEvidenceProvider())
    service = PlannerService(llm_planner=llm_planner, llm_first=True)

    context = _build_check_context(
        ranked_candidates=[
            {
                "memory_id": "mem-allowed",
                "anchor": {
                    "room_id": "bedroom",
                    "anchor_id": "bedside_table_1",
                    "anchor_type": "table",
                    "viewpoint_id": "bedside_table_viewpoint",
                },
                "score": 7.0,
            }
        ],
        runtime_state=RuntimeState(
            task_negative_evidence=[
                TaskNegativeEvidence(
                    task_request_id="req-neg-2",
                    location_key="kitchen:kitchen_table_1",
                    status="searched_not_found",
                    object_category="medicine_box",
                )
            ],
            candidate_exclusion_state={"kitchen:kitchen_table_1": "searched_not_found"},
        ),
    )
    plan = service.plan(context)

    assert plan.memory_grounding == ["mem-allowed"]
    assert service.last_diagnostics["fallback_used"] is True
    assert service.last_diagnostics["planner_error_type"] == "ValueError"


def test_replanning_prompt_includes_recent_failure_and_candidate_exclusion() -> None:
    runtime_state = RuntimeState(
        retry_budget=1,
        recent_failure_analysis=FailureAnalysis(
            failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
            reason="searched_not_found",
            selected_candidate_id="mem-medicine-1",
        ),
        candidate_exclusion_state={"kitchen:kitchen_table_1": "searched_not_found"},
        high_level_progress=HighLevelProgress(
            current_subgoal_id="sg-2",
            current_subgoal_type=SubgoalType.OBSERVE,
            completed_subgoal_ids=["sg-1"],
            pending_subgoal_ids=["sg-3"],
            execution_phase="executing",
            replan_count=1,
        ),
        embodied_action_progress=EmbodiedActionProgress(
            active_skill_name="mock_atomic_executor.execute",
            current_action_phase="handover",
        ),
        runtime_object_updates=[
            RuntimeObjectUpdate(
                object_ref="mem-medicine-1",
                source="execution_evidence",
                reason="target_location_changed",
            )
        ],
    )
    context = _build_check_context(
        ranked_candidates=[{"memory_id": "mem-medicine-1", "score": 6.0}],
        runtime_state=runtime_state,
    )

    prompt = LLMHighLevelPlanner._build_prompt(context)

    assert "recent_failure_type" in prompt
    assert "candidate_exclusion_state" in prompt
    assert "high_level_progress" in prompt
    assert "embodied_action_progress" in prompt
    assert "runtime_object_updates" in prompt

    context_dump = context.model_dump()
    assert "task_progress" not in context_dump
    assert "current_object_changes" not in context_dump
    assert "embodied_progress" not in context_dump


def _build_check_context(
    *,
    ranked_candidates: list[dict[str, object]],
    runtime_state: RuntimeState | None = None,
):
    runtime = runtime_state or RuntimeState()
    return build_task_context(
        request=TaskRequest(source="cli", user_id="u-stage15", utterance="帮我看看药盒还在不在"),
        parsed_task=ParsedTask(
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            target_object=TargetObject(category="medicine_box", aliases=["药盒"]),
            requires_navigation=True,
            requires_manipulation=False,
        ),
        runtime_state=runtime,
        ranked_candidates=ranked_candidates,
    )
