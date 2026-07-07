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
        if command == "go to coffeemachine 1":
            self.current_admissible = ["look", "take mug 1 from coffeemachine 1"]
            return (
                ["You arrive at coffeemachine 1. On it, you see a mug 1."],
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


def test_go_to_target_teleports_to_specific_movable_object(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, visible_id: str | None = None, reachable: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = (
                {visible_id: [0, 0, 3, 3]} if visible_id else {}
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
                        "objectId": "Dresser|0",
                        "objectType": "Dresser",
                        "receptacleObjectIds": ["Watch|0"],
                        "position": {"x": 1.0, "y": 0.5, "z": 1.0},
                    },
                    {
                        "objectId": "Watch|0",
                        "objectType": "Watch",
                        "pickupable": True,
                        "parentReceptacles": ["Dresser|0"],
                        "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                        "visible": visible_id == "Watch|0",
                    },
                    {
                        "objectId": "Watch|1",
                        "objectType": "Watch",
                        "pickupable": True,
                        "position": {"x": 2.0, "y": 1.0, "z": 2.0},
                        "visible": False,
                    },
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
                self.last_event = Event(visible_id="Watch|0")
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

    result = adapter.go_to_target(
        "watch",
        tool_name="robot_go_to",
        tool_args={"target": "watch"},
    )

    assert result.success is True
    assert result.translated_command == "go to target watch"
    assert env.actions == []
    assert thor_env.actions[0] == {"action": "GetReachablePositions"}
    assert result.tool_args["resolved_kind"] == "movable_object"
    assert result.tool_args["resolved_label"] == "watch 1"
    assert result.tool_args["object_label"] == "watch 1"
    assert result.tool_args["source_receptacle"] == "dresser 1"
    assert result.tool_args["object_id"] == "Watch|0"


def test_go_to_target_teleports_to_toggle_object(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, visible: bool = False, reachable: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = (
                {"FloorLamp|0": [0, 0, 3, 3]} if visible else {}
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
                        "toggleable": True,
                        "position": {"x": 2.0, "y": 1.2, "z": 2.0},
                        "visible": visible,
                    },
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            if action["action"] == "GetReachablePositions":
                self.last_event = Event(reachable=True)
            elif action["action"] == "TeleportFull":
                self.last_event = Event(visible=True)
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

    result = adapter.go_to_target(
        "floor lamp",
        tool_name="robot_go_to",
        tool_args={"target": "floor lamp"},
    )

    assert result.success is True
    assert env.actions == []
    assert result.tool_args["resolved_kind"] == "toggle_object"
    assert result.tool_args["resolved_label"] == "floorlamp 1"
    assert result.tool_args["object_label"] is None
    assert result.tool_args["source_receptacle"] is None


def test_go_to_target_accepts_alfworld_text_alias_for_movable_object(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, visible: bool = False, reachable: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = (
                {"SoapBar|0": [0, 0, 3, 3]} if visible else {}
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
                        "objectId": "CounterTop|0",
                        "objectType": "CounterTop",
                        "receptacleObjectIds": ["SoapBar|0"],
                        "position": {"x": 1.0, "y": 0.5, "z": 1.0},
                    },
                    {
                        "objectId": "SoapBar|0",
                        "objectType": "SoapBar",
                        "pickupable": True,
                        "parentReceptacles": ["CounterTop|0"],
                        "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                        "visible": visible,
                    },
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            if action["action"] == "GetReachablePositions":
                self.last_event = Event(reachable=True)
            elif action["action"] == "TeleportFull":
                self.last_event = Event(visible=True)
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

    result = adapter.go_to_target(
        "handsoapbar 1",
        tool_name="robot_go_to",
        tool_args={"target": "handsoapbar 1"},
    )

    assert result.success is True
    assert env.actions == []
    assert result.tool_args["resolved_kind"] == "movable_object"
    assert result.tool_args["resolved_label"] == "soapbar 1"
    assert result.tool_args["object_label"] == "soapbar 1"
    assert result.tool_args["source_receptacle"] == "countertop 1"
    assert result.tool_args["object_id"] == "SoapBar|0"


def test_go_to_target_accepts_alfworld_text_alias_for_fixture(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, visible: bool = False, reachable: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = (
                {"HandTowelHolder|0": [0, 0, 3, 3]} if visible else {}
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
                        "objectId": "HandTowelHolder|0",
                        "objectType": "HandTowelHolder",
                        "position": {"x": 2.0, "y": 1.2, "z": 2.0},
                        "visible": visible,
                    },
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            if action["action"] == "GetReachablePositions":
                self.last_event = Event(reachable=True)
            elif action["action"] == "TeleportFull":
                self.last_event = Event(visible=True)
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

    result = adapter.go_to_target(
        "towelholder 1",
        tool_name="robot_go_to",
        tool_args={"target": "towelholder 1"},
    )

    assert result.success is True
    assert env.actions == []
    assert result.tool_args["resolved_kind"] == "receptacle_or_fixture"
    assert result.tool_args["resolved_label"] == "handtowelholder 1"
    assert result.tool_args["object_id"] == "HandTowelHolder|0"


def test_find_object_uses_thor_metadata_then_navigates_to_source(tmp_path: Path) -> None:
    numpy = pytest.importorskip("numpy")

    class Event:
        frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
        metadata = {
            "objects": [
                {
                    "objectId": "CoffeeMachine|0",
                    "objectType": "CoffeeMachine",
                    "receptacleObjectIds": ["Mug|0"],
                },
                    {
                        "objectId": "Mug|0",
                        "objectType": "Mug",
                        "pickupable": True,
                        "visible": True,
                        "parentReceptacles": ["CoffeeMachine|0"],
                        "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                    },
            ],
        }

    class ThorEnv:
        last_event = Event()

    class InnerEnv:
        env = ThorEnv()

    env = FakeBatchEnv()
    env.envs = [InnerEnv()]
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="episode", seed=123)
    adapter.reset()

    result = adapter.find_object(
        "mug",
        tool_name="robot_find_object",
        tool_args={"object": "mug"},
    )

    assert result.success is True
    assert result.translated_command == "find object mug -> go to coffeemachine 1"
    assert env.actions == [["go to coffeemachine 1"]]
    assert result.tool_args["object_label"] == "mug 1"
    assert result.tool_args["source_receptacle"] == "coffeemachine 1"


def test_manipulate_with_thor_takes_unnumbered_object(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, carrying: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.metadata = {
                "lastActionSuccess": True,
                "inventoryObjects": (
                    [{"objectId": "Mug|0", "objectType": "Mug"}]
                    if carrying
                    else []
                ),
                "objects": [
                    {
                        "objectId": "Desk|0",
                        "objectType": "Desk",
                        "receptacle": True,
                        "receptacleObjectIds": ["Mug|0"],
                        "position": {"x": 1.0, "y": 0.5, "z": 1.0},
                    },
                    {
                        "objectId": "Mug|0",
                        "objectType": "Mug",
                        "pickupable": True,
                        "visible": True,
                        "parentReceptacles": ["Desk|0"],
                        "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                    },
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            if action == {
                "action": "PickupObject",
                "objectId": "Mug|0",
                "forceAction": True,
            }:
                self.last_event = Event(carrying=True)
                return self.last_event
            raise AssertionError(action)

        def get_goal_satisfied(self) -> bool:
            return False

        def get_goal_conditions_met(self):
            return (0, 1)

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

    result = adapter.manipulate_with_thor(
        action="take",
        tool_name="robot_manipulate",
        tool_args={"action": "take", "object": "mug"},
    )

    assert result.success is True
    assert result.translated_command == "thor take"
    assert env.actions == []
    assert thor_env.actions == [
        {"action": "PickupObject", "objectId": "Mug|0", "forceAction": True}
    ]
    assert result.tool_args["backend"] == "thor_api"
    assert result.tool_args["backend_actions"] == ["PickupObject"]
    assert result.tool_args["object_resolution_object_id"] == "Mug|0"
    assert result.state.inventory == "You are carrying: mug."


def test_manipulate_with_thor_prefers_last_go_to_object_id(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, carrying: bool = False, visible_id: str | None = None) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = (
                {visible_id: [0, 0, 3, 3]} if visible_id else {}
            )
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": [{"x": 1.0, "z": 1.0}],
                "inventoryObjects": (
                    [{"objectId": "Mug|B", "objectType": "Mug"}]
                    if carrying
                    else []
                ),
                "objects": [
                    {
                        "objectId": "Mug|A",
                        "objectType": "Mug",
                        "pickupable": True,
                        "visible": True,
                        "position": {"x": 8.0, "y": 1.0, "z": 8.0},
                    },
                    {
                        "objectId": "Mug|B",
                        "objectType": "Mug",
                        "pickupable": True,
                        "visible": True,
                        "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                    },
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            if action["action"] == "GetReachablePositions":
                self.last_event = Event()
            elif action["action"] == "TeleportFull":
                self.last_event = Event(visible_id="Mug|B")
            elif action == {
                "action": "PickupObject",
                "objectId": "Mug|B",
                "forceAction": True,
            }:
                self.last_event = Event(carrying=True)
            else:
                raise AssertionError(action)
            return self.last_event

        def get_goal_satisfied(self) -> bool:
            return False

        def get_goal_conditions_met(self):
            return (0, 1)

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

    go_to = adapter.go_to_target(
        "mug 2",
        tool_name="robot_go_to",
        tool_args={"target": "mug 2"},
    )
    take = adapter.manipulate_with_thor(
        action="take",
        tool_name="robot_manipulate",
        tool_args={"action": "take", "object": "mug"},
    )

    assert go_to.success is True
    assert take.success is True
    assert {"action": "PickupObject", "objectId": "Mug|B", "forceAction": True} in thor_env.actions
    assert take.tool_args["object_resolution_object_id"] == "Mug|B"


def test_manipulate_with_thor_fails_without_metadata_match(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
        metadata = {
            "lastActionSuccess": True,
            "inventoryObjects": [],
            "objects": [
                {
                    "objectId": "Bowl|0",
                    "objectType": "Bowl",
                    "pickupable": True,
                    "visible": True,
                },
            ],
        }

    class ThorEnv:
        last_event = Event()

        def __init__(self) -> None:
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            raise AssertionError(action)

        def get_goal_satisfied(self) -> bool:
            return False

        def get_goal_conditions_met(self):
            return (0, 1)

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

    result = adapter.manipulate_with_thor(
        action="take",
        tool_name="robot_manipulate",
        tool_args={"action": "take", "object": "mug"},
    )

    assert result.success is False
    assert result.failure_reason == "invalid_action"
    assert result.feedback == "No THOR object matched mug."
    assert thor_env.actions == []


def test_manipulate_with_thor_accepts_object_for_open_and_target_for_use(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, opened: bool = False, toggled: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.metadata = {
                "lastActionSuccess": True,
                "inventoryObjects": [],
                "objects": [
                    {
                        "objectId": "Safe|0",
                        "objectType": "Safe",
                        "openable": True,
                        "isOpen": opened,
                    },
                    {
                        "objectId": "FloorLamp|0",
                        "objectType": "FloorLamp",
                        "toggleable": True,
                        "isToggled": toggled,
                    },
                ],
            }

    class ThorEnv:
        def __init__(self) -> None:
            self.last_event = Event()
            self.actions: list[dict] = []

        def step(self, action: dict):
            self.actions.append(action)
            if action == {"action": "OpenObject", "objectId": "Safe|0", "forceAction": True}:
                self.last_event = Event(opened=True)
            elif action == {"action": "ToggleObjectOn", "objectId": "FloorLamp|0", "forceAction": True}:
                self.last_event = Event(opened=True, toggled=True)
            else:
                raise AssertionError(action)
            return self.last_event

        def get_goal_satisfied(self) -> bool:
            return False

        def get_goal_conditions_met(self):
            return (0, 1)

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

    opened = adapter.manipulate_with_thor(
        action="open",
        tool_name="robot_manipulate",
        tool_args={"action": "open", "object": "safe"},
    )
    used = adapter.manipulate_with_thor(
        action="use",
        tool_name="robot_manipulate",
        tool_args={"action": "use", "target_receptacle": "floor lamp"},
    )

    assert opened.success is True
    assert used.success is True
    assert thor_env.actions == [
        {"action": "OpenObject", "objectId": "Safe|0", "forceAction": True},
        {"action": "ToggleObjectOn", "objectId": "FloorLamp|0", "forceAction": True},
    ]
