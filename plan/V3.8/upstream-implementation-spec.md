# MindMemOS Upstream Implementation Spec (V3.8)

Repo: `/Users/wylam/Documents/workspace/MindMemOS` — branch `feature/v3.8-native-lifecycle`.
Baseline: `186db4a75122b1d8691933f280bec10191c82c28`.

This is the contract HomeMaster's workers/SDK facade will be coded against.
Implement server + SDK + tests so every route below exists with the exact
semantics described. Do not break existing endpoints or SDK signatures —
all additions are optional params / new endpoints.

## 1. Operation ledger + idempotent Add

New module `src/mindmemos/mindmemos/services/operations.py` (or extend the
existing service layer): a durable operation ledger keyed by
`operation_id` (client-supplied, e.g. `turnadd:<session>:<turn>`).

Table/collection `operations`:

| field | meaning |
|---|---|
| operation_id | unique key |
| kind | `add` / `feedback` / `dreaming` / `skill_evolution` |
| request_hash | sha256 of canonical request body (sorted JSON) |
| status | `accepted` / `processing` / `completed` / `no_action` / `failed` |
| result_json | final business result (memory_ids, add_record_id, actions…) |
| error | terminal error string |
| created_at / updated_at | |

### `POST /v1/memory/add`

- New optional body field `operation_id: str`.
- When present, first look up the ledger:
  - existing op with same `request_hash` → return stored result verbatim
    (HTTP 200, `code=ok`, plus `replayed: true` in data).
  - existing op with different `request_hash` → HTTP 409, `code=conflict`.
  - `status=processing` and lease alive → HTTP 202/`code=processing`.
- Otherwise: reserve the op (status=processing), run the add pipeline, write
  result to the ledger, return result.
- Response `data` MUST always include: `operation_id`, `add_record_id`,
  `memory_ids` (list of every memory id written — may be empty), `status`
  (`completed` or `no_action`). A zero-memory successful extraction is
  `no_action` with `memory_ids=[]`, NOT an error.
- The reservation must happen in the same DB transaction (or equivalent
  atomicity) as the AddRecord creation described in §2.

### `GET /v1/operations/{operation_id}`

Returns `{"operation_id","kind","status","result":{...}|null,"error":...,"created_at","updated_at"}`.
Unknown id → HTTP 404 `code=not_found`. HomeMaster uses this to reconcile
lost responses before retrying.

## 2. AddRecord reservation + deterministic write plan

In the add service (currently `services/` + pipeline):

1. `add_record` is created/reserved FIRST (status `processing`) carrying
   `operation_id`, `request_hash`, `extraction` and `write_plan` fields.
2. After extraction, before ANY Qdrant/Neo4j write, persist a deterministic
   write plan on the add record:
   `write_plan = {"version": 1, "actions": [{"action_id", "action" (insert|update|delete|noop),
   "target_memory_id", "collection", "content_hash", "metadata_hash"}...]}`
   — target ids are minted here once and NEVER re-minted on retry.
3. Execute writes from the plan; record per-action outcome.
4. On ledger retry with the same op: skip extraction, reuse stored plan +
   target ids; writes are idempotent upserts keyed by target id.
5. On success: add_record → `completed`, operation → `completed` with
   `memory_ids` = plan target ids actually written.
6. Extraction produced nothing: add_record → `no_action`, op → `no_action`,
   `memory_ids=[]`.
7. On failure: add_record → `failed` (keep plan for forensics), op → `failed`.
   Re-submitting the SAME operation_id+payload after failure: allowed to retry
   once more through the same reservation path (idempotent — reuse record).

## 3. Tool-message preservation (TurnGrouper fix)

`src/mindmemos/mindmemos/**/turn_grouper.py`: `tool` is in `_STANDARD_ROLES`
but the main grouping loop has no `tool` branch — tool results are silently
dropped. Fix:

- A `tool` role message joins the in-progress turn as a tool-result entry.
- Parse the canonical JSON envelope when present:
  `{"tool_call_id": ..., "name": ..., "result": ..., "status": "success"|"error"}`
  (fall back to raw content when not JSON).
- Assistant messages whose content is a JSON envelope
  `{"tool_call_id","name","arguments"}` are preserved as tool-CALL entries
  with the same `tool_call_id` (do not merge/drop them).
- Result: every `tool_call_id` appears at most once as call + once as result;
  ordering is input order; unmatched entries are kept, not dropped.
- Add tests: tool-call/result pairing, repeated ids, unmatched results,
  interleaved plain text.

## 4. `POST /v1/memory/feedback` — operation_id + window

