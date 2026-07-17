"""Build the exact eleven-tool coworker registry in stable order."""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.browser_tools import browser_tool_specs
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id
from homemaster.benchmarking.coworker_demo.decision_tools import make_sop_decide
from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient
from homemaster.benchmarking.coworker_demo.terminal_tools import make_terminal_execute
from homemaster.domain.tools import make_skill_view
from homemaster.task_state.tools import make_task_planner_tool, make_task_progress_check_tool
from homemaster.tools.registry import ToolRegistry
from homemaster.tools.spec import ToolSpec

EXPECTED_COWORKER_TOOLS = (
    "task_planner",
    "task_progress_check",
    "skill_view",
    "browser_navigate",
    "browser_observe",
    "browser_click",
    "browser_fill",
    "browser_select",
    "browser_wait",
    "terminal_execute",
    "sop_decide",
)


def build_coworker_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_wrap_task_tool(make_task_planner_tool(), planner=True))
    registry.register(_wrap_task_tool(make_task_progress_check_tool(), planner=False))
    registry.register(_coworker_skill_view())
    for spec in browser_tool_specs():
        registry.register(spec)
    registry.register(make_terminal_execute())
    registry.register(make_sop_decide())
    if tuple(registry.all_names()) != EXPECTED_COWORKER_TOOLS:
        raise RuntimeError("coworker registry order drifted from the stable contract")
    return registry


def _coworker_skill_view() -> ToolSpec:
    spec = make_skill_view()
    original = spec.executor
    schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "enum": ["change_execution", "evidence_discipline"],
                "description": "Name of one available coworker skill to view.",
            }
        },
        "required": ["skill_name"],
    }

    def executor(*, arguments: dict[str, Any], run_context: RunContext):
        if original is None:
            raise RuntimeError(f"{spec.name} has no executor")
        budget = run_context.deps["coworker_budget"]
        outcome = run_context.deps["coworker_outcome"]
        budget.before_external(outcome)
        return original(arguments=arguments, run_context=run_context)

    return spec.model_copy(update={"input_schema": schema, "executor": executor})


def _wrap_task_tool(spec: ToolSpec, *, planner: bool) -> ToolSpec:
    original = spec.executor

    def executor(*, arguments: dict[str, Any], run_context: RunContext):
        if original is None:
            raise RuntimeError(f"{spec.name} has no executor")
        budget = run_context.deps["coworker_budget"]
        outcome = run_context.deps["coworker_outcome"]
        budget.before_external(outcome)
        result = original(arguments=arguments, run_context=run_context)
        client: EnvironmentClient = run_context.deps["coworker_environment"]
        state = client.state(run_context.run_id)
        if planner:
            node_id = "PLAN_CREATED"
        elif state["phase"] == "ready_to_change":
            node_id = "PRE_PROGRESS"
        elif state["phase"] == "change_applied":
            node_id = "NORMAL_PROGRESS" if state.get("business_verified") else "IMPLEMENT_PROGRESS"
        elif state["phase"] == "rollback_submitted":
            node_id = "ROLLBACK_PROGRESS"
        else:
            node_id = None
        mirrored = client.runtime_event(
            run_context.run_id,
            action_id=correlated_action_id(run_context),
            tool_name=spec.name,
            arguments=arguments,
            node_id=node_id,
        )
        if isinstance(result.data, dict):
            result.data["coworker_evidence_refs"] = [mirrored["event"]["event_id"]]
        return result

    return spec.model_copy(update={"executor": executor})
