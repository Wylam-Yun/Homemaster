# V2.6 Memory System Architecture

Home composition owns `FileMemoryStore`, `FrozenMemoryContextService`, `MemoryEvidenceLedger`, optional
`ManagedNeo4jRuntime`, `EmbeddedMindMemOS`, and `MemoryAddQueue`. They start before the first run, enter
`application_services`, and close in reverse ownership order. In managed mode the order is Neo4j, MindMemOS, then the
Add queue; shutdown seals and drains the queue before MindMemOS close and Neo4j lease release. Tools resolve them only
from `ToolExecutionContext.services`; there is no backend selector or process-global HomeMaster store.

## Ownership

```text
SOUL / USER / MEMORY -> FileMemoryStore -> memory.data_root/files
evidence refs         -> MemoryEvidenceLedger -> memory.data_root/evidence.sqlite3
fact / procedure      -> MemoryAddQueue (single FIFO worker)
                      -> EmbeddedMindMemOS -> MindMemOS native pipelines
                                           -> local Qdrant + configured Neo4j
Add job audit         -> memory.data_root/mindmemos/add_jobs.jsonl
feedback context      -> RunContext.deps -> frozen provider-attempt snapshot
dreaming watermark   -> memory.data_root/mindmemos/dreaming_state
managed local Neo4j   -> ManagedNeo4jRuntime -> file lock + per-process leases
```

SOUL/USER/MEMORY remain file-owned. `MemoryMigrationCoordinator` only publishes legacy file memory and verifies the
existing Evidence database. It never reads or migrates old mem0 Qdrant/history data. `memory.root` is the only accepted
legacy field; `memory.mem0` is rejected. `doctor` uses read-only `inspect()` and never opens MindMemOS.

## Structured Flow

