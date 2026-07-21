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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SkillSource = Literal["builtin", "user", "project", "explicit"]


class SkillProvenance(BaseModel):
    """Resolved origin of one skill definition."""

    model_config = ConfigDict(frozen=True)

    source: SkillSource
    path: Path
    root: Path


class SkillSpec(BaseModel):
    """Lightweight skill specification for progressive disclosure."""

    name: str
    description: str
    tool_names: list[str] = Field(min_length=1)
    system_prompt_fragment: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_path: Path | None = None
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    version: str = "v1"
    command_name: str | None = None
    display_name: str | None = None
    aliases: tuple[str, ...] = ()
    user_invocable: bool = True
    disable_model_invocation: bool = False
    model: str | None = None
    argument_hint: str | None = None
    source: SkillSource = "builtin"
    resource_root: Path | None = None
    provenance: tuple[SkillProvenance, ...] = ()
    model_config = ConfigDict(arbitrary_types_allowed=True)
