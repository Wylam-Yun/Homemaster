from __future__ import annotations

import json
from pathlib import Path

from case02_openenv.api import create_app
from case02_openenv.config import ServiceConfig
from fastapi.testclient import TestClient


def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            ServiceConfig(
                data_root=Path("data/coworker_demo/case_02").resolve(),
                artifact_root=tmp_path / "runs",
            )
        )
    )


def test_health_create_unknown_and_docs_contract(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.get("/healthz").status_code == 200
    assert api.get("/docs").status_code == 404
    assert api.get("/openapi.json").status_code == 404
    created = api.post("/api/runs", json={"run_id": "api-run", "scenario_id": "normal"})
    assert created.status_code == 200
    assert created.json()["state"]["phase"] == "created"
    missing = api.get("/api/runs/does-not-exist/state")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "unknown_run"


def test_stale_and_replayed_action_fail_without_mutation(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "ledger-api", "scenario_id": "normal"})
    reservation = {
        "operation": "reserve",
        "action_id": "once",
        "tool_name": "browser_click",
        "page_state_version": 1,
    }
    assert api.post("/api/runs/ledger-api/action-events", json=reservation).status_code == 200
    checked = api.post(
        "/api/runs/ledger-api/ticket/config-check",
        json={"action_id": "once", "page_state_version": 1, "check": "extension_config"},
    )
    assert checked.status_code == 200
    replay = api.post(
        "/api/runs/ledger-api/ticket/config-check",
        json={"action_id": "once", "page_state_version": 1, "check": "upstream_ready"},
    )
    assert replay.status_code == 409
    assert replay.json()["error_code"] == "action_replay"
    state = api.get("/api/runs/ledger-api/state").json()["state"]
    assert state["config_checks"] == {"extension_config": True}


def test_recorded_browser_action_consumes_its_reservation(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "read-ledger", "scenario_id": "normal"})
    reservation = {
        "operation": "reserve",
        "action_id": "observe-once",
        "tool_name": "browser_observe",
        "page_state_version": 1,
    }
    assert api.post("/api/runs/read-ledger/action-events", json=reservation).status_code == 200
    recorded = {
        **reservation,
        "operation": "record",
        "arguments": {"url": "http://case02.test/ticket/read-ledger"},
    }
    assert api.post("/api/runs/read-ledger/action-events", json=recorded).status_code == 200
    state = api.get("/api/runs/read-ledger/state").json()["state"]
    assert state["action_ledger"]["observe-once"]["consumed"] is True
    replay = api.post("/api/runs/read-ledger/action-events", json=recorded)
    assert replay.status_code == 409
    assert replay.json()["error_code"] == "action_replay"


def test_required_route_methods_are_present(tmp_path: Path) -> None:
    app = client(tmp_path).app
    actual = {
        (method, route.path) for route in app.routes for method in getattr(route, "methods", set())
    }
    required = {
        ("GET", "/healthz"),
        ("POST", "/api/runs"),
        ("POST", "/api/runs/{run_id}/reset"),
        ("GET", "/api/runs/{run_id}/state"),
        ("GET", "/api/runs/{run_id}/audit"),
        ("POST", "/api/runs/{run_id}/runtime-events"),
        ("POST", "/api/runs/{run_id}/action-events"),
        ("POST", "/api/runs/{run_id}/decisions"),
        ("POST", "/api/runs/{run_id}/ticket/config-check"),
        ("POST", "/api/runs/{run_id}/monitor/query"),
        ("POST", "/api/runs/{run_id}/automation/jobs"),
        ("GET", "/api/runs/{run_id}/automation/jobs/{job_id}"),
        ("POST", "/api/runs/{run_id}/terminal"),
        ("POST", "/api/runs/{run_id}/recording/start"),
        ("GET", "/api/runs/{run_id}/recording"),
        ("POST", "/api/runs/{run_id}/recording/stop"),
        ("POST", "/api/runs/{run_id}/finalize"),
        ("GET", "/api/runs/{run_id}/scores"),
        ("GET", "/api/runs/{run_id}/events"),
        ("POST", "/api/runs/{run_id}/presentation-events"),
        ("GET", "/api/runs/{run_id}/presentation-events"),
        ("GET", "/api/runs/{run_id}/presentation"),
        ("GET", "/ticket/{run_id}"),
        ("GET", "/monitor/{run_id}"),
        ("GET", "/automation/{run_id}"),
        ("GET", "/observer/{run_id}"),
    }
    assert required <= actual


def test_openapi_snapshot_matches_runtime_schema(tmp_path: Path) -> None:
    generated = client(tmp_path).app.openapi()
    snapshot = json.loads(Path("apps/case02_openenv/openapi.json").read_text(encoding="utf-8"))
    assert generated == snapshot
    assert set(snapshot["paths"]) == {
        route.path
        for route in client(tmp_path).app.routes
        if getattr(route, "methods", None)
        and route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    }


def test_sse_emits_snapshot_and_replays_only_after_last_event_id(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "sse-run", "scenario_id": "normal"})
    store = api.app.state.store
    first = store.record("sse-run", source="state", kind="first", status="succeeded")
    second = store.record("sse-run", source="state", kind="second", status="succeeded")
    api.app.state.sse_idle_iterations = 1
    api.app.state.sse_poll_interval_s = 0

    response = api.get("/api/runs/sse-run/events", headers={"Last-Event-ID": first.event_id})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in response.text
    assert f"id: {second.event_id}" in response.text
    assert f"id: {first.event_id}" not in response.text
    assert '"kind":"second"' in response.text


