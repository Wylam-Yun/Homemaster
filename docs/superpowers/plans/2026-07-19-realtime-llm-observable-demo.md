# Real-Time LLM Observable Coworker Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Per repository AGENTS.md, the primary agent performs implementation, debugging, testing, external verification, documentation, and review-finding fixes. Subagents are used only for the plan review and final code review gates. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make real Mimo Coworker runs continuously show the model plan, every selected tool, environment results, deterministic decision summaries, and recoverable incidents, then deliver independently verified normal and anomaly videos executed by mimo-v2.5.

**Architecture:** Upgrade the run-owned presentation protocol to version 2. Keep the runtime and model tool surface unchanged, project only safe structured data at CoworkerTraceSink, and use a pure server-side reducer to rebuild plan, current action/result, decision summary, incidents, and critical history from the append-only presentation ledger. The observer renders typed snapshot/SSE state without inferring business meaning or exposing hidden reasoning.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, pytest, JavaScript, CSS, Playwright, TigerVNC, FFmpeg/libx264, ffprobe, HomeMaster GenericAgentRuntime, Mimo mimo-v2.5.

**Design source:** docs/superpowers/specs/2026-07-19-realtime-llm-observable-demo-design.md

**External linchpin:** Fresh Mimo provider execution is UNVERIFIED until a new transport request returns model mimo-v2.5 and both final run bundles pass the independent verifier. The 2026-07-19 preflight proves configuration and dependencies, not provider acceptance or business completion.

---

## File Map

### New files

- apps/case02_openenv/src/case02_openenv/observable_presentation.py
  - pure append-only event reducer, incident recovery, and history selection.
- apps/case02_openenv/src/case02_openenv/presentation_models.py
  - version-2 input, event, task, plan, decision, incident, history, observable
    state, and snapshot models;
  - closed Chinese label dictionaries;
- tests/case02_openenv/test_observable_presentation.py
  - reducer, failure recovery, history filtering, and snapshot reconstruction.
- docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md
  - all real attempts, final accepted run IDs, model identity, scores, failures,
    video hashes, and external end-state evidence.

### Modified producer files

- src/homemaster/benchmarking/coworker_demo/presentation.py
  - safe planner, public reply, and failure-code projection.
- src/homemaster/benchmarking/coworker_demo/tracing.py
  - mirror assistant.reply in addition to existing tool/runtime boundaries.
- src/homemaster/benchmarking/coworker_demo/turn.py
  - no behavior change; expose authoritative terminal/premature outcome to the
    public-reply presentation event without feeding observer data back.

### Modified service and API files

- apps/case02_openenv/src/case02_openenv/presentation.py
  - protocol-v2 input, event, and snapshot contracts.
- apps/case02_openenv/src/case02_openenv/episode_store.py
  - append v2 events and call the pure reducer for each candidate snapshot.
- apps/case02_openenv/src/case02_openenv/api.py
  - publish v2 snapshot and SSE contracts.
- apps/case02_openenv/openapi.json
  - refreshed deterministic API snapshot.

### Modified observer files

- apps/case02_openenv/templates/observer.html
  - five fixed read-only information regions.
- apps/case02_openenv/static/observer.js
  - render plan, model output, result, summary, incidents, and history.
- apps/case02_openenv/static/app.css
  - fixed 1920x1080 readable geometry.

### Modified verification files

- apps/case02_openenv/src/case02_openenv/evaluation/scoring.py
  - register and require v2 presentation artifacts.
- apps/case02_openenv/src/case02_openenv/recording/verifier.py
  - retain media checks and add named event-frame inputs.
- scripts/coworker_demo/verify_run_bundle.py
  - independently validate v2, incident/history correlation, forbidden fields,
    model identity, and named event frames.
- scripts/coworker_demo/scripted_shell_gate.py
  - add a test-only failure-matrix profile; never use it for final acceptance.

### Modified tests

- tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py
- tests/case02_openenv/test_presentation.py
- tests/case02_openenv/test_api_contract.py
- tests/case02_openenv/test_pages.py
- tests/case02_openenv/test_recorder.py
- tests/coworker_demo/test_verify_run_bundle_presentation.py
- tests/case02_openenv/test_independent_bundle_verifier.py

### Modified documentation

- README.md
- docs/coworker-demo-user-guide.md
- docs/architecture/coworker-demo.md
- docs/pitfalls.md
- CHANGELOG.md

---

### Task 1: Define Presentation Protocol V2 And Pure Observable Reducer

**Files:**

- Create: apps/case02_openenv/src/case02_openenv/observable_presentation.py
- Create: apps/case02_openenv/src/case02_openenv/presentation_models.py
- Create: tests/case02_openenv/test_observable_presentation.py
- Modify: apps/case02_openenv/src/case02_openenv/presentation.py
- Modify: CHANGELOG.md

- [ ] **Step 1: Add failing type and reducer tests**

Create tests that instantiate version-2 plans, summaries, incidents, and
history, then reduce ordered events:

~~~python
def test_reducer_rebuilds_plan_current_action_and_result() -> None:
    events = [
        presentation_event(
            sequence=1,
            event_type="tool.call_started",
            tool_call_id="call-plan",
            action_id="action-plan",
            tool_name="task_planner",
            status="running",
        ),
        presentation_event(
            sequence=2,
            event_type="tool.call_completed",
            tool_call_id="call-plan",
            action_id="action-plan",
            tool_name="task_planner",
            status="succeeded",
            plan={
                "items": [
                    {"id": "precheck", "title": "Complete checks", "status": "in_progress"}
                ],
                "current_id": "precheck",
                "next_focus": "Query alarm",
            },
        ),
    ]

    observable = reduce_observable_state(run_state("observable-plan"), events)

    assert observable.plan.current_id == "precheck"
    assert observable.plan.items[0].status == "in_progress"
    assert observable.current_action.tool_name == "task_planner"
    assert observable.last_result.status == "succeeded"
~~~

Add parametrized tests for every design failure code and decision-summary
state. Add tests proving schema version 1 is rejected by v2 models.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

~~~bash
apps/case02_openenv/.venv/bin/pytest \
  tests/case02_openenv/test_observable_presentation.py -q
~~~

Expected: collection or import failure because observable_presentation does not
exist and presentation models still require schema version 1.

- [ ] **Step 3: Add the v2 immutable models**

Implement these public shapes in presentation_models.py. Keep closed labels in
the same module so validation and display vocabulary share one source:

