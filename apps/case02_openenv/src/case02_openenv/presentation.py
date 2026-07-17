"""Map live runtime actions to exact, locked SOP ticket text."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from case02_openenv.models import EpisodePhase, RunState

MONITOR_BIDS = {
    "monitor-query-alarm",
    "monitor-query-probe",
    "monitor-query-capacity",
    "monitor-query-runtime-metrics",
    "monitor-query-traffic",
}
CONFIG_BIDS = {
    "ticket-query-extension-config",
    "ticket-query-upstream-ready",
}
ORCHESTRATION_TOOLS = {
    "task_planner",
    "task_progress_check",
    "skill_view",
    "sop_decide",
}
DISPLAY_STAGES = {
    "check_before_change",
    "change_implement",
    "implementation_verify",
    "change_verified",
    "business_verify",
    "change_rollback",
    "terminal",
}


class PresentationMappingError(RuntimeError):
    """Raised when an action cannot be mapped to trusted ticket text."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class PresentationInput(BaseModel):
    runtime_event_type: Literal[
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "runtime.turn_completed",
        "runtime.turn_failed",
    ]
    tool_call_id: str | None = None
    action_id: str | None = None
    tool_name: str | None = None
    status: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)


class PresentationTask(BaseModel):
    stage: str
    check_name: str
    source_field: Literal["operate_description", "operate_verified", "operate_rollback"]
    source_text: str
    source_sha256: str


class PresentationEvent(BaseModel):
    schema_version: Literal[1] = 1
    event_id: str
    sequence: int
    run_id: str
    event_type: str
    timestamp: datetime
    tool_call_id: str | None = None
    action_id: str | None = None
    stage: str
    task: PresentationTask | None = None
    tool_name: str | None = None
    status: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    failure: str | None = None


def ticket_task(
    ticket: dict[str, Any],
    stage: str,
    index: int,
    source_field: Literal["operate_description", "operate_verified", "operate_rollback"],
) -> PresentationTask:
    try:
        source = ticket[stage][index]
        source_text = source[source_field]
        check_name = source["check_name"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PresentationMappingError(
            f"No trusted SOP mapping for ticket:{stage}[{index}].{source_field}"
        ) from exc
    if not isinstance(source_text, str) or not source_text:
        raise PresentationMappingError(
            f"No trusted SOP mapping for ticket:{stage}[{index}].{source_field}"
        )
    return PresentationTask(
        stage=stage,
        check_name=check_name,
        source_field=source_field,
        source_text=source_text,
        source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    )


def map_task(
    ticket: dict[str, Any],
    state: RunState,
    item: PresentationInput,
    previous: PresentationTask | None,
) -> PresentationTask | None:
    if item.runtime_event_type in {"runtime.turn_completed", "runtime.turn_failed"}:
        return previous

    arguments = item.arguments
    bid = arguments.get("bid")
    operation = arguments.get("operation") or item.result.get("operation")
    post = state.phase in {
        EpisodePhase.CHANGE_APPLIED,
        EpisodePhase.VERIFYING,
        EpisodePhase.ANOMALY_DETECTED,
        EpisodePhase.ROLLBACK_SUBMITTED,
        EpisodePhase.ROLLED_BACK,
        EpisodePhase.COMPLETED,
    }

    if bid in CONFIG_BIDS:
        return ticket_task(ticket, "check_before_change", 1, "operate_description")
    if bid in MONITOR_BIDS:
        stage = "change_verified" if post else "check_before_change"
        return ticket_task(ticket, stage, 0, "operate_description")

    if item.tool_name == "browser_navigate":
        route = arguments.get("route")
        if route == "ticket":
            return ticket_task(ticket, "check_before_change", 0, "operate_description")
        if route == "monitor":
            stage = "change_verified" if post else "check_before_change"
            return ticket_task(ticket, stage, 0, "operate_description")
        if route == "automation" and state.phase == EpisodePhase.ROLLBACK_SUBMITTED:
            return ticket_task(ticket, "change_implement", 0, "operate_rollback")
        if route == "automation" and previous is not None:
            return previous
        return ticket_task(ticket, "change_implement", 0, "operate_description")

    if item.tool_name in {"browser_fill", "browser_select"}:
        value = arguments.get("value")
        if value == "remove" or state.phase == EpisodePhase.ROLLBACK_SUBMITTED:
            return ticket_task(ticket, "change_implement", 0, "operate_rollback")
        if value in {"business_verify", "svc_usage_record_fetcher"}:
            return ticket_task(ticket, "change_verified", 1, "operate_description")
        if isinstance(bid, str) and bid.startswith("automation-"):
            return ticket_task(ticket, "change_implement", 0, "operate_description")

    if bid == "automation-submit" or item.tool_name == "browser_wait":
        business_name = ticket["change_verified"][1]["check_name"]
        if operation == "business_verify" or state.phase == EpisodePhase.VERIFYING:
            return ticket_task(ticket, "change_verified", 1, "operate_description")
        if operation == "remove" or state.phase == EpisodePhase.ROLLBACK_SUBMITTED:
            return ticket_task(ticket, "change_implement", 0, "operate_rollback")
        if operation is None and previous is not None and previous.check_name == business_name:
            return previous
        return ticket_task(ticket, "change_implement", 0, "operate_description")

    if item.tool_name == "terminal_execute":
        field: Literal["operate_verified", "operate_rollback"] = (
            "operate_rollback"
            if state.phase == EpisodePhase.ROLLBACK_SUBMITTED
            else "operate_verified"
        )
        return ticket_task(ticket, "change_implement", 0, field)

    if item.tool_name in ORCHESTRATION_TOOLS or item.tool_name == "browser_observe":
        return previous

    control = bid or operation
    raise PresentationMappingError(
        f"No trusted SOP mapping for {item.tool_name or 'run event'}:{control}"
    )


def display_stage(
    ticket: dict[str, Any],
    state: RunState,
    item: PresentationInput,
    task: PresentationTask | None,
) -> str:
    operation = item.arguments.get("operation") or item.result.get("operation")
    if state.terminal_outcome is not None or item.runtime_event_type.startswith("runtime.turn_"):
        return "terminal"
    if state.phase in {EpisodePhase.ROLLBACK_SUBMITTED, EpisodePhase.ROLLED_BACK}:
        return "change_rollback"
    business_name = ticket["change_verified"][1]["check_name"]
    if operation == "business_verify" or (task is not None and task.check_name == business_name):
        return "business_verify"
    if item.tool_name == "terminal_execute" or (
        item.tool_name == "browser_wait" and operation == "add"
    ):
        return "implementation_verify"
    if task is not None and task.stage == "change_verified":
        return "change_verified"
    if task is not None and task.stage == "change_implement":
        return "change_implement"
    return "check_before_change"
