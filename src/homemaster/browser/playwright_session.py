"""The single Playwright-owned browser implementation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from homemaster.browser.contracts import (
    BrowserElement,
    BrowserSessionError,
    BrowserSnapshot,
)
from homemaster.browser.inspection import (
    collect_elements,
    current_state,
    filter_elements,
    fingerprint_from_state,
)
from homemaster.browser.policy import BrowserPolicy
from homemaster.browser.targets import SnapshotStore, TargetResolutionError

T = TypeVar("T")


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
            await self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._page = await self._context.new_page()
            self._video = self._page.video
            self._started = True
            self._write_event("session_started", {"headless": self.headless})
        except BaseException:
            await self._cleanup_started_resources(save_artifacts=False)
            raise

    async def navigate(self, url: str) -> Mapping[str, object]:
        requested = self.policy.validate_initial_url(url)

        async def action() -> Mapping[str, object]:
            before = self._page.url
            response = await self._page.goto(
                requested,
                wait_until="domcontentloaded",
                timeout=self.policy.navigation_timeout_ms,
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
            elements, total, frames = await collect_elements(
                self._page, limit=self.policy.max_elements
            )
            selected = filter_elements(elements, filters)
            selected_total = len(selected)
            selected = selected[:limit]
            body_text = await self._page.locator("body").inner_text()
            text = body_text[: self.policy.max_text_chars]
            return self._snapshots.replace(
                generation=self.generation,
                url=self._page.url,
                title=await self._page.title(),
                text=text,
                elements=selected,
                total_matches=selected_total
                if any(filters.get(key) for key in ("role", "name", "label", "text"))
                else total,
                truncated=(selected_total > len(selected) or total > len(elements)),
                frames=frames,
            )

        return await self._execute(
            "inspect",
            dict(filters),
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=False,
        )

    async def fill(self, snapshot_id: str, element_id: str, value: str) -> Mapping[str, object]:
        async def action() -> Mapping[str, object]:
            element, state = await self._target(snapshot_id, element_id)
            if not bool(state["editable"]) or element.control_type not in {
                "input",
                "text",
                "email",
                "number",
                "search",
                "tel",
                "url",
                "textarea",
                "contenteditable",
            }:
                raise BrowserSessionError("unsupported_control", "target is not editable")
            await element.handle.fill(value, timeout=self.policy.action_timeout_ms)
            actual_state = await current_state(element)
            actual = str(actual_state.get("value") or "")
            self._snapshots.invalidate()
            if actual != value:
                raise BrowserSessionError(
                    "readback_mismatch",
                    "filled value did not match DOM readback",
                    details={"expected": value, "actual": actual},
                    backend_attempted=True,
                )
            return self._receipt(
                element,
                expected=value,
                actual=actual,
                verified=True,
                match="exact",
            )

        return await self._execute(
            "fill",
            {"snapshot_id": snapshot_id, "element_id": element_id, "value": value},
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def select(self, snapshot_id: str, element_id: str, option: str) -> Mapping[str, object]:
        async def action() -> Mapping[str, object]:
            element, state = await self._target(snapshot_id, element_id)
            if element.tag == "select":
                candidates = [
                    item
                    for item in element.options
                    if str(item.get("label", "")).casefold() == option.casefold()
                    or str(item.get("value", "")).casefold() == option.casefold()
                ]
                unique = {
                    (str(item.get("label", "")), str(item.get("value", ""))): item
                    for item in candidates
                }
                if len(unique) != 1:
                    raise BrowserSessionError(
                        "option_not_unique",
                        "option must match exactly one label or value",
                        details={"available": [dict(item) for item in element.options[:20]]},
                    )
                chosen = next(iter(unique.values()))
                await element.handle.select_option(
                    value=str(chosen["value"]), timeout=self.policy.action_timeout_ms
                )
                actual_state = await current_state(element)
                actual = str(actual_state.get("value", ""))
                expected = str(chosen["value"])
            elif element.role == "combobox":
                await element.handle.click(timeout=self.policy.action_timeout_ms)
                matches: list[tuple[Any, dict[str, object]]] = []
                for frame in self._page.frames:
                    for handle in await frame.query_selector_all('[role="option"]'):
                        option_state = dict(await current_state(_temporary_element(handle)))
                        if (
                            bool(option_state.get("visible"))
                            and str(option_state.get("name", "")).casefold() == option.casefold()
                        ):
                            matches.append((handle, option_state))
                if len(matches) != 1:
                    raise BrowserSessionError(
                        "option_not_unique",
                        "ARIA option must match exactly one accessible name",
                        details={"match_count": len(matches)},
                        backend_attempted=True,
                    )
                await matches[0][0].click(timeout=self.policy.action_timeout_ms)
                actual_state = await current_state(element)
                actual = str(actual_state.get("value") or actual_state.get("text") or "")
                expected = option
                if option.casefold() not in actual.casefold():
                    raise BrowserSessionError(
                        "readback_mismatch",
                        "selected option did not match combobox DOM readback",
                        details={"expected": option, "actual": actual},
                        backend_attempted=True,
                    )
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
                match="exact",
            )

        return await self._execute(
            "select",
            {"snapshot_id": snapshot_id, "element_id": element_id, "option": option},
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def check(self, snapshot_id: str, element_id: str) -> Mapping[str, object]:
        return await self._set_checked(snapshot_id, element_id, desired=True)

    async def uncheck(self, snapshot_id: str, element_id: str) -> Mapping[str, object]:
        return await self._set_checked(snapshot_id, element_id, desired=False)

    async def click(self, snapshot_id: str, element_id: str) -> Mapping[str, object]:
        async def action() -> Mapping[str, object]:
            element, _ = await self._target(snapshot_id, element_id)
            before_url = self._page.url
            before_hash = await self._dom_hash()
            await element.handle.click(timeout=self.policy.action_timeout_ms)
            await self._page.wait_for_timeout(50)
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
            )

        return await self._execute(
            "click",
            {"snapshot_id": snapshot_id, "element_id": element_id},
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=True,
        )

    async def backfill(self, snapshot_id: str, element_id: str) -> Mapping[str, object]:
        async def action() -> Mapping[str, object]:
            element, state = await self._target(snapshot_id, element_id)
            if not bool(state["editable"]) or element.control_type not in {
                "input",
                "text",
                "textarea",
                "contenteditable",
            }:
                raise BrowserSessionError(
                    "unsupported_control", "target cannot receive a clipboard backfill"
                )
            png = await self._page.screenshot(type="png", timeout=self.policy.action_timeout_ms)
            png_base64 = base64.b64encode(png).decode("ascii")
            expected_data_url = f"data:image/png;base64,{png_base64}"
            expected_sha256 = hashlib.sha256(png).hexdigest()
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
                preview_match=True,
                preview_sha256=expected_sha256,
                clipboard_file_count=int(paste["clipboard_file_count"]),
                clipboard_item_count=int(paste["clipboard_item_count"]),
                paste_accepted=True,
                dom_changed=True,
            )

        return await self._execute(
            "backfill",
            {"snapshot_id": snapshot_id, "element_id": element_id},
            action,
            timeout_ms=self.policy.action_timeout_ms + 1_000,
            mutating=True,
        )

    async def wait(self, condition: Mapping[str, object]) -> Mapping[str, object]:
        kind = str(condition.get("kind", ""))
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

    async def screenshot(self) -> bytes:
        async def action() -> bytes:
            return await self._page.screenshot(type="png", timeout=self.policy.action_timeout_ms)

        return await self._execute(
            "screenshot",
            {},
            action,
            timeout_ms=self.policy.action_timeout_ms,
            mutating=False,
        )

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
        self, snapshot_id: str, element_id: str
    ) -> tuple[BrowserElement, dict[str, object]]:
        try:
            element = self._snapshots.resolve(snapshot_id, element_id, generation=self.generation)
        except TargetResolutionError as exc:
            raise BrowserSessionError(exc.code, str(exc)) from exc
        connected = bool(await element.handle.evaluate("el => el.isConnected"))
        if not connected:
            raise BrowserSessionError("stale_ref", "target element is detached")
        await element.handle.scroll_into_view_if_needed(timeout=self.policy.action_timeout_ms)
        state = await current_state(element)
        current_fingerprint = fingerprint_from_state(state, frame_id=element.frame_id)
        if current_fingerprint != element.fingerprint:
            raise BrowserSessionError("stale_ref", "target identity changed after inspection")
        if not bool(state.get("visible")):
            raise BrowserSessionError("target_not_visible", "target is not visible")
        if not bool(state.get("enabled")):
            raise BrowserSessionError("target_disabled", "target is disabled")
        if bool(state.get("obscured")):
            raise BrowserSessionError("target_obscured", "target is obscured")
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
                raise BrowserSessionError(exc.code, str(exc)) from exc
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
        raise BrowserSessionError("invalid_condition", f"unsupported wait condition: {kind}")

    async def _dom_hash(self) -> str:
        content = await self._page.content()
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def _has_exact_image_preview(self, expected_data_url: str) -> bool:
        return bool(
            await self._page.locator("img").evaluate_all(
                "(images, expected) => images.some((image) => image.src === expected)",
                expected_data_url,
            )
        )

    async def _route_request(self, route: Any, request: Any) -> None:
        if request.is_navigation_request() and request.frame.parent_frame is None:
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
        deadline = started + min(self.policy.navigation_timeout_ms / 1000, 5.0)
        last_hash = ""
        stable_since = started
        while time.monotonic() < deadline:
            current_hash = await self._dom_hash()
            now = time.monotonic()
            if current_hash != last_hash:
                last_hash = current_hash
                stable_since = now
            if now - started >= 0.4 and now - stable_since >= 0.3:
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
                    "browser operation exceeded its infrastructure timeout; session fenced",
                    backend_attempted=True,
                    outcome_unknown=mutating,
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


__all__ = ["PlaywrightBrowserSession"]
