# Real-Time LLM Observable Coworker Demo Design

## Status

Approved for implementation planning on 2026-07-19.

## Goal

Run the real configured LLM through the existing Coworker normal and
post-change-anomaly scenarios while the executive recording clearly shows:

1. the current locked SOP task;
2. the model-authored plan and live plan state;
3. every model-selected tool and its safe arguments;
4. the external result returned by the environment;
5. a deterministic real-time decision summary;
6. failures, their control reason, and the later recovery action; and
7. the terminal outcome and independently verified video state.

The two required acceptance recordings must use the configured Mimo /
mimo-v2.5 provider. scripted-coworker is permitted for focused presentation
tests only and cannot satisfy real-LLM acceptance.

## Problem

The current executive layout is readable for a clean scripted path, but it is
not sufficient for observing a real model:

- the July 18 acceptance videos use scripted-coworker, not the real provider;
- the current action is visible, but its relationship to model output is not
  explicit;
- planner content is reduced to counts, so the page does not show the model's
  actual plan or updates;
- all failed calls display the same generic sentence;
- a failure disappears when the next tool starts;
- the page does not show a safe explanation of known facts, the active control
  rule, and the next admissible action; and
- raw assistant.thinking exists in runtime traces but is intentionally excluded
  because it is private model reasoning, not a safe presentation contract.

The old July 16 runs remain evidence of real mimo-v2.5 behavior, but they are
not replay inputs. New acceptance runs let the model choose each action live.

## Confirmed Product Path

The normal product path already composes the required components:

~~~text
homemaster shell
  -> route_coworker_ticket
  -> run_coworker_turn
  -> start environment and create run
  -> start executive observer and FFmpeg recording
  -> launch headed Agent Chrome
  -> load configured chat provider
  -> GenericAgentRuntime tool loop
  -> CoworkerTraceSink
  -> presentation projection and SSE
  -> final scoring, video hold, stop, and independent verification
~~~

The 2026-07-19 preflight passed with Mimo / mimo-v2.5, one configured key,
the locked bundle, Chrome, TigerVNC, FFmpeg/ffprobe, libx264, Bash, bubblewrap,
tmux, disk space, and port 8765 all available.

scripts/coworker_demo/scripted_shell_gate.py is a separate deterministic
acceptance harness. It is not the only product path and must not be used for
the two final real-LLM recordings.

## Non-Goals

- Do not force the real model to reproduce the old 43- or 49-call sequence.
- Do not inject the old trajectory, GT nodes, evaluator hints, or hidden
  scenario values into the model.
- Do not show raw chain-of-thought, assistant.thinking, provider payloads,
  prompts, headers, credentials, or unrestricted exception text.
- Do not add a second summarizer LLM.
- Do not allow the observer to mutate the run or become visible to Agent tools.
- Do not replace DOM tools with coordinate clicks or VLM screenshots.
- Do not edit or accelerate the delivered video after recording.
- Do not discard a failed real-LLM run because it is unsuitable as a demo.

## Design Principles

### Truthful Layering

The screen distinguishes four owners of information:

| Layer | Owner | Meaning |
|---|---|---|
| Model plan | real LLM through task tools | What the model intends to do |
| Model action | real LLM tool selection | What tool the model selected |
| Environment result | browser, terminal, task store, or SOP gate | What happened |
| Decision summary | deterministic presentation rules | What evidence permits next |

No environment result is presented as a model claim, and no model claim is
presented as externally verified fact.

### No Hidden Reasoning Disclosure

The feature presents a real-time decision summary, not model chain-of-thought.
The summary is rederived from safe tool arguments, safe results, persisted task
state, current SOP phase, and known gate rules.

### Observer Isolation

The observer remains read-only and outside the model route allowlist:

~~~text
runtime -> trace sink -> safe projector -> environment presentation store
        -> snapshot/SSE -> observer
~~~

There is no observer-to-runtime channel.

### Fail Closed

Unknown tools, free-form failure strings, inconsistent lifecycle events,
unsafe planner text, unmatched action IDs, and unknown decision rules are
redacted or fail presentation verification. They are never guessed into a
plausible display.

