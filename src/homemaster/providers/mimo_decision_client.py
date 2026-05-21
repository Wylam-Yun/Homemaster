"""MimoDecisionClient — protocol and live implementation.

Protocol:
  MimoDecisionClient.decide() takes context + tools + settings,
  returns AgentDecision. No fallback — fails fast on error.

Stub implementation for testing lives in the test helpers package.
"""

from __future__ import annotations

import time
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
        turn_index: int = 0,
    ) -> AgentDecision: ...


class LiveMimoDecisionClient:
    """Production client. Calls live Mimo. No deterministic fallback.

    Parses raw Mimo JSON output into AgentDecision.
    If parsing fails, returns FinishDecision(status="failed").
    """

    def __init__(self, provider_config: ProviderConfig, *, event_sink: Any = None) -> None:
        self._provider = provider_config
        self._event_sink = event_sink

    def decide(
        self,
        *,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        settings: Any,
        turn_index: int = 0,
    ) -> AgentDecision:
        from homemaster.llm_client import RawJsonLLMClient
        from homemaster.token_budget import initial_max_tokens

        client = RawJsonLLMClient(self._provider)
        prompt = self._build_prompt(context, tools)
        max_tokens = initial_max_tokens("agent_runtime_decision")

        self._emit("llm_call_started", settings, turn_index=turn_index, payload={
            "provider_name": self._provider.name,
            "model": self._provider.model,
            "max_tokens": max_tokens,
        })

        t0 = time.perf_counter()
        try:
            raw = client.complete_json(prompt, max_tokens=max_tokens, temperature=0.0)
        except Exception as exc:
            self._emit("llm_call_failed", settings, turn_index=turn_index, payload={
                "provider_name": self._provider.name,
                "model": self._provider.model,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            })
            raise

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._emit("llm_call_completed", settings, turn_index=turn_index, payload={
            "provider_name": self._provider.name,
            "model": self._provider.model,
            "duration_ms": elapsed_ms,
        }, provider_name=self._provider.name, duration_ms=elapsed_ms)

        return parse_agent_decision(raw.json_payload if hasattr(raw, 'json_payload') else raw)

    def _emit(self, event_type: str, settings: Any, turn_index: int = 0, **kwargs: Any) -> None:
        """Emit a RuntimeEvent if event_sink is set."""
        if self._event_sink is None:
            return
        from homemaster.events.runtime_events import RuntimeEvent
        self._event_sink.emit(RuntimeEvent(
            turn_index=turn_index,
            event_type=event_type,
            run_id=getattr(settings, 'run_id', ''),
            **kwargs,
        ))

    def _build_prompt(
        self, context: dict[str, Any], tools: list[dict[str, Any]]
    ) -> str:
        """Build a minimal prompt from context and tool manifests."""
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
