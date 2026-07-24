# OpenHarness-compatible Skills and generic network/file tools implementation plan

Status: implementation plan, product code not started
Date: 2026-07-22
Owner: main agent
Locked upstream: `../OpenHarness@9b2efd795c6aa09f88b0c257d269a9e518da6ae7`

## 1. Goal and user-visible acceptance

HomeMaster must stop treating a Skill as a Home-only tool bundle and restore the
OpenHarness/Codex progressive-disclosure contract:

1. A standard `SKILL.md` containing only `name`, `description`, and Markdown body
   loads unchanged. `tool_names` and other Home metadata are optional extensions.
2. The model receives a compact, current list of available Skills and can call
   the existing `skill_view` alias to receive the complete original `SKILL.md`.
3. `skill_view` refreshes discovery before lookup. A Skill written during the
   current process is visible without restarting HomeMaster.
4. Home profile exposes generic `web_fetch` and `write_file` tools. The agent can
   fetch public text, write `~/.homemaster/skills/<name>/SKILL.md`, and immediately
   load it through `skill_view`.
5. HomeMaster discovers Codex compatibility roots
   `~/.codex/skills` and `~/.codex/skills/.system` in addition to the existing
   HomeMaster, Agents, and Claude roots. The local Codex system
   `skill-creator/SKILL.md` is therefore usable directly, without copying it.
6. Skills remain instructions, not authority. A Skill never registers a tool,
   widens a ToolView, grants a capability, or bypasses permission checks.
7. ALFWorld and Coworker public tool manifests remain byte-for-byte compatible.
   Coworker keeps its benchmark-specific two-name `skill_view` behavior.

The generic tools deliberately increase agent authority, as accepted by the
user. Existing Home permission modes, capabilities, protected path patterns,
tool allow/deny lists, and structured audit pipeline still apply.

## 2. Root cause and current-vs-target contract

The failure is not an installation-command bug.

Current HomeMaster:

```text
application composition
  -> load one SkillRegistry snapshot
  -> require non-empty tool_names
  -> reject names outside frozen Home ToolView
  -> inject snapshot as a run dependency
  -> skill_view returns selected metadata/body fields
```

This conflicts with locked OpenHarness in four concrete places:

- `src/homemaster/skills/spec.py` requires `tool_names`; upstream
  `skills/types.py::SkillDefinition` does not.
- HomeMaster discards the complete raw `SKILL.md`; upstream preserves it as
  `SkillDefinition.content` and `SkillTool` returns that content.
- HomeMaster `skill_view` reads a composition-time snapshot; upstream
  `SkillTool.execute()` reloads discovery for every invocation.
- HomeMaster never projects `candidate_summaries()` into model context;
  upstream `prompts/context.py::_build_skills_section()` lists names and
  descriptions in the system prompt.

The existing manifest labels the loader port as mode `V`, even though the
required schema, full-content, reload, and prompt-context behavior was removed.
That port classification and its characterization tests allowed a false
compatibility claim.

Target flow:

```text
configured builtin/user/Codex/project/explicit roots
  -> refreshable OpenHarness-compatible loader
  -> compact Available Skills context

web_fetch(url)
  -> public HTTP response status + bounded untrusted text
write_file(path, content)
  -> Home permission decision -> write -> independent read-back verification
skill_view(skill_name)
  -> refresh discovery -> return exact full SKILL.md content
```

The upstream assumption that removes an existing mode is: Skills do not own or
grant tools. Therefore Skill loading no longer depends on MCP discovery or a
final frozen ToolView. The special “empty registry until MCP starts, then
revalidate Skill tool_names” branch is removed rather than patched.

## 3. Alternatives and decision

### Option 1: dedicated `skill_install` tool only

Lowest write/network surface and easiest validation, but it does not restore the
generic OpenHarness agent workflow and only supports installer-known sources.
Rejected because the user explicitly selected generic tools.

### Option 2: generic `web_fetch` + `write_file` and refreshable standard Skills

