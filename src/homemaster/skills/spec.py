"""Compatibility import for the V2.0 OpenHarness Skill definition."""

from homemaster.skills.types import SkillDefinition

# This alias preserves imports without retaining the obsolete HomeMaster
# metadata and tool-name contract.
SkillSpec = SkillDefinition

__all__ = ["SkillDefinition", "SkillSpec"]
