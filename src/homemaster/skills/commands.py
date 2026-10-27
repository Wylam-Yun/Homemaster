"""Shared user-invocable Skill slash-command resolution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from homemaster.skills.registry import SkillRegistry
from homemaster.skills.types import SkillDefinition


@dataclass(frozen=True)
class ResolvedSkillCommand:
    prompt: str
    model_override: str | None
    # Identity of the resolved skill version. ``None`` only for results built
    # before this field existed (tests/fixtures); real resolutions always fill
    # it so callers can record a Skill Trace invocation.
    skill_name: str | None = None
    skill_content_sha256: str | None = None
    arguments: str = ""


def resolve_skill_command(
    raw_input: str,
    registry: SkillRegistry,
    *,
    session_id: str | None = None,
) -> ResolvedSkillCommand | None:
    if not raw_input.startswith("/"):
        return None
    name, _, arguments = raw_input[1:].partition(" ")
    name = name.strip()
    if not name or any(char.isspace() for char in name):
        return None
    registry.refresh()
    skill = registry.get(name)
    if skill is None:
        return None
    if not skill.user_invocable:
        raise ValueError(f"skill {name!r} is not user-invocable")
    return ResolvedSkillCommand(
        prompt=_render_skill_prompt(skill, arguments, session_id=session_id),
        model_override=skill.model,
        skill_name=skill.name,
        skill_content_sha256=hashlib.sha256(skill.content.encode("utf-8")).hexdigest(),
        arguments=arguments.strip(),
    )


def _render_skill_prompt(
    skill: SkillDefinition,
    arguments: str,
    *,
    session_id: str | None,
) -> str:
    prompt = skill.content
    raw_arguments = arguments.strip()
    if skill.base_dir:
        prompt = f"Base directory for this skill: {skill.base_dir}\n\n{prompt}"
        prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.base_dir)
    prompt = prompt.replace("${ARGUMENTS}", raw_arguments).replace("$ARGUMENTS", raw_arguments)
    if session_id:
        prompt = prompt.replace("${CLAUDE_SESSION_ID}", session_id)
    if raw_arguments and "${ARGUMENTS}" not in skill.content and "$ARGUMENTS" not in skill.content:
        prompt = f"{prompt}\n\nArguments: {raw_arguments}"
    return prompt


__all__ = ["ResolvedSkillCommand", "resolve_skill_command"]
