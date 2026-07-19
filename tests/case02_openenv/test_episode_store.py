from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from case02_openenv.episode_store import EpisodeError, EpisodeStore
from case02_openenv.models import (
    ActionReservation,
    AutomationJob,
    DecisionRequest,
    EpisodePhase,
    JobStatus,
)


def reserve(store: EpisodeStore, run_id: str, tool: str, action_id: str) -> tuple[str, int]:
    version = store.state(run_id).state_version
    store.reserve_action(
        run_id,
        ActionReservation(action_id=action_id, tool_name=tool, page_state_version=version),
    )
    return action_id, version


def test_runs_have_isolated_state_files_jobs_and_ledgers(store: EpisodeStore) -> None:
    first = store.create("normal-a", "normal")
    second = store.create("anomaly-b", "post_change_anomaly")
    assert first.run_id != second.run_id
    assert store.episode("normal-a").run_root != store.episode("anomaly-b").run_root
    assert store.episode("normal-a").config_file != store.episode("anomaly-b").config_file

    action_id, version = reserve(store, "normal-a", "browser_click", "a-1")
    event, _ = store.check_config(
        "normal-a", action_id=action_id, page_state_version=version, check="extension_config"
    )
    assert event.run_id == "normal-a"
    assert store.audit("anomaly-b") == []
    assert "a-1" not in store.state("anomaly-b").action_ledger


def test_locked_bundle_drift_is_rejected_after_run_creation(tmp_path: Path) -> None:
    data_root = tmp_path / "case_02"
    shutil.copytree(Path("data/coworker_demo/case_02"), data_root)
    isolated = EpisodeStore(data_root=data_root, artifact_root=tmp_path / "runs")
    locked_hashes = isolated.source_hashes("normal")
    isolated.create("locked-run", "normal", locked_hashes=locked_hashes)
    ticket = data_root / "test_set/item_change_ticket.json"
    ticket.write_text(ticket.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(EpisodeError, match="hash mismatch"):
        isolated.verify_locked_sources("locked-run")


def test_reset_clears_current_evidence_and_restores_file(store: EpisodeStore) -> None:
    store.create("reset-run", "normal")
    action_id, version = reserve(store, "reset-run", "browser_click", "a-1")
    store.check_config(
        "reset-run", action_id=action_id, page_state_version=version, check="extension_config"
    )
    assert store.audit("reset-run")
    state = store.reset("reset-run")
    assert state.phase == EpisodePhase.CREATED
    assert store.audit("reset-run") == []
    assert json.loads(store.episode("reset-run").config_file.read_text()) == {}


def test_action_replay_and_stale_version_are_rejected(store: EpisodeStore) -> None:
    store.create("ledger-run", "normal")
    _, version = reserve(store, "ledger-run", "browser_click", "once")
    store.check_config(
        "ledger-run", action_id="once", page_state_version=version, check="extension_config"
    )
    with pytest.raises(EpisodeError, match="consumed"):
        store.check_config(
            "ledger-run", action_id="once", page_state_version=version, check="upstream_ready"
        )
    with pytest.raises(EpisodeError, match="stale"):
        store.reserve_action(
            "ledger-run",
            ActionReservation(
                action_id="stale", tool_name="browser_click", page_state_version=version
            ),
        )


def test_terminal_outcome_rejects_pre_reserved_and_runtime_actions(
    store: EpisodeStore,
) -> None:
    run_id = "terminal-ledger-run"
    store.create(run_id, "normal")
    reserve(store, run_id, "browser_click", "reserved-before-terminal")
    action, version = reserve(store, run_id, "sop_decide", "stop")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="check_before_change",
            decision="block",
        ),
    )
    current_version = store.state(run_id).state_version
    with pytest.raises(EpisodeError, match="terminal outcome"):
        store.consume_action(
            run_id,
            "reserved-before-terminal",
            current_version,
            "browser_click",
        )
    with pytest.raises(EpisodeError, match="terminal outcome"):
        store.validate_runtime_node(run_id, "PLAN_CREATED")
    with pytest.raises(EpisodeError, match="terminal outcome"):
        store.require_terminal_wait(run_id)


