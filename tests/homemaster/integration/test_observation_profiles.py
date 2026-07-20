from __future__ import annotations

from homemaster.adapters import build_environment_profiles
from homemaster.tools.contracts import PostActionObservation


def test_each_environment_profile_exposes_exactly_one_observe_variant() -> None:
    profiles = build_environment_profiles()

    for environment, profile in profiles.items():
        assert profile.model_tool_names.count("observe") == 1
        assert [
            internal_id
            for internal_id in profile.enabled_tool_ids
            if internal_id.endswith(".observe.v1")
        ] == [f"{environment}.observe.v1"]
        assert "robot_observe" not in profile.model_tool_names
        assert "browser_observe" not in profile.model_tool_names


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


def test_environment_profiles_execute_typed_observation_policies() -> None:
    profiles = build_environment_profiles()
    home = profiles["home"].view.lookup("robot_manipulate").tool
    alfworld = profiles["alfworld"].view.lookup("robot_go_to").tool
    coworker = profiles["coworker"].view.lookup("browser_navigate").tool
    assert home is not None and alfworld is not None and coworker is not None

    assert home.definition.verification_policy.requires_pre_observation == "current_bound"
    assert (
        alfworld.definition.verification_policy.post_action_observation
        is PostActionObservation.FRESH_AFTER_BACKEND_ADVANCE
    )
    assert (
        coworker.definition.verification_policy.post_action_observation
        is PostActionObservation.FRESH_AFTER_BACKEND_ADVANCE
    )
