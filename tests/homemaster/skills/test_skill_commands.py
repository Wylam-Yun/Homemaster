from pathlib import Path

import pytest

from homemaster.skills.commands import resolve_skill_command
from homemaster.skills.registry import SkillProvenance, SkillRegistry
from homemaster.skills.types import SkillDefinition


def _registry(*, user_invocable: bool = True) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="demo",
            description="Demo",
            content="Run from ${CLAUDE_SKILL_DIR}: $ARGUMENTS for ${CLAUDE_SESSION_ID}",
            source="user",
            base_dir="/tmp/demo",
            command_name="demo",
            user_invocable=user_invocable,
            model="configured-model",
        ),
        provenance=SkillProvenance("user", Path("/tmp/demo/SKILL.md"), Path("/tmp")),
    )
    return registry


def test_skill_slash_command_expands_upstream_placeholders() -> None:
    result = resolve_skill_command("/demo one two", _registry(), session_id="session-1")

    assert result is not None
    assert result.model_override == "configured-model"
    assert result.prompt == (
        "Base directory for this skill: /tmp/demo\n\n"
        "Run from /tmp/demo: one two for session-1"
    )


def test_unknown_slash_command_falls_through() -> None:
    assert resolve_skill_command("/unknown arg", _registry()) is None


def test_non_user_invocable_skill_is_rejected() -> None:
    with pytest.raises(ValueError, match="not user-invocable"):
        resolve_skill_command("/demo", _registry(user_invocable=False))
