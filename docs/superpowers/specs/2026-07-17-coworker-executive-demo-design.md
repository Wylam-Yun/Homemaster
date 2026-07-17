# Change Coworker Executive Demo Design

## Goal

Make the existing Change Coworker recording understandable to a leadership
audience without changing the Agent's business decisions or replacing the real
browser run with a scripted animation.

The recording must continuously answer four questions:

1. Which SOP stage is active?
2. What exact SOP task is the Agent executing?
3. Which tool did the Agent call, with which important arguments?
4. What externally visible result allowed the workflow to continue, stop, or
   roll back?

The deliverable remains one continuous, unedited 1920x1080 recording of the
real run. Clarity comes from a live executive observer, not post-production.

## Audience And Success Criteria

The primary audience is leadership. The recording should communicate progress,
control, evidence, and outcome without requiring the viewer to read source code,
raw JSON, or a scrolling terminal transcript.

A successful recording has these properties:

- The active SOP stage is always visible.
- During a business action, the current task is the exact source text from the
  locked change ticket, not a model-authored paraphrase.
- Every model-selected browser or terminal tool appears with a concise argument
  summary and a result or explicit failure state.
- Submitted and completed automation jobs are visually distinct and show the
  exact job ID.
- A causal anomaly visibly changes the workflow into rollback, and the reason is
  understandable from the observer.
- The final screen states complete, rolled back, blocked, escalated, or
  insufficient evidence and shows the key supporting evidence.
- The observer never exposes provider secrets, raw prompts, hidden evaluator
  state, or model chain-of-thought.
- The existing run bundle remains independently verifiable by run ID.

## Scope

In scope:

- Replace the current observer/transcript recording layout with a leadership
  recording layout.
- Add a deterministic mapping from runtime actions to SOP stages and locked SOP
  source text.
- Add a sanitized presentation-event projection for live tool and result cards.
- Make the observer recover correctly after an SSE reconnect.
- Record normal-completion and anomaly-rollback scenarios through the same UI.
- Add presentation completeness checks to the delivered run bundle.

Out of scope:

- Changing the Agent prompt to force a visually pleasing action sequence.
- Giving observer-only information to the Agent.
- Adding screenshot or VLM input to the Agent.
- Replacing DOM tools with coordinate-based browser control.
- Post-production editing, acceleration, subtitles, or reordered actions.
- Showing raw model reasoning.
- Replacing the simulated case environment with a real monitoring system.

## Design Principles

- **Truth before polish.** Every displayed fact comes from a persisted runtime,
  browser, terminal, or environment event.
- **Exact SOP provenance.** Current-task text comes from the locked ticket bundle
  and retains its source field and source hash.
- **Observer isolation.** The executive observer is read-only and unavailable to
  the Agent browser allowlist and tool registry.
- **Action/result correlation.** Tool request, backend receipt, and visible
  result are joined by current-run `action_id`; automation completion also uses
  the exact `job_id`.
- **No premature success.** A started action remains running until a trusted
  completion or failure event arrives.
- **Continuous raw evidence.** The primary video is the unedited FFmpeg capture
  from display start through frozen final scores.

## Recording Layout

The TigerVNC display remains 1920x1080. The observer runs as a full-screen
background, and the headed Agent Chrome window occupies a fixed left content
area above it.

```text
+--------------------------------------------------------------------------+
| SOP stage strip: prechecks > execute > verify > postchecks > business    |
+--------------------------------------------------+-----------------------+
|                                                  | Current SOP task      |
|                                                  | Exact ticket text     |
|              Real Agent Chrome                   +-----------------------+
|              ticket / monitor / automation       | Current action        |
|                                                  | tool + safe arguments |
|                                                  +-----------------------+
|                                                  | Latest result         |
|                                                  | status + evidence     |
|                                                  +-----------------------+
|                                                  | Completed / next      |
+--------------------------------------------------+-----------------------+
| Run outcome / verification state                                         |
+--------------------------------------------------------------------------+
```

Target proportions:

- Stage strip: approximately 96 pixels high.
- Agent Chrome: approximately 1320 pixels wide, using the remaining central
  height while leaving the observer's stage and outcome bands visible.
