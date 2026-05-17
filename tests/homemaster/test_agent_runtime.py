"""Tests for AgentRuntime — MVP tool loop, non-linear scenarios, rejection paths.

Uses FakeMimoDecisionClient to verify the runtime executes model-chosen
tools, not a hardcoded pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemaster.agent.context_builder import ContextBuilder
from homemaster.agent.decision import AgentDecision, FinishDecision, ToolCallDecision
from homemaster.agent.runtime import AgentRuntime
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.events.sinks import JsonlEventSink
from homemaster.memory.context_snapshot import ContextSnapshot
from homemaster.tools.builtin import build_skill_registry, build_tool_registry
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.state_updater import StateUpdater


class FakeMimoDecisionClient:
    """Offline test double. Returns decisions from a fixed list."""

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self._decisions = list(decisions)
        self._index = 0

    def decide(self, **kwargs: Any) -> AgentDecision:
        if self._index >= len(self._decisions):
            return FinishDecision(status="failed", summary="no more decisions")
        d = self._decisions[self._index]
        self._index += 1
        return d


def _make_settings(**kwargs) -> RuntimeSettings:
    defaults = {
        "run_id": "test-mvp",
        "runtime_root": "/tmp/runs",
        "debug_root": "/tmp/debug",
        "results_root": "/tmp/results",
        "max_turns": 12,
    }
    defaults.update(kwargs)
    return RuntimeSettings(**defaults)


def _build_runtime(
    decisions: list, settings: RuntimeSettings | None = None, tmp_path: Path | None = None
) -> AgentRuntime:
    settings = settings or _make_settings()
    output_dir = tmp_path / "events" if tmp_path else Path("/tmp/test_events")
    return AgentRuntime(
        settings=settings,
        decision_client=FakeMimoDecisionClient(decisions),
        tool_registry=build_tool_registry(),
        skill_registry=build_skill_registry(),
        event_sink=JsonlEventSink(output_dir=output_dir),
        context_builder=ContextBuilder(),
        dispatcher=ToolDispatcher(),
        state_updater=StateUpdater(),
        context_snapshot=ContextSnapshot(),
    )


# ---------------------------------------------------------------------------
# Happy-path: linear tool calls → FinishDecision(completed)
# ---------------------------------------------------------------------------


def test_runtime_happy_path_linear_tools(tmp_path: Path) -> None:
    """Linear tool sequence: understand → navigate → observe → manipulate → verify → finish."""
    decisions = [
        ToolCallDecision(tool="understand_task", arguments={}),
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        ToolCallDecision(tool="observe", arguments={"target_object": "cup"}),
        ToolCallDecision(
            tool="manipulate",
            arguments={"action": "pick_up", "target_object": "cup"},
        ),
        ToolCallDecision(
            tool="verify",
            arguments={"target_object": "cup", "expected_state": "delivered"},
        ),
        FinishDecision(status="completed", summary="cup delivered"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("fetch the cup")

    assert result.final_status == "completed"
    assert result.state.turn_index == 5  # 5 tool calls before finish
    assert result.state.current_location == "kitchen"
    assert result.state.holding_object == "cup"
    assert len(result.events) > 0


# ---------------------------------------------------------------------------
# Non-linear: model chooses different tools based on failure
# ---------------------------------------------------------------------------


def test_runtime_nonlinear_tool_selection_after_success(tmp_path: Path) -> None:
    """Model freely chooses next tool — not a fixed pipeline.

    After navigate and observe both succeed, model chooses retrieve_memory
    (which fails due to no task_card), proving the runtime doesn't enforce
    a fixed sequence.
    """
    decisions = [
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        ToolCallDecision(tool="observe", arguments={"target_object": "cup"}),
        ToolCallDecision(tool="retrieve_memory", arguments={}),
        FinishDecision(status="completed", summary="recovered"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("fetch the cup")

    assert result.state.turn_index == 3
    assert result.final_status == "completed"
    # retrieve_memory fails (no task_card) — recorded as failure
    assert len(result.state.failures) == 1
    assert result.state.failures[0]["tool"] == "retrieve_memory"


def test_runtime_verify_failed_then_observe(tmp_path: Path) -> None:
    """After verify fails, model chooses observe again."""
    decisions = [
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        ToolCallDecision(tool="observe", arguments={"target_object": "cup"}),
        ToolCallDecision(
            tool="manipulate",
            arguments={"action": "pick_up", "target_object": "cup"},
        ),
        ToolCallDecision(tool="verify", arguments={"target_object": "cup"}),
        # verify passes (holding cup), but model decides to observe again
        ToolCallDecision(tool="observe", arguments={"target_object": "cup"}),
        FinishDecision(status="completed", summary="verified and re-observed"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("fetch the cup")

    assert result.final_status == "completed"
    assert result.state.turn_index == 5
    assert len(result.state.verifications) == 1


def test_runtime_all_candidates_exhausted_finishes_failed(tmp_path: Path) -> None:
    """When no more candidates, model returns FinishDecision(status='failed')."""
    decisions = [
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        ToolCallDecision(tool="observe", arguments={"target_object": "nonexistent"}),
        FinishDecision(status="failed", summary="object not found anywhere"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("fetch nonexistent object")

    assert result.final_status == "failed"


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------


def test_runtime_invalid_tool_name_appends_failure(tmp_path: Path) -> None:
    """Invalid tool name → failure appended, runtime continues."""
    decisions = [
        ToolCallDecision(tool="nonexistent_tool", arguments={}),
        FinishDecision(status="failed", summary="couldn't proceed"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("test")

    assert len(result.state.failures) == 1
    assert "invalid or non-selectable" in result.state.failures[0]["error"]


def test_runtime_finish_task_tool_call_rejected(tmp_path: Path) -> None:
    """tool_call to finish_task is rejected (selectable_by_model=False)."""
    decisions = [
        ToolCallDecision(tool="finish_task", arguments={}),
        FinishDecision(status="completed", summary="done"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("test")

    # finish_task should be rejected as non-selectable
    assert len(result.state.failures) == 1


def test_runtime_max_turns_exceeded(tmp_path: Path) -> None:
    """Runtime stops after max_turns and sets status=failed."""
    # Create more tool calls than max_turns
    decisions = [
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        ToolCallDecision(tool="navigate", arguments={"room_hint": "bedroom"}),
        ToolCallDecision(tool="navigate", arguments={"room_hint": "bathroom"}),
    ]
    settings = _make_settings(max_turns=2)
    runtime = _build_runtime(decisions, settings=settings, tmp_path=tmp_path)
    result = runtime.run("test")

    assert result.final_status == "failed"
    assert result.state.turn_index == 2


# ---------------------------------------------------------------------------
# Event trace
# ---------------------------------------------------------------------------


def test_runtime_emits_events_for_each_turn(tmp_path: Path) -> None:
    """EventSink records decision, tool_call, tool_result, state_transition for each turn."""
    decisions = [
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        FinishDecision(status="completed", summary="done"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("test")

    event_types = [e.event_type for e in result.events]
    assert "decision" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "state_transition" in event_types


def test_runtime_schema_invalid_tool_emits_error_event(tmp_path: Path) -> None:
    """Invalid tool name emits error event with tool name in payload."""
    decisions = [
        ToolCallDecision(tool="totally_bogus", arguments={}),
        FinishDecision(status="failed", summary="bad tool"),
    ]
    runtime = _build_runtime(decisions, tmp_path=tmp_path)
    result = runtime.run("test")

    error_events = [e for e in result.events if e.event_type == "error"]
    assert len(error_events) >= 1
    assert error_events[0].payload["tool"] == "totally_bogus"
    assert "invalid" in error_events[0].payload["error"]
    assert len(result.state.failures) == 1


# ---------------------------------------------------------------------------
# Tool manifest
# ---------------------------------------------------------------------------


def test_verify_in_selectable_tool_manifest() -> None:
    """verify must appear in tool_manifests() (selectable_by_model=True)."""
    registry = build_tool_registry()
    names = [m["name"] for m in registry.tool_manifests()]
    assert "verify" in names


def test_finish_task_not_in_selectable_tool_manifest() -> None:
    """finish_task must NOT appear in tool_manifests()."""
    registry = build_tool_registry()
    names = [m["name"] for m in registry.tool_manifests()]
    assert "finish_task" not in names


def test_all_11_tools_registered() -> None:
    """All 11 tools must be in the registry."""
    registry = build_tool_registry()
    expected = {
        "understand_task", "retrieve_memory", "ground_target", "get_skill",
        "navigate", "observe", "manipulate", "verify",
        "update_memory", "update_user_profile", "finish_task",
    }
    assert set(registry.all_names()) == expected
