"""StaticStepDecisionProvider extracted from stages/executor.py.

Moved during Phase 1 deterministic cleanup.  This is a test-only helper
for pre-scripting StepDecision sequences in executor unit tests.
Must never be imported by production src/homemaster code.
"""

from __future__ import annotations

from typing import Any

from homemaster.contracts import (
    ExecutionState,
    PlanningContext,
    StepDecision,
    Subtask,
)
from homemaster.stages.executor import Stage05ExecutionError


class StaticStepDecisionProvider:
    """Small deterministic provider for tests."""

    def __init__(self, decisions: list[StepDecision]) -> None:
        self._decisions = list(decisions)

    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        if not self._decisions:
            raise Stage05ExecutionError("no StepDecision available for static provider")
        return self._decisions.pop(0)
