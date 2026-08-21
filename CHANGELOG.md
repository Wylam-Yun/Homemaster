# Changelog

## Unreleased

### Fixed

- Fixed the Web Console silently completing a turn when a disconnected approval failed closed and the
  runtime returned an `ask_user_question` prompt in `runtime.turn_completed.question`. The public projection
  now emits that exact question as `answer.snapshot` before `run.completed`, while ordinary
  `assistant.reply`/`final_reply` handling remains non-duplicating. A real four-approval Web protocol run
  returned HTTP 200 for every decision and independently finished the fixed ALFWorld episode with
  `won=true`, `done=true`, the statue still held, and zero invalid actions.

### Added

- Added inline Web previews for authorized `image/*` tool artifacts. Each image stays inside its producing tool
  card, opens in a keyboard-accessible viewport-bounded lightbox, restores focus on close, retains an original
  artifact link, and falls back to the same authorized link when either image load fails. Non-image artifacts and
  the backend artifact protocol remain unchanged. A real Chrome/ALFWorld run rendered six inline 300x300 PNGs,
  verified Escape/backdrop/button close behavior at desktop and 375px mobile widths, read the artifact with HTTP
  200 and nonblank pixels, and independently finished with worker `won=true`, held `statue 1`, and zero invalid
  actions.

- Added `homemaster serve --alfworld` so the existing React/FastAPI Web Console can run the configured
  fixed ALFWorld episode through the same application wrapper, tool profile, environment dependencies,
  session owner, `ToolExecutor -> PermissionChecker`, and one-shot Web approval Future as other entries.
  Ordinary `serve` remains unchanged and loopback-only. A real Web HTTP/WebSocket run approved five
  independently correlated mutations, completed the statue/floorlamp task, and the authenticated worker
  state endpoint independently returned `won=true`, `done=true`, the held statue, and zero invalid actions;
  five public PNG artifacts independently decoded nonblank with matching response, event, and byte hashes.

- Added exact Feishu tool-confirmation cards for permission decisions that return
  `requires_confirmation=True`. One application-owned handler now binds an opaque approval to the requester,
  source chat, original card message, session, and Gateway generation; deny, timeout, cancellation, session
  replacement, restart, and shutdown fail closed before resource/backend execution, while callbacks create no
  inbound message or model turn. Successful API responses without one real message ID fail closed; blocking audit
  writes are deadline-bounded, and cancellation-resistant late REST sends retain a tracked owner so their cards are
  reconciled to an expired/closed terminal before shutdown can report complete. Focused black-box coverage proves
  approve mutates isolated external state exactly once and deny leaves it unchanged. The current trusted
  `feishu-owner` still has `tool.auto`, so production policy
  bypasses this gate. A safe live harness confirmed two sends, two same-card patches, and two independent message
  readbacks with Feishu business code 0; each card read back as terminal with no action value. Because the concurrently
  running legacy Gateway may consume the same app's WebSocket events, neither callback reached the harness, so the
  callback identity loop and approve-backend exactly-once live path remain `UNVERIFIED`.

- Added `homemaster serve`, a loopback-only FastAPI + React Web Console with session creation/resume,
  HTTP command and cancellation endpoints, one session-scoped reconnecting WebSocket, streamed thinking and answer
  deltas with snapshot calibration, per-`tool_call_id` tool cards, opaque artifact downloads, and one-shot dangerous
  operation approval. The browser reducer fences every request to its authoritative run ID, and production packaging
  includes the Vite build plus required DeepSeek Harness and Hermes Agent MIT notices.

### Documentation

- Defined the Web inline-image artifact design: authorized `image/*` artifacts stay inside their producing
  tool card, open in an accessible viewport-bounded lightbox, retain an original-artifact link, and fall back
  to the existing link row on load failure. Added the task-level TDD, packaging, documentation, and real
  ALFWorld/browser verification plan. The design changes no backend protocol or artifact authorization.

- Defined the Web ALFWorld composition contract and implementation plan so
  `homemaster serve --alfworld` reuses the existing ALFWorld application wrapper, shared
  `ApplicationRuntime`, permission gate, and Web approval protocol instead of creating a
  transport-specific execution path. The plan preserves loopback binding and fixed-episode
  single-session ownership, and requires a real `won=true` browser approval gate before release.

- Documented the approved unbounded Dreaming wait design: HomeMaster will stop cancelling consolidation at a fixed
  wall-clock deadline, retain durable pending/recovery state and explicit cancellation semantics, and verify the
  external finalization terminal without changing unrelated Provider, Agent, benchmark, or tool timeouts.

- Recorded the formal HPC2/hkust4 portability gates, the editable MindMemOS cross-venv fix, and the one-instance
  LoCoMo and visual ALFWorld external termination evidence in the session handoff.

### Changed

- Allowed frozen Provider requests to retry after transports deterministically omit historical images. Retry safety
  continues to require an incomplete response, no visible non-reasoning delta or committed assistant/tool/external
  action, and an identical serialized request hash; normal historical-image omission no longer disables retries for
  later visual turns.

- Removed HomeMaster's fixed 300-second Dreaming cancellation. Session finalization now waits for the native
  consolidation pipeline's real terminal result while preserving explicit caller cancellation, durable pending/startup
  recovery, terminal verification, and duration events; unrelated Agent, Provider, benchmark, and tool timeouts remain
  unchanged.

- Added opt-in local approval to the interactive CLI through
  `--permission-mode full_auto|confirm|plan`, while preserving `full_auto` as the default for automation and leaving
  one-shot, dry-run, Gateway, and benchmark paths unchanged. `confirm` reuses the canonical permission checker and
  prompts only for mutating tools not already allowed; denial occurs before resource acquisition/backend invocation,
  concurrent prompts are serialized, and requested/completed decisions enter the runtime trace. Rich tool progress now
  yields terminal ownership before the approval prompt so `Execute? [y/N]:` remains visible and interactive.

- Changed model-visible `mindmemos_add` from admission-only `accepted + job_id` to verified
  `stored + memory_id`: the call waits only for exact Memory, BM25 and Neo4j provenance readback, while two
  application-owned workers enrich the same ID with dense vectors and native Entity/`MENTIONS`. Internal pending state
  is neither model input nor model output. Session Finalizer now enables Entity in both the Vanilla extractor and config,
  allowing feedback/Dreaming benchmarks to receive graph scopes without delaying interactive Add on remote embeddings.
- Unified semantic session completion behind `application.session(...)`. Interactive Shell, one-shot CLI, ALFWorld and
  LoCoMo now close the same idempotent session scope, which admits the existing Session Finalizer to the
  application-owned FIFO without waiting. `/new` can start the next session immediately; graceful application shutdown
  drains accepted finalization before MindMemOS and Neo4j close. `ApplicationRuntime.run()` remains one turn and never
  infers session completion. Gateway messages remain turns until that entry has an explicit reset/expiry event. A real
  visual ALFWorld episode now finishes with formal success and persists session-derived active memories with verified
  Qdrant/Neo4j lineage before clean resource shutdown. The final integration candidate passed one frozen
  `valid_unseen` visual episode with external `won=true`, all ten PNG frames independently decoded and non-blank, and
  seven of seven Finalizer memories independently read back as active with nonzero 4096-dimensional vectors and graph
  provenance.

- Routed the ALFWorld benchmark through the canonical HomeMaster application composition so visual episodes use the
  same embedded MindMemOS, FIFO Add queue, automatic recall, evidence ledger and managed-Neo4j lifecycle as other
  entries. The legacy ALFWorld `memory_mode` remains disabled only to prevent the obsolete second writer. Benchmark
  runtime/session roots can now be isolated explicitly, and memory/Neo4j/Java paths written relative to YAML resolve
  against that config file instead of the process cwd, making the harness portable between machines. Worktree-local
  `.runtime/` assets are ignored to prevent Java, Neo4j binaries or live database state from entering a commit.

- LoCoMo source conversations use finalizing session scopes, while QA probes use ordinary one-turn runs so evaluation
  answers cannot be admitted into the production-style Session Finalizer memory. A one-source/one-probe live run on
  `hkust4` completed with only the source session admitted; both persisted memories were independently active.

- Changed `mindmemos_search` to accept and return the complete native MindMemOS type vocabulary instead of the reduced
  `fact|procedure` search view. Valid active `profile`, `fact`, `experience`, `episodic`, `tool_trace`,
  `skill_candidate`, and `file_knowledge` memories now preserve their native type and exact content; the existing LoCoMo
  store returns both Finalizer-created tool traces by their real IDs with no corruption diagnostics. Model-authored
  `mindmemos_add` remains restricted to evidence-backed `fact|procedure` writes.

- Replaced model-visible structured `mindmemos_add` with `content + memory_type` direct flat writes
  because its Schema/LLM extraction dominated Add latency while the public schema is not yet stable.
  The initial write stores exact content, BM25, Qdrant Memory and Neo4j Source/`EXTRACTED_FROM`
  directly, with no chat call. Facts map to native `fact` and procedures to `experience`; legacy structured memories
  remain readable/updateable. Dense and Entity enrichment now follow the stored-first contract above. The 100-record recall benchmark writes its canonical
  JSON as exact flat content and verifies raw content/type instead of requiring `record_json`.