## Architecture

### Existing Components Retained

- GenericAgentRuntime remains the source of model-selected tool events.
- CoworkerTraceSink remains the ordered local trace and presentation mirror.
- the client presentation projector remains the privacy boundary;
- EpisodeStore remains the run-owned append-only presentation ledger;
- snapshot plus SSE remains the observer recovery mechanism; and
- headed Chrome, TigerVNC, FFmpeg, scoring, manifest, and independent verifier
  remain the recording contract.

### New Server-Side Observability Component

Add a focused module under the case environment for:

1. validating projected plan snapshots;
2. deriving deterministic decision summaries;
3. opening and resolving incidents;
4. selecting critical-history entries; and
5. rebuilding observable state from the append-only event stream.

This logic does not live in observer.js. The browser renders typed state and
does not infer business meaning.

### Presentation Protocol Version

The presentation API and persisted artifacts move to schema version 2.
Version 2 adds:

- plan;
- public_model_output;
- decision_summary;
- incident_delta;
- incidents; and
- critical_history.

Every producer, model, store, verifier, fixture, API snapshot, and observer
consumer is updated together. A protocol audit test enumerates all public
presentation fields and verifies producer and consumer coverage.

## Safe Model Output

### Tool Selection

A tool.call_started event is the model's primary live structured output. The
observer labels it MODEL ACTION and shows:

- tool name in large type;
- a stable Chinese action label;
- safe arguments; and
- whether the action is orchestration, observation, mutation, waiting,
  independent verification, or gate decision.

Every model-selected tool appears in the current action card. Only critical
actions persist in history.

### Planner Output

Successful task_planner and task_progress_check results project the persisted
task snapshot, not merely tool arguments:

~~~json
{
  "items": [
    {"id": "precheck", "title": "Complete pre-change checks", "status": "completed"},
    {"id": "implement", "title": "Submit and verify change", "status": "in_progress"}
  ],
  "current_id": "implement",
  "next_focus": "Wait for the exact submitted job"
}
~~~

Limits:

- at most 12 items;
- item ID at most 64 safe identifier characters;
- title at most 160 Unicode characters after control-character rejection;
- next_focus at most 240 Unicode characters;
- statuses use the existing closed task-status set;
- evidence text, constraints, open questions, completion summaries, prompts,
  and arbitrary nested values are excluded; and
- unsafe text is redacted and records a presentation failure.

The plan panel updates only after the task store returns a successful persisted
snapshot.

### Public Reply

An assistant.reply runtime event is user-visible model output and may be
projected as model.public_reply. This includes a premature public reply because
hiding it would hide observable model behavior. The projected reply is:

- plain text only;
- capped at 1,200 Unicode characters;
- stripped of control characters;
- passed through secret-pattern checks; and
- omitted with a presentation failure if it cannot be safely projected; and
- marked terminal or premature from the authoritative runtime outcome rather
  than inferred from its wording.

Raw assistant.thinking, reasoning deltas, prompts, and provider responses remain
forbidden.

## Deterministic Decision Summary

The summary contains three typed fields:

~~~json
{
  "state": "blocked",
  "fact": {
    "code": "job_accepted_not_terminal",
    "values": {"job_id": "job-add-122901efdd"}
  },
  "judgment": {
    "code": "independent_readback_not_allowed",
    "values": {}
  },
  "next_action": {
    "code": "wait_exact_job",
    "values": {"job_id": "job-add-122901efdd"}
  }
}
~~~

Allowed states:

~~~text
planning, observing, ready, waiting, verified, blocked,
anomaly, recovering, terminal
~~~

Examples:

| Trusted fact | Control judgment | Next action |
|---|---|---|
| Ticket opened | Plan not persisted | Create a plan |
| Config ready, monitors incomplete | Proceed gate closed | Complete monitors |
| Add accepted, not terminal | Grep forbidden | Wait for exact add job |
| Add succeeded, grep missing | Not independently verified | Run locked grep |
| Causal alarm active | Normal completion forbidden | Record rollback |
| Remove succeeded, grep missing | Rollback not verified | Run absence grep |
| Absence grep exit 1 and empty | Rollback evidence complete | Record rolled back |

