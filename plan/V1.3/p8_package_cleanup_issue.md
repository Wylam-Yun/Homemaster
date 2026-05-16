# HomeMaster Agent Harness Engineering Issues

## 0. Summary

The main engineering lesson from the Agent Harness methodology is that HomeMaster should not remain only a Stage Pipeline. It should gradually evolve into a state-driven Agent Runtime.

Current HomeMaster already has the key components of an agent system:

- task understanding
- memory retrieval
- grounding
- planning
- skill execution
- verification
- recovery
- summary
- memory commit

However, these components are still mainly organized as a linear Stage Pipeline:

```text
Stage02 Task Understanding
  ↓
Stage03 Memory RAG
  ↓
Stage04 Grounding / Planning Context
  ↓
Stage05 Planning + Execution + Verification + Recovery
  ↓
Stage06 Summary + Memory Commit
```

This is useful for functional validation, but it is not yet a real Agent Runtime.

The follow-up engineering direction should be:

```text
From Stage Pipeline
To State-driven Agent Runtime
```

The target architecture should include:

* a single `AgentRuntime` loop
* a system-level `AgentState`
* Mimo-driven structured tool decisions
* `MimoDecisionClient` boundary with fake/live implementations
* runtime-side schema / permission / availability guards
* Schema-driven `ToolSpec` / `SkillSpec`
* `Dispatcher` and `StateUpdater`
* runtime event trace
* centralized per-turn `ContextBuilder`
* pluggable memory / skill / context / hook mechanisms

The key principle is:

> Mimo decides the next tool call from state and context. Runtime owns boundaries: schema validation, tool availability, execution permission, state update, and trace. `phase_label` is only a trace/status label, not a fixed control-flow pipeline.

`run_homemaster_task()` should target `AgentRuntime.run()` by default. Any Stage02-06 pipeline path that remains during migration must be an explicit compatibility helper/flag, never the default production entrypoint.

## 0.1 Mini-Agent Engineering Lessons Worth Borrowing

`/Users/wylam/Documents/workspace/Mini-Agent` is a small project, but it has several engineering patterns that HomeMaster should explicitly learn from during the AgentRuntime migration.

Useful patterns:

* `mini_agent/agent.py` keeps the main runtime shape simple: LLM response, optional tool calls, tool execution, tool results, next turn.
* `mini_agent/tools/base.py` makes tools first-class with `name`, `description`, JSON schema parameters, `execute()`, and schema conversion methods.
* `mini_agent/tools/file_tools.py` puts operational constraints directly into tool descriptions: read before write, exact edit strings, line-numbered reads, offset/limit for large files.
* `mini_agent/tools/skill_loader.py` and `mini_agent/tools/skill_tool.py` implement progressive disclosure: expose skill metadata first, then use a `get_skill` tool to load full skill content only when needed.
* `mini_agent/config.py` has a clear configuration object boundary.
* `mini_agent/logger.py` records the request / response / tool-result loop, making agent behavior inspectable.
* The top-level package shape, `agent.py / tools/ / llm/ / schema/ / config/ / skills`, makes the runtime, tools, model clients, schemas, configuration, and skills easy to locate.
* `docs/`, `examples/`, and `tests/` explain and protect the extension surface instead of leaving it implicit.

HomeMaster should borrow these ideas, but with stricter production boundaries:

```text
Mini-Agent message history          -> HomeMaster AgentState as the single runtime state
Mini-Agent Tool                     -> HomeMaster ToolSpec with executor_mode, output_schema, state_effects
Mini-Agent tool schema              -> compact Mimo tool manifest + runtime-only full spec
Mini-Agent skill progressive load   -> HomeMaster SkillSpec + SkillLoader + SkillRegistry + get_skill tool
Mini-Agent logger                   -> structured RuntimeEvent JSONL with secret redaction
Mini-Agent config object            -> run-scoped RuntimeSettings, not import-time globals
Mini-Agent package layout           -> HomeMaster target subpackages for agent/tools/skills/providers/events/config
Mini-Agent examples/docs/tests      -> HomeMaster docs/examples/tests as migration gates
```

Do not copy these demo-level shortcuts:

```text
do not rely on message history as the only state
do not use success/content/error-only ToolResult for embodied state updates
do not turn skills into a second executor system or a hidden workflow engine
do not log raw prompts or raw provider responses by default
do not allow wide filesystem writes without run_id/artifact boundary validation
do not let CLI become a second runtime policy implementation
do not keep adding active implementation modules to src/homemaster/ root
```

---

# 1. Issue: Current system is Pipeline-first, not a real Agent Loop

## Context

Current HomeMaster still runs mainly as a fixed Stage Pipeline:

```text
Stage02 Task Understanding
  ↓
Stage03 Memory Retrieval
  ↓
Stage04 Grounding / Planning Context
  ↓
Stage05 Planning + Execution + Verification + Recovery
  ↓
Stage06 Summary + Memory Commit
```

This means the current system mostly follows:

```text
understand → retrieve → ground → plan/execute → summarize
```

This structure is suitable for validating individual stages and making the whole pipeline run end-to-end.

## Problem

A real Agent Loop should be a state-driven tool loop:

```python
while not state.is_terminal():
    context = context_builder.build(state)
    decision = mimo.decide(context=context, tools=tool_registry.tool_manifests())

    if decision.type == "tool_call":
        tool_spec = tool_registry.get(decision.tool)
        validated_args = runtime.validate_tool_call(tool_spec, decision.arguments, state)
        result = dispatcher.execute(tool_spec, validated_args, state)
        state = state_updater.apply(state, decision, result)
        event_sink.emit_tool_result(state, decision, result)
        continue

    if decision.type == "finish":
        state.mark_finished(decision)
        continue

    state.mark_failed("invalid_decision")
```

Current HomeMaster has some execution-loop behavior inside Stage05, but this logic is still hidden inside one large stage. It has not yet become the system-level runtime architecture.

Current problems:

1. The main flow is still a fixed Stage order.
2. The system does not decide every step based on a unified runtime state.
3. Stage05 contains planning, execution, verification, and recovery, but this is not exposed as a first-class Agent Runtime Loop.
4. Stage07 is a single-task runner that wires Stage02–06 together; it is not itself a business Agent Loop.
5. The existing `PipelineContext` and `ExecutionState` are not unified into a system-level `AgentState` as the central runtime state.
6. There is no `AgentRuntime.run()` loop where Mimo chooses one structured tool call or terminal finish every turn.
7. The system is closer to a task pipeline with recovery capability than a state-driven agent.

## Impact

* The system is harder to extend to real VLN / VLA / VLM modules.
* Recovery is still tied to stage execution instead of being part of the central runtime.
* Debugging focuses on stage outputs instead of step-by-step agent behavior.
* The architecture does not yet clearly express what the agent is doing at each runtime step.
* It is hard to pause, resume, replay, or inspect the agent at a fine-grained level.

## Recommendation

Introduce a state-driven `AgentRuntime` tool loop.

The engineering implementation should not use recursive task-level and execution-level loops such as:

```text
TaskLoop
  ↓
EmbodiedLoop
  ↓
failure calls back into TaskLoop
```

Instead, use:

```text
single AgentRuntime Loop
  + layered AgentState
  + per-turn ContextBuilder
  + Mimo structured decision
  + ToolSpec registry
  + Dispatcher
  + StateUpdater
  + EventSink
```

Example runtime loop:

```python
while not state.is_terminal():
    if state.turn_index >= settings.max_turns:
        state.mark_failed("max_turns_exceeded")
        break

    context = context_builder.build(state)
    decision = mimo_decision_client.decide(
        context=context,
        tools=tool_registry.tool_manifests(),
    )
    event_sink.emit_decision(state, decision)

    if decision.type == "tool_call":
        tool_spec = tool_registry.get(decision.tool)
        validated_args = tool_spec.input_schema.validate(decision.arguments)
        result = dispatcher.execute(tool_spec, validated_args, state)
        state = state_updater.apply(state=state, decision=decision, result=result)
        event_sink.emit_tool_result(state, decision, result)
    elif decision.type == "finish":
        state.mark_finished(decision)
    else:
        state.mark_failed("invalid_decision")

    state.turn_index += 1
```

Conceptually, the system can still distinguish two levels:

```text
Task-level:
  goal understanding
  task decomposition
  memory retrieval
  global completion judgment
  replan or finish failed

Execution-level:
  navigation
  observation
  manipulation
  verification
  local recovery
```

