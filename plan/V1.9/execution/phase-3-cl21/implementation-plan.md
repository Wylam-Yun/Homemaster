# CL-21 Hooks and Plugins Implementation Plan

## Scope

Implement an explicit, application-owned extension layer on HPC2. The first delivery supports
versioned local extension manifests, trusted local async hook entrypoints, provenance-bound plugin tools,
generation-fenced hook execution, and atomic hook reload. It does not enable any extension by default,
does not add a new remote channel, and does not access hkust4. "Isolation" in this MVP means load/error/
generation/resource isolation between explicitly approved extensions; it is not an OS sandbox for hostile
Python. A later untrusted extension tier requires a subprocess plus a separately verified sandbox/IPC policy.

The locked OpenHarness seed is commit `9b2efd795c6aa09f88b0c257d269a9e518da6ae7`; its hook event,
priority, matcher, timeout and blocking semantics are retained as characterization inputs. The
HomeMaster delta deliberately excludes arbitrary shell/HTTP hooks and rejects synchronous callbacks.
Timeout/cancel fences the result of cooperative async callbacks but is not claimed to revoke arbitrary
side effects from trusted code; hooks therefore cannot be a terminal, device-safety, permission or scorer owner.

## Alternatives and Decision

1. **Declarative-only manifest**: safest loading and rollback, but cannot contribute executable tools or
   hooks. Rejected because CL-21 must exercise hook lifecycle and Catalog provenance.
2. **Sandboxed subprocess-per-extension**: required for hostile code and enforceable kill-on-timeout, but
   adds an IPC protocol and a platform sandbox that cannot be verified on the current deployment. Reserved
   for a later untrusted extension API; a bare subprocess alone would still access host files/network.
3. **Direct imports with implicit discovery**: lowest implementation cost, but import failure, capability
   expansion and stale callbacks can affect the application. Rejected.
4. **Explicit approved local manifest plus trusted async entrypoints (recommended MVP)**: keeps one stable
   protocol, allows deterministic tests and atomic hook generation swap, and leaves a sandboxed subprocess
   boundary available later. Deployment config pins bytes and grants capabilities; code is trusted, while
   its access through HomeMaster interfaces remains capability- and permission-gated.

## Contracts

- `ExtensionManifest`: `schema_version`, stable `extension_id`, SemVer `version`, requested capabilities,
  and entrypoint names. `ExtensionApprovalConfig` separately supplies exact id/version, expected canonical
  content SHA-256, approved capability grants and explicitly enabled Home tool ids. A manifest cannot grant
  itself authority. Duplicate ids, unsupported versions, missing entrypoints and path escapes fail closed.
- `ExtensionSpec`: validated manifest plus loaded async hook callbacks and `RegisteredTool` values. A tool
  must have `ExecutionBackend.PLUGIN`, `ToolProvenance.source == "plugin"`, exact canonical reference
  `extension:<id>@<version>#sha256:<content_digest>`, and typed required capabilities. Requested
  capabilities, deployment-approved grants and run-principal capabilities are intersected; declared but
  ungranted, granted but principal-missing, undeclared and exact-tool-denied cases all fail closed.
- `HookEvent`, `HookSpec`, `HookContext`, `HookResult`, and `AggregatedHookResult`: `APPLICATION_START`,
  `RUN_START`, `RUN_END`, `APPLICATION_STOP`, typed payload, matcher, descending priority with stable ties,
  deadline, blocking-on-failure, redacted result fields, extension id and generation. A callback receives no
  application runtime or raw credentials and must be async; cancellation-resistant code is treated as a
  trusted-code defect, fenced from publication, and never advertised as an enforceable sandbox timeout.
- `ExtensionGeneration`: immutable hook specs, generation id and immutable `tool_plane_digest`. Hot reload is
  hooks-only. Any manifest version, provenance, capability, tool id/schema/executor or approved-tool change
  rejects reload with `restart_required`; there is no mixed new-hook/old-tool generation. Reload builds a
  candidate fully, refuses swap while old callbacks are active, then atomically swaps. Failed/busy candidates
  retain the prior generation; every result still checks its captured generation before publication.
