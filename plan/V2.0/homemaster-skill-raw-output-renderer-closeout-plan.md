# HomeMaster Skill, Raw Output, And Rich Renderer Closeout Plan

## Status

- Owner: main agent
- Date: 2026-07-24
- Baseline commit: `9fd31005061d55bf05e2e22314beecad22c67b26`
- Implementation: complete; checkpoint code audited, minimal regression fixes applied, and external gates recorded
- Plan review: complete; three findings incorporated (surface matrix, hard Feishu gate, and blocked Skill conflicts)
- Final code review: pending one read-only reviewer after implementation, verification, and documentation
- Execution handoff:
  `plan/V2.0/homemaster-skill-raw-output-renderer-closeout-handoff.md`
- Predecessor universal-tool plan:
  `plan/V2.0/homemaster-skill-identity-raw-output-remediation-plan.md`

## Execution Result

- HomeMaster installed the two owner-supplied sources into `/hpc2hdd/home/wyuan140/.homemaster/skills`: the complete
  Superpowers tree (14 Skill directories) and Vercel `find-skills` (15 directories total). Fresh-process Registry and
  `skill` calls, per-directory file inventories, and SHA-256 comparisons passed. The prior failed 14-directory target
  remains preserved at `~/.homemaster/skills.failed-20260724-run2`; no `.codex/skills` files changed.
- Focused closeout gates passed: raw/event/config/Gateway/Feishu/MCP/extensions `188 passed`, Rich/output matrix
  `27 passed`, CLI streaming/PTY `10 passed`, and full `tests/homemaster` `1158 passed, 1 skipped`.
- Installed wheel gate passed for `/tmp/hm-closeout-wheel-final3-20260724/dist/homemaster-0.1.0-py3-none-any.whl`,
  SHA-256 `be4aa95f042a87a6f4236db5a3b9268607cb0f3221c529f8815cf8b396dfaa09`; isolated Registry verified
  `15/15` Skills and `58` tools, and installed CLI black-box tests passed.
- Real Feishu chat-list, message-create, and independent message-get readback passed with business code `0` and one
  exact unique canary. Feishu media/reaction/groups/reconnect, the `lark` domain, and ALFWorld remain `UNVERIFIED`.
- Full repository result is `1585 passed, 1 skipped, 3 failed`; the three failures are pre-existing Coworker external
  gates: two require absent `/usr/bin/google-chrome`, and the tmux/bubblewrap terminal gate reproduced an external
  grep `exit_code=1` with empty stdout. No new failure was introduced.

## Why This Plan Exists

The predecessor plan was narrowed during execution to the universal ordinary-name tool migration. That mainline is
complete, reviewed, tested, and checkpointed, but the checkpoint also contains partially audited work for three
important V2.0 outcomes:

1. HomeMaster-owned Skill identity and authoritative Skill roots.
2. Exact raw textual output and the event/public-projection boundary.
3. Concise interactive Rich rendering without weakening machine-output contracts.

Those changes are not incidental cleanup. They need their own locked scope, tests, external terminal-state gates,
documentation, and final review. This plan closes those three outcomes without reopening the universal-tool
architecture.

## Baseline Facts

- Branch: `codex/realtime-rich-streaming-cli`.
- `9fd3100` is a deliberately mixed workspace checkpoint, not a release-shaped commit.
- The worktree was clean immediately after the checkpoint.
- Universal execution is already `ToolRegistry -> PermissionChecker -> ToolExecutor`; Catalog, ToolView, Profile
  routing, Pipeline, and per-run `enabled_tool_ids` must not be restored.
- The repository contains 14 complete Superpowers Skill directories under `.homemaster/skills`, 50 files total:
  `brainstorming`, `dispatching-parallel-agents`, `executing-plans`, `finishing-a-development-branch`,
  `receiving-code-review`, `requesting-code-review`, `subagent-driven-development`, `systematic-debugging`,
  `test-driven-development`, `using-git-worktrees`, `using-superpowers`, `verification-before-completion`,
  `writing-plans`, and `writing-skills`.