The server-side observability component owns the closed code-to-Chinese-label
mapping and emits code, label_zh, and safe values together. observer.js only
renders those typed fields and performs no business interpretation. Arbitrary
model text never populates a fact or judgment.

## Failure Taxonomy

The client projector extracts only stable, allowlisted control codes:

| Safe code | Chinese label | Recovery target |
|---|---|---|
| missing_precheck_evidence | 变更前检查证据不完整 | successful precheck proceed |
| progress_required | 当前阶段进度尚未记录 | successful progress update |
| wait_required | 尚未等待准确任务完成 | successful matching wait |
| rollback_decision_required | 尚未授权回滚 | successful rollback authorization |
| invalid_decision_for_stage | 当前阶段不接受该决定 | successful valid decision |
| stale_state_version | 页面状态已经变化 | fresh read and retry |
| action_replay | 重复动作已被拒绝 | fresh distinct action |
| terminal_outcome | 运行已经结束 | no recovery |
| unclassified_failure | 未分类执行失败 | correlated retry if possible |

Message-only errors such as "remove requires a rollback decision" are
normalized to a safe code at the projector boundary.

## Incident Lifecycle

Every failed or rejected model tool opens an incident:

~~~json
{
  "incident_id": "incident-0002",
  "status": "open",
  "failure_code": "wait_required",
  "failed_tool": "terminal_execute",
  "failed_action_id": "action-...",
  "opened_sequence": 52,
  "target": {"job_id": "job-add-122901efdd"},
  "recovery": null
}
~~~

Recovery rules:

| Failure | Resolve when |
|---|---|
| missing_precheck_evidence | check_before_change + proceed succeeds |
| progress_required | required task_progress_check succeeds |
| wait_required | browser_wait succeeds for the same job ID |
| rollback_decision_required | change_verified + rollback succeeds |
| invalid_decision_for_stage | intended decision succeeds at valid stage |
| stale_state_version | fresh observation then successful retry |
| action_replay | distinct action for same tool succeeds |
| unclassified_failure | correlated retry succeeds |

Recovery records the tool/action, resolved sequence, and intervening model-call
count. The observer shows an open incident expanded and red. Once resolved, it
collapses to one green line but remains in the snapshot and ledger. Terminal
incidents never auto-resolve.

## Critical History

The current action card shows every tool. Persistent history keeps only:

- SOP gate decisions;
- automation submission and exact job completion;
- terminal grep verification;
- causal alarm detection;
- failures and recoveries; and
- terminal outcome.

Successful planner, progress, skill, navigation, observation, fill, select, and
ordinary monitor actions remain visible while current but do not accumulate
unless they fail or produce a causal anomaly.

## Executive Observer Layout

The 1920x1080 contract remains:

- 96 px stage strip;
- 1320 px Agent Chrome area;
- 600 px observer dashboard;
- 84 px outcome footer.

The dashboard has five fixed regions:

1. Current SOP: stage, check name, locked source.
2. Model Plan: persisted items, current item, next focus.
3. Model Action / Output: large tool name, Chinese label, safe arguments, and
   terminal public reply.
4. Environment Result + Real-Time Decision Summary: external status, failure
   code, evidence, known fact, control judgment, and next action.
5. Incidents + Critical History: expanded open incident, collapsed resolved
   rows, and bounded critical timeline.

Each region has stable dimensions and internal overflow. Dynamic text cannot
resize the grid or overlap adjacent regions. The active plan item and open
incident remain visible without interaction.

The observer uses text nodes only and has no mutating controls or tabs that can
hide current evidence.

## Snapshot And SSE Recovery

The version-2 snapshot is the complete render source:

~~~json
{
  "schema_version": 2,
  "run_id": "coworker-...",
  "stage": "change_implement",
  "plan": {},
  "current_task": {},
  "current_action": {},
  "last_result": {},
  "public_model_output": {},
  "decision_summary": {},
  "incidents": [],
  "critical_history": [],
  "last_sequence": 52,
  "presentation_generation": 0
}
~~~

