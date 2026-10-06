from __future__ import annotations

from homemaster.alfworld.benchmark.translator import create_translator
from homemaster.alfworld.prompt import build_episode_prompt
from homemaster.alfworld.types import AlfworldEnvState


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
        translator=create_translator("AlfredThorEnv"),
        memory_mode="disabled",
        max_invalid_actions=100,
        max_env_steps=50,
        observation_mode="textual_debug",
    )

    assert "must use tools" in prompt.lower()
    assert "raw environment commands" in prompt
    assert "move {object} to {target_receptacle}" in prompt
    assert "go to countertop 1" not in prompt
    assert "admissible_commands" not in prompt
    assert "recalled automatically" in prompt
    assert "memory tools may be used" in prompt
    assert "Memory tools are not available" not in prompt
    assert "50 environment action steps" in prompt
    # Deployment names must not leak into model-visible prompt text.
    assert "ALFWorld" not in prompt
    assert "MindMemOS" not in prompt
    assert "stand at the microwave while holding the object" in prompt
    assert "Do not open, put into, close, or use the microwave" in prompt
    assert "stand at the fridge while holding the object" in prompt
    assert "stand at a sinkbasin while holding the object" in prompt


def test_visual_eval_prompt_omits_text_observation_and_scores() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task=(
            "-= Welcome =-\n\nYou see hidden object list.\n\n"
            "Your task is to: look at mug under the desklamp"
        ),
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

    assert "Use the explicit observe tool" in prompt
    # Observation/verify/progress rules live in the deployment system prompt,
    # not duplicated in the benchmark episode message.
    assert "Action results provide receipts" not in prompt
    assert "Call observe after actions" not in prompt
    assert "robot_inspect_view" not in prompt
    assert "task_progress_check" not in prompt
    assert "robot_verify" not in prompt
    assert "ALFWorld" not in prompt
    assert "MindMemOS" not in prompt
    assert "Your task is to: look at mug under the desklamp" in prompt
    assert "hold the target object in inventory" in prompt
    assert "turn on the named lamp while still holding the object" in prompt
    assert "observe alone does not satisfy this task" in prompt
    assert "You see hidden object list" not in prompt
    assert "goal_condition_success_rate" not in prompt
    assert "latest observation, feedback" not in prompt
    assert "Observation forms:" not in prompt


def test_non_light_task_omits_light_task_semantics() -> None:
    state = AlfworldEnvState(
        episode_id="valid_unseen/pick_and_place_simple-Apple-None-TableTop-1",
        task="Your task is to: put an apple on the table",
        observation="",
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
    )

    prompt = build_episode_prompt(
        state=state,
        translator=create_translator("AlfredThorEnv"),
        memory_mode="disabled",
        max_invalid_actions=100,
        max_env_steps=50,
    )

    assert "hold the target object in inventory" not in prompt
    assert "observe alone does not satisfy this task" not in prompt
