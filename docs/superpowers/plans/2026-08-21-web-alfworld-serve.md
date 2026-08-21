# Web ALFWorld Serve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task in the main agent. Repository rules prohibit implementation subagents; use one reviewer only at the plan gate and one reviewer only after final verification.

**Goal:** Add `homemaster serve --alfworld` so the existing Web Console can run one real fixed ALFWorld episode through the existing approval UI and shared ApplicationRuntime.

**Architecture:** Preserve one runtime and permission pipeline across transports. Web composition will select the existing ALFWorld tool registry, asynchronously create the existing ALFWorld binding, wrap the base application with `AlfworldGatewayApplication`, and run Uvicorn in the same owned lifecycle. React and the Web approval protocol remain unchanged.

**Tech Stack:** Python 3.11, Typer, FastAPI, Uvicorn, asyncio, React 18, TypeScript, Vitest, pytest, ALFWorld/AI2-THOR.

---

## File Map

- `src/homemaster/cli/app.py`: expose the `serve --alfworld` CLI selection.
- `src/homemaster/web/serve.py`: own Web application composition, ALFWorld binding startup, Uvicorn lifecycle, and partial-startup cleanup.
- `src/homemaster/gateway/alfworld.py`: give the reused ALFWorld application wrapper an explicit idempotent close path that seals session admission before delegating resource cleanup.
- `tests/homemaster/web/test_serve.py`: lock CLI selection, default composition, ALFWorld composition, cleanup, and server lifecycle.
- `tests/homemaster/gateway/test_alfworld_http_live.py`: reuse existing live environment conventions only; do not weaken opt-in gates.
- `README.md`, `docs/web-console-user-guide.md`, `docs/architecture/application-runtime.md`, `CHANGELOG.md`, `docs/session-handoff.md`: document the same shipped behavior and verification evidence.

### Task 1: Lock The CLI And Composition Contract

**Files:**
- Modify: `tests/homemaster/web/test_serve.py`
- Modify: `src/homemaster/cli/app.py`

- [ ] Add a CLI test that invokes `serve --alfworld`, replaces `run_web_server` with a recorder, and asserts `environment="alfworld"` while preserving default host and port.
- [ ] Add a default CLI test that invokes `serve` and asserts `environment=None`.
- [ ] Run `scripts/homemaster test -q tests/homemaster/web/test_serve.py`; require the new ALFWorld assertion to fail before implementation.
- [ ] Add a boolean Typer option named `--alfworld` to `serve_command` and pass `environment="alfworld" if alfworld else None` into `run_web_server`.
- [ ] Re-run the focused test and require both CLI cases to pass.

### Task 2: Compose ALFWorld Inside The Web Server Lifecycle

**Files:**
- Modify: `tests/homemaster/web/test_serve.py`
- Modify: `src/homemaster/web/serve.py`
- Modify: `src/homemaster/gateway/alfworld.py`

- [ ] Add an async composition test with fakes for `create_home_application`, `create_alfworld_gateway_binding`, and the Uvicorn server. Assert `tool_environment="alfworld"`, `PermissionMode.DEFAULT`, the exact base resource scope, one wrapper, and one call to the server's async `serve()`.
- [ ] Add failure tests for binding startup and server startup. Independently assert the base application is closed exactly once, the ALFWorld owner is sealed when it exists, and no orphan server task remains.
- [ ] Add a default-mode test proving no ALFWorld binding is created and existing `create_home_web_app()` semantics remain compatible.
- [ ] Run the new tests and require failures caused by the missing environment-aware lifecycle.
- [ ] Implement an async internal server owner in `web/serve.py` that validates loopback before application construction, creates one `WebConfirmationHandler`, passes `tool_environment` and `PermissionMode.DEFAULT` to `create_home_application`, awaits `create_alfworld_gateway_binding` only for ALFWorld mode, wraps with `AlfworldGatewayApplication`, builds the FastAPI app, then awaits `uvicorn.Server(uvicorn.Config(...)).serve()`.
- [ ] Keep `run_web_server()` synchronous for Typer by calling `asyncio.run()` exactly once. Do not start ALFWorld in one event loop and serve it from another.
- [ ] Add `AlfworldGatewayApplication.aclose()` that first calls its idempotent `seal()` and then closes the delegated base application. Keep Gateway's existing explicit shutdown order unchanged.
- [ ] On every exception before FastAPI lifespan ownership begins, close the confirmation handler and the correct application owner. Avoid double-closing after lifespan has taken ownership.
- [ ] Re-run `tests/homemaster/web/test_serve.py` and the existing Gateway ALFWorld wrapper tests; require all to pass.

