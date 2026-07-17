# Change Coworker Executive Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the crowded coworker recording layout with a continuous, leadership-readable recording that shows the real Agent page beside a live, trustworthy SOP/tool/result dashboard.

**Architecture:** Keep the Agent, DOM tools, environment gates, recorder, and evaluator behavior unchanged. Add deterministic run-scoped action correlation, a sanitized presentation-event stream, exact ticket-to-SOP mapping, a read-only executive observer, and a presentation verification gate registered in the run manifest.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Playwright, vanilla HTML/CSS/JavaScript, Server-Sent Events, TigerVNC, FFmpeg, pytest.

---

## File Map

Create these focused units:

- `src/homemaster/benchmarking/coworker_demo/correlation.py`: derive one stable current-run action ID from a model tool-call ID.
- `src/homemaster/benchmarking/coworker_demo/presentation.py`: client-side allowlist projection from runtime events to safe presentation requests.
- `apps/case02_openenv/src/case02_openenv/presentation.py`: presentation models, exact SOP mapping, append-only ledger, snapshots, and verification.
- `tests/homemaster/benchmarking/coworker_demo/test_correlation.py`: dispatcher/correlation lifecycle tests.
- `tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py`: runtime projection and secret-removal tests.
- `tests/case02_openenv/test_presentation.py`: SOP mapping, ledger, correlation, snapshot, and verification tests.

Modify these existing units:

- `src/homemaster/tools/dispatcher.py`: expose only the currently executing model tool-call ID to coworker executors, then restore context.
- `src/homemaster/benchmarking/coworker_demo/browser_tools.py`: replace random browser action IDs with stable correlated IDs.
- `src/homemaster/benchmarking/coworker_demo/terminal_tools.py`: correlate terminal actions.
- `src/homemaster/benchmarking/coworker_demo/decision_tools.py`: correlate SOP decisions.
- `src/homemaster/benchmarking/coworker_demo/registry.py`: correlate mirrored planner/progress events.
- `src/homemaster/benchmarking/coworker_demo/tracing.py`: send safe start/completion/failure/run events to the environment.
- `src/homemaster/benchmarking/coworker_demo/environment_client.py`: add the typed presentation POST client.
- `apps/case02_openenv/src/case02_openenv/episode_store.py`: own one presentation ledger per run and reset it safely.
- `apps/case02_openenv/src/case02_openenv/api.py`: add presentation POST/snapshot/SSE endpoints and use the executive recording session.
- `apps/case02_openenv/templates/observer.html`: replace raw state/audit panes with the executive dashboard.
- `apps/case02_openenv/static/observer.js`: render snapshot/SSE updates with `textContent` only.
- `apps/case02_openenv/static/app.css`: add the 1920x1080 observer background/dashboard layout without changing Agent page contracts.
- `apps/case02_openenv/src/case02_openenv/recording/display.py`: launch the observer full-screen and remove the recording xterm.
- `src/homemaster/benchmarking/coworker_demo/turn.py`: launch Agent Chrome in the fixed left content area.
- `config/coworker_demo.example.yaml`: publish the new Agent window geometry.
- `apps/case02_openenv/src/case02_openenv/evaluation/scoring.py`: register presentation artifacts and separate presentation failure from business/video failure.
- `scripts/coworker_demo/verify_run_bundle.py`: independently recheck presentation completeness and hashes.
- `tests/case02_openenv/test_api_contract.py`: cover presentation routes and SSE resume.
- `tests/case02_openenv/test_pages.py`: cover read-only observer structure and secret isolation.
- `tests/case02_openenv/test_recorder.py`: cover full-screen observer command and no xterm.
- `tests/case02_openenv/test_scoring.py`: cover presentation artifact/formal-success gates.
- `tests/coworker_demo/test_verify_dataset_bundle_stdlib.py`: extend the independent verifier source contract if this is the existing verifier-contract test; otherwise create `tests/coworker_demo/test_verify_run_bundle_presentation.py`.
- `apps/case02_openenv/openapi.json`: refresh the offline API snapshot after route changes.
- `docs/coworker-demo-user-guide.md`: document the leadership recording and artifacts.
- `docs/architecture/coworker-demo.md`: document the observer-only presentation flow and trust boundary.

Do not modify the Agent prompt, tool schemas, DAG requirements, result checkpoints, monitor simulation, or video codec contract.

### Task 1: Stable Run-Scoped Tool Correlation

**Files:**
- Create: `src/homemaster/benchmarking/coworker_demo/correlation.py`
- Modify: `src/homemaster/tools/dispatcher.py:52-135`
- Modify: `src/homemaster/benchmarking/coworker_demo/browser_tools.py:24-33`
- Modify: `src/homemaster/benchmarking/coworker_demo/terminal_tools.py:14-23`
- Modify: `src/homemaster/benchmarking/coworker_demo/decision_tools.py:14-23`
- Modify: `src/homemaster/benchmarking/coworker_demo/registry.py:72-103`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_correlation.py`

- [ ] **Step 1: Write failing tests for stable IDs and dispatcher scoping**

```python
from __future__ import annotations

from types import SimpleNamespace

from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def test_correlated_action_id_is_stable_and_run_scoped() -> None:
    context = RunContext(session_id="s", run_id="run-a", turn_index=0, settings=None)
    context.deps["current_tool_call_id"] = "call-17"
    first = correlated_action_id(context)
    assert first == correlated_action_id(context)
    assert first.startswith("action-")

    other = RunContext(session_id="s", run_id="run-b", turn_index=0, settings=None)
    other.deps["current_tool_call_id"] = "call-17"
    assert correlated_action_id(other) != first


def test_dispatcher_scopes_current_tool_call_id_per_executor() -> None:
    seen: list[str] = []

    def executor(*, arguments, run_context):
        seen.append(run_context.deps["current_tool_call_id"])
        return ToolResult(success=True, tool_name="capture", data={"success": True})

    dispatcher = ToolDispatcher()
    dispatcher.register(
        ToolSpec(
            name="capture",
            input_schema={"type": "object", "properties": {}},
            executor_mode="programmatic",
            executor=executor,
        )
    )
    context = RunContext(session_id="s", run_id="run", turn_index=0, settings=SimpleNamespace())
    dispatcher.dispatch(
        tool_calls=[
            ToolCall(id="call-a", name="capture", arguments={}),
            ToolCall(id="call-b", name="capture", arguments={}),
        ],
        run_context=context,
    )
    assert seen == ["call-a", "call-b"]
    assert "current_tool_call_id" not in context.deps
```

- [ ] **Step 2: Run the tests and verify they fail because the helper/context do not exist**

Run:

```bash
.venv/bin/pytest tests/homemaster/benchmarking/coworker_demo/test_correlation.py -q
```

Expected: collection fails with `ModuleNotFoundError` for `correlation`.

- [ ] **Step 3: Implement the stable helper and scoped dispatcher context**

Create `correlation.py`:

```python
"""Stable correlation between model tool calls and external actions."""

from __future__ import annotations

import uuid

from homemaster.agent.normalized import RunContext


