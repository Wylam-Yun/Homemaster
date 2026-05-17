"""Tests for new SkillSpec / SkillRegistry from homemaster.skills.registry."""

from __future__ import annotations

import pytest

from homemaster.skills.spec import SkillSpec
from homemaster.skills.registry import SkillRegistry
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.spec import ToolSpec


def _make_tool(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"Tool {name}",
        executor_mode="simulated_skill",
        failure_semantics="raise",
    )


def _make_skill(name: str, allowed_tools: list[str]) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=f"Skill {name}",
        allowed_tools=allowed_tools,
        context_snippet=f"Context for {name}",
    )


def test_skill_spec_no_executor_field() -> None:
    """SkillSpec must not have an executor field."""
    spec = _make_skill("test", ["navigate"])
    assert not hasattr(spec, "executor") or spec.model_fields.get("executor") is None


def test_skill_registry_register_and_get() -> None:
    registry = SkillRegistry()
    spec = _make_skill("my_skill", ["navigate"])
    registry.register(spec)
    assert registry.get("my_skill") is spec
    assert registry.get("nonexistent") is None


def test_skill_registry_all_names() -> None:
    registry = SkillRegistry()
    registry.register(_make_skill("a", ["navigate"]))
    registry.register(_make_skill("b", ["observe"]))
    assert set(registry.all_names()) == {"a", "b"}


def test_skill_registry_candidate_summaries_no_body() -> None:
    registry = SkillRegistry()
    registry.register(_make_skill("s1", ["navigate", "observe"]))
    summaries = registry.candidate_summaries("test task")
    assert len(summaries) == 1
    assert summaries[0]["name"] == "s1"
    assert "context_snippet" not in summaries[0]


def test_skill_registry_validate_tools_all_present() -> None:
    tool_registry = ToolRegistry()
    tool_registry.register(_make_tool("navigate"))
    tool_registry.register(_make_tool("observe"))

    skill_registry = SkillRegistry()
    skill_registry.register(_make_skill("s1", ["navigate", "observe"]))

    missing = skill_registry.validate_tools(tool_registry)
    assert missing == []


def test_skill_registry_validate_tools_missing_tool() -> None:
    tool_registry = ToolRegistry()
    tool_registry.register(_make_tool("navigate"))

    skill_registry = SkillRegistry()
    skill_registry.register(_make_skill("s1", ["navigate", "nonexistent"]))

    missing = skill_registry.validate_tools(tool_registry)
    assert "nonexistent" in missing
