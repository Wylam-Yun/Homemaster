"""Load exactly the two generic coworker skills."""

from __future__ import annotations

from pathlib import Path

from homemaster.skills.loader import SkillLoader
from homemaster.skills.registry import SkillProvenance, SkillRegistry

SKILL_ROOT = Path(__file__).resolve().parent / "skills"


def load_coworker_skills() -> SkillRegistry:
    registry = SkillRegistry()
    loader = SkillLoader()
    for name in ("change_execution", "evidence_discipline"):
        path = SKILL_ROOT / name / "SKILL.md"
        registry.register(
            loader.load_from_file(path),
            provenance=SkillProvenance("coworker", path, SKILL_ROOT),
        )
    return registry
