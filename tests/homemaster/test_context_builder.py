"""Tests for ContextBuilder — three-layer output, no secrets/trace leakage."""

from __future__ import annotations

from homemaster.agent.context_builder import ContextBuilder
from homemaster.agent.state import AgentState


def _make_state(**kwargs) -> AgentState:
    defaults = {
        "run_id": "test-001",
        "user_request": "fetch the cup",
        "task_card": {"target": "cup", "intent": "fetch"},
        "current_location": "kitchen",
        "turn_index": 2,
        "memory_context_snapshot": "# Memory\n- cup in kitchen",
        "user_context_snapshot": "# User\n- prefers quiet",
    }
    defaults.update(kwargs)
    return AgentState(**defaults)


def test_context_builder_has_three_layers() -> None:
    builder = ContextBuilder()
    state = _make_state()
    context = builder.build(state, tool_manifests=[], skill_summaries=[], max_turns=12)

    assert "stable_context" in context
    assert "task_state_context" in context
    assert "recent_dynamics_context" in context


def test_stable_context_contains_manifests_and_snapshots() -> None:
    builder = ContextBuilder()
    state = _make_state()
    manifests = [{"name": "navigate", "description": "go somewhere"}]
    summaries = [{"name": "fetch_object", "description": "fetch stuff"}]
    context = builder.build(
        state, tool_manifests=manifests, skill_summaries=summaries, max_turns=12,
    )

    stable = context["stable_context"]
    assert stable["tool_manifests"] == manifests
    assert stable["skill_summaries"] == summaries
    assert stable["memory_snapshot"] == "# Memory\n- cup in kitchen"
    assert stable["user_snapshot"] == "# User\n- prefers quiet"


def test_task_state_context_contains_user_request_and_card() -> None:
    builder = ContextBuilder()
    state = _make_state()
    context = builder.build(state, tool_manifests=[], skill_summaries=[], max_turns=12)

    task = context["task_state_context"]
    assert task["user_request"] == "fetch the cup"
    assert task["task_card"] == {"target": "cup", "intent": "fetch"}
    assert task["current_location"] == "kitchen"


def test_recent_dynamics_contains_turn_index_and_failures() -> None:
    builder = ContextBuilder()
    state = _make_state()
    state.failures.append({"tool": "observe", "error": "not found"})
    context = builder.build(state, tool_manifests=[], skill_summaries=[], max_turns=12)

    dynamics = context["recent_dynamics_context"]
    assert dynamics["turn_index"] == 2
    assert dynamics["max_turns"] == 12
    assert len(dynamics["failures"]) == 1


def test_context_builder_includes_loaded_skill_contexts() -> None:
    builder = ContextBuilder()
    state = _make_state()
    state.loaded_skill_contexts["fetch_object"] = {"content": "skill data"}
    context = builder.build(state, tool_manifests=[], skill_summaries=[], max_turns=12)

    # loaded_skill_contexts should be in stable_context (Finding 7)
    stable = context["stable_context"]
    assert "loaded_skill_contexts" in stable
    assert "fetch_object" in stable["loaded_skill_contexts"]


def test_context_builder_limits_recent_items() -> None:
    builder = ContextBuilder()
    state = _make_state()
    for i in range(10):
        state.actions.append({"tool": "navigate", "result": {"i": i}})
        state.observations.append({"tool": "observe", "result": {"i": i}})
    context = builder.build(state, tool_manifests=[], skill_summaries=[], max_turns=12)

    dynamics = context["recent_dynamics_context"]
    assert len(dynamics["recent_actions"]) == 5
    assert len(dynamics["recent_observations"]) == 5
