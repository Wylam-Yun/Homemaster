from __future__ import annotations

import os
from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    build_alfworld_batch_env,
)
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig


@pytest.mark.live_alfworld
def test_live_alfworld_textworld_reset_and_look(tmp_path: Path) -> None:
    root = os.environ.get("HOMEMASTER_ALFWORLD_ROOT")
    config_path = os.environ.get("HOMEMASTER_ALFWORLD_CONFIG")
    if not root or not config_path:
        pytest.skip("set HOMEMASTER_ALFWORLD_ROOT and HOMEMASTER_ALFWORLD_CONFIG")

    config = AlfworldBenchmarkConfig(
        alfworld_root=Path(root),
        alfworld_config=Path(config_path),
        trace_root=tmp_path / "traces",
        env_type="AlfredTWEnv",
        split=os.environ.get("HOMEMASTER_ALFWORLD_SPLIT", "valid_seen"),  # type: ignore[arg-type]
        episodes=1,
    )
    env = build_alfworld_batch_env(config)
    adapter = AlfworldEnvAdapter(env=env, episode_prefix=config.split, seed=42)
    state = adapter.reset()

    assert state.observation
    assert state.frame_path is None
    result = adapter.step("look", tool_name="robot_observe", tool_args={"mode": "look"})
    assert result.state.step_index == 1
    assert result.state.observation
