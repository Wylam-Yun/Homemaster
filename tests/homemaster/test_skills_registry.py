"""Tests for OpenHarness-compatible Skill discovery and summaries."""

from __future__ import annotations

from homemaster.skills.loader import load_builtin_skills, load_bundled_skills
from homemaster.skills.registry import SkillRegistry


def test_bundled_and_home_builtin_skills_register_as_full_markdown_packages() -> None:
    registry = SkillRegistry()
    load_bundled_skills(registry)
    load_builtin_skills(registry)

    names = set(registry.all_names())
    assert {"skill-creator", "commit", "fetch_object", "check_object_state"} <= names
    skill = registry.get("fetch_object")
    assert skill is not None
    assert skill.content.startswith("---")
    assert skill.base_dir is not None
    assert not hasattr(skill, "tool_names")


def test_skills_are_instructions_not_capability_declarations() -> None:
    registry = SkillRegistry()
    load_builtin_skills(registry)

    for skill in registry.all():
        assert isinstance(skill.content, str)
        assert skill.content
        assert not hasattr(skill, "tool_names")


def test_registry_indexes_command_name_display_name_and_aliases() -> None:
    registry = SkillRegistry()
    load_bundled_skills(registry)

    skill = registry.get("skill-creator")
    assert skill is not None
    assert registry.get(skill.name) is skill
    assert registry.get(skill.command_name or "") is skill


def test_skill_registry_all_returns_a_deterministic_list() -> None:
    registry = SkillRegistry()
    load_bundled_skills(registry)

    skills = registry.all()
    assert [skill.command_name for skill in skills] == sorted(
        skill.command_name for skill in skills
    )


def test_skill_registry_candidate_summaries_do_not_leak_full_bodies() -> None:
    registry = SkillRegistry()
    load_bundled_skills(registry)

    summaries = registry.candidate_summaries()
    assert summaries
    for summary in summaries:
        assert {"name", "command_name", "description", "model_invocable"} <= set(summary)
        assert "content" not in summary


def test_skill_creator_documents_transactional_git_url_installation() -> None:
    registry = SkillRegistry()
    load_bundled_skills(registry)

    skill = registry.get("skill-creator")
    assert skill is not None
    assert "git clone" in skill.content
    assert "check every destination name" in skill.content
    assert "report the conflict" in skill.content
    assert "staging directory" in skill.content
    assert "atomic rename" in skill.content
