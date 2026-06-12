from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
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