- The actual user target `/hpc2hdd/home/wyuan140/.homemaster/skills` was absent when this plan was written. Do not
  infer successful user installation from the repository relocation.
- The owner later narrowed the live installation request to two upstream URLs: the complete
  `https://github.com/obra/superpowers` source and
  `https://github.com/vercel-labs/skills/blob/main/skills/find-skills/SKILL.md`. This is two requested sources, not
  a claim that the Superpowers repository contains only one `SKILL.md`; HomeMaster must inspect and install the
  source's real directory structure itself.
- `SkillLoader` already uses `~/.homemaster/skills` and project `.homemaster/skills`; the bundled `skill-creator`
  already names those roots.
- The checkpoint already deletes `events/sanitizer.py`, changes `PublicEventProjection` to copy textual values, and
  changes multiple CLI, event, MCP, Feishu, config, doctor, and trace tests to expect exact secret-shaped values.
- The checkpoint also makes `FeishuApiService.__repr__` expose its raw app secret. This expands disclosure beyond
  user/provider/tool content, but it is included in the historical owner choice locked below.
- The Rich renderer already has concise start/completion behavior and focused tests, but it has not been accepted
  under an independent external terminal black-box gate or audited for every truncation/reference invariant.
- Locally available universal-tool verification at the checkpoint passed: `tests/homemaster` reported
  `1134 passed, 1 skipped`; focused application, generic runtime, and Registry/Executor suites passed; Ruff,
  compileall, wheel inspection, isolated CLI composition, and a real Bash file canary passed. Real ALFWorld remains
  `UNVERIFIED` because this environment has no ALFWorld engine/dataset. ALFWorld is not a closeout blocker unless
  this work changes its execution path.

## Non-Negotiable Boundaries

1. Do not modify the completed universal-tool routing architecture except for a proven regression caused by one of
   these three workstreams.
2. Skills remain instruction documents. They never grant tools, capabilities, permissions, or environment access.
3. Preserve principal capabilities, command/path rules, plan mode, confirmation/`tool.auto`, tenant ACLs, artifact
   isolation, deadlines, cancellation, resource leases, and external terminal-state verification.
4. Extension `enabled_tool_ids` remains only a load-time third-party export approval boundary.
5. Exact textual output never means committing real credentials, weakening authentication, exposing binary payloads
   as ordinary text, bypassing event field allowlists, or crossing tenant/session/run ownership.
6. Rich concision changes only the interactive presentation. Model-facing tool results and `json`/`stream-json`
   machine contracts remain complete according to their typed schemas.
7. Preserve user changes. Recheck the target immediately before any user-home Skill installation; never merge,
   overwrite, or delete an existing target without explicit authorization.

## Architecture Decision 0: Exact Output Boundary

The historical combined plan and the current repository rules disagree. The historical owner decision requested
exact credentials and paths in config displays, traces, SDK logs, and Feishu service representations. Current
`CLAUDE.md` requires public events, config displays, SDK logs, and operational traces to redact credentials, URL
queries, and host paths. The executor must not silently choose whichever rule is convenient.

### Candidates

1. **Exact canonical content, safe control-plane diagnostics (recommended).** Preserve exact user, provider, tool,
   MCP resource/error, and explicitly requested config content through authorized typed outputs. Keep authentication
   headers, connector credentials that were not part of that content, SDK request internals, object `repr`, and
   ambient operational logs secret-safe. This gives raw-output fidelity without turning incidental diagnostics into
   credential export surfaces. It is deterministic by boundary, not a runtime mode.
2. **Exact text everywhere, including ambient logs and `repr`.** This matches the broadest reading of the historical
   plan and much of the checkpoint, but materially expands credential exposure, conflicts with current repository
   rules, and makes harmless logging or exception formatting an export operation. Selecting it requires an explicit
   owner decision and synchronized updates to `CLAUDE.md`, architecture, user guide, and threat model before code
   changes.
3. **Configurable raw/redacted mode.** This supports multiple deployments but doubles the event/output state space,
   risks inconsistent projections across consumers, and was explicitly a non-goal of the historical plan.
