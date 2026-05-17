"""Tests for AgentDecision contract."""

from __future__ import annotations

from homemaster.agent.decision import (
    FinishDecision,
    ToolCallDecision,
    parse_agent_decision,
)


def test_tool_call_decision_from_dict() -> None:
    raw = {"type": "tool_call", "tool": "navigate", "arguments": {"target": "kitchen"}}
    decision = parse_agent_decision(raw)
    assert isinstance(decision, ToolCallDecision)
    assert decision.tool == "navigate"
    assert decision.arguments == {"target": "kitchen"}


def test_finish_decision_from_dict() -> None:
    raw = {"type": "finish", "status": "completed", "summary": "Done"}
    decision = parse_agent_decision(raw)
    assert isinstance(decision, FinishDecision)
    assert decision.status == "completed"


def test_unknown_type_returns_failed_finish() -> None:
    raw = {"type": "ask_user", "question": "Where?"}
    decision = parse_agent_decision(raw)
    assert isinstance(decision, FinishDecision)
    assert decision.status == "failed"
    assert "Unknown decision type" in decision.summary


def test_invalid_tool_call_returns_failed_finish() -> None:
    raw = {"type": "tool_call"}  # missing required 'tool'
    decision = parse_agent_decision(raw)
    assert isinstance(decision, FinishDecision)
    assert decision.status == "failed"


def test_empty_dict_returns_failed_finish() -> None:
    decision = parse_agent_decision({})
    assert isinstance(decision, FinishDecision)
    assert decision.status == "failed"


def test_tool_call_serialization() -> None:
    decision = ToolCallDecision(tool="observe", arguments={"camera": "front"})
    data = decision.model_dump()
    assert data["type"] == "tool_call"
    assert data["tool"] == "observe"


def test_finish_serialization() -> None:
    decision = FinishDecision(status="failed", summary="timeout", failure_reason="no response")
    data = decision.model_dump()
    assert data["type"] == "finish"
    assert data["failure_reason"] == "no response"