On reconnect:

1. reject an older generation;
2. reject a snapshot older than the rendered sequence;
3. replace all dynamic state from one accepted snapshot;
4. resume SSE after the snapshot sequence; and
5. deduplicate incidents and history by stable IDs.

No client-only incident or planner state is authoritative.

## Verification

### Unit And Contract Tests

- project safe planner snapshots from successful task results;
- reject unsafe, oversized, nested, or secret-bearing planner text;
- exclude raw evidence, prompts, constraints, open questions, and thinking;
- map every allowlisted failure code and redact unknown exceptions;
- derive each decision summary from explicit run states and safe results;
- cover every incident open/resolve mapping;
- audit version-2 producer and consumer field coverage;
- preserve action/result correlation and cross-run rejection; and
- reject version-1 payloads explicitly.

### Observer Tests

- all five regions exist and remain read-only;
- every tool replaces the current action visibly;
- plan updates render from snapshot state;
- environment result is separate from model action;
- decision summary uses closed translated codes;
- open incidents are expanded and red;
- resolved incidents collapse to one line;
- critical history excludes noncritical successful tools;
- public replies are visible and marked terminal or premature from runtime state;
- no unsafe HTML APIs are used; and
- fixed 1920x1080 geometry prevents overlap.

### Focused External Presentation Gate

A controlled non-final test run triggers each known gate failure. Per instance:

1. assert the environment rejects the action;
2. assert the stream contains the stable failure code;
3. assert an observer screenshot shows the Chinese explanation;
4. execute the matching recovery and assert resolved state; and
5. assert the resolved row remains in the final snapshot.

This validates presentation behavior but does not substitute for real LLM.

### Real-LLM Acceptance Runs

Run normal and post_change_anomaly through the normal HomeMaster shell with
Mimo / mimo-v2.5. Each run is valid only when:

1. transport events name mimo-v2.5, never scripted-coworker;
2. the model receives only the locked Ticket, normal prompt, tools, and skills;
3. recording starts before the first provider call and remains continuous;
4. every tool has a presentation start and terminal result;
5. all observable state correlates to the same run;
6. failed calls remain visible and persisted if produced;
7. normal ends with config present and business verification success;
8. anomaly ends with remove success plus grep exit 1 and empty stdout;
9. video is H.264, 1920x1080, yuv420p, nonblank, readable, and overlap-free at
   start, middle, terminal, and incident frames; and
10. the independent verifier passes hashes, presentation completeness, video
    properties, current-run IDs, and external end state.

A real-model business failure is retained as an evaluation sample. It does not
count as a successful final demo and is never relabeled as scripted acceptance.

## Reproducibility Evaluation

The first normal and anomaly runs produce the two required videos but do not
establish reliability alone. A stability claim requires multiple per-scenario
runs, reported individually:

- premature precheck completion;
- missing progress gate;
- grep before exact wait;
- remove before rollback authorization;
- invalid rollback stage;
- extra calls between causal alarm and rollback;
- total and rejected calls; and
- final external outcome.

Do not aggregate by best run or hide failed instances.

## Documentation

Update together:

- README.md: real-LLM capability and scripted-gate distinction;
- docs/coworker-demo-user-guide.md: launch, panel semantics, and evidence;
- docs/architecture/coworker-demo.md: version-2 data and privacy flow;
- CHANGELOG.md: change, reason, and user-visible impact; and
- docs/pitfalls.md: scripted/real ambiguity and generic failure blind spot.

## Rollout

1. implement and verify protocol version 2;
2. implement safe planner and final-reply projection;
3. implement decision summaries and incidents;
4. implement the five-region observer;
5. pass unit, contract, reconnect, privacy, and layout tests;
6. pass controlled failure/recovery presentation gates;
7. run and verify fresh real-LLM normal recording;
8. run and verify fresh real-LLM anomaly recording;
9. inspect incident and terminal frames per run; and
10. publish run IDs, video paths, model identity, scores, failures, hashes, and
    external end-state evidence.