- `RunRequest.enabled_tool_ids` becomes a narrowing override: every requested id must already exist in the
  selected profile's `enabled_tool_ids`. It can never expand a profile to a merely Catalog-registered plugin
  id. CLI/Gateway metadata has no authority to enable extensions.
- Canonical content digest is recomputed by the host over canonical JSON manifest bytes (without any
  self-asserted digest) plus sorted `(relative_entrypoint_path, file_bytes)` records. The loader opens and
  reads each contained, non-symlink entrypoint once, verifies the deployment-pinned digest, and compiles the
  same bytes; it never hashes one path then imports it again. MVP entrypoints are single files and cannot use
  relative package imports. Symlink swap, wrong hash and same id/version with changed bytes fail closed.

## Implementation Steps

1. Add `src/homemaster/extensions/contracts.py` with strict manifest/spec/event/hook/result/generation
   dataclasses and JSON-safe redaction helpers. Add compatibility manifest fixtures and a provenance
   manifest entry for the locked OpenHarness hook seed.
2. Add `extensions/loader.py`: parse only deployment-approved manifest paths, independently recompute the
   canonical content digest, realpath-contain and reject symlink entrypoints, compile exactly the verified
   bytes, validate returned tools/hooks, and isolate a failed extension as a typed diagnostic without
   mutating the current Catalog or generation.
3. Add `extensions/hook_runner.py`: deterministic priority/matcher selection, cooperative async deadline/
   cancellation, approved-grant checks, active-callback accounting, generation-fenced publication and
   blocking aggregation. Reject synchronous callbacks. Never use a hook as the only implementation of
   `ToolExecutionPipeline`, permission, device lease, terminal policy, verification or scorer ownership.
4. Add `extensions/reloader.py`: immutable hooks-only candidate build, pinned content hash check, exact
   tool-plane equality, active-callback busy rejection, atomic generation swap, rollback on failure and
   defensive stale-generation result fencing.
5. Add typed extension approvals to config and integrate explicit extensions into Home composition/factory.
   Register approved tools before final Home `ToolView`, enable only ids listed in the approval, and leave
   ALFWorld/Coworker profiles unchanged. Add exact `required_capabilities` to canonical tool definition and
   permission evaluation. Enforce that request-level enabled ids are a subset of profile ids.
6. Integrate lifecycle precisely: `APPLICATION_START` runs once after extension validation and before the
   first provider; a blocking failure rolls back application-owned startup resources. `RUN_START` runs once
   per `ApplicationRuntime.run()` after its generation is acquired but before provider/backend work; blocking
   fails only that run. `RUN_END` runs best-effort exactly once in the run finally path for success, failure
   and cancel. `APPLICATION_STOP` runs best-effort once before extension resources close. One failed run must
   not close the application or affect another session.
7. Add focused tests for manifest/schema/provenance, duplicate ids, import failure isolation, containment,
   wrong hash/hash-then-replace/symlink swap, three-way capability denial, profile narrowing, CLI/Gateway
   spoof rejection, priority/matcher, cooperative timeout/blocking/cancel, reload success/restart-required/
   rollback/busy, stale fencing, lifecycle exactly-once, per-run rollback and cleanup.
8. Update README, architecture, user guide, CHANGELOG, progress, state and upstream port manifest. Run
   extension/application targeted tests, full HPC2 non-live, Ruff, format, compileall, JSON and diff gates.

## Explicit Non-goals

- No implicit user/project plugin discovery.
- No arbitrary shell command, HTTP endpoint, prompt model, agent hook, synchronous callback or untrusted
  code execution in this MVP.
- No runtime mutation of an already-frozen ToolView; any tool-plane change requires application restart.
- No hkust4 access, Telegram network call, live provider, device, browser or external plugin gate.

## Definition of Done

All implementation and non-live tests pass; every extension instance has per-instance pass/fail evidence;
the application remains usable with zero extensions; failed extension load leaves the prior application
generation and resources intact; trusted in-process code is never described as sandboxed; all external
symbols remain `UNVERIFIED` until a user-guided live gate;
one read-only plan review occurs before implementation and one read-only CL-21 stage review occurs after
implementation, tests and documents.
