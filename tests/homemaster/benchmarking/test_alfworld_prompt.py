from __future__ import annotations

from homemaster.benchmarking.alfworld.prompt import build_episode_prompt
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import AlfworldEnvState


def test_episode_prompt_requires_tools_and_omits_admissible_commands() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="put apple on table",
        observation="You are in the kitchen.",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=0,
        invalid_action_count=0,
        admissible_commands=("go to countertop 1",),
    )

    prompt = build_episode_prompt(
        state=state,
        translator=create_translator("AlfredTWEnv"),
        memory_mode="disabled",
        max_invalid_actions=100,
    )

    assert "must use tools" in prompt.lower()
    assert "raw ALFWorld commands" in prompt
    assert "move {object} to {target_receptacle}" in prompt
    assert "go to countertop 1" not in prompt
    assert "admissible_commands" not in prompt
    assert "Memory tools are not available" in prompt
