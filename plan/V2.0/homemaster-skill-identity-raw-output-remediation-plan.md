# HomeMaster Skill Identity And Raw Output Remediation Plan

## Status

- Owner: main agent
- Date: 2026-07-24
- Implementation: not started
- Plan review: complete; findings incorporated, two user-owned ID decisions pending
- Final code review: pending

## User Decisions

1. Keep the OpenHarness operating model for external Skills: Bash plus authoritative Skill guidance. Do not add a
   dedicated `skill_install` tool.
2. Make HomeMaster paths authoritative everywhere. User Skills live under
   `~/.homemaster/skills/<skill-name>/SKILL.md`; project Skills live under
   `<git-root>/.homemaster/skills/<skill-name>/SKILL.md`.
3. Replace public/stable OpenHarness-derived tool identities with HomeMaster identities. Whether internal IDs retain
   the `.v1` incompatible-contract suffix is a pending user decision; model aliases remain `bash`, `skill`,
   `read_file`, and so on either way.
4. Remove runtime redaction of host paths, credentials, configured sensitive literals, URL userinfo/query values,
   tool inputs/results, errors, traces, config displays, and Feishu content. Runtime output must preserve exact
   values supplied to it.
5. Keep authorization, event-type/field allowlists, payload size limits, correlation, tenant ACLs, binary artifact
   isolation, and tracked-config placeholders. These controls do not rewrite runtime text and are not redaction.
6. Make interactive Rich tool output concise: show the invoked tool and useful input summary, followed by success
   or failure status. Do not render the canonical model-facing result envelope in the default Rich UI.

## Root Cause

HomeMaster changed its loader roots from OpenHarness paths to HomeMaster paths but retained the upstream bundled
`skill-creator` instructions and verification snippets. The copied instructions name `~/.openharness/skills` and
import `openharness.skills`, while the live Home loader scans `~/.homemaster/skills` and `.homemaster/skills`.
There is no installer tool in either product. The model therefore used Bash without one authoritative HomeMaster
path contract, copied the repository to `<project>/skills`, checked only shell return codes and files, and falsely
reported Registry installation.

The local CLI also reuses `PublicEventProjection`, which was designed as a remote Gateway boundary. Its free-text
host-path and credential substitutions therefore hide exact local results. Tool completion then renders the
complete canonical model envelope in a Rich panel, producing excessive output.

## Scope

### Skill contract

- Rewrite the bundled Home skill creation/maintenance guidance in
  `src/homemaster/skills/bundled/content/skill-creator.md` to name only HomeMaster commands, imports, roots, and
  verification.
- Ensure available-skill descriptions and user documentation call it a HomeMaster Skill.
- Keep `SkillLoader` roots at `~/.homemaster/skills` and `.homemaster/skills`; do not add project-root `skills/` as
  a compatibility root because doing so would preserve the mistaken contract.
- Add an executable verification example that refreshes HomeMaster's real Registry and fails unless every expected
  Skill name resolves from the selected root.
- Repair the already downloaded Superpowers copy by staging each complete Skill directory for the user root. The
  real target root was absent during planning. Recheck immediately before writing; reject any existing same-name
  target and request explicit overwrite direction rather than merging or replacing it. Publish staged directories
  atomically. Do not delete the existing project-root copy without separate user authorization.

### Public HomeMaster identity

- Change exact `openharness.<name>[.v1]` Home tool internal IDs to `homemaster.<name>[.v1]`. Do not rename dynamic
  third-party `mcp.<server>.<tool>.v1` IDs. Whether existing `home.*` and `core.*` IDs are also consolidated under
  `homemaster.*` is a pending user decision and must be represented by an explicit old-to-new table before edits.
- Change public environment/provenance labels and Home-owned class/module names that currently expose OpenHarness.
- Keep `src/openharness` as an internal vendored upstream namespace in this change. Direct upstream imports remain
  implementation details and must not appear in Home user guidance, model-visible manifests, config examples, or
  runtime public summaries.
