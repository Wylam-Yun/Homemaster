# ALFWorld Benchmark Harness Design

## Goal

Build an ALFWorld benchmark path that evaluates HomeMaster as a harnessed agent,
not just Mimo as a direct ALFWorld command generator.

The first implementation targets `AlfredTWEnv` so the benchmark can run before
the visual THOR environment is ready. The design keeps a stable visual extension
point so `AlfredThorEnv` can be added later without changing the HomeMaster agent
loop or benchmark result schema.

## Scope

In scope for the first version:

- Run ALFWorld TextWorld episodes through HomeMaster's existing
  `GenericAgentRuntime`.
- Replace simulated home robot executors with ALFWorld-backed executors for the
  benchmark run.
- Evaluate HomeMaster through tool calls such as `robot_observe`,
  `robot_navigate`, `robot_manipulate`, and `robot_verify`.
- Disable memory tools by configuration for the first benchmark condition.
- Record per-step traces and per-run summaries.
- Keep `frame_path` and visual metadata fields in the environment state schema,
  with `null` values under `AlfredTWEnv`.

Out of scope for the first version:

- Full `AlfredThorEnv` execution.
- Direct command generation baseline.
- Memory ablations.
- Training or fine-tuning.
- Hardcoded benchmark-specific shortcuts that bypass HomeMaster tools.

## Design Principles

- HomeMaster owns the agent loop. The benchmark runner must not implement a
  second model-calling loop that bypasses `GenericAgentRuntime`.
- ALFWorld is an execution backend. It is injected through benchmark-specific
  tool executors and `run_context.deps`, not through changes to generic runtime
  state.
- Command translation is isolated. TextWorld and THOR command differences must
  live behind a translator interface, not inside prompts or generic tool code.
- The benchmark is configuration-driven. Environment type, split, episode count,
  max invalid actions, memory mode, trace root, and debug options must be
  configurable.
- First-version memory is disabled by omission: memory tools are not registered
  in the benchmark tool registry when `memory_mode=disabled`.
- Failed actions are not repaired by the harness. The tool result reports the
  failure and current environment feedback; the model must recover through the
  HomeMaster loop.

## Architecture

```text
ALFWorldBenchmarkRunner
  - selects episodes and aggregates metrics
  - creates AlfworldEnvAdapter
  - creates AgentSession
  - builds ALFWorld-backed ToolRegistry
  - calls GenericAgentRuntime.run(...)

GenericAgentRuntime
  - owns model/tool/result iterations
  - receives ALFWorld-backed tool specs
  - appends ToolResult messages to session context

ALFWorld-backed tools
  - convert HomeMaster tool calls into ALFWorld commands
  - call AlfworldEnvAdapter.step(command)
  - return ToolResult with observation, feedback, won/done, and metrics

AlfworldEnvAdapter
  - wraps AlfredTWEnv in v1
  - exposes reset/current_state/step
  - records environment trace

AlfworldCommandTranslator
  - maps tool arguments to env-specific command strings
  - TextWorld v1: put -> "move <object> to <receptacle>"
  - THOR future: put -> "put <object> in/on <receptacle>"
```

## Components

### `AlfworldEnvAdapter`

The adapter is the only code that directly owns an ALFWorld environment
instance.

Responsibilities:

- Load ALFWorld config.
- Initialize `AlfredTWEnv`.
- Reset an episode and expose the task description and first observation.
- Execute one ALFWorld command with `env.step([command])`.
- Track current observation, feedback, reward, done, won, goal condition success
  rate, invalid action count, and step count.
- Record trace events in a structured form.
- Expose `frame_path=None` under TextWorld.

State shape:

```json
{
  "episode_id": "string",
  "task": "string",
  "observation": "string",
  "inventory": "string | null",
  "last_command": "string | null",
  "last_feedback": "string | null",
  "reward": 0.0,
  "done": false,
  "won": false,
  "goal_condition_success_rate": 0.0,
  "frame_path": null,
  "step_index": 0,
  "invalid_action_count": 0
}
```

