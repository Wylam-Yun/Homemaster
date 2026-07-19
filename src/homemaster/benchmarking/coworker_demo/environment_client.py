"""Typed HTTP client and service-process lifecycle for case02_openenv."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.config import CoworkerConfig
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome


class EnvironmentClientError(RuntimeError):
    pass


class EnvironmentClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 20.0,
        budget: CoworkerBudget | None = None,
        outcome: CoworkerOutcome | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.budget = budget
        self.outcome = outcome or CoworkerOutcome()
        self._client = httpx.Client(base_url=self.base_url, transport=transport)

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/healthz", check_budget=False)

    def create_run(
        self,
        run_id: str,
        scenario_id: str,
        locked_hashes: dict[str, str],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/runs",
            json={
                "run_id": run_id,
                "scenario_id": scenario_id,
                "locked_hashes": locked_hashes,
            },
        )

    def reset(self, run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/runs/{run_id}/reset")

    def state(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/runs/{run_id}/state")["state"]

    def audit(self, run_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/runs/{run_id}/audit")["events"]

    def presentation_event(
        self, run_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runs/{run_id}/presentation-events",
            json=payload,
            check_budget=False,
        )

    def reserve(self, run_id: str, action_id: str, tool_name: str, version: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runs/{run_id}/action-events",
            json={
                "operation": "reserve",
                "action_id": action_id,
                "tool_name": tool_name,
                "page_state_version": version,
            },
        )

    def record_action(
        self,
        run_id: str,
        *,
        action_id: str,
        tool_name: str,
        version: int,
        arguments: dict[str, Any],
        node_id: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runs/{run_id}/action-events",
            json={
                "operation": "record",
                "action_id": action_id,
                "tool_name": tool_name,
                "page_state_version": version,
                "arguments": arguments,
                "node_id": node_id,
                "evidence_refs": evidence_refs or [],
            },
        )

    def runtime_event(
        self,
        run_id: str,
        *,
        action_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
        node_id: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/runs/{run_id}/runtime-events",
            json={
                "action_id": action_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "node_id": node_id,
                "evidence_refs": evidence_refs or [],
            },
        )

    def terminal(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/api/runs/{run_id}/terminal", json=payload)

    def decision(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/api/runs/{run_id}/decisions", json=payload)

    def job(self, run_id: str, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/runs/{run_id}/automation/jobs/{job_id}")

    def finalize(self, run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/runs/{run_id}/finalize", check_budget=False)

    def start_recording(self, run_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/runs/{run_id}/recording/start", timeout_s=45.0
        )

    def recording_status(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/runs/{run_id}/recording", check_budget=False)

    def stop_recording(self, run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/runs/{run_id}/recording/stop", check_budget=False)

    def scores(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/runs/{run_id}/scores", check_budget=False)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        check_budget: bool = True,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        request_timeout_s = timeout_s if timeout_s is not None else self.timeout_s
        if check_budget and self.budget is not None:
            self.budget.before_external(self.outcome)
            timeout = self.budget.timeout(request_timeout_s)
        else:
            timeout = request_timeout_s
        try:
            response = self._client.request(method, path, json=json, timeout=timeout)
        except httpx.HTTPError as exc:
            raise EnvironmentClientError(
                f"environment request failed: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise EnvironmentClientError(
                f"environment returned non-JSON HTTP {response.status_code}"
            ) from exc
        if response.status_code >= 400 or payload.get("success") is False:
            code = payload.get("error_code", "environment_rejected")
            raise EnvironmentClientError(f"{code}: {payload.get('message', payload)}")
        return payload


class EnvironmentProcess:
    def __init__(self, config: CoworkerConfig, *, log_dir: Path) -> None:
        self.config = config
        self.log_dir = log_dir
        self.process: subprocess.Popen[Any] | None = None
        self.handles: list[Any] = []

    def start(self, client: EnvironmentClient) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout = (self.log_dir / "service.stdout.log").open("w", encoding="utf-8")
        stderr = (self.log_dir / "service.stderr.log").open("w", encoding="utf-8")
        self.handles.extend([stdout, stderr])
        environment = {
            **os.environ,
            "CASE02_DATA_ROOT": str(self.config.paths.data_root),
            "CASE02_ARTIFACT_ROOT": str(self.config.paths.artifact_root),
            "CASE02_BIND_HOST": self.config.service.bind_host,
            "CASE02_PORT": str(self.config.service.port),
        }
        self.process = subprocess.Popen(
            [str(self.config.paths.service_python), "-m", "case02_openenv"],
            cwd=self.config.source_path.parent.parent,
            env=environment,
            stdout=stdout,
            stderr=stderr,
        )
        deadline = time.monotonic() + self.config.service.startup_timeout_s
        last_error = "not attempted"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise EnvironmentClientError(
                    f"environment service exited during startup: {self.process.returncode}"
                )
            try:
                if client.health().get("status") == "ok":
                    return
            except EnvironmentClientError as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise EnvironmentClientError(f"environment health check timed out: {last_error}")

    def stop(self) -> int | None:
        if self.process is None:
            return None
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for handle in self.handles:
            handle.close()
        return self.process.returncode
