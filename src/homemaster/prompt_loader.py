"""Minimal prompt template loader for HomeMaster.

Loads .txt templates from src/homemaster/prompts/ and renders them
using string.Template (stdlib, no external dependencies).
"""

from __future__ import annotations

from pathlib import Path
from string import Template

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_template(name: str) -> Template:
    """Load a prompt template by filename (e.g. 'stage_01_task_card_prompt.txt')."""
    text = (_PROMPTS_DIR / name).read_text(encoding="utf-8")
    return Template(text)


def render(name: str, **kwargs: str) -> str:
    """Load and render a prompt template with the given variables.

    All values must be strings (callers pre-serialize to JSON strings
    before calling render). Raises KeyError if a required variable
    is missing, FileNotFoundError if the template does not exist.
    """
    return load_template(name).substitute(kwargs)