~~~python
PlanStatus = Literal["pending", "in_progress", "completed", "failed", "cancelled"]
DecisionState = Literal[
    "planning",
    "observing",
    "ready",
    "waiting",
    "verified",
    "blocked",
    "anomaly",
    "recovering",
    "terminal",
]
IncidentStatus = Literal["open", "resolved"]


class ObservablePlanItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    title: str
    status: PlanStatus


class ObservablePlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[ObservablePlanItem, ...] = ()
    current_id: str | None = None
    next_focus: str | None = None


class SummaryTerm(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    label_zh: str
    values: dict[str, str | int | bool] = Field(default_factory=dict)


class DecisionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: DecisionState
    fact: SummaryTerm
    judgment: SummaryTerm
    next_action: SummaryTerm


class IncidentRecovery(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_name: str
    action_id: str
    resolved_sequence: int
    intervening_model_calls: int


class PresentationIncident(BaseModel):
    model_config = ConfigDict(frozen=True)
    incident_id: str
    status: IncidentStatus
    failure_code: str
    label_zh: str
    failed_tool: str
    failed_action_id: str
    opened_sequence: int
    target: dict[str, str] = Field(default_factory=dict)
    recovery: IncidentRecovery | None = None


class CriticalHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    history_id: str
    sequence: int
    kind: Literal["gate", "job", "grep", "causal_alarm", "incident", "recovery", "terminal"]
    label_zh: str
    status: str
    action_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class PublicModelOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["assistant_reply"]
    text: str
    outcome: Literal["terminal", "premature"]


class ObservablePresentationState(BaseModel):
    model_config = ConfigDict(frozen=True)
    plan: ObservablePlan
    current_action: PresentationEvent | None
    last_result: PresentationEvent | None
    public_model_output: PublicModelOutput | None
    decision_summary: DecisionSummary
    incidents: tuple[PresentationIncident, ...] = ()
    critical_history: tuple[CriticalHistoryEntry, ...] = ()
~~~

Add closed dictionaries FAILURE_LABELS_ZH, TOOL_LABELS_ZH,
FACT_LABELS_ZH, JUDGMENT_LABELS_ZH, and NEXT_ACTION_LABELS_ZH. Models reject
unknown codes rather than accept arbitrary text.

- [ ] **Step 4: Upgrade service presentation contracts to v2**

Move PresentationInput, PresentationTask, PresentationEvent, and
PresentationSnapshot into presentation_models.py and change them to
schema_version Literal[2]. case02_openenv.presentation imports and re-exports
these names so existing callers do not silently switch to a different
interface. Add typed optional fields:

~~~python
failure_code: str | None = None
plan: ObservablePlan | None = None
public_model_output: PublicModelOutput | None = None
decision_summary: DecisionSummary | None = None
incident_delta: PresentationIncident | None = None
~~~

PresentationInput.runtime_event_type also accepts model.public_reply. The client
projector includes schema_version: 2 on every POST; version 1 is not silently
upgraded.

PresentationSnapshot adds:

~~~python
plan: ObservablePlan
current_action: PresentationEvent | None
last_result: PresentationEvent | None
public_model_output: PublicModelOutput | None
decision_summary: DecisionSummary
incidents: list[PresentationIncident]
critical_history: list[CriticalHistoryEntry]
~~~

Retain exact SOP task/source hash fields and action correlation.

- [ ] **Step 5: Implement the pure reducer**

Implement:

~~~python
def reduce_observable_state(
    state: RunState,
    events: Sequence[PresentationEvent],
) -> ObservablePresentationState:
    plan = latest_successful_plan(events)
    current_action = latest_started_action(events)
    last_result = latest_terminal_result(events)
    incidents = reduce_incidents(events)
    history = select_critical_history(events, incidents)
    decision = derive_decision_summary(state, events, incidents)
    public_output = latest_public_model_output(events)
    return ObservablePresentationState(
        plan=plan,
        current_action=current_action,
        last_result=last_result,
        public_model_output=public_output,
        decision_summary=decision,
        incidents=incidents,
        critical_history=history,
    )
~~~

The reducer reads only ordered persisted events and RunState. It performs no
I/O and does not mutate Episode.

- [ ] **Step 6: Implement exact incident recovery rules**

Use a closed mapping keyed by failure code. Matching wait recovery must compare
the incident job_id with browser_wait result job_id. Decision recovery must
compare intended decision and valid stage. Count only later tool.call_started
events when computing intervening_model_calls.

Unknown failures remain open until the same tool has a successful retry with a
matching safe target; terminal_outcome never resolves.

- [ ] **Step 7: Run reducer and existing presentation tests**

Run:

~~~bash
apps/case02_openenv/.venv/bin/pytest \
  tests/case02_openenv/test_observable_presentation.py \
  tests/case02_openenv/test_presentation.py -q
~~~

Expected: PASS.

- [ ] **Step 8: Record the changelog and commit**

Add this exact Unreleased/Added entry:

~~~text
新增 presentation v2 强类型协议与纯事件 reducer，从同一 run 的 append-only 展示事件确定性重建模型计划、当前动作/结果、决策摘要、异常恢复和关键历史，避免浏览器或 Episode 维护不可审计的第二套状态。
~~~

Commit with the same content:

~~~bash
git add \
  apps/case02_openenv/src/case02_openenv/observable_presentation.py \
  apps/case02_openenv/src/case02_openenv/presentation_models.py \
  apps/case02_openenv/src/case02_openenv/presentation.py \
  tests/case02_openenv/test_observable_presentation.py \
  tests/case02_openenv/test_presentation.py \
  CHANGELOG.md
git commit -m "feat(coworker): add observable presentation v2 reducer" \
  -m "新增 presentation v2 强类型协议与纯事件 reducer，从同一 run 的 append-only 展示事件确定性重建模型计划、当前动作/结果、决策摘要、异常恢复和关键历史，避免浏览器或 Episode 维护不可审计的第二套状态。"
~~~

---

### Task 2: Project Safe Model Plan, Public Reply, And Failure Codes

**Files:**

- Modify: src/homemaster/benchmarking/coworker_demo/presentation.py
- Modify: src/homemaster/benchmarking/coworker_demo/tracing.py
- Modify: src/homemaster/benchmarking/coworker_demo/turn.py
- Modify: tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py
- Modify: tests/homemaster/benchmarking/coworker_demo/test_environment_client.py
- Modify: CHANGELOG.md

- [ ] **Step 1: Add failing safe-projection tests**

Add tests for:

~~~python
def test_successful_task_snapshot_projects_bounded_plan() -> None:
    projected = project_runtime_event(
        event(
            "tool.call_completed",
            name="task_planner",
            tool_call_id="call-plan",
            payload={"data": task_snapshot()},
        )
    )
    assert projected["plan"]["current_id"] == "precheck"
    assert projected["plan"]["items"] == [
        {"id": "precheck", "title": "Complete checks", "status": "in_progress"}
    ]
    serialized = json.dumps(projected)
    assert "evidence" not in serialized
    assert "constraints" not in serialized
    assert "open_questions" not in serialized


def test_known_failure_is_normalized_without_raw_exception() -> None:
    projected = project_runtime_event(
        event(
            "tool.call_failed",
            name="browser_click",
            tool_call_id="call-remove",
            payload={"data": {"failure_reason": "RuntimeError: remove requires a rollback decision"}},
        )
    )
    assert projected["failure_code"] == "rollback_decision_required"
    assert "RuntimeError" not in json.dumps(projected)


def test_assistant_reply_projects_bounded_public_text_but_thinking_does_not() -> None:
    reply = project_runtime_event(event("assistant.reply", payload={"reply": "Run blocked."}))
    assert reply["runtime_event_type"] == "model.public_reply"
    assert reply["public_model_output"]["text"] == "Run blocked."
    assert project_runtime_event(
        event("assistant.thinking", payload={"thinking": "private reasoning"})
    ) is None
~~~

Add per-limit tests for 12 plan items, 64-character IDs, 160-character titles,
240-character next focus, 1,200-character reply, control characters, and secret
sentinels.

- [ ] **Step 2: Run tests and verify RED**

Run:

~~~bash
.venv/bin/pytest \
  tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py -q
~~~

Expected: new assertions fail because current projection emits schema v1,
discards planner details, excludes assistant.reply, and has no failure_code.

- [ ] **Step 3: Implement closed planner projection**

Add _safe_plan_snapshot that accepts only a successful persisted task snapshot.
Build a fresh dictionary containing items, current_id, and next_focus. Do not
copy arbitrary source dictionaries.

Use the task store field names actually present in runtime data:

~~~python
def _safe_plan_snapshot(value: Any) -> dict[str, Any] | None:
    source = _dict(value)
    subtasks = source.get("subtasks")
    if not isinstance(subtasks, list):
        return None
    items = [_safe_plan_item(item) for item in subtasks[:12]]
    current = source.get("current_subtask")
    next_focus = source.get("next_focus")
    return {
        "items": items,
        "current_id": _safe_plan_id(current),
        "next_focus": _safe_display_text(next_focus, limit=240),
    }
~~~

Any unsafe item makes the affected field null/redacted and records a generic
projection failure code. Never forward evidence arrays or free-form constraints.
Use homemaster.events.trace.sanitize_for_log only as a detector: if sanitized
text differs from input text, reject the field instead of displaying the
redaction marker. This prevents a secret-bearing plan/reply from being treated
as a successful presentation.

- [ ] **Step 4: Implement closed failure-code normalization**

Match only exact code prefixes or exact stable substrings:

~~~python
_FAILURE_PATTERNS = (
    ("missing_precheck_evidence", "missing_precheck_evidence"),
    ("progress_required", "progress_required"),
    ("wait_required", "wait_required"),
    ("remove requires a rollback decision", "rollback_decision_required"),
    ("invalid_decision_for_stage", "invalid_decision_for_stage"),
    ("stale_state_version", "stale_state_version"),
    ("action_replay", "action_replay"),
    ("terminal_outcome", "terminal_outcome"),
)
~~~

Return unclassified_failure when no pattern matches. Do not include the raw
failure string in the projected payload.

- [ ] **Step 5: Project public replies without thinking**

Add assistant.reply to the trace mirror set. Emit runtime_event_type
model.public_reply with bounded plain text and no tool identity. Keep
assistant.thinking excluded. Mark a public reply premature or terminal from the
authoritative Coworker outcome supplied by turn.py, never from reply wording.

Do not synthesize a model reply when stop_condition creates an application
terminal message; the footer already owns terminal outcome.

- [ ] **Step 6: Run focused client tests**

Run:

~~~bash
.venv/bin/pytest \
  tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py \
  tests/homemaster/benchmarking/coworker_demo/test_environment_client.py -q
~~~

Expected: PASS, with the existing secret-removal and correlation tests still
green.

- [ ] **Step 7: Record the changelog and commit**

Add:

~~~text
实时展示投影新增持久化 Planner 快照、公开 assistant reply 和封闭失败码；继续拒绝 assistant.thinking、Prompt、证据原文、任意异常文本及敏感字段，且不向模型建立观察面板回流。
~~~

Commit:

~~~bash
git add \
  src/homemaster/benchmarking/coworker_demo/presentation.py \
  src/homemaster/benchmarking/coworker_demo/tracing.py \
  src/homemaster/benchmarking/coworker_demo/turn.py \
  tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py \
  CHANGELOG.md
git commit -m "feat(coworker): project safe model output and plans" \
  -m "实时展示投影新增持久化 Planner 快照、公开 assistant reply 和封闭失败码；继续拒绝 assistant.thinking、Prompt、证据原文、任意异常文本及敏感字段，且不向模型建立观察面板回流。"
~~~

---

### Task 3: Integrate Observable Reduction Into EpisodeStore, Snapshot, And SSE

**Files:**

- Modify: apps/case02_openenv/src/case02_openenv/episode_store.py
- Modify: apps/case02_openenv/src/case02_openenv/api.py
- Modify: tests/case02_openenv/test_presentation.py
- Modify: tests/case02_openenv/test_api_contract.py
- Modify: CHANGELOG.md

- [ ] **Step 1: Add failing append, snapshot, and reconnect tests**

Cover:

~~~python
def test_snapshot_contains_reduced_plan_summary_incidents_and_history(api) -> None:
    run_id = create_run(api, "observable-snapshot")
    post_started_and_failed_wait_required(api, run_id, job_id="job-add-abcdef1234")

    snapshot = api.get(f"/api/runs/{run_id}/presentation").json()["snapshot"]

    assert snapshot["schema_version"] == 2
    assert snapshot["current_action"]["tool_name"] == "terminal_execute"
    assert snapshot["last_result"]["failure_code"] == "wait_required"
    assert snapshot["decision_summary"]["state"] == "blocked"
    assert snapshot["incidents"][0]["status"] == "open"
    assert snapshot["critical_history"][-1]["kind"] == "incident"
~~~

Add a reconnect test that loads a snapshot after failure, posts a matching wait
success, reconnects after the prior sequence, and sees one resolved incident
without duplicate history.

- [ ] **Step 2: Run tests and verify RED**

Run:

~~~bash
apps/case02_openenv/.venv/bin/pytest \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_api_contract.py -q
~~~

Expected: v2 payload rejection or missing snapshot fields.

- [ ] **Step 3: Build candidate snapshots from candidate events**

In record_presentation:

1. validate the run ID and action correlation;
2. map exact SOP source;
3. build the candidate PresentationEvent;
4. append it only to an in-memory candidate event list;
5. call reduce_observable_state with current RunState and candidate events;
6. build the complete v2 snapshot;
7. append JSONL and atomically write snapshot;
8. roll back JSONL on snapshot failure; and
9. publish candidate state only after both writes succeed.

Do not add mutable plan/incident/history fields to Episode. Reset clears the
event ledger and generation exactly as before.

- [ ] **Step 4: Separate current action from last environment result**

The snapshot reducer selects:

~~~python
current_action = latest event with status running, else latest tool start
last_result = latest event with status in accepted/succeeded/failed/rejected
~~~

A result does not overwrite the model-action ownership label; both cards retain
the same tool_call_id and action_id.

- [ ] **Step 5: Preserve snapshot/SSE monotonic recovery**

Keep generation and last_sequence checks. The SSE stream sends the full v2
snapshot first and events strictly after the requested sequence. API tests
assert no duplicate incidents or history after reconnect.

- [ ] **Step 6: Add protocol implementation audit**

Add a test that compares the public fields of PresentationInput,
PresentationEvent, PresentationSnapshot, and observable models against an
explicit expected field set. Also assert observer fixture keys and verifier
required keys cover every v2 field.

- [ ] **Step 7: Run service contract tests**

Run:

~~~bash
apps/case02_openenv/.venv/bin/pytest \
  tests/case02_openenv/test_observable_presentation.py \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_api_contract.py -q
~~~

Expected: PASS.

- [ ] **Step 8: Record the changelog and commit**

Add:

~~~text
EpisodeStore 现从候选 append-only 展示事件原子重建 presentation v2 Snapshot，SSE 重连可恢复模型计划、当前动作/结果、决策摘要和异常历史，不在浏览器或 Episode 中维护漂移副本。
~~~

Commit:

~~~bash
git add \
  apps/case02_openenv/src/case02_openenv/episode_store.py \
  apps/case02_openenv/src/case02_openenv/api.py \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_api_contract.py \
  CHANGELOG.md
git commit -m "feat(coworker): publish observable v2 snapshots" \
  -m "EpisodeStore 现从候选 append-only 展示事件原子重建 presentation v2 Snapshot，SSE 重连可恢复模型计划、当前动作/结果、决策摘要和异常历史，不在浏览器或 Episode 中维护漂移副本。"
~~~

---

### Task 4: Build The Five-Region Executive Observer

**Files:**

- Modify: apps/case02_openenv/templates/observer.html
- Modify: apps/case02_openenv/static/observer.js
- Modify: apps/case02_openenv/static/app.css
- Modify: tests/case02_openenv/test_pages.py
- Modify: tests/case02_openenv/test_recorder.py
- Modify: CHANGELOG.md

- [ ] **Step 1: Add failing structure and rendering-contract tests**

Require these IDs:

~~~text
model-plan
plan-current
plan-next
model-output-kind
model-output-tool
model-output-arguments
public-model-reply
environment-result-status
environment-result-summary
decision-fact
decision-judgment
decision-next
open-incident
resolved-incidents
critical-history
~~~

Assert no input, button, form, select, textarea, mutating URL, innerHTML,
insertAdjacentHTML, document.write, or eval exists.

Add JavaScript source-contract assertions that observer rendering consumes
snapshot.plan, snapshot.current_action, snapshot.last_result,
snapshot.public_model_output, snapshot.decision_summary, snapshot.incidents,
and snapshot.critical_history.

- [ ] **Step 2: Run page tests and verify RED**

Run:

~~~bash
apps/case02_openenv/.venv/bin/pytest \
  tests/case02_openenv/test_pages.py \
  tests/case02_openenv/test_recorder.py -q
~~~

Expected: missing element and rendering-field failures.

- [ ] **Step 3: Replace the dashboard markup**

Keep the seven-stage header, 1320 px reserved Agent area, and outcome footer.
Create five semantic sections:

~~~html
<section class="observer-card current-sop-card" aria-labelledby="current-sop-heading"></section>
<section class="observer-card model-plan-card" aria-labelledby="model-plan-heading"></section>
<section class="observer-card model-output-card" aria-labelledby="model-output-heading"></section>
<section class="observer-card result-decision-card" aria-labelledby="result-heading"></section>
<section class="observer-card incident-history-card" aria-labelledby="incident-heading"></section>
~~~

Use visible Chinese labels for plan, model action, environment result, decision
summary, open incident, recovered incidents, and critical history.

- [ ] **Step 4: Implement render-only v2 JavaScript**

Implement small functions:

~~~javascript
const renderPlan = (plan) => {
  const items = (plan && plan.items) || [];
  const rows = items.map((entry) => {
    const item = document.createElement("li");
    item.dataset.status = entry.status;
    item.textContent = entry.title;
    return item;
  });
  node("model-plan").replaceChildren(...rows);
  setText("plan-current", plan && plan.current_id);
  setText("plan-next", plan && plan.next_focus);
};

const renderModelOutput = (action, publicOutput) => {
  setText("model-output-tool", action && action.tool_name, "等待模型选择工具");
  setText("model-output-arguments", safeObjectText(action && action.arguments));
  setText("public-model-reply", publicOutput && publicOutput.text);
};

const renderEnvironmentResult = (result) => {
  showStatus(result ? result.status : "running");
  setText("environment-result-summary", safeObjectText(result && result.result));
};

const renderDecisionSummary = (summary) => {
  setText("decision-fact", summary && summary.fact && summary.fact.label_zh);
  setText("decision-judgment", summary && summary.judgment && summary.judgment.label_zh);
  setText("decision-next", summary && summary.next_action && summary.next_action.label_zh);
};

const renderResolvedIncident = (entry) => {
  const item = document.createElement("li");
  item.dataset.status = "resolved";
  item.textContent = entry.label_zh + " · " + entry.recovery.tool_name;
  return item;
};

const renderHistoryEntry = (entry) => {
  const item = document.createElement("li");
  item.dataset.kind = entry.kind;
  item.textContent = entry.label_zh;
  return item;
};

const renderIncidents = (incidents) => {
  const open = (incidents || []).find((entry) => entry.status === "open");
  setText("open-incident", open && open.label_zh, "当前无未恢复异常");
  const resolved = (incidents || []).filter((entry) => entry.status === "resolved");
  node("resolved-incidents").replaceChildren(...resolved.map(renderResolvedIncident));
};

const renderCriticalHistory = (history) => {
  node("critical-history").replaceChildren(...(history || []).map(renderHistoryEntry));
};
~~~

Replace the empty bodies with textContent/createElement/replaceChildren logic.
Open incidents render expanded with failure code and Chinese label. Resolved
incidents render one collapsed line containing failure and recovery tool.
Every tool name uses the server-provided closed label and remains visible in
large text.

applySnapshot replaces all six dynamic structures atomically. applyEvent may
render immediate current action/result but always refreshes the authoritative
snapshot afterward.

- [ ] **Step 5: Implement fixed no-overlap CSS**

Use a fixed dashboard grid inside the existing 900 px center band:

~~~css
.executive-dashboard {
  display: grid;
  grid-template-rows: 150px 190px 145px 205px 162px;
  gap: 12px;
  height: 900px;
  overflow: hidden;
}
~~~

Each card uses min-height: 0 and internal overflow: hidden or auto. Do not use
viewport-scaled font sizes or negative letter spacing. Keep tool name, failure
code, and decision next action at readable fixed sizes.

- [ ] **Step 6: Add browser rendering checks**

Use the case app test client plus Playwright to post a v2 snapshot containing:

- a 12-item plan with long titles;
- a long safe job ID;
- one open incident;
- two resolved incidents;
- five history rows; and
- a bounded public reply.

Capture 1920x1080 and assert every required element has a positive bounding box
inside its card and no sibling rectangles overlap.

- [ ] **Step 7: Run observer tests**

Run:

~~~bash
apps/case02_openenv/.venv/bin/pytest \
  tests/case02_openenv/test_pages.py \
  tests/case02_openenv/test_recorder.py -q
~~~

Expected: PASS.

- [ ] **Step 8: Record the changelog and commit**

Add:

~~~text
高管录屏面板改为五区固定布局，常驻展示真实模型计划、每次模型工具选择、独立环境返回和确定性决策摘要；异常展开置顶并在匹配恢复后折叠保留，长文本不再挤压或覆盖相邻区域。
~~~

Commit:

~~~bash
git add \
  apps/case02_openenv/templates/observer.html \
  apps/case02_openenv/static/observer.js \
  apps/case02_openenv/static/app.css \
  tests/case02_openenv/test_pages.py \
  tests/case02_openenv/test_recorder.py \
  CHANGELOG.md
git commit -m "feat(coworker): render observable model behavior" \
  -m "高管录屏面板改为五区固定布局，常驻展示真实模型计划、每次模型工具选择、独立环境返回和确定性决策摘要；异常展开置顶并在匹配恢复后折叠保留，长文本不再挤压或覆盖相邻区域。"
~~~

---

### Task 5: Strengthen Independent Presentation And Model-Identity Verification

**Files:**

- Modify: apps/case02_openenv/src/case02_openenv/presentation.py
- Modify: apps/case02_openenv/src/case02_openenv/evaluation/scoring.py
- Modify: apps/case02_openenv/src/case02_openenv/recording/verifier.py
- Modify: scripts/coworker_demo/verify_run_bundle.py
- Modify: apps/case02_openenv/openapi.json
- Modify: tests/coworker_demo/test_verify_run_bundle_presentation.py
- Modify: tests/case02_openenv/test_independent_bundle_verifier.py
- Modify: tests/case02_openenv/test_api_contract.py
- Modify: CHANGELOG.md

- [ ] **Step 1: Add failing independent verifier tests**

Build valid v2 fixture bundles, then mutate one fact at a time:

~~~python
@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("schema_v1", "presentation_schema_version"),
        ("missing_plan", "presentation_snapshot_missing:plan"),
        ("unknown_failure_code", "presentation_failure_code"),
        ("orphan_incident", "presentation_incident_orphan"),
        ("bad_recovery_sequence", "presentation_recovery_order"),
        ("history_unknown_event", "presentation_history_event"),
        ("forbidden_thinking", "presentation_forbidden_field"),
        ("wrong_model", "runtime_model_identity"),
    ],
)
def test_v2_verifier_rejects_single_contract_violation(
    valid_bundle: Path, mutation: str, expected: str
) -> None:
    mutate_bundle(valid_bundle, mutation)
    report = verify(valid_bundle, DATA_ROOT, expected_model="mimo-v2.5")
    assert expected in report["failures"]