- Rewrote the top-level README from scratch: capability overview, architecture diagram and module
  table, quickstart, CLI reference, run modes, memory/skills/MCP/security/benchmark sections with
  links into the existing user guides, an honest current-boundaries section, and no machine-local
  absolute paths. All referenced files, tool names, and CLI commands were re-verified against the
  current code; the public memory tool count now matches the seven registered tools
  (`context_memory`, `mindmemos_search/history/add/update/delete/feedback`).

### Fixed

- Fixed idle browser disconnects leaving outbound-only WebSocket handlers blocked forever on the event
  queue. The stream now races event delivery with the ASGI receive channel and always cancels/joins the
  losing waiter. A real idle disconnect followed by one `SIGINT` completed FastAPI, ALFWorld worker, Unity,
  Xvfb, display-lock, and listener cleanup without a traceback.

- Fixed the portable ALFWorld launcher failing at real memory startup when MindMemOS is installed editable in the
  formal HomeMaster environment. The launcher now resolves that source root through the bound formal Python, adds it
  to the dedicated ALFWorld process, and verifies the complete cross-environment import closure before starting THOR.

- Extended one-time ALFWorld setup to bind and validate the external config/dataset root alongside its dedicated
  Python environment. Portable benchmark commands now use `.runtime/alfworld` and no longer depend on an old
  HomeMaster worktree or machine-specific ALFWorld path.

- Kept the portable launcher's successful runtime preflight off stdout so JSON CLI commands emit exactly one
  machine-readable payload; preflight failures still retain their nonzero status and diagnostics.

- Upgraded the memory migration manifest contract to v2 so a portable runtime accepts logical mount aliases that
  resolve to the same data root and can safely open historical v1 manifests from either the Mem0 four-component or
  MindMemOS two-component era. The locked upgrade validates the original contract, preserves immutable v1 audit
  copies, and still rejects unknown shapes, genuinely different roots, missing published targets and audit conflicts.

- Fixed stored-first memory enrichment falsely rejecting successful Qdrant dense-vector writes
  when float32 persistence introduces normal round-trip precision differences; the readback gate
  now checks dimensions, nonzero content, and per-value numerical tolerance before Entity enrichment.
- Prevented one-shot and child-worker shutdown from admitting Session Finalizer work when the FIFO exists but embedded
  MindMemOS did not become available. Admission now requires both queue readiness and memory-runtime readiness, so an
  optional memory startup failure cannot append a late traceback to an otherwise valid CLI result.

- Made the ALFWorld episode prompt expose the real `look_at_obj_in_light` goal semantics: the target must be held while
  the named lamp is turned on. A complete visual Mimo run had valid provider/runtime/harness coverage but scored zero
  because the model treated navigation plus `observe` as completion even though ALFWorld remained `won=false`.

- Stopped the ALFWorld episode prompt from interpreting legacy `memory_mode=disabled` as canonical memory being
  unavailable. The benchmark entry already requires embedded MindMemOS and its FIFO; prompts now accurately expose
  automatic recall and registered memory tools while the legacy flag continues to disable only the obsolete writer.

- Prevented repeated Ctrl+C from aborting interactive Session finalization or application close.
  During terminal exit drain the shell temporarily owns SIGINT, reports the first repeated interrupt,
  and continues draining flat Add jobs, queued Session finalizations and owned resources before
  restoring normal run-cancellation behavior. Background `/new` finalization does not consume a
  Ctrl+C intended to cancel the current run.

- Made structured feedback updates replace the complete canonical record instead of changing only
  display content while retaining stale `record_json`. Structured feedback now reuses the
  deterministic versioned writer and verifies canonical content, exact record metadata, archived
  old/active new states and `DERIVED_FROM`. Memory add/update tools no longer expose or accept
  opaque evidence refs; the Runtime selects current tenant/session/run/turn evidence internally,
  preventing a model from reusing an evidence token from an earlier run.

- Bound the runtime's authoritative working directory into the existing system prompt so the model
  can distinguish the current workspace from a separately named project or environment. Canonical
  tool results now retain their provider projection through the migration adapter, exposing status,
  return codes, cwd and structured errors in actual Anthropic/OpenAI tool-result content while
  preserving the established memory-result and internal metadata shapes.

- Changed provider retry from a single closed error whitelist to up to eight total frozen-request
  attempts for provider failures. The first retry is immediate, subsequent retries use 3/6/12/24/48/96
  second backoff, and retries still stop after visible output, tool or external commits, request-hash
  drift, or the shared run deadline.

- Kept the legacy-term cleanup guard focused on product architecture by explicitly exempting the
  real MindMemOS recall boundary test and third-party logging boundary test, where `pipeline` is
  part of an external typed API or dependency vocabulary rather than a HomeMaster legacy layer.

- Distinguished a MindMemOS backend-raised `TimeoutError` from expiry of the shared run deadline,
  so a backend timeout remains a best-effort automatic-recall error while an actually exhausted
  run budget still prevents a Provider request with no time remaining.

- Retried one frozen provider request when a transient stream failure follows only hidden reasoning,
  while continuing to block retries after visible text, tool calls, completion data, or committed
  effects. Empty SDK error messages now fall back through the exception chain and exception type.

- Made validated HomeMaster `FactRecord` and `ProcedureRecord` types authoritative across the MindMemOS LLM extraction
  boundary. Entity-generation output is now deterministically projected from request-scoped `record_json`, fact identity
  uses the complete `<subject>::<predicate>`, and cross-identity semantic entity merge is disabled. This prevents
  procedural-looking fact values from being reclassified as `task_experience` or merged into a nearby fact before raw
  terminal readback.

- Prepared the Browser Gateway branch for main integration by restoring Ruff-clean configuration
  exports and teaching the legacy-term cleanup guard to exclude the intentional Browser benchmark
  trajectory vocabulary and vendored third-party sources. This keeps the guard focused on HomeMaster
  product architecture without weakening its coverage of ordinary product files.

### Added

- Added a portable per-checkout memory runtime setup and launcher. One server-local setup binds the HomeMaster Python
  environment, an optional separate ALFWorld/Torch environment, Java, Neo4j and an existing or new memory root into
  ignored `.runtime` paths, rewrites the ignored private YAML to stable relative paths, and refuses conflicting
  bindings. `scripts/homemaster` then fixes the config, dependency paths and cwd for every command, so migration does
  not require repeated exports or machine-specific code changes.

- Added a `benchmark-locomo` full-application pilot that replays 50-100 dated LoCoMo
  dialogue turns with original speaker names through the ordinary HomeMaster runtime,
  production Session Finalizer, implicit feedback, dreaming, automatic recall, and
  model-visible memory tools. It keeps one focal speaker's memories under a normalized
  tenant key, applies a per-run deadline, verifies every Finalizer memory by raw ID, and
  writes complete JSON/JSONL artifacts for manual feature inspection without claiming a
  QA quality score.

- Added `file_read_example.py` as a standalone UTF-8 file-reading example with explicit
  missing-file and unexpected-error handling.

- Added one unified `mindmemos_update` dispatch boundary and `mindmemos_history`. Memories with a
  valid `record_json` now receive a deterministic linked version that updates content, structured
  metadata, memory/entity vectors and Neo4j relationships without re-running Schema Add; Vanilla
  memories continue to use native in-place update. Corrupt structured metadata fails closed, and
  history returns active and archived Qdrant records connected by `DERIVED_FROM`.

- Added V2.6 memory self-correction: one `mindmemos_feedback` tool binds the successful provider
  attempt's frozen visible recall context, revalidates every raw target, and exposes per-action
  terminal receipts. Session finalization now resumes independently across Vanilla Add, native
  implicit feedback, and a persistent per-project/user dreaming batch; the configurable default
  threshold is eight confirmed ordinary additions, failures retain pending work, and only verified
  raw/lineage/consolidation terminal states consume a batch. Vendored MindMemOS now returns truthful
  typed feedback/dreaming outcomes and sends non-empty user messages to the Mimo/Anthropic route.

- Added the Hermes-style dual search surface: `terminal` lets the model choose the complete command, while
  `search_files` selects `rg`/`grep`/`find` in the execution environment and runs through the real process-group
  timeout and cancellation supervisor. Renamed the model-facing `bash` entry to `terminal` and removed the
  model-visible Python `grep`/`glob` entries so large searches no longer block the HomeMaster process behind an
  unused timeout field.

- Added V2.5 automatic experience recall before the first Provider request of a new Session and the
  first real user turn after completed compaction. A durable generation-fenced Session latch drives
  one direct MindMemOS Vanilla Top-3 search with no type filter; native results are injected once as
  run-scoped model context without fake tool messages. Empty, unavailable, and ordinary-error paths
  remain best effort, Provider retry reuses the frozen context, and manual, threshold, and reactive
  compaction re-arm only the next user run.

