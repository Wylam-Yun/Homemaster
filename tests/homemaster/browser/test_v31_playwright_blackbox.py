from __future__ import annotations

import functools
import hashlib
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from homemaster.browser.contracts import BrowserSessionError
from homemaster.browser.playwright_session import PlaywrightBrowserSession
from homemaster.browser.policy import BrowserPolicy


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        del format, args


@pytest.fixture
def v31_origin() -> str:
    root = Path(__file__).parent / "fixtures"
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _target(name: str, *, role: str | None = None, frame_ref: str | None = None):
    value: dict[str, object] = {"name": name, "match": "exact"}
    if role is not None:
        value["role"] = role
    if frame_ref is not None:
        value["frame_ref"] = frame_ref
    return value


async def _new_session(tmp_path: Path, origin: str, *, eval_allowed: bool = False):
    session = PlaywrightBrowserSession(
        session_id=tmp_path.name,
        policy=BrowserPolicy(allowed_origins=(origin,), eval_allowed=eval_allowed),
        video_dir=tmp_path / "video",
    )
    await session.start()
    await session.navigate(f"{origin}/v31-controls.html")
    return session


@pytest.mark.asyncio
async def test_v31_representation_refs_reads_and_compound_controls(
    tmp_path: Path, v31_origin: str
) -> None:
    session = await _new_session(tmp_path, v31_origin)
    try:
        dom = await session.inspect({"view": "dom", "limit": 120})
        ax = await session.inspect({"view": "ax", "interactive_only": True})
        hybrid = await session.inspect({"view": "hybrid", "limit": 120})
        frames = await session.inspect({"view": "frames"})
        assert "fixture-node-01" in dom.text
        assert "button" in ax.text and "Double action" in ax.text
        assert dom.text in hybrid.text
        assert len(frames.frames) == 2

        scoped = await session.inspect(
            {"view": "dom", "scope": _target("Scoped controls", role="region")}
        )
        assert {item.name for item in scoped.elements} == {"Scoped child"}

        frame = next(item for item in frames.frames if str(item["url"]).endswith("v31-frame.html"))
        frame_ref = str(frame["frame_id"])
        frame_find = await session.find(
            {"name": "Frame action", "match": "exact", "frame_ref": frame_ref}
        )
        assert frame_find["matches_n"] == 1
        assert frame_find["target"]["frame_id"] == frame_ref

        shadow = await session.find({"name": "Shadow action", "match": "exact"})
        assert shadow["matches_n"] == 1
        css = await session.find({"css": "#account"})
        assert css["matches_n"] == 1 and css["read_only"] is True
        with pytest.raises(BrowserSessionError) as ambiguous:
            await session.find({"name": "Duplicate action", "match": "exact"})
        assert ambiguous.value.code == "target_ambiguous"
        assert ambiguous.value.details["matches_n"] == 2
        with pytest.raises(BrowserSessionError) as missing:
            await session.find({"name": "Missing action", "match": "exact"})
        assert missing.value.code == "target_not_found"

        assert (await session.read({"kind": "title"}))["value"] == "V3.1 Browser Black Box"
        assert (await session.read({"kind": "url"}))["value"].endswith("v31-controls.html")
        account = await session.find({"name": "Account name", "match": "exact"})
        account_ref = str(account["target"]["target_ref"])
        assert (
            await session.read({"kind": "value", "target": {"target_ref": account_ref}})
        )["value"] == "initial"
        attributes = await session.read(
            {"kind": "attributes", "target": {"target_ref": account_ref}}
        )
        assert attributes["value"]["id"] == "account"
        tree = await session.read({"kind": "html", "format": "tree", "max_chars": 20_000})
        assert tree["matches_n"] == 1 and tree["value"]
        scoped_tree = await session.read(
            {
                "kind": "html",
                "format": "tree",
                "scope": _target("Readable report region", role="region"),
                "max_depth": 2,
                "children_max": 2,
                "text_max": 40,
            }
        )
        assert scoped_tree["value"]["tree"]["tag"] == "article"
        form = await session.read({"kind": "form_state"})
        assert "settings-form" in str(form["value"])

        first = await session.extract({"chunk_size": 80, "start_char": 0})
        assert first["markdown"] and first["has_more"] is True
        second = await session.extract(
            {"chunk_size": 80, "start_char": first["next_start_char"]}
        )
        assert second["start_char"] == first["next_start_char"]
        scoped_extract = await session.extract(
            {"scope": _target("Readable report region", role="region")}
        )
        assert "Paragraph alpha" in scoped_extract["markdown"]
        assert "Account name" not in scoped_extract["markdown"]
        frame_extract = await session.extract({"frame_ref": frame_ref})
        assert "Frame readable content" in frame_extract["markdown"]

        await session._page.evaluate(
            "document.getElementById('account').dataset.browserAction = 'stable-change'"
        )
        stable = await session.fill({"target_ref": account_ref}, "stable-value")
        assert stable["actual"] == "stable-value" and stable["match"] == "stable"
        await session._page.evaluate(
            """() => {
              const old = document.getElementById('account');
              const fresh = document.createElement('input');
              fresh.id = 'account'; fresh.name = 'account'; fresh.value = old.value;
              old.replaceWith(fresh);
            }"""
        )
        reidentified = await session.fill({"target_ref": account_ref}, "reidentified-value")
        assert reidentified["actual"] == "reidentified-value"
        assert reidentified["match"] == "reidentified"

        date = await session.fill(_target("Run date", role="textbox"), "2026-08-21")
        time = await session.fill(_target("Run time", role="textbox"), "21:25:07")
        timestamp = await session.fill(
            _target("Run timestamp", role="textbox"), "2026-08-21T21:25:07"
        )
        assert date["actual"] == "2026-08-21"
        assert time["actual"] == "21:25:07"
        assert timestamp["actual"] == "2026-08-21T21:25:07"

        region = await session.select(_target("Region", role="combobox"), "United States")
        zones = await session.select(
            _target("Zones", role="combobox"),
            None,
            options=["Zone A", "Zone C"],
            match="label",
        )
        assert region["actual"] == "us"
        assert zones["actual"] == ["a", "c"]

        checkbox = await session.check(_target("Enabled", role="checkbox"))
        checkbox_again = await session.check(_target("Enabled", role="checkbox"))
        radio = await session.check(_target("Choice B", role="radio"))
        switch = await session.check(_target("Notifications", role="switch"))
        assert checkbox["actual"] is True and checkbox["changed"] is True
        assert checkbox_again["actual"] is True and checkbox_again["changed"] is False
        assert radio["actual"] is True
        assert switch["actual"] is True
        with pytest.raises(BrowserSessionError) as radio_uncheck:
            await session.uncheck(_target("Choice B", role="radio"))
        assert radio_uncheck.value.code == "unsupported_control"

        analyzed_navigation = await session.analyze(
            {"url": f"{v31_origin}/v31-frame.html", "settle_ms": 10, "network_limit": 10}
        )
        assert analyzed_navigation["http_status"] == 200
        assert analyzed_navigation["final_url"].endswith("v31-frame.html")
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_v31_actions_events_network_files_and_screenshot(
    tmp_path: Path, v31_origin: str
) -> None:
    session = await _new_session(tmp_path, v31_origin)
    try:
        replaced = await session.type(_target("Account name"), "alpha", mode="replace")
        appended = await session.type(_target("Account name"), "-beta", mode="append")
        focused = await session.focus(_target("Keyboard target"))
        hovered = await session.hover(
            _target("Hover details", role="button"),
            expect={"kind": "text_present", "value": "Tooltip visible"},
        )
        doubled = await session.click(_target("Double action", role="button"), click_count=2)
        pressed = await session.press(
            "Enter",
            _target("Keyboard target"),
            expect={"kind": "text_present", "value": "Enter received"},
        )
        assert replaced["actual"] == "alpha"
        assert appended["actual"] == "alpha-beta"
        assert focused["focused"] is True
        assert hovered["hovered"] is True and hovered["postcondition"]["matched"] is True
        assert doubled["interaction_verified"] is True
        assert pressed["postcondition"]["matched"] is True
        assert (await session.read({"kind": "text", "target": _target("Double count 1")}))[
            "value"
        ] == "Double count 1"

        scrolled = await session.scroll(
            {"mode": "into_view", "target": _target("Scrollable action", role="button")}
        )
        assert scrolled["visible"] is True
        container_scrolled = await session.scroll(
            {
                "mode": "by",
                "container": _target("Scroll box", role="region"),
                "direction": "up",
                "amount_px": 90,
            }
        )
        assert container_scrolled["changed"] is True
        assert container_scrolled["after"]["y"] < container_scrolled["before"]["y"]

        artifact_dir = session.video_dir / "artifacts"
        artifact_dir.mkdir(parents=True)
        artifact = artifact_dir / "evidence.txt"
        artifact.write_text("approved upload evidence", encoding="utf-8")
        uploaded = await session.upload(
            _target("Attachment", role="textbox"), ["artifact:evidence.txt"]
        )
        assert uploaded["count"] == 1 and uploaded["files"][0]["name"] == "evidence.txt"
        assert await session._page.get_attribute("body", "data-upload-name") == "evidence.txt"
        with pytest.raises(BrowserSessionError) as raw_path:
            await session.upload(_target("Attachment", role="textbox"), [str(artifact)])
        assert raw_path.value.code == "artifact_not_allowed"

        dragged = await session.drag(
            _target("Drag source", role="button"),
            _target("Drop target", role="button"),
            expect={"kind": "text_present", "value": "Dropped"},
        )
        assert dragged["dom_changed"] is True and dragged["postcondition"]["matched"] is True

        dialog = await session.dialog(
            {"action": "accept", "trigger": _target("Open confirm", role="button")}
        )
        assert dialog["handled"] is True and dialog["type"] == "confirm"
        assert await session._page.get_attribute("body", "data-dialog-result") == "accepted"

        download = await session.download(
            {"trigger": _target("Download report", role="link"), "pattern": "v31-download.txt"}
        )
        path = Path(str(download["artifact_path"]))
        assert download["completed"] is True and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == download["sha256"]
        assert (await session.wait({"kind": "download", "timeout_ms": 2_000}))["matched"]

        await session.click(_target("Fetch success", role="button"))
        waited = await session.wait(
            {"kind": "response", "value": "v31-network.json", "timeout_ms": 2_000}
        )
        assert waited["matched"] is True
        network = await session.network(
            {"mode": "list", "resource_type": "fetch", "include_static": False}
        )
        request = next(item for item in network["requests"] if "v31-network.json" in item["url"])
        detail = await session.network(
            {"mode": "detail", "request_ref": request["request_ref"], "max_body_chars": 2_000}
        )
        assert "network-ready" in detail["request"]["body"]
        await session.click(_target("Fetch failure", role="button"))
        failed = await session.network({"mode": "list", "failed_only": True})
        assert any(item["status"] == 404 for item in failed["requests"])

        await session._page.evaluate(
            "() => Promise.all(Array.from({length:12}, (_, i) => "
            "fetch(`v31-network.json?cursor=${i}`)))"
        )
        network_page = await session.network(
            {"mode": "list", "resource_type": "fetch", "include_static": True, "limit": 200}
        )
        cursor_requests = [
            item for item in network_page["requests"] if "?cursor=" in str(item["url"])
        ]
        assert len(cursor_requests) == 12
        continued_network = await session.network(
            {
                "mode": "list",
                "resource_type": "fetch",
                "include_static": True,
                "cursor": cursor_requests[8]["request_ref"],
                "limit": 20,
            }
        )
        assert [item["request_ref"] for item in continued_network["requests"]] == [
            item["request_ref"] for item in cursor_requests[9:]
        ]
        assert continued_network["next_cursor"] == cursor_requests[-1]["request_ref"]

        for index in range(12):
            await session._page.evaluate("i => console.info(`cursor-message-${i}`)", index)
        cursor_page = await session.console({"cursor": "console-9", "limit": 20})
        assert any(item["cursor"] == "console-10" for item in cursor_page["messages"])
        console_tail = await session.console(
            {"cursor": cursor_page["next_cursor"], "limit": 20}
        )
        assert console_tail["messages"] == []
        assert console_tail["next_cursor"] == cursor_page["next_cursor"]

        semantic_wait = await session.wait(
            {
                "kind": "selector_present",
                "target": _target("Fetch success", role="button"),
                "timeout_ms": 2_000,
            }
        )
        assert semantic_wait["matched"] is True

        analyzed = await session.analyze({"settle_ms": 10, "network_limit": 20})
        assert any(
            candidate["resource_type"] == "fetch" and candidate["score"] > 0
            for candidate in analyzed["api_candidates"]
        )

        console = await session.console({"level": "info", "limit": 20})
        assert any("v31-console-ready" in item["text"] for item in console["messages"])
        screenshot = await session.screenshot(width=640, height=480, annotate_refs=True)
        assert screenshot["width"] == 640 and screenshot["height"] == 480
        assert screenshot["ref_map"]
        assert Path(str(screenshot["artifact_path"])).is_file()
        assert hashlib.sha256(
            Path(str(screenshot["artifact_path"])).read_bytes()
        ).hexdigest() == screenshot["sha256"]
        frame_ref = next(
            item["frame_id"]
            for item in (await session.inspect({"view": "frames"})).frames
            if not item["is_main"]
        )
        frame_shot = await session.screenshot(frame_ref=frame_ref)
        assert frame_shot["frame_ref"] == frame_ref
        assert frame_shot["width"] > 0 and frame_shot["height"] > 0
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_v31_tabs_history_and_eval_authorization(tmp_path: Path, v31_origin: str) -> None:
    denied = await _new_session(tmp_path / "denied", v31_origin)
    try:
        with pytest.raises(BrowserSessionError) as eval_denied:
            await denied.eval(
                {"script": "() => document.title", "expected_postcondition": "none"}
            )
        assert eval_denied.value.code == "capability_denied"

        created = await denied.tabs(
            {"action": "new", "url": f"{v31_origin}/v31-controls.html"}
        )
        new_ref = str(created["active_tab"])
        assert len(created["tabs"]) == 2
        tab_one_snapshot = await denied.inspect({"name": "Account name", "match": "exact"})
        tab_two_ref = str(tab_one_snapshot.elements[0].target_ref)
        selected = await denied.tabs({"action": "select", "tab_ref": "tab-1"})
        assert selected["active_tab"] == "tab-1"
        with pytest.raises(BrowserSessionError) as wrong_tab:
            await denied.fill({"target_ref": tab_two_ref}, "wrong-tab")
        assert wrong_tab.value.code == "stale_ref"

        popup = await denied.click(_target("Open popup", role="link"))
        assert popup["popup_opened"] is True
        popup_ref = str(popup["popup_tab_ref"])
        assert (await denied.wait({"kind": "popup", "timeout_ms": 2_000}))["matched"]
        await denied.tabs({"action": "close", "tab_ref": popup_ref})
        closed = await denied.tabs({"action": "close", "tab_ref": new_ref})
        assert len(closed["tabs"]) == 1
        with pytest.raises(BrowserSessionError) as last_tab:
            await denied.tabs({"action": "close", "tab_ref": "tab-1"})
        assert last_tab.value.code == "last_tab_close"
        assert len((await denied.tabs({"action": "list"}))["tabs"]) == 1

        await denied.navigate(f"{v31_origin}/v31-frame.html")
        back = await denied.history("back")
        forward = await denied.history("forward")
        reload = await denied.history("reload")
        assert back["url_after"].endswith("v31-controls.html")
        assert forward["url_after"].endswith("v31-frame.html")
        assert reload["changed"] is True
    finally:
        await denied.aclose()

    allowed = await _new_session(tmp_path / "allowed", v31_origin, eval_allowed=True)
    try:
        evaluated = await allowed.eval(
            {"script": "() => document.title", "expected_postcondition": "none"}
        )
        assert "V3.1 Browser Black Box" in evaluated["result"]
        assert evaluated["dom_changed"] is False
    finally:
        await allowed.aclose()
