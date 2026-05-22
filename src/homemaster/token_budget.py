"""Central max_tokens policy for HomeMaster LLM calls."""

from __future__ import annotations

from typing import Literal

from homemaster.runtime import RuntimeConfigError, get_config_section, load_homemaster_config

LLMCallKind = Literal[
    "agent_response",
    "tool_task_interpreter",
    "tool_memory_query",
    "tool_task_summarizer",
]

_DEFAULT_MAX_LLM_ATTEMPTS = 3

_DEFAULT_INITIAL_MAX_TOKENS: dict[LLMCallKind, int] = {
    "agent_response": 4096,
    "tool_task_interpreter": 4096,
    "tool_memory_query": 4096,
    "tool_task_summarizer": 8192,
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
                raise RuntimeConfigError(
                    f"token_budget.initial_max_tokens has unknown key: {k!r}. "
                    f"Expected one of: {sorted(_DEFAULT_INITIAL_MAX_TOKENS)}"
                )
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
