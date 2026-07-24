# V1.9 Generic Screenshot Observe Implementation Plan

## Decision and Scope

Replace the three environment-specific observation contracts with one ordinary screenshot tool:

```text
observe({}) -> exactly one current-frame PNG image in the model-visible tool result
```

The canonical tool identity is `core.observe.v1`; its model alias remains `observe`. It is enabled in Home,
ALFWorld, and Coworker without changing the relative order or aliases of the remaining public tools. The tool has
an empty-object input schema, no model-visible text or JSON payload, and no action authorization, freshness,
provider-binding, state-machine, or benchmark-specific semantics.

This change does not modify Gateway, skills, streaming, Coworker DOM action tools, ALFWorld navigation/manipulation
tools, or MCP. Existing structured action receipts remain unchanged. Coworker scoring keeps `TICKET_READ`, but it is
grounded only by `browser_navigate(route=ticket)`, never by a screenshot.

## Root Cause

The current implementation conflates two independent concerns:

1. Explicit visual inspection requested by the model.
2. A `NEEDS_OBSERVE -> OBSERVED_UNBOUND -> BOUND_READY` control state used to authorize later actions.

That coupling creates environment-specific `observe` implementations, exposes structured observation metadata to
the model, and lets provider binding/freshness checks reject unrelated business actions. Real Coworker evidence
already shows this boundary failing: the environment and browser ran, but the observation freshness contract
rejected every business action. A generic screenshot does not need, and must not own, action authorization.

## Public Contract

`core.observe.v1` has:

- alias: `observe`
- input: an object with no properties and `additionalProperties: false`
- output: exactly one non-empty valid PNG image
- success model message: exactly one `ContentBlock(type="image")`
- failure: one typed tool error; no stale/placeholder image
- execution proof: none; screenshot acquisition itself is the requested read operation
- capabilities/state effects: none

The internal result may carry content and decoded-pixel SHA-256 values for validation, trace correlation, and
tests. Those fields must not create a model-visible text block.

Lock the projection design as a canonical `ResultProjection` enum on `ToolExecutionResult` with `STANDARD` as the
default and `IMAGE_ONLY` as the new explicit value. This is preferred over a result subclass (which would complicate
the frozen dataclass/adapter surface) or a tool-ID special case (which would couple generic transport to a product
name). `IMAGE_ONLY` is valid only for a successful result with `text == ""`, `len(images) == 1`, empty attachments,
and no error. Its `to_message()` content is exactly that one image block; internal `data` and
hashes may remain on `ToolResultMessage.data` for runtime/audit consumers but do not become a content block.
Failures use the standard typed-error projection. No executor, serializer, or provider branches on `observe`, an
internal tool ID, or an environment name.

The registered output schema is the locked empty object schema
`{"type": "object", "properties": {}, "additionalProperties": false}` because the existing schema validator applies
to `result.data`, not media. Runtime enforcement of one valid PNG belongs to the generic screenshot executor plus
the `IMAGE_ONLY` result invariant; tests cover both boundaries.

## Core Design

Introduce a runtime-checkable `ScreenshotSource` protocol at the tool/environment boundary:

```python
class ScreenshotSource(Protocol):
    async def screenshot(self) -> bytes: ...
```