4. **Restore blanket runtime redaction.** This is safest for accidental disclosure but recreates the original bug:
   local authoritative output is rewritten, exact tool/provider diagnostics are lost, and local and remote consumers
   again share an unsuitable projection.

### Owner Selection: Candidate 2 (`LOCKED`)

The historical combined plan records candidate 2 as an explicit owner decision: runtime textual output preserves
exact paths, configured sensitive literals, credentials, URL userinfo/query values, errors, traces, config displays,
Feishu content, SDK logs, and service representations. Candidate 1 remains the engineering recommendation because
it has the smaller disclosure surface, but it is not the selected product behavior. The execution agent must not
silently substitute candidate 1.

Before changing production code, synchronize `CLAUDE.md` and the architecture rules with this locked choice. This
is a required first implementation step, not permission to weaken authentication, authorization, tenant ownership,
binary isolation, tracked-secret rules, or invalid-auth non-echo behavior. Candidates 1, 3, and 4 require a new
owner decision.

### Locked Surface Matrix

`exact` means no key-, pattern-, configured-literal-, path-, URL-, or chunk-based rewriting. It does not bypass the
existing typed event field allowlist or size/binary rules. `secret-safe` is not selected for any accepted runtime
text surface under candidate 2; it remains applicable only to authentication rejection and tracked/static material
that is not an authorized runtime output.

| Surface / field | Treatment | Authorized subject / route | Persisted terminal state | Model or remote visibility |
| --- | --- | --- | --- | --- |
| Provider assistant deltas and final text | `exact` | Current authenticated run principal and generation | Stream/event/session outputs | Model-originated; local/authorized remote consumer sees exact text |
| Tool name, arguments, textual result, typed error | `exact` | Principal must pass existing tool permission/capability checks | Event/session/CLI outputs; owned artifact when required | Model and authorized local/remote consumer |
| CLI `text`, `json`, `stream-json` | `exact` for selected typed fields | Local CLI principal | stdout; trace/session files when configured | Local user; model sees only canonical runtime messages |
| Rich tool start | `exact` complete Bash command; exact or explicitly bounded/ref'd other input | Local CLI principal after tool authorization | Terminal stderr only unless separately traced | Local user |
| Rich tool completion | Concise typed status; bounded exact failure detail; no full result envelope | Local CLI principal | Terminal stderr only unless separately traced | Local user; model result remains complete on its own path |
| Gateway public event text/final/error/cancel | `exact` after event-type/field allowlist and generation fencing | Authenticated tenant/session/run route | Gateway event/outbound records | Authorized remote recipient |
| Feishu outbound business content | `exact` | Trusted authenticated Feishu owner route; routing target remains envelope-owned | Feishu message plus owned delivery record | Intended Feishu recipient |
| Feishu SDK logs and `FeishuApiService.__repr__` | `exact`, including configured transport values under the locked owner choice | Deployment process/file ACL; never an authorization source | Configured logs/stderr | Operator only unless another authorized surface explicitly carries it |
| Explicit config show, doctor, dry-run, and config-tool output | `exact`, including `SecretStr` values | Local CLI invocation or run principal that passes the existing registered tool permission/capability contract | stdout/tool result/trace according to invoked entry point | Local user and, for config tool, requesting model/run |
| MCP tool result, error, status detail, resource URI/content, and preview | `exact` textual values | Authenticated run plus MCP/tool permission and tenant resource ownership | Tool result; raw owned artifact where binary/size rules require | Requesting model/run; public remote route only through its allowlist |
| MCP/Feishu/hook/extension/application audit and trace text | `exact` | Deployment process and owning tenant/session/run where applicable | Configured JSONL/audit store | Operator; model only if an explicit typed result exposes it |
| Session JSONL, memory/runtime store text, benchmark traces | `exact` | Owning tenant/session/run or benchmark process | Their authoritative owned files/stores | Owner/operator; not automatically public |
| Authentication headers/tokens on rejected or unauthenticated requests | `forbidden` from echoed error/event output | No authenticated subject exists | Rejection audit may contain only non-secret status/identity hash | Never model/remote-visible |
| Binary/image/audio/video bytes and host storage paths used only for transport | `opaque-ref` outside their authorized artifact store | Tenant/session/run ACL and artifact capability | Owned artifact store | Model/remote sees only the existing allowed media block or opaque reference |
| Tenant/session/run correlation and routing fields | Allowlisted structural copy only | Authenticated route and generation | Typed event/delivery record | Only fields already permitted by the public contract |
| Tracked config/examples, source, docs, fixtures | `forbidden` for real credentials; placeholders/canaries only | Repository policy | Git tree | Public to repository readers |