- Added a reproducible 100-record memory recall benchmark that writes synthetic website-operation facts through the real
  serial `homemaster -p` surface, checkpoints every externally confirmed mutation, and separately scores exact retrieval,
  paraphrased retrieval, distractor discrimination, and natural memory-tool routing. Synthetic procedures remain typed
  as user-stated facts, private JSONL artifacts preserve per-instance evidence, confirmed records are never rewritten on
  resume, and the unattended `overnight` command gates 100 exact recall calls on 100 confirmed writes. Test records are
  retained by explicit request, and outcome-unknown writes are never automatically retried.

- Added an optional HomeMaster-managed private Neo4j lifecycle for embedded MindMemOS. Multiple HomeMaster processes on
  one node share the service through a file lock and per-process leases: the first starts it, stale leases are recovered,
  and the last clean exit stops it. Managed startup now precedes MindMemOS startup, failures block entry into the shell,
  doctor remains read-only and redacts the YAML password, and `external` mode preserves caller-owned Neo4j behavior.

- Replaced the application-owned `Mem0MemoryStore` path with embedded MindMemOS native schema pipelines while keeping
  the six public memory tool contracts and SOUL/USER/MEMORY file ownership unchanged. Facts map to MindMemOS `fact`;
  procedures map to `experience` with the complete HomeMaster record preserved in metadata. Add/search/get/update/delete
  now return raw reusable memory IDs and verify raw terminal state. Added HomeMaster fact/task-experience schemas, a
  compact extraction prompt honored by the vendored pipeline, nested request-metadata decoding, application lifecycle
  wiring, and real Qdrant/Neo4j/chat/embedding CRUD coverage. Removed the mem0 runtime, dependency packaging, integrity
  manifest, configuration, tests and Qdrant/history migration branches because old mem0 data intentionally starts fresh
  and is not migrated; legacy SOUL/USER/MEMORY file migration remains supported.

- Added `homemaster --gateway --browser`, preserving the existing Feishu Channel and Home general
  tools while injecting a run-scoped, origin-restricted Playwright session. The browser surface now
  includes semantic inspect/actions, mandatory post-action `observe`, and `browser_backfill`, which
  pastes a current-page PNG into an editable evidence control only when the page accepts the paste
  and renders a preview with the exact same SHA-256. Delayed disallowed main-frame navigation is
  aborted before dispatch and fences the session. Browser runs have no tool-iteration limit, without
  adding a Browser-specific numeric budget. Added one generic `change-ticket-executor` Skill whose SOP is read dynamically
  from the ticket URL, plus separate complete normal and anomaly/rollback GT for the Ant Design Pro
  Mock UI demo; ticket digest and per-`sop_step_id` provenance are checked against the source, while
  unexecuted full-normal and rollback UI branches stay `UNVERIFIED`. The independent live Mock gate
  now crosses authenticated Feishu DTO, ChannelBridge, BrowserGatewayApplication and ApplicationRuntime
  in one run, verifying four per-field DOM values, command preview,
  `SUCCESS (exitCode=0)`, nine observation images, screenshot preview and confirmed backfill, nonempty
  JSONL/trace/WebM artifacts, and browser cleanup. Browser and ALFWorld Gateway modes are mutually
  exclusive; no Feishu Channel implementation was changed.

- Added `homemaster --gateway --alfworld`, which keeps HomeMaster/Gateway in the
  project environment and starts the existing ALFWorld environment through a
  small loopback HTTP worker in its own configured Python environment. The
  Gateway owns one fixed episode and one exclusive session, forces a real
  `observe` call after every attempted navigation/manipulation backend action,
  and sends the same observation image to the model and Feishu. Feishu progress
  now shows an optional model-created task plan, semantic navigation/manipulation
  updates, their correlated images, and model replies; internal usage updates,
  thinking, raw tool lifecycle events, and observation-protocol corrections that
  never reached the backend stay in JSONL only. Model-authored reply text is
  forwarded byte-for-byte without trimming. Ordinary Gateway,
  ALFWorld, and Coworker compose explicit environment-specific tool registries.
  `alfworld_gateway.allow_offscreen_object_navigation` now preserves that V1.8
  point-navigation behavior by default while allowing memory-search experiments
  to reject strict-invisible non-receptacles before any THOR action; offscreen
  receptacles remain navigable. The managed worker reports the canonical policy
  in readiness and health, persists every runtime THOR action event for an
  independent external-state gate, and fails closed on frozen/current receptacle
  metadata drift. HomeMaster structured memory remains controlled by
  `memory.enabled` rather than the legacy benchmark `memory_mode`; successful
  environment tool results now include only their opaque run-bound memory
  evidence refs in actual model-visible content, so the model can legally call
  `add_memory` without receiving object IDs, containment, poses, or trace data.

- Made HomeMaster self-contained for server migration: the complete verified `mem0ai==2.0.13` runtime and Apache-2.0
  license are vendored into the repository and wheel with per-file integrity enforcement, while the separate `mem0ai`
  distribution dependency is removed. Deleted the unused `src/openharness` package and upstream-only tests/generator,
  archived their immutable provenance, and retained HomeMaster-owned tools, Skills, behavior tests, and MIT attribution.
  Persistent memory now derives only from configurable `memory.data_root` (`files/qdrant/history/evidence`); one
  journaled coordinator performs recoverable component migration from legacy paths before stores open, the explicit
  `homemaster memory migrate` command returns typed receipts, and doctor reports migration state without writes.
  Completed manifests now fail closed when published targets are missing or structurally invalid, migration locks and
  rechecks legacy file sources before publication, SQLite copies use consistent snapshots, and doctor verifies vendored
  mem0 bytes without executing mem0 or opening/materializing the backend.

- Removed duplicate structured-memory retrieval in `mem0ai==2.0.13`: HomeMaster now calls the public hybrid
  `Memory.search()` once and merges its results only with metadata-exact matches, instead of invoking a second Qdrant
  BM25 search. `mem0ai[nlp]` and the locked `en_core_web_sm==3.8.0` model are project dependencies, so the public API's
  lemma/entity paths load without warnings or a first-search download. The model uses a PEP 508 direct URL in wheel
  metadata, so HomeMaster wheels remain installable outside the source checkout. Hybrid results use one truthful source
  label.

- Made Anthropic streaming tool calls use the SDK's final `tool_use.input` as the authoritative parameter source while
  preserving live text/thinking deltas. Memory tools now derive model-visible and runtime-validated inputs from Pydantic
  models, expose complete fact/procedure schemas, route user preferences and long-term health schedules to `USER.md`, and
  strictly decode Mimo's JSON-encoded nested `record` object before full typed validation. The structured-memory search
  contract now asks for one query per current request because exact and mem0 hybrid retrieval are already
  merged inside that single tool call. Frozen file-memory blocks now use semantic identity/profile/memory headings rather
  than path-like `.md` headings. The model-facing file-memory tool no longer exposes a redundant `read` action; writes
  retain independent disk readback through the internal store boundary.

- Packaged the locked offline `Qdrant/bm25` artifact and materialized it atomically into the persistent project
  FastEmbed cache (`.cache/homemaster/fastembed` by default). Both HomeMaster's preflight and mem0/Qdrant now use
  that cache, so deployments no longer depend on volatile `/tmp/fastembed_cache` or a first-run model download.

- Fixed all six V2.1 memory tools to project their complete structured result into model-visible tool-result content,
  instead of exposing only a success sentence while retaining records and IDs solely in internal message data. This
  makes add IDs, search records, get/update payloads, delete receipts, and file-memory state readable by the next model
  iteration; an ApplicationRuntime regression now parses recalled records from the actual tool message.

- Added the V2.1 layered memory system: frozen SOUL/USER/MEMORY Markdown, six canonical Home memory tools, typed
  fact/procedure records, run-bound evidence, `mem0ai==2.0.13` with embedded Qdrant, exact + semantic + offline BM25
  retrieval, corrupt-record diagnostics, fail-closed outbound policy, privacy-safe JSONL, timeout-owned mutation
  fencing, backend doctor status, independent mutation readback and clean process reopen. The stable PyPI
  wheel is intentionally used even though unused backend files differ from the Git tag; selected Qdrant/core paths
  match. Legacy object-memory tools remain benchmark-only. This changes the default Home tool surface and adds local
  private memory files plus SiliconFlow embedding traffic; see `docs/memory-user-guide.md`.

- Added the gateway-only shorthand `homemaster --gateway --config <path>`. It delegates to the
  existing Feishu/Lark Gateway lifecycle without creating an interactive shell or a second
  application runtime; `homemaster gateway --config <path>` remains supported.
- Made Gateway `SIGINT`/`SIGTERM` enter its deadline-bounded Runtime shutdown path so the spawned
  Feishu WebSocket worker is stopped and joined instead of surviving as an orphan process.
- Documented the pending V2.1 generic-browser-tools and memory-system implementation plans, including
  the browser plan handoff, locked design decisions, validation gates, and current blockers. These
  documents do not claim that either V2.1 feature has been implemented.
