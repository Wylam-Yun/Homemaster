"""Tests for ToolSpec, ToolResult, ToolRegistry."""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def _make_tool(name: str, **overrides: Any) -> ToolSpec:
    defaults = {
        "name": name,
        "description": f"Tool {name}",
        "executor_mode": "simulated_skill",
        "failure_semantics": "raise",
    }
    defaults.update(overrides)
    return ToolSpec(**defaults)


def test_to_mimo_manifest_excludes_executor() -> None:
    def my_executor(*, arguments: Any, run_context: RunContext) -> ToolResult:
        return ToolResult(success=True)

    spec = _make_tool("test_tool", executor=my_executor)
    manifest = spec.to_mimo_manifest()
    assert "executor" not in manifest
    assert manifest["name"] == "test_tool"
    assert manifest["executor_mode"] == "simulated_skill"


def test_to_mimo_manifest_includes_executor_mode() -> None:
    spec = _make_tool("test_tool", executor_mode="programmatic")
    manifest = spec.to_mimo_manifest()
    assert manifest["executor_mode"] == "programmatic"


def test_tool_manifests_filters_non_selectable() -> None:
    registry = ToolRegistry()
    registry.register(_make_tool("visible"))
    registry.register(_make_tool("hidden", selectable_by_model=False))
    manifests = registry.tool_manifests()
    assert len(manifests) == 1
    assert manifests[0]["name"] == "visible"


def test_finish_task_not_in_manifests() -> None:
    registry = ToolRegistry()
    registry.register(_make_tool("navigate"))
    registry.register(_make_tool(
        "finish_task", selectable_by_model=False, executor_mode="internal",
    ))
    names = [m["name"] for m in registry.tool_manifests()]
    assert "navigate" in names
    assert "finish_task" not in names


def test_tool_result_no_state_patch() -> None:
    result = ToolResult(success=True, data={"found": True})
    fields = set(type(result).model_fields.keys())
    assert "state_patch" not in fields


def test_tool_registry_get_unknown() -> None:
    registry = ToolRegistry()
    assert registry.get("nonexistent") is None


def test_tool_registry_all_names() -> None:
    registry = ToolRegistry()
    registry.register(_make_tool("a"))
    registry.register(_make_tool("b"))
    assert set(registry.all_names()) == {"a", "b"}

