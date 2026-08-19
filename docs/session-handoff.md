# Session Handoff

## Current State

On 2026-08-19, the `hkust4` deployment checkout at
`/home/haodong2/weilin/red_bird/Homemaster` was aligned with this HPC2 workspace. The remote checkout tracks
`origin/mindmem` at `c0a9dad4b3f85ccb95df8040c75ae3957aa26346`; Git-visible uncommitted workspace content is synchronized
separately, while `.git`, ignored local configuration, caches and virtual environments remain host-owned.

`mindmemos_search` now uses all seven native MindMemOS memory types. It no longer maps `experience` to a public
`procedure` type or reports valid `tool_trace` records as corrupt. This changes only search input/projection;
model-authored `mindmemos_add` remains `fact|procedure`.

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

V2.7 public `mindmemos_add` accepts only exact `content + memory_type`. After current-scope evidence validation it
synchronously writes local BM25, Qdrant Memory and Neo4j Source/`EXTRACTED_FROM`, verifies both stores, then returns
`stored + memory_id`. Two application-owned workers enrich the same ID with remote dense vectors and native
Entity/`MENTIONS`; internal pending state is not exposed to the model. Interactive Session finalization remains in its
serial FIFO but now enables native Entity in both the Vanilla extractor and config before implicit feedback and Dreaming.
Application close drains both queues before MindMemOS. There is no broker, durable queue or automatic retry;
crash/`SIGKILL` may lose unfinished background enrichment.

Terminal cleanup owns SIGINT continuously across finalization admission and FIFO drain; application close has a second
guard. Background `/new` finalization does not own SIGINT, so Ctrl+C during an ordinary run still cancels only that run.
The observed `zh_core_web_sm` warnings were unrelated optional-NER degradation.

Direct `mindmemos_update` now dispatches from the stored raw memory: Vanilla memories without `record_json` use the
native same-ID updater; valid Schema memories use a deterministic versioned fast path that updates content,
structured metadata, vectors and graph lineage without re-running Schema Add; corrupt `record_json` fails closed. The
new `mindmemos_history` tool reads the real `DERIVED_FROM` component from either an active or archived version.

Historical Schema memories remain compatible and are not migrated. The Schema feedback/evidence follow-up is implemented.
Structured recalled memories carry their full parsed record
into planning; update without a complete valid replacement record fails closed. HomeMaster validates type, identity
and source, derives content from that record, reuses the deterministic versioned writer, and verifies exact persisted
record/content plus old/new states and lineage. Add/update provider schemas and messages no longer expose
`memory-evidence-*`; executors select current tenant/session/run/turn/source evidence from the ledger.

## Verification

On 2026-08-19 a real `mimo-v2.5` ApplicationRuntime run selected exactly one `mindmemos_add` for
`Project live-tool-9fa5f6e933 uses the uv package manager.`. The first provider decision took 10.743s, the synchronous
tool call returned verified `stored` ID `556ddef6-69b7-5076-86d3-ed54f8b7c927` in 78.9ms, and the provider's final
acknowledgement took 24.586s. The cold application call was 74.0s including managed backend startup. Automatic
enrichment admission reached `queued -> processing`; its first real run exposed an exact-float readback bug even though
Qdrant held a nonzero 4096-dimensional vector with only `7.45e-9` maximum float32 round-trip error. The verifier now
uses per-value numerical tolerance. Retrying the same ID, rather than creating a replacement, reached `completed` in
85.475s with both pending markers false, exact original content, two Entity nodes (`live-tool-9fa5f6e933`, `uv`), two
`MENTIONS`, and exactly one Memory. The focused memory/tool/queue suite passes with `39 passed`; Ruff, compileall and
`git diff --check` pass.

A separate isolated one-turn LoCoMo `conv-26` run used Caroline as both the real memory project and user identity and
forced only the Dreaming threshold from 8 to 1. Session Finalizer completed one active fact, implicit feedback completed
with zero corrective actions, and Dreaming emitted threshold-reached, started, then `no_action` with no failure. Its
persistent state has no inflight or pending batch and records the add record in the successful watermark. Independent
Qdrant/Neo4j readback found exact content `Caroline greeted Mel on 2023-05-08 at 13:56:00.`, a nonzero 4096-dimensional
dense vector, one Memory, one Source, and two Entity/`MENTIONS` targets (`Caroline`, `Mel`). This proves Dreaming starts
and terminates; `no_action` is the native outcome for that single greeting, not evidence that it was skipped.

