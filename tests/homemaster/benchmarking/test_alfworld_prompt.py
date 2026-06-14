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
        max_env_steps=50,
        observation_mode="textual_debug",
    )

    assert "must use tools" in prompt.lower()
    assert "raw ALFWorld commands" in prompt
    assert "move {object} to {target_receptacle}" in prompt
    assert "go to countertop 1" not in prompt
    assert "admissible_commands" not in prompt
    assert "Memory tools are not available" in prompt
    assert "50 ALFWorld environment action steps" in prompt
    assert "stand at the microwave while holding the object" in prompt
    assert "Do not open, put into, close, or use the microwave" in prompt
    assert "stand at the fridge while holding the object" in prompt
    assert "stand at a sinkbasin while holding the object" in prompt


def test_visual_eval_prompt_omits_text_observation_and_scores() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="-= Welcome =-\n\nYou see hidden object list.\n\nYour task is to: look at mug under the desklamp",
        observation="You see hidden object list.",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.5,
        frame_path=None,
        step_index=0,
        invalid_action_count=0,
    )

    prompt = build_episode_prompt(
        state=state,
        translator=create_translator("AlfredThorEnv"),
        memory_mode="disabled",
        max_invalid_actions=100,
        max_env_steps=50,
        observation_mode="visual_eval",
    )

    assert "Use camera observations" in prompt
    assert "Tool results provide images and minimal execution status" in prompt
    assert "Your task is to: look at mug under the desklamp" in prompt
    assert "You see hidden object list" not in prompt
    assert "goal_condition_success_rate" not in prompt
    assert "latest observation, feedback" not in prompt
