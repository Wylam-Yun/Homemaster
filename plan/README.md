# plan/ -- Implementation Plan Archive

This directory is the **historical archive of formal implementation plans**. Nothing here is
active design truth. For the current state of the repository, read in this order:

1. `docs/session-handoff.md` -- the single live handoff/state document.
2. `CHANGELOG.md` -- what each delivery actually changed, in order.
3. `README.md` / `docs/architecture/` -- current capabilities and architecture.

Each `V<x.y>/` directory holds the plans, specs, review dispositions, and locked baseline
evidence for that delivery. They are kept as immutable history (see
`2026-08-27-retire-coworker-subsystem.md` for the preservation policy) and are skipped by the
legacy-terms guard. Standalone topic plans live directly under `plan/`.

| Version | Date | Focus |
|---------|------|-------|
| V1.4 | 2026-05-19 | Agent loop full migration |
| V1.5 | 2026-06-13 | Context, task state, compaction |
| V1.6 | 2026-06-29 | Cross-spec coordination, object-memory RAG (later retired) |
| V1.7 | 2026-07-13 | ALFWorld put partial-pose, feedback, evaluation contract |
| V1.8 | 2026-07-13 | ALFWorld frozen-snapshot controlled-time oracle execution |
| V1.9 | 2026-07-20 | Coworker benchmark delivery (subsystem retired 2026-08-27; baseline evidence stays) |
| V2.0 | 2026-07-24 | Skill, raw output, rich renderer closeout |
| V2.1 | 2026-07-24 | Memory system discussion |
| V2.2 | 2026-07-28 | ALFWorld Gateway implementation |
| V2.3 | 2026-08-04 | MindMemOS embedded integration |
| V2.4 | 2026-08-13 | Post-session experience finalization |
| V2.5 | 2026-08-13 | Experience recall and use |
| V2.6 | 2026-08-17 | Memory experience self-correction |
| V2.7 | 2026-08-18 | Direct flat MindMemOS add |
| V2.8 | 2026-08-20 | Web Console |
| V2.9 | 2026-08-20 | Feishu CLI-equivalent confirmation |
| V3.0 | 2026-08-24 | Ops Monitor agent change execution |
| V3.1 | 2026-08-26 | V3.1 browser tools (OpenCLI 1.8.7 vendor, capability matrix) |

Standalone plans:

- `2026-08-27-retire-coworker-subsystem.md` -- complete retirement of the Coworker/Case02
  subsystem; the supported browser architecture is the V3.1 Browser Gateway.
- `2026-08-27-retire-v19-alfworld-release-certification.md` -- retirement of the V1.8 ten-trial
  inventory and V1.9 fixed release-certification layer; current ALFWorld runtime remains supported.
- `alfworld-mindmem-integration-plan.md`, `alfworld-mindmemos-portable-benchmark-plan.md`,
  `locomo-full-pipeline-pilot-plan.md`, `native-mindmemos-search-types-implementation-plan.md`,
  `portable-memory-runtime-implementation-plan.md`, `unified-session-finalization-plan.md`,
  `interactive-cli-confirmation-implementation-plan.md` -- cross-version topic plans, all
  delivered; see `CHANGELOG.md` for the corresponding entries.
