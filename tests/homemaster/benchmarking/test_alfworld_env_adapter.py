from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    _event_world_sha256,
    _external_event_read,
    split_to_train_eval,
)
from homemaster.benchmarking.alfworld.execution import (
    AgentPose,
    ExecutionBudget,
    PoseContext,
)
from homemaster.benchmarking.alfworld.trial_selection import TrialSelectionEntry


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


def test_world_hash_ignores_view_metadata_but_tracks_object_state() -> None:
    before = {
        "objects": [{"objectId": "Mug|1", "isOpen": False, "visible": True, "distance": 1}],
        "cameraPosition": {"x": 0, "y": 1, "z": 0},
        "colorBounds": [{"color": [1, 2, 3], "bounds": [0, 0, 1, 1]}],
        "hand": {"localPosition": {"x": 0.0, "y": -0.16, "z": 0.38}},
        "sceneName": "FloorPlan1_physics",
    }
    after_view_change = {
        "objects": [{"objectId": "Mug|1", "isOpen": False, "visible": False, "distance": 9}],
        "cameraPosition": {"x": 4, "y": 1, "z": 2},
        "colorBounds": [],
        "hand": {"localPosition": {"x": 1e-7, "y": -0.16000002, "z": 0.38000005}},
        "sceneName": "FloorPlan1_physics",
    }
    after_object_change = {
        **after_view_change,
        "objects": [{"objectId": "Mug|1", "isOpen": True, "visible": False, "distance": 9}],
    }

    assert _event_world_sha256(before) == _event_world_sha256(after_view_change)
    assert _event_world_sha256(before) != _event_world_sha256(after_object_change)


def test_world_hash_ignores_agent_coupled_geometry_for_held_objects() -> None:
    before = {
        "objects": [
            {
                "objectId": "Statue|1",
                "isPickedUp": True,
                "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                "objectBounds": {"center": {"x": 1.0, "y": 1.0, "z": 1.0}},
            }
        ],
        "inventoryObjects": [{"objectId": "Statue|1"}],
        "sceneName": "FloorPlan1_physics",
    }
    after_navigation = {
        **before,
        "objects": [
            {
                "objectId": "Statue|1",
                "isPickedUp": True,
                "position": {"x": 4.0, "y": 1.2, "z": 3.0},
                "rotation": {"x": 12.0, "y": 90.0, "z": 5.0},
                "objectBounds": {"center": {"x": 4.0, "y": 1.2, "z": 3.0}},
            }
        ],
    }
    dropped = {
        **after_navigation,
        "objects": [
            {
                **after_navigation["objects"][0],
                "isPickedUp": False,
            }
        ],
        "inventoryObjects": [],
    }
    moved_after_drop = {
        **dropped,
        "objects": [
            {
                **dropped["objects"][0],
                "position": {"x": 5.0, "y": 1.2, "z": 3.0},
            }
        ],
    }

    assert _event_world_sha256(before) == _event_world_sha256(after_navigation)
    assert _event_world_sha256(before) != _event_world_sha256(dropped)
    assert _event_world_sha256(dropped) != _event_world_sha256(moved_after_drop)