But engineering implementation should remain one runtime loop. These labels can appear in traces as `phase_label`, but they must not become mandatory flow gates such as fixed `PLAN -> DECIDE -> ACT -> VERIFY -> RECOVER`.

Current phase should not expose an `ask_user` tool to Mimo. When information is insufficient, Mimo should make a best-effort decision from `AgentState`; if no safe progress remains, it should finish with `status="failed"` and a reason.

If legacy Stage pipeline compatibility temporarily keeps `ask_user`, it must be marked `legacy_compat_only=true`, reachable only through an explicit compatibility entrypoint, and excluded from AgentRuntime behavior.

## Acceptance Criteria

* A design document defines `AgentRuntime`, `AgentState`, structured Mimo decisions, `ToolSpec` / `SkillSpec`, `Dispatcher`, `StateUpdater`, and `EventSink`.
* The distinction between current `PipelineContext`, current `ExecutionState`, and future `AgentState` is documented.
* Runtime control is described as a single loop, not recursive double loops.
* Recovery is modeled as Mimo choosing the next tool after a failure record is written to `AgentState`, not as a forced `RECOVER` stage.
* `phase_label` is documented as a trace/status label only.
* The current AgentRuntime version explicitly does not expose or execute `ask_user`.
* Legacy compatibility `ask_user`, if temporarily retained, is explicit-only and marked outside AgentRuntime.
* The design supports future pause / resume / replay / event trace.

---

# 2. Issue: `PipelineContext` and `ExecutionState` are not enough; a unified `AgentState` is needed

## Context

Current `PipelineContext` works as the shared context for the Stage Pipeline. Each stage receives the latest `ctx`, reads what it needs, writes its output, and returns an updated context.

For example:

```text
Stage02 reads utterance → writes task_card
Stage03 reads task_card → writes memory_result
Stage04 reads task_card + memory_result + world → writes planning_context
Stage05 reads planning_context → writes execution_result
Stage06 reads execution_result → writes task_summary and memory_commit
```

This is useful for a linear pipeline.

The codebase also already has `ExecutionState`, `FailureRecord`, verification results, and recovery decision contracts. These are important runtime pieces, but they are still owned by stage-specific execution flow rather than one system-level runtime state.

## Problem

`PipelineContext` plus `ExecutionState` is not the same as a full agent runtime state.

`PipelineContext` mainly solves data passing between stages. `ExecutionState` tracks Stage05 execution progress. But a real Agent Runtime needs one state object that can represent and drive:

* current trace/status label
* current task goal
* current subtask
* current execution state
* latest observation
* memory context
* available skills
* last action
* verification result
* failure records
* recovery attempts
* terminal status

Current state is split across `PipelineContext`, Stage05 `ExecutionState`, failure records, recovery attempts, and summary evidence. These pieces are useful, but together they still do not drive a step-by-step observe / decide / act / verify / recover loop.

## Impact

* The system does not have one clear source of truth across pipeline-level and execution-level runtime state.
* Execution state, memory result, failure records, recovery attempts, and summary evidence are still owned by different modules.
* Recovery decisions are harder to reason about because state ownership is not centralized.
* Future real robot integration will require a stronger runtime state model.

## Recommendation

Introduce a system-level `AgentState` model that sits above the current pipeline contracts. It can reuse existing contracts such as `ExecutionState`, `FailureRecord`, `PlanningContext`, and `VerificationResult`, but should own the runtime status, accumulated evidence, and transition history.

Important boundary:

```text
AgentState may expose phase_label/status_label for trace and UI.
AgentRuntime must never branch on phase_label as a control-flow state machine.
The next action is chosen by Mimo decision over AgentState + ContextBuilder output.
```

Example structure:

```python
@dataclass
class AgentState:
    run_id: str
    scenario: str
    user_utterance: str

    phase_label: str | None  # trace/status only; never used for runtime branching
    task: TaskState
    current_subtask: SubtaskState | None
    execution: ExecutionState

    memory_context: MemoryContext | None
    planning_context: PlanningContext | None

    observations: list[Observation]
    actions: list[ActionRecord]
    verifications: list[VerificationResult]
    failures: list[FailureRecord]
    recovery_attempts: list[RecoveryAttempt]

    final_status: Literal[
        "running",
        "completed",
        "failed",
    ]
```

`PipelineContext` can remain for the current Stage Pipeline, and `ExecutionState` can remain as the execution-level state contract. Future runtime work should make `AgentState` the central state object that coordinates those pieces.

Legacy `needs_user_input` / `ask_user` behavior should be quarantined from `AgentRuntime` and removed from the target runtime. Current AgentRuntime must not pause for user input; if it cannot continue safely, it should finish with `status="failed"` and a reason.

If compatibility pipeline keeps `needs_user_input` during migration, that behavior is legacy-only and must not appear in default `run_homemaster_task()` or AgentRuntime final status handling.

## Acceptance Criteria

* A new system-level `AgentState` contract is defined.
* The document clearly explains how `AgentState` differs from both `PipelineContext` and `ExecutionState`.
* Trace/status labels can be represented in `AgentState` as `phase_label`, but runtime control does not branch on them.
* Target runtime status is limited to `running`, `completed`, and `failed`.
* Failure records, recovery attempts, observations, actions, and verification results are all attached to `AgentState`.
* Future runtime modules read and update `AgentState` through a controlled state updater.

---

# 3. Issue: `task_runner.py` still acts as a runtime god object

## Context

`task_runner.py` is currently the main entrypoint behind `homemaster run`.

It prepares the task run, constructs `PipelineContext`, builds the default stage registry, executes stages, handles errors, writes debug artifacts, and returns `HomeMasterRunResult`.

## Problem

`task_runner.py` currently owns too many responsibilities:

* scenario validation
* run id generation
* scenario path resolution
* runtime memory path preparation
* debug case directory preparation
* live/mock runtime mode validation
* provider availability check
* world/memory source resolution
* `PipelineContext` construction
* `StageRegistry` construction
* stage execution loop
* stage-level logging
* stage-level exception handling
* debug asset writing
* final result conversion

This makes it more than a task runner. It is currently:

```text
task starter
+ runtime policy holder
+ pipeline runner
+ error handler
+ debug writer
+ result assembler
```

## Impact

* The file becomes hard to maintain as runtime complexity grows.
* Pipeline execution logic is mixed with scenario/path setup logic.
* Error handling and debug writing cannot be reused by another runtime.
* It is hard to later introduce `AgentRuntime` cleanly.
* Changes to pipeline behavior require modifying `task_runner.py`.

## Recommendation

Split the responsibilities:

```text
task_runner.py
  responsible for starting one task run and returning result

pipeline/runner.py
  compatibility-only execution of StageRegistry over PipelineContext

config/runtime_resolver.py
  responsible for scenario, world, memory, runtime mode, and paths

events/debug_writer.py
  responsible for input / expected / actual / result.md / trace assets

agent/runtime.py
  primary Mimo-driven tool loop
```

A cleaner structure could be:

```python
resolved = runtime_resolver.resolve_run_inputs(...)
settings = RuntimeSettings.from_resolved(resolved)

agent_result = agent_runtime.run(
    user_request=resolved.utterance,
    runtime_settings=settings,
)

debug_writer.write_agent_result(agent_result)
return HomeMasterRunResult.from_agent_result(agent_result)
```

If legacy Stage02-06 behavior must remain during migration, it should be behind an explicit compatibility helper:

```python
pipeline_result = pipeline_runner.run(ctx, registry)
```

That helper is not the target runtime architecture.

## Acceptance Criteria

* `task_runner.py` no longer directly owns the stage loop.
* Primary task execution is handled by `AgentRuntime.run()`.
* Stage execution, if retained, is handled by `PipelineRunner` only as a compatibility layer.
* Debug asset writing is isolated from task setup.
* Runtime mode and data-source resolution are isolated from pipeline execution.
* `task_runner.py` becomes easier to scan and mostly reads as orchestration glue.

---

# 4. Issue: Stage abstraction should become a compatibility layer, not the future runtime

## Context

Current HomeMaster has introduced:

* `Stage` protocol
* `PipelineContext`
* `StageRegistry`
* `StageAdapter`

Each stage is wrapped into a common interface:

```python
class Stage:
    name: str

    def execute(ctx: PipelineContext) -> PipelineContext:
        ...
```

This allows the task runner to execute stages in a unified way:

```python
for stage in registry.stages():
    ctx = stage.execute(ctx)
```

## Problem

