"""Tests for ToolDispatcher — new dispatch(list[ToolCall]) → list[ToolResultMessage] API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homemaster.agent.messages import ToolCall, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def _make_settings(**kwargs: Any) -> SimpleNamespace:
    defaults = {
        "run_id": "test-001",
        "runtime_root": "/tmp/runs",
        "debug_root": "/tmp/debug",
        "results_root": "/tmp/results",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_run_context(settings: SimpleNamespace | None = None, **kwargs: Any) -> RunContext:
    defaults = {
        "session_id": "s1",
        "run_id": "r1",
        "turn_index": 0,
        "event_sink": None,
    }
    defaults.update(kwargs)
    return RunContext(settings=settings or _make_settings(), **defaults)


# ---------------------------------------------------------------------------
# ToolDispatcher tests
# ---------------------------------------------------------------------------


def test_dispatcher_accepts_generic_agent_state(tmp_path: Any) -> None:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        assert run_context.deps == {"current_tool_call_id": "call_1"}
        return ToolResult(
            success=True,
            tool_name="echo",
            executor_mode="programmatic",
            data=arguments,
        )

    spec = ToolSpec(
        name="echo",
        description="Echo input",
        input_schema={"type": "object", "required": ["text"]},
        executor_mode="programmatic",
        executor=executor,
    )
    settings = SimpleNamespace(
        run_id="r1",
        runtime_root=tmp_path,
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
        run_context=_make_run_context(settings),
    )
    assert result[0].tool_call_id == "call_1"
    assert result[0].is_error is False


def test_dispatcher_passes_run_context_deps_without_interpreting_them(tmp_path: Any) -> None:
    sentinel_home = object()

    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        assert run_context.deps["home"] is sentinel_home
        return ToolResult(
            success=True,
            tool_name="uses_home",
            executor_mode="programmatic",
            data={},
        )

    spec = ToolSpec(
        name="uses_home",
        description="Uses opaque home deps",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )
    settings = SimpleNamespace(
        run_id="r1",
        runtime_root=tmp_path,
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="uses_home", arguments={})],
        run_context=RunContext(
            session_id="s1",
            run_id="r1",
            turn_index=0,
            settings=settings,
            event_sink=None,
            deps={"home": sentinel_home},
        ),
    )
    assert result[0].tool_call_id == "call_1"


def test_dispatch_returns_error_for_unknown_tool() -> None:
    dispatcher = ToolDispatcher()
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="nonexistent", arguments={})],
        run_context=_make_run_context(),
    )
    assert len(result) == 1
    assert result[0].is_error is True
    assert "unknown tool" in result[0].content[0].text


def test_dispatch_returns_error_when_no_executor() -> None:
    spec = ToolSpec(
        name="empty",
        description="No executor",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=None,
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="empty", arguments={})],
        run_context=_make_run_context(),
    )
    assert result[0].is_error is True
    assert "no executor" in result[0].content[0].text


def test_dispatch_returns_error_on_executor_exception() -> None:
    def bad_executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        raise ValueError("boom")

    spec = ToolSpec(
        name="bad",
        description="Raises",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=bad_executor,
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="bad", arguments={})],
        run_context=_make_run_context(),
    )
    assert result[0].is_error is True
    assert "boom" in result[0].content[0].text


def test_dispatch_validates_required_arguments() -> None:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return ToolResult(success=True, tool_name="echo")

    spec = ToolSpec(
        name="echo",
        description="Requires text",
        input_schema={"type": "object", "required": ["text"]},
        executor_mode="programmatic",
        executor=executor,
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="echo", arguments={})],
        run_context=_make_run_context(),
    )
    assert result[0].is_error is True
    assert "missing required" in result[0].content[0].text


def test_dispatcher_callable_adapter(tmp_path: Any) -> None:
    """ToolDispatcher.__call__(name, args) works as a callable adapter."""
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return ToolResult(success=True, tool_name="echo", data=arguments)

    spec = ToolSpec(
        name="echo",
        description="Echo",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )
    settings = SimpleNamespace(
        run_id="r1",
        runtime_root=tmp_path,
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    dispatcher.set_run_context(RunContext(
        session_id="s1",
        run_id="r1",
        turn_index=0,
        settings=settings,
        event_sink=None,
    ))
    result = dispatcher("echo", {"text": "hi"})
    assert isinstance(result, ToolResultMessage)
    assert result.is_error is False


def test_dispatch_preserves_parallel_tool_call_ids() -> None:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return ToolResult(success=True, tool_name="echo", data=arguments)

    spec = ToolSpec(
        name="echo",
        description="Echo",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    result = dispatcher.dispatch(
        tool_calls=[
            ToolCall(id="call_1", name="echo", arguments={"a": 1}),
            ToolCall(id="call_2", name="echo", arguments={"b": 2}),
        ],
        run_context=_make_run_context(),
    )
    assert len(result) == 2
    assert result[0].tool_call_id == "call_1"
    assert result[1].tool_call_id == "call_2"


def test_dispatch_failure_preserves_data_for_model_context() -> None:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return ToolResult(
            success=False,
            tool_name="robot_navigate",
            executor_mode="programmatic",
            failure_reason="invalid_action",
            data={
                "attempted_command": "go to fridge 1",
                "feedback": "Nothing happens.",
                "observation": "You are in the kitchen.",
                "done": False,
                "won": False,
                "invalid_action_count": 1,
            },
            retryable=True,
        )

    spec = ToolSpec(
        name="robot_navigate",
        description="Navigate",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )
    dispatcher = ToolDispatcher()
    dispatcher.register(spec)

    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call_1", name="robot_navigate", arguments={})],
        run_context=_make_run_context(),
    )

    message = result[0]
    assert message.is_error is True
    assert message.data is not None
    assert message.data["failure_reason"] == "invalid_action"
    assert message.data["observation"] == "You are in the kitchen."
    assert "Nothing happens." in message.content[0].text
    assert "admissible_commands" not in message.content[0].text
