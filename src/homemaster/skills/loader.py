"""SkillLoader — reads SKILL.md files and produces SkillSpec.

SKILL.md format:
  YAML-like frontmatter between --- delimiters, followed by markdown body.
  Frontmatter fields: name, description, allowed_tools, activation_rules,
                      constraints, success_criteria, examples, version
  Body becomes context_snippet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homemaster.skills.spec import SkillSpec

_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"


class SkillLoader:
    """Loads SkillSpec from SKILL.md files."""

    def load_from_file(self, path: Path) -> SkillSpec:
        """Load a SkillSpec from a SKILL.md file."""
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_skill_md(raw)
        meta["context_snippet"] = body.strip()
        meta["content_path"] = path
        return SkillSpec.model_validate(meta)

    def load_builtin(self, name: str) -> SkillSpec:
        """Load a builtin skill by name."""
        skill_path = _BUILTIN_DIR / name / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Builtin skill not found: {skill_path}")
        return self.load_from_file(skill_path)


def _parse_skill_md(raw: str) -> tuple[dict[str, Any], str]:
    """Parse SKILL.md into (frontmatter_dict, body_text).

    Simple parser: reads key: value lines and key: JSON lines.
    List values must be JSON arrays (e.g., ["item1", "item2"]).
    """
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md must have frontmatter between --- delimiters")

    meta: dict[str, Any] = {}
    for line in parts[1].strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            continue
        # Try JSON parsing for arrays/objects
        if value.startswith("[") or value.startswith("{"):
            try:
                meta[key] = json.loads(value)
            except json.JSONDecodeError:
                meta[key] = value
        else:
            meta[key] = value

    body = parts[2]
    return meta, body
