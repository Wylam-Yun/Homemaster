# Real-Time LLM Observable Coworker Demo Design

## Status

Approved for implementation planning on 2026-07-19. The independent plan
review completed the same day; all ten findings are incorporated into this
design and the implementation plan before code work begins.

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
- server-produced tool_label_zh and tool_kind;
- public_model_output;
- decision_summary;
- incident_delta;
- incidents; and
- critical_history.

Every producer, model, store, verifier, fixture, API snapshot, and observer
consumer is updated together. A protocol audit test enumerates all public
presentation fields and verifies producer and consumer coverage.

Each action/result event carries the server-produced tool_label_zh and a closed
tool_kind value: orchestration, observation, mutation, wait, verification, or
gate. The observer renders these values and never infers a tool's business
meaning from its name. The protocol audit also requires the model-output-kind
consumer to render whether the current model output is a tool selection,
intermediate public reply, terminal reply, or premature reply.

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
- statuses use the exact existing task-status set: pending, in_progress,
  completed, blocked, cancelled, and uncertain;
- evidence text, constraints, open questions, completion summaries, prompts,
  and arbitrary nested values are excluded; and
- the active item is rendered in a pinned row above the bounded historical
  item list so a 12-item plan cannot scroll the current work out of view; and
- unsafe text is rejected, never replaced by a display redaction marker, and
  records a presentation failure.

The plan panel updates only after the task store returns a successful persisted
snapshot.

### Public Reply

An assistant.reply runtime event is user-visible model output and may be
projected as model.public_reply. This includes a premature public reply because
hiding it would hide observable model behavior. The projected reply is:

- plain text only;
- capped at 1,200 Unicode characters;
- stripped of control characters;
- passed through dedicated free-text secret checks against configured provider
  secrets held only in memory, known credential/JWT/PEM/signed-URL/sk-token
  patterns, and high-entropy token detection;
- omitted with a presentation failure if it cannot be safely projected; and
- initially marked intermediate because the runtime emits assistant.reply
  before following tool calls or runtime.turn_completed; the reducer later
  reclassifies the latest reply as terminal only when the authoritative
  environment state is terminal, or premature when runtime completes without
  a business terminal state.

Application-generated stop-condition text is never synthesized as model
output. Secret values and rejected source strings are never written to the
presentation ledger or diagnostic log.

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
| plan_required | 尚未创建执行计划 | successful task_planner |
| missing_precheck_evidence | 变更前检查证据不完整 | successful precheck proceed |
| progress_required | 当前阶段进度尚未记录 | successful progress update |
| wait_required | 尚未等待准确任务完成 | successful matching wait |
| postchecks_required | 变更后检查尚未完成 | complete required postchecks |
| rollback_verification_required | 回滚验证尚未完成 | successful absence verification |
| rollback_decision_required | 尚未授权回滚 | successful rollback authorization |
| missing_anomaly_evidence | 缺少因果异常证据 | record current-run causal alarm |
| missing_implementation_evidence | 缺少实施完成证据 | record current-run implementation |
| missing_postcheck_evidence | 缺少变更后检查证据 | record required postchecks |
| missing_rollback_evidence | 缺少回滚完成证据 | record current-run rollback evidence |
| external_state_mismatch | 外部状态与预期不一致 | refresh and verify external state |
| parameter_mismatch | 操作参数与锁定目标不一致 | retry exact locked target |
| command_not_allowed | 命令不在允许范围 | use an allowlisted command |
| invalid_decision_for_stage | 当前阶段不接受该决定 | successful valid decision |
| stale_state_version | 页面状态已经变化 | fresh read and retry |
| action_replay | 重复动作已被拒绝 | fresh distinct action |
| terminal_outcome | 运行已经结束 | no recovery |
| unclassified_failure | 未分类执行失败 | correlated retry if possible |

These are the stable EpisodeError.code values plus safe wrapper mappings used
by the actual environment. Message-only errors such as "remove requires a
rollback decision" are normalized to a safe code at the projector boundary.
Where a wrapper does not expose an exact stable code, it maps to a closed safe
family; unrestricted exception text never enters the protocol. Tests enumerate
every safety-relevant code and fail when a new EpisodeError code lacks an
explicit safe mapping.

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

The active plan item and next focus are pinned above the scrollable/compressed
completed-item list. A long-plan 12-item screenshot test must prove both remain
visible without scrolling.

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
- public replies are visible as intermediate, then reclassified terminal or
  premature from authoritative runtime and business state;
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

1. transport events name mimo-v2.5, never scripted-coworker, and the run-owned
   safe provider identity artifact names provider Mimo, HTTPS scheme,
   token-plan-cn.xiaomimimo.com host, and a key-free configuration fingerprint;
   generated provider overrides and loopback endpoints are rejected;
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

The safe provider artifact proves which client configuration was used, not the
server's internal model implementation. Server-side model identity remains
UNVERIFIED until fresh provider responses return successfully and name the
expected model. No key, credential, header, query secret, or full endpoint URL
is persisted.

The independent verifier reconstructs the business evidence from raw run-owned
artifacts. For normal it requires the exact four-field configuration record,
the matching add job succeeded with external return code 0, and the locked grep
returned exit 0 with exact stdout. For anomaly it additionally requires the
current run's causal alarm to reference that add job, rollback authorization to
precede remove submission, the exact remove job to succeed with return code 0,
and the absence grep to return exit 1 with empty stdout. Mutation tests alter
each fact independently and must fail per scenario instance.

### Recording Time Base And Failed Attempts

The recorder persists UTC wall-clock and monotonic timestamps for recording
start and first-packet-ready, plus FFmpeg out_time observations. Presentation
events persist UTC time and same-host monotonic offset. Named-frame MP4 offsets
are calculated from the shared monotonic origin plus a bounded UI settle margin,
then clamped only when the source event is inside the recording interval. Each
manifest entry stores the source event ID, source timestamps, calculated offset,
settle margin, and video duration. DOM screenshots assert exact text; decoded
video frames independently assert nonblank pixels and a visible observer region.

run_coworker_turn creates attempt_manifest.json immediately after allocating a
run root and atomically updates it through provider, business, presentation,
recording, and verification outcomes. The shell prints the run root from a
typed failure carrying that path, while the acceptance harness also discovers
new attempt manifests by before/after directory diff. Thus a provider or video
exception cannot make a real attempt disappear from the report.

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
