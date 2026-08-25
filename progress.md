# Current HomeMaster Progress

## 2026-08-25 Task TODO tool contract clarification

- Status: `COMPLETED`. `task_planner` now explicitly creates or completely replaces the
  model-owned TODO list, while the legacy-named `task_progress_check` explicitly updates selected
  statuses, evidence, focus, and overall task state. Both descriptions state that they do not
  observe, execute, or independently verify external work, and every public schema property now
  has a model-facing description.
- Browser Gateway and `change-ticket-executor` no longer require a browser write immediately after
  a progress update. TODO updates occur only after model-visible terminal evidence supports a state
  change; the only strict adjacent pair is a fresh `browser_inspect` followed by the browser write
  that consumes its references.
- Evidence: focused task/prompt/Skill tests `14 passed`; shared task-state/context/runtime tests
  `72 passed`; Ruff, format, and `git diff --check` pass. A fresh Anthropic request-boundary probe
  confirms both expanded descriptions and all top-level property descriptions reach Provider tool
  schemas unchanged.

## 2026-08-25 Browser tool model-contract clarification

- Status: `COMPLETED`. All ten generic browser tools now carry detailed
  model-facing usage and recovery descriptions, and every public input property explains its
  exact meaning. `snapshot_id` remains the inspection-batch identifier and `element_id` remains
  local to that same inspection; `browser_check` and `browser_uncheck` remain separate tools.
- `observe` is now both the Runtime-owned automatic post-write capture and a model-selectable
  read-only visual inspection tool. A valid manual observation receives one post-image model-turn
  grace, does not recursively trigger automatic observation, grants no action reference, and
  requires a fresh `browser_inspect` before interaction.
- Evidence: focused browser/observation/schema/Skill tests `27 passed`; shared Runtime/context
  tests `58 passed`; CLI/config tests `69 passed`; real Playwright integration `15 passed, 1
  skipped`; scoped Ruff, format, `git diff --check`, and a fresh Provider-schema probe pass. The
  probe exposes all ten browser tools including model-selectable `observe` and confirms nonempty
  descriptions for every top-level browser-tool property.
- Blocking items: none. The repository still contains unrelated pre-existing dirty-worktree
  changes; this work does not stage, revert, or commit them.

## 2026-08-25 Ops Monitor Agent run 25 acceptance verifier repair

- Status: `COMPLETED`. The original `ops-monitor-real-20260825-25` business run remains immutable and successful:
  RC 0, empty stderr, exact fixture `agent_version=1.0.0`, asset API
  `fixture-node-01/running/1.0.0/fixture-region-01`, and zero residual run processes.
- Root cause: the first deterministic bundle was materialized before click-result route tracking was in place, so
  post-change asset work was mislabeled and the independent verifier rejected missing `change_verified`. The
  materializer had already published a `succeeded` output before that final check, leaving a misleading invalid bundle.
- Repair: verifier now rebuilds trajectory from copied raw runtime events and requires exact equality; materialization
  verifies the private temporary bundle before atomic publication. Regressions prove a self-consistent derived
  trajectory mutation is rejected and a failed final check leaves no formal directory.
- Evidence: focused pytest `3 passed`; Ruff and format checks pass. Fresh immutable-log bundle
  `/tmp/homemaster/runs/ops-monitor-real-20260825-25/deterministic-verifier-v2` passes a fresh product verifier,
  independent SHA-256/size/mode checks for all 35 artifacts, exact external asset readback, RC/stderr/fixture checks,
  and process cleanup. Its 81 executed calls classify as framework 8, pre-change 45, implementation 12, post-change
  16; ten model protocol mistakes were blocked with zero hard tool failures.
- Blocking items: none for the requested verifier repair and run-25 acceptance report. The original invalid
  `deterministic` directory is intentionally preserved as negative evidence and is not an accepted bundle.

## 2026-07-28 Self-contained mem0, OpenHarness cleanup and memory data root

- Status: `COMPLETED`. The complete proven `mem0ai==2.0.13` runtime is vendored into Git and
  the HomeMaster wheel with full manifest verification and no external `mem0ai` distribution dependency.
  `src/openharness`, its upstream-only tests and live V2 generator are deleted; immutable provenance is archived.
- Persistent runtime data derives only from `memory.data_root` (default `~/.homemaster/memory`) while code and data
  remain independently transferable. A journaled component coordinator migrates old files, validates Qdrant/SQLite,
  preserves sources, resumes interrupted plans and runs before any application/Gateway store opens. Doctor is read-only
  and `homemaster memory migrate` emits typed success/failure receipts.
- Plan source: `plan/self-contained-mem0-openharness-cleanup-memory-layout-plan-zh.md`.
- Plan review: the single read-only gate found one critical migration-publication flaw, three additional high-severity
  gaps and one medium OpenHarness-boundary ambiguity. All are accepted: migration is now component-published with a
  recovery journal, historical default discovery is explicit, vendored bytes use distribution/hash verification, one
  coordinator owns every store-opening entry, and OpenHarness provenance has one archival path.
- Current evidence: focused configuration/migration/doctor gates pass; two independent real embedded-Qdrant legacy
  layouts preserve fact/procedure IDs, file memory and evidence through migration, update/delete and restart. A fresh
  venv installed only the HomeMaster wheel from an empty cwd and passed vendored integrity, dense/BM25 CRUD, 61-tool
  construction, license inventory, absent `mem0ai` distribution and absent `openharness` package.
- Current internal gates: full non-live suite `1538 passed, 1 skipped, 11 deselected`; full Ruff, changed-file format,
  `uv lock --check` and `git diff --check` pass. The first isolated full-suite run intentionally changed HOME and exposed
  four missing-browser-cache failures plus two tmux HOME-inheritance failures; the browser cache was explicitly bound,
  tmux now receives per-case HOME, and the complete rerun passed.
- Final review: the sole read-only reviewer found four valid boundaries: completed manifests trusted identity without
  target validation, copy did not recheck a changing source, ready-state doctor opened/materialized the backend, and its
  mem0 import preceded vendor verification. All were accepted. Published targets now fail closed when missing or
  structurally invalid; file sources use their real lock plus a pre-publish digest recheck; SQLite uses backup snapshots;
  doctor verifies vendored bytes without executing mem0 and reports `probe=not_opened` with zero filesystem writes.
- Post-review targeted remediation: migration/vendor/mem0/doctor regressions pass (`30 passed`), targeted Ruff/format,
  diff and lock checks pass. A current-byte wheel installed with all 87 declared dependencies in a fresh Python 3.11
  venv; it verified vendored bytes, absent external `mem0ai`/OpenHarness, successful legacy publication, missing-target
  conflict and unchanged legacy source. The same install correctly rejected unsupported Python 3.14 because locked
  spaCy has no cp314 wheel. Commit/push/merge are next. No second final review was started.
- Blocking items: none. The currently running pre-change Gateway was not terminated and real user memory was not moved;
  the first controlled restart or explicit migration command will use the same coordinator after the old owner closes.

## 2026-07-28 Public mem0 hybrid search and spaCy environment

- Status: implemented and focused tests pass. HomeMaster calls `Memory.search()` once and no longer adds a second
  `keyword_search()`. The project lock and `.venv` now include `mem0ai[nlp]==2.0.13`, spaCy 3.8.14 and the locked
  `en_core_web_sm==3.8.0` wheel; both full and lemma pipelines load without runtime download. Real embedded-Qdrant
  tests verify one public hybrid call, one internal BM25 call, correct recall and no missing-spaCy warning.

## 2026-07-28 Persistent offline BM25 cache

- Status: completed. The locked `Qdrant/bm25` artifact is now package data and is atomically materialized into
  `memory.mem0.fastembed_cache_path`, defaulting to the checkout's ignored
  `.cache/homemaster/fastembed`. HomeMaster's preflight and mem0/Qdrant use this same cache and force offline mode;
  the doctor reports the resolved path in `memory_backend.details.fastembed_cache_path`.
- Migration: a new clone or server requires no `/tmp/fastembed_cache` copy and no model download. The 18 locked
  files are restored from the source/wheel on first memory backend startup. Deployments whose checkout is read-only
  must set `memory.mem0.fastembed_cache_path` to a writable persistent absolute path.
- Evidence: focused memory/doctor slice `26 passed`; Ruff and diff checks pass. A true cold-cache
  `homemaster doctor --json` passed with offline mode and unreachable HTTP(S) proxies. `uv build --wheel` succeeded,
  a fresh venv installed only that wheel outside the checkout, and offline FastEmbed loaded all 18 packaged files
  into an empty cache.

## 2026-07-27 V2.1 memory system implementation

- Status: WP1-WP6 implementation, documentation, live provider gate and isolated wheel gate are complete. The user
  explicitly waived the one final read-only review before commit. The formal source of truth is
  `plan/V2.1/homemaster-memory-system-implementation-plan-zh.md`; the discussion file is historical only.
- Dependencies/config: `mem0ai==2.0.13`, `fastembed==0.8.0`, resolved `qdrant-client==1.18.0` are locked. The owner
  selected the stable PyPI wheel even though unused backend files differ from the Git tag; selected Qdrant/core paths
  match. `memory.enabled=false` removes all six public memory tools and memory context injection without selecting a
  second backend.
- File memory: private atomic SOUL/USER/MEMORY stores, threat scan, drift backup, capacity/unique-match errors and
  per-session frozen prompt are implemented. Same-session writes persist without changing its frozen prompt; a new
  session observes them.
- mem0/Qdrant: typed fact/procedure schemas, deterministic serialization/dedupe, persistent evidence ledger, CRUD,
  same-ID update, stale evidence rejection, exact hints and independent terminal readback/raw-point checks pass on
  real embedded Qdrant. Process-level close/reopen returns zero with no Qdrant destructor warning.
- Hybrid root cause and repair: the installed mem0 2.0.13 public `search()` already performs dense and BM25 retrieval.
  HomeMaster calls it once and only merges metadata-exact candidates, preventing the former second BM25 call. A raw
  point contains the named `bm25` vector, and real Chinese queries prove public hybrid recall. The incident is recorded in
  `docs/pitfalls.md` and a preventive rule in `CLAUDE.md`.
- BM25 offline gate: `Qdrant/bm25` commit `e499a1f8d6bec960aab5533a0941bf914e70faf9` and all 18 artifact file
  SHA-256 values are locked. Startup resolves fastembed's actual cache directory with `local_files_only=True`, verifies
  commit/file set/checksums and finite Chinese sparse encoding, and fails unavailable rather than downloading or
  degrading. The gate passes with `HF_HUB_OFFLINE=1` and an unreachable proxy.
- Public tools: default Home exposes exactly `memory`, `add_memory`, `search_memories`, `get_memory`,
  `update_memory`, `delete_memory`; legacy `memory_retriever`/`memory_writer` remain only in benchmark memory modes.
  File mutation has action-level `tool.mutate`; scope/metadata/dedupe/provenance are application-owned.
- Runtime evidence: the canonical current user turn is registered before the first provider call and its opaque ref is
  rendered in a separate runtime context block while user text remains byte-for-byte unchanged. Successful backend
  read observations or verification-passed mutations are registered only after ToolExecutor completion. A full
  ApplicationRuntime gate proves user-statement fact write; another proves ordered observation refs are committed in
  one iteration and consumed by a procedure write only in the next. Evidence is tenant/session/run/turn bound;
  procedure evidence must cover every ordered step plus final success and be strictly monotonic.
- Current evidence: focused memory/runtime slice `99 passed`; offline BM25 artifact gate `1 passed`; full non-live
  suite `1238 passed, 2 skipped`. The real SiliconFlow gate returned HTTP success for
  `Qwen/Qwen3-Embedding-8B`, 4096 finite dimensions, per-case Chinese fact/procedure hits, same-ID update, idempotent
  procedure and delete/reopen absence. Outbound capture excludes credentials, evidence refs, URL query, actual
  procedure input, chat payload and telemetry hosts. Corrupt records are quarantined with secret-safe diagnostics;
  timed-out mutations retain store ownership and lock until the sync worker finishes.
- Release evidence: `uv build` succeeds; a fresh Python 3.14 venv outside the checkout installed the wheel with all
  declared dependencies, imported homemaster/mem0/fastembed/qdrant, read SOUL/threat-pattern package data, exposed
  exactly the six new Home tools, excluded legacy Home tools and removed all six when disabled. Real config doctor is
  PASS; a deliberate Qdrant lock conflict is WARN with file memory still readable.
- Current commit gate: the merged full non-live suite passes (`1673 passed, 1 skipped, 11 deselected`); focused Ruff,
  format, lock and diff checks pass. Commit and push are next.
- Blocking items: none. Preserve unrelated pre-existing browser changes and the user's edits in
  `src/homemaster/application/runtime.py` and `src/homemaster/tools/base.py`.

## 2026-07-27 V2.1 generic browser phase-one feasibility

- Status: `PHASE_1_IMPLEMENTED_ANT_GATE_BLOCKED`. Generic run-scoped Playwright ownership, nine canonical tools,
  immutable per-run Registry views, live DOM readback, latest-only exact refs, timeout fencing, JSONL, PNG, trace
  and WebM are implemented. Coworker migration and production policy remain phase two and were not started.
- Current evidence: browser/runtime focused `15 passed, 1 skipped`; isolated wheel import from an empty cwd launched
  Chrome 149, audited all nine tools, exercised DOM/PNG, produced a valid trace and VP8 1280x720 WebM, and decoded
  two PNG frames. Ant frontend `54` Vitest tests, Biome, `tsc --noEmit`, and production build pass.
- External blocker: the shared Linux account has `fs.inotify.max_user_watches=8192`; an unrelated VS Code file
  watcher owns 8113 watches. Umi dev refuses to start even with polling. The process was not terminated. Production
  preview is not a substitute because the exported route resolves to Umi `EmptyRoute` with an empty root.
- Full HomeMaster run: `1160 passed, 2 skipped, 28 failed`. All 28 failures share the concurrent memory-system
  validator requiring `MemoryEmbedding` in older custom-provider fixtures; browser tests and stacks are not involved.
  Do not alter that user-owned work from the browser task.
- Next: on Mac, start Ant dev with Node 22+, set `HOMEMASTER_ANT_ORIGIN`, run the deterministic Runtime Ant test,
  verify per-field DOM/SUCCESS/command/image/video/trace/cleanup, then attempt the real-provider gate. Do not mark
  `GENERIC_BROWSER_FEASIBILITY_PASS` or enter phase two before those gates pass.

## 2026-07-27 V2.1 memory system implementation

- Status: `IMPLEMENTING`. The user authorized implementation of
  `plan/V2.1/homemaster-memory-system-implementation-plan-zh.md`; its single plan-review gate was already completed
  and all nine findings were incorporated before implementation. The historical discussion document is context only.
- Current actual baseline is Git `f4c6709a898e2c819de6d9d6fd054ab9ca8bef2a`, newer than the plan's recorded
  baseline. The worktree also contains pre-existing generic-browser changes, including overlapping edits in
  `src/homemaster/application/runtime.py` and `src/homemaster/tools/base.py`; this task must preserve and integrate
  with those bytes rather than revert or overwrite them.
- Current stage: repository/config/runtime/profile/pitfall audit and WP1 dependency linchpin verification.
  `mem0ai`, `qdrant-client`, and `fastembed` are not yet installed in the project venv. PyPI wheel/source equivalence,
  compatible exact fastembed version, embedded-Qdrant CRUD/filter/hybrid behavior, offline BM25 artifact behavior,
  telemetry suppression, non-networking mem0 LLM construction, and public resource-close behavior remain
  `UNVERIFIED` until exercised in the target venv.
