"""Typed contracts shared by the service, clients and evaluators."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class EpisodePhase(StrEnum):
    CREATED = "created"
    PRECHECKING = "prechecking"
    READY_TO_CHANGE = "ready_to_change"
    CHANGE_SUBMITTED = "change_submitted"
    CHANGE_APPLIED = "change_applied"
    VERIFYING = "verifying"
    ANOMALY_DETECTED = "anomaly_detected"
    ROLLBACK_SUBMITTED = "rollback_submitted"
    ROLLED_BACK = "rolled_back"
    COMPLETED = "completed"
    ROLLBACK_VERIFIED = "rollback_verified"
    BLOCKED_PRECHECK = "blocked_precheck"
    ESCALATED = "escalated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ENVIRONMENT_FAILED = "environment_failed"
    AGENT_BUDGET_EXHAUSTED = "agent_budget_exhausted"


class JobStatus(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuditEvent(BaseModel):
    schema_version: Literal[1] = 1
    event_id: str
    sequence: int
    run_id: str
    action_id: str | None = None
    source: str
    kind: str
    stage: str
    status: str
    timestamp: datetime = Field(default_factory=utc_now)
    arguments: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    node_id: str | None = None
    state_version: int


class AutomationJob(BaseModel):
    job_id: str
    run_id: str
    action_id: str
    operation: Literal["add", "remove", "business_verify"]
    status: JobStatus = JobStatus.ACCEPTED
    business_return_code: int | None = None
    submitted_payload: dict[str, str]
    evidence_refs: list[str] = Field(default_factory=list)


class ActionReservation(BaseModel):
    action_id: str
    tool_name: str
    page_state_version: int


class ActionRecord(BaseModel):
    action_id: str
    tool_name: str
    reserved_state_version: int
    consumed: bool = False


class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    phase: EpisodePhase = EpisodePhase.CREATED
    state_version: int = 1
    variables: dict[str, str]
    target: dict[str, str]
    upstream_ready: bool
    anomaly_code: str | None = None
    causal_anomaly_armed: bool = False
    causal_add_job_id: str | None = None
    causal_grep_evidence_id: str | None = None
    terminal_outcome: str | None = None
    prechecks: dict[str, str] = Field(default_factory=dict)
    postchecks: dict[str, str] = Field(default_factory=dict)
    config_checks: dict[str, bool] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    jobs: dict[str, AutomationJob] = Field(default_factory=dict)
    action_ledger: dict[str, ActionRecord] = Field(default_factory=dict)
    add_grep_evidence_id: str | None = None
    rollback_grep_evidence_id: str | None = None
    business_verified: bool = False


class RunCreateRequest(BaseModel):
    run_id: str
    scenario_id: Literal["normal", "post_change_anomaly"] = "normal"


class RuntimeEventRequest(BaseModel):
    action_id: str | None = None
    tool_name: str
    status: str = "succeeded"
    arguments: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    node_id: str | None = None


class ActionEventRequest(BaseModel):
    operation: Literal["reserve", "record"]
    action_id: str
    tool_name: str
    page_state_version: int
    status: str = "succeeded"
    arguments: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    node_id: str | None = None


class ConfigCheckRequest(BaseModel):
    action_id: str
    page_state_version: int
    check: Literal["extension_config", "upstream_ready"]


class MonitorQueryRequest(BaseModel):
    action_id: str
    page_state_version: int
    query: Literal["alarm", "probe", "capacity", "runtime_metrics", "traffic"]
    region: str
    cluster: str


class AutomationJobRequest(BaseModel):
    action_id: str
    page_state_version: int
    script: Literal["svc_cfg_cli_runner", "svc_usage_record_fetcher"]
    operation: Literal["add", "remove", "business_verify"]
    parameters: dict[str, str]


class TerminalRequest(BaseModel):
    action_id: str
    page_state_version: int
    command: str


class DecisionRequest(BaseModel):
    action_id: str
    page_state_version: int
    stage: Literal["check_before_change", "change_implement", "change_verified", "change_rollback"]
    decision: Literal[
        "proceed",
        "block",
        "rollback",
        "complete",
        "rolled_back",
        "escalate",
        "insufficient_evidence",
    ]
    evidence_refs: list[str] = Field(default_factory=list)


class ToolPayload(BaseModel):
    success: bool
    run_id: str
    action_id: str
    backend_status: Literal["not_applicable", "accepted", "running", "succeeded", "failed"]
    page_state_version: int
    visible_observation: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    retryable: bool = False
    failure_reason: str | None = None
