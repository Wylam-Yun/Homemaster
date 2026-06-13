"""Tests for V1.5 AgentState runtime bookkeeping."""

from __future__ import annotations

from homemaster.agent.state import AgentState, CompactionRecord, ProviderUsage


def test_agent_state_tracks_runtime_counters() -> None:
    state = AgentState(run_id="r1", session_id="s1")

    assert state.status == "running"
    assert state.turn_index == 0
    assert state.iteration_index == 0
    assert state.total_model_calls == 0
    assert state.total_tool_calls == 0
    assert state.consecutive_tool_errors == 0
    assert state.no_progress_iterations == 0


def test_agent_state_records_provider_usage_and_compaction() -> None:
    state = AgentState(run_id="r1", session_id="s1")
    state.provider_usage = ProviderUsage(input_tokens=10, output_tokens=3, total_tokens=13)
    state.last_compaction = CompactionRecord(
        kind="micro",
        before_tokens=120_000,
        after_tokens=80_000,
        reason="auto",
    )

    dumped = state.model_dump(mode="json")

    assert dumped["provider_usage"]["input_tokens"] == 10
    assert dumped["last_compaction"]["kind"] == "micro"


def test_begin_iteration_increments_counters() -> None:
    state = AgentState(run_id="r1", session_id="s1")
    state.begin_iteration(0)
    state.begin_iteration(1)

    assert state.iteration_index == 1
    assert state.total_model_calls == 2


def test_record_tool_results_tracks_errors() -> None:
    state = AgentState(run_id="r1", session_id="s1")

    state.record_tool_results([
        {"tool_call_id": "c1", "name": "t1", "is_error": True, "text": "fail"},
    ])
    assert state.consecutive_tool_errors == 1

    state.record_tool_results([
        {"tool_call_id": "c2", "name": "t1", "is_error": False, "text": "ok"},
    ])
    assert state.consecutive_tool_errors == 0


def test_agent_state_serialization_roundtrip() -> None:
    state = AgentState(run_id="r1", session_id="s1", metadata={"debug": True})
    state.provider_usage = ProviderUsage(input_tokens=100, output_tokens=50, total_tokens=150)
    dumped = state.model_dump(mode="json")
    restored = AgentState.model_validate(dumped)
    assert restored.run_id == "r1"
    assert restored.metadata == {"debug": True}
    assert restored.provider_usage.input_tokens == 100