- Fixed Coworker external tool routing so browser receipts, terminal verification, SOP decisions, and task-plan
  mirror events consistently use the Case02 `coworker-...` domain run ID rather than the generic application
  `run-...` ID. This restores real environment calls after the unified runtime began assigning its own run IDs.
- Replaced the duplicate model-visible `skill` and `skill_view` entry points with one explicit
  `load_skill(name=...)` tool across Home and Coworker profiles. Available Skills continues to preload only names
  and descriptions; complete Skill Markdown enters model context only after `load_skill` is called, while user
  slash invocation remains unchanged. The deterministic Coworker presentation gate now uses the same tool contract,
  with a Registry compatibility regression preventing scripted calls to removed tools.
- Clarified that enabling the Feishu Gateway does not start a daemon or configure boot-time recovery, and documented
  the currently verified manual `PYTHONPATH=src uv run homemaster gateway --config config/homemaster.yaml` command
  consistently in both README Gateway sections. A live configured Gateway reached a persisted `replied` session,
  and the owner confirmed normal Feishu message delivery and readback.
- Replaced raw Pydantic tool-input failures with one structured, source-neutral result containing the stable error
  code, tool name, received argument keys, missing required arguments, validation issues, and
  `backend_attempted=false`. The tool boundary no longer guesses at Provider causes or suggests retries and
  alternative tools.
- Fixed Home profile `bash` on macOS by preserving the OpenHarness platform split: Linux may use the
  `script -qefc` PTY wrapper, while macOS executes through `bash -lc`. Platform-specific argv regressions and a
  real macOS process gate now prevent Linux-only compatibility assumptions from disabling every shell command.

### V2 workspace checkpoint

- Preserved the complete accumulated V2 workspace in one checkpoint: universal ordinary-name tool execution,
  repository Skill relocation into `.homemaster/skills`, the still-unsplit raw-output/event/renderer changes, and
  the V2.1 memory-system discussion. This checkpoint records the current tested working state; it is not a claim
  that every included line belongs to the universal-tools release boundary.

### V2.0 Skills, exact output, and Rich closeout

- Made HomeMaster install GitHub repository and `blob/.../SKILL.md` sources transactionally through bundled
  `skill-creator`: clone once, preflight every conflict, stage on the target filesystem, atomically publish, roll
  back failures, and verify every name in a fresh Registry. A real HomeMaster run installed the complete 14-Skill
  Superpowers tree plus `find-skills`; independent relative-file/SHA-256 comparison and universal `skill` calls
  passed for all 15 directories without changing `~/.codex/skills`.
- Locked candidate 2 across CLI, events, Gateway, config, MCP, hooks/extensions, traces, Feishu SDK logs and service
  representations: authenticated and authorized selected runtime text remains exact. Event allowlists, tenant ACLs,
  invalid-auth non-echo, binary opaque references, and placeholder-only tracked config remain unchanged. Recursive
  canonical result data is thawed before session/model copying, and CLI plus interactive entrypoints now honor the
  configured tool-iteration budget.
- Kept Rich concise without changing machine contracts: complete Bash commands render literally, success bodies are
  omitted, and failure details are bounded to 500 characters with an explicit truncation marker. Focused raw/Rich
  gates (`188` raw/event, `27` Rich matrix, `10` CLI/PTY), first-byte text/JSON/stream-JSON tests, and 48/120-column
  PTY Bash runs pass; full `tests/homemaster` passes (`1158 passed, 1 skipped`). Real Feishu message create
  returned business code 0 and an independent new-client message get read back the unique exact canary once.
- Remaining external scope is explicit: Feishu media, reaction, group operations, reconnect and the `lark` domain,
  plus real ALFWorld, remain `UNVERIFIED`; they are not part of the verified message/raw-output closeout claim.

### V2.0 universal tool execution

- Replaced the active application `ToolProfile -> ToolView -> ToolCatalog -> ToolExecutionPipeline` route with one
  ordinary-name `ToolRegistry -> PermissionChecker -> ToolExecutor` path. `RunRequest.enabled_tool_ids` was removed;
  Home, ALFWorld, Coworker and dry-run now compose the same universal Registry, while environment selection only
  binds the runtime Backend.
- Made bulk Registry registration atomic and moved MCP, Feishu group and approved extension tools onto that same
  Registry. Hidden `homemaster.<name>.v1` IDs remain diagnostic metadata and duplicate ordinary names fail
  composition.
- Preserved command/path rules, principal capabilities, session plan mode, deadlines, cancellation and resource
  leases on the new executor. Importing the application factory no longer loads the legacy Catalog or Pipeline.
- Final review remediation makes Registry composition fail closed on every undeclared ordinary-name collision and
  validates the exact source set and winner for intentional adapter overlaps. One deadline now covers resource-lease
  acquisition, backend execution, and lease release; mutating timeout, exception, and cancellation after backend
  start report `outcome_unknown` with `backend_attempted=true`. Cancellation retains that uncertainty in the run-local
  event result without publishing stale-generation events or committing run-local session/task state.
- Moved application-owned background tasks into independent process groups so stop and close terminate the real
  child workload, not only its shell. The release gate now inspects a cleanly rebuilt wheel and verifies from an
  isolated installation that the removed modules and `openharness` package are absent.

### V1.9 Generic screenshot observe

- Replaced environment-specific observation state machines with one `core.observe.v1` tool shared by Home,
  ALFWorld, and Coworker. `observe({})` returns exactly one current PNG image to the model with no text, DOM,
  state, or observation-binding metadata, and screenshot calls no longer authorize or gate actions.
- Removed the retired `ObservationService`, legacy benchmark observe registry factories, ALFWorld
  `FrameLedger`/model-view authorization path, provider-attempt binding fields, and inactive Coworker
  failure/presentation compatibility paths. Generic image transport hashes remain available for internal validation
  and audit; Coworker `TICKET_READ` is now grounded only by ticket navigation, never by a screenshot.
- Fixed the Feishu artifact path so media persistence no longer rewrites provider-facing tool results. An `observe`
  screenshot remains the single image sent to the model while the Gateway public event independently receives a
  tenant/session/run-scoped artifact reference for Feishu delivery.

### V2.0 OpenHarness Skills and default tools

- Replaced HomeMaster's capability-bearing Skill model with OpenHarness-compatible instruction documents. Skills
  no longer require `tool_names`, cannot grant tools or permissions, dynamically discover Home-owned user/project
  roots, and expose complete Markdown through `load_skill`, Available Skills context, and slash invocation. The
  earlier duplicate `skill` and `skill_view` model tools were removed before release. Eight upstream bundled Skills
  are included in installed wheels.
- Ported the locked OpenHarness 39/39 default tool surface into the Home profile, including files, process, network,
  LSP, images, config/plan, Cron, tasks, child agents, teams and MCP configuration. Application-owned services,
  immutable path resolution, per-resource verification leases, real return codes and structured audits preserve
  Home's permission and terminal-state boundaries; ALFWorld and Coworker tool surfaces remain unchanged.
- Added durable remote `ask_user_question` waiting/resume without duplicate terminal output, Cron lifecycle CLI,
  and a default child worker that explicitly inherits the parent config path. Added a real installation gate for
  Git clone/checkout, `skill-creator`, archives, Python/Shell scripts, isolated Python/npm dependencies, HTTPS plus
  independent curl hash, dynamic same-process discovery, and second CLI process discovery.
- Fixed package data so all bundled Markdown survives wheel installation, and retained the locked upstream source,
  Skills, tools and tests at commit `9b2efd795c6aa09f88b0c257d269a9e518da6ae7` with a checkable port manifest.
- Config display remains permission- and schema-bounded while candidate 2 preserves selected provider/MCP
  credentials, URL userinfo and configured literals exactly. Cron, config, MCP management and
  task/agent/team tools now require independent `scheduler.manage`, `config.mutate`, `mcp.manage` and
  `process.spawn` capabilities in addition to generic tool permissions.
- Added explicit data-only Plugin Skill discovery for `plugin.json` and `.claude-plugin/plugin.json`, including
  enablement overrides, project opt-in, contained `skills_dir`, symlink/traversal rejection and named builtin
  override authorization. Plugin Python, tools, hooks and MCP are never imported by this adapter.
- Fixed installed-wheel Home profile startup by promoting Pillow from the Coworker extra to a core dependency and
  loading MCP-only upstream adapters only when an MCP manager exists. The isolated wheel gate now installs declared
  runtime dependencies and instantiates all 39 OpenHarness default tools outside the checkout.

### Single-Feishu OpenHarness Gateway migration

- Added direct `gateway.feishu.app_id/app_secret` loading from the ignored mode-0600 YAML requested by the
  deployment owner. The pair is file-first, cannot be mixed across YAML/environment sources, uses `SecretStr`,
  and is stored as typed secret configuration; tracked examples contain placeholders only.