def correlated_action_id(run_context: RunContext) -> str:
    tool_call_id = run_context.deps.get("current_tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise RuntimeError("current model tool_call_id is unavailable")
    seed = f"{run_context.run_id}:{tool_call_id}"
    return f"action-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"
```

In `ToolDispatcher.dispatch`, immediately before `spec.executor(...)`, save and set the scoped value, then restore it in `finally`:

```python
missing = object()
previous_tool_call_id = run_context.deps.get("current_tool_call_id", missing)
run_context.deps["current_tool_call_id"] = tc.id
try:
    if hasattr(token, "enter_tool"):
        token.enter_tool()
    tool_result = spec.executor(arguments=tc.arguments, run_context=run_context)
except Exception as exc:
    tool_result = ToolResult(
        success=False,
        tool_name=tc.name,
        executor_mode=spec.executor_mode,
        failure_reason=f"{type(exc).__name__}: {exc}",
    )
finally:
    if previous_tool_call_id is missing:
        run_context.deps.pop("current_tool_call_id", None)
    else:
        run_context.deps["current_tool_call_id"] = previous_tool_call_id
    if hasattr(token, "exit_tool"):
        token.exit_tool()
```

Replace each coworker `uuid.uuid4()` action creation with:

```python
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id

action_id = correlated_action_id(run_context)
```

In `_wrap_task_tool`, replace the version-derived mirrored action ID with:

```python
action_id=correlated_action_id(run_context),
```

Remove now-unused `uuid` imports.

- [ ] **Step 4: Run focused correlation and existing dispatcher tests**

Run:

```bash
.venv/bin/pytest \
  tests/homemaster/benchmarking/coworker_demo/test_correlation.py \
  tests/homemaster/test_tool_dispatcher.py \
  tests/homemaster/benchmarking/coworker_demo/test_registry.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the correlation slice**

```bash
git add \
  src/homemaster/tools/dispatcher.py \
  src/homemaster/benchmarking/coworker_demo/correlation.py \
  src/homemaster/benchmarking/coworker_demo/browser_tools.py \
  src/homemaster/benchmarking/coworker_demo/terminal_tools.py \
  src/homemaster/benchmarking/coworker_demo/decision_tools.py \
  src/homemaster/benchmarking/coworker_demo/registry.py \
  tests/homemaster/benchmarking/coworker_demo/test_correlation.py
git commit -m "feat(coworker): correlate model tools with external actions"
```

### Task 2: Exact SOP Mapping And Presentation Ledger

**Files:**
- Create: `apps/case02_openenv/src/case02_openenv/presentation.py`
- Modify: `apps/case02_openenv/src/case02_openenv/episode_store.py:35-121`
- Test: `tests/case02_openenv/test_presentation.py`

- [ ] **Step 1: Write failing tests for exact source text, stage disambiguation, and fail-closed mapping**

```python
from __future__ import annotations

import hashlib

import pytest

from case02_openenv.models import EpisodePhase
from case02_openenv.presentation import PresentationInput, PresentationMappingError


def test_monitor_mapping_uses_exact_pre_and_post_sop_source(store) -> None:
    run_id = "presentation-map"
    store.create(run_id, "normal")
    episode = store.episode(run_id)
    pre = store.presentation_task(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="call-pre",
            action_id="action-pre",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "monitor-query-alarm"},
        ),
    )
    assert pre.source_text == episode.ticket["check_before_change"][0]["operate_description"]
    assert pre.source_sha256 == hashlib.sha256(pre.source_text.encode()).hexdigest()

    episode.state.phase = EpisodePhase.CHANGE_APPLIED
    post = store.presentation_task(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="call-post",
            action_id="action-post",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "monitor-query-alarm"},
        ),
    )
    assert post.source_text == episode.ticket["change_verified"][0]["operate_description"]
    assert post.source_text != pre.source_text

    post_stage = store.presentation_stage(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="call-post",
            action_id="action-post",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "monitor-query-alarm"},
        ),
        post,
    )
    assert post_stage == "change_verified"


def test_config_and_business_actions_map_to_distinct_exact_items(store) -> None:
    run_id = "presentation-items"
    store.create(run_id, "normal")
    episode = store.episode(run_id)
    config_task = store.presentation_task(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="config",
            action_id="action-config",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "ticket-query-extension-config"},
        ),
    )
    assert config_task.source_text == episode.ticket["check_before_change"][1]["operate_description"]

    episode.state.phase = EpisodePhase.VERIFYING
    business_task = store.presentation_task(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="business",
            action_id="action-business",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "automation-submit", "operation": "business_verify"},
        ),
    )
    assert business_task.source_text == episode.ticket["change_verified"][1]["operate_description"]


def test_unknown_business_control_fails_closed(store) -> None:
    run_id = "presentation-unknown"
    store.create(run_id, "normal")
    with pytest.raises(PresentationMappingError, match="trusted SOP mapping"):
        store.presentation_task(
            run_id,
            PresentationInput(
                runtime_event_type="tool.call_started",
                tool_call_id="unknown",
                action_id="action-unknown",
                tool_name="browser_click",
                status="running",
                arguments={"bid": "unknown-control"},
            ),
        )
```

- [ ] **Step 2: Run the tests and verify they fail before presentation models exist**

```bash
.venv/bin/pytest tests/case02_openenv/test_presentation.py -q
```

Expected: collection fails with `ModuleNotFoundError: case02_openenv.presentation`.

- [ ] **Step 3: Implement typed presentation input/event/task models and the exact mapper**

Create these public contracts in `presentation.py`:

```python
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from case02_openenv.artifacts import append_jsonl, atomic_write_json
from case02_openenv.models import EpisodePhase, RunState


class PresentationMappingError(RuntimeError):
    pass


class PresentationInput(BaseModel):
    runtime_event_type: Literal[
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "runtime.turn_completed",
        "runtime.turn_failed",
    ]
    tool_call_id: str | None = None
    action_id: str | None = None
    tool_name: str | None = None
    status: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PresentationTask(BaseModel):
    stage: str
    check_name: str
    source_field: Literal["operate_description", "operate_verified", "operate_rollback"]
    source_text: str
    source_sha256: str


class PresentationEvent(BaseModel):
    schema_version: Literal[1] = 1
    event_id: str
    sequence: int
    run_id: str
    event_type: str
    timestamp: datetime
    tool_call_id: str | None = None
    action_id: str | None = None
    stage: str
    task: PresentationTask | None = None
    tool_name: str | None = None
    status: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    failure: str | None = None


MONITOR_BIDS = {
    "monitor-query-alarm",
    "monitor-query-probe",
    "monitor-query-capacity",
    "monitor-query-runtime-metrics",
    "monitor-query-traffic",
}
CONFIG_BIDS = {
    "ticket-query-extension-config",
    "ticket-query-upstream-ready",
}
ORCHESTRATION_TOOLS = {"task_planner", "task_progress_check", "skill_view", "sop_decide"}
DISPLAY_STAGES = {
    "check_before_change",
    "change_implement",
    "implementation_verify",
    "change_verified",
    "business_verify",
    "change_rollback",
    "terminal",
}


def ticket_task(ticket: dict[str, Any], stage: str, index: int, source_field: str) -> PresentationTask:
    item = ticket[stage][index]
    text = str(item[source_field])
    if not text:
        raise PresentationMappingError("No trusted SOP mapping: source text is empty")
    return PresentationTask(
        stage=stage,
        check_name=str(item.get("check_name") or stage),
        source_field=source_field,
        source_text=text,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
```

Implement `map_task(ticket, state, item, previous)` with these explicit branches:

```python
def map_task(
    ticket: dict[str, Any],
    state: RunState,
    item: PresentationInput,
    previous: PresentationTask | None,
) -> PresentationTask | None:
    if item.runtime_event_type in {"runtime.turn_completed", "runtime.turn_failed"}:
        return previous
    bid = str(item.arguments.get("bid") or "")
    operation = str(item.arguments.get("operation") or item.result.get("operation") or "")
    post = state.phase in {
        EpisodePhase.CHANGE_APPLIED,
        EpisodePhase.VERIFYING,
        EpisodePhase.ANOMALY_DETECTED,
        EpisodePhase.ROLLBACK_SUBMITTED,
        EpisodePhase.ROLLED_BACK,
        EpisodePhase.COMPLETED,
    }
    if bid in CONFIG_BIDS:
        return ticket_task(ticket, "check_before_change", 1, "operate_description")
    if bid in MONITOR_BIDS:
        return ticket_task(
            ticket,
            "change_verified" if post else "check_before_change",
            0,
            "operate_description",
        )
    if item.tool_name == "browser_navigate":
        route = str(item.arguments.get("route") or "")
        if route == "ticket":
            return ticket_task(ticket, "check_before_change", 0, "operate_description")
        if route == "monitor":
            return ticket_task(
                ticket,
                "change_verified" if post else "check_before_change",
                0,
                "operate_description",
            )
        if route == "automation" and state.phase == EpisodePhase.ROLLBACK_SUBMITTED:
            return ticket_task(ticket, "change_implement", 0, "operate_rollback")
        if route == "automation" and previous is not None:
            return previous
        if route == "automation":
            return ticket_task(ticket, "change_implement", 0, "operate_description")
    if item.tool_name in {"browser_fill", "browser_select"}:
        value = str(item.arguments.get("value") or "")
        if value == "remove" or state.phase == EpisodePhase.ROLLBACK_SUBMITTED:
            return ticket_task(ticket, "change_implement", 0, "operate_rollback")
        if value in {"business_verify", "svc_usage_record_fetcher"}:
            return ticket_task(ticket, "change_verified", 1, "operate_description")
        if str(item.arguments.get("bid") or "").startswith("automation-"):
            return ticket_task(ticket, "change_implement", 0, "operate_description")
    if bid == "automation-submit" or item.tool_name == "browser_wait":
        if operation == "business_verify" or state.phase == EpisodePhase.VERIFYING:
            return ticket_task(ticket, "change_verified", 1, "operate_description")
        if operation == "remove" or state.phase == EpisodePhase.ROLLBACK_SUBMITTED:
            return ticket_task(ticket, "change_implement", 0, "operate_rollback")
        business_name = str(ticket["change_verified"][1].get("check_name") or "")
        if not operation and previous is not None and previous.check_name == business_name:
            return previous
        return ticket_task(ticket, "change_implement", 0, "operate_description")
    if item.tool_name == "terminal_execute":
        field = "operate_rollback" if state.phase == EpisodePhase.ROLLBACK_SUBMITTED else "operate_verified"
        return ticket_task(ticket, "change_implement", 0, field)
    if item.tool_name in ORCHESTRATION_TOOLS or item.tool_name == "browser_observe":
        return previous
    raise PresentationMappingError(
        f"No trusted SOP mapping for {item.tool_name or 'run event'}:{bid or operation}"
)
```

Add a separate display-stage function so the leadership strip is more specific than the business `EpisodePhase`:

```python
def display_stage(
    ticket: dict[str, Any],
    state: RunState,
    item: PresentationInput,
    task: PresentationTask | None,
) -> str:
    operation = str(item.arguments.get("operation") or item.result.get("operation") or "")
    if state.terminal_outcome is not None or item.runtime_event_type.startswith("runtime.turn_"):
        return "terminal"
    if state.phase in {EpisodePhase.ROLLBACK_SUBMITTED, EpisodePhase.ROLLED_BACK}:
        return "change_rollback"
    if operation == "business_verify" or (
        task is not None
        and task.check_name == str(ticket["change_verified"][1].get("check_name") or "")
    ):
        return "business_verify"
    if item.tool_name == "terminal_execute" or (
        item.tool_name == "browser_wait" and operation == "add"
    ):
        return "implementation_verify"
    if task is not None and task.stage == "change_verified":
        return "change_verified"
    if task is not None and task.stage == "change_implement":
        return "change_implement"
    return "check_before_change"
```

Add `presentation_task()` and `presentation_stage()` to `EpisodeStore` as thin, locked calls to `map_task(...)` and `display_stage(...)`. Store `current_presentation_task` on `Episode` and update it only after a successful mapping. In `record_presentation()`, set `PresentationEvent.stage` from `display_stage(...)`, not from `EpisodeStore.stage_for(...)`.

- [ ] **Step 4: Run mapper tests and existing episode tests**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_episode_store.py -q
```

Expected: all tests pass; existing business phases remain unchanged.

- [ ] **Step 5: Commit the SOP mapping slice**

```bash
git add \
  apps/case02_openenv/src/case02_openenv/presentation.py \
  apps/case02_openenv/src/case02_openenv/episode_store.py \
  tests/case02_openenv/test_presentation.py
git commit -m "feat(coworker): map live actions to exact SOP text"
```

### Task 3: Append-Only Presentation API, Snapshot, And SSE Resume

**Files:**
- Modify: `apps/case02_openenv/src/case02_openenv/presentation.py`
- Modify: `apps/case02_openenv/src/case02_openenv/episode_store.py:35-211`
- Modify: `apps/case02_openenv/src/case02_openenv/api.py:18-107,281-346`
- Modify: `src/homemaster/benchmarking/coworker_demo/environment_client.py:95-116`
- Modify: `tests/case02_openenv/test_api_contract.py`
- Modify: `tests/case02_openenv/test_presentation.py`

- [ ] **Step 1: Add failing API tests for append, snapshot, cross-run rejection, and resume**

```python
def test_presentation_post_snapshot_and_sse_resume(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "present-api", "scenario_id": "normal"})
    started = api.post(
        "/api/runs/present-api/presentation-events",
        json={
            "runtime_event_type": "tool.call_started",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "tool_name": "browser_click",
            "status": "running",
            "arguments": {"bid": "ticket-query-extension-config"},
        },
    )
    assert started.status_code == 200
    first = started.json()["event"]

    completed = api.post(
        "/api/runs/present-api/presentation-events",
        json={
            "runtime_event_type": "tool.call_completed",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "tool_name": "browser_click",
            "status": "succeeded",
            "arguments": {"bid": "ticket-query-extension-config"},
            "result": {"status": "ready"},
        },
    )
    assert completed.status_code == 200

    snapshot = api.get("/api/runs/present-api/presentation").json()["snapshot"]
    assert snapshot["current_task"]["source_field"] == "operate_description"
    assert snapshot["last_event"]["status"] == "succeeded"

    api.app.state.sse_idle_iterations = 1
    api.app.state.sse_poll_interval_s = 0
    stream = api.get(
        "/api/runs/present-api/presentation-events",
        headers={"Last-Event-ID": first["event_id"]},
    )
    assert first["event_id"] not in stream.text
    assert completed.json()["event"]["event_id"] in stream.text


