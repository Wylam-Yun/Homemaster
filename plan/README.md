# plan/ -- Implementation Plan Archive

This directory normally contains the **historical archive of formal implementation plans**.
Exception: the user designated [V3.4](V3.4/README.md) as the active permission design review
location; it remains a draft until reviewed. Other archived versions are not active design truth. For the current state of the repository, read in this order:

1. `docs/session-handoff.md` -- the single live handoff/state document.
2. `CHANGELOG.md` -- what each delivery actually changed, in order.
3. `README.md` / `docs/architecture/` -- current capabilities and architecture.

Each `V<x.y>/` directory holds the plans, specs, review dispositions, and locked baseline
evidence for that delivery. They are kept as immutable history and are skipped by the
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
| V3.2 | 2026-09-02 | Reproducible core and ALFWorld decoupling (setup/bundle, managed-local Neo4j) |
| V3.3 | 2026-09-08 | ALFWorld trajectory memory |
| V3.4 | 2026-09-09 | Household resource permissions (object x action, destination areas) |
| V3.5 | 2026-09-18 | Architecture convergence: single composition, canonical Tool contract, isolated worker |
| V3.6 | 2026-09-18 | Canonical schema-episode contract, trajectory schema memory |
| V3.7 | 2026-10-01 | AgentScope migration (phased decisions and designs) |

Standalone plans:

- `2026-09-08-alfworld-benchmark-hanghan-pitfalls.md` -- ALFWorld benchmark environment
  pitfalls on the Hanghan GPU machine (postmortem notes).
- `2026-10-06-web-first-ux-and-install.md` -- web-first UX and distributable install plan
  (W4/W5 UI interaction workflow elevated to first-class).
- `2026-10-06-web-first-ux-review-disposition.md` -- independent-review disposition for the
  web-first UX plan.
- `2026-10-06-p3-ui-batch1-impl.md` -- P3 UI batch-1 implementation spec derived from the
  web-first UX plan (W4/W5).
- `2026-10-14-robocasa-integration-impl.md` -- RoboCasa benchmark integration design via an
  isolated RPent worker.
