"""One monotonic run deadline and exact external action budgets."""

from __future__ import annotations

import time
from dataclasses import dataclass

from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CoworkerBudget:
    max_wall_time_s: float = 1200.0
    max_browser_actions: int = 64
    max_terminal_actions: int = 4
    start_monotonic: float | None = None
    browser_actions: int = 0
    terminal_actions: int = 0

    def __post_init__(self) -> None:
        if self.start_monotonic is None:
            self.start_monotonic = time.monotonic()

    @property
    def remaining_s(self) -> float:
        assert self.start_monotonic is not None
        return max(0.0, self.max_wall_time_s - (time.monotonic() - self.start_monotonic))

    def timeout(self, configured_s: float) -> float:
        remaining = self.remaining_s
        if remaining <= 0:
            raise BudgetExceeded("coworker wall-clock deadline exhausted")
        return min(configured_s, remaining)

    def before_browser(self, outcome: CoworkerOutcome) -> None:
        self._active(outcome)
        if self.browser_actions >= self.max_browser_actions:
            raise BudgetExceeded("coworker browser action budget exhausted")
        self.browser_actions += 1

    def before_terminal(self, outcome: CoworkerOutcome) -> None:
        self._active(outcome)
        if self.terminal_actions >= self.max_terminal_actions:
            raise BudgetExceeded("coworker terminal action budget exhausted")
        self.terminal_actions += 1

    def before_external(self, outcome: CoworkerOutcome) -> None:
        self._active(outcome)

    def _active(self, outcome: CoworkerOutcome) -> None:
        if outcome.terminal:
            raise BudgetExceeded("coworker run already reached a terminal outcome")
        if self.remaining_s <= 0:
            raise BudgetExceeded("coworker wall-clock deadline exhausted")
