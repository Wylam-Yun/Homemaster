from __future__ import annotations

from datetime import UTC, datetime

import pytest
from case02_openenv.models import EpisodePhase, RunState
from case02_openenv.observable_presentation import reduce_observable_state
from case02_openenv.presentation_models import (
    ObservablePlan,
    ObservablePlanItem,
    PresentationEvent,
)
from pydantic import ValidationError


def run_state(run_id: str = "observable-run") -> RunState:
    return RunState(
        run_id=run_id,
        variables={
            "TenantId": "tenant-a",
            "ItemCode": "item-a",
            "ItemName": "Name A",
            "ItemDesc": "Description A",
        },
        target={"region": "cn-a", "cluster": "cluster-a"},
        upstream_ready=True,
    )


def presentation_event(
    sequence: int,
    event_type: str,
    *,
    status: str,
    tool_name: str | None = None,
    action_id: str | None = None,
    tool_call_id: str | None = None,
    failure_code: str | None = None,
    arguments: dict | None = None,
    result: dict | None = None,
    plan: ObservablePlan | None = None,
    public_model_output: dict | None = None,
) -> PresentationEvent:
    return PresentationEvent(
        event_id=f"presentation-{sequence:05d}",
        sequence=sequence,
        run_id="observable-run",
        event_type=event_type,
        timestamp=datetime(2026, 7, 19, 8, 0, sequence, tzinfo=UTC),
        tool_call_id=tool_call_id,
        action_id=action_id,
        stage="check_before_change",
        tool_name=tool_name,
        tool_label_zh=(
            "等待自动化任务"
            if tool_name == "browser_wait"
            else ("创建执行计划" if tool_name else None)
        ),
        tool_kind=(
            "wait" if tool_name == "browser_wait" else ("orchestration" if tool_name else None)
        ),
        status=status,
        arguments=arguments or {},
        result=result or {},
        failure_code=failure_code,
        plan=plan,
        public_model_output=public_model_output,
    )


def test_v2_plan_accepts_real_blocked_and_uncertain_statuses() -> None:
    plan = ObservablePlan(
        items=(
            ObservablePlanItem(id="blocked", title="Blocked step", status="blocked"),
            ObservablePlanItem(id="uncertain", title="Uncertain step", status="uncertain"),
        ),
        current_id="blocked",
        next_focus="Collect evidence",
    )
    assert [item.status for item in plan.items] == ["blocked", "uncertain"]

    with pytest.raises(ValidationError):
        ObservablePlanItem(id="bad", title="Bad status", status="failed")


def test_reducer_rebuilds_plan_current_action_and_result() -> None:
    plan = ObservablePlan(
        items=(ObservablePlanItem(id="precheck", title="Complete checks", status="in_progress"),),
        current_id="precheck",
        next_focus="Query alarm",
    )
    events = [
        presentation_event(
            1,
            "tool.call_started",
            status="running",
            tool_name="task_planner",
            action_id="action-plan",
            tool_call_id="call-plan",
        ),
        presentation_event(
            2,
            "tool.call_completed",
            status="succeeded",
            tool_name="task_planner",
            action_id="action-plan",
            tool_call_id="call-plan",
            plan=plan,
        ),
    ]

    observable = reduce_observable_state(run_state(), events)

    assert observable.plan.current_id == "precheck"
    assert observable.plan.items[0].status == "in_progress"
    assert observable.current_action.tool_name == "task_planner"
    assert observable.last_result.status == "succeeded"
    assert observable.decision_summary.state == "observing"


def test_wait_incident_resolves_only_for_same_job() -> None:
    events = [
        presentation_event(
            1,
            "tool.call_started",
            status="running",
            tool_name="browser_wait",
            action_id="action-failed",
            tool_call_id="call-failed",
            arguments={"job_id": "job-add-abcdef1234"},
        ),
        presentation_event(
            2,
            "tool.call_failed",
            status="rejected",
            tool_name="browser_wait",
            action_id="action-failed",
            tool_call_id="call-failed",
            failure_code="wait_required",
            arguments={"job_id": "job-add-abcdef1234"},
        ),
        presentation_event(
            3,
            "tool.call_completed",
            status="succeeded",
            tool_name="browser_wait",
            action_id="action-wrong",
            tool_call_id="call-wrong",
            result={"job_id": "job-add-other9999", "status": "succeeded"},
        ),
    ]
    unresolved = reduce_observable_state(run_state(), events)
    assert unresolved.incidents[0].status == "open"

    events.append(
        presentation_event(
            4,
            "tool.call_completed",
            status="succeeded",
            tool_name="browser_wait",
            action_id="action-recovered",
            tool_call_id="call-recovered",
            result={"job_id": "job-add-abcdef1234", "status": "succeeded"},
        )
    )
    recovered = reduce_observable_state(run_state(), events)
    assert recovered.incidents[0].status == "resolved"
    assert recovered.incidents[0].recovery.action_id == "action-recovered"


