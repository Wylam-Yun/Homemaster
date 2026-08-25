from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from homemaster.browser.contracts import BrowserSession, BrowserSessionError, BrowserSnapshot
from homemaster.browser.playwright_session import PlaywrightBrowserSession
from homemaster.browser.policy import BrowserPolicy
from homemaster.browser.tools import build_browser_registered_tools, build_browser_run_registry
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolExecutionContext, ToolRegistry, ToolRegistryError

EXPECTED_METHODS = {
    "navigate",
    "inspect",
    "fill",
    "select",
    "check",
    "uncheck",
    "click",
    "backfill",
    "wait",
    "screenshot",
    "aclose",
}


def test_browser_session_protocol_public_method_audit() -> None:
    methods = {
        name
        for name, value in inspect.getmembers(BrowserSession, inspect.isfunction)
        if not name.startswith("_")
    }

    assert methods == EXPECTED_METHODS


def test_playwright_session_implements_every_public_protocol_method(tmp_path: Path) -> None:
    session = PlaywrightBrowserSession(
        session_id="audit",
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_dir=tmp_path,
    )
    assert all(callable(getattr(session, name, None)) for name in EXPECTED_METHODS)


def test_browser_core_has_no_coworker_imports() -> None:
    root = Path(__file__).parents[3] / "src" / "homemaster" / "browser"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "benchmarking.coworker_demo" not in source


def test_browser_registered_tools_lock_schema_and_execution_matrix() -> None:
    tools = build_browser_registered_tools(object())
    definitions = {tool.definition.model_alias: tool.definition for tool in tools}

    assert tuple(definitions) == (
        "browser_navigate",
        "browser_inspect",
        "browser_fill",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_click",
        "browser_backfill",
        "browser_wait",
        "observe",
    )
    assert {
        name: tuple(definition.input_schema.get("required", ()))
        for name, definition in definitions.items()
    } == {
        "browser_navigate": ("url",),
        "browser_inspect": (),
        "browser_fill": ("snapshot_id", "element_id", "value"),
        "browser_select": ("snapshot_id", "element_id", "option"),
        "browser_check": ("snapshot_id", "element_id"),
        "browser_uncheck": ("snapshot_id", "element_id"),
        "browser_click": ("snapshot_id", "element_id"),
        "browser_backfill": ("snapshot_id", "element_id"),
        "browser_wait": ("condition",),
        "observe": (),
    }
    writes = {
        "browser_navigate": ("browser.navigate",),
        "browser_fill": ("browser.dom_write",),
        "browser_select": ("browser.dom_write",),
        "browser_check": ("browser.dom_write",),
        "browser_uncheck": ("browser.dom_write",),
        "browser_click": ("browser.interact",),
        "browser_backfill": ("browser.dom_write",),
    }
    for name, definition in definitions.items():
        assert definition.resource_key == "browser:backend"
        assert definition.concurrency_policy.value == "resource_key"
        if name in writes:
            assert definition.state_effects == writes[name]
            expected = {"device.control"}
            if name == "browser_navigate":
                expected.add("network.http")
            assert set(definition.required_capabilities) == expected
            assert definition.verification_policy.execution_proof.value == "structured_receipt"
        else:
            assert definition.state_effects == ("read",)
            assert definition.required_capabilities == ("device.read",)
            expected_proof = "structured_receipt" if name == "browser_wait" else "none"
            assert definition.verification_policy.execution_proof.value == expected_proof
    observation_actions = {
        name for name, definition in definitions.items() if definition.requires_model_observation
    }
    assert observation_actions == {
        "browser_fill",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_click",
        "browser_backfill",
    }
    for definition in definitions.values():
        for property_schema in definition.input_schema.get("properties", {}).values():
            assert property_schema.get("description"), definition.model_alias

    condition_schema = definitions["browser_wait"].input_schema["properties"]["condition"]
    for property_schema in condition_schema["properties"].values():
        assert property_schema.get("description")

    mutation_names = {
        "browser_fill",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_click",
        "browser_backfill",
    }
    for name in mutation_names:
        description = definitions[name].description
        assert "browser_inspect alone immediately before" in description
        assert "snapshot" in description
        assert "next_snapshot is review-only" in description

    reference_properties = definitions["browser_click"].input_schema["properties"]
    assert "Inspection-batch identifier" in reference_properties["snapshot_id"]["description"]
    assert "local to snapshot_id" in reference_properties["element_id"]["description"]
    assert "never mix, guess, or reuse" in reference_properties["element_id"]["description"]

    click_description = definitions["browser_click"].description
    assert "visible=true" in click_description
    assert "enabled=true" in click_description
    assert "obscured=false" in click_description
    assert "Do not use click to fill" in click_description
    assert "exact intended target" in definitions["browser_inspect"].description
    assert "invalidates every earlier snapshot" in definitions["browser_inspect"].description
    assert "absolute HTTP(S) URL" in definitions["browser_navigate"].description
    assert "timeout means the condition was not reached" in definitions["browser_wait"].description
    assert (
        "Call this when semantic text and controls are insufficient"
        in definitions["observe"].description
    )
    assert "call browser_inspect before any interaction" in definitions["observe"].description


