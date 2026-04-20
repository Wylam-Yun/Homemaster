from __future__ import annotations

import pytest
from pydantic import ValidationError

from task_brain.capabilities import default_capability_registry
from task_brain.context import TaskContext, build_task_context
from task_brain.domain import (
    CapabilitySpec,
    EmbodiedActionProgress,
    HighLevelProgress,
    Observation,
    ObservationSource,
    ParsedTask,
    RobotRuntimeState,
    RuntimeObjectUpdate,
    RuntimeState,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
    TaskRequest,
)
from task_brain.parser import parse_instruction


def test_parser_extracts_check_medicine_task() -> None:
    request = TaskRequest(source="cli", user_id="u1", utterance="帮我看看药盒还在不在")
    parsed = parse_instruction(request)

    assert parsed.intent == TaskIntent.CHECK_OBJECT_PRESENCE
    assert parsed.target_object.category == "medicine_box"
    assert "药盒" in parsed.target_object.aliases
    assert parsed.requires_manipulation is False


def test_parser_extracts_fetch_cup_task() -> None:
    request = TaskRequest(source="cli", user_id="u1", utterance="去厨房把水杯拿给我")
    parsed = parse_instruction(request)

    assert parsed.intent == TaskIntent.FETCH_OBJECT
    assert parsed.target_object.category == "cup"
    assert parsed.explicit_location_hint == "厨房"
    assert parsed.delivery_target == "user"
    assert parsed.requires_manipulation is True


def test_task_context_includes_memory_observation_runtime_and_capabilities() -> None:
    request = TaskRequest(source="cli", user_id="u2", utterance="去厨房把水杯拿给我")
    parsed_task = ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"], attributes=[]),
        explicit_location_hint="厨房",
        delivery_target="user",
        requires_navigation=True,
        requires_manipulation=True,
    )
    observation = Observation(
        observation_id="obs-ctx-1",
        source=ObservationSource.MOCK_WORLD,
        viewpoint_id="kitchen_table_viewpoint",
        room_id="kitchen",
    )
    runtime_state = RuntimeState(
        current_observation=observation,
        robot_runtime_state=RobotRuntimeState(
            viewpoint_id="kitchen_table_viewpoint",
            room_id="kitchen",
        ),
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-ctx-1",
                location_key="kitchen:kitchen_table_1",
                status="searched_not_found",
                object_category="cup",
            )
        ],
    )

    context = build_task_context(
        request=request,
        parsed_task=parsed_task,
        runtime_state=runtime_state,
        ranked_candidates=[{"memory_id": "mem-cup-1", "score": 9.0}],
        object_memory_hits=[{"memory_id": "mem-cup-1"}],
        category_prior_hits=[{"anchor_id": "kitchen_table_1"}],
        recent_episodic_summaries=[{"episode_id": "ep-1", "summary": "cup in kitchen"}],
        adapter_status={"mock_perception": "ok"},
        constraints={"allow_revisit": False},
    )

    assert context.request is request
    assert context.parsed_task is parsed_task
    assert context.current_observation is observation
    assert context.robot_runtime_state is runtime_state.robot_runtime_state
    assert context.ranked_candidates[0]["memory_id"] == "mem-cup-1"
    assert context.object_memory_hits[0]["memory_id"] == "mem-cup-1"
    assert context.category_prior_hits[0]["anchor_id"] == "kitchen_table_1"
    assert context.recent_episodic_summaries[0]["episode_id"] == "ep-1"
    assert "mock_perception.observe" in context.capability_registry
    assert isinstance(context.capability_registry["mock_perception.observe"], CapabilitySpec)
    assert context.capability_registry["mock_perception.observe"].timeout_s == 3.0
    assert context.adapter_status["mock_perception"] == "ok"
    assert context.constraints["allow_revisit"] is False

    assert not hasattr(context, "memory_store")
    assert not hasattr(context, "world")
    assert not hasattr(context, "trace")