The stored-first external gate passes in `120.36s` against real Qdrant, Neo4j and configured chat/embedding APIs. Before
enhancement, the returned ID independently had exact active content, non-empty BM25, an all-zero dense vector, one
Memory/Source/`EXTRACTED_FROM`, and both internal pending markers. After the two-worker queue drained, the same ID had a
non-zero dense vector, both markers cleared, per-Entity Qdrant vectors and per-Entity Neo4j `MENTIONS`; the graph still
contained exactly one Memory. The related non-live runtime/tool/application/benchmark queues also pass, including the
maximum-concurrency-two and model-visible receipt checks with no `job_id` or background fields.

The existing LoCoMo store at `/tmp/homemaster/locomo-memory-100-20260818-v2` was opened through the production
HomeMaster composition. A real `mindmemos_search` filtered by `tool_trace` returned exactly the two known Finalizer IDs
`767380b8-cc62-5df0-a8d4-296f4c6d9ff6` and `bbc2af94-06bd-509b-94f9-149b99e7f3ec`, both with exact content and native
type, and returned no diagnostics.

The direct-flat follow-up passes 253 non-live memory/application/experience tests (1 skipped, 4 live deselected), plus
focused Ruff, compileall and `git diff --check`. Its latest real Qdrant/Neo4j gate passes in 23.10s for one fact and one procedure:
each raw ID is independently active with exact submitted content and the expected native type, each graph has exactly one
Source and `EXTRACTED_FROM`, and each has zero Entity and `MENTIONS`. The captured log has exactly two
`memory.add.embed` calls, zero `kind=chat` calls and no `memory.add.extract`. Each raw memory's persisted
`add_record_id` independently resolves to an `ok` operation record whose returned memory ID matches that raw memory.

The full non-live/non-stress collection reaches `1693 passed, 7 skipped, 11 deselected`. The unfiltered run additionally
has six expected failures because Playwright is not installed and two because the installed MCP package lacks
`mcp.server`. After excluding those unavailable optional integrations, one order-dependent pre-existing SIGINT test fails
with a fake run returning `None`; the complete interactive CLI file passes independently with `13 passed`. This failure
does not traverse the Add queue or MindMemOS writer and was not changed in this follow-up.

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
run before a delayed old finalizer completes; Add and finalization execute in exact FIFO order with maximum
memory concurrency one; a failed finalizer produces a failed work receipt without blocking the next Add; close waits
for queued finalization. Real SIGINT injection still proves terminal drain and application close continue after the
signal while an ordinary run remains cancellable. Full Ruff, compileall and `git diff --check` pass.

For Session `20260818_144025_8e5055`, both duplicate structured Add jobs completed externally: job
`67f114f4-ce97-46d5-ba21-2acae1e4186a` wrote `7eaa5795-2bdf-402d-87e4-5000e071cb52`, and job
`450eadab-ab86-474f-9fcd-bbd096cb8b0e` wrote `c24dab78-dbc1-47fe-9fc5-f53141f5a706`. Its persisted experience job has
completed Vanilla Add but still has implicit feedback and dreaming-counter pending; do not report that old process as
fully finalized.

## Next Step

Any HomeMaster shell started before this change cannot hot-load the direct-flat Add contract. Start a fresh shell from
this working tree before interactive verification; do not infer behavior from a pre-change process.

## Blockers

No memory code blocker is known. Do not commit without fresh explicit authorization, and preserve the concurrent
provider and V2.7 async-add changes. Archived memories created by an older direct-update implementation without a real
`DERIVED_FROM` edge cannot be reconstructed into history and must not be guessed. Keep the structured feedback
exact-record/content/lineage terminal gate when changing the vendored executor. Optional Playwright/MCP environment
failures and unrelated cleanup-guard expected-set drift remain outside this follow-up.