The current Stage abstraction is still thin, and strengthening it alone would move the project in the wrong direction.

`StageAdapter` mainly acts as a wrapper around existing functions:

```text
Stage02Adapter → run_stage02()
Stage03Adapter → run_stage03()
Stage04Adapter → build_planning_context()
Stage05Adapter → run_stage05_plan() + run_stage05_with_recovery()
Stage06Adapter → summary + memory commit
```

`StageRegistry` is also thin. It is mainly an ordered list with name deduplication and lookup:

* `register(stage)`
* `stages()`
* `get_stage(name)`
* `has(name)`
* `__len__()`

It does not execute the pipeline. It does not manage lifecycle, dependency validation, retries, timeouts, or failure strategy.

The actual execution loop still lives in `task_runner.py`.

However, the target architecture should not be a richer Stage Pipeline. The Stage stack should become a migration/compatibility layer while `AgentRuntime` becomes the primary runtime.

## Impact

* The system has the appearance of a pipeline framework but not a complete pipeline runtime.
* Stage order is still hardcoded in `build_default_registry()`.
* Stages cannot be easily configured, skipped, replaced, or inserted.
* Stage dependencies are implicit rather than validated.
* Logging, timing, and exception handling remain outside the pipeline framework.
* If this is treated as the main architecture problem, future work may over-invest in PipelineRunner instead of AgentRuntime.

## Recommendation

Introduce a dedicated `PipelineRunner` only as a compatibility runner.

Responsibilities of compatibility `PipelineRunner`:

* execute stages in order
* record stage start and completion events
* measure duration
* catch stage exceptions
* preserve partial context
* return structured success/failure result
* make legacy pipeline execution testable during migration

Do not add new business behavior to `PipelineRunner`. New embodied behavior should go through `AgentRuntime`, `ToolSpec`, `Dispatcher`, and `StateUpdater`.

Suggested shape:

```python
@dataclass
class PipelineRunResult:
    status: Literal["PASS", "FAIL"]
    context: PipelineContext
    failed_stage: str | None
    error_type: str | None
    message: str | None

class PipelineRunner:
    def run(
        self,
        ctx: PipelineContext,
        registry: StageRegistry,
    ) -> PipelineRunResult:
        ...
```

## Acceptance Criteria

* `PipelineRunner` exists as a separate module.
* `PipelineRunner` is documented as a compatibility layer.
* `task_runner.py` does not directly loop over stages.
* `PipelineRunner` preserves partial context on failure.
* Stage start/completion/failure events are emitted consistently.
* StageRegistry remains responsible only for registration and lookup.
* New runtime behavior is not added to Stage adapters when it belongs in AgentRuntime tools.

---

# 5. Issue: Runtime observability is too coarse for live Agent runs

## Context

After P3, live runs emit stage-level lifecycle logs:

```text
run started
stage started
stage completed
run finished
```

Example:

```text
[live-fetch-cup-001] stage stage02 started  scenario=fetch_cup_retry  modes=task_understanding=live_llm
[live-fetch-cup-001] stage stage02 completed in 17.47s  status=PASS
[live-fetch-cup-001] stage stage03 started  scenario=fetch_cup_retry  modes=memory_query=live_llm embedding=live_embedding
[live-fetch-cup-001] stage stage03 completed in 32.67s  status=PASS
[live-fetch-cup-001] stage stage05 started  scenario=fetch_cup_retry  modes=planning=live_llm step_decision=test_double step_decision_smoke=live_llm skills=mock_skill verification=mock_symbolic
[live-fetch-cup-001] stage stage05 completed in 62.02s  status=PASS
```

This confirms the pipeline is alive, but does not show what the agent is doing inside each stage.

## Problem

Current runtime logs are too coarse.

During long live runs, users cannot see:

* which LLM call is currently running
* whether the model is generating a task card, memory query, plan, step decision, recovery decision, or summary
* what orchestration plan was produced
* which subtask is active
* which skill was selected
* what `skill_input` was sent
* what the skill returned
* whether verification passed or failed
* whether recovery was triggered
* whether the system is slow or actually hung

`RawJsonLLMClient.complete_json()` also waits for the full provider response before parsing JSON. There is no model output streaming. As a result, the CLI may stay silent during long provider calls.

## Impact

* Live runs feel opaque.
* Users cannot distinguish slow provider calls from hung processes.
* Stage05 is especially hard to follow because planning, step decision, skill execution, verification, and recovery are collapsed into one stage duration.
* Debug artifacts are useful after the run, but not during the run.
* The system is not yet suitable for monitoring real agent behavior.

## Recommendation

Introduce runtime event tracing.

Mini-Agent's `AgentLogger` is a useful reference because it records the LLM request, LLM response, and tool result loop. HomeMaster should borrow that inspectability but upgrade the format and safety boundary: one JSON object per event, stable event types, compact payload summaries, and mandatory secret redaction.

Every important runtime step should emit a structured event:

```json
{
  "run_id": "live-fetch-cup-001",
  "step_index": 7,
  "phase_label": "verification",
  "event_type": "verification_completed",
  "subtask_id": "find_cup",
  "status": "failed",
  "state_status": "running",
  "failure_record_id": "failure-find-cup-001",
  "duration_ms": 1234,
  "payload": {
    "verification_passed": false
  }
}
```

Events should be emitted for:

* run started / finished
* stage started / completed / failed
* LLM call started / completed / failed
* plan generated
* subtask started / completed / failed
* step decision generated
* skill call started / completed / failed
* verification started / completed / failed
* recovery decision generated
* memory retrieval started / completed
* memory commit completed

## Acceptance Criteria

* Each LLM call emits start/end/failure events.
* Stage05 emits internal events for planning, step decision, skill execution, verification, and recovery.
* Each event includes `run_id`, `event_id`, `event_type`, `phase_label`, `status`, and timestamp/duration.
* Runtime event trace can be written to JSONL.
* Event payloads do not include raw prompts, raw provider responses, API keys, authorization headers, tokens, or secrets by default.
* CLI can optionally display important progress events in real time.
* Debug artifacts and runtime trace share consistent event identifiers.

---

# 6. Issue: Context construction is scattered; a unified `ContextBuilder` is needed

## Context

Current HomeMaster has many context-related objects:

* `TaskCard`
* `MemoryRetrievalResult`
* `PlanningContext`
* `ExecutionState`
* `FailureRecord`
* `EvidenceBundle`
* `TaskSummary`

These are produced and consumed across different stages.

## Problem

Context construction is currently distributed across stage-specific modules.

Examples:

```text
Stage02 builds TaskCard
Stage03 builds MemoryRetrievalQuery and MemoryRetrievalResult
Stage04 builds PlanningContext
Stage05 reads PlanningContext and ExecutionState
Recovery reads FailureRecord and negative evidence
Stage06 builds EvidenceBundle and summary prompt
```

This works in a linear pipeline, but it is not ideal for a runtime agent.

In a real Agent Runtime, each decision should be made from a carefully assembled context based on the current state.

Mini-Agent is useful here as a contrast. It mostly relies on message history plus skill metadata, which is acceptable for a minimal demo agent. HomeMaster is an embodied task runtime, so it should borrow Mini-Agent's progressive-disclosure instinct but not its message-history-as-state shortcut.

Without a centralized context builder:

* each stage or prompt builder decides what context to include
* context compression is inconsistent
* failure records and negative evidence may not be injected consistently
* planner, action selector, recovery, and summary may see different versions of the task state
* long-running tasks will be hard to manage as context grows

## Recommendation

Introduce a `ContextBuilder`.

Responsibilities:

```text
Input:
  AgentState

Output:
  compact per-turn context for Mimo
  current goal and terminal constraints
  recent actions and observations
  failure records and negative evidence
  target candidates and memory hits
  available tool manifest
```

The context should be layered rather than a single dumped prompt:

```text
stable_context:
  system/runtime constraints
  selected skill summaries
  compact tool manifest
  MEMORY.md snapshot
  USER.md snapshot

task_state_context:
  user request
  task_card
  target candidates
  current location/object/holding state
  memory hits and rejected evidence

recent_dynamics_context:
  recent actions
  recent observations
  recent verifications
  open failures
  negative evidence
  turn_index / max_turns
```

`MEMORY.md` and `USER.md` are model-facing snapshots only. Structured memory remains the source of truth. Mimo must not edit these files directly; it can only submit proposals through `update_memory` and `update_user_profile`, and Runtime decides whether to commit them.

Snapshot refresh must also be explicit:

