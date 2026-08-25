"""Tests for ToolSpec, ToolResult, ToolRegistry."""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
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


def test_tool_result_no_state_patch() -> None:
    result = ToolResult(success=True, data={"found": True})
    fields = set(type(result).model_fields.keys())
    assert "state_patch" not in fields
