# HomeMaster 100-Record Memory Recall Benchmark Design

Date: 2026-08-10
Status: approved for implementation planning

## Purpose

Build a repeatable benchmark that writes 100 synthetic website-operation memories through the real public HomeMaster CLI, then measures both MindMemOS retrieval quality and HomeMaster's ability to choose memory retrieval without being prompted. The benchmark uses the current configured memory store and deliberately retains the written records after evaluation. Cleanup is outside this version's scope.

This benchmark is not intended to prove that the simulated website procedures work in a real browser. Every record is stored as a user-stated `fact`, not as an environment-verified `procedure`.

## Scope and non-goals

The benchmark will:

- generate a deterministic 100-record synthetic dataset;
- invoke `python -m homemaster.cli -p` once per record, strictly serially;
- require exactly one `add_memory` operation for each write request;
- checkpoint every confirmed write and resume without rewriting successful records;
- run forced retrieval, paraphrased retrieval, distractor, and natural-routing evaluations;
- score every record independently and produce JSONL evidence plus a Markdown report.

The benchmark will not:

- write directly to Qdrant, Neo4j, or an internal executor;
- store these records as `procedure` memories or fabricate environment evidence;
- run writes concurrently;
- automatically retry an outcome-unknown mutation;
- delete benchmark records or provide a cleanup command in this version;
- claim that any synthetic button sequence is valid for a real website.

## User-facing commands

The implementation will provide one script with explicit phases:

```bash
python scripts/memory_recall_benchmark.py generate
python scripts/memory_recall_benchmark.py write --run-id <run-id>
python scripts/memory_recall_benchmark.py resume --run-id <run-id>
python scripts/memory_recall_benchmark.py evaluate --run-id <run-id>
python scripts/memory_recall_benchmark.py status --run-id <run-id>
```

`generate` creates a new run ID unless one is supplied. `write`, `resume`, `evaluate`, and `status` require an existing run ID. No command deletes data.

## Runtime artifacts

Artifacts live outside Git under a mode-0700 run directory:

```text
~/.homemaster/benchmarks/<run-id>/
  dataset.json
  checkpoint.json
  write-results.jsonl
  recall-results.jsonl
  routing-results.jsonl
  raw/
    write-0001.stdout.jsonl
    write-0001.stderr.log
    query-*.stdout.jsonl
    query-*.stderr.log
  summary.json
  report.md
```

Files containing traces use mode 0600. JSONL is append-only. `checkpoint.json`, `summary.json`, and `report.md` are published atomically through a temporary file in the same directory followed by rename.

## Dataset

The dataset contains 100 ordinary `FactRecord` values across ten fictional websites:

1. 星河商城: products, orders, refunds, and coupons;
2. 云桥邮箱: message search, attachments, and rules;
3. 北辰文档: creation, sharing, export, and version restore;
4. 灯塔工单: search, assignment, escalation, and closure;
5. 青禾 CRM: customers, contacts, opportunities, and follow-ups;
6. 天穹分析: filters, charts, reports, and CSV export;
7. 松果人事: employees, leave, attendance, and onboarding;
8. 银湾财务: invoices, expenses, reconciliation, and payment requests;
9. 远帆旅行: flights, hotels, changes, and itinerary export;
10. 云峰平台: instances, logs, alerts, and access-key pages.

The record mix is fixed:

- 70 target operation facts;
- 20 near-neighbor distractor facts;
- 10 unrelated page facts.

Each target fact has a unique subject name:

```text
HM100::<run-id>::<four-digit-index>::<site>::<goal>
```

Its predicate is the deterministic snake-case value `web_operation_steps`. Its JSON value contains:

```json
{
  "site": "星河商城",
  "page": "订单中心",
  "goal": "按订单号搜索订单",
  "steps": [
    "点击顶部的订单中心",
    "点击订单号搜索框",
    "输入完整订单号",
    "点击搜索按钮",
    "点击唯一结果的查看详情"
  ],
  "expected_result": "打开对应订单的详情页",
  "synthetic": true
}
```

All records use `source=user_statement`. Dataset generation is deterministic for a recorded seed. It does not call an LLM, so the expected records and answer keys cannot drift between runs.

Near-neighbor distractors share meaningful vocabulary with a target while differing in exactly recorded fields such as site, goal, entry page, final button, or step order. They describe separate synthetic tasks rather than intentionally false versions of a target. Unrelated distractors contain page-level facts such as help links, theme settings, or version labels.

## Write execution

For every dataset row, the script launches a fresh subprocess from the repository root:

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli \
  -p '<record-specific write instruction>' \
  --output-format stream-json
