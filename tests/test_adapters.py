from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from task_brain.adapters import (
    EmbodiedSubgoalRequest,
    FakeRoboBrainClient,
    MockAtomicExecutor,
    MockPerceptionAdapter,
    MockVLNAdapter,
)
from task_brain.domain import (
    EmbodiedActionProgress,
    HighLevelProgress,
    RuntimeState,
    SubgoalType,
)
from task_brain.world import MockWorld


def _fixture_world_path() -> Path:
    return Path(__file__).parent / "fixtures" / "world_minimal.json"


def test_mock_vln_returns_navigation_result() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    result = MockVLNAdapter.navigate(world, "kitchen_table_viewpoint")

    assert result.status == "success"
    assert result.arrived is True
    assert result.evidence["source"] == "mock_vln"
    assert result.evidence["viewpoint_id"] == "kitchen_table_viewpoint"
    assert result.evidence["room_id"] == "kitchen"


def test_mock_vln_invalid_viewpoint_raises_value_error() -> None:
    world = MockWorld.from_file(_fixture_world_path())

    with pytest.raises(ValueError, match="unknown viewpoint_id"):
        MockVLNAdapter.navigate(world, "invalid_viewpoint")


def test_fake_robobrain_accepts_embodied_subgoal_request() -> None:
    request = EmbodiedSubgoalRequest(
        subgoal={"subgoal_type": "embodied_manipulation"},
        target_object={"category": "cup", "aliases": ["水杯"]},
        current_observation={"room_id": "kitchen"},
        constraints={"max_steps": 5},
        success_conditions=[{"name": "object_secured", "args": ["cup"]}],
    )

    response = FakeRoboBrainClient().plan(request)

    assert response.evidence["source"] == "fake_robobrain"
    assert response.evidence["planned_for_subgoal"] == "embodied_manipulation"
    assert len(response.atomic_plan) >= 1


def test_fake_robobrain_returns_atomic_plan() -> None:
    response = FakeRoboBrainClient().plan(
        {"subgoal": {"subgoal_type": "return_to_user"}, "constraints": {"max_steps": 3}}
    )

    assert len(response.atomic_plan) >= 1
    assert response.atomic_plan[0]["action"] == "move_base"
    assert response.evidence["atomic_action_count"] == len(response.atomic_plan)
    assert "passed" not in response.evidence


def test_mock_atomic_executor_applies_success_delta() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    plan = FakeRoboBrainClient().plan({"subgoal": {"subgoal_type": "embodied_manipulation"}})
    runtime_state = RuntimeState(
        embodied_action_progress=EmbodiedActionProgress(
            active_skill_name="mock_atomic_executor.execute",
            current_action_phase="grasping",
            local_world_state_flags={"holding_target": False},
        )
    )

    result = MockAtomicExecutor.execute(
        plan=plan,
        runtime_state=runtime_state,
        world=world,
        attempt=1,
    )

    assert result.status == "success"
    assert result.execution_result["status"] == "success"
    assert result.execution_result["state_delta"]["executor_status"] == "success"
    assert result.execution_result["state_delta"]["last_attempt"] == 1
    assert result.execution_result["runtime_object_updates_candidate"] == []
    assert (
        result.evidence["runtime_progress"]["active_skill_name"]
        == "mock_atomic_executor.execute"
    )


def test_mock_atomic_executor_can_inject_failure() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    plan = FakeRoboBrainClient().plan({"subgoal": {"subgoal_type": "embodied_manipulation"}})
    runtime_state = RuntimeState()

    result = MockAtomicExecutor.execute(
        plan=plan,
        runtime_state=runtime_state,
        world=world,
        attempt=1,
        failure_rules=[{"attempt": 1, "status": "failed", "reason": "grasp_failed"}],
    )

    assert result.status == "failed"
    assert result.execution_result["status"] == "failed"
    assert result.execution_result["reason"] == "grasp_failed"
    assert result.evidence["failure_injected"] is True


def test_executor_emits_runtime_object_update_candidates_for_drop_or_relocation() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    plan = FakeRoboBrainClient().plan({"subgoal": {"subgoal_type": "embodied_manipulation"}})
    runtime_state = RuntimeState()

    result = MockAtomicExecutor.execute(
        plan=plan,
        runtime_state=runtime_state,
        world=world,
        attempt=2,
        failure_rules=[
            {
                "attempt": 2,
                "status": "failed",
                "reason": "target_dropped",
                "runtime_object_updates_candidate": [
                    {
                        "object_ref": "mem-cup-1",
                        "source": "execution_evidence",
                        "reason": "target_dropped",
                    },
                    {
                        "object_ref": "mem-cup-1",
                        "source": "execution_evidence",
                        "reason": "target_location_changed",
                    },
                ],
            }
        ],
    )

    updates = result.execution_result["runtime_object_updates_candidate"]
    assert len(updates) == 2
    assert updates[0]["reason"] == "target_dropped"
    assert updates[1]["reason"] == "target_location_changed"


def test_adapter_reads_runtime_progress_but_does_not_create_second_state_source() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    plan = FakeRoboBrainClient().plan({"subgoal": {"subgoal_type": "embodied_manipulation"}})

    runtime_state = RuntimeState(
        high_level_progress=HighLevelProgress(
            current_subgoal_id="sg-3",
            current_subgoal_type=SubgoalType.EMBODIED_MANIPULATION,
            completed_subgoal_ids=["sg-1", "sg-2"],
            pending_subgoal_ids=["sg-4"],
            execution_phase="executing",
            replan_count=0,
        ),
        embodied_action_progress=EmbodiedActionProgress(
            active_skill_name="mock_atomic_executor.execute",
            current_action_phase="approach_target",
            local_world_state_flags={"container_opened": True},
        ),
        current_observation=observation,
    )
    snapshot_before = deepcopy(runtime_state.model_dump())

    result = MockAtomicExecutor.execute(
        plan=plan,
        runtime_state=runtime_state,
        world=world,
        attempt=1,
    )

    assert result.evidence["runtime_progress"]["current_action_phase"] == "approach_target"
    assert runtime_state.model_dump() == snapshot_before
    assert "task_progress" not in result.execution_result
    assert "current_object_changes" not in result.execution_result
    assert "embodied_progress" not in result.execution_result
