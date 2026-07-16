from __future__ import annotations

import pytest
from case02_openenv.automation import AutomationEngine
from case02_openenv.episode_store import EpisodeError, EpisodeStore
from case02_openenv.terminal.executor import TerminalExecutor
from case02_openenv.terminal.policy import CommandPolicy

from tests.case02_openenv.test_automation import wait_job
from tests.case02_openenv.test_episode_store import (
    complete_prechecks,
    record_progress,
    reserve,
)


def test_real_tmux_bash_bubblewrap_executor_proves_add_state(store: EpisodeStore) -> None:
    run_id = "terminal-product"
    store.create(run_id, "normal")
    complete_prechecks(store, run_id)
    record_progress(store, run_id, "PRE_PROGRESS")
    state = store.state(run_id)
    action, version = reserve(store, run_id, "browser_click", "submit")
    job = AutomationEngine(store, settle_delay_s=0.01).submit(
        run_id,
        action_id=action,
        page_state_version=version,
        script="svc_cfg_cli_runner",
        operation="add",
        parameters=state.variables,
    )
    wait_job(store, run_id, job.job_id)

    action, version = reserve(store, run_id, "terminal_execute", "terminal-too-early")
    policy = CommandPolicy(state.variables["TenantId"], state.variables["ItemCode"])
    with pytest.raises(EpisodeError, match=f"browser_wait.*{job.job_id}"):
        TerminalExecutor(store, timeout_s=10).execute(
            run_id,
            action_id=action,
            page_state_version=version,
            command=policy.exact_command,
        )
    assert not (store.episode(run_id).run_root / "terminal").exists()

    store.record(
        run_id,
        source="browser",
        kind="browser_action",
        status="succeeded",
        action_id="wait-add",
        arguments={"tool_name": "browser_wait", "job_id": job.job_id},
        node_id="ADD_WAIT",
    )
    action, version = reserve(store, run_id, "terminal_execute", "terminal")
    result = TerminalExecutor(store, timeout_s=10).execute(
        run_id,
        action_id=action,
        page_state_version=version,
        command=policy.exact_command,
    )
    assert result["exit_code"] == 0
    assert all(value in result["stdout"] for value in state.variables.values())
    assert all(code == 0 for code in result["tmux_returncodes"].values())
    assert store.state(run_id).add_grep_evidence_id == result["evidence_id"]