```text
memory/context_snapshot.py:
  input: object_memory records + fact_memory.jsonl + user profile/preference records
  output: MEMORY.md / USER.md + source version metadata + content hash
  refresh: AgentRuntime start, accepted update_memory commit, accepted update_user_profile commit, stale snapshot detection
```

Possible module:

```text
homemaster/agent/context_builder.py
```

Example responsibilities:

* extract current task goal
* include current subtask and execution state
* include selected target or grounding status
* include memory evidence and rejected hits
* include negative evidence
* include recent observations
* include failure records
* include world summary
* include available tools and executor modes
* include compact active skill metadata and loaded skill snippets when present
* include `MEMORY.md` / `USER.md` snapshots as stable model context
* compress context without choosing the next control-flow step
* expose compact tool manifests rather than full tool implementation details
* expose skill summaries first and full skill content only after `get_skill`
* exclude full runtime trace, raw prompts, raw provider responses, and secrets by default

## Acceptance Criteria

* Context construction for the AgentRuntime decision loop is centralized.
* Failure records and negative evidence are consistently injected when relevant.
* Mimo decision prompts consume structured context objects rather than manually pulling scattered fields.
* The system clearly distinguishes raw memory/world data from compressed runtime context.
* The compact context uses `AgentState` as source of truth, not full message history.
* Context output is split into stable context, task state context, and recent dynamics context.
* `MEMORY.md` / `USER.md` are treated as read-only prompt snapshots; structured memory/profile stores remain authoritative.
* `update_memory` / `update_user_profile` are the only model-facing paths for memory/profile update proposals.
* Snapshot metadata records source versions / content hash; stale snapshots are regenerated before the next ContextBuilder build.
* Full debug/event trace is not automatically injected into the next Mimo decision.
* `ContextBuilder.build(state)` does not decide whether the next step is plan / act / verify / recover; it only prepares context.

---

# 7. Issue: ToolSpec and Mini-Agent-style SkillSpec need clear boundaries

## Context

Current HomeMaster already has a `SkillRegistry` concept. Stage05 uses selected skills such as navigation, operation, and verification in mock execution.

This is a useful starting point, but it mixes two ideas that should become separate:

```text
Tool:
  atomic executable runtime capability
  called by Dispatcher
  returns ToolResult
  updated into AgentState by StateUpdater

Skill:
  Mini-Agent-style task strategy package
  loaded from metadata / SKILL.md
  constrains and explains tool usage
  never bypasses Dispatcher or StateUpdater
```

Future HomeMaster will need to expose real tools:

* VLN navigation
* VLA manipulation
* VLM verification
* memory retrieval
* task understanding
* memory update / commit

It will also need task-level skills such as `fetch_object` and `check_object_state`, but those skills should be progressive-disclosure guidance over tools, not another execution path.

## Problem

The current skill layer is still closer to a mock execution mechanism than a full Schema-driven tool/skill system.

A real Agent Harness should treat tools and skills as first-class but different runtime concepts.

Each tool should have:

* name
* description
* input schema
* output schema
* executor mode
* execution permission / preconditions
* executor
* trace metadata
* whether it is selectable by the LLM
* whether verification is required after execution
* state effects
* failure semantics

Each skill should have:

* name
* description
* allowed tools
* activation rules
* compact context snippet
* full `SKILL.md` content path
* examples / constraints / success criteria
* version and source path

Each skill must not have:

* executor
* ToolResult
* direct AgentState mutation
* deterministic/test_double fallback
* hidden workflow control flow

Without this structure:

* skill descriptions may drift from actual input validation
* prompt tool manifests and skill summaries may become inconsistent
* future real modules will be harder to plug in
* verification requirements will be scattered
* runtime trace will not know how to represent each skill consistently
* future skills may accidentally become another Stage Pipeline if their boundary is not explicit

## Recommendation

Evolve the current `SkillRegistry` into two coordinated registries:

```text
ToolRegistry:
  executable ToolSpec entries
  direct source for Mimo tool manifest
  Dispatcher uses it to execute tools

SkillRegistry:
  Mini-Agent-style SkillSpec entries
  compact skill metadata for ContextBuilder
  full skill content loaded through get_skill
  may restrict allowed tools for active skill context
```

Example:

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    executor_mode: Literal[
        "live_llm",
        "live_embedding",
        "programmatic",
        "simulated_skill",
        "simulated_verification",
        "not_integrated",
    ]
    selectable_by_model: bool
    requires_verification: bool
    state_effects: list[str]
    failure_semantics: str
    executor: ToolExecutor | None
```

```python
@dataclass
class SkillSpec:
    name: str
    description: str
    allowed_tools: list[str]
    activation_rules: list[str]
    context_snippet: str
    content_path: Path
    examples: list[str]
    constraints: list[str]
    success_criteria: list[str]
    version: str
```

This should borrow Mini-Agent's useful `Tool.to_schema()` idea: the tool object should be able to generate the model-facing schema instead of relying on hand-written prompt strings. HomeMaster needs two views:

```text
compact Mimo manifest:
  name
  description
  input_schema
  executor_mode
  simulated marker

runtime-only full spec:
  output_schema
  state_effects
  failure_semantics
  selectable_by_model
  executor
  trace metadata
```

Tool descriptions should carry real operational constraints, similar to Mini-Agent's file tools. For HomeMaster, this means `navigate`, `observe`, `manipulate`, and `verify` must say whether they are simulated, what state they may update, and how failures are represented.

Skills should borrow Mini-Agent's `SkillLoader` / `GetSkillTool` pattern:

```text
SkillLoader:
  reads SKILL.md frontmatter and body
  validates name / description / allowed_tools / activation_rules
  resolves relative references under the skill root

SkillRegistry:
  stores compact SkillSpec metadata
  chooses candidate skills from user_request / task_card
  exposes summaries to ContextBuilder

get_skill tool:
  programmatic runtime tool
  input: skill_name
  output: full skill content + allowed_tools + constraints
  StateUpdater stores loaded skill context in AgentState
```

This makes skills implemented, testable, and ready for the next iteration, while keeping the runtime light: Mimo still returns only `tool_call` or `finish`; a skill never executes by itself.

Responsibility boundary:

```text
ToolSpec:
  declares capability, input/output schemas, executor_mode, selectable_by_model, state_effects, failure_semantics
  generates compact Mimo manifest
  never executes tools

ToolRegistry:
  stores ToolSpec by name
  returns compact manifests only for selectable_by_model=True
  may register internal tools, but does not expose them to Mimo

Dispatcher:
  validates tool existence, permission, availability, and input schema
  calls the executor
  converts executor exceptions into ToolResult failures
  never mutates AgentState

ToolResult:
  contains typed data, evidence_refs, failure_reason, retryable, and summary
  does not contain state_patch or direct AgentState mutation instructions

StateUpdater:
  is the only writer of AgentState
  interprets ToolSpec.state_effects plus ToolResult typed data/failure
  appends FailureRecord on failed tools

EventSink:
  append-only sanitized trace
  never decides flow and never mutates AgentState
```

First-version runtime tools:

```text
understand_task
retrieve_memory
ground_target
get_skill
navigate
observe
manipulate
verify
update_memory
update_user_profile
finish_task
```

V1.3 should make `verify` Mimo-selectable. Runtime should not automatically inject verification after every action; it should validate and execute `verify` only when Mimo explicitly chooses it. This keeps verification inside the tool loop instead of recreating a hidden ACT -> VERIFY pipeline.

First-version skills:

```text
fetch_object:
  allowed_tools:
    understand_task
    retrieve_memory
    ground_target
    get_skill
    navigate
    observe
    manipulate
    verify
    update_memory

check_object_state:
  allowed_tools:
    understand_task
    retrieve_memory
    ground_target
    get_skill
    navigate
    observe
    verify
    update_memory
    update_user_profile
