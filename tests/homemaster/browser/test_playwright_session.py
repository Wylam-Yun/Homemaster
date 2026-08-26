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
        recovered = await session.fill(
            {"target_ref": tenant.target_ref},
            "tenant-43",
        )
        assert recovered["actual"] == "tenant-43"
        assert recovered["verified"] is True

        masked_snapshot = await session.inspect({"name": "Masked date time"})
        masked = _find(masked_snapshot, "Masked date time")
        masked_receipt = await session.fill(
            masked_snapshot.snapshot_id, masked.element_id, "2026-08-21 21:25:07"
        )
        assert masked_receipt["actual"] == "2026-08-21 21:25:07"
        assert masked_receipt["input_method"] == "keyboard_fallback"
        assert masked_receipt["initial_actual"] == "component-normalized"

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
            {"kind": "text_present", "value": "Applied tenant-43", "timeout_ms": 2_000}
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
    screenshots = sorted((session.video_dir / "screenshots").glob("screenshot-*.png"))
    assert len(screenshots) >= 1
    assert all(path.stat().st_size > 0 for path in screenshots)
    rows = [json.loads(line) for line in session.action_log_path.read_text().splitlines()]
    assert rows[0]["operation"] == "session_started"
    assert rows[-1]["operation"] == "session_closed"
    assert [row["operation"] for row in rows].count("fill") == 3