~~~

- [ ] **Step 2: Run verifier tests and verify RED**

Run:

~~~bash
.venv/bin/pytest \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  tests/case02_openenv/test_independent_bundle_verifier.py \
  tests/case02_openenv/test_api_contract.py -q
~~~

Expected: v2 fixtures or expected_model argument unsupported.

- [ ] **Step 3: Extend product presentation verification**

verify_presentation_payload requires:

- schema version 2 on every event and snapshot;
- one terminal result per tool start;
- exact action ID correlation;
- plan only on successful planner/progress results;
- safe failure code on every failed/rejected event;
- one incident per failed/rejected model tool;
- recovery after open sequence with a valid target;
- critical-history IDs referencing existing events/incidents;
- no assistant.thinking, prompt, evidence body, constraints, headers, or secret
  keys anywhere in presentation JSON; and
- observer alive through recording stop.

- [ ] **Step 4: Extend the independent stdlib verifier**

Change verify signature:

~~~python
def verify(
    run_root: Path,
    data_root: Path,
    *,
    expected_model: str | None = None,
) -> dict[str, Any]:
~~~

When expected_model is provided, parse agent/runtime_events.jsonl and require:

1. at least one transport.request_started;
2. every provider transport event model equals expected_model;
3. no scripted-coworker value;
4. tool-start count equals presentation verification tool_call_count; and
5. every runtime failed tool has a matching presentation failure code.

