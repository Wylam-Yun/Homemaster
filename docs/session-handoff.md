# Session Handoff

## Current State

Unified Session Finalization is implemented through idempotent `application.session(...)` scopes. Shell, one-shot,
ALFWorld and LoCoMo now submit the existing Finalizer to the shared FIFO at their semantic session boundary; `/new`
does not wait, while normal application close drains before MindMemOS/Neo4j. `run()` remains a turn operation and
Gateway remains unfinalized per message because it has no explicit conversation-end event. Admission requires both a
started FIFO and available embedded MindMemOS.

ALFWorld benchmark portability work is active on `alfworld-benchmark-local` and mirrored to the hkust4 worktree
`/home/haodong2/weilin/red_bird/Homemaster-alfworld` branch `alfworld-benchmark`. The adapter now delegates to canonical
`create_home_application(tool_environment="alfworld")`, requires embedded MindMemOS plus its FIFO, and keeps legacy
`memory_mode=disabled` solely to prevent the obsolete ALFWorld writer. ALFWorld itself remains unchanged and owns only
environment/actions/images. Runtime and session roots can be supplied exactly; YAML memory/Neo4j/Java relative paths
resolve against the config directory.

On hkust4, the original copied Neo4j directory was preserved as `.runtime/neo4j-contaminated-20260819`; a clean verified
Neo4j 2026.05.0 distribution and Temurin 21.0.11 now live under ignored `.runtime/neo4j` and `.runtime/java`. The private
ignored `config/homemaster.yaml` points to those paths and to a fresh smoke memory root. That environment-qualification
step made no model/chat call; the complete benchmark runs are recorded below.

The first complete visual LLM evaluation is recorded under ignored run ID
`alfworld-llm-memory-statue-20260819`. Its single score-eligible `valid_unseen` episode completed with valid provider,
runtime and harness coverage, but scored zero with `classification=agent_model_failure` and `failure_reason=not_won`.
Mimo navigated among the floorlamp and three statues and observed real frames, then incorrectly treated visual proximity
as completion. It never took a statue and used the floorlamp, so ALFWorld never reported `won=true`. This is an agent task
semantics failure, not an environment, navigation, provider or MindMemOS availability failure.

Prompt composition now exposes the formal `look_at_obj_in_light` semantics without exposing expert actions or hidden
scene identity: hold the target object, approach the named lamp, and turn it on while still holding the object. The same
frozen trial then passed under run ID `alfworld-llm-memory-statue-semantics-20260819` with `agent_success`, external
`won=true`, goal-condition success 1.0 and no invalid action.

After unified session finalization was connected, the same frozen visual trial passed again under run ID
`alfworld-session-finalizer-memory-20260819`. Its FIFO job `4655a03a-67a2-4852-b036-2134d43249d1` reached
queued/processing/completed; experience job `3f231bc2facb95fb7585f876208da423b5985a934796a6259bc9050bf76d6311`
completed and recorded four active memories. Independent application restart read all four from Qdrant with exact
episode `session_id`, `source_session_id` and request ID, and read their `EXTRACTED_FROM` edges from Neo4j.

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

V2.7 public `mindmemos_add` now accepts only exact `content + memory_type`. After current-scope evidence validation it
returns `accepted + job_id` without `memory_id`; one process-local FIFO worker performs a deterministic direct flat write
and exact raw readback. The explicit path calls no chat/Schema/Vanilla extraction and writes no Entity or `MENTIONS`;
it retains memory embedding/BM25, Qdrant Memory and Neo4j Source/`EXTRACTED_FROM`. Interactive Session finalization is a
typed item in that same FIFO. `/new` enqueues the old Session and immediately switches; terminal exit and application close
drain flat Add plus unchanged Vanilla Add, implicit feedback and
optional dreaming before MindMemOS closes. There is intentionally no broker, durable queue, parallelism or retry;
crash/`SIGKILL` may lose unfinished work.

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

The hkust4 terminal gates pass: Neo4j config validation exits 0; production ALFWorld composition starts Bolt and
MindMemOS, exposes the exact ALFWorld and six MindMemOS tools, then removes the listener/process on close. A real FIFO
fact Add returns accepted/completed; a fresh verifier reads exact ID/content/native fact type from Qdrant and exactly one
Memory, Source and `EXTRACTED_FROM` from Neo4j. Its provider log has one embedding call and zero chat calls. The isolated
ALFWorld live smoke passes in 66.57s with reset, one rejected invisible target, one real Drawer navigation, a nonblank
300x300 PNG, and worker/Unity/Xvfb cleanup. The final synced worktree passes 218 ALFWorld tests, 67
config/composition/parity tests, Ruff, compileall and `git diff --check`. The final private Neo4j config gate also verifies
loopback Bolt ready, HTTP disabled, MindMemOS available, clean close with zero Neo4j processes, and `.runtime/` ignored.

The complete Mimo visual episode exits zero and produces a formal score. All 30 provider requests have completed
responses with no provider error; 26 carry outbound images. The model issued 29 tool calls and six real model-driven
THOR navigation actions after 36 setup actions. All seven captured 300x300 PNGs decode successfully and are nonblank.
Canonical MindMemOS emitted `memory.automatic_recall` with `status=empty`, `count=0` and no error against the fresh
memory root; Neo4j independently completed its Bolt handshake and database query. Final cleanup leaves zero Neo4j,
Unity and `Xvfb :110` processes and no listener on port 7687.

The fixed-semantics rerun has 12/12 completed provider responses, zero provider errors and 11 image-bearing requests.
It made exactly five model backend actions after 36 setup actions: navigate to statue, take statue from shelf, navigate
to the floorlamp twice, and use the floorlamp. The terminal action independently reports `inventory=["statue 1"]` and
`target_state=toggled_on`; the run summary reports `success=true`, `classification=agent_success`,
`goal_condition_success_rate=1.0`, and formal success rate 1.0. All seven 300x300 frames decode and are nonblank;
automatic recall again completed without error. Final cleanup leaves zero Neo4j, Unity and `Xvfb :111` processes and
no Bolt listener.

The unified-finalizer rerun has 18/18 completed provider responses, zero provider errors and 17 image-bearing requests.
All seven 300x300 PNG frames decode and have nonzero pixel variance. The formal episode summary reports success rate,
goal-condition success, provider availability, runtime availability and harness coverage all 1.0. Finalizer drain took
221.5 seconds and completed before process exit. The four persisted native types are `fact`, `experience`, `tool_trace`
and `skill_candidate`; raw metadata and graph source edges agree on the episode lineage. Post-run and post-verifier
checks found no THOR, `Xvfb :113`, Neo4j or benchmark process and no listener on port 7687.

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

The one-episode visual benchmark gate is complete. Before a larger run, freeze the evaluation set and scoring protocol;
continue requiring per-episode external `won=true`, valid coverage and complete resource cleanup.

Any HomeMaster shell started before this change cannot hot-load the direct-flat Add contract. Start a fresh shell from
this working tree before interactive verification; do not infer behavior from a pre-change process.

## Blockers

No memory, infrastructure or one-episode benchmark blocker is known. Do not commit without fresh explicit authorization,
and preserve the concurrent
provider and V2.7 async-add changes. Archived memories created by an older direct-update implementation without a real
`DERIVED_FROM` edge cannot be reconstructed into history and must not be guessed. Keep the structured feedback
exact-record/content/lineage terminal gate when changing the vendored executor. Optional Playwright/MCP environment
failures and unrelated cleanup-guard expected-set drift remain outside this follow-up.
