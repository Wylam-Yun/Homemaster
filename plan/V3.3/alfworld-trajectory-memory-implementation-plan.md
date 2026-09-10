# V3.3 ALFWorld Trajectory Memory Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with focused tests and external readback gates.

**Goal:** Persist every ALFWorld episode/subtask as an immutable, outcome-labelled trajectory memory and compile only verified successful trajectories into traceable executable experience.

**Architecture:** Keep the generic SessionFinalizer for dialogue experience. Add an ALFWorld domain writer that receives a canonical runner result before session close, stores a typed trajectory record with source hash, and verifies Qdrant/Neo4j readback. Add a server-owned compile job service that validates scope, source integrity, outcome, schema, and lineage before publishing derived experience; expose it through restricted Web API/UI projections.

**Tech Stack:** Python dataclasses/Pydantic, embedded MindMemOS, Qdrant Local, Neo4j, FastAPI, React/TypeScript, pytest, Ruff.

---

### Task 1: Lock the domain contracts and deterministic outcome rules

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/types.py`
- Create: `src/homemaster/benchmarking/alfworld/trajectory_memory.py`
- Test: `tests/homemaster/benchmarking/test_alfworld_trajectory_memory.py`

- [x] Add typed `AlfworldOutcome` (`success`, `failure`, `unknown`), canonical final-state payload, trajectory record, compile result, and deterministic helpers.
- [x] Make `won=true` the only success condition; unreadable/contradictory/infrastructure termination maps to unknown.
- [x] Canonicalize source payload and compute lowercase SHA-256.
- [x] Add tests for all outcome branches, forbidden provider-facing fields, and stable hashes.

### Task 2: Persist trajectory memory through the application-owned MindMemOS boundary

**Files:**
- Modify: `src/homemaster/adapters/alfworld_entry.py`
- Modify: `src/homemaster/memory/models.py`
- Modify: `src/homemaster/memory/serialization.py`
- Modify: `src/homemaster/memory/mindmemos_runtime.py`
- Test: `tests/homemaster/benchmarking/test_alfworld_entry.py`
- Test: `tests/homemaster/memory/test_mindmemos_runtime.py`

- [x] Add a domain writer API that stores trajectory records through the existing application-owned backend.
- [x] Preserve immutable source content and metadata, including source trace hash and final state.
- [x] Verify active raw Qdrant state and Neo4j source relationship before returning success.
- [x] Emit `memory.trajectory.queued`, `stored`, and `readback_verified` events with typed identity and status.

### Task 3: Integrate single-episode and taskset lifecycle ordering

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/runner.py`
- Modify: `src/homemaster/benchmarking/alfworld/tracing.py`
- Test: `tests/homemaster/benchmarking/test_alfworld_runner.py`

- [ ] Write the canonical `alfworld.episode_finished` event and source hash before `entry.end_session()`.
- [ ] Submit one trajectory per episode and per taskset subtask; preserve not-run subtasks as non-executable records where final state is known/unknown.
- [ ] Keep taskset aggregate separate from per-subtask outcome.
- [ ] Add ordering and per-instance tests, including `done=false/won=false` and infrastructure uncertainty.

### Task 4: Add ALFWorld compiler and durable compile receipts

**Files:**
- Create: `src/homemaster/experience/alfworld_compiler.py`
- Create: `src/homemaster/experience/alfworld_compile_jobs.py`
- Modify: `src/homemaster/memory/mindmemos_runtime.py`
- Test: `tests/homemaster/experience/test_alfworld_compiler.py`
- Test: `tests/homemaster/experience/test_alfworld_compile_jobs.py`

- [ ] Accept only scoped, raw-verified trajectory records with matching source hash.
- [ ] Compile successful actions into safe ALFWorld `ProcedureRecord` steps with non-empty expectations.
- [ ] Compile failure/unknown only as non-executable diagnostics, never executable procedures.
- [ ] Make `(source hash, compiler version)` idempotent and fail closed on tampering or any failed external readback.
- [ ] Emit started/completed/failed compile events and persist job receipts.

### Task 5: Expose restricted Web API and memory projections

**Files:**
- Modify: `src/homemaster/memory/management.py`
- Modify: `src/homemaster/web/schemas.py`
- Modify: `src/homemaster/web/app.py`
- Modify: `web/src/api/http.ts`
- Modify: `web/src/components/MemoryPage.tsx`
- Modify: `web/src/components/MemoryDetailDialog.tsx`
- Test: `tests/homemaster/web/test_app.py`
- Test: `tests/homemaster/web/test_memory_readonly.py`
- Test: `web/src/components/MemoryPage.test.tsx`

- [ ] Add `POST /api/memories/{memory_id}/compile` and `GET /api/memory-compilations/{job_id}`.
- [ ] Derive tenant/session/run/source scope server-side and reject client-supplied internal references.
- [ ] Display ALFWorld outcome, classification, goal/subtask identity, executable state, derived state, and compile state.
- [ ] Ensure failure/unknown records cannot appear in executable procedure recommendations.

### Task 6: Run package tests, external black-box gates, and synchronize documentation

**Files:**
- Modify: `docs/architecture/alfworld-harness.md`
- Modify: `docs/architecture/memory-system.md`
- Modify: `docs/alfworld-user-guide.md`
- Modify: `docs/memory-user-guide.md`
