# HomeMaster Tool, Skill, And Raw Output Unification Plan

## Status

- Owner: main agent
- Date: 2026-07-24
- Implementation: complete for the universal-tool mainline; final review pending
- Plan review: complete; applicable findings incorporated before the user locked the replacement design
- Final code review: pending
- External verification: clean wheel and real Bash/task process terminal-state gates passed; real ALFWorld remains
  `UNVERIFIED` because the installed environment has no ALFWorld engine/dataset
- Handoff: `plan/V2.0/homemaster-skill-identity-raw-output-remediation-handoff.md`

## Decisions

1. Port the OpenHarness tool model into the `homemaster` namespace without a runtime dependency on the
   `openharness` package: one `BaseTool`, one `ToolRegistry`, small `ToolResult`, and one ordinary-name execution
   path.
2. Expose every registered tool to the model by ordinary name. Remove environment ToolViews and capability/source
   routing. Keep `.v1` only as hidden `stable_id` metadata.
3. Fail application composition on duplicate names. Migrate legacy namespaced IDs in config to ordinary names with
   warnings; reject allow/deny conflicts and unknown names.
4. Keep application concerns outside the tool core: Gateway authentication, tenant artifact ownership, generation
   fencing, cancellation, deadlines, event correlation, and binary artifact isolation.
5. Keep OpenHarness-style permission checks by ordinary tool name, read-only status, command rules, and path rules.
6. Use the ALFWorld `robot_go_to` input contract `{"target": "..."}`. A missing connected Backend returns
   `unsupported_capability`; no Home fake success implementation remains.
7. Preserve extension `enabled_tool_ids` only as a load-time approval boundary for third-party code. It decides
   which approved extension exports are registered; it must not become a per-run ToolView or request filter.
8. Freeze Skills migration, raw-output/redaction changes, Rich rendering changes, and broad naming cleanup during
   this execution. Existing worktree changes in those areas are preserved but are not expanded or used as evidence
   that the universal-tool migration is complete.

## Scope Correction

The mainline is narrower than the earlier combined remediation plan:

- Replace `Profile -> ToolView -> enabled_tool_ids -> ToolCatalog -> ToolExecutionPipeline` in the application and
  environment composition path with one ordinary-name `ToolRegistry -> ToolExecutor` path.
- Keep real security controls: authenticated principal capabilities, command/path deny rules, plan mode,
  confirmation/`tool.auto`, deadlines, cancellation, resource leases, and external terminal-state verification.
- Keep extension export approval at load time. Removing it would broaden the authority of approved third-party
  packages and is not required to remove runtime tool routing.
- Allow existing tool bodies to cross one explicit migration adapter into `BaseTool`; do not turn this task into a
  rewrite of every backend implementation.
- Do not continue Skills, raw-output, renderer, event, or generic naming cleanup in this execution.

Rejected alternatives:

1. Leave Catalog/Profile compatibility builders in the wheel. Lowest effort, but preserves a second architecture
   and lets future callers accidentally restore per-environment filtering.
2. Rewrite every tool body directly as a `BaseTool`. Cleanest eventual endpoint, but mixes backend rewrites into the
   routing change and greatly expands regression risk.
3. Remove extension export approval together with runtime ToolViews. Superficially uniform, but silently grants all
   exports from an approved extension; this weakens a genuine security boundary.

Selected route: delete the runtime/composition routing abstractions and their production consumers, preserve the
load-time extension approval boundary, and retain a one-way implementation adapter until tool bodies are migrated
separately.

## Public Interfaces

```python
class BaseTool:
    name: str
    stable_id: str
    description: str
    input_model: type[BaseModel]

    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolResult: ...

@dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class ToolExecutionContext:
    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)
```

`ToolRegistry` is keyed only by `BaseTool.name`. Registration is ordered and duplicate names raise an error. Model
schemas come directly from `input_model.model_json_schema()`. All built-in stable IDs use
`homemaster.<ordinary-name>.v1` and are diagnostic metadata only.

## Implementation

1. Add the new core and ordinary-name permission checker, then migrate the application execution loop to validate
   Pydantic input, evaluate permissions, execute `BaseTool`, and return `ToolResult`.
2. Convert built-in, Home, ALFWorld, Coworker, task-state, MCP, and extension registrations. Build one Registry in
   the composition root and remove Catalog/View/Profile/Pipeline after every production consumer is migrated.
3. Port required cron/task/team support into HomeMaster modules, remove production imports of `src/openharness`, and
   replace `homemaster.tools.openharness_*` names. Historical attribution remains in notices and design records.
4. Retain extension export approval as a composition-time subset, adapt approved tools once, and atomically register
   them by ordinary name. MCP and Feishu tools use the same atomic Registry operation.
5. Delete obsolete Catalog/View/Pipeline modules, the old pipeline-only permission policy, profile builders, and
   tests that assert the removed architecture. Port behavioral coverage to Registry/Executor rather than keeping
   compatibility APIs for tests.
6. Update README, architecture, user guide, CHANGELOG, this plan, and the live handoff to describe the single path
   and the retained security boundaries.

## Tests And External Gates

- Unit/integration: duplicate registration, schemas, ordinary-name permissions, legacy migration, missing Backend,
  ALFWorld navigation contract, identical tool set across entry points, and interface coverage for every tool.
- Build a wheel, install it into a clean virtual environment, and run the CLI without the source checkout or
  `openharness` package.
- Real Bash canary: check return code and independently read the created file. Real ALFWorld canary: choose a target
  from actual observation, check engine return code, and independently observe the state change.
- Assert the installed wheel has no Catalog/View/Pipeline modules and importing/composing HomeMaster does not load
  or require `openharness`.
- Run focused and full pytest, Ruff, compileall, config/Markdown parsing, and `git diff --check`.
- After implementation, tests, external verification, and docs, run one read-only final code review. Resolve findings
  and run targeted verification without a second automatic review.

## Non-Goals

- No `skill_install` tool.
- No profile ban/allow design in this change.
- No Backend architecture rewrite.
- No optional runtime redaction mode and no real credentials committed to Git.
