from __future__ import annotations

import asyncio
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

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