- Executive dashboard: approximately 600 pixels wide.
- All persistent dashboard text uses a leadership-readable font size; raw JSON
  is never the default rendering.

The current xterm transcript window is removed from the recording layout.
Terminal execution remains real tmux/Bash execution, but its safe command,
exit code, and concise output are projected into the latest-result card.

## SOP Stage And Task Mapping

### Source Of Truth

The locked ticket remains the only source for business task text. The mapper
uses the same ticket bundle locked by `CaseRepository` and exposes:

```json
{
  "stage": "check_before_change",
  "check_name": "...",
  "source_field": "operate_description",
  "source_text": "exact ticket text",
  "source_sha256": "..."
}
```

The displayed source text is not translated, summarized, or rewritten.

### Deterministic Mapping Inputs

The mapping key is:

```text
environment phase + tool name + safe tool arguments + grounded DAG node
```

Examples:

- `browser_click(bid=monitor-query-alarm)` in prechecking maps to the
  pre-change alarm SOP item.
- The same `bid` after implementation maps to the post-change alarm SOP item.
- `browser_click(bid=automation-submit)` with operation `add` maps to the
  implementation SOP item.
- `browser_wait(job_id=<add job>)` retains the implementation SOP text and
  changes the action status to waiting for the exact job.
- The locked `terminal_execute` retains the implementation or rollback SOP text
  and displays independent readback.
- A causal alarm followed by rollback maps to `operate_rollback` where present.

Planner, progress, skill, and SOP-decision tools do not invent a new current
task. They retain the current business SOP text and display an orchestration
label such as `Planning`, `Updating progress`, or `Recording gate decision`.

If no unique trusted mapping exists, the observer displays
`No trusted SOP mapping` and records a presentation failure. It must not guess.

## Presentation Event Projection

### Event Schema

Add an append-only, run-owned presentation stream with these event types:

```text
presentation.snapshot
presentation.task_changed
presentation.tool_started
presentation.tool_completed
presentation.tool_failed
presentation.stage_changed
presentation.run_completed
```

Each event contains:

```json
{
  "schema_version": 1,
  "sequence": 42,
  "run_id": "coworker-...",
  "timestamp": "...",
  "event_type": "presentation.tool_completed",
  "action_id": "action-...",
  "stage": "check_before_change",
  "task": {
    "source_field": "operate_description",
    "source_text": "...",
    "source_sha256": "..."
  },
  "tool": {
    "name": "browser_click",
    "arguments": {"bid": "monitor-query-alarm"}
  },
  "result": {
    "status": "clear",
    "job_id": null,
    "exit_code": null,
    "evidence_refs": ["ev-..."]
  }
}
```

### Allowed Presentation Fields

Arguments and results are projected by tool-specific allowlists. Allowed fields
include:

```text
route, bid, check, query, region, cluster, script, operation,
job_id, target_status, milestone, decision, status, exit_code,
evidence_refs, safe result summary
```

Terminal output is truncated for display, while the complete output remains in
the existing terminal artifact. Provider configuration, prompts, raw assistant
responses, arbitrary headers, hidden scenario values, evaluator details, and
secrets are forbidden.

### Correlation And Ordering

- Tool start and completion join on exact current-run `action_id`.
- Automation submission and completion additionally join on exact `job_id`.
- Sequence numbers are monotonic within a run.
- The projector rejects cross-run action IDs and evidence references.
- The observer treats out-of-order completion as a presentation error rather
  than silently attaching it to the latest tool.

## Executive Observer Behavior

The observer renders five persistent areas:

1. **Stage strip.** Completed stages are checked, the active stage is
   highlighted, rollback is red, and terminal states are explicit.
2. **Current SOP task.** Shows the exact source text and a short source label.
3. **Current action.** Shows tool name, a human-readable action label, and safe
   key arguments.
4. **Latest result.** Distinguishes running, accepted, succeeded, failed,
   rejected, active anomaly, and independently verified.
5. **Progress and outcome.** Shows completed business checks, next required
   check, final node/checkpoint counts, scores, and video-verification state.

