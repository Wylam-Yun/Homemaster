# V2.1 Memory System Architecture

Home composition owns `FileMemoryStore`, `FrozenMemoryContextService`, `MemoryEvidenceLedger` and
`Mem0MemoryStore`. They start before the first run, enter `application_services`, and close in reverse ownership order.
Tools resolve them only from `ToolExecutionContext.services`; there are no process-global stores or backend selectors.

```text
canonical user turn -> evidence ledger -> opaque current-turn ref -> Runtime Context
verified/read tool result -> evidence ledger -> opaque ordered ref -> later provider iteration

memory tool -> USER.md / MEMORY.md -> fsync + atomic replace + independent readback
add/update/delete -> evidence validation -> deterministic record/text/metadata
                  -> mem0 infer=False -> embedded Qdrant -> SDK receipt + get + raw point
search -> exact metadata + semantic embedding + local BM25 -> RRF merge -> schema validation
       -> valid records + secret-safe corrupt-record diagnostics
       -> JSON tool-result content -> next provider iteration reads records and IDs
```

All six memory tools project their complete structured result into `ToolResultMessage.content`; the same payload remains
in `data` for internal events and programmatic consumers. Provider transports serialize `content`, not `data`, so IDs,
records, file entries, receipts and typed errors must never exist only in metadata. Structured mem0 memories are recalled
on demand through `search_memories`; V2.1 does not run an automatic search before every user turn.

File prompt order is base system prompt, SOUL, USER, MEMORY. A session id owns one immutable snapshot; disk mutation
does not change an active session. Threat scanning occurs both at write and snapshot/read time.

Fact identity is subject type plus stable subject id (or normalized name) plus predicate; value changes keep the same ID.
Procedure identity is normalized entry URL plus normalized name. Canonical JSON, search text, dedupe key and flattened
metadata are generated together. `provenance_seq` comes only from the persistent ledger and prevents delayed old evidence
from overwriting newer state.

The installed mem0 Qdrant adapter writes named BM25 sparse vectors but its effective public semantic search does not
consume them. HomeMaster therefore invokes semantic and `keyword_search` branches separately and merges by ID/RRF; exact
hints precede both. Startup verifies the offline `Qdrant/bm25` artifact at locked commit
`e499a1f8d6bec960aab5533a0941bf914e70faf9`. Any missing/corrupt branch fails unavailable rather than silently
degrading.

External mutation success requires both an accepted SDK receipt and an independent terminal-state readback, including raw
Qdrant payload/vector checks. Close explicitly releases mem0 history SQLite and the underlying Qdrant client; a separate
process immediately reopens the same path in the lifecycle gate.

Sync-backed mutations are owned by a store task protected by the store lock. Caller timeout/cancellation fences the returned
result but cannot cancel an already running Qdrant thread; the owner retains the lock until the worker finishes, and close waits
before releasing SQLite/Qdrant resources. An unconfirmed timed-out mutation is non-retryable `outcome_unknown`.