```

`finish_task` is an internal runtime finalization helper, not a Mimo-selectable tool. Mimo terminates by returning a structured `FinishDecision`:

```json
{
  "type": "finish",
  "status": "completed",
  "summary": "The requested object has been delivered."
}
```

`ask_user` may have an interface placeholder for the future, but it must not be exposed in `tool_manifests()` in the current phase.

The same schema should drive:

* input validation
* prompt tool manifest and skill summary generation
* runtime trace
* simulated executor
* future real executor
* `StateUpdater`
* documentation/examples for adding tools

## Acceptance Criteria

* Each tool/skill has a structured `ToolSpec` / `SkillSpec`.
* Tool input validation comes from the declared schema.
* Tool manifests for Mimo selection are generated from `ToolSpec`.
* Skill summaries for Mimo context are generated from `SkillSpec`; full skill content is loaded only through `get_skill`.
* Model-facing tool manifests are compact and do not expose runtime-only executor details.
* Tool descriptions encode executor mode, allowed inputs, state effects, and failure semantics.
* `SkillSpec` contains no executor and cannot mutate `AgentState`.
* `SkillLoader` loads `SKILL.md` metadata/body and validates `allowed_tools`.
* `SkillRegistry` can select candidate skill metadata without deciding the next action.
* `get_skill` is implemented as a normal runtime tool, records trace events, and stores loaded skill context through `StateUpdater`.
* Mock and future real executors use the same registry interface.
* Verification requirements are represented in skill metadata.
* Runtime trace records tool calls and `get_skill` skill-loading events consistently.
* `navigate` / `observe` / `manipulate` report `simulated_skill`; `verify` reports `simulated_verification` until real VLN / VLA / VLM are integrated.
* `verify` is selectable by Mimo in V1.3 and appears in the compact tool manifest.
* `ask_user` is not selectable by Mimo in this version.
* `finish_task` is not selectable by Mimo; terminal behavior uses `FinishDecision`.

---

# 8. Issue: Package cleanup remains incomplete after P8

## Context

P8 moved several major components into subpackages:

* `homemaster.cli`
* `homemaster.pipeline`
* `homemaster.stages`

This resolved visible naming ambiguity around files like `pipeline.py` and `frontdoor.py`, while preserving older import paths through compatibility shims.

However, `src/homemaster/` still contains many root-level modules.

## Problem

The root package still looks crowded and partially migrated.

This is exactly where Mini-Agent has a useful lesson: even though it is small, its package layout makes responsibilities visible before opening any file. `agent.py`, `tools/`, `llm/`, `schema/`, and `config.py` communicate the project shape directly. HomeMaster needs the same clarity, but with subpackages appropriate for a larger embodied runtime.

There are two groups mixed together:

### 1. Compatibility shim modules

Examples:

```text
executor.py
frontdoor.py
stage_04.py
stage_05.py
stage_06.py
skill_selector.py
verifier.py
orchestrator.py
summary.py
recovery.py
pipeline_core.py
pipeline_stages.py
stage_runtime.py
doctor.py
interactive_shell.py
```

These mostly re-export from new subpackages. They are useful for old imports, and their file docstrings already identify them as backward-compatibility shims. However, they still occupy the top-level namespace, so the package layout still looks partially migrated until a reader opens individual files.

### 2. Real implementation modules still kept flat

Examples:

```text
memory_rag.py
memory_index.py
memory_tokenizer.py
memory_profile.py
memory_commit.py
runtime_memory_store.py
scenario_catalog.py
scenario_runner.py
scenario_validator.py
runtime.py
token_budget.py
llm_client.py
embedding_client.py
grounding.py
world_overlay.py
skill_registry.py
contracts.py
```

These are active implementation files, not shims.

## Impact

* New contributors can identify shim files after opening them, but the package layout alone still requires file-by-file inspection.
* Stage-related logic is split between `stages/` and root-level modules such as `memory_rag.py`, `grounding.py`, and `skill_registry.py`.
* The root package still has high visual noise.
* Compatibility shims have no explicit lifecycle or removal plan.
* P8 can feel incomplete even though major CLI and stage movement was done.

## Recommendation

Create a P8.1 or P9 cleanup phase focused on domain package organization and shim lifecycle.

Suggested direction:

```text
homemaster/
  agent/
    runtime.py
    state.py
    decision.py
    context_builder.py
    result.py

  tools/
    spec.py
    registry.py
    dispatcher.py
    state_updater.py
    results.py
    builtin.py
    simulated.py
    skill_tools.py

  skills/
    spec.py
    loader.py
    registry.py
    builtin/
      fetch_object/SKILL.md
      check_object_state/SKILL.md

  events/
    runtime_events.py
    sinks.py
    sanitizer.py

  providers/
    llm_client.py
    embedding_client.py
    mimo_decision_client.py

  config/
    runtime_settings.py
    runtime_paths.py
    token_budget.py
    recovery.py

  core/
    contracts.py
    execution_state.py
    planning_context.py
    task_record.py
    failure_log.py
    orchestration_validator.py

  memory/
    rag.py
    index.py
    tokenizer.py
    profile.py
    commit.py
    runtime_store.py
    fact_memory.py
    context_snapshot.py

  scenarios/
    catalog.py
    runner.py
    validator.py
    world_overlay.py
    failure_rule_provider.py

  observability/
    logger.py
    trace.py

  cli/
  pipeline/
  stages/
```

Root-level files should be restricted:

```text
allowed at homemaster/ root:
  __init__.py
  task_runner.py as public run facade
  documented compatibility shims

not allowed as new active implementation:
  runtime_events.py
  simulated_skills.py
  new provider clients
  new tool registries
  new settings modules
```

Compatibility shims can remain temporarily, but they should have a documented lifecycle beyond the existing per-file docstrings.

## Acceptance Criteria

* Root-level implementation modules are reduced to a small public API surface.
* New AgentRuntime work creates active modules under `agent/`, `tools/`, `events/`, `providers/`, `config/`, `memory/`, `scenarios/`, or `core/`, not directly under the root package.
* Compatibility shims are clearly identifiable from package-level documentation, not only from each shim file's docstring.
* New internal imports use domain package paths.
* Old import paths continue to work during a compatibility window.
* Tests cover representative old import paths and new import paths.
* Shim removal or deprecation plan is documented.
* Scenario baseline status remains unchanged.

---

# 9. Issue: CLI app mixes entrypoint, runtime policy, and debug commands

## Context

`src/homemaster/cli/app.py` is the Typer CLI entrypoint. It defines commands such as:

* `run`
* `doctor`
* `contract-smoke`
* `understand`

It also wires `run` to `run_homemaster_task()`.

## Problem 1: `run` command description is stale

The `run` command docstring says:

```text
Run one HomeMaster task through Stage02-Stage06.
```

But the surrounding logic describes it as a Stage07 run:

```text
--scenario is required for Stage07 runs
Stage07 only supports --mock-skills
```

## Impact

* Contributors cannot immediately tell whether `homemaster run` is Stage02–06 pipeline execution or Stage07 scenario execution.
* Help text under-describes current behavior.
* Stage boundary becomes harder to understand.

## Problem 2: CLI knows too much about Stage07 policy

`app.py` directly enforces:

* `--scenario` is required
* `--mock-skills` must be enabled

These are Stage07/runtime constraints, not pure CLI concerns.

## Impact

* Stage07 policy is split between CLI and task runner.
* Future real VLN/VLA/VLM support may require changes in both places.
* CLI becomes less stable as a front door.

## Problem 3: Error handling collapses expected runtime errors and unknown crashes

Current code catches:

```python
except (HomeMasterRunError, Exception) as exc:
```

Since `Exception` already includes `HomeMasterRunError`, this is redundant. More importantly, expected runtime failures and unexpected programming errors are handled the same way.

## Impact

* It is harder to distinguish user/runtime errors from code crashes.
* CLI error output is less precise.
* Debugging live runs becomes harder.

## Problem 4: Production and debug commands are mixed in one flat command group

The top-level CLI currently mixes normal user-facing commands with diagnostic commands:

```text
run
doctor
understand
contract-smoke
```

This is manageable now but may become crowded as more stage/debug tools are added.

## Recommendation

Keep `app.py` as a thin command entrypoint.

Suggested direction:

```text
homemaster run
homemaster doctor
homemaster stage understand
homemaster smoke contract
```

Move runtime-specific policy into task runner or runtime resolver.

Separate error handling:

```python
except HomeMasterRunError as exc:
    # expected runtime/business failure

except Exception as exc:
    # unexpected internal crash
