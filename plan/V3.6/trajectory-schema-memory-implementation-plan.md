# V3.6 Trajectory Schema Memory Implementation Plan

> **For agentic workers:** Implement this plan task-by-task. Keep the checkbox steps up to date, run the stated verification after each task, and do not replace the existing MindMemOS schema planner/writer with a second Homemaster-specific persistence path.

日期：2026-09-17。状态：只完成设计与实施计划，以下实施任务均未执行。使用 `executing-plans` 按序独立实施，不派子代理。代码核对基线：`67c18645d1b46cc715639328441e8e28aa81a4fc`。

**Goal:** Replace the session finalizer's vanilla experience write with one episode-level MindMemOS schema-add flow that independently extracts `object_location`, `search_observation`, and `task_procedure`, while preserving failure lessons separately from externally verified reusable procedure steps.

**Architecture:** Homemaster deterministically collects and normalizes one session trace, pairs tool calls with results, and submits one complete episode. MindMemOS runs three fixed-schema LLM extractors against that same episode, merges their raw entities/edges, then invokes the existing `SchemaAddPlanner.build_write_plan()` and mutation writer exactly once. `object_location` and `search_observation` map to native `fact`; `task_procedure` maps to native `experience`, with the domain type retained in schema metadata.

**Tech Stack:** Python 3.11 project runtime (`.runtime/venv`), Pydantic models, embedded MindMemOS schema add pipeline, Qdrant, Neo4j, pytest/pytest-asyncio, JSONL trace files, and the existing HomeMaster job/finalizer framework.

---

## Scope And Invariants

### 已核对的现状与新增接口

- `SessionFinalizer.finalize()` 目前调用 `add_vanilla()`；`_verified_active_adds()` 只检查 add 事件的 active 状态，不能覆盖 schema merge/update。
- `SchemaAddExtractor.extract_episode()` 目前只有 `conversation_text` 和 `dialogue_timestamp` 两个参数；实际 `_generate_episode_memory()` 直接使用 `select_schema()/extract_memory()/prepare_raw_memory()`。仅扩展 `extract_episode()` 不会改变生产路径。
- `AddPipelineSyncResult` 当前只有 `status/memories`，位于 `third_party/MindMemOS/src/mindmemos/mindmemos/typing/service.py`；per-type 结果不能凭空从此 DTO 读取。
- `_execute_episode_task_inner()` 重试耗尽会返回 `[]`，而 `add_sync()` 默认构造 `status="ok"`。V3.6 必须显式传播抽取/写入失败，不能将其视为三类均未检测到。
- `force_generation=True` 是强制 drain，不是禁止 chunking。需为完整 episode 建立明确边界，不允许一次 finalization 被切成多个领域写入或与其他 session 合并。
- 下文 `extract_fixed_episode()`、`add_schema_episode()` 和 `schema_episode` 结果字段均为本计划拟新增接口，不是 checkout 已有 API。

### 实现选择（已选路线）

| 方案 | 代价与取舍 | 决策 |
| --- | --- | --- |
| 在现有 schema pipeline 中增加固定抽取配置和完整 episode 边界 | 需补 DTO/buffer 回归；复用全部 planner/writer | 采用 |
| 三次现有 add 调用 | 改动表面少，但三次 job/episode/写入破坏一致性 | 不采用 |
| Homemaster 自建抽取与存储 pipeline | 代码和维护成本高，重复实现 merge/writer | 不采用 |

### 执行进度

当前：spec 与本计划已编写；下一步为 Task 0 基线测试。尚未运行实现测试、真实 LLM、数据库或家庭任务验收。依赖顺序：Task 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10。跨库幂等和真实任务终态证据是必须验证的工程条件，不是假定已具备的能力；具体过程状态在执行时同步 `docs/session-handoff.md`。

The implementation must preserve these invariants:

1. One finalized session produces one top-level add job and one MindMemOS add record.
2. All three fixed extractors receive the same normalized paired episode. They are not a mutually exclusive classifier and there is no semantic router choosing one type.
3. An extractor returning empty is `not_detected`, not an error.
4. A detected type may contain zero, one, or multiple candidates. Per-type errors are retained in the aggregate result.
5. The three candidate sets are merged before one planner call and one writer mutation.
6. Tool arguments and results are copied from the trace; the LLM may summarize them but may not invent them.
7. `task_procedure.is_executable` is constrained by deterministic evidence validation. An LLM `true` is not sufficient.
8. `ProcedureRecord` remains the existing successful-path executable SOP model. V3.6 episode memories may contain failure, partial, or unknown outcomes and must not be silently promoted to `ProcedureRecord`.
9. Existing `episodes` schema configuration stays available to the native pipeline, but V3.6 extractors do not generate episode entities.
10. Job completion means the external schema add and required raw-memory readback succeeded, not merely that a local trace or log was written.