def test_decision_rejects_unknown_and_cross_run_evidence(store: EpisodeStore) -> None:
    store.create("evidence-a", "normal")
    store.create("evidence-b", "normal")
    foreign = store.record(
        "evidence-a",
        source="backend",
        kind="receipt",
        status="succeeded",
        action_id="foreign-action",
        node_id="FOREIGN",
    )
    action, version = reserve(store, "evidence-b", "sop_decide", "bad-evidence")
    with pytest.raises(EpisodeError, match="not persisted for this run"):
        store.decide(
            "evidence-b",
            DecisionRequest(
                action_id=action,
                page_state_version=version,
                stage="check_before_change",
                decision="block",
                evidence_refs=[foreign.event_id, "invented-evidence"],
            ),
        )
    assert store.state("evidence-b").action_ledger[action].consumed is False


def complete_prechecks(store: EpisodeStore, run_id: str) -> None:
    counter = 0
    for check in ("extension_config", "upstream_ready"):
        counter += 1
        action, version = reserve(store, run_id, "browser_click", f"config-{counter}")
        store.check_config(run_id, action_id=action, page_state_version=version, check=check)
    state = store.state(run_id)
    for query in ("alarm", "probe", "capacity", "runtime_metrics", "traffic"):
        counter += 1
        action, version = reserve(store, run_id, "browser_click", f"monitor-{counter}")
        store.monitor_query(
            run_id,
            action_id=action,
            page_state_version=version,
            query=query,
            region=state.target["region"],
            cluster=state.target["cluster"],
        )
    action, version = reserve(store, run_id, "sop_decide", "decision-pre")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="check_before_change",
            decision="proceed",
        ),
    )


def record_progress(store: EpisodeStore, run_id: str, node_id: str) -> None:
    store.record(
        run_id,
        source="runtime",
        kind="tool_result",
        status="succeeded",
        action_id=f"progress-{node_id.lower()}",
        arguments={"tool_name": "task_progress_check"},
        node_id=node_id,
        mutate_version=False,
    )


def test_precheck_transition_requires_all_external_receipts(store: EpisodeStore) -> None:
    store.create("pre-run", "normal")
    action, version = reserve(store, "pre-run", "sop_decide", "too-early")
    with pytest.raises(EpisodeError, match="all prechecks"):
        store.decide(
            "pre-run",
            DecisionRequest(
                action_id=action,
                page_state_version=version,
                stage="check_before_change",
                decision="proceed",
            ),
        )
    store.reset("pre-run")
    complete_prechecks(store, "pre-run")
    assert store.state("pre-run").phase == EpisodePhase.READY_TO_CHANGE


def test_browser_action_requires_real_progress_event_after_pre_decision(
    store: EpisodeStore,
) -> None:
    run_id = "progress-gate"
    store.create(run_id, "normal")
    complete_prechecks(store, run_id)
    state = store.state(run_id)

    with pytest.raises(EpisodeError, match="task_progress_check.*PRE_PROGRESS"):
        store.reserve_action(
            run_id,
            ActionReservation(
                action_id="blocked-browser",
                tool_name="browser_navigate",
                page_state_version=state.state_version,
            ),
        )

    store.record(
        run_id,
        source="runtime",
        kind="tool_result",
        status="succeeded",
        arguments={"tool_name": "skill_view"},
        node_id="PRE_PROGRESS",
        mutate_version=False,
    )
    with pytest.raises(EpisodeError, match="task_progress_check.*PRE_PROGRESS"):
        store.reserve_action(
            run_id,
            ActionReservation(
                action_id="spoofed-progress",
                tool_name="browser_observe",
                page_state_version=state.state_version,
            ),
        )

    record_progress(store, run_id, "PRE_PROGRESS")
    record = store.reserve_action(
        run_id,
        ActionReservation(
            action_id="allowed-browser",
            tool_name="browser_navigate",
            page_state_version=state.state_version,
        ),
    )
    assert record.action_id == "allowed-browser"


