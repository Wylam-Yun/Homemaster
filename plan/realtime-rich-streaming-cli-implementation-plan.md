# Realtime Rich Streaming CLI Implementation Plan

Status: `PLAN_REVIEWED_LOCKED_FOR_IMPLEMENTATION`

Date: 2026-07-22

## 1. Objective

Restore true user-visible streaming and port the OpenHarness Python Rich terminal renderer into HomeMaster without
adding another public event family.

The delivered behavior must be:

- model text reaches a text or `stream-json` consumer before the provider stream completes;
- interactive and legacy progress output distinguish assistant text, tool start, tool completion, errors, status, and
  compaction with Rich formatting;
- the public live event contract remains exactly the seven existing OpenHarness-derived `StreamEvent` variants;
- complete assistant text is persisted once and is not printed again after its deltas;
- JSON automation output remains free of Rich/ANSI output;
- Gateway terminal-final ownership and private/public redaction boundaries do not regress.

This plan changes a public output protocol and crosses provider, agent runtime, events, CLI, and Gateway trust
boundaries. It is a major change under the repository rules. The plan receives one read-only reviewer before
implementation; after all implementation, tests, external terminal validation, documentation, and postmortem work,
the final code receives one read-only reviewer.

### Root Cause Already Established

Do not restart with speculative provider fixes. Provider transports already yield deltas. The loss occurs in
`src/homemaster/agent/generic_runtime.py::_consume_stream()`: the unified runtime collects the complete provider
stream, aggregates it, and only then emits `assistant.reply`. Historical V1.4 `MimoTransport` emitted
`transport.delta`; commit `dd11ffc` replaced that path with the unified `LLMClient` and did not restore
delta-to-event forwarding. Later async changes retained the collect-then-publish behavior. Current `stream-json` is
also rendered after `RunResult` exists, so it is batch JSON-lines rather than a live stream.

The seven needed public event classes are therefore already present. The change is to restore publication, correct
the existing projection, and attach renderers/sinks; it is not to invent another event family.

## 2. Locked Upstream And Existing Contract

Locked OpenHarness source:

- repository: `../OpenHarness`
- commit: `9b2efd795c6aa09f88b0c257d269a9e518da6ae7`
- `src/openharness/engine/stream_events.py`
  SHA-256: `813917fc2f5e25bdb5d99a1c0d52d34221584c32cabbeebf426e5f580fe3852d`
- `src/openharness/ui/output.py`
  SHA-256: `60d0d106d235f242fce44d75b752b7b1acb98f04af6e04e7eb0c0326d7ebcec4`
- `src/openharness/ui/app.py`
  SHA-256: `87e8ca3af04fad6184f35e85c7c590b82e4084114d4ffc0409a17aa90c9f4789`

HomeMaster already contains adapted definitions for the exact seven OpenHarness UI events in
`src/homemaster/events/stream_events.py`:

1. `AssistantTextDelta`
2. `AssistantTurnComplete`
3. `ToolExecutionStarted`
4. `ToolExecutionCompleted`
5. `ErrorEvent`
6. `StatusEvent`
7. `CompactProgressEvent`

No eighth public `StreamEvent` variant is permitted in this change. In particular, do not add public
`AssistantThinkingDelta`, `RunStarted`, `ModelRequestStarted`, or a separate `ToolExecutionFailed`. OpenHarness uses
`ToolExecutionCompleted.is_error` for failure. Model-waiting is renderer state, not a claim that the model exposed
reasoning.

HomeMaster `RuntimeEvent` remains the private trace/audit/lifecycle envelope. It is not a second UI contract.
`StreamEvent` is the existing safe UI projection. Do not rename HomeMaster domain ledgers or make them depend on UI
events.

## 3. Alternatives And Decision

### Option 1: Patch `ConsoleEventSink` Only

Emit `transport.delta` and print it immediately in the existing sink.

Trade-off: smallest diff, but `stream-json` stays batch-oriented, Markdown and tool rendering stay weak, and the
OpenHarness event contract remains incorrectly wired.

### Option 2: Restore The Seven Stream Events And Port Python Rich Rendering

