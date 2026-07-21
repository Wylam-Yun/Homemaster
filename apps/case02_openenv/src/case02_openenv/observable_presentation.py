"""Pure reconstruction of observable state from persisted presentation events."""

from __future__ import annotations

from collections.abc import Sequence

from case02_openenv.models import RunState
from case02_openenv.presentation_models import (
    FACT_LABELS_ZH,
    FAILURE_LABELS_ZH,
    JUDGMENT_LABELS_ZH,
    NEXT_ACTION_LABELS_ZH,
    CriticalHistoryEntry,
    DecisionSummary,
    IncidentRecovery,
    ObservablePlan,
    ObservablePresentationState,
    PresentationEvent,
    PresentationIncident,
    PublicModelOutput,
    SummaryTerm,
)

_TERMINAL_STATUSES = {"accepted", "succeeded", "failed", "rejected"}
_TARGET_KEYS = ("job_id", "stage", "decision", "operation", "bid", "value")


def _summary_term(
    code: str,
    labels: dict[str, str],
    values: dict[str, str | int | bool] | None = None,
) -> SummaryTerm:
    return SummaryTerm(code=code, label_zh=labels[code], values=values or {})


def _safe_target(event: PresentationEvent) -> dict[str, str]:
    target: dict[str, str] = {}
    for source in (event.arguments, event.result):
        for key in _TARGET_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value:
                target[key] = value
    return target


def _matches_target(incident: PresentationIncident, event: PresentationEvent) -> bool:
    if not incident.target:
        return True
    candidate = _safe_target(event)
    return all(candidate.get(key) == value for key, value in incident.target.items())


def _is_recovery(incident: PresentationIncident, event: PresentationEvent) -> bool:
    if event.sequence <= incident.opened_sequence or event.status != "succeeded":
        return False
    code = incident.failure_code
    if code == "terminal_outcome":
        return False
    if code == "wait_required":
        return event.tool_name == "browser_wait" and _matches_target(incident, event)
    if code == "plan_required":
        return event.tool_name == "task_planner" and event.plan is not None
    if code == "progress_required":
        return event.tool_name == "task_progress_check"
    if code in {
        "missing_precheck_evidence",
        "missing_anomaly_evidence",
        "missing_implementation_evidence",
        "missing_postcheck_evidence",
        "missing_rollback_evidence",
        "postchecks_required",
        "rollback_verification_required",
        "rollback_decision_required",
        "invalid_decision_for_stage",
    }:
        return event.tool_name in {"browser_click", "terminal_execute", "sop_decide"}
    if code in {"stale_state_version", "action_replay", "parameter_mismatch"}:
        return event.tool_name == incident.failed_tool and _matches_target(incident, event)
    if code == "command_not_allowed":
        return event.tool_name == "terminal_execute"
    if code == "external_state_mismatch":
        return event.tool_name in {"observe", "terminal_execute", "sop_decide"}
    return event.tool_name == incident.failed_tool and _matches_target(incident, event)


def reduce_incidents(events: Sequence[PresentationEvent]) -> tuple[PresentationIncident, ...]:
    incidents: list[PresentationIncident] = []
    for event in events:
        if event.status in {"failed", "rejected"} and event.failure_code:
            incidents.append(
                PresentationIncident(
                    incident_id=f"incident-{event.sequence:05d}",
                    status="open",
                    failure_code=event.failure_code,
                    label_zh=FAILURE_LABELS_ZH[event.failure_code],
                    failed_tool=event.tool_name or "runtime",
                    failed_action_id=event.action_id or event.event_id,
                    opened_sequence=event.sequence,
                    target=_safe_target(event),
                )
            )
            continue
        if event.status != "succeeded":
            continue
        for index, incident in enumerate(incidents):
            if incident.status == "resolved" or not _is_recovery(incident, event):
                continue
            intervening = sum(
                candidate.event_type == "tool.call_started"
                and incident.opened_sequence < candidate.sequence < event.sequence
                for candidate in events
            )
            incidents[index] = incident.model_copy(
                update={
                    "status": "resolved",
                    "recovery": IncidentRecovery(
                        tool_name=event.tool_name or "runtime",
                        action_id=event.action_id or event.event_id,
                        resolved_sequence=event.sequence,
                        intervening_model_calls=intervening,
                    ),
                }
            )
    return tuple(incidents)


def _latest_plan(events: Sequence[PresentationEvent]) -> ObservablePlan:
    return next(
        (
            event.plan
            for event in reversed(events)
            if event.status == "succeeded" and event.plan is not None
        ),
        ObservablePlan(),
    )


def _latest_public_output(
    state: RunState, events: Sequence[PresentationEvent]
) -> PublicModelOutput | None:
    reply_event = next(
        (event for event in reversed(events) if event.public_model_output is not None),
        None,
    )
    if reply_event is None:
        return None
    output = reply_event.public_model_output
    assert output is not None
    runtime_complete = any(
        event.sequence > reply_event.sequence
        and event.event_type in {"runtime.turn_completed", "runtime.turn_failed"}
        for event in events
    )
    if not runtime_complete:
        return output.model_copy(update={"outcome": "intermediate"})
    outcome = "terminal" if state.terminal_outcome is not None else "premature"
    return output.model_copy(update={"outcome": outcome})