Expose CLI option:

~~~text
--expected-model mimo-v2.5
~~~

- [ ] **Step 5: Add named event-frame verification**

Persist presentation sequence timestamps for:

- first model action;
- each incident open;
- each incident resolution;
- causal alarm;
- terminal outcome.

The recorder verifier extracts those frames in addition to first/middle/last.
For each 1920x1080 frame, assert nonblank content and that the right observer
region has nontrivial variance. Store paths and checks under video_manifest.

Do not use OCR as the only correctness gate. Correlate each frame timestamp
with the persisted presentation event and separately use Playwright DOM
assertions for exact visible text.

- [ ] **Step 6: Refresh OpenAPI snapshot**

Generate apps/case02_openenv/openapi.json using the same ServiceConfig fixture
as test_openapi_snapshot_matches_runtime_schema. Serialize sorted UTF-8 JSON
with one trailing newline.

- [ ] **Step 7: Run verifier and API tests**

Run:

~~~bash
.venv/bin/pytest \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  tests/case02_openenv/test_independent_bundle_verifier.py \
  tests/case02_openenv/test_api_contract.py -q
~~~

Expected: PASS.

- [ ] **Step 8: Record the changelog and commit**

Add:

~~~text
产品与独立 bundle verifier 现在强制核对 presentation v2 全字段、异常/恢复/历史关联、禁止字段、每次工具生命周期、关键事件画面和真实 provider 模型身份；可用 --expected-model mimo-v2.5 拒绝 scripted 视频冒充实时 LLM 验收。
~~~