def test_presentation_rejects_run_mismatch(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "run-a", "scenario_id": "normal"})
    response = api.post(
        "/api/runs/run-a/presentation-events",
        json={
            "runtime_event_type": "tool.call_started",
            "tool_call_id": "call",
            "action_id": "action",
            "tool_name": "browser_click",
            "status": "running",
            "arguments": {"bid": "ticket-query-extension-config", "run_id": "run-b"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "presentation_run_mismatch"
```

- [ ] **Step 2: Run the new API tests and verify 404/attribute failures**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_api_contract.py::test_presentation_post_snapshot_and_sse_resume \
  tests/case02_openenv/test_api_contract.py::test_presentation_rejects_run_mismatch -q
```

Expected: tests fail because presentation routes and ledger methods do not exist.

- [ ] **Step 3: Implement ledger recording, state snapshots, and reset cleanup**

Add ledger state to `Episode`:

```python
presentation_events: list[PresentationEvent] = field(default_factory=list)
current_presentation_task: PresentationTask | None = None
presentation_failures: list[str] = field(default_factory=list)
```

Implement these `EpisodeStore` methods under the episode lock:

```python
def record_presentation(self, run_id: str, item: PresentationInput) -> PresentationEvent:
    episode = self._episode(run_id)
    with episode.lock:
        embedded_run = item.arguments.get("run_id") or item.result.get("run_id")
        if embedded_run not in {None, run_id}:
            raise EpisodeError("presentation_run_mismatch", "presentation event belongs to another run")
        failure: str | None = None
        try:
            task = map_task(episode.ticket, episode.state, item, episode.current_presentation_task)
        except PresentationMappingError as exc:
            failure = str(exc)
            episode.presentation_failures.append(failure)
            task = episode.current_presentation_task
        if task is not None:
            episode.current_presentation_task = task
        sequence = len(episode.presentation_events) + 1
        event = PresentationEvent(
            event_id=f"presentation-{sequence:05d}-{uuid.uuid4().hex[:8]}",
            sequence=sequence,
            run_id=run_id,
            event_type=item.runtime_event_type,
            timestamp=item.timestamp,
            tool_call_id=item.tool_call_id,
            action_id=item.action_id,
            stage=self.stage_for(episode.state),
            task=task,
            tool_name=item.tool_name,
            status=item.status,
            arguments=item.arguments,
            result=item.result,
            evidence_refs=item.evidence_refs,
            failure=failure,
        )
        episode.presentation_events.append(event)
        append_jsonl(episode.run_root / "presentation/events.jsonl", event.model_dump(mode="json"))
        atomic_write_json(
            episode.run_root / "presentation/snapshot.json",
            self.presentation_snapshot(run_id),
        )
        return event.model_copy(deep=True)
```

Add `presentation_snapshot()` with non-stale in-flight state and deduplicated completed tasks:

```python
terminal_tool_calls = {
    event.tool_call_id
    for event in events
    if event.status in {"accepted", "succeeded", "failed", "rejected"}
}
in_flight = next(
    (
        event
        for event in reversed(events)
        if event.status == "running" and event.tool_call_id not in terminal_tool_calls
    ),
    None,
)
completed_by_hash: dict[str, PresentationTask] = {}
for event in events:
    if event.status == "succeeded" and event.task is not None:
        completed_by_hash[event.task.source_sha256] = event.task

snapshot = {
    "schema_version": 1,
    "run_id": run_id,
    "phase": episode.state.phase,
    "stage": self.stage_for(episode.state),
    "terminal_outcome": episode.state.terminal_outcome,
    "current_task": episode.current_presentation_task.model_dump(mode="json")
        if episode.current_presentation_task else None,
    "in_flight": in_flight.model_dump(mode="json") if in_flight else None,
    "last_event": events[-1].model_dump(mode="json") if events else None,
    "last_sequence": events[-1].sequence if events else 0,
    "completed_steps": [
        task.model_dump(mode="json") for task in completed_by_hash.values()
    ],
    "next_step": (
        episode.current_presentation_task.check_name
        if episode.current_presentation_task is not None
        else "等待 Agent 读取变更单"
    ),
    "presentation_failures": list(episode.presentation_failures),
}
return snapshot
```

On reset, clear presentation lists and delete `presentation/events.jsonl` and `presentation/snapshot.json` together with existing run-local traces.

- [ ] **Step 4: Add POST, snapshot GET, and SSE GET endpoints plus the client method**

In `api.py`, add:

```python
@app.post("/api/runs/{run_id}/presentation-events")
async def append_presentation(run_id: str, payload: PresentationInput) -> dict[str, Any]:
    event = store.record_presentation(run_id, payload)
    return {"success": True, "event": event.model_dump(mode="json")}


@app.get("/api/runs/{run_id}/presentation")
async def presentation_snapshot(run_id: str) -> dict[str, Any]:
    return {"success": True, "snapshot": store.presentation_snapshot(run_id)}


@app.get("/api/runs/{run_id}/presentation-events")
async def presentation_events(
    run_id: str, last_event_id: str | None = Header(default=None)
) -> StreamingResponse:
    store.state(run_id)

    async def stream():
        current = store.presentation_events(run_id)
        start = next(
            (index + 1 for index, event in enumerate(current) if event.event_id == last_event_id),
            0,
        )
        snapshot = store.presentation_snapshot(run_id)
        yield f"event: presentation.snapshot\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
        cursor = start
        idle = 0
        while idle < app.state.sse_idle_iterations:
            current = store.presentation_events(run_id)
            while cursor < len(current):
                event = current[cursor]
                yield f"id: {event.event_id}\nevent: presentation.event\ndata: {event.model_dump_json()}\n\n"
                cursor += 1
                idle = 0
            idle += 1
            await asyncio.sleep(app.state.sse_poll_interval_s)

    return StreamingResponse(stream(), media_type="text/event-stream")
```

Add to `EnvironmentClient`:

```python
def presentation_event(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return self._request(
        "POST",
        f"/api/runs/{run_id}/presentation-events",
        json=payload,
        check_budget=False,
    )
```

- [ ] **Step 5: Run API, ledger, and client tests**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_api_contract.py \
  tests/homemaster/benchmarking/coworker_demo/test_environment_client.py -q
```

Expected: all tests pass except the known OpenAPI snapshot test, which is refreshed in Task 8.

- [ ] **Step 6: Commit the presentation API slice**

```bash
git add \
  apps/case02_openenv/src/case02_openenv/presentation.py \
  apps/case02_openenv/src/case02_openenv/episode_store.py \
  apps/case02_openenv/src/case02_openenv/api.py \
  src/homemaster/benchmarking/coworker_demo/environment_client.py \
  tests/case02_openenv/test_api_contract.py \
  tests/case02_openenv/test_presentation.py \
  tests/homemaster/benchmarking/coworker_demo/test_environment_client.py
git commit -m "feat(coworker): add presentation event stream"
```

### Task 4: Safe Runtime Tool/Result Projection

**Files:**
- Create: `src/homemaster/benchmarking/coworker_demo/presentation.py`
- Modify: `src/homemaster/benchmarking/coworker_demo/tracing.py:13-63`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py`

- [ ] **Step 1: Write failing tests for allowlisted projection and start/result correlation**

```python
from __future__ import annotations

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.benchmarking.coworker_demo.presentation import project_runtime_event


def test_started_projection_keeps_only_safe_tool_arguments() -> None:
    event = RuntimeEvent(
        type="tool.call_started",
        session_id="s",
        run_id="run",
        turn_index=0,
        tool_call_id="call-1",
        name="browser_click",
        payload={"arguments": {"bid": "monitor-query-alarm", "api_key": "secret"}},
    )
    projected = project_runtime_event(event)
    assert projected is not None
    assert projected["action_id"].startswith("action-")
    assert projected["arguments"] == {"bid": "monitor-query-alarm"}
    assert "secret" not in str(projected)


def test_completed_projection_summarizes_receipt_without_raw_prompt() -> None:
    event = RuntimeEvent(
        type="tool.call_completed",
        session_id="s",
        run_id="run",
        turn_index=0,
        tool_call_id="call-1",
        name="browser_click",
        payload={
            "args": {"bid": "monitor-query-alarm"},
            "data": {
                "success": True,
                "action_id": "action-trusted",
                "backend_status": "succeeded",
                "visible_observation": {
                    "receipt": {
                        "payload": {"query": "alarm", "status": "clear", "active_alarms": []},
                        "evidence_refs": ["ev-1"],
                    }
                },
                "raw_prompt": "forbidden",
            },
        },
    )
    projected = project_runtime_event(event)
    assert projected["action_id"] == "action-trusted"
    assert projected["result"] == {"status": "clear", "query": "alarm"}
    assert projected["evidence_refs"] == ["ev-1"]
    assert "forbidden" not in str(projected)


def test_non_tool_noise_is_not_projected() -> None:
    event = RuntimeEvent(
        type="assistant.thinking",
        session_id="s",
        run_id="run",
        turn_index=0,
        payload={"thinking": "private reasoning"},
    )
    assert project_runtime_event(event) is None
```

- [ ] **Step 2: Run the tests and verify the projection module is missing**

```bash
.venv/bin/pytest tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py -q
```

Expected: collection fails with `ModuleNotFoundError`.

- [ ] **Step 3: Implement a tool-specific allowlist projector**

Create `presentation.py` with these constants and entry point:

```python
from __future__ import annotations

from typing import Any

from homemaster.benchmarking.coworker_demo.correlation import action_id_for
from homemaster.events.runtime_events import RuntimeEvent


ARGUMENT_FIELDS = {
    "task_planner": {"goal", "current_subtask", "next_focus"},
    "task_progress_check": {"current_subtask", "next_focus"},
    "skill_view": {"skill_name"},
    "browser_navigate": {"route"},
    "browser_observe": set(),
    "browser_click": {"bid"},
    "browser_fill": {"bid", "value"},
    "browser_select": {"bid", "value"},
    "browser_wait": {"job_id", "target_status"},
    "terminal_execute": {"command"},
    "sop_decide": {"stage", "decision"},
}


def _allow(source: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    return {key: source[key] for key in fields if key in source}


def project_runtime_event(event: RuntimeEvent) -> dict[str, Any] | None:
    if event.type not in {
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "runtime.turn_completed",
        "runtime.turn_failed",
    }:
        return None
    if event.type.startswith("runtime.turn_"):
        return {
            "runtime_event_type": event.type,
            "status": "succeeded" if event.type.endswith("completed") else "failed",
            "timestamp": event.timestamp,
        }
    tool_name = str(event.name or "")
    tool_call_id = str(event.tool_call_id or "")
    arguments = event.payload.get("arguments") or event.payload.get("args") or {}
    data = event.payload.get("data") or {}
    action_id = str(data.get("action_id") or action_id_for(event.run_id, tool_call_id))
    result, evidence_refs = summarize_tool_result(tool_name, data)
    return {
        "runtime_event_type": event.type,
        "tool_call_id": tool_call_id,
        "action_id": action_id,
        "tool_name": tool_name,
        "status": (
            "running" if event.type == "tool.call_started"
            else "failed" if event.type == "tool.call_failed"
            else str(data.get("backend_status") or "succeeded")
        ),
        "arguments": _allow(dict(arguments), ARGUMENT_FIELDS.get(tool_name, set())),
        "result": result,
        "evidence_refs": evidence_refs,
        "timestamp": event.timestamp,
    }
```

Refactor `correlation.py` so both sides use:

```python
def action_id_for(run_id: str, tool_call_id: str) -> str:
    seed = f"{run_id}:{tool_call_id}"
    return f"action-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"


def correlated_action_id(run_context: RunContext) -> str:
    tool_call_id = run_context.deps.get("current_tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise RuntimeError("current model tool_call_id is unavailable")
    return action_id_for(run_context.run_id, tool_call_id)
```

Implement `summarize_tool_result()` with explicit branches for monitor receipts, config receipts, automation jobs, waits, terminal exit/output, decisions, and task tools. Return no arbitrary keys and cap command/stdout display strings at 320 characters:

```python
def _clip(value: Any, limit: int = 320) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_tool_result(
    tool_name: str, data: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    visible = data.get("visible_observation")
    visible = visible if isinstance(visible, dict) else {}
    receipt = visible.get("receipt")
    receipt = receipt if isinstance(receipt, dict) else {}
    payload = receipt.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    evidence = [str(item) for item in data.get("evidence_refs") or []]
    evidence.extend(str(item) for item in receipt.get("evidence_refs") or [])
    evidence = list(dict.fromkeys(evidence))

    if tool_name == "browser_click":
        allowed = {
            key: payload[key]
            for key in (
                "check", "ready", "query", "stage", "status",
                "alarm_code", "job_id", "operation",
            )
            if key in payload
        }
        if not allowed:
            allowed = {
                key: visible[key]
                for key in ("job_id", "operation", "status")
                if key in visible
            }
        return allowed, evidence
    if tool_name == "browser_wait":
        return {
            key: visible[key]
            for key in ("job_id", "operation", "status")
            if key in visible
        }, evidence
    if tool_name == "terminal_execute":
        return {
            "exit_code": visible.get("exit_code"),
            "stdout": _clip(visible.get("stdout")),
            "stderr": _clip(visible.get("stderr")),
        }, evidence
    if tool_name == "sop_decide":
        return {
            key: data[key]
            for key in ("backend_status", "terminal", "classification")
            if key in data
        }, evidence
    if tool_name in {"browser_fill", "browser_select"}:
        return {
            key: visible[key]
            for key in ("bid", "value", "readback")
            if key in visible
        }, evidence
    return {"success": bool(data.get("success", True))}, evidence
```

- [ ] **Step 4: Mirror only projected events from `CoworkerTraceSink`**

Replace the generic runtime mirror in `emit()` with:

```python
from homemaster.benchmarking.coworker_demo.presentation import project_runtime_event

projected = project_runtime_event(event)
if projected is not None:
    try:
        self.client.presentation_event(self.run_id, projected)
    except Exception as exc:
        self.mirror_failures.append(f"{type(exc).__name__}: {exc}")
```

Keep the local full runtime trace and human transcript behavior unchanged. Do not send assistant thinking/reply payloads to the presentation endpoint.

- [ ] **Step 5: Test projection, trace sink behavior, and secret exclusion**

```bash
.venv/bin/pytest \
  tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py \
  tests/homemaster/test_debug_assets_do_not_contain_secrets.py \
  tests/homemaster/test_event_sinks.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the runtime projection slice**

```bash
git add \
  src/homemaster/benchmarking/coworker_demo/correlation.py \
  src/homemaster/benchmarking/coworker_demo/presentation.py \
  src/homemaster/benchmarking/coworker_demo/tracing.py \
  tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py
git commit -m "feat(coworker): project safe live tool results"
```

### Task 5: Executive Observer UI

**Files:**
- Modify: `apps/case02_openenv/templates/observer.html`
- Modify: `apps/case02_openenv/static/observer.js`
- Modify: `apps/case02_openenv/static/app.css`
- Modify: `apps/case02_openenv/src/case02_openenv/api.py:339-346`
- Modify: `tests/case02_openenv/test_pages.py`

- [ ] **Step 1: Write failing page-contract tests for the executive dashboard**

```python
def test_observer_is_read_only_executive_dashboard(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/runs", json={"run_id": "executive-page", "scenario_id": "normal"})
    response = api.get("/observer/executive-page")
    assert response.status_code == 200
    for element_id in (
        "sop-stage-strip",
        "current-sop-text",
        "current-tool-name",
        "current-tool-arguments",
        "latest-result-status",
        "latest-result-evidence",
        "completed-steps",
        "next-step",
        "run-outcome",
    ):
        assert f'id="{element_id}"' in response.text
    assert "data-bid=" not in response.text
    assert "Environment state" not in response.text
    assert "Evidence timeline" not in response.text


def test_observer_script_uses_text_content_for_event_data(tmp_path: Path) -> None:
    script = client(tmp_path).get("/static/observer.js").text
    assert "EventSource" in script
    assert ".textContent" in script
    assert ".innerHTML" not in script
```

- [ ] **Step 2: Run page tests and verify missing IDs/EventSource failures**

```bash
.venv/bin/pytest tests/case02_openenv/test_pages.py -q
```

Expected: the two new tests fail against the raw-state observer.

- [ ] **Step 3: Replace the observer template with semantic, read-only regions**

Use this structure; render all dynamic content as initially empty text nodes:

```html
<body class="executive-observer">
  <header id="sop-stage-strip" class="stage-strip" aria-label="SOP progress">
    <div class="run-identity"><strong>Change Coworker</strong><span>{{ run_id }}</span></div>
    <ol id="stage-list"></ol>
  </header>
  <main class="recording-canvas">
    <section class="agent-window-reserved" aria-label="Real Agent Chrome recording area"></section>
    <aside class="executive-dashboard">
      <section class="dashboard-card current-task-card">
        <p class="card-label">CURRENT SOP TASK</p>
        <strong id="current-stage">Preparing</strong>
        <p id="current-sop-name"></p>
        <p id="current-sop-text" class="sop-source"></p>
      </section>
      <section class="dashboard-card tool-card">
        <p class="card-label">AGENT ACTION</p>
        <strong id="current-tool-name">Waiting for Agent</strong>
        <dl id="current-tool-arguments"></dl>
      </section>
      <section class="dashboard-card result-card">
        <p class="card-label">LATEST RESULT</p>
        <strong id="latest-result-status">Pending</strong>
        <p id="latest-result-summary"></p>
        <p id="latest-result-evidence"></p>
      </section>
      <section class="dashboard-card progress-card">
        <p class="card-label">PROGRESS</p>
        <ol id="completed-steps"></ol>
        <p id="next-step"></p>
      </section>
    </aside>
  </main>
  <footer class="outcome-strip">
    <strong id="run-outcome">Run in progress</strong>
    <span id="score-summary">Scores pending</span>
  </footer>
  <script>window.OBSERVER_RUN_ID = {{ run_id | tojson }};</script>
  <script src="/static/observer.js"></script>
</body>
```

- [ ] **Step 4: Implement snapshot/SSE rendering without HTML injection**

In `observer.js`, define stage labels and safe render helpers:

```javascript
"use strict";
(() => {
  const runId = window.OBSERVER_RUN_ID;
  const stages = [
    ["check_before_change", "变更前检查"],
    ["change_implement", "变更执行"],
    ["implementation_verify", "独立验证"],
    ["change_verified", "变更后检查"],
    ["business_verify", "业务验证"],
    ["change_rollback", "回滚"],
    ["terminal", "完成"],
  ];
  let lastSequence = 0;

  function text(id, value) {
    document.getElementById(id).textContent = value == null || value === "" ? "—" : String(value);
  }

  function renderObject(dl, payload) {
    dl.replaceChildren();
    Object.entries(payload || {}).forEach(([key, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = key;
      dd.textContent = typeof value === "string" ? value : JSON.stringify(value);
      dl.append(dt, dd);
    });
  }

  function renderStages(activeStage) {
    const list = document.getElementById("stage-list");
    list.replaceChildren();
    stages.forEach(([key, label]) => {
      const item = document.createElement("li");
      item.textContent = label;
      item.className = key === activeStage ? "active" : "";
      list.append(item);
    });
  }

  function renderCompleted(steps) {
    const list = document.getElementById("completed-steps");
    list.replaceChildren();
    (steps || []).forEach((step) => {
      const item = document.createElement("li");
      item.textContent = step.check_name;
      list.append(item);
    });
  }

  function markCompleted(task) {
    if (!task) return;
    const list = document.getElementById("completed-steps");
    const names = Array.from(list.children, (item) => item.textContent);
    if (names.includes(task.check_name)) return;
    const item = document.createElement("li");
    item.textContent = task.check_name;
    list.append(item);
  }

  function renderSnapshot(snapshot) {
    lastSequence = Math.max(lastSequence, snapshot.last_sequence || 0);
    text("current-stage", snapshot.stage || snapshot.phase);
    renderStages(snapshot.stage || snapshot.phase);
    if (snapshot.current_task) {
      text("current-sop-name", snapshot.current_task.check_name);
      text("current-sop-text", snapshot.current_task.source_text);
    }
    if (snapshot.last_event) renderEvent(snapshot.last_event, true);
    renderCompleted(snapshot.completed_steps);
    text("next-step", snapshot.next_step);
    text("run-outcome", snapshot.terminal_outcome || "Run in progress");
  }

  function renderEvent(event, force = false) {
    if (!force && (event.sequence || 0) <= lastSequence) return;
    lastSequence = Math.max(lastSequence, event.sequence || 0);
    text("current-stage", event.stage);
    renderStages(event.stage);
    if (event.task) {
      text("current-sop-name", event.task.check_name);
      text("current-sop-text", event.task.source_text);
    }
    text("current-tool-name", event.tool_name || "Run finalization");
    renderObject(document.getElementById("current-tool-arguments"), event.arguments);
    text("latest-result-status", event.status);
    text("latest-result-summary", JSON.stringify(event.result || {}));
    text("latest-result-evidence", (event.evidence_refs || []).join(" · "));
    if (event.status === "succeeded") markCompleted(event.task);
    text("next-step", event.status === "running" ? event.task?.source_text : "Waiting for the next Agent action");
  }

  fetch(`/api/runs/${runId}/presentation`)
    .then((response) => response.json())
    .then((payload) => renderSnapshot(payload.snapshot));
  const source = new EventSource(`/api/runs/${runId}/presentation-events`);
  source.addEventListener("presentation.snapshot", (event) => renderSnapshot(JSON.parse(event.data)));
  source.addEventListener("presentation.event", (event) => renderEvent(JSON.parse(event.data)));
  source.onerror = () => text("latest-result-status", "Reconnecting to run events");
})();
```

- [ ] **Step 5: Add executive layout CSS while keeping Agent page selectors unchanged**

Add observer-scoped rules only:

```css
.executive-observer {
  width: 1920px;
  height: 1080px;
  overflow: hidden;
  background: #0b1220;
  color: #f8fafc;
}
.stage-strip { height: 96px; display: grid; grid-template-columns: 300px 1fr; padding: 18px 28px; }
.stage-strip ol { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; margin: 0; padding: 0; list-style: none; }
.recording-canvas { width: 1920px; height: 900px; margin: 0; display: grid; grid-template-columns: 1320px 600px; }
.agent-window-reserved { background: #111827; }
.executive-dashboard { padding: 18px; display: grid; gap: 14px; grid-template-rows: 2fr 1fr 1fr 1fr; }
.dashboard-card { overflow: hidden; border: 1px solid #334155; border-radius: 12px; padding: 18px; background: #111827; }
.card-label { margin: 0 0 8px; color: #67e8f9; font-size: 14px; font-weight: 800; letter-spacing: .08em; }
.sop-source { max-height: 310px; overflow: hidden; font-size: 20px; line-height: 1.55; white-space: pre-wrap; }
.outcome-strip { height: 84px; display: flex; align-items: center; justify-content: space-between; padding: 0 28px; font-size: 22px; }
```

Add status classes for `running`, `accepted`, `succeeded`, `failed`, `rejected`, and `anomaly`, with contrast-safe colors.

- [ ] **Step 6: Run page/API tests**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_pages.py \
  tests/case02_openenv/test_api_contract.py -q
```

Expected: all tests except the deferred OpenAPI snapshot refresh pass.

- [ ] **Step 7: Commit the executive observer UI**

```bash
git add \
  apps/case02_openenv/templates/observer.html \
  apps/case02_openenv/static/observer.js \
  apps/case02_openenv/static/app.css \
  apps/case02_openenv/src/case02_openenv/api.py \
  tests/case02_openenv/test_pages.py
git commit -m "feat(coworker): add executive recording dashboard"
```

### Task 6: Full-Screen Observer And Agent Window Layout

**Files:**
- Modify: `apps/case02_openenv/src/case02_openenv/recording/display.py:13-152`
- Modify: `apps/case02_openenv/src/case02_openenv/api.py:369-421`
- Modify: `config/coworker_demo.example.yaml:15-31`
- Modify: `tests/case02_openenv/test_recorder.py`

- [ ] **Step 1: Write failing tests for observer geometry and removal of xterm**

```python
def test_executive_observer_command_is_full_screen_and_has_no_xterm(tmp_path: Path) -> None:
    manager = DisplayManager(tmp_path)
    command = manager._observer_command("http://127.0.0.1:8765/observer/run")
    assert "--window-position=0,0" in command
    assert "--window-size=1920,1080" in command
    assert all("xterm" not in part for part in command)


def test_display_stop_reports_observer_was_alive(tmp_path: Path) -> None:
    manager = DisplayManager(tmp_path)
    class FakeProcess:
        returncode = None
        def poll(self): return None
        def terminate(self): self.returncode = -15
        def wait(self, timeout): return self.returncode
    manager.processes["observer"] = FakeProcess()
    result = manager.stop()
    assert result["observer_was_alive"] is True
```

- [ ] **Step 2: Run recorder tests and verify helper/health failures**

```bash
.venv/bin/pytest tests/case02_openenv/test_recorder.py -q
```

Expected: failures because `_observer_command` and `observer_was_alive` do not exist.

- [ ] **Step 3: Refactor DisplayManager to launch only the observer background**

Replace `start_companion_windows` with:

```python
def _observer_command(self, observer_url: str) -> list[str]:
    profile = self.run_root / "browser/observer-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return [
        self.chrome,
        f"--app={observer_url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--disable-dev-shm-usage",
        "--window-position=0,0",
        "--window-size=1920,1080",
    ]


def start_companion_windows(self, *, observer_url: str) -> None:
    environment = {**os.environ, "DISPLAY": self.display}
    self.processes["observer"] = subprocess.Popen(
        self._observer_command(observer_url),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
```

Remove xterm construction. In `stop()`, capture `observer_was_alive = process.poll() is None` before intentional termination and return a structured payload:

```python
{
    "observer_was_alive": observer_was_alive,
    "return_codes": {"observer": ..., "tigervnc": ...},
}
```

Update `_ServiceRecordingSession` to call `start_companion_windows(observer_url=...)` and preserve the structured display health in the recording-stop result.

- [ ] **Step 4: Publish the fixed Agent window geometry**

Change the tracked example to:

```yaml
browser:
  chrome_executable: /usr/bin/google-chrome
  action_timeout_s: 20
  viewport_width: 1320
  viewport_height: 900
  window_x: 0
  window_y: 96
```

For the acceptance server only, apply the same non-secret browser block to the gitignored `config/coworker_demo.yaml`; do not stage that file.

- [ ] **Step 5: Run recorder, configuration, and linchpin helper tests**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_recorder.py \
  tests/homemaster/benchmarking/coworker_demo/test_config.py \
  tests/case02_openenv/test_linchpin_helpers.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit layout changes**

```bash
git add \
  apps/case02_openenv/src/case02_openenv/recording/display.py \
  apps/case02_openenv/src/case02_openenv/api.py \
  config/coworker_demo.example.yaml \
  tests/case02_openenv/test_recorder.py
git commit -m "feat(coworker): record executive observer layout"
```

### Task 7: Presentation Verification And Formal-Success Gate

**Files:**
- Modify: `apps/case02_openenv/src/case02_openenv/presentation.py`
- Modify: `apps/case02_openenv/src/case02_openenv/evaluation/scoring.py:15-135`
- Modify: `apps/case02_openenv/src/case02_openenv/api.py:248-265,413-421`
- Modify: `tests/case02_openenv/test_presentation.py`
- Modify: `tests/case02_openenv/test_scoring.py`

- [ ] **Step 1: Write failing verification tests for complete and incomplete streams**

```python
def test_presentation_verifier_requires_terminal_results_for_every_tool(store) -> None:
    run_id = "presentation-verify"
    store.create(run_id, "normal")
    store.record_presentation(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="call-1",
            action_id="action-1",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "ticket-query-extension-config"},
        ),
    )
    report = store.verify_presentation(run_id, observer_was_alive=True)
    assert report["passed"] is False
    assert "missing_terminal_event:call-1" in report["failures"]

    store.record_presentation(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_completed",
            tool_call_id="call-1",
            action_id="action-1",
            tool_name="browser_click",
            status="succeeded",
            arguments={"bid": "ticket-query-extension-config"},
            result={"status": "ready"},
        ),
    )
    report = store.verify_presentation(run_id, observer_was_alive=True)
    assert report["passed"] is True


def test_formal_success_rejects_presentation_failure() -> None:
    from case02_openenv.evaluation.scoring import formal_success

    assert formal_success(
        trajectory_score=100.0,
        result_score=100.0,
        safety_failure=False,
        environment_failure=False,
        artifact_failure=False,
        presentation_failure=False,
    ) is True
    assert formal_success(
        trajectory_score=100.0,
        result_score=100.0,
        safety_failure=False,
        environment_failure=False,
        artifact_failure=False,
        presentation_failure=True,
    ) is False
```

- [ ] **Step 2: Run verification/scoring tests and verify missing method/field failures**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_scoring.py -q
```

Expected: failures because presentation verification and summary fields do not exist.

- [ ] **Step 3: Implement independent presentation verification**

Add `verify_presentation(events, failures, observer_was_alive)` in `presentation.py`:

```python
def verify_presentation_payload(
    events: list[PresentationEvent],
    mapping_failures: list[str],
    *,
    observer_was_alive: bool,
) -> dict[str, Any]:
    starts = {event.tool_call_id: event for event in events if event.status == "running"}
    terminal = {
        event.tool_call_id: event
        for event in events
        if event.status in {"accepted", "succeeded", "failed", "rejected"}
    }
    failures = list(mapping_failures)
    for tool_call_id in sorted(key for key in starts if key):
        if tool_call_id not in terminal:
            failures.append(f"missing_terminal_event:{tool_call_id}")
        elif terminal[tool_call_id].action_id != starts[tool_call_id].action_id:
            failures.append(f"action_id_mismatch:{tool_call_id}")
    if not observer_was_alive:
        failures.append("observer_exited_before_recording_stop")
    if not any(event.task and event.task.source_text for event in events):
        failures.append("missing_sop_source_text")
    return {
        "schema_version": 1,
        "passed": not failures,
        "event_count": len(events),
        "tool_call_count": len(starts),
        "failures": failures,
    }
```

`EpisodeStore.verify_presentation()` writes `presentation/verification.json` atomically and returns it.

- [ ] **Step 4: Register required presentation artifacts and gate formal success**

Add to `CORE_ARTIFACTS`:

```python
"presentation/events.jsonl",
"presentation/snapshot.json",
"presentation/verification.json",
```

Before artifact verification, register those files with producer `presentation`. Add summary fields:

```python
"presentation_failure": not presentation_report["passed"],
"presentation_failures": presentation_report["failures"],
```

Include `presentation_failure` in formal-success calculation but do not change trajectory or result scores.

Add the pure helper exercised above and use it for both initial and video-verified summaries:

```python
def formal_success(
    *,
    trajectory_score: float,
    result_score: float,
    safety_failure: bool,
    environment_failure: bool,
    artifact_failure: bool,
    presentation_failure: bool,
) -> bool:
    return bool(
        trajectory_score == 100.0
        and result_score == 100.0
        and not safety_failure
        and not environment_failure
        and not artifact_failure
        and not presentation_failure
    )
```

Extend `test_full_normal_run_freezes_24_nodes_and_14_results` by recording one correlated presentation start/completion pair before finalization. Assert the video-verified call with `observer_was_alive=True` remains formally successful, then call it with `observer_was_alive=False` and assert `presentation_failure is True` and `formal_success is False`.

```python
store.record_presentation(
    run_id,
    PresentationInput(
        runtime_event_type="tool.call_started",
        tool_call_id="presentation-test",
        action_id="action-presentation-test",
        tool_name="browser_click",
        status="running",
        arguments={"bid": "ticket-query-extension-config"},
    ),
)
store.record_presentation(
    run_id,
    PresentationInput(
        runtime_event_type="tool.call_completed",
        tool_call_id="presentation-test",
        action_id="action-presentation-test",
        tool_name="browser_click",
        status="succeeded",
        arguments={"bid": "ticket-query-extension-config"},
        result={"status": "ready"},
    ),
)
verified = finalize_run(
    store,
    run_id,
    video_verified=True,
    observer_was_alive=True,
)["summary"]
assert verified["presentation_failure"] is False
assert verified["formal_success"] is True

observer_failed = finalize_run(
    store,
    run_id,
    video_verified=True,
    observer_was_alive=False,
)["summary"]
assert observer_failed["presentation_failure"] is True
assert observer_failed["formal_success"] is False
```

Make the recording-stop health path explicit so the check is based on the real display process, not a caller-supplied optimistic default:

```python
# recording/display.py — capture before intentional termination
observer = self.processes.get("observer")
observer_was_alive = observer is not None and observer.poll() is None

# public_views.py — preserve the health result returned by DisplayManager.stop()
display_result = self.display.stop()
return {
    **recording_result,
    "observer_was_alive": display_result["observer_was_alive"],
    "display_return_codes": display_result["return_codes"],
}

# api.py — the recording-stop endpoint is the sole production caller
stop_result = recording_manager.stop(run_id)
result = finalize_run(
    store,
    run_id,
    video_verified=True,
    observer_was_alive=bool(stop_result["observer_was_alive"]),
)
```

Change `publish_video_verification` to require `observer_was_alive: bool` (no default), rerun `store.verify_presentation()` with that final display health, then recompute and atomically rewrite the summary. Update `_ServiceRecordingSession.stop()`, the recording-stop API response model, and their tests for the structured display-stop payload.

- [ ] **Step 5: Run scoring, artifact, API, and recorder tests**

```bash
.venv/bin/pytest \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_scoring.py \
  tests/case02_openenv/test_artifacts.py \
  tests/case02_openenv/test_api_contract.py \
  tests/case02_openenv/test_recorder.py -q
```

Expected: all focused tests pass except the deferred OpenAPI snapshot refresh.

- [ ] **Step 6: Commit presentation delivery gates**

```bash
git add \
  apps/case02_openenv/src/case02_openenv/presentation.py \
  apps/case02_openenv/src/case02_openenv/evaluation/scoring.py \
  apps/case02_openenv/src/case02_openenv/api.py \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_scoring.py
git commit -m "feat(coworker): verify executive presentation artifacts"
```

### Task 8: Independent Bundle Verification, Docs, Full Regression, And Fresh Recordings

**Files:**
- Modify: `scripts/coworker_demo/verify_run_bundle.py`
- Create: `tests/coworker_demo/test_verify_run_bundle_presentation.py`
- Modify: `apps/case02_openenv/openapi.json`
- Modify: `docs/coworker-demo-user-guide.md`
- Modify: `docs/architecture/coworker-demo.md`

- [ ] **Step 1: Write a failing standard-library verifier test**

```python
from __future__ import annotations

import json
from pathlib import Path

from scripts.coworker_demo.verify_run_bundle import verify_presentation_bundle


def test_independent_verifier_rejects_missing_tool_completion(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    path = run_root / "presentation/events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "sequence": 1,
            "event_id": "presentation-1",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "status": "running",
            "task": {"source_text": "locked SOP", "source_sha256": "bad"},
        }) + "\n",
        encoding="utf-8",
    )
    failures = verify_presentation_bundle(run_root)
    assert "missing_terminal_event:call-1" in failures
    assert "sop_source_hash_mismatch:presentation-1" in failures
```

- [ ] **Step 2: Run the verifier test and verify missing helper failure**

```bash
.venv/bin/pytest tests/coworker_demo/test_verify_run_bundle_presentation.py -q
```

Expected: collection fails because `verify_presentation_bundle` does not exist.

- [ ] **Step 3: Extend the independent verifier without importing product evaluator code**

Implement `verify_presentation_bundle(run_root: Path) -> list[str]` using only the standard library:

```python
def verify_presentation_bundle(run_root: Path) -> list[str]:
    failures: list[str] = []
    events_path = run_root / "presentation/events.jsonl"
    if not events_path.is_file():
        return ["missing:presentation/events.jsonl"]
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    starts = {event.get("tool_call_id"): event for event in events if event.get("status") == "running"}
    terminal = {
        event.get("tool_call_id"): event
        for event in events
        if event.get("status") in {"accepted", "succeeded", "failed", "rejected"}
    }
    for event in events:
        task = event.get("task") or {}
        source_text = task.get("source_text")
        source_hash = task.get("source_sha256")
        if source_text and hashlib.sha256(source_text.encode("utf-8")).hexdigest() != source_hash:
            failures.append(f"sop_source_hash_mismatch:{event.get('event_id')}")
    for tool_call_id, started in starts.items():
        completed = terminal.get(tool_call_id)
        if completed is None:
            failures.append(f"missing_terminal_event:{tool_call_id}")
        elif completed.get("action_id") != started.get("action_id"):
            failures.append(f"action_id_mismatch:{tool_call_id}")
    return failures
```

Call it from the existing run verifier, compare its failures with `presentation/verification.json`, and require all three presentation artifacts in the manifest with matching hashes.

- [ ] **Step 4: Refresh the offline OpenAPI snapshot and run drift test**

Generate the snapshot from `create_app(...).openapi()` using the same `ServiceConfig` as `test_openapi_snapshot_matches_runtime_schema`, serialize with `ensure_ascii=False`, sorted keys, and a trailing newline. Then run:

```bash
.venv/bin/pytest tests/case02_openenv/test_api_contract.py::test_openapi_snapshot_matches_runtime_schema -q
```

Expected: PASS.

- [ ] **Step 5: Update user and architecture documentation**

Document these exact operator facts:

- The video remains one unedited `video/demo.mp4`.
- The left region is the real Agent Chrome; the right region is a read-only observer.
- Current task text is exact locked SOP source text.
- Tool/result cards are allowlisted projections, not chain-of-thought.
- `presentation/events.jsonl`, `presentation/snapshot.json`, and `presentation/verification.json` are required artifacts.
- A presentation failure can make `formal_success=false` without changing business scores.
- The observer is not available to the Agent and does not confirm real monitoring truth.

- [ ] **Step 6: Run formatting, focused suites, and the full test suite**

```bash
.venv/bin/ruff check src apps/case02_openenv/src tests scripts
.venv/bin/pytest tests/homemaster/benchmarking/coworker_demo tests/case02_openenv tests/coworker_demo -q
.venv/bin/pytest -q
```

Expected: ruff exits 0 and all tests pass. If a live-provider test is explicitly marked/skipped by existing policy, record the skip reason; do not hide an unexpected failure.

- [ ] **Step 7: Run the secret-safe preflight**

```bash
.venv/bin/python scripts/coworker_demo/preflight.py \
  --coworker-config config/coworker_demo.yaml \
  --provider-config config/homemaster.yaml
```

Expected: PASS without printing provider credentials; port 8765 is free and both configs retain mode 0600.

- [ ] **Step 8: Record a fresh normal run and verify its complete bundle**

Start the existing shell and submit only the locked normal ticket path:

```bash
.venv/bin/homemaster shell
```

```text
/home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json
```

After the shell prints the fresh run ID, capture it without using an old directory and run the verifier:

```bash
read -r -p "Paste the fresh normal run ID: " NORMAL_COWORKER_RUN_ID
test -f "var/coworker-demo/$NORMAL_COWORKER_RUN_ID/video/demo.mp4"
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  "var/coworker-demo/$NORMAL_COWORKER_RUN_ID"
```

Expected:

```text
trajectory 24/24
results 14/14
presentation passed
video verification passed
formal success true
```

Visually inspect `var/coworker-demo/$NORMAL_COWORKER_RUN_ID/video/demo.mp4` and confirm the stage, exact SOP task, tool, and result are readable throughout.

- [ ] **Step 9: Record a fresh anomaly run and verify rollback presentation**

Submit:

```text
post_change_anomaly /home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json
```

After the shell prints the fresh anomaly run ID, run:

```bash
read -r -p "Paste the fresh anomaly run ID: " ANOMALY_COWORKER_RUN_ID
test -f "var/coworker-demo/$ANOMALY_COWORKER_RUN_ID/video/demo.mp4"
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  "var/coworker-demo/$ANOMALY_COWORKER_RUN_ID"
```

Expected:

```text
trajectory 22/22
results 11/11
presentation passed
rollback reason visible
remove wait and absence grep visible
video verification passed
formal success true
```

- [ ] **Step 10: Commit docs, verifier, snapshot, and acceptance-test changes**

```bash
git add \
  scripts/coworker_demo/verify_run_bundle.py \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  apps/case02_openenv/openapi.json \
  docs/coworker-demo-user-guide.md \
  docs/architecture/coworker-demo.md
git commit -m "docs(coworker): document verified executive recording"
```

## Final Verification Checklist

- [ ] `git status --short` is empty.
- [ ] `git diff --check HEAD~8..HEAD` reports no whitespace errors.
- [ ] The full pytest suite passes.
- [ ] Both fresh run bundle verifications pass independently.
- [ ] Neither presentation stream contains `api_key`, provider credentials, raw prompt text, evaluator-only hidden scenario fields, or assistant thinking.
- [ ] The Agent page remains restricted to ticket, monitor, and automation routes.
- [ ] The observer has no `data-bid` mutation controls.
- [ ] Every tool start has a same-`tool_call_id`, same-`action_id` terminal presentation event.
- [ ] Every displayed SOP source hash matches the locked ticket text.
- [ ] The delivered video is the original continuous recording, not a post-produced derivative.
