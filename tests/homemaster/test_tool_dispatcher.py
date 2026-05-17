"""Tests for ToolDispatcher and StateUpdater."""

from __future__ import annotations

from typing import Any

from homemaster.agent.state import AgentState
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec
from homemaster.tools.state_updater import StateUpdater


def _make_settings(**kwargs) -> RuntimeSettings:
    defaults = {
        "run_id": "test-001",
        "runtime_root": "/tmp/runs",
        "debug_root": "/tmp/debug",
        "results_root": "/tmp/results",
    }
    defaults.update(kwargs)
    return RuntimeSettings(**defaults)


def _make_spec(name: str = "test_tool", **kwargs) -> ToolSpec:
    defaults = {
        "name": name,
        "description": "A test tool",
        "executor_mode": "programmatic",
        "selectable_by_model": True,
    }
    defaults.update(kwargs)
    return ToolSpec(**defaults)


# ---------------------------------------------------------------------------
# ToolDispatcher tests
# ---------------------------------------------------------------------------


def test_dispatch_invokes_executor() -> None:
    def _ok_executor(
        *,
        arguments: dict,
        state: AgentState,
        settings: Any,
        event_sink: Any = None,
    ) -> ToolResult:
        return ToolResult(success=True, tool_name="test_tool", data={"ok": True})

    spec = _make_spec(executor=_ok_executor)
    dispatcher = ToolDispatcher()
    state = AgentState()
    settings = _make_settings()

    result = dispatcher.dispatch(spec=spec, arguments={}, state=state, settings=settings)
    assert result.success is True
    assert result.data == {"ok": True}


def test_dispatch_returns_failure_when_no_executor() -> None:
    spec = _make_spec(executor=None)
    dispatcher = ToolDispatcher()
    state = AgentState()
    settings = _make_settings()

    result = dispatcher.dispatch(spec=spec, arguments={}, state=state, settings=settings)
    assert result.success is False
    assert "no executor" in result.failure_reason


def test_dispatch_returns_failure_on_executor_exception() -> None:
    def _bad_executor(
        *,
        arguments: dict,
        state: AgentState,
        settings: Any,
        event_sink: Any = None,
    ) -> ToolResult:
        raise ValueError("boom")

    spec = _make_spec(executor=_bad_executor)
    dispatcher = ToolDispatcher()
    state = AgentState()
    settings = _make_settings()

    result = dispatcher.dispatch(spec=spec, arguments={}, state=state, settings=settings)
    assert result.success is False
    assert "boom" in result.failure_reason


def test_dispatch_blocks_tool_not_in_active_skill() -> None:
    def _ok_executor(
        *,
        arguments: dict,
        state: AgentState,
        settings: Any,
        event_sink: Any = None,
    ) -> ToolResult:
        return ToolResult(success=True, tool_name="blocked_tool")

    spec = _make_spec(name="blocked_tool", executor=_ok_executor)
    dispatcher = ToolDispatcher()
    state = AgentState(
        active_skills=["fetch_object"],
        loaded_skill_contexts={"fetch_object": {"allowed_tools": ["navigate", "observe"]}},
    )
    settings = _make_settings()

    result = dispatcher.dispatch(spec=spec, arguments={}, state=state, settings=settings)
    assert result.success is False
    assert "not allowed" in result.failure_reason


def test_dispatch_allows_tool_when_no_active_skill() -> None:
    def _ok_executor(
        *,
        arguments: dict,
        state: AgentState,
        settings: Any,
        event_sink: Any = None,
    ) -> ToolResult:
        return ToolResult(success=True, tool_name="any_tool")

    spec = _make_spec(name="any_tool", executor=_ok_executor)
    dispatcher = ToolDispatcher()
    state = AgentState()  # no active_skills
    settings = _make_settings()

    result = dispatcher.dispatch(spec=spec, arguments={}, state=state, settings=settings)
    assert result.success is True


# ---------------------------------------------------------------------------
# StateUpdater tests
# ---------------------------------------------------------------------------


def test_state_updater_appends_failure_on_failed_result() -> None:
    updater = StateUpdater()
    spec = _make_spec()
    state = AgentState()
    result = ToolResult(success=False, tool_name="test", failure_reason="boom")

    updated = updater.apply(state=state, result=result, spec=spec)
    assert len(updated.failures) == 1
    assert updated.failures[0]["error"] == "boom"


def test_state_updater_sets_task_card_from_understand_task() -> None:
    updater = StateUpdater()
    spec = _make_spec(name="understand_task")
    state = AgentState()
    result = ToolResult(
        success=True, tool_name="understand_task",
        data={"task_card": {"target": "cup", "intent": "fetch"}},
    )

    updated = updater.apply(state=state, result=result, spec=spec)
    assert updated.task_card == {"target": "cup", "intent": "fetch"}


def test_state_updater_sets_memory_hits_from_retrieve_memory() -> None:
    updater = StateUpdater()
    spec = _make_spec(name="retrieve_memory")
    state = AgentState()
    result = ToolResult(
        success=True, tool_name="retrieve_memory",
        data={"hits": [{"memory_id": "m1"}, {"memory_id": "m2"}]},
    )

    updated = updater.apply(state=state, result=result, spec=spec)
    assert len(updated.memory_hits) == 2


def test_state_updater_sets_current_location_from_navigate() -> None:
    updater = StateUpdater()
    spec = _make_spec(name="navigate")
    state = AgentState()
    result = ToolResult(
        success=True, tool_name="navigate",
        data={"location": "kitchen", "observation": "arrived"},
    )

    updated = updater.apply(state=state, result=result, spec=spec)
    assert updated.current_location == "kitchen"
    assert len(updated.actions) == 1


def test_state_updater_sets_holding_from_manipulate() -> None:
    updater = StateUpdater()
    spec = _make_spec(name="manipulate")
    state = AgentState()
    result = ToolResult(
        success=True, tool_name="manipulate",
        data={"holding": "cup", "action": "pick_up"},
    )

    updated = updater.apply(state=state, result=result, spec=spec)
    assert updated.holding_object == "cup"


def test_state_updater_appends_verification() -> None:
    updater = StateUpdater()
    spec = _make_spec(name="verify")
    state = AgentState()
    result = ToolResult(
        success=True, tool_name="verify",
        data={"verified": True, "reason": "object held"},
    )

    updated = updater.apply(state=state, result=result, spec=spec)
    assert len(updated.verifications) == 1
    assert updated.verifications[0]["result"]["verified"] is True


def test_state_updater_sets_loaded_skill_context_from_get_skill() -> None:
    updater = StateUpdater()
    spec = _make_spec(name="get_skill")
    state = AgentState()
    result = ToolResult(
        success=True, tool_name="get_skill",
        data={"name": "fetch_object", "content": "...", "allowed_tools": ["navigate"]},
    )

    updated = updater.apply(state=state, result=result, spec=spec)
    assert "fetch_object" in updated.loaded_skill_contexts
    assert updated.loaded_skill_contexts["fetch_object"]["content"] == "..."
