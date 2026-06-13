# HomeMaster V1.5 Context, Task State, and Compaction Spec

Date: 2026-06-13

## Goal

HomeMaster V1.5 should move from "send `session.messages` directly to the model" to a generic context assembly layer that supports:

- request-level system prompt injection;
- model-owned task planning and progress state;
- per-model context budgeting and automatic compaction;
- configurable runtime/provider/context settings;
- clean removal of older context-builder leftovers that are no longer part of the active architecture.

This spec supersedes the earlier task-state-only draft because task-state injection, system prompt delivery, and context compaction must be implemented together. A task snapshot that is injected before each model call is also part of the context budget, so it cannot be designed separately from compaction.

## Current Code Facts

- `src/homemaster/agent/generic_runtime.py` appends the user message to `AgentSession` and calls `LLMTransport.stream(session.messages, tools=...)` on every loop iteration.
- `src/homemaster/providers/transport.py` does not accept `system_prompt`, so `ContextComposer.system_prompt` cannot reach the provider.
- `src/homemaster/providers/mimo_transport.py` builds Anthropic/OpenAI payloads from messages only. It currently does not set Anthropic `system` or OpenAI `role=system`.
- `src/homemaster/agent/context.py` is a baseline max-message slicer; it is tested but not used by `GenericAgentRuntime`.
- `src/homemaster/agent/state.py` and `src/homemaster/agent/context_builder.py` are old runtime-state/context-builder artifacts. They are not the active production path.
- `src/homemaster/benchmarking/alfworld/runner.py` builds `RunContext.deps` with ALFWorld-specific objects, then starts `GenericAgentRuntime`.
- ALFWorld robot tools return normal observations, feedback, and frame paths. They should not own generic task planning or hidden progress inference.
- `src/homemaster/prompts/agent_system_prompt.txt` exists but is not delivered by the generic runtime.

## Non-Goals

- Do not make `GenericAgentRuntime` import ALFWorld or home-domain modules.
- Do not make planner/progress tools infer progress from hidden simulator state, admissible commands, object-location oracles, or goal oracles.
- Do not append task snapshots into `AgentSession` as permanent history.
- Do not keep legacy context paths for compatibility if the new active path replaces them. Remove dead code once tests are migrated.
- Do not build a second parallel configuration system. Extend the existing config/runtime settings into a typed, explicit run configuration.
- Do not implement robot-state context in V1.5. Reserve the provider interface for it.

## Architecture

```text
AgentSession.messages
RunContext
AgentState
TaskStateStore
Tool registry
Runtime config
        |
        v
ContextAssembler
        |
        +-- SystemPromptProvider
        +-- ConversationProvider
        +-- TaskStateSnapshotProvider
        +-- FailureSummaryProvider
        +-- RuntimeBudgetStatusProvider
        +-- MemoryContextProvider
        +-- SkillContextProvider
        |
        v
ContextBundle[ContextItem]
        |
        v
BudgetManager
        |
        +-- under threshold -> render full/selected context
        |
        +-- over threshold -> CompactEngine
              |
              +-- micro compact old tool results / images / observations
              +-- summary compact older conversation groups
              +-- emergency shrink recent window
        |
        v
ComposedContext(system_prompt, messages, tools, metrics)
        |
        v
LLMTransport.stream(...)
```

The model call path must become:

```text
for each agent-loop iteration:
  context = prepare_model_context(session, run_context, agent_state, tools)
  transport.stream(context.messages, tools=context.tools, system_prompt=context.system_prompt)
```

The compaction check runs before every model call, not only once per user turn. Tool results are often the largest context growth source, so the loop must check after tools have appended results and before the next model call.

## Context Item Model

Context assembly should operate on `ContextItem`, not on raw message slices.

Suggested fields:

```python
class ContextItem:
    id: str
    kind: str
    priority: ContextPriority
    freshness: ContextFreshness
    placement: ContextPlacement
    group_id: str | None
    depends_on: list[str]
    token_estimate: int
    render_full() -> str | list[Message]
    render_compact() -> str | list[Message]
    render_summary() -> str | list[Message]
    render_pointer() -> str | list[Message]
```

Priorities:

