from __future__ import annotations

import re
from pathlib import Path

from tests.case02_openenv.test_api_contract import client


def test_agent_pages_render_unique_data_bids_and_no_hidden_sentinel(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "page-run", "scenario_id": "normal"})
    api.app.state.store.episode("page-run").state.anomaly_code = "HIDDEN_FAULT_SENTINEL"
    for route in ("ticket", "monitor", "automation"):
        response = api.get(f"/{route}/page-run")
        assert response.status_code == 200
        assert "HIDDEN_FAULT_SENTINEL" not in response.text
        bids = re.findall(r'data-bid="([^"]+)"', response.text)
        assert bids
        assert len(bids) == len(set(bids))
    observer = api.get("/observer/page-run")
    assert "HIDDEN_FAULT_SENTINEL" not in observer.text
    assert "data-bid=" not in observer.text


def test_executive_observer_page_is_read_only_and_leadership_scoped(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "leadership-run", "scenario_id": "normal"})

    response = api.get("/observer/leadership-run")

    assert response.status_code == 200
    assert '<body class="executive-observer">' in response.text
    for node_id in (
        "sop-stage-strip",
        "stage-list",
        "current-stage",
        "current-sop-name",
        "current-sop-text",
        "current-tool-name",
        "current-tool-arguments",
        "latest-result-status",
        "latest-result-summary",
        "latest-result-evidence",
        "completed-steps",
        "next-step",
        "run-outcome",
        "score-summary",
    ):
        assert f'id="{node_id}"' in response.text
    for label in (
        "变更前检查",
        "变更执行",
        "独立验证",
        "变更后检查",
        "业务验证",
        "回滚",
        "完成",
    ):
        assert label in response.text
    for forbidden in (
        "data-bid=",
        "Environment state",
        "Evidence timeline",
        "observer-state",
        "raw state",
    ):
        assert forbidden not in response.text
    assert re.search(r"<(?:input|button|form|select|textarea)\b", response.text) is None


def test_executive_observer_script_uses_safe_presentation_stream(tmp_path: Path) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js")

    assert script.status_code == 200
    assert "fetch(`/api/runs/${runId}/presentation`)" in script.text
    assert "new EventSource(`/api/runs/${runId}/presentation-events`)" in script.text
    assert 'addEventListener("presentation.snapshot"' in script.text
    assert 'addEventListener("presentation.event"' in script.text
    assert "textContent" in script.text
    assert "createElement" in script.text
    assert "replaceChildren" in script.text
    for forbidden in (
        "innerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "/state",
        "/audit",
        "/scores",
    ):
        assert forbidden not in script.text


def test_executive_observer_controller_resets_generation_and_rejects_failures(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text

    assert "snapshot.presentation_generation" in script
    assert "generation !== presentationGeneration" in script
    assert "lastSequence = 0" in script
    assert "clearDynamicState()" in script
    assert "event.sequence <= lastSequence" in script
    assert 'event.status === "succeeded" && !event.failure' in script
    assert "source_sha256 || event.task.check_name" in script
    assert "Math.max" not in script


def test_observer_presentation_snapshot_exposes_stream_generation(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "generation-run", "scenario_id": "normal"})

    response = api.get("/api/runs/generation-run/presentation")

    assert response.status_code == 200
    assert response.json()["snapshot"]["presentation_generation"] == 0


def test_snapshot_stage_wins_over_an_older_last_event_stage(tmp_path: Path) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text
    snapshot_block = script[script.index("const applySnapshot") : script.index("const applyEvent")]

    last_event_render = snapshot_block.index("renderEvent(snapshot.last_event)")
    assert snapshot_block.index("renderStages(snapshot.stage)") > last_event_render


def test_executive_observer_css_has_fixed_recording_geometry(tmp_path: Path) -> None:
    api = client(tmp_path)
    css = api.get("/static/app.css").text

    assert ".executive-observer" in css
    for contract in (
        "width: 1920px",
        "height: 1080px",
        "overflow: hidden",
        "height: 96px",
        "grid-template-columns: 300px 1fr",
        "grid-template-columns: repeat(7, minmax(0, 1fr))",
        "height: 900px",
        "grid-template-columns: 1320px 600px",
        "height: 84px",
        "font-size: 20px",
        "line-height: 1.55",
        "white-space: pre-wrap",
        "box-sizing: border-box",
    ):
        assert contract in css


def test_linked_javascript_does_not_embed_hidden_contract_terms(tmp_path: Path) -> None:
    api = client(tmp_path)
    for name in ("ticket", "monitor", "automation"):
        script = api.get(f"/static/{name}.js")
        assert script.status_code == 200
        for forbidden in ("HIDDEN_FAULT_SENTINEL", "trajectory_score", "formal_success"):
            assert forbidden not in script.text


def test_agent_public_projections_exclude_observer_only_state(tmp_path: Path) -> None:
    from case02_openenv.public_views import automation_view, monitor_view, ticket_view

    api = client(tmp_path)
    api.post(
        "/api/runs",
        json={"run_id": "projection-run", "scenario_id": "post_change_anomaly"},
    )
    episode = api.app.state.store.episode("projection-run")
    episode.state.anomaly_code = "HIDDEN_FAULT_SENTINEL"
    episode.state.causal_add_job_id = "HIDDEN_JOB_SENTINEL"
    episode.state.causal_grep_evidence_id = "HIDDEN_EVIDENCE_SENTINEL"
    views = (
        ticket_view(episode.state, episode.ticket),
        monitor_view(episode.state),
        automation_view(episode.state),
    )
    encoded = "\n".join(view.model_dump_json() for view in views)
    for forbidden in (
        "HIDDEN_FAULT_SENTINEL",
        "HIDDEN_JOB_SENTINEL",
        "HIDDEN_EVIDENCE_SENTINEL",
        "anomaly_code",
        "terminal_outcome",
        "trajectory_score",
        "formal_success",
    ):
        assert forbidden not in encoded