The observer uses SSE for updates. On initial load or reconnect, it requests a
presentation snapshot containing the current stage, task, in-flight action,
last completed action, and last sequence number. It resumes after that sequence
without duplicating cards.

The observer is intentionally read-only. It has no controls that mutate the
run, and its route is not added to the Agent's allowed routes.

## Continuous Recording Contract

Only the raw continuous recording is delivered as the primary video. The
recording contract remains:

- 1920x1080 H.264, yuv420p.
- FFmpeg starts before the model run and proves first-packet readiness.
- No cuts, speed changes, overlays added after recording, or reordered actions.
- Waiting periods remain visible; the dashboard states what is being awaited.
- The final outcome and frozen numeric scores remain visible for the configured
  final hold.
- Independent ffprobe and frame verification remain mandatory.

The existing `video/demo.mp4` path may remain stable. No executive derivative is
required. The presentation stream and presentation verification report are
added to the run manifest.

## Failure Handling

- **Missing SOP mapping:** show a visible mapping failure, persist it, and fail
  presentation verification.
- **Tool never completes:** keep the action in running state with elapsed time;
  do not display success.
- **Unmatched action/result:** display a correlation failure and fail
  presentation verification.
- **SSE disconnect:** show reconnecting state, recover from snapshot and
  sequence, and avoid duplicate events.
- **Observer process exit:** record the process return code and fail the video
  presentation gate.
- **Oversized result:** show a safe truncated summary and link it internally to
  the complete run artifact by evidence ID.
- **Terminal or backend rejection:** show the rejected tool and stable reason;
  do not remove it from the live presentation.
- **Run terminal state:** prevent later model-selected external actions while
  allowing fixed finalization, score hold, recorder stop, and verification.

Business success and presentation success remain separate. A run can reach a
correct business outcome but fail the deliverable if the recording does not
show trustworthy stage, task, and tool/result state.

## Verification And Tests

### Unit Tests

- Every required normal and anomaly DAG node has an unambiguous stage mapping.
- Every business node maps to the exact locked ticket source text and hash.
- Pre-change and post-change uses of the same monitor `bid` map to different SOP
  entries.
- Planner/progress/decision events retain the current business task.
- Unknown mappings fail closed.
- Tool-specific projection removes forbidden and secret fields.
- Action/result and job correlation rejects cross-run or mismatched IDs.

### Observer Tests

- Snapshot rendering shows stage, exact SOP text, current action, and result.
- SSE reconnect resumes from the correct sequence without duplicates.
- Accepted and succeeded automation states render differently.
- Causal anomaly and rollback render with distinct severity and task text.
- Long command output is safely summarized without losing exit code or evidence.
- The page has no mutating controls and exposes no hidden evaluator state.

### End-To-End Tests

Run both scenarios through the real provider path:

- Normal completion: all required nodes and checkpoints, business verification,
  complete outcome, and a readable final summary.
- Post-change anomaly: causal alarm, rollback decision, exact remove wait,
  absence grep, rolled-back outcome, and a readable final summary.

For each run, verify:

- Every model-selected browser and terminal call has a presentation start and a
  terminal presentation result, including rejected calls.
- Presentation action IDs and evidence IDs exist in the current run audit.
- Current-task hashes match the locked ticket bundle.
- The observer process remains live through final score hold.
- The video is 1920x1080, nonblank, continuous, and independently verified.
- `run_manifest.json` hashes the presentation stream and verification report.
- Secret scanning finds no credentials, raw prompts, or forbidden hidden state.

## Rollout

1. Add the SOP mapper and presentation schema with unit tests.
2. Add sanitized runtime and environment projections.
3. Build the executive observer and reconnectable snapshot/SSE flow.
4. Change only the display layout and remove the xterm recording window.
5. Add presentation verification and manifest registration.
6. Run the normal and anomaly acceptance recordings from fresh run IDs.
7. Retain the existing observer behind a development-only route only if it is
   still needed for debugging; it must not appear in the delivered video.

No Agent prompt or evaluator trajectory changes are required for this feature.
