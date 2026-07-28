from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from homemaster.browser.contracts import BrowserSession
from homemaster.browser.playwright_session import PlaywrightBrowserSession
from homemaster.browser.policy import BrowserPolicy
from homemaster.browser.tools import build_browser_registered_tools, build_browser_run_registry
from homemaster.tools.base import ToolRegistry, ToolRegistryError

EXPECTED_METHODS = {
    "navigate",
    "inspect",
    "fill",
    "select",
    "check",
    "uncheck",
    "click",
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


def test_browser_run_registry_is_frozen() -> None:
    registry = build_browser_run_registry(ToolRegistry(), object())

    assert registry.frozen is True
    with pytest.raises(ToolRegistryError, match="frozen"):
        registry.register_many(())
