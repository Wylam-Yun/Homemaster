"""Prompt template loader for HomeMaster.

Loads .md templates from src/homemaster/prompts/ and renders them
using string.Template (stdlib, no external dependencies).

Only the prompts listed in PromptId are loadable.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from string import Template

_PROMPTS_DIR = Path(__file__).parent


class PromptId(StrEnum):
    """Closed set of loadable prompt templates."""

    AGENT_SYSTEM = "agent_system_prompt"
    BROWSER_GATEWAY = "browser_gateway"
    COMPACT_SUMMARY = "compact_summary_prompt"


def load_prompt(prompt_id: str | PromptId) -> str:
    """Load a prompt template by ID.

    Args:
        prompt_id: A PromptId enum value or its string value.

    Returns:
        The raw template text.

    Raises:
        KeyError: If prompt_id is not a valid PromptId.
        FileNotFoundError: If the template file does not exist.
    """
    if isinstance(prompt_id, PromptId):
        name = prompt_id.value
    else:
        # Validate against known IDs
        try:
            name = PromptId(prompt_id).value
        except ValueError:
            raise KeyError(
                f"Unknown prompt ID: {prompt_id!r}. "
                f"Valid IDs: {[p.value for p in PromptId]}"
            ) from None
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def render(prompt_id: str | PromptId, **kwargs: str) -> str:
    """Load and render a prompt template with the given variables.

    All values must be strings (callers pre-serialize to JSON strings
    before calling render). Raises KeyError if a required variable
    is missing or if prompt_id is invalid.
    """
    text = load_prompt(prompt_id)
    return Template(text).substitute(kwargs)