## File Map

Files expected to change:

- `src/homemaster/experience/finalizer.py`: submit one normalized schema episode, persist per-type add results, preserve retry/idempotency behavior.
- `src/homemaster/experience/schema_episode.py`: deterministic trace envelope, tool-call/result pairing, provenance serialization, and procedure executability evidence projection.
- `src/homemaster/memory/mindmemos_runtime.py`: expose one `add_schema_episode(...)` adapter using the existing native schema add pipeline and return a recorded add receipt.
- `src/homemaster/memory/mindmemos_entity_modeling.json`: add the three domain schema definitions while retaining `fact`, `task_experience`, and `episodes` compatibility.
- `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/schema_extractor.py`: add fixed-schema extraction without schema selection and expose per-type empty/candidate results.
- `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/schema_normalizer.py`: reject wrong entity types for a fixed extractor instead of silently falling back to another schema type.
- `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/_schema_utils.py`: keep domain-to-native memory type mapping explicit and tested.
- `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/add/schema/schema_add.py`: orchestrate the three fixed extractors, merge raw outputs, and call the existing planner/writer once.
- `third_party/MindMemOS/src/mindmemos/mindmemos/typing/service.py`: add an optional, typed `schema_episode` receipt to the sync result, defaulting to absent for legacy callers.
- `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/base.py`: audit the fixed extraction contract and its implementations without forcing unrelated extractors to support HomeMaster domain types.
- `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/memory_db/schema_add_buffer_store.py`: retain scoped episode/replay identity using the existing buffer persistence boundary.
- `src/homemaster/memory/schema_episode_prompts.json`: fixed domain prompt content loaded by the existing composition layer, not inline keyword classifiers.
- `src/homemaster/memory/schema_episode_validation.py`: validate candidate evidence before planning; never summarize locations or infer failure causes in rules.
- `pyproject.toml`: include the new prompt JSON in package data and verify it is available in an installed package.
- `src/homemaster/memory/automatic_recall.py`: preserve and render domain type/outcome metadata so consumers can distinguish observations, procedures, and failure lessons.
- `tests/homemaster/experience/test_finalizer.py`: replace vanilla-add fakes with the episode schema-add contract and test retries/idempotency.
- `tests/homemaster/experience/test_schema_episode.py`: deterministic normalization, pairing, provenance, and executability evidence tests.
- `tests/homemaster/memory/test_mindmemos_runtime.py`: adapter contract and metadata mapping tests.
- `tests/homemaster/memory/test_automatic_recall_integration.py`: gated real Qdrant/Neo4j black-box acceptance for location, search, and procedure memories.
- `third_party/MindMemOS/tests/components/test_schema_add_extractor.py`: fixed-schema extraction and type validation tests.
- `third_party/MindMemOS/tests/workers/test_schema_add_episode.py`: one planner/writer call for three extractor outputs, empty handling, and per-type failure reporting.
- `third_party/MindMemOS/tests/components/test_schema_normalizer.py`: explicit wrong-type rejection and no fallback regression tests.

Files required for delivery documentation after implementation:

- `docs/architecture/memory-system.md`
- `docs/memory-user-guide.md`
- `README.md`
- `CHANGELOG.md`
- `docs/session-handoff.md`

## Task 0: Capture The Baseline And Contracts

**Files:**
- Test: `tests/homemaster/experience/test_schema_episode.py`
- Test: `tests/homemaster/experience/test_finalizer.py`
- Test: `tests/homemaster/memory/test_mindmemos_runtime.py`