def test_presentation_api_appends_snapshots_and_resumes_sse(tmp_path: Path) -> None:
    api = client(tmp_path)
    run_id = "present-api"
    api.post("/api/runs", json={"run_id": run_id, "scenario_id": "normal"})
    started = {
        "runtime_event_type": "tool.call_started",
        "tool_call_id": "call-1",
        "action_id": "action-1",
        "tool_name": "browser_click",
        "status": "running",
        "arguments": {"bid": "ticket-query-extension-config"},
    }
    completed = {
        **started,
        "runtime_event_type": "tool.call_completed",
        "status": "succeeded",
        "result": {"message": "ready"},
    }

    first = api.post(f"/api/runs/{run_id}/presentation-events", json=started)
    second = api.post(f"/api/runs/{run_id}/presentation-events", json=completed)
    assert first.status_code == second.status_code == 200
    assert [first.json()["event"]["sequence"], second.json()["event"]["sequence"]] == [1, 2]
    snapshot = api.get(f"/api/runs/{run_id}/presentation").json()["snapshot"]
    assert snapshot["current_task"]["source_field"] == "operate_description"
    assert snapshot["last_event"]["status"] == "succeeded"

    api.app.state.sse_idle_iterations = 2
    api.app.state.sse_poll_interval_s = 0
    response = api.get(
        f"/api/runs/{run_id}/presentation-events",
        headers={"Last-Event-ID": first.json()["event"]["event_id"]},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: presentation.snapshot" in response.text
    assert f'id: {first.json()["event"]["event_id"]}' not in response.text
    assert f'id: {second.json()["event"]["event_id"]}' in response.text
    assert "event: presentation.event" in response.text
    assert '"status":"succeeded"' in response.text
    assert ": heartbeat" in response.text


def test_presentation_rejects_embedded_cross_run_identity(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "run-a", "scenario_id": "normal"})
    response = api.post(
        "/api/runs/run-a/presentation-events",
        json={
            "runtime_event_type": "tool.call_started",
            "tool_call_id": "call-cross-run",
            "action_id": "action-cross-run",
            "status": "running",
            "arguments": {"run_id": "run-b", "bid": "ticket-query-extension-config"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "presentation_run_mismatch"


def test_presentation_api_rejects_incoherent_lifecycle_payloads(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "lifecycle-api", "scenario_id": "normal"})
    contradictory = api.post(
        "/api/runs/lifecycle-api/presentation-events",
        json={
            "runtime_event_type": "tool.call_completed",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "status": "running",
        },
    )
    missing_identity = api.post(
        "/api/runs/lifecycle-api/presentation-events",
        json={"runtime_event_type": "tool.call_started", "status": "running"},
    )
    assert contradictory.status_code == 422
    assert missing_identity.status_code == 422


def test_presentation_sse_restarts_cursor_after_reset(
    tmp_path: Path, monkeypatch
) -> None:
    import case02_openenv.api as api_module

    api = client(tmp_path)
    run_id = "presentation-sse-reset"
    api.post("/api/runs", json={"run_id": run_id, "scenario_id": "normal"})
    store = api.app.state.store
    old = store.record_presentation(
        run_id,
        api_module.PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="old-call",
            action_id="old-action",
            status="running",
        ),
    )
    new_event = {}

    async def reset_once(_seconds: float) -> None:
        if new_event:
            return
        store.reset(run_id)
        new_event["event"] = store.record_presentation(
            run_id,
            api_module.PresentationInput(
                runtime_event_type="tool.call_started",
                tool_call_id="new-call",
                action_id="new-action",
                status="running",
            ),
        )

    monkeypatch.setattr(api_module.asyncio, "sleep", reset_once)
    api.app.state.sse_idle_iterations = 2
    api.app.state.sse_poll_interval_s = 0

    response = api.get(f"/api/runs/{run_id}/presentation-events")

    assert response.status_code == 200
    assert f"id: {old.event_id}" in response.text
    assert response.text.count("event: presentation.snapshot") >= 2
    assert f'id: {new_event["event"].event_id}' in response.text


def test_recording_start_constructs_a_run_session(tmp_path: Path, monkeypatch) -> None:
    import case02_openenv.api as api_module

    class FakeRecordingSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return {"success": True, "status": "recording", "display": ":144"}

    monkeypatch.setattr(api_module, "_ServiceRecordingSession", FakeRecordingSession)
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "recording-api", "scenario_id": "normal"})
    response = api.post("/api/runs/recording-api/recording/start")
    assert response.status_code == 200
    assert response.json()["display"] == ":144"
    assert "recording-api" in api.app.state.recorders


def test_recording_stop_publishes_real_observer_health(tmp_path: Path, monkeypatch) -> None:
    import case02_openenv.evaluation.scoring as scoring_module

    captured: dict[str, object] = {}

    class FakeRecordingSession:
        def stop(self):
            return {
                "success": True,
                "status": "verified",
                "manifest": {"sha256": "video-sha"},
                "observer_was_alive": False,
                "display_return_codes": {"observer": 1, "tigervnc": -15},
            }

    def fake_publish(store, run_id, manifest, *, observer_was_alive):
        captured.update(
            run_id=run_id,
            manifest=manifest,
            observer_was_alive=observer_was_alive,
        )
        return {"formal_success": False, "presentation_failure": True}

    monkeypatch.setattr(scoring_module, "publish_video_verification", fake_publish)
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "recording-stop", "scenario_id": "normal"})
    api.app.state.recorders["recording-stop"] = FakeRecordingSession()

    response = api.post("/api/runs/recording-stop/recording/stop")

    assert response.status_code == 200
    assert response.json()["observer_was_alive"] is False
    assert captured == {
        "run_id": "recording-stop",
        "manifest": {"sha256": "video-sha"},
        "observer_was_alive": False,
    }