Emit private deltas at the generic runtime boundary, project them into the existing seven OpenHarness event types,
and port/adapt `OutputRenderer` for interactive/progress CLI. Add a live JSON-lines sink for `stream-json`.

Trade-off: touches several boundaries, but fixes the actual cross-layer contract without adding React or a second
agent loop.

### Option 3: Replace The Private Runtime Bus With A StreamEvent-Only Bus

Move trace/audit delivery off `EventBus` and make the application bus carry only OpenHarness events.

Trade-off: conceptually pure, but unnecessarily restructures Gateway generation fencing, bounded backpressure,
trace sinks, and application lifecycle in the same change.

### Option 4: Port The Full React Ink TUI

Port `frontend/terminal` and its backend protocol.

Trade-off: closest visual parity, but adds a Node/React runtime, frontend lifecycle, IPC, themes, modal state, and a
much larger test surface.

Decision: implement Option 2. Preserve the seven-event interface so Option 4 can be added later without changing the
agent/provider contract.

## 4. Required Invariants

1. The generic runtime, not individual provider implementations, owns delta-to-live-event publication. Every real,
   fake, Anthropic, and OpenAI transport must therefore behave consistently.
2. `_consume_stream()` continues collecting every `TransportDelta` for final `AssistantMessage` aggregation while
   immediately feeding accepted text deltas to the run-scoped public streaming sanitizer. The sanitizer may retain
   only an unstable suffix; every stable safe prefix is published without waiting for provider completion.
3. Only `text_delta` becomes `AssistantTextDelta`. `reasoning_delta`, partial tool JSON, provider metadata, API keys,
   raw paths, and resource URIs do not enter the seven-event public stream.
4. `AssistantTurnComplete` occurs exactly once per successfully aggregated assistant message and before any tool from
   that message starts. It contains a safe complete assistant message, including tool calls for tool-only turns but
   excluding reasoning/provider metadata. Its renderer branch replaces the current live region with final Markdown;
   it does not append the complete text as a second visible answer.
5. `ToolExecutionStarted` and `ToolExecutionCompleted` preserve deterministic start-order/FIFO pairing without
   changing the locked OpenHarness DTO fields. Failure uses `is_error=True`; do not add a public failure class.
6. A provider failure after one or more visible deltas yields exactly one `ErrorEvent`, never a false
   `AssistantTurnComplete`. Existing retry fencing must still prohibit retry after partial committed deltas.
7. `RuntimeEvent`, session persistence, `RunResult.final_reply`, provider-attempt records, authoritative tool/device
   ledgers, and Gateway terminal final remain the owners they are today.
8. Local Rich output and machine-readable output are separate sinks. `json` and `stream-json` stdout contain no Rich
   control sequences or human status lines.
9. Gateway does not forward raw reasoning and does not gain duplicate terminal answers. Whether a channel coalesces
   text deltas is a channel rendering policy, not a new event type.
10. Renderer close/cancel/error paths always stop active spinners and terminate any open assistant live region.
11. Streaming redaction is stateful per run/assistant turn. No chunk is released while it could still be a prefix of
    a configured secret, credential assignment, host path, or URL query. Completion, failure, and cancellation pass
    the carry through the full sanitizer, emit only the resulting safe tail before the terminal event, and erase the
    state. State is never shared with another run or assistant turn.
12. The Rich renderer has one explicit state owner and one visible assistant region. Raw delta text and final
    Markdown are never appended as two separate answers.

## 5. Event Mapping

Update `src/homemaster/events/stream_events.py::project_stream_event()` rather than adding public classes.

