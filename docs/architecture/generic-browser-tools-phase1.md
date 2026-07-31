# Generic Browser Gateway Architecture

## Status

The generic browser layer is available through the explicit
`homemaster --gateway --browser` deployment mode. It remains isolated from Coworker's legacy driver;
the browser mode does not load Coworker tools or migrate that environment.

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

`BrowserGatewayApplication` wraps the authoritative `RunRequest` created by `ChannelBridge`, preserving
Feishu text, session, generation, delivery context and permission subject while adding only
`profile=browser` and the configured session factory. The application Registry is never mutated to
toggle a run. All ten session-bound tools use `resource_key=browser:backend`, so DOM actions and
screenshots cannot interleave within a session.

## Phase-One Contracts

- `browser_inspect` creates the only current snapshot and returns bounded text plus visible interactive elements.
- Actions accept only `snapshot_id + element_id`. They do not accept selectors, JavaScript or ordinal bypasses.
- A target must remain connected, visible, enabled, unobscured and fingerprint-identical.
- Navigation and every successful write invalidate the current snapshot. A new inspect is required before another
  write.
- Fill, select and binary actions read the live DOM after the action. Click proves interaction, not business success.
- Target actions scroll the resolved element into view before refreshing visibility, enabled and obscured state.
- `browser_backfill` captures a PNG and dispatches it as a clipboard file to an editable target. Success requires the
  page to accept the paste, a subsequent DOM change and an image preview with the exact same data URL. The receipt
  contains MIME type, size, source SHA-256 and matching preview SHA-256, never bytes.
- Main-frame navigation requests are checked before network dispatch. A disallowed origin is aborted, recorded and
  immediately fences the run-owned session, including delayed page-initiated navigation.
- Infrastructure timeout or cancellation fences the session; it is not retried or reused.
- `observe` captures the same page and remains image-only.
- Navigate, mutation, backfill and wait results establish an observation barrier. The next provider tool call is
  restricted to `observe`; its canonical image also flows through Gateway MEDIA to Feishu.
- Session creation, provider construction failure, interface audit failure, run completion and application close all
  converge on the same run-owned cleanup stack.

## Ticket Skill Boundary

The browser prompt contains environment and observation rules only. Change-ticket tasks load the single
`change-ticket-executor` Skill, which defines a generic ticket-reading, plan-locking, terminal-verification,
review-image, backfill and rollback meta-workflow. Concrete fields, commands, steps and GT node IDs are absent from
both prompt and Skill; they come from the ticket text read with Home general tools. The current demo's complete normal
and anomaly/rollback GT is stored separately under `data/browser_demo/case_02/`. Its ticket SHA-256 and every
`sop_step_id` mapping are validated against the source ticket. The normal implementation is `VERIFIED`; the full
normal and anomaly/rollback UI execution remain `UNVERIFIED`.

## Evidence

Each operation appends one JSONL row with input, duration, outcome, error code, generation and fence state. Playwright
tracing captures screenshots and DOM snapshots, and the context records a run-owned WebM. These are execution facts;
business success still requires an independent page-state assertion.

## Deployment Limits

The configured start URL and every navigation remain restricted to injected HTTP(S) origins, including the final URL
after redirects. Popup policy, full cross-origin frame policy, snapshot revision history, Shadow DOM completeness and
timeout recovery remain outside this delivery. The verified Ant target is a deterministic Mock UI; UI success is not
evidence of a real backend business change. Native fixture selection is verified, while any untested external Ant
Select variant remains `UNVERIFIED` until exercised in that exact runtime.