The implementing agent must verify the existing registered permission/capability contract for each tool/entry point;
the matrix does not invent a new capability name. Content cannot grant itself authority, and a model-provided route,
path, URI, or credential cannot override authenticated ownership.

## Target Data Flow

```text
authoritative user/provider/tool content
  -> typed RuntimeEvent / RunResult (exact text)
  -> boundary-specific field allowlist + ownership + size/binary rules
  -> local text/json/stream-json or authorized remote content (exact text)

control-plane credentials and transport internals
  -> typed secret/config objects
  -> unwrap only at an authenticated external call or matrix-authorized exact output
  -> config displays, SDK logs, repr, diagnostics, and audit text are exact under candidate 2

unauthenticated credentials, tracked material, and binary/ownership internals
  -> invalid-auth non-echo / placeholder-only Git policy / ACL artifact isolation
  -> forbidden text echo or opaque reference as specified by the matrix

ToolExecutionStarted / ToolExecutionCompleted
  -> same exact typed event stream
  -> machine sinks preserve typed contract
  -> Rich sink renders concise start/status view only
```

No second event model, optional redaction mode, or renderer-owned authorization branch is introduced.

## Workstream A: Skill Identity And Installation

### Outcome

Every HomeMaster instruction, discovery path, installed package, and real Registry agrees that user Skills live at
`~/.homemaster/skills/<name>/SKILL.md` and project Skills live at
`<git-root>/.homemaster/skills/<name>/SKILL.md`. A filesystem copy is not considered installed until a fresh
HomeMaster process resolves and reads it through the authoritative Registry and `skill` tool.

### Tests First

1. Add/strengthen contract tests for the bundled `skill-creator`: HomeMaster roots, commands, and imports are
   present; user-facing OpenHarness paths/names are absent.
2. Add a negative black-box proving `<project>/skills` is not an automatic discovery root.
3. Copy an unmodified upstream-format multi-Skill tree, including `references/`, `scripts/`, and `assets/`, into an
   isolated `~/.homemaster/skills`; refresh the live Registry and assert every expected name independently through
   Registry lookup, `skill(name=...)`, and the compatibility `skill_view` path while it remains supported.
4. Audit all Skill source implementations against the same precedence/provenance/containment contract: bundled,
   builtin, user, project, explicit, and data-only plugin.
5. Build and install a wheel, then assert bundled Markdown/package data and Registry behavior outside the checkout.
6. Add a per-Skill tree inventory test. Compare relative file sets and SHA-256 values; aggregate `any` or one best
   Skill is not an acceptance gate.

### Implementation

1. Produce a current-state matrix before editing: each intended behavior, production owner, existing test, and
   missing external gate. Do not rewrite already correct loader/Registry code.
2. Finish HomeMaster naming in bundled guidance, Available Skills context, README, user guide, examples, and
   verifier output. Historical attribution and vendored-source records may retain OpenHarness names.
3. Keep only the authoritative automatic roots. Do not add `.codex/skills`, `.claude/skills`, `.agents/skills`, or
   `<project>/skills`; those sources require explicit migration/configuration.
4. Make `scripts/v20/verify_skill_installation.py` consume the universal Registry/Executor without reviving legacy
   profiles. Ensure it verifies real process return codes and each resolved path/content independently.
