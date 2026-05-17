"""get_skill tool schema — retrieves full skill content for the model.

This module defines the ToolSpec for get_skill. The actual executor
will be wired in Phase 4 (AgentRuntime MVP).
"""

from __future__ import annotations

from typing import Any

from homemaster.tools.spec import ToolSpec

GET_SKILL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill_name": {
            "type": "string",
            "description": "Name of the skill to retrieve.",
        },
    },
    "required": ["skill_name"],
}

GET_SKILL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "content": {"type": "string"},
        "allowed_tools": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
    },
}


def make_get_skill_spec() -> ToolSpec:
    """Create the get_skill ToolSpec (executor wired in Phase 4)."""
    return ToolSpec(
        name="get_skill",
        description="Retrieve full skill content, allowed tools, and constraints.",
        input_schema=GET_SKILL_INPUT_SCHEMA,
        output_schema=GET_SKILL_OUTPUT_SCHEMA,
        executor_mode="internal",
        selectable_by_model=True,
        failure_semantics="raise",
    )