Synchronous environment implementations may be adapted once at composition boundaries, especially for the
thread-owned Coworker backend. The generic executor resolves the source from `ToolExecutionContext.backend` (or
the borrowed backend's explicit delegated screenshot source), calls it, verifies PNG signature/decodability and
non-zero dimensions with Pillow, computes internal hashes, and returns one `ResultImage` with an image-only model
projection. It contains no checks for environment names.

Keep `ToolExecutionContext.backend` as the injection point because it already represents the borrowed current-run
environment and preserves run/resource ownership. Do not add benchmark fields to the context.

## Environment Connections

### Coworker

- Add a screenshot method to the browser driver using `page.screenshot(type="png", full_page=False)` after the
  existing owner-thread and allowed-page checks.
- Expose it through the existing `ThreadOwnedSyncBackendAdapter`/`ThreadOwnedObservationBackend` ownership path so
  Playwright remains on its creating thread.
- Do not call the environment REST client, reserve an action, emit a DOM observation receipt, or alter `bid`-based
  click/fill/select/navigation behavior.
- Keep `TICKET_READ` tied to the existing ticket navigation receipt and its `route=ticket` action, so `observe({})`
  remains benchmark-neutral.
- Retire the legacy model-visible `make_browser_observe` implementation from the Coworker profile; retain only any
  private compatibility code still required by historical artifacts, with no registration under `observe`.
- The exact Playwright screenshot call is **UNVERIFIED** in the target Chrome/X environment until its real browser
  terminal gate passes.

### ALFWorld

- Read the current state's `frame_path` bytes without stepping the environment.
- Require a current frame and preserve its PNG bytes exactly in the returned image payload.
- Do not invoke translator `observe`, textual `look`, navigation/manipulation, or scoring logic.
- A missing/unreadable/non-PNG frame returns a typed failure.

### Home/Desktop

- Capture the current display with Pillow `ImageGrab.grab`, using the configured/current `DISPLAY` when applicable,
  and encode the captured RGB image as PNG.
- A missing or unusable display returns a typed `screenshot_unavailable` failure with no fallback JSON state and no
  fabricated image.
- Keep world/memory resources and all non-observe Home behavior unchanged.
- Pillow `ImageGrab.grab` against the target X display is **UNVERIFIED** until the real display terminal gate passes.

## Remove Observation Gating

- Remove `before_action` and `after_action` observation calls from `ToolExecutionPipeline`.
- Remove `requires_pre_observation` and `post_action_observation` from active `VerificationPolicy` construction and
  profile policies. Remove their obsolete enums/statuses/helpers when repository-wide references permit; otherwise
  leave only clearly deprecated inert compatibility parsing, never runtime enforcement.
- Stop constructing `ObservationLedger` per run, stop installing `ObservationProviderCommitter`, and stop binding
  provider attempts to observations.
- Remove observation-reset invalidation hooks and provider-binding/freshness dependencies from the normal runtime.
- Retire `ObservationService` and environment-specific capture/verifier registration from active composition once
  no non-observe consumer remains. Delete dead state-machine implementation rather than retain an alternate mode.
- Keep provider image encoding and ordinary context image retention/stripping behavior; only the special observation
  identity/binding contract is removed.

## Profile Migration

- Register `core.observe.v1` once per catalog using the same generic definition and executor.
- Enable that ID in Home, ALFWorld, and Coworker profile views at the same position currently occupied by each old
  observe ID.
- Remove `home.observe.v1`, `alfworld.observe.v1`, and `coworker.observe.v1` registrations.
- Update snapshots/audits that assert internal IDs while proving model aliases and their ordering are unchanged.

## Tests

Start with failing contract tests, then implement:

1. Generic executor: empty args, one valid PNG, non-empty dimensions, exact bytes, one image-only model block, and
   typed failures for missing source, exceptions, empty bytes, non-PNG bytes, and invalid PNG.
2. Profiles: all three expose `core.observe.v1` as `observe`; no environment-specific observe IDs; unchanged alias
   order; no action policy requires observation.
3. Pipeline/runtime: actions execute before or after any screenshot without observation state; provider attempts do
   not commit/bind observations; retries/cancellation remain unchanged.
4. Coworker: screenshot executes on the owner thread, uses viewport PNG, contains actual page pixels, and does not
   call DOM observation/client reserve/record APIs.
5. ALFWorld: returned bytes equal the exact current frame file and do not advance state/event/action counters.
6. Home: deterministic injected display capture produces the expected PNG; headless capture failure is typed.
7. Remove or rewrite state-machine tests. Do not preserve tests whose only assertion is observation debt, freshness,
   provider binding, or post-action invalidation. Retain independent tests for general image transport.
8. Provider projection: inspect the actual next provider request and assert the tool result contains one image block
   and zero text blocks for each profile. Cover every configured provider transport's wire shape.

## External Terminal Black-Box Gates

Per environment, validate both the call result and the next model input rather than internal trace alone:

- Coworker: on a real headed Playwright page with a uniquely colored pixel fixture, call `observe`, decode the image
  from the captured provider request, assert its dimensions/pixel marker, assert one image/zero text, and confirm the
  browser action tools still change the DOM afterward. Also require successful browser/process return codes.
- ALFWorld: in the available real environment, compare returned PNG bytes/pixels to the current environment frame
  per episode and assert one image/zero text in the next provider request. For each episode select a known legal,
  deterministic action, require its successful return code, and independently assert the expected external
  state/frame/step-count change. An attempted or classified failure is not a pass. Apply gates per episode rather
  than with an aggregate `any` gate.
- Home: under an available real X display, place a known pixel fixture on screen, capture it, decode the provider
  request image, and assert the marker/dimensions plus one image/zero text. Separately run headless and assert the
  typed failure with no image. Require process return codes.

If this machine lacks Chrome, ALFWorld runtime, or an X display, run all deterministic orthogonal fixtures locally
and report the corresponding real-environment gate as `UNVERIFIED`; do not claim it from mocks or traces.

Add a provider acceptance matrix separate from request-shape capture. For every configured provider transport,
assert its exact serialized image-only tool-result wire shape. Then send at least one real multimodal tool-result
continuation through an available supported provider and require a successful API response, proving the provider
accepts and processes the shape. OpenAI-compatible and Anthropic tool-result image acceptance remain **UNVERIFIED**
until their respective real API gates pass; absent credentials are recorded per transport as `UNVERIFIED`, never
replaced by a captured/mock request.

## Documentation and Migration Record

- Update README capability lists and examples.
- Update the Home/general, Coworker, and ALFWorld user guidance with the exact `observe({})` behavior and clarify
  that action tools remain independent.
- Update architecture documentation to show screenshot-source injection and removal of the observation control
  state from the action pipeline.
- Add a CHANGELOG entry describing what changed, why, and compatibility impact.
- Add a top entry to `docs/pitfalls.md` recording the real-run false positive: internal tests passed while
  observation gating rejected benchmark actions.
- Add a positive rule to `CLAUDE.md`: never use an inspection/read tool as an authorization state machine for
  unrelated actions; validate model-visible multimodal cardinality at the provider request boundary.
- Keep `progress.md` current through implementation, verification, review findings, and unresolved real gates.

## Execution Order and Review Gates

1. Complete this plan and receive exactly one read-only reviewer review before product edits.
2. Disposition every plan finding and lock the plan.
3. Add RED tests for the public/tool-message contract, gating removal, and each screenshot source.
4. Implement the generic result projection, tool, and source protocol.
5. Connect Coworker, ALFWorld, and Home; migrate profiles; remove the active observation state machine.
6. Run focused tests, then full non-live tests, Ruff, format check, compileall, interface audit, and diff check.
7. Run all available external terminal black-box gates and record PASS/FAIL/UNVERIFIED per environment.
8. Update all same-source documentation and the live progress record.
9. Receive exactly one read-only final code review after implementation, verification, and docs are complete.
10. Disposition findings, make targeted fixes, and run targeted verification without another automatic review.

## Definition of Done

- `observe({})` is one canonical tool and yields exactly one PNG image to the model in all three profiles.
- The screenshot is the actual current Coworker page, ALFWorld frame, or desktop display.
- No observe-specific text/JSON/DOM/state metadata reaches the model.
- Coworker DOM tools and ALFWorld action tools work independently of whether `observe` was called.
- No active provider-binding/freshness/observation-debt state can block actions.
- Focused and full internal gates pass; every available external gate validates actual pixels and return codes.
- README, guides, architecture, CHANGELOG, pitfall, positive rule, and progress record match the code.
- The required plan and final read-only reviews are completed and all findings are dispositioned.
