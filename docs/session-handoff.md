# Session Handoff

## Current State

Branch `mindmem` contains the uncommitted V2.6 memory self-correction implementation. Explicit feedback is wired through
the successful provider attempt's frozen recall context. Session finalization has resumable add, implicit-feedback,
dreaming-counter and dreaming stages. Dreaming batches are scope-locked and persist under
`memory.data_root/mindmemos/dreaming_state`.

Direct `mindmemos_update` now dispatches from the stored raw memory: Vanilla memories without `record_json` use the
native same-ID update pipeline; valid Schema memories use a deterministic versioned fast path that updates content,
structured metadata, vectors and graph lineage without re-running Schema Add; corrupt `record_json` fails closed. The
new `mindmemos_history` tool reads the real `DERIVED_FROM` component from either an active or archived version.

## Verification

Focused V2.6 unit/integration gates pass, including cross-process single-owner claiming and typed failure propagation.
The complete non-live suite passes with `1675 passed, 2 skipped, 10 deselected`. The installed-wheel probe passes with
`2 passed`; full `compileall`, Ruff and `git diff --check` also pass.

The combined live gate passes with `2 passed in 885.84s`. A real provider run selected exactly one
`mindmemos_feedback` call; Qdrant readback verified the old raw record was archived, the corrected raw record was active,
and an unrelated record was unchanged, while Neo4j readback verified the `DERIVED_FROM` relation. The real dreaming
no-action path verified the seed raw record, completed add-record status, cleared persistent pending state and exact
watermark.

The direct update/history follow-up passes 29 focused runtime/tool tests, 19 memory-tool tests and 64
registry/application/package tests. Its broader non-live gate passes with `1672 passed, 3 skipped, 11 deselected`; the
excluded tests require optional Playwright or MCP dependencies absent from this environment. A separate real
Qdrant/Neo4j/provider gate passes in `103.71s`: the old Schema memory is archived, the new memory and `record_json` both
contain `uv`, provenance advances to 2, the reused entity contains `uv` rather than `conda`, `DERIVED_FROM` exists, and
history queried from either ID returns `[new, old]`. Trace timing shows the direct Schema update completes in about one
second and does not enter Schema Add or its chat stages.

## Next Step

Review the V2.6 diff and commit only after explicit user authorization.

The currently running HomeMaster shell was started before this code changed. Exit and restart it before interactive
testing so that the seven-tool memory registry, including `mindmemos_history`, is loaded.

## Blockers

No code blocker is known. Do not commit without explicit user authorization, and preserve the user's provider retry and
existing CHANGELOG changes. Archived memories created by an older direct-update implementation without a real
`DERIVED_FROM` edge cannot be reconstructed into history and must not be guessed. The feedback executor's handling of
Schema `record_json` is a separate known issue; this follow-up fixes the direct update path only.
