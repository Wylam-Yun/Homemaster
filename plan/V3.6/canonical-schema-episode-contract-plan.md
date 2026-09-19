# V3.6 Canonical Schema Episode Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task with verification checkpoints.

**Goal:** Make Schema Episode extraction deterministic at the code boundary so LLM output cannot choose domain types, evidence encoding, or record serialization.

**Architecture:** Fixed extractors request semantic candidate fields plus evidence references. HomeMaster compiles each candidate against the normalized episode into one canonical domain record, canonical `properties` projection, and canonical evidence IDs. MindMemOS planner and receipt builder consume only that projection; legacy `dynamic_property` shapes remain read-compatible but are not the primary write path.

**Tech Stack:** Python 3.11, pytest, MindMemOS schema extractor/planner, Qdrant, Neo4j, real ALFWorld gate.

---

### Task 1: Lock the canonical contract with fast tests

**Files:**
- Modify: `tests/homemaster/experience/test_schema_episode.py`
- Modify: `third_party/MindMemOS/tests/workers/test_schema_add_episode.py`

- [ ] Add tests proving compiler output always has canonical `entity_type`, canonical `record.type`, `source.event_ids`, JSON-string `properties[0].value`, and no dependency on model-provided type/source aliases.
- [ ] Add a planner/receipt regression proving a normalized dynamic record is projected into `properties` before planning and receipt IDs are retained.
- [ ] Run the focused tests and capture the expected failures.

### Task 2: Add deterministic candidate compiler

**Files:**
- Modify: `src/homemaster/memory/schema_episode_validation.py`
- Modify: `src/homemaster/memory/schema_episode_prompts.json`

- [ ] Define one compiler entry point that receives `(entity_type, candidate, episode)`.
- [ ] Derive `record.type` from `entity_type` and derive source event IDs only from explicit candidate evidence references matched against episode events/tool steps/provenance.
- [ ] Accept only semantic candidate fields; reject unknown evidence references and incomplete domain fields.
- [ ] Produce one canonical JSON string in `properties[0].value`, with `dynamic_property.episode_record` retained only as a compatibility mirror.
- [ ] Update prompts to say type/source/serialization are compiler-owned and LLM must return semantic fields plus `evidence_event_ids`.

### Task 3: Make extraction call the compiler before planner

**Files:**
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/components/extractor/schema/schema_extractor.py`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/add/schema/schema_add.py`

- [ ] Pass a compiler callback into fixed episode extraction.
- [ ] Compile every fixed candidate immediately after model JSON parsing and before normalization/planning.
- [ ] Return canonical entities only; model aliases never reach planner.
- [ ] Preserve failure propagation and per-domain statuses.

### Task 4: Make receipt structural

**Files:**
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/add/schema/schema_add.py`
- Modify: `tests/homemaster/memory/test_mindmemos_runtime.py`

- [ ] Build per-domain memory IDs from structured planner events/metadata, not event content string matching.
- [ ] Keep a compatibility fallback only for legacy events, covered by a regression.
- [ ] Assert returned IDs are individually active and domain records are readable.

### Task 5: Verify end-to-end and update living docs

**Files:**
- Modify: `docs/pitfalls.md`
- Modify: `docs/session-handoff.md`
- Modify: `CHANGELOG.md`

- [ ] Run focused suite, compileall, diff check.
- [ ] Run strict real ALFWorld schema gate with isolated storage.
- [ ] Run automatic recall black-box gate with isolated storage; report PASS/FAIL honestly.
- [ ] Record the canonical-contract root cause and final gates in docs.
