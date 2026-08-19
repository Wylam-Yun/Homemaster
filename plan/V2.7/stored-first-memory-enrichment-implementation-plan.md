# V2.7 Stored-First Memory Enrichment Implementation Plan

Status: Implemented and externally verified on 2026-08-19.

## Decision

Split only the model-visible `mindmemos_add` path into a verified storage phase and an internal enrichment phase:

```text
content + memory_type + current execution evidence
-> local preprocessing and BM25
-> Qdrant Memory + Neo4j Memory/Source/EXTRACTED_FROM
-> exact external readback
-> return stored + memory_id to the model
-> application-owned dense embedding and Entity enrichment (maximum concurrency 2)
```

The model submits only `content + memory_type`. The result contains the verified stored Memory ID and does not expose
internal `pending`, embedding, Entity or Dreaming state. A successful tool result means the exact Memory is already
stored and addressable; it does not claim that semantic indexing or graph enrichment has completed.

Session finalization remains an application-owned background job and continues to use native Vanilla Add. Its
Vanilla extractor and config both enable native Entity output, so the Finalizer writes Memory, Entity and `MENTIONS`
through the original MindMemOS flow before implicit feedback and Dreaming.

## Public Contract

- `mindmemos_add` still accepts exactly non-empty `content` and `memory_type=fact|procedure`.
- Success returns `status=stored`, a non-empty `memory_id`, and `verified_terminal_state=true`; it contains no `job_id`
  and no background-work fields.
- Before success, HomeMaster independently reads the Qdrant Memory and verifies active status, exact content, mapped
  native type and direct-flat extraction marker. It also verifies the Neo4j Memory, Source and `EXTRACTED_FROM` edge.
- BM25 is written with the Memory, so exact/keyword retrieval is available immediately. Dense semantic quality and
  Entity graph recall are eventually enriched.
- A storage failure returns failure or outcome-unknown according to the observed external terminal state. Later
  enrichment failure never changes an already returned storage success.

## Internal Data Flow

1. Replace the direct-flat embedding-before-write path with local preprocessing and BM25-only write. Persist explicit
   internal `vector_pending=true` and `entity_enrichment_pending=true` metadata.
2. Make the tool await this bounded storage operation directly instead of submitting it behind Session Finalizer work.
3. Add an application-owned enrichment queue with two workers. Admission occurs only after verified Memory storage.
4. Dense enrichment calls the existing embedding client and patches the same Qdrant point, clearing `vector_pending`
   only after vector readback succeeds.
5. Entity enrichment reuses the Vanilla entity-aware extractor, entity normalization/deduplication, stable Entity IDs,
   entity vectorizer, DTO builders, `MENTIONS` builder and `MemoryDbWriter`. It never creates, updates or archives a
   Memory.
6. Clear `entity_enrichment_pending` only after Neo4j independently shows the expected Entity nodes and `MENTIONS`
   edges. Zero valid entities is a successful empty result.
7. Structured JSONL records queue admission, start, per-stage completion/failure, duration and IDs without copying
   Memory content into the log. Application close drains both workers before MindMemOS closes.
8. The existing serial memory-work queue remains the owner of ordered Session Finalizer work; model Add no longer uses
   this queue and therefore cannot wait behind a multi-minute Finalizer.

## Dreaming Boundary

- Finalizer Vanilla Add enables native entities before registering its newly added Memory IDs for Dreaming.
- Dreaming keeps using the existing `Memory-[:MENTIONS]->Entity` scopes.
- Direct-flat Memories enriched earlier may join a Finalizer-triggered scope as shared-Entity neighbors; direct-flat
  Adds do not independently advance the current Finalizer Dreaming threshold in this MVP.
- A failed Finalizer Entity/Add operation fails the Finalizer before Dreaming. Existing no-scope records must not be
  used as evidence that consolidation occurred.

## Verification

- Tool test: block both enrichment workers and prove `mindmemos_add` still returns only after exact Memory storage,
  with `status=stored`, a real `memory_id`, no `job_id` and no background fields.
- Ordering test: block Session Finalizer work and prove a concurrent model Add reaches stored terminal state without
  waiting behind it.
- Queue tests: assert maximum enrichment concurrency two, per-job failure isolation and close-time drain.
- Runtime tests: assert the first write contains exact payload and BM25 but a pending/zero dense vector; after
  enrichment assert the same ID has a non-zero dense vector and its pending marker is cleared.
- Entity tests: independently verify every expected Entity and `MENTIONS` edge, and prove no second Memory is created.
- Finalizer test: inspect the real provider request for entity-aware Vanilla output and verify Entity/`MENTIONS` graph
  state before Dreaming begins.
- Real external gate: dispatch `mindmemos_add` through `ApplicationRuntime`, assert the returned Memory ID and latency,
  then independently read Qdrant and Neo4j before and after background drain. Run a Finalizer batch containing repeated
  entities and require Dreaming relation detection/action planning to be reached or produce a justified post-cluster
  no-action result.

## Documentation And Commit

Update the architecture, memory user guide, README, CHANGELOG and session handoff with the stored-versus-enriched
contract. Do not commit without fresh explicit authorization; the commit message must carry the same reason and impact
as the CHANGELOG entry.
