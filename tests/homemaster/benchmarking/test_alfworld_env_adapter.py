from __future__ import annotations

from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    split_to_train_eval,
)


class FakeBatchEnv:
    def __init__(self) -> None:
        self.actions: list[list[str]] = []
        self.reset_called = False
        self.current_admissible = ["look", "go to countertop 1"]

    def seed(self, seed: int) -> None:
        self.seed_value = seed

    def reset(self):
        self.reset_called = True
        return (
            ["Your task is to: put apple on the table\nYou are in the kitchen."],
            {
                "extra.gamefile": ["/games/pick_and_place/task/game.tw-pddl"],
                "admissible_commands": [self.current_admissible],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )

    def step(self, actions: list[str]):
        self.actions.append(actions)
        command = actions[0]
        if command == "go to countertop 1":
            self.current_admissible = ["look", "take apple 1 from countertop 1"]
            return (
                ["You arrive at countertop 1. You see apple 1."],
                [0.0],
                [False],
                {
                    "admissible_commands": [self.current_admissible],
                    "won": [False],
                    "goal_condition_success_rate": [0.0],
                },
            )
        return (
            ["Nothing happens."],
            [0.0],
            [False],
            {
                "admissible_commands": [self.current_admissible],
                "won": [False],
                "goal_condition_success_rate": [0.0],
            },
        )


def test_split_to_train_eval_mapping() -> None:
    assert split_to_train_eval("train") == "train"
    assert split_to_train_eval("valid_seen") == "eval_in_distribution"
    assert split_to_train_eval("valid_unseen") == "eval_out_of_distribution"


def test_adapter_reset_normalizes_initial_state_without_visible_admissible_commands() -> None:
    env = FakeBatchEnv()
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="episode", seed=123)

    state = adapter.reset()

    assert env.reset_called is True
    assert state.episode_id == "pick_and_place/task"
    assert state.step_index == 0
    assert state.frame_path is None
    assert state.to_model_visible_dict()["observation"].startswith("Your task is to")
    assert "admissible_commands" not in state.to_model_visible_dict()
    assert state.to_debug_dict()["admissible_commands"] == [
        "look",
        "go to countertop 1",
    ]


def test_adapter_step_tracks_invalid_action_using_hidden_admissible_commands() -> None:
    adapter = AlfworldEnvAdapter(env=FakeBatchEnv(), episode_prefix="episode", seed=123)
    adapter.reset()

    valid = adapter.step("go to countertop 1", tool_name="robot_navigate", tool_args={})
    invalid = adapter.step("go to fridge 1", tool_name="robot_navigate", tool_args={})

    assert valid.success is True
    assert valid.state.invalid_action_count == 0
    assert invalid.success is False
    assert invalid.failure_reason == "invalid_action"
    assert invalid.state.invalid_action_count == 1
    assert "admissible_commands" not in invalid.to_model_visible_data()
