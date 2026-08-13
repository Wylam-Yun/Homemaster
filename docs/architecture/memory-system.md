# V2.1 Memory System Architecture

Home composition owns `FileMemoryStore`, `FrozenMemoryContextService`, `MemoryEvidenceLedger`, optional
`ManagedNeo4jRuntime`, and `EmbeddedMindMemOS`. They start before the first run, enter `application_services`, and close
in reverse ownership order. In managed mode the order is Neo4j start, then MindMemOS start; shutdown is MindMemOS close,
then Neo4j lease release. Tools resolve them only from `ToolExecutionContext.services`; there is no backend selector or
process-global HomeMaster store.

## Ownership

```text
SOUL / USER / MEMORY -> FileMemoryStore -> memory.data_root/files
evidence refs         -> MemoryEvidenceLedger -> memory.data_root/evidence.sqlite3
fact / procedure      -> EmbeddedMindMemOS -> MindMemOS native pipelines
                                           -> local Qdrant + configured Neo4j
managed local Neo4j   -> ManagedNeo4jRuntime -> file lock + per-process leases
```

SOUL/USER/MEMORY remain file-owned. `MemoryMigrationCoordinator` only publishes legacy file memory and verifies the
existing Evidence database. It never reads or migrates old mem0 Qdrant/history data. `memory.root` is the only accepted
legacy field; `memory.mem0` is rejected. `doctor` uses read-only `inspect()` and never opens MindMemOS.

## Structured Flow

```text
canonical user turn / verified tool result
  -> MemoryEvidenceLedger issues opaque ordered evidence ref
  -> mindmemos_add/mindmemos_update validates FactRecord or ProcedureRecord and evidence
  -> deterministic TextMessage + metadata(record_json, provenance_seq, homemaster_memory_type)
  -> EmbeddedMindMemOS.add()
  -> MindMemOS schema_add.add_sync()
  -> raw memory readback by persistent ID
  -> complete JSON ToolResultMessage.content

mindmemos_search
  -> EmbeddedMindMemOS.search()
  -> MindMemOS search_pipeline.search()
  -> raw memory read by each returned ID
  -> nested request metadata decoded and record schema validated
  -> HomeMaster field filters
  -> model-visible records and raw IDs
```

HomeMaster `fact` maps to MindMemOS `fact`. HomeMaster `procedure` maps to MindMemOS `experience`, while metadata keeps
`homemaster_memory_type=procedure` and the complete serialized `ProcedureRecord`. The model only receives raw memory IDs
that can be passed to update and delete; episodic or aggregate view IDs are not returned as structured record IDs.

MindMemOS schema add is configured with HomeMaster's `fact` and `task_experience` entity schemas. The compact extraction
prompt requires `entities` and `edges`, forbids episodic-only output, and is explicitly preserved when MindMemOS selects
the request language prompt set. Chat output defaults to 8192 tokens when the HomeMaster provider does not set a limit,
preventing truncated entity JSON.

The validated `record_json` remains authoritative after LLM extraction. A request-scoped typed projection replaces the
entity-generation parse with exactly one deterministic entity: `FactRecord` becomes `fact` with identity
`<full subject name>::<predicate>`, while `ProcedureRecord` becomes `task_experience` with its exact procedure name.
This prevents procedural-looking fact values from being reclassified by the model. Cross-identity LLM entity merge is
disabled; native exact-name resolution still handles the same identity. Tool success still requires raw memory readback
to equal the complete original record.

`EmbeddedMindMemOS` is a lifecycle and configuration adapter, not a second memory engine. It creates MindMemOS native
add/search/get/update/delete pipelines, maps HomeMaster chat and embedding providers into MindMemOS config, disables
telemetry and Kafka, and owns native pipeline cleanup. In `managed_local` mode, `ManagedNeo4jRuntime` serializes lifecycle
transitions with an asynchronously acquired `flock`, prunes stale same-node leases using PID start identity, starts the
service for the first client, and stops only after the last client exits. The owner marker is written as a start intent
before launch and promoted by that same start operation with Neo4j's `dbms.info()` server ID after readiness; stop
requires the current ID to match. An incomplete `starting` intent never grants stop ownership. A reachable service
without a valid matching HomeMaster owner marker is treated as external and is never stopped.
Feedback, dreaming and skill evolution remain outside the current structured-memory tool surface.

## Tool Contracts

All five model-visible memory tools project their full structured result into `ToolResultMessage.content`; the same payload remains in
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
# Session experience finalization

交互 Shell 持有一个轻量 `SessionFinalizer`。它不新增第二份 Session Trace，也不改变既有 Application
Trace：结束 Session 时直接读取当前 `HomeApplicationBundle.trace_path`，按 `session_id` 过滤并排除
`transport.delta`。筛选后的事件组成仅驻留内存的 `TaskTraceEnvelope`，再由 renderer 选择用户输入、
模型思考、非空助手回复以及工具成功/失败结果，转换为带角色的 `DialogueMessage` 后交给应用持有的原生
`vanilla_add` pipeline。Transport、usage、内部 ID 和重复终态不会进入模型；完整原始轨迹只保留在
`runtime_events.jsonl`。

`EmbeddedMindMemOS` 同时持有既有 `schema_add` 和新增 `vanilla_add`；typed `mindmemos_add` 继续走 Schema
Add，Session 经验只走 Vanilla Add。两者共享 application-owned Qdrant、Neo4j、reader、writer、recorder、
LLM 和 embedding client。Job ID 由 session ID、精选消息的 SHA-256 和 extractor version 组成；已完成
Job 不重复提交。第一版不提供后台 outbox、跨 Application Trace 合并或严格 exactly-once。

原生 Vanilla experience 没有 typed Schema Add 的 `record_json`。`mindmemos_search` 仅对
`mem_extract_type=vanilla`、`mem_type=experience`、`status=active` 且正文非空的记录建立公开投影，并沿用既有
`procedure` 类型返回正文和来源 Session；其他损坏或缺失 `record_json` 的 Schema 记录仍 fail closed。
