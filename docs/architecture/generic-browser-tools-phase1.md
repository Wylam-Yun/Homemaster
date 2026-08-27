# Generic Browser Gateway Architecture

## Status

V3.1 is the active browser protocol for `homemaster --gateway --browser` and `homemaster serve
--browser`. It targets previously unseen, conventional DOM/ARIA pages without site, route, ticket, or
framework-specific branches. Browser mode remains isolated from the ALFWorld owner.

## Ownership And Data Flow

An enabled `RunRequest` carries one `BrowserSessionFactory`. `ApplicationRuntime` creates one
run-owned `PlaywrightBrowserSession`, derives a frozen tool Registry from that exact session, and
closes it through `RunResourceScope` on every terminal path. Disabled runs do not create Chromium.

```text
RunRequest + run policy
  -> BrowserSessionFactory.create(run_id)
  -> one Playwright context / pages / listeners / trace / video
  -> 27 safe typed browser tools (+ gated browser_eval when authorized)
  -> semantic target or retained target_ref
  -> origin/frame/tab/actionability checks
  -> Playwright action
  -> independent DOM/file/URL/network terminal-state readback
  -> structured receipt + JSONL + screenshot/trace/WebM
  -> run-scope aclose
```

All browser tools serialize on `resource_key=browser:backend`; screenshots, actions, event listeners,
and backfill therefore use the same page and cannot race inside one session. Tabs, frames, popups,
dialogs, downloads, network responses, and refs carry run-owned identity.

Browser Web composition does not select a permission mode of its own. The configured
`permissions.mode` flows into the shared `ApplicationRuntime -> PermissionChecker` path, so
`full_auto`, `confirm`, and `plan` retain their normal semantics. A transport must not inject a
browser-specific default that masks the authoritative configuration.

## Tool And Target Protocol

The safe surface is `browser_navigate`, `browser_inspect`, `browser_find`, `browser_read`,
`browser_extract`, `browser_screenshot`, `browser_fill`, `browser_type`, `browser_select`,
`browser_check`, `browser_uncheck`, `browser_click`, `browser_hover`, `browser_focus`,
`browser_press`, `browser_scroll`, `browser_upload`, `browser_drag`, `browser_dialog`,
`browser_tabs`, `browser_history`, `browser_wait`, `browser_console`, `browser_network`,
`browser_download`, `browser_analyze`, and `browser_backfill`.

Known unique targets may be acted on directly with role/name/label/text/testid semantics. Unknown or
ambiguous targets are discovered with inspect/find, then acted on through the returned `target_ref`.
References are session, tab, frame, generation, and identity scoped. Resolution reports `exact`,
`stable`, or `reidentified`; conflicting or non-unique recovery fails as `stale_ref` or
`target_ambiguous` and never chooses the first match silently.

Inspect collection, retained-element filtering, find, and direct semantic actions share one text
matcher. Exact and contains matching case-fold and remove whitespace only when it lies between two Han
characters (`U+3400-U+4DBF`, `U+4E00-U+9FFF`, or `U+F900-U+FAFF`), covering framework display spacing
such as `确 认` without changing ordinary Latin/number spaces. Regex matching always runs against the
original unnormalized string.

The Runtime does not hide action schemas before inspect and has no pre-dispatch snapshot lease. It
passes the provider's semantic target or `target_ref` unchanged to the browser tool; the session-owned
resolver is the single authority for uniqueness, scope, identity recovery, and actionability. This
avoids a second V2.1 policy layer contradicting V3.1 target semantics.

CSS is read-only discovery. Typed actions re-resolve a semantic target or ref and verify visibility,
enabled state, obscuration, control type, origin, frame, and uniqueness. Editability is a separate
dimension: fill, type, and clipboard backfill reject readonly targets, while selection, click, focus,
keyboard, hover, scroll, drag, dialog, download, and binary-control actions do not reject a target only
because its DOM input is readonly. This supports compound controls such as Ant Design's readonly-input
ARIA combobox without weakening the common actionability gate. Navigation or cross-origin identity
changes invalidate incompatible refs; ordinary DOM rerenders may recover a unique stable identity.

## Representation And OpenCLI Boundary

Playwright remains the only browser owner. HomeMaster vendors the locked OpenCLI 1.8.7 browser
algorithm/test dependency closure under `browser/vendor/opencli_1_8_7` and injects deterministic
page-side scripts through `OpenCLIPageAdapter`. It never starts the OpenCLI daemon, Extension,
profile takeover, tab lease, CLI lifecycle, or a second Node browser session.

The vendor source, dependencies, tests, fixtures, generated scripts, license, provenance, and hashes
are package data. `SHA256SUMS` uses lexical paths so symlinks remain independently covered. Runtime
loads generated scripts through `importlib.resources` and verifies their manifest hash before page
evaluation.

The selected launch mode is part of the deployment contract. A headless-shell probe does not establish
that the full Chromium revision needed by a headful recorded run exists. Release preflight launches the
same headful mode and executable as the real run, opens the target origin, and verifies an exact DOM
control before provider execution.

## Mutation And Observation

Every mutation reads the actual DOM/file/URL result after dispatch. Fill/type/select/check actions
verify exact live control state; click proves the requested interaction and reports resulting URL/DOM
change but does not claim business success. Upload accepts approved artifact refs, never arbitrary
host paths. Backfill locks one PNG byte sequence, pastes it, and compares the rendered image hash with
the source hash.

Browser writes do not force a screenshot observation round trip. Their structured receipts are
checked against independent DOM or business postconditions. The model uses `browser_screenshot`
explicitly for visual inspection or annotated refs; screenshots are evidence, not action
authorization. Event-driven dialog, popup, download, and response operations arm their listener
before the trigger. Cursor-based console/network/extract reads preserve bounded continuation
without unbounded follow loops.

`browser_eval` is absent from the normal Registry. A run explicitly granted `browser.eval` receives
one gated tool scoped to an authorized tab/frame, with full input/output audit and a required external
postcondition. Typed tools never fall back to eval implicitly.

## Policy And Evidence

Initial, redirected, tab, popup, and frame URLs are checked against injected HTTP(S) origins. A
disallowed page transition is aborted and fences the session. Timeouts and cancellation also fence
the owner rather than retrying an uncertain mutation.

Each operation appends structured JSONL with input, target resolution, duration, return/error status,
generation, and external readback. Playwright produces run-owned trace and WebM artifacts. These prove
browser facts only; business success still requires independent per-field terminal assertions and
external return codes.

## Ticket Skill Boundary

The browser prompt defines only environment, target, observation, and verification rules.
`change-ticket-executor` supplies a generic plan-locking, execution, terminal-verification, evidence,
backfill, and rollback workflow. Ticket paths, SOP IDs, field values, commands, routes, and product
names are not hardcoded in the Skill, prompt, resolver, or browser backend.

## Deployment Limits

V3.1 covers standards-based DOM/ARIA workflows, Shadow DOM, policy-allowed frames, compound controls,
tabs, popups, dialogs, downloads, and common SPA rerenders. Canvas or visually encoded state still
requires screenshots; hostile anti-bot pages, browser extensions, existing-profile takeover, and
unapproved cross-origin content are outside the supported authority. A successful Mock UI or fixture
run is not evidence that a real backend business change occurred.
