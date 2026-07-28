# Generic Browser Tools Phase-One Architecture

## Status

This document describes an experimental, default-hidden feasibility path. It is not a released browser API.
Coworker still uses its existing driver until phase two removes that duplicate responsibility.

## Ownership And Data Flow

An enabled `RunRequest` carries a `browser_session_factory` dependency. `ApplicationRuntime` creates one session
before constructing the provider, registers it in a run-owned `RunResourceScope`, and derives a frozen Registry from
the application Registry. The same derived Registry supplies the provider manifest and tool dispatch. Disabled runs
continue using the application Registry and do not create Chrome.

```text
RunRequest dependency
  -> BrowserSessionFactory.create(run_id)
  -> run-owned PlaywrightBrowserSession
  -> frozen Registry with browser_* and session-bound observe
  -> provider manifest and ToolExecutor dispatch
  -> live Playwright page
  -> DOM readback / PNG / JSONL / trace / WebM
  -> run-scope close
```

The application Registry is never mutated to toggle a run. All nine tools use `resource_key=browser:backend`, so DOM
actions and screenshots cannot interleave within a session.

## Phase-One Contracts

- `browser_inspect` creates the only current snapshot and returns bounded text plus visible interactive elements.
- Actions accept only `snapshot_id + element_id`. They do not accept selectors, JavaScript or ordinal bypasses.
- A target must remain connected, visible, enabled, unobscured and fingerprint-identical.
- Navigation and every successful write invalidate the current snapshot. A new inspect is required before another
  write.
- Fill, select and binary actions read the live DOM after the action. Click proves interaction, not business success.
- Infrastructure timeout or cancellation fences the session; it is not retried or reused.
- `observe` captures the same page and remains image-only.
- Session creation, provider construction failure, interface audit failure, run completion and application close all
  converge on the same run-owned cleanup stack.

## Evidence

Each operation appends one JSONL row with input, duration, outcome, error code, generation and fence state. Playwright
tracing captures screenshots and DOM snapshots, and the context records a run-owned WebM. These are execution facts;
business success still requires an independent page-state assertion.

## Deliberate Limits

Phase one allows only injected trusted HTTP(S) origins and is for local fixtures. Redirect, popup and per-frame
production policy are not complete. Cross-origin iframe collection is only a capability probe. Snapshot revisions,
stable re-identification, bounded history, Shadow DOM completeness and timeout recovery are phase-two work.

ARIA combobox selection is implemented but remains unverified against the real Ant Design Select until the Ant dev
page can run. The current milestone must not be advertised through README or a user guide until the real Ant gate and
the later Coworker migration are complete.