- Next: capture RED configuration/dependency/tool-surface tests, lock the minimum dependencies with `uv`, compare the
  installed `mem0ai==2.0.13` wheel to the locked reference source, then run isolated real Qdrant and outbound-network
  probes before implementing the file and mem0 stores.
- Blocking items: none. No product-code implementation or success claim will be made if a linchpin fails; the design
  will be revisited without adding an MCP/cloud/local fallback mode.
- Owner decision after the dependency probe: use the published `mem0ai==2.0.13` wheel and pin its deployed behavior,
  rather than requiring byte identity with the Git tag. The wheel differs from the tagged checkout only in Chroma,
  OpenSearch, and PGVector files; the selected Qdrant path and mem0 core files are identical. HomeMaster will expose
  no backend selector and tests must prove it constructs only Qdrant. The real `Qdrant/bm25` artifact was fetched
  through `hf-mirror.com` at repository commit `e499a1f8d6bec960aab5533a0941bf914e70faf9`; per-file hashes were captured,
  and a second process encoded Chinese text with `HF_HUB_OFFLINE=1`, an invalid proxy, and `local_files_only=True`.

## 2026-07-27 Gateway shorthand and signal-cleanup repair

- Status: completed. `homemaster --gateway --config <path>` is a gateway-only alias for the existing
  Feishu/Lark lifecycle. It neither starts the local interactive shell nor creates a second
  `ApplicationRuntime`; the explicit `homemaster gateway --config <path>` entry remains compatible.
- The implementation rejects `--gateway` combined with one-shot, dry-run or interactive-only options and
  accepts the same explicit config path as the existing gateway command. README, the Skills/config user guide,
  CHANGELOG and CLI help now describe the alias and the corrected Feishu/Lark WebSocket transport.
- During live handover, a real `SIGTERM` initially removed only the CLI parent and left the spawned WebSocket
  worker with an established TLS socket. Root cause was default signal termination bypassing `asyncio.run()`
  cleanup. Gateway now maps `SIGINT`/`SIGTERM` to cancellation of its Runtime service task, which owns the
  existing deadline-bounded channel stop/join path. The incident and preventive rule are recorded in
  `docs/pitfalls.md` and `CLAUDE.md`.
- Current evidence: 30 focused CLI/Gateway tests pass; Ruff, format and diff checks pass. A real alias launch
  produced exactly one gateway parent, one spawn worker and one established Feishu TLS connection. A real TERM
  then removed all three per-instance states. A fresh alias launch is currently online; no business message was
  sent during this lifecycle-only validation.

## 2026-07-24 Feishu P2P access-event ACK repair

- Status: implementation and available self-verification are complete. The supplied Feishu delivery history was
  traced to an exact boundary mismatch: HomeMaster registered only `im.message.receive_v1`, while subscribed
  `im.chat.access_event.bot_p2p_chat_entered_v1` deliveries reached `lark-oapi==1.7.1` with no processor; the SDK
  returned 500 and the platform retried each event twice.
- The access event now has an explicit no-op ACK with hashed structured audit. It never enters the IPC queue or
  Runtime. The first post-fix real user message then exposed a second boundary defect: SDK private messages use
  `chat_type=p2p`, but the channel accepted only canonical `private/group`, so the platform ACK succeeded while the
  application silently rejected the packet before Runtime and produced no new session/trace.
- The sole normalize boundary now maps exact external `p2p` to internal `private`; unknown values remain rejected.
  A locked real-SDK payload regression passes access-event ACK, message dispatch, normalization, channel acceptance,
  private bus identity and `open_id` reply routing in one chain. The focused Feishu/Gateway slice passes (`49 passed`).
- A fresh real message then created Gateway session `gw-25d72ed7d051d61c3b9eba18ea0db8f6`, persisted two
  `replied` revisions, completed two model calls with zero consecutive tool errors, and kept the fail-fast egress
  service healthy after a confirmed-success Feishu send. That reply exposed another subscribed non-business event,
  `im.message.message_read_v1`; it now also has a real-SDK-tested no-op ACK and zero IPC packets.
- Real ignored-config verification on current bytes passed endpoint business code `0`, WebSocket handshake/close,
  and the production spawn-worker start/deadline-stop gate with return code `0` and no residual worker. Ruff,
  format and diff checks pass for the changed source/test files.
- Final private-text black-box gate passed on current bytes. The user confirmed the Feishu reply was visible, then a
  third message produced revision 3 with `session_status=agent_status=replied`, three total model calls, three ordered
  user/assistant pairs, all assistant `finish_reason=stop`, and zero consecutive tool errors. No
  `message_read_v1 processor not found` or retry appeared during the subsequent 30-second retry window; Gateway and
  its WebSocket worker remain healthy and online. Full media/reaction/group/reconnect/domain Phase 9 coverage is
  still `UNVERIFIED` and is not claimed by this repair.

## 2026-07-23 V2.0 completed after final review remediation

- Status: `COMPLETED`. The plan-mandated single read-only final review found config-show credential exposure,
  missing independent management capabilities, and a missing data-only Plugin Skill adapter. All three were fixed;
  remediation covers user/model/JSONL redaction, capability declaration and denial, manifest enablement, project
  opt-in, traversal/symlink rejection, builtin override authorization, and no executable plugin import. Per policy,
  no second final review was started.
- Post-review targeted evidence: remediation regression `30 passed`; expanded Skills/service/config/factory/
  permission/Feishu/Telegram/child/Cron/installed-package slice `132 passed`; focused Ruff and format, compileall,
  `uv lock --check`, upstream port-manifest check and `git diff --check` pass.
- The reviewer residual wheel risk was closed with a stronger isolated gate. It initially exposed missing core
  Pillow and an eager MCP-extra import; Pillow is now core, MCP-only adapters lazy-load only with a manager, and the
  rebuilt wheel installs declared dependencies and constructs 39/39 tools outside the checkout (`1 passed`). Related
  upstream default-registry and MCP tests pass (`28 passed`).
- Verification on current implementation bytes: locked upstream `151 passed`; non-live suite
  `1405 passed, 7 deselected, 1 warning`; installed wheel and real child-worker/provider gates pass; manifest,
  `uv lock --check`, compileall, focused Ruff/format and diff checks pass. Full-repo format reports 85 inherited/
  unrelated files and was not used to rewrite them.
- Real installation gate reports `status=PASS`: exact Git HEAD
  `9b2efd795c6aa09f88b0c257d269a9e518da6ae7`, 9-file server `skill-creator` migration, real create/validate,
  zip/tar plus traversal rejection, Python/Shell sentinels, `packaging==24.2`, `is-number@7.0.0`, HTTPS/curl raw
  SHA-256 `db58ae9b4bc03a8bd17181ab1dafda2e294aada9d9f753dc747533d2145a5345`, and second CLI process discovery.
- Known unrelated dirty-state limitation: pre-existing Coworker source changes make
  `plan/V1.9/baseline/coworker-contract-hashes.json` stale. The V2.0 run excluded only
  `tests/homemaster/test_v19_baseline_artifacts.py` rather than overwriting that user-owned baseline.

## 2026-07-23 V2.0 OpenHarness Skills and default tools (historical implementation log)

- Historical status at the time: `IMPLEMENTING`. The user-approved plan is
  `plan/V2.0/Skills兼容与联网安装能力计划.md`; its recorded plan review remains the only plan-review
  gate. No final-code review is allowed until all 39 tools, service dependencies, black-box gates and
  documentation are complete.
- Current continuation baseline: the 15 unchanged upstream test modules pass (`151 passed`) and the
  Home parity/Skill-command/CLI/factory compatibility slice passes (`31 passed`). The upstream task
  suite still emits one `BaseSubprocessTransport` unraisable warning after the event loop closes; this
  is an open lifecycle defect, not an accepted warning.
- The 23 service-backed tool names are registered and the shared slash-Skill resolver/model override
  path is implemented. Service ownership is not complete: the first adapter revision forwards Home
  services as metadata, while several upstream tools still call process-global OpenHarness managers or
  `~/.openharness` settings directly. Those tools remain incomplete until they use application-owned
  Home services, close cleanly, and pass per-tool external terminal-state gates.
- Application-owned task/Cron/team/config/plan services are now wired through the composition root.
  Cron create/list/toggle/manual-trigger/delete, task output/update/list/process-stop, team registry
  persistence, and enter-plan/write-denial/exit-plan/write-success black-box gates pass. Upstream global
  task cleanup is awaited by the test harness; all `151` locked upstream tests now pass without the
  prior subprocess-transport warning.
- A clean checkout of `OpenHarness@9b2efd795c6aa09f88b0c257d269a9e518da6ae7` exists at
  `/tmp/homemaster-v20-openharness-9b2efd7` for this session. The generated V2.0 manifest locks 39
  default tools, 130 static reachable source files, eight bundled Skill Markdown files and 15 upstream
  test files. Regenerate/check it with
  `uv run python scripts/v20/generate_upstream_port_manifest.py --upstream /tmp/homemaster-v20-openharness-9b2efd7 [--check]`.
- Root-cause baseline: HomeMaster's former `SkillSpec` required `tool_names`, so the real Codex
  `skill-creator/SKILL.md` was rejected despite existing unit tests passing. Home profile initially had
  12 tools, not the upstream 39. New RED tests captured both failures before product changes.
- Completed core Skills migration: OpenHarness `SkillDefinition`, frontmatter parser and eight bundled
  Skills are present; Home builtin Skills remain after them. Home-only source containment, override
  authorization, provenance and diagnostics are adapters, no longer fields or capability gates on the
  Skill definition. Automatic discovery is only `~/.homemaster/skills` and project
  `.homemaster/skills`; Codex/Claude/Agents directories are not scanned.
- Completed entry wiring: `skill(name=...)` is in the Home ToolView as `openharness.skill.v1`, requires
  `tool.read`, refreshes the registry per call and returns complete original Markdown plus `base_dir`.
  Legacy `skill_view(skill_name=...)` shares that behavior. Context now exposes only an Available Skills
  summary. Coworker remains its exact 11-tool profile.
- Focused verification on current bytes: 49 Skills/context/profile/domain/factory tests passed, the
  standard `skill` pipeline refresh/read test passed, Ruff and `git diff --check` passed. The V2.0
  39-tool parity test intentionally remains RED until the remaining tool port is implemented.
- Completed shared execution/path transaction foundation: `ToolExecutionContext` now captures an existing
  canonical immutable working directory at construction; application composition passes its locked directory into
  each canonical tool executor. Permission path checks and dynamic filesystem resource keys use the same resolver,
  so execution-time `cwd` changes cannot split policy from execution. A resource lease now covers executor, output
  validation and verifier instead of being released before independent terminal-state verification.
- Focused current-byte evidence for this stage: 73 application/runtime/Pipeline/permission/Skills/V2 path tests
  passed. The V2 regression changes the real process working directory, proves a relative denied path stays anchored
  to the composition directory, and proves a second same-path transaction cannot enter its executor while the first
  verifier holds the lease. Ruff, compileall, `git diff --check`, and locked upstream manifest validation passed.
- First core-file tranche is now present in the Home profile: `read_file`, `write_file`, `edit_file`, `glob`, and
  `grep`, with OpenHarness names and schemas. Mutation uses a target-directory temporary file, flush/fsync and
  atomic replace; its verifier independently reopens the final file and checks byte count plus SHA-256 before the
  per-path lease releases. The filesystem gate wrote, reread, searched, globbed and edited actual temporary files;
  focused file/path/Pipeline/application tests passed (`47 passed`). The full 39-tool parity test remains intentionally
  RED because unported tools are still absent.
- Completed the network/process and portable-core tranche: `web_fetch`/`web_search` use an identity-only,
  strict-UTF-8, streaming HTTP adapter with `trust_env=False`, redirect limits, credential rejection and raw byte
  receipts; the HTTP black-box fixture independently rereads the response and matches the SHA-256. `bash` resolves
  cwd from the immutable working directory, preserves real return codes, merges stdout/stderr, and kills its full
  process group on timeout and cancellation; real `git --version`, nonzero stdout/stderr, timeout and cancellation
  process-table gates passed. `brief`, `sleep`, `tool_search`, `todo_write`, `notebook_edit`, and Git worktree
  create/remove are now present. TODO/notebook/worktree mutations require independent byte/JSON/Git-porcelain
  readback. Current combined V2 core evidence is `17 passed` plus Ruff, compileall and diff checks.
- Next: add explicit runtime-service injection, then port `ask_user_question`, session plan mode, config/MCP,
  provider image, LSP, Cron, persistent background task, agent/team and remaining default-tool interfaces. The
  complete 39-tool parity assertion remains intentionally RED; no live Skill install, Cron, MCP or provider
  black-box gate has been claimed yet.

## 2026-07-23 Generic screenshot observe

- 用户锁定新契约：`observe({})` 是与 benchmark 无关的通用截图工具，只向模型返回一张当前画面的
  PNG；Coworker 继续用既有 DOM 工具操作，ALFWorld 继续用既有动作工具，截图只用于模型主动确认。
- 根因已定位：旧三个同别名环境工具把显式截图与 observation ledger/provider binding/freshness
  动作门禁耦合，真实 Coworker 运行因此在浏览器正常时仍拒绝全部业务动作。
- 正式实施计划已写入 `plan/V1.9/generic-screenshot-observe-implementation-plan.md`。唯一一次计划只读评审
  已完成，5 项发现全部采纳：锁定 canonical `IMAGE_ONLY` 结果投影及运行时不变量、空 data schema、
  provider transport 真 API 接收矩阵、ALFWorld 每 episode 成功动作外部终态门，并把 Playwright/X 截图
  API 标为真环境通过前 `UNVERIFIED`。`ObservationService`、旧 benchmark observe registry、ALFWorld
  `FrameLedger`/model-view 绑定和 provider attempt 中的绑定字段已删除；Coworker 的截图/plan action gate
  及其死 failure-code 投影也已清理。`TICKET_READ` 现只由 `browser_navigate(route=ticket)` 的真实导航收据
  产生，截图不会生成 benchmark 专属评分事件。当前状态：`FINAL_REVIEW_REMEDIATION_VERIFYING`。
- 最终只读评审的三个 P1 已逐项整改：provider attempt 只记录实际序列化的图片；Coworker 的 ticket 节点只由
  导航动作计分并重锁 dataset manifest；ALFWorld 动作改读当前 THOR event，不再依赖 provider-bound 截图。
  整改后聚焦 `134 passed`，Ruff、compileall、diff、legacy-term guard 与 Coworker dataset verifier 均 PASS。
  `tests/homemaster/test_v19_baseline_artifacts.py` 仍有一项已存在的 V2 Skills 工具面快照漂移，未在本次
  observe 收口中修改；真实 Chrome/X/ALFWorld/provider 接收门仍为 `UNVERIFIED`。
- 追加最终评审发现 Gateway/飞书启用时 `ArtifactPublisher` 会在 provider message 前清空所有 image/
  attachment，`IMAGE_ONLY` observe 因此直接抛错。根因是飞书公共 artifact 投影与模型投影共用了同一份
  被改写结果。现已拆成两个投影：canonical result 原样生成模型消息，Publisher 只返回公共 artifact refs；
  集成回归同时断言第二次模型请求含准确截图、`tool.call_completed` 只携带可按 tenant/session/run 回读的
  handle。最终扩展回归 `92 passed`；本轮直接相关回归 `3 passed`，相关 Ruff lint/format、compileall 与
  `git diff --check` 均 PASS。真实 Chrome/X/ALFWorld/provider/飞书图片终态门仍为 `UNVERIFIED`。

## 2026-07-22

### Single-Feishu OpenHarness migration

