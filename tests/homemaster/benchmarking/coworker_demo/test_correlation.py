from __future__ import annotations

from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def _run_context(run_id: str = "run-a") -> RunContext:
    return RunContext(
        session_id="s", run_id=run_id, turn_index=0, settings=None, event_sink=None
    )


def _capture_spec(executor):
    return ToolSpec(
        name="capture",
        description="Capture the current model tool call ID.",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=executor,
    )


def test_correlated_action_id_is_stable_and_run_scoped() -> None:
    run_a = _run_context()
    run_a.deps["current_tool_call_id"] = "call-17"
    run_b = _run_context("run-b")
    run_b.deps["current_tool_call_id"] = "call-17"

    first = correlated_action_id(run_a)

    assert first.startswith("action-")
    assert correlated_action_id(run_a) == first
    assert correlated_action_id(run_b) != first


def test_dispatcher_scopes_current_tool_call_id_to_each_executor() -> None:
    observed: list[str] = []

    def executor(*, arguments, run_context):
        observed.append(run_context.deps["current_tool_call_id"])
        return ToolResult(success=True, tool_name="capture")

    dispatcher = ToolDispatcher()
    dispatcher.register(_capture_spec(executor))
    run_context = _run_context()

    dispatcher.dispatch(
        tool_calls=[
            ToolCall(id="call-a", name="capture", arguments={}),
            ToolCall(id="call-b", name="capture", arguments={}),
        ],
        run_context=run_context,
    )

    assert observed == ["call-a", "call-b"]
    assert "current_tool_call_id" not in run_context.deps


def test_dispatcher_restores_prior_tool_call_id_when_executor_fails() -> None:
    prior = object()

    def executor(*, arguments, run_context):
        assert run_context.deps["current_tool_call_id"] == "call-failing"
        raise ValueError("boom")

    dispatcher = ToolDispatcher()
    dispatcher.register(_capture_spec(executor))
    run_context = _run_context()
    run_context.deps["current_tool_call_id"] = prior

    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call-failing", name="capture", arguments={})],
        run_context=run_context,
    )

    assert result[0].is_error is True
    assert run_context.deps["current_tool_call_id"] is prior
