# V3.5 Main CLI Attempt

The formal benchmark CLI was run from the V3.5 source checkout with
`PYTHONPATH=src` and an explicitly bound isolated ALFWorld worker. The worker
completed readiness, reset, scene loading, and frame artifact creation; its
stderr contains no traceback. The model loop reached a real provider request
and a worker-backed tool result.

This is not a passing main-CLI gate. The run was reclaimed while a later
provider request was in flight, so no formal `summary.json` or terminal episode
record was produced. The run must be repeated with a bounded provider request
and a persisted terminal/cleanup record before Phase 5 Step 3 can be checked.
