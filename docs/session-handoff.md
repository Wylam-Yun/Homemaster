# Session Handoff

## Web ALFWorld Serve (2026-08-21)

- `homemaster serve --alfworld` now registers the ALFWorld profile, starts the configured fixed-episode worker,
  wraps the shared application with `AlfworldGatewayApplication`, and keeps the existing Web approval protocol.
  Ordinary `serve` remains loopback-only and unchanged.
- hkust4 real run `run-d28943a728f9` used the public Web session/message/WebSocket/approval/artifact APIs. Five
  mutating tool calls each produced an exact approval ID and returned HTTP 200/approved before execution. The run
  completed the statue/floorlamp goal. An independent authenticated `/v1/state` read returned state sequence 6,
  `won=true`, `done=true`, inventory `statue 1`, and invalid-action count 0. All five public 300x300 PNG artifacts
  independently decoded nonblank and matched their event hash and `X-Content-SHA256`.
- A first run with shared production memory was correctly not counted as success: a recalled peppershaker task
  caused model goal drift and exhausted the tool budget. The successful deterministic demonstration disabled memory
  only in the already-loaded process configuration; the ignored canonical config was restored immediately after
  startup.
- Live shutdown exposed and fixed an outbound-only WebSocket leak. After the fix, an idle client disconnect followed
  by one SIGINT completed application shutdown with no traceback; the exact HomeMaster, worker, Unity, Xvfb, display
  lock, and port listener all disappeared.
- The persistent hkust4 ignored `config/homemaster.yaml` now points `alfworld_gateway` at the existing
  `.runtime/alfworld`, `.runtime/alfworld-venv`, one-trial statue manifest, and application-managed display `:120`.

## Permission Approval Handoff (2026-08-20)

### User Decision

The local CLI approval flow is the reference behavior. Feishu must use the same permission decision
and approval lifecycle; do not create a second Feishu-only permission policy. The future Web console
must reuse the same approval contract as both CLI and Feishu.

The default remains unrestricted automation (`full_auto`). Approval is opt-in: a caller selects the
normal/confirm policy mode, or a configured permission subject lacks `tool.auto`. `plan` remains a
deny-without-prompt mode. This scope does not include remote approval policy, risk tiers, approval
receipts, task authorization, or multi-tenant auth.

### What Is Implemented

- `src/homemaster/tools/executor.py` is the single execution gate. It validates arguments, calls
  `PermissionChecker`, then invokes `confirmation_handler` before resource acquisition and backend
  execution. Denial produces no resource lease and no external mutation.
- `src/homemaster/cli/confirmation.py` implements the current local adapter. Prompts are serialized,
  fail closed (`y`/`yes` only), and emit `permission.confirmation_requested` and
  `permission.confirmation_completed` into the runtime trace.
- `src/homemaster/application/factory.py` and `src/homemaster/cli/composition.py` already inject
  permission settings and an optional confirmation handler without changing the default.
- CLI entry points support `--permission-mode full_auto|confirm|plan`; only explicit `confirm` removes
  `tool.auto` from the local interactive subject.

### Feishu Gap (Current Reality)

`src/homemaster/channels/bridge.py` already creates a `RunRequest` with the inbound principal's
`permission_subject`, so Feishu is on the same application/runtime path. However, the Gateway/Feishu
composition does not currently provide a remote confirmation handler. Also,
`src/homemaster/events/public_projection.py` does not yet expose the two confirmation events. Therefore
Feishu currently has no way to display a pending approval or resolve it; adding a Feishu-side policy
checker would create the split this handoff explicitly forbids.

### Recommended Unified Design

1. Move the handler contract and request/decision DTO out of `cli/` into `homemaster.permissions` (or a
   small application-level confirmation module). The executor should depend only on this protocol:
   `confirm(request) -> bool` (or a typed decision), never on CLI/Feishu/Web types.
2. Keep channel adapters thin:
   - CLI adapter renders the request and reads `y`/`yes`.
   - Remote adapter stores one pending request keyed by `approval_id`, emits the public request event,
     and awaits a single resolver call. Feishu cards/callbacks and Web HTTP/WS endpoints only render
     and resolve that request; they do not decide permissions.
3. Select policy in one place through `PermissionSettingsConfig.mode` and the same
   `PermissionChecker`. Feishu and Web subjects must be constructed without `tool.auto` when their
   selected mode requires approval. No `feishu_confirm` or `web_confirm` mode should be added.
4. Extend the public event projection with a safe confirmation-request/completed schema. Include the
   approval id, tool name, validated arguments, cwd and correlation fields; define redaction before
   exposing arguments because tool arguments can contain secrets. Preserve the full payload only in the
   private JSONL trace.
5. Define pending-request lifecycle before transport work: timeout/cancel/close resolves as denied,
   duplicate or late callbacks are rejected, and every request is removed after resolution. The
   `session_id`, `run_id`, `turn_index` and `tool_call_id` must be checked on resolution.

### Next Steps For The Successor

1. Add the shared confirmation protocol/DTO and adapt `CliConfirmationHandler` without changing CLI
   behavior or the `ToolExecutor` gate.
