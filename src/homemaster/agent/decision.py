"""Structured decision contracts for AgentRuntime.

Mimo output is parsed into one of two decision types:
- ToolCallDecision: invoke a tool with arguments
- FinishDecision: task completed or failed

No ``ask_user`` type — the model must either act or finish.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from homemaster.contracts import ContractModel


class ToolCallDecision(ContractModel):
    """Mimo decided to invoke a tool."""

    type: Literal["tool_call"] = "tool_call"
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class FinishDecision(ContractModel):
    """Mimo decided the task is done (success or failure)."""

    type: Literal["finish"] = "finish"
    status: Literal["completed", "failed"]
    summary: str = Field(min_length=1)
    failure_reason: str | None = None


AgentDecision = ToolCallDecision | FinishDecision


def parse_agent_decision(raw: dict[str, Any]) -> AgentDecision:
    """Parse a raw dict into an AgentDecision.

    Unknown type, unknown tool, or schema errors produce a FinishDecision(status="failed").
    """
    decision_type = raw.get("type", "")

    if decision_type == "tool_call":
        try:
            return ToolCallDecision.model_validate(raw)
        except Exception:
            return FinishDecision(
                status="failed",
                summary=f"Invalid tool_call decision: {raw}",
            )

    if decision_type == "finish":
        try:
            return FinishDecision.model_validate(raw)
        except Exception:
            return FinishDecision(
                status="failed",
                summary=f"Invalid finish decision: {raw}",
            )

    return FinishDecision(
        status="failed",
        summary=f"Unknown decision type: {decision_type!r}",
    )
