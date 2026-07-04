"""Tests verifying prompt files are clean and prompt loader rejects old names."""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path("src/homemaster/prompts")
FORBIDDEN = (
    "Stage",
    "stage_",
    "pipeline",
    "scenario",
    "deterministic",
    "mock_skills",
    "orchestration",
    "step_decision",
    "verify before summary",
    "always call task_interpreter first",
)


def test_new_prompts_do_not_encode_fixed_flow() -> None:
    for path in PROMPT_DIR.glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN:
            assert term not in text, f"{path} contains {term}"


def test_prompt_loader_rejects_deleted_numbered_prompt_names() -> None:
    from homemaster.prompts.loader import load_prompt

    try:
        load_prompt("stage_01_task_card_prompt")
    except (KeyError, ValueError, FileNotFoundError):
        return
    raise AssertionError("prompt loader accepted a deleted numbered prompt")