Commit:

~~~bash
git add \
  apps/case02_openenv/src/case02_openenv/presentation.py \
  apps/case02_openenv/src/case02_openenv/evaluation/scoring.py \
  apps/case02_openenv/src/case02_openenv/recording/verifier.py \
  scripts/coworker_demo/verify_run_bundle.py \
  apps/case02_openenv/openapi.json \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  tests/case02_openenv/test_independent_bundle_verifier.py \
  tests/case02_openenv/test_api_contract.py \
  CHANGELOG.md
git commit -m "feat(coworker): verify observable real-model recordings" \
  -m "产品与独立 bundle verifier 现在强制核对 presentation v2 全字段、异常/恢复/历史关联、禁止字段、每次工具生命周期、关键事件画面和真实 provider 模型身份；可用 --expected-model mimo-v2.5 拒绝 scripted 视频冒充实时 LLM 验收。"
~~~

---

### Task 6: Add A Test-Only Failure/Recovery Black-Box Gate

**Files:**

- Modify: scripts/coworker_demo/scripted_shell_gate.py
- Modify: tests/case02_openenv/test_pages.py
- Modify: tests/coworker_demo/test_verify_run_bundle_presentation.py
- Modify: docs/pitfalls.md
- Modify: CHANGELOG.md

- [ ] **Step 1: Add failing failure-matrix profile tests**