```

The instruction gives the complete fact, the current unique benchmark subject, and requires HomeMaster to call `add_memory` exactly once. It forbids search, get, update, delete, robot, browser, and observation tools for that turn. A fresh user turn supplies the legitimate `user_statement` evidence for that one fact.

Writes are strictly sequential. The target is calculated once from `dataset.json` and never regenerated during retry or resume. A write is confirmed only when the machine-readable stream contains a matching `tool_completed` event for `add_memory` whose decoded result satisfies all of these per-record conditions:

- CLI exit code is zero;
- `success` is `true`;
- `status` is `success`;
- `verified_terminal_state` is `true`;
- `backend_attempted` is `true`;
- a non-empty memory ID is returned;
- the returned record exactly round-trips to the expected subject, predicate, value, and source.

The assistant's final prose is retained but never used as write proof. The successful receipt, memory ID, elapsed time, provider-call counts, usage, and raw-output paths are appended to `write-results.jsonl` before the checkpoint advances.

## Failure and resume semantics

The default per-record subprocess timeout is ten minutes and is configurable. The script records whether an `add_memory` `tool_started` event was observed.

- Failure before `tool_started`: mark the item `safe_to_retry`, stop, and allow `resume`.
- Confirmed successful `tool_completed`: checkpoint the item and continue.
- `tool_started` without a confirmed terminal receipt, timeout after `tool_started`, `outcome_unknown`, malformed receipt, missing ID, or mismatched round-trip record: mark the item `outcome_unknown`, stop, and do not automatically retry.
- Confirmed rejected mutation with `backend_attempted=false`: record the typed error, stop, and allow an explicit later resume only when the result declares the operation safe to retry.

`resume` reads the locked dataset and checkpoint. It skips every confirmed item and never recomputes names or payloads. The script refuses to resume when the dataset hash differs from the hash stored in the checkpoint.

## Evaluation suites

Evaluation begins only after 100 confirmed writes.

### Exact forced retrieval

Run one forced `search_memories` query for each of the 100 records using its exact site and goal. The prompt explicitly requests structured-memory search. This suite measures exact addressability and duplicate behavior.

### Paraphrased forced retrieval

Run one deterministic Chinese paraphrase for each of the 70 target operation facts. The query preserves the site and goal but avoids copying the stored wording. It explicitly requests structured-memory search.

### Distractor discrimination

Run 20 contrast queries paired with the near-neighbor distractors. Each query contains the differentiating site, page, or expected result required to select the target rather than its distractor.

### Natural tool routing

Select 30 target facts using a deterministic stratified sample of three per fictional website. Ask natural questions such as:

```text
我想在星河商城按订单号找到订单详情，应该依次点什么？
```

These prompts do not mention memory, retrieval, or tool names. This suite measures whether HomeMaster calls `search_memories`, answers from the returned record, fabricates an answer, or incorrectly calls `observe` or a robot/browser tool.

Every evaluation query is a separate `homemaster -p --output-format stream-json` invocation. Evaluation failures do not stop later queries; each result remains independently visible.

## Per-instance scoring

The scorer parses tool events and returned records rather than trusting assistant prose. Every query records:

- expected memory ID and actual ranked IDs;
- whether the expected ID appears at rank 1 and within ranks 1-5;
- reciprocal rank;
- duplicate IDs or multiple records with the same benchmark subject;
- exact site, page, goal, expected-result, and ordered-step equality;
- whether a near-neighbor distractor outranked the target;
- tools called and their order;
- final-answer fidelity to the retrieved ordered steps;
- query latency and provider usage.

Natural-routing queries additionally record whether `search_memories` was called, whether `observe` or any robot/browser tool was called, and whether the final answer was grounded in the expected record.

No aggregate `any` or best-instance condition can turn failed instances into passes.

## Aggregate report

`summary.json` and `report.md` include:

- write success rate;
- Recall@1, Recall@5, and mean reciprocal rank by suite and website;
- exact ordered-step accuracy;
- distractor confusion rate;
- duplicate-record rate;
- natural memory-routing rate;
- natural final-answer accuracy;
- incorrect observe/robot/browser routing rate;
- P50 and P95 write and search latency;
- model and embedding call counts and token usage;
- a complete table of every failed or ambiguous instance.

The report separates retrieval-engine failures from agent-routing failures. A natural query that never calls memory cannot be counted as a MindMemOS retrieval miss; it is classified as a routing failure.

## Security and operational constraints

- The script never prints or serializes provider credentials or the loaded HomeMaster configuration.
- It does not call `doctor --json`, whose current provider details are unsafe to publish.
- Raw traces may contain opaque evidence references and therefore remain mode 0600.
- Only one writer subprocess runs at a time; local Qdrant and managed Neo4j are not accessed concurrently by benchmark writers.
- Records remain in the current configured memory store after evaluation. The report lists their run prefix and exact IDs so a separately designed cleanup workflow can remove them later.
- The implementation does not modify the current memory schema, public memory-tool contract, or managed Neo4j lifecycle.

## Acceptance criteria

Implementation is complete when:

1. dataset generation deterministically produces exactly 70 targets, 20 near-neighbor distractors, and 10 unrelated distractors with 100 unique subject names;
2. the write runner demonstrably invokes the real `homemaster -p` command once per attempted record and never writes concurrently;
3. checkpoint and resume behavior prevents confirmed records from being rewritten;
4. mutation ambiguity stops the run without automatic retry;
5. evaluators execute all four suites and persist per-instance evidence;
6. the scorer independently validates ranked IDs, complete record fields, and step order;
7. the report separates retrieval failures from tool-routing failures and includes all required aggregate metrics;
8. artifacts have the specified private permissions;
9. no cleanup or deletion operation exists in this version;
10. a live smoke run writes and reads back at least one benchmark record through the external CLI with confirmed return code and terminal record state before the 100-record run is handed off.
