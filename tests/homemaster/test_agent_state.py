"""Tests for AgentState — construction, defaults, and boundary assertions."""

from __future__ import annotations

from homemaster.agent.state import AgentState


def test_agent_state_default_construction() -> None:
    state = AgentState()
    assert state.run_id == ""
    assert state.status == "running"
    assert state.turn_index == 0
    assert state.iteration_index == 0
    assert state.total_model_calls == 0
    assert state.metadata == {}
    assert state.last_assistant_text is None


def test_agent_state_with_values() -> None:
    state = AgentState(
        run_id="test-001",
        session_id="s1",
        status="running",
        turn_index=3,
    )
    assert state.run_id == "test-001"
    assert state.session_id == "s1"
    assert state.turn_index == 3


def test_agent_state_serialization_roundtrip() -> None:
    state = AgentState(
        run_id="test-002",
        metadata={"debug": True},
    )
    dumped = state.model_dump(mode="json")
    restored = AgentState.model_validate(dumped)
    assert restored.run_id == "test-002"
    assert restored.metadata == {"debug": True}


def test_agent_state_has_no_home_task_fields() -> None:
    fields = set(AgentState.model_fields)
    assert "task_card" not in fields
    assert "memory_hits" not in fields
    assert "current_location" not in fields
    assert "holding_object" not in fields
    assert "selected_target" not in fields
    assert "target_candidates" not in fields
    assert "current_object" not in fields
    assert "actions" not in fields
    assert "observations" not in fields
    assert "verifications" not in fields
    assert "failures" not in fields
    assert "active_skills" not in fields
    assert "loaded_skill_contexts" not in fields
    assert "memory_context_snapshot" not in fields
    assert "user_context_snapshot" not in fields


def test_agent_state_mutable_metadata() -> None:
    state = AgentState()
    state.metadata["key"] = "value"
    assert state.metadata["key"] == "value"
