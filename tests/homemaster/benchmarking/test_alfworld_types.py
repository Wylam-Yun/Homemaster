from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldExecutionFeedback,
    AlfworldResetResult,
)


def test_config_defaults_keep_memory_disabled_and_invalid_limit_100(tmp_path: Path) -> None:
    config = AlfworldBenchmarkConfig(
        alfworld_root=tmp_path / "alfworld",
        alfworld_config=tmp_path / "base_config.yaml",
        trace_root=tmp_path / "traces",
    )

    assert config.env_type == "AlfredTWEnv"
    assert config.split == "valid_seen"
    assert config.memory_mode == "disabled"
    assert config.max_invalid_actions == 100
    assert config.max_env_steps == 50
    assert config.max_tool_iterations == 1000
    assert config.provider_name is None
    assert config.observation_mode == "visual_eval"


def test_config_rejects_non_positive_episode_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="episodes"):
        AlfworldBenchmarkConfig(
            alfworld_root=tmp_path,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            episodes=0,
        )


def test_config_rejects_non_positive_env_step_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_env_steps"):
        AlfworldBenchmarkConfig(
            alfworld_root=tmp_path,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            max_env_steps=0,
        )


def test_env_state_model_visible_dict_omits_admissible_commands() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="put apple on table",
        observation="You are in the kitchen.",
        inventory=None,
        last_command="go to fridge 1",
        last_feedback="Nothing happens.",
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=3,
        invalid_action_count=2,
        admissible_commands=("look", "inventory"),
    )

    visible = state.to_model_visible_dict()
    debug = state.to_debug_dict()

    assert "admissible_commands" not in visible
    assert visible["frame_path"] is None
    assert visible["invalid_action_count"] == 2
    assert debug["admissible_commands"] == ["look", "inventory"]


def test_config_rejects_unknown_observation_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="observation_mode"):
        AlfworldBenchmarkConfig(
            alfworld_root=tmp_path,
            alfworld_config=tmp_path / "base_config.yaml",
            trace_root=tmp_path / "traces",
            observation_mode="oracle_text",  # type: ignore[arg-type]
        )


def test_thor_ready_reset_requires_complete_snapshot_identity() -> None:
    state = AlfworldEnvState(
        episode_id="episode-1",
        task="task",
        observation="room",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path="frame-0000.png",
        step_index=0,
        invalid_action_count=0,
    )
    result = AlfworldResetResult(
        backend_kind="thor",
        ready=True,
        state=state,
        scene_generation=1,
        goal_generation=1,
        scene_reset_fingerprint="1" * 64,
        goal_trial_fingerprint="2" * 64,
        snapshot_sha256="3" * 64,
        snapshot_ref="snapshot.json",
        setup_trigger=None,
        setup_failure=None,
        classification=None,
        score_eligible=True,
        setup_backend_action_count=8,
        recovery_status="restored",
        cleanup_status="not_needed",
        quarantine_required=False,
        environment_disposition="ready",
        evidence_ref="reset.json",
    )
    assert result.ready

    with pytest.raises(ValueError):
        AlfworldResetResult(**{**result.__dict__, "snapshot_sha256": None})


def test_execution_feedback_has_one_safe_model_projection() -> None:
    feedback = AlfworldExecutionFeedback(
        success=False,
        action="navigate",
        object=None,
        target="mug 1",
        inventory=(),
        inventory_status="ok",
        object_state=None,
        object_state_status="not_applicable",
        target_state="not_visible",
        target_state_status="ok",
        state_changed=False,
        state_read_status="ok",
        error="target_not_visible",
        terminal=False,
        classification=None,
        score_eligible=True,
        detail_code="target_not_visible",
    )

    assert feedback.failure_reason == "target_not_visible"
    assert feedback.to_model_payload() == {
        "success": False,
        "action": "navigate",
        "object": None,
        "target": "mug 1",
        "inventory": [],
        "inventory_status": "ok",
        "object_state": None,
        "object_state_status": "not_applicable",
        "target_state": "not_visible",
        "target_state_status": "ok",
        "state_changed": False,
        "state_read_status": "ok",
        "error": "target_not_visible",
        "terminal": False,
        "classification": None,
        "score_eligible": True,
        "detail": "mug 1 is not visible in the current view.",
    }

    with pytest.raises(ValueError):
        AlfworldExecutionFeedback(**{**feedback.__dict__, "terminal": True})
