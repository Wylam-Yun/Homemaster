"""SkillRegistry — stores SkillSpec, returns compact summaries."""

from __future__ import annotations

from typing import Any

from homemaster.skills.spec import SkillSpec


class SkillRegistry:
    """Registry of available skills.

    Stores SkillSpec by name. Returns compact summaries for candidate
    selection. Validates that tool_names exist in a ToolRegistry.
    """

    def __init__(self) -> None:
        self._specs: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> SkillSpec | None:
        return self._specs.get(name)

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
        ]
