from __future__ import annotations

from types import SimpleNamespace

from workers.alfworld_worker.thor_backend import ThorBackend, _plan_object_ids


def test_plan_object_ids_preserve_exact_alfred_targets() -> None:
    assert _plan_object_ids(
        {
            "plan": {
                "high_pddl": [
                    {
                        "discrete_action": {"action": "PickupObject"},
                        "planner_action": {"objectId": "AlarmClock|target"},
                    },
                    {
                        "discrete_action": {"action": "ToggleObject"},
                        "planner_action": {"objectId": "DeskLamp|target"},
                    },
                ]
            }
        }
    ) == {"take": "AlarmClock|target", "use": "DeskLamp|target"}


def test_use_dispatches_to_thor_toggle_instead_of_text_command() -> None:
    lamp = {
        "objectId": "DeskLamp|1",
        "objectType": "DeskLamp",
        "name": "DeskLamp",
        "toggleable": True,
        "isToggled": False,
    }
    event = SimpleNamespace(
        metadata={
            "lastActionSuccess": True,
            "errorMessage": "",
            "objects": [lamp],
            "inventoryObjects": [],
        }
    )

    class FakeThor:
        def __init__(self) -> None:
            self.last_event = event
            self.calls: list[dict[str, object]] = []

        def step(self, action: dict[str, object]) -> SimpleNamespace:
            self.calls.append(action)
            lamp["isToggled"] = True
            return event

        def get_goal_satisfied(self) -> bool:
            return False

        def get_goal_conditions_met(self) -> tuple[int, int]:
            return (0, 1)

    thor = FakeThor()
    backend = object.__new__(ThorBackend)
    backend._env = SimpleNamespace(
        envs=[
            SimpleNamespace(
                env=thor,
                controller=SimpleNamespace(
                    get_admissible_commands=lambda: [],
                    objects={
                        "DeskLamp|1": {
                            "object_id": "DeskLamp|1",
                            "object_type": "DeskLamp",
                            "num_id": "desklamp 1",
                        }
                    },
                    receptacles={},
                ),
            )
        ]
    )
    backend._state = {"objects": [lamp], "admissible_commands": []}
    backend._scene_objects = [lamp]
    backend._payload = {"allow_offscreen_object_navigation": False}
    backend._goal_object_ids = {"use": "DeskLamp|1"}
    backend._state_sequence = 1
    backend._make_state = lambda observations, info, *, command, reward=0.0, done=False: {
        "objects": [lamp],
        "last_command": command,
        "last_feedback": "Executed use DeskLamp.",
        "won": False,
        "done": False,
    }

    result = backend.act(
        {
            "kind": "manipulate",
            "action": "use",
            "tool_name": "robot_manipulate",
            "tool_args": {"action": "use", "object": "desklamp"},
        }
    )

    assert result["external_return_code"] == 0
    assert thor.calls == [
        {"action": "ToggleObjectOn", "objectId": "DeskLamp|1", "forceAction": True}
    ]
    assert result["step"]["success"] is True
