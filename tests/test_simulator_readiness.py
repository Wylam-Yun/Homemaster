from __future__ import annotations

import pytest

from task_brain.adapters.simulator_style import SimulatorEvent, SimulatorStyleAdapter
from task_brain.context import build_task_context
from task_brain.domain import (
    ObservationSource,
    ParsedTask,
    Predicate,
    RobotRuntimeState,
    RuntimeState,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskRequest,
)
from task_brain.evidence import build_verification_evidence
from task_brain.planner import PlannerService
from task_brain.verification import VerificationEngine


def _simulator_event() -> SimulatorEvent:
    return SimulatorEvent.model_validate(
        {
            "event_id": "evt-1",
            "pose": {
                "viewpoint_id": "vp-bedside",
                "room_id": "bedroom",
                "x": 1.0,
                "y": 0.0,
                "z": 2.0,
            },
            "visible_objects": [
                {
                    "object_id": "obj-med-1",
                    "category": "medicine_box",
                    "aliases": ["药盒"],
                    "detector_id": "det-med-1",
                    "memory_id": "mem-med-1",
                    "confidence_level": "high",
                }
            ],
            "visible_anchors": [
                {
                    "room_id": "bedroom",
                    "anchor_id": "bedside_table",
                    "anchor_type": "table",
                    "viewpoint_id": "vp-bedside",
                }
            ],
            "scene_relations": [
                {
                    "relation_type": "on",
                    "subject_object_id": "obj-med-1",
                    "target_object_id": "bedside_table",
                }
            ],
            "object_states": {"obj-med-1": {"opened": False}},
            "metadata": {"simulator_name": "demo_sim"},
        }
    )


def test_simulator_event_converts_to_standard_observation() -> None:
    observation = SimulatorStyleAdapter.to_observation(_simulator_event())

    assert observation.source == ObservationSource.AI2_THOR
    assert observation.observation_id == "sim-obs-evt-1"
    assert observation.viewpoint_id == "vp-bedside"
    assert observation.room_id == "bedroom"
    assert observation.raw_ref == "simulator:evt-1"
    assert len(observation.visible_objects) == 1
    assert observation.visible_objects[0].category == "medicine_box"
    assert observation.visible_anchors[0].anchor_id == "bedside_table"


def test_simulator_observation_keeps_detector_and_memory_id_boundary() -> None:
    observation = SimulatorStyleAdapter.to_observation(_simulator_event())
    item = observation.visible_objects[0]

    assert item.detector_id == "det-med-1"
    assert item.memory_id == "mem-med-1"
    assert item.detector_id != item.memory_id
    assert item.observation_object_id != item.memory_id


def test_planner_accepts_context_built_from_simulator_observation() -> None:
    observation = SimulatorStyleAdapter.to_observation(_simulator_event())
    runtime_state = RuntimeState(
        current_observation=observation,
        robot_runtime_state=RobotRuntimeState(
            viewpoint_id=observation.viewpoint_id,
            room_id=observation.room_id,
        ),
    )
    request = TaskRequest(
        source="simulator:test",
        user_id="u-sim",
        utterance="去看看药盒是不是还在。",
        request_id="req-sim-1",
    )
    parsed_task = ParsedTask(
        intent=TaskIntent.CHECK_OBJECT_PRESENCE,
        target_object=TargetObject(category="medicine_box", aliases=["药盒"]),
        requires_navigation=True,
        requires_manipulation=False,
    )
    context = build_task_context(
        request=request,
        parsed_task=parsed_task,
        runtime_state=runtime_state,
        ranked_candidates=[
            {
                "memory_id": "mem-med-1",
                "anchor": {
                    "room_id": "bedroom",
                    "anchor_id": "bedside_table",
                    "anchor_type": "table",
                },
            }
        ],
    )

    plan = PlannerService(llm_first=False).plan(context)
    assert plan.subgoals
    assert plan.subgoals[0].subgoal_type == SubgoalType.NAVIGATE


def test_verification_accepts_simulator_observation_evidence_without_raw_world() -> None:
    observation = SimulatorStyleAdapter.to_observation(_simulator_event())
    evidence = build_verification_evidence(
        observation=observation,
        robot_runtime_state=RobotRuntimeState(
            viewpoint_id=observation.viewpoint_id,
            room_id=observation.room_id,
        ),
    )
    result = VerificationEngine().evaluate(
        success_conditions=[Predicate(name="visible_category", args=["medicine_box"])],
        evidence=evidence,
    )

    assert result.passed is True
    assert result.failed_conditions == []
    with pytest.raises(TypeError):
        build_verification_evidence(
            observation=observation,
            robot_runtime_state=evidence.robot_runtime_state,
            world={"raw": "payload"},
        )


def test_simulator_raw_ref_is_debug_reference_only() -> None:
    observation = SimulatorStyleAdapter.to_observation(
        {
            "event_id": "evt-raw-1",
            "pose": {"viewpoint_id": "vp-kitchen", "room_id": "kitchen"},
            "visible_objects": [{"object_id": "obj-cup-1", "category": "cup"}],
            "metadata": {"secret_token": "never-leak"},
        }
    )

    assert observation.raw_ref == "simulator:evt-raw-1"
    assert "secret_token" not in observation.raw_ref
    assert "obj-cup-1" not in observation.raw_ref