```text
required   Must remain model-visible for correctness. May use compact view, but cannot be dropped.
important  Relevant to the active task. Can degrade full -> compact -> summary.
auxiliary  Helpful or historical. Can degrade summary -> pointer -> drop.
trace_only Never model-visible. Kept only in trace/debug output.
```

Freshness:

```text
current  current request, current task state, current loop tool pairs
recent   recent turns/tool cycles
old      older conversation, old observations, old tool outputs
archived completed task state or trace-only historical data
```

Placements:

```text
system_prompt     request-level provider system prompt
context_prelude   synthetic user/context message before normal conversation
conversation      normal user/assistant/tool messages
tool_schema       provider tool schema
trace_only        trace/debug only
```

## Context Categories

| Context kind | Source | Priority while active | Completion/old priority | Compaction rule |
| --- | --- | --- | --- | --- |
| `system_prompt` | prompt loader/config | required | required | Never drop. Send as request-level system prompt. Count in budget. |
| `latest_user_request` | `AgentSession` current user message | required | n/a | Never drop. |
| `current_loop_tool_pairs` | `AgentSession` messages from current loop | required | important | Never split assistant tool calls and corresponding tool results. |
| `task_state_snapshot` | `TaskStateStore` | required | important | Active snapshot keeps goal/current_subtask/next_focus/all subtask statuses/recent evidence. Completed snapshot becomes short completion summary then archives. |
| `failure_summary` | `AgentState` + tool results + task-state blocked/uncertain updates | important | auxiliary | Keep active blockers and avoid-repeating hints. Old resolved failures collapse into counts/summaries. |
| `runtime_budget_status` | `AgentState` + budget manager | important | auxiliary | Tiny JSON. Keep current iteration, error/no-progress counts, last compaction, estimated tokens. |
| `recent_agent_steps` | `AgentSession` grouped messages | important | auxiliary when old | Preserve latest complete tool cycles by count and token budget. |
| `recent_user_turns` | `AgentSession` grouped messages | important | auxiliary when old | Preserve latest user turns by count and token budget. |
| `tool_schemas` | tool registry | important | important | Include all for now. Later add tool selector if schemas dominate budget. |
| `memory_context` | memory retriever/store | important/auxiliary | auxiliary | Keep top hits compactly: ids, locations, confidence, reasons. Old retrieval payloads can be summarized. |
| `skill_context` | skill registry | auxiliary/important when loaded | auxiliary | Keep summaries, not full skill bodies. Full skill content should be fetched by tool. |
| `old_tool_results` | older conversation | auxiliary | auxiliary | full -> one-line summary -> placeholder/drop. Keep trace full. |
| `old_images` | older content blocks/tool results | auxiliary | auxiliary | latest image may remain; old images become path/caption/key facts, no base64. |
| `debug/provider_metadata/raw_env_trace` | event sinks/trace files | trace_only | trace_only | Never put in model context. |
| `robot_state` | future robot adapter | reserved | reserved | Not implemented in V1.5. Provider slot is reserved. |

## Agent State Boundary

`AgentState` is internal runtime state. It is not a prompt format and must not be injected wholesale.

The current `src/homemaster/agent/state.py` is too small and tied to old context-builder assumptions. Replace it with a runtime bookkeeping model that supports context providers:

```text
AgentState
  run_id
  session_id
  status
  turn_index
  iteration_index
  total_model_calls
  total_tool_calls
  active_task_snapshot_id
  last_assistant_text
  last_tool_calls
  last_tool_results_summary
  consecutive_tool_errors
  no_progress_iterations
  last_progress_marker
  last_compaction
  estimated_context_tokens
  provider_usage
  metadata
```

Model-visible context is derived from it:

```text
AgentState -> FailureSummaryProvider
AgentState -> RuntimeBudgetStatusProvider
```

Do not preserve the old `ContextBuilder` three-layer output. Once the new `ContextAssembler` is active and tests are migrated, remove:

- `src/homemaster/agent/context_builder.py`;
- tests that only validate the old `ContextBuilder`;
- old state-updater path if it is still unused by the generic runtime;
- any duplicate builtin/skill tool modules that are no longer used by active registries.

## Task State

### Store

Add a generic run-scoped `TaskStateStore`, preferably under `src/homemaster/task_state/`.

