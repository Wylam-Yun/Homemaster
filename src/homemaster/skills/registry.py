"""SkillRegistry — stores SkillSpec, returns compact summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homemaster.skills.spec import SkillSource, SkillSpec


@dataclass(frozen=True)
class SkillLoadIssue:
    """Secret-safe diagnostic for one rejected automatic skill source."""

    source: SkillSource
    code: str
    detail: str


class SkillRegistry:
    """Registry of available skills.

    Stores SkillSpec by name. Returns compact summaries for candidate
    selection. Validates that tool_names exist in a ToolRegistry.
    """

    def __init__(self) -> None:
        self._specs: dict[str, SkillSpec] = {}
        self._lookup: dict[str, str] = {}
        self._issues: list[SkillLoadIssue] = []

    def register(self, spec: SkillSpec, *, allow_builtin_override: bool = False) -> None:
        existing = self._specs.get(spec.name)
        if existing is not None:
            if existing.source == "builtin" and spec.source != "explicit":
                if not allow_builtin_override:
                    raise ValueError(
                        f"skill {spec.name!r} cannot override builtin without named authorization"
                    )
            if existing.source == "builtin" and not allow_builtin_override:
                raise ValueError(
                    f"skill {spec.name!r} cannot override builtin without named authorization"
                )
            if _SOURCE_PRIORITY[spec.source] < _SOURCE_PRIORITY[existing.source]:
                return
            prior = existing.provenance
            spec = spec.model_copy(update={"provenance": (*prior, *spec.provenance)})
        candidate = dict(self._specs)
        candidate[spec.name] = spec
        lookup = _build_lookup(candidate)
        self._specs = candidate
        self._lookup = lookup

    def get(self, name: str) -> SkillSpec | None:
        canonical = self._lookup.get(name, name)
        return self._specs.get(canonical)

    def get_model_visible(self, name: str) -> SkillSpec | None:
        spec = self.get(name)
        if spec is not None and spec.disable_model_invocation:
            return None
        return spec

    @property
    def issues(self) -> tuple[SkillLoadIssue, ...]:
        return tuple(self._issues)

    def record_issue(self, issue: SkillLoadIssue) -> None:
        self._issues.append(issue)

    def replace_with(self, registry: SkillRegistry) -> None:
        """Atomically replace this handle with an already validated registry snapshot."""

        if not isinstance(registry, SkillRegistry):
            raise TypeError("registry must be a SkillRegistry")
        self._specs = dict(registry._specs)
        self._lookup = dict(registry._lookup)
        self._issues = list(registry._issues)

    def all(self) -> list[SkillSpec]:
        """Return all registered specs."""
        return list(self._specs.values())

    def all_names(self) -> list[str]:
        return list(self._specs.keys())

    def candidate_summaries(self, task_hint: str = "") -> list[dict[str, Any]]:
        """Return compact skill summaries for model selection.

        Does NOT return full SKILL.md body — only name, description,
        and tool_names.
        """
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "tool_names": spec.tool_names,
            }
            for spec in self._specs.values()
            if not spec.disable_model_invocation
        ]


_SOURCE_PRIORITY = {"builtin": 0, "user": 1, "project": 2, "explicit": 3}


def _build_lookup(specs: dict[str, SkillSpec]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for spec in specs.values():
        keys = (spec.name, spec.command_name, spec.display_name, *spec.aliases)
        for key in keys:
            if not key:
                continue
            owner = lookup.get(key)
            if owner is not None and owner != spec.name:
                raise ValueError(
                    f"skill lookup alias {key!r} is ambiguous between {owner!r} and {spec.name!r}"
                )
            lookup[key] = spec.name
    return lookup