- Migrated the Gateway composition from Telegram to one Feishu/Lark channel using `lark-oapi`, with deterministic
  message dedup, typed reply/thread context, safe inbound attachments, static text/post/card/table rendering, and
  image/audio/video/file delivery. Per deployment-owner decision, the Feishu transport is a fully trusted entry:
  every non-bot sender receives one fixed owner principal, and no bot/user ID, allowlist, or mention is configured.
  Localized post/real card inbound parsing and OpenHarness-complete Markdown link/multi-table rendering remain known
  migration gaps; current support is limited to the tested simplified payloads.
- Added application-owned `FeishuApiService` and typed create/rename group operations. Exact group capabilities
  are checked by the canonical permission policy; member/chat targets come only from the authenticated route,
  operation ids lock targets, timeouts remain `outcome_unknown`, and success requires an independent state read.
- Added typed delivery receipts and MEDIA artifacts. Tool bytes are persisted without modifying provider-facing
  content; public events expose only tenant/session/run-bound opaque handles, and each media item remains an
  independent non-coalescing outbound event.
- Fixed Feishu dedup to reserve before processing but commit only after inbound publish; parse/download/persist/
  reaction/bus failures now release the claim and clean uncommitted attachments so platform redelivery can recover.
- Fixed repeated Feishu `bot_p2p_chat_entered_v1` and `message_read_v1` delivery failures by registering both
  subscribed non-business events with explicit no-op ACKs. Entering a bot chat or reading a message now succeeds at
  the platform dispatcher without creating an inbound message, invoking HomeMaster Runtime, or sending an
  unsolicited response; ordinary message events keep their existing single-packet ingress path.
- Fixed real Feishu private messages being silently discarded after platform ACK because the SDK emits
  `chat_type=p2p` while HomeMaster's channel contract uses `private`. The transport now normalizes the exact external
  value at its boundary, keeps unknown chat types rejected, and proves a real SDK-format message reaches the private
  inbound bus and `open_id` reply route.
- Isolated the SDK WebSocket client in a terminable subprocess because the installed SDK exposes no verified
  public stop API. Fatal/completion state reaches the supervisor, and one shutdown deadline covers active runs,
  outbound drain, channel termination, and service joins. SDK logs preserve exact runtime text under candidate 2;
  structured API audit records remain field-allowlisted.
- Breaking configuration migration: `gateway.telegram` and the `python-telegram-bot` gateway extra are replaced
  by the sole active `gateway.feishu` configuration and `lark-oapi`. Telegram source/tests remain for historical
  compatibility but are not composed, documented, or installed by the Gateway extra.
- The installed SDK returned endpoint HTTP 200/business code 0 and completed a real Feishu WebSocket
  handshake/close. A real private text message passes platform ACK, SDK dispatch, canonical routing, session
  persistence and confirmed send. Closeout additionally verified message create with business code 0 and an
  independent message get whose content exactly matched the unique canary. Media, reaction, group terminal state,
  reconnect behavior, bot self-event semantics, remaining API symbols, and the `lark` domain remain `UNVERIFIED`.

### Realtime CLI streaming

- Changed the generic agent runtime to publish exact text deltas immediately before
  provider completion while retaining the same authoritative aggregate and persistence path.
- Added the adapted OpenHarness Rich renderer plus live text and JSON-lines sinks over the
  existing exact seven-event public contract.
- Kept buffered JSON as one document; `stream-json` now flushes ordered event objects and one
  final HomeMaster result. Live text/Rich entrypoints no longer echo the final answer twice.
- Preserved Gateway terminal-final ownership and blocked retry/compaction after a committed
  partial delta. This fixes a user-visible regression where provider streaming tests passed
  although every CLI consumer waited for the complete response.

### Verification

- V1.9 final-review remediation concentrated verification passed: full non-live `1295 passed, 11 deselected`,
  focused review regression `193 passed`, runtime stress `5 passed`, Ruff check/format, compileall and diff gates.
  Real Mimo API (`3 passed`), MCP stdio/HTTP (`21 passed`), device audit/file (`27 passed`, fake backend) and
  extension filesystem/reload (`32 passed`) gates also passed.
- Candidate `800d391dd780fd13e5d3116bb269a99b9b975474` executed the locked four real THOR episodes and one real
  Coworker normal run. ALFWorld finished 0/4 with three Harness-invalid episodes and one score-eligible model
  failure; its wrapper returned 1. Coworker reached 32 real Mimo attempts and produced a verified 255-second H.264
  video, but the changed observation freshness contract rejected business actions and the independent verifier
  returned 1 for missing `presentation/events.jsonl`. These are preserved failed release attempts, not PASS.
- Repaired the hkust4 Coworker editable install that pointed at a stale source tree, and completed Doctor/preflight
  without dependency upgrades. The temporary V1.9 candidate additionally received its omitted lock-matched
  Playwright/greenlet/pyee Coworker runtime dependencies; the initial `ModuleNotFoundError` attempt remains in the
  append-only release ledger.

### Added

- CL-21 增加默认关闭、部署者显式批准的 trusted local extension layer：canonical manifest/entrypoint
  SHA-256、same-bytes compile、non-symlink containment、content-bound plugin provenance、canonical
  `required_capabilities` 与 requested/deployment/run-principal 三方 capability 交集。原因：manifest
  requested capability 不是部署授权，Catalog 中存在的 plugin id 也不能由 request/CLI/Gateway 扩张
  profile。影响：扩展工具只进入 Home final ToolView，ALFWorld/Coworker 不变；坏 hash/import/collision
  在 Catalog mutation 前整体失败。
- CL-21 lifecycle 只接受可信 async callback，区分 application 与 run start/end/stop，提供稳定 priority/
  matcher、cooperative timeout/cancel、blocking、generation fencing、脱敏 JSONL trace 和 cleanup。
  hooks-only reload 在活动 callback 时返回 `busy`，任何 tool/provenance/capability/profile 变化返回
  `restart_required`。该 MVP 不宣称 hostile-code sandbox，也不允许 hook 成为 permission、device safety、
  terminal、verifier 或 scorer 的唯一 owner。当前证据仅为 HPC2 non-live；hkust4 外部门按用户要求延后。
- CL-21 stage review 后收紧边界：reload identity 现在固定 extension id/version/requested/granted
  capabilities 与完整 tool plane；exact tool/hook token 不能替代 canonical required capability；显式空
  `enabled_tool_ids` 关闭全部工具，非法扩权在任何 run hook 前拒绝。callback timeout 改为独立 task
  hard result fence，抗取消 task 继续计入 active 并阻止 reload/cleanup；application close 先 quiesce 再
  stop/cleanup。entrypoint 每级目录均通过 pinned dir-fd 与 `O_NOFOLLOW` 打开，失败 candidate、partial
  load 和 Catalog collision 会释放已取得的 extension cleanup ownership。原因：原有同源测试无法覆盖
  `asyncio.wait_for` 抗取消、父目录 symlink TOCTOU 和空 tuple fail-open。影响：post-review HPC2
  non-live 为 `1285 passed, 7 deselected`，未访问 hkust4 或 live 外部系统。

- CL-20 增加 Gateway、channel-neutral typed identity、确定性 tenant/session 路由、附件 containment、
  bounded priority bus 和严格 `PublicEventProjection`。首个 remote channel 为默认关闭的 Telegram
  long polling；它只从环境变量读取 token，并将 exact sender mapping 转成 immutable principal，
  不信任 prompt、metadata 或 session override。
- Gateway 复用 application factory 的同一个 `ApplicationRuntime`，通过 `RunRequest` 执行并用
  generation fencing、cancel-and-join、SessionBackend snapshot recovery 和 unpaired tool-tail 清洗
  拒绝 late result。progress 可合并/淘汰，final/error/cancel 保留并在满载 critical queue 时反压。
  Gateway 只消费 events 层的 allowlist/exact-text/correlation public projection。

影响：新增 `gateway` optional extra 和 `homemaster gateway --config ...`；具体
python-telegram-bot 运行时符号等待用户指导的 hkust4 真环境核对，当前只宣称 HPC2 non-live gate。

- 收尾修复：borrowed device handle 的 pool fencing generation 不再冒充 backend application-run
  generation；观察 capture/provider binding 读取独立的 `backend_generation`，并保留原有 disconnect/
  close fencing 语义。同步刷新 CL-20 upstream manifest destinations/provenance、`uv.lock` baseline
  hash 与 legacy 文档术语。

- 阶段 review 修复：egress 消费时重新核对 generation/identity，Telegram 在认证前不查询或下载附件，
  supervisor 观察全部 service task 并在 outbound drain 后停止 channel；assistant reply 不再与
  `RunResult` duplicate final。progress、final、error、cancel 共用递归 public projection，补充自由文本
  credential、host path、URL query 和配置 secret 脱敏。新增 review regression 后 targeted gate 为
  `151 passed`，完整 HPC2 non-live gate 为 `1251 passed, 7 deselected`。

### Fixed

