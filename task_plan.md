# Real-Time LLM Observable Coworker Delivery

## Goal

Deliver two continuous videos executed by real Mimo `mimo-v2.5`: `normal` and
`post_change_anomaly`. Each video must visibly show the model plan, selected
tools, environment results, deterministic decision summaries, incidents, and
recovery. Scripted runs remain presentation tests and cannot satisfy final
acceptance.

## Gates

- A final accepted run must have a successful top-level attempt, real provider
  identity, fresh successful model responses, 100 trajectory/result/overall
  scores, expected external state, successful process return codes, and an
  independent verifier PASS.
- Assertions are per run and per target. A successful video or business state
  cannot hide a lifecycle, provider, artifact, or instance failure.
- Every failed real attempt remains preserved and is included in the acceptance
  report with root cause and disposition.
- Main agent performs implementation and verification. The only remaining
  subagent gate is one read-only final code review after implementation, videos,
  verification, documentation, and report are complete.

## Phases

| Phase | Status | Exit criterion |
|---|---|---|
| 1. Design and implementation plan | complete | Design, plan, and plan-review disposition are committed. |
| 2. Observable presentation implementation | complete | Presentation v2, pure reducer, five-zone observer, safe projection, and verifier are committed. |
| 3. Controlled presentation gates | complete | Normal/anomaly failure recovery and every named incident frame pass. |
| 4. Internal verification | complete | 791 tests pass, lint/compile pass, and Mimo preflight passes. |
| 5. Recording-stop lifecycle repair | complete | Dedicated timeout, idempotent service stop, RED/green tests, and real two-stop HTTP gate pass. |
| 6. Real normal acceptance | complete | `coworker-20260720-024949-b7004546` passes shell, external-state, video, visual, and independent verifier gates. |
| 7. Real anomaly acceptance | complete | `coworker-20260720-025635-a46d87ca` passes per-target rollback, video, visual, and independent verifier gates. |
| 8. Acceptance report and final audit | complete | All attempts, hashes, evidence, 793/1 tests, preflight, dual verifiers, docs, and clean worktree agree. |
| 9. Final code review | complete | Both P2 findings are fixed; 798/1 tests, compileall, focused checks, and dual stronger verifiers pass. |
| 10. Gateway activation and live verification | complete | The configured Gateway ran successfully, its persisted session reached `replied`, and the owner confirmed normal Feishu message delivery and readback. |

## Failed Attempts To Preserve

- `coworker-20260720-022516-8c773877`: real normal business and video success,
  but attempt failed because a 20-second client timeout caused a duplicate,
  non-idempotent recording stop. This attempt is evidence for the lifecycle
  root cause, not a final accepted video.

## Current Next Action

Commit the accepted Linux/macOS tool portability, structured input-error,
`load_skill`, Gateway documentation, and scripted-gate compatibility changes.

# 2026-08-21 Configuration And Dead-Code Audit

## Goal

Reduce HomeMaster's configuration and implementation surface without deleting a
supported capability. A deletion is accepted only when its configuration key,
module, symbol, or branch is proven unreachable from shipped entrypoints and
dynamic discovery paths, or when the user explicitly removes that product scope.

## Cleanup Strategies

1. **Proven-dead-only (recommended first layer):** delete only candidates with
   converging static, runtime, test, documentation, and packaging evidence. Lowest
   regression risk, but it will not remove intentionally supported benchmark/demo
   verticals merely because the current operator does not use them.
2. **Remove selected verticals:** delete complete benchmark/demo slices such as
   ALFWorld or coworker only after an explicit product-scope decision. Larger
   reduction, but removes CLI/docs/tests/data and may break external users.
3. **Memory-only product:** retain the agent/memory/provider core and redesign the
   package around it. Largest simplification, but this is an architectural and
   public-API change requiring a separate approved plan and migration story.
4. **Archive/deprecate:** move legacy material out of active paths or warn before
   later deletion. Lowest immediate compatibility risk, but does not meet the
   request to actually remove dead code and keeps maintenance overhead.

## Phases

| Phase | Status | Exit criterion |
|---|---|---|
| A. Baseline and product-surface inventory | in_progress | Dirty paths, entrypoints, package data, dynamic loaders, configs, docs and supported commands are mapped. |
| B. Configuration consumer audit | pending | Every tracked example/manifest and every schema field is classified as required, optional-live, compatibility, or dead with consumer evidence. |
| C. Whole-repository reachability audit | pending | Python, scripts, apps, web, data and package exports have static plus dynamic reachability evidence; test-only and externally public surfaces are distinguished. |
| D. Deletion boundary decision | pending | Proven-dead candidates are locked; any vertical/product-scope candidate is separately presented for user approval. |
| E. RED guards and scoped deletion | pending | Failure-first tests/guards cover the intended absence and supported paths before deletion. |
| F. Internal and black-box validation | pending | Per-entrypoint tests, lint/type/build checks, package inspection and real CLI/config terminal-state gates pass with return codes checked. |
| G. Documentation and handoff | pending | README, user/architecture docs, CHANGELOG where applicable, findings/progress and session handoff match the final product surface. |

## Acceptance Gates

- No tracked or untracked user work is overwritten; current untracked `story/`
  report assets remain untouched.
- Real secret-bearing `config/homemaster.yaml` and
  `config/coworker_demo.yaml` remain gitignored and are never printed or staged.
- No candidate is called dead solely because `rg` finds no import: CLI entrypoints,
  string imports, extension/skill/MCP discovery, package data, subprocess targets,
  tests and documented public APIs are audited independently.
- Configuration deletion must fail on unknown stale keys or otherwise prove that
  the loader intentionally ignores them; example and real configuration schemas
  are compared by key path without exposing values.
- Each retained shipped CLI path gets an independent process return-code gate.
  Each removed path gets an absence gate against the built wheel/install tree.
- A green unit suite is not final acceptance; the built artifact and at least one
  real configured HomeMaster invocation must demonstrate the external terminal
  state expected after cleanup.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---|---|
| Initial repository-wide `rg` output exceeded the capture budget and was truncated. | 1 | Replace monolithic text search with machine-readable inventories and bounded per-surface queries. |
| Remote host has no `apply_patch`. | 1 | Copy only target files to a local temporary mirror, edit with `apply_patch`, copy back, and verify hashes/status. |
| First `scp` was blocked by the prior filesystem/network sandbox and the turn was interrupted. | 1 | After permissions changed, verify the local directory was empty and the remote hashes were unchanged, then copy again. |
| Module reachability audit read regex group 1 from a pattern with no capture group. | 1 | Record the audit-tool failure, change it to `group(0)`, and rerun before using any result. |
| Module graph omitted absolute `from` imports in scripts/tests, yielding 28 candidates and all-false test reachability. | 2 | Reject the report, parse level-0 imports without requiring a current package, and rerun. |

## Current Next Action

Complete Phase A by mapping package/CLI/dynamic-loader/config consumers and
recording the first evidence-backed candidate table. Do not delete code yet.