- The deployment owner supplied `app_id/app_secret` and required direct ignored-YAML storage. The real
  `config/homemaster.yaml` now contains the pair with mode 0600; product config resolves a complete YAML pair before
  the legacy environment pair, rejects partial/cross-source credentials, and keeps the secret in `SecretStr` plus
  public sanitizer registration. The deployment owner then explicitly selected a fully trusted Feishu entry, so
  no bot/user open IDs or principal mapping are required and the real Gateway config is enabled.
- All non-bot senders now map to one fixed `feishu-owner` with canonical Home and Feishu group capabilities; sender
  IDs remain reply/session routing data only, and all group messages are accepted without mention. Focused config,
  Feishu, and group-operation regression passes (`50 passed`).
- Gateway composition now has one active Feishu/Lark channel and no selector/registry. `lark-oapi==1.7.1` replaces
  the Telegram optional dependency; Telegram source and focused tests remain but are outside active composition,
  public config, and docs.
- Implemented fixed trusted-owner mapping, sender-isolated private/group/thread routing,
  message-id dedup, typed delivery context, safe inbound files, reaction timing, static rich/card rendering,
  outbound media receipts, application-owned API/group resources, typed group tools, and independent group-state
  verification.
- ArtifactPublisher now persists image/attachment bytes without rewriting provider-facing content and returns only
  tenant/session/run-bound opaque refs for the public projection; Gateway emits each ref as an independent critical
  MEDIA item while the model retains media it needs to interpret.
- The installed SDK has no verified public WebSocket stop API, so the client runs in a spawn subprocess with typed
  fatal/completion IPC and terminate/join/kill cleanup. One absolute deadline covers active workers, drain,
  channel stop, and services. Dependency logs are record-level sanitized; API audit JSONL contains only action,
  target hash, duration, return code, and certainty.
- Post-change external probe on the real ignored YAML passed endpoint HTTP 200/business code 0, WebSocket connected,
  and clean socket close. Cold `lark-oapi` import took about 48.2 seconds on the HPC filesystem; the complete probe
  took about 48.8 seconds. Full non-live regression passes (`1386 passed, 7 deselected`), focused Gateway/security/
  artifact regression passes (`110 passed`), and CLI streaming regression passes (`39 passed`).
- The sole final reviewer found one high dedup rollback bug and the two already-known rich-content migration gaps.
  Dedup now uses reserve/commit/rollback; transient download and bus rejection redeliveries pass, followed by
  `52 passed` Feishu/config and `92 passed` channel/Gateway/permission/artifact targeted verification. The rich-content
  gaps were not expanded into this trusted-entry change; public docs now describe the simplified supported payloads.
- Final internal evidence on the current bytes: non-live suite `1386 passed, 1 skipped, 3 deselected`; the three
  deselections are `live_coworker` browser tests requiring absent `/usr/bin/google-chrome`. Full Ruff lint,
  changed-file format-check, compileall, `uv lock --check`, `git diff --check`, interface audit, and worktree
  side-effect audit pass. The inherited Starlette/httpx deprecation warning is unchanged. Real `lark-oapi` endpoint
  discovery returned code 0 and the WebSocket handshake connected; remaining message/media/reaction/group terminal
  states and bot self-event semantics stay `UNVERIFIED` pending Phase 9.

### Realtime Rich streaming CLI

- Status: implementation, external terminal validation, documentation, provenance, the single
  final read-only review, finding disposition, and post-review verification are complete.
- Restored generic-runtime delta publication, exact seven-event projection, per-turn streaming
  redaction, Rich/text/stream-json sinks, CLI ownership, safe result serialization, and the
  no-retry-after-visible-delta fence.
- Baseline cleanup guard fixed by excluding the two canonical pipeline files from the legacy
  term scan; baseline then passed 1295 tests with 11 deselected.
- External gates passed for live text/stream-json first byte, buffered JSON, partial provider
  failure, tool failure, SIGINT cancellation, split-secret/path/URL redaction, and interpreted
  tmux Rich final-screen state.
- The sole final reviewer found three issues: configured literals in private RuntimeEvent payloads,
  missing configured-literal sanitation for fatal `stream-json` errors, and premature result output
  before resource close. All three were reproduced with RED tests, fixed, and dispositioned without
  requesting a second review.
- Verification: 84 post-review focused tests passed; complete non-live suite passed with 1337 tests
  and 7 deselected; Ruff, format, compileall, diff, cleanup/secret guards, and the 15-port upstream
  manifest validator passed. The separate live-coworker browser gate remains unavailable because
  `/usr/bin/google-chrome` is not installed and is not claimed.
- No blocker. Implementation branch is ready for integration choice.

- V1.9 final-review remediation concentrated verification is complete: full non-live `1295 passed, 11 deselected`,
  final-review focus `193 passed`, runtime stress `5 passed`, Ruff check/changed-file format, compileall and
  `git diff --check` all pass. The inherited Starlette/httpx deprecation warning remains unchanged.
- Candidate `800d391dd780fd13e5d3116bb269a99b9b975474` passed three real Mimo live API tests, real MCP stdio and
  loopback HTTP (`21 passed`), device audit/file gates (`27 passed`, external fake backend only) and extension
  digest/reload/rollback filesystem gates (`32 passed`). This closes the internal verification task but does not
  prove a real robot SDK/device gate.
- The hkust4 Coworker canonical `.venv` editable install was repaired: its `direct_url.json` had pointed to stale
  `/data1/haodong2/weilin_workspace/Homemaster`; reinstalling the current project with `uv pip install --no-deps
  -e .` restored imports to the Coworker repository. Doctor and preflight pass without dependency upgrades.
- The temporary V1.9 candidate environment initially omitted the declared `coworker` optional extra. The first
  formal attempt `coworker-20260722-101417-86a6e389` is preserved as failed with `ModuleNotFoundError`.
  Installing lock-matched Playwright 1.61.0, greenlet 3.5.3 and pyee 13.0.1 fixed that environment boundary.
- The repaired Coworker attempt `coworker-20260722-102116-a79213c1` reached real Mimo, headed Chrome, service,
  VNC and FFmpeg: 32 provider attempts, return codes observer=0/tigervnc=0/ffmpeg=0, and a verified 255-second
  H.264 video. The changed observation freshness contract rejected every business action, leaving only 1/24
  trajectory nodes and no `presentation/events.jsonl`; formal success is false and the independent verifier
  returned 1. This is a valid “can run” reproduction, not a Coworker release PASS.
- The locked ALFWorld run `v19-800d391dd780-3a166c923d50` started all four real AI2-THOR episodes and preserved
  per-episode evidence. Results were 0/4: episodes 1 and 4 ended `execution_state_uncertain`, episode 2 ended
  setup-terminal `scan_restore_mismatch` before a provider attempt, and episode 3 was score-eligible but not won.
  Provider/runtime availability were both 1.0, harness coverage was 0.25, and the release wrapper returned 1
  because episode 2 correctly had no `provider_attempts.jsonl`. This is also reproduced, not release PASS.
- Current split verdict: V1.9 code, documentation and final-review remediation internal gates are PASS; real
  provider/MCP/filesystem gates are PASS; ALFWorld 4/4 and Coworker 1/1 formal release gates were executed and
  are FAIL under the changed observation contract. No additional reviewer is permitted or needed for this record.

## 2026-07-21

- 已恢复中断现场：HEAD `70d8e91` 的 V1.9 core CL-01～CL-16d 已提交，M0/最终真实
  ALFWorld 4/4 与 Coworker 1/1 外部门仍未完成；未提交区是 CL-17 半成品。
- CL-17 根因：loader/config 局部测试存在但未接 Home composition，installed-wheel 测试未真实构建
  wheel，provider list provenance 不完整，并错误删除了脱敏 example 配置。
- 已完成 Home one-shot/Interactive/dry-run SkillRegistry 接线、四级来源与 provenance、路径 containment、
  ToolView capability、named builtin override、model invocation gate、typed auth/env/CLI precedence、递归
  redaction，并恢复 example 模板。
- Anthropic SDK 外部符号已在本机 0.116.0 核对：`AsyncAnthropic(auth_token=...)` 构造与关闭成功。
- CL-17 已通过内部与本机黑盒门：全量 non-live `1155 passed, 7 deselected`；全仓 Ruff lint、改动
  format、compileall、cleanup guard、upstream manifest、diff/secret/config 权限通过。真实 wheel 已
  隔离安装并从源码树外发现 builtin SKILL.md；dry-run 确认 12 项 Home ToolView、CLI provenance、
  skill 路径不外泄和 `external_io=false`。
- CL-17 machine state 已写入 `plan/V1.9/execution/phase-1-cl17/state.json`，状态 GATE_PASSED；按当前
  AGENTS 规则不在实施中追加 reviewer，唯一最终代码评审保留到全部实现、外部终态和文档完成之后。
- CL-17 已提交为 `79f286f` 并推送到 `origin/codex/v1.9-release`。恢复真实 Provider 门时发现两条
  `live_api` 用例未 await 已异步化的 `LLMClient.complete()`，首次运行在发出外部请求前失败；修复后
  三条真实 Mimo/mimo-v2.5 用例全部通过，包含 system prompt、任务上下文和真实工具循环。
- ALFWorld 锁定 manifest 与外部 dataset bytes 已独立验证：selected=4、source_entries=10、
  `dataset_bytes_verified=true`。HPC2 当前项目环境尚未装入外部 ALFWorld runtime，且没有现成 X
  display；继续以 import origin 和真实 THOR preflight 取证，不把 manifest PASS 当作 4/4。
- hkust4 的 canonical worktree 位于 `/home/haodong2/weilin_workspace/Homemaster`，当前为干净 detached
  `70d8e91`；HTTPS/SSH GitHub fetch 均无远端凭据，需用不含配置/credential 的 Git bundle 同步新候选。
- 用户更正执行顺序：先在 HPC2 完成 CL-18～CL-21 全部代码、non-live 测试与文档，再由用户指导
  hkust4 和最终 4+1 live gate；在此之前不再触碰 hkust4。已进行的远端动作仅为只读定位/状态检查和
  一次因无凭据失败的 fetch，没有运行 Coworker、没有 checkout、没有修改远端代码，远端仍是干净
  detached `70d8e91`。
- 下一步：仅在 HPC2 实施 CL-18 MCP client/resources/tool adapter 与 artifact policy；全部代码完成后
  再冻结最终 SHA、同步 hkust4 并执行外部终态门。
- CL-18 代码与同源文档已完成并保持 `VERIFYING`：application 首次真实 run 前幂等 async start，
  application-owned MCP manager 支持 stdio/streamable HTTP、部分失败、timeout/cancel/disconnect、
  close isolation、typed status 和 JSONL audit；discovery 后原子注册保真 JSON Schema、重冻 Home
  ToolView 并重验 skills，ALFWorld/Coworker profile 不变。
- MCP tool/resource 原始输出现以 tenant/session/run 精确 ACL、opaque handle、quota/TTL、0600 原子
  文件先落盘后脱敏；resource URI 不进入模型，改用 opaque `resource_id`。普通 dry-run 零外部 I/O，
  显式 `--probe` 才临时连接并立即关闭。真实 FastMCP stdio subprocess 与 HPC2 loopback HTTP fixture
  已完成 handshake/list/call/resource/cleanup，本结果不是 hkust4 live gate。
- 下一步：仅在 HPC2 实施 CL-19 permission/auth/device/lease/emergency-stop foundations；CL-18～CL-21
  全部代码、文档和 non-live gate 完成前不访问 hkust4。
- CL-19 代码与同源文档已完成并保持 `VERIFYING`：统一 pipeline 默认接 capability-aware policy，
  remote Bearer 只映射预配置 typed principal；application-owned connection pool 与 generation-aware
  physical-device FIFO lease 共用 fencing owner。同设备写动作串行、不同设备并行，disconnect/stale/
  emergency-stop 在 backend 前拒绝等待动作，活动动作返回不可自动重试的 `outcome_unknown`。
- emergency-stop 要求 `device.control`，并同时核对 control 返回和外部 stopped 状态；owned/borrowed
  connection 隔离关闭，lease/fence/stop 写入 mode-0600 `device_audit.jsonl`。CL-19 相关 115 项
  HPC2 回归与 Ruff 已通过；这不是 hkust4 真机 gate。
- CL-18 阶段 reviewer 已完成并处置全部 5 项发现：tenant/principal artifact 分区、resource preview URI、
  resource audit URI、audit sink cleanup isolation 和显式 probe audit。修复按 RED-to-GREEN 完成，扩大
  MCP/artifact/application/CLI 回归 `40 passed`，Ruff/format/diff gate 通过；状态继续保持 `VERIFYING`，
  因为按用户顺序 hkust4 外部门须等待 CL-18～CL-21 全部代码完成。
- CL-19 阶段 reviewer 的 3 项高风险和 3 项中风险发现已全部处置：补齐 factory/runtime 到 borrowed
  backend 的真实 pool 接线、跨 tenant physical-device owner、disconnect/close fencing、grant 后
  backend 前复核、generation 单调性和 typed stop/state query contract。扩大 permission/auth/device/
  application/MCP 回归 `143 passed`；具体外部设备 enum 保持 `UNVERIFIED`，没有访问 hkust4。
- 下一步：锁定 CL-20 的第一个真实 channel 后，仅在 HPC2 实施 Gateway/channel/public projection；
  CL-18～CL-21 全部代码、文档和 non-live gate 完成前不访问 hkust4。

## CL-20 Gateway、Channel 与公共事件投影

- 2026-07-21：首个真实 channel 锁定为 Telegram long polling；OpenHarness/nanobot provenance 为
  `473ae5ef18394ab839a3364eee66836ef9776902`，其 OpenHarness mirror commit 为
  `9b2efd795c6aa09f88b0c257d269a9e518da6ae7`。上游无界 queue 与 per-session runtime 未直接复制。
- 已实现 typed `ChannelIdentity`/`InboundMessage`/`OutboundMessage`、exact principal mapping、
  sender-inclusive group/thread routing、attachment realpath containment、global/tenant/session
  bounded priority bus、progress coalescing、critical backpressure、deadline drain、Gateway bridge/runtime、
  cancel-and-join、restart snapshot cleanup/generation fencing、Telegram adapter 和 CLI `gateway`。
- 已实现 `PublicEventProjection` 与 `public_gateway_stream`：private RuntimeEvent 先进入 application
  ledger，Gateway 只接收 allowlist/redacted/correlated DTO；terminal result 由 bridge 统一发布，避免
  duplicate final。
- CL-20 收尾修复了 connection-pool fencing generation 与 backend application-run generation 混用的
  根因：handle 保留 pool `generation`，观察账本和 runtime 改读 `backend_generation`；新增回归覆盖
  borrowed ALFWorld handle 的真实 capture/provider binding。同步补齐三个 manifest destination、真实
  `OhmoGatewayBridge` provenance symbol、`uv.lock` baseline hash 和 legacy 文档术语。
- CL-20 阶段只读 reviewer 已完成并处置全部 5 组发现：egress 重新核对 generation/identity，Telegram
  auth-before-download，terminal final 单一 owner，全部 service task fail-fast + outbound drain 后 stop，
  progress/final/error/cancel 共用自由文本脱敏 projection。新增 RED 回归并同步 README、用户指南、
  架构、CHANGELOG、pitfall 与正向规则；没有追加 review。
- 当前 post-review targeted non-live：CL-20 及 application/device/ALFWorld 回归 `151 passed`；完整
  HPC2 non-live `1251 passed, 7 deselected`，Ruff、format、compileall、cleanup guard、manifest JSON
  和 `git diff --check` 通过。具体 python-telegram-bot 外部 API 仍 `UNVERIFIED`，不访问 hkust4。