- [ ] Record the current test baseline from the remote checkout:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest tests/homemaster/experience/test_finalizer.py tests/homemaster/memory/test_mindmemos_runtime.py -q
```

Expected: the pre-change focused suite passes; record the count in the implementation notes without changing unrelated failures.

- [ ] Add contract fixtures for one episode containing: a failed inspection, a successful search, a placement action, exact tool arguments, exact tool results, event IDs, and timestamps.

- [ ] Add assertions for the required output shape before implementation: one normalized episode, three named extractor slots, per-type `not_detected` support, and `task_procedure` fields `outcome`, `is_executable`, `steps`, `reusable_steps`, and `failure_lesson`.

- [ ] Run only the new contract tests and confirm they fail for the missing schema-episode implementation. Do not weaken the assertions to make the baseline pass.

## Task 1: Define The Domain Schema And Native Mapping

**Files:**
- Modify: `src/homemaster/memory/mindmemos_entity_modeling.json`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/_schema_utils.py`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/schema_normalizer.py`
- Test: `tests/homemaster/memory/test_mindmemos_runtime.py`
- Test: `third_party/MindMemOS/tests/components/test_schema_normalizer.py`

- [ ] Add explicit schema entries for `object_location`, `search_observation`, and `task_procedure`.

- [ ] Use the native `entities/edges` shape, not the spec's domain JSON as a replacement pipeline format. For each domain entity use `description=summary` and one `properties` entry named `episode_record`, whose string `value` is valid JSON encoding the complete domain record (`type`, `source`, and all domain fields). Keep `outcome_evidence_event_ids` in that record. This provides one self-contained recall unit; do not spread one procedure across separate step/lesson memories. Use `json.dumps/json.loads`, never `str(dict)`.
- [ ] Add round-trip tests through the existing planner: the `episode_record` remains parseable with unchanged arguments, results, event IDs, and execution flag. Keep observations differentiated by object, time and source; new locations retain old evidence/history. Prevent generic property text merging from rewriting structured records into prose or treating failures as permanent facts.

  `object_location` must describe object identity, relation, location, observation kind, optional position/frame/unit/source, summary, provenance, and observed time. `search_observation` must describe object, searched location, `found`/`not_found`/`blocked`/`error`, action evidence, summary, and provenance. `task_procedure` must describe task/target, outcome, complete steps, optional failure lesson, reusable step indices, and summary.

- [ ] Keep `episodes` in the schema file and keep it excluded by `strip_for_generation()` for this flow.

- [ ] Make the native mapping explicit and test it:

```python
assert schema_memory_type("object_location") == "fact"
assert schema_memory_type("search_observation") == "fact"
assert schema_memory_type("task_procedure") == "experience"
assert schema_memory_type("episodes") == "episodic"
```

The domain type must remain in the entity/property metadata used by HomeMaster and in the generated memory content; native `mem_type` alone is not enough to identify the domain type.

- [ ] Change fixed-schema normalization so an entity emitted by an extractor for type `T` is rejected when its `entity_type` is not exactly `T`. Map native `mem_type` only during planning. Do not use the current “first valid non-episodes type” fallback for fixed extractors.

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_mindmemos_runtime.py third_party/MindMemOS/tests/components/test_schema_normalizer.py -q
```

Expected: schema parsing, mapping, and wrong-type rejection pass; the existing native `fact/task_experience/episodes` behavior remains covered.

## Task 2: Implement Deterministic Trace Normalization And Pairing

**Files:**
- Create: `src/homemaster/experience/schema_episode.py`
- Modify: `src/homemaster/experience/finalizer.py`
- Test: `tests/homemaster/experience/test_schema_episode.py`

- [ ] Define a normalized event representation that retains `event_id`, `session_id`, `timestamp`, event type, tool name, `tool_call_id`, arguments, raw result, status, and source event IDs. Preserve event order by timestamp plus original trace order; do not sort by an unstable set/map traversal.

- [ ] Pair by `(session_id, run_id, tool_call_id)` without adjacency guesses. Current completed/failed events may already include `payload.args` and `payload.result`; started events may include `payload.arguments`. Preserve both when present and reject conflicting arguments. An unmatched result carrying its own real args remains usable evidence, but do not fabricate a missing started event. A call without a result retains `status=null,result=null` and cannot prove success. Repeated IDs or conflicting duplicate results produce explicit diagnostics, not silent overwrite.

- [ ] Exclude `transport.delta`, internal model/thinking metadata, and unrelated debug events from actionable tool evidence while preserving user text, assistant task intent, tool calls, tool results, and explicit task completion/failure messages.

- [ ] Serialize the normalized episode deterministically for hashing. Use stable JSON key ordering, explicit UTF-8 encoding, and the existing session/input/extractor-version job identity scheme. Freeze the trace boundary at finalization; exclude newly emitted memory/feedback events and wall-clock `ended_at` from the hash. Include schema/prompt version in the extractor version. Preserve trace order for missing timestamps; legacy missing event IDs receive deterministic trace-offset references explicitly labeled as synthesized.

- [ ] Add paired examples for:

```text
call -> success result
call -> failed result
call with no result
result with no call
failed attempt -> corrective call -> final success
```

