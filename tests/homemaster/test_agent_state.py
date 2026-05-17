"""Tests for AgentState — construction, defaults, serialization."""

from __future__ import annotations

from homemaster.agent.state import AgentState


def test_agent_state_default_construction() -> None:
    state = AgentState()
    assert state.run_id == ""
    assert state.status == "running"
    assert state.turn_index == 0
    assert state.task_card is None
    assert state.memory_hits == []
    assert state.failures == []
    assert state.selected_target is None


def test_agent_state_with_values() -> None:
    state = AgentState(
        run_id="test-001",
        user_request="fetch the cup",
        status="running",
        turn_index=3,
        current_location="kitchen",
        holding_object="cup",
    )
    assert state.run_id == "test-001"
    assert state.user_request == "fetch the cup"
    assert state.current_location == "kitchen"
    assert state.holding_object == "cup"


def test_agent_state_serialization_roundtrip() -> None:
    state = AgentState(
        run_id="test-002",
        task_card={"target": "cup", "intent": "fetch"},
        memory_hits=[{"memory_id": "m1", "object_category": "cup"}],
    )
    dumped = state.model_dump(mode="json")
    restored = AgentState.model_validate(dumped)
    assert restored.run_id == "test-002"
    assert restored.task_card == {"target": "cup", "intent": "fetch"}
    assert len(restored.memory_hits) == 1


def test_agent_state_selected_target() -> None:
    target = {"memory_id": "m1", "room_id": "kitchen", "anchor_id": "a1"}
    state = AgentState(selected_target=target)
    assert state.selected_target is not None
    assert state.selected_target["memory_id"] == "m1"
    assert state.selected_target["room_id"] == "kitchen"


def test_agent_state_mutable_lists() -> None:
    state = AgentState()
    state.failures.append({"tool": "observe", "error": "not found"})
    state.actions.append({"tool": "navigate", "result": {}})
    assert len(state.failures) == 1
    assert len(state.actions) == 1