- 下一步：仅在 HPC2 进入 CL-21；用户要求的 hkust4 真机测试继续延期。

## CL-21 Hooks 与 Plugins

- 已完成 plan reviewer 后锁定的 trusted-local MVP：显式 approval、canonical content digest、secure
  same-bytes compile、async-only hooks、content-bound plugin tools、canonical required capabilities、
  atomic Catalog registration、profile narrowing、generation-fenced hook runner 和 hooks-only reloader。
- lifecycle 已接入 application/run 两层：application start/stop 各一次，run start/end 每 run 各一次；
  blocking run hook 只失败当前 run，失败/取消仍走 run end。stop 后再执行 async extension cleanup，
  hook/cleanup 状态进入脱敏 RuntimeEvent JSONL。
- 定向与扩大 HPC2 non-live 已通过：extension/config 聚焦 25 项、extension/application 聚焦 15 项，
  application/tools/permissions/config/gateway/channels 扩大回归 239 项；stage review 后新增 timeout
  resistance、reload identity、exact capability、candidate cleanup、close/reload、empty narrowing、dir-fd
  containment、process-control 和 lifecycle/Gateway 回归，focused 为 `64 passed`。
- 本阶段唯一一次只读 stage review 已完成，9 项 findings 全部采纳并修复，没有追加 review：reload 固定
  id/version/requested/granted/tool-plane；timeout hard-fence 且保留真实 active task；exact token 不再绕过
  canonical capability；失败 candidate/partial load/Catalog collision cleanup；close 先 quiesce；空 override
  不再 fail-open；entrypoint 逐目录 fd 无 symlink；loader 不吞进程控制异常；缺失回归已补齐。
- post-review 完整 HPC2 non-live 为 `1285 passed, 7 deselected`；全仓 Ruff、12 个涉及文件 format、
  compileall、canonical manifest/state JSON 与 diff gates 通过。唯一 warning 是继承的 Starlette/httpx
  deprecation；没有 live I/O。
- 具体外部引擎/API/device 符号没有在 CL-21 新增背书，既有外部符号继续 `UNVERIFIED`。未访问 hkust4；
  全部 CL-18～CL-21 代码与文档完成后，才按用户指导执行真环境测试。
- 下一步：完成状态/文档 gate 后启动一次 V1.9 整体 final reviewer；处理其 findings 后只做针对性验证，
  不追加 CL-21 或 final review。
- V1.9 唯一整体 final reviewer 已完成并消费评审名额，报告 6 项发现：device audit sink 可阻断 lease/
  emergency-stop，MCP discovered tool 被误判只读且 attempted failure 语义 fail-open，Gateway shutdown
  deadline 未覆盖 worker join，public backlog generation 在消费时漂移，extension cleanup owner 存在
  loader/composition 窗口，以及 digest 未覆盖 adjacent helper。
- 6 项代码与同源文档修复已全部实现：authoritative device event 与 typed audit failure 隔离；MCP 未验证
  工具默认 mutating、已尝试失败为 `outcome_unknown`；Gateway 以一个 absolute deadline 硬限制完整关闭，
  generation 在 RuntimeEvent 生产时固化；reload await partial cleanup、composition 持续持有 rollback owner；
  manifest `dependencies` 纳入 digest 并只从已验证 bytes import，真实 `__file__` 不暴露部署目录。
- 当前状态是 `FINAL_REVIEW_REMEDIATION_IMPLEMENTED`，不是验证 PASS。此前 `1285 passed, 7 deselected`
  发生在这 6 项修改之前，不能作为新候选证据；按用户要求先完成全部代码和文档，再由用户指导集中测试。
  未访问 hkust4、未运行 live Telegram/MCP/provider/device/browser/ALFWorld/Coworker 测试，也不得追加
  CL-21 或 V1.9 final review。

# Historical Coworker Demo Progress

## 2026-07-20

- Presentation v2、五区观察面板、Planner/公开回复/工具/结果/异常恢复展示已实现并提交。
- Controlled normal/anomaly failure gates 已通过，12 张 incident 帧已逐张人工核对。
- 全量内部验证已通过：791 passed、1 skipped；preflight 确认 Mimo `mimo-v2.5` 配置可用。
- 第一条真实 normal attempt `coworker-20260720-022516-8c773877` 已由 Mimo 完成 24/24、14/14 和 100 分，并产出通过视频验证的 362 秒 MP4。
- 该 attempt 因 20 秒录屏停止客户端超时及清理路径重复停止被标记失败；stdout 证明第一次 stop 为 200，第二次为 500，stderr 为已关闭 FFmpeg stdin 的 `BrokenPipeError`。
- 已用 RED 测试锁定专用停止超时与会话幂等约束；实现后聚焦测试 21 passed，lint/format/diff check 通过。
- 真实服务黑盒门 `recording-stop-gate-20260720-024549` 录制外部 MP4 后连续停止两次，两个 HTTP 返回码均为 200、FFmpeg 返回码 0、响应和磁盘视频哈希一致。
- 接受 normal `coworker-20260720-024949-b7004546`：Mimo 42 次调用，24/24、14/14、100 分、终态 complete、独立 verifier PASS，7 张关键帧人工通过。
- 接受 anomaly `coworker-20260720-025635-a46d87ca`：Mimo 44 次调用，22/22、11/11、100 分、终态 rolled_back、add/remove/grep/config 外部终态与独立 verifier PASS，12 张命名帧人工通过。
- 验收报告已写入 `docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md`，保留并拒绝生命周期失败的首个真实 normal attempt。
- 最终内部审计通过：793 passed、1 skipped；ruff lint、compileall、cleanup guard、本次文件 format-check、preflight、双 bundle verifier、协议 v2、文档/secret 和 clean-worktree 审计全部通过。全库 format-check 仅保留 40 个未触及历史文件。
- 唯一一次最终 reviewer 提出两个 P2：Planner 投影失败可静默成功、provider 只验至少一个响应；两项均采纳，10 个 RED 回归覆盖根因。
- 修复后 128 项聚焦测试通过，两条 accepted bundle 在逐 Planner plan 与逐 iteration provider 配对的更强 verifier 下仍 PASS。
- 评审修复后最终全量回归 798 passed、1 skipped，compileall 与双 bundle verifier PASS。
- 下一步：提交评审修复并完成交付；不追加 reviewer。

# Historical ALFWorld Harness Progress

## 2026-07-12

- Read the user-provided task attachment.
- Loaded the planning, design-brainstorming, and implementation-planning workflows.
- Confirmed SSH access to `hkust4` and inspected branch, recent commits, and dirty worktree.
- Confirmed that no previous root planning files existed.
- Created this planning state; no product code has been modified.
- Read both V1.7 specifications and the issue record in full.
- Mapped the approved architectural invariants and all explicit implementation gates.
- Started three parallel read-only investigations: independent design review, failed-episode root cause, and runtime/symbol map.
- Inspected the Episode summary, trajectory, model/runtime JSONL schemas, implementation entry points, and the latest relevant commit scope.
- Logged the missing `jq` utility and selected a dependency-free structured parsing fallback.
- Read the main navigation, target resolution, candidate generation, manipulation, visual projection, and inspect-view implementations.
- Confirmed concrete code-level sources for navigation false positives, double parsing, invalid-action misaccounting, and feedback collapse.
- Mapped the THOR put call, result/summary dataclasses, Runner stop condition, registry, and documentation/test surfaces.
- Confirmed that prior per-Shelf claims lack a persisted raw evidence artifact in the repository and must be rerun/persisted.
- Began live-runtime discovery; ruled out the default Python and recorded the existing Xvfb displays without assuming they are usable.
- Located and inspected the dedicated `hm_alfworld` Conda environment and verified access to X display `:99`.
- Recorded exact package versions and the pre-existing `pip check` failure without changing dependencies.
- Received and incorporated the independent read-only root-cause investigation.
- Verified the model-visible manipulation JSON and identical frame hashes directly.
- Located all three candidate trial directories for the target task; exact trial identification remains pending.
- Identified the exact historical trial as `trial_T20190908_122154_042763` from the Pencil pose encoded in the trace objectId.
- Received the first independent design review with verdict `FIX`.
- Dispositioned every review item and revised both V1.7 specs without changing product code.
- Reduced the design alternatives to four and retained the already approved same-target bounded local-pose approach.
- Built and linted a direct-ALFWorld runtime contract probe under ignored `var/alfworld-evidence`; it imports no HomeMaster product logic.
- Ran three controlled-Xvfb contract probes with exit code 0 and persisted JSONL, raw events, frame hashes, and PNGs.
- Verified reachable/Teleport/frame/Pickup/Put/open contracts and recorded the rejected interactive-pose action.
- Preserved toggle as UNVERIFIED after three real failures; the failure detail proved that raw THOR strings can leak objectIds and coordinates.
- Built and linted the all-six-Shelf characterization script; pure-function tests prove its candidate ordering is independent of input list order. Independent review is in progress before execution.
- Completed the independent pre-execution characterization review with verdict `FIX` and dispositioned all eight original blockers plus the two-stage-budget supplement; every finding was accepted.
- Split implementation ownership to avoid concurrent writes: this batch modifies only `independent_alfworld_probe.py`, while a separate state-machine batch owns `alfworld_pose_characterization.py`.
- Updated the probe evidence writer to reject nonempty output directories and artifact overwrites, reject NaN/Infinity canonical JSON, and record separate canonical-content and persisted-file hashes.
- Added a unified tri-state external read covering return status, inventory, exact Pencil/all Shelves, parent/child state, actual pose, detections, frame identity, reachable fields, and goal status without collapsing missing data into empty state.
- Made every returned `thor.step` persist a raw event; exceptions and missing events now preserve explicit call/read status and make the probe exit nonzero unless the exception was declared as the expected result of a negative contract probe.
- Ran the project-environment Ruff and bytecode checks successfully, then passed pure negative tests for NaN rejection, dual-hash recomputation, nonempty-directory rejection, returned-event raw evidence, and exception-to-missing-state handling. THOR was not started.
- Received the helper negative review and accepted all five blockers: metadata-safe projections, strict bbox/frame observations, stale-action rejection, real terminal-state gates, and nonzero Teleport proof.
- Added the pre-existing ALFWorld test/Ruff baselines to findings so later product work cannot misattribute one Runner failure or 37 lint findings to this evidence helper.
- Recorded and corrected the failed remote-test invocation pattern: remote scripts run through SSH stdin rather than using a remote path as a local `workdir`.
- Re-ran the corrected six-Shelf characterization in a fresh `shelf-characterization-v3` directory; all six exploration instances passed exact navigation and put terminal gates.
- Derived the fixed production budgets as navigation `65/66/34804 ms` and local put `9/17/5669 ms`, then passed Shelf 3/4/6 production independently under those limits.
- Integrated the common put executor into the Adapter with strict exact-instance resolution, PoseContext validation, current-pose-first retry, exhaustive terminal classification, and correct `isPickedUp` reads.
- Added model-visible structured put feedback, deterministic detail redaction, terminal dispatch gating, ordinary Episode outcome/coverage, and long-horizon taskset outcome propagation with remaining subtasks marked not-run.
- Removed `robot_inspect_view` from the public registry and prompt.
- Corrected backend action accounting so `GetReachablePositions` counts as a THOR action and Adapter results propagate navigation/put counts into `EpisodeOutcome` and trace data.
- Fixed the production budgets in product defaults and wrote the final evidence back into both V1.7 specifications and the issue record.
- Ran three independent product-Harness Xvfb processes for Shelf 3/4/6; all exited 0 and passed THOR return, inventory, `isPickedUp`, exact parent/child, goal `1/1`, and final-image pixel gates.
- Added CLI coverage/formal-score output with RED-first tests; CLI tests pass.
- Recovered one shared-worktree synchronization error that temporarily overwrote newer taskset type fields, re-synced the authoritative remote file, and reran all affected tests.
- Added README guidance, an ALFWorld user guide, an architecture document, CHANGELOG entries, and launched the required independent serious-bug postmortem for `docs/pitfalls.md` and `CLAUDE.md`.
- Added per-candidate navigation/put JSONL trace events, raw event refs/hashes, budget usage and terminal events while recursively excluding internal execution fields from model-visible results.
- Tightened held-state proof to require `isPickedUp=true` and put success to require `isPickedUp=false`; missing values now terminate uncertain. Added an `ExecutionBackend` implementation audit.
- Removed the residual tools-side `robot_inspect_view` factory, executor and tests; only historical trace fixtures and absence assertions remain.
- Re-ran the final product black-box gate after all trace/state changes in `product-harness-v2`; Shelf 3/4/6 each exited 0 and independently passed return/state/goal/frame assertions.
- Cleaned an ignored Python 3.10 `__pycache__` directory that alone violated an import-boundary test, and renamed a blocked legacy cleanup term without changing grounding behavior.
- Final repository verification: `352 passed, 1 skipped`; focused ALFWorld benchmark `121 passed`; Ruff check, Ruff format check, compileall, cleanup guard and `git diff --check` pass.

## 2026-07-13