- V1.9 整体 final review 的 6 项发现已完成代码修复与集中验证：设备 authoritative event append
  与 audit sink 故障隔离，审计失败不再阻断 lease release 或 emergency-stop；未知 MCP discovered tool
  默认按 mutating fail closed，已尝试调用的 timeout/call failure 返回 `outcome_unknown`；Gateway 用一个
  absolute deadline 硬限制 active run、bus、channel 与 service-task shutdown，并在 RuntimeEvent 生产时
  固化 Gateway generation；extension reload 失败会 await partial candidate cleanup，composition 在
  ApplicationRuntime 接管前持有 rollback ownership；manifest 可显式声明 flat Python dependency files，
  digest 与 same-bytes loader 覆盖全部依赖且不暴露真实 `__file__`。原因：阶段内绿灯没有覆盖旁路 sink
  反向中断控制面、backlog 代际漂移、抗取消 shutdown、跨构建阶段 cleanup ownership 和 entrypoint
  之外的行为字节。影响：MCP SDK mutation annotation 仍标 `UNVERIFIED`，trusted extension 仍不宣称
  hostile-code sandbox。评审修复后的独立证据为 `1295 passed, 11 deselected`，没有沿用修复前的
  `1285 passed`；hkust4 外部门的真实失败结果见本节 Verification。

- 修复两条真实 Provider 验收仍按同步方式调用异步 `LLMClient.complete()`、导致 coroutine 逃逸且请求
  根本未发出的问题；live gate 现在直接 await 真实入口并断言外部响应，防止 non-live 全绿掩盖正式
  Provider 门失效。

### V1.9 Phase 2 - 权限、认证与设备资源基础

- 修复阶段评审发现的生产接线与 fencing 缺口：runtime 现在在 run 边界把 borrowed backend 自动
  注册为 tenant-pinned pool handle；同一 physical backend 不能跨 tenant 生成第二个 lease slot；
  disconnect/repeat-disconnect/stop/close 共用原子单调 `fence_next`；已 grant waiter 在 backend 前
  再次锁内核对。原因：只有 isolated pool 测试而没有生产环境接线，会让所有 ownership 与串行门在
  真路径失效；immutable registration generation 也会在 stop 后回退。影响：活动动作在 terminal
  transition 后为 `outcome_unknown`，等待动作从不进入 backend，borrowed backend 仍不会被关闭。
- 急停外部边界改为内部 `DeviceControlReceipt` + 独立 `DeviceStateObservation`，拒绝 raw
  `"success"/"stopped"` 字符串；两次 return code 保留到 result、authoritative event 和 mode-0600
  audit。具体机器人 SDK enum 仍为 `UNVERIFIED`，等待用户指导的 hkust4 真机核对。阶段扩大回归
  `143 passed`，未访问 hkust4。

- 变更：统一 canonical execution chain 默认接入 capability-aware permission policy；Bearer credential 只映射预配置
  typed principal/tenant/capabilities，robot read/control 与 MCP 分别要求 `device.read`、
  `device.control` 和 `mcp.call`，显式 allow、prompt、metadata 与 skill 均不能扩权。
  原因：远程入口不能复用本地隐式信任，也不能建立第二条绕过 canonical executor 的机器人路径。
  影响：本地 `RunRequest` 保留兼容能力；远程 channel 必须显式配置 principal capability，敏感路径、
  command deny 和 tool deny 继续 fail closed。
- 变更：application-owned connection pool 与 generation-aware physical-device FIFO lease 绑定；同设备
  写动作串行、不同设备并发，disconnect/stale/emergency-stop 拒绝等待动作并把活动动作标为
  `outcome_unknown`。急停要求 control capability，且必须同时看到成功返回和外部 stopped 状态。
  原因：连接状态、互斥锁和急停若各自维护 generation，会允许 late result、重复动作或断线后误执行。
  影响：owned/borrowed connection 按 ownership 隔离关闭；设备 lease/fence/stop 追加到 mode-0600
  `device_audit.jsonl`；未知写结果不可自动重试。

### V1.9 Phase 1 - MCP、Resources 与 Tool Output Artifacts

- 修复阶段评审发现的五个边界缺口：artifact 分区改用 immutable tenant identity 而非 principal id；
  resource URI 从 model preview 移除且 audit 只保留 SHA-256 引用；audit sink 故障以 typed failure
  留存但不改变连接状态或中止 cleanup；显式 `--probe` 写入独立 mode-0600 JSONL audit。原因：
  principal/tenant 混淆会破坏 quota/ACL domain，resource payload 与 audit 可能泄漏 query token 或宿主
  路径，观测系统故障也不能反向泄漏连接。影响：raw resource artifact 仍在精确 tenant ACL 内保真；
  MCP、artifact、application/CLI 聚焦回归 `40 passed`，未访问 hkust4。

- 变更：增加 optional MCP SDK、application-owned async manager、stdio/streamable HTTP discovery、
  typed per-server status、timeout/cancel/disconnect fencing、best-effort close isolation 和脱敏 JSONL
  audit；首次真实 run 前原子注册保真 JSON Schema tools，并在 final Home ToolView 后重验 skills。
  原因：动态 MCP 连接与 event loop 绑定，不能由每个 turn 临时创建，也不能用浅层 schema 投影或
  单个 server 失败破坏 builtin Catalog。
  影响：普通 dry-run 保持零外部 I/O，`--probe` 才临时连接；WebSocket 明确 unsupported；
  ALFWorld/Coworker ToolView 不变，每个 Home run 仍只能执行其 frozen ToolView 中的 MCP stable id。
- 变更：新增 tenant/session/run 分区 `ToolOutputStore`，原始 MCP 输出在脱敏前以 opaque handle、
  quota、TTL、0600 原子文件和精确 ACL 落盘；模型只接收 bounded preview/hash/handle，resource URI
  由 adapter 内部映射为 opaque `resource_id`。
  原因：外部工具结果和 URI 可能包含 credential、宿主路径或大 payload，不能直接进入模型、事件
  或 session snapshot。
  影响：调用者必须持有准确 partition identity 才能读取 artifact；config summary、状态、异常、
  dry-run 和 audit 不输出 MCP headers/env/URL userinfo。

### V1.9 Phase 1 - Skills 与配置来源

- 变更：移植 OpenHarness YAML frontmatter、skill discovery 和 registry 控制流，增加 builtin/user/
  project/explicit 优先级、完整 provenance、Git-root/symlink containment、ToolView capability gate、
  named builtin override、model invocation gate 和真实 wheel package-data 验证；Home one-shot、
  Interactive 与 dry-run 现在使用同一份 registry。
  原因：旧 loader 只支持两个硬编码 builtin 和逐行伪 YAML，且用户/项目 skill 没有路径、能力或
  覆盖边界；局部实现若不接 composition 只能得到内部自洽测试，不能成为用户能力。
  影响：自动来源中的非法单项会以 secret-safe diagnostic 拒绝且不影响 builtin，显式来源失败会
  阻止启动；ALFWorld manifest 和 Coworker 固定十一项 ToolView 不变。
- 变更：provider/auth 配置新增 `api_key/auth_token` typed schema、provider-specific env、有限 CLI
  model override、逐字段 `default/file/env/cli` provenance 和递归 redaction；恢复字段完整且只有
  占位值的 `config/homemaster.example.yaml`。
  原因：环境/CLI 合并必须可诊断且不能被 ambient `ANTHROPIC_*` 变量改变身份，也不能在异常、
  doctor、dry-run、日志或事件中泄漏 credential。
  影响：真实配置仍只保存在 ignored mode-0600 `config/homemaster.yaml`；Anthropic SDK
  `auth_token` 已在安装的 0.116.0 真环境构造器中核对可用。
- 验证：全量 non-live `1155 passed, 7 deselected`；全仓 Ruff lint、改动文件 format-check、compileall、
  cleanup guard、OpenHarness port manifest 和 diff/secret/config 权限门通过。真实 wheel 已安装进隔离
  venv 并从源码树外发现 builtin `SKILL.md`；dry-run 黑盒返回 12 项 Home ToolView、CLI model 来源、
  skill 诊断和 `external_io=false`，不输出 skill 宿主机路径。

### V1.8 Implementation

