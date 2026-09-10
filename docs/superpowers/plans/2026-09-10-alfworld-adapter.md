# ALFWorld adapter implementation plan

**Goal:** Complete approved option B: repair benchmark semantic adaptation and audit related boundaries using per-instance real THOR evidence.

**Architecture:** Keep the existing navigation executor and model tool interface. Resolve exact targets once; prepare enclosing containers from authoritative THOR state before target visibility navigation. Macro transitions use refreshed state and verify native benchmark predicates and inventory. Unsupported expert tasks are explicit setup failures only for affected THOR execution.

**Tech Stack:** Python, Pydantic, pytest, installed ALFWorld/AI2-THOR, existing isolated runtime environments.

## Execution and acceptance

- [ ] Reproduce fixed-sequence macro failures in `tests/homemaster/benchmarking/test_alfworld_adapter_semantics.py`; run `.runtime/venv/bin/pytest -q tests/homemaster/benchmarking/test_alfworld_adapter_semantics.py` before implementation.
- [ ] Repair `src/homemaster/benchmarking/alfworld/env_adapter.py`: lock object IDs; avoid redundant open/close/toggle; turn microwave off before opening; force a real off-to-on faucet transition after placement; verify native heated/cooled/cleaned sets and exact inventory after retrieval. Reject contradictory external receipts.
- [ ] Reproduce hidden-container navigation using original fixed manifest cases 4, 5, 6. Prepare ancestors outside-in with shared action/time budgets and preserve every receipt. Verify target visibility and bbox in final external event, never infer visibility from containment.
- [ ] Audit `types.py`, reset and runner: preserve `unsupported_task_type`, scope expert restriction to THOR, verify no reset dispatch and all failure artifacts. Cover textworld and missing selection metadata.
- [ ] Audit all tool dispatch paths (take/put/open/close/use/slice and macro navigation), exact-instance resolution, action accounting and external goal checks. Fix proven defects with regressions; report unsupported capability explicitly.
- [ ] Inspect existing Dreaming preflight ID validation and tests; prove invalid references cause zero mutations. Do not guess repair mappings for historical archived data.
- [ ] Run adapter/runner/navigation/execution/interface and memory regression gates. Real isolated THOR cases: ordinary placement; fridge and microwave contents; heat/cool/clean; unsupported task. Each requires action success, independent metadata/native goal predicate, output artifacts and cleanup. Full model episodes additionally require `won=true`, complete stderr inspection and memory readback.
- [ ] Update README, user guide, architecture, CHANGELOG, pitfalls and relevant CLAUDE rules. Maintain `docs/session-handoff.md` as the sole live progress record. Preserve existing unrelated changes; no push.

## Evidence boundaries

Previous transcript claims are historical, not current gates. Existing working-tree reset guard maps to `setup_unexpected` and has no new regression. Current fixed macros are confirmed by source inspection; actual bug reproduction remains pending. Native runtime `ThorEnv.update_states` records heat on microwave on, cool on fridge close, clean on faucet on; tests must inspect those exact external predicates.
