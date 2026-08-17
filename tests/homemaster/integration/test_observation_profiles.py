from homemaster.adapters import build_tool_registry


def test_alfworld_registry_has_one_observe_and_one_navigation_contract() -> None:
    registry = build_tool_registry(environment="alfworld")

    assert registry.all_names().count("observe") == 1
    assert registry.all_names().count("robot_go_to") == 1
    assert "robot_navigate" not in registry.all_names()


def test_environment_registries_expose_only_the_selected_surface() -> None:
    common = set(build_tool_registry(environment=None).all_names())
    local_robot = set(build_tool_registry(environment="local_robot").all_names())
    alfworld = set(build_tool_registry(environment="alfworld").all_names())
    coworker = set(build_tool_registry(environment="coworker").all_names())

    assert {"terminal", "search_files", "observe", "ask_user_question"} <= common
    assert not {"robot_go_to", "browser_navigate"} & common
    assert {"robot_go_to", "robot_manipulate"} <= local_robot
    assert "browser_navigate" not in local_robot
    assert {"robot_go_to", "robot_manipulate"} <= alfworld
    assert "browser_navigate" not in alfworld
    assert {"browser_navigate", "browser_click", "terminal_execute"} <= coworker
    assert "robot_go_to" not in coworker


def test_physical_mutations_keep_resource_key_serialization() -> None:
    robot_registry = build_tool_registry(environment="alfworld")
    coworker_registry = build_tool_registry(environment="coworker")

    for name in ("robot_go_to", "robot_manipulate"):
        tool = robot_registry.get(name)
        assert tool is not None
        assert tool.concurrency_policy == "resource_key"
        assert tool.resource_key is not None and tool.resource_key.endswith(":backend")
    for name in ("browser_navigate", "browser_click", "browser_fill", "browser_select"):
        tool = coworker_registry.get(name)
        assert tool is not None
        assert tool.concurrency_policy == "resource_key"
        assert tool.resource_key is not None and tool.resource_key.endswith(":backend")