def test_operational_browser_action_requires_real_plan_after_ticket_read(
    store: EpisodeStore,
) -> None:
    run_id = "plan-gate"
    store.create(run_id, "normal")
    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        arguments={"tool_name": "browser_navigate", "route": "ticket"},
        node_id="TICKET_READ",
        mutate_version=False,
    )
    state = store.state(run_id)
    observed = store.reserve_action(
        run_id,
        ActionReservation(
            action_id="read-ticket",
            tool_name="browser_observe",
            page_state_version=state.state_version,
        ),
    )
    assert observed.action_id == "read-ticket"
    with pytest.raises(EpisodeError, match="task_planner.*PLAN_CREATED"):
        reserve(store, run_id, "browser_navigate", "monitor-too-early")

    store.record(
        run_id,
        source="runtime",
        kind="tool_result",
        status="succeeded",
        arguments={"tool_name": "skill_view"},
        node_id="PLAN_CREATED",
        mutate_version=False,
    )
    with pytest.raises(EpisodeError, match="task_planner.*PLAN_CREATED"):
        reserve(store, run_id, "browser_click", "spoofed-plan")

    store.record(
        run_id,
        source="runtime",
        kind="tool_result",
        status="succeeded",
        arguments={"tool_name": "task_planner"},
        node_id="PLAN_CREATED",
        mutate_version=False,
    )
    allowed, _version = reserve(store, run_id, "browser_navigate", "after-plan")
    assert allowed == "after-plan"


def test_plan_requires_ticket_read_first(store: EpisodeStore) -> None:
    run_id = "ticket-before-plan"
    store.create(run_id, "normal")

    with pytest.raises(EpisodeError, match="open and read the ticket"):
        store.validate_runtime_node(run_id, "PLAN_CREATED")

    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        arguments={"tool_name": "browser_navigate", "route": "ticket"},
        node_id="TICKET_READ",
        mutate_version=False,
    )
    store.validate_runtime_node(run_id, "PLAN_CREATED")


@pytest.mark.parametrize(
    ("phase", "business_verified", "required_node"),
    [
        (EpisodePhase.CHANGE_APPLIED, False, "IMPLEMENT_PROGRESS"),
        (EpisodePhase.CHANGE_APPLIED, True, "IMPLEMENT_PROGRESS"),
    ],
)
def test_browser_progress_gate_tracks_current_phase(
    store: EpisodeStore,
    phase: EpisodePhase,
    business_verified: bool,
    required_node: str,
) -> None:
    run_id = f"gate-{required_node.lower()}"
    store.create(run_id, "normal")
    store.episode(run_id).state.phase = phase
    store.episode(run_id).state.business_verified = business_verified
    state = store.state(run_id)
    with pytest.raises(EpisodeError, match=required_node):
        store.reserve_action(
            run_id,
            ActionReservation(
                action_id="blocked",
                tool_name="browser_observe",
                page_state_version=state.state_version,
            ),
        )
    record_progress(store, run_id, required_node)
    store.reserve_action(
        run_id,
        ActionReservation(
            action_id="allowed",
            tool_name="browser_observe",
            page_state_version=state.state_version,
        ),
    )


def test_post_change_actions_require_implementation_decision(store: EpisodeStore) -> None:
    run_id = "implementation-decision-gate"
    store.create(run_id, "normal")
    state = store.episode(run_id).state
    state.phase = EpisodePhase.CHANGE_SUBMITTED
    state.add_grep_evidence_id = "terminal-add"
    with pytest.raises(EpisodeError, match="change_implement.*proceed"):
        reserve(store, run_id, "browser_navigate", "postcheck-too-early")

    action, version = reserve(store, run_id, "sop_decide", "complete-too-early")
    with pytest.raises(EpisodeError, match="change_implement.*proceed"):
        store.decide(
            run_id,
            DecisionRequest(
                action_id=action,
                page_state_version=version,
                stage="change_verified",
                decision="complete",
            ),
        )


def test_complete_requires_normal_progress_before_terminal_outcome(
    store: EpisodeStore,
) -> None:
    run_id = "normal-terminal-progress"
    store.create(run_id, "normal")
    episode = store.episode(run_id)
    state = episode.state
    state.phase = EpisodePhase.CHANGE_APPLIED
    state.postchecks = {
        "alarm": "clear",
        "probe": "normal",
        "capacity": "sufficient",
        "runtime_metrics": "normal",
        "traffic": "normal",
    }
    state.business_verified = True
    key = f"{state.variables['TenantId']}:{state.variables['ItemCode']}"
    episode.config_file.write_text(json.dumps({key: state.variables}), encoding="utf-8")

    action, version = reserve(store, run_id, "sop_decide", "complete-too-early")
    with pytest.raises(EpisodeError, match="NORMAL_PROGRESS"):
        store.decide(
            run_id,
            DecisionRequest(
                action_id=action,
                page_state_version=version,
                stage="change_verified",
                decision="complete",
            ),
        )
    assert store.state(run_id).terminal_outcome is None

    record_progress(store, run_id, "NORMAL_PROGRESS")
    action, version = reserve(store, run_id, "sop_decide", "complete-after-progress")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="change_verified",
            decision="complete",
        ),
    )
    assert store.state(run_id).terminal_outcome == "complete"