Matches OpenHarness control flow, permits the model to install ordinary text
Skills, and reuses HomeMaster permission/audit boundaries. It has a larger
security surface and cannot by itself clone repositories or preserve arbitrary
binary assets. Selected for this change.

### Option 3: expose a generic shell tool

Would support `git clone`, archives, binaries, and arbitrary installers, but
expands command execution far beyond the stated Skills goal and overlaps the
Coworker terminal surface. Deferred; this plan does not add shell execution to
Home profile.

### Option 4: allow `skill_view` to read any supplied filesystem path

Makes the local Codex path work without installation, but bypasses deterministic
source discovery, leaks arbitrary readable files through a nominal Skill tool,
and makes names/provenance unstable. Rejected. Codex roots are explicit
compatibility sources instead.

Layered route: deliver Option 2 now. Keep tool definitions and source discovery
modular so a future approved installer/download-artifact tool can reuse them,
without adding that API in advance.

## 4. Upstream source map and port modes

Every copied or adapted symbol remains pinned to the locked commit above.

| Target concern | Locked upstream source | Mode | HomeMaster delta |
|---|---|---:|---|
| Skill data | `skills/types.py::SkillDefinition` | A | retain provenance, resource root, optional Home metadata |
| Frontmatter | `skills/_frontmatter.py` | A | retain fail-closed YAML validation |
| Discovery | `skills/loader.py::{load_skill_registry,load_skills_from_dirs,discover_project_skill_dirs}` | A | add Home/Codex roots, containment, builtin override policy, diagnostics |
| Registry | `skills/registry.py::SkillRegistry` | A | deterministic precedence/provenance and atomic refresh handle |
| Skill read tool | `tools/skill_tool.py::SkillTool` | A | retain public alias `skill_view` and canonical Home execution envelope |
| Skills prompt section | `prompts/context.py::_build_skills_section` | A | implement as Home `SkillsContextProvider` |
| HTTP fetch | `tools/web_fetch_tool.py::WebFetchTool` | A | canonical Home tool result and structured logging |
| HTTP guard | `utils/network_guard.py` public-target validation | A | Home config boundary; public HTTP/HTTPS only, validate every redirect |
| File write | `tools/file_write_tool.py::FileWriteTool` | A | Home permissions, serialized writes, typed receipt and read-back verifier |

`plan/V1.9/upstream-port-manifest.json` will be corrected: loader mode `V`
becomes `A`, the missing type/tool/context/web/file/network entries are added,
source hashes are recalculated from the locked tree, and copied plus Home delta
test IDs are recorded. No external enum or API symbol is accepted merely because
it imports; `httpx` behavior is exercised in the project environment.

## 5. Data model and discovery changes

### 5.1 Skill definition

Modify `src/homemaster/skills/spec.py` so the canonical loaded object preserves:

- required `name`, `description`, `content`, `source`;
- optional `path`, `resource_root`, `command_name`, `display_name`, `aliases`;
- invocation flags and optional `model`/`argument_hint`;
- Home extensions `tool_names`, `constraints`, `success_criteria`, and
  `system_prompt_fragment`, all optional and default-empty;
- full replacement provenance.

`content` is the exact UTF-8 file text, including frontmatter. The parsed body
may populate the compatibility `system_prompt_fragment`, but `skill_view`
returns `content`, not a reconstructed document. Unknown valid frontmatter is
ignored as metadata while remaining present in `content`.

Remove the loader's `allowed_tool_names` gate. Optional `tool_names` is descriptive
only and cannot affect the catalog. Existing builtins remain valid.

### 5.2 Sources and precedence

Preserve deterministic precedence:

```text
builtin < user compatibility roots < project roots < explicit roots
```

Default user roots become:

```text
~/.homemaster/skills
~/.codex/skills
~/.codex/skills/.system
~/.agents/skills
~/.claude/skills
```