Responsibilities:

- store the active model-owned task plan;
- validate snapshot schema and subtask statuses;
- create/replace plans from explicit model tool input;
- apply explicit progress updates from model tool input;
- render bounded model-visible snapshots;
- render full trace/debug snapshots.

Non-responsibilities:

- no LLM calls;
- no hidden-state reads;
- no automatic planning;
- no automatic progress inference;
- no robot/environment actions.

Inject through `RunContext.deps`:

```text
run_context.deps["task_state_store"] = TaskStateStore(...)
```

### Snapshot Schema

Model-visible active snapshot:

```json
{
  "type": "task_state_snapshot",
  "snapshot_id": "task-state-0008",
  "status": "active",
  "goal": "put a hot apple in fridge",
  "current_subtask": "heat_apple",
  "next_focus": "Heat the held apple with the microwave, then go to the fridge.",
  "open_questions": [],
  "constraints": [
    "Only use model-visible observations and tool results."
  ],
  "subtasks": [
    {
      "id": "find_apple",
      "description": "Find an apple.",
      "status": "completed",
      "evidence": ["apple was observed in sinkbasin"]
    },
    {
      "id": "heat_apple",
      "description": "Make the apple hot.",
      "status": "in_progress",
      "evidence": ["holding apple", "microwave located"]
    }
  ],
  "updated_at_iteration": 12
}
```

Allowed task status:

```text
none | active | completed | failed | cancelled
```

Allowed subtask statuses:

```text
pending | in_progress | completed | blocked | cancelled | uncertain
```

Evidence must be model-submitted and model-visible. It can cite observations, feedback, inventory, tool results, user instructions, or previous task snapshot content. It must not cite hidden environment state.

Active snapshot render bound:

- include `status`, `goal`, `current_subtask`, `next_focus`;
- include all active subtasks;
- include `id`, `description`, `status` for every subtask;
- include only the most recent 1-2 evidence strings per subtask;
- include short `open_questions` and `constraints` lists.

Completed snapshot behavior:

- downgrade from `required` to `important`;
- keep a short completion summary for the next 1-2 user turns;
- archive full snapshot to trace/store;
- do not keep completed task detail indefinitely in active model context.

### Tools

`task_planner`:

- model-owned planning tool;
- requires `goal` and non-empty `subtasks`;
- does not auto-generate a plan when only `goal` is supplied;
- validates unique subtask ids;
- defaults omitted status to `pending`;
- stores and returns normalized snapshot.

`task_progress_check`:

- model-owned progress update tool;
- requires an active plan;
- validates every `subtask_id`;
- applies only explicit model-submitted status/evidence updates;
- does not infer progress from latest observation or hidden fields;
- stores and returns normalized snapshot.

Both tools are generic and should live outside ALFWorld-specific packages.

## Failure Summary

`FailureSummaryProvider` reads from `AgentState`, recent tool results, and task-state blocked/uncertain updates. It does not read hidden simulator state.

Model-visible shape:

```json
{
  "type": "failure_summary",
  "active_failures": [
    {
      "subtask_id": "heat_apple",
      "tool": "robot_manipulate",
      "reason": "Nothing happens",
      "attempts": 2,
      "last_evidence": "open microwave failed twice"
    }
  ],
  "recent_recoveries": [
    "navigated from sinkbasin to microwave after first failed heat attempt"
  ],
  "avoid_repeating": [
    "Do not retry the same manipulate command without a changed observation or revised plan."
  ]
}
```

Rules:

- active blockers are `important`;
- no active blockers means the item can be `auxiliary`;
- keep only recent 3-5 unresolved/resolved items;
- old resolved failures collapse into counts/summaries;
- repeated same-tool same-args failures increment `no_progress_iterations`.

## Runtime Budget Status

`RuntimeBudgetStatusProvider` derives a tiny status item from `AgentState` and the budget manager:

```json
{
  "type": "runtime_budget_status",
  "iteration_index": 18,
  "max_tool_iterations": null,
  "consecutive_tool_errors": 1,
  "no_progress_iterations": 3,
  "elapsed_minutes": 7.4,
  "estimated_context_tokens": 142000,
  "last_compaction": "none"
}
```

This item is small enough to keep as `important`.