Add profile choices clean and observable_failures. Tests assert the failure
profile contains, in live order:

Normal:

~~~text
premature precheck progress
rejected precheck proceed
five monitor checks
successful precheck proceed
rejected automation navigation because progress is missing
successful progress update and recovery
normal completion
~~~

Anomaly:

~~~text
rejected precheck proceed
prechecks and successful proceed
add submit
rejected grep before wait
matching wait and grep recovery
causal alarm
four additional monitor reads
rejected remove before authorization
rejected rollback at wrong stage
valid rollback authorization
remove, matching wait, absence grep, rolled_back
~~~

This profile tests presentation only. Its model remains scripted-coworker.

- [ ] **Step 2: Run tests and verify RED**

Run:

~~~bash
.venv/bin/pytest \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  tests/case02_openenv/test_pages.py -q
~~~

Expected: profile argument and incident assertions unsupported.

- [ ] **Step 3: Implement profile-specific scripted steps**

Add profile to ScriptedConversation and CLI. Reuse existing dynamic evidence,
job, command, and visible-field resolvers. Never copy old evidence or job IDs.
Keep clean as the default so existing gates retain behavior.

- [ ] **Step 4: Run the controlled normal failure gate**

Run:

~~~bash
.venv/bin/python scripts/coworker_demo/scripted_shell_gate.py \
  --scenario normal \
  --profile observable_failures \
  --ticket data/coworker_demo/case_02/test_set/item_change_ticket.json \
  --output-root var/coworker-demo/observable-failure-gate
~~~

Expected:

- shell return 0;
- formal bundle success;
- exactly two resolved incidents;
- no open incident;
- each failure and recovery has a named event frame;
- video and presentation verification pass.

- [ ] **Step 5: Run the controlled anomaly failure gate**

Run:

~~~bash
.venv/bin/python scripts/coworker_demo/scripted_shell_gate.py \
  --scenario post_change_anomaly \
  --profile observable_failures \
  --ticket data/coworker_demo/case_02/test_set/item_change_ticket.json \
  --output-root var/coworker-demo/observable-failure-gate
~~~

Expected:

- shell return 0;
- rolled_back;
- exactly four resolved incidents;
- no open incident;
- causal alarm and all failure/recovery frames present;
- add grep exit 0 and rollback grep exit 1 with empty output.

- [ ] **Step 6: Inspect every incident frame**

Use view_image on each extracted incident-open and incident-resolved PNG.
Per frame verify:

- model tool name is readable;
- environment status and safe failure code are visible;
- Chinese reason is visible;
- open incident is expanded;
- recovered incident is one collapsed line;
- no panel text overlaps; and
- Agent Chrome remains visible.

Any per-instance failure fails the gate; do not accept one good frame as an
aggregate pass.

- [ ] **Step 7: Record the pitfall and changelog**

Add the newest docs/pitfalls.md entry:

~~~text
症状：零失败 scripted 视频和单测都通过，但真实 LLM 的拒绝原因在录屏里只闪现通用失败。
根因：验收轨迹没有失败实例，observer 只保留 latest result，presentation 投影丢弃稳定错误码。
修法/教训：发布前用独立 failure-matrix 黑盒 run 逐实例触发并恢复所有门禁错误，同时保留最终真实 LLM 验收；脚本门只验证展示，绝不替代真实模型。
Ref：实时 LLM 可观测 Coworker 设计与 observable-failure-gate run bundle。
~~~

Add CHANGELOG:

~~~text
新增仅用于展示黑盒验收的 observable_failures 脚本 profile，逐实例触发并恢复前检、进度、等待和回滚门禁错误，验证视频能读出具体原因；该 profile 明确不计入最终真实 LLM 验收。
~~~

- [ ] **Step 8: Commit**

~~~bash
git add \
  scripts/coworker_demo/scripted_shell_gate.py \
  tests/case02_openenv/test_pages.py \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  docs/pitfalls.md \
  CHANGELOG.md
