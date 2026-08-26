"""The single Playwright-owned browser implementation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, TypeVar

from homemaster.browser.contracts import (
    BrowserElement,
    BrowserSessionError,
    BrowserSnapshot,
    Target,
)
from homemaster.browser.inspection import (
    collect_elements,
    current_state,
    filter_elements,
    fingerprint_from_state,
)
from homemaster.browser.opencli_adapter import OpenCLIPageAdapter
from homemaster.browser.policy import BrowserPolicy
from homemaster.browser.targets import (
    SnapshotStore,
    TargetResolutionError,
    resolve_semantic,
)

T = TypeVar("T")

_FORM_STATE_JS = """
() => ({
  forms: Array.from(document.forms).map(form => ({
    id: form.id || null,
    action: form.action || null,
    fields: Array.from(form.elements).map(el => ({
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      name: el.getAttribute('name') || el.id || null,
      label: el.getAttribute('aria-label') || '',
      value: (el.getAttribute('type') || '').toLowerCase() === 'password' ? null : ('value' in el ? String(el.value) : ''),
      checked: 'checked' in el ? Boolean(el.checked) : null,
      disabled: Boolean(el.disabled),
    }))
  }))
})
"""


class PlaywrightBrowserSession:
    def __init__(
        self,
        *,
        session_id: str,
        policy: BrowserPolicy,
        video_dir: Path,
        headless: bool = True,
    ) -> None:
        if not isinstance(policy, BrowserPolicy):
            raise TypeError("policy must be BrowserPolicy")
        self.session_id = session_id
        self.policy = policy
        self.video_dir = Path(video_dir)
        self.headless = headless
        self.generation = 0
        self.video_path: Path | None = None
        self.trace_path = self.video_dir / "browser_trace.zip"
        self.action_log_path = self.video_dir / "browser_actions.jsonl"
        self._snapshots = SnapshotStore(session_id=session_id)
        self._lock = asyncio.Lock()
        self._started = False
        self._closed = False
        self._fenced = False
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._video: Any = None
        self._origin_violation: BrowserSessionError | None = None
        self._screenshot_index = 0
        self._pending_dialog: Any = None
        self._dialog_event: asyncio.Event | None = None
        self._network_events: list[dict[str, object]] = []
        self._network_responses: dict[str, Any] = {}
        self._console_events: list[dict[str, object]] = []
        self._downloads: list[dict[str, object]] = []
        self._tab_refs: dict[str, Any] = {}

    @property
    def fenced(self) -> bool:
        return self._fenced

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        if self._started:
            return
        self.video_dir.mkdir(parents=True, exist_ok=True)
        from playwright.async_api import async_playwright

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                record_video_dir=str(self.video_dir),
                record_video_size={"width": 1280, "height": 720},
            )
            await self._context.route("**/*", self._route_request)
            self._context.on("request", self._capture_request)
            self._context.on("response", self._capture_response)
            self._context.on("download", self._capture_download)
            self._context.on("page", self._capture_page)
            await self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._page = await self._context.new_page()
            self._attach_page(self._page)
            self._video = self._page.video
            self._started = True
            self._tab_refs["tab-1"] = self._page
            self._write_event("session_started", {"headless": self.headless})
        except BaseException:
            await self._cleanup_started_resources(save_artifacts=False)
            raise

    async def navigate(self, url: str, **kwargs: object) -> Mapping[str, object]:
        requested = self.policy.validate_initial_url(url)

        async def action() -> Mapping[str, object]:
            before = self._page.url
            response = await self._page.goto(
                requested,
                wait_until=str(kwargs.get("wait_until", "domcontentloaded")),
                timeout=int(kwargs.get("timeout_ms") or self.policy.navigation_timeout_ms),
            )
            status = response.status if response is not None else None
            if status is not None and status >= 400:
                raise BrowserSessionError(
                    "navigation_http_error",
                    f"navigation returned HTTP {status}",
                    details={"status": status},
                    backend_attempted=True,
                )
            stable = await self._wait_for_dom_stable()
            if not stable:
                raise BrowserSessionError(
                    "navigation_unstable",
                    "page DOM did not become stable after navigation",
                    backend_attempted=True,
                )
            self.generation += 1
            self._snapshots.invalidate()
            final_url = self._page.url
            self.policy.validate_final_url(final_url)
            return {
                "requested_url": requested,
                "final_url": final_url,
                "title": await self._page.title(),
                "redirected": final_url != requested,
                "load_state": "dom_stable",
                "http_status": status,
                "page_generation": self.generation,
                "url_before": before,
            }

        return await self._execute(
            "navigate",
            {"url": requested},
            action,
            timeout_ms=self.policy.navigation_timeout_ms + 1_000,
            mutating=True,
        )

    async def inspect(self, filters: Mapping[str, object]) -> BrowserSnapshot:
        requested_limit = filters.get("limit", self.policy.max_elements)
        limit = min(int(requested_limit), self.policy.max_elements)

        async def action() -> BrowserSnapshot:
            view = str(filters.get("view", "hybrid"))
            if view not in {"dom", "ax", "hybrid", "frames"}:
                raise BrowserSessionError(
                    "invalid_argument", "view must be dom, ax, hybrid, or frames"
                )
            collect_filters = dict(filters)
            scope_value = collect_filters.pop("scope", None)
            elements, total, frames = await collect_elements(
                self._page,
                limit=self.policy.max_elements,
                filters=collect_filters,
            )
            selected = filter_elements(elements, collect_filters)
            scope_element = None
            if isinstance(scope_value, (Mapping, str)):
                scope_target = scope_value
                if isinstance(scope_value, Mapping) and filters.get("frame_ref") is not None:
                    scope_target = dict(scope_value)
                    scope_target.setdefault("frame_ref", filters["frame_ref"])
                scope_element, _, _ = await self._resolve_target(scope_target, writable=False)
                selected = [
                    element
                    for element in selected
                    if await element.handle.evaluate(
                        "(el, root) => el !== root && root.contains(el)", scope_element.handle
                    )
                ]
                total = len(selected)
            selected_total = total
            selected = selected[:limit]
            frame = self._frame_for_ref(filters.get("frame_ref"))
            adapter = OpenCLIPageAdapter(frame)
            dom_text = ""
            ax_text = ""
            if scope_element is not None and view in {"dom", "hybrid"}:
                dom_text = str(await scope_element.handle.inner_text())
            elif view in {"dom", "hybrid"}:
                dom_text = await adapter.dom_snapshot(
                    interactive_only=bool(filters.get("interactive_only", False))
                )
            if view in {"ax", "hybrid"}:
                if filters.get("frame_ref") is None:
                    ax_text = await adapter.ax_snapshot(
                        interactive_only=bool(filters.get("interactive_only", False)),
                        root=scope_element.handle if scope_element is not None else None,
                    )
            if view == "dom":
                text = dom_text
            elif view == "ax":
                text = ax_text
            elif view == "frames":
                text = ""
            else:
                text = f"{dom_text}\n\n{ax_text}"
            text = _redact_snapshot_text(text)[: self.policy.max_text_chars]
            diff: dict[str, object] = {}
            diff_from = filters.get("diff_from")
            if isinstance(diff_from, str) and diff_from:
                try:
                    previous = self._snapshots.get(diff_from)
                except TargetResolutionError as exc:
                    raise BrowserSessionError(exc.code, str(exc), details=exc.details) from exc
                previous_lines = set(previous.text.splitlines())
                current_lines = set(text.splitlines())
                diff = {
                    "from_snapshot_id": diff_from,
                    "added": sorted(current_lines - previous_lines)[:100],
                    "removed": sorted(previous_lines - current_lines)[:100],
                    "truncated": len(current_lines - previous_lines) > 100
                    or len(previous_lines - current_lines) > 100,
                }
            return self._snapshots.replace(
                generation=self.generation,
                url=self._page.url,
                title=await self._page.title(),
                text=text,
                elements=selected,
                total_matches=selected_total
                if any(filters.get(key) for key in ("role", "name", "label", "text"))
                else total,
                truncated=selected_total > len(selected),
                frames=frames,
                view=view,
                created_at_ms=int(time.time() * 1000),
                diff=diff,
            )

        return await self._execute(
            "inspect",
            dict(filters),
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=False,
        )

    async def fill(
        self,
        snapshot_id: str | Mapping[str, object],
        element_id: str,
        value: str | None = None,
    ) -> Mapping[str, object]:
        modern_target = value is None
        target_value: Mapping[str, object] | str = snapshot_id
        desired_value = element_id if modern_target else str(value)

        async def action() -> Mapping[str, object]:
            if modern_target:
                element, state, match_level = await self._resolve_target(
                    target_value, writable=True, require_editable=True
                )
            else:
                element, state = await self._target(
                    str(snapshot_id), element_id, require_editable=True
                )
                match_level = "exact"
            if not bool(state["editable"]) or element.control_type not in {
                "input",
                "text",
                "email",
                "number",
                "search",
                "tel",
                "url",
                "date",
                "time",
                "datetime-local",
                "month",
                "week",
                "textarea",
                "contenteditable",
            }:
                raise BrowserSessionError(
                    "unsupported_control",
                    "target is not editable",
                    hint="This control cannot accept a direct value assignment. Use browser_select for options, browser_click for toggles, or pick an editable target via browser_inspect.",
                )
            await element.handle.fill(desired_value, timeout=self.policy.action_timeout_ms)
            actual_state = await current_state(element)
            initial_actual = str(actual_state.get("value") or "")
            actual = initial_actual
            input_method = "fill"
            if actual != desired_value:
                await element.handle.click(timeout=self.policy.action_timeout_ms)
                await self._page.wait_for_timeout(50)
                await self._page.keyboard.press("ControlOrMeta+A")
                if desired_value:
                    await self._page.keyboard.type(desired_value, delay=1)
                else:
                    await self._page.keyboard.press("Backspace")
                deadline = time.monotonic() + min(
                    self.policy.action_timeout_ms / 1000,
                    0.5,
                )
                while actual != desired_value and time.monotonic() < deadline:
                    await self._page.wait_for_timeout(25)
                    actual_state = await current_state(element)
                    actual = str(actual_state.get("value") or "")
                input_method = "keyboard_fallback"
            self._snapshots.invalidate()
            if actual != desired_value:
                raise BrowserSessionError(
                    "readback_mismatch",
                    "filled value did not match DOM readback",
                    details={
                        "expected": desired_value,
                        "initial_actual": initial_actual,
                        "actual": actual,
                    },
                    backend_attempted=True,
                )
            return self._receipt(
                element,
                expected=desired_value,
                actual=actual,
                verified=True,
                input_method=input_method,
                initial_actual=initial_actual,
                match=match_level,
            )

        return await self._execute(
            "fill",
            (
                {"target": dict(target_value), "value": desired_value}
                if modern_target
                else {"snapshot_id": snapshot_id, "element_id": element_id, "value": desired_value}
            ),
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def select(
        self,
        snapshot_id: str | Mapping[str, object],
        element_id: object,
        option: str | None = None,
        **kwargs: object,
    ) -> Mapping[str, object]:
        modern_target = option is None
        target_value: Mapping[str, object] | str = snapshot_id
        requested_options = kwargs.get("options")
        if requested_options is None:
            requested_options = [element_id if modern_target else option]
        if not isinstance(requested_options, (list, tuple)) or not requested_options:
            raise BrowserSessionError("invalid_argument", "select requires option or options")
        option_values = [str(value) for value in requested_options]
        match_mode = str(kwargs.get("match", "label"))
        if match_mode not in {"label", "value"}:
            raise BrowserSessionError("invalid_argument", "select match must be label or value")

        async def action() -> Mapping[str, object]:
            if modern_target:
                element, state, match_level = await self._resolve_target(
                    target_value, writable=True
                )
            else:
                element, state = await self._target(str(snapshot_id), str(element_id))
                match_level = "exact"
            if element.tag == "select":
                chosen_items: list[Mapping[str, object]] = []
                for requested in option_values:
                    candidates = [
                        item
                        for item in element.options
                        if str(item.get(match_mode, "")).casefold() == requested.casefold()
                        and not bool(item.get("disabled", False))
                    ]
                    unique = {
                        (str(item.get("label", "")), str(item.get("value", ""))): item
                        for item in candidates
                    }
                    if len(unique) != 1:
                        raise BrowserSessionError(
                            "option_not_unique",
                            "option must match exactly one label or value",
                            details={"requested": requested, "available": [dict(item) for item in element.options[:20]]},
                        )
                    chosen_items.append(next(iter(unique.values())))
                if not bool(element.compound.get("multiple", False)) and len(chosen_items) != 1:
                    raise BrowserSessionError(
                        "unsupported_control",
                        "select does not accept multiple options",
                    )
                values = [str(item["value"]) for item in chosen_items]
                await element.handle.select_option(value=values, timeout=self.policy.action_timeout_ms)
                actual_state = await current_state(element)
                actual = (
                    list(actual_state.get("selectedValues", []))
                    if len(values) > 1
                    else str(actual_state.get("value", ""))
                )
                expected = values[0] if len(values) == 1 else values
                if actual != expected:
                    raise BrowserSessionError(
                        "readback_mismatch",
                        "selected option state did not match DOM readback",
                        details={"expected": expected, "actual": actual},
                        backend_attempted=True,
                    )
            elif element.role == "combobox":
                if state.get("expanded") is not True:
                    await element.handle.click(timeout=self.policy.action_timeout_ms)
                available = await self._combobox_options(element, state)
                matches: list[tuple[Any, dict[str, object]]] = []
                for requested in option_values:
                    current_matches = [
                        (handle, option_state)
                        for handle, option_state in available
                        if str(option_state.get("name", "")).casefold() == requested.casefold()
                        and bool(option_state.get("enabled", True))
                    ]
                    if len(current_matches) != 1:
                        raise BrowserSessionError(
                            "option_not_unique",
                            "ARIA option must match exactly one accessible name",
                            details={"requested": requested, "matches": [str(option_state.get("name", "")) for _, option_state in current_matches], "available": [str(option_state.get("name", "")) for _, option_state in available[:20]]},
                            backend_attempted=True,
                        )
                    matches.extend(current_matches)
                if len(matches) != 1:
                    raise BrowserSessionError(
                        "unsupported_control", "ARIA combobox does not accept multiple options"
                    )
                await matches[0][0].click(timeout=self.policy.action_timeout_ms)
                actual_state = await current_state(element)
                actual = str(actual_state.get("value") or actual_state.get("text") or "")
                expected = option_values[0]
                if option_values[0].casefold() not in actual.casefold():
                    actual = await self._verify_combobox_selection(element, option_values[0])
            else:
                raise BrowserSessionError(
                    "unsupported_control", "target is not a select or combobox"
                )
            self._snapshots.invalidate()
            return self._receipt(
                element,
                expected=expected,
                actual=actual,
                verified=True,
                match=match_level,
            )

        return await self._execute(
            "select",
            (
                {"target": dict(target_value), "option": option_values[0], "options": option_values, "match": match_mode}
                if modern_target
                else {"snapshot_id": snapshot_id, "element_id": element_id, "option": option, "match": match_mode}
            ),
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def _combobox_options(
        self,
        element: BrowserElement,
        state: Mapping[str, object] | None = None,
    ) -> list[tuple[Any, dict[str, object]]]:
        current = dict(state or await current_state(element))
        controls = str(current.get("ariaControls") or "")
        frame = await element.handle.owner_frame()
        if frame is None:
            raise BrowserSessionError(
                "option_discovery_failed",
                "combobox is not attached to a frame",
                backend_attempted=True,
            )
        selector = '[role="option"]'
        if controls:
            selector = f'[id={json.dumps(controls)}] [role="option"]'
        deadline = time.monotonic() + self.policy.wait_timeout_ms / 1000
        while True:
            available: list[tuple[Any, dict[str, object]]] = []
            for handle in await frame.query_selector_all(selector):
                option_state = dict(await current_state(_temporary_element(handle)))
                if bool(option_state.get("visible")):
                    available.append((handle, option_state))
            if available or time.monotonic() >= deadline:
                return available
            await self._page.wait_for_timeout(50)

    async def _verify_combobox_selection(self, element: BrowserElement, option: str) -> str:
        await element.handle.click(timeout=self.policy.action_timeout_ms)
        available = await self._combobox_options(element)
        matches = [
            option_state
            for _, option_state in available
            if str(option_state.get("name", "")).casefold() == option.casefold()
        ]
        try:
            if len(matches) == 1 and matches[0].get("selected") is True:
                return str(matches[0].get("name", ""))
            raise BrowserSessionError(
                "readback_mismatch",
                "selected option did not match semantic combobox readback",
                details={
                    "expected": option,
                    "selected": [
                        str(option_state.get("name", ""))
                        for _, option_state in available
                        if option_state.get("selected") is True
                    ],
                },
                backend_attempted=True,
            )
        finally:
            await element.handle.press("Escape", timeout=self.policy.action_timeout_ms)

    async def check(
        self, snapshot_id: str | Mapping[str, object], element_id: str | None = None
    ) -> Mapping[str, object]:
        if element_id is None:
            return await self._set_checked_target(snapshot_id, desired=True)
        return await self._set_checked(str(snapshot_id), element_id, desired=True)

    async def uncheck(
        self, snapshot_id: str | Mapping[str, object], element_id: str | None = None
    ) -> Mapping[str, object]:
        if element_id is None:
            return await self._set_checked_target(snapshot_id, desired=False)
        return await self._set_checked(str(snapshot_id), element_id, desired=False)

    async def click(
        self,
        snapshot_id: str | Mapping[str, object],
        element_id: str | None = None,
        **kwargs: object,
    ) -> Mapping[str, object]:
        modern_target = element_id is None
        target_value: Mapping[str, object] | str = snapshot_id
        click_count = int(kwargs.get("click_count", 1))
        if click_count not in {1, 2}:
            raise BrowserSessionError("invalid_argument", "click_count must be 1 or 2")

        async def action() -> Mapping[str, object]:
            if modern_target:
                element, _, match_level = await self._resolve_target(target_value, writable=True)
            else:
                element, _ = await self._target(str(snapshot_id), str(element_id))
                match_level = "exact"
            before_url = self._page.url
            before_hash = await self._dom_hash()
            pages_before = set(self._context.pages)
            await element.handle.click(
                click_count=click_count, timeout=self.policy.action_timeout_ms
            )
            await self._page.wait_for_timeout(50)
            popup_pages = [page for page in self._context.pages if page not in pages_before]
            popup_page = popup_pages[-1] if popup_pages else None
            popup_ref = self._tab_ref_for_page(popup_page) if popup_page is not None else None
            after_url = self._page.url
            self.policy.validate_final_url(after_url)
            after_hash = await self._dom_hash()
            if after_url != before_url:
                self.generation += 1
            self._snapshots.invalidate()
            return self._receipt(
                element,
                interaction_verified=True,
                url_before=before_url,
                url_after=after_url,
                page_generation=self.generation,
                dom_changed=before_hash != after_hash,
                click_count=click_count,
                match=match_level,
                popup_opened=popup_page is not None,
                popup_tab_ref=popup_ref,
            )

        return await self._execute(
            "click",
            (
                {"target": dict(target_value), "click_count": click_count}
                if modern_target
                else {
                    "snapshot_id": snapshot_id,
                    "element_id": element_id,
                    "click_count": click_count,
                }
            ),
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def backfill(
        self,
        snapshot_id: str | Mapping[str, object],
        element_id: str | None = None,
        **kwargs: object,
    ) -> Mapping[str, object]:
        modern_target = element_id is None
        target_value: Mapping[str, object] | str = snapshot_id

        async def action() -> Mapping[str, object]:
            if modern_target:
                element, state, match_level = await self._resolve_target(
                    target_value, writable=True, require_editable=True
                )
            else:
                element, state = await self._target(
                    str(snapshot_id), str(element_id), require_editable=True
                )
                match_level = "exact"
            if not bool(state["editable"]) or element.control_type not in {
                "input",
                "text",
                "textarea",
                "contenteditable",
            }:
                raise BrowserSessionError(
                    "unsupported_control", "target cannot receive a clipboard backfill"
                )
            png = await self._capture_png(
                full_page=bool(kwargs.get("full_page", False)),
                width=kwargs.get("width"),
                height=kwargs.get("height"),
            )
            png_base64 = base64.b64encode(png).decode("ascii")
            expected_data_url = f"data:image/png;base64,{png_base64}"
            expected_sha256 = hashlib.sha256(png).hexdigest()
            source_width, source_height = _png_dimensions(png)
            before_hash = await self._dom_hash()
            paste = dict(
                await element.handle.evaluate(
                    """
                    (el, payload) => {
                      const binary = atob(payload.base64);
                      const bytes = new Uint8Array(binary.length);
                      for (let index = 0; index < binary.length; index += 1) {
                        bytes[index] = binary.charCodeAt(index);
                      }
                      const file = new File([bytes], payload.fileName, {
                        type: payload.mimeType,
                      });
                      const clipboard = new DataTransfer();
                      clipboard.items.add(file);
                      const event = new ClipboardEvent('paste', {
                        bubbles: true,
                        cancelable: true,
                        clipboardData: clipboard,
                      });
                      el.focus();
                      const dispatchReturned = el.dispatchEvent(event);
                      return {
                        default_prevented: event.defaultPrevented,
                        dispatch_returned: dispatchReturned,
                        clipboard_file_count: event.clipboardData.files.length,
                        clipboard_item_count: event.clipboardData.items.length,
                      };
                    }
                    """,
                    {
                        "base64": png_base64,
                        "fileName": "homemaster-browser-backfill.png",
                        "mimeType": "image/png",
                    },
                )
            )
            self._snapshots.invalidate()
            deadline = time.monotonic() + self.policy.action_timeout_ms / 1000
            after_hash = await self._dom_hash()
            preview_match = await self._has_exact_image_preview(expected_data_url)
            while (after_hash == before_hash or not preview_match) and time.monotonic() < deadline:
                await self._page.wait_for_timeout(25)
                after_hash = await self._dom_hash()
                preview_match = await self._has_exact_image_preview(expected_data_url)
            paste_accepted = bool(paste.get("default_prevented"))
            dom_changed = before_hash != after_hash
            if not paste_accepted or not dom_changed or not preview_match:
                raise BrowserSessionError(
                    "backfill_rejected",
                    "page did not render the exact clipboard image backfill",
                    details={
                        "paste_accepted": paste_accepted,
                        "dom_changed": dom_changed,
                        "preview_match": preview_match,
                        "clipboard_file_count": int(paste.get("clipboard_file_count", 0)),
                    },
                    backend_attempted=True,
                )
            return self._receipt(
                element,
                mime_type="image/png",
                byte_count=len(png),
                sha256=expected_sha256,
                source_width=source_width,
                source_height=source_height,
                preview_match=True,
                preview_sha256=expected_sha256,
                clipboard_file_count=int(paste["clipboard_file_count"]),
                clipboard_item_count=int(paste["clipboard_item_count"]),
                paste_accepted=True,
                dom_changed=True,
                match=match_level,
            )

        return await self._execute(
            "backfill",
            (
                {"target": dict(target_value), **kwargs}
                if modern_target
                else {"snapshot_id": snapshot_id, "element_id": element_id, **kwargs}
            ),
            action,
            timeout_ms=self.policy.action_timeout_ms + 1_000,
            mutating=True,
        )

    async def wait(self, condition: Mapping[str, object]) -> Mapping[str, object]:
        # Accept one accidental tool-envelope wrapper so a malformed model call
        # produces a useful bounded wait instead of an opaque empty-kind error.
        nested_condition = condition.get("condition")
        if "kind" not in condition and isinstance(nested_condition, Mapping):
            condition = nested_condition
        kind = str(condition.get("kind", ""))
        if not kind:
            raise BrowserSessionError(
                "invalid_argument",
                "wait requires condition.kind; do not wrap condition more than once",
            )
        timeout_ms = min(
            int(condition.get("timeout_ms", self.policy.wait_timeout_ms)),
            self.policy.wait_timeout_ms,
        )

        async def action() -> Mapping[str, object]:
            deadline = time.monotonic() + timeout_ms / 1000
            last_state: dict[str, object] = {}
            stable_hash = ""
            stable_since = time.monotonic()
            while True:
                if kind == "time":
                    elapsed_ms = int((time.monotonic() - (deadline - timeout_ms / 1000)) * 1000)
                    requested_ms = max(
                        0, int(condition.get("value", condition.get("duration_ms", 0)) or 0)
                    )
                    if elapsed_ms >= requested_ms:
                        return {
                            "condition": kind,
                            "matched": True,
                            "elapsed_ms": elapsed_ms,
                            "url": self._page.url,
                        }
                matched, last_state, stable_hash, stable_since = await self._condition_state(
                    condition,
                    kind=kind,
                    stable_hash=stable_hash,
                    stable_since=stable_since,
                )
                if matched:
                    return {
                        "condition": kind,
                        "matched": True,
                        "last_state": last_state,
                        "url": self._page.url,
                    }
                if time.monotonic() >= deadline:
                    raise BrowserSessionError(
                        "wait_timeout",
                        "browser condition did not match before timeout",
                        details={"condition": kind, "last_state": last_state},
                        backend_attempted=True,
                    )
                await self._page.wait_for_timeout(50)

        return await self._execute(
            "wait",
            {"condition": dict(condition)},
            action,
            timeout_ms=timeout_ms + 1_000,
            mutating=False,
        )

    async def history(self, action: str, **kwargs: object) -> Mapping[str, object]:
        if action not in {"back", "forward", "reload"}:
            raise BrowserSessionError(
                "invalid_argument", "history action must be back, forward, or reload"
            )

        async def operation() -> Mapping[str, object]:
            before = self._page.url
            response = None
            wait_until = str(kwargs.get("wait_until", "domcontentloaded"))
            timeout_ms = int(kwargs.get("timeout_ms") or self.policy.navigation_timeout_ms)
            if action == "back":
                response = await self._page.go_back(timeout=timeout_ms, wait_until=wait_until)
            elif action == "forward":
                response = await self._page.go_forward(timeout=timeout_ms, wait_until=wait_until)
            else:
                response = await self._page.reload(timeout=timeout_ms, wait_until=wait_until)
            after = self._page.url
            self.policy.validate_final_url(after)
            self.generation += 1
            self._snapshots.invalidate()
            return {
                "action": action,
                "changed": before != after or action == "reload",
                "url_before": before,
                "url_after": after,
                "title": await self._page.title(),
                "http_status": response.status if response is not None else None,
                "page_generation": self.generation,
            }

        return await self._execute(
            "history",
            {"action": action, **kwargs},
            operation,
            timeout_ms=int(kwargs.get("timeout_ms") or self.policy.navigation_timeout_ms) + 1000,
            mutating=True,
        )

    async def find(self, query: Mapping[str, object]) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            css = query.get("css")
            if css is not None:
                if not isinstance(css, str) or not css.strip():
                    raise BrowserSessionError(
                        "invalid_selector", "css must be a non-empty selector"
                    )
                try:
                    handles = await self._page.query_selector_all(css)
                except Exception as exc:
                    raise BrowserSessionError(
                        "invalid_selector", "invalid CSS selector", details={"css": css}
                    ) from exc
                candidates: list[dict[str, object]] = []
                for index, handle in enumerate(handles[:200]):
                    state = dict(await current_state(_temporary_element(handle)))
                    if not bool(state.get("visible")):
                        continue
                    candidates.append(
                        {
                            "index": index,
                            "tag": state.get("tag"),
                            "role": state.get("role"),
                            "accessible_name": state.get("name"),
                            "text": str(state.get("text", ""))[:200],
                            "visible": True,
                        }
                    )
                if not candidates:
                    raise BrowserSessionError(
                        "target_not_found",
                        "CSS selector matched no visible element",
                        details={"css": css, "matches_n": 0},
                        hint="The selector matched nothing visible. Check the selector syntax, drop the visibility assumption, or use a semantic find (role/name/text) - or run browser_inspect to see what is actually on the page.",
                    )
                if len(candidates) > 1 and query.get("nth") is None:
                    raise BrowserSessionError(
                        "target_ambiguous",
                        "CSS selector matched multiple visible elements",
                        details={
                            "css": css,
                            "matches_n": len(candidates),
                            "candidates": candidates[:10],
                        },
                        hint="Several visible elements matched. Add nth (0-based) or use a more specific CSS selector, then retry.",
                    )
                index = int(query.get("nth", 0))
                if index < 0 or index >= len(candidates):
                    raise BrowserSessionError(
                        "target_not_found",
                        "CSS candidate index is out of range",
                        details={"css": css, "nth": index, "matches_n": len(candidates)},
                        hint="The requested nth index is outside the visible match count. Use an index from 0 through matches_n - 1, or narrow the selector.",
                    )
                return {
                    "matches_n": len(candidates),
                    "entries": candidates,
                    "target": candidates[index],
                    "read_only": True,
                }
            # Keep semantic discovery narrow.  Picker popups can contain hundreds of
            # transient nodes; collecting the whole interactive tree before applying
            # a role/name/text filter makes re-render races likely and needlessly
            # turns a targeted find into an infrastructure-timeout operation.
            collection_filters = {
                key: query[key]
                for key in ("role", "name", "label", "text", "testid", "frame_ref", "match")
                if key in query
            }
            elements, total, frames = await collect_elements(
                self._page,
                limit=min(
                    int(query.get("limit", self.policy.max_elements)), self.policy.max_elements
                ),
                filters=collection_filters,
            )
            filters = dict(query)
            filters.setdefault("match", "exact")
            selected = filter_elements(elements, filters)
            candidates = [
                element.to_public_dict() for element in selected[: int(query.get("limit", 50))]
            ]
            if not selected:
                raise BrowserSessionError(
                    "target_not_found",
                    "no element matched the requested query",
                    details={"requested": dict(query), "matches_n": 0, "candidates": []},
                )
            if (
                str(query.get("match", "exact")) != "regex"
                and len(selected) > 1
                and query.get("nth") is None
            ):
                raise BrowserSessionError(
                    "target_ambiguous",
                    "query matched multiple elements",
                    details={
                        "requested": dict(query),
                        "matches_n": len(selected),
                        "candidates": candidates,
                    },
                )
            index = int(query["nth"]) if query.get("nth") is not None else 0
            if index < 0 or index >= len(selected):
                raise BrowserSessionError(
                    "target_not_found",
                    "nth candidate is out of range",
                    details={"matches_n": len(selected), "nth": index, "candidates": candidates},
                )
            snapshot = self._snapshots.replace(
                generation=self.generation,
                url=self._page.url,
                title=await self._page.title(),
                elements=selected,
                total_matches=len(selected),
                truncated=total > len(elements),
                frames=frames,
                view="find",
                created_at_ms=int(time.time() * 1000),
            )
            retained = snapshot.elements
            return {
                "matches_n": len(retained),
                "entries": [element.to_public_dict() for element in retained],
                "target": retained[index].to_public_dict(),
                "frames": frames,
            }

        return await self._execute(
            "find", dict(query), operation, timeout_ms=self.policy.action_timeout_ms, mutating=False
        )

    async def read(self, query: Mapping[str, object]) -> Mapping[str, object]:
        kind = str(query.get("kind", ""))

        async def operation() -> Mapping[str, object]:
            frame = self._frame_for_ref(query.get("frame_ref"))
            adapter = OpenCLIPageAdapter(frame)
            if kind == "title":
                value = (
                    await self._page.title()
                    if frame is self._page.main_frame
                    else await frame.evaluate("() => document.title")
                )
                return {"kind": kind, "value": value}
            if kind == "url":
                return {"kind": kind, "value": frame.url}
            if kind == "form_state":
                return {"kind": kind, "value": await adapter.form_state()}
            scope = query.get("scope")
            target = query.get("target")
            html_root = target if target is not None else scope
            if kind == "html":
                root_handle = None
                match_level = None
                target_ref = None
                if isinstance(html_root, (Mapping, str)):
                    scoped_target = html_root
                    if isinstance(html_root, Mapping) and query.get("frame_ref") is not None:
                        scoped_target = dict(html_root)
                        scoped_target.setdefault("frame_ref", query["frame_ref"])
                    element, _, match_level = await self._resolve_target(
                        scoped_target, writable=False
                    )
                    root_handle = element.handle
                    target_ref = element.target_ref
                if str(query.get("format", "html")) == "tree":
                    value = await adapter.html_tree(
                        root_handle,
                        depth=min(max(0, int(query.get("max_depth", 8))), 20),
                        children_max=min(max(1, int(query.get("children_max", 100))), 500),
                        text_max=min(max(1, int(query.get("text_max", 500))), 5000),
                    )
                else:
                    raw = (
                        str(await root_handle.evaluate("el => el.outerHTML"))
                        if root_handle is not None
                        else await frame.content()
                    )
                    value = raw[: min(int(query.get("max_chars", 20_000)), 100_000)]
                result = {"kind": kind, "value": value, "matches_n": 1}
                if target_ref is not None:
                    result.update({"target_ref": target_ref, "match": match_level})
                return result
            if not isinstance(target, (Mapping, str)):
                raise BrowserSessionError("invalid_argument", "this read kind requires target")
            element, state, level = await self._resolve_target(target, writable=False)
            if kind == "text":
                value = str(state.get("text", ""))
            elif kind == "value":
                value = state.get("value")
            elif kind == "attributes":
                value = await element.handle.evaluate(
                    "el => Array.from(el.attributes).reduce((out, attr) => (out[attr.name] = attr.value, out), {})"
                )
            elif kind == "html":
                value = str(await element.handle.evaluate("el => el.outerHTML"))[
                    : int(query.get("max_chars", 20000))
                ]
            else:
                raise BrowserSessionError("invalid_argument", f"unsupported read kind: {kind}")
            return {"kind": kind, "value": value, "target_ref": element.target_ref, "match": level}

        return await self._execute(
            "read", dict(query), operation, timeout_ms=self.policy.action_timeout_ms, mutating=False
        )

    async def extract(self, query: Mapping[str, object]) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            frame = self._frame_for_ref(query.get("frame_ref"))
            root_handle = None
            scope = query.get("scope")
            if isinstance(scope, (Mapping, str)):
                scoped_target = scope
                if isinstance(scope, Mapping) and query.get("frame_ref") is not None:
                    scoped_target = dict(scope)
                    scoped_target.setdefault("frame_ref", query["frame_ref"])
                element, _, _ = await self._resolve_target(scoped_target, writable=False)
                root_handle = element.handle
            extracted = await OpenCLIPageAdapter(frame).cleaned_html(root_handle)
            if extracted.get("invalidSelector"):
                raise BrowserSessionError(
                    "invalid_selector", str(extracted.get("reason", "invalid scope"))
                )
            if extracted.get("notFound"):
                raise BrowserSessionError("target_not_found", "no readable extraction root")
            text = _html_to_markdown(str(extracted.get("html", "")))
            start = max(0, int(query.get("start_char", 0)))
            size = min(max(1, int(query.get("chunk_size", 12000))), self.policy.max_text_chars)
            if start > len(text):
                raise BrowserSessionError(
                    "cursor_out_of_range", "extract cursor is past page content"
                )
            end = min(len(text), start + size)
            return {
                "url": frame.url,
                "title": str(extracted.get("title", "")),
                "markdown": text[start:end],
                "start_char": start,
                "next_start_char": end if end < len(text) else None,
                "has_more": end < len(text),
                "source": "opencli-cleaned-html",
            }

        return await self._execute(
            "extract",
            dict(query),
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=False,
        )

    async def type(
        self, target: Mapping[str, object] | str, text: str, **kwargs: object
    ) -> Mapping[str, object]:
        mode = str(kwargs.get("mode", "replace"))
        if mode not in {"replace", "append"}:
            raise BrowserSessionError("invalid_argument", "type mode must be replace or append")

        async def operation() -> Mapping[str, object]:
            element, state, level = await self._resolve_target(
                target, writable=True, require_editable=True
            )
            if not bool(state.get("editable")):
                raise BrowserSessionError(
                    "unsupported_control",
                    "target is not editable",
                    hint="This control cannot accept a direct value assignment. Use browser_select for options, browser_click for toggles, or pick an editable target via browser_inspect.",
                )
            await element.handle.click(timeout=self.policy.action_timeout_ms)
            if mode == "replace":
                await self._page.keyboard.press("ControlOrMeta+A")
            await self._page.keyboard.type(text, delay=int(kwargs.get("delay_ms", 0)))
            actual = str((await current_state(element)).get("value") or "")
            expected = text if mode == "replace" else str(state.get("value") or "") + text
            if actual != expected:
                raise BrowserSessionError(
                    "readback_mismatch",
                    "typed value did not match DOM readback",
                    details={"expected": expected, "actual": actual},
                    backend_attempted=True,
                )
            self._snapshots.invalidate()
            return self._receipt(
                element,
                previous_value=state.get("value"),
                mode=mode,
                text=text,
                actual=actual,
                match=level,
            )

        return await self._execute(
            "type",
            {"target": target, "text": text, **kwargs},
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def hover(
        self, target: Mapping[str, object] | str, **kwargs: object
    ) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            element, _, level = await self._resolve_target(target, writable=True)
            await element.handle.hover(timeout=self.policy.action_timeout_ms)
            if kwargs.get("duration_ms"):
                await self._page.wait_for_timeout(min(int(kwargs["duration_ms"]), 5000))
            hovered = bool(await element.handle.evaluate("el => el.matches(':hover')"))
            if not hovered:
                raise BrowserSessionError(
                    "readback_mismatch", "target is not hovered", backend_attempted=True
                )
            postcondition = await self._verify_expect(kwargs.get("expect"))
            return self._receipt(
                element, hovered=True, postcondition=postcondition, match=level
            )

        return await self._execute(
            "hover",
            {"target": target, **kwargs},
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def focus(self, target: Mapping[str, object] | str) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            element, _, level = await self._resolve_target(target, writable=True)
            await element.handle.focus()
            active = await self._page.evaluate(
                "el => document.activeElement === el", element.handle
            )
            if not active:
                raise BrowserSessionError(
                    "readback_mismatch", "target did not become active", backend_attempted=True
                )
            return self._receipt(element, focused=True, match=level)

        return await self._execute(
            "focus",
            {"target": target},
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def press(
        self, key: str, target: Mapping[str, object] | str | None = None, **kwargs: object
    ) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            element = None
            level = "focused"
            if target is not None:
                element, _, level = await self._resolve_target(target, writable=True)
                await element.handle.focus()
            await self._page.keyboard.press(key)
            postcondition = await self._verify_expect(kwargs.get("expect"))
            return {
                "key": key,
                "target_ref": element.target_ref if element else None,
                "match": level,
                "url": self._page.url,
                "postcondition": postcondition,
            }

        return await self._execute(
            "press",
            {"key": key, "target": target, **kwargs},
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def scroll(self, query: Mapping[str, object]) -> Mapping[str, object]:
        mode = str(query.get("mode", "by"))

        async def operation() -> Mapping[str, object]:
            before = await self._page.evaluate(
                "() => ({x: scrollX, y: scrollY, h: document.documentElement.scrollHeight})"
            )
            if mode == "into_view":
                target = query.get("target")
                if not isinstance(target, (Mapping, str)):
                    raise BrowserSessionError("invalid_argument", "into_view requires target")
                element, _, level = await self._resolve_target(target, writable=True)
                await element.handle.scroll_into_view_if_needed(
                    timeout=self.policy.action_timeout_ms
                )
                visible = bool(await element.handle.is_visible())
                return {
                    "mode": mode,
                    "visible": visible,
                    "target_ref": element.target_ref,
                    "match": level,
                }
            if mode == "auto":
                steps = min(max(1, int(query.get("steps", 5))), 20)
                for _ in range(steps):
                    await self._page.mouse.wheel(0, min(1200, int(query.get("amount_px", 700))))
                    await self._page.wait_for_timeout(min(1000, int(query.get("delay_ms", 100))))
            else:
                direction = str(query.get("direction", "down"))
                amount = min(max(1, int(query.get("amount_px", 600))), 5000)
                dx = amount if direction == "right" else -amount if direction == "left" else 0
                dy = amount if direction == "down" else -amount if direction == "up" else 0
                if query.get("container"):
                    container, _, level = await self._resolve_target(
                        query["container"], writable=False
                    )
                    before = await container.handle.evaluate(
                        "el => ({x:el.scrollLeft,y:el.scrollTop,h:el.scrollHeight,w:el.scrollWidth})"
                    )
                    await container.handle.evaluate("(el, delta) => el.scrollBy(delta.x, delta.y)", {"x": dx, "y": dy})
                    after = await container.handle.evaluate(
                        "el => ({x:el.scrollLeft,y:el.scrollTop,h:el.scrollHeight,w:el.scrollWidth})"
                    )
                    return {
                        "mode": mode,
                        "before": before,
                        "after": after,
                        "changed": before != after,
                        "container_ref": container.target_ref,
                        "container_match": level,
                    }
                else:
                    await self._page.evaluate("([x,y]) => window.scrollBy(x,y)", [dx, dy])
            after = await self._page.evaluate(
                "() => ({x: scrollX, y: scrollY, h: document.documentElement.scrollHeight})"
            )
            return {"mode": mode, "before": before, "after": after, "changed": before != after}

        return await self._execute(
            "scroll", dict(query), operation, timeout_ms=self.policy.wait_timeout_ms, mutating=True
        )

    async def upload(
        self, target: Mapping[str, object] | str, artifact_refs: list[str]
    ) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            element, _, level = await self._resolve_target(target, writable=True)
            if element.control_type != "file" and element.tag != "input":
                raise BrowserSessionError(
                    "unsupported_control",
                    "target is not a file input",
                    hint="Upload requires a file input target. Use browser_find with a css selector for input[type=file], or inspect compound output to locate one.",
                )
            paths: list[str] = []
            for ref in artifact_refs:
                path = self._approved_artifact_path(ref)
                if not path.is_file():
                    raise BrowserSessionError(
                        "artifact_not_allowed", "approved upload artifact does not exist"
                    )
                paths.append(str(path))
            if len(paths) > 1 and not bool(element.compound.get("multiple", False)):
                raise BrowserSessionError(
                    "unsupported_control", "file input does not accept multiple files"
                )
            await element.handle.set_input_files(paths, timeout=self.policy.action_timeout_ms)
            files = await element.handle.evaluate(
                "el => Array.from(el.files || []).map(f => ({name:f.name,size:f.size,type:f.type}))"
            )
            if len(files) != len(paths):
                raise BrowserSessionError(
                    "readback_mismatch",
                    "file input did not expose all uploaded files",
                    backend_attempted=True,
                )
            return self._receipt(element, files=files, count=len(files), match=level)

        return await self._execute(
            "upload",
            {"target": target, "artifact_refs": artifact_refs},
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    def _approved_artifact_path(self, ref: str) -> Path:
        if not ref.startswith("artifact:"):
            raise BrowserSessionError(
                "artifact_not_allowed", "upload requires an artifact-store reference"
            )
        path = self.video_dir / "artifacts" / Path(ref.removeprefix("artifact:")).name
        try:
            resolved = path.expanduser().resolve(strict=False)
            run_root = self.video_dir.parent.parent.resolve(strict=False)
            resolved.relative_to(run_root)
        except (OSError, ValueError) as exc:
            raise BrowserSessionError(
                "artifact_not_allowed",
                "upload artifact must belong to the current run artifact root",
            ) from exc
        return resolved

    async def drag(
        self,
        source: Mapping[str, object] | str,
        destination: Mapping[str, object] | str,
        **kwargs: object,
    ) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            source_el, _, source_level = await self._resolve_target(source, writable=True)
            destination_el, _, destination_level = await self._resolve_target(
                destination, writable=True
            )
            before_hash = await self._dom_hash()
            before_source_box = await source_el.handle.bounding_box()
            await self._drag_handles(source_el.handle, destination_el.handle)
            after_hash = await self._dom_hash()
            after_source_box = await source_el.handle.bounding_box()
            dom_changed = before_hash != after_hash
            position_changed = before_source_box != after_source_box
            postcondition = await self._verify_expect(kwargs.get("expect"))
            if postcondition is None and not dom_changed and not position_changed:
                raise BrowserSessionError(
                    "postcondition_unmet",
                    "drag produced no observable DOM or position change",
                    backend_attempted=True,
                )
            return {
                "dragged": True,
                "source_ref": source_el.target_ref,
                "destination_ref": destination_el.target_ref,
                "source_match": source_level,
                "destination_match": destination_level,
                "dom_changed": dom_changed,
                "position_changed": position_changed,
                "postcondition": postcondition,
            }

        return await self._execute(
            "drag",
            {"source": source, "destination": destination, **kwargs},
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def tabs(self, query: Mapping[str, object]) -> Mapping[str, object]:
        action = str(query.get("action", "list"))

        async def operation() -> Mapping[str, object]:
            pages = list(self._context.pages)
            for index, page in enumerate(pages, start=1):
                self._tab_refs.setdefault(f"tab-{index}", page)
            if action == "new":
                page = await self._context.new_page()
                ref = next(
                    (key for key, candidate in self._tab_refs.items() if candidate is page),
                    f"tab-{len(self._tab_refs) + 1}",
                )
                self._tab_refs[ref] = page
                if query.get("url"):
                    requested = self.policy.validate_initial_url(str(query["url"]))
                    await page.goto(
                        requested,
                        wait_until="domcontentloaded",
                        timeout=self.policy.navigation_timeout_ms,
                    )
                    self.policy.validate_final_url(page.url)
                self._page = page
                self.generation += 1
                self._snapshots.invalidate()
                return {"action": action, "active_tab": ref, "tabs": await self._tab_list()}
            if action == "select":
                ref = str(query.get("tab_ref", ""))
                page = self._tab_refs.get(ref)
                if page is None:
                    raise BrowserSessionError("tab_not_found", "tab_ref is not owned by this run")
                if self._page is not page:
                    self._page = page
                    self.generation += 1
                    self._snapshots.invalidate()
                return {"action": action, "active_tab": ref, "tabs": await self._tab_list()}
            if action == "close":
                ref = str(query.get("tab_ref", ""))
                page = self._tab_refs.get(ref)
                if page is None:
                    raise BrowserSessionError("tab_not_found", "tab_ref is not owned by this run")
                if len(self._tab_refs) == 1:
                    raise BrowserSessionError(
                        "last_tab_close", "cannot close the last run-owned tab"
                    )
                self._tab_refs.pop(ref)
                await page.close()
                if self._page is page:
                    remaining = list(self._tab_refs.values())
                    self._page = remaining[-1]
                self.generation += 1
                self._snapshots.invalidate()
                return {"action": action, "closed_tab": ref, "tabs": await self._tab_list()}
            return {
                "action": "list",
                "active_tab": self._active_tab_ref(),
                "tabs": await self._tab_list(),
            }

        return await self._execute(
            "tabs",
            dict(query),
            operation,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=action != "list",
        )

    async def dialog(self, query: Mapping[str, object]) -> Mapping[str, object]:
        action = str(query.get("action", ""))
        if action not in {"accept", "dismiss"}:
            raise BrowserSessionError("invalid_argument", "dialog action must be accept or dismiss")

        async def operation() -> Mapping[str, object]:
            trigger = query.get("trigger")
            if isinstance(trigger, (Mapping, str)):
                self._pending_dialog = None
                self._dialog_event = asyncio.Event()
                element, _, match = await self._resolve_target(trigger, writable=True)
                click_task = asyncio.create_task(
                    element.handle.click(
                        no_wait_after=True, timeout=self.policy.action_timeout_ms
                    )
                )
                try:
                    await asyncio.wait_for(
                        self._dialog_event.wait(),
                        timeout=int(query.get("timeout_ms", self.policy.wait_timeout_ms)) / 1000,
                    )
                except TimeoutError as exc:
                    click_task.cancel()
                    await asyncio.gather(click_task, return_exceptions=True)
                    raise BrowserSessionError(
                        "dialog_not_found",
                        "trigger did not open a dialog before timeout",
                        backend_attempted=True,
                    ) from exc
            else:
                match = "pending"
            dialog = self._pending_dialog
            if dialog is None:
                raise BrowserSessionError("dialog_not_found", "no pending browser dialog")
            if action == "accept":
                await dialog.accept(
                    str(query["prompt_text"]) if query.get("prompt_text") is not None else None
                )
            else:
                await dialog.dismiss()
            if isinstance(trigger, (Mapping, str)):
                await click_task
            self._pending_dialog = None
            return {
                "handled": True,
                "action": action,
                "type": dialog.type,
                "message": dialog.message,
                "match": match,
            }

        return await self._execute(
            "dialog",
            dict(query),
            operation,
            timeout_ms=int(query.get("timeout_ms", self.policy.wait_timeout_ms))
            + self.policy.action_timeout_ms
            + 1_000,
            mutating=True,
        )

    async def network(self, query: Mapping[str, object]) -> Mapping[str, object]:
        mode = str(query.get("mode", "list"))

        async def operation() -> Mapping[str, object]:
            if mode == "detail":
                ref = str(query.get("request_ref", ""))
                match = next(
                    (item for item in self._network_events if item.get("request_ref") == ref), None
                )
                if match is None:
                    raise BrowserSessionError("request_not_found", "request_ref is not available")
                response = self._network_responses.get(ref)
                detail = dict(match)
                if response is not None:
                    try:
                        body = await response.text()
                    except Exception:
                        body = ""
                    max_chars = min(max(1, int(query.get("max_body_chars", 20_000))), 100_000)
                    detail["body"] = _redact_snapshot_text(body)[:max_chars]
                    detail["body_truncated"] = len(body) > max_chars
                return {"mode": mode, "request": detail}
            events = list(self._network_events)
            since_ms = int(query.get("since_ms", 0) or 0)
            until_ms = int(query.get("until_ms", 2**63 - 1) or 2**63 - 1)
            events = [
                item for item in events if since_ms <= int(item.get("timestamp_ms", 0)) <= until_ms
            ]
            if not query.get("include_static", False):
                events = [
                    item
                    for item in events
                    if item.get("resource_type") not in {"image", "font", "stylesheet", "media"}
                ]
            if query.get("failed_only"):
                events = [item for item in events if int(item.get("status", 0) or 0) >= 400]
            resource_type = query.get("resource_type")
            if isinstance(resource_type, str) and resource_type:
                events = [item for item in events if item.get("resource_type") == resource_type]
            fields = query.get("fields")
            if isinstance(fields, (list, tuple)):
                allowed = {str(field) for field in fields}
                events = [
                    {key: value for key, value in item.items() if key in allowed or key == "request_ref"}
                    for item in events
                ]
            cursor = query.get("cursor")
            if cursor:
                cursor_index = next(
                    (
                        index
                        for index, item in enumerate(events)
                        if item.get("request_ref") == str(cursor)
                    ),
                    None,
                )
                if cursor_index is None:
                    raise BrowserSessionError("cursor_not_found", "network cursor is not retained")
                events = events[cursor_index + 1 :]
            limit = min(max(1, int(query.get("limit", 50))), 200)
            selected = events[-limit:]
            return {
                "mode": "list",
                "requests": selected,
                "next_cursor": selected[-1].get("request_ref") if selected else str(cursor or "") or None,
            }

        return await self._execute(
            "network",
            dict(query),
            operation,
            timeout_ms=self.policy.wait_timeout_ms,
            mutating=False,
        )

    async def download(self, query: Mapping[str, object]) -> Mapping[str, object]:
        trigger = query.get("trigger")
        if not isinstance(trigger, (Mapping, str)):
            raise BrowserSessionError("invalid_argument", "download requires a trigger target")

        async def operation() -> Mapping[str, object]:
            async with self._page.expect_download(
                timeout=int(query.get("timeout_ms", self.policy.wait_timeout_ms))
            ) as info:
                element, _, level = await self._resolve_target(trigger, writable=True)
                await element.handle.click(timeout=self.policy.action_timeout_ms)
            download = await info.value
            filename = download.suggested_filename
            pattern = query.get("pattern")
            if isinstance(pattern, str) and pattern not in filename and pattern not in download.url:
                await download.cancel()
                raise BrowserSessionError(
                    "download_pattern_mismatch",
                    "download did not match requested pattern",
                    backend_attempted=True,
                )
            download_dir = self.video_dir / "downloads"
            download_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = download_dir / Path(filename).name
            await download.save_as(str(destination))
            if not destination.is_file():
                raise BrowserSessionError(
                    "download_failed",
                    "completed download artifact is missing",
                    backend_attempted=True,
                )
            data = destination.read_bytes()
            return {
                "completed": True,
                "filename": filename,
                "artifact_path": str(destination),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "match": level,
            }

        return await self._execute(
            "download",
            dict(query),
            operation,
            timeout_ms=int(query.get("timeout_ms", self.policy.wait_timeout_ms)) + 1000,
            mutating=True,
        )

    async def eval(self, query: Mapping[str, object]) -> Mapping[str, object]:
        if not bool(getattr(self.policy, "eval_allowed", False)):
            raise BrowserSessionError(
                "capability_denied", "browser.eval is not authorized for this run"
            )
        script = str(query.get("script", ""))
        if not script:
            raise BrowserSessionError("invalid_argument", "script is required")
        expected = str(query.get("expected_postcondition", "none"))
        requested_tab = query.get("tab_ref")
        if requested_tab is not None and str(requested_tab) != self._active_tab_ref():
            raise BrowserSessionError(
                "tab_not_active",
                "eval tab_ref is not the active run-owned tab",
                details={"requested_tab": requested_tab, "active_tab": self._active_tab_ref()},
            )
        frame = self._frame_for_ref(query.get("frame_ref"))
        frame_url = frame.url if frame is not self._page else self._page.url
        self.policy.validate_final_url(frame_url)

        async def operation() -> Mapping[str, object]:
            before = await self._dom_hash()
            result = await frame.evaluate(script, query.get("arguments"))
            encoded = json.dumps(result, ensure_ascii=False, default=str)
            max_chars = min(
                max(1, int(query.get("max_result_chars", self.policy.max_result_chars))),
                self.policy.max_result_chars,
            )
            after = await self._dom_hash()
            if expected != "none" and before == after:
                raise BrowserSessionError(
                    "postcondition_unmet",
                    "expected eval postcondition did not change DOM",
                    backend_attempted=True,
                )
            return {
                "result": encoded[:max_chars],
                "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
                "dom_changed": before != after,
                "expected_postcondition": expected,
                "tab_ref": self._active_tab_ref(),
                "frame_ref": str(query.get("frame_ref") or "f0"),
                "origin": frame_url,
            }

        audit_arguments = {key: value for key, value in query.items() if key != "script"}
        audit_arguments["script_sha256"] = hashlib.sha256(script.encode()).hexdigest()
        return await self._execute(
            "eval",
            audit_arguments,
            operation,
            timeout_ms=int(query.get("timeout_ms", self.policy.action_timeout_ms)),
            mutating=True,
        )

    async def analyze(self, query: Mapping[str, object]) -> Mapping[str, object]:
        requested_url = query.get("url")

        async def operation() -> Mapping[str, object]:
            http_status = None
            if requested_url is not None:
                requested = self.policy.validate_initial_url(str(requested_url))
                response = await self._page.goto(
                    requested,
                    wait_until="domcontentloaded",
                    timeout=self.policy.navigation_timeout_ms,
                )
                http_status = response.status if response is not None else None
                self.policy.validate_final_url(self._page.url)
                self.generation += 1
                self._snapshots.invalidate()
            settle_ms = min(max(0, int(query.get("settle_ms", 0))), self.policy.wait_timeout_ms)
            if settle_ms:
                await self._page.wait_for_timeout(settle_ms)
            text = await self._page.locator("body").inner_text()
            anti_bot_terms = [
                term
                for term in ("captcha", "cloudflare", "verify you are human", "access denied")
                if term in text.casefold()
            ]
            candidates = []
            for item in self._network_events[
                -min(max(1, int(query.get("network_limit", 20))), 200) :
            ]:
                resource_type = str(item.get("resource_type", ""))
                if resource_type not in {"xhr", "fetch"}:
                    continue
                status = int(item.get("status", 0) or 0)
                score = 3 if 200 <= status < 300 else 1 if status else 0
                candidates.append(
                    {
                        "request_ref": item.get("request_ref"),
                        "url": item.get("url"),
                        "method": item.get("method"),
                        "status": item.get("status"),
                        "resource_type": resource_type,
                        "score": score,
                        "evidence": "captured run-owned browser response",
                    }
                )
            return {
                "requested_url": str(requested_url) if requested_url is not None else None,
                "final_url": self._page.url,
                "http_status": http_status,
                "title": await self._page.title(),
                "rendering": "dom",
                "anti_bot": anti_bot_terms,
                "api_candidates": sorted(candidates, key=lambda item: int(item["score"]), reverse=True),
                "recommendation": "inspect or network",
            }

        return await self._execute(
            "analyze",
            dict(query),
            operation,
            timeout_ms=(
                self.policy.navigation_timeout_ms
                + min(max(0, int(query.get("settle_ms", 0))), self.policy.wait_timeout_ms)
                + 1_000
                if requested_url is not None
                else min(max(0, int(query.get("settle_ms", 0))), self.policy.wait_timeout_ms)
                + self.policy.action_timeout_ms
            ),
            mutating=requested_url is not None,
        )

    async def console(self, query: Mapping[str, object]) -> Mapping[str, object]:
        async def operation() -> Mapping[str, object]:
            level = str(query.get("level", "all"))
            if level not in {"all", "error", "warning", "log", "info", "debug"}:
                raise BrowserSessionError("invalid_argument", "unsupported console level")
            since_ms = int(query.get("since_ms", 0) or 0)
            until_ms = int(query.get("until_ms", 2**63 - 1) or 2**63 - 1)
            events = [
                item
                for item in self._console_events
                if since_ms <= int(item.get("timestamp_ms", 0)) <= until_ms
            ]
            cursor = query.get("cursor")
            if cursor:
                cursor_index = next(
                    (
                        index
                        for index, item in enumerate(events)
                        if item.get("cursor") == str(cursor)
                    ),
                    None,
                )
                if cursor_index is None:
                    raise BrowserSessionError("cursor_not_found", "console cursor is not retained")
                events = events[cursor_index + 1 :]
            if level != "all":
                events = [item for item in events if item.get("type") == level]
            limit = min(max(1, int(query.get("limit", 50))), 200)
            selected = events[-limit:]
            return {
                "messages": selected,
                "next_cursor": (
                    selected[-1].get("cursor")
                    if selected
                    else str(cursor or "") or None
                ),
            }

        return await self._execute(
            "console",
            dict(query),
            operation,
            timeout_ms=self.policy.wait_timeout_ms,
            mutating=False,
        )

    async def screenshot(self, **kwargs: object) -> Mapping[str, object] | bytes:
        ref_map: dict[str, str] = {}

        async def action() -> bytes:
            frame_ref = kwargs.get("frame_ref")
            if frame_ref is not None and kwargs.get("annotate_refs"):
                raise BrowserSessionError(
                    "invalid_argument",
                    "annotated screenshots currently require the main frame",
                )
            if kwargs.get("annotate_refs"):
                elements, total, frames = await collect_elements(
                    self._page,
                    limit=min(self.policy.max_elements, 100),
                    filters={"actionable_only": True},
                )
                snapshot = self._snapshots.replace(
                    generation=self.generation,
                    url=self._page.url,
                    title=await self._page.title(),
                    elements=elements,
                    total_matches=total,
                    truncated=total > len(elements),
                    frames=frames,
                    view="annotated",
                    created_at_ms=int(time.time() * 1000),
                )
                labels = []
                for index, element in enumerate(snapshot.elements, start=1):
                    if element.frame_id != "f0" or not element.target_ref:
                        continue
                    box = await element.handle.bounding_box()
                    if box is None:
                        continue
                    label = str(index)
                    ref_map[label] = element.target_ref
                    labels.append({"label": label, "x": box["x"], "y": box["y"]})
                await self._page.evaluate(
                    """labels => {
                      const root = document.createElement('div');
                      root.dataset.homemasterAnnotationRoot = 'true';
                      Object.assign(root.style, {position:'absolute',left:'0',top:'0',zIndex:'2147483647',pointerEvents:'none'});
                      for (const item of labels) {
                        const tag = document.createElement('span');
                        tag.textContent = item.label;
                        Object.assign(tag.style, {position:'absolute',left:`${item.x}px`,top:`${item.y}px`,background:'#ffdf00',color:'#111',border:'1px solid #111',font:'bold 12px sans-serif',padding:'1px 3px'});
                        root.appendChild(tag);
                      }
                      document.documentElement.appendChild(root);
                    }""",
                    labels,
                )
            try:
                png = await self._capture_png(
                    full_page=bool(kwargs.get("full_page", False)),
                    width=kwargs.get("width"),
                    height=kwargs.get("height"),
                    frame_ref=frame_ref,
                )
            finally:
                if kwargs.get("annotate_refs"):
                    await self._page.evaluate(
                        "() => document.querySelector('[data-homemaster-annotation-root]')?.remove()"
                    )
            self._screenshot_index += 1
            screenshot_dir = self.video_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(screenshot_dir, 0o700)
            path = screenshot_dir / f"screenshot-{self._screenshot_index:04d}.png"
            path.write_bytes(png)
            os.chmod(path, 0o600)
            return png

        png = await self._execute(
            "screenshot",
            dict(kwargs),
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=False,
        )
        if not kwargs:
            return png
        actual_width, actual_height = _png_dimensions(png)
        return {
            "artifact_path": str(
                self.video_dir / "screenshots" / f"screenshot-{self._screenshot_index:04d}.png"
            ),
            "png": base64.b64encode(png).decode("ascii"),
            "byte_count": len(png),
            "sha256": hashlib.sha256(png).hexdigest(),
            "width": actual_width,
            "height": actual_height,
            "full_page": bool(kwargs.get("full_page", False)),
            "annotate_refs": bool(kwargs.get("annotate_refs", False)),
            "ref_map": ref_map,
            "tab_ref": self._active_tab_ref(),
            "frame_ref": str(kwargs.get("frame_ref") or "f0"),
            "page_generation": self.generation,
        }

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._snapshots.invalidate()
            try:
                await self._cleanup_started_resources(save_artifacts=True)
            finally:
                self._closed = True
                self._write_event(
                    "session_closed",
                    {"video_path": str(self.video_path) if self.video_path else None},
                )

    async def _set_checked_target(
        self, target: Mapping[str, object] | str, *, desired: bool
    ) -> Mapping[str, object]:
        operation = "check" if desired else "uncheck"

        async def action() -> Mapping[str, object]:
            element, state, match_level = await self._resolve_target(target, writable=True)
            return await self._set_checked_element(
                element, state, desired=desired, match_level=match_level
            )

        return await self._execute(
            operation,
            {"target": dict(target) if isinstance(target, Mapping) else target},
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def _set_checked_element(
        self,
        element: BrowserElement,
        state: Mapping[str, object],
        *,
        desired: bool,
        match_level: str,
    ) -> Mapping[str, object]:
        if element.role not in {"checkbox", "switch", "radio"}:
            raise BrowserSessionError("unsupported_control", "target is not a binary control")
        if not desired and element.role == "radio":
            raise BrowserSessionError(
                "unsupported_control", "radio controls cannot be directly unchecked"
            )
        current = bool(state.get("checked"))
        changed = current != desired
        if changed:
            if element.tag == "input":
                method = element.handle.check if desired else element.handle.uncheck
                await method(timeout=self.policy.action_timeout_ms)
            else:
                await element.handle.click(timeout=self.policy.action_timeout_ms)
        actual_state = await current_state(element)
        actual = bool(actual_state.get("checked"))
        self._snapshots.invalidate()
        if actual is not desired:
            raise BrowserSessionError(
                "readback_mismatch",
                "binary control state did not match DOM readback",
                details={"expected": desired, "actual": actual},
                backend_attempted=changed,
            )
        return self._receipt(
            element,
            expected=desired,
            actual=actual,
            changed=changed,
            verified=True,
            match=match_level,
        )

    async def _set_checked(
        self, snapshot_id: str, element_id: str, *, desired: bool
    ) -> Mapping[str, object]:
        operation = "check" if desired else "uncheck"

        async def action() -> Mapping[str, object]:
            element, state = await self._target(snapshot_id, element_id)
            if element.role not in {"checkbox", "switch", "radio"}:
                raise BrowserSessionError("unsupported_control", "target is not a binary control")
            if not desired and element.role == "radio":
                raise BrowserSessionError(
                    "unsupported_control", "radio controls cannot be directly unchecked"
                )
            current = bool(state.get("checked"))
            changed = current != desired
            if changed:
                if element.tag == "input":
                    method = element.handle.check if desired else element.handle.uncheck
                    await method(timeout=self.policy.action_timeout_ms)
                else:
                    await element.handle.click(timeout=self.policy.action_timeout_ms)
            actual_state = await current_state(element)
            actual = bool(actual_state.get("checked"))
            self._snapshots.invalidate()
            if actual is not desired:
                raise BrowserSessionError(
                    "readback_mismatch",
                    "binary control state did not match DOM readback",
                    details={"expected": desired, "actual": actual},
                    backend_attempted=changed,
                )
            return self._receipt(
                element,
                expected=desired,
                actual=actual,
                changed=changed,
                verified=True,
                match="exact",
            )

        return await self._execute(
            operation,
            {"snapshot_id": snapshot_id, "element_id": element_id},
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def _target(
        self,
        snapshot_id: str,
        element_id: str,
        *,
        require_editable: bool = False,
    ) -> tuple[BrowserElement, dict[str, object]]:
        try:
            element = self._snapshots.resolve(snapshot_id, element_id, generation=self.generation)
        except TargetResolutionError as exc:
            raise BrowserSessionError(exc.code, str(exc), details=exc.details) from exc
        connected = bool(await element.handle.evaluate("el => el.isConnected"))
        if not connected:
            raise BrowserSessionError(
                "stale_ref",
                "target element is detached",
                hint="The element was removed from the page after inspection. Re-run browser_inspect to get a fresh snapshot.",
            )
        await element.handle.scroll_into_view_if_needed(timeout=self.policy.action_timeout_ms)
        state = await current_state(element)
        current_fingerprint = fingerprint_from_state(state, frame_id=element.frame_id)
        if current_fingerprint != element.fingerprint:
            raise BrowserSessionError(
                "stale_ref",
                "target identity changed after inspection",
                hint="The page re-rendered and this ref no longer identifies the same element. Re-run browser_inspect and pick a fresh target.",
            )
        state = await self._prepare_actionable(
            element, require_editable=require_editable
        )
        return element, state

    async def _resolve_target(
        self,
        target_value: Mapping[str, object] | str,
        *,
        writable: bool,
        require_editable: bool = False,
    ) -> tuple[BrowserElement, dict[str, object], str]:
        """Resolve a semantic target or retained ref and recover one re-render."""

        target = Target.from_value(target_value)
        if target.tab_ref is not None and target.tab_ref != self._active_tab_ref():
            raise BrowserSessionError(
                "tab_not_active",
                "target tab_ref is not the active run-owned tab",
                details={"requested_tab": target.tab_ref, "active_tab": self._active_tab_ref()},
            )
        if target.target_ref:
            try:
                snapshot, element = self._snapshots.resolve_target_ref(target.target_ref)
            except TargetResolutionError as exc:
                raise BrowserSessionError(exc.code, str(exc), details=exc.details) from exc
            if (
                snapshot.generation != self.generation
                or snapshot.url.split("#", 1)[0] != self._page.url.split("#", 1)[0]
            ):
                raise BrowserSessionError(
                    "stale_ref",
                    "target reference belongs to a previous navigation",
                    details={
                        "target_ref": target.target_ref,
                        "snapshot_generation": snapshot.generation,
                        "page_generation": self.generation,
                        "retry": "inspect or find the target again on the current page",
                    },
                )
            try:
                connected = bool(await element.handle.evaluate("el => el.isConnected"))
                owner_frame = await element.handle.owner_frame()
                if owner_frame is None or owner_frame.page is not self._page:
                    raise BrowserSessionError(
                        "stale_ref",
                        "target reference belongs to another tab",
                        details={"target_ref": target.target_ref},
                    )
                state = await current_state(element) if connected else {}
                if connected:
                    live_fp = fingerprint_from_state(state, frame_id=element.frame_id)
                    if live_fp == element.fingerprint:
                        return (
                            element,
                            await self._resolved_state(
                                element,
                                state,
                                writable,
                                require_editable=require_editable,
                            ),
                            "exact",
                        )
                    if _has_stable_identity(element, state):
                        return (
                            element,
                            await self._resolved_state(
                                element,
                                state,
                                writable,
                                require_editable=require_editable,
                            ),
                            "stable",
                        )
            except BrowserSessionError:
                raise
            except Exception:
                connected = False
            recovered = await self._reidentify(element)
            if recovered is None:
                raise BrowserSessionError(
                    "stale_ref",
                    "target reference could not be uniquely recovered",
                    details={
                        "target_ref": target.target_ref,
                        "retry": "inspect or find the target again and choose one unique candidate",
                    },
                )
            recovered_element, recovered_state = recovered
            return (
                recovered_element,
                await self._resolved_state(
                    recovered_element,
                    recovered_state,
                    writable,
                    require_editable=require_editable,
                ),
                "reidentified",
            )

        target_filters = {
            key: getattr(target, key)
            for key in ("role", "name", "label", "text", "testid", "frame_ref")
            if getattr(target, key) is not None
        }
        elements, _, _ = await collect_elements(
            self._page,
            limit=self.policy.max_elements,
            filters=target_filters,
        )
        try:
            resolution = resolve_semantic(elements, target, writable=writable)
        except TargetResolutionError as exc:
            raise BrowserSessionError(exc.code, str(exc), details=exc.details) from exc
        element = resolution.element
        state = await current_state(element)
        return (
            element,
            await self._resolved_state(
                element,
                state,
                writable,
                require_editable=require_editable,
            ),
            resolution.level,
        )

    async def _resolved_state(
        self,
        element: BrowserElement,
        state: Mapping[str, object],
        writable: bool,
        *,
        require_editable: bool,
    ) -> dict[str, object]:
        if not writable:
            return dict(state)
        return await self._prepare_actionable(
            element, require_editable=require_editable
        )

    async def _prepare_actionable(
        self, element: BrowserElement, *, require_editable: bool = False
    ) -> dict[str, object]:
        await element.handle.scroll_into_view_if_needed(timeout=self.policy.action_timeout_ms)
        state = await current_state(element)
        if not bool(state.get("visible")):
            raise BrowserSessionError(
                "target_not_visible",
                "target is not visible",
                hint="The control exists but is hidden. It may appear after scrolling, opening a dropdown, or expanding a section; use browser_scroll or open the owning control first.",
            )
        if not bool(state.get("enabled")):
            raise BrowserSessionError(
                "target_disabled",
                "target is disabled",
                hint="The control is disabled by the page. Pick an enabled control, or complete the prerequisite step (form validation, prior field) that enables it.",
            )
        if require_editable and bool(state.get("readonly")):
            raise BrowserSessionError(
                "target_readonly",
                "target is readonly",
                hint="This control rejects direct text entry. Select from its dropdown options instead of filling, or pick an editable target; use browser_inspect to find one.",
            )
        if bool(state.get("obscured")):
            raise BrowserSessionError(
                "target_obscured",
                "target is obscured",
                hint="Another element covers this control. Close the overlaying popover, dialog, or dropdown first, or use browser_press Escape and retry.",
            )
        return state

    async def _reidentify(
        self, original: BrowserElement
    ) -> tuple[BrowserElement, dict[str, object]] | None:
        elements, _, _ = await collect_elements(
            self._page,
            limit=self.policy.max_elements,
            filters={},
        )
        candidates = [
            element
            for element in elements
            if element.frame_id == original.frame_id
            and element.tag == original.tag
            and element.role == original.role
            and element.name == original.name
            and (original.stable_id or original.testid)
            and (
                element.stable_id == original.stable_id
                if original.stable_id
                else element.testid == original.testid
            )
        ]
        if not candidates:
            candidates = [
                element
                for element in elements
                if element.frame_id == original.frame_id
                and element.tag == original.tag
                and element.role == original.role
                and element.name == original.name
            ]
        if len(candidates) != 1:
            return None
        element = candidates[0]
        state = await current_state(element)
        if not bool(state.get("visible")) or not bool(state.get("enabled")):
            return None
        return element, state

    async def _condition_state(
        self,
        condition: Mapping[str, object],
        *,
        kind: str,
        stable_hash: str,
        stable_since: float,
    ) -> tuple[bool, dict[str, object], str, float]:
        value = str(condition.get("value", ""))
        if kind in {"text_present", "text_absent"}:
            text = await self._page.locator("body").inner_text()
            present = value in text
            return (
                (present if kind == "text_present" else not present),
                {"present": present},
                stable_hash,
                stable_since,
            )
        if kind == "url_contains":
            matched = value in self._page.url
            return matched, {"url": self._page.url}, stable_hash, stable_since
        if kind == "url":
            match = str(condition.get("match", "exact"))
            actual = self._page.url
            matched = actual == value if match == "exact" else value in actual
            return matched, {"url": actual}, stable_hash, stable_since
        if kind in {"selector_present", "selector_absent"}:
            target = condition.get("target")
            if isinstance(target, (Mapping, str)):
                try:
                    await self._resolve_target(target, writable=False)
                    present = True
                except BrowserSessionError as exc:
                    if exc.code in {"target_not_found", "stale_ref", "target_not_visible"}:
                        present = False
                    else:
                        raise
            else:
                try:
                    present = bool(await self._page.locator(value).count())
                except Exception as exc:
                    raise BrowserSessionError(
                        "invalid_selector",
                        "invalid CSS selector in wait",
                        details={"selector": value},
                    ) from exc
            return (
                (present if kind == "selector_present" else not present),
                {
                    "present": present,
                    "selector": value if not isinstance(target, (Mapping, str)) else None,
                    "target": dict(target) if isinstance(target, Mapping) else target,
                },
                stable_hash,
                stable_since,
            )
        if kind in {"xhr", "response"}:
            events = self._network_events
            request_ref = condition.get("request_ref")
            matched_events = [
                item
                for item in events
                if (
                    item.get("request_ref") == request_ref
                    if request_ref
                    else value in str(item.get("url", ""))
                )
            ]
            if kind == "xhr":
                matched_events = [
                    item for item in matched_events if item.get("resource_type") in {"xhr", "fetch"}
                ]
            completed = [item for item in matched_events if item.get("status") is not None]
            return bool(completed), {"matches": completed[-5:]}, stable_hash, stable_since
        if kind == "popup":
            pages = list(self._context.pages)
            return len(pages) > 1, {"tabs": len(pages)}, stable_hash, stable_since
        if kind == "dialog":
            return (
                self._pending_dialog is not None,
                {"pending": self._pending_dialog is not None},
                stable_hash,
                stable_since,
            )
        if kind == "download":
            return (
                bool(self._downloads),
                {"downloads": self._downloads[-5:]},
                stable_hash,
                stable_since,
            )
        if kind == "element_state":
            target = condition.get("target")
            if not isinstance(target, (Mapping, str)):
                raise BrowserSessionError(
                    "invalid_condition",
                    "element_state requires target",
                    hint="Pass the condition object itself, not nested inside another condition key, and include its target field (e.g. {\"kind\": \"element_state\", \"target\": {...}, \"state\": \"visible\"}).",
                )
            try:
                element, state, _ = await self._resolve_target(target, writable=False)
            except BrowserSessionError as exc:
                if exc.code in {
                    "target_not_found",
                    "stale_ref",
                    "target_not_visible",
                    "target_disabled",
                }:
                    return False, {"error_code": exc.code}, stable_hash, stable_since
                raise
            requested_state = str(condition.get("state", condition.get("value", "visible")))
            values = {
                "visible": bool(state.get("visible")),
                "enabled": bool(state.get("enabled")),
                "disabled": not bool(state.get("enabled")),
                "editable": bool(state.get("editable")),
                "checked": bool(state.get("checked")),
                "unchecked": not bool(state.get("checked")),
                "expanded": bool(state.get("expanded")),
                "collapsed": not bool(state.get("expanded")),
            }
            return (
                bool(values.get(requested_state, False)),
                {"state": requested_state, "actual": state, "target_ref": element.target_ref},
                stable_hash,
                stable_since,
            )
        if kind == "dom_stable":
            current_hash = await self._dom_hash()
            now = time.monotonic()
            if current_hash != stable_hash:
                stable_hash = current_hash
                stable_since = now
            quiet_ms = (now - stable_since) * 1000
            return quiet_ms >= 300, {"quiet_ms": int(quiet_ms)}, stable_hash, stable_since
        if kind.startswith("element_"):
            snapshot_id = str(condition.get("snapshot_id", ""))
            element_id = str(condition.get("element_id", ""))
            try:
                element = self._snapshots.resolve(
                    snapshot_id, element_id, generation=self.generation
                )
            except TargetResolutionError as exc:
                raise BrowserSessionError(exc.code, str(exc), details=exc.details) from exc
            try:
                connected = bool(await element.handle.evaluate("el => el.isConnected"))
                state = await current_state(element) if connected else {}
            except Exception as exc:
                if type(exc).__module__.split(".")[0] != "playwright":
                    raise
                connected = False
                state = {}
            if kind == "element_present":
                return connected, {"present": connected}, stable_hash, stable_since
            if kind == "element_absent":
                return not connected, {"present": connected}, stable_hash, stable_since
            if kind == "element_enabled":
                matched = connected and bool(state.get("enabled"))
                return matched, {"enabled": bool(state.get("enabled"))}, stable_hash, stable_since
            if kind == "element_disabled":
                matched = connected and not bool(state.get("enabled"))
                return matched, {"enabled": bool(state.get("enabled"))}, stable_hash, stable_since
            if kind == "element_text":
                actual = str(state.get("text", ""))
                return value in actual, {"text": actual}, stable_hash, stable_since
        raise BrowserSessionError(
            "invalid_condition",
            f"unsupported wait condition: {kind!r}",
            hint=(
                "Pass one flat condition object with a supported kind: text_present, text_absent, "
                "selector_present, selector_absent, time, xhr, response, dom_stable, url, "
                "element_state, popup, dialog, or download. If you wrapped the condition inside "
                "a 'condition' key, unwrap it - the schema takes the condition directly."
            ),
        )

    async def _dom_hash(self) -> str:
        content = await self._page.content()
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def _drag_handles(self, source: Any, destination: Any) -> None:
        source_frame = await source.owner_frame()
        destination_frame = await destination.owner_frame()
        if source_frame is None or destination_frame is None or source_frame is not destination_frame:
            raise BrowserSessionError(
                "unsupported_control", "drag source and destination must be in the same frame"
            )
        source_token = uuid.uuid4().hex
        destination_token = uuid.uuid4().hex
        await source.evaluate(
            "(el, token) => el.setAttribute('data-homemaster-drag-token', token)", source_token
        )
        await destination.evaluate(
            "(el, token) => el.setAttribute('data-homemaster-drag-token', token)",
            destination_token,
        )
        try:
            source_locator = source_frame.locator(
                f'[data-homemaster-drag-token="{source_token}"]'
            )
            destination_locator = source_frame.locator(
                f'[data-homemaster-drag-token="{destination_token}"]'
            )
            await source_locator.drag_to(
                destination_locator, timeout=self.policy.action_timeout_ms
            )
        finally:
            for handle in (source, destination):
                try:
                    await handle.evaluate(
                        "el => el.removeAttribute('data-homemaster-drag-token')"
                    )
                except Exception:
                    pass

    async def _verify_expect(self, value: object) -> Mapping[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or not isinstance(value.get("kind"), str):
            raise BrowserSessionError(
                "invalid_argument", "expect must be one browser condition object"
            )
        matched, state, _, _ = await self._condition_state(
            value,
            kind=str(value["kind"]),
            stable_hash="",
            stable_since=time.monotonic(),
        )
        if not matched:
            raise BrowserSessionError(
                "postcondition_unmet",
                "requested browser postcondition was not met",
                details={"expect": dict(value), "last_state": state},
                backend_attempted=True,
            )
        return {"matched": True, "condition": str(value["kind"]), "state": state}

    async def _capture_png(
        self,
        *,
        full_page: bool,
        width: object = None,
        height: object = None,
        frame_ref: object = None,
    ) -> bytes:
        previous = self._page.viewport_size or {"width": 1280, "height": 720}
        requested_width = int(width) if width is not None else int(previous["width"])
        requested_height = int(height) if height is not None else int(previous["height"])
        if not 1 <= requested_width <= 4096 or not 1 <= requested_height <= 4096:
            raise BrowserSessionError(
                "invalid_argument", "screenshot dimensions must be between 1 and 4096 pixels"
            )
        changed = requested_width != previous["width"] or (
            not full_page and requested_height != previous["height"]
        )
        if changed:
            await self._page.set_viewport_size(
                {
                    "width": requested_width,
                    "height": int(previous["height"]) if full_page else requested_height,
                }
            )
        try:
            if frame_ref is not None:
                frame = self._frame_for_ref(frame_ref)
                if frame is self._page or frame is self._page.main_frame:
                    return await self._page.screenshot(
                        type="png",
                        full_page=full_page,
                        animations="disabled",
                        timeout=self.policy.action_timeout_ms,
                    )
                frame_element = await frame.frame_element()
                return await frame_element.screenshot(
                    type="png",
                    animations="disabled",
                    timeout=self.policy.action_timeout_ms,
                )
            return await self._page.screenshot(
                type="png",
                full_page=full_page,
                animations="disabled",
                timeout=self.policy.action_timeout_ms,
            )
        finally:
            if changed:
                await self._page.set_viewport_size(previous)

    async def _tab_list(self) -> list[dict[str, object]]:
        rows = []
        for ref, page in self._tab_refs.items():
            rows.append(
                {
                    "tab_ref": ref,
                    "url": page.url,
                    "title": await page.title(),
                    "active": page is self._page,
                }
            )
        return rows

    def _tab_ref_for_page(self, requested_page: Any) -> str | None:
        for ref, page in self._tab_refs.items():
            if page is requested_page:
                return ref
        return None

    def _active_tab_ref(self) -> str | None:
        return self._tab_ref_for_page(self._page)

    def _frame_for_ref(self, frame_ref: object) -> Any:
        if frame_ref is None:
            return self._page
        frames = list(self._page.frames)
        value = str(frame_ref)
        if not value.startswith("f") or not value[1:].isdigit():
            raise BrowserSessionError("frame_not_found", "frame_ref is invalid")
        index = int(value[1:])
        if index < 0 or index >= len(frames):
            raise BrowserSessionError("frame_not_found", "frame_ref is not available")
        return frames[index]

    def _capture_dialog(self, dialog: Any) -> None:
        if self._pending_dialog is not None:
            try:
                asyncio.create_task(dialog.dismiss())
            except Exception:
                pass
            return
        self._pending_dialog = dialog
        if self._dialog_event is not None:
            self._dialog_event.set()

    def _attach_page(self, page: Any) -> None:
        page.on("dialog", self._capture_dialog)
        page.on("console", self._capture_console)

    def _capture_page(self, page: Any) -> None:
        if page in self._tab_refs.values():
            return
        ref = f"tab-{len(self._tab_refs) + 1}"
        self._tab_refs[ref] = page
        self._attach_page(page)

    def _capture_request(self, request: Any) -> None:
        index = len(self._network_events) + 1
        try:
            frame = request.frame
        except Exception:
            frame = None
        page = getattr(frame, "page", None)
        tab_ref = self._tab_ref_for_page(page)
        frame_ref = None
        if page is not None and frame is not None:
            try:
                frame_ref = f"f{list(page.frames).index(frame)}"
            except ValueError:
                pass
        identity = "\0".join(
            (
                self.session_id,
                str(tab_ref or ""),
                str(self.generation),
                str(index),
                str(request.method),
                str(request.url),
            )
        )
        request_ref = f"req-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        self._network_events.append(
            {
                "request_ref": request_ref,
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "timestamp_ms": int(time.time() * 1000),
                "status": None,
                "tab_ref": tab_ref,
                "frame_ref": frame_ref,
                "capture_generation": self.generation,
            }
        )
        try:
            request._homemaster_request_ref = request_ref
        except Exception:
            pass

    def _capture_response(self, response: Any) -> None:
        request = response.request
        ref = getattr(request, "_homemaster_request_ref", None)
        for item in reversed(self._network_events):
            if ref and item.get("request_ref") == ref:
                item.update(
                    {
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                    }
                )
                self._network_responses[str(ref)] = response
                break

    def _capture_console(self, message: Any) -> None:
        message_type = str(getattr(message, "type", "log"))
        if message_type == "warning":
            message_type = "warning"
        elif message_type not in {"error", "warning", "log", "info", "debug"}:
            message_type = "log"
        index = len(self._console_events) + 1
        try:
            text = str(message.text)
        except Exception:
            text = ""
        self._console_events.append(
            {
                "cursor": f"console-{index}",
                "type": message_type,
                "text": text[:4000],
                "location": getattr(message, "location", {}),
                "timestamp_ms": int(time.time() * 1000),
                "tab_ref": self._active_tab_ref(),
                "frame_ref": "f0",
            }
        )

    def _capture_download(self, download: Any) -> None:
        self._downloads.append(
            {
                "suggested_filename": download.suggested_filename,
                "timestamp_ms": int(time.time() * 1000),
            }
        )

    async def _has_exact_image_preview(self, expected_data_url: str) -> bool:
        return bool(
            await self._page.locator("img").evaluate_all(
                "(images, expected) => images.some((image) => image.src === expected)",
                expected_data_url,
            )
        )

    async def _route_request(self, route: Any, request: Any) -> None:
        is_main_navigation = False
        if request.is_navigation_request():
            try:
                frame = request.frame
            except Exception as exc:
                if type(exc).__module__.split(".")[0] != "playwright":
                    raise
                is_main_navigation = True
            else:
                is_main_navigation = frame.parent_frame is None
        if is_main_navigation:
            try:
                self.policy.validate_final_url(request.url)
            except BrowserSessionError as exc:
                self._origin_violation = exc
                self._fenced = True
                self._snapshots.invalidate()
                self._write_event(
                    "origin_violation",
                    {"url": request.url},
                    error_code=exc.code,
                    outcome="blocked",
                )
                await route.abort()
                return
        await route.continue_()

    async def _wait_for_dom_stable(self) -> bool:
        started = time.monotonic()
        deadline = started + self.policy.navigation_timeout_ms / 1000
        last_hash = ""
        stable_since = started
        while time.monotonic() < deadline:
            current_hash = await self._dom_hash()
            has_rendered_content = bool(
                await self._page.evaluate(
                    """() => {
                      const body = document.body;
                      if (!body) return false;
                      if ((body.innerText || '').trim()) return true;
                      return Boolean(body.querySelector(
                        'input, textarea, select, button, a[href], img, svg, canvas, video, iframe'
                      ));
                    }"""
                )
            )
            now = time.monotonic()
            if current_hash != last_hash:
                last_hash = current_hash
                stable_since = now
            if has_rendered_content and now - started >= 0.4 and now - stable_since >= 0.3:
                return True
            await self._page.wait_for_timeout(50)
        return False

    async def _execute(
        self,
        operation: str,
        arguments: Mapping[str, object],
        action: Callable[[], Awaitable[T]],
        *,
        timeout_ms: int,
        mutating: bool,
    ) -> T:
        async with self._lock:
            self._require_available()
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(action(), timeout=timeout_ms / 1000)
            except asyncio.CancelledError:
                if mutating:
                    self._fenced = True
                    self._snapshots.invalidate()
                self._write_event(
                    operation,
                    arguments,
                    started=started,
                    error_code="execution_cancelled",
                    outcome="outcome_unknown" if mutating else "cancelled",
                )
                raise
            except TimeoutError as exc:
                if mutating:
                    self._fenced = True
                    self._snapshots.invalidate()
                self._write_event(
                    operation,
                    arguments,
                    started=started,
                    error_code="action_timeout",
                    outcome="outcome_unknown" if mutating else "timeout",
                )
                raise BrowserSessionError(
                    "action_timeout",
                    "browser operation exceeded its infrastructure timeout",
                    backend_attempted=True,
                    outcome_unknown=mutating,
                    hint=(
                        "The page was too slow or too large for this one operation. For reads, "
                        "retry with a narrower scope, fewer elements, or browser_screenshot "
                        "instead. For writes, the session is now fenced: do not retry the write."
                    ),
                ) from exc
            except BrowserSessionError as exc:
                self._write_event(
                    operation,
                    arguments,
                    started=started,
                    error_code=exc.code,
                    outcome="outcome_unknown" if exc.outcome_unknown else "failure",
                )
                raise
            except Exception as exc:
                if self._origin_violation is not None:
                    violation = self._origin_violation
                    self._write_event(
                        operation,
                        arguments,
                        started=started,
                        error_code=violation.code,
                        outcome="failure",
                    )
                    raise violation from exc
                if mutating:
                    self._fenced = True
                    self._snapshots.invalidate()
                self._write_event(
                    operation,
                    arguments,
                    started=started,
                    error_code="browser_action_failed",
                    outcome="outcome_unknown" if mutating else "failure",
                )
                raise BrowserSessionError(
                    "browser_action_failed",
                    f"{type(exc).__name__}: {exc}",
                    backend_attempted=True,
                    outcome_unknown=mutating,
                ) from exc
            self._write_event(operation, arguments, started=started, result=result)
            return result

    def _require_available(self) -> None:
        if not self._started:
            raise BrowserSessionError("session_not_started", "browser session is not started")
        if self._closed:
            raise BrowserSessionError("session_closed", "browser session is closed")
        if self._fenced:
            raise BrowserSessionError(
                "session_fenced",
                "browser session was fenced after an uncertain operation",
                hint="A previous write had an unknown outcome, so this session refuses further browser actions. Report the fenced session and the last attempted action; do not retry writes through a new session.",
            )
        if self._page is not None and self._page.url != "about:blank":
            try:
                self.policy.validate_final_url(self._page.url)
            except BrowserSessionError:
                self._fenced = True
                self._snapshots.invalidate()
                raise

    async def _cleanup_started_resources(self, *, save_artifacts: bool) -> None:
        if self._context is not None:
            if save_artifacts:
                try:
                    await self._context.tracing.stop(path=str(self.trace_path))
                except Exception as exc:
                    self._write_event("trace_stop_failed", {"error": str(exc)})
            try:
                await self._context.close()
            finally:
                self._context = None
        if self._video is not None and save_artifacts:
            try:
                self.video_path = Path(await self._video.path())
            except Exception as exc:
                self._write_event("video_path_failed", {"error": str(exc)})
        self._video = None
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

    def _receipt(self, element: BrowserElement, **values: object) -> dict[str, object]:
        return {
            "target": {
                "element_id": element.element_id,
                "control_type": element.control_type,
                "role": element.role,
                "name": element.name,
                "frame_id": element.frame_id,
            },
            **values,
        }

    def _write_event(
        self,
        operation: str,
        arguments: Mapping[str, object],
        *,
        started: float | None = None,
        result: object | None = None,
        error_code: str | None = None,
        outcome: str = "success",
    ) -> None:
        self.video_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_ns": time.time_ns(),
            "session_id": self.session_id,
            "operation": operation,
            "arguments": dict(arguments),
            "duration_ms": None if started is None else (time.monotonic() - started) * 1000,
            "outcome": outcome,
            "error_code": error_code,
            "result": _public_result(result),
            "generation": self.generation,
            "fenced": self._fenced,
        }
        with self.action_log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
            stream.write("\n")


def _temporary_element(handle: Any) -> BrowserElement:
    return BrowserElement(
        element_id="temporary",
        tag="",
        control_type="",
        role="",
        name="",
        label="",
        text="",
        value=None,
        frame_id="",
        visible=True,
        enabled=True,
        editable=False,
        handle=handle,
    )


def _public_result(value: object | None) -> object | None:
    if isinstance(value, BrowserSnapshot):
        return value.to_public_dict()
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, bytes):
        return {"byte_count": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    return value


def _png_dimensions(value: bytes) -> tuple[int, int]:
    if len(value) < 24 or not value.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BrowserSessionError(
            "invalid_screenshot", "browser screenshot did not return a valid PNG"
        )
    return int.from_bytes(value[16:20], "big"), int.from_bytes(value[20:24], "big")


def _redact_snapshot_text(value: str) -> str:
    """Redact common credential-bearing attributes from model-visible snapshots."""

    patterns = (
        r"(?i)(authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|cookie)\s*="
        r"\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
        r"(?i)(bearer)\s+[A-Za-z0-9._~+/=-]{8,}",
    )
    redacted = value
    redacted = re.sub(patterns[0], r"\1=\"[REDACTED]\"", redacted)
    redacted = re.sub(patterns[1], r"\1 [REDACTED]", redacted)
    return redacted


def _has_stable_identity(element: BrowserElement, state: Mapping[str, object]) -> bool:
    stable_id = str(state.get("stableId", ""))
    test_id = str(state.get("testId", ""))
    strong_identity = bool(
        (element.stable_id and stable_id == element.stable_id)
        or (element.testid and test_id == element.testid)
    )
    return (
        strong_identity
        and str(state.get("tag", "")) == element.tag
        and str(state.get("role", "")) == element.role
    )


class _MarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.link_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(f"\n\n{'#' * int(tag[1])} ")
        elif tag in {"p", "div", "section", "article", "tr"}:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            self.parts.append("[")
            self.link_stack.append(dict(attrs).get("href") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            self.parts.append(f"]({href})" if href else "]")

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if normalized:
            self.parts.append(normalized)


def _html_to_markdown(value: str) -> str:
    parser = _MarkdownParser()
    parser.feed(value)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


__all__ = ["PlaywrightBrowserSession"]