- Recovered the interrupted V1.8 continuation and checked both existing pose-investigation subagents; both were complete and had made no file changes.
- Confirmed the canonical `hkust4` worktree is clean at `0fdfeaa` and that the older local staging tree is stale and unsafe for synchronization.
- Created a fresh isolated clone from the canonical remote commit.
- Added the V1.8 Oracle-pose and typed-feedback design draft without modifying product code.
- Marked all direct HomeMaster uses of observed ALFWorld runtime internals `UNVERIFIED` pending the Phase 0 true-environment gate.
- Began the required independent design-review phase.
- Completed the independent V1.8 design review with verdict `FIX` and a separate read-only code-boundary audit.
- Accepted and dispositioned every review item; no finding was rejected.
- Revised the design to remove hidden-object/legacy navigation bypasses, make typed feedback the sole Adapter-to-Dispatcher authority, split runtime feasibility from product black-box verification, define context rebase/consume/invalid transitions, cover all public actions, and separate responsibility metrics.
- Started the final design-document self-audit; product code remains untouched.
- Completed the revised-spec self-audit: Markdown fences are balanced, `git diff --check` passes, and the changed documents contain no API token/credential pattern.
- Added a Chinese CHANGELOG entry that states the problem, selected design, impact and explicit design-only/UNVERIFIED boundary.
- Synced the five-file design scope to `hkust4`; local and remote SHA-256 values match for every file, and the remote worktree contains no other change.
- Logged and isolated one non-mutating remote check quoting error; the equivalent local fence check already passed and the remote secret check is rerun separately.
- Completed remote staged-scope verification and pushed the five-file, design-only V1.8 commit on `visualagentloop`; no product source, tests or runtime configuration changed.
- Handoff state is now user design review. Implementation planning and product changes remain blocked on explicit approval, followed by Gate A.
- Re-read the user attachment and all seven authoritative V1.8/project state documents before continuing.
- Confirmed the canonical remote worktree is clean and both local/remote branch refs equal `22cb122e1b186c8e3877cd6504f805685e1bbfc7`.
- Recorded the user's explicit implementation approval and fixed the review budget to one implementation-plan review plus one final code review; the architecture will not be reviewed again.
- Started read-only code-boundary mapping for Oracle execution, typed feedback/scoring, and the Gate A exact-instance matrix. No product code has been changed.
- Ran the pre-change focused baseline with the remote Python 3.11 environment: `145 passed in 2.11s`; no product code or dependency changed.
- Completed three read-only boundary maps for Oracle/action execution, typed feedback/scoring and Gate A. They made no file changes and did not endorse external runtime symbols.
- Proved the original artifact cannot deterministically recover four exact trials; synchronized the specification to use an 18-trial candidate-complete Gate A/B superset and a transparently labeled fixed ten-task regression manifest.
- Wrote the exact V1.8 implementation plan and began main-agent coverage/placeholder/type-consistency self-review. No product code has changed.
- Completed main-agent plan self-review: specification sections 5-15 map to tasks, all closed errors/actions/metrics are named, 20 locked trial paths exist, 62 plan and 62 spec Markdown fences are balanced, secret/red-flag scans are empty, and `git diff --check` passes.
- Completed the only independent implementation-plan review with verdict `FIX`; accepted and dispositioned all ten findings in the plan/spec without a second review or product change.
- Advanced to Gate A exact-case discovery and standalone runtime feasibility. Product source remains unchanged.
- Applied the latest AGENTS rule to delivery order: documentation and serious-bug learning now precede the single code-review round; no additional review round was introduced.
- Continued Gate A execution with three isolated helper owners plus a separate read-only smoke root-cause investigator; product source remains untouched.
- Received the completed Gate A controller/input helper: the 20-trial matrix, two-stage controller, strict hash/count/return-code gates, process-group timeout cleanup, and fake 20-discovery/166-case orchestration checks pass. These are helper-internal checks only; no THOR case has passed yet.
- Confirmed the Gate A worker is still implementing the standalone probe and the independent verifier is still implementing per-action terminal-state semantics and its CLI. Gate A remains in progress and all external symbols remain UNVERIFIED.
- Independently parsed the controller's actual flat fake-summary schema and recomputed all 166 case workers: 86 returned 0, each negative code 41-44 appeared exactly 20 times, all case IDs were unique, and no worker timed out or had incomplete cleanup. This remains internal controller evidence only.
- Completed a separate staged true-environment diagnosis of the failed minimal Oracle smoke. Reset and the exact `TeleportFull` move succeeded; ndarray bbox validation/serialization caused the missing JSON and exit 1. Normalizing the bbox alone produced exit 0 and a positive-area exact detection.
- Isolated the Unity `ArgumentNullException(name)` to ai2thor close/teardown by timestamp and source inspection. It recurred after the successful normalized run and left no process residue, so it is not evidence that reset or movement failed.
- Received the independent Gate A verifier and corrected one missed formatting gate. Main-thread rerun now passes compile, Ruff lint and Ruff format; its 20-trial/100-case fixture exits 0, while terminal-state tamper, RGB/hash tamper, transitive forbidden import and missing raw artifact independently fail with the expected nonzero exit classes and locations.
- Completed the first three-helper static/CLI pass, then stopped before remote execution after finding two composition false-positive paths: no independent discovery-before-action checkpoint, and no enforced complete Oracle-map/five-location-profile coverage.
- Started targeted helper corrections with separate file owners: controller two-phase orchestration, probe exact Oracle/profile evidence, and verifier discovery/run plus per-instance mutation gates. Product source remains frozen.
- Main-thread cross-file execution rejected the first supposedly final split controller artifact: verifier discover exited 2 on a missing context expectation field. Returned the controller schema/fake to RED and kept the verifier strict; no remote Gate A work was started.
- Split v6 reached 198/198 fake case semantic PASS, then failed the independent source-identity gate because a controller trial-hash summary fix changed its bytes. Invalidated v6 and started a clean v7 freeze/run from final helper bytes.
- Froze final helper/fake bytes and completed split v8 cross-file validation. Main-thread verifier calls independently pass both phases: discover freezes 198 unique cases from 20 trials with zero case workers, and run verifies 198/198 cases with zero discovery workers and zero failures.
- Kept verifier reports at `external_symbols_status=UNVERIFIED` even when synthetic artifacts pass; only the upcoming real `hkust4` runtime, per-case return codes and external terminal states can change the main-agent verification conclusion.
- Added the ndarray bbox/teardown timing lesson to the top of `docs/pitfalls.md` with archived byte-matched smoke evidence refs.

## 2026-07-14

- Confirmed the prior Gate A controller and verifier agents completed; the worker is no longer live, while its finalized probe artifact and recorded hash are present. No agent is still blocking discovery.
- Restored `task_plan.md`, `findings.md`, and `progress.md` under the planning-with-files workflow and confirmed only documentation/evidence-planning files are dirty; product source remains unchanged.
- Confirmed the first real discovery run failed at `episode-0001-candidate-1`, exited before any test action or manifest freeze, completed cleanup, and left a complete raw reset plus result/Oracle/movable artifacts in `discovery-run-001`.
- Parsed the raw artifacts directly: observed scene is `FloorPlan219_physics`; all four Statue surface candidates have reciprocal Oracle-backed parents but fail strict visibility; the 24-entry Oracle artifact has no FloorLamp; positive `use` and all movable profiles are therefore absent.
- Extracted all six task-relevant raw objects: two toggleable FloorLamps exist but are invisible/unmapped, while all four pickupable Statues are invisible under the metadata+bbox strict gate (including one bbox-only false positive that the gate correctly rejects).
- Logged a failed repository-relative dataset-path assumption and switched the next investigation to recover the actual ALFWorld data root from process/config evidence.
- Recovered the authoritative ALFWorld root and runtime versions from the immutable process record, then traced the exact probe branches that exclude reset-invisible semantic movables and toggleable fixtures. External-controller intent is still being verified before any fix.
- Completed the raw-object RCA: all task objects, reciprocal relationships, bboxes, Oracle membership and return/cleanup evidence were recomputed from immutable artifacts; no helper or product file changed.
- Completed the scene-name RCA: source plus 541 real events prove a base-name versus Unity-asset-name comparison defect, not a wrong-scene reset; FloorPlan10 runtime support remains UNVERIFIED.
- Verified in installed ALFWorld source and a real-environment constants query that the Oracle controller map intentionally covers static receptacles only. Non-receptacle FloorLamp/DeskLamp/Statue targets cannot be assumed to have direct entries in that external map.
- Completed the third execution RCA. It independently reproduced the use/movable filters, proved `receps.json` and the persisted Oracle map are identical, and traced worker exit 2 through the controller's immediate stop before manifest/verifier execution.
- Confirmed all three RCA agents are complete and no background agent remains active.
- Reconciled the failure with the approved V1.8 spec: a helper-only eligibility/count change would mask a real product pose-source gap. Entered the brainstorming design gate and kept all helper/product code frozen pending the upstream scan-policy decision.
- Started three parallel read-only root-cause investigations for raw object facts, scene-name provenance, and helper filter/data-flow attribution. No fix has been attempted.

## 2026-07-15

### Gate A V3 Main-Thread Continuation

- Restored the reviewed implementation plan and live state in the isolated local worktree; confirmed remote `hkust4` HEAD remains `22cb122` and product/config/test code is still unchanged.
- Recomputed local helper identities and independently reran all three v3 suites. Probe passed 12 checks, controller passed 19 checks, and verifier reproduced one schema RED: a valid five-field fixture pose was incorrectly validated as a full seven-field `TeleportFull` request.
- Traced the failure to the two validator calls inside `_validate_v3_visibility_fixture()`. Changed only those calls to the existing five-field actual-pose validator; verifier compile and all 16 mutation/wiring checks now pass locally.
- This is internal evidence only. The helpers have not yet been synchronized to `hkust4`, and no real v3 discovery/case external terminal state has passed.