- [ ] Assert structured arguments/results are JSON-value equal after round-trip, textual payload values are unchanged, and event IDs are present on every step. Canonical key ordering need not preserve original JSON whitespace.

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest tests/homemaster/experience/test_schema_episode.py -q
```

Expected: deterministic normalization and pairing pass without any LLM call.

## Task 3: Add Three Fixed-Schema LLM Extractors

**Files:**
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/schema_extractor.py`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/schema_normalizer.py`
- Test: `third_party/MindMemOS/tests/components/test_schema_add_extractor.py`

- [ ] Extend the existing `SchemaAddExtractor` path with a fixed-schema extraction operation that accepts a required domain type and does not call `select_schema()`.

The operation must use the existing LLM client, prompt set, JSON parser, retry loop, and normalizer. Its behavior is:

```python
await extractor.extract_fixed_episode(
    entity_type="object_location",
    conversation_text=paired_episode_text,
    dialogue_timestamp=dialogue_timestamp,
    provenance=provenance,
)
```

It returns a normalized raw-memory dictionary plus an explicit `status` of `completed` or `not_detected`; LLM output with no candidates is not an exception.

- [ ] Store three fixed prompt instructions in `src/homemaster/memory/schema_episode_prompts.json`, loaded by `EmbeddedMindMemOS.start()` into the existing prompt configuration. Do not replace `build_mindmemos_add_prompts()` globally: its mandatory `fact/task_experience` output is still needed by `add_record()`. Each domain prompt must require:

  - empty `entities`/`edges` when the type is not evidenced;
  - exact real tool arguments/results and event IDs;
  - summary grounded in the structured fields;
  - no guessed coordinates, permanent location claims, or failure causes;
  - `task_procedure` outcome and failure/recovery semantics from the spec;
  - explicit domain `entity_type` and no cross-type entities.

- [ ] Validate the returned entity type before normalizer fallback can run. A wrong type is a fixed-extractor failure, not a conversion to `fact`.

- [ ] Fixed extraction must also distinguish malformed JSON from a valid empty object. The existing `extract_memory()` converts non-dict output to empty and can return the last invalid response after three attempts; add a strict fixed-schema path that raises after exhausted validation retries. Keep ordinary schema callers backward compatible. Validate all domain fields and evidence references before calling `prepare_raw_memory()`.

- [ ] Test with a fake LLM response that returns all three types from the same episode. Assert that each fixed extractor only accepts its own type, that an empty response is `not_detected`, and that invalid cross-type output is rejected.

- [ ] Test the four procedure outcomes and the recovery case:

```text
success -> executable candidate allowed for later external validation
failure -> complete failed prefix plus evidence-backed failure_lesson
partial -> completed prefix plus failure/blocked position, not executable
unknown -> observed actions only, no inferred lesson, not executable
failure + correction + final success -> lesson retained, reusable_steps limited to verified final path
```

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest third_party/MindMemOS/tests/components/test_schema_add_extractor.py -q
```

Expected: all extractor tests pass, including no-router and empty-result behavior.

## Task 4: Orchestrate One Episode And One Planner/Writer Mutation

