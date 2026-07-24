# HomeMaster Skill, Raw Output, And Rich Renderer Closeout Handoff

## Mission

Finish the three critical V2.0 workstreams preserved in checkpoint `9fd3100`:

1. HomeMaster Skill identity, authoritative roots, complete installation, and real Registry verification.
2. Exact raw canonical text with a deliberate event/security boundary.
3. Concise interactive Rich rendering with unchanged machine-output contracts.

Canonical closeout plan:
`plan/V2.0/homemaster-skill-raw-output-renderer-closeout-plan.md`.

## Read First

1. `AGENTS.md` instructions supplied by the owner for design, debugging, verification, documentation, and review.
2. `CLAUDE.md`, especially Gateway/public projection, config, MCP/resource, package-data, and external terminal-state
   rules.
3. This handoff and the canonical closeout plan.
4. `plan/V2.0/homemaster-skill-identity-raw-output-remediation-plan.md` and its handoff for the completed
   universal-tool boundary.
5. `docs/architecture/application-runtime.md`, `docs/skills-and-config-user-guide.md`, `docs/pitfalls.md`, and the
   Unreleased section of `CHANGELOG.md`.

The pre-checkpoint version of the earlier combined plan contains useful historical intent but is not canonical:

```bash
git show 9fd3100^:plan/V2.0/homemaster-skill-identity-raw-output-remediation-plan.md
```

## Repository State At Handoff Creation

- Repository: `/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`
- Branch: `codex/realtime-rich-streaming-cli`
- Baseline: `9fd31005061d55bf05e2e22314beecad22c67b26`
- Baseline message: `chore(checkpoint): preserve accumulated V2 workspace`
- Baseline was clean before these two planning documents were added.
- The checkpoint has not been pushed according to the prior session record.
- Do not amend, reset, split, push, or rewrite the checkpoint unless the owner explicitly asks.

## Plan Review Record

The single allowed read-only plan review is complete. The main agent adopted all three findings:

1. Added a locked per-surface output matrix and removed implementation-time discretion over the raw/security split.
2. Made real Feishu transport/business success plus independent readback a hard closeout gate; unavailable real
   verification is `BLOCKED/UNVERIFIED`, not DoD.
3. Made actual-user Skill target conflicts blocking; `skip` requires an explicit scope and completion-claim change.

Do not request another plan review automatically. The remaining independent review gate is the one final code review
after all implementation, external terminal-state validation, and documentation are complete.

## Completed And Frozen Mainline

The following is done and is not part of this implementation:

- One ordinary-name `ToolRegistry -> PermissionChecker -> ToolExecutor` execution path.
- No production `ToolCatalog`, `ToolView`, `ToolExecutionPipeline`, `HomePermissionPolicy`, or runtime
  `enabled_tool_ids` filtering.
- Extension `enabled_tool_ids` exists only as load-time approval for third-party exports.
- Principal capabilities, command/path rules, plan mode, confirmation/`tool.auto`, deadline, cancellation, leases,
  and terminal-state checks are preserved.
- Registry collisions fail closed; deadline and mutating-outcome uncertainty review findings are resolved.
- Installed wheel contains 58 unique ordinary tool names and no removed Catalog/Pipeline/OpenHarness package.

Do not use the closeout as an excuse to redo tool routing or rename generic modules.

## Three-Workstream State At Handoff Creation

### Skills

- Repository `.homemaster/skills` contains 14 Superpowers directories and 50 files, moved byte-for-byte from the old
  project `skills/` location in the checkpoint.
- Loader defaults already point to `~/.homemaster/skills` and project `.homemaster/skills`.
- Bundled `skill-creator` already documents HomeMaster roots.
- `scripts/v20/verify_skill_installation.py` already uses the universal Registry/Executor and performs several real
  clone/script/dependency/discovery gates.
- At handoff creation, the actual user root `/hpc2hdd/home/wyuan140/.homemaster/skills` was absent; this was the
  historical gap closed by the execution evidence below.
- Existing source tests do not by themselves prove all 14 complete trees survive installation and resolve through a
  fresh actual-user process.