2. Implement an application-owned pending confirmation registry/handler. Keep it independent of
   Feishu and Web transport; inject it from the Gateway composition.
3. Add public event projection and Feishu card/callback plumbing, then add the Web API/WS plumbing by
   reusing the same registry.
4. Run black-box gates per channel: approve and deny one real mutating tool, assert the external state
   and return status, assert denial acquires no resource and calls no backend, and verify timeout,
   cancellation, duplicate and stale callback behavior. Also verify `full_auto` still performs an
   automated mutation without waiting.

### Current Blockers / Boundaries

- No Feishu or Web implementation has been started in this workspace; this is a design handoff, not a
  claim that remote approval is already functional.
- Feishu callback identity/authentication and the exact card format still need to be decided at the
  channel layer. They must resolve an existing approval id, never re-run permission logic.
- Do not commit or revert unrelated portable-memory changes. `plan/V2.8/` is pre-existing work and must
  remain untouched.

## Current State

Latest portable deployment gates (2026-08-20): `mindmem` commit `f052a67` is pushed. The formal hkust4 checkout
`/home/haodong2/weilin/red_bird/Homemaster` fast-forwarded to that commit and one-time setup now reports
`alfworld_ready=true`, binding `.runtime/alfworld` to the complete config/dataset root. A first formal visual run
failed before the model request because an editable MindMemOS `.pth` was not executed across the ALFWorld venv; the
launcher now resolves the formal source root and performs a final-consumer import preflight. The fresh rerun
`alfworld-portable-hkust4-20260820-1230` passed one frozen `AlfredThorEnv` episode with external `won=true`,
`agent_success`, goal-condition/formal success 1.0, provider/runtime/harness/evaluation coverage 1.0, ten nonblank
PNG frames, and no stderr. Its Finalizer job `f1608581f4703520f0059fcde40ffe9d08ceac304cb77775582b7e2c3da1b52c`
completed; six memory IDs were independently read back active from Qdrant and Neo4j with nonzero dense vectors,
BM25, `EXTRACTED_FROM`, and `MENTIONS`. THOR, Xvfb, benchmark, Bolt listener, and lease cleanup returned to zero.
The HPC2 formal launcher run `locomo-portable-hpc2-20260820-1200` completed `conv-26` with two source turns,
one Finalizer session, one active memory readback, and zero QA probes. Its stderr contains only the structured
`memory.migration.completed` event for the fresh isolated root; no traceback or external failure occurred.

Interactive CLI local approval is implemented and awaiting commit. `homemaster` and `homemaster shell` expose
`--permission-mode full_auto|confirm|plan`; the default remains `full_auto`, while explicit `confirm` maps to the
existing default policy and removes `tool.auto` only from the local shell subject. Approval is fail-closed, serialized,
audited, and occurs before resource acquisition/backend invocation. The permission-focused gate passed 89 tests with
one pre-existing Python 3.13 `asyncio.to_thread` cancellation test deselected; the independent application black-box
gate passed both approval and denial, including exact file terminal state and backend call counts. Ruff, compileall,
CLI help and `git diff --check` also pass. `plan/V2.8/` remains untouched.

Portable deployment work is in progress on top of `ba1be641885c72c7c1a4068eb7c878ffb129f2bc`: the repository now
contains a one-time runtime setup and `scripts/homemaster` launcher, with tests for relative configuration, existing
memory binding and conflict rejection. The formal worktree on hkust4 must be initialized independently; the previous
visual candidate used a detached code checkout plus the old ALFWorld runtime and is not the formal migration gate.

The integration is committed locally at `HEAD` on branch `integration/alfworld-into-mindmem-20260819`. Its two parents
are
`b96e603a542584cb6754cc2c517790a74e6eb986` (stored-first `mindmem`) and
`a228c1506a4d10d3b0a07f0c554cbec252c647f1` (ALFWorld). Git produced no textual conflict. The semantic overlap was the
old admission-only `accepted + job_id` contract versus `stored + memory_id`; the integrated result intentionally keeps
the stored-first public contract and adds ALFWorld's canonical visual/session behavior around it. Neither source branch
or original worktree was moved, and nothing has been pushed. Product code was live-tested at candidate
`7964bc441b15808be95c0f306955df6398e1ad7b`; subsequent amendments change only the four verification/rules documents
listed below.

On 2026-08-19, the `hkust4` deployment checkout at
`/home/haodong2/weilin/red_bird/Homemaster` was aligned with this HPC2 workspace. The remote checkout tracks
`origin/mindmem` at `c0a9dad4b3f85ccb95df8040c75ae3957aa26346`; Git-visible uncommitted workspace content is synchronized
separately, while `.git`, ignored local configuration, caches and virtual environments remain host-owned.
Unified Session Finalization is implemented through idempotent `application.session(...)` scopes. Shell, one-shot,
ALFWorld and LoCoMo source ingestion submit the existing Finalizer to the shared FIFO at their semantic session
boundary; LoCoMo QA probes remain ordinary one-turn runs so answers do not enter benchmark memory. `/new` does not
wait, while normal application close drains before MindMemOS/Neo4j. `run()` remains a turn operation and Gateway
remains unfinalized per message because it has no explicit conversation-end event. Admission requires both a started
FIFO and available embedded MindMemOS.