The adapter may keep complete `admissible_commands` in trace/debug data, but the
first benchmark mode must not feed them into model-visible context.

### `AlfworldCommandTranslator`

The translator converts HomeMaster tool arguments into concrete ALFWorld
commands. It does not execute commands and does not choose alternatives.

Interface:

```python
class AlfworldCommandTranslator:
    env_type: str

    def public_action_schema(self) -> dict: ...
    def navigate(self, *, target_receptacle: str) -> str: ...
    def observe(self, *, target: str | None = None) -> str: ...
    def manipulate(self, *, action: str, **kwargs: object) -> str: ...
```

First-version TextWorld mappings:

```text
robot_observe({})                         -> look
robot_observe({"target": "apple 1"})      -> examine apple 1
robot_navigate({"target_receptacle": R})  -> go to R
take:  object + source_receptacle         -> take O from R
put:   object + target_receptacle         -> move O to R
open:  target_receptacle                  -> open R
close: target_receptacle                  -> close R
use:   object                             -> use O
heat:  object + tool_receptacle           -> heat O with R
cool:  object + tool_receptacle           -> cool O with R
clean: object + tool_receptacle           -> clean O with R
slice: object + tool_receptacle           -> slice O with R
inventory                                 -> inventory
```

Future THOR mapping changes only the translator, not tool schemas or runner
logic. The known difference is `put`, which becomes
`put <object> in/on <receptacle>`.

### Benchmark Tool Registry

The benchmark must build its own registry instead of reusing the current home
registry as-is, because the current robot executors are simulated.

First-version registry:

```text
task_interpreter      optional, programmatic
robot_observe         ALFWorld-backed
robot_navigate        ALFWorld-backed
robot_manipulate      ALFWorld-backed
robot_verify          ALFWorld-backed
task_summarizer       optional, programmatic
```

Not registered when `memory_mode=disabled`:

```text
memory_retriever
target_grounder
memory_writer
```

This avoids accidental dependence on HomeMaster's local household memory corpus
while preserving a clean future path for memory ablations:

```text
memory_mode=disabled  -> no memory tools
memory_mode=readonly  -> memory_retriever only
memory_mode=full      -> memory_retriever + memory_writer
```

### Tool Schemas

`robot_navigate`:

```json
{
  "target_receptacle": "string"
}
```

`robot_observe`:

```json
{
  "target": "string | null",
  "mode": "look | inventory | examine"
}
```

`robot_manipulate`:

```json
{
  "action": "take | put | open | close | use | heat | cool | clean | slice",
  "object": "string | null",
  "source_receptacle": "string | null",
  "target_receptacle": "string | null",
  "tool_receptacle": "string | null"
}
```

Validation rules:

- `take` requires `object` and `source_receptacle`.
- `put` requires `object` and `target_receptacle`.
- `open` and `close` require `target_receptacle`.
- `use` requires `object`.
- `heat`, `cool`, `clean`, and `slice` require `object` and
  `tool_receptacle`.
- Invalid arguments produce a failed `ToolResult`; they do not call
  `env.step(...)`.

`robot_verify`:

```json
{
  "expected_done": "string | null"
}
```

`robot_verify` reads adapter state. A HomeMaster final reply is not success by
itself. Benchmark success requires `env_state.won == true`.

## Prompt Design

The benchmark system prompt should be generated from configuration and the
translator's `public_action_schema()`. It must not hardcode TextWorld action
strings outside the translator.

Prompt requirements:

- The assistant is controlling a home assistant robot in ALFWorld.
- The assistant must complete the task by calling tools, not by outputting raw
  ALFWorld commands.
- Object and receptacle names must match the current environment language when
  possible, for example `apple 1` or `countertop 1`.
- Failed tools provide feedback and the latest observation. The assistant must
  recover through additional tool calls.
