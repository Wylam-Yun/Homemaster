"""Six model-selectable browser tools backed only by BrowserDriver."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.browser_driver import BrowserDriver
from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.correlation import (
    correlated_action_id,
    coworker_domain_run_id,
)
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def _deps(run_context: RunContext) -> tuple[BrowserDriver, CoworkerBudget, CoworkerOutcome]:
    return (
        run_context.deps["coworker_browser"],
        run_context.deps["coworker_budget"],
        run_context.deps["coworker_outcome"],
    )


def _execute(
    tool_name: str,
    run_context: RunContext,
    callback: Callable[[BrowserDriver, str], dict[str, Any]],
) -> ToolResult:
    driver, budget, outcome = _deps(run_context)
    domain_run_id = coworker_domain_run_id(run_context)
    budget.before_browser(outcome)
    action_id = correlated_action_id(run_context)
    observation = callback(driver, action_id)
    return ToolResult(
        success=True,
        tool_name=tool_name,
        executor_mode="programmatic",
        data={
            "success": True,
            "run_id": domain_run_id,
            "action_id": action_id,
            "backend_status": "succeeded",
            "page_state_version": observation.get("page_state_version", 0),
            "visible_observation": observation,
            "evidence_refs": observation.get("evidence_refs", []),
            "retryable": False,
        },
    )


def make_browser_navigate() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return _execute(
            "browser_navigate",
            run_context,
            lambda driver, action_id: driver.navigate(str(arguments["route"]), action_id),
        )

    return ToolSpec(
        name="browser_navigate",
        description="Open this run's ticket, monitor, or automation page in the headed browser.",
        input_schema={
            "type": "object",
            "properties": {
                "route": {"type": "string", "enum": ["ticket", "monitor", "automation"]}
            },
            "required": ["route"],
        },
        executor_mode="programmatic",
        executor=executor,
    )


def make_browser_click() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return _execute(
            "browser_click",
            run_context,
            lambda driver, action_id: driver.click(str(arguments["bid"]), action_id),
        )

    return ToolSpec(
        name="browser_click",
        description="Click one unique visible enabled data-bid and return its backend receipt.",
        input_schema={
            "type": "object",
            "properties": {"bid": {"type": "string"}},
            "required": ["bid"],
        },
        executor_mode="programmatic",
        executor=executor,
    )


def make_browser_fill() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return _execute(
            "browser_fill",
            run_context,
            lambda driver, action_id: driver.fill(
                str(arguments["bid"]), str(arguments["value"]), action_id
            ),
        )

    return ToolSpec(
        name="browser_fill",
        description="Fill one visible input identified by data-bid and verify its DOM readback.",
        input_schema={
            "type": "object",
            "properties": {"bid": {"type": "string"}, "value": {"type": "string"}},
            "required": ["bid", "value"],
        },
        executor_mode="programmatic",
        executor=executor,
    )


def make_browser_select() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return _execute(
            "browser_select",
            run_context,
            lambda driver, action_id: driver.select(
                str(arguments["bid"]), str(arguments["value"]), action_id
            ),
        )

    return ToolSpec(
        name="browser_select",
        description="Select one option by value and verify the DOM readback.",
        input_schema={
            "type": "object",
            "properties": {"bid": {"type": "string"}, "value": {"type": "string"}},
            "required": ["bid", "value"],
        },
        executor_mode="programmatic",
        executor=executor,
    )


def make_browser_wait() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        _driver, budget, _outcome = _deps(run_context)
        timeout = budget.timeout(float(arguments.get("timeout_s", 30)))
        return _execute(
            "browser_wait",
            run_context,
            lambda driver, action_id: driver.wait_for_job(
                str(arguments["job_id"]), action_id, timeout
            ),
        )

    return ToolSpec(
        name="browser_wait",
        description="Wait for the exact visible automation job row to reach succeeded or failed.",
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "target_status": {"type": "string", "enum": ["terminal"]},
                "timeout_s": {"type": "number", "minimum": 0.1, "maximum": 120},
            },
            "required": ["job_id", "target_status"],
        },
        executor_mode="programmatic",
        executor=executor,
    )


def browser_tool_specs() -> list[ToolSpec]:
    return [
        make_browser_navigate(),
        make_browser_click(),
        make_browser_fill(),
        make_browser_select(),
        make_browser_wait(),
    ]
