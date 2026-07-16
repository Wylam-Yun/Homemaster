from __future__ import annotations

from case02_openenv.automation import AutomationEngine
from case02_openenv.episode_store import EpisodeStore
from case02_openenv.evaluation.scoring import finalize_run
from case02_openenv.evaluation.trajectory import normalize_events
from case02_openenv.models import DecisionRequest

from tests.case02_openenv.test_automation import wait_job
from tests.case02_openenv.test_episode_store import complete_prechecks, reserve


def grounded(store: EpisodeStore, run_id: str, node_id: str, action_id: str) -> None:
    store.record(
        run_id,
        source="runtime",
        kind="tool_result",
        status="succeeded",
        action_id=action_id,
        arguments={"tool_name": "task_progress_check"},
        node_id=node_id,
    )


def grounded_wait(
    store: EpisodeStore, run_id: str, node_id: str, action_id: str, job_id: str
) -> None:
    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        action_id=action_id,
        arguments={"tool_name": "browser_wait", "job_id": job_id},
        node_id=node_id,
    )


def test_normalizer_rejects_decision_with_unpersisted_evidence(store: EpisodeStore) -> None:
    run_id = "normalize-evidence"
    store.create(run_id, "normal")
    forged = store.record(
        run_id,
        source="decision",
        kind="sop_decision",
        status="succeeded",
        action_id="decision-action",
        evidence_refs=["invented-evidence"],
        node_id="TERMINAL_DECISION",
    )
    effective, rejected = normalize_events(store.audit(run_id))
    assert effective == []
    assert rejected == [{"event_id": forged.event_id, "reason": "unknown_evidence_ref"}]


def test_full_normal_run_freezes_24_nodes_and_14_results(store: EpisodeStore) -> None:
    run_id = "score-normal"
    store.create(run_id, "normal")
    grounded(store, run_id, "TICKET_READ", "navigate")
    grounded(store, run_id, "PLAN_CREATED", "planner")
    complete_prechecks(store, run_id)
    grounded(store, run_id, "PRE_PROGRESS", "progress-pre")

    engine = AutomationEngine(store, settle_delay_s=0.01)
    current = store.state(run_id)
    action, version = reserve(store, run_id, "browser_click", "add")
    add = engine.submit(
        run_id,
        action_id=action,
        page_state_version=version,
        script="svc_cfg_cli_runner",
        operation="add",
        parameters=current.variables,
    )
    wait_job(store, run_id, add.job_id)
    grounded_wait(store, run_id, "ADD_WAIT", "wait-add", add.job_id)
    action, version = reserve(store, run_id, "terminal_execute", "grep-add")
    store.terminal_completed(
        run_id,
        action_id=action,
        page_state_version=version,
        command="locked grep",
        exit_code=0,
        stdout=" ".join(current.variables.values()),
        evidence_id="terminal-add",
    )
    action, version = reserve(store, run_id, "sop_decide", "implementation")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="change_implement",
            decision="proceed",
        ),
    )
    grounded(store, run_id, "IMPLEMENT_PROGRESS", "progress-implementation")

    current = store.state(run_id)
    for index, query in enumerate(("alarm", "probe", "capacity", "runtime_metrics", "traffic")):
        action, version = reserve(store, run_id, "browser_click", f"post-{index}")
        store.monitor_query(
            run_id,
            action_id=action,
            page_state_version=version,
            query=query,
            region=current.target["region"],
            cluster=current.target["cluster"],
        )
    current = store.state(run_id)
    action, version = reserve(store, run_id, "browser_click", "business")
    business = engine.submit(
        run_id,
        action_id=action,
        page_state_version=version,
        script="svc_usage_record_fetcher",
        operation="business_verify",
        parameters={
            "resource_bucket": current.target["resource_bucket"],
            "business_timestamp": current.target["business_timestamp"],
            "factor": "0",
        },
    )
    wait_job(store, run_id, business.job_id)
    grounded_wait(store, run_id, "BUSINESS_WAIT", "wait-business", business.job_id)
    grounded(store, run_id, "NORMAL_PROGRESS", "progress-normal")
    action, version = reserve(store, run_id, "sop_decide", "complete")
    store.decide(
        run_id,
        DecisionRequest(
            action_id=action,
            page_state_version=version,
            stage="change_verified",
            decision="complete",
        ),
    )

    summary = finalize_run(store, run_id)["summary"]
    assert summary["matched_nodes"] == summary["required_nodes"] == 24
    assert summary["passed_checkpoints"] == summary["required_checkpoints"] == 14
    assert summary["trajectory_score"] == summary["result_score"] == 100.0
    assert summary["formal_success"] is None
    assert summary["video_verification"] == "pending"

    episode = store.episode(run_id)
    for relative in ("video/demo.mp4", "video/poster.png", "video/video_manifest.json"):
        path = episode.run_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"verified-test-artifact")
        episode.registry.register(relative, producer="test")
    verified = finalize_run(store, run_id, video_verified=True)["summary"]
    assert verified["artifact_failure"] is False
    assert verified["artifact_failures"] == []
    assert verified["formal_success"] is True

    (episode.run_root / "input/item_change_ticket.json").write_text("{}\n", encoding="utf-8")
    tampered = finalize_run(store, run_id, video_verified=True)["summary"]
    assert tampered["artifact_failure"] is True
    assert tampered["artifact_failures"] == ["hash_drift:input/item_change_ticket.json"]
    assert tampered["formal_success"] is False