- 问题：V1.8 最初的 current-visible 导航前置条件与公开工具面不兼容，模型无法主动改变视角让离屏目标进入画面，导致工具循环零 backend action 后耗尽预算并被误归为 Agent 失败；reset evidence、ALFWorld control state 和持物导航的物理状态投影也不完整。
  变更：V1.8 使用 committed-frame integrity gate 和 frozen scene index，优先当前可见 exact target，否则消费同一 reset snapshot 的一个 direct pose 做单次离屏导航；新增独立 physical-world/control hashes、成功与失败 reset ledger/snapshot/raw event 持久化，并规范化 held object 随 agent 改变的 geometry。
  原因：既保留“模型动作必须绑定到成功 Provider 请求所见 frame”的完整性约束，又解除不可满足的目标可见性死锁；同时让 setup、恢复、导航和责任分类可从 artifact 独立重算，避免把 Harness 状态漂移伪装成模型失败。
  影响：`AlfredThorEnv` 继续强制 `--trial-manifest`；离屏目标只允许一个冻结 direct pose，移动后必须准确可见，不会恢复 V1.7 candidate search 或 hidden-parent search。CLI/summary 分开报告 raw/Agent-on-valid、evaluation/Harness coverage、Provider/Runtime availability 和 formal-score gate。V1.7 compatibility bodies仍物理保留，但正式 V1.8 call graph guards不可达。
  验证：修复聚焦回归 `72 passed`，完整套件（含 live API）`410 passed, 1 skipped`；changed Python files Ruff、compileall 和 whitespace checks 通过。单条 `alfworld-v18-offscreen-fix-smoke-20260718-003` 完成 36 setup 与 4 model backend actions，score-eligible 且 Provider/Runtime/evaluation/Harness coverage 全为 1。固定十 Episode `alfworld-valid_unseen-v18-offscreen-fix-20260718-002` 完整退出，52 Provider attempts、29 model backend actions、1 Agent success、5/10 score-eligible；4 条 FloorPlan10 physical-world drift 和 1 条持有 Basketball 时的 THOR navigation rejection 保持 Harness invalid，coverage 0.5、`formal_score_available=false`。10 个 snapshot、311 组 setup request/event/world/control/raw/frame hashes 和 321 个 event files 独立重算通过；Gate A 19/20 与缺失 exact-case manifest 仍不记为 PASS。

### Documentation

- 调整实时 Mimo 验收报告中的 verifier JSON 摘录，移除已由小节标题表达且触发全仓历史术语守卫的冗余场景字段，不改变 run、分数、返回码、视频哈希或验收结论。
- README、用户指南和架构文档同步说明实时 Mimo 入口、五区可观测面板、presentation v2、异常恢复、公开输出/隐藏推理边界、`--expected-model` 验证、失败 attempt 保留，以及 scripted gate 不能替代真实 LLM 视频。
- 处置实时 LLM 可观测演示计划评审的十项问题：补齐自由文本机密拒绝、独立外部终态 mutation 门、真实 Planner 状态、reply 中间态、provider 端点身份、完整失败码、工具中文标签/类别、录像单调时间基准、失败 run manifest 与长计划当前项固定显示；所有问题均在实施前写回设计和计划。
- 新增实时 LLM 可观测 Coworker 实施计划：按 presentation v2 类型/纯 reducer、安全 Planner/公开回复/失败码投影、原子 Snapshot/SSE、五区观察面板、独立 verifier、失败恢复黑盒门、文档和真实 Mimo normal/anomaly 连续视频十一项任务推进；主 agent 独立实施，仅在计划和最终代码两个固定关卡使用 reviewer。
- 新增实时 LLM 可观测 Coworker 演示设计：明确最终 normal/anomaly 视频必须由 Mimo mimo-v2.5 现场选工具执行，不能以 scripted-coworker 替代；定义模型计划、模型动作、环境返回、确定性决策摘要、异常恢复折叠、公开回复与隐藏推理隔离，以及 presentation v2 协议、真实外部终态和连续视频验收门。
- 新增 Change Coworker 用户指南与架构文档，覆盖现有 shell 的 normal/anomaly 输入、隔离配置、preflight、真实 DOM/tmux 执行、双域评分、run bundle、独立验证和可选 VNC 观察。
- 新增经独立评审并逐条处置的 V1.8 ALFWorld Oracle 位姿与强类型执行反馈设计：针对真实 10 条运行暴露的候选预算截断、可见但不可操作、Put 状态投影错层和 Provider 误计分问题，明确删除隐藏对象/legacy 导航旁路，以单一 Oracle pose、exact target 可见终态、可 rebase 的执行 context、Adapter 到 Dispatcher 唯一 typed feedback 和分域评分替代 V1.7 搜索路径；本提交仅交付设计，产品接线在 Gate A/B 真环境通过前保持 `UNVERIFIED`。
- 修订 V1.8 ALFWorld reset transaction 设计：保留 immutable `discovery-run-007` 的温度漂移证据，不通过删除 raw THOR `ObjectTemperature` 弱化 world digest。用户批准的 setup 固定为 `initial event -> ChangeTimeScale(0.01) -> query -> N scan Teleports -> exact pose restore -> ChangeTimeScale(1.0) -> atomic publish`，成功 setup 计数由 `N+2` 变为 `N+4`。中途失败必须 best-effort 恢复 pose 和 normal time，任一恢复无法确认即关闭/quarantine 且不发布 partial snapshot；唯一模型初始 event/frame 来自最后成功的 normal-time return event。`PausePhysicsAutoSim` 已在真实 ai2thor 2.1.0 中证明无效，`0.01 -> 1.0` 的稳定与恢复行为已正交验证。该阶段保留的 current-visible 规则随后被本次 frozen-snapshot correction 明确取代。
- 修订 V1.8 ALFWorld 位姿与强类型执行反馈设计：current frame 仍必须与成功 Provider request 的图片绑定，但目标本身不再要求预先 strict-visible。generic label 优先当前可见 exact peer，否则稳定选择 frozen full set 中第一个非 inventory peer；显式 ordinal 始终绑定 frozen full set。离屏目标只能消费自己的 direct snapshot pose，不能通过 hidden parent 定位；返回 event 必须证明准确 objectId 可见且 bbox 为正。physical world 与 ALFWorld control state 分开哈希，held object 的 agent-coupled geometry 被规范化，inventory、picked-up、containment 和任务状态仍保留。该修订以真实 Runner smoke 和逐项 artifact 重算验证，但未把不完整 Gate A/B 宣称为 PASS。
- 记录 V1.8 Gate A smoke 的证据坑：真环境 bbox 为 NumPy ndarray，动作门成功后可能在 JSON 序列化阶段失败；ai2thor teardown 的独立 Player.log 异常不能替代动作返回码、准确外部终态、artifact 和进程退出码四道门。

### Added

- 新增仅用于展示黑盒验收的 `observable_failures` 脚本 profile：normal/anomaly 分别逐实例触发并恢复叙事门禁错误，全码矩阵另行验证 18 个稳定安全码的投影、恢复规则和 Chrome 展开/折叠；该 profile 明确不计入最终真实 LLM 验收。
- 产品与独立 bundle verifier 现在强制核对 presentation v2 全字段、异常/恢复/历史关联、禁止字段、每次工具生命周期、关键事件画面、当前 run 外部终态和真实 provider 模型身份；`--expected-model mimo-v2.5` 会拒绝 scripted 视频、回环/覆盖 provider、缺少成功响应或身份文件晚于首个请求的验收。
- 高管录屏面板改为五区固定布局，常驻展示真实模型计划、每次模型工具选择、独立环境返回和确定性决策摘要；异常展开置顶并在匹配恢复后折叠保留，长文本不再挤压或覆盖相邻区域。
- EpisodeStore 现从候选 append-only 展示事件原子重建 presentation v2 Snapshot，SSE 重连可恢复模型计划、当前动作/结果、决策摘要和异常历史，不在浏览器或 Episode 中维护漂移副本。
- 实时展示投影新增持久化 Planner 快照、公开 assistant reply 和封闭失败码；继续拒绝 assistant.thinking、Prompt、证据原文、任意异常文本及敏感字段，且不向模型建立观察面板回流。
- 新增 presentation v2 强类型协议与纯事件 reducer，从同一 run 的 append-only 展示事件确定性重建模型计划、当前动作/结果、决策摘要、异常恢复和关键历史，避免浏览器或 Episode 维护不可审计的第二套状态。
- 在现有 `homemaster shell` 中加入严格 ticket router 和独立 coworker child runtime；有效 `case_02` run 获得六项浏览器工具、真实受限终端、SOP 决策、planner/progress 和两个通用 skill，共固定十一项工具。
- 新增 run-scoped FastAPI 环境、ticket/monitor/automation/observer 页面、异步自动化 job、action ledger、真实 tmux/Bash/bubblewrap 执行、31 节点场景 DAG、16 项结果检查和 raw/effective trajectory artifact。
- 新增 localhost-only TigerVNC headed display、FFmpeg x11grab/libx264 录制、first-packet 落盘门、ffprobe/首中末帧验证、OpenAPI snapshot、SSE replay 与产品独立 bundle verifier。

### Fixed

