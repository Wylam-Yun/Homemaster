# V3.5 Phase 3 Evidence

Harness contract and failure black-box evidence, 2026-09-26.

`tests/homemaster/alfworld/test_harness_contract.py` covers reset-before-action,
missing grounding, backend failure receipts, stale snapshot rejection, external
state-change verification, and close failure propagation. Result: `6 passed`.

The real THOR per-trial gate is recorded in Phase 5. The independent failure
black-box is recorded at
`failure-live-20260926b/failure.json` and covers two separate instances:

- an unresolved target returned typed `target_unresolved` with external return
  code `64`, did not attempt the backend, and left the state digest unchanged;
- a forced backend failure returned an error with external return code `1`,
  recorded `backend_attempted=true`, and left the state digest unchanged.

Both instances closed through the worker with return code `0`, worker exit code
`0`, no descendants, and no traceback in persistent stderr. The earlier
`failure-live-20260926.json` file is retained as a pre-fix failure record and
is not acceptance evidence.
