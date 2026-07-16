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
    assert "HIDDEN_FAULT_SENTINEL" in observer.text
    assert "data-bid=" not in observer.text


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