**Files:**
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/add/schema/schema_add.py`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/typing/service.py`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/memory_db/schema_add_buffer_store.py`
- Test: `third_party/MindMemOS/tests/workers/test_schema_add_episode.py`
- Test: `third_party/MindMemOS/tests/workers/test_schema_add_drain.py`

- [ ] Extend `_SchemaAddRuntime` with the fixed extractor configuration or a fixed-extractor factory that reuses its existing LLM client, project entity manager, prompt set, and normalizer.

- [ ] Recognize validated metadata `ingress="homemaster_schema_episode_v1"` on this adapter's request only. Preserve the serialized episode as one buffer unit, keyed by tenant/session/request, and bypass semantic chunk splitting for that unit. Do not change ordinary `add()` or async callers. Oversized input must fail explicitly before LLM invocation rather than silently truncate actions or split into independently written episodes.

- [ ] In `_generate_episode_memory()`, run the three fixed extractor calls against the same `conversation_text`, `dialogue_timestamp`, and provenance. Use the existing `asyncio.TaskGroup` pattern so a real extractor exception cancels sibling calls and enters the existing episode retry path; a normal `not_detected` result does not cancel anything.

- [ ] Merge non-empty raw entities and edges from all three results. Add the existing one episode entity only once. Preserve each candidate's domain type and provenance metadata during merge.

- [ ] Treat extractor exceptions and fixed-schema validation errors as episode-level add failures before the planner is called. Do not write a partial set from two successful extractors while the third failed; retry the same normalized episode and job. Only a normal empty result is a successful `not_detected` type.

- [ ] Apply the HomeMaster evidence validator as a composition-supplied callback after extraction and before the shared planner. Keep its code in `schema_episode_validation.py`, not a reverse import of HomeMaster from MindMemOS. Validate event existence, exact args/results, coordinate source, and trusted goal evidence; write the sanitized executable flag and reusable indices into `episode_record` before it can be persisted.

- [ ] Call exactly one existing planner method:

```python
plan, events, pending_archives, pending_updates = await rt.planner.build_write_plan(
    raw_entities=merged_entities,
    raw_edges=merged_edges,
    episode_entity=episode_entity,
    context=episode_context,
    request_metadata=add_record_ops.metadata(records),
    created_at=added_at,
    episode_time=dialogue_timestamp,
    prompt_set=request_prompts,
)
```

- [ ] Build the existing `MemoryDbMutationPlan` and call `db_writer.apply_mutation_plan(...)` exactly once for the episode. Do not call the top-level add pipeline three times and do not add a second writer.

- [ ] Return an aggregate result containing per-type status, candidate count, native memory IDs, extractor errors, procedure outcome counts, and the count of candidates eligible for external executability validation. The aggregate result must distinguish `not_detected` from `failed`.

- [ ] Extend `AddPipelineSyncResult` with optional `schema_episode` receipt containing `episode_id`, `types`, and `write_status`. Each type carries `status`, `candidate_count`, `memory_ids`, `memory_count`, and `error`; procedures also carry `outcome_counts` and final `executable_count`. Thread this through the episode/drain return and persisted operation record, not a shared mutable `last_result`. Keep the legacy `memories` list unchanged. A failed fixed episode must raise or return an error through `add_sync()`, never fall through `_execute_episode_task_inner()` to an `ok` empty list. Include cancelled sibling status on extraction failure.
- [ ] Preserve native objectification/episode description calls and document them as existing LLM calls in addition to the three domain calls. Preserve configured native merge/search-field LLM stages. “Three extractors” does not mean exactly three total LLM requests.
- [ ] Before mutation, persist the assigned episode/write identities and replayable plan using the existing add/buffer record boundaries; protect the same request from concurrent writers. A single writer invocation is not a Qdrant/Neo4j distributed transaction. On crash, read each store and replay the same plan/IDs instead of re-extracting and minting new ones. Verify planner random IDs, updates and archives in the replay tests; never claim crash idempotency from stable job ID alone.

- [ ] Add instrumentation around extractor start/end, planner invocation, writer invocation, returned status, and elapsed time as structured JSON fields. Do not use instrumentation as the success criterion; the writer return status and later readback remain authoritative.

- [ ] Add tests that count calls to the planner and writer. For an episode producing all three types, assert planner call count `== 1` and writer mutation call count `== 1`. For an all-empty episode, assert no actionable entities are written and the add result is `not_detected` rather than failed.

All-empty means top-level native `status="ok"`, all three per-type statuses `not_detected`, and zero domain memories; a native episode audit record is allowed. Single-call counts apply to the first successful attempt; crash replay may invoke the same writer plan again without duplicate external records.

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest third_party/MindMemOS/tests/workers/test_schema_add_episode.py third_party/MindMemOS/tests/workers/test_schema_add_drain.py -q
```

Expected: one-episode orchestration, retries, empty handling, and single-write behavior pass.

## Task 5: Expose The Schema Episode Adapter

**Files:**
- Modify: `src/homemaster/memory/mindmemos_runtime.py`
- Test: `tests/homemaster/memory/test_mindmemos_runtime.py`

- [ ] Add `EmbeddedMindMemOS.add_schema_episode(...)` beside the existing `add()` and `add_vanilla()` methods. It must construct the existing `AddPipelineInput`, call `self._add_pipeline.add_sync(...)`, record the add input through the existing recorder, and return a `RecordedAddResult`-compatible receipt.

Proposed adapter contract (implemented in this task): `add_schema_episode(episode: dict[str, Any], context: Any, *, metadata: dict[str, Any]) -> RecordedAddResult`. Encode one canonical JSON episode as one native `TextMessage`, carry ingress/version metadata, and return `RecordedAddResult(add_record_id, result)` where `result.schema_episode` is defined in Task 4. Derive the add record ID deterministically from tenant + job request ID, persist it before submission, and read an existing successful receipt on restart. Do not copy `uuid4()`-per-retry behavior from `add_vanilla()`.

- [ ] Keep `add_vanilla()` unchanged for existing callers outside V3.6. The finalizer is the only caller migrated to `add_schema_episode()`.

- [ ] Pass session ID, input hash, extractor version, ingress name, and domain-schema version in request metadata. Keep metadata serializable and ensure it survives the native add record path.

