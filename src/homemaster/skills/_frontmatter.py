"""Shared YAML frontmatter parsing for SKILL.md files.

Ported from OpenHarness 9b2efd7, src/openharness/skills/_frontmatter.py.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def parse_skill_metadata(
    default_name: str,
    content: str,
    *,
    fallback_template: str = "Skill: {name}",
) -> dict[str, Any]:
    """Extract metadata with full YAML scalar support and a body fallback."""

    name = default_name
    description = ""
    frontmatter: dict[str, Any] = {}
    body = content
    if content.startswith("---\n"):
        end_index = content.find("\n---\n", 4)
        if end_index != -1:
            body = content[end_index + 5 :]
            try:
                metadata = yaml.safe_load(content[4:end_index])
            except yaml.YAMLError as exc:
                raise ValueError(f"invalid SKILL.md YAML frontmatter: {exc}") from exc
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("SKILL.md frontmatter must be a YAML mapping")
            if isinstance(metadata, dict):
                frontmatter = metadata
                value = metadata.get("name")
                if isinstance(value, str) and value.strip():
                    name = value.strip()
                value = metadata.get("description")
                if isinstance(value, str) and value.strip():
                    description = value.strip()

    if not description:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                if name == default_name:
                    name = stripped[2:].strip() or default_name
                continue
            if stripped and not stripped.startswith("#"):
                description = stripped[:200]
                break
    if not description:
        description = fallback_template.format(name=name)
    return {
        "name": name,
        "description": description,
        "frontmatter": frontmatter,
        "body": body,
    }