- Memory tools are not available in the first benchmark mode.
- Success is determined by environment `won`, not by a verbal completion claim.

The user/context message for an episode includes:

```text
Task:
<ALFWorld task description>

Current environment:
observation: <current observation>
inventory: <inventory if available>
last_feedback: <feedback if any>
goal_progress: <goal condition success rate>
frame_path: null
```

Tool results are the primary context update mechanism. Each successful or failed
tool call returns the latest observation and environment status. The
`GenericAgentRuntime` appends those tool results to the session so the next model
iteration can react.

## Failure Handling

The harness does not provide `admissible_commands_preview` in the model-visible
tool result, even after failures. This is intentional: the benchmark should test
whether HomeMaster can recover from failed actions using environment feedback
and observation alone.

Failed step result shape:

```json
{
  "success": false,
  "failure_reason": "invalid_action | no_effect | translator_validation_error | env_error",
  "data": {
    "attempted_command": "go to fridge 1",
    "feedback": "Nothing happens.",
    "observation": "You are at countertop 1. You see apple 1.",
    "done": false,
    "won": false,
    "goal_condition_success_rate": 0.0,
    "invalid_action_count": 3
  },
  "summary": "Action failed. Feedback: Nothing happens."
}
```

An episode fails when:

- `max_tool_iterations` is reached before `won`.
- `invalid_action_count >= max_invalid_actions`.
- ALFWorld reports unrecoverable environment error.

The initial default for `max_invalid_actions` is `100`, but it must be a config
value.

## Success Criteria

Per episode success:

```text
env_state.won == true
```

`done == true` without `won == true` is a failed episode. A final assistant reply
without env `won` is a failed episode.

Aggregated metrics:

- success rate
- average goal condition success rate
- average steps
- average model calls/tool iterations
- invalid action count and rate
- timeout count
- environment error count
- average latency and token usage when available

## Trace Requirements

Each episode writes structured JSONL events. Every tool step records:

```json
{
  "episode_id": "string",
  "step_index": 1,
  "tool_name": "robot_navigate",
  "tool_args": {},
  "translated_command": "go to countertop 1",
  "tool_success": true,
  "failure_reason": null,
  "observation": "string",
  "feedback": "string",
  "reward": 0.0,
  "done": false,
  "won": false,
  "goal_condition_success_rate": 0.0,
  "frame_path": null
}
```

Trace data must not include API keys or raw provider credentials. Full
`admissible_commands` may be included only in debug trace fields that are not
fed back to the model.

## CLI and Configuration

The benchmark should be reachable through an explicit command, for example:

```text
python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /path/to/alfworld \
  --alfworld-config configs/base_config.yaml \
  --env-type AlfredTWEnv \
  --split valid_seen \
  --episodes 5 \
  --memory-mode disabled \
  --max-invalid-actions 100 \
  --max-tool-iterations 150 \
  --trace-root /tmp/homemaster/alfworld
```

Exact CLI naming can follow existing Typer conventions, but benchmark settings
must not be hardcoded into implementation modules.

## Visual Compatibility

The first version sets `frame_path=null`. Visual support later should:

- Implement a THOR adapter behind the same `AlfworldEnvAdapter` interface.
- Save current frames to the episode trace directory.
- Set `frame_path` to the saved image path.
- Extend the Mimo transport to send image blocks when `frame_path` is present.
- Use `ThorCommandTranslator` for command differences, especially `put`.

No change should be required in `GenericAgentRuntime` to add visual input.

## Open Questions

- Whether `task_interpreter` and `task_summarizer` should be included in the
  first benchmark registry or omitted for a narrower robot-control-only run.
  The recommended default is to include them as optional HomeMaster harness
  components, while keeping memory disabled.
- Whether debug traces should include full `admissible_commands`. The
  recommended default is yes for offline analysis, but never in model-visible
  tool results for the primary benchmark condition.
