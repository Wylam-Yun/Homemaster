# V2.7 Direct Flat MindMemOS Add Implementation Plan

Status: Completed and externally verified on 2026-08-18.

## Decision

Replace only the model-visible `mindmemos_add` payload and its accepted FIFO worker operation:

```text
content + memory_type + current execution evidence
-> deterministic flat-memory write plan
-> memory embedding and BM25
-> Qdrant memory
-> Neo4j Memory, Source and EXTRACTED_FROM
-> exact raw readback
```

The public Add payload contains exactly `content` and `memory_type`. `fact` maps to the native MindMemOS
`fact` type and `procedure` maps to `experience`. The persisted memory content is exactly the submitted string.

This path does not call Schema Add, Vanilla `memory.add.extract`, or any chat model. Its write plan contains no
Entity, entity vector, `MENTIONS`, `MENTIONED_IN_SOURCE`, property-memory or semantic graph writes.

Session finalization remains unchanged and continues to use native Vanilla Add with `enable_entities=False`:

```text
Session transcript -> memory.add.extract -> flat experience memory
```

There is no offline graph worker, outbox, enrichment status or schema migration in this change. Existing structured
memories remain readable and updateable; no existing memory is rewritten or deleted.

## Public Contract

- `mindmemos_add` accepts non-empty `content` and `memory_type=fact|procedure`; `record` is rejected.
- Success remains asynchronous acceptance with `status=accepted`, an opaque `job_id`, and
  `verified_terminal_state=false`.
- The queue freezes the exact content, declared type, selected current evidence/provenance and authoritative request
  context. Later run or turn changes cannot alter the job.
- Search projects both direct flat facts and experiences as `content` results. It keeps the existing structured-record
  projection for historical Schema memories.
- Update continues to dispatch memories without `record_json` through the native flat-memory update path.

## Implementation

1. Replace `AddMemoryInput.record` with `content`, and validate current-scope evidence without relying on a caller-owned
   structured record.
2. Change `MemoryAddQueue` jobs from `MemoryRecord` to exact `content`, `memory_type` and frozen evidence metadata. The
   single FIFO worker calls `EmbeddedMindMemOS.add_flat`.
3. Add `EmbeddedMindMemOS.add_flat` using MindMemOS typed DTOs and `MemoryDbWriter`: preprocess only for indexing
   metadata, generate BM25 and one memory embedding, create one message Source and one `EXTRACTED_FROM` edge, then
   write with strong consistency.
4. Persist an Add operation record around the direct write and mark it completed or failed. Do not route through either
   Add pipeline.
5. Independently read the raw memory by returned ID and require active status, exact content, mapped native type and
   expected extraction marker before reporting worker completion.
6. Generalize flat search projection from only Vanilla experiences to active flat facts and experiences while retaining
   memory-type filters and source metadata.

## Verification

- Provider-facing schema contains only `content` and `memory_type`; old `record` input fails before backend admission.
- Queue tests assert exact frozen values, FIFO order, one active writer, failure isolation and close-time drain.
- Runtime tests assert exact original content, `fact|experience` mapping, memory/BM25 vectors, Source and
  `EXTRACTED_FROM`, with empty entities/entity vectors and no `MENTIONS`.
- A recording chat client asserts zero `chat` calls and zero `memory.add.*` chat tasks for explicit Add. Session Vanilla
  tests continue to assert its existing extraction call and `enable_entities=False` configuration.
- Search tests independently cover a direct flat fact, direct flat procedure and legacy structured record.
- The external gate writes at least one fact and one procedure through the real queue. For each ID independently, Qdrant
  raw readback must match exact content/type/status and Neo4j must contain its Memory, Source and `EXTRACTED_FROM` edge,
  with no Entity or `MENTIONS`. Application close and writer return status must both be successful.

## Documentation

Update the memory architecture, user guide, README capability text, CHANGELOG and session handoff. Do not commit without
fresh explicit authorization; when a commit is requested, its message must carry the same change, reason and impact as
the CHANGELOG entry.
