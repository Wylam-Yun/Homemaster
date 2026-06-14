"""Model profile helpers for context budgeting."""

from __future__ import annotations

import re

from homemaster.config.model_config import ProviderProfileConfig

DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000


def resolve_context_window_tokens(
    provider: ProviderProfileConfig,
    *,
    fallback_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
) -> int:
    """Resolve context window for budget math.

    Provider config is an explicit override. Known model profiles are used next.
    Unknown models fall back conservatively because OpenAI-compatible model
    metadata does not reliably expose context length across providers.
    """
    if provider.context_window_tokens is not None:
        return provider.context_window_tokens
    inferred = infer_context_window_tokens(provider.model)
    if inferred is not None:
        return inferred
    return fallback_tokens


def infer_context_window_tokens(model: str) -> int | None:
    normalized = _normalize_model_name(model)
    if "mimo" in normalized and ("v2.5" in normalized or "v25" in normalized):
        return 1_000_000
    return None


def _normalize_model_name(model: str) -> str:
    value = model.strip().casefold()
    value = value.rsplit("/", maxsplit=1)[-1]
    value = re.sub(r"[\s_]+", "-", value)
    return value