```

## Acceptance Criteria

* `run` docstring accurately describes current behavior.
* Stage07-specific policy is not duplicated in CLI and task runner.
* Expected runtime errors and unexpected exceptions are handled separately.
* Debug/smoke/stage commands are grouped or clearly documented.
* `app.py` remains a thin command registration layer.

---

# 10. Issue: `live_models=True` does not mean live step decision execution

## Context

HomeMaster has started to label runtime boundaries more honestly.

For Stage05, the current boundary is:

```text
planning = live_llm when live_models=True
step_decision = test_double
step_decision_smoke = live_llm when live_models=True
skills = mock_skill
verification = mock_symbolic
```

This is useful because it admits that the actual Stage05 action-selection path is still not fully live.

The codebase already contains:

* `LiveStepDecisionProvider`
* `live_step_decision_smoke()`
* `StaticScenarioDecisionProvider`

## Problem

When `live_models=True`, Stage05 uses the live LLM for orchestration planning and for a first-step smoke check, but the real execution loop still uses `StaticScenarioDecisionProvider`.

This means:

1. Live Stage05 planning is real.
2. Live Stage05 step-decision smoke is real.
3. Actual step-by-step execution decision is still deterministic.
4. Recovery rounds also rebuild a fresh `StaticScenarioDecisionProvider`.

So the phrase "live Stage07 scenario" is accurate for some brain stages, but not for the actual Stage05 decision loop.

## Evidence

`RuntimeMode` explicitly marks the actual decision provider as test-double:

```python
step_decision="test_double"
step_decision_smoke="live_llm" if live_models else "n/a"
```

`Stage05Adapter` runs `live_step_decision_smoke()`, then separately builds:

```python
decision_provider = StaticScenarioDecisionProvider(...)
```

`run_stage05_with_recovery()` also rebuilds a fresh static decision provider on each recovery round.

## Impact

* Live baseline results may look more agentic than they really are.
* The system cannot yet validate whether Mimo can choose the right skill at every execution step.
* Recovery behavior is still constrained by deterministic scenario logic.
* Future readers may misunderstand `--live-models` as meaning all brain decisions are live.
* This blurs the boundary between smoke testing and runtime execution.

## Recommendation

Do not solve this only by adding a better Stage05 decision-provider flag. That would make the current Stage Pipeline more honest, but it would still hide action selection inside Stage05.

Target direction:

```text
AgentRuntime turn
  -> ContextBuilder builds compact state context
  -> Mimo returns structured tool_call or finish
  -> Runtime validates tool/schema/permission
  -> Dispatcher executes tool
  -> StateUpdater writes result or failure into AgentState
