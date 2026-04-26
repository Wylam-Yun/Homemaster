"""Central max_tokens policy for HomeMaster LLM calls."""

from __future__ import annotations

from typing import Literal

MAX_LLM_ATTEMPTS = 3

LLMCallKind = Literal[
    "stage_01_smoke",
    "stage_02_task_card",
    "stage_03_memory_query",
    "stage_05_orchestration",
    "stage_05_step_decision",
    "stage_05_recovery",
    "stage_06_summary",
]

# These are intentionally 2x the first conservative schedule. Each failed
# attempt doubles the previous output budget, up to MAX_LLM_ATTEMPTS.
INITIAL_MAX_TOKENS: dict[LLMCallKind, int] = {
    "stage_01_smoke": 4096,
    "stage_02_task_card": 4096,
    "stage_03_memory_query": 4096,
    "stage_05_orchestration": 16384,
    "stage_05_step_decision": 4096,
    "stage_05_recovery": 8192,
    "stage_06_summary": 16384,
}


def initial_max_tokens(kind: LLMCallKind) -> int:
    return INITIAL_MAX_TOKENS[kind]


def max_tokens_for_attempt(initial_tokens: int, attempt_index: int) -> int:
    if attempt_index < 1:
        raise ValueError("attempt_index must be >= 1")
    return initial_tokens * (2 ** (attempt_index - 1))
