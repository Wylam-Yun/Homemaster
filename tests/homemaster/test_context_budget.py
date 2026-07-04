"""Tests for ContextBudget — token estimation and threshold decisions."""

from __future__ import annotations

from homemaster.agent.context import (
    BudgetDecision,
    ContextBudget,
    estimate_text_tokens,
)


def test_estimate_text_tokens_handles_ascii_and_cjk() -> None:
    assert estimate_text_tokens("abcd") == 1
    assert estimate_text_tokens("水杯") >= 1


def test_budget_thresholds_scale_with_context_window() -> None:
    budget = ContextBudget(
        context_window_tokens=1_000_000,
        output_reserve_tokens=8192,
        threshold_ratio=0.5,
        recent_tail_ratio=0.2,
        safety_buffer_tokens=13_000,
    )

    assert budget.compaction_threshold_tokens == 500_000
    assert budget.recent_tail_budget_tokens == 100_000
    assert budget.should_compact(499_999) is BudgetDecision.NO_COMPACT
    assert budget.should_compact(500_000) is BudgetDecision.COMPACT


def test_budget_hard_cap_limits_threshold() -> None:
    budget = ContextBudget(
        context_window_tokens=100_000,
        output_reserve_tokens=4096,
        threshold_ratio=0.99,
        safety_buffer_tokens=13_000,
    )

    hard_cap = 100_000 - 4096 - 13_000
    assert budget.compaction_threshold_tokens == hard_cap


def test_padded_applies_padding_factor() -> None:
    budget = ContextBudget(
        context_window_tokens=1_000_000,
        output_reserve_tokens=8192,
        token_estimation_padding=4 / 3,
    )

    assert budget.padded(1000) == 1333