- Add deterministic config migration for exact legacy OpenHarness IDs in `allowed_tools`, `denied_tools`, retry
  lists, exact `tool:<internal_id>` capabilities (including Gateway grants), the plan-mode policy exception, default
  profiles/views, and other persisted Home policy fields. Reject ambiguous/colliding mappings.
- Audit all interface implementations and all default profile IDs after the rename.

### Raw runtime output

- Replace public-content sanitization with exact-value projection. Event allowlisting remains, but selected content
  and metadata are copied without key-based or free-text rewriting.
- Remove `StreamingPublicTextSanitizer` and publish provider text deltas exactly and immediately. Completion must
  still reconcile the exact aggregate without duplicate output.
- Remove runtime credential/path/URL rewriting from CLI text/JSON/stream-JSON, Gateway, Feishu messages and SDK
  logs, hook/extension diagnostics, MCP results/errors, session JSONL, memory/runtime stores, application traces,
  config/doctor/dry-run/config-tool output, and benchmark traces.
- Replace helpers named `redact*`/`sanitize*` whose only purpose is secrecy with raw serialization. Retain separately
  named structural normalization and payload-length limiting.
- Pydantic `SecretStr` values used in authoritative config must be explicitly unwrapped only at requested runtime
  output boundaries so output is exact. Tracked examples remain placeholders and real config remains gitignored.
- Preserve binary/image transport rules and opaque artifact handles; sending raw binary paths or base64 through
  ordinary text is not required to satisfy exact textual output.
- Add a repository-wide classified inventory for `redact*`, secrecy-related `sanitize*`, `[REDACTED*]`, credential
  substitution, path masking, and URL stripping across HomeMaster and the vendored runtime paths HomeMaster invokes.
  Every remaining match must be recorded with a non-secrecy reason such as structural tool-pair repair or size
  limiting; dormant secrecy code is not accepted merely because a test does not reach it.

### Concise interactive tool rendering

- `ToolExecutionStarted`: render one line containing the model alias and the complete Bash command. Other structured
  inputs may use deterministic bounded summaries only with an explicit truncation marker and a durable raw-output
  reference; truncation must never be silent.
- `ToolExecutionCompleted`: render one status line only. Success is derived from the typed event; failure includes a
  bounded exact error summary and return code when supplied.
- Do not print the model-facing `ToolExecutionResult.to_public_dict()` JSON envelope in default Rich output.
- Keep `stream-json` machine output complete and exact; concision applies only to the interactive Rich renderer.
- Preserve FIFO matching for concurrent same-name tool calls and spinner cleanup.

## Tests First

1. Add failing Skill contract tests asserting the bundled guide contains HomeMaster roots/imports and no
   OpenHarness user-facing name or path.
2. Add a black-box loader test that copies a real upstream-format multi-Skill tree into
   `~/.homemaster/skills`, refreshes the live Home registry, and checks every Skill independently through both
   Registry lookup and the model-facing `skill` tool.
3. Add a negative test proving `<project>/skills` is not silently treated as installed.
4. Add catalog/profile/interface audit tests requiring every Home default ID to use `homemaster.*.v1` and every
   renamed implementation to cover the Tool interface.
5. Add legacy-ID config migration tests for allow, deny, retry, duplicate, and unknown values.
   Include exact `tool:<internal_id>` Gateway grants and the plan-mode exception.
6. Replace redaction expectations with exact-output tests containing host paths, URL userinfo/query, API keys,
   bearer tokens, passwords, configured secret literals, and nested secret-shaped keys.
7. Exercise exact output separately through local Rich/text/JSON/stream-JSON, Gateway bridge, Feishu renderer/log
   path, config tool, MCP error/resource, hook diagnostics, session JSONL, and application trace.
8. Add cross-chunk streaming tests proving exact text is emitted before provider completion and no carry/redaction
   delay remains.