| Private HomeMaster event | Public StreamEvent | Rule |
| --- | --- | --- |
| `transport.delta` with non-empty `text_delta` | `AssistantTextDelta` | Emit immediately; sanitized text only |
| `transport.delta` without text | none | Reasoning/tool/provider fields stay private |
| successful aggregated `assistant.reply` | `AssistantTurnComplete` | Include safe `AssistantMessage` text and usage; do not display text twice |
| `runtime.turn_completed` | none | Run terminal is not another assistant turn complete |
| `tool.call_started` | `ToolExecutionStarted` | Sanitized tool name/input |
| `tool.call_completed` | `ToolExecutionCompleted` | `is_error=False` |
| `tool.call_failed` | `ToolExecutionCompleted` | `is_error=True` |
| `transport.request_failed` | none | Internal attempt diagnostic; never a second user error |
| `transport.request_retrying` | `StatusEvent` | Intermediate retry notice only |
| final `runtime.turn_failed` / budget failure | `ErrorEvent` | Sole run-level error owner; safe code/message |
| `runtime.cancelled` | `StatusEvent` | Cancellation notice, not a provider error |
| `runtime.reactive_compact_started` | `CompactProgressEvent` | Fixed legal phase `compact_start`, trigger `reactive` |
| existing completed `context.compaction` | `CompactProgressEvent` | Fixed legal phase `compact_end`; preserve trigger |
| unknown/private event | none | Fail closed |

Do not retain the current incorrect mapping from complete `assistant.reply` to `AssistantTextDelta`.

The private `assistant.reply` payload must be sufficient to build one `AssistantTurnComplete`: safe complete text,
finish reason, usage, and safe complete tool calls. Emit it for every successfully aggregated assistant message,
including a tool-only message with empty text, so per-model-turn completion remains deterministic.
`MessagesLogSink` continues to skip empty text. The public event fields remain identical to locked OpenHarness; do not
add `tool_call_id`. Public tool starts and completions must therefore be emitted in deterministic start order, and the
renderer uses a per-tool-name FIFO rather than one global `_last_tool_input`. Add a two-same-name parallel-tool test.

Failure/cancel ordering is locked as follows:

- retryable failure before any delta: internal request failure -> one `StatusEvent` retry notice -> next request;
- final failure before any delta: internal request failure -> one run-level `ErrorEvent` -> failed result;
- final failure after partial delta: visible deltas -> one run-level `ErrorEvent` -> failed result, with no assistant
  completion;
- cancellation: optional visible deltas -> one cancellation `StatusEvent` -> cancelled result, with no error event or
  assistant completion;
- tool failure: one `ToolExecutionCompleted(is_error=True)` and no additional `ErrorEvent` unless the entire run later
  fails for a separate reason.

The existing private compaction events only prove reactive start and completed compaction. This change maps those two
points honestly; it does not synthesize unsupported OpenHarness phases.

## 6. Implementation Steps

### Step 0: Preserve The Current Dirty Worktree

- Read `CLAUDE.md`, `docs/pitfalls.md`, and `progress.md` before implementation.
- Record the pre-change `git status --short` in the session handoff, not a long-lived baseline artifact.
- Do not revert or reformat the existing V1.9 remediation changes.
- Work only in the files named below unless a failing test proves another owner must change.

### Step 1: Add RED Contract And Timing Tests

Modify/add focused tests before production code:

- `tests/homemaster/test_generic_agent_runtime.py`
  - delayed async transport yields a first text delta and then blocks;
  - assert the live sink receives that delta while the run task is still incomplete;
  - release the stream and assert the aggregated final text is unchanged;
  - assert exactly one assistant completion precedes tool start;
  - assert partial-delta failure produces an error and no completion.
- `tests/homemaster/events/test_public_projection.py`
  - assert the exact seven-class union remains unchanged;
  - map `transport.delta.text_delta` to `AssistantTextDelta`;
  - reject reasoning/tool/provider/private delta fields;
  - map complete assistant message to `AssistantTurnComplete`;
  - prove `runtime.turn_completed` does not create a duplicate completion;
  - preserve tool error and compaction mappings.
- `tests/homemaster/events/test_streaming_sanitizer.py`
  - split every configured-sensitive-literal case at every character boundary;
  - split credential assignments, bearer/basic values, host paths, and URL query/fragment examples at multiple
    boundaries, including immediately before and after the distinguishing delimiter;
  - assert concatenated released text never contains the sensitive literal and equals one-shot sanitization after
    completion;
  - assert failure/cancel finalization cannot leak the retained suffix and that state from one run/turn cannot affect
    another.
