from __future__ import annotations

from pathlib import Path

from task_brain.adapters import MockPerceptionAdapter
from task_brain.domain import Predicate, RobotRuntimeState
from task_brain.evidence import build_verification_evidence
from task_brain.verification import VerificationEngine
from task_brain.world import MockWorld


def _fixture_world_path() -> Path:
    return Path(__file__).parent / "fixtures" / "world_minimal.json"


def test_verifies_visible_category_from_observation() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    engine = VerificationEngine()

    result = engine.evaluate(
        success_conditions=[["visible_category", "cup"]],
        evidence=build_verification_evidence(
            observation=observation,
            robot_runtime_state=RobotRuntimeState(
                viewpoint_id="kitchen_table_viewpoint",
                room_id="kitchen",
            ),
        ),
    )

    assert result.passed is True
    assert result.failed_conditions == []
    assert "visible_category" in result.evidence["matched_signals"]


def test_rejects_missing_visible_category_from_observation() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    engine = VerificationEngine()

    result = engine.evaluate(
        success_conditions=[["visible_category", "medicine_box"]],
        evidence=build_verification_evidence(
            observation=observation,
            robot_runtime_state=RobotRuntimeState(
                viewpoint_id="kitchen_table_viewpoint",
                room_id="kitchen",
            ),
        ),
    )

    assert result.passed is False
    assert len(result.failed_conditions) == 1
    assert result.failed_conditions[0].name == "visible_category"


def test_verifies_holding_category_from_runtime_state() -> None:
    engine = VerificationEngine()
    evidence = build_verification_evidence(
        observation=None,
        robot_runtime_state=RobotRuntimeState(
            viewpoint_id="kitchen_table_viewpoint",
            room_id="kitchen",
            holding_object_category="cup",
            is_holding_object=True,
        ),
    )

    result = engine.evaluate(
        success_conditions=[["holding_category", "robot", "cup"]],
        evidence=evidence,
    )

    assert result.passed is True
    assert result.failed_conditions == []


def test_execution_result_alone_does_not_mark_success() -> None:
    engine = VerificationEngine()
    evidence = build_verification_evidence(
        observation=None,
        robot_runtime_state=RobotRuntimeState(
            viewpoint_id="kitchen_table_viewpoint",
            room_id="kitchen",
        ),
        execution_result={"status": "success", "near_user": True},
    )

    result = engine.evaluate(
        success_conditions=[["visible_category", "cup"]],
        evidence=evidence,
    )

    assert result.passed is False
    assert result.failed_conditions[0].name == "visible_category"


def test_final_success_requires_final_task_verification() -> None:
    engine = VerificationEngine()

    missing_near_user = engine.evaluate(
        success_conditions=[["task_goal_satisfied", "cup"]],
        evidence=build_verification_evidence(
            observation=None,
            robot_runtime_state=RobotRuntimeState(
                holding_object_category="cup",
                is_holding_object=True,
            ),
            execution_result={"status": "success", "near_user": False},
        ),
    )
    assert missing_near_user.passed is False

    final_verified = engine.evaluate(
        success_conditions=[["task_goal_satisfied", "cup"]],
        evidence=build_verification_evidence(
            observation=None,
            robot_runtime_state=RobotRuntimeState(
                holding_object_category="cup",
                is_holding_object=True,
            ),
            execution_result={"status": "success", "near_user": True},
        ),
    )
    assert final_verified.passed is True


def test_runtime_drop_or_relocation_signal_blocks_manipulation_or_final_success() -> None:
    engine = VerificationEngine()
    evidence = build_verification_evidence(
        observation=None,
        robot_runtime_state=RobotRuntimeState(
            holding_object_category="cup",
            is_holding_object=True,
        ),
        execution_result={
            "status": "success",
            "near_user": True,
            "runtime_object_updates_candidate": [
                {
                    "object_ref": "mem-cup-1",
                    "source": "execution_evidence",
                    "reason": "target_dropped",
                }
            ],
            "evidence": {
                "runtime_progress": {
                    "local_world_state_flags": {"target_dropped": True}
                }
            },
        },
    )

    result = engine.evaluate(
        success_conditions=[
            ["object_secured", "cup"],
            ["task_goal_satisfied", "cup"],
        ],
        evidence=evidence,
    )

    assert result.passed is False
    assert {item.name for item in result.failed_conditions} == {
        "object_secured",
        "task_goal_satisfied",
    }
    assert "target_dropped" in result.evidence["blocked_by_runtime_updates"]


def test_unknown_predicate_fails_closed() -> None:
    engine = VerificationEngine()
    result = engine.evaluate(
        success_conditions=[Predicate(name="unknown_predicate", args=["x"])],
        evidence=build_verification_evidence(
            observation=None,
            robot_runtime_state=RobotRuntimeState(),
        ),
    )

    assert result.passed is False
    assert len(result.failed_conditions) == 1
    assert result.failed_conditions[0].name == "unknown_predicate"


def test_verification_interface_does_not_require_raw_world() -> None:
    world = MockWorld.from_file(_fixture_world_path())
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    evidence = build_verification_evidence(
        observation=observation,
        robot_runtime_state=RobotRuntimeState(
            viewpoint_id="kitchen_table_viewpoint",
            room_id="kitchen",
        ),
        execution_result={"status": "success"},
    )
    engine = VerificationEngine()

    result = engine.evaluate(
        success_conditions=[["observation_captured", "mem-cup-1"]],
        evidence=evidence,
    )

    assert result.passed is True
    assert result.failed_conditions == []
    assert not hasattr(evidence, "world")