- User selected design option A: allow a deterministic, audited pre-model scene scan to build one exact-object Oracle pose snapshot, while preserving single-pose action-time execution and the ban on expert/candidate fallbacks.
- Re-entered the brainstorming design workflow and kept helper/product code frozen while validating three implementation boundaries and their trade-offs.
- Located the reset-to-first-provider boundary in the existing Adapter/Runner. The general pose snapshot must be built and the exact initial pose restored before `reset()` returns or saves the model's initial frame; setup actions need separate accounting.
- Logged and corrected one read-only source-search path error; no files beyond planning state were changed.
- Identified a second design choice: whether pre-scan observation authorizes target discovery or only supplies a pose after current-view discovery. The recommended boundary preserves strict current visibility so setup Oracle data cannot leak hidden targets.
- Read the installed FloorPlan 10/219/308 layout maps without running THOR. Their canonical scan sets are bounded at 15/20/13 unique poses respectively, plus one restore action; target coverage remains UNVERIFIED pending Gate A.
- Mapped scan failure into the existing closed responsibility taxonomy and identified the typed reset-result boundary needed to terminate before any provider request while preserving setup-action accounting.
- Reconciled the snapshot with long-horizon goal changes: scan once per scene generation, retain static entries, and make moved-object entries deterministically stale via per-entry state signatures rather than rescanning or using stale poses.
- Received the integration-boundary and independent risk analyses. Both recommend a HomeMaster-owned same-environment reset transaction, a single published snapshot authority, typed reset failure and separate setup accounting; neither modified files or ran THOR.
- Explicitly narrowed the new exception: fixed target-independent setup scanning is allowed, while action-time search, early stop, alternate-pose retry and fallback remain forbidden.
- Completed all three read-only scan-design investigations. They covered algorithm/source constraints, product integration, failure/accounting and independent risk; no THOR run or product/helper edit occurred.
- Independently verified the historical FloorLamp control: its known-good pose was geometry candidate 1 and is absent from both static pose sources; navigation succeeded twice, while the exact Toggle terminal contract remains UNVERIFIED.
- Prepared three architecture choices and selected the bounded same-environment geometry snapshot as the recommendation pending user approval.
- User approved the failure boundary: if no usable direct or parent pose exists, return a Harness navigation error. Clarified and recorded that an unusable locked parent pose terminates without trying a second pose.
- User approved beginning the revised design delivery. Entered Phase 15a to update the V1.8 spec/CHANGELOG/live state, perform one scoped major-delta design review and self-audit, and commit only design documentation; product and Gate helper code stay frozen.
- Completed the first whole-spec symbol/section map and confirmed the scan replacement affects decision, component, navigation, accounting, migration, validation and invariant sections; began an atomic design rewrite rather than a fixture-specific patch.
- Rewrote the V1.8 decision summary, first-real-Gate-A evidence, goals/non-goals, four alternatives and external runtime contract around the approved bounded reset scan and sole snapshot authority. Expert/task-conditioned/action-time fallback remains forbidden; no product/helper code changed.
- Added the typed reset transaction, frozen scan plan, immutable per-ID snapshot, world/restore digest, object-state freshness and sole store interfaces. Rewrote anchor resolution so current visibility authorizes movable/toggle/fixture navigation and a locked direct/parent pose failure cannot select another pose.
- Updated navigation, setup failure mapping, the unified gateway, separate setup/model/total counters, scan observability and migration guards. The spec now permits only one pure one-pose geometry rule inside reset setup and explicitly keeps it unreachable from action-time execution.
- Resolved two consistency findings during drafting: split scan locking into pre-query policy freeze and post-query/pre-Teleport plan freeze with count `1+N+1`, and split stable scene-reset identity from changing goal-trial identity for tasksets.
- Rewrote Gate A/B, hidden-object/context/feedback tests, implementation order and final invariants for real setup scan/restore, per-ID snapshot evidence, zero-provider setup terminals and strict direct/parent single-pose behavior.
- Logged one read-only spec-map agent transport failure; it made no changes and will be retried only as a narrower final-draft audit.
- Accepted and fixed the consistency review's pose-freshness finding: separated `relocated` from unknown `stale` and excluded non-geometric Open/Use/state transitions from the pose freshness fingerprint.
- Resumed Phase 15a through the existing planning files; the design-only boundary and frozen product/helper scope remain unchanged.
- Received three final-draft Gate blockers from the independent consistency reviewer: scan-plan provenance/initial observation, exact coverage-state definitions, and a public non-leaking exploration prefix for initially invisible positive targets. Began main-thread evidence and contradiction audit before editing the design.
- Logged an unavailable `request_user_input` tool invocation during recovery; it made no state change and will not be repeated.
- Confirmed the resumed worktree contains only the revised spec, live planning state and the pre-existing pitfalls update; product/helper code is unchanged, the old implementation plan remains untracked, and CHANGELOG is still pending.
- Logged one harmless poll of a nonexistent deferred exec cell; no command ran and no repository or external state changed.
- Completed a full main-thread read of the revised specification. Confirmed four cross-section blockers rather than local wording defects: deduped scan requests lose provenance, reset result types cannot encode early/uncertain failure, Slice context semantics assume an unverified ID contract, and Gate B incorrectly binds the scene snapshot to the changing goal trial.
- Locked the next edit scope to the spec schema/state machine/Gate A-B/review disposition plus live state and CHANGELOG; product code, Gate helpers and the old implementation plan remain frozen.
- Inspected the existing long-horizon boundary and confirmed `advance_goal()` invokes external `thor_env.set_task(...)` while swallowing goal-state read failures. Added an explicit typed/control-accounting correction to the revised design scope.
- Accepted the two bounded review verdicts as FIX. Remaining edits are now limited to selected-pose public witnesses, reachable hash separation, live scene preservation across goal advance, root-owned taskset control terminals, portable path containment, and final type-combination closure.
- One wide reset/goal typed-contract patch was atomically rejected after surrounding text changed; no partial write occurred. Switched to narrow current-context patches.
- Completed the first frozen-draft mechanical gates: `git diff --check` passes and the revised spec has 70 balanced Markdown fences. Logged one no-op deferred-cell poll failure with no state change.
- Completed scoped delta disposition, changed the spec status to review-complete/awaiting user review, rewrote the Unreleased CHANGELOG entry, and retained the directly related ndarray/teardown pitfall. Product/helper code and the stale untracked implementation plan remain outside commit scope.
- Re-ran the final-draft whitespace/fence gates after all design edits: `git diff --check` passes and the spec still has 70 balanced Markdown fences.
- Final placeholder and credential-pattern scans found zero matches. The stale-semantics scan found no raw-reachable hash, goal-bound snapshot, unconditional Slice rebase, map-only status or old setup/model-only counter wording; remaining matches are explicit required-coverage failure and GateCase denial rules.
- Closed failure-code reference audit: all 22 `SetupFailureCode` and all 9 `GoalAdvanceFailureCode` values have at least one disposition/use beyond their declaration. Final tracked scope is six documentation/state files and no product/helper code.
- Froze candidate spec bytes at SHA-256 `cc33486e831882b27e7384152b7f959a5a4ea1e4726268e6ef59746f77e077e3` and asked the same consistency reviewer for a final-byte contradiction check only.
- Main-thread final-section reread found and fixed one root/subtask control-accounting contradiction: all `set_task` control actions now live only in the taskset root ledger, while every subtask row remains model-only and not-run rows remain all-zero. Also closed GoalAdvance success/terminal cleanup/disposition combinations.
- Superseded the candidate hash with final spec SHA-256 `24c8abd5e0db8e80ea926a7f70aec93c090c8334e1c8ea6458ab1aa59cf811e8`; the byte-exact reviewer is checking only those final two deltas plus fence parity.
- Received byte-exact PASS for final spec SHA-256 `24c8abd5e0db8e80ea926a7f70aec93c090c8334e1c8ea6458ab1aa59cf811e8`: full 1,555-line contradiction audit, 70 balanced fences and `git diff --check` pass; no external symbol was endorsed.
- Pre-commit recheck preserved the exact spec SHA and returned a clean `git diff --check`; final staging remains pending so the stale untracked implementation plan cannot enter by accident.
- Final fence count remains 70 and the combined placeholder/credential scan has zero matches. Proceeding with explicit six-file staging only.
- Created one local design-only commit with exactly the six tracked docs/state files, then amended that same unpushed commit to include the requested test evidence; its body matches the Unreleased CHANGELOG entries.
- Direct push was safely rejected because `origin` is a non-bare worktree with `visualagentloop` checked out. Read-only audit confirmed remote HEAD is still `22cb122` and its five dirty files are this task's earlier synchronized draft; `docs/pitfalls.md` already matches final bytes and the remote untracked implementation plan remains untouched.
- User explicitly instructed not to push and will handle publishing later. Stopped all remote synchronization/commit work; remote branch remains unchanged.
- Began requested verification against unchanged product code: use the known `hm_alfworld` project interpreter for focused ALFWorld/Runner/Dispatcher regression, then full repository tests and static/mechanical gates.
- Focused ALFWorld/Runner/Dispatcher regression passed `145 passed in 3.97s`, matching the pre-change test count.
- Full repository pytest returned `351 passed, 1 skipped, 1 failed in 13.47s`; the only failure is `test_no_legacy_terms_remain_in_tracked_product_files`.
- Root-cause audit proved the cleanup guard globally blocks the generic word `deterministic`, while untouched baseline commit `22cb122` already contains it in the V1.7 spec and an existing execution test. Kept the FAIL visible and made no product/test change; continuing with all other tests and static gates.
- Excluding only the proven baseline cleanup guard, the rest of the repository passed `351 passed, 1 skipped, 1 warning in 11.00s`; warning is the existing `jieba/pkg_resources` deprecation.
- Ruff lint reports 39 pre-existing errors and Ruff format reports 41 unchanged files; all are under clean `src/`/`tests/`, not this six-file design scope. Preserved both FAILs without mutation. `compileall -q src/homemaster tests/homemaster` passes.
- Post-test document identity remains exact: spec SHA-256 `24c8abd5e0db8e80ea926a7f70aec93c090c8334e1c8ea6458ab1aa59cf811e8`, with 70 balanced Markdown fences.
- Post-test placeholder/credential scan and `git diff --check` pass. Remote HEAD remains exactly `22cb122e1b186c8e3877cd6504f805685e1bbfc7`; remote status contains only this task's earlier five docs plus the untouched stale implementation plan, with no product/test change.
- User explicitly ordered rewriting and executing the implementation plan immediately, with no further review and no push. Loaded `writing-plans` and `executing-plans`; the existing untracked plan is confirmed stale because it still uses the map-only store, trial-bound snapshot, old review tasks and lacks typed reset/control/public discovery.
- Started three parallel read-only implementation maps for reset/snapshot, Runner/control, and Gate runtime boundaries. They are implementation inputs, not reviews.
- Main-thread code map confirmed no existing scan/snapshot/reset module; `execution.py` and the 3,000+ line Adapter currently own the relevant responsibilities. Locked the replacement-plan file split around focused snapshot/reset modules plus existing execution/runtime binding.
- Confirmed the remote Gate A controller/probe/verifier/matrix scaffolding exists. The only real run stopped after the first discovery trial and produced no frozen exact cases; preserved it and reserved fresh output paths for the replacement scan Gate.
- Replaced the stale implementation plan wholesale with a 553-line, ten-task execution plan aligned to final spec SHA `24c8abd5...`; it has 38 balanced fences, no review/push task, Gate A before product edits, and mandatory full completion of all ten real-API Episodes.
- Plan self-audit removed all placeholder-like values, closed the single-gateway/reset-publisher call graph, retained only explicit no-review/no-push constraints, and found no stale trial-bound/map-store API reference.
- Final plan self-audit passed 19 required-boundary checks with 38 balanced fences; marked the checklist complete and entered execution without review.
- Entered `executing-plans` Task 1. `verify_gate_a.py self-test-v2` produced the expected RED (`invalid choice`, only old `discover/run` exist); no product source changed.
- Pulled the five ignored Gate A helper/input files into the local ignored evidence tree and assigned non-overlapping implementation ownership for probe, verifier and controller/matrix v2.
- User requested main-agent implementation with no broad subagent use; stopped all three current implementation owners and will not spawn another until the one final review after code/tests/Gate B/docs.
- Retained their landed partial work: probe discovery v2 is wired but case_main remains old; controller/matrix v2 is substantially wired; verifier self-test-v2 is GREEN but actual discover/run still needs a main-thread v2 branch.
- Main-thread post-interrupt baseline: all three Gate helper files compile; verifier `self-test-v2` independently passes the valid fixture and all ten named negative mutations.
- Controller `self-test-v2` independently passes 12/12 gates. After correcting two schema assumptions in the audit command, main thread proved all 20 v1/v2 trial rows identical on locked fields and the 25-item public vocabulary hash valid.
- Resumed Task 1 in the main agent and proved the previous GREEN was incomplete: `case_main()` still called `oracle_lookup_twice`, its CLI had no matrix input, and `verify_case_bundle()` accepted count-only v2 case results without loading setup artifacts. The three-entry AST gate failed exactly those paths before repair.
- Rewired every case worker to consume the frozen matrix/hash, perform a fresh reset/query/complete scan/restore transaction, start tested actions from the verified restore event, and resolve the only pose from the fresh snapshot. The old Oracle-map lookup is no longer reachable from `case_main()`.
- Extended the independent run verifier to reload and recompute every per-case policy/reachable/plan/addressability/snapshot/restore/coverage/witness binding, compare fresh and discovery snapshot identities, verify tested/setup/model counts, derive one Slice identity mode, and atomically write immutable `independent-verification.json` only on complete PASS.
- Added a probe self-test and expanded controller/verifier entrypoint audits. Local results: probe 7/7, controller 13/13, verifier 10/10 mutations plus 3/3 wiring checks; all three helpers compile and pass Ruff lint/format.
- Ran an orthogonal temporary case-artifact check through the actual new verifier function: the valid setup produced zero failures; replacing `oracle-snapshot.json` with `{}` produced four artifact/setup failures. No remote bytes or product source changed yet.
- Corrected the execution host to `hkust4` and preserved the pre-run helper bytes under `helper-archive/pre-discovery-run-003`; the archive hashes match the bytes used by `discovery-run-002`.
- Added RED checks for the missing v2 matrix adapter, legacy `discovery_contract` rejection, and completed-transaction evidence retention. The fixed remote helpers hash to probe `1b1ca9efa0d9396d2adbef691580588f3c0b712fee6fdde91622e2ca394d0a15`, controller `58b0b435cd95ba8345129015d610f7905c3ba113b0fe7d9504b48fb5057b58cb`, and verifier `7cb32fdd7288fe7ac3dcbf215c8d0f10de91a7ea1cec53212e599b1badbb655a`.
- Ran real `discovery-run-003`. The first worker exited 2 with `cleanup_complete=true`; the repaired result retained all raw refs, 27 setup actions, zero tested/model actions, 40 frozen cases, and no Gate/THOR residue.
- Independently recomputed the remaining failure: `FloorLamp|-00.65|+00.27|+04.43` is strict-visible only at scan step 17 with bbox area `13455`; that step has only target-derived geometry provenance and no visible public receptacle. The approved public-witness contract therefore rejects it exactly as specified.
- Stopped before any structural relaxation, verifier bypass, case run, or product edit. The next action requires the user's upstream authorization choice.
- User resolved the Run-003 architecture gate: offscreen public semantic targets remain navigable from the frozen direct snapshot pose; only descendants of a closed openable container are temporarily non-navigable.
- Locked the closed-container response as non-terminal `target_not_visible`, zero backend movement, no parent hint, and no automatic parent navigation/Open. Continued model exploration can naturally terminate at the existing no-progress/tool limit as Agent failure.
- Entered written-spec self-review under the brainstorming gate. Product code and Gate helpers remain frozen; no `discovery-run-004` or implementation begins before the user reviews the reconciled spec.
- Reconciled the older `hkust4` dirty spec hunks against the local current draft; all substantive requirements are retained or expanded, while the old status line is superseded.
- Found one written-spec contradiction before implementation: reset-only generic grounding cannot select a newly exposed closed-container child. Also found the corresponding post-open navigation transition missing from Gate A/B.
- Verified the existing runtime-limit endpoint in source/config: consecutive error threshold 5, no-progress threshold 20, ALFWorld tool-iteration cap 1000; all three terminate as Agent-owned failures rather than Harness failures.
- Repaired the written-spec grounding contradiction: frozen-addressable generic selection remains first; only an empty frozen-addressable set can use a stable current-visible/no-closed-ancestor fallback. Explicit ordinals remain frozen and never fall back.
- Corrected containment time semantics so reset `closed_ancestor` cannot block a child forever after a verified Open, while Open alone still cannot authorize an occluded child.
- Added initial-failure -> child-parent-independent public exploration/Open -> current-visible child navigation black-box gates, plus a negative Open-but-still-occluded case. Marked historical D5/D6 review rows explicitly superseded/narrowed by the user-led §16.3 delta.
- Final resolver reread removed one stale early-return phrase: an empty frozen-addressable set is not enough for `target_not_visible` until the stable current-visible fallback is also empty.
- Reworded the post-open Gate so it is executable on immutable ALFWorld trials: the public exploration sequence/hash is committed before containment evidence is read and reused byte-for-byte across paired placements; the test does not assume it can reposition hidden objects.
- Confirmed the same guard values in the real `hkust4` config, not only source defaults.
- Completed written-spec self-review at 1,591 lines and SHA-256 `eaa9c97f82aee45c08a4e388189f68ef56cdea73d65e9d34e88e011c781c4ba3`; fences, placeholders, credential patterns and whitespace gates pass.
- Synchronized the five committed design/live-state files to `hkust4` after proving remote-only live-doc lines were either retained or explicitly superseded. Per-file local/remote SHA-256 values match, remote `git diff --check` passes, and product/helper bytes remain untouched.
- User supplied a new visible-only navigation constraint before implementation, superseding commit `62bdd6b`'s screen-off authorization. Re-entered brainstorming; product/helper code remains frozen.
- Mapped more than thirty affected spec references across target resolution, navigation semantics, Gate A/B, invariants and review history. Paused before editing the authoritative spec to clarify whether Drawer/Cabinet/Shelf are also visible-only.
- User confirmed all physical targets are visible-only, including Drawer/Cabinet/Shelf/CounterTop. Locked the common zero-action miss behavior and removed containment from navigation authorization in the pending design.
- User approved retaining reset pre-scan/sole snapshot only as post-visibility execution data. Began whole-spec rewrite mapping; product and Gate helpers remain frozen.
- Completed the second-half rewrite map. Locked a same-target visible/invisible paired Gate that proves snapshot existence cannot authorize an invisible call.
- Rewrote the spec through Gate A/B, implementation invariants and historical disposition. First reverse scan has no live screen-off success branch; one stale coverage name and an explicit call-order guard remain.
- Added the call-order guard and renamed pose coverage. Fresh reread found trace event ordering and Gate-fixture no-leak list still need correction before the next audit.
- Corrected trace ordering and fixture no-leak scope. Gate/history reread identified three narrow consistency edits before the full mechanical scan.
- Closed those consistency edits, then strengthened current-visible semantics to require exact event/model image identity before pose lookup. Updated paired Gate fixtures and the core invariant accordingly.
- Completed the whole-spec self-review for the approved visible-only design: 1,614 lines, SHA-256 `b1ee5ef4b402ab55bed793941c86c1b5f3fd900035db8142499b1f5b848c5fac`, 70 balanced fences and all mechanical/semantic scans pass. Preparing an explicit five-file design commit/sync only.
- Amended the superseded containment-design commit to `docs(alfworld): require current-visible navigation targets`; its body is byte-for-byte the updated CHANGELOG entry. No push was attempted.
- Synchronized only `CHANGELOG.md`, the authoritative spec and the three live-state documents to `hkust4:/home/haodong2/weilin/red_bird/Homemaster`.
- Verified all five local/remote SHA-256 values independently, remote `git diff --check` exit 0, unchanged remote HEAD `22cb122`, and no remote product/test/config/Gate-helper delta. The frozen written spec now awaits explicit user approval; implementation and `discovery-run-004` remain blocked by design.
- User explicitly approved the synchronized frozen visible-only specification and instructed implementation to begin. Entered `writing-plans`; the stale untracked implementation plan must be replaced and pass the one permitted read-only plan review before any product/helper edit or `discovery-run-004`.
- Re-read the complete 1,614-line frozen spec, the stale 615-line implementation plan, current product types/Runner/Adapter/tools/Dispatcher/Runtime boundaries, all related pitfalls, and the real remote helper entrypoints.
- Proved the stale plan's concrete invalid assumptions: required public witnesses, screen-off positive navigation, reuse of v2 input/run-002 names, an obsolete spec hash and a runtime command rooted at a non-canonical display path.
- Traced current image delivery end to end and found no successful Provider-attempt-to-event frame binding. Locked a new generic model-view commit observer plus ALFWorld frame ledger as a prerequisite to the visible authorization implementation; no source/helper code has been changed.
- Replaced the stale 615-line containment/public-witness implementation plan with a 971-line visible-only plan covering Gate A v3, closed types/manifests, pure snapshot, reset/gateway, Provider view commits, exact navigation/manipulation, sole feedback, Runner/metrics, guards, Gate B, ten real Episodes, final docs/review/commit.
- Main-agent plan self-review passes at SHA-256 `9af25b8314b696d43220ec8833d223b87410ea5dd079ed43da991bc4e0e66938`: 56 balanced fences, no placeholder/credential/stale-type/old-hash/whitespace hits, clean `git diff --check`, and explicit coverage of spec sections 5-15. Product and Gate helper code remain unchanged pending the one-time read-only plan review.
- The sole visible-only implementation-plan review returned `FIX` on candidate `9af25b8314b696d43220ec8833d223b87410ea5dd079ed43da991bc4e0e66938`; the reviewer changed no file, delegated no work and endorsed no external runtime symbol.
- Accepted all five findings: staged setup/navigation/global gateway guards; one Gate-A-proven source-level Slice behavior with no runtime dual mode; hard ten-Episode exit/bijection/per-instance/coverage/availability gates; ordered opaque frame bindings for duplicate or multi-image requests; and atomic migration of all six `test_alfworld_tools.py` StepResult constructors.
- Final reviewed implementation plan is 1,014 lines at SHA-256 `f9f8f998a779754c538ff9a2e931ec1f463c58e1297d1a84a4489a4928c977ab`, with 56 balanced fences and clean placeholder, credential, stale-symbol, whitespace and `git diff --check` gates. No second plan review is permitted.
- Entered Gate A v3 execution. The next action is to synchronize the reviewed plan/live state to `hkust4`, archive the current helper bytes, create the immutable v3 matrix/case namespace and drive the three real helper entrypoints RED-to-GREEN before `discovery-run-004` and `case-run-004`. Product/config/test code remains frozen.

### Gate A V3 Helper Continuation

