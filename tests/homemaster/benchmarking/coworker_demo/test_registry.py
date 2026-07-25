from __future__ import annotations

from types import SimpleNamespace

import pytest

from homemaster.benchmarking.coworker_demo.budget import BudgetExceeded, CoworkerBudget
from homemaster.benchmarking.coworker_demo.registry import (
    EXPECTED_COWORKER_TOOLS,
    build_coworker_tool_registry,
)
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome
from homemaster.domain.tool_registry import build_home_tool_registry


def test_registry_contains_exactly_ten_action_tools_in_stable_order() -> None:
    registry = build_coworker_tool_registry()
    assert tuple(registry.all_names()) == EXPECTED_COWORKER_TOOLS
    assert len(registry.all_names()) == 10
    skill_names = registry.get("load_skill").input_schema["properties"]["name"]["enum"]
    assert skill_names == ["change_execution", "evidence_discipline"]


def test_default_home_registry_is_unchanged() -> None:
    home_registry = build_home_tool_registry()
    home_names = home_registry.all_names()
    assert "browser_navigate" not in home_names
    assert "sop_decide" not in home_names
    assert "enum" not in home_registry.get("load_skill").input_schema["properties"]["name"]


@pytest.mark.parametrize("tool_name", ["task_planner", "task_progress_check", "load_skill"])
def test_builtin_coworker_tools_reject_calls_after_terminal_outcome(tool_name: str) -> None:
    outcome = CoworkerOutcome()
    outcome.mark("complete")
    run_context = SimpleNamespace(
        deps={"coworker_budget": CoworkerBudget(), "coworker_outcome": outcome}
    )
    spec = build_coworker_tool_registry().get(tool_name)
    assert spec is not None and spec.executor is not None
    with pytest.raises(BudgetExceeded, match="terminal outcome"):
        spec.executor(arguments={}, run_context=run_context)