Project roots remain git-bounded. Codex project compatibility `.codex/skills`
is added alongside `.homemaster/skills`, `.agents/skills`, and
`.claude/skills`. Each root still accepts the standard direct-child layout
`<root>/<skill>/SKILL.md`; `.system` is an explicit root, not an unconstrained
recursive scan.

Builtin override authorization, source provenance, real-path containment,
symlink escape rejection, and secret-safe automatic-source diagnostics remain.

### 5.3 Refresh ownership

Introduce one application-owned refreshable registry handle in
`src/homemaster/skills/registry.py`. It stores an immutable current snapshot and
a loader callback derived from resolved Skill source configuration.

- `refresh()` loads a complete candidate first and atomically swaps only after
  validation succeeds.
- `skill_view` calls `refresh()` immediately before lookup.
- `SkillsContextProvider.collect()` calls `refresh()` immediately before listing.
- failures in automatic sources remain item diagnostics; invalid explicit roots
  fail closed and leave the previous valid snapshot intact.
- refresh is serialized so concurrent model turns cannot partially publish a
  registry.

Because Skills no longer depend on ToolView aliases, composition loads this
handle immediately whether MCP is configured or not. MCP startup no longer
replaces/revalidates the Skill registry.

## 6. Model context and `skill_view`

Add `SkillsContextProvider` to `src/homemaster/agent/context.py` and wire the
application-owned registry through the context assembler factory.

The provider renders only model-invocable Skill summaries:

```md
# Available Skills

Use `skill_view(skill_name="<name>")` to load the complete instructions.

- **skill-creator**: Guide for creating effective skills.
```

It never injects full Skill bodies. The existing `context.enabled_providers`
value `skills` becomes effective rather than silently ignored. Context token
estimation includes the rendered section.

Home `skill_view` keeps its model alias and `skill_name` argument for backward
compatibility. On success it returns full `content` plus bounded metadata and
provenance; on disabled model invocation it returns a typed refusal; on miss it
returns `skill not found` after refresh. Coworker's narrowed wrapper and enum do
not use the Home refresh handle and remain unchanged.

## 7. Generic tool design

### 7.1 `web_fetch`

Add canonical Home tool `home.web_fetch.v1`, alias `web_fetch`:

```json
{
  "url": "https://raw.githubusercontent.com/.../SKILL.md",
  "max_chars": 50000
}
```

Behavior adapted from OpenHarness:

- HTTP/HTTPS only; no embedded URL credentials;
- reject localhost, link-local, private, multicast, metadata, and non-global
  targets; validate DNS and every redirect hop;
- explicit 15 second timeout and at most five redirects;
- no ambient proxy/credential trust unless a future typed Home config explicitly
  enables it;
- preserve response URL, HTTP status, content type, truncation flag, and text;
- HTML converts to bounded readable text; plain Markdown remains exact except
  for explicit character truncation;
- prefix returned external text as untrusted data.

The definition is read-only (`state_effects=("read_only",)`), parallel, and
requires the existing `tool.read` capability through Home policy. HTTP failures
return typed failure with `backend_attempted` reflecting whether a request was
sent. Structured JSONL records URL, final status, byte/character counts, elapsed
time, and error class without response secrets.

### 7.2 `write_file`

Add canonical Home tool `home.write_file.v1`, alias `write_file`:

```json
{
  "path": "~/.homemaster/skills/example/SKILL.md",
  "content": "---\nname: example\n...",
  "create_directories": true
}
```

Behavior adapted from OpenHarness:

- expand `~`; resolve relative paths from the application working directory;
- use existing `path` permission inspection before execution, including protected
  patterns and configured denies;
- optionally create parent directories;
- write complete UTF-8 text and return resolved path, byte count, and SHA-256;
- classify as mutating (`state_effects=("filesystem.write",)`), serialized, and
  require existing `tool.mutate`; PLAN blocks it and DEFAULT can request
  confirmation, while accepted FULL_AUTO can execute it;
- use an external-state verifier that opens the resulting path independently and
  checks exact byte count and SHA-256 before the pipeline reports verified
  success. Failure after an attempted write is not reported as confirmed success.