- 修复成功 Planner/进度快照投影失败后仍发布无 plan 的 succeeded 事件，以及真实 provider 验收只要求至少一个 request/response 的假阳性窗口；投影现拒绝无法安全生成的 plan，独立 verifier 逐个要求成功 Planner 带 plan，并按连续 iteration 核对 request/response 唯一配对、顺序和工具调用前置响应。
- 修复长视频停止验证继承 20 秒通用请求超时、超时清理再次向已退出 FFmpeg 写入 `q` 的问题；客户端现使用 180 秒专用停止超时，服务录制会话用锁缓存完成结果并幂等返回，避免已成功视频被重复停止标成失败 attempt。
- 修复 attempt manifest 的 `run_root` 字段与 helper 形参冲突导致真实 shell 在分配 run 后立即失败，以及固定 0.35 秒命名帧越过下一事件却仍通过像素门的问题；顶层 shell 黑盒门和独立 verifier 现在分别锁定真实入口与帧事件边界。
- 修复 coworker 正式成功把 `artifact_failure` 固定为 false、manifest 缺项不失败的问题；最终评分和独立 verifier 现在都要求核心 artifact 已登记、完整且哈希一致。
- 修复终态后预留 action、runtime event 和内置 planner/progress/skill 仍可继续执行，以及 decision 可引用伪造或跨 run evidence 的问题；服务端、工具端、归一化和离线 verifier 现在共享终态与证据所有权门禁。
- 修复独立 bundle verifier 信任产品首中末帧布尔结论的问题；它现在独立核对 FFmpeg/first-packet/视频哈希，并从视频重新解码 raw RGB 帧计算非黑比例、方差和首末变化。
- 修复模型可跳过 planner、阶段 progress、exact job wait 或 implementation proceed，导致外部结果成功但 DAG 轨迹级联失配的问题；环境现在在副作用或审计写入前验证真实前置节点，拒绝伪造、错序和跨 job 证据。
- 修复服务启动路径对 venv Python 使用 `Path.resolve()` 后解引用解释器 symlink、丢失 venv 包环境的问题；子进程保留配置的绝对 venv 路径。
- 修复 fragmented MP4 已编码帧但媒体 packet 尚未落盘时过早放行 provider 的问题；录制门同时要求 progress 和 header 后文件正向增长，并固定短 GOP 与 flush。
- 修复 current-visible 前置条件让三工具公开面无法推进离屏任务、最终以零模型 backend action 耗尽工具预算的问题；`robot_go_to` 现在对冻结 exact target 只尝试一个 snapshot pose，并在移动后严格验证准确目标。
- 修复持有物随 `TeleportFull` 改变世界坐标、旋转和 bounds 导致 physical-world hash 误报 `execution_state_uncertain` 的问题；拿起、放下、inventory、containment 和非 held 物体变化仍可检测。
- 修复 reset terminal 引用不存在 evidence、恢复位姿不匹配被误写为 time-scale reject、goal/control 读取异常被哈希为合法 null，以及 raw event/frame hash 无法从 artifact 独立重算的问题。
- 修复 ALFWorld 导航把检测框存在误报为准确目标已可见的问题；导航现在锁定准确 objectId，并同时核对 THOR 返回码、实际 pose、`metadata.visible`、正面积 bbox 和最终 event 图片。
- 修复 `pencil 2` 等显式实例 miss 被二次解析并回退到其他实例的问题；grounding 现在由确定性 `SceneObjectIndex` 一次完成并锁定。
- 修复 put 只调用一次 THOR、只信任 `lastActionSuccess` 且把底层失败压缩成 `action_failed` 的问题；新执行器在同一目标的固定局部候选内重试，并用 inventory、`isPickedUp` 和准确 parent/child membership 验证终态。
- 修复 Harness terminal 后模型仍可继续调用 robot 工具和错误计入 Agent invalid/score 的问题；普通 Episode 与长程 taskset 现在共享 `EpisodeOutcome`，未运行子任务有明确基础设施标记。

### Changed

- 删除不产生新观察的 `robot_inspect_view`。
- 将含真实认证信息的 `config/homemaster.yaml` 从版本控制移除并加入 `.gitignore`；运行机器继续保留本地文件，仓库只提交脱敏的 `config/homemaster.example.yaml`。
- 模型 put 反馈新增稳定 inventory、object state、state change、error 和安全 detail；内部 objectId、坐标、候选及专家信息不会进入模型上下文。
- 导航与 put 内部 trace 新增 context、逐候选 move/put/read、raw event hash、预算用量、context invalidation 和 terminal JSONL 事件；`isPickedUp` 缺失不再被接受为成功状态。
- 汇总与 CLI 同时报告有效子集 Agent 成功率、Harness coverage、基础设施失败数和正式分数可用门。
- 根据六 Shelf 真环境 characterization 固定生产预算：导航 `65 candidates / 66 backend actions / 34804 ms`，局部 put `9 / 17 / 5669 ms`。

### Verification

- 最终 reviewer 的两项 P2 均已采纳：新增 10 个 RED 回归后，Planner 投影与 provider iteration 加强门 128 项聚焦测试通过；评审修复后全量 798 项通过、1 项跳过，两条 accepted real-Mimo bundle 在更强 verifier 下仍独立 PASS。
- 最终审计通过：793 项测试通过、1 项跳过；ruff lint、compileall、历史术语守卫、本次 4 个代码/测试文件 format-check、preflight 和两条真实 bundle 独立验证均通过。全库 format-check 仍只报告 40 个未触及的历史文件，不纳入本次格式化范围。
- 完成两条由真实 Mimo `mimo-v2.5` 现场决策的可观测录屏：normal `coworker-20260720-024949-b7004546` 与 post_change_anomaly `coworker-20260720-025635-a46d87ca` 均通过模型身份、工具/展示关联、真实配置终态、自动化返回码、grep、连续 H.264 视频、逐张人工画面检查和独立 bundle verifier；失败 normal `coworker-20260720-022516-8c773877` 同时保留并写入验收报告。
- 真实服务幂等停止黑盒门 `recording-stop-gate-20260720-024549` 连续两次 `recording/stop` 均返回 HTTP 200，FFmpeg 返回码 0，两个响应与磁盘 MP4 的 SHA-256 一致，证明重复停止不再触碰已退出的录制进程。
- 最终审计处置后全量测试为 `478 passed, 1 skipped`；两个正式 bundle 均通过加强后的独立 manifest/evidence/ffprobe/raw-RGB 帧验证。
- 真实 Mimo `normal` run `coworker-20260716-154711-853f071d` 达到 24/24 节点、14/14 检查点和 trajectory/result/overall 100，正式成功；H.264 视频 SHA-256 为 `a6cd33f1b3c62ca3820ea870c5ffcbe8f236cfb5c66090332f46ae707593755e`。
- 真实 Mimo `post_change_anomaly` run `coworker-20260716-160128-c4f0faa9` 达到 22/22 节点、11/11 检查点、add/remove 与 grep `[0,1]`，正式回滚成功；H.264 视频 SHA-256 为 `d00f19c7b699cc5d832f349eb86a9ab2e0b0aa2a050f7e99b6e335fcfd64cfcd`。
- V1.8 本次设计提交的聚焦 ALFWorld/Runner/Dispatcher 回归为 `145 passed`；排除已证明在 `22cb122` 就会失败的 cleanup guard 后，其余全仓为 `351 passed, 1 skipped`，compileall 和文档 hash/fence/placeholder/secret/diff 门通过。完整 pytest 仍显示该唯一预存 guard FAIL（它全局禁用通用词 `deterministic`，而未修改的 V1.7 spec/既有测试已包含该词）；Ruff lint 的 39 项和 format 的 41 个文件也全部来自未修改的 `src/`/`tests/`，本设计任务未擅自修复。
- ALFWorld benchmark 单测与接口回归通过；真实 Shelf 1-6 exploration 全部达到 put 外部终态和 goal `1/1`。
- Shelf 3/4/6 在独立 Xvfb 产品 Harness 进程中分别通过 THOR return code、inventory、`isPickedUp`、准确 parent/child、goal 和最终图片像素门。
# 2026-08-18

### Added

- Added timestamp field to `DialogueMessage` in Session finalizer, with a `_timestamp_millis()`
  helper that parses ISO 8601 timestamps from runtime events to millisecond epoch. The finalizer
  now preserves per-message timing so dialogue summaries carry accurate temporal context.

### Changed

- Updated session-handoff documentation with Schema feedback/evidence follow-up results, async
  Session-finalization regression results, and live regression results. The V2.6 memory
  self-correction implementation plan now records the confirmed implementation outcome.

# 2026-08-13

### Added

- 交互 Session 在 `/exit`、EOF、提示符 Ctrl+C 和 `/new` 时自动汇总当前 Application Trace，并通过
  MindMemOS 原生 Vanilla Add 沉淀长期经验；输入 Envelope 与轻量 Job 记录持久保存在
  `memory.data_root/experience_jobs`，Add 失败不阻止退出。
- 新增 `homemaster --debug`，显示 Session Finalizer 的事件数量、输入路径、耗时和实际 Memory operation。

### Changed

- 将模型可见记忆工具重命名为职责明确的 `context_memory`、`mindmemos_search`、`mindmemos_add`、
  `mindmemos_update` 和 `mindmemos_delete`，并重写描述以直接说明作用、返回内容和调用时机。搜索结果已包含
  完整记录，因此移除冗余的模型可见 `get_memory`；底层 raw-ID 读取继续供搜索、更新和写后验证使用。
- `EmbeddedMindMemOS` 在保留 typed Schema Add 的同时持有独立 Vanilla Add pipeline，共享既有 Qdrant、
  Neo4j、LLM、Embedding 与 operation recorder。
- `mindmemos_search` 将原生 Vanilla Session experience 作为既有 `procedure` 类型返回，同时保持损坏的 Schema
  记录 fail closed，使自动沉淀经验能在新进程中被真实 LLM 召回。
