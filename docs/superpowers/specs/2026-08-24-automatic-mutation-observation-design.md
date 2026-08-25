# Automatic Write-Tool Observation Design

## Goal

After a browser write or interaction tool executes, HomeMaster must capture the
current page automatically. Query, navigation, and wait tools do not capture an
automatic observation. MiMo never chooses or schedules the follow-up `observe`.

## Problem

HomeMaster currently sets a model-observation barrier after browser actions and
asks MiMo to call `observe` on the next turn. If MiMo calls `browser_inspect`
first, the runtime rejects that call even though the action succeeded. This makes
a deterministic runtime rule depend on model compliance and produces invalid
tool lifecycles.

## Classification

Classification is static and comes from the browser tool definition, so it works
the same on every page and requires no page-specific markup.

- No automatic observation: `browser_inspect`, `browser_wait`,
  `browser_navigate`, and `observe`.
- Automatic observation: `browser_fill`, `browser_select`, `browser_check`,
  `browser_uncheck`, `browser_click`, and `browser_backfill`.

The existing `requires_model_observation` definition flag remains the source of
truth for write/interaction tools, but its meaning changes from “ask the model to
observe next” to “the runtime automatically captures observation now”. Browser
navigation and wait stop setting this flag.

## Runtime Flow

1. MiMo emits exactly one write/interaction tool call.
2. HomeMaster dispatches the action once and preserves its structured receipt.
3. If the backend was attempted, HomeMaster dispatches the existing read-only
   `observe` implementation internally without another model turn.
4. HomeMaster validates that the observation contains exactly one valid PNG.
5. The PNG and its content/pixel hashes are attached to the original action tool
   result with `automatic_observation` metadata.
6. MiMo receives one action result containing both the action receipt and the
   current page image, then chooses its next business step.

The automatic observation is a runtime sub-operation, not a synthetic model tool
call. The provider transcript therefore retains one result per model-selected
tool call.

## Failure Semantics

- Pre-backend validation failures do not observe.
- Once a write/interaction backend was attempted, observation runs even when the
  action reports failure because a partial side effect may exist.
- Observation capture retries up to three times internally; it never repeats the
  original action.
- If all captures fail, HomeMaster preserves and publishes the original action
  receipt, emits `automatic_observation_failed`, and terminates the run before
  another write/interaction.
- A confirmed action success must not be rewritten as an action failure merely
  because its evidence capture failed.

## Compatibility

The generic tool definition remains page- and environment-independent. Existing
session fields for an old pending model-observation barrier remain readable for
resume compatibility, but newly executed actions no longer create a barrier or
consume a model turn for `observe`.

## Verification

1. Query/navigation/wait tools perform zero automatic observation calls.
2. Each write/interaction tool performs exactly one successful automatic
   observation after one backend attempt.
3. MiMo can call `browser_inspect` immediately after the action result without a
   protocol rejection.
4. Invalid observations retry without repeating the action, then fail the run.
5. Browser tool definitions classify only the six write/interaction tools for
   automatic observation.
6. A fresh Ops Monitor run contains no failed tool calls, changes the fixture
   from `0.9.0` to `1.0.0`, reads the running `1.0.0` asset back over HTTP,
   creates distinct precheck/postcheck WSO evidence IDs, exits with RC 0 and
   empty stderr, and leaves no browser processes.

## Non-Goals

- Page-specific query/mutation annotations.
- Inferring business intent from button text or network traffic.
- Direct API, JavaScript, CDP, coordinate, or alternate-browser shortcuts.
