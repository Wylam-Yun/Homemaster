from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from case02_openenv.presentation_models import FAILURE_LABELS_ZH
from PIL import Image, ImageStat

from tests.case02_openenv.test_api_contract import client, create_run


def test_agent_pages_render_unique_data_bids_and_no_hidden_sentinel(tmp_path: Path) -> None:
    api = client(tmp_path)
    create_run(api, "page-run")
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
    create_run(api, "leadership-run")

    response = api.get("/observer/leadership-run")

    assert response.status_code == 200
    assert '<body class="executive-observer">' in response.text
    for node_id in (
        "sop-stage-strip",
        "stage-list",
        "current-stage",
        "current-sop-name",
        "current-sop-text",
        "model-plan",
        "plan-active",
        "plan-current",
        "plan-next",
        "model-output-kind",
        "model-output-tool",
        "model-output-tool-label",
        "model-output-tool-kind",
        "model-output-arguments",
        "public-model-reply",
        "environment-result-status",
        "environment-result-summary",
        "environment-result-failure",
        "decision-fact",
        "decision-judgment",
        "decision-next",
        "open-incident",
        "resolved-incidents",
        "critical-history",
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
    assert "fetch(`/api/runs/${runId}/scores`)" in script.text
    assert "window.setInterval(refreshScores, 400)" in script.text
    assert "trajectory=${formatScore(summary.trajectory_score)}" in script.text
    assert "result=${formatScore(summary.result_score)}" in script.text
    assert "video_verification=${formatState(summary.video_verification)}" in script.text
    assert "formal_success=${formatState(summary.formal_success)}" in script.text
    assert "textContent" in script.text
    assert "createElement" in script.text
    assert "replaceChildren" in script.text
    for snapshot_field in (
        "snapshot.plan",
        "snapshot.current_action",
        "snapshot.last_result",
        "snapshot.public_model_output",
        "snapshot.decision_summary",
        "snapshot.incidents",
        "snapshot.critical_history",
    ):
        assert snapshot_field in script.text
    assert 'setText("model-output-kind"' in script.text
    assert "action.tool_label_zh" in script.text
    assert "action.tool_kind" in script.text
    for forbidden in (
        "innerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "/state",
        "/audit",
    ):
        assert forbidden not in script.text

    for name in ("ticket", "monitor", "automation"):
        agent_script = api.get(f"/static/{name}.js")
        assert "/scores" not in agent_script.text


def test_executive_observer_controller_resets_generation_and_renders_incidents(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text

    assert "snapshot.presentation_generation" in script
    assert "generation > presentationGeneration" in script
    assert "lastSequence = 0" in script
    assert "clearDynamicState()" in script
    assert "event.sequence <= lastSequence" in script
    assert 'entry.status === "open"' in script
    assert 'entry.status === "resolved"' in script
    assert "open.failure_code" in script
    assert "entry.recovery.tool_name" in script
    assert "Math.max" not in script


def test_observer_presentation_snapshot_exposes_stream_generation(tmp_path: Path) -> None:
    api = client(tmp_path)
    create_run(api, "generation-run")

    response = api.get("/api/runs/generation-run/presentation")

    assert response.status_code == 200
    assert response.json()["snapshot"]["presentation_generation"] == 0


def test_snapshot_stage_wins_over_an_older_last_event_stage(tmp_path: Path) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text
    snapshot_block = script[script.index("const applySnapshot") : script.index("const applyEvent")]

    result_render = snapshot_block.index("renderEnvironmentResult(snapshot.last_result)")
    assert snapshot_block.index("renderStages(snapshot.stage)") > result_render


def test_live_event_refreshes_snapshot_without_creating_another_stream(tmp_path: Path) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text
    event_handler = script[
        script.index('addEventListener("presentation.event"') : script.index("stream.onerror")
    ]

    assert "const refreshSnapshot" in script
    assert "if (applyEvent(event))" in event_handler
    assert "refreshSnapshot();" in event_handler
    assert script.count("new EventSource(") == 1


def test_snapshot_refresh_rejects_older_generation_and_sequence(tmp_path: Path) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text
    snapshot_block = script[script.index("const applySnapshot") : script.index("const applyEvent")]

    assert "generation < presentationGeneration" in snapshot_block
    assert "generation === presentationGeneration" in snapshot_block
    assert "snapshotSequence < lastSequence" in snapshot_block


def test_failed_snapshot_refresh_preserves_the_newer_live_result(tmp_path: Path) -> None:
    api = client(tmp_path)
    script = api.get("/static/observer.js").text
    refresh_block = script[script.index("const refreshSnapshot") : script.index("const connect")]

    assert "showStatus" not in refresh_block
    assert 'setText("environment-result-summary"' not in refresh_block


def test_presentation_snapshot_supplies_live_progress_and_footer_values(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    create_run(api, "footer-run")
    api.app.state.store.episode("footer-run").state.terminal_outcome = "completed"
    event = api.post(
        "/api/runs/footer-run/presentation-events",
        json={
            "runtime_event_type": "runtime.turn_completed",
            "status": "succeeded",
            "result": {
                "score_summary": {
                    "outcome_category": "completed",
                    "formal_success": True,
                }
            },
        },
    )

    snapshot = api.get("/api/runs/footer-run/presentation").json()["snapshot"]

    assert event.status_code == 200
    assert snapshot["last_sequence"] == 1
    assert snapshot["next_step"] == "等待 Agent 读取变更单"
    assert snapshot["terminal_outcome"] == "completed"
    assert snapshot["last_event"]["result"]["score_summary"] == {
        "outcome_category": "completed",
        "formal_success": True,
    }


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
        "grid-template-rows: 150px 190px 145px 205px 162px",
        "height: 84px",
        ".plan-pinned",
        "#model-plan",
        '.open-incident[data-status="open"]',
        "#score-summary { font-size: 17px; }",
        "line-height: 1.55",
        "white-space: pre-wrap",
        "box-sizing: border-box",
    ):
        assert contract in css


@pytest.mark.live_coworker
def test_long_plan_keeps_active_item_and_next_focus_visible_without_overlap(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    api = client(tmp_path)
    run_id = "long-plan-layout"
    create_run(api, run_id)
    html = api.get(f"/observer/{run_id}").text
    css = api.get("/static/app.css").text
    script = api.get("/static/observer.js").text
    long_active = "Active model-authored planning item " + "x" * 120
    long_focus = "Next model focus " + "y" * 220
    snapshot = {
        "presentation_generation": 0,
        "last_sequence": 8,
        "stage": "change_implement",
        "terminal_outcome": None,
        "current_task": {
            "check_name": "Submit the locked configuration change",
            "source_text": "Run the exact approved automation with locked parameters.",
        },
        "plan": {
            "items": [
                {
                    "id": f"step-{index}",
                    "title": long_active if index == 7 else f"Completed plan item {index}",
                    "status": "in_progress" if index == 7 else "completed",
                }
                for index in range(12)
            ],
            "current_id": "step-7",
            "next_focus": long_focus,
        },
        "current_action": {
            "tool_name": "browser_wait",
            "tool_label_zh": "等待自动化任务",
            "tool_kind": "wait",
            "arguments": {"job_id": "job-add-abcdef1234"},
        },
        "last_result": {
            "status": "rejected",
            "failure_code": "wait_required",
            "result": {},
        },
        "public_model_output": {
            "kind": "assistant_reply",
            "outcome": "intermediate",
            "text": "Waiting for the exact submitted job.",
        },
        "decision_summary": {
            "fact": {"label_zh": "当前存在未恢复异常", "values": {}},
            "judgment": {"label_zh": "必须先完成匹配恢复", "values": {}},
            "next_action": {"label_zh": "执行异常要求的恢复动作", "values": {}},
        },
        "incidents": [
            {
                "status": "open",
                "failure_code": "wait_required",
                "label_zh": "尚未等待准确任务完成",
                "failed_tool": "terminal_execute",
                "target": {"job_id": "job-add-abcdef1234"},
            }
        ],
        "critical_history": [
            {"kind": "incident", "label_zh": "尚未等待准确任务完成", "status": "open"}
        ],
    }
    bootstrap = f"""
      <script>
        window.TEST_SNAPSHOT = {json.dumps(snapshot, ensure_ascii=False)};
        window.fetch = (url) => Promise.resolve({{
          ok: true,
          json: () => Promise.resolve(String(url).endsWith('/scores')
            ? {{status: 'pending'}}
            : {{snapshot: window.TEST_SNAPSHOT}}),
        }});
        window.EventSource = class {{ addEventListener() {{}} }};
        window.setInterval = () => 0;
      </script>
    """
    html = html.replace('<link rel="stylesheet" href="/static/app.css">', f"<style>{css}</style>")
    html = html.replace(
        '<script src="/static/observer.js"></script>',
        f"{bootstrap}<script>{script}</script>",
    )
    screenshot = tmp_path / "long-plan-observer.png"

    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.set_content(html, wait_until="domcontentloaded")
        page.wait_for_function(
            "document.getElementById('plan-active').textContent.startsWith('Active')"
        )
        plan_card = page.locator(".model-plan-card").bounding_box()
        active = page.locator("#plan-active").bounding_box()
        next_focus = page.locator("#plan-next").bounding_box()
        cards = page.locator(".observer-card").all()
        boxes = [card.bounding_box() for card in cards]
        page.screenshot(path=str(screenshot))
        browser.close()

    assert plan_card and active and next_focus
    for box in (active, next_focus):
        assert plan_card["y"] <= box["y"]
        assert box["y"] + box["height"] <= plan_card["y"] + plan_card["height"]
    for first, second in zip(boxes, boxes[1:], strict=False):
        assert first and second
        assert first["y"] + first["height"] <= second["y"]
    image = Image.open(screenshot).convert("RGB")
    observer_region = image.crop((1320, 96, 1920, 996))
    assert max(ImageStat.Stat(observer_region).var) > 100


@pytest.mark.live_coworker
def test_every_safe_failure_code_renders_expanded_then_one_line_resolved(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    api = client(tmp_path)
    run_id = "failure-matrix-layout"
    create_run(api, run_id)
    html = api.get(f"/observer/{run_id}").text
    css = api.get("/static/app.css").text
    script = api.get("/static/observer.js").text
    base_snapshot = {
        "presentation_generation": 0,
        "last_sequence": 1,
        "stage": "check_before_change",
        "terminal_outcome": None,
        "current_task": None,
        "plan": {"items": [], "current_id": None, "next_focus": None},
        "current_action": None,
        "last_result": None,
        "public_model_output": None,
        "decision_summary": {
            "fact": {"label_zh": "当前存在未恢复异常", "values": {}},
            "judgment": {"label_zh": "必须先完成匹配恢复", "values": {}},
            "next_action": {"label_zh": "执行异常要求的恢复动作", "values": {}},
        },
        "incidents": [],
        "critical_history": [],
    }
    html = html.replace('<link rel="stylesheet" href="/static/app.css">', f"<style>{css}</style>")

    def render_html(snapshot: dict) -> str:
        serialized_snapshot = json.dumps(snapshot, ensure_ascii=False)
        bootstrap = f"""
          <script>
            window.fetch = () => Promise.resolve({{
              ok: true,
              json: () => Promise.resolve({{snapshot: {serialized_snapshot}}}),
            }});
            window.EventSource = class {{ addEventListener() {{}} }};
            window.setInterval = () => 0;
          </script>
        """
        return html.replace(
            '<script src="/static/observer.js"></script>',
            f"{bootstrap}<script>{script}</script>",
        )

    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for index, (failure_code, label) in enumerate(FAILURE_LABELS_ZH.items(), start=1):
            incident = {
                "incident_id": f"incident-{index:05d}",
                "status": "open",
                "failure_code": failure_code,
                "label_zh": label,
                "failed_tool": "browser_click",
                "failed_action_id": f"failed-{index}",
                "opened_sequence": index,
                "target": {"bid": "automation-submit"},
                "recovery": None,
            }
            open_snapshot = {**base_snapshot, "incidents": [incident]}
            page.set_content(render_html(open_snapshot), wait_until="domcontentloaded")
            page.wait_for_function(
                "code => document.getElementById('open-incident').textContent.includes(code)",
                arg=failure_code,
            )
            open_text = page.locator("#open-incident").text_content()
            assert open_text and failure_code in open_text and label in open_text
            assert page.locator("#open-incident").get_attribute("data-status") == "open"

            incident["status"] = "resolved"
            incident["recovery"] = {
                "tool_name": "browser_click",
                "action_id": f"recovery-{index}",
                "resolved_sequence": index + 1,
                "intervening_model_calls": 1,
            }
            resolved_snapshot = {**base_snapshot, "incidents": [incident]}
            page.set_content(render_html(resolved_snapshot), wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.querySelectorAll('#resolved-incidents li').length === 1"
            )
            assert page.locator("#open-incident").text_content() == "当前无未恢复异常"
            resolved = page.locator("#resolved-incidents li")
            assert resolved.count() == 1
            assert resolved.first.text_content() == f"{label} · browser_click"
        browser.close()


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
    create_run(api, "projection-run", "post_change_anomaly")
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
