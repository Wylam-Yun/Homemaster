# Unified Session Finalization Implementation Plan

## Goal

Make session completion a single HomeMaster application lifecycle operation. A caller marks
the lifetime of a session through `application.session(...)`; leaving or explicitly closing
that scope submits the existing `SessionFinalizer` to the application-owned memory FIFO and
returns immediately. `ApplicationRuntime.run()` remains a turn runner and does not infer
session completion.

## Root Cause

Session finalization is currently composed and scheduled independently by the interactive
Shell and LoCoMo. ALFWorld closes `ApplicationRuntime` after an episode but has no session-end
lifecycle operation, so its trace is persisted while no MindMemOS Vanilla Add is submitted.
`ApplicationRuntime.aclose()` only closes application resources and must not guess which
sessions are semantically complete.

## Locked Design

1. Add a generic session-end callback contract to the application layer. The application
   layer must not import MindMemOS or experience implementations.
2. Add an idempotent `ApplicationSession` scope returned by
   `ApplicationRuntime.session(session_id, exit_reason=...)`.
3. Closing the scope invokes the callback synchronously. The Home composition callback only
   enqueues work on `MemoryAddQueue`, so close returns without awaiting Finalizer work.
4. Compose one `SessionFinalizer` and one session-end callback in
   `create_home_application()`. Memory-disabled applications accept session close as a no-op.
5. Preserve FIFO ordering and let application resource shutdown drain the queue before
   MindMemOS and managed Neo4j close.
6. Do not modify the semantics of `ApplicationRuntime.run()` and do not finalize after an
   individual turn.
7. A scope is finalized at most once. Repeated close does not enqueue duplicate work.
8. Normal success, agent failure, timeout, cancellation, or a handled exception all close the
   surrounding session scope. Process hard-kill recovery is explicitly out of scope.

## Entry-Point Migration

- Interactive Shell: keep one scope across turns; `/new`, EOF, interrupt, and `/exit` close it.
  `/new` does not wait for finalization; terminal exit relies on application close to drain.
- Single-run CLI: one invocation is one session scope.
- ALFWorld: one ordinary episode is one session scope. A continuous taskset keeps one scope
  across all subtasks and closes it once when the taskset entry closes.
- LoCoMo: each source conversation is one session scope. Benchmark readback may explicitly
  wait for FIFO idle because scoring needs the memory result, while admission itself remains
  nonblocking.
- Gateway: no per-message finalization. A gateway message is a turn, and the current gateway
  has no semantic conversation-end event. The unified API is available for a later explicit
  reset/expiry boundary.

## Tests First

1. Application contract tests: callback invoked once on normal and exceptional scope exit;
   repeated close is idempotent; `run()` alone never invokes it.
2. Composition tests: memory-enabled composition creates Finalizer work; memory-disabled
   composition is a no-op; application close drains accepted finalization before resource
   close.
3. Shell tests: `/new` admits finalization and starts the next session before old work
   completes; exit drains.
4. ALFWorld entry tests: ordinary and continuous taskset sessions finalize once with the
   correct session ID and reason, before application resource close.
5. LoCoMo tests: remove benchmark-owned Finalizer construction and retain verified readback.
6. Production-call audit: all entry points with an explicit session-end boundary use the
   unified lifecycle API.

## Verification

1. Run focused application, composition, Shell, ALFWorld, LoCoMo, and queue tests.
2. Run the broader HomeMaster test suite relevant to application and benchmarks.
3. On `hkust4`, run one complete visual ALFWorld episode with memory enabled.
4. Independently verify environment success and return status.
5. Independently read back the session memory from Qdrant and Neo4j, including source session
   lineage, and verify Finalizer/queue receipts.
6. Verify Unity, Xvfb, Neo4j, and listener cleanup per instance.

## Documentation

Update the architecture guide, memory guide, ALFWorld guide, README, CHANGELOG,
`docs/pitfalls.md`, `CLAUDE.md`, and `docs/session-handoff.md` with the implemented lifecycle
and the external verification result. Do not commit without explicit user authorization.