def _critical_history(
    events: Sequence[PresentationEvent],
    incidents: Sequence[PresentationIncident],
) -> tuple[CriticalHistoryEntry, ...]:
    entries: list[CriticalHistoryEntry] = []
    incident_by_sequence = {incident.opened_sequence: incident for incident in incidents}
    for event in events:
        incident = incident_by_sequence.get(event.sequence)
        if incident is not None:
            entries.append(
                CriticalHistoryEntry(
                    history_id=f"history-incident-{event.sequence:05d}",
                    sequence=event.sequence,
                    kind="incident",
                    label_zh=incident.label_zh,
                    status=incident.status,
                    action_id=event.action_id,
                    evidence_refs=tuple(event.evidence_refs),
                )
            )
        if event.status != "succeeded":
            continue
        kind: str | None = None
        label = event.tool_label_zh or "关键执行结果"
        if event.tool_name == "sop_decide":
            kind = "terminal" if event.result.get("terminal_outcome") else "gate"
        elif event.tool_name == "browser_wait":
            kind = "job"
        elif event.tool_name == "terminal_execute":
            kind = "grep"
        elif event.result.get("caused_by_current_change") is True:
            kind = "causal_alarm"
        if kind is not None:
            entries.append(
                CriticalHistoryEntry(
                    history_id=f"history-{kind}-{event.sequence:05d}",
                    sequence=event.sequence,
                    kind=kind,  # type: ignore[arg-type]
                    label_zh=label,
                    status=event.status,
                    action_id=event.action_id,
                    evidence_refs=tuple(event.evidence_refs),
                )
            )
    for incident in incidents:
        if incident.recovery is None:
            continue
        entries.append(
            CriticalHistoryEntry(
                history_id=f"history-recovery-{incident.recovery.resolved_sequence:05d}",
                sequence=incident.recovery.resolved_sequence,
                kind="recovery",
                label_zh=f"已恢复：{incident.label_zh}",
                status="resolved",
                action_id=incident.recovery.action_id,
            )
        )
    return tuple(sorted(entries, key=lambda entry: (entry.sequence, entry.history_id)))


def derive_decision_summary(
    state: RunState,
    events: Sequence[PresentationEvent],
    incidents: Sequence[PresentationIncident],
    plan: ObservablePlan,
) -> DecisionSummary:
    if state.terminal_outcome is not None:
        return DecisionSummary(
            state="terminal",
            fact=_summary_term(
                "terminal_outcome", FACT_LABELS_ZH, {"outcome": state.terminal_outcome}
            ),
            judgment=_summary_term("run_terminal", JUDGMENT_LABELS_ZH),
            next_action=_summary_term("none", NEXT_ACTION_LABELS_ZH),
        )
    open_incident = next(
        (incident for incident in reversed(incidents) if incident.status == "open"), None
    )
    if open_incident is not None:
        return DecisionSummary(
            state="blocked",
            fact=_summary_term(
                "incident_open",
                FACT_LABELS_ZH,
                {"failure_code": open_incident.failure_code},
            ),
            judgment=_summary_term("recovery_required", JUDGMENT_LABELS_ZH),
            next_action=_summary_term("recover_incident", NEXT_ACTION_LABELS_ZH),
        )
    if not plan.items:
        return DecisionSummary(
            state="planning",
            fact=_summary_term("ticket_opened", FACT_LABELS_ZH),
            judgment=_summary_term("plan_required", JUDGMENT_LABELS_ZH),
            next_action=_summary_term("create_plan", NEXT_ACTION_LABELS_ZH),
        )
    latest = events[-1] if events else None
    if latest is not None and latest.status == "running":
        return DecisionSummary(
            state="waiting" if latest.tool_kind == "wait" else "observing",
            fact=_summary_term("action_running", FACT_LABELS_ZH),
            judgment=_summary_term("observe_result", JUDGMENT_LABELS_ZH),
            next_action=_summary_term("await_result", NEXT_ACTION_LABELS_ZH),
        )
    return DecisionSummary(
        state="observing",
        fact=_summary_term(
            "action_succeeded" if latest and latest.status == "succeeded" else "plan_persisted",
            FACT_LABELS_ZH,
        ),
        judgment=_summary_term("continue_sop", JUDGMENT_LABELS_ZH),
        next_action=_summary_term("continue_locked_sop", NEXT_ACTION_LABELS_ZH),
    )


def reduce_observable_state(
    state: RunState,
    events: Sequence[PresentationEvent],
) -> ObservablePresentationState:
    """Rebuild all observable state without I/O or mutation."""

    ordered = tuple(sorted(events, key=lambda event: event.sequence))
    plan = _latest_plan(ordered)
    current_action = next(
        (event for event in reversed(ordered) if event.event_type == "tool.call_started"),
        None,
    )
    last_result = next(
        (
            event
            for event in reversed(ordered)
            if event.event_type.startswith("tool.") and event.status in _TERMINAL_STATUSES
        ),
        None,
    )
    incidents = reduce_incidents(ordered)
    return ObservablePresentationState(
        plan=plan,
        current_action=current_action,
        last_result=last_result,
        public_model_output=_latest_public_output(state, ordered),
        decision_summary=derive_decision_summary(state, ordered, incidents, plan),
        incidents=incidents,
        critical_history=_critical_history(ordered, incidents),
    )