git commit -m "test(coworker): gate observable failure recovery" \
  -m "新增仅用于展示黑盒验收的 observable_failures 脚本 profile，逐实例触发并恢复前检、进度、等待和回滚门禁错误，验证视频能读出具体原因；该 profile 明确不计入最终真实 LLM 验收。"
~~~

---

### Task 7: Update User, Architecture, And Operator Documentation

**Files:**

- Modify: README.md
- Modify: docs/coworker-demo-user-guide.md
- Modify: docs/architecture/coworker-demo.md
- Modify: CHANGELOG.md

- [ ] **Step 1: Update README capability and evidence labels**

State that:

- normal shell uses configured real provider;
- July 18 accepted recordings are scripted presentation gates;
- final real-LLM acceptance requires expected model mimo-v2.5;
- planner/model action/environment result/decision summary are different owners;
- hidden thinking is never displayed; and
- scripted_shell_gate cannot satisfy final demo acceptance.

- [ ] **Step 2: Update the user guide**

Document:

~~~bash
.venv/bin/python scripts/coworker_demo/preflight.py \
  --coworker-config config/coworker_demo.yaml \
  --provider-config config/homemaster.yaml

printf '%s\n/exit\n' \
  '/absolute/path/to/item_change_ticket.json' \
  | .venv/bin/homemaster shell

printf '%s\n/exit\n' \
  'post_change_anomaly /absolute/path/to/item_change_ticket.json' \
  | .venv/bin/homemaster shell
~~~

Explain all five observer regions, open/resolved incidents, public reply versus
hidden thinking, v2 artifacts, expected-model verification, and how failed
real runs are retained.

- [ ] **Step 3: Update architecture**

Document:

~~~text
real LLM
  -> tool/public-reply runtime events
  -> safe v2 projection
  -> append-only presentation ledger
  -> pure observable reducer
  -> atomic snapshot + SSE
  -> read-only observer + continuous video
~~~

State invariants for protocol coverage, incident recovery, no reverse data
flow, external-state verification, and real/scripted model identity.

- [ ] **Step 4: Run documentation consistency checks**

Run:

~~~bash
rg -n "scripted-coworker|mimo-v2.5|assistant.thinking|presentation v2|expected-model" \
  README.md docs/coworker-demo-user-guide.md docs/architecture/coworker-demo.md
~~~

Expected: each document explicitly distinguishes real acceptance from scripted
presentation tests and forbids hidden reasoning display.

- [ ] **Step 5: Record changelog and commit**

Add:

~~~text
README、用户指南和架构文档同步说明实时 Mimo 入口、五区可观测面板、presentation v2、异常恢复、公开输出/隐藏推理边界、expected-model 验证及 scripted gate 不能替代真实 LLM 视频。
~~~

Commit:

~~~bash
git add README.md docs/coworker-demo-user-guide.md docs/architecture/coworker-demo.md CHANGELOG.md
git commit -m "docs(coworker): document real-time observable runs" \
  -m "README、用户指南和架构文档同步说明实时 Mimo 入口、五区可观测面板、presentation v2、异常恢复、公开输出/隐藏推理边界、expected-model 验证及 scripted gate 不能替代真实 LLM 视频。"
~~~

---

### Task 8: Run Full Internal Verification Before External Acceptance

**Files:**

- Modify only if a test reveals a root-cause defect in an in-scope file.
- Update CHANGELOG.md before any corrective commit.

- [ ] **Step 1: Run formatting and static checks**

Run:

~~~bash
.venv/bin/ruff format --check src apps/case02_openenv/src tests scripts/coworker_demo
.venv/bin/ruff check src apps/case02_openenv/src tests scripts/coworker_demo
.venv/bin/python -m compileall -q src apps/case02_openenv/src scripts/coworker_demo
git diff --check
~~~

Expected: all commands exit 0.

- [ ] **Step 2: Run focused tests**

~~~bash
.venv/bin/pytest \
  tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py \
  tests/homemaster/benchmarking/coworker_demo/test_environment_client.py \
  tests/coworker_demo/test_verify_run_bundle_presentation.py \
  tests/case02_openenv/test_observable_presentation.py \
  tests/case02_openenv/test_presentation.py \
  tests/case02_openenv/test_api_contract.py \
  tests/case02_openenv/test_pages.py \
  tests/case02_openenv/test_recorder.py \
  tests/case02_openenv/test_independent_bundle_verifier.py -q
~~~

Expected: PASS.

- [ ] **Step 3: Run the complete suite**

~~~bash
.venv/bin/pytest -q
~~~

Expected: PASS with only repository-documented skips.

- [ ] **Step 4: Run preflight again**

~~~bash
.venv/bin/python scripts/coworker_demo/preflight.py \
  --coworker-config config/coworker_demo.yaml \
  --provider-config config/homemaster.yaml
~~~

Expected: pass true and provider model mimo-v2.5. This still leaves actual
provider acceptance UNVERIFIED.

- [ ] **Step 5: Confirm a clean implementation worktree**

~~~bash
git status --short
git log --oneline -8
~~~

Expected: no uncommitted source/doc changes; each implementation commit has a
matching CHANGELOG entry.

---

### Task 9: Produce And Verify The Real Mimo Normal Recording

**Files:**

- Create after success: docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md
- Modify: CHANGELOG.md

- [ ] **Step 1: Launch normal through the real shell path**

Do not set HOMEMASTER_COWORKER_PROVIDER_CONFIG to a generated scripted config.
Run:

~~~bash
TICKET=/home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json
printf '%s\n/exit\n' "$TICKET" \
  | .venv/bin/homemaster shell \
  | tee var/coworker-demo/real-normal-shell.log
NORMAL_RUN_ROOT=$(sed -n 's/^运行产物：//p' \
  var/coworker-demo/real-normal-shell.log | tail -n 1)
test -n "$NORMAL_RUN_ROOT" && test -d "$NORMAL_RUN_ROOT"
~~~

Expected: shell prints a fresh run root and video path.

- [ ] **Step 2: Verify provider identity before judging business outcome**

Run:

~~~bash
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  "$NORMAL_RUN_ROOT" \
  --expected-model mimo-v2.5
~~~

Expected: process exit 0, no scripted-coworker transport, and verifier report
pass true.

- [ ] **Step 3: Assert normal external end state**

Independently read:

- scores/summary.json: scenario normal, terminal complete, formal success true;
- environment/episode_root service config: exact four-field record present;
- terminal/commands.jsonl: locked grep exit 0 with exact record;
- business job audit: exact job reached succeeded with return code 0; and
- presentation/verification.json: v2 passed with every tool lifecycle paired.

