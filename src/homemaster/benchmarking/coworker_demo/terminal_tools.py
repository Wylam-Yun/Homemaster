"""Restricted terminal tool adapter."""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id
from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def make_terminal_execute() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        client: EnvironmentClient = run_context.deps["coworker_environment"]
        budget = run_context.deps["coworker_budget"]
        outcome = run_context.deps["coworker_outcome"]
        budget.before_terminal(outcome)
        action_id = correlated_action_id(run_context)
        version = client.state(run_context.run_id)["state_version"]
        client.reserve(run_context.run_id, action_id, "terminal_execute", version)
        payload = client.terminal(
            run_context.run_id,
            {
                "action_id": action_id,
                "page_state_version": version,
                "command": str(arguments["command"]),
            },
        )
        return ToolResult(
            success=True,
            tool_name="terminal_execute",
            executor_mode="programmatic",
            data=payload,
            evidence_refs=payload.get("evidence_refs", []),
        )

    return ToolSpec(
        name="terminal_execute",
        description=(
            "Execute only the ticket's exact grep -A 3 verification command in real tmux/Bash."
        ),
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        executor_mode="programmatic",
        executor=executor,
    )
