"""SkillRegistry — stores SkillSpec, returns compact summaries."""

from __future__ import annotations

from typing import Any

from homemaster.skills.spec import SkillSpec
from homemaster.tools.registry import ToolRegistry


class SkillRegistry:
    """Registry of available skills.

    Stores SkillSpec by name. Returns compact summaries for candidate
    selection. Validates that allowed_tools exist in a ToolRegistry.
    """

    def __init__(self) -> None:
        self._specs: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> SkillSpec | None:
        return self._specs.get(name)

    def all_names(self) -> list[str]:
        return list(self._specs.keys())

    def candidate_summaries(self, task_hint: str = "") -> list[dict[str, Any]]:
        """Return compact skill summaries for model selection.

        Does NOT return full SKILL.md body — only name, description,
        allowed_tools, and activation_rules.
        """
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "allowed_tools": spec.allowed_tools,
                "activation_rules": spec.activation_rules,
            }
            for spec in self._specs.values()
        ]

    def validate_tools(self, tool_registry: ToolRegistry) -> list[str]:
        """Check that all allowed_tools exist in the ToolRegistry.

        Returns list of missing tool names (empty if all valid).
        """
        missing: list[str] = []
        known = set(tool_registry.all_names())
        for spec in self._specs.values():
            for tool_name in spec.allowed_tools:
                if tool_name not in known and tool_name not in missing:
                    missing.append(tool_name)
        return missing