5. Recheck the actual user root, then give the two owner-supplied URLs verbatim to a real HomeMaster run. HomeMaster,
   not the implementing agent, must inspect, download, stage, hash, and publish the complete upstream Skill trees.
   If any target exists, stop before mutation and request an explicit per-name overwrite/skip decision; do not merge
   directory contents. Until the owner resolves every conflict, Workstream A and the overall closeout are `BLOCKED`.
   If the owner selects `skip`, update this plan's target and completion claim explicitly; a skipped target cannot be
   reported as installed.
6. Do not delete the repository `.homemaster/skills` copy. Its project-scoped behavior and the user installation are
   separate supported terminal states.

### External Terminal-State Gates

1. Run the installed HomeMaster with the two owner-supplied URLs and actual HOME. After it exits successfully, use a
   fresh process outside the checkout to enumerate every `SKILL.md` HomeMaster installed from both sources; invoke
   each independently through `skill` and compare returned content/path to disk.
2. Repeat in an isolated HOME using the built wheel. This is a portability gate, not a substitute for the actual
   user target.
3. For every Skill discovered under either requested source, independently compare source and installed relative
   file lists and SHA-256 values.
4. Run the migrated `skill-creator` script to create and validate a new disposable Skill under an isolated
   `.homemaster/skills`, start a second CLI process, and prove dynamic discovery. Check every subprocess return code.

## Workstream B: Raw Text And Event Boundary

### Outcome

Authorized canonical textual content is byte-for-byte/character-for-character faithful across the selected output
surfaces, while control-plane credentials, ownership metadata, binary artifacts, and operational diagnostics obey
the locked Decision 0 boundary. The exact seven public stream-event types and one terminal `RunResult` ownership are
unchanged.

### Tests First

1. Materialize the locked matrix as a machine-checkable classified output-surface inventory covering CLI
   text/JSON/stream-JSON, Rich, Gateway bridge, Feishu
   content and SDK logs, config/doctor/dry-run/config tool, MCP results/errors/resources, hook/extension diagnostics,
   session JSONL, memory/runtime stores, application trace, and benchmark traces.
2. For each surface, classify each field as `canonical_content`, `control_plane_secret`, `ownership_reference`,
   `binary_artifact`, or `operational_metadata`; record the responsible projection and expected
   exact/forbidden/opaque behavior.
3. Add failing table-driven tests with distinct canaries for host path, URL userinfo/query, bearer token, API-key
   shape, password assignment, configured secret literal, nested secret-shaped key, Unicode, and chunk boundaries.
   Do not use one canary to claim all surfaces passed.
4. Add streaming tests proving the first exact delta is externally visible before provider completion, no
   cross-chunk carry delays content, and completion reconciles the authoritative aggregate without duplicate final.
5. Add negative tests proving event-type/field allowlists, payload limits, tenant/session/run ACLs, binary isolation,
   generation fencing, and invalid-auth non-echo behavior still fail closed.
6. Add a repository inventory gate for `redact*`, secrecy-related `sanitize*`, `[REDACTED*]`, credential
   substitution, path masking, URL stripping, `SecretStr` unwrapping, and secret-bearing `repr`. Every remaining
   match needs a classified owner and reason; deletion-by-name without caller audit is forbidden.

### Implementation

1. Trace each canary from source to sink and identify the first boundary that changes or leaks it before editing.
2. Separate structural projection from content transformation. Keep typed event selection, ownership correlation,
   artifact reference validation, JSON-safe copying, and length rules independently named and tested.
3. Preserve exact canonical content at the locked candidate-2 boundary. Never derive authorization from content,
   renderer metadata, or an opaque artifact handle.
4. Keep control-plane secrets in `SecretStr`/typed secret storage. Unwrap only at a specifically authorized external
   call or a surface marked `exact` in the locked matrix. Invalid-auth errors remain non-echoing because no
   authenticated output route exists.
5. Audit MCP separately: ACL-protected raw resources retain their URI/content; model previews, public metadata, and
   audit text follow the locked matrix. Resource ownership and binary transport still use ACL artifacts/opaque refs.
6. Audit Feishu separately: exact outbound message content must not be rewritten, but routing identity, credentials,
   headers, and SDK internals cannot be sourced from or overwritten by model-visible content.
