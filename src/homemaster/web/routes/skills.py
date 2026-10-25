"""Skill routes: list the registry and resolve slash commands server-side."""

from __future__ import annotations

from fastapi import FastAPI

from homemaster.skills.commands import resolve_skill_command
from homemaster.web._shared import (
    _SKILL_SOURCE_LABELS,
    _application_services,
    _error,
)
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import SkillResolveRequest


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application

    @app.get("/api/skills")
    async def list_skills() -> object:
        registry = _application_services(application).get("skill_registry")
        if registry is None:
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill registry is unavailable.",
                retryable=True,
            )
        skills: list[dict[str, object]] = []
        for skill in registry.list_skills():
            provenance = registry.provenance_for(skill)
            raw_source = provenance[-1].source if provenance else getattr(skill, "source", "")
            skills.append(
                {
                    "name": skill.name,
                    "command_name": skill.command_name,
                    "description": skill.description,
                    "source": _SKILL_SOURCE_LABELS.get(raw_source, raw_source),
                    "user_invocable": bool(skill.user_invocable),
                    # Per-skill enablement is a P1 feature (skills.enabled.json);
                    # every registered skill is invocable today.
                    "enabled": True,
                }
            )
        return {"skills": skills}

    @app.post("/api/skills/resolve")
    async def resolve_skill(body: SkillResolveRequest) -> object:
        registry = _application_services(application).get("skill_registry")
        if registry is None:
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill registry is unavailable.",
                retryable=True,
            )
        try:
            resolved = resolve_skill_command(body.text, registry)
        except ValueError:
            return _error(
                422,
                "skill_not_invocable",
                "The skill cannot be invoked directly by users.",
                retryable=False,
            )
        if resolved is None:
            return {"kind": "plain"}
        name, _, arguments = body.text[1:].partition(" ")
        return {
            "kind": "skill",
            "name": name.strip(),
            "arguments": arguments.strip(),
            "prompt": resolved.prompt,
            "model_override": resolved.model_override,
        }


__all__ = ["register"]