- Continued in the main agent from the interrupted helper implementation; no implementation subagent was started and the final-review slot remains unused.
- The latest local helpers now include explicit generic/ordinal grounding, paired post-Open behavior, same-batch committed-model-view behavior, independent report evidence, and removal of dead v2/public-witness authorities.
- Current internal gates pass: all three files compile; probe `self-test-v3` passes 21 checks, controller passes 32, and verifier passes its complete v3 mutation/wiring suite. These results do not establish a real THOR external terminal state.
- The latest bytes have not yet passed remote Ruff after the final same-batch/report edits, have not been synchronized into the formal `hkust4` helper paths, and have not produced `discovery-run-004` or `case-run-004`.
- Local environment has no Ruff executable; use the existing project Ruff on `hkust4` without installing or changing dependencies, then synchronize only mechanical formatting back to the local ignored helpers.
- Copied the latest helper/matrix bytes to a new isolated `hkust4:/tmp` directory. Ruff lint passed immediately; format-check reported all three Python helpers would be reformatted.
- Mechanically formatted only the temporary copies with project Ruff 0.15.20, then reran lint and format-check to PASS. Synchronized the formatted helpers back locally; final hashes are probe `b8859dd8...`, controller `a6f8fa3...`, verifier `980c9a82...`, while matrix v3 remains `5a0c7816...`.
- Post-format local verification passes: `py_compile`; probe v3 21/21; controller v3 32/32; verifier all 27 mutation checks and 9 actual-entrypoint wiring checks. The results remain internal evidence and do not advance external-symbol status.
- Began the post-format dead-authority audit. No executable public-witness/double-lookup/v2-self-test symbol remains; retained `v2`-named artifact validators and the verifier's internal common-schema adapter require call/data-flow inspection rather than name-based deletion.
- Completed the controller branch RCA: the post-v3 dispatch legacy discovery body is logically unreachable and solely owns three obsolete validators, including the last executable `matrix.discovery_contract` reader. Prepare a RED AST guard, then remove only that branch and its orphaned validators.
- Added the controller `legacy_discovery_authorities_removed` self-test guard. It failed RED with exit 2 and named exactly the three proven orphaned validators; no production or remote file changed.
- Removed the unreachable legacy discovery body plus its three sole-caller validators. The same controller guard now passes, compile passes, and self-test-v3 is 33/33; forbidden names remain only as guard literals.
- Cleanup exposed that `matrix_sha256` was consumed only by the deleted unreachable branch. The active v3 discovery validator receives the matrix object but not the expected matrix hash; investigate the persisted result contract before changing it.
- Confirmed the frozen plan, worker and verifier all require the persisted discovery matrix hash. Added an isolated controller mutation test; it failed RED because the wrong hash reached a stub v3 validator.
- Added the exact matrix-hash check before v3 artifact validation. Controller compile and self-test-v3 now pass 34/34, including both dead-authority removal and matrix-hash drift rejection.
- Re-read locked Task 1 Step 4. Confirmed verifier self-test lacks the mandated actual `CaseBundle` valid/tamper gate; begin constructing it from existing verifier schemas before formal remote synchronization.
- Searched all preserved Gate A directories; no complete synthetic v3 case artifact bundle exists to reuse. The new self-test must create fresh temporary raw JSON/frame artifacts and pass them through the verifier's public schemas and `verify_case_bundle()`.
- Selected a bounded synthetic fixture design: initial/restored invisible, one frozen scan step visible, snapshot `ok`, then an invisible zero-action case. It will support independent persisted snapshot, model-view, fixture-event and count mutations while leaving real movement to the external Gate.
- Mapped `CaseBundle`, case-result schema and verifier dependencies. The synthetic leaf must persist the full seven setup JSON artifacts, query/scan/restore raw events and frames, committed model-view/authorization artifacts, and the case initial snapshot; verifier will reload and independently derive all bindings.
- Completed the independent setup derivation map: policy/addressability, reachable payload, scan provenance, snapshot ranking, restore equality, required coverage and visibility oracle raw-frame checks all consume persisted raw artifacts rather than worker summary booleans.
- Completed case-setup binding map: invisible case must bind the restored event/frame to model-view and authorization, keep fixture actions/lookup/parent/context/actions/navigation at zero, preserve setup count 3, and return the exact non-terminal `target_not_visible` public bytes.
- Added five named CaseBundle checks to the verifier self-test. The suite is intentionally RED with exit 1 and exactly those five `not implemented` failures; existing manifest/mutation/wiring checks remain PASS.
- Implemented the complete temporary CaseBundle generator. It persists canonical JSON refs, independently decodable RGB8 PNGs, query/scan/restore events, snapshot/oracle/model-view/authorization artifacts and an invisible zero-action case.
- Verifier compile and self-test-v3 now PASS. The valid bundle yields zero failures; self-consistent tampering is rejected specifically as `snapshot_derivation`, `model_view`, `visibility_fixture` and `setup_count` after all shallow reference hashes are recomputed.
- Latest remote temporary Ruff pass: lint PASS; verifier alone required mechanical formatting; final lint and format-check PASS. Local hashes now equal the formatted temporary bytes: probe `b8859dd8...`, controller `de586538...`, verifier `24d34672...`, matrix `5a0c7816...`.
- Formal remote preflight passes archive and empty-output guards. Printed actual controller/verifier help for both phases; prepare the stale v2 README rewrite using those exact arguments before replacing formal helper bytes.
- Rewrote the Gate A operational README to v3 current-visible semantics, canonical `/data1` paths, exact current CLI arguments, run-004 phase isolation and v3 evidence layout.
- One local audit orchestration attempt failed before execution because Markdown backticks were placed in a JavaScript template literal. The corrected ordinary-string audit passes: 190 lines, 14 balanced fences, no stale self-test-v2/exact-cases-v2/run-002/public-witness or `/home` command hits, and clean `git diff --check`.
- Formal Step 5 caught a false temporary Ruff green: repo config reported two `B023` closures, one `E501`, and three format deltas. The combined SSH command lacked fail-fast, so later passing self-tests masked those failures in the final exit code.
- Fixed the three lint findings, formatted all helpers with the explicit repository `pyproject.toml`, and synchronized the project-formatted bytes back locally. Local compile plus probe 21/21, controller 34/34, and the complete verifier suite remain PASS.
- Added the serious false-green postmortem to the top of `docs/pitfalls.md` and positive enforcement rules to `CLAUDE.md`.
- Re-synchronized project-formatted helpers to the formal `hkust4` paths. Formal Step 5 used `set -e` and explicit `--config pyproject.toml`; compile, Ruff lint, Ruff format-check, probe 21/21, controller 34/34, and verifier complete mutation/bundle/wiring suite all exit 0.
- Started real `discovery-run-004`; it stopped after the first trial and wrote no manifest. The worker exited 0/status PASS, cleanup complete, setup 27, tested/model 0, with all transaction artifacts intact; controller summary exited 1.
- Structured RCA identified the sole controller reason: the `grounding_ordinal_missing` sentinel `__gate_a_missing_ordinal__|...|FloorLamp|3` has no frozen visibility oracle. That absence is intentional and unique; all six real task IDs have oracles and no other case lacks one.
- The verifier already derives missing-ordinal binding from trial ID, sentinel, public object type and ordinal index without an oracle. Controller `validate_v3_discovery_result()` omitted that branch and unconditionally looked up every requested ID. No fix has yet been applied.
- Added an actual-entrypoint order guard and reproduced RED: controller self-test exited 2 because missing ordinal was validated after oracle lookup.
- Added the closed missing-ordinal binding derivation and placed it before ordinary oracle lookup. Correct oracle absence passes; binding drift and a sentinel appearing as a real oracle are rejected. Compile and controller self-test-v3 now pass 38/38.
- Archived the exact run-004 helper/matrix/README bytes under `helper-archive/pre-discovery-run-005` locally and remotely with matching hashes.
- Loaded the fixed controller from an isolated remote path and replayed immutable run-004's full first-trial artifact tree. `validate_discovery_result()` now passes all 23 cases, including exactly one missing ordinal, while retaining setup 27 and tested/model 0.
- Post-format full local regression passes: compile, probe 21/21, controller 38/38, verifier complete suite, and `git diff --check`.
- Added the run-004 missing-ordinal postmortem to `docs/pitfalls.md` and two positive rules to `CLAUDE.md`.
- Started `discovery-run-005`. Trial 1 passed through the missing-ordinal repair; trial 2 worker exited 2 after 24 setup actions, zero tested/model actions, clean cleanup and one issue about a strict-visible surface Statue lacking an anchor.
- Raw evidence proved the Statue snapshot/oracle and final classifications already selected the Statue's own direct pose. Root cause was an obsolete parent-anchor issue appended before the direct-snapshot override.
- Added a RED synthetic surface/Dresser case, moved the direct override before the final error check, and turned probe self-test GREEN at 22 checks.
- Offline replay first hit two replay-harness omissions (importlib module registration and runtime-added traj hash); after correcting only the harness, full run-005 `discover_cases()` replay yields 31 cases, empty issues and the Statue itself as surface anchor.
- Added the direct-anchor ordering postmortem and positive final-state error rule. Preserve run-005 and prepare run-006; a third real composition defect requires a broader audit before another fix.
- Archived the exact run-005 helper/matrix/README bytes under `helper-archive/pre-discovery-run-006` locally and remotely with matching hashes.
- Project-config formatted the fixed probe. Full local compile, probe 22/22, controller 38/38, verifier complete suite, README run-006/fence audit, and `git diff --check` pass.
- Before spending a third real run, entered a main-thread audit of every discovery issue append versus later target/anchor state changes; no subagent or product edit is involved.
- Audited every `issues.append` in `classify_semantic_movables()` and `discover_cases()`. Rejected the apparent direct-snapshot/ambiguous-parent issue after tracing ownership: direct pose resolves navigation but cannot resolve the required movable location profile, so that ambiguity must remain a failure.
- Traced the post-Open discovery/case path and confirmed one frozen-expectation defect: it equates actual-parent identity with post-Open child visibility instead of deriving visibility from the returned event. Controller and verifier bake the same assumption into kind/count/result checks; kept run-006 unspent for a RED-first shared-contract repair.
- Resumed from the post-Open v2 handoff in the main thread. Confirmed product/config/test code remains frozen and `discovery-run-006`, `case-run-006`, and `exact-cases-v3.json` remain unspent per the prior checkpoint.
- Root-caused the first offline replay failure before editing: `/tmp/hm_gate_a_post_open_replay.py` rebuilt only the restored event, but `_grounding_cases()` also requires the formal transaction's rich restored capture. The persisted normalized restored state is insufficient by itself; the harness must reconstruct the capture from immutable raw event and frame artifacts.
- Reconstructed and independently byte-checked the restored RGB/PNG/event/state envelope; the next replay reached case derivation. Its per-worker post-Open nonempty assertion correctly exposed an audit-design mistake: run-004 candidate-1 has no such case, and run-005 candidate-2 has no eligible closed-container profile. Changed the guard to require nonempty aggregate coverage and began locating a preserved real post-Open worker.
- Scanned all seven preserved discovery results from runs 001-005. None has an eligible closed-container profile or post-Open case, so removed the aggregate nonempty assertion from the compatibility replay and made the absence explicit in output. External post-Open proof remains blocked on fresh run-006, whose formal verifier already requires both dynamic outcomes.
- Replayed current helper bytes against immutable run-004 candidate-1 and run-005 candidate-2. Results are 23 and 16 cases respectively, zero issues, controller PASS and independent post-Open binding verifier PASS; the audit output explicitly reports zero historical post-Open coverage. Advanced to the remaining field-reference/README/project-Ruff audit before formal run-006 synchronization.
- Began the whole-helper post-Open reference audit. No stale pre-frozen visibility symbol or obsolete case kind was found; the one parent/container equality is relation classification only. Gate README still needs explicit sequence-v2 dynamic outcome and run-006 dual-outcome release wording.
- Traced post-Open discovery and independent run verification through the raw Open event. Paused run-006 preparation to audit a possible controller/process-code mismatch between dynamic selected outcomes and static manifest status fields; no code change is justified until that branch is proven reachable and wrong.
- Closed the status/return-code audit: dynamic `target_not_visible` is an allowed observation, not a negative transport exit, so Gate status/process remain pass/0 while public outcome fields stay negative and non-terminal. Updated the Gate README with sequence-v2 semantics, exact two-envelope counts, relation/outcome separation, run-006 coverage gates, explicit project Ruff config and fail-fast execution.
- First remote project-config Ruff run stopped fail-fast on one 101-character probe AST-test assignment. Applied a semantics-neutral line wrap only; all remote gates must restart from compile rather than treating later unexecuted checks as passed.
- After the line wrap, project Ruff lint passed and format-check requested mechanical changes in all three helper copies. Because fail-fast correctly prevented self-tests from running, began isolated project-config formatting; no formal helper or run artifact has changed yet.
- Isolated project formatting completed and compile/lint/format passed, but controller self-test correctly refused an audit directory missing adjacent matrix-v3. Classified this as harness setup, not helper behavior; add both frozen matrices and rerun every gate from the start.
- Added both frozen matrices and restarted the isolated gate sequence. Project compile/lint/format, probe 24/24, controller 41/41 and verifier 35 checks all pass; synchronized mechanically formatted bytes back locally, matched hashes, and reran the same local compile/self-tests successfully.
- Formal preflight on hkust4 confirmed HEAD `22cb122`, exact equality between current formal bytes and the pre-run-006 archive, known doc-only Git dirt, and absence of run-006/case-run-006/manifest. Added the post-Open relation-versus-visibility pitfall and the corresponding positive rule before formal helper synchronization.
- Synchronized only the final formatted helpers and Gate README to formal hkust4, preserving the pre-run-006 archive. One fail-fast formal preflight passed exact hashes, HEAD/dirty-path whitelist, deletion guards, compile, project Ruff, 24/41/35 self-tests, README audit and final-byte run-004/run-005 compatibility replay. Advanced to fresh discovery-run-006; product/config/test remain frozen.
- Live-doc sync flattened `docs/pitfalls.md` to an unintended root `pitfalls.md`; identified immediately before launching the run. Remove only the session-created extra file, copy to the correct docs path and recheck remote status/hashes.
- Corrected the live-doc path, verified all five local/remote hashes and clean diff formatting, then launched fresh discovery-run-006.
- Run-006 passed the first three trials with setup counts 27/24/27 and zero tested/model/issues, then stopped at trial 4. The failing worker completed 28 setup actions and cleanup with zero tested/model but could not compose a unique-parent post-Open row despite two eligible closed children. Preserved the run and entered the mandated broader architecture/data-flow audit before any fix.
- Completed the broader audit. Production visibility-oracle construction intentionally leaves hidden closed children unobserved with no execution anchor, while post-Open probe/controller/verifier synthetic contracts incorrectly require that impossible field. Selected the plan-consistent upstream fix: derive a separate child parent/snapshot binding from reset raw containment and never use the child visibility oracle as pose authority.
- Added RED guards before implementation. Probe failed on a production-shape closed child with null oracle execution anchor; controller rejected its actual discovery source for reading that field; verifier reported only `post_open_child_binding_independent=fail`. All three compiled and every older verifier check stayed green, isolating the repair boundary.
- Removed the impossible child-oracle pose dependency in all five consumers. Controller 42 checks and verifier 36 checks turned GREEN. Probe passed the target condition and then exposed missing pre-existing frame fields in the newly added synthetic container fixture; completing only that test envelope before the next full run.
- Completed the synthetic fixture and reran all local gates: probe 26/26, controller 42/42 and verifier 36/36 pass. The new probe behavior case produces both relation kinds with a production-shape unobserved child oracle; actual-entrypoint guards prove controller/verifier derive the separate binding from raw reset containment.
- Isolated project lint passed for the run-006 repair; format-check requested only probe reformatting and stopped before later gates. Began mechanical formatting in the isolated directory, with formal helper/run bytes unchanged.
- Formatted the isolated probe and passed project compile/lint/format plus 26/42/36 self-tests. Immutable run-006 trial-4 replay still failed at the same composition guard, proving the child-binding fix was necessary but incomplete.
- Traced the remaining failure to oracle coverage scope: task semantic types excluded Drawer, while the target-independent sequence requires it. Raw audit proved all nine candidate containers already have `ok` snapshots and both visibility roles in the frozen scan. Locked the upstream union coverage rule and prepared RED entrypoint guards across all three helpers.
- Restored the interrupted session from `task_plan.md`, `findings.md`, `progress.md` and the immutable run-006 handoff; confirmed product/config/test code remains frozen and no implementation subagent was started.
- Reproduced the intended second-layer RED exactly: compile PASS; probe/controller failed only `post_open_container_oracle_coverage_missing`; verifier reported only `post_open_container_oracle_coverage=fail` while every older check passed.
- Added the same union coverage contract to all three real entrypoints. Probe now emits container oracles from the frozen reset container order; controller and verifier independently recompute expected container IDs from raw reset evidence rather than trusting the producer.
- Local post-fix gates PASS: `py_compile`; probe 27/27; controller 43/43; verifier complete suite including `post_open_container_oracle_coverage`. Current hashes are probe `cea44449...`, controller `7bf0db62...`, verifier `cefe847a...`; remote Ruff and immutable run-006 replay remain pending.
- Copied candidates into isolated `hkust4:/tmp/hm-gate-a-run007-audit.lZgwnM` with both frozen matrices. Explicit project-config Ruff lint passed; format-check requested only probe. Mechanically formatted that isolated probe, then reran compile/lint/format and all 27/43/verifier checks from the start to PASS.
- Synchronized the formatted probe byte back locally and reran local compile plus all helper self-tests to PASS. Final candidate hashes are probe `a120d634...`, controller `7bf0db62...`, verifier `cefe847a...`.
- Extended `/tmp/hm_gate_a_post_open_replay.py` to reconstruct reset/scan/restore events and RGB/PNG captures, rebuild the oracle with the fixed producer, replace only a temporary copied worker tree, update refs/result hashes, and invoke controller plus the verifier's full discovery artifact validator.
- Immutable run-006 trial-4 replay PASS: oracle rows 6 -> 15, closed containers 9, cases 46, post-Open cases 9 (`unique_parent=1`, `other_container=8`), issues 0, controller PASS, verifier PASS. The original run-006 bytes remain unchanged.
- Added the synthetic-fixture false-green postmortem to `docs/pitfalls.md` and its positive producer-coverage audit rule to `CLAUDE.md`.
- Rewrote the Gate README so run-006 is immutable failure evidence and all commands/layout use fresh discovery-run-007/case-run-007. Created `helper-archive/pre-discovery-run-007` and proved all five archived bytes equal the final formal candidates.
- Synchronized only the three helpers, matrix, README, archive and correctly targeted live documents to `hkust4`. Formal helpers now hash to probe `a120d634...`, controller `7bf0db62...`, verifier `cefe847a...`; matrix remains `5a0c7816...`, README `a27c94b8...`.
- Full formal fail-fast preflight PASS after correcting one README-count audit assumption. It proves HEAD/dirty whitelist, run-006 preservation SHA `36bcfe4b...`, run-007/manifest deletion, formal/archive/doc hashes, compile, project Ruff, 27/43/verifier self-tests and final-helper immutable rebuilt replay (46 cases, 9 post-Open, controller/verifier PASS).
- Launched fresh `discovery-run-007`. Trials 1-4 PASS; the repaired historical trial 4 emits 15 oracle rows and 46 cases with no issues. Trial 5 exits 2 after query plus three successful scan Teleports, zero tested/model actions and clean cleanup; no manifest is written.
- Root-caused trial 5 without editing helpers: only three unrelated raw THOR temperatures drift `Cold -> RoomTemp`; all structural/action/ALFWorld goal-state fields are stable. Preserved run-007 at summary SHA `9633c3d9...` and failed result SHA `abd45bc3...`.
- Verified ALFWorld task source uses explicit `heated_objects/cooled_objects` sets for goal/reward. Tested two real-build controls in isolated exact-trial environments: physics pause is accepted but ineffective; `ChangeTimeScale(0.01)` holds the full world stable through all 26 poses and restore, while restoring `1.0` makes the same temperature evolution resume by the seventh Teleport. Await user approval before changing the two-action setup protocol.
- User approved the fixed `ChangeTimeScale(0.01) -> scan -> ChangeTimeScale(1.0)` approach. No helper/product code was edited while the design gate remained active.
- Updated the formal spec and CHANGELOG with the complete transaction, failure recovery, `N+4` accounting, normal-time final-event authority, build-scoped effect control and immutable run-006/run-007/fresh run-008 rules.
- Main-thread spec self-review passes at SHA-256 `92ef48a68a30f021aa81cadf5177e07ecf9d12ecda924744a50a4c6f612a9f49`: 70 balanced fences, zero placeholders/stale successful counts/executable run-007 outputs/credential hits, and clean whitespace. Await commit, remote hash verification and written-spec confirmation before updating the implementation plan.
- Committed only the formal spec and matching CHANGELOG as `61b76dc`; helper, matrix, README and product bytes were not included.
- Synchronized the spec/CHANGELOG/live handoff files to hkust4 and proved all five local/remote SHA-256 values identical. Run-006/run-007 summary hashes remain unchanged, run-008 discovery/case paths remain absent, and the next gate is explicit written-spec confirmation.
- User explicitly approved implementation. Invoked the writing-plans workflow and replaced the current Task 1 boundary with the controlled-time matrix/probe/controller/verifier/effect-control/run-008 procedure; the historical run-004 plan remains a non-executable appendix.
- Main-thread plan self-review passes at SHA-256 `ed91439f0ae0f6453084c37f95032600467e8a4e5d225f1c60c99877e916bd71`: 1,207 lines, 70 balanced fences, no placeholders, consistent time-control fields/phases, clean whitespace/credential scan and no second plan review.
- Preserved discovery runs through 015. Run 015 completed 20 workers with 19 PASS and one supplemental Slice reset-settling failure; no manifest was produced.
- User removed perfect Gate A and Gate B as delivery prerequisites on 2026-07-17 and directed immediate product implementation with failures exposed.
- Archived the unfinished controlled-settling helper draft and restored probe/controller/verifier exactly to `pre-discovery-run-015` hashes.
- Entered Task 2 product baseline/types/manifest work; internal tests remain regression gates while external Gate B is non-blocking evidence.
- Completed Tasks 2-10 product implementation: strict portable trial selection, pure scan/snapshot store, one gateway, typed reset/goal lifecycle, Provider attempt/model-view binding, closed retry ownership, current-visible one-pose navigation, exact manipulation feedback, Dispatcher terminal gating, Runner/taskset accounting and structural guards.
- Focused verification reached taskset/trial `17 passed`, provider/runtime `39 passed`, implementation matrix `188 passed`, and full repository `391 passed, 1 skipped`; Ruff check on all 48 changed Python files, compileall and cleanup legacy guard passed. Broad format-check retained the inherited 26-file baseline rather than bulk-formatting unrelated implementation bytes.
- Created ignored Gate B product worker/verifier and a sorted changed-file SHA-256 manifest. The complete matrix and fixed ten-Episode manifest remain impossible because Gate A did not produce `exact-cases-v3.json`; no replacement trials were selected.
- Gate B `run-001` exposed a real runner defect before Adapter construction: `build_alfworld_batch_env_with_first_trial()` was called with its keyword-only trial path positionally. Preserved the failed run, fixed the call, added a strict-signature regression and passed its targeted test/Ruff gate.
- Gate B `run-002` entered real THOR on FloorPlan219 and preserved a typed setup terminal: trigger `scan_pose_mismatch`, final `scan_time_scale_restore_rejected`, classification `execution_state_uncertain`, five setup actions, zero model/Provider actions. Independent verifier reports artifact consistency but exits 2 with the full matrix incomplete.
- Direct complete-diff review found one production identity omission: ordinary THOR reset verified the trial on disk but did not compare the actual runtime scene with `expected_logical_scene`. Added the only allowed exact mapping, `FloorPlanN_physics -> FloorPlanN`; wrong physics scenes now return `runtime_scene_mismatch`, while bare/noncanonical scene names return `reset_identity_unreadable`, both before setup actions and with cleanup.
- Added two parameterized runtime-scene regressions (`2 passed, 33 deselected`) and reran Gate B as fresh `run-003`. The valid FloorPlan219 runtime passed the new identity gate, then reproduced the same honest `scan_pose_mismatch -> scan_time_scale_restore_rejected` terminal, five setup actions, zero model/Provider actions, `slice_verified=true`, `overall_status=incomplete`, verifier exit 2.
- Corrected the earlier no-run disposition: ignored `config/homemaster.yaml` already contained the user-provided Mimo profile, and a real project `LLMClient` request returned `OK`. Built `config/alfworld_v18_regression_trials.json` from six `historical_exact` rows plus the pre-Gate `candidate-1` for Episodes 1/3/7/9, labeled `deterministic_replacement`; all ten entries pass bytes/scene/goal loader checks.
- Rewrote README, architecture and user guide for the V1.8 lifecycle and added the locked Chinese delivery report. Gate A/B failures, missing reset evidence artifact, physical compatibility-code residual and unavailable ten-Episode run are explicit rather than aggregated away.
- Completed the one-time main-agent direct complete-diff review required by the active no-subagent constraint. Reviewed execution, formal V1.8 Adapter call graph, typed feedback/metrics, trace observer, Provider/runtime ordering and structural guards; after the runtime-scene fix, no further blocking defect was found.
- Final remote verification passed before the follow-up manifest: ALFWorld/Provider/Runtime focus `202 passed, 1 skipped`; full repository `394 passed, 1 skipped`; changed-file Ruff on 48 Python files; compileall; 11 cleanup/interface/V1.8 guard tests; Gate helper lint/format; two changed JSON plus 14 Gate evidence JSON parses; 13 Markdown fence checks; secret/placeholder/whitespace and `git diff --check`. The inherited format baseline remains exactly 26 files and was not bulk-rewritten.
- Re-ran the independent Gate B verifier on `run-003`: zero attempts/requests, no artifact-consistency failures, `slice_verified=true`, `overall_status=incomplete`, missing `exact-cases-v3.json`, exit 2. This remains non-blocking incomplete evidence rather than PASS.
- Ran all ten pinned Episodes as `alfworld-valid_unseen-v18-realapi-20260718-001`. The process exited 0 with ten result rows, but all ten are score-ineligible setup terminals at `scan_pose_mismatch -> scan_time_scale_restore_rejected`: 50 setup requests, zero agent tools/model/Provider requests, 0% evaluation/Harness coverage and no formal score.
- Added a committed-manifest regression and reran the plan's exact focused/full commands at `193 passed` and `395 passed, 1 skipped`; JSON, Ruff, compile and whitespace checks remain clean.

