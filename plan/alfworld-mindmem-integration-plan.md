# ALFWorld and Stored-First Memory Integration Plan

## Goal

Integrate `alfworld-benchmark-local` commit `a228c15` into the `mindmem` line at
`b96e603` without regressing the stored-first `mindmemos_add` contract or its
application-owned enrichment workers. Preserve the unified semantic session boundary,
portable benchmark composition, and real visual ALFWorld verification.

## Locked Invariants

1. Public `mindmemos_add` remains synchronous through verified raw storage and returns
   `stored + memory_id`; it never returns the obsolete `accepted + job_id` contract.
2. `MemoryEnrichmentQueue` remains an independently owned two-worker application resource.
   Its startup, service injection, drain, and close ordering must survive the merge.
3. `application.session(...)` remains an idempotent semantic boundary. Closing it admits
   Session Finalizer work to the existing serial memory-work FIFO without making
   `ApplicationRuntime.run()` infer session completion.
4. ALFWorld uses the canonical HomeMaster composition with embedded MindMemOS, automatic
   recall, managed Neo4j, memory FIFO, enrichment workers, and application cleanup.
5. LoCoMo source conversations close a finalizing session scope and wait for verified
   readback. QA probe sessions do not enter Session Finalizer and cannot write benchmark
   answers back into the memory being evaluated.
6. Relative memory, Neo4j, and Java paths loaded from YAML remain anchored to the YAML
   directory. Existing absolute paths remain unchanged.

## Integration Steps

1. Merge `alfworld-benchmark-local` into an integration branch created from `mindmem` with
   `--no-commit`.
2. Resolve additive documentation conflicts by retaining both verified change records and
   rewriting the live handoff as one current state.
3. Resolve `cli/composition.py` semantically: retain stored-first enrichment construction
   and add the session finalization controller, benchmark roots, tenant scope, and trace
   override.
4. Retain the current stored-first application tests and add the ALFWorld session tests.
   Remove assertions that restore the obsolete asynchronous Add receipt.
5. Change LoCoMo QA probes to ordinary turn execution without a finalizing session scope;
   add a regression proving only source sessions are admitted.
6. Run focused tests for application runtime, memory tools/runtime/enrichment, composition,
   Session Finalizer, Shell, one-shot, ALFWorld, LoCoMo, and config resolution.
7. Run Ruff, compileall, `git diff --check`, cleanup guard, and the complete runnable
   non-live/non-stress suite.
8. On `hkust4`, run one real visual ALFWorld episode and one record from each other affected
   benchmark entry. Require return-status checks, per-instance external terminal readback,
   valid visual frames where applicable, and complete process/listener cleanup.

## Merge Commit Gate

Do not create the merge commit until all locally available gates pass and the live evidence
is recorded in `CHANGELOG.md` and `docs/session-handoff.md`. The commit body must describe
the same behavior, reason, impact, and verification as the CHANGELOG entry.
