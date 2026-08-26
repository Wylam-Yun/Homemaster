# OpenCLI 1.8.7 Capability Matrix

Date: 2026-08-26

Source: `@jackwener/opencli@1.8.7`, tag `v1.8.7`, commit
`87b60a36590c3e2a466c37266c3348d73d7f68fe`. This matrix records the final V3.1 disposition; the
normative public schemas and acceptance criteria remain in `browser-tools-spec.md`.

## Browser Commands

| OpenCLI capability | HomeMaster V3.1 surface | Status | Evidence boundary |
| --- | --- | --- | --- |
| `state`, DOM snapshot and diff | `browser_inspect(view=dom)` | implemented | DOM/Shadow DOM/table/scroll-container black box |
| AX snapshot and frame AX inventory | `browser_inspect(view=ax|frames)` | implemented | CDP AX tree and iframe black box |
| CSS and semantic `find` | `browser_find` | implemented | CSS read-only plus role/name/label/text/testid cases |
| numeric refs, fingerprint recovery | shared target resolver | implemented | exact/stable/reidentified/stale per-target cases |
| compound date/time/select/file metadata | inspect/read and typed actions | implemented | native and ARIA control readback |
| `get` title/URL/text/value/attributes/HTML | `browser_read` | implemented | per-kind external DOM readback |
| form state and HTML tree | `browser_read` | implemented | form and bounded-tree fixtures |
| article/Markdown extraction and cursor | `browser_extract` | implemented | upstream fixtures and page cursor continuation |
| `open` | `browser_navigate` | implemented | final URL and response status |
| `back` plus forward/reload | `browser_history` | implemented | final URL/history readback |
| `click`, `dblclick` | `browser_click(click_count=1|2)` | implemented | click counters and resulting DOM state |
| `fill`, `type` | `browser_fill`, `browser_type` | implemented | exact value readback, replace and append |
| `check`, `uncheck` | `browser_check`, `browser_uncheck` | implemented | checked/ARIA state readback |
| `select` | `browser_select` | implemented | native value and ARIA selection readback |
| `hover`, `focus` | `browser_hover`, `browser_focus` | implemented | hover/focus DOM state |
| `keys` | `browser_press` | implemented | key-driven DOM state |
| `scroll`, auto-scroll and into-view | `browser_scroll` | implemented | page/container positions per target |
| `upload` | `browser_upload` | implemented | approved artifact and file-input readback |
| `drag` | `browser_drag` | implemented | source/target drop state |
| `dialog` | `browser_dialog` | implemented | listener-before-trigger and dialog receipt |
| `tab` and popup coordination | `browser_tabs`, `browser_wait` | implemented | run-owned tab list and popup terminal state |
| `screenshot`, full-page and annotated refs | `browser_screenshot` | implemented | decoded PNG plus annotation mapping |
| `console` | `browser_console` | implemented | bounded time/cursor continuation |
| `network` | `browser_network` | implemented | request-specific list/detail/failure and cursor |
| download wait and artifact collection | `browser_download` | implemented | completion status, file bytes and artifact hash |
| selector/text/time/XHR/download waits | `browser_wait` | implemented | semantic/CSS/URL/state/popup/dialog/download cases |
| `analyze` | `browser_analyze` | implemented | navigation timeout and structured API candidates |
| page-context `eval` | gated `browser_eval` | implemented | absent by default; capability, origin and frame gates |

## Non-Runtime OpenCLI Surfaces

| OpenCLI surface | Status | Reason and replacement |
| --- | --- | --- |
| `bind`, `unbind`, `close` lease | bridge_only | OpenCLI Extension ownership; HomeMaster owns its context and tabs |
| daemon lifecycle and transport | bridge_only | no second browser owner or Node runtime process |
| existing Chrome/profile takeover | bridge_only | user-approved HomeMaster Chromium context is the security boundary |
| `init` adapter scaffold | adapter_dev_only | adapter authoring, not a browser operation |
| `verify` adapter fixture command | adapter_dev_only | HomeMaster uses pytest and independent browser black boxes |
| raw CDP endpoint/cookies/session lease | internal_only | Playwright owner may use CDP internally; never model-visible |
| Node `fetchJson` and `~/.opencli` cache | internal_only | network observation and artifacts use HomeMaster policy/storage |

No OpenCLI general browser capability audited in Spec section 2.8 remains `planned` or `unsupported`.
The upstream baseline is independently locked by 27 passing test files (406 tests); HomeMaster behavior
is separately covered by provider-contract tests and real Playwright terminal-state black boxes.
