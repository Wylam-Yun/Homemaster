"""Tests for _agent_result_to_home_master_result — Phase 5 result mapping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from homemaster.agent.state import AgentState
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.task_runner import _agent_result_to_home_master_result

_TASK_CARD = {
    "task_type": "fetch_object",
    "target": "cup",
    "success_criteria": ["cup delivered"],
    "needs_clarification": False,
    "confidence": 0.9,
}


def _make_agent_result(state: AgentState, final_status: str = "completed",
                       events: list | None = None):
    result = MagicMock()
    result.run_id = "test-map-001"
    result.final_status = final_status
    result.state = state
    result.events = events or []
    return result


def _base_kwargs() -> dict:
    return dict(
        scenario="test_scenario",
        utterance="fetch the cup",
        paths={"world": "/tmp/world.json", "memory": "/tmp/memory.json"},
        model_boundary={"provider": "Mimo"},
        case_dir=Path("/tmp/case"),
        results_dir=Path("/tmp/results"),
        runtime_memory_root=Path("/tmp/memory"),
    )


def test_mapping_with_task_card_and_memory_hits() -> None:
    state = AgentState(
        run_id="test-map-001",
        task_card=_TASK_CARD,
        memory_hits=[{
            "document_id": "m1", "memory_id": "m1",
            "object_category": "cup", "room_id": "kitchen", "anchor_id": "a1",
        }],
        selected_target={"memory_id": "m1", "room_id": "kitchen", "anchor_id": "a1"},
        target_candidates=[
            {"memory_id": "m1", "object_category": "cup", "room_id": "kitchen", "anchor_id": "a1"},
        ],
        verifications=[{
            "turn": 0, "tool": "verify",
            "result": {"verified": True, "target_object": "cup"},
        }],
        current_location="kitchen",
        holding_object="cup",
        turn_index=3,
    )
    result = _make_agent_result(state)
    hmr = _agent_result_to_home_master_result(result, **_base_kwargs())

    assert hmr.task_card is not None
    assert hmr.task_card.target == "cup"
    assert hmr.planning_context is not None
    assert hmr.planning_context.selected_target is not None
    assert hmr.planning_context.selected_target.memory_id == "m1"
    assert hmr.evidence_bundle is not None
    assert len(hmr.evidence_bundle.verified_facts) == 1
    assert hmr.execution_result is not None
    assert hmr.execution_result["current_location"] == "kitchen"
    assert hmr.orchestration_plan is None


def test_mapping_partial_state_no_memory() -> None:
    state = AgentState(
        run_id="test-map-002",
        task_card=_TASK_CARD,
    )
    result = _make_agent_result(state)
    hmr = _agent_result_to_home_master_result(result, **_base_kwargs())

    assert hmr.task_card is not None
    assert hmr.planning_context is None  # no memory_hits
    assert hmr.evidence_bundle is not None
    assert len(hmr.evidence_bundle.evidence_refs) == 0


def test_mapping_failed_with_failures() -> None:
    state = AgentState(
        run_id="test-map-003",
        failures=[
            {"turn": 0, "tool": "observe", "error": "object not found"},
            {"turn": 1, "tool": "navigate", "error": "room not found"},
        ],
    )
    result = _make_agent_result(state, final_status="failed")
    hmr = _agent_result_to_home_master_result(result, **_base_kwargs())

    assert hmr.final_status == "failed"
    assert hmr.evidence_bundle is not None
    assert len(hmr.evidence_bundle.failure_facts) == 2
    assert "object not found" in hmr.evidence_bundle.failure_facts[0]
    assert hmr.stage_statuses["agent_runtime"]["status"] == "FAIL"


def test_mapping_empty_state() -> None:
    state = AgentState(run_id="test-map-004")
    result = _make_agent_result(state)
    hmr = _agent_result_to_home_master_result(result, **_base_kwargs())

    assert hmr.task_card is None
    assert hmr.planning_context is None
    assert hmr.evidence_bundle is not None
    assert len(hmr.evidence_bundle.evidence_refs) == 0
    assert hmr.execution_result is not None
    assert hmr.execution_result["final_status"] == "completed"
    assert hmr.memory_commit is None


def test_mapping_with_update_memory_action() -> None:
    state = AgentState(
        run_id="test-map-005",
        actions=[
            {"turn": 0, "tool": "navigate", "result": {}},
            {"turn": 1, "tool": "update_memory", "result": {
                "committed": True, "object_category": "cup",
            }},
        ],
    )
    result = _make_agent_result(state)
    hmr = _agent_result_to_home_master_result(result, **_base_kwargs())

    assert hmr.memory_commit is not None
    assert hmr.memory_commit["committed"] is True
    assert len(hmr.memory_commit["actions"]) == 1


def test_mapping_stage_statuses_from_events() -> None:
    events = [
        RuntimeEvent(turn_index=0, event_type="tool_call",
                     payload={"tool": "understand_task"}, timestamp="t"),
        RuntimeEvent(turn_index=0, event_type="tool_result",
                     payload={"tool": "understand_task", "success": True}, timestamp="t"),
        RuntimeEvent(turn_index=1, event_type="tool_call",
                     payload={"tool": "navigate"}, timestamp="t"),
        RuntimeEvent(turn_index=1, event_type="tool_result",
                     payload={"tool": "navigate", "success": True}, timestamp="t"),
        RuntimeEvent(turn_index=2, event_type="tool_call",
                     payload={"tool": "observe"}, timestamp="t"),
        RuntimeEvent(turn_index=2, event_type="tool_result",
                     payload={"tool": "observe", "success": False}, timestamp="t"),
    ]
    state = AgentState(run_id="test-map-006")
    result = _make_agent_result(state, events=events)
    hmr = _agent_result_to_home_master_result(result, **_base_kwargs())

    assert hmr.stage_statuses["stage02"]["status"] == "PASS"
    assert "understand_task" in hmr.stage_statuses["stage02"]["tools"]
    assert hmr.stage_statuses["stage05"]["status"] == "FAIL"  # observe failed
    assert hmr.stage_statuses["agent_runtime"]["status"] == "PASS"