- `operation_id` (optional): same ledger semantics as add. Response data
  includes `operation_id`, `feedback_record_id`, `actions`
  (list of `{action_id, action: add|update|delete|noop, memory_id|null}`).
- New optional `window` object for implicit mode:
  `{"since_activity_id": str|null, "window_start": iso8601|null, "window_end": iso8601|null}`
  restricting implicit candidate collection to activity inside the window.
- Explicit mode (feedback text + messages + recalled_memories) unchanged
  except `operation_id` support.

## 5. `POST /v1/memory/dreaming` — seeds

- `operation_id` (optional, ledger semantics).
- New optional `seed_add_record_ids: list[str]` — when present, the dreaming
  pipeline uses ONLY those add records as hot seeds (ignore recency-based
  collection). Empty list → `no_action` response (successful no-op).
- Response data: `operation_id`, `dreaming_record_id`,
  `produced_memory_ids`, `actions` (list of action records), `status`.

## 6. Skill Trace endpoints (new, dedicated store)

New table/collection `skill_traces` + `skill_evolution_batches`.

`POST /v1/skills/{cloud_skill_id}/traces`
body: `{invocation_id, skill_version_id, skill_content_hash, task_input,
tool_steps: [{tool_call_id, tool_name, arguments, tool_result_tail,
tool_result_truncated, status}], agent_completion, outcome (success|failure),
score (0..1)}`
- Idempotent on `invocation_id` (same id → same response).
- Validate score/outcome consistency: success ∈ [0.5,1], failure ∈ [0,0.5].
- Response data: `{"invocation_id","cloud_skill_id","status":"sealed",
  "unconsumed_count": <n>}`.

`POST /v1/skills/{cloud_skill_id}/evolution-batches`
body: `{batch_id, trace_ids: [8 invocation_ids]}`
- Idempotent on `batch_id`; verify the listed trace_ids are valid sealed
  traces for this skill (unknown/mismatched → 409).
- Runs the existing SkillEvolver against EXACTLY those traces (do NOT use
  `skill_bindings` on AddRecords as the source).
- Produces a **draft** `SKILL.md` version only — never auto-activates.
- Response data: `{batch_id, draft_version_id|null, status:
  completed|no_action, reason}`.

`GET /v1/skills/{cloud_skill_id}/traces/{invocation_id}` — readback.

## 7. Management parity (check, only fill gaps)

`/v1/memory/get|list|scroll|update|delete|search` already exist. Verify and
extend ONLY where missing:

- `update` must accept structured/typed memory ids and perform native
  version/graph update (create a new version + lineage, not delete+recreate).
  If current update is text-only, add optional body field `fields: dict` for
  structured field updates; keep `content` for plain.
- `get` response must include `history`/`version` info when available
  (needed by the web history view).
- `search` response must include `search_record_id` in data (one record per
  call) — if not present, persist a SearchRecord and return its id.
- Every mutating management call may also carry `operation_id` (optional).

## 8. SDK parity (`src/mindmemos_sdk/mindmemos_sdk/`)

Add matching params/endpoints on BOTH sync `MemoryClient` and
`AsyncMemoryClient` (and `memory/core.py` request builders):

- `add(..., operation_id=None)`; new `operation_status(operation_id)` →
  `GET /v1/operations/{id}`.
- `feedback(..., operation_id=None, window=None)`;
- `dreaming(..., operation_id=None, seed_add_record_ids=None)`;
- `update(memory_id, content=None, fields=None)`;
- `SkillCloudClient.submit_trace(cloud_skill_id, body)`,
  `.evolution_batch(cloud_skill_id, batch_id, trace_ids)`,
  `.get_trace(cloud_skill_id, invocation_id)`.
- New models: `OperationStatus`, `SkillTrace`, `EvolutionBatchResult`.

## 9. Tests (upstream repo, run with its own uv/pytest)

- Ledger: same op same payload → same result, no double write (assert
  add_record count); same op different payload → 409; status endpoint
  lifecycle.
- Write plan: retry after simulated failure reuses plan/target ids.
- TurnGrouper tool preservation.
- Feedback window + explicit op id; dreaming seed scoping + empty seeds.
- Skill trace idempotency, score/outcome validation, batch claiming
  wrong-id rejection, draft-only evolution.
- SDK request builders produce the exact bodies above (sync+async parity).

## Non-goals

- No changes to existing public response shapes beyond additive fields.
- No removal of existing embedding/extraction pipelines.
- Keep `mindmemos`/`mindmemos_sdk` package boundary clean — HomeMaster will
  depend ONLY on `mindmemos_sdk`.