def test_rolled_back_requires_rollback_progress_before_terminal_outcome(
    store: EpisodeStore,
) -> None:
    run_id = "rollback-terminal-progress"
    store.create(run_id, "post_change_anomaly")
    state = store.episode(run_id).state
    state.phase = EpisodePhase.ROLLBACK_SUBMITTED
    state.rollback_grep_evidence_id = "terminal-rollback"
    state.jobs["remove-job"] = AutomationJob(
        job_id="remove-job",
        run_id=run_id,
        action_id="remove-action",
        operation="remove",
        status=JobStatus.SUCCEEDED,
        business_return_code=0,
        submitted_payload={
            "TenantId": state.variables["TenantId"],
            "ItemCode": state.variables["ItemCode"],
        },
    )

    action, version = reserve(store, run_id, "sop_decide", "rollback-too-early")
    with pytest.raises(EpisodeError, match="ROLLBACK_PROGRESS"):
        store.decide(
            run_id,
            DecisionRequest(
                action_id=action,
                page_state_version=version,
                stage="change_rollback",
                decision="rolled_back",
            ),
        )
    assert store.state(run_id).terminal_outcome is None

    record_progress(store, run_id, "ROLLBACK_PROGRESS")
    action, version = reserve(store, run_id, "sop_decide", "rollback-after-progress")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="change_rollback",
            decision="rolled_back",
        ),
    )
    assert store.state(run_id).terminal_outcome == "rolled_back"


def test_normal_progress_requires_wait_for_exact_business_job(store: EpisodeStore) -> None:
    run_id = "normal-progress-wait"
    store.create(run_id, "normal")
    state = store.episode(run_id).state
    state.jobs["business-job"] = AutomationJob(
        job_id="business-job",
        run_id=run_id,
        action_id="business-action",
        operation="business_verify",
        status=JobStatus.SUCCEEDED,
        business_return_code=0,
        submitted_payload={},
    )
    with pytest.raises(EpisodeError, match="all five postchecks.*probe"):
        store.validate_runtime_node(run_id, "NORMAL_PROGRESS")
    state.postchecks = {
        "alarm": "clear",
        "probe": "normal",
        "capacity": "sufficient",
        "runtime_metrics": "normal",
        "traffic": "normal",
    }
    with pytest.raises(EpisodeError, match="browser_wait.*business-job"):
        store.validate_runtime_node(run_id, "NORMAL_PROGRESS")
    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        arguments={"tool_name": "browser_wait", "job_id": "other-job"},
        node_id="BUSINESS_WAIT",
        mutate_version=False,
    )
    with pytest.raises(EpisodeError, match="business-job"):
        store.validate_runtime_node(run_id, "NORMAL_PROGRESS")
    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        arguments={"tool_name": "browser_wait", "job_id": "business-job"},
        node_id="BUSINESS_WAIT",
        mutate_version=False,
    )
    store.validate_runtime_node(run_id, "NORMAL_PROGRESS")


def test_rollback_progress_requires_terminal_absence_proof(store: EpisodeStore) -> None:
    run_id = "rollback-progress-terminal"
    store.create(run_id, "post_change_anomaly")
    with pytest.raises(EpisodeError, match="rollback terminal grep"):
        store.validate_runtime_node(run_id, "ROLLBACK_PROGRESS")
    store.record(
        run_id,
        source="terminal",
        kind="command_completed",
        status="succeeded",
        node_id="ROLLBACK_GREP",
        mutate_version=False,
    )
    store.validate_runtime_node(run_id, "ROLLBACK_PROGRESS")


def test_invalid_stage_decision_does_not_create_terminal_outcome(
    store: EpisodeStore,
) -> None:
    store.create("invalid-decision", "normal")
    store.episode("invalid-decision").state.phase = EpisodePhase.CHANGE_APPLIED
    action, version = reserve(store, "invalid-decision", "sop_decide", "invalid-proceed")
    with pytest.raises(EpisodeError, match="not valid"):
        store.decide(
            "invalid-decision",
            DecisionRequest(
                action_id=action,
                page_state_version=version,
                stage="change_verified",
                decision="proceed",
            ),
        )
    assert store.state("invalid-decision").terminal_outcome is None
