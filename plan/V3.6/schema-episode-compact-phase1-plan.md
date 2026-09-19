# Schema Episode Compact Input Phase 1

**Goal:** Compact canonical episode input without losing tool evidence, and admit up to 3 MiB UTF-8.
**Architecture:** Keep the existing fixed-extractor/native planner/writer path in this phase. Pair tools first;
then remove assistant replies and duplicate argument fields from event payloads, preserving event identities,
tool results for coordinate validation, task text, terminal events and exact tool_steps. No chunker migration here.
**Execution:** Independently in the existing dirty checkout; no commit/reset or simulator launch.

- [ ] Add regression asserting no assistant.reply events, no duplicate call/result argument fields, unchanged exact
  tool_steps, and all source_event_ids still resolving to events. Run red before changing the normalizer.
- [ ] Add ingress boundary regressions: exactly 3 MiB accepted through the native pipeline, one byte over rejected,
  including multibyte Unicode. Use fake pipeline failure as a reached-pipeline signal, not a storage-success claim.
- [ ] In schema_episode.py remove assistant.reply from actionable types; after pairing pop arguments/args from
  tool event payloads. Do not remove IDs or tool result payloads, because evidence validation uses them.
- [ ] Set _SCHEMA_EPISODE_MAX_BYTES = 3 * 1024 * 1024 in mindmemos_runtime.py. Preserve strict > boundary.
- [ ] Run experience/runtime/fixed-extractor tests, Ruff, compile and diff checks. Rebuild the real 144618 trace and
  assert fewer bytes, exact tool_steps and retained non-reply evidence; persist sizes and hash.
- [ ] Replay the saved 144618 trace through real finalization, without THOR. Use the new input hash/job identity,
  retain the old failed receipt, verify native status plus per-domain active raw memory and independent graph links.
  Valid not_detected must be reported honestly. If an external model request fails, record the exact failed phase.
- [ ] Update architecture, user guide, README limit, changelog, pitfalls and session handoff with observed results.

**Boundary:** Automatic chunking, retrieval corruption, simulator errors and LLM quality are separate work.
3 MiB is an ingress ceiling, not a promise of provider context capacity. Original traces remain intact.