- [ ] Bind the Task 4 pre-planner evidence callback in the adapter. It does not reclassify memories or infer semantic success from keywords. Accept only already-authoritative environment goal feedback or an existing structured terminal check tied to this task; an assistant reply, `runtime.turn_completed`, or tool dispatch success alone is insufficient. If this checkout's trace does not expose such evidence for a backend, preserve the experience with `is_executable=false`, `reusable_steps=[]`, and explicitly record the missing verifier support. Do not invent a generic rule-based task evaluator.

- [ ] Verify the adapter's returned status, add record ID, and event memory IDs are the same values used by finalizer readback. Test failed native add marks the add record failed and does not report success.

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_mindmemos_runtime.py -q
```

Expected: adapter receipt, metadata, native failure propagation, and readback-facing IDs pass.

## Task 6: Migrate SessionFinalizer And Preserve Job Semantics

**Files:**
- Modify: `src/homemaster/experience/finalizer.py`
- Modify: `src/homemaster/experience/__init__.py`
- Modify: `tests/homemaster/experience/test_finalizer.py`
- Modify: `tests/homemaster/experience/test_session_finalization.py`

- [ ] Replace the finalizer's `messages = _render_messages(...); add_vanilla(messages, ...)` call with one normalized episode submission to `add_schema_episode(...)`. Keep `_render_messages()` only if an existing non-V3.6 caller still needs it; do not send a second vanilla add for the same episode.

- [ ] Include the normalized paired episode and provenance in the schema-add input. The finalizer remains responsible for collection, stable hashing, job identity, phase transitions, and retry state; it does not classify memory types or summarize locations/procedures.

- [ ] Extend the `add` job phase with:

```json
{
  "status": "completed",
  "ingress": "episode",
  "algorithm": "schema_add_v1",
  "types": {
    "object_location": {"status": "completed", "memory_count": 1},
    "search_observation": {"status": "not_detected", "memory_count": 0},
    "task_procedure": {"status": "completed", "memory_count": 1, "executable_count": 1}
  },
  "add_record_id": "add-v36-example",
  "active_memory_ids": ["location-example", "procedure-example"]
}
```

- [ ] Keep finalizer completion dependent on native add success and `_verified_active_adds()` readback. Add per-type memory IDs to the readback set so a partial native response cannot be reported as complete.

- [ ] Extend the existing readback beyond `operation == "add"`: resolve update/merge final IDs, verify expected archived predecessors and lineage when returned, and check domain record contents, not only `active`. Resume a committed native add whose local job receipt was not saved by using the stable add ID. Do not repeat successful extraction or writes when only feedback/dreaming remains. Legacy `experience-v2` jobs stay unchanged; new jobs use a new version.

- [ ] Preserve phase retry behavior: if schema add fails, the next finalization retries the same job/input and does not create a second successful add; if add succeeds but implicit feedback or dreaming fails, their existing phases retry without repeating the add.

- [ ] Update the fake MindMemOS in `test_finalizer.py` to expose `add_schema_episode()`, return per-type aggregate results, and record invocation count. Add tests for one call, same job ID on retry, not-detected types, per-type failure metadata, and no duplicate add after downstream phase failure.

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest tests/homemaster/experience/test_finalizer.py tests/homemaster/experience/test_session_finalization.py -q
```

Expected: finalization, failure recovery, and idempotency pass with one episode-level add.

## Task 7: Preserve Domain Metadata Through Recall

**Files:**
- Modify: `src/homemaster/memory/automatic_recall.py`
- Modify: `src/homemaster/tools/memory_tools.py` only where the existing memory search/add projections would otherwise hide the domain type or executable flag.
- Test: `tests/homemaster/memory/test_automatic_recall.py`
- Test: `tests/homemaster/memory/test_automatic_recall_integration.py`

- [ ] Confirm the schema search result model exposes native `memory_type`, content, event/source timestamp, and metadata/lineage fields. Preserve domain type, outcome, and `is_executable` in the serialized memory content or metadata used by recall.

Current production automatic recall in `src/homemaster/application/runtime.py` and explicit memory search in `src/homemaster/tools/memory_tools.py` use `search_pipeline="vanilla"`. Keep this choice; do not switch production recall to schema search to make tests pass. The self-contained `episode_record` content must survive vanilla search. Test schema search additionally, but it is not a substitute for the actual consumer path.

- [ ] Update `build_automatic_recall_context()` only as needed to render these fields without treating recalled memory as an instruction. A recalled failure lesson must be visibly distinguishable from a reusable procedure.

