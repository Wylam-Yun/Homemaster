from __future__ import annotations

from homemaster.adapters import build_environment_profiles
from homemaster.tools.contracts import ConcurrencyPolicy


def test_each_environment_profile_exposes_exactly_one_observe_variant() -> None:
    profiles = build_environment_profiles()

    for _environment, profile in profiles.items():
        assert profile.model_tool_names.count("observe") == 1
        observe_ids = [
            internal_id
            for internal_id in profile.enabled_tool_ids
            if internal_id.endswith(".observe.v1")
        ]
        assert observe_ids == [
            "core.observe.v1"
        ]
        assert "robot_observe" not in profile.model_tool_names


def test_home_and_alfworld_navigation_surface_uses_only_robot_go_to() -> None:
    profiles = build_environment_profiles()

    for environment in ("home", "alfworld"):
        names = profiles[environment].model_tool_names
        assert "robot_go_to" in names
        assert "robot_navigate" not in names
        assert names.count("robot_go_to") == 1


def test_coworker_profile_remains_exactly_eleven_tools_in_order() -> None:
    profile = build_environment_profiles()["coworker"]

    assert profile.model_tool_names == (
        "task_planner",
        "task_progress_check",
        "skill_view",
        "browser_navigate",
        "observe",
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_wait",
        "terminal_execute",
        "sop_decide",
    )


def test_environment_actions_are_not_gated_by_screenshots() -> None:
    profiles = build_environment_profiles()
    home = profiles["home"].view.lookup("robot_manipulate").tool
    alfworld = profiles["alfworld"].view.lookup("robot_go_to").tool
    coworker = profiles["coworker"].view.lookup("browser_navigate").tool
    assert home is not None and alfworld is not None and coworker is not None

    assert home.definition.model_alias == "robot_manipulate"
    assert alfworld.definition.model_alias == "robot_go_to"
    assert coworker.definition.model_alias == "browser_navigate"


def test_coworker_action_tools_remain_available() -> None:
    view = build_environment_profiles()["coworker"].view

    for name in (
        "task_planner",
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_wait",
        "sop_decide",
    ):
        tool = view.lookup(name).tool
        assert tool is not None


def test_physical_mutations_use_typed_backend_resource_keys() -> None:
    profiles = build_environment_profiles()

    for environment, aliases in {
        "home": ("robot_go_to", "robot_manipulate"),
        "alfworld": ("robot_go_to", "robot_manipulate"),
        "coworker": ("browser_navigate", "browser_click", "browser_fill", "browser_select"),
    }.items():
        for alias in aliases:
            tool = profiles[environment].view.lookup(alias).tool
            assert tool is not None
            assert tool.definition.concurrency_policy is ConcurrencyPolicy.RESOURCE_KEY
            assert tool.definition.resource_key == f"{environment}:backend"
