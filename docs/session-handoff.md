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
raw readback. Interactive Session finalization is now a typed item in that same FIFO. `/new` enqueues the old Session and
immediately switches; terminal exit and application close drain structured Add plus Vanilla Add, implicit feedback and
optional dreaming before MindMemOS closes. There is intentionally no broker, durable queue, parallelism or retry;
crash/`SIGKILL` may lose unfinished work.

Terminal cleanup owns SIGINT continuously across finalization admission and FIFO drain; application close has a second
guard. Background `/new` finalization does not own SIGINT, so Ctrl+C during an ordinary run still cancels only that run.
The observed `zh_core_web_sm` warnings were unrelated optional-NER degradation.

Direct `mindmemos_update` now dispatches from the stored raw memory: Vanilla memories without `record_json` use the
native same-ID updater; valid Schema memories use a deterministic versioned fast path that updates content,
structured metadata, vectors and graph lineage without re-running Schema Add; corrupt `record_json` fails closed. The
new `mindmemos_history` tool reads the real `DERIVED_FROM` component from either an active or archived version.

The Schema feedback/evidence follow-up is implemented. Structured recalled memories carry their full parsed record
into planning; update without a complete valid replacement record fails closed. HomeMaster validates type, identity
and source, derives content from that record, reuses the deterministic versioned writer, and verifies exact persisted
record/content plus old/new states and lineage. Add/update provider schemas and messages no longer expose
`memory-evidence-*`; executors select current tenant/session/run/turn/source evidence from the ledger.

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

The Schema feedback/evidence follow-up passes 191 focused application, memory, experience and vendored-feedback tests
(1 skipped), plus 65 registry/runtime/package/benchmark/shell tests. Its latest full unfiltered non-live collection
reached 1684 passed, 3 skipped and 12 deselected; the nine failures are the six absent-Playwright tests, two incompatible
MCP protocol tests, and pre-existing cleanup-guard expected-set drift for `docs/session-handoff.md` and
`dreaming_state.py`. No failure traverses this memory path. Ruff, compileall and `git diff --check` pass.

The exact live regression passes in `436.55s`. Starting from Schema `package_manager.value="uv"`, the real provider
made exactly one `mindmemos_feedback` call with only natural-language feedback. Two recalled same-source raw versions
(fact and episodic) were verified individually: every replacement record/value and canonical content contains online
`uv` and offline `Poetry`, every old version is archived, every new version is active with `DERIVED_FROM`, and each
entity description contains both values. The unrelated editor memory remained active and byte-identical.

The async Session-finalization regression passes as part of 147 focused tests: 57 CLI/memory, 54
experience/ApplicationRuntime/composition and 36 benchmark/cleanup-guard tests. It proves `/new` reaches the next real
run before a delayed old finalizer completes; structured Add and finalization execute in exact FIFO order with maximum
memory concurrency one; a failed finalizer produces a failed work receipt without blocking the next Add; close waits
for queued finalization. Real SIGINT injection still proves terminal drain and application close continue after the
signal while an ordinary run remains cancellable. Full Ruff, compileall and `git diff --check` pass.

For Session `20260818_144025_8e5055`, both duplicate structured Add jobs completed externally: job
`67f114f4-ce97-46d5-ba21-2acae1e4186a` wrote `7eaa5795-2bdf-402d-87e4-5000e071cb52`, and job
`450eadab-ab86-474f-9fcd-bbd096cb8b0e` wrote `c24dab78-dbc1-47fe-9fc5-f53141f5a706`. Its persisted experience job has
completed Vanilla Add but still has implicit feedback and dreaming-counter pending; do not report that old process as
fully finalized.

## Next Step

Two pre-change HomeMaster shells are still running (`13371` and `539714`). They cannot hot-load the async `/new`
behavior. Let their current memory work finish or terminate them explicitly, then start a fresh shell from this
working tree before interactive verification.

## Blockers

No memory code blocker is known. Do not commit without fresh explicit authorization, and preserve the concurrent
provider and V2.7 async-add changes. Archived memories created by an older direct-update implementation without a real
`DERIVED_FROM` edge cannot be reconstructed into history and must not be guessed. Keep the structured feedback
exact-record/content/lineage terminal gate when changing the vendored executor. Optional Playwright/MCP environment
failures and unrelated cleanup-guard expected-set drift remain outside this follow-up.
