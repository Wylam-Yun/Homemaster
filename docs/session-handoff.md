# Session Handoff

## Current State

The application now binds its authoritative working directory into the existing system prompt before frozen
identity/profile/persistent memory. Relative terminal/file paths therefore have a model-visible base, while a named
host, environment, device or project is explicitly not treated as the current workspace without visible evidence.
The existing runtime-context and automatic-recall ordering are unchanged.

Canonical `ToolExecutionResult` values now survive the generic adapter until the application message boundary, where
their existing provider projection supplies status, cwd, return code and structured errors. The established memory-tool
top-level JSON and flat internal metadata remain compatible; images and artifacts retain their prior projections.

Commit `e5c555f` is the clean baseline before the current workspace/provider-result, feedback-evidence and V2.7
async-memory-add changes. Explicit feedback is wired through
the successful provider attempt's frozen recall context. Session finalization has resumable add, implicit-feedback,
dreaming-counter and dreaming stages. Dreaming batches are scope-locked and persist under
`memory.data_root/mindmemos/dreaming_state`.

V2.7 changes public structured `mindmemos_add` to application-owned async acceptance. After scope evidence and record
validation it returns `accepted + job_id` without `memory_id`; one process-local FIFO worker performs Schema Add and exact
raw readback. Application close drains the queue before MindMemOS closes, and interactive finalization waits for the
queue before Vanilla Add, implicit feedback and optional dreaming. There is intentionally no broker, durable queue,
parallelism or retry; crash/`SIGKILL` may lose unfinished work.

Direct `mindmemos_update` now dispatches from the stored raw memory: Vanilla memories without `record_json` use the
native same-ID updater; valid Schema memories use a deterministic versioned fast path that updates content,
structured metadata, vectors and graph lineage without re-running Schema Add; corrupt `record_json` fails closed. The
new `mindmemos_history` tool reads the real `DERIVED_FROM` component from either an active or archived version.

## Verification

The new black-box regression executes real `pwd` and `sh -c 'exit 7'` subprocesses and parses the final Anthropic and
OpenAI request bodies. It passes for the exact workspace, status, return code, timeout flag and structured error. Sync
and async context assembly produce the same base prompt -> workspace -> frozen memory order. Application (120), tools
(129), generic-runtime/dispatcher (50) and memory (111 passed, 1 skipped) suites pass.

A real Mimo run from `/tmp/homemaster-workspace-replay-20260818` returned `replied` and asked for Aurora-A18's missing
path before installation. The persisted conversation contained only user and assistant messages, with no tool calls or
`uv venv`; `pyproject.toml`, `uv.lock` and `.venv/sentinel.txt` retained their exact pre-run SHA-256 hashes. The first
attempt correctly failed before Provider because the already-running HomeMaster process owned the configured local
Qdrant path; rerunning with an isolated temporary HOME passed without stopping that process.

The wider non-memory/non-live/non-stress run reached 1280 passed, 2 skipped and 9 deselected. Its 12 failures were six
missing-Playwright tests, two unavailable MCP protocol tests, and four tests affected by concurrent V2.7 memory-queue or
pre-existing cleanup-classification changes; none traverse the workspace or canonical tool-result projection paths.

Focused V2.6 unit/integration gates pass, including cross-process single-owner claiming and typed failure propagation.
The complete non-live suite passes with `1675 passed, 2 skipped, 10 deselected`. The installed-wheel probe passes with
`2 passed`; full `compileall`, Ruff and `git diff --check` also pass.

The combined live gate passes with `2 passed in 885.84s`. A real provider run selected exactly one
`mindmemos_feedback` call; Qdrant readback verified the old raw record was archived, the corrected raw record was active,
and an unrelated record was unchanged, while Neo4j readback verified the `DERIVED_FROM` relation. The real dreaming
no-action path verified the seed raw record, completed add-record status, cleared persistent pending state and exact
watermark.

The direct update/history follow-up passes 29 focused runtime/tool tests, 19 memory-tool tests and 64
registry/application/package tests. Its broader non-live gate passes with `1672 passed, 3 skipped, 11 deselected`; the
excluded tests require optional Playwright or MCP dependencies absent from this environment. A separate real
Qdrant/Neo4j/provider gate passes in `103.71s`: the old Schema memory is archived, the new memory and `record_json` both
contain `uv`, provenance advances to 2, the reused entity contains `uv` rather than `conda`, `DERIVED_FROM` exists, and
history queried from either ID returns `[new, old]`. Trace timing shows the direct Schema update completes in about one
second and does not enter Schema Add or its chat stages.

The V2.7 focused async Add/benchmark/lifecycle set passes with 30 tests. The expanded non-live memory, application,
benchmark and CLI gate passes with `271 passed, 1 skipped, 3 deselected`; Ruff and `git diff --check` pass for the touched
implementation and tests. The isolated V2.7 live gate passes in `337.10s`: two rapid jobs independently reached queued,
processing and completed in FIFO order; application close waited for both, and a fresh backend instance independently
read each returned ID as active with the exact original `record_json` before cleanup.

The final runnable non-live suite passes with `1686 passed, 3 skipped, 12 deselected`. Six browser tests require the
absent Playwright extra, and two MCP protocol tests require a compatible `mcp.server` package; they are independently
excluded rather than counted as product regressions. Full Ruff, compileall, cleanup guard and `git diff --check` pass.

## Next Step

Restart any HomeMaster process created before this change before evaluating the new tool contract.

The currently running HomeMaster shell was started before this code changed. Exit and restart it before interactive
testing so that the seven-tool memory registry, including `mindmemos_history`, is loaded.

## Blockers

No code blocker is known. The user explicitly authorized a global baseline commit and the final implementation commit;
preserve all concurrent provider/evidence/feedback changes. Archived memories created by an older direct-update implementation without a real
`DERIVED_FROM` edge cannot be reconstructed into history and must not be guessed. Structured feedback now replaces and
verifies canonical `record_json`; keep its exact-record terminal gate when changing the vendored executor.