```

If the Stage05 pipeline compatibility path remains temporarily, it can use `LiveStepDecisionProvider`, but only as a transitional compatibility path. Production status/debug output must say:

```text
runtime_entrypoint = AgentRuntime.run or pipeline_compat
target_runtime = AgentRuntime tool loop
stage05_compatibility_path = true/false
```

`live_step_decision_smoke()` should not be part of `run_homemaster_task()` or `AgentRuntime.run()`. If kept, it belongs in dev/test smoke tooling only.

## Acceptance Criteria

* Main action selection happens through AgentRuntime structured Mimo decisions.
* Stage05 compatibility path, if retained, reports `compatibility_stage05_path=true` and cannot use `StaticScenarioDecisionProvider`.
* `live_step_decision_smoke()` is removed from production run paths or moved to dev/test smoke tooling.
* Tool failures are recorded in `AgentState.failures`; the next Mimo turn decides recovery behavior.
* Live baseline reports distinguish AgentRuntime tool-loop execution from pipeline compatibility execution.

---

# 11. Issue: Runtime and test artifacts are written into tracked fixture directories

## Context

HomeMaster writes useful debug assets for Stage02, Stage03, Stage06, and Stage07:

```text
input.json
expected.json
actual.json
result.md
llm_samples.jsonl
trace/*.jsonl
```

These assets are valuable for inspection and baseline review.

However, several documented commands and scripts still use:

```text
--debug-root tests/homemaster/llm_cases
```

The repository also contains committed files under `tests/homemaster/llm_cases`.

## Problem

Normal test or scenario execution can mutate files that look like test fixtures or baselines.

For example, even deterministic or mocked provider calls can update elapsed time fields:

```text
provider.attempts[].elapsed_ms
provider.elapsed_ms
```

This causes harmless but noisy git diffs.

The underlying issue is that the project mixes two different concepts:

```text
fixture / baseline assets
runtime debug output
```

## Impact

* Running tests can leave the working tree dirty.
* Review diffs can contain timing noise instead of meaningful behavioral changes.
* Contributors may accidentally commit generated debug output.
* Live runs and fixture refreshes are too easy to confuse.
* Baseline management becomes less trustworthy.

## Recommendation

Separate generated runtime artifacts from committed fixture assets.

Suggested direction:

```text
tests/homemaster/llm_cases/
  committed fixtures and snapshots only

var/homemaster/debug/
  normal run debug output

var/homemaster/runs/
  runtime memory and task records
```

Fixture refresh should be an explicit command, not the default behavior of normal tests or README examples.

Also consider sanitizing volatile fields before writing committed snapshots:

```text
elapsed_ms
timestamps
attempt duration
provider latency
run-specific paths
```

## Acceptance Criteria

* README examples no longer write normal debug output into tracked fixture directories.
* Scenario scripts default to `var/homemaster/debug`.
* Tests that write artifacts use `tmp_path` or ignored runtime directories by default.
* Fixture refresh is explicit and documented.
* Volatile timing fields are excluded from committed baseline snapshots or normalized.

---

# 12. Issue: Runtime configuration is loaded at import time and becomes process-global

## Context

HomeMaster has a growing amount of runtime configuration:

* provider defaults
* runtime paths
* provider client timeouts
* retrieval scoring weights
* executor step limits
* recovery max attempts

Some of these values are loaded into module-level constants at import time.

Examples:

```text
runtime.py
  DEFAULT_PROVIDER_NAME
  DEFAULT_LIVE_MODELS
  DEFAULT_STAGE_07_RUNTIME_ROOT

memory_rag.py
  METADATA_WEIGHT_*
  RRF_K
  TOP_K_LIMIT

executor.py
  STEP_MULTIPLIER
  MINIMUM_MAX_STEPS

recovery_config.py
  MAX_RECOVERY_ATTEMPTS
```

## Problem

Import-time configuration is convenient for a small script, but it becomes brittle for a long-running runtime.

Mini-Agent is a useful partial reference: it groups model, agent, tool, and MCP settings into a `Config` object. HomeMaster should borrow the explicit object boundary, but push it further by making settings run-scoped rather than process-global.

The system cannot easily:

* run two tasks with different config in the same Python process
* reload config safely
* pass a run-scoped config object through the runtime
* isolate config changes in tests
* explain exactly which config was used for a run

This matters more once HomeMaster becomes an `AgentRuntime`, because the runtime will likely stay alive across tasks.

## Impact

* Config behavior depends on import order.
* Tests need subprocesses or monkeypatching to avoid global-state contamination.
* Runtime settings are harder to include in replay/debug artifacts.
* Long-running processes cannot update config cleanly.
* Future pluggable memory / skill / provider systems will have unclear config ownership.

## Recommendation

Introduce a run-scoped settings object.

Example:

```python
@dataclass(frozen=True)
class RuntimeSettings:
    provider_defaults: ProviderDefaults
    runtime_paths: RuntimePaths
    provider_client: ProviderClientSettings
    retrieval_scoring: RetrievalScoringSettings
    executor: ExecutorSettings
    recovery: RecoverySettings
    agent_runtime: AgentRuntimeSettings
    tool_registry: ToolRegistrySettings
    event_trace: EventTraceSettings
```

The task runner or future `AgentRuntime` should construct this once per run and pass it down.

Module-level constants can remain only as hardcoded defaults, not as loaded config.

## Acceptance Criteria

* Loaded config is represented by a single run-scoped settings object.
* Stage functions receive config from runtime settings rather than import-time globals.
* Tests can run multiple configs in one process without subprocess reload hacks.
* Debug artifacts record the effective runtime settings used by the run.
* Importing modules does not read user config files except through explicit loader calls.

---

# 13. Issue: Ruff/static quality gate is configured but currently failing

## Context

`pyproject.toml` enables Ruff checks:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

This is a good baseline for catching syntax-adjacent issues, import hygiene problems, stale code, and common bug patterns.

## Problem

The configured Ruff check currently fails across scripts, source files, and tests.

Observed categories include:

```text
E501 line-too-long
F401 unused-import
F541 f-string-missing-placeholders
I001 unsorted-imports
E402 module-import-not-at-top-of-file
E741 ambiguous-variable-name
F841 unused-variable
B017 assert-raises-exception
F811 redefined-while-unused
F821 undefined-name
```

Some findings are cosmetic, but several are meaningful quality signals.

Examples:

* a test uses `pytest.MonkeyPatch` without importing `pytest`
* one test function is defined twice
* source code contains unused variables
* scripts contain many stale f-strings and line-length violations

## Impact

* Static checks cannot be used as a reliable gate.
* Real issues are hidden among expected style noise.
* Future refactors will produce large cleanup diffs unrelated to behavior.
* CI cannot enforce the project’s own configured quality bar.
* New contributors do not know whether Ruff failures matter.

## Recommendation

Decide and document the static quality policy.

Either:

```text
Option A:
  Fix the Ruff errors and make `ruff check .` a required quality gate.
```

or:

```text
Option B:
  Narrow the Ruff scope with explicit per-file ignores for scripts / legacy tests,
  then make the remaining check a required quality gate.
```

The important point is that configured checks should not remain permanently red without explanation.

## Acceptance Criteria

* `ruff check .` passes, or documented per-file ignores explain every intentional exception.
* CI or local verification instructions include Ruff.
* Test files no longer contain duplicate test definitions or undefined names.
* Script lint exceptions are explicit rather than accidental.
* New code is expected to keep Ruff green.

---

# 14. Issue: `run_id` is not path-safe before being used in output paths

## Context

The CLI accepts a user-provided run id:

```text
--run-id
```

The task runner then uses it to construct runtime and debug paths:

```python
runtime_memory_dir = Path(runtime_memory_root) / run_id / "memory"
case_dir = Path(debug_root) / "stage_07" / run_id
```

The same value is also used in trace filenames:

```python
trace / f"{run_id}.jsonl"
```

## Problem

`run_id` is not validated as a filesystem-safe slug.

This means values with path separators, `..`, or absolute path syntax could escape the intended output root or create confusing nested paths.

Even if the CLI is currently local-only, this is still a runtime hygiene problem because `run_id` becomes both:

```text
identity
filesystem path component
trace file name
```

## Impact

* Debug output can be written outside the intended debug root.
* Runtime memory can be written outside the intended runtime root.
* Trace paths can become invalid or ambiguous.
* Future API/server entrypoints would inherit an avoidable path traversal risk.
* Replay and artifact lookup become unreliable if run ids are not normalized.

## Recommendation

Validate and normalize run ids at the runtime boundary.

Suggested rule:

```text
^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$
```

Reject:

```text
absolute paths
path separators
..
empty strings
control characters
overly long ids
```

Generated run ids should use the same slug rule.

## Acceptance Criteria

* User-provided `run_id` is validated before any file materialization.
* Invalid `run_id` values fail with a clear `HomeMasterRunError`.
* Generated run ids are path-safe.
* Trace filenames and runtime/debug directories are derived only from validated run ids.
* Tests cover path traversal attempts and accepted slug examples.

---

# 15. Issue: Deterministic mode must be removed from production runtime

## Context

The target is not merely to label deterministic behavior more honestly.

The target is to clean out `deterministic` as a runtime mode from the production path.

In other words:

```text
deterministic / test_double may exist as test fixtures or test doubles
but must not remain selectable as a normal HomeMaster runtime mode.
```

One important exception is allowed for now:

```text
mock navigation / operation / verification skills are temporarily acceptable
because real VLA / VLN / VLM executors are not integrated yet.
```

That exception is specifically about the robot skill layer. It is not permission to keep deterministic brain, deterministic memory query, deterministic embedding, deterministic planning, or static step decision as production runtime modes.

## Validated Findings

### Finding 1: `stage_runtime.py` is the deterministic residual core

`src/homemaster/pipeline/stage_runtime.py` explicitly says it contains deterministic providers.

It also defines production-package deterministic/test-double components:

```text
StaticMemoryQueryProvider
KeywordEmbeddingProvider
StaticScenarioDecisionProvider
deterministic_task_card()
deterministic_query()
deterministic_plan()
dummy_provider()
```

These are not only comments. They are used when `live_models=False`.

`RuntimeMode.from_flags()` also maps non-live brain stages to:

```text
test_double
```

and hard-codes:

```python
step_decision="test_double"
```

This confirms that `stage_runtime.py` is currently the main deterministic mode hub.

Recommended direction:

* remove production support for `live_models=False` brain execution
* remove `test_double` as a normal `RuntimeMode` value for production runs
* move deterministic providers and deterministic builders to `tests/homemaster/test_doubles/`
* keep `src/homemaster` focused on live providers, protocols, runtime wiring, and explicit not-integrated boundaries

### Finding 2: Stage05 actual step decision is still static

The earlier finding remains fully valid.

Current behavior:

```text
live orchestration planning: real Mimo
first-step smoke decision: real Mimo
actual execution decision loop: StaticScenarioDecisionProvider
recovery execution decision loop: fresh StaticScenarioDecisionProvider
```

The code already contains `LiveStepDecisionProvider`, but the normal Stage05 adapter does not wire it into actual execution.

`Stage05Adapter` runs:

```python
live_step_decision_smoke(...)
```

then builds:

```python
decision_provider = StaticScenarioDecisionProvider(...)
```

`run_stage05_with_recovery()` then overwrites the passed provider in live mode by calling:

```python
decision_provider = _fresh_decision_provider(ctx)
```

and `_fresh_decision_provider()` always returns `StaticScenarioDecisionProvider`.

Recommended direction:

* delete or rename `live_step_decision_smoke()` so it cannot be mistaken for runtime execution
* move actual action selection into AgentRuntime Mimo tool decisions
* if Stage05 compatibility path temporarily remains, use `LiveStepDecisionProvider` there only as a transitional provider
* make recovery loop preserve the configured decision provider policy instead of silently replacing it
* report whether the run used `AgentRuntime.run()` or `pipeline_compat`
* treat this as P0 cleanup, not a later AgentRuntime enhancement; Phase 1 is not complete while Stage05 actual execution can still use static providers

### Finding 3: `executor.py` still contains a deterministic test provider

`src/homemaster/stages/executor.py` still defines:

```python
class StaticStepDecisionProvider:
    """Small deterministic provider for tests."""
```

This is explicitly test-only logic living inside the production package.

Recommended direction:

* move `StaticStepDecisionProvider` to `tests/homemaster/test_doubles/decision_provider.py`
* keep only the `StepDecisionProvider` Protocol in production code
* update tests to import deterministic decision providers from the test-double package
* ensure root shims such as `src/homemaster/executor.py` do not re-export static providers

### Finding 4: `SkillRegistry` default executors are mock, but this is currently an allowed simulation boundary

`src/homemaster/skill_registry.py` defaults to mock navigation and operation executors:

```text
_run_mock_navigation
_run_mock_operation
build_default_skill_registry()
```

The registry docstring also says the default registry registers navigation, operation, and verification with mock handlers.

This is real and important, but it should be classified differently from deterministic brain fallback.

Because real VLA / VLN / VLM executors are not integrated yet, mock skills are currently allowed. The issue is not that mock skills exist. The issue is that they are exposed as the default Stage05 execution path without a strongly named simulation boundary.

Recommended direction:

* keep schema/manifest registration in production code
* make executor registration explicit as `simulated_skill` / `simulated_verification`
* avoid presenting AgentRuntime or Stage05 compatibility execution as real robot execution while simulated executors are active
* when real executors are requested but unavailable, fail fast with a clear not-integrated error
* later replace the simulation layer with real navigation, operation, and verification executors

### Finding 5: `RuntimeMode` still allows test-double and mock modes in the production runtime model

`ComponentMode` currently includes:

```text
test_double
mock_skill
mock_symbolic
not_integrated
```

This is honest about the current behavior, but it also means the runtime model still treats deterministic/test-double execution and vague mock execution as first-class production-selectable modes.

Recommended direction:

* remove `test_double` from production runtime modes
* replace vague `mock_skill` / `mock_symbolic` production labels with explicit `simulated_skill` / `simulated_verification`
* prefer names that separate real, simulated, programmatic, and not-integrated components:

```text
live_llm
live_embedding
programmatic
simulated_skill
simulated_verification
not_integrated
```

If deterministic providers are still needed for tests, they should live under test doubles and should not be reachable through normal CLI/runtime flags.

### Finding 6: CLI and task runner still expose or depend on mock/test-double flags

`homemaster run` currently exposes:

```text
--live-models / --no-live-models
--mock-skills / --no-mock-skills
```

Defaults also still come from:

```text
DEFAULT_LIVE_MODELS=False
DEFAULT_MOCK_SKILLS=True
```

`task_runner.py` rejects `mock_skills=False`:

```text
mock_skills=False is not supported: robot/VLA/VLM not integrated.
```

This means the current CLI surface still exposes deterministic mode through `--no-live-models`, while the robot layer can only run in mock/simulated mode.

Recommended direction:

* remove `--no-live-models` from the production CLI
* remove `DEFAULT_LIVE_MODELS=False` as a production default
* keep robot tools only as an explicit `skill_mode="simulated"` option until real VLA/VLN/VLM integration exists
* rename user-facing wording from "real scenario" to "AgentRuntime with live Mimo decisions + simulated robot tools"
* make unsupported real robot mode fail fast with a clear not-integrated message

### Finding 7: README, scenario scripts, and debug artifacts still carry mock/deterministic traces

README examples and `scripts/run_homemaster_scenarios.sh` still run with:

```text
--mock-skills
```

README already admits that navigation / operation / verification are mock skills. The misleading part is mainly wording around "real scenario" or live runs, because users may read that as a fully live Stage05 agent.

Committed and generated artifacts under paths such as:

```text
tests/homemaster/llm_cases/
var/homemaster/debug/
```

also contain:

```text
test_double
mock_skill
mock_symbolic
deterministic
```

Recommended direction:

* update README wording to "live brain + simulated robot skills"
* make scripts write normal debug output to ignored runtime/debug paths by default
* keep deterministic fixtures only as explicit legacy/test baselines
* mark old deterministic artifacts as deprecated if they remain useful for comparison

## Impact

* `live_models=True` can still be misread as fully live agent execution.
* Stage05 action selection is less agentic than the live-run label suggests.
* Production code still contains deterministic provider implementations that should only exist as tests or legacy baselines.
* `--no-live-models` keeps deterministic mode available as a normal user-facing path.
* Mock skill behavior is currently necessary, but its boundary is not named strongly enough.
* CLI, README, and artifacts can overstate how live the current system is.

## Recommendation

Use a stricter boundary:

```text
production runtime:
  live LLM / live embedding / programmatic only
  no deterministic mode
  no test_double mode

test runtime:
  deterministic providers under tests/homemaster/test_doubles

robot execution layer:
  simulated_skill allowed until real VLA/VLN/VLM executors exist
  real_skill fails fast while not integrated
```

Do not treat mock skills as the same category as deterministic brain fallback.

Mock skills are a temporary robot-layer simulation because VLA/VLN/VLM are not ready.

Deterministic brain providers are test doubles. They should not be selectable through production runtime flags.

## Acceptance Criteria

* `deterministic` is no longer a production runtime mode.
* `--no-live-models` is removed from production CLI or moved behind an explicit test/dev entrypoint.
* `RuntimeMode` no longer exposes `test_double` as a normal production mode.
* `src/homemaster` no longer exposes deterministic brain providers as normal runtime choices.
* `StaticScenarioDecisionProvider` and `StaticStepDecisionProvider` are moved to test doubles or otherwise dev-scoped.
* root compatibility shims use explicit exports and do not re-export static/test-double providers.
* `src/homemaster` does not import `tests` or `tests/homemaster/test_doubles`.
* The main runtime does not hide action selection inside Stage05; Mimo tool selection happens in `AgentRuntime` every turn.
* If a Stage05 pipeline compatibility path remains temporarily, it may use `LiveStepDecisionProvider`, but it must be marked transitional, explicit-only, and must not be the default production entrypoint.
* Tool failures are written into `AgentState.failures`; the next Mimo turn decides retry / observe / retrieve_memory / replan-like behavior / finish failed.
* Mock skills are explicitly labeled as simulated robot skills and remain allowed only because real VLA/VLN/VLM executors are not integrated.
* CLI and README distinguish "live brain" from "simulated robot execution".
* `tests/homemaster/test_stage_07_scenarios_live.py -m "not live_api"` runs the broad offline Stage07 scenario matrix, not only a single smoke case.
* Legacy deterministic fixtures are clearly marked as test baselines or deprecated artifacts.
* Every implementation phase must close with a fresh independent review/test subagent before the next phase starts; blocker findings must be fixed and re-reviewed.

---

# 16. Suggested Priority Order

The issues above do not need to be solved all at once.

Recommended priority:

```text
P0: Remove deterministic/test_double and all production static Stage05/executor/recovery providers
P1: Add target package skeleton, RuntimeSettings, MimoDecisionClient, decision contract, ToolSpec/ToolResult boundaries, and lightweight SkillSpec/SkillLoader contracts
P2: Add AgentRuntime / AgentState / layered ContextBuilder MVP loop with fake Mimo decisions and Mini-Agent-style skill loading
P3: Move actual action selection out of Stage05 compatibility into AgentRuntime Mimo tool decisions
P4: Add runtime event trace / observability for every decision, tool call, result, and state transition
P5: Stop normal runs/tests from mutating tracked fixture assets
P6: Keep robot tools as explicit simulated_skill / simulated_verification until VLA/VLN/VLM exist
P7: Keep PipelineRunner only as a compatibility layer for legacy Stage02-06 execution
P8: Add first embodied skills as progressive-disclosure strategy packages, not executors
P9: Validate path-sensitive run ids
P10: Clean up package structure and shim lifecycle
P11: Clean up CLI command boundaries
P12: Make Ruff/static quality checks green or explicitly scoped
```

Phase gate rule:

```text
Every P0-P12 implementation phase must end with:
1. the implementer running that phase's test plan
2. a fresh subagent reviewing code, tests, artifacts, and git diff against this issue document and the implementation plan
3. explicit PASS / BLOCKED / PASS_WITH_FOLLOWUPS
4. blocker fixes plus a second fresh review before moving on
```

Reasoning:

* Deterministic/test_double and production static Stage05/executor/recovery providers must be removed first because otherwise Phase 1 can pass while production still runs static decisions.
* Target package skeleton, run-scoped RuntimeSettings, and lightweight SkillSpec boundaries must come before AgentRuntime MVP so the new runtime does not inherit root-level module sprawl, import-time config, or a second skill execution engine.
* `MimoDecisionClient` must be defined before AgentRuntime MVP so offline tests can use fake decisions while production has a live-only decision boundary with no deterministic fallback.
* Stage05 static action selection should be removed in P0; later work should move action selection to AgentRuntime Mimo tool decisions, not hide `LiveStepDecisionProvider` inside Stage05.
* Mock skills are temporarily acceptable, but the simulation boundary must be explicit before real VLA/VLN/VLM integration starts.
* Observability should cover agent turns, decisions, tool calls, results, and state transitions because every later change becomes easier to debug.
* Artifact hygiene should happen before more baselines are generated, otherwise fixture noise will keep accumulating.
* Run-scoped config is needed before HomeMaster becomes a long-running runtime.
* PipelineRunner extraction can still reduce compatibility code complexity, but it must remain explicit-only and must not become the future main runtime or default `run_homemaster_task()` path.
* ToolSpec, SkillSpec, and ContextBuilder prepare for real VLN/VLA/VLM integration without making skills a hidden workflow engine.
* Run id validation is small but important because it protects every filesystem artifact path.
* Mini-Agent's small-loop/tool-contract/progressive-disclosure shape is a useful reference for P1/P2/P8, but HomeMaster must add embodied state, structured tool results, simulation labels, controlled memory/profile updates, and redacted event trace.
* Package cleanup should happen after runtime boundaries become clearer, otherwise files may be moved before the architecture is stable.
* Ruff cleanup can be done incrementally, but the project should not leave configured checks permanently red.

---

# 17. Final Direction

HomeMaster should gradually evolve from:

```text
CLI
  ↓
task_runner.py
  ↓
Stage Pipeline
  ↓
Stage02 → Stage03 → Stage04 → Stage05 → Stage06
```

to:

```text
CLI
  ↓
TaskRunner
  ↓
AgentRuntime
  ↓
AgentState-driven tool loop
    while not terminal:
      read AgentState
      build compact context
      Mimo chooses tool_call or finish
      runtime validates schema / permission / availability
      dispatcher executes tool
      state updater records action / observation / verification / failure
      event sink writes trace
  ↓
summary and memory commit
```

The current Stage Pipeline can remain as a transitional implementation, but the long-term goal should be a real Agent Harness:

```text
single runtime loop
system-level unified state
schema-driven tools
centralized context management
structured event trace
pluggable memory / skill / context modules
phase_label as trace label, not forced control flow
no ask_user behavior in the current phase
compact tool manifests and compact context, not full prompt/history dumps
structured ToolResult and StateUpdater, not natural-language state guessing
package layout where agent/tools/events/providers/config/memory/scenarios/core boundaries are visible from directories
```
