"""Tests for SkillLoader — loading builtin SKILL.md files."""

from __future__ import annotations

from homemaster.skills.loader import SkillLoader


def test_load_builtin_fetch_object() -> None:
    loader = SkillLoader()
    spec = loader.load_builtin("fetch_object")
    assert spec.name == "fetch_object"
    assert "navigate" in spec.allowed_tools
    assert "observe" in spec.allowed_tools
    assert spec.context_snippet  # body is not empty


def test_load_builtin_check_object_state() -> None:
    loader = SkillLoader()
    spec = loader.load_builtin("check_object_state")
    assert spec.name == "check_object_state"
    assert "navigate" in spec.allowed_tools
    assert "update_user_profile" in spec.allowed_tools


def test_load_builtin_not_found() -> None:
    loader = SkillLoader()
    import pytest
    with pytest.raises(FileNotFoundError):
        loader.load_builtin("nonexistent_skill")


def test_fetch_object_allowed_tools_count() -> None:
    loader = SkillLoader()
    spec = loader.load_builtin("fetch_object")
    assert len(spec.allowed_tools) >= 5  # at least 5 tools


def test_check_object_state_has_constraints() -> None:
    loader = SkillLoader()
    spec = loader.load_builtin("check_object_state")
    assert len(spec.constraints) >= 1