def test_external_event_control_hash_tracks_alfworld_state_not_current_view() -> None:
    class Task:
        task_type = "look_at_obj_in_light"
        traj = {
            "task_type": task_type,
            "pddl_params": {"object_target": "Mug", "toggle_target": "DeskLamp"},
        }
        step_num = 0
        goal_idx = 0
        finished = -1
        goal_finished = False
        num_subgoals = 2

        def goal_conditions_met(self, state: Any) -> tuple[int, int]:
            visible_lamp = any(
                item.get("objectType") == "DeskLamp"
                and item.get("isToggled") is True
                and item.get("visible") is True
                for item in state.metadata["objects"]
            )
            return (int(visible_lamp), 1)

        def goal_satisfied(self, state: Any) -> bool:
            return self.goal_conditions_met(state) == (1, 1)

    def event(*, visible: bool) -> Any:
        return SimpleNamespace(
            frame=b"frame",
            instance_detections2D=(
                {"DeskLamp|1": [0, 0, 10, 10]} if visible else {}
            ),
            metadata={
                "lastAction": "TeleportFull",
                "lastActionSuccess": True,
                "sceneName": "FloorPlan1_physics",
                "inventoryObjects": [],
                "agent": {
                    "position": {"x": 0.0, "y": 0.9, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "cameraHorizon": 0.0,
                },
                "objects": [
                    {
                        "objectId": "DeskLamp|1",
                        "objectType": "DeskLamp",
                        "position": {"x": 1.0, "y": 1.0, "z": 1.0},
                        "isToggled": True,
                        "visible": visible,
                        "distance": 1.0 if visible else 9.0,
                    }
                ],
            },
        )

    thor_env = SimpleNamespace(
        task=Task(),
        cleaned_objects=set(),
        cooled_objects=set(),
        heated_objects=set(),
    )
    thor_env.last_event = event(visible=False)
    offscreen = _external_event_read(thor_env.last_event, event_sequence=1, thor_env=thor_env)
    thor_env.last_event = event(visible=True)
    onscreen = _external_event_read(thor_env.last_event, event_sequence=2, thor_env=thor_env)

    assert offscreen.status == onscreen.status == "ok"
    assert offscreen.world_sha256 == onscreen.world_sha256
    assert offscreen.control_sha256 == onscreen.control_sha256
    assert offscreen.control_payload is not None
    control_sha256 = hashlib.sha256(
        json.dumps(
            offscreen.control_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert control_sha256 == offscreen.control_sha256
    assert offscreen.raw_metadata_payload is not None
    assert offscreen.raw_frame_bytes == b"frame"
    raw_metadata = json.dumps(
        offscreen.raw_metadata_payload,
        allow_nan=False,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(raw_metadata + b"frame").hexdigest() == offscreen.raw_event_sha256
    assert hashlib.sha256(b"frame").hexdigest() == offscreen.frame_sha256

    thor_env.task.step_num = 1
    advanced = _external_event_read(thor_env.last_event, event_sequence=3, thor_env=thor_env)
    assert advanced.control_sha256 != onscreen.control_sha256

    class BrokenTask(Task):
        def goal_conditions_met(self, state: Any) -> tuple[int, int]:
            raise RuntimeError("goal evaluator failed")

    thor_env.task = BrokenTask()
    unreadable = _external_event_read(
        thor_env.last_event,
        event_sequence=4,
        thor_env=thor_env,
    )
    assert unreadable.status == "malformed"
    assert unreadable.control_sha256 is None
    assert unreadable.raw_metadata_payload is not None
    assert unreadable.raw_frame_bytes == b"frame"
    unreadable_metadata = json.dumps(
        unreadable.raw_metadata_payload,
        allow_nan=False,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert (
        hashlib.sha256(unreadable_metadata + b"frame").hexdigest()
        == unreadable.raw_event_sha256
    )
    assert hashlib.sha256(b"frame").hexdigest() == unreadable.frame_sha256


def test_adapter_reset_normalizes_initial_state_without_visible_admissible_commands() -> None:
    env = FakeBatchEnv()
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="episode", seed=123)

    reset_result = adapter.reset()
    assert reset_result.ready
    assert reset_result.state is not None
    state = reset_result.state

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


@pytest.mark.parametrize(
    ("runtime_scene", "expected_failure"),
    [
        pytest.param(
            "FloorPlan2_physics",
            "runtime_scene_mismatch",
            id="wrong-runtime-scene",
        ),
        pytest.param(
            "FloorPlan1",
            "reset_identity_unreadable",
            id="bare-logical-scene-is-not-a-runtime-asset",
        ),
    ],
)
def test_v18_reset_rejects_runtime_scene_before_setup_actions(
    runtime_scene: str,
    expected_failure: str,
) -> None:
    class Event:
        metadata = {
            "lastAction": "Reset",
            "lastActionSuccess": True,
            "objects": [],
            "sceneName": runtime_scene,
        }

    class ThorEnv:
        last_event = Event()
        actions: list[dict[str, Any]] = []

        def step(self, action: dict[str, Any]) -> Any:
            self.actions.append(action)
            raise AssertionError("runtime scene failure must precede setup actions")

    class BatchEnv(FakeBatchEnv):
        def __init__(self) -> None:
            super().__init__()
            self.thor = ThorEnv()
            self.envs = [SimpleNamespace(env=self.thor)]
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    env = BatchEnv()
    adapter = AlfworldEnvAdapter(
        env=env,
        episode_prefix="episode",
        seed=123,
        require_v18_reset=True,
    )
    selection = TrialSelectionEntry(
        trial_id="case-1/traj_data.json",
        trial_sha256="a" * 64,
        expected_logical_scene="FloorPlan1",
        goal_identity="{}",
        goal_fingerprint="b" * 64,
        identity_status="test",
    )

    result = adapter.reset(selection_entry=selection)

    assert not result.ready
    assert result.setup_trigger == expected_failure
    assert result.setup_failure == expected_failure
    assert result.classification == "execution_state_uncertain"
    assert result.setup_backend_action_count == 0
    assert result.cleanup_status == "succeeded"
    assert result.environment_disposition == "closed"
    assert env.thor.actions == []
    assert env.close_calls == 1


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
    assert invalid.failure_reason == "invalid_tool_arguments"
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

    reset_result = adapter.reset()
    assert reset_result.ready
    assert reset_result.state is not None
    reset_state = reset_result.state
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
            self.instance_detections2D = {"FloorLamp|1": [0, 0, 3, 3]} if visible else {}
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": (
                    [{"x": 1.0, "z": 1.0}, {"x": 2.0, "z": 2.0}] if reachable else []
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
    assert all(action["action"] == "TeleportFull" for action in thor_env.actions[1:])


def test_go_to_target_teleports_to_specific_movable_object(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    numpy = pytest.importorskip("numpy")

    class Event:
        def __init__(self, *, visible_id: str | None = None, reachable: bool = False) -> None:
            self.frame = numpy.zeros((4, 4, 3), dtype=numpy.uint8)
            self.instance_detections2D = {visible_id: [0, 0, 3, 3]} if visible_id else {}
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": (
                    [{"x": 1.0, "z": 1.0}, {"x": 2.0, "z": 2.0}] if reachable else []
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
            self.instance_detections2D = {"FloorLamp|0": [0, 0, 3, 3]} if visible else {}
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": (
                    [{"x": 1.0, "z": 1.0}, {"x": 2.0, "z": 2.0}] if reachable else []
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
            self.instance_detections2D = {"SoapBar|0": [0, 0, 3, 3]} if visible else {}
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": (
                    [{"x": 1.0, "z": 1.0}, {"x": 2.0, "z": 2.0}] if reachable else []
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
            self.instance_detections2D = {"HandTowelHolder|0": [0, 0, 3, 3]} if visible else {}
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": (
                    [{"x": 1.0, "z": 1.0}, {"x": 2.0, "z": 2.0}] if reachable else []
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
                    [{"objectId": "Mug|0", "objectType": "Mug"}] if carrying else []
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
            self.instance_detections2D = {visible_id: [0, 0, 3, 3]} if visible_id else {}
            self.metadata = {
                "lastActionSuccess": True,
                "agent": {"position": {"y": 0.9}},
                "reachablePositions": [{"x": 1.0, "z": 1.0}],
                "inventoryObjects": (
                    [{"objectId": "Mug|B", "objectType": "Mug"}] if carrying else []
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
    assert result.failure_reason == "invalid_tool_arguments"
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
            elif action == {
                "action": "ToggleObjectOn",
                "objectId": "FloorLamp|0",
                "forceAction": True,
            }:
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


_EXACT_SHELF_ID = "Shelf|A"
_OTHER_SHELF_ID = "Shelf|B"
_PENCIL_ID = "Pencil|-01.57|+00.88|+00.83"
_DESK_ID = "Desk|-01.90|+00.00|+00.20"


def _navigation_objects(
    *,
    exact_visible: bool,
    other_visible: bool,
    include_exact: bool = True,
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if include_exact:
        objects.append(
            {
                "objectId": _EXACT_SHELF_ID,
                "objectType": "Shelf",
                "receptacle": True,
                "position": {"x": 1.0, "y": 0.87, "z": 1.0},
                "visible": exact_visible,
            }
        )
    objects.append(
        {
            "objectId": _OTHER_SHELF_ID,
            "objectType": "Shelf",
            "receptacle": True,
            "position": {"x": 2.0, "y": 0.87, "z": 2.0},
            "visible": other_visible,
        }
    )
    return objects


class _NavigationEvent:
    def __init__(
        self,
        *,
        action_success: bool = True,
        exact_visible: bool = False,
        other_visible: bool = False,
        detections: dict[str, list[int]] | None = None,
        reachable: bool = False,
        requested_action: dict[str, Any] | None = None,
        actual_pose: str = "match",
        include_agent: bool = True,
        include_exact: bool = True,
        frame: Any = None,
    ) -> None:
        self.frame = frame
        self.instance_detections2D = detections or {}
        metadata: dict[str, Any] = {
            "lastActionSuccess": action_success,
            "errorMessage": "" if action_success else "move rejected",
            "objects": _navigation_objects(
                exact_visible=exact_visible,
                other_visible=other_visible,
                include_exact=include_exact,
            ),
            "reachablePositions": ([{"x": 0.5, "y": 0.0, "z": 0.0}] if reachable else []),
        }
        if include_agent:
            if requested_action is None:
                x, y, z, rotation, horizon = 0.0, 0.9010564, 0.0, 0.0, 0.0
            else:
                x = float(requested_action["x"])
                y = float(requested_action["y"])
                z = float(requested_action["z"])
                rotation = float(requested_action["rotation"])
                horizon = float(requested_action["horizon"])
                if actual_pose == "mismatch":
                    x += 0.5
            metadata["agent"] = {
                "position": {"x": x, "y": y, "z": z},
                "rotation": {"x": 0.0, "y": rotation, "z": 0.0},
                "cameraHorizon": horizon,
            }
        self.metadata = metadata


class _ThorBase:
    def get_goal_satisfied(self) -> bool:
        return False

    def get_goal_conditions_met(self) -> tuple[int, int]:
        return (0, 1)


class _InnerThorEnv:
    def __init__(self, thor_env: Any) -> None:
        self.env = thor_env


def _adapter_for_thor(
    thor_env: Any,
    *,
    frame_dir: Path | None = None,
) -> AlfworldEnvAdapter:
    env = FakeBatchEnv()
    env.envs = [_InnerThorEnv(thor_env)]
    adapter = AlfworldEnvAdapter(
        env=env,
        episode_prefix="episode",
        seed=123,
        frame_dir=frame_dir,
    )
    adapter.reset()
    return adapter


class _VisibilityGateThor(_ThorBase):
    def __init__(self) -> None:
        self.last_event = _NavigationEvent()
        self.actions: list[dict[str, Any]] = []
        self.teleport_count = 0

    def step(self, action: dict[str, Any]) -> _NavigationEvent:
        self.actions.append(action)
        if action["action"] == "GetReachablePositions":
            self.last_event = _NavigationEvent(reachable=True)
            return self.last_event
        if action["action"] != "TeleportFull":
            raise AssertionError(action)

        self.teleport_count += 1
        if self.teleport_count == 1:
            # Exact detection is present but exact metadata.visible is false.
            # A different Shelf is fully visible and must not substitute.
            self.last_event = _NavigationEvent(
                exact_visible=False,
                other_visible=True,
                detections={
                    _EXACT_SHELF_ID: [0, 0, 20, 20],
                    _OTHER_SHELF_ID: [0, 0, 30, 30],
                },
                requested_action=action,
            )
        elif self.teleport_count == 2:
            # Exact metadata is visible, but only another Shelf has a box.
            self.last_event = _NavigationEvent(
                exact_visible=True,
                other_visible=True,
                detections={_OTHER_SHELF_ID: [0, 0, 30, 30]},
                requested_action=action,
            )
        elif self.teleport_count == 3:
            # An exact zero-area box is not a rendered-observation success.
            self.last_event = _NavigationEvent(
                exact_visible=True,
                detections={_EXACT_SHELF_ID: [0, 0, 0, 20]},
                requested_action=action,
            )
        else:
            self.last_event = _NavigationEvent(
                exact_visible=True,
                detections={_EXACT_SHELF_ID: [1, 2, 21, 22]},
                requested_action=action,
            )
        return self.last_event


def test_navigation_requires_return_visible_and_positive_exact_box_from_same_event() -> None:
    thor_env = _VisibilityGateThor()
    adapter = _adapter_for_thor(thor_env)

    result = adapter.go_to_target(
        "shelf 1",
        tool_name="robot_go_to",
        tool_args={"target": "shelf 1"},
    )

    assert result.success is True
    assert result.failure_reason is None
    assert result.tool_args["object_id"] == _EXACT_SHELF_ID
    assert "object_id" not in result.to_model_visible_data()
    assert "tool_args" not in result.to_model_visible_data()
    assert thor_env.teleport_count == 4
    assert [action["action"] for action in thor_env.actions] == [
        "GetReachablePositions",
        "TeleportFull",
        "TeleportFull",
        "TeleportFull",
        "TeleportFull",
    ]

    events = list(result.trace_events)
    navigation_context = next(
        event
        for event in events
        if event["event"] == "context_created" and event["context_kind"] == "navigation"
    )
    candidate_hash = navigation_context["locked_candidates_hash"]
    assert len(candidate_hash) == 64
    assert len(navigation_context["locked_candidates"]) >= 4
    attempts = [event for event in events if event["event"] == "attempt_started"]
    assert len(attempts) == 4
    assert all(event["locked_candidates_hash"] == candidate_hash for event in attempts)
    for attempt in attempts:
        per_attempt = [
            event["event"] for event in events if event.get("attempt_id") == attempt["attempt_id"]
        ]
        assert per_attempt == [
            "attempt_started",
            "move_started",
            "move_result",
            "observation_read_result",
        ]
    move_results = [event for event in events if event["event"] == "move_result"]
    assert all(event["raw_event_ref"] for event in move_results)
    assert all(len(event["raw_event_hash"]) == 64 for event in move_results)
    terminal = events[-1]
    assert terminal["event"] == "execution_terminal"
    assert terminal["classification"] == "success"
    assert terminal["budget_used"]["candidates"] == 4
    assert terminal["budget_used"]["backend_actions"] == 5
    assert terminal["budget_stop_reason"] is None


class _ContradictoryMoveThor(_ThorBase):
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.last_event = _NavigationEvent()
        self.actions: list[dict[str, Any]] = []
        self.teleport_count = 0

    def step(self, action: dict[str, Any]) -> _NavigationEvent | None:
        self.actions.append(action)
        if action["action"] == "GetReachablePositions":
            self.last_event = _NavigationEvent(reachable=True)
            return self.last_event
        if action["action"] != "TeleportFull":
            raise AssertionError(action)

        self.teleport_count += 1
        if self.teleport_count > 1:
            raise AssertionError("navigation continued after an uncertain move")
        if self.mode == "exception":
            raise RuntimeError("TeleportFull raised")
        if self.mode == "no_event":
            return None
        if self.mode == "failure_pose_changed":
            self.last_event = _NavigationEvent(
                action_success=False,
                requested_action=action,
            )
        elif self.mode == "success_pose_mismatch":
            self.last_event = _NavigationEvent(
                exact_visible=True,
                detections={_EXACT_SHELF_ID: [0, 0, 20, 20]},
                requested_action=action,
                actual_pose="mismatch",
            )
        elif self.mode == "missing_actual_pose":
            self.last_event = _NavigationEvent(
                exact_visible=True,
                detections={_EXACT_SHELF_ID: [0, 0, 20, 20]},
                requested_action=action,
                include_agent=False,
            )
        elif self.mode == "missing_exact_target_read":
            self.last_event = _NavigationEvent(
                exact_visible=False,
                detections={_EXACT_SHELF_ID: [0, 0, 20, 20]},
                requested_action=action,
                include_exact=False,
            )
        else:
            raise AssertionError(self.mode)
        return self.last_event


@pytest.mark.parametrize(
    "mode",
    [
        "success_pose_mismatch",
        "failure_pose_changed",
        "exception",
        "no_event",
        "missing_actual_pose",
        "missing_exact_target_read",
    ],
)
def test_navigation_move_contradictions_are_terminal_uncertain(mode: str) -> None:
    thor_env = _ContradictoryMoveThor(mode)
    adapter = _adapter_for_thor(thor_env)

    result = adapter.go_to_target(
        "shelf 1",
        tool_name="robot_go_to",
        tool_args={"target": "shelf 1"},
    )

    assert result.success is False
    assert result.failure_reason == "execution_state_uncertain"
    assert result.state.invalid_action_count == 0
    assert thor_env.teleport_count == 1


class _FakeClock:
    def __init__(self) -> None:
        self.value_ms = 0.0

    def __call__(self) -> float:
        return self.value_ms

    def advance(self, elapsed_ms: float) -> None:
        self.value_ms += elapsed_ms


class _BudgetThor(_ThorBase):
    def __init__(self, *, clock: _FakeClock, action_elapsed_ms: float) -> None:
        self.clock = clock
        self.action_elapsed_ms = action_elapsed_ms
        self.last_event = _NavigationEvent()
        self.actions: list[dict[str, Any]] = []
        self.teleport_count = 0

    def step(self, action: dict[str, Any]) -> _NavigationEvent:
        self.actions.append(action)
        if action["action"] == "GetReachablePositions":
            self.last_event = _NavigationEvent(reachable=True)
            return self.last_event
        if action["action"] != "TeleportFull":
            raise AssertionError(action)
        self.teleport_count += 1
        self.clock.advance(self.action_elapsed_ms)
        self.last_event = _NavigationEvent(
            action_success=True,
            exact_visible=False,
            detections={},
            requested_action=action,
        )
        return self.last_event


@pytest.mark.parametrize(
    (
        "max_candidates",
        "max_backend_actions",
        "max_elapsed_ms",
        "action_elapsed_ms",
        "expected_stop_reason",
        "expected_teleports",
        "expected_backend_actions",
    ),
    [
        pytest.param(
            1,
            100,
            10_000,
            0.0,
            "max_navigation_candidates",
            1,
            2,
            id="candidates",
        ),
        pytest.param(
            100,
            1,
            10_000,
            0.0,
            "max_navigation_backend_actions",
            0,
            1,
            id="actions",
        ),
        pytest.param(
            100,
            100,
            1,
            2.0,
            "max_navigation_elapsed_ms",
            1,
            2,
            id="elapsed",
        ),
    ],
)
def test_navigation_budget_stops_without_an_n_plus_one_teleport(
    max_candidates: int,
    max_backend_actions: int,
    max_elapsed_ms: int,
    action_elapsed_ms: float,
    expected_stop_reason: str,
    expected_teleports: int,
    expected_backend_actions: int,
) -> None:
    clock = _FakeClock()
    thor_env = _BudgetThor(clock=clock, action_elapsed_ms=action_elapsed_ms)
    adapter = _adapter_for_thor(thor_env)
    # These are fixed per run in production. Direct injection keeps this unit
    # test independent of CLI/config plumbing while still exercising Adapter.
    adapter._navigation_budget = SimpleNamespace(  # type: ignore[attr-defined]
        max_navigation_candidates=max_candidates,
        max_navigation_backend_actions=max_backend_actions,
        max_navigation_elapsed_ms=max_elapsed_ms,
    )
    adapter._monotonic_ms = clock  # type: ignore[attr-defined]

    result = adapter.go_to_target(
        "shelf 1",
        tool_name="robot_go_to",
        tool_args={"target": "shelf 1"},
    )

    assert result.success is False
    assert result.failure_reason == "oracle_navigation_failed"
    assert result.state.invalid_action_count == 0
    assert result.tool_args["budget_stop_reason"] == expected_stop_reason
    assert "budget_stop_reason" not in result.to_model_visible_data()
    assert "tool_args" not in result.to_model_visible_data()
    assert thor_env.teleport_count == expected_teleports
    assert result.backend_action_count == expected_backend_actions
    terminal = result.trace_events[-1]
    assert terminal["event"] == "execution_terminal"
    assert terminal["budget_stop_reason"] == expected_stop_reason
    assert terminal["budget_used"]["backend_actions"] == expected_backend_actions


def test_navigation_success_frame_pixels_come_from_the_final_success_event(
    tmp_path: Path,
) -> None:
    pil_image = pytest.importorskip("PIL.Image")
    numpy = pytest.importorskip("numpy")
    initial_frame = numpy.full((4, 4, 3), 3, dtype=numpy.uint8)
    reachable_frame = numpy.full((4, 4, 3), 17, dtype=numpy.uint8)
    success_frame = numpy.full((4, 4, 3), 231, dtype=numpy.uint8)

    class FrameThor(_ThorBase):
        def __init__(self) -> None:
            self.last_event = _NavigationEvent(frame=initial_frame)
            self.final_success_event: _NavigationEvent | None = None

        def step(self, action: dict[str, Any]) -> _NavigationEvent:
            if action["action"] == "GetReachablePositions":
                self.last_event = _NavigationEvent(
                    reachable=True,
                    frame=reachable_frame,
                )
            elif action["action"] == "TeleportFull":
                self.last_event = _NavigationEvent(
                    exact_visible=True,
                    detections={_EXACT_SHELF_ID: [0, 0, 3, 3]},
                    requested_action=action,
                    frame=success_frame,
                )
                self.final_success_event = self.last_event
            else:
                raise AssertionError(action)
            return self.last_event

    thor_env = FrameThor()
    adapter = _adapter_for_thor(thor_env, frame_dir=tmp_path / "frames")

    result = adapter.go_to_target(
        "shelf 1",
        tool_name="robot_go_to",
        tool_args={"target": "shelf 1"},
    )

    assert result.success is True
    assert thor_env.final_success_event is not None
    assert result.state.frame_path is not None
    frame_path = Path(result.state.frame_path)
    assert frame_path.exists()
    assert frame_path.stat().st_size > 0
    saved_pixels = numpy.asarray(pil_image.open(frame_path).convert("RGB"))
    assert numpy.array_equal(saved_pixels, thor_env.final_success_event.frame)
    assert numpy.array_equal(saved_pixels, success_frame)
    assert not numpy.array_equal(saved_pixels, reachable_frame)


def test_external_read_preserves_old_parent_and_exact_target_child_membership() -> None:
    class PutTerminalThor(_ThorBase):
        def __init__(self) -> None:
            self.last_event = SimpleNamespace(
                metadata={
                    "lastActionSuccess": True,
                    "inventoryObjects": [],
                    "agent": {
                        "position": {"x": 0.5, "y": 0.9010564, "z": 0.0},
                        "rotation": {"x": 0.0, "y": 90.0, "z": 0.0},
                        "cameraHorizon": 15.0,
                    },
                    "objects": [
                        {
                            "objectId": _PENCIL_ID,
                            "objectType": "Pencil",
                            "parentReceptacles": [_DESK_ID, _EXACT_SHELF_ID],
                        },
                        {
                            "objectId": _EXACT_SHELF_ID,
                            "objectType": "Shelf",
                            "receptacle": True,
                            "receptacleObjectIds": [_PENCIL_ID],
                        },
                        {
                            "objectId": _OTHER_SHELF_ID,
                            "objectType": "Shelf",
                            "receptacle": True,
                            "receptacleObjectIds": [],
                        },
                    ],
                },
                frame=None,
                instance_detections2D={},
            )

    adapter = _adapter_for_thor(PutTerminalThor())

    read = adapter.read_external_state(
        exact_object_id=_PENCIL_ID,
        exact_target_id=_EXACT_SHELF_ID,
    )

    assert read.status == "ok"
    assert read.inventory_object_ids == ()
    assert read.held_object_id is None
    assert read.exact_object_present is True
    assert _DESK_ID in read.object_parent_ids
    assert _EXACT_SHELF_ID in read.object_parent_ids
    assert _PENCIL_ID in read.target_child_ids
    assert _OTHER_SHELF_ID not in read.object_parent_ids


_PUT_CURRENT_POSE = AgentPose(
    x=0.0,
    y=0.9010564,
    z=0.0,
    rotation=0.0,
    horizon=0.0,
)
_PUT_LOCAL_POSE = AgentPose(
    x=0.5,
    y=0.9010564,
    z=0.0,
    rotation=90.0,
    horizon=15.0,
)


class _PutEvent:
    def __init__(
        self,
        *,
        action_success: bool,
        pose: AgentPose,
        placed: bool = False,
    ) -> None:
        self.frame = None
        self.instance_detections2D: dict[str, list[int]] = {}
        self.metadata = {
            "lastActionSuccess": action_success,
            "errorMessage": "" if action_success else "No valid Receptacle found",
            "inventoryObjects": (
                [] if placed else [{"objectId": _PENCIL_ID, "objectType": "Pencil"}]
            ),
            "agent": {
                "position": {"x": pose.x, "y": pose.y, "z": pose.z},
                "rotation": {"x": 0.0, "y": pose.rotation, "z": 0.0},
                "cameraHorizon": pose.horizon,
            },
            "objects": [
                {
                    "objectId": _PENCIL_ID,
                    "objectType": "Pencil",
                    "pickupable": True,
                    "isPickedUp": not placed,
                    "parentReceptacles": ([_DESK_ID, _EXACT_SHELF_ID] if placed else [_DESK_ID]),
                    "position": {"x": 0.1, "y": 0.88, "z": 0.2},
                },
                {
                    "objectId": _EXACT_SHELF_ID,
                    "objectType": "Shelf",
                    "receptacle": True,
                    "receptacleObjectIds": [_PENCIL_ID] if placed else [],
                    "position": {"x": 1.0, "y": 0.87, "z": 1.0},
                },
                {
                    "objectId": _DESK_ID,
                    "objectType": "Desk",
                    "receptacle": True,
                    "receptacleObjectIds": [_PENCIL_ID],
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                },
            ],
        }


class _ScriptedPutThor(_ThorBase):
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.pose = _PUT_CURRENT_POSE
        self.placed = False
        self.put_count = 0
        self.actions: list[dict[str, Any]] = []
        self.last_event = _PutEvent(
            action_success=True,
            pose=self.pose,
            placed=False,
        )

    def step(self, action: dict[str, Any]) -> _PutEvent:
        self.actions.append(action)
        if action["action"] == "TeleportFull":
            assert self.mode in {"success_after_local", "all_fail"}
            assert self.put_count == 1
            self.pose = AgentPose(
                x=float(action["x"]),
                y=float(action["y"]),
                z=float(action["z"]),
                rotation=float(action["rotation"]),
                horizon=float(action["horizon"]),
            )
            self.last_event = _PutEvent(
                action_success=True,
                pose=self.pose,
                placed=False,
            )
            return self.last_event
        if action["action"] != "PutObject":
            raise AssertionError(action)
        assert action == {
            "action": "PutObject",
            "objectId": _PENCIL_ID,
            "receptacleObjectId": _EXACT_SHELF_ID,
            "forceAction": True,
            "placeStationary": True,
        }
        self.put_count += 1
        if self.mode == "success_after_local" and self.put_count == 2:
            self.placed = True
            self.last_event = _PutEvent(
                action_success=True,
                pose=self.pose,
                placed=True,
            )
        elif self.mode == "contradictory_success":
            assert self.put_count == 1
            self.last_event = _PutEvent(
                action_success=True,
                pose=self.pose,
                placed=False,
            )
        else:
            assert self.put_count <= 2
            self.last_event = _PutEvent(
                action_success=False,
                pose=self.pose,
                placed=False,
            )
        return self.last_event

    def get_goal_satisfied(self) -> bool:
        return self.placed

    def get_goal_conditions_met(self) -> tuple[int, int]:
        return (1, 1) if self.placed else (0, 1)


class _NoPutRequestThor(_ThorBase):
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.last_event = _PutEvent(
            action_success=True,
            pose=_PUT_CURRENT_POSE,
            placed=False,
        )

    def step(self, action: dict[str, Any]) -> _PutEvent:
        self.actions.append(action)
        raise AssertionError(f"unexpected THOR request: {action}")


def _lock_put_context(
    adapter: AlfworldEnvAdapter,
    *,
    local_candidates: tuple[AgentPose, ...] = (_PUT_LOCAL_POSE,),
) -> PoseContext:
    context = PoseContext.lock(
        context_id="put-context-1",
        scene_generation=1,
        goal_generation=0,
        source_event_sequence=0,
        source_frame_hash="put-source-frame",
        anchor_object_id=_EXACT_SHELF_ID,
        current_actual_pose=_PUT_CURRENT_POSE,
        local_candidates=local_candidates,
        created_tool_call_id="call-put-1",
    )
    adapter._pose_context = context  # type: ignore[attr-defined]
    adapter._put_budget = ExecutionBudget(  # type: ignore[attr-defined]
        max_pose_candidates=len(context.locked_candidates),
        max_backend_actions=10,
        max_elapsed_ms=10_000,
    )
    return context


def _put(
    adapter: AlfworldEnvAdapter,
    *,
    object_label: str = "pencil 1",
    target_label: str = "shelf 1",
) -> Any:
    return adapter.manipulate_with_thor(
        action="put",
        tool_name="robot_manipulate",
        tool_args={
            "action": "put",
            "object": object_label,
            "target_receptacle": target_label,
        },
    )


def test_put_retries_locked_local_pose_with_exact_ids_and_stops_at_first_success() -> None:
    thor_env = _ScriptedPutThor("success_after_local")
    adapter = _adapter_for_thor(thor_env)
    context = _lock_put_context(adapter)

    result = _put(adapter)

    assert result.success is True
    assert result.failure_reason is None
    assert result.state.invalid_action_count == 0
    assert result.state.inventory == "You are carrying nothing."
    assert result.tool_args["final_classification"] == "success"
    assert result.tool_args["held_object_id"] == _PENCIL_ID
    assert result.tool_args["target_object_id"] == _EXACT_SHELF_ID
    assert result.tool_args["locked_candidates_hash"] == context.candidates_hash
    assert result.tool_args["pose_candidates_attempted"] == 2
    assert result.tool_args["put_attempt_count"] == 2
    assert result.tool_args["backend_action_count"] == 3
    model_args = result.to_model_visible_data()
    for internal_key in (
        "held_object_id",
        "target_object_id",
        "locked_candidates_hash",
        "pose_candidates_attempted",
        "put_attempt_count",
        "backend_action_count",
    ):
        assert internal_key not in model_args
    assert result.backend_action_count == 3
    assert result.trace_events[0]["locked_candidates_hash"] == context.candidates_hash
    assert result.trace_events[0]["held_object_id"] == _PENCIL_ID
    assert result.trace_events[0]["target_receptacle_id"] == _EXACT_SHELF_ID
    assert result.trace_events[-1]["classification"] == "success"
    assert [action["action"] for action in thor_env.actions] == [
        "PutObject",
        "TeleportFull",
        "PutObject",
    ]
    final_read = adapter.read_external_state(
        exact_object_id=_PENCIL_ID,
        exact_target_id=_EXACT_SHELF_ID,
    )
    assert final_read.status == "ok"
    assert _PENCIL_ID not in final_read.inventory_object_ids
    assert _EXACT_SHELF_ID in final_read.object_parent_ids
    assert _PENCIL_ID in final_read.target_child_ids


def test_put_without_pose_context_returns_navigation_required_without_thor_request() -> None:
    thor_env = _NoPutRequestThor()
    adapter = _adapter_for_thor(thor_env)

    result = _put(adapter)

    assert result.success is False
    assert result.failure_reason == "navigation_required"
    assert result.state.invalid_action_count == 0
    assert thor_env.actions == []
    assert [event["event"] for event in result.trace_events] == [
        "context_created",
        "context_invalidated",
        "execution_terminal",
    ]
    assert result.trace_events[-1]["classification"] == "navigation_required"
    assert result.trace_events[-1]["budget_used"]["backend_actions"] == 0


def test_navigation_explicit_target_miss_still_writes_terminal_execution_trace() -> None:
    thor_env = _NoPutRequestThor()
    adapter = _adapter_for_thor(thor_env)

    result = adapter.go_to_target(
        "shelf 2",
        tool_name="robot_go_to",
        tool_args={"target": "shelf 2", "tool_call_id": "call-nav-missing"},
    )

    assert result.success is False
    assert result.failure_reason == "target_not_found"
    assert thor_env.actions == []
    assert [event["event"] for event in result.trace_events] == [
        "context_created",
        "context_invalidated",
        "execution_terminal",
    ]
    assert result.trace_events[-1]["classification"] == "target_not_found"
    assert result.trace_events[-1]["budget_used"]["backend_actions"] == 0


@pytest.mark.parametrize(
    ("object_label", "target_label"),
    [
        pytest.param("pencil 2", "shelf 1", id="missing-explicit-object"),
        pytest.param("pencil 1", "shelf 2", id="missing-explicit-target"),
    ],
)
def test_put_explicit_index_miss_never_falls_back_to_another_instance(
    object_label: str,
    target_label: str,
) -> None:
    thor_env = _NoPutRequestThor()
    adapter = _adapter_for_thor(thor_env)
    _lock_put_context(adapter)

    result = _put(
        adapter,
        object_label=object_label,
        target_label=target_label,
    )

    assert result.success is False
    assert result.failure_reason == "target_not_found"
    assert result.state.invalid_action_count == 0
    assert thor_env.actions == []


def test_put_exhaustion_is_terminal_harness_failure_without_invalid_increment() -> None:
    thor_env = _ScriptedPutThor("all_fail")
    adapter = _adapter_for_thor(thor_env)
    _lock_put_context(adapter)

    result = _put(adapter)

    assert result.success is False
    assert result.failure_reason == "harness_operation_failure"
    assert result.state.invalid_action_count == 0
    assert result.tool_args["final_classification"] == "harness_operation_failure"
    assert result.tool_args["terminal"] is True
    assert result.tool_args["score_eligible"] is False
    assert result.tool_args["budget_stop_reason"] == "max_pose_candidates"
    assert result.tool_args["pose_candidates_attempted"] == 2
    assert result.tool_args["put_attempt_count"] == 2
    assert result.tool_args["backend_action_count"] == 3
    model_args = result.to_model_visible_data()
    assert "budget_stop_reason" not in model_args
    assert "pose_candidates_attempted" not in model_args
    assert "put_attempt_count" not in model_args
    assert "backend_action_count" not in model_args
    assert result.backend_action_count == 3
    assert result.trace_events[-1]["budget_stop_reason"] == "max_pose_candidates"
    assert result.trace_events[-1]["budget_used"]["candidates"] == 2
    assert result.trace_events[-1]["budget_used"]["put_attempts"] == 2
    assert result.trace_events[-1]["budget_used"]["backend_actions"] == 3
    assert [action["action"] for action in thor_env.actions] == [
        "PutObject",
        "TeleportFull",
        "PutObject",
    ]


def test_put_success_with_unchanged_terminal_state_stops_uncertain_immediately() -> None:
    thor_env = _ScriptedPutThor("contradictory_success")
    adapter = _adapter_for_thor(thor_env)
    _lock_put_context(adapter)

    result = _put(adapter)

    assert result.success is False
    assert result.failure_reason == "execution_state_uncertain"
    assert result.state.invalid_action_count == 0
    assert result.tool_args["final_classification"] == "execution_state_uncertain"
    assert result.tool_args["terminal"] is True
    assert result.tool_args["score_eligible"] is False
    assert result.tool_args["pose_candidates_attempted"] == 1
    assert result.tool_args["put_attempt_count"] == 1
    assert result.tool_args["backend_action_count"] == 1
    model_args = result.to_model_visible_data()
    assert "pose_candidates_attempted" not in model_args
    assert "put_attempt_count" not in model_args
    assert "backend_action_count" not in model_args
    assert result.backend_action_count == 1
    assert result.trace_events[-1]["budget_used"]["candidates"] == 1
    assert result.trace_events[-1]["budget_used"]["put_attempts"] == 1
    assert result.trace_events[-1]["budget_used"]["backend_actions"] == 1
    assert thor_env.actions == [
        {
            "action": "PutObject",
            "objectId": _PENCIL_ID,
            "receptacleObjectId": _EXACT_SHELF_ID,
            "forceAction": True,
            "placeStationary": True,
        }
    ]
