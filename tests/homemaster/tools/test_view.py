from __future__ import annotations

import pytest

from homemaster.tools.catalog import ToolCatalog, ToolCatalogError, ToolLookupStatus
from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)


class Executor:
    async def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, data={"ok": True})


def _tool(internal_id: str, alias: str, source: str) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id=internal_id,
            model_alias=alias,
            description=f"{source} {alias}",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            output_schema={},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source=source, reference=f"{source}.{alias}"),
            version="1.0.0",
        ),
        executor=Executor(),
    )


def _catalog() -> tuple[ToolCatalog, RegisteredTool, RegisteredTool, RegisteredTool]:
    catalog = ToolCatalog()
    observe = _tool("home.observe.v1", "observe", "home")
    move = _tool("home.robot_go_to.v1", "robot_go_to", "home")
    alfworld_observe = _tool("alfworld.observe.v1", "observe", "alfworld")
    for tool in (observe, move, alfworld_observe):
        catalog.register(tool)
    return catalog, observe, move, alfworld_observe


def test_view_freezes_ordered_manifests_and_execution_lookup() -> None:
    catalog, observe, move, _ = _catalog()
    view = catalog.freeze(("home.robot_go_to.v1", "home.observe.v1"))

    assert view.enabled_tool_ids == ("home.robot_go_to.v1", "home.observe.v1")
    assert view.list_tools() == (move, observe)
    assert [manifest["name"] for manifest in view.manifests()] == [
        "robot_go_to",
        "observe",
    ]
    assert view.lookup("robot_go_to").tool is move
    assert view.lookup("home.robot_go_to.v1").tool is move
    assert view.lookup("alfworld.observe.v1").status is ToolLookupStatus.TOOL_DISABLED
    assert view.lookup("definitely_unknown").status is ToolLookupStatus.UNKNOWN_TOOL
    assert view.is_enabled("home.observe.v1") is True
    assert view.is_enabled("alfworld.observe.v1") is False

    manifests = view.manifests()
    manifests[0]["name"] = "mutated"
    assert view.manifests()[0]["name"] == "robot_go_to"


def test_view_rejects_alias_conflicts_and_reports_both_provenances() -> None:
    catalog, _, _, _ = _catalog()
    with pytest.raises(ToolCatalogError) as caught:
        catalog.freeze(("home.observe.v1", "alfworld.observe.v1"))
    message = str(caught.value)
    assert "model alias conflict: observe" in message
    assert "home:home.observe" in message
    assert "alfworld:alfworld.observe" in message


def test_view_rejects_duplicate_and_unknown_enabled_ids() -> None:
    catalog, _, _, _ = _catalog()
    with pytest.raises(ToolCatalogError, match="must be unique"):
        catalog.freeze(("home.observe.v1", "home.observe.v1"))
    with pytest.raises(ToolCatalogError, match="unknown enabled tool ids"):
        catalog.freeze(("missing.observe.v1",))
    with pytest.raises(TypeError, match="ordered sequence"):
        catalog.freeze("home.observe.v1")


def test_existing_views_are_isolated_from_later_catalog_registration() -> None:
    catalog, _, move, _ = _catalog()
    first = catalog.freeze(("home.robot_go_to.v1",))
    later = _tool("home.inspect.v1", "inspect", "home")
    catalog.register(later)
    second = catalog.freeze(("home.inspect.v1", "home.robot_go_to.v1"))

    assert first.list_tools() == (move,)
    assert first.lookup("inspect").status is ToolLookupStatus.UNKNOWN_TOOL
    assert second.lookup("inspect").tool is later
    assert first.view_id != second.view_id


def test_view_id_is_stable_and_order_sensitive() -> None:
    catalog, _, _, _ = _catalog()
    first = catalog.freeze(("home.observe.v1", "home.robot_go_to.v1"))
    same = catalog.freeze(("home.observe.v1", "home.robot_go_to.v1"))
    reversed_view = catalog.freeze(("home.robot_go_to.v1", "home.observe.v1"))
    empty = catalog.freeze(())

    assert first.view_id == same.view_id
    assert first.view_id != reversed_view.view_id
    assert len(empty.view_id) == 64
    assert empty.manifests() == ()


def test_disabled_tools_never_enter_provider_manifests() -> None:
    catalog, _, _, _ = _catalog()
    view = catalog.freeze(("home.robot_go_to.v1",))
    encoded = repr(view.manifests())
    assert "robot_go_to" in encoded
    assert "observe" not in encoded
    assert view.lookup("observe").status is ToolLookupStatus.TOOL_DISABLED
