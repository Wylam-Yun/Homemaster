"""Headed Playwright driver restricted to the three Agent-visible pages."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient


class BrowserDriver(Protocol):
    def navigate(self, route: str, action_id: str) -> dict[str, Any]: ...

    def observe(self, action_id: str) -> dict[str, Any]: ...

    def click(self, bid: str, action_id: str) -> dict[str, Any]: ...

    def fill(self, bid: str, value: str, action_id: str) -> dict[str, Any]: ...

    def select(self, bid: str, value: str, action_id: str) -> dict[str, Any]: ...

    def wait_for_job(self, job_id: str, action_id: str, timeout_s: float) -> dict[str, Any]: ...


class PlaywrightBrowserDriver:
    allowed_routes = {"ticket", "monitor", "automation"}

    def __init__(
        self,
        *,
        run_id: str,
        base_url: str,
        display: str,
        chrome_executable: Path,
        profile_dir: Path,
        trace_path: Path,
        client: EnvironmentClient,
        timeout_s: float = 20.0,
        window_x: int = 640,
        window_y: int = 0,
        width: int = 1280,
        height: int = 720,
    ) -> None:
        from playwright.sync_api import sync_playwright

        self.run_id = run_id
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.timeout_ms = int(timeout_s * 1000)
        self.trace_path = trace_path
        profile_dir.mkdir(parents=True, exist_ok=True)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=str(chrome_executable),
            headless=False,
            env={**os.environ, "DISPLAY": display},
            args=[
                "--disable-dev-shm-usage",
                "--no-first-run",
                f"--window-position={window_x},{window_y}",
                f"--window-size={width},{height}",
            ],
            viewport={"width": width, "height": height},
        )
        self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def navigate(self, route: str, action_id: str) -> dict[str, Any]:
        if route not in self.allowed_routes:
            raise ValueError("navigation is restricted to ticket, monitor and automation")
        version = self.client.state(self.run_id)["state_version"]
        self.client.reserve(self.run_id, action_id, "browser_navigate", version)
        url = f"{self.base_url}/{route}/{self.run_id}"
        self.page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
        recorded = self.client.record_action(
            self.run_id,
            action_id=action_id,
            tool_name="browser_navigate",
            version=version,
            arguments={"route": route, "url": url},
            node_id="TICKET_READ" if route == "ticket" else None,
        )
        observation = self._observation()
        observation["evidence_refs"] = [recorded["event"]["event_id"]]
        return observation

    def observe(self, action_id: str) -> dict[str, Any]:
        self._require_agent_page()
        version = self.client.state(self.run_id)["state_version"]
        self.client.reserve(self.run_id, action_id, "browser_observe", version)
        recorded = self.client.record_action(
            self.run_id,
            action_id=action_id,
            tool_name="browser_observe",
            version=version,
            arguments={"url": self.page.url},
        )
        observation = self._observation()
        observation["evidence_refs"] = [recorded["event"]["event_id"]]
        return observation

    def click(self, bid: str, action_id: str) -> dict[str, Any]:
        self._require_agent_page()
        locator = self.page.locator(f'[data-bid="{bid}"]')
        self._unique_actionable(locator, bid)
        self._prepare_receipt(bid)
        state = self.client.state(self.run_id)
        version = int(state["state_version"])
        self.client.reserve(self.run_id, action_id, "browser_click", version)
        self.page.evaluate(
            "([id, version]) => { window.__coworkerAction = {id, version}; }",
            [action_id, version],
        )
        locator.click(timeout=self.timeout_ms)
        receipt = self._wait_for_receipt(bid)
        if receipt.get("error"):
            raise RuntimeError(receipt["error"])
        return {
            "url": self.page.url,
            "bid": bid,
            "receipt": receipt,
            "page_state_version": self.client.state(self.run_id)["state_version"],
            "evidence_refs": receipt.get("evidence_refs", []),
        }

    def fill(self, bid: str, value: str, action_id: str) -> dict[str, Any]:
        self._require_agent_page()
        locator = self.page.locator(f'[data-bid="{bid}"]')
        self._unique_actionable(locator, bid)
        version = self.client.state(self.run_id)["state_version"]
        self.client.reserve(self.run_id, action_id, "browser_fill", version)
        locator.fill(value, timeout=self.timeout_ms)
        readback = locator.input_value()
        if readback != value:
            raise RuntimeError("fill readback differs from requested value")
        return self._record_readback("browser_fill", bid, value, action_id)

    def select(self, bid: str, value: str, action_id: str) -> dict[str, Any]:
        self._require_agent_page()
        locator = self.page.locator(f'[data-bid="{bid}"]')
        self._unique_actionable(locator, bid)
        version = self.client.state(self.run_id)["state_version"]
        self.client.reserve(self.run_id, action_id, "browser_select", version)
        locator.select_option(value=value, timeout=self.timeout_ms)
        readback = locator.input_value()
        if readback != value:
            raise RuntimeError("select readback differs from requested value")
        return self._record_readback("browser_select", bid, value, action_id)

    def wait_for_job(self, job_id: str, action_id: str, timeout_s: float) -> dict[str, Any]:
        self._require_agent_page()
        locator = self.page.locator(f'tr[data-job-id="{job_id}"]')
        if locator.count() != 1:
            raise ValueError(f"job row is not uniquely visible: {job_id}")
        version = self.client.state(self.run_id)["state_version"]
        self.client.reserve(self.run_id, action_id, "browser_wait", version)
        self.page.wait_for_function(
            "jobId => { const row = document.querySelector("
            '`tr[data-job-id="${jobId}"]`); return row && '
            "['succeeded', 'failed'].includes(row.dataset.jobStatus); }",
            arg=job_id,
            timeout=int(timeout_s * 1000),
        )
        cells = locator.locator("td").all_text_contents()
        operation, status = cells[1], cells[2]
        if status != "succeeded":
            raise RuntimeError(f"job {job_id} reached {status}")
        node_id = {
            "add": "ADD_WAIT",
            "remove": "REMOVE_WAIT",
            "business_verify": "BUSINESS_WAIT",
        }[operation]
        version = self.client.state(self.run_id)["state_version"]
        recorded = self.client.record_action(
            self.run_id,
            action_id=action_id,
            tool_name="browser_wait",
            version=version,
            arguments={
                "job_id": job_id,
                "operation": operation,
                "target_status": "terminal",
                "status": status,
            },
            node_id=node_id,
        )
        return {
            "job_id": job_id,
            "operation": operation,
            "status": status,
            "page_state_version": version,
            "evidence_refs": [recorded["event"]["event_id"]],
        }

    def close(self) -> None:
        try:
            self._context.tracing.stop(path=str(self.trace_path))
        finally:
            self._context.close()
            self._playwright.stop()

    def _record_readback(
        self, tool_name: str, bid: str, value: str, action_id: str
    ) -> dict[str, Any]:
        version = self.client.state(self.run_id)["state_version"]
        recorded = self.client.record_action(
            self.run_id,
            action_id=action_id,
            tool_name=tool_name,
            version=version,
            arguments={"bid": bid, "value": value, "readback": value},
        )
        return {
            "bid": bid,
            "value": value,
            "readback": value,
            "page_state_version": version,
            "evidence_refs": [recorded["event"]["event_id"]],
        }

    def _wait_for_receipt(self, bid: str) -> dict[str, Any]:
        if bid.startswith("ticket-query-"):
            selector = "#ticket-receipt"
        elif bid.startswith("monitor-query-"):
            selector = "#monitor-result"
        elif bid == "automation-submit":
            selector = "#automation-receipt"
        else:
            raise ValueError(f"click target is not a backend-mutating control: {bid}")
        self.page.wait_for_function(
            "selector => { const el = document.querySelector(selector); "
            "return el && (el.classList.contains('success') || "
            "el.classList.contains('error') || el.dataset.evidenceRefs); }",
            arg=selector,
            timeout=self.timeout_ms,
        )
        element = self.page.locator(selector)
        text = element.inner_text()
        classes = element.get_attribute("class") or ""
        evidence = (element.get_attribute("data-evidence-refs") or "").split(",")
        if "error" in classes:
            return {"error": text, "evidence_refs": []}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"text": text}
        return {"payload": payload, "evidence_refs": [item for item in evidence if item]}

    def _prepare_receipt(self, bid: str) -> None:
        if bid.startswith("ticket-query-"):
            selector = "#ticket-receipt"
        elif bid.startswith("monitor-query-"):
            selector = "#monitor-result"
        elif bid == "automation-submit":
            selector = "#automation-receipt"
        else:
            return
        self.page.locator(selector).evaluate(
            "el => { delete el.dataset.evidenceRefs; "
            "el.classList.remove('success', 'error'); el.textContent = 'waiting'; }"
        )

    def _observation(self) -> dict[str, Any]:
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "visible_text": self.page.locator("body").inner_text(timeout=self.timeout_ms),
            "controls": self.page.locator("[data-bid]").evaluate_all(
                "els => els.map(el => ({bid: el.dataset.bid, "
                "text: el.innerText || el.value || '', disabled: !!el.disabled}))"
            ),
        }

    def _require_agent_page(self) -> None:
        allowed = {f"{self.base_url}/{route}/{self.run_id}" for route in self.allowed_routes}
        if self.page.url.rstrip("/") not in {url.rstrip("/") for url in allowed}:
            raise ValueError("current page is outside the Agent navigation allowlist")

    @staticmethod
    def _unique_actionable(locator: Any, bid: str) -> None:
        if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
            raise ValueError(f"data-bid must be unique, visible and enabled: {bid}")
