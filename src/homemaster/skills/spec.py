"""SkillSpec — declarative skill metadata.

Hard constraints:
  - SkillSpec does NOT contain executor
  - SkillSpec does NOT return ToolResult
  - SkillSpec does NOT directly modify AgentState
  - SkillSpec does NOT allow fallback to stub implementations
  - SkillRegistry does NOT decide next action, only provides candidate metadata
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class SkillSpec(BaseModel):
    """Lightweight skill specification for progressive disclosure."""

    name: str
    description: str
    allowed_tools: list[str] = Field(min_length=1)
    activation_rules: list[str] = Field(default_factory=list)
    context_snippet: str = ""
    content_path: Path | None = None
    examples: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    version: str = "v1"
