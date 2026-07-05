from __future__ import annotations

from pathlib import Path

import pytest

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


def test_adapter_step_uses_environment_feedback_for_invalid_actions() -> None:
    env = FakeBatchEnv()
    env.current_admissible = ["look"]
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="episode", seed=123)
    adapter.reset()

    valid = adapter.step("go to countertop 1", tool_name="robot_navigate", tool_args={})
    invalid = adapter.step("go to fridge 1", tool_name="robot_navigate", tool_args={})

    assert valid.success is True
    assert valid.failure_reason is None
    assert valid.state.invalid_action_count == 0
    assert invalid.success is False
    assert invalid.failure_reason == "invalid_action"
    assert invalid.state.invalid_action_count == 1
    assert "admissible_commands" not in invalid.to_model_visible_data()


def test_adapter_saves_thor_frames_when_available(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)

    class ThorEnv:
        last_event = Event()

    class InnerEnv:
        env = ThorEnv()

    env = FakeBatchEnv()
    env.envs = [InnerEnv()]
    adapter = AlfworldEnvAdapter(
        env=env,
        episode_prefix="episode",
        seed=123,
        frame_dir=tmp_path / "frames",
    )

    reset_state = adapter.reset()
    step_result = adapter.step(
        "go to countertop 1",
        tool_name="robot_navigate",
        tool_args={},
    )

    assert reset_state.frame_path is not None
    assert Path(reset_state.frame_path).name == "frame-0000.png"
    assert Path(reset_state.frame_path).exists()
    assert step_result.state.frame_path is not None
    assert Path(step_result.state.frame_path).name == "frame-0001.png"
    assert Path(step_result.state.frame_path).exists()


def test_virtual_navigate_teleports_until_target_is_visible(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, visible: bool = False, reachable: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = (
                {"FloorLamp|1": [0, 0, 3, 3]} if visible else {}
            )
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": (
                    [{"x": 1.0, "z": 1.0}, {"x": 2.0, "z": 2.0}]
                    if reachable
                    else []
                ),
                "objects": [
                    {
                        "objectId": "FloorLamp|0",
                        "objectType": "FloorLamp",
                        "position": {"x": 10.0, "y": 1.2, "z": 10.0},
                        "visible": False,
                    },
                    {
                        "objectId": "FloorLamp|1",
                        "objectType": "FloorLamp",
                        "position": {"x": 2.0, "y": 1.2, "z": 2.0},
                        "visible": visible,
                    }
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []
            self.teleport_count = 0

        def step(self, action: dict):
            self.actions.append(action)
            if action["action"] == "GetReachablePositions":
                self.last_event = Event(reachable=True)
            elif action["action"] == "TeleportFull":
                self.teleport_count += 1
                self.last_event = Event(visible=self.teleport_count >= 2)
            else:
                raise AssertionError(action)
            return self.last_event

        def get_goal_satisfied(self) -> bool:
            return False

        def get_goal_conditions_met(self):
            return (0, 2)

    class InnerEnv:
        def __init__(self, thor_env: ThorEnv) -> None:
            self.env = thor_env

    env = FakeBatchEnv()
    thor_env = ThorEnv()
    env.envs = [InnerEnv(thor_env)]
    adapter = AlfworldEnvAdapter(
        env=env,
        episode_prefix="episode",
        seed=123,
        frame_dir=tmp_path / "frames",
    )
    adapter.reset()

    result = adapter.virtual_navigate(
        "floorlamp",
        tool_name="robot_navigate",
        tool_args={},
    )

    assert result.success is True
    assert result.failure_reason is None
    assert result.translated_command == "virtual go to floorlamp"
    assert result.state.step_index == 1
    assert result.state.invalid_action_count == 0
    assert result.state.frame_path is not None
    assert Path(result.state.frame_path).exists()
    assert thor_env.actions[0] == {"action": "GetReachablePositions"}
    assert thor_env.teleport_count >= 2
    assert all(
        action["action"] == "TeleportFull"
        for action in thor_env.actions[1:]
    )
