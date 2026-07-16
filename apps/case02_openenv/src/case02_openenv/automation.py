"""Asynchronous automation jobs with external configuration mutation."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from case02_openenv.artifacts import atomic_write_json
from case02_openenv.episode_store import EpisodeError, EpisodeStore
from case02_openenv.models import AutomationJob, JobStatus


class AutomationEngine:
    def __init__(self, store: EpisodeStore, *, settle_delay_s: float = 0.15) -> None:
        self.store = store
        self.settle_delay_s = settle_delay_s

    def submit(
        self,
        run_id: str,
        *,
        action_id: str,
        page_state_version: int,
        script: str,
        operation: str,
        parameters: dict[str, str],
    ) -> AutomationJob:
        episode = self.store.episode(run_id)
        with episode.lock:
            self.store.consume_action(run_id, action_id, page_state_version, "browser_click")
            self._validate(episode.state, script, operation, parameters)
            job = AutomationJob(
                job_id=f"job-{operation}-{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                action_id=action_id,
                operation=operation,
                submitted_payload=dict(parameters),
            )
            self.store.add_job(run_id, job)
            thread = threading.Thread(target=self._run, args=(run_id, job.job_id), daemon=True)
            thread.start()
            return job.model_copy(deep=True)

    def _run(self, run_id: str, job_id: str) -> None:
        self.store.update_job(run_id, job_id, JobStatus.RUNNING, None)
        time.sleep(self.settle_delay_s)
        episode = self.store.episode(run_id)
        job = episode.state.jobs[job_id]
        try:
            with episode.lock:
                payload = json.loads(episode.config_file.read_text(encoding="utf-8"))
                variables = episode.state.variables
                key = f"{variables['TenantId']}:{variables['ItemCode']}"
                if job.operation == "add":
                    payload[key] = {
                        "TenantId": variables["TenantId"],
                        "ItemCode": variables["ItemCode"],
                        "SpecCode": variables["SpecCode"],
                        "ExtensionName": variables["ExtensionName"],
                    }
                    atomic_write_json(episode.config_file, payload)
                elif job.operation == "remove":
                    payload.pop(key, None)
                    atomic_write_json(episode.config_file, payload)
                elif job.operation == "business_verify":
                    if key not in payload:
                        raise RuntimeError("configuration is absent")
                    episode.state.business_verified = True
            self.store.update_job(run_id, job_id, JobStatus.SUCCEEDED, 0)
        except Exception:
            self.store.update_job(run_id, job_id, JobStatus.FAILED, 1)

    @staticmethod
    def _validate(state: Any, script: str, operation: str, parameters: dict[str, str]) -> None:
        expected_script = (
            "svc_usage_record_fetcher" if operation == "business_verify" else "svc_cfg_cli_runner"
        )
        if script != expected_script:
            raise EpisodeError("script_operation_mismatch", "script does not support the operation")
        if operation == "add":
            if state.phase.value != "ready_to_change":
                raise EpisodeError(
                    "pre_gate_not_satisfied", "add requires the precheck proceed gate"
                )
            expected = state.variables
        elif operation == "remove":
            if state.phase.value != "rollback_submitted":
                raise EpisodeError("rollback_not_authorized", "remove requires a rollback decision")
            expected = {key: state.variables[key] for key in ("TenantId", "ItemCode")}
        else:
            if state.phase.value not in {"change_applied", "verifying"}:
                raise EpisodeError("invalid_phase", "business verification requires applied change")
            expected = {
                "resource_bucket": state.target["resource_bucket"],
                "business_timestamp": state.target["business_timestamp"],
                "factor": "0",
            }
        if any(parameters.get(key) != value for key, value in expected.items()):
            raise EpisodeError(
                "parameter_mismatch", "submitted parameters differ from locked values"
            )