- Owner scope update: live installation acceptance now uses exactly two requested upstream URLs,
  `https://github.com/obra/superpowers` and
  `https://github.com/vercel-labs/skills/blob/main/skills/find-skills/SKILL.md`. Finish the HomeMaster Skills
  capability first, then pass both URLs to a real HomeMaster run and let HomeMaster download/install them. A manual
  implementing-agent copy is not acceptance evidence.

### Raw Output And Events

- `src/homemaster/events/sanitizer.py` is deleted.
- `PublicEventProjection.project_content()` and `.copy_value()` currently preserve text/values.
- CLI, event sink, MCP, Feishu, config, doctor, trace, and benchmark tests contain new expectations for raw
  secret-shaped values.
- Current Feishu service `repr` includes the app secret; several tests explicitly expect that behavior.
- Current `CLAUDE.md` still requires redaction for public events, config display, SDK logs, URL query/userinfo, and
  paths. The code, tests, and governing rules are contradictory.
- Decision 0 is locked to the historical owner selection: candidate 2, exact runtime text across every surface in
  the plan's locked matrix, including config credentials, SDK logs, and service `repr`. Candidate 1 remains the
  smaller-risk engineering recommendation but is not selected. Governing rules must be synchronized before
  production edits; the execution agent may not reinterpret the product choice.

### Rich Renderer

- `RichOutputRenderer` already renders concise start/completion rows and has tests for exact long Bash commands,
  absent large result bodies, concurrent same-name FIFO behavior, and secret-shaped content.
- This remains checkpoint evidence, not final acceptance. Audit truncation markers, durable raw references,
  correlation, cancellation, terminal widths, stdout/stderr ownership, and installed PTY behavior before deciding
  what code is missing.

## Known Baseline Verification

- `tests/homemaster`: `1134 passed, 1 skipped` at the completed checkpoint.
- Focused application suite: `83 passed`.
- Generic AgentRuntime: `25 passed`.
- Universal Registry/Executor: `26 passed`.
- Ruff, compileall, `git diff --check`, and production legacy-symbol audit: PASS.
- Clean wheel: 211 entries; CLI help, 58-tool Registry composition, and real Bash file terminal-state canary: PASS.
- Full repository previously had three external Coworker failures: two missing `/usr/bin/google-chrome`, one
  tmux/bubblewrap terminal-state failure. Reproduce and classify; do not automatically waive a new failure under
  those labels.
- Real ALFWorld: `UNVERIFIED` because the environment lacks the module/dataset. It is outside this closeout unless
  the implementation touches its execution/event path.
- Real Feishu message readback was `UNVERIFIED` at handoff creation; the installed SDK/API symbol and business
  response are verified in the closeout evidence below.

## First Actions For The Executing Agent

1. Confirm branch, HEAD, and worktree. Preserve the planning-doc changes.
2. Read the complete canonical plan, confirm that no newer owner instruction supersedes locked Decision 0, and
   synchronize contradictory governing rules. Do not edit raw-output production code first.
3. Build a current-state matrix from code and tests for all three workstreams.
4. Run focused baselines and record exact commands/results in this handoff.
5. Add RED tests for genuine missing invariants before production changes.
6. Complete Skills, then raw/event, then Rich, following the dependency order in the plan.

## Execution Discipline

- Main agent performs implementation, debugging, tests, external validation, docs, and review-finding fixes.
- Do not delegate implementation. The plan reviewer has already been consumed when this handoff is marked locked.
- Use exactly one final reviewer only after all implementation, external gates, and docs are complete.
- Find root cause before fixes. If a current checkpoint test expects unsafe or contradictory behavior, do not edit
  the test merely to match a preference; trace the boundary and apply locked Decision 0.
- Verify every external terminal state independently and check return/business status. Never report mock/event/trace
  evidence as a real Skill installation, terminal rendering, or Feishu delivery.
- Per-Skill and per-output-surface assertions are mandatory; aggregate best/any checks are forbidden.

## Definition Of Done