def test_browser_run_registry_is_frozen() -> None:
    registry = build_browser_run_registry(ToolRegistry(), object())

    assert registry.frozen is True
    with pytest.raises(ToolRegistryError, match="frozen"):
        registry.register_many(())


@pytest.mark.asyncio
async def test_failed_browser_action_preserves_executor_error(tmp_path: Path) -> None:
    class _ObscuredSession:
        async def click(self, snapshot_id: str, element_id: str):
            del snapshot_id, element_id
            raise BrowserSessionError("target_obscured", "target is obscured")

    registered = next(
        tool
        for tool in build_browser_registered_tools(_ObscuredSession())
        if tool.definition.model_alias == "browser_click"
    )
    tool = from_registered_tool(registered)
    arguments = tool.input_model(snapshot_id="snapshot-1", element_id="element-1")

    result = await tool.execute(arguments, ToolExecutionContext(tmp_path))

    assert result.is_error is True
    assert result.metadata["status"] == "failure"
    assert result.metadata["error_code"] == "target_obscured"
    assert result.output == "target is obscured"


@pytest.mark.asyncio
async def test_successful_browser_action_requires_a_fresh_inspection(tmp_path: Path) -> None:
    class _ClickableSession:
        inspect_calls: list[dict[str, object]] = []

        async def click(self, snapshot_id: str, element_id: str):
            return {
                "snapshot_id_used": snapshot_id,
                "element_id_used": element_id,
                "interaction_verified": True,
            }

        async def inspect(self, filters: dict[str, object]):
            self.inspect_calls.append(filters)
            return BrowserSnapshot(
                snapshot_id="snapshot-after-click",
                generation=1,
                url="http://example.test/after",
                title="After click",
                text="updated",
                elements=(),
                total_matches=0,
                truncated=False,
            )

    session = _ClickableSession()
    registered = next(
        tool
        for tool in build_browser_registered_tools(session)
        if tool.definition.model_alias == "browser_click"
    )
    tool = from_registered_tool(registered)
    arguments = tool.input_model(snapshot_id="snapshot-1", element_id="element-1")

    result = await tool.execute(arguments, ToolExecutionContext(tmp_path))

    assert result.is_error is False
    assert result.metadata["snapshot_consumed"] is True
    assert result.metadata["next_action_requires_new_inspect"] is True
    assert session.inspect_calls == [
        {"interactive_only": True, "actionable_only": True, "limit": 200}
    ]
    assert "snapshot_id" not in result.metadata["next_snapshot"]
    assert result.metadata["next_snapshot"]["reference_mode"] == "review_only"
    assert all(
        "element_id" not in element for element in result.metadata["next_snapshot"]["elements"]
    )
    assert result.metadata["next_snapshot_ready"] is True
