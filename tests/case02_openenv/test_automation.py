from __future__ import annotations

import time

from case02_openenv.automation import AutomationEngine
from case02_openenv.episode_store import EpisodeStore
from case02_openenv.models import JobStatus

from tests.case02_openenv.test_episode_store import complete_prechecks, record_progress, reserve


def wait_job(store: EpisodeStore, run_id: str, job_id: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if store.job(run_id, job_id).status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            return
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_add_job_mutates_only_after_success(store: EpisodeStore) -> None:
    store.create("job-run", "normal")
    complete_prechecks(store, "job-run")
    record_progress(store, "job-run", "PRE_PROGRESS")
    engine = AutomationEngine(store, settle_delay_s=0.02)
    state = store.state("job-run")
    action, version = reserve(store, "job-run", "browser_click", "submit-add")
    job = engine.submit(
        "job-run",
        action_id=action,
        page_state_version=version,
        script="svc_cfg_cli_runner",
        operation="add",
        parameters=state.variables,
    )
    wait_job(store, "job-run", job.job_id)
    assert store.job("job-run", job.job_id).status == JobStatus.SUCCEEDED
    assert store.config_contains_target("job-run")


def test_jobs_are_namespaced_by_run(store: EpisodeStore) -> None:
    store.create("jobs-a", "normal")
    store.create("jobs-b", "normal")
    complete_prechecks(store, "jobs-a")
    record_progress(store, "jobs-a", "PRE_PROGRESS")
    state = store.state("jobs-a")
    action, version = reserve(store, "jobs-a", "browser_click", "submit")
    job = AutomationEngine(store, settle_delay_s=0.01).submit(
        "jobs-a",
        action_id=action,
        page_state_version=version,
        script="svc_cfg_cli_runner",
        operation="add",
        parameters=state.variables,
    )
    wait_job(store, "jobs-a", job.job_id)
    assert job.job_id not in store.state("jobs-b").jobs