- `tests/homemaster/test_event_sinks.py`
  - characterize the renderer state transitions in Step 4, one-region Rich streaming, Markdown replacement, tool
    success/error, generic unknown-tool fallback, spinner cleanup, and no duplicate final text;
  - cover pure text, tool-only, two same-name tools, a second model request after tools, failure before the first token,
    failure after partial text, and cancellation from every active state.
- `tests/homemaster/test_cli_run.py`
  - replace the current single-document `stream-json` assertion with ordered JSON-lines assertions;
  - assert every exact event/result schema, one terminal result, safe result serialization, fatal-no-result behavior,
    and exit-code compatibility.

The first two timing tests must fail against the current implementation for the expected reason: no delta is visible
before stream completion.

### Step 2: Restore Delta Publication In The Generic Runtime

Modify `src/homemaster/agent/generic_runtime.py`:

- add an async per-delta callback to `_consume_stream()`;
- after cancellation fencing and before requesting the next delta, append the accepted delta and invoke the callback;
- feed text through the run/assistant-turn-scoped `StreamingPublicTextSanitizer`; publish each released safe prefix as
  private `transport.delta` through the existing run-fenced event sink, with only normalized fields required by
  internal trace/projection;
- at provider EOF, failure, or cancellation, finalize the sanitizer before the completion/error/cancel event, emit any
  safe remaining text in order, and erase its state; finalization never creates `AssistantTurnComplete` itself;
- preserve iteration/run/session correlation and existing deadline/cancel behavior;
- do not make `LLMClient`, Anthropic transport, or OpenAI transport print or publish UI events independently;
- after aggregation, emit one complete private assistant event with text, finish reason, and usage even for tool-only
  turns;
- keep persistence and final `RunResult` ownership unchanged.

Run the generic runtime and async provider focused tests after this step.

### Step 3: Correct The Seven-Event Projection

Modify:

- `src/homemaster/events/stream_events.py`
- `tests/homemaster/events/test_public_projection.py`

Implement the mapping in Section 5. Sanitize before constructing a `StreamEvent`. Keep all seven current dataclass
shapes source-compatible with the locked OpenHarness definitions and existing HomeMaster type substitutions.

Add `StreamingPublicTextSanitizer` next to the public projection/sanitization boundary. It is an incremental scanner,
not `sanitize(chunk)` applied independently:

- it is constructed per `(run_id, assistant turn)` with the same configured sensitive values and free-text rules as
  `PublicEventProjection`;
- it retains the longest unstable suffix: a suffix that is a prefix of an exact sensitive value, an incomplete
  bearer/basic credential, a credential-key assignment, a recognized host-path token, or a URL query/fragment token;
- complete lexical units and prefixes proven unrelated to those constructs pass through the existing one-shot
  sanitizer and are released immediately; no fixed-size carry may be used if it can split a longer configured secret;
- `finish()` marks input EOF, runs the entire carry through the same one-shot sanitizer, returns only safe text, and
  clears internal state; normal completion, failure, and cancellation all use this operation before their next
  public event;
- the implementing agent must document the scanner's delimiters and retained-memory ceiling in code. If an
  unterminated suspicious token reaches that ceiling, replace the retained construct with a redaction marker and
  continue; never release a raw prefix merely to cap memory. Ordinary prose must still satisfy the pre-completion
  first-byte gate.

CLI composition must provide the sanitizer with all locally configured provider and MCP secret values through one
non-logging helper. Do not store those values in RuntimeEvent payloads, trace files, renderer state dumps, or result
metadata. Complete assistant text, tool input/output, status/error text, and CLI result serialization must reuse the
same one-shot public sanitization rules; `sanitize_event_payload()` alone is not sufficient for credential-like text
whose dictionary key is innocuous.

Do not implement this as stateless `str.replace()` per delta: `"api_" + "key=secret"`, a configured secret split
between chunks, and `"https://host/path?to" + "ken=value"` must remain non-leaking.

Audit every real consumer of `project_stream_event()` and `EventBus.public_stream()`. No consumer may still assume
that `AssistantTextDelta` contains the full final answer.

### Step 4: Port The Python Rich Renderer

Create `src/homemaster/cli/rich_renderer.py` by adapting locked OpenHarness
`src/openharness/ui/output.py::OutputRenderer`.

Retain:

- model-working and tool spinners;
- one Rich `Live` assistant region updated as safe text arrives;
- final Markdown replacement in that same region on `AssistantTurnComplete`;
- tool start summaries;
- success/error panels;
- generic result truncation;
- minimal/default styles.

HomeMaster adaptations:

- import the existing HomeMaster seven-event classes;
- inject `Console`/file/terminal capability for deterministic tests and stdout/stderr separation;
- route `ErrorEvent` and `StatusEvent` to safe system/status rendering;
- make spinner/line cleanup explicit and idempotent;
- keep a generic unknown-tool summary (tool name plus first safe argument);
- do not hard-code robot tool branches in the MVP;
- consume only sanitized/projected payloads;
- use ASCII fallbacks when the terminal cannot render Unicode;
- do not import OpenHarness at runtime.

The renderer controller is the sole state owner. Implement and test these states and transitions:

| State | Input/control | Required transition and visible effect |
| --- | --- | --- |
| `idle` | `transport.request_started` control | `waiting-model`; start one "model working" spinner (not model reasoning) |
| `waiting-model` | first `AssistantTextDelta` | atomically stop spinner, open one assistant `Live` region, enter `streaming-assistant` |
| `waiting-model` | tool-only `AssistantTurnComplete` | stop spinner, finalize the empty/text region without an echo, enter `running-tools` |
| `streaming-assistant` | later delta | update the same region; do not append a second answer block |
| `streaming-assistant` | `AssistantTurnComplete` | replace that region with final sanitized Markdown; enter `running-tools` if tool calls exist, otherwise `idle` |
| any non-closed state | `ToolExecutionStarted` | stop model spinner/live update as needed, enter `running-tools`, track the tool by per-name FIFO |
| `running-tools` | tool completion | finish the matching FIFO item; remain until all active tools finish |
| `running-tools`/`idle` | next `transport.request_started` | start a fresh model spinner in `waiting-model` |
| any non-closed state | terminal error/cancel/close/finally | stop all spinners and live regions idempotently, enter `closed` |

`transport.request_started` and close/finally are renderer lifecycle controls delivered by the private sink adapter;
they are not an eighth public `StreamEvent`. Reasoning text is never shown. Exact Rich `Live` cursor/control behavior is
`UNVERIFIED` until the implementation agent checks the installed Rich version in the real environment and passes the
PTY final-screen gate in Section 7.

Keep `ConsoleEventSink` temporarily for compatibility unless all real imports are migrated in the same change and an
audit proves there are no remaining consumers. Do not silently change JSONL or messages-log behavior.

### Step 5: Add Live Output Sinks And Wire CLI Entry Points

Add narrowly scoped CLI adapters, preferably in `src/homemaster/cli/live_output.py`:

- `RichStreamEventSink`: private RuntimeEvent -> safe `StreamEvent` projection -> Rich renderer;
- `TextStreamEventSink`: plain assistant deltas with immediate flush for non-interactive `-p text`;
- `StreamJsonEventSink`: OpenHarness-compatible JSON lines for the seven events with immediate flush.

Wire through `src/homemaster/cli/composition.py`, `run_command.py`, and `interactive_shell.py`:

- interactive mode uses the Rich renderer;
- legacy `run --progress`/`--verbose` uses the Rich renderer on stderr;
- `-p --output-format text` streams plain assistant text to stdout and does not print `final_reply` again;
- `-p --output-format stream-json` emits ordered event lines as they occur, then one HomeMaster `type=result` terminal
  envelope for compatibility;
- `-p --output-format json` remains a single buffered result document;
- non-TTY text output contains no spinner or ANSI control sequences;
- errors and diagnostic status never corrupt machine-readable stdout.

Freeze `stream-json` as UTF-8, one compact JSON object plus newline per flush, with these top-level schemas (fields
shown are exact):

| `type` | Required fields |
| --- | --- |
| `assistant_delta` | `type`, `text` |
| `assistant_complete` | `type`, `message`, `usage` |
| `tool_started` | `type`, `tool_name`, `tool_input` |
| `tool_completed` | `type`, `tool_name`, `output`, `is_error`, `metadata` |
| `error` | `type`, `message`, `recoverable` |
| `status` | `type`, `message` |
| `compact_progress` | `type`, `phase`, `trigger`, `message`, `attempt`, `checkpoint`, `metadata` |
| `result` | existing HomeMaster `run_result_envelope`: `type`, `run_id`, `session_id`, `status`, `final_reply`, `error_code`, `metadata` |