@pytest.mark.parametrize(
    ("failure_code", "failed_tool", "recovery_tool"),
    [
        ("plan_required", "browser_navigate", "task_planner"),
        ("missing_precheck_evidence", "sop_decide", "sop_decide"),
        ("progress_required", "browser_navigate", "task_progress_check"),
        ("wait_required", "browser_wait", "browser_wait"),
        ("postchecks_required", "task_progress_check", "browser_click"),
        ("rollback_verification_required", "task_progress_check", "terminal_execute"),
        ("rollback_decision_required", "browser_click", "sop_decide"),
        ("missing_anomaly_evidence", "sop_decide", "sop_decide"),
        ("missing_implementation_evidence", "sop_decide", "sop_decide"),
        ("missing_postcheck_evidence", "sop_decide", "sop_decide"),
        ("missing_rollback_evidence", "sop_decide", "sop_decide"),
        ("external_state_mismatch", "sop_decide", "browser_observe"),
        ("parameter_mismatch", "browser_fill", "browser_fill"),
        ("command_not_allowed", "terminal_execute", "terminal_execute"),
        ("invalid_decision_for_stage", "sop_decide", "sop_decide"),
        ("stale_state_version", "browser_fill", "browser_fill"),
        ("action_replay", "browser_fill", "browser_fill"),
        ("terminal_outcome", "browser_click", None),
    ],
)
def test_every_safe_failure_code_has_an_exact_incident_recovery_rule(
    failure_code: str,
    failed_tool: str,
    recovery_tool: str | None,
) -> None:
    target = (
        {"job_id": "job-add-abcdef1234"}
        if failure_code == "wait_required"
        else (
            {"bid": "automation-tenant-id", "value": "tenant-a"}
            if failed_tool == "browser_fill"
            else {}
        )
    )
    failed = presentation_event(
        1,
        "tool.call_failed",
        status="rejected",
        tool_name=failed_tool,
        action_id=f"failed-{failure_code}",
        tool_call_id=f"call-{failure_code}",
        failure_code=failure_code,
        arguments=target,
    )
    events = [failed]
    if recovery_tool is not None:
        recovery_result = dict(target)
        recovery_plan = None
        if failure_code == "plan_required":
            recovery_plan = ObservablePlan(
                items=(ObservablePlanItem(id="recover", title="Recover", status="in_progress"),)
            )
        events.append(
            presentation_event(
                2,
                "tool.call_completed",
                status="succeeded",
                tool_name=recovery_tool,
                action_id=f"recovery-{failure_code}",
                tool_call_id=f"recovery-call-{failure_code}",
                arguments=target,
                result=recovery_result,
                plan=recovery_plan,
            )
        )

    observable = reduce_observable_state(run_state(), events)
    incident = observable.incidents[0]

    assert incident.failure_code == failure_code
    if recovery_tool is None:
        assert incident.status == "open"
        assert incident.recovery is None
    else:
        assert incident.status == "resolved"
        assert incident.recovery is not None
        assert incident.recovery.tool_name == recovery_tool


def test_public_reply_lifecycle_is_intermediate_terminal_or_premature() -> None:
    reply = presentation_event(
        1,
        "model.public_reply",
        status="succeeded",
        public_model_output={
            "kind": "assistant_reply",
            "text": "I will inspect the current state.",
            "outcome": "intermediate",
        },
    )
    assert reduce_observable_state(run_state(), [reply]).public_model_output.outcome == (
        "intermediate"
    )

    complete = presentation_event(2, "runtime.turn_completed", status="succeeded")
    assert reduce_observable_state(run_state(), [reply, complete]).public_model_output.outcome == (
        "premature"
    )

    terminal_state = run_state()
    terminal_state.phase = EpisodePhase.COMPLETED
    terminal_state.terminal_outcome = "complete"
    assert (
        reduce_observable_state(terminal_state, [reply, complete]).public_model_output.outcome
        == "terminal"
    )


def test_v1_presentation_event_is_rejected() -> None:
    payload = presentation_event(
        1,
        "runtime.turn_completed",
        status="succeeded",
    ).model_dump(mode="json")
    payload["schema_version"] = 1
    with pytest.raises(ValidationError):
        PresentationEvent.model_validate(payload)