This plan intentionally allows absolute paths that pass Home permission rules,
as requested. It does not add arbitrary binary download, repository clone,
archive extraction, executable permission changes, or shell execution.

### 7.3 Profile exposure

Register both tools only in `build_home_profile()` and append them to the Home
ordered IDs. Do not add them to `build_alfworld_profile()` or
`build_coworker_profile()`. Update the Home baseline fixture intentionally;
assert ALFWorld and Coworker fixtures are unchanged.

All tool calls continue through:

```text
model alias -> frozen ToolView -> HomePermissionPolicy
  -> ToolExecutionPipeline -> executor -> verifier -> typed result/events
```

## 8. Implementation sequence and exact surfaces

1. Add RED compatibility tests for a minimal standard Skill with no
   `tool_names`, exact raw content, Codex direct and `.system` roots, prompt
   summaries, and refresh-after-write.
2. Adapt Skill types/loader/registry and delete ToolView-dependent Skill
   validation/composition branches.
3. Adapt Home `skill_view` to refresh and return full content while preserving
   Coworker behavior.
4. Add `SkillsContextProvider` and pass the registry handle into Home context
   assembly without changing non-Home callers.
5. Add `src/homemaster/tools/network_guard.py` and
   `src/homemaster/tools/general_io.py` with canonical tool definitions,
   executors, and the file verifier; register only in Home profile.
6. Update dry-run output to report standard metadata without assuming
   `tool_names`, plus both new Home tools.
7. Correct upstream manifest and baseline fixtures.
8. Run focused tests, interface consistency audit, complete non-live suite, and
   external black-box gate.
9. Update all user-visible and architecture documentation, changelog, pitfall,
   positive engineering rule, and live progress handoff.

Expected product/test files include:

```text
src/homemaster/skills/{spec,loader,registry}.py
src/homemaster/domain/tools.py
src/homemaster/agent/context.py
src/homemaster/application/factory.py
src/homemaster/cli/{composition,dry_run,run_command,interactive_shell}.py
src/homemaster/adapters/profiles.py
src/homemaster/tools/{network_guard,general_io}.py
tests/homemaster/skills/
tests/homemaster/tools/
tests/homemaster/application/
tests/homemaster/test_context_assembler.py
plan/V1.9/{upstream-port-manifest.json,baseline/tool-surfaces.json}
```

Implementation will use existing dependencies (`httpx`, Pydantic, PyYAML); no
new package is planned.

## 9. Test matrix

### Skill compatibility and security

- standard Codex/OpenHarness file with only name/description/body loads;
- exact raw UTF-8 content round-trips through `skill_view`;
- optional Home metadata still parses but cannot change ToolView;
- unknown tool names no longer reject a Skill and never appear in catalog;
- direct `~/.codex/skills/<name>` and
  `~/.codex/skills/.system/<name>` discovery;
- source precedence, aliases, invocation flags, provenance, builtin replacement;
- malformed YAML, missing required metadata, path/symlink/resource escapes;
- automatic-source diagnostic isolation and explicit-source fail-closed behavior;
- write a new Skill after application creation, then context and `skill_view`
  both observe it without restart;
- disabled model invocation absent from summaries and refused by tool lookup.

### HTTP and file tools

- reject non-HTTP schemes, URL credentials, localhost/private/literal IPs,
  private DNS resolutions, and redirect-to-private targets;
- public response, redirect limit, timeout, HTTP error, HTML conversion,
  Markdown preservation, max length, and untrusted-content marker;
- absolute, relative, and `~` paths; parent creation; overwrite; Unicode UTF-8;
- protected/config-denied path refusal happens before executor/write;
- PLAN/default/full-auto permission behavior and `tool.read`/`tool.mutate` split;
- exact file SHA/byte read-back verification; verifier catches tampering/missing
  file; concurrent writes are serialized;
- canonical definition/executor/verifier interface audit covers every new public
  method and result schema.

