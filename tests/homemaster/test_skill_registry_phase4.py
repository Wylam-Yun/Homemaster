"""Tests for Phase4 SkillRegistry — validate_tools with 11 builtin tools."""

from __future__ import annotations

from homemaster.skills.registry import SkillRegistry
from homemaster.skills.spec import SkillSpec
from homemaster.tools.builtin import build_tool_registry


def test_skill_registry_validate_tools_with_11_builtins() -> None:
    """SkillRegistry.validate_tools() must pass when all allowed_tools exist."""
    tool_registry = build_tool_registry()
    skill_registry = SkillRegistry()

    # Register a skill that uses a subset of the 11 tools
    skill_registry.register(SkillSpec(
        name="fetch_object",
        description="Fetch an object",
        allowed_tools=["navigate", "observe", "manipulate", "verify"],
    ))

    missing = skill_registry.validate_tools(tool_registry)
    assert missing == []


def test_skill_registry_validate_tools_reports_missing() -> None:
    """validate_tools() must report tools that don't exist in ToolRegistry."""
    tool_registry = build_tool_registry()
    skill_registry = SkillRegistry()

    skill_registry.register(SkillSpec(
        name="bad_skill",
        description="References nonexistent tools",
        allowed_tools=["navigate", "nonexistent_tool"],
    ))

    missing = skill_registry.validate_tools(tool_registry)
    assert "nonexistent_tool" in missing
    assert "navigate" not in missing


def test_skill_registry_validate_tools_empty_registry() -> None:
    """validate_tools() on empty SkillRegistry returns empty list."""
    tool_registry = build_tool_registry()
    skill_registry = SkillRegistry()

    missing = skill_registry.validate_tools(tool_registry)
    assert missing == []


def test_build_tool_registry_accepts_skill_registry() -> None:
    """build_tool_registry(skill_registry=...) must work with explicit registry."""
    skill_registry = SkillRegistry()
    skill_registry.register(SkillSpec(
        name="test_skill",
        description="test",
        allowed_tools=["navigate"],
    ))

    tool_registry = build_tool_registry(skill_registry=skill_registry)
    assert len(tool_registry.all_names()) == 11
    assert "get_skill" in tool_registry.all_names()