- [ ] Add a unit-level recall rendering test with one location fact, one not-found search observation, one failed procedure, and one verified reusable procedure. Assert all four remain distinguishable after serialization.

- [ ] Run the focused recall tests without external services first:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_automatic_recall.py -q
```

Expected: recall rendering unit tests pass. Real integration remains in its separate gated file.

## Task 8: Add Contract And Regression Coverage

**Files:**
- Modify: `tests/homemaster/experience/test_schema_episode.py`
- Modify: `tests/homemaster/experience/test_finalizer.py`
- Modify: `tests/homemaster/memory/test_mindmemos_runtime.py`
- Modify: `third_party/MindMemOS/tests/components/test_schema_add_extractor.py`
- Modify: `third_party/MindMemOS/tests/workers/test_schema_add_episode.py`
- Modify: `third_party/MindMemOS/tests/components/test_schema_normalizer.py`

- [ ] Add per-instance assertions, not aggregate “any target passed” assertions, for:

  - each of the three fixed extractors;
  - each procedure outcome;
  - every tool step's order, arguments, result, status, and evidence IDs;
  - every detected type's write/readback state;
  - every active memory ID returned by the add result.

- [ ] Add an interface audit test for every MindMemOS implementation used by HomeMaster, asserting the public schema episode method exists and has the expected callable contract. This prevents a fake or alternate implementation from silently missing the new adapter method.

Audit at least the finalizer, session-finalization, add-queue and ALFWorld runner fakes by inspecting `tests/homemaster/experience/test_finalizer.py`, `tests/homemaster/experience/test_session_finalization.py`, `tests/homemaster/memory/test_add_queue.py`, and `tests/homemaster/benchmarking/test_alfworld_runner.py`. Update only implementations actually passed to SessionFinalizer, not unrelated file-memory stores. Add crash-injection tests after Qdrant write, after Neo4j write, after add receipt, and before local job completion; assert same external IDs and one committed domain set per request.

- [ ] Add negative tests for invented coordinates, invented tool arguments, unpaired result promotion, unknown entity-type fallback, LLM-only executable claims, and failure lessons without evidence.

- [ ] Run the complete affected unit/contract set:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m pytest \
  tests/homemaster/experience \
  tests/homemaster/memory/test_mindmemos_runtime.py \
  tests/homemaster/memory/test_models_and_evidence.py \
  third_party/MindMemOS/tests/components/test_schema_add_extractor.py \
  third_party/MindMemOS/tests/components/test_schema_normalizer.py \
  third_party/MindMemOS/tests/workers/test_schema_add_episode.py \
  third_party/MindMemOS/tests/workers/test_schema_add_drain.py -q
```

Expected: all affected tests pass, with only explicitly environment-gated real tests skipped when their flag is absent.

## Task 9: Run Real Qdrant/Neo4j Black-Box Acceptance

**Files:**
- Modify: `tests/homemaster/memory/test_automatic_recall_integration.py`
- Test data: use a unique per-run marker and the existing HomeMaster config/runtime; do not commit credentials or generated databases.

- [ ] Add a gated real test that starts the existing `ManagedNeo4jRuntime` and `EmbeddedMindMemOS`, writes one episode containing:

```text
1. inspect living_room_table -> not_found
2. inspect bedroom_drawer -> found medicine
3. put medicine -> success
```

The test must go through `add_schema_episode()` and the real native schema add path, not a mocked planner or direct database insert.

These names are semantic fixture labels, not assumed real tool APIs. Add a separate real-environment test `tests/homemaster/memory/test_schema_episode_alfworld_integration.py` using the current ALFWorld runner in `src/homemaster/benchmarking/alfworld/runner.py`, tool definitions in `src/homemaster/benchmarking/alfworld/tools.py`, and terminal feedback from `src/homemaster/benchmarking/alfworld/env_adapter.py`. Feed its actual `runtime_events.jsonl` to `SessionFinalizer`. Do not inject hidden object locations into the LLM input. A hand-written trace with real DB/LLM is a storage test only, not proof of household task improvement.

- [ ] Assert the external return status is successful and every returned memory ID passes `get_raw()` with `status == "active"` and the expected native type (`fact` for location/search, `experience` for procedure).

- [ ] Assert Neo4j contains entity/memory relationships for each detected domain type and Qdrant contains the active raw memories. The assertions must read the stores independently from the add result.

- [ ] Search with `search_pipeline="vanilla"` from a new session context and assert, per target, that:

  - the medicine location and container are returned;
  - the not-found observation is returned as an observation, not as a permanent absence fact;
  - the procedure summary contains the successful path, and a recovery lesson only when evidence supports one (`not_found` alone does not require a tool-failure lesson);
  - the executable projection contains only the externally verified reusable steps.