9. Add Rich renderer tests proving a large Bash result envelope is absent while command summary and typed success
   or failure remain visible. Use a command longer than the old boundary with unique path/token canaries at its tail
   and assert the complete command is displayed.
10. Add the repository-wide classified redaction inventory gate and fail on every unclassified secrecy transform.

## Implementation Sequence

1. Land RED tests for the four behavior groups without modifying production code.
2. Correct bundled Skill guidance and Home public descriptions; run focused Skill tests.
3. Rename public tool IDs and labels with exact legacy config migration; run profile, permission, catalog, default
   tool, installed-wheel, and interface audit tests.
4. Remove runtime redaction from the canonical event/config/trace layers, then simplify each consumer. Delete dead
   redaction functions only after repository-wide caller audit.
5. Simplify Rich tool completion rendering without changing model-facing or stream-JSON result contracts.
6. Update README, architecture, user guide, example configuration comments, pitfalls, CLAUDE positive rules, and
   CHANGELOG. Document that runtime secrets are intentionally emitted to configured channels and traces by owner
   decision.
7. Recheck the real target for conflicts, stage and atomically publish the 14 existing complete Superpowers Skill
   directories to `~/.homemaster/skills`, and verify each target file/resource hash against the preserved project
   copy.

## Verification

### Internal gates

- Focused Skill loader/registry/tool/installed-wheel tests.
- Focused tool catalog/profile/permission/config migration/interface audit tests.
- Focused event, streaming, CLI, Gateway, Feishu, MCP, extension, trace, and config tests.
- Full repository pytest.
- Ruff check/format-check on changed Python files, compileall, Markdown fence audit, JSON/YAML parse, secret
  placeholder audit for tracked files, and `git diff --check`.

### External terminal-state gates

1. Start a fresh HomeMaster CLI process outside the source checkout against the actual user HOME repaired by this
   change. Assert all 14 exact names appear in the live Registry, each resolved path is under the actual target root,
   and invoke each Skill independently. Repeat with an isolated HOME as a separate portability test, not a substitute
   for the actual target.
2. Run a real Bash tool call whose complete command exceeds the old summary boundary and places unique cwd, path,
   URL-query and token-shaped canaries at its tail. Assert the local terminal contains every exact value and omits
   the canonical result envelope.
3. Send a unique path/token canary through the configured real Feishu channel. The concrete Feishu send receipt,
   business success fields, and message readback API are `UNVERIFIED` until queried in the installed real SDK and
   environment. Require the verified transport/business success signal and independently retrieve or inspect the
   resulting message content to prove the exact canaries are present. A mock, local enqueue, or event trace is
   insufficient.
4. Run config display and JSONL trace canaries and read the actual output files/messages to prove exact nested values
   are present.
5. For every Superpowers Skill, compare source and installed `SKILL.md`, `references`, `scripts`, and `assets` file
   lists and SHA-256 values independently. Do not use aggregate `any`/best checks.

## Documentation And Postmortem

This incident is a serious false completion: shell/file checks passed while the live Registry rejected all 14
Skills. Add a top-of-file `docs/pitfalls.md` entry with symptom, root cause, correction, and refs. Add positive rules
to `CLAUDE.md`: installation completion requires the authoritative Registry/consumer black box, and copied upstream
guidance must be rebased when ownership paths or namespaces change.

## Review Gates

1. After this plan is complete and before implementation, one read-only reviewer subagent reviews this plan once.
   The main agent resolves each finding and locks the plan without a second plan review.
2. After implementation, tests, external terminal-state verification, and documentation are complete, one read-only
   reviewer subagent reviews the final diff once. The main agent resolves findings and runs targeted verification;
   no automatic second review is allowed.

## Non-Goals

- No dedicated Skill installer.
- No full rename of the vendored `src/openharness` Python package in this change.
- No weakening of authorization, filesystem containment, tenant ACL, capability checks, or external return-code
  verification.
- No committing real credentials to Git or replacing placeholders in tracked configuration.
- No deletion of the existing project-root Superpowers copy without explicit authorization.
