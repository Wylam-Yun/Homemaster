from __future__ import annotations

from pathlib import Path

import pytest

from task_brain.adapters import MockPerceptionAdapter
from task_brain.domain import ObservationSource, RobotRuntimeState
from task_brain.evidence import build_verification_evidence
from task_brain.world import MockWorld


def _fixture_world_path() -> Path:
    return Path(__file__).parent / "fixtures" / "world_minimal.json"


def test_mock_perception_returns_standard_observation() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")

    assert observation.source == ObservationSource.MOCK_WORLD
    assert observation.room_id == "kitchen"
    assert observation.viewpoint_id == "kitchen_table_viewpoint"
    assert observation.raw_ref == "mock_world:kitchen_table_viewpoint"
    assert len(observation.visible_objects) == 1
    assert observation.visible_objects[0].category == "cup"
    assert observation.visible_anchors[0].anchor_type == "table"
    assert observation.scene_relations[0].relation_type == "on"
    assert world.query_predicates("visible_category", args=["cup"])


def test_observation_distinguishes_detector_id_from_memory_id() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    observed = observation.visible_objects[0]

    assert observed.detector_id == "det-cup-1"
    assert observed.memory_id == "mem-cup-1"
    assert observed.detector_id != observed.memory_id
    assert observed.observation_object_id != observed.memory_id


def test_verification_evidence_wraps_observation_without_raw_world_dependency() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    runtime = RobotRuntimeState(viewpoint_id="kitchen_table_viewpoint", room_id="kitchen")

    evidence = build_verification_evidence(
        observation=observation,
        robot_runtime_state=runtime,
        execution_result={"status": "ok"},
    )

    assert evidence.observation is observation
    assert evidence.robot_runtime_state is runtime
    assert evidence.execution_result == {"status": "ok"}
    assert not hasattr(evidence, "world")

    with pytest.raises(TypeError):
        build_verification_evidence(
            observation=observation,
            robot_runtime_state=runtime,
            world={"raw": "payload"},
        )


def test_observe_with_invalid_viewpoint_raises_value_error() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    with pytest.raises(ValueError, match="unknown viewpoint_id"):
        MockPerceptionAdapter.observe(world, "invalid_viewpoint")