## Configuration

HomeMaster should have one typed runtime configuration loaded explicitly and passed through `RuntimeSettings`.

Provider/model profile owns model-specific capacities:

```json
{
  "providers": {
    "default": "mimo_v25",
    "items": [
      {
        "name": "mimo_v25",
        "protocol": "anthropic",
        "base_url": "https://example/v1",
        "model": "MiMo-V2.5",
        "api_keys": ["secret"],
        "context_window_tokens": 1000000,
        "max_output_tokens": 8192
      }
    ]
  }
}
```

Context policy owns generic ratios and preserve counts:

```json
{
  "context": {
    "auto_compact_enabled": true,
    "compression_threshold_ratio": 0.50,
    "recent_tail_ratio": 0.20,
    "preserve_recent_agent_steps": 20,
    "preserve_recent_user_turns": 3,
    "token_estimation_padding": 1.333,
    "safety_buffer_tokens": 13000,
    "image_token_estimate": 4096,
    "enabled_providers": [
      "conversation",
      "task_state_snapshot",
      "failure_summary",
      "runtime_budget_status",
      "memory",
      "skills"
    ]
  }
}
```

Runtime safety owns loop guards:

```json
{
  "runtime": {
    "max_tool_iterations": null,
    "max_consecutive_tool_errors": 5,
    "max_no_progress_iterations": 20,
    "max_wall_clock_minutes": null,
    "runtime_root": "/tmp/homemaster/runs",
    "debug_root": "/tmp/homemaster/debug",
    "results_root": "/tmp/homemaster/results"
  }
}
```

Prompt config:

```json
{
  "prompts": {
    "agent_system_prompt": "agent_system_prompt",
    "compact_summary_prompt": "compact_summary_prompt"
  }
}
```

Resolution order:

```text
CLI override
-> provider profile field
-> known model registry
-> built-in default
-> conservative fallback
```

For MiMo-V2.5, set the provider profile `context_window_tokens` to `1000000`. Do not hard-code this into generic context logic. If the user switches provider/model, the resolved profile controls the window.

API keys may live in local config for this project, but example configs must not contain real keys and all trace/debug output must redact them.

## Budget and Compaction Policy

Token estimate:

```text
estimated_input =
  estimate(system_prompt)
+ estimate(rendered_context_items)
+ estimate(tool_schemas)
+ image_budget
+ padding
```

Use provider usage after requests for calibration, but pre-request compaction must rely on estimates.

Defaults inspired by Hermes and OpenHarness:

- Hermes: threshold defaults to 50% of context, `protect_last_n=20`, recent tail budget is 20% of threshold.
- OpenHarness: `AUTOCOMPACT_BUFFER_TOKENS=13000`, `TOKEN_ESTIMATION_PADDING=4/3`, default keep recent is smaller and more conservative.

HomeMaster default:

```text
compression_threshold = context_window_tokens * 0.50
recent_tail_budget = compression_threshold * 0.20
preserve_recent_agent_steps = 20
preserve_recent_user_turns = 3
safety_buffer_tokens = 13000
```

Examples:

```text
1M context   -> threshold 500k, recent tail 100k
200k context -> threshold 100k, recent tail 20k
128k context -> threshold 64k, recent tail 12.8k
```

Compaction stages:

1. **No-op**: below threshold, render according to default full/compact choices.
2. **Micro compact**: no LLM call.
   - old tool results -> one-line summaries;
   - duplicate tool results -> back-reference;
   - old images -> path/caption/placeholders;
   - old observations -> key facts;
   - old task planner/progress results -> placeholder because current snapshot is authoritative.
3. **Summary compact**:
   - summarize older conversation groups;
   - preserve current request, active task snapshot, current loop tool pairs, recent tail;
   - never split tool call/result groups.
4. **Emergency shrink**:
   - shrink recent agent steps from 20 -> 12 -> 8;
   - if still too large, return `context_overflow`.
5. **Reactive compact**:
   - if provider returns context-length errors, force compact once and retry current model call once.

Important: compacted model-visible history may replace `session.messages` after a successful compaction. Full original raw content must be preserved in trace/debug storage. If compaction is only temporary per request, every later iteration will repeatedly compact the same raw history.

## Runtime Loop Guards

