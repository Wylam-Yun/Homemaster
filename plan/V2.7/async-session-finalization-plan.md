# V2.7 Async Session Finalization Plan

## Problem

Structured `mindmemos_add` is accepted into an application-owned FIFO, but `/new` currently calls
`wait_idle()` and then runs Vanilla Add, implicit feedback and dreaming inline. Session rotation is
therefore blocked by all pending memory work even though the process remains alive.

## Options

1. Keep `/new` as a synchronous drain barrier. Ordering is simple, but it blocks the exact workflow
   that async Add is meant to unblock.
2. Enqueue Session finalization in the existing memory FIFO. This preserves one memory worker and
   exact ordering while allowing the next Session to run immediately. This is the selected option.
3. Add a separate finalization FIFO plus a shared MindMemOS lock. This duplicates lifecycle state and
   creates cross-queue ordering and shutdown failure modes.
4. Use a durable broker. This survives process failure, but adds deployment and retry semantics that
   are outside the accepted process-local MVP.

## Invariants

- `/new` snapshots the old Session id and enqueues its finalization without waiting for completion.
- Structured Add and Session finalization share one FIFO and never overlap with each other.
- The next HomeMaster run can execute while old-Session memory work continues in the background.
- `/exit`, EOF and prompt Ctrl+C enqueue the current Session, drain all accepted memory work, then
  close the application.
- Ctrl+C during an ordinary run still cancels only that run. Repeated Ctrl+C during exit drain remains
  ignored until cleanup completes.
- Every queued finalization has structured queued, processing and terminal audit events. A failed
  finalization does not stop later FIFO work.

## Verification

- Prove `/new` reaches the next run before a deliberately blocked old-Session finalizer completes.
- Prove `Add A -> Finalize A -> Add B` executes in exact FIFO order with maximum memory concurrency 1.
- Prove multiple `/new` operations preserve finalization order.
- Prove every process-exit path drains finalizations before application close.
- Prove real SIGINT still cancels a run and cannot interrupt exit drain or close.

## Implementation Result

The selected single-FIFO design is implemented. Session finalization admission is a synchronous
`put_nowait`, so `/new` cannot drive the worker before switching Session. Finalization domain failures
become failed queue receipts without stopping later work. The focused CLI/memory, lifecycle,
benchmark and cleanup verification passes with 147 tests; Ruff, compileall and `git diff --check`
also pass. Exact live-process receipts and remaining old-process state are recorded in
`docs/session-handoff.md`.
