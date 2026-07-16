from __future__ import annotations

from case02_openenv.automation import AutomationEngine
from case02_openenv.episode_store import EpisodeStore
from case02_openenv.models import DecisionRequest

from tests.case02_openenv.test_automation import wait_job
from tests.case02_openenv.test_episode_store import complete_prechecks, record_progress, reserve


def test_anomaly_arms_only_after_current_add_grep(store: EpisodeStore) -> None:
    run_id = "causal-run"
    store.create(run_id, "post_change_anomaly")
    complete_prechecks(store, run_id)
    record_progress(store, run_id, "PRE_PROGRESS")
    state = store.state(run_id)
    action, version = reserve(store, run_id, "browser_click", "submit")
    job = AutomationEngine(store, settle_delay_s=0.01).submit(
        run_id,
        action_id=action,
        page_state_version=version,
        script="svc_cfg_cli_runner",
        operation="add",
        parameters=state.variables,
    )
    wait_job(store, run_id, job.job_id)
    assert store.state(run_id).causal_anomaly_armed is False
    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        action_id="wait-add",
        arguments={"tool_name": "browser_wait", "job_id": job.job_id},
        node_id="ADD_WAIT",
    )

    action, version = reserve(store, run_id, "terminal_execute", "grep")
    stdout = " ".join(store.state(run_id).variables.values())
    store.terminal_completed(
        run_id,
        action_id=action,
        page_state_version=version,
        command="locked grep",
        exit_code=0,
        stdout=stdout,
        evidence_id="grep-current",
    )
    causal = store.state(run_id)
    assert causal.causal_anomaly_armed is True
    assert causal.causal_add_job_id == job.job_id
    assert causal.causal_grep_evidence_id == "grep-current"

    action, version = reserve(store, run_id, "sop_decide", "implement")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="change_implement",
            decision="proceed",
        ),
    )
    record_progress(store, run_id, "IMPLEMENT_PROGRESS")
    current = store.state(run_id)
    action, version = reserve(store, run_id, "browser_click", "alarm")
    _event, observation = store.monitor_query(
        run_id,
        action_id=action,
        page_state_version=version,
        query="alarm",
        region=current.target["region"],
        cluster=current.target["cluster"],
    )
    assert observation["status"] == "active"
    assert observation["caused_by_current_change"] is True
