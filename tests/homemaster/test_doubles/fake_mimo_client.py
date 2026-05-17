"""FakeMimoDecisionClient — offline test double for AgentRuntime tests.

Returns decisions from a fixed list. Used in test_agent_runtime.py to
verify that the runtime executes model-chosen tools, not a hardcoded pipeline.
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.decision import AgentDecision, FinishDecision
from homemaster.providers.mimo_decision_client import MimoDecisionClient


class FakeMimoDecisionClient:
    """Offline test double. Returns decisions from a fixed list."""

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self._decisions = list(decisions)
        self._index = 0

    def decide(
        self,
        *,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        settings: Any,
    ) -> AgentDecision:
        if self._index >= len(self._decisions):
            return FinishDecision(status="failed", summary="no more decisions")
        d = self._decisions[self._index]
        self._index += 1
        return d