7. Delete dormant secrecy transforms only after the classified caller audit. Retain structural normalization and
   payload limiting under names that do not imply secrecy.
8. Update contradictory rules and docs in the same change. Do not leave `CLAUDE.md`, architecture, README, user
   guide, and tests asserting different output policies.

### External Terminal-State Gates

1. Installed CLI: use a provider/tool fixture that blocks after the first delta. Assert the terminal/file receives
   each exact canonical canary before completion, the process is still running at that point, the final aggregate is
   exact and appears once, and the exit code is zero.
2. JSON and stream-JSON: parse actual stdout independently, assert schema/order/final ownership, and check stderr
   separation. Read the actual JSONL/session/trace files and apply the Decision 0 matrix per file.
3. Config/doctor/dry-run/config-tool: invoke real installed entry points, check exit codes, and compare actual output
   with the locked exact/forbidden/opaque field matrix. Direct helper serialization is not sufficient.
4. Feishu: send unique harmless token/path-shaped canaries through the configured real channel; require verified
   transport/business success and independently retrieve the resulting message content through a verified SDK/API
   readback. Mock enqueue, local event trace, or user visual inspection is not PASS. The SDK/API readback symbol is
   `UNVERIFIED` until checked in the installed real environment. If credentials, the contract, or the environment
   are unavailable, Workstream B and the overall closeout are `BLOCKED/UNVERIFIED`; do not enter final review, claim
   DoD, or create a release/completion commit.
5. Confirm actual operational logs and object representations satisfy the selected candidate with independent file
   reads. Never use a real credential as a canary.

## Workstream C: Concise Rich Renderer

### Outcome

Interactive Rich output shows what tool is running and whether it succeeded without dumping the canonical
model-facing result envelope. Machine output remains complete and deterministic.

### Tests First

1. `ToolExecutionStarted`: Bash renders the complete command, including a unique canary beyond the old summary
   boundary. Other structured inputs use deterministic bounded summaries only when an explicit truncation marker
   and durable raw-output reference exist.
2. `ToolExecutionCompleted`: success renders one concise status line; failure renders one bounded exact error summary
   plus return code when present. The full model-facing result envelope/body is absent from default Rich output.
3. Assert text/JSON/stream-JSON still expose their typed contracts and are not shortened by Rich helpers.
4. Cover FIFO matching for concurrent same-name calls, nested/interleaved tools, spinner/live-region cleanup,
   cancellation, failure, and terminal final ownership.
5. Add terminal-width cases and long unbroken words so content never overlaps or silently disappears.

### Implementation

1. Keep rendering downstream of typed stream events. The renderer must not inspect permissions, mutate events, or
   become an alternate result owner.
2. Use an explicit tool-call correlation identifier if the typed stream already provides one. If it does not, first
   prove whether FIFO-by-name is sufficient for all producers; do not add renderer-only IDs without synchronizing
   every event producer and consumer.
3. Render the full Bash command exactly. For other inputs, either render a complete deterministic summary or persist
   the full value through the existing artifact/output-store ownership path and show an explicit truncation marker
   plus opaque reference.
4. Derive success/failure from typed completion status, not output text. Bound only interactive failure detail;
   preserve complete machine/event content.
5. Keep Rich/progress on stderr and text/json/stream-json data on stdout. Ensure final assistant output appears once.

### External Terminal-State Gates

1. Run the installed CLI in a real PTY at narrow and wide widths. Execute a Bash command longer than the former
   boundary with distinct head/tail canaries; assert both are visible, one concise completion appears, the canonical
   envelope/body is absent, and return code is zero.
2. Run the same request in JSON and stream-JSON modes. Parse stdout, assert complete typed data, and prove Rich
   control text did not contaminate it.
3. Run two overlapping same-name tools and one failure. Assert per-call start/completion pairing, no stuck spinner,
   one final, and stable terminal layout after exit.

## Execution Order

1. Confirm the locked Decision 0 against current owner instructions, then synchronize the contradictory governing
   rules before production edits. Any requested policy change returns the plan to owner decision rather than being
   improvised during implementation.
