from homemaster.adapters import build_universal_tool_registry


def test_universal_registry_has_one_observe_and_one_navigation_contract() -> None:
    registry = build_universal_tool_registry()

    assert registry.all_names().count("observe") == 1
    assert registry.all_names().count("robot_go_to") == 1
    assert "robot_navigate" not in registry.all_names()


def test_home_alfworld_and_coworker_tools_share_one_surface() -> None:
    names = set(build_universal_tool_registry().all_names())

    assert {
        "bash",
        "robot_go_to",
        "robot_manipulate",
        "browser_navigate",
        "browser_click",
        "terminal_execute",
    } <= names


def test_physical_mutations_keep_resource_key_serialization() -> None:
    registry = build_universal_tool_registry()

    for name in (
        "robot_go_to",
        "robot_manipulate",
        "browser_navigate",
        "browser_click",
        "browser_fill",
        "browser_select",
    ):
        tool = registry.get(name)
        assert tool is not None
        assert tool.concurrency_policy == "resource_key"
        assert tool.resource_key is not None and tool.resource_key.endswith(":backend")