`assistant_complete.message` has exactly `role`, `content`, `tool_calls`, and `finish_reason`. `content` contains only
safe text blocks with exactly `type` and `text`; it excludes image source data and block metadata. Each tool call has
exactly `id`, `name`, and `arguments`. The message wire object excludes `reasoning_content`, provider metadata, and
embedded usage because usage is the sibling event field. Nullable event fields in the table remain present with
JSON `null`, so consumers do not need shape guessing.

The first seven are the OpenHarness event family serialized by HomeMaster; `type=result` is an explicitly documented
HomeMaster terminal extension, not an eighth `StreamEvent`. A normal or runtime-failed run that returns `RunResult`
emits exactly one result line and it is last. If startup/composition or another fatal CLI exception prevents creation
of any `RunResult`, emit at most one safe `error` line, emit no fabricated result, and exit nonzero. An
`assistant_complete.message` snapshot and the final result may structurally repeat final text for consumers; a human
renderer still displays that text once. Remove the current post-run `Assistant:`/`assistant:`/`final_reply` echo from
each live text/Rich entrypoint, while leaving buffered `json` as one document.

Do not mutate or overwrite internal `RunResult.final_reply`. Before serializing `text`, `json`, `stream-json`, or a
fatal CLI error, derive a safe public copy with the same complete one-shot sanitizer used by
`AssistantTurnComplete`. This includes `result.final_reply` and public result metadata. The reconstructed text-mode
stdout must equal that safe final text exactly once.

Do not create a second application/runtime path for streaming. Every entry still submits the same `RunRequest` to
the same `ApplicationRuntime`.

### Step 6: Preserve Gateway Security And Terminal Ownership

Audit, and change only if required:

- `src/homemaster/events/public_projection.py`
- `src/homemaster/gateway/runtime.py`
- `tests/homemaster/gateway/test_runtime.py`

Required result:

- Gateway can consume safe tool/status/compaction progress without private fields;
- raw reasoning never crosses the boundary;
- text-delta channel policy may drop or coalesce deltas;
- `RunResult` remains the only terminal final;
- no `AssistantTurnComplete`, assistant delta, or `runtime.turn_completed` produces a duplicate outbound final;
- generation fencing and bounded queue behavior remain unchanged.

### Step 7: Provenance, Documentation, And Serious-Bug Postmortem

Update:

- `plan/V1.9/upstream-port-manifest.json` with a new adapted port entry for locked OpenHarness `OutputRenderer`, its
  source hash, copied/characterization tests, HomeMaster deltas, and sync policy;
- `THIRD_PARTY_NOTICES.md` with the OpenHarness MIT notice; this is mandatory because the renderer is substantially
  adapted from the locked OpenHarness source;
- `README.md` with actual text/json/stream-json behavior;
- `docs/skills-and-config-user-guide.md` with interactive, print, piping, and JSON-lines examples;
- `docs/architecture/application-runtime.md` with the private RuntimeEvent -> seven StreamEvent -> renderer data flow
  and terminal-final invariant;
- `CHANGELOG.md` with what changed, why, and compatibility impact;
- `progress.md` with current status, next step, blockers, and validation facts.

This is a serious “tests green but user-visible feature absent” regression. After the fix, add the newest entry to
`docs/pitfalls.md` covering symptom, root cause, false-green tests, fix, and references. Add a positive imperative rule
to the relevant section of `CLAUDE.md`: provider-level streaming does not prove UI streaming; require a pre-completion
first-byte black-box gate for every live output entry.

## 7. Verification Gates

### Internal Focused Gates

Run focused tests for:

- provider async streaming;
- generic agent runtime;
- StreamEvent projection/EventBus;
- event sinks/Rich renderer;
- one-shot and interactive CLI;
- Gateway projection/runtime;
- cancellation/deadline and runtime stress.