- [ ] Add a failure-termination case and a failure-recovery case. For failure termination, assert the failure lesson is retrievable but `is_executable=false`. For recovery, assert both the lesson and verified successful path are retrievable and that the failure step is not in `reusable_steps`.

- [ ] Verify the real external behavior separately from memory writes: use the existing task/tool harness to run a follow-up search and assert per target that the recalled location reduces the search set or tool-call count, and that the task reaches the expected terminal result. Do not use a global minimum, maximum, or “any target passed” criterion.

Freeze task IDs, scene/initial state, model configuration and the improvement criterion before running matched memory-off/memory-on trials. For each target require external goal success without regression and its predefined improvement (fewer searched places/calls or avoiding a documented failure). A task must not earn PASS merely because another target improved. Evaluation sessions must not be re-ingested by finalization. Use an isolated tenant/run root; clean only records owned by that run, require successful cleanup return status, and independently read back archived/absent state. Inspect stderr for late DB/process errors.

- [ ] Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
HOMEMASTER_RUN_REAL_AUTOMATIC_RECALL=1 \
  .runtime/venv/bin/python -m pytest tests/homemaster/memory/test_automatic_recall_integration.py -q -s
```

Expected: real Qdrant and Neo4j writes, independent readback, recall, and follow-up task behavior all pass. If the external test fails, record the actual store/status/identifier evidence before changing code.

The separate household test is gated by the new `HOMEMASTER_RUN_REAL_SCHEMA_EPISODE_ALFWORLD` variable, defined in its test file, and uses the project ALFWorld environment:

```bash
HOMEMASTER_RUN_REAL_SCHEMA_EPISODE_ALFWORLD=1 \
  .runtime/alfworld-venv/bin/python -m pytest tests/homemaster/memory/test_schema_episode_alfworld_integration.py -q -s
```

Task 0 must verify imports/config/dataset/services in that interpreter before this run. Missing resources are reported as BLOCKED, never as PASS or silent fallback to a mock. Storage acceptance and household benefit are separate gates; both are required for final delivery.

## Task 10: Documentation, Changelog, And Handoff

**Files:**
- Modify: `docs/architecture/memory-system.md`
- Modify: `docs/memory-user-guide.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/session-handoff.md`

- [ ] Update the architecture document with the exact data flow:

```text
trace -> deterministic normalization -> tool/result pairing
      -> one episode ingress
      -> three fixed LLM extractors
      -> merged raw entities/edges
      -> one native SchemaAddPlanner call
      -> one mutation writer
      -> Qdrant + Neo4j readback
```

- [ ] Document the LLM/rule boundary, no-router design, domain-to-native mapping, failure/partial/unknown semantics, and `ProcedureRecord` versus episode `task_procedure` boundary.

- [ ] Add a user-facing example showing a failed search, a successful recovery, the recalled location, and the fact that only the verified successful path is executable.

- [ ] Add a CHANGELOG entry before any implementation commit. Use the same complete sentence in the eventual commit message: “Replace session vanilla experience writes with one episode-level schema add using fixed object-location, search-observation, and task-procedure extractors; preserve failure lessons separately from externally verified reusable steps.”

- [ ] Update `docs/session-handoff.md` with the implementation commit/baseline, tests run, real Qdrant/Neo4j acceptance result, remaining environment facts, and any external failure evidence.

- [ ] Run final static/document checks:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
git diff --check
rg -n "type router|semantic router|task_procedure|reusable_steps|failure_lesson|is_executable|schema_add_v1" \
  plan/V3.6/trajectory-schema-memory-spec.md \
  plan/V3.6/trajectory-schema-memory-implementation-plan.md
```

Expected: no whitespace errors, no contradictory router language, and matching field names between spec and plan.

## Final Verification Gate

Before declaring V3.6 complete, run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.runtime/venv/bin/python -m compileall -q src third_party/MindMemOS/src
.runtime/venv/bin/python -m pytest tests/homemaster/experience tests/homemaster/memory/test_mindmemos_runtime.py -q
git diff --check
```

The completion report must include:

- changed files and the final commit SHA if implementation was committed;
- focused unit/contract test result;
- real Qdrant/Neo4j black-box result, including per-target assertions;
- external return statuses and active-memory readback evidence;
- any explicitly skipped environment-gated test and why;
- confirmation that the finalizer creates one add job, invokes one schema episode ingress, one planner, and one writer mutation per episode.