2. Capture a baseline matrix and rerun focused existing tests. Record failures without changing production code.
3. Add RED tests for all three workstreams. Each failing test must identify a real missing invariant, not merely an
   outdated checkpoint expectation.
4. Finish Workstream A and its focused/internal/wheel/actual-user external gates.
5. Finish Workstream B from canonical event boundary outward; run focused gates after each consumer group.
6. Finish Workstream C after the event contract is stable.
7. Run cross-workstream installed-wheel CLI tests and every required external terminal-state gate. A missing real
   Feishu send/readback gate blocks the closeout; it is not replaced by a local test.
8. Update README, architecture, Skills/config user guide, `docs/pitfalls.md`, governing positive rules, CHANGELOG,
   this plan, and the live handoff. User-visible behavior and current data flow must agree.
9. Run the complete internal verification set and inspect the worktree for test-created files.
10. Start exactly one read-only final code reviewer. Resolve every finding, document non-adoptions, and run targeted
    verification only; do not automatically request a second final review.
11. Before commit, make CHANGELOG and commit message describe the same closeout change and validation. Do not call
    the mixed checkpoint a release commit.

## Verification Matrix

### Focused Internal Gates

- Skills: loader, Registry, commands, package data, plugin/data-only sources, installation verifier.
- Raw/event: public projection, streaming, sinks, config/doctor/dry-run, Gateway, Feishu, MCP, hooks/extensions,
  session/trace, benchmark projections.
- Renderer: event sinks, CLI run, CLI streaming black box, concurrent tool rendering.
- Universal regression: Registry/Executor, application runtime/factory, permission and cancellation suites.

### Repository Gates

- Full `tests/homemaster` suite.
- Full repository suite with live/external failures separately classified; no new failure may be waived as an
  environment issue without reproducing the baseline cause.
- Ruff check and format check on changed Python files.
- `compileall`, Markdown fence/link checks, JSON/YAML parsing, tracked-secret placeholder audit, classified redaction
  inventory, legacy universal-tool symbol audit, and `git diff --check`.
- Clean wheel build and isolated installation outside the checkout.

### Required Evidence Record

For every external gate record: command, environment/interpreter, installed artifact hash, start/end time, return
code, per-target assertion results, and independently read terminal/file/API state. `UNVERIFIED` remains explicit;
tests or traces cannot relabel it PASS.

## Documentation And Commit Boundary

- `README.md`: authoritative Skill roots, locked exact/forbidden/opaque output policy, Rich vs machine behavior.
- `docs/architecture/application-runtime.md`: the target data flow and ownership/security invariants.
- `docs/skills-and-config-user-guide.md`: complete installation, verification, and output examples.
- `docs/pitfalls.md`: preserve the false Skill-installation lesson and add any new non-obvious boundary failures.
- `CLAUDE.md`: reconcile its output rules with locked Decision 0; do not leave contradictory instructions.
- `CHANGELOG.md`: one closeout entry stating what changed, why, impact, verification, and any `UNVERIFIED` external
  gate. The commit message must carry the same facts.
- Keep the universal-tool checkpoint and closeout logically distinguishable in history. A later split/rebase is a
  separate user decision; this plan does not rewrite published history.

## Review Gates

1. Complete: one reviewer subagent performed the single read-only plan review; the main agent adopted all three
   findings and locked the plan without a second plan review.
2. After all implementation, tests, external terminal-state verification, and documentation are complete, one
   reviewer subagent performs one read-only final code review. The implementing main agent resolves findings and
   runs targeted verification without an automatic re-review.

## Non-Goals

- No dedicated `skill_install` tool.
- No restoration of Catalog/Profile/ToolView/Pipeline or runtime tool filtering.
- No rewrite of every tool backend into a new abstraction.
- No new event taxonomy or alternate terminal-result owner.
- No optional raw/redacted runtime mode unless Decision 0 explicitly selects candidate 3.
- No deletion of repository or user Skill trees without separate authorization.
- No claim that real Feishu readback or ALFWorld engine behavior is verified until tested in the actual environment.
