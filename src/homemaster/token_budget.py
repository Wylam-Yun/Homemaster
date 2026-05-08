"""Central max_tokens policy for HomeMaster LLM calls."""

from __future__ import annotations

from typing import Literal

from homemaster.runtime import RuntimeConfigError, get_config_section, load_homemaster_config

LLMCallKind = Literal[
    "stage_01_smoke",
    "stage_02_task_card",
    "stage_03_memory_query",
    "stage_05_orchestration",
    "stage_05_step_decision",
    "stage_05_recovery",
    "stage_06_summary",
]

_DEFAULT_MAX_LLM_ATTEMPTS = 3

# These are intentionally 2x the first conservative schedule. Each failed
# attempt doubles the previous output budget, up to MAX_LLM_ATTEMPTS.
_DEFAULT_INITIAL_MAX_TOKENS: dict[LLMCallKind, int] = {
    "stage_01_smoke": 4096,
    "stage_02_task_card": 4096,
    "stage_03_memory_query": 4096,
    "stage_05_orchestration": 16384,
    "stage_05_step_decision": 4096,
    "stage_05_recovery": 8192,
    "stage_06_summary": 16384,
}


def _load_token_budget_config() -> tuple[int, dict[LLMCallKind, int]]:
    section = get_config_section(load_homemaster_config(), "token_budget")
    if section is None:
        return _DEFAULT_MAX_LLM_ATTEMPTS, dict(_DEFAULT_INITIAL_MAX_TOKENS)

    max_attempts = section.get("max_llm_attempts", _DEFAULT_MAX_LLM_ATTEMPTS)
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise RuntimeConfigError(
            f"token_budget.max_llm_attempts must be a positive int, got {max_attempts!r}"
        )

    tokens = dict(_DEFAULT_INITIAL_MAX_TOKENS)
    overrides = section.get("initial_max_tokens")
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise RuntimeConfigError("token_budget.initial_max_tokens must be a JSON object")
        for k, v in overrides.items():
            if k not in _DEFAULT_INITIAL_MAX_TOKENS:
                continue  # ignore unknown keys (forward-compat)
            if not isinstance(v, int) or v < 1:
                raise RuntimeConfigError(
                    f"token_budget.initial_max_tokens.{k} must be a positive int, got {v!r}"
                )
            tokens[k] = v
    return max_attempts, tokens


MAX_LLM_ATTEMPTS, INITIAL_MAX_TOKENS = _load_token_budget_config()


def initial_max_tokens(kind: LLMCallKind) -> int:
    return INITIAL_MAX_TOKENS[kind]


def max_tokens_for_attempt(initial_tokens: int, attempt_index: int) -> int:
    if attempt_index < 1:
        raise ValueError("attempt_index must be >= 1")
    return initial_tokens * (2 ** (attempt_index - 1))