@pytest.mark.asyncio
async def test_navigate_waits_for_spa_hydration_before_reporting_dom_stable(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="delayed-hydration",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        navigation = await session.navigate(f"{control_origin}/delayed-render.html")
        snapshot = await session.inspect({"name": "Hydrated action"})

        assert navigation["load_state"] == "dom_stable"
        assert [_find(snapshot, "Hydrated action").role] == ["button"]
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_navigate_honors_configured_timeout_beyond_five_seconds(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="slow-hydration",
        policy=BrowserPolicy(
            allowed_origins=(control_origin,),
            navigation_timeout_ms=7_000,
        ),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        navigation = await session.navigate(
            f"{control_origin}/delayed-render.html?delay=5200"
        )
        snapshot = await session.inspect({"name": "Hydrated action"})

        assert navigation["load_state"] == "dom_stable"
        assert [_find(snapshot, "Hydrated action").role] == ["button"]
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_infrastructure_timeout_fences_session(tmp_path: Path, control_origin: str) -> None:
    session = PlaywrightBrowserSession(
        session_id="timeout",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    session._started = True
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


@pytest.mark.asyncio
async def test_readonly_timeout_does_not_fence_session(tmp_path: Path) -> None:
    session = PlaywrightBrowserSession(
        session_id="readonly-timeout",
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_dir=tmp_path / "video",
    )
    session._started = True
    with pytest.raises(BrowserSessionError) as caught:
        await session._execute(
            "inspect",
            {},
            lambda: asyncio.sleep(1),
            timeout_ms=10,
            mutating=False,
        )
    assert caught.value.code == "action_timeout"
    assert caught.value.outcome_unknown is False
    assert session.fenced is False


@pytest.mark.asyncio
async def test_repeated_mutation_executes_again_instead_of_replaying_receipt(
    tmp_path: Path,
) -> None:
    session = PlaywrightBrowserSession(
        session_id="idempotent-mutation",
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_dir=tmp_path / "video",
    )
    session._started = True
    backend_calls = 0

    async def mutation():
        nonlocal backend_calls
        backend_calls += 1
        return {"interaction_verified": True, "backend_sequence": backend_calls}

    first = await session._execute(
        "click",
        {"snapshot_id": "snapshot-1", "element_id": "element-1"},
        mutation,
        timeout_ms=1_000,
        mutating=True,
    )
    second = await session._execute(
        "click",
        {"snapshot_id": "snapshot-1", "element_id": "element-1"},
        mutation,
        timeout_ms=1_000,
        mutating=True,
    )

    assert backend_calls == 2
    assert first == {"interaction_verified": True, "backend_sequence": 1}
    assert second == {"interaction_verified": True, "backend_sequence": 2}
    rows = [json.loads(line) for line in session.action_log_path.read_text().splitlines()]
    assert [row["outcome"] for row in rows] == ["success", "success"]


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
        assert target.obscured is False

        receipt = await session.click(snapshot.snapshot_id, target.element_id)

        assert receipt["interaction_verified"] is True
        assert await target.handle.inner_text() == "Offscreen applied"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_action_refreshes_obscured_state_after_snapshot(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="snapshot-actionability",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/controls.html")
        await session._page.evaluate(
            """() => {
              const target = document.createElement('button');
              target.id = 'covered-action';
              target.textContent = 'Covered action';
              target.style.position = 'fixed';
              target.style.top = '100px';
              target.style.left = '100px';
              target.style.zIndex = '1';
              target.onclick = () => { target.textContent = 'Clicked'; };
              document.body.appendChild(target);
              const cover = document.createElement('div');
              cover.id = 'action-cover';
              cover.style.cssText = [
                'position: fixed', 'inset: 0', 'z-index: 999999',
                'background: rgba(0,0,0,.1)'
              ].join(';');
              document.body.appendChild(cover);
            }"""
        )
        snapshot = await session.inspect({"name": "Covered action"})
        target = _find(snapshot, "Covered action")
        assert target.obscured is True

        await session._page.locator("#action-cover").evaluate("element => element.remove()")
        receipt = await session.click(snapshot.snapshot_id, target.element_id)

        assert receipt["interaction_verified"] is True
        assert await session._page.locator("#covered-action").inner_text() == "Clicked"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_navigation_request_without_frame_is_still_origin_checked(
    tmp_path: Path,
) -> None:
    from playwright.async_api import Error as PlaywrightError

    class _Route:
        aborted = False
        continued = False

        async def abort(self) -> None:
            self.aborted = True

        async def continue_(self) -> None:
            self.continued = True

    class _Request:
        url = "https://outside.example.test/"

        def is_navigation_request(self) -> bool:
            return True

        @property
        def frame(self):
            raise PlaywrightError(
                "Frame for this navigation request is not available, because the request "
                "was issued before the frame is created."
            )

    session = PlaywrightBrowserSession(
        session_id="missing-navigation-frame",
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_dir=tmp_path / "video",
    )
    route = _Route()

    await session._route_request(route, _Request())

    assert route.aborted is True
    assert route.continued is False
    assert session.fenced is True
    assert session._origin_violation is not None
    assert session._origin_violation.code == "origin_not_allowed"


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


@pytest.mark.asyncio
async def test_inspect_discovers_semantic_popup_date_and_time_elements(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="semantic-inspect",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/semantic-controls.html")
        snapshot = await session.inspect({})

        assert {element.name for element in snapshot.elements} >= {
            "Cloud service",
            "Date 2026-08-21",
            "Hour 15",
        }

        date_cell = await session.find({"role": "gridcell", "name": "21", "match": "exact"})
        assert date_cell["matches_n"] == 1
        clicked_date = await session.click(
            {"role": "gridcell", "name": "21", "match": "exact"}
        )
        assert clicked_date["interaction_verified"] is True

        combobox = _find(snapshot, "Cloud service")
        await combobox.handle.click()
        await asyncio.sleep(0.2)
        popup_snapshot = await session.inspect({"role": "option"})

        assert {element.name for element in popup_snapshot.elements} == {
            "VPC",
            "Monitor Agent Service",
        }
        expanded_snapshot = await session.inspect({"name": "Cloud service"})
        expanded_combobox = _find(expanded_snapshot, "Cloud service")
        assert expanded_combobox.expanded is True

        selected = await session.select(
            expanded_snapshot.snapshot_id, expanded_combobox.element_id, "Monitor Agent Service"
        )
        assert selected["actual"] == "Monitor Agent Service"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_inspect_filters_before_applying_the_public_element_limit(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="filtered-limit",
        policy=BrowserPolicy(allowed_origins=(control_origin,), max_elements=5),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/many-controls.html")

        snapshot = await session.inspect({"name": "Target after limit"})

        assert snapshot.total_matches == 1
        assert [_find(snapshot, "Target after limit").role] == ["button"]
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_select_waits_for_async_options_and_verifies_semantic_readback(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="semantic-select",
        policy=BrowserPolicy(
            allowed_origins=(control_origin,),
            action_timeout_ms=2_000,
            wait_timeout_ms=1_000,
        ),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/semantic-controls.html")
        snapshot = await session.inspect({"name": "Cloud service"})
        combobox = _find(snapshot, "Cloud service")

        receipt = await session.select(
            snapshot.snapshot_id,
            combobox.element_id,
            "Monitor Agent Service",
        )

        assert receipt["actual"] == "Monitor Agent Service"
        assert receipt["verified"] is True
        assert receipt["match"] == "exact"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_select_accepts_readonly_aria_combobox_but_fill_rejects_it(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="readonly-semantic-select",
        policy=BrowserPolicy(
            allowed_origins=(control_origin,),
            action_timeout_ms=2_000,
            wait_timeout_ms=1_000,
        ),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/semantic-controls.html")
        target = {
            "role": "combobox",
            "name": "Readonly cloud service",
            "match": "exact",
        }

        receipt = await session.select(target, "Monitor Agent Service")

        assert receipt["actual"] == "Monitor Agent Service"
        assert receipt["verified"] is True
        assert receipt["match"] == "exact"
        with pytest.raises(BrowserSessionError) as caught:
            await session.fill(target, "must not replace the value")
        assert caught.value.code == "target_readonly"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_click_matches_cjk_label_with_framework_inserted_display_spacing(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="cjk-display-spacing",
        policy=BrowserPolicy(allowed_origins=(control_origin,)),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/semantic-controls.html")

        snapshot = await session.inspect(
            {"role": "button", "name": "确认", "match": "exact"}
        )
        assert snapshot.total_matches == 1
        assert snapshot.elements[0].name == "确 认"

        found = await session.find(
            {"role": "button", "name": "确认", "match": "contains"}
        )
        assert found["matches_n"] == 1
        assert found["target"]["name"] == "确 认"

        receipt = await session.click(
            {"role": "button", "name": "确认", "match": "exact"}
        )

        assert receipt["interaction_verified"] is True
        assert await session._page.locator("#cjk-confirm-status").inner_text() == (
            "CJK confirm clicked"
        )
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_select_failure_lists_requested_matches_and_available_options(
    tmp_path: Path, control_origin: str
) -> None:
    session = PlaywrightBrowserSession(
        session_id="semantic-select-failure",
        policy=BrowserPolicy(
            allowed_origins=(control_origin,),
            action_timeout_ms=2_000,
            wait_timeout_ms=1_000,
        ),
        video_dir=tmp_path / "video",
    )
    await session.start()
    try:
        await session.navigate(f"{control_origin}/semantic-controls.html")
        snapshot = await session.inspect({"name": "Cloud service"})
        combobox = _find(snapshot, "Cloud service")

        with pytest.raises(BrowserSessionError) as caught:
            await session.select(snapshot.snapshot_id, combobox.element_id, "Unknown")

        assert caught.value.code == "option_not_unique"
        assert caught.value.details == {
            "requested": "Unknown",
            "matches": [],
            "available": ["VPC", "Monitor Agent Service"],
        }
    finally:
        await session.aclose()