### Regression and profile stability

- Home manifest gains exactly `web_fetch` and `write_file`;
- ALFWorld manifest digest and ordered aliases unchanged;
- Coworker eleven-tool manifest, its `skill_view` enum, and benchmark scorer
  input unchanged;
- MCP startup no longer controls Skill availability and existing MCP lifecycle
  tests remain green;
- dry-run, one-shot, interactive, session resume, and installed-wheel Skill
  discovery tests remain green.

## 10. External terminal-state black-box gate

The final gate runs as a fresh process in an isolated temporary `HOME` and uses
the real canonical ToolView, permission policy, execution pipeline, and Skill
loader. It must not import executor internals to decide success.

1. Fetch a pinned public raw standard `SKILL.md` over HTTPS through
   `web_fetch`; assert process/tool success, final HTTP status 200, nonempty
   returned bytes, expected pinned content marker/hash, and no truncation.
2. Feed the returned Markdown text to `write_file` at
   `$HOME/.homemaster/skills/<name>/SKILL.md`; assert pipeline success and
   verification PASS.
3. Independently open the external file from the test process and assert exact
   bytes and SHA-256, not only the tool receipt or trace.
4. Invoke `skill_view` in the same application process; assert the new exact name
   exists and returned content equals the independently read file.
5. Start a second fresh HomeMaster process with the same isolated HOME; assert
   dry-run/registry discovery returns the same exact Skill and process exit code
   zero.
6. Independently inspect the public tool manifest and assert the external
   response status/verification status, then assert each target separately
   rather than using any aggregate `any()` gate.

If restricted network prevents the HTTPS call, rerun the same command with
approved network escalation. A mocked HTTP test is not a substitute for this
gate. The pinned remote URL/hash is recorded in the test evidence so upstream
content drift fails visibly instead of silently accepting unrelated bytes.

An application-level fake model test will additionally emit the exact sequence
`web_fetch -> write_file -> skill_view` through normal tool calls and assert its
final reply, but it remains an internal orchestration test and does not replace
the external file/status checks above.

## 11. Observability, documentation, and migration

Update:

- `README.md`: Home Skills compatibility and Home generic tool capability;
- `docs/skills-and-config-user-guide.md`: standard minimal example, Codex roots,
  agent-driven text installation flow, permission examples, and limitations;
- `docs/architecture/application-runtime.md`: refreshable registry, context
  projection, new tool/policy/verifier data flow, and removed MCP dependency;
- `CHANGELOG.md`: what changed, why the previous behavior was incompatible, and
  the increased authority/security impact;
- `docs/pitfalls.md`: spec/manifest claimed an upstream port while tests only
  exercised Home-only `tool_names` Skills;
- `CLAUDE.md`: positive rule requiring an unchanged upstream-format fixture and
  user-visible end-to-end gate for every compatibility port;
- `progress.md`: current state, verification evidence, next step, blockers, and
  key environment facts.

The user guide will state precisely: the tools support bounded text fetch/write,
not arbitrary complete repository installation. Skills with referenced text
files can be installed file-by-file; binary assets, archives, dependencies, and
executables require a future explicitly approved tool surface.

## 12. Rollback and completion criteria

Rollback is one coherent revert of the new Home profile tools, refresh/context
wiring, and standard Skill data model. Files already written by an agent under
the user's home are external state and are not deleted automatically. No data
migration or irreversible format rewrite is performed.

Definition of done:

- plan review findings are dispositioned before implementation;
- all focused and full non-live tests exit zero;
- external HTTPS status and external file terminal-state gates pass;
- same-process refresh and second-process rediscovery pass per Skill;
- Home gains exactly two intended tools; ALFWorld/Coworker remain stable;
- README, user guide, architecture, CHANGELOG, pitfall, CLAUDE rule, manifest,
  and progress handoff are synchronized;
- one final read-only reviewer finds no unresolved release blocker, or each
  finding is explicitly fixed/dispositioned followed by targeted verification.