```text
new Session / first user turn after completed Compact
  -> SessionRuntime.require_recall generation-fenced latch
  -> ApplicationRuntime builds deterministic query
  -> EmbeddedMindMemOS.search(top_k=3, vanilla, rerank=false, filters=None)
  -> run-scoped ContextAssembler memory prelude
  -> first Provider request; no tool messages or persistent history mutation

canonical user turn / verified tool result
  -> MemoryEvidenceLedger records ordered scope evidence (not model-visible)
  -> mindmemos_add validates FactRecord or ProcedureRecord against current scope
  -> enqueue immutable record/provenance/request context; return accepted + job_id
  -> one application-owned FIFO worker (eventual visibility)
  -> deterministic TextMessage + metadata(record_json, provenance_seq, homemaster_memory_type)
  -> EmbeddedMindMemOS.add_record()
  -> MindMemOS schema_add.add_sync()
  -> raw memory readback by persistent ID
  -> completed|failed job JSONL event

mindmemos_search
  -> EmbeddedMindMemOS.search()
  -> MindMemOS search_pipeline.search()
  -> raw memory read by each returned ID
  -> nested request metadata decoded and record schema validated
  -> HomeMaster field filters
  -> model-visible records and raw IDs

mindmemos_update
  -> read exact raw ID and inspect request metadata
  -> valid record_json: validate complete record/evidence/identity/provenance
     -> deterministic native DB plan writes new memory + updated entity/vectors/graph
     -> archive old memory + new-[:DERIVED_FROM]->old + raw/lineage readback
  -> no record_json: native DefaultUpdatePipeline updates content/vector in place + raw readback
  -> present but invalid record_json: fail closed without mutation

mindmemos_history
  -> traverse the DERIVED_FROM component from one exact ID in Neo4j
  -> read every version from Qdrant and return newest first

successful provider attempt selects mindmemos_feedback
  -> bind the exact frozen provider messages to that tool-call ID
  -> include automatic recall and only still-visible manual search results
  -> re-read every recalled raw ID and verify scope/status/content
  -> native explicit feedback plans add/update/delete/noop
  -> structured update must carry a complete replacement_record
  -> HomeMaster validates type/identity/source and derives content from that record
  -> deterministic versioned writer updates record_json/content/vectors/graph together
  -> per-action Qdrant status/content/full-record and Neo4j lineage readback

SessionFinalizer
  -> wait until the structured Add FIFO is idle
  -> vanilla add and raw readback
  -> operation-record implicit feedback and per-action readback
  -> register only confirmed ordinary add IDs in the persistent watermark
  -> threshold/pending batch invokes native dreaming with session_id=None
  -> per-action raw/lineage and add-record consolidation readback
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
disabled; native exact-name resolution still handles the same identity. Worker completion requires raw memory readback
to equal the complete original record.

`EmbeddedMindMemOS` is a lifecycle and configuration adapter, not a second memory engine. It creates MindMemOS native
add/search/get/update/delete/feedback/dreaming pipelines, maps HomeMaster chat and embedding providers into MindMemOS config, disables
telemetry and Kafka, and owns native pipeline cleanup. In `managed_local` mode, `ManagedNeo4jRuntime` serializes lifecycle
transitions with an asynchronously acquired `flock`, prunes stale same-node leases using PID start identity, starts the
service for the first client, and stops only after the last client exits. The owner marker is written as a start intent
before launch and promoted by that same start operation with Neo4j's `dbms.info()` server ID after readiness; stop
requires the current ID to match. An incomplete `starting` intent never grants stop ownership. A reachable service
without a valid matching HomeMaster owner marker is treated as external and is never stopped. Feedback and dreaming
reuse the same application-owned resources; HomeMaster does not add Kafka, HTTP self-calls or a second database. Skill
evolution remains out of scope.

`MemoryAddQueue` changes only the public structured Add timing contract. Admission occurs after permission, record and
scope-evidence validation; it returns `accepted`, an opaque `job_id`, no `memory_id`, and
`verified_terminal_state=false` (`status=success, domain_status=accepted` in the generic provider envelope). One worker
runs jobs FIFO with no concurrency and no retry. A failed job is terminal and
does not stop later jobs. Clean application shutdown drains accepted jobs before closing database resources, while
crash, power loss and `SIGKILL` may lose process-local work. Update/delete/feedback/history/search remain synchronous.

## Tool Contracts

All seven model-visible memory tools project their full structured result into `ToolResultMessage.content`; the same payload remains in
`data` for internal consumers. Provider transports serialize `content`, so IDs, records, receipts and typed errors must
not exist only in metadata.

`mindmemos_feedback` accepts only a non-empty `feedback` string. Its recalled IDs are trusted only when they came from
the successful provider attempt's frozen automatic/manual recall projection; aggregate schema IDs, free-text IDs,
compacted search results, wrong-scope records, archived records and changed content fail closed before the backend runs.
For a structured target, the planner must return the complete corrected `replacement_record`; missing or invalid records,
type/identity/source changes, and free-text-only updates fail closed. HomeMaster ignores planner-authored display text,
derives canonical content from the replacement record, then uses the same deterministic versioned writer as direct
structured update. Success requires the old raw memory archived, the new raw memory active, exact `record_json`, exact
generated content, and `new-[:DERIVED_FROM]->old`. Any failed action makes the whole tool result an error.

Opaque evidence refs remain only in the application-owned ledger and internal audit state. They are absent from
provider messages and the public add/update schemas. Executors select evidence by exact tenant/session/run/turn and
record source; therefore an old run's ref cannot be copied into a new mutation.

Tool schemas and runtime validation use the same Pydantic models. Mimo may encode the nested record as a JSON object
string; the boundary decodes only an object and then performs the unchanged discriminated `FactRecord | ProcedureRecord`
validation. Arbitrary text, arrays and invalid records remain rejected.

Updates preserve evidence ordering. `record_json` is the authoritative mode discriminator: valid structured memories
receive a deterministic versioned DB plan, absent `record_json` uses native in-place content update, and malformed
`record_json` fails closed. Structured success requires the old raw memory archived, the new raw memory active with the
exact replacement record, and `new-[:DERIVED_FROM]->old`; Vanilla success requires exact same-ID content readback.
`mindmemos_history` exposes only real lineage and never infers missing pre-upgrade links.

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
Job 不重复提交。`job.json` schema v2 分别记录 `add`、`implicit_feedback`、`dreaming_counter` 和 `dreaming`
阶段；失败重试从未完成阶段继续，不会重复已确认的 Vanilla Add。

Implicit feedback 只从同一 project/user 的 MindMemOS add/search operation records 重建 session rounds，不从
finalizer 直接接收 messages 或 feedback 文本。成功 action 必须逐条回读；失败的 add record 不会被标成
`feedback_processed`。

`DreamingStateStore` 以 `sha256(project_id NUL user_id)` 为 scope 文件名，使用 `flock`、临时文件、文件
`fsync`、原子替换和目录 `fsync`。默认累计 8 条已回读 active 的普通 session add memory 后锁定一个 batch；
执行期间的新 arrival 留给下一批。只有 typed `actions` 或真实 `no_action` 且所有 raw/lineage/add-record
终态通过时才消费 batch；provider、解析、DB、timeout 或进程崩溃都保留 pending，启动或下一次 finalization
重试。

原生 Vanilla experience 没有 typed Schema Add 的 `record_json`。`mindmemos_search` 仅对
`mem_extract_type=vanilla`、`mem_type=experience`、`status=active` 且正文非空的记录建立公开投影，并沿用既有
`procedure` 类型返回正文和来源 Session；其他损坏或缺失 `record_json` 的 Schema 记录仍 fail closed。
