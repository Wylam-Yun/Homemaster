"""Typed, immutable presentation protocol contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from case02_openenv.models import EpisodePhase

PlanStatus = Literal[
    "pending",
    "in_progress",
    "completed",
    "blocked",
    "cancelled",
    "uncertain",
]
DecisionState = Literal[
    "planning",
    "observing",
    "ready",
    "waiting",
    "verified",
    "blocked",
    "anomaly",
    "recovering",
    "terminal",
]
IncidentStatus = Literal["open", "resolved"]
ToolKind = Literal[
    "orchestration",
    "observation",
    "mutation",
    "wait",
    "verification",
    "gate",
]

TOOL_PRESENTATION: dict[str, tuple[str, ToolKind]] = {
    "task_planner": ("创建执行计划", "orchestration"),
    "task_progress_check": ("更新计划进度", "orchestration"),
    "skill_view": ("读取操作规范", "orchestration"),
    "browser_navigate": ("打开业务页面", "observation"),
    "observe": ("读取页面状态", "observation"),
    "browser_click": ("执行页面操作", "mutation"),
    "browser_fill": ("填写变更参数", "mutation"),
    "browser_select": ("选择变更选项", "mutation"),
    "browser_wait": ("等待自动化任务", "wait"),
    "terminal_execute": ("执行独立终端验证", "verification"),
    "sop_decide": ("提交流程决策", "gate"),
}

FAILURE_LABELS_ZH: dict[str, str] = {
    "plan_required": "尚未创建执行计划",
    "missing_precheck_evidence": "变更前检查证据不完整",
    "progress_required": "当前阶段进度尚未记录",
    "wait_required": "尚未等待准确任务完成",
    "postchecks_required": "变更后检查尚未完成",
    "rollback_verification_required": "回滚验证尚未完成",
    "rollback_decision_required": "尚未授权回滚",
    "missing_anomaly_evidence": "缺少因果异常证据",
    "missing_implementation_evidence": "缺少实施完成证据",
    "missing_postcheck_evidence": "缺少变更后检查证据",
    "missing_rollback_evidence": "缺少回滚完成证据",
    "external_state_mismatch": "外部状态与预期不一致",
    "parameter_mismatch": "操作参数与锁定目标不一致",
    "command_not_allowed": "命令不在允许范围",
    "invalid_decision_for_stage": "当前阶段不接受该决定",
    "stale_state_version": "页面状态已经变化",
    "action_replay": "重复动作已被拒绝",
    "terminal_outcome": "运行已经结束",
    "unclassified_failure": "未分类执行失败",
}

FACT_LABELS_ZH: dict[str, str] = {
    "ticket_opened": "已打开锁定变更单",
    "plan_persisted": "模型计划已持久化",
    "action_running": "模型已选择当前工具",
    "action_succeeded": "环境已返回成功",
    "action_failed": "环境拒绝或执行失败",
    "incident_open": "当前存在未恢复异常",
    "terminal_outcome": "运行已达到终态",
}
JUDGMENT_LABELS_ZH: dict[str, str] = {
    "plan_required": "必须先创建可审计计划",
    "observe_result": "必须读取并核对环境返回",
    "recovery_required": "必须先完成匹配恢复",
    "continue_sop": "允许继续锁定流程",
    "run_terminal": "不再允许新的业务动作",
}
NEXT_ACTION_LABELS_ZH: dict[str, str] = {
    "create_plan": "创建并持久化执行计划",
    "await_result": "等待当前工具返回",
    "recover_incident": "执行异常要求的恢复动作",
    "continue_locked_sop": "继续下一项锁定操作",
    "none": "无后续动作",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class ObservablePlanItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: PlanStatus


class ObservablePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ObservablePlanItem, ...] = ()
    current_id: str | None = None
    next_focus: str | None = None


class SummaryTerm(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    label_zh: str
    values: dict[str, str | int | bool] = Field(default_factory=dict)


class DecisionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: DecisionState
    fact: SummaryTerm
    judgment: SummaryTerm
    next_action: SummaryTerm


class IncidentRecovery(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    action_id: str
    resolved_sequence: int
    intervening_model_calls: int


class PresentationIncident(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    status: IncidentStatus
    failure_code: str
    label_zh: str
    failed_tool: str
    failed_action_id: str
    opened_sequence: int
    target: dict[str, str] = Field(default_factory=dict)
    recovery: IncidentRecovery | None = None


class CriticalHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    history_id: str
    sequence: int
    kind: Literal["gate", "job", "grep", "causal_alarm", "incident", "recovery", "terminal"]
    label_zh: str
    status: str
    action_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class PublicModelOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["assistant_reply"]
    text: str
    outcome: Literal["intermediate", "terminal", "premature"]


class PresentationInput(BaseModel):
    schema_version: Literal[2] = 2
    runtime_event_type: Literal[
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "model.public_reply",
        "runtime.turn_completed",
        "runtime.turn_failed",
    ]
    tool_call_id: str | None = None
    action_id: str | None = None
    tool_name: str | None = None
    tool_label_zh: str | None = None
    tool_kind: ToolKind | None = None
    status: Literal["running", "accepted", "succeeded", "failed", "rejected"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    plan: ObservablePlan | None = None
    public_model_output: PublicModelOutput | None = None
    timestamp: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> PresentationInput:
        allowed = {
            "tool.call_started": {"running"},
            "tool.call_completed": {"accepted", "succeeded"},
            "tool.call_failed": {"failed", "rejected"},
            "model.public_reply": {"succeeded"},
            "runtime.turn_completed": {"succeeded"},
            "runtime.turn_failed": {"failed"},
        }
        if self.status not in allowed[self.runtime_event_type]:
            raise ValueError(f"status {self.status!r} is invalid for {self.runtime_event_type!r}")
        is_tool = self.runtime_event_type.startswith("tool.")
        if is_tool and not (self.tool_call_id and self.tool_call_id.strip()):
            raise ValueError("tool lifecycle events require a nonempty tool_call_id")
        if is_tool and not (self.action_id and self.action_id.strip()):
            raise ValueError("tool lifecycle events require a nonempty action_id")
        if is_tool:
            expected = TOOL_PRESENTATION.get(self.tool_name or "")
            if expected is None:
                raise ValueError("tool lifecycle events require a known tool_name")
            supplied = (self.tool_label_zh, self.tool_kind)
            if supplied == (None, None):
                self.tool_label_zh, self.tool_kind = expected
            elif supplied != expected:
                raise ValueError("tool presentation label or kind does not match server contract")
        elif self.tool_name or self.tool_label_zh or self.tool_kind:
            raise ValueError("non-tool events cannot carry tool identity")
        if self.failure_code is not None and self.failure_code not in FAILURE_LABELS_ZH:
            raise ValueError("unknown presentation failure code")
        if self.runtime_event_type == "model.public_reply" and self.public_model_output is None:
            raise ValueError("model.public_reply requires public_model_output")
        return self


class PresentationTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: str
    check_name: str
    source_field: Literal["operate_description", "operate_verified", "operate_rollback"]
    source_text: str
    source_sha256: str


class PresentationEvent(BaseModel):
    schema_version: Literal[2] = 2
    event_id: str
    sequence: int
    run_id: str
    event_type: str
    timestamp: datetime
    monotonic_offset_s: float | None = None
    tool_call_id: str | None = None
    action_id: str | None = None
    stage: str
    task: PresentationTask | None = None
    tool_name: str | None = None
    tool_label_zh: str | None = None
    tool_kind: ToolKind | None = None
    status: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    failure: str | None = None
    failure_code: str | None = None
    plan: ObservablePlan | None = None
    public_model_output: PublicModelOutput | None = None
    decision_summary: DecisionSummary | None = None
    incident_delta: PresentationIncident | None = None


class ObservablePresentationState(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: ObservablePlan
    current_action: PresentationEvent | None
    last_result: PresentationEvent | None
    public_model_output: PublicModelOutput | None
    decision_summary: DecisionSummary
    incidents: tuple[PresentationIncident, ...] = ()
    critical_history: tuple[CriticalHistoryEntry, ...] = ()


class PresentationSnapshot(BaseModel):
    schema_version: Literal[2] = 2
    run_id: str
    phase: EpisodePhase
    stage: str
    terminal_outcome: str | None = None
    current_task: PresentationTask | None = None
    plan: ObservablePlan = Field(default_factory=ObservablePlan)
    current_action: PresentationEvent | None = None
    last_result: PresentationEvent | None = None
    public_model_output: PublicModelOutput | None = None
    decision_summary: DecisionSummary
    incidents: list[PresentationIncident] = Field(default_factory=list)
    critical_history: list[CriticalHistoryEntry] = Field(default_factory=list)
    in_flight: list[PresentationEvent] = Field(default_factory=list)
    last_event: PresentationEvent | None = None
    last_sequence: int = 0
    completed_steps: list[PresentationTask] = Field(default_factory=list)
    next_step: str
    presentation_failures: list[str] = Field(default_factory=list)