The pre-integration ALFWorld portability work was developed on `alfworld-benchmark-local` and mirrored to the hkust4
worktree `/home/haodong2/weilin/red_bird/Homemaster-alfworld` branch `alfworld-benchmark`. The integrated adapter now
delegates to canonical
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

The final integration candidate was copied by Git bundle to detached hkust4 worktree
`/home/haodong2/weilin/red_bird/Homemaster-integration-7964bc4` and validated with a fresh, non-reused run ID
`alfworld-integration-7964bc4-visual-final-clean1`. The frozen
`valid_unseen/look_at_obj_in_light-Statue-None-FloorLamp-219` episode ran through `AlfredThorEnv` and
`observation_mode=visual_eval`: shell RC was zero, stderr was empty, score and goal-condition success were 1.0,
classification was `agent_success`, formal score was available, all provider/runtime/harness/evaluation coverage was
1.0, and both runtime event streams ended with `alfworld_won`. The final backend actions held `statue 1`, approached
`floorlamp 1`, and left it `toggled_on`. All 29 provider attempts completed without a cause/error; at least two carried
unstripped images. All ten 300x300 PNG frames decoded and had nonzero per-channel range.

Session Finalizer job `d7170b82-3b54-4688-80a1-751a544be896` completed. An independent backend restart checked all seven
persisted IDs separately: every raw record was active with exact expected content, every Qdrant dense vector had 4096
nonzero dimensions with both enrichment-pending flags cleared, every Neo4j Memory had one or more
`EXTRACTED_FROM` edges, and its `MENTIONS` count exactly matched Qdrant metadata. After both the benchmark and verifier,
candidate processes, run-specific THOR/Xvfb, Bolt 7687 listeners and managed-Neo4j lease files were all zero. Artifacts
remain under the original ignored runtime root at
`/home/haodong2/weilin/red_bird/Homemaster-alfworld/.runtime/benchmark-runs/test/alfworld-integration-7964bc4-visual-final-clean1`.

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

The final integration gates pass locally with 134 focused tests plus 3 LoCoMo source-only Finalizer regressions. The
complete non-live/non-stress collection reached 1710 passed, 3 skipped, 16 deselected and 9 environment failures: six
require the absent Playwright extra, two require an installed `mcp.server`, and one cleanup-guard mismatch was fixed.
Excluding those unavailable optional integrations, the final collection passes with 1711 passed, 3 skipped and 16
deselected. Ruff, compileall, cleanup guard and `git diff --check` pass. The detached hkust4 candidate also passes 56
focused tests.

The hkust4 LoCoMo live run `locomo-integration-7964bc4-one` replayed `conv-26`/Caroline with three source turns and one
QA probe. It returned RC zero with one completed source session and one probe. Only the source session received a
Finalizer receipt; the QA record has no finalization. Its two active memory IDs were independently read back. This is
the black-box complement to the admission-handler regression proving QA answers do not enter evaluation memory.

An earlier ALFWorld orchestration attempt is deliberately not counted as evidence. The same run ID/root was launched
twice and the second launch deleted the first process's live state, producing a successful summary alongside a readonly
SQLite traceback and an orphan Neo4j. The root cause and prevention rule are recorded in `docs/pitfalls.md` and
`CLAUDE.md`; no product-code workaround was made.

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
The following ALFWorld and Neo4j results are historical pre-integration gates from `a228c15`; they do not certify the
current stored-first integration until rerun on its immutable candidate commit. The hkust4 terminal gates pass: Neo4j
config validation exits 0; production ALFWorld composition starts Bolt and
MindMemOS, exposes the exact ALFWorld and six MindMemOS tools, then removes the listener/process on close. A real FIFO
fact Add returned the old `accepted/completed` contract; a fresh verifier read exact ID/content/native fact type from
Qdrant and exactly one
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

The one-episode LoCoMo and visual ALFWorld integration gates and the documentation-only merge amendment are complete.
The branch can be handed off for review; do not push or move `mindmem`,
`alfworld-benchmark-local` or `alfworld-benchmark` without explicit instruction. Before a larger benchmark run, freeze
the evaluation set and scoring protocol; continue requiring per-episode external `won=true`, valid coverage and complete
resource cleanup.

Any HomeMaster shell started before this change cannot hot-load the direct-flat Add contract. Start a fresh shell from
this working tree before interactive verification; do not infer behavior from a pre-change process.

## Blockers

No memory, infrastructure or one-episode benchmark blocker is known. Commit authorization has been granted for this
integration, but push authorization has not. Preserve the concurrent provider and V2.7 async-add changes. Archived
memories created by an older direct-update implementation without a real
`DERIVED_FROM` edge cannot be reconstructed into history and must not be guessed. Keep the structured feedback
exact-record/content/lineage terminal gate when changing the vendored executor. Optional Playwright/MCP environment
failures and unrelated cleanup-guard expected-set drift remain outside this follow-up.