Do not use a small hard-coded iteration limit for home/robot long-horizon tasks.

Recommended runtime policy:

```text
max_tool_iterations = null
max_consecutive_tool_errors = 5
max_no_progress_iterations = 20
max_wall_clock_minutes = configurable/null
max_env_steps = domain-specific
user_interrupt = always allowed
```

`null` means no fixed iteration cap. Other guards prevent infinite loops.

If a guard trips:

- write a structured runtime event;
- ask the model for a final recovery/clarification response without tool calls when possible;
- return a clear error code if the model cannot produce a reply.

## Prompts

Prompt files live in `src/homemaster/prompts`.

Required V1.5 prompt files:

- `agent_system_prompt.txt`
- `compact_summary_prompt.txt`

`PromptId` must include `COMPACT_SUMMARY = "compact_summary_prompt"`.

System prompt responsibilities:

- stable role and tool-use policy;
- model-owned planning/progress update rules;
- injected context semantics;
- no hidden-state assumptions;
- failure recovery and no-progress behavior;
- concise user-language final replies.

Compact summary prompt responsibilities:

- summarize only prior model-visible content;
- preserve task goal, current plan, completed/blocked/uncertain subtasks, evidence, failures, constraints, and remaining work;
- state that current `task_state_snapshot` will be injected separately and must not be overwritten by summary;
- do not invent facts or turn unverified claims into completed work;
- output concise structured text.

## Deletion and Cleanup Plan

Remove or replace after the new path is active:

- `src/homemaster/agent/context_builder.py`: replaced by `ContextAssembler`.
- old tests that assert `ContextBuilder` shape as provider context.
- old `ContextComposer(max_messages=...)` behavior if replaced by provider/item-based composition. Keep only if refactored into the new assembler.
- `src/homemaster/agent/state.py` fields that only exist for the old context builder.
- unused `tools/state_updater.py` if no active runtime path uses it.
- duplicate old builtin/skill tool wrappers if domain registries have replaced them.

Do not keep adapter shims just to satisfy old tests. Update tests to the V1.5 architecture.

## Test Plan

Unit tests:

- `LLMTransport.stream` accepts `system_prompt`.
- Mimo Anthropic payload includes request-level `system`.
- Mimo OpenAI payload includes first `role=system` message.
- prompt loader can load `compact_summary_prompt`.
- `TaskStateStore` validates statuses, ids, progress updates, and completed snapshot downgrade.
- `TaskStateSnapshotProvider` injects active snapshot ephemerally and does not append to `AgentSession`.
- `FailureSummaryProvider` aggregates repeated failures and resolved failures.
- `RuntimeBudgetStatusProvider` renders small current status.
- `BudgetManager` calculates thresholds from provider profile context window.
- tool-call/tool-result groups are preserved across compaction boundaries.
- old images/tool results are micro-compacted without losing trace data.

Integration tests:

- every model call in `GenericAgentRuntime` goes through context preparation.
- system prompt appears in live provider payload tests.
- active task snapshot appears before each later model call after `task_planner`.
- `task_progress_check` updates snapshot only from explicit model input.
- completed snapshot is downgraded and eventually archived.
- 20 recent agent steps and token-budget tail are respected.
- context-length provider error triggers one reactive compact/retry.
- `max_tool_iterations=null` permits long loop but no-progress/error guards stop repeated failure loops.

Benchmark smoke:

- ALFWorld runner injects `TaskStateStore` through `RunContext.deps`.
- robot tools remain ALFWorld-specific and do not own generic task state.
- final ALFWorld success remains environment/verify based.

## Implementation Order

1. Add typed config models for provider/context/runtime/prompt settings.
2. Update prompt loader and prompt files.
3. Extend transport interface and Mimo payload builders for system prompt.
4. Introduce `AgentState` runtime bookkeeping and loop guard counters.
5. Add `TaskStateStore`, `task_planner`, and `task_progress_check`.
6. Add `ContextItem`, providers, `ContextAssembler`, `BudgetManager`, and `CompactEngine`.
7. Replace direct `session.messages` transport calls with `prepare_model_context`.
8. Add compaction trace events and provider usage calibration.
9. Remove old context-builder/state-updater leftovers and migrate tests.
