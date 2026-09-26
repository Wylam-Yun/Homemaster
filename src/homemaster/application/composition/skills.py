"""Skill registry composition and refresh policy."""

from __future__ import annotations

from pathlib import Path

from homemaster.config import HomeMasterConfig
from homemaster.skills.loader import load_skill_registry
from homemaster.skills.registry import SkillRegistry


def compose_skill_registry(config: HomeMasterConfig, *, cwd: Path | None = None) -> SkillRegistry:
    sources = config.skills
    discovery_cwd = cwd or Path.cwd()

    def discover() -> SkillRegistry:
        return load_skill_registry(
            cwd=discovery_cwd,
            user_dirs=sources.user_dirs,
            project_dirs=sources.project_dirs,
            explicit_dirs=sources.explicit_dirs,
            allow_project=sources.allow_project,
            plugin_roots=sources.plugin_roots,
            enabled_plugins=sources.enabled_plugins,
            allow_project_plugin_skills=sources.allow_project_plugin_skills,
            allowed_builtin_overrides=sources.allowed_builtin_overrides,
        )

    registry = discover()
    registry.set_refresher(discover)
    return registry


__all__ = ["compose_skill_registry"]
