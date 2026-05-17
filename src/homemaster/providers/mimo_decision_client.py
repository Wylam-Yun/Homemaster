"""MimoDecisionClient — protocol and live implementation.

Protocol:
  MimoDecisionClient.decide() takes context + tools + settings,
  returns AgentDecision. No deterministic fallback.

FakeMimoDecisionClient lives in tests/homemaster/test_doubles/.
"""

from __future__ import annotations

from typing import Any, Protocol

from homemaster.agent.decision import AgentDecision, parse_agent_decision
from homemaster.runtime import ProviderConfig


class MimoDecisionClient(Protocol):
    """Protocol for Mimo-based decision making."""

    def decide(
        self,
        *,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        settings: Any,  # RuntimeSettings
    ) -> AgentDecision: ...


class LiveMimoDecisionClient:
    """Production client. Calls live Mimo. No deterministic fallback.

    Parses raw Mimo JSON output into AgentDecision.
    If parsing fails, returns FinishDecision(status="failed").
    """

    def __init__(self, provider_config: ProviderConfig) -> None:
        self._provider = provider_config

    def decide(
        self,
        *,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        settings: Any,
    ) -> AgentDecision:
        from homemaster.llm_client import RawJsonLLMClient

        client = RawJsonLLMClient(self._provider)
        # Build prompt from context + tools (Phase 4 will refine this)
        prompt = self._build_prompt(context, tools)
        raw = client.call_json(prompt)
        return parse_agent_decision(raw)

    def _build_prompt(
        self, context: dict[str, Any], tools: list[dict[str, Any]]
    ) -> str:
        """Build a minimal prompt from context and tool manifests.

        Phase 4 will replace this with proper prompt construction.
        """
        import json

        return (
            "You are a home robot assistant. "
            "Given the following context and available tools, "
            "decide which tool to call or whether to finish.\n\n"
            f"Context: {json.dumps(context, ensure_ascii=False)}\n\n"
            f"Tools: {json.dumps(tools, ensure_ascii=False)}\n\n"
            "Respond with a JSON object: "
            '{"type": "tool_call", "tool": "<name>", "arguments": {...}} '
            'or {"type": "finish", "status": "completed|failed", "summary": "..."}'
        )
