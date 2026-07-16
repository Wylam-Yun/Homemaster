from __future__ import annotations

import pytest

from homemaster.benchmarking.coworker_demo.budget import BudgetExceeded, CoworkerBudget
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome


def test_budgets_refuse_n_plus_one_without_incrementing() -> None:
    budget = CoworkerBudget(max_browser_actions=2, max_terminal_actions=1)
    outcome = CoworkerOutcome()
    budget.before_browser(outcome)
    budget.before_browser(outcome)
    with pytest.raises(BudgetExceeded, match="browser"):
        budget.before_browser(outcome)
    assert budget.browser_actions == 2
    budget.before_terminal(outcome)
    with pytest.raises(BudgetExceeded, match="terminal"):
        budget.before_terminal(outcome)
    assert budget.terminal_actions == 1


def test_terminal_outcome_blocks_all_later_external_calls() -> None:
    budget = CoworkerBudget()
    outcome = CoworkerOutcome()
    outcome.mark("complete")
    with pytest.raises(BudgetExceeded, match="terminal outcome"):
        budget.before_external(outcome)
