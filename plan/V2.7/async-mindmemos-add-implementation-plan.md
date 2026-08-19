# V2.7 Asynchronous MindMemOS Add Implementation Plan

> Superseded for the worker payload and persistence path by
> `plan/V2.7/direct-flat-mindmemos-add-implementation-plan.md`. FIFO acceptance and lifecycle semantics remain current;
> public Add no longer accepts a structured record and the worker no longer executes Schema Add.

## Decision

Change only the model-visible structured `mindmemos_add` operation from terminal write semantics to
application-owned acceptance semantics. After input, evidence, permission and scope validation, the tool
freezes one immutable job, appends it to one process-local FIFO queue and returns `accepted` with a stable
`job_id`. One application-owned worker executes the existing native Schema Add and raw terminal readback.

The queue is intentionally non-durable. Clean application shutdown drains accepted work; process crash,
power loss and `SIGKILL` may lose queued work. V2.7 adds no Kafka, SQLite queue, parallel workers or retry.

Session finalization is not split into queue jobs. Before the existing finalizer starts, the interactive
shell waits for structured Add jobs to finish. The existing ordered Vanilla Add, raw readback, implicit
feedback, feedback readback, dreaming counter and optional dreaming sequence remains unchanged.

## Public Contract

- `mindmemos_add` success means the validated job was accepted by the application queue.
- The immediate domain result contains `status=accepted`, a non-empty opaque `job_id`, and
  `verified_terminal_state=false`; it contains no `memory_id`. The generic provider/stream envelope preserves this as
  `status=success, domain_status=accepted`.
- `backend_attempted=true` records that an application-owned mutation was accepted and therefore must not
  be automatically retried as a pre-backend rejection.
- Search is eventually consistent with accepted Add jobs. A subsequent tool call in the same run may not
  find the new record yet.
- Update, delete, feedback, history and search retain their current terminal contracts.

## Ownership And Ordering

`MemoryAddQueue` is an application resource created beside `EmbeddedMindMemOS`, started only after
MindMemOS is ready, injected through application services and registered after MindMemOS in the LIFO
resource scope. Its close therefore seals admission, drains all accepted jobs, stops the worker and only
then allows MindMemOS, Qdrant and Neo4j to close.

There is exactly one worker and one FIFO queue. Each job freezes:

- `job_id`;
- the already validated complete `FactRecord` or `ProcedureRecord`;
- the selected provenance sequence;
- the authoritative `MemoryRequestContext`.

The worker must explicitly bind the frozen typed record while running the native pipeline. It must not
depend on the submitting task's `ContextVar`, evidence refs, current run generation or current session.
Run cancellation after acceptance does not cancel application-owned work.

Job states are `queued -> processing -> completed|failed`. Failure is terminal, is written to structured
JSONL and does not stop the worker from processing later jobs. There is no retry.

## Code Changes

1. Add `src/homemaster/memory/add_queue.py` with immutable job and receipt types plus the single-worker
   lifecycle: `start`, `enqueue`, `wait_idle`, and `aclose`.
2. Move the structured record serialization, native Add, candidate-ID collection and per-ID raw readback
   from the tool module into one `EmbeddedMindMemOS.add_record` terminal operation. Both the queue worker
   and focused runtime tests use this single path.
3. Change `AddMemoryExecutor` to retain current validation, freeze provenance/context, enqueue one job and
   return the accepted receipt. Update its model description and memory audit fields so acceptance is not
   described as persisted success.
4. Compose and start the queue after MindMemOS, expose it as `memory_add_queue`, add it to
   `HomeApplicationBundle`, and register it after MindMemOS so LIFO cleanup drains before backend close.
5. Change the interactive shell finalization wrapper to await queue idle before calling the unchanged
   `SessionFinalizer.finalize`. `/exit`, EOF, prompt-level Ctrl-C and `/new` all use this wrapper. A Ctrl-C
   during an active run continues to cancel only that run.
6. Keep application close as the universal drain gate for one-shot CLI, Gateway and exceptional cleanup
   paths that do not invoke the interactive SessionFinalizer.
7. Update the benchmark classifier to distinguish accepted tool receipts from terminal job completion and
   use the post-exit job record plus raw database readback for confirmed writes.

## Observability

Append one field-limited JSONL event for every state transition. Each event includes `job_id`, job type,
state, tenant/project/session/run identity, enqueue/start/completion timestamps, duration, and on completion
the exact returned memory ID. Failure includes the exception type and exact message. Record content and
evidence refs are not duplicated into the job log.

The existing memory operation audit records tool acceptance separately from worker completion. It must not
set `terminal_verified=true` for an accepted result.

## Tests

### Queue and tool tests

- The tool returns `accepted` before a blocked fake Add is released.
- Two rapid Add calls receive distinct job IDs and execute FIFO with maximum active count one.
- Accepted work survives submitting run cancellation.
- One failed job is logged failed and the next job still completes; no retry occurs.
- Admission after seal fails before queue mutation.
- `wait_idle` and `aclose` wait through raw terminal readback, not only native Add return.
- Worker execution observes the frozen typed record and request context from its own job.

### Lifecycle and finalizer tests

- Composition starts MindMemOS before the queue and closes the queue before MindMemOS.
- Interactive exit order is structured queue drain, Vanilla Add, implicit feedback, optional dreaming,
  MindMemOS close.
- `/new` finalizes the old session only after the queue is idle.
- One-shot application close drains an accepted Add even though the tool result already returned.
- Queue failure remains observable but does not prevent best-effort cleanup of later resources.

### External terminal gate

Run one real application dispatch with the configured chat/embedding providers and real local
Qdrant/Neo4j. Assert the tool result returns `accepted` before worker completion, the process/application
close return status is successful, the job JSONL reaches `completed`, and the returned ID independently
reads back as active with the exact original typed record. A second rapid record must independently reach
completed and independently read back; no aggregate `any` assertion is allowed.

## Documentation And Commit

Update the architecture memory data flow and shutdown order, the memory user guide tool receipt and
eventual-consistency behavior, the README capability summary, CHANGELOG and session handoff. The final
commit message must carry the same change, reason and impact as the CHANGELOG entry.
