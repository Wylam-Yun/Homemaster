from __future__ import annotations

import pytest

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.correlation import (
    action_id_for,
    correlated_action_id,
    coworker_domain_run_id,
)


def _run_context(run_id: str = "run-a") -> RunContext:
    return RunContext(
        session_id="s",
        run_id=run_id,
        turn_index=0,
        settings=None,
        event_sink=None,
        deps={"coworker_domain_run_id": run_id},
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


def test_correlated_action_id_has_unambiguous_run_and_call_boundaries() -> None:
    first = _run_context("a:b")
    first.deps["current_tool_call_id"] = "c"
    second = _run_context("a")
    second.deps["current_tool_call_id"] = "b:c"

    assert correlated_action_id(first) != correlated_action_id(second)


def test_domain_run_id_is_explicit_and_distinct_from_application_run_id() -> None:
    context = _run_context("run-application")
    context.deps["coworker_domain_run_id"] = "coworker-domain"
    context.deps["current_tool_call_id"] = "call-17"

    assert coworker_domain_run_id(context) == "coworker-domain"
    assert correlated_action_id(context) == action_id_for("coworker-domain", "call-17")


def test_domain_run_id_is_required_for_coworker_external_actions() -> None:
    context = RunContext(
        session_id="s", run_id="run-application", turn_index=0, settings=None, event_sink=None
    )

    with pytest.raises(RuntimeError, match="coworker_domain_run_id"):
        coworker_domain_run_id(context)
