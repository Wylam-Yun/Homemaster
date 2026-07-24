"""HomeMaster Skill registry with source diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from homemaster.skills.types import SkillDefinition


@dataclass(frozen=True)
class SkillProvenance:
    """HomeMaster-owned source metadata kept outside SkillDefinition."""

    source: str
    path: Path
    root: Path


@dataclass(frozen=True)
class SkillLoadIssue:
    """Secret-safe diagnostic for one rejected skill source."""

    source: str
    code: str
    detail: str


class SkillRegistry:
    """Store HomeMaster Skills under every declared lookup name."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._provenance: dict[int, tuple[SkillProvenance, ...]] = {}
        self._issues: list[SkillLoadIssue] = []
        self._refresher: Callable[[], SkillRegistry] | None = None

    def register(
        self,
        skill: SkillDefinition,
        *,
        provenance: SkillProvenance,
        allow_builtin_override: bool = False,
    ) -> None:
        if not isinstance(skill, SkillDefinition):
            raise TypeError("skill must be a SkillDefinition")
        existing = self._skills.get(skill.name)
        if existing is not None:
            existing_source = self.provenance_for(existing)[-1].source
            if (
                existing_source in {"bundled", "builtin"}
                and provenance.source not in {"bundled", "builtin"}
                and not allow_builtin_override
            ):
                raise ValueError(
                    f"skill {skill.name!r} cannot override builtin without named authorization"
                )
            inherited = self.provenance_for(existing)
        else:
            inherited = ()

        for key in self._keys(skill):
            conflict = self._skills.get(key)
            if conflict is not None and conflict is not existing:
                raise ValueError(f"skill lookup name {key!r} conflicts with {conflict.name!r}")
        if existing is not None:
            self._remove(existing)
        for key in self._keys(skill):
            self._skills[key] = skill
        self._provenance[id(skill)] = (*inherited, provenance)

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def get_model_visible(self, name: str) -> SkillDefinition | None:
        """Compatibility accessor that enforces the invocation flag."""

        skill = self.get(name)
        if skill is None or skill.disable_model_invocation:
            return None
        return skill

    def list_skills(self) -> list[SkillDefinition]:
        unique: dict[tuple[str, str | None], SkillDefinition] = {}
        for skill in self._skills.values():
            unique[(skill.source, skill.path or skill.name)] = skill
        return sorted(unique.values(), key=lambda skill: skill.command_name or skill.name)

    def all(self) -> list[SkillDefinition]:
        return self.list_skills()

    def all_names(self) -> list[str]:
        return sorted(self._skills)

    def provenance_for(self, skill: SkillDefinition) -> tuple[SkillProvenance, ...]:
        return self._provenance.get(id(skill), ())

    def candidate_summaries(self, task_hint: str = "") -> list[dict[str, object]]:
        del task_hint
        return [
            {
                "name": skill.name,
                "command_name": skill.command_name,
                "description": skill.description,
                "user_invocable": skill.user_invocable,
                "model_invocable": not skill.disable_model_invocation,
            }
            for skill in self.list_skills()
        ]

    @property
    def issues(self) -> tuple[SkillLoadIssue, ...]:
        return tuple(self._issues)

    def record_issue(self, issue: SkillLoadIssue) -> None:
        self._issues.append(issue)

    def replace_with(self, registry: SkillRegistry) -> None:
        if not isinstance(registry, SkillRegistry):
            raise TypeError("registry must be a SkillRegistry")
        self._skills = dict(registry._skills)
        self._provenance = dict(registry._provenance)
        self._issues = list(registry._issues)

    def set_refresher(self, refresher: Callable[[], SkillRegistry]) -> None:
        if not callable(refresher):
            raise TypeError("skill registry refresher must be callable")
        self._refresher = refresher

    def refresh(self) -> SkillRegistry:
        """Atomically publish a newly discovered complete Skill snapshot."""

        if self._refresher is None:
            return self
        snapshot = self._refresher()
        self.replace_with(snapshot)
        return self

    def _remove(self, skill: SkillDefinition) -> None:
        for key, value in tuple(self._skills.items()):
            if value is skill:
                del self._skills[key]

    @staticmethod
    def _keys(skill: SkillDefinition) -> tuple[str, ...]:
        return tuple(
            key
            for key in (skill.name, skill.command_name, skill.display_name, *skill.aliases)
            if key
        )


__all__ = ["SkillLoadIssue", "SkillProvenance", "SkillRegistry"]
