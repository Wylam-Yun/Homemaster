"""Persisted SOP decision tool and terminal outcome bridge."""

from __future__ import annotations

from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id
from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def make_sop_decide() -> ToolSpec:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        client: EnvironmentClient = run_context.deps["coworker_environment"]
        budget = run_context.deps["coworker_budget"]
        outcome = run_context.deps["coworker_outcome"]
        budget.before_external(outcome)
        action_id = correlated_action_id(run_context)
        version = client.state(run_context.run_id)["state_version"]
        client.reserve(run_context.run_id, action_id, "sop_decide", version)
        payload = client.decision(
            run_context.run_id,
            {
                "action_id": action_id,
                "page_state_version": version,
                "stage": str(arguments["stage"]),
                "decision": str(arguments["decision"]),
                "evidence_refs": list(arguments.get("evidence_refs") or []),
            },
        )
        outcome.mark(str(arguments["decision"]))
        payload["terminal"] = outcome.terminal
        payload["classification"] = outcome.classification
        return ToolResult(
            success=True,
            tool_name="sop_decide",
            executor_mode="programmatic",
            data=payload,
            evidence_refs=payload.get("evidence_refs", []),
        )

    return ToolSpec(
        name="sop_decide",
        description=(
            "Persist an evidence-backed SOP gate decision. Use check_before_change/proceed after "
            "prechecks and change_implement/proceed after the add job plus terminal readback. "
            "Terminal decisions stop the run."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": [
                        "check_before_change",
                        "change_implement",
                        "change_verified",
                        "change_rollback",
                    ],
                },
                "decision": {
                    "type": "string",
                    "enum": [
                        "proceed",
                        "block",
                        "rollback",
                        "complete",
                        "rolled_back",
                        "escalate",
                        "insufficient_evidence",
                    ],
                },
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["stage", "decision", "evidence_refs"],
        },
        executor_mode="programmatic",
        executor=executor,
    )
