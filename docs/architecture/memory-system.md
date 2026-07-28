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
search -> exact metadata + mem0 hybrid search -> RRF merge -> schema validation
       -> valid records + secret-safe corrupt-record diagnostics
       -> JSON tool-result content -> next provider iteration reads records and IDs
```

All six memory tools project their complete structured result into `ToolResultMessage.content`; the same payload remains
in `data` for internal events and programmatic consumers. Provider transports serialize `content`, not `data`, so IDs,
records, file entries, receipts and typed errors must never exist only in metadata. Structured mem0 memories are recalled
on demand through `search_memories`; V2.1 does not run an automatic search before every user turn.

All six tool input schemas are generated from Pydantic input models and the same models validate execution input. Local
`$defs`/`$ref` values are deterministically inlined only in the provider projection. Mimo may encode the nested
`add_memory`/`update_memory` record as a JSON object string even when the schema advertises an object; the typed input
boundary decodes only a valid JSON object and then applies the unchanged discriminated `FactRecord | ProcedureRecord`
validation. Arbitrary text, arrays and invalid records remain rejected.

For Anthropic-format chat, live text and thinking still stream from delta events, but tool name/input, stop reason and final
usage come from the SDK's `get_final_message()` result. HomeMaster does not parse XML-like `<tool_call>` text as executable
input and no longer maintains an independent production source of assembled tool arguments.

File prompt order is base system prompt, Assistant Identity (SOUL), User Profile (USER), Persistent Memory (MEMORY).
The semantic headings deliberately do not look like workspace file paths. A session id owns one immutable snapshot;
disk mutation does not change an active session. Threat scanning occurs both at write and snapshot/read time.

Fact identity is subject type plus stable subject id (or normalized name) plus predicate; value changes keep the same ID.
Procedure identity is normalized entry URL plus normalized name. Canonical JSON, search text, dedupe key and flattened
metadata are generated together. `provenance_seq` comes only from the persistent ledger and prevents delayed old evidence
from overwriting newer state.

The installed mem0 2.0.13 public `Memory.search()` owns lemma/entity preprocessing, dense-vector retrieval, Qdrant BM25
and their score fusion. HomeMaster calls that public hybrid API once and merges its results with metadata-exact hints; it
does not invoke a second `keyword_search()`. The locked `Qdrant/bm25` artifact is packaged with HomeMaster; startup atomically materializes it
under the configured persistent FastEmbed cache (default project `.cache/homemaster/fastembed`) and sets both the
preflight and mem0/Qdrant branch to that same offline cache. It then verifies commit
`e499a1f8d6bec960aab5533a0941bf914e70faf9`, file set, SHA-256 values and Chinese encoding. Any packaged-artifact
integrity or persistent-cache failure leaves the backend unavailable rather than silently degrading. spaCy and the
locked `en_core_web_sm` model are project dependencies, so mem0 never needs a first-search model download.

External mutation success requires both an accepted SDK receipt and an independent terminal-state readback, including raw
Qdrant payload/vector checks. Close explicitly releases mem0 history SQLite and the underlying Qdrant client; a separate
process immediately reopens the same path in the lifecycle gate.

Sync-backed mutations are owned by a store task protected by the store lock. Caller timeout/cancellation fences the returned
result but cannot cancel an already running Qdrant thread; the owner retains the lock until the worker finishes, and close waits
before releasing SQLite/Qdrant resources. An unconfirmed timed-out mutation is non-retryable `outcome_unknown`.
