# Web ALFWorld Serve Design

## Decision

`homemaster serve --alfworld` will run the existing Web Console against the same ALFWorld
application adapter used by the Gateway. CLI and Web remain transport adapters over one
`ApplicationRuntime`; neither transport owns a separate tool catalog, permission policy, or
environment implementation.

The current capability difference is a composition omission, not an intentional product boundary.
The Gateway path creates an ALFWorld tool registry, starts the configured ALFWorld worker, and wraps
the base application so each request receives the fixed episode backend and dependencies. The Web
path currently creates only the base `home` application. React cannot repair that omission because
the environment resource is process-owned Python state.

## Scope

- Add an opt-in `--alfworld` flag to `homemaster serve`.
- In that mode, load the existing `alfworld_gateway` configuration, register the ALFWorld tool
  profile, start the existing worker binding, and wrap the base application with
  `AlfworldGatewayApplication`.
- Keep the existing Web HTTP, WebSocket, reducer, artifact, and approval contracts unchanged.
- Keep loopback-only binding. Remote operators continue to use an SSH tunnel.
- Preserve ordinary `homemaster serve` behavior.
- Do not change Feishu, permission policy, risk tiers, ALFWorld action semantics, or React layout.

## Architecture And Data Flow

```text
homemaster serve --alfworld
  -> validate loopback host
  -> create_home_application(tool_environment="alfworld",
                             permission_mode=DEFAULT,
                             confirmation_handler=WebConfirmationHandler)
  -> create_alfworld_gateway_binding(existing config and resource scope)
  -> AlfworldGatewayApplication(base application, session owner, binding)
  -> create_web_app(wrapped application, same confirmation handler)
  -> FastAPI / React

browser message
  -> Web RunRequest with the existing web-local-operator subject
  -> ALFWorld wrapper binds profile/environment/dependencies
  -> ApplicationRuntime
  -> ToolExecutor -> PermissionChecker
  -> WebConfirmationHandler blocks the exact mutating tool call
  -> approval.requested WebSocket event -> existing React dialog
  -> POST /api/approvals/{approval_id}
  -> the same blocked call resumes
  -> ALFWorld backend receipt and world-state verification
```

The Web permission subject already excludes `tool.auto`, and Web composition already selects
`PermissionMode.DEFAULT`. Therefore ALFWorld mutating tools use the existing approval Future without
any new policy mode or UI protocol.

## Lifecycle And Failure Semantics

ALFWorld startup is asynchronous, so the Web server must create the binding and run Uvicorn in one
owned event-loop lifecycle. Startup failure closes the partially-created base application and any
already-bound resources before returning a nonzero CLI result. Normal shutdown first prevents new
ALFWorld session claims, then the existing Web lifespan denies pending approvals, cancels/joins Web
runs, closes event delivery, and closes the base application resources.

The existing ALFWorld single-session invariant remains authoritative. The first Web session to run
owns the fixed episode. A second session receives `alfworld_session_busy`; the implementation will
not add reset, episode selection, or multi-episode behavior for this demo.

## Verification

Internal gates:

- CLI parsing proves `serve --alfworld` reaches Web composition with the ALFWorld selection.
- Composition tests prove the ALFWorld tool environment, binding, wrapper, and cleanup are wired,
  while default `serve` remains unchanged.
- Existing Web approval tests remain green; a focused integration test uses a real
  `ApplicationRuntime` dispatch and a controlled mutating backend to prove approve executes once and
  reject executes zero times.
- Frontend typecheck, unit tests, and production build remain green because the browser protocol does
  not change.

External black-box gate on `hkust4`:

- Start the real `homemaster serve --alfworld` and require successful process/listener readiness.
- Connect through an SSH loopback tunnel, create one browser session, and submit one task for the
  configured fixed episode.
- For every approval instance, assert it belongs to that session/run/tool call; approve it through
  the public HTTP endpoint and require a successful HTTP response.
- Require the ALFWorld external terminal state `won=true`, per-action successful backend receipts,
  nonblank rendered frames, clean stderr, and full worker/process cleanup after shutdown.
- A rejected controlled action must leave the independently-read external state unchanged.

## Documentation

Update README, the Web Console user guide, application architecture, changelog, and session handoff
with the new command, single-session rule, SSH tunnel example, approval behavior, and verified live
evidence. No HTML documentation is generated.
