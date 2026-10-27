# V3.8 Memory System Architecture

V3.8 replaces the embedded MindMemOS runtime with a standalone MindMemOS HTTP server reached through
`mindmemos_sdk`. HomeMaster no longer owns memory pipelines, Qdrant, or Neo4j lifecycle — the server owns all memory
processing and storage. HomeMaster owns: canonical session traces, the durable work outbox, archive snapshots,
feedback scheduling, skill invocation sealing, and raw-store verification. The upstream pin is recorded in
`third_party/MINDMEMOS_UPSTREAM.md` (branch `feature/v3.8-native-lifecycle`).

```text
terminal turn -> SessionTraceStore.commit_terminal (SQLite tx)
              -> exactly one turn_add outbox intent (durable before ack)
              -> MemoryWorkerPool -> MindMemOSClient.add_turn (Vanilla Add, operation_id)
              -> QdrantNeo4jVerifier raw readback -> job completed

explicit archive -> ArchiveService.create_archive (immutable snapshot, idempotent boundary)
                -> archive_dreaming job -> resolve seed op ids via operations ledger
                -> Dreaming(seed_add_record_ids) -> raw readback of produced memories

agent mindmemos_search -> MindMemOSClient.search (agent-invoked only; no automatic recall)
agent mindmemos_feedback -> FeedbackContextBinder snapshot of visible context
                         -> MindMemOSClient.feedback_explicit

daily 03:00 -> ImplicitFeedbackScheduler -> watermark row + implicit_feedback job per window
            -> client.feedback_implicit(window={window_start, window_end})

skill command resolved -> SkillInvocationTracker.begin (turn boundary)
                      -> seal at terminal commit (steps materialized from canonical events)
                      -> every 8th sealed trace -> skill_evolution job
                      -> submit_skill_trace x8 (upstream seal) -> submit_evolution_batch
                      -> draft skill version
```

## Ownership

```text
SOUL / USER / MEMORY   -> FileMemoryStore -> memory.data_root/files
evidence refs          -> MemoryEvidenceLedger -> memory.data_root/evidence.sqlite3
canonical turn trace   -> SQLiteMemoryWorkStore -> <data_root>/memory_work.db
outbox jobs            -> memory_work.jobs (claimed/failed/outcome_unknown/terminal)
archive snapshots      -> memory_work.archives (immutable boundary, idempotent)
skill invocations      -> memory_work.skill_invocations (+_steps)
feedback watermarks    -> memory_work.feedback_watermarks (per scope per window)
audit                  -> memory_work.audit + optional audit JSONL on the pool
```

`mindmemos_sdk` is an optional dependency under the `memory` extra; `MindMemOSClient` lazy-loads it and raises a
typed `MindMemOSClientError` naming the required extra when absent. `memory.mode: files` runs file memory only and
never opens the SDK, the work store, or any worker.

## Durable lifecycle contract

- `SessionTraceStore.begin_turn` is mandatory admission: if the durable begin row fails the run returns
  `memory_trace_unavailable` before any model call.
- `commit_terminal` writes terminal status + canonical events + the `turn_add` intent in one SQLite transaction —
  a crash between "run finished" and "job enqueued" is impossible by construction.
- Workers claim jobs with `owner_id` fencing; a crashed claim is reclaimed by `audit` on next pool start.
- Any external call whose result may have been sent but is unknown settles `outcome_unknown` and reconciles through
  `GET /v1/memory/operations/{id}` before retrying — never blind-retry, never drop.
- Server-side idempotency keys: `turnadd:{session}:{turn}`, `dreaming:` job key, `implicitcheck:{scope}:{window}`,
  `skillevolve:{batch_id}`, `invocation_id` for traces, `batch_id` for evolution — replays return the same receipt;
  a changed payload under the same key returns 409 and fails the job loudly.

## Raw verification (black-box gate)

`QdrantNeo4jVerifier` reads the server's real stores, not SDK receipts: `add_record_v1` / `memory_item_v1` /
`search_record_v1` / `operations` / `skill_trace_pending_v1` Qdrant collections and Neo4j `Memory` nodes keyed by
`project_id` + `memory_id`. Add jobs and feedback mutations settle `completed` only when the raw rows exist;
Dreaming settles only when every `produced_memory_ids` entry reads back. Missing rows retry with backoff, then
fail — a green SDK receipt is never accepted as proof of persistence.

## Removed in V3.8

- `EmbeddedMindMemOS`, vendored `third_party/MindMemOS/`, and every process-local memory pipeline call.
- `MemoryAddQueue` / `MemoryEnrichmentQueue` and the Session Finalizer FIFO (`experience/finalizer.py`,
  `session_finalization.py`, `schema_episode*.py`, ALFWorld compiler/dreaming-state modules).
- Automatic recall (`application/memory_recall.py`, `benchmarking/memory_recall.py`, the recall latch and
  compaction-triggered recall). Search is agent-invoked only.
- `mindmemos_add` agent tool and the `add_flat` update fallback in `mindmemos_update`.
- AddRecord-based skill evolution (`trajectory_memory.py` writers); trajectory data records kept as
  `trajectory_records.py` for benchmark read models only.
- `mindmemos_runtime.py`, `mindmemos_lifecycle.py`, `mindmemos_writes.py`, `mindmemos_queries.py`,
  `mindmemos_feedback.py`, `automatic_recall.py`, `add_queue.py`, `enrichment_queue.py`.

## Configuration

```yaml
memory:
  mode: full                      # files | full
  data_root: ../.runtime/memory
  mindmemos:
    base_url: http://127.0.0.1:8765   # required in full mode
    api_key: "..."                    # or HOMEMASTER_MEMORY_API_KEY env
  qdrant:                           # raw verifier only
    mode: server
    url: http://127.0.0.1:6333
  neo4j:                            # raw verifier only
    mode: server
    uri: bolt://127.0.0.1:7687
    username: neo4j
    password: "..."
  workers:
    count: 2
    poll_interval_seconds: 1.0
  implicit_feedback:
    enabled: true
    timezone_name: Asia/Shanghai
    hour: 3
```

`mindmemos.base_url` must be absolute http(s) in full mode. Qdrant/Neo4j config feeds only the raw verifier — the
server owns the real connections; when a verifier section is absent the matching raw gate is skipped and reported,
never silently passed.

## Tools (agent-visible)

- `context_memory` — unchanged USER/MEMORY file memory writes.
- `mindmemos_search` — agent-invoked search; emits a `search_record_id` the feedback binder ties to the call.
- `mindmemos_history`, `mindmemos_update`, `mindmemos_delete` — native client CRUD; update uses the server-native
  versioned write path (no flat fallback).
- `mindmemos_feedback` — explicit feedback; the binder deep-copies the visible message window and only the
  search results that were actually visible to the agent at call time.

No `mindmemos_add`: adds are exclusively the per-terminal-turn Vanilla Add owned by the outbox worker.

## Web memory management

`web/routes/memory.py` proxies the native management endpoints through `MindMemOSClient` (create / list / search /
detail / history / update / delete) for both plain and structured memories, under the existing typed `tenant_id`
ACL. Graph reads go through the raw verifier and fail closed when unavailable. Archive-status routes read
`ArchiveService` receipts (`archive_id`, `job_id`, `status`) — the deleted `session_finalization` controller is
gone.