Do not accept score logs alone.

- [ ] **Step 4: Inspect video and named frames**

Use view_image for:

- first model action;
- middle frame;
- every incident open/resolved frame if present;
- business verification;
- terminal outcome.

Per frame assert plan, tool, environment result, decision summary, and
incidents are readable without overlap. Verify the Agent page shows the
corresponding real action.

- [ ] **Step 5: Handle a failed real attempt without hiding it**

If provider, model, business, presentation, or video verification fails:

1. preserve the run bundle and record the failure in the acceptance report;
2. find the root cause before editing;
3. distinguish model behavior from product defect;
4. for a product defect, add a failing regression, make one targeted fix,
   update CHANGELOG, commit, and rerun internal verification;
5. use a fresh run ID for the next real attempt; and
6. continue until one independently verified real normal video succeeds.

- [ ] **Step 6: Start the acceptance report**

Record every normal attempt in a table with run ID, model, call count, failure
count, outcome, external config state, presentation result, video hash, and
accepted/rejected reason. Mark exactly one successful normal run as the
accepted normal video.

---

### Task 10: Produce And Verify The Real Mimo Anomaly Recording

**Files:**

- Modify: docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md
- Modify: CHANGELOG.md

- [ ] **Step 1: Launch anomaly through the real shell path**

~~~bash
TICKET=/home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json
printf '%s\n/exit\n' "post_change_anomaly $TICKET" \
  | .venv/bin/homemaster shell \
  | tee var/coworker-demo/real-anomaly-shell.log
ANOMALY_RUN_ROOT=$(sed -n 's/^运行产物：//p' \
  var/coworker-demo/real-anomaly-shell.log | tail -n 1)
test -n "$ANOMALY_RUN_ROOT" && test -d "$ANOMALY_RUN_ROOT"
~~~

Expected: shell prints a fresh anomaly run root and video path.

- [ ] **Step 2: Verify model identity and bundle**

~~~bash
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  "$ANOMALY_RUN_ROOT" \
  --expected-model mimo-v2.5
~~~

Expected: exit 0 and pass true.

- [ ] **Step 3: Assert anomaly external end state per target**

Independently assert:

- causal alarm is active, caused_by_current_change true, and references this
  run's exact add job;
- add job succeeded and add grep exited 0;
- rollback decision preceded remove submission;
- exact remove job succeeded with return code 0;
- rollback grep exited 1 with empty stdout;
- final config file has no target record;
- terminal outcome is rolled_back; and
- presentation v2 has no orphan/open incident unless the run terminally failed.

- [ ] **Step 4: Inspect causal-alarm, rollback, incident, and terminal frames**

Use view_image on every named frame. Per frame verify the red causal anomaly,
model-selected action, safe environment result, decision next action, expanded
open incident or collapsed recovery, rollback stage, and final rolled_back
outcome are readable without overlap.

- [ ] **Step 5: Handle failed attempts with the same root-cause discipline**

Preserve every failed bundle, classify model versus product failure, fix only
proven product defects with regression coverage, and use a fresh run ID.
Continue until one independently verified real anomaly video succeeds.

- [ ] **Step 6: Complete report and changelog**

The report includes:

- every normal and anomaly attempt;
- accepted normal and anomaly run IDs;
- runtime model identity;
- exact video absolute paths and SHA-256;
- call and rejected-call counts;
- whether each July 16 failure pattern naturally recurred;
- final scores and formal success;
- per-run external config state;
- independent verifier output; and
- explicit statement that no scripted run is an accepted final video.

Add CHANGELOG:

~~~text
完成两条由真实 Mimo mimo-v2.5 现场决策的可观测录屏：normal 与 post_change_anomaly 均通过模型身份、工具/展示关联、真实配置终态、自动化返回码、grep、连续 H.264 视频和独立 bundle verifier；所有失败尝试同时保留在验收报告。
~~~

- [ ] **Step 7: Commit the acceptance evidence report**

~~~bash
git add \
  docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md \
  CHANGELOG.md
git commit -m "docs(coworker): record real-Mimo observable acceptance" \
  -m "完成两条由真实 Mimo mimo-v2.5 现场决策的可观测录屏：normal 与 post_change_anomaly 均通过模型身份、工具/展示关联、真实配置终态、自动化返回码、grep、连续 H.264 视频和独立 bundle verifier；所有失败尝试同时保留在验收报告。"
~~~

---

### Task 11: Final Audit Before Code Review

**Files:**

- Modify only for a proven in-scope issue.

- [ ] **Step 1: Re-run targeted and full tests after final-run fixes**

Run the Task 8 focused command and full pytest command again.

Expected: PASS.

- [ ] **Step 2: Re-run independent verification for both accepted bundles**

~~~bash
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  "$(sed -n 's/^运行产物：//p' \
    var/coworker-demo/real-normal-shell.log | tail -n 1)" \
  --expected-model mimo-v2.5

.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  "$(sed -n 's/^运行产物：//p' \
    var/coworker-demo/real-anomaly-shell.log | tail -n 1)" \
  --expected-model mimo-v2.5
~~~

Expected: both exit 0 and pass true.

- [ ] **Step 3: Audit interfaces, secrets, and documentation**

Run:

~~~bash
rg -n "assistant\.thinking|api[_-]?key|authorization|raw prompt|scripted-coworker" \
  apps/case02_openenv/templates \
  apps/case02_openenv/static \
  docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md
git diff --check b05c90e..HEAD
git status --short
~~~

Expected:

- no secret or hidden-thinking disclosure in UI/report;
- scripted-coworker appears only in explicit non-acceptance explanations;
- no uncommitted implementation changes; and
- all interfaces, implementations, verifiers, docs, and OpenAPI use protocol v2.

- [ ] **Step 4: Trigger the one allowed final code-review gate**

After all implementation, tests, external verification, videos, report, and
documentation are complete, start one reviewer subagent. The reviewer reads
the final diff and accepted run evidence, reports findings only, edits nothing,
and does not spawn subagents.

- [ ] **Step 5: Resolve review findings**

For each finding:

- accepted finding: make the smallest root-cause fix, update CHANGELOG if
  user-visible, commit, and run targeted plus affected external verification;
- rejected finding: record a concrete evidence-based reason;
- do not start another review automatically.

- [ ] **Step 6: Publish final handoff**

Report clickable paths for:

- design spec;
- implementation plan;
- accepted normal video and run bundle;
- accepted anomaly video and run bundle;
- acceptance report; and
- exact verification commands and results.