### Task 3: Prove Approval Resumes The Exact ALFWorld-Bound Run

**Files:**
- Modify: `tests/homemaster/web/test_serve.py`
- Test: `tests/homemaster/web/test_confirmations.py`
- Test: `tests/homemaster/web/test_app.py`

- [ ] Add a focused integration fixture whose wrapper injects a controlled external mutating backend into a real `ApplicationRuntime` request and whose permission decision requires confirmation.
- [ ] In the approve case, wait for the public `approval.requested` event, POST the exact approval ID, require HTTP 200 with `approved=true`, await the terminal run, and independently read the external state to assert one mutation.
- [ ] In the reject case, POST the exact approval ID with reject, require HTTP 200 with `approved=false`, await the terminal run, and independently assert the external state is unchanged and backend call count is zero.
- [ ] Assert per-instance correlation fields (`session_id`, `run_id`, request ID, tool call ID) rather than using an `any`/best aggregate.
- [ ] Run the Web app, confirmation, event projection, and serve suites together and require all to pass.

### Task 4: Verify Frontend And Static Packaging

**Files:**
- Verify: `web/src/App.tsx`
- Verify: `web/src/components/ApprovalDialog.tsx`
- Verify: `src/homemaster/web/static.py`

- [ ] Run `npm test -- --run` in `web/` and require all Vitest tests to pass.
- [ ] Run `npm run typecheck` in `web/` and require TypeScript success.
- [ ] Run `npm run build` in `web/` and require a nonempty `web/dist/index.html` plus emitted assets.
- [ ] Run the static packaging test and build a wheel from a clean build root; inspect the wheel archive and require the built index/assets and notices to be present.

### Task 5: Run The Real ALFWorld Web Approval Gate

**Files:**
- Runtime evidence only under ignored unique `.runtime/` and `var/` roots.

- [ ] Run `scripts/homemaster doctor --json` and an ALFWorld runtime-path preflight. Require exit code zero and the configured interpreter, assets, config, dataset, display/Xvfb, and trial manifest to exist.
- [ ] Allocate a unique run label, runtime root, trace root, stdout, stderr, and TCP port. Fail closed if any already exists.
- [ ] Start exactly one `scripts/homemaster serve --alfworld --host 127.0.0.1 --port <port>` process and require a successful listener/readiness response.
- [ ] Establish an SSH loopback tunnel and use the public Web HTTP/WebSocket surface to create one session and submit the configured episode task.
- [ ] For each emitted approval, record exact correlation, resolve it once through `POST /api/approvals/{approval_id}`, and require HTTP success. Reject duplicate or stale IDs.
- [ ] Require the terminal Web result to be successful and independently query/read ALFWorld evidence for `won=true`, successful backend return codes, and per-action terminal state. Decode every emitted PNG and require nonblank pixels.
- [ ] Stop the server through its normal signal path. Require server exit success, complete stderr with no traceback/backend error, closed ALFWorld worker/Xvfb resources, no listener, and no orphan child process.
- [ ] Run a controlled reject gate from a fresh unique environment and assert the relevant external world state does not change and the backend action is never attempted.

### Task 6: Documentation And Static Gates

**Files:**
- Modify: `README.md`
- Modify: `docs/web-console-user-guide.md`
- Modify: `docs/architecture/application-runtime.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/session-handoff.md`

- [ ] Document `homemaster serve --alfworld`, SSH tunnelling, fixed-episode/single-session ownership, approval behavior, and startup/shutdown failures with one exact usage example.
- [ ] Explain in architecture docs that CLI, Web, and Gateway share `ApplicationRuntime`; transport composition must select the same environment wrapper to achieve capability parity.
- [ ] Record only observed live evidence. Mark any unverified external symbol or terminal contract `UNVERIFIED`.
- [ ] Add the changelog entry before the final implementation commit; use the same change/why/impact content in the commit message.
- [ ] Run focused Python tests, touched-file Ruff, compileall, cleanup guard, `git diff --check`, Web tests/typecheck/build, wheel inspection, and the real ALFWorld black-box gate.
- [ ] Check `git status --short`; preserve the pre-existing untracked `story/alfworld-memory-report.html` and remove only generated files owned by this task.

## Self-Review Result

- Spec coverage: every scope, lifecycle, approval, external terminal, documentation, and cleanup requirement maps to a task.
- Placeholder scan: no deferred implementation markers or unspecified test steps remain.
- Type consistency: the plan reuses existing `Literal["alfworld"]`, `PermissionMode.DEFAULT`, `AlfworldGatewayApplication`, `WebConfirmationHandler`, and public approval endpoint names observed in the repository.
- Scope: one Web composition feature; no Feishu or permission-policy redesign.
