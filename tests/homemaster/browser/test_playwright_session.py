from __future__ import annotations

import asyncio
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest

from homemaster.browser.contracts import BrowserSessionError
from homemaster.browser.playwright_session import PlaywrightBrowserSession
from homemaster.browser.policy import BrowserPolicy


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        del format, args


@pytest.fixture
def control_origin() -> str:
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


def _find(snapshot, name: str):
    matches = [element for element in snapshot.elements if element.name == name]
    assert len(matches) == 1, [(item.element_id, item.name) for item in snapshot.elements]
    return matches[0]


@pytest.mark.asyncio
async def test_real_controls_readback_stale_refs_and_artifacts(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="controls",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        navigation = await session.navigate(f"{control_origin}/controls.html")
        assert navigation["http_status"] == 200

        tenant_snapshot = await session.inspect({"name": "TenantId"})
        tenant = _find(tenant_snapshot, "TenantId")
        receipt = await session.fill(tenant_snapshot.snapshot_id, tenant.element_id, "tenant-42")
        assert receipt["actual"] == "tenant-42"
        assert receipt["verified"] is True
        with pytest.raises(BrowserSessionError, match="stale_ref"):
            await session.fill(tenant_snapshot.snapshot_id, tenant.element_id, "stale")

        region_snapshot = await session.inspect({"name": "Region"})
        region = _find(region_snapshot, "Region")
        selected = await session.select(
            region_snapshot.snapshot_id, region.element_id, "United States"
        )
        assert selected["actual"] == "us"

        checkbox_snapshot = await session.inspect({"name": "Subscribe"})
        checkbox = _find(checkbox_snapshot, "Subscribe")
        checked = await session.check(checkbox_snapshot.snapshot_id, checkbox.element_id)
        assert checked["actual"] is True
        assert checked["changed"] is True

        checkbox_snapshot = await session.inspect({"name": "Subscribe"})
        checkbox = _find(checkbox_snapshot, "Subscribe")
        unchanged = await session.check(checkbox_snapshot.snapshot_id, checkbox.element_id)
        assert unchanged["actual"] is True
        assert unchanged["changed"] is False

        checkbox_snapshot = await session.inspect({"name": "Subscribe"})
        checkbox = _find(checkbox_snapshot, "Subscribe")
        unchecked = await session.uncheck(checkbox_snapshot.snapshot_id, checkbox.element_id)
        assert unchecked["actual"] is False

        duplicate_snapshot = await session.inspect({"name": "Duplicate"})
        assert duplicate_snapshot.total_matches == 2

        apply_snapshot = await session.inspect({"name": "Apply"})
        apply = _find(apply_snapshot, "Apply")
        click = await session.click(apply_snapshot.snapshot_id, apply.element_id)
        assert click["interaction_verified"] is True
        waited = await session.wait(
            {"kind": "text_present", "value": "Applied tenant-42", "timeout_ms": 2_000}
        )
        assert waited["matched"] is True

        backfill_snapshot = await session.inspect({"name": "Evidence backfill"})
        backfill = _find(backfill_snapshot, "Evidence backfill")
        pasted = await session.backfill(backfill_snapshot.snapshot_id, backfill.element_id)
        assert pasted["mime_type"] == "image/png"
        assert pasted["byte_count"] > 0
        assert len(pasted["sha256"]) == 64
        assert pasted["clipboard_file_count"] == 1
        assert pasted["paste_accepted"] is True
        assert pasted["dom_changed"] is True
        assert pasted["preview_match"] is True
        assert pasted["preview_sha256"] == pasted["sha256"]
        accepted = await session.wait(
            {"kind": "text_present", "value": "Accepted image/png", "timeout_ms": 2_000}
        )
        assert accepted["matched"] is True

        plain_snapshot = await session.inspect({"name": "TenantId"})
        plain = _find(plain_snapshot, "TenantId")
        with pytest.raises(BrowserSessionError) as rejected:
            await session.backfill(plain_snapshot.snapshot_id, plain.element_id)
        assert rejected.value.code == "backfill_rejected"
        assert rejected.value.backend_attempted is True

        broken_snapshot = await session.inspect({"name": "Broken evidence backfill"})
        broken = _find(broken_snapshot, "Broken evidence backfill")
        with pytest.raises(BrowserSessionError) as broken_result:
            await session.backfill(broken_snapshot.snapshot_id, broken.element_id)
        assert broken_result.value.code == "backfill_rejected"
        assert broken_result.value.details["preview_match"] is False

        png = await session.screenshot()
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        await session.aclose()

    assert session.closed is True
    assert session.video_path is not None
    assert session.video_path.is_file() and session.video_path.stat().st_size > 0
    assert session.trace_path.is_file() and session.trace_path.stat().st_size > 0
    rows = [json.loads(line) for line in session.action_log_path.read_text().splitlines()]
    assert rows[0]["operation"] == "session_started"
    assert rows[-1]["operation"] == "session_closed"
    assert [row["operation"] for row in rows].count("fill") == 2


@pytest.mark.asyncio
async def test_infrastructure_timeout_fences_session(tmp_path: Path, control_origin: str) -> None:
    session = PlaywrightBrowserSession(
        session_id="timeout",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        with pytest.raises(BrowserSessionError) as caught:
            await session._execute(
                "test_timeout",
                {},
                lambda: asyncio.sleep(1),
                timeout_ms=10,
                mutating=True,
            )
        assert caught.value.code == "action_timeout"
        assert caught.value.outcome_unknown is True
        assert session.fenced is True
        with pytest.raises(BrowserSessionError) as fenced:
            await session.screenshot()
        assert fenced.value.code == "session_fenced"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_offscreen_target_scrolls_into_view_before_actionability_check(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="offscreen",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/controls.html")
        snapshot = await session.inspect({"name": "Offscreen apply"})
        target = _find(snapshot, "Offscreen apply")

        receipt = await session.click(snapshot.snapshot_id, target.element_id)

        assert receipt["interaction_verified"] is True
        assert await target.handle.inner_text() == "Offscreen applied"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_delayed_cross_origin_navigation_is_blocked_and_fences_session(
    tmp_path: Path, control_origin: str
) -> None:
    request_count = 0

    class _TargetHandler(_QuietHandler):
        def do_GET(self):
            nonlocal request_count
            request_count += 1
            super().do_GET()

    root = Path(__file__).parent / "fixtures"
    handler = functools.partial(_TargetHandler, directory=str(root))
    target_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
    target_thread.start()
    target = f"http://127.0.0.1:{target_server.server_port}/controls.html"
    session = PlaywrightBrowserSession(
        session_id="origin-fence",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        query = urlencode({"target": target})
        await session.navigate(f"{control_origin}/controls.html?{query}")
        snapshot = await session.inspect({"name": "Delayed leave"})
        button = _find(snapshot, "Delayed leave")
        await session.click(snapshot.snapshot_id, button.element_id)
        await asyncio.sleep(0.3)

        assert session.fenced is True
        assert request_count == 0
        with pytest.raises(BrowserSessionError) as fenced:
            await session.inspect({})
        assert fenced.value.code == "session_fenced"
    finally:
        await session.aclose()
        target_server.shutdown()
        target_server.server_close()
        target_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_same_and_cross_origin_iframe_collection_capability_probe(
    tmp_path: Path, control_origin: str
) -> None:
    root = Path(__file__).parent / "fixtures"
    handler = functools.partial(_QuietHandler, directory=str(root))
    cross_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    cross_thread = threading.Thread(target=cross_server.serve_forever, daemon=True)
    cross_thread.start()
    cross_origin = f"http://127.0.0.1:{cross_server.server_port}"
    session = PlaywrightBrowserSession(
        session_id="iframes",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    try:
        await session.start()
        cross_url = quote(f"{cross_origin}/cross-frame.html", safe="")
        await session.navigate(f"{control_origin}/iframes.html?cross={cross_url}")
        await asyncio.sleep(0.2)
        snapshot = await session.inspect({})

        assert len(snapshot.frames) == 3
        frame_urls = {str(frame["url"]) for frame in snapshot.frames}
        assert f"{control_origin}/frame.html" in frame_urls
        assert f"{cross_origin}/cross-frame.html" in frame_urls
        assert {element.name for element in snapshot.elements} == {
            "Framed input",
            "Cross-origin input",
        }
    finally:
        await session.aclose()
        cross_server.shutdown()
        cross_server.server_close()
        cross_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_navigate_waits_for_async_dom_to_be_stable(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="async-render",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        navigation = await session.navigate(f"{control_origin}/async-render.html")
        snapshot = await session.inspect({"name": "Late field"})

        assert navigation["load_state"] == "dom_stable"
        assert [element.name for element in snapshot.elements] == ["Late field"]
    finally:
        await session.aclose()