def test_task_context_keeps_negative_evidence_for_replanning() -> None:
    request = TaskRequest(source="cli", user_id="u3", utterance="帮我看看药盒还在不在")
    parsed_task = ParsedTask(
        intent=TaskIntent.CHECK_OBJECT_PRESENCE,
        target_object=TargetObject(category="medicine_box", aliases=["药盒"], attributes=[]),
        requires_navigation=True,
        requires_manipulation=False,
    )
    runtime_state = RuntimeState(
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-ctx-2",
                location_key="kitchen:kitchen_cabinet_1",
                status="searched_not_found",
                object_category="medicine_box",
                evidence={"source": "verify_object_presence"},
            )
        ],
        candidate_exclusion_state={"kitchen:kitchen_cabinet_1": "searched_not_found"},
    )

    context = build_task_context(
        request=request,
        parsed_task=parsed_task,
        runtime_state=runtime_state,
    )

    assert len(context.task_negative_evidence) == 1
    assert context.task_negative_evidence[0].location_key == "kitchen:kitchen_cabinet_1"
    assert context.task_negative_evidence[0].status == "searched_not_found"
    assert context.category_prior_hits == []
    assert context.recent_episodic_summaries == []
    assert "mock_perception.observe" in context.capability_registry
    assert context.adapter_status == {}
    assert context.constraints == {}

    with pytest.raises(ValidationError):
        TaskContext(
            request=request,
            parsed_task=parsed_task,
            runtime_state=runtime_state,
            memory_store={"forbidden": True},
        )


def test_parser_rejects_unsupported_instruction() -> None:
    request = TaskRequest(source="cli", user_id="u4", utterance="今天天气怎么样")
    with pytest.raises(ValueError, match="unsupported instruction"):
        parse_instruction(request)


def test_build_task_context_injects_default_registry_when_not_provided() -> None:
    request = TaskRequest(source="cli", user_id="u5", utterance="帮我看看药盒还在不在")
    parsed_task = ParsedTask(
        intent=TaskIntent.CHECK_OBJECT_PRESENCE,
        target_object=TargetObject(category="medicine_box", aliases=["药盒"], attributes=[]),
    )
    runtime_state = RuntimeState()

    context = build_task_context(
        request=request,
        parsed_task=parsed_task,
        runtime_state=runtime_state,
    )

    default_registry = default_capability_registry()
    assert set(context.capability_registry.keys()) == set(default_registry.keys())
    assert "memory.reconcile" in context.capability_registry
    assert context.capability_registry["memory.reconcile"].name == "memory.reconcile"


def test_build_task_context_accepts_validated_registry_override() -> None:
    request = TaskRequest(source="cli", user_id="u6", utterance="去厨房把水杯拿给我")
    parsed_task = ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"], attributes=[]),
    )
    runtime_state = RuntimeState()

    override_registry = {
        "custom.observe": {
            "name": "custom.observe",
            "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"y": {"type": "string"}}},
            "failure_modes": ["timeout"],
            "timeout_s": 1.0,
            "returns_evidence": True,
        }
    }
    context = build_task_context(
        request=request,
        parsed_task=parsed_task,
        runtime_state=runtime_state,
        capability_registry=override_registry,
    )
    assert list(context.capability_registry.keys()) == ["custom.observe"]
    assert context.capability_registry["custom.observe"].name == "custom.observe"

    with pytest.raises(ValueError, match="capability name mismatch"):
        build_task_context(
            request=request,
            parsed_task=parsed_task,
            runtime_state=runtime_state,
            capability_registry={
                "bad.key": {
                    "name": "other.name",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "failure_modes": ["timeout"],
                    "timeout_s": 1.0,
                    "returns_evidence": True,
                }
            },
        )


def test_task_context_passes_runtime_progress_without_creating_second_state_source() -> None:
    request = TaskRequest(source="cli", user_id="u7", utterance="去厨房把水杯拿给我")
    parsed_task = ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"], attributes=[]),
    )
    runtime_state = RuntimeState(
        high_level_progress=HighLevelProgress(
            current_subgoal_id="sg-2",
            current_subgoal_type=SubgoalType.OBSERVE,
            completed_subgoal_ids=["sg-1"],
            pending_subgoal_ids=["sg-3", "sg-4"],
            execution_phase="executing",
            replan_count=1,
        ),
        embodied_action_progress=EmbodiedActionProgress(
            active_skill_name="mock_atomic_executor.execute",
            current_action_phase="approaching_target",
            completed_action_phases=[],
            pending_action_phases=["grasp_target"],
            local_world_state_flags={"container_opened": True},
        ),
        runtime_object_updates=[
            RuntimeObjectUpdate(
                object_ref="mem-cup-001",
                source="execution_evidence",
                reason="target_location_changed",
            )
        ],
    )

    context = build_task_context(
        request=request,
        parsed_task=parsed_task,
        runtime_state=runtime_state,
    )

    assert context.runtime_state.high_level_progress is not None
    assert context.runtime_state.high_level_progress.current_subgoal_id == "sg-2"
    assert context.runtime_state.embodied_action_progress is not None
    assert context.runtime_state.embodied_action_progress.local_world_state_flags[
        "container_opened"
    ]
    assert len(context.runtime_state.runtime_object_updates) == 1

    assert not hasattr(context, "task_progress")
    assert not hasattr(context, "current_object_changes")
    assert not hasattr(context, "embodied_progress")