## 2026-07-28 Memory Tool Result Projection

- Fixed the six V2.1 memory tools at the ApplicationRuntime message boundary: complete structured results now enter
  model-visible tool-result `content` as JSON while remaining available in internal `data`. No automatic per-turn memory
  prefetch was added.
- Added real ApplicationRuntime regressions proving `add_memory` exposes its returned ID and `search_memories` exposes
  records/value through the next model request. Focused memory plus ApplicationRuntime suite: `86 passed`.
- Real HomeMaster black box passed. Mimo wrote fact `HomeMaster工具结果修复探针-20260728-B`, returned ID
  `be2d4e6a-26c4-4893-9428-74cc6420d9b7`, and a fresh session recalled `回归测试柜 / 第五层`; the search event's
  model-facing `result` contained the complete record. The test memory was deleted, then a newly constructed store returned
  `memory_not_found` for that ID.
- `uv build`, `uv lock --check`, targeted Ruff lint/format and `git diff --check` pass. The non-live repository suite ended
  `1 failed, 1662 passed, 1 skipped, 7 deselected`; the sole unrelated failure is Cron startup. It reproduces alone because
  the unmodified worker cold import takes about 5.86 seconds while `cron start` permits only 5 seconds to publish its PID,
  then its termination wait reaches the test's 10-second subprocess timeout. Cron was diagnosed but not changed.
- The first real write attempt also reconfirmed the existing tool-schema usability issue: `record` is advertised only as a
  generic object, so Mimo initially omitted required FactRecord fields. This is separate from result projection and remains
  deferred per the locked scope.

## 2026-07-28 SDK Tool Calls And Pydantic Memory Inputs

- Anthropic live text/thinking remains streamed, while executable tool calls now come exclusively from the SDK final
  message's `tool_use.name/input`; HomeMaster no longer uses its independent streamed argument assembly in production.
- All six memory tools now generate provider schemas and validate execution input from Pydantic models. Local references
  are inlined for provider visibility, and the complete Fact/Procedure/Subject structures are exposed.
- Real Mimo proved that nested `record` is emitted as a JSON object string despite the object schema. A strict Pydantic
  before-validator now decodes only JSON objects, then applies the unchanged discriminated record validation.
- Real USER.md gate passed on the first native `memory(target=user)` call; disk readback and a fresh-session prompt answer
  both returned evening walks and 08:00 running. The exact test entry was deleted and USER.md returned empty.
- Real structured gate passed on the first `add_memory` call after compatibility handling. ID
  `57a453d4-ac30-40c6-8c6a-6840e5e5fedc` stored the F probe at `回归测试柜 / 第八层`; a fresh session recalled it,
  deletion succeeded, and a later fresh search returned no record. Earlier D/E probes never passed input validation.
- Final HomeMaster non-live verification passed with `1242 passed, 2 skipped`; the provider/tool-call regression slice
  passed with `26 passed`. Targeted Ruff lint/format, `git diff --check`, `uv build`, and `uv lock --check` also pass.
- Case02 environment/browser/terminal tests are outside this memory-system scope and are not part of this acceptance.

## 2026-08-21 Configuration And Dead-Code Audit

- Read the `planning-with-files` skill and resumed the existing root planning
  documents instead of overwriting them.
- Captured remote baseline: branch `mindmem`, only untracked user-owned `story/`
  artifacts, six config files with four tracked and two ignored real configs.
- Enumerated the Python package and executable entrypoints; identified dynamic
  loader surfaces that require dedicated reachability checks.
- Initial monolithic search was truncated; logged the error and switched to
  bounded, machine-readable audit steps.
- Added the formal seven-phase cleanup plan and four cleanup strategies. Phase A
  is in progress; no project code or configuration has been deleted or edited.
- Read packaging, unified config, CLI composition, application factory,
  extension loading and skill discovery boundaries. Confirmed that dynamic
  loading/package-data/subprocess surfaces require dedicated checks.
- Ran the field-level AST audit and rejected naive zero counts for sections
  consumed through `model_dump()` (`retrieval_scoring`, `grounding`). The first
  module-graph run failed inside the audit script before producing a report; no
  project deletion followed from it.
- Rejected the second module-graph output after noticing its external absolute
  `from` imports were absent. The 28 reported modules are not accepted
  candidates; the parser is being corrected before reuse.
- Corrected and reran the graph, then manually traced leaf candidates. Confirmed
  the old registry/dispatcher stack and V1.6 object-memory RAG stack have no
  shipped production consumer; separated stable public re-export modules and
  the offline BM25 preflight for further compatibility/invariant review.
# 2026-07-25 Gateway Activation

- Root-cause evidence from the preceding diagnostic turn: the ignored mode-0600 config enables Gateway/Feishu and contains both direct credentials, but no HomeMaster Gateway process or tagged session exists.
- `uv run homemaster --help` failed because the project package was absent from `.venv`; `lark_oapi` was also absent. `uv sync --check --extra dev --extra gateway` reported seven required packages without mutating the environment.
- Synchronized the locked environment and started the configured Gateway. Independent local inspection found the active `uv run homemaster gateway` parent and project-venv child plus Gateway session `gw-25d72ed7d051d61c3b9eba18ea0db8f6` at revision 4 with `session_status=replied` and 20 persisted messages.
- The owner completed the Feishu-side black-box check and confirmed normal message delivery and readback. Gateway activation and live verification completed successfully; the test process was later stopped.
