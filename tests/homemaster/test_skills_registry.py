"""Tests for the rewritten skills registry and loader."""

from __future__ import annotations

from homemaster.skills.loader import load_builtin_skills
from homemaster.skills.registry import SkillRegistry


def test_builtin_skills_register_as_metadata_packages() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    names = set(registry.all_names())
    assert "fetch_object" in names
    assert "check_object_state" in names
    skill = registry.get("fetch_object")
    assert skill.tool_names
    assert hasattr(skill, "metadata")
    assert hasattr(skill, "system_prompt_fragment")


def test_skills_do_not_define_runtime_modes() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    for skill in registry.all():
        text = (skill.description or "") + " " + " ".join(skill.tool_names)
        assert "mock_skills" not in text
        assert "deterministic" not in text


def test_skill_view_uses_progressive_disclosure() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    skill = registry.get("fetch_object")
    assert "full_prompt" not in skill.metadata
    assert set(skill.tool_names)


def test_skill_spec_uses_tool_names_not_allowed_tools() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    for skill in registry.all():
        assert hasattr(skill, "tool_names")
        assert isinstance(skill.tool_names, list)
        assert len(skill.tool_names) > 0


def test_skill_registry_all_returns_list() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    all_skills = registry.all()
    assert isinstance(all_skills, list)
    assert len(all_skills) >= 2


def test_skill_registry_candidate_summaries() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)
    summaries = registry.candidate_summaries()
    assert len(summaries) >= 2
    for summary in summaries:
        assert "name" in summary
        assert "description" in summary
        assert "tool_names" in summary
