from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.browser_tools import make_browser_navigate
from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.decision_tools import make_sop_decide
from homemaster.benchmarking.coworker_demo.registry import build_coworker_tool_registry
from homemaster.benchmarking.coworker_demo.terminal_tools import make_terminal_execute
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome
from homemaster.task_state.store import TaskStateStore


class RecordingEnvironment:
    def __init__(self) -> None:
        self.run_ids: list[str] = []

    def state(self, run_id: str) -> dict[str, Any]:
        self.run_ids.append(run_id)
        return {"state_version": 7, "phase": "ready_to_change", "business_verified": False}

    def reserve(self, run_id: str, *_args: Any) -> None:
        self.run_ids.append(run_id)

    def terminal(self, run_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        self.run_ids.append(run_id)
        return {"evidence_refs": ["ev-terminal"]}

    def decision(self, run_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        self.run_ids.append(run_id)
        return {"evidence_refs": ["ev-decision"]}

    def runtime_event(self, run_id: str, **_kwargs: Any) -> dict[str, Any]:
        self.run_ids.append(run_id)
        return {"event": {"event_id": "ev-plan"}}


class RecordingBrowser:
    def navigate(self, route: str, action_id: str) -> dict[str, Any]:
        return {
            "route": route,
            "action_id": action_id,
            "page_state_version": 7,
            "evidence_refs": ["ev-browser"],
        }


def _context(environment: RecordingEnvironment) -> RunContext:
    return RunContext(
        session_id="coworker-domain",
        run_id="run-application",
        turn_index=0,
        settings=None,
        event_sink=None,
        deps={
            "coworker_domain_run_id": "coworker-domain",
            "coworker_environment": environment,
            "coworker_browser": RecordingBrowser(),
            "coworker_budget": CoworkerBudget(),
            "coworker_outcome": CoworkerOutcome(),
            "current_tool_call_id": "call-1",
            "task_state_store": TaskStateStore(run_id="run-application"),
        },
    )


def _execute(spec: Any, arguments: dict[str, Any], context: RunContext) -> Any:
    assert spec.executor is not None
    return spec.executor(arguments=arguments, run_context=context)


def test_all_coworker_external_tools_route_to_domain_run_id() -> None:
    environment = RecordingEnvironment()
    context = _context(environment)

    browser_result = _execute(make_browser_navigate(), {"route": "ticket"}, context)
    _execute(make_terminal_execute(), {"command": "grep -A 3 key config"}, context)
    _execute(
        make_sop_decide(),
        {"stage": "check_before_change", "decision": "proceed", "evidence_refs": []},
        context,
    )
    planner = build_coworker_tool_registry().get("task_planner")
    assert planner is not None
    _execute(
        planner,
        {
            "goal": "change",
            "subtasks": [{"id": "1", "description": "perform change"}],
        },
        context,
    )

    assert browser_result.data["run_id"] == "coworker-domain"
    assert environment.run_ids
    assert set(environment.run_ids) == {"coworker-domain"}