Then run Ruff with the repository configuration, format check for touched files, `compileall`, `git diff --check`,
the upstream port-manifest validator, and the repository cleanup/secret guards.

### External Terminal Black-Box Gate

Unit tests and in-memory sinks are not sufficient. Add/run a subprocess-level test or self-verifier using a loopback
fake Anthropic-compatible SSE server and the real top-level HomeMaster CLI.

For each output mode, assert independently:

1. `text`: the first text bytes appear on the subprocess stdout pipe while the fake provider is still blocked and the
   CLI process is still running; after release, exit code is zero and concatenated assistant text equals the final
   reply exactly once.
2. `stream-json`: the first `assistant_delta` JSON line appears before provider release; every line parses
   independently; event order is correct; the terminal result line is last; exit code is zero.
3. `json`: no bytes are emitted before completion; exactly one JSON document appears after release; exit code is zero.
4. interactive Rich PTY: drive the real CLI in a PTY, interpret its control stream with a terminal emulator or
   screenshot mechanism verified in the implementation environment, and assert the final screen state rather than
   grepping the raw escape transcript. The spinner is visible before the first delta, absent after it, tool
   start/completion are distinguishable, final Markdown appears once, later prompts are not overwritten, and the
   process exits cleanly with no residual spinner/live content.

The exact external terminal-emulator library/API and Rich `Live` behavior are `UNVERIFIED` in this plan. The
implementing agent must first prove that the selected emulator/API is installed and actually interprets this Rich
version's cursor/control sequences. If none is available, use a real-terminal screenshot/pixel or final-screen
capture mechanism; raw transcript substring assertions do not satisfy this gate.

Also execute failure instances independently:

- partial delta followed by provider failure;
- tool failure (`ToolExecutionCompleted.is_error=True`);
- cancellation during provider wait;
- a payload containing a fake API key, credential assignment, host path, and URL query token.

For every instance assert process return code, stdout/stderr separation, no duplicate final, and no sensitive literal
in either stream. Do not use an aggregate `any`/best-instance criterion.

### Full Regression Gate

After focused and external gates pass, run the complete non-live test suite. Live provider/Gateway/device tests remain
separate and must not be claimed unless actually run in their real environment.

## 8. Review And Commit Protocol

1. This completed plan receives exactly one read-only reviewer before implementation.
2. The implementing agent addresses each plan-review finding and records the disposition below; do not request a
   second plan review.
3. Implement code, tests, external terminal validation, docs, provenance, postmortem, and all gates.
4. Start exactly one read-only final-code reviewer only after all of Step 3 is complete.
5. Address every final finding and run targeted verification; do not request another review unless the user asks.
6. Before committing, ensure `CHANGELOG.md` contains the same change/why/impact as the commit message.

## 9. Plan Review Disposition

The required single read-only plan review is complete. All six findings are accepted and incorporated:

1. Duplicate answer rendering: replaced raw token printing plus final Markdown append with one Rich `Live` region
   that is updated and then replaced; all live entrypoint final echoes are explicitly removed.
2. Cross-chunk secret leakage: specified a run/turn-scoped incremental sanitizer, EOF/error/cancel finalization, and
   character-boundary split tests with reconstructed stdout/stderr secret assertions.
3. Duplicate errors: assigned provider-attempt failures to private diagnostics/status only and the terminal run
   failure to the sole `ErrorEvent`, with fixed failure/cancel ordering in Section 5.
4. Renderer lifecycle ambiguity: added one explicit controller, state table, idempotent cleanup, and required scenario
   coverage including second model requests and partial failures.
5. Tool-only turns and tool pairing: required full safe assistant completion including tool calls, preserved the
   locked DTOs, and specified deterministic per-tool-name FIFO pairing including same-name parallel coverage.
6. JSON/PTY underspecification: froze all JSON-lines schemas plus HomeMaster's terminal extension and replaced raw PTY
   transcript matching with verified final-screen-state validation; unverified external terminal symbols remain
   marked `UNVERIFIED`.

Do not request a second plan review. The next reviewer is the single final-code reviewer, started only after the
implementing agent has completed code, tests, external terminal validation, documentation, provenance, and the
postmortem.
