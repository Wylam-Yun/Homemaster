"""ToolRegistry — stores ToolSpec by name, returns compact manifests."""

from __future__ import annotations

from typing import Any

from homemaster.tools.spec import ToolSpec


class ToolRegistry:
    """Registry of available tools.

    Stores ToolSpec instances by name. Returns compact Mimo manifests
    only for tools with selectable_by_model=True.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all_names(self) -> list[str]:
        return list(self._specs.keys())

    def tool_manifests(self) -> list[dict[str, Any]]:
        """Return compact manifests for model-selectable tools only."""
        return [
            spec.to_mimo_manifest()
            for spec in self._specs.values()
            if spec.selectable_by_model
        ]
