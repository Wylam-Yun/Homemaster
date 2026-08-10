# V2.1 Memory System Architecture

Home composition owns `FileMemoryStore`, `FrozenMemoryContextService`, `MemoryEvidenceLedger` and
`EmbeddedMindMemOS`. They start before the first run, enter `application_services`, and close in reverse ownership
order. Tools resolve them only from `ToolExecutionContext.services`; there is no backend selector or process-global
HomeMaster store.

## Ownership

```text
SOUL / USER / MEMORY -> FileMemoryStore -> memory.data_root/files
evidence refs         -> MemoryEvidenceLedger -> memory.data_root/evidence.sqlite3
fact / procedure      -> EmbeddedMindMemOS -> MindMemOS native pipelines
                                           -> local Qdrant + configured Neo4j
```

SOUL/USER/MEMORY remain file-owned. `MemoryMigrationCoordinator` only publishes legacy file memory and verifies the
existing Evidence database. It never reads or migrates old mem0 Qdrant/history data. `memory.root` is the only accepted
legacy field; `memory.mem0` is rejected. `doctor` uses read-only `inspect()` and never opens MindMemOS.

## Structured Flow

```text
canonical user turn / verified tool result
  -> MemoryEvidenceLedger issues opaque ordered evidence ref
  -> add_memory/update_memory validates FactRecord or ProcedureRecord and evidence
  -> deterministic TextMessage + metadata(record_json, provenance_seq, homemaster_memory_type)
  -> EmbeddedMindMemOS.add()
  -> MindMemOS schema_add.add_sync()
  -> raw memory readback by persistent ID
  -> complete JSON ToolResultMessage.content

search_memories
  -> EmbeddedMindMemOS.search()
  -> MindMemOS search_pipeline.search()
  -> raw memory read by each returned ID
  -> nested request metadata decoded and record schema validated
  -> HomeMaster field filters
  -> model-visible records and raw IDs
```

HomeMaster `fact` maps to MindMemOS `fact`. HomeMaster `procedure` maps to MindMemOS `experience`, while metadata keeps
`homemaster_memory_type=procedure` and the complete serialized `ProcedureRecord`. The model only receives raw memory IDs
that can be passed to get, update and delete; episodic or aggregate view IDs are not returned as structured record IDs.

MindMemOS schema add is configured with HomeMaster's `fact` and `task_experience` entity schemas. The compact extraction
prompt requires `entities` and `edges`, forbids episodic-only output, and is explicitly preserved when MindMemOS selects
the request language prompt set. Chat output defaults to 8192 tokens when the HomeMaster provider does not set a limit,
preventing truncated entity JSON.

`EmbeddedMindMemOS` is a lifecycle and configuration adapter, not a second memory engine. It creates MindMemOS native
add/search/get/update/delete pipelines, maps HomeMaster chat and embedding providers into MindMemOS config, disables
telemetry and Kafka, and owns Qdrant/Neo4j cleanup. Feedback, dreaming and skill evolution remain outside the current
structured-memory tool surface.

## Tool Contracts

All six memory tools project their full structured result into `ToolResultMessage.content`; the same payload remains in
`data` for internal consumers. Provider transports serialize `content`, so IDs, records, receipts and typed errors must
not exist only in metadata.

Tool schemas and runtime validation use the same Pydantic models. Mimo may encode the nested record as a JSON object
string; the boundary decodes only an object and then performs the unchanged discriminated `FactRecord | ProcedureRecord`
validation. Arbitrary text, arrays and invalid records remain rejected.

Updates preserve evidence ordering. HomeMaster first verifies that the replacement has newer `provenance_seq`, archives
the old raw memory through the native delete pipeline, then writes and reads back a replacement through schema add. If
the archive succeeded but replacement terminal state cannot be confirmed, the result is `memory_outcome_unknown` and
must not be automatically retried.

File prompt order remains base system prompt, Assistant Identity (SOUL), User Profile (USER), Persistent Memory (MEMORY).
A session owns one immutable snapshot; file mutations affect new sessions only. Structured memories are searched on
demand and never grant device, browser or environment authority.