- A real installed HomeMaster receives the two owner-supplied URLs and installs their complete actual Skill trees.
  Every resulting Skill independently matches its upstream source and resolves through fresh actual-user and
  isolated-wheel processes. Any existing-target conflict keeps the closeout `BLOCKED` until the owner resolves it;
  a skipped target requires an explicit scope/DoD revision and cannot be called installed.
- Raw/event output behavior matches one locked boundary matrix across every listed consumer, with contradictions in
  code, tests, `CLAUDE.md`, architecture, and user docs removed.
- Real installed CLI first-byte, JSONL/file, config-entrypoint, and PTY Rich gates pass with return codes and
  independently read terminal/files.
- Real Feishu transport/business status and independent readback both pass. If credentials, SDK/API contract, or
  environment are unavailable, the closeout remains `BLOCKED/UNVERIFIED`; mocks cannot upgrade it or satisfy DoD.
- Rich is concise interactively, exact for complete Bash invocation, stable under concurrency/failure, and does not
  contaminate machine stdout or duplicate final output.
- Full locally available tests and repository quality gates pass; known external failures are separately reproduced
  and classified.
- README, architecture, user guide, pitfalls/rules as applicable, CHANGELOG, plan, and this handoff are synchronized.
- One final reviewer has reviewed the completed diff; findings are resolved with targeted verification.
- Commit message and CHANGELOG carry the same change, reason, impact, validation, and `UNVERIFIED` facts.

## Closeout Evidence (2026-07-24)

- HomeMaster, not the implementing agent, processed the two owner URLs. Actual user state is 15 Skill directories:
  14 Superpowers directories plus `find-skills`; fresh-process Registry/`skill` resolution and upstream per-tree
  file/SHA-256 checks passed. The failed prior target is preserved at `~/.homemaster/skills.failed-20260724-run2`.
- Wheel `/tmp/hm-closeout-wheel-final3-20260724/dist/homemaster-0.1.0-py3-none-any.whl` has SHA-256
  `be4aa95f042a87a6f4236db5a3b9268607cb0f3221c529f8815cf8b396dfaa09`; isolated Registry and installed CLI
  black-box gates passed.
- Focused gates passed: raw/event/config/Gateway/Feishu/MCP/extensions `188`, Rich/output matrix `27`, CLI/PTY `10`,
  and full `tests/homemaster` `1158 passed, 1 skipped`.
- Real Feishu chat-list, message-create, and independent message-get readback passed with business code `0` and one
  exact canary. Media/reaction/groups/reconnect, `lark`, and ALFWorld remain `UNVERIFIED`.
- Full repository: `1585 passed, 1 skipped, 3 failed`; two failures are missing `/usr/bin/google-chrome`, and one
  independently reproduced tmux/bubblewrap external grep terminal-state failure (`exit_code=1`, empty stdout).
- Repository gates: structured data `73` files PASS, product Markdown fences/links `90` files PASS, compileall PASS,
  diff-check PASS, and changed-file Ruff lint PASS. Ruff format reports baseline drift in seven already-unformatted
  changed files; no mass-formatting was applied. Legacy-term guard retains only its ten classified historical/domain
  hits, and the forbidden legacy tool architecture symbol audit is clean.

## Live Update Template

Keep this section current during execution instead of adding history to long-term architecture docs.

```text
Current phase: final read-only review pending
Decision 0: candidate 2 LOCKED; exact runtime text, existing auth/ACL/allowlist/binary boundaries retained
Completed: implementation, actual-user Skill install, focused/full tests, wheel/CLI/PTY gates, Feishu readback, docs
Next: perform the single final read-only code review, address findings with targeted verification, then commit
Blocked/UNVERIFIED: Feishu media/reaction/groups/reconnect and `lark`; real ALFWorld remains out of scope
Focused verification: raw/event/config/Gateway/Feishu/MCP/extensions 188; Rich 27; CLI/PTY 10;
  tests/homemaster 1158 passed, 1 skipped
External terminal-state evidence: upstream HEADs superpowers=3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9,
  vercel-labs/skills=e173b8c88f2581cfdaa1b6767c6519a08155790e
Worktree/HEAD: closeout changes plus both preserved plan documents are uncommitted on 9fd31005061d55bf05e2e22314beecad22c67b26
```
