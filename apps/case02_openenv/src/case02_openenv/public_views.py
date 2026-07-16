"""Explicit projections for pages visible to the Agent browser."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from case02_openenv.models import RunState


class TicketPublicView(BaseModel):
    run_id: str
    state_version: int
    title: str
    description: str
    variables: dict[str, str]
    sections: list[dict[str, Any]]


class MonitorPublicView(BaseModel):
    run_id: str
    state_version: int
    target: dict[str, str]
    latest_results: dict[str, str] = Field(default_factory=dict)


class AutomationPublicView(BaseModel):
    run_id: str
    state_version: int
    variables: dict[str, str]
    target: dict[str, str]
    jobs: list[dict[str, Any]] = Field(default_factory=list)


def ticket_view(state: RunState, ticket: dict[str, Any]) -> TicketPublicView:
    sections = []
    for stage in ("check_before_change", "change_implement", "change_verified"):
        for step in ticket.get(stage, []):
            sections.append(
                {
                    "stage": stage,
                    "name": step.get("check_name", ""),
                    "description": step.get("operate_description", ""),
                    "verification": step.get("operate_verified", ""),
                    "rollback": step.get("operate_rollback", ""),
                    "commands": step.get("command_list", []),
                }
            )
    return TicketPublicView(
        run_id=state.run_id,
        state_version=state.state_version,
        title=ticket.get("sop_doc_name", "Change ticket"),
        description=ticket.get("description", ""),
        variables=dict(state.variables),
        sections=sections,
    )


def monitor_view(state: RunState) -> MonitorPublicView:
    results = dict(state.prechecks)
    results.update({f"post_{key}": value for key, value in state.postchecks.items()})
    return MonitorPublicView(
        run_id=state.run_id,
        state_version=state.state_version,
        target={"region": state.target["region"], "cluster": state.target["cluster"]},
        latest_results=results,
    )


def automation_view(state: RunState) -> AutomationPublicView:
    return AutomationPublicView(
        run_id=state.run_id,
        state_version=state.state_version,
        variables=dict(state.variables),
        target={
            "resource_bucket": state.target["resource_bucket"],
            "business_timestamp": state.target["business_timestamp"],
        },
        jobs=[
            {
                "job_id": job.job_id,
                "operation": job.operation,
                "status": job.status,
                "business_return_code": job.business_return_code,
            }
            for job in state.jobs.values()
        ],
    )
