# Real-Time LLM Observable Coworker Delivery

## Goal

Deliver two continuous videos executed by real Mimo `mimo-v2.5`: `normal` and
`post_change_anomaly`. Each video must visibly show the model plan, selected
tools, environment results, deterministic decision summaries, incidents, and
recovery. Scripted runs remain presentation tests and cannot satisfy final
acceptance.

## Gates

- A final accepted run must have a successful top-level attempt, real provider
  identity, fresh successful model responses, 100 trajectory/result/overall
  scores, expected external state, successful process return codes, and an
  independent verifier PASS.
- Assertions are per run and per target. A successful video or business state
  cannot hide a lifecycle, provider, artifact, or instance failure.
- Every failed real attempt remains preserved and is included in the acceptance
  report with root cause and disposition.
- Main agent performs implementation and verification. The only remaining
  subagent gate is one read-only final code review after implementation, videos,
  verification, documentation, and report are complete.

## Phases

| Phase | Status | Exit criterion |
|---|---|---|
| 1. Design and implementation plan | complete | Design, plan, and plan-review disposition are committed. |
| 2. Observable presentation implementation | complete | Presentation v2, pure reducer, five-zone observer, safe projection, and verifier are committed. |
| 3. Controlled presentation gates | complete | Normal/anomaly failure recovery and every named incident frame pass. |
| 4. Internal verification | complete | 791 tests pass, lint/compile pass, and Mimo preflight passes. |
| 5. Recording-stop lifecycle repair | complete | Dedicated timeout, idempotent service stop, RED/green tests, and real two-stop HTTP gate pass. |
| 6. Real normal acceptance | complete | `coworker-20260720-024949-b7004546` passes shell, external-state, video, visual, and independent verifier gates. |
| 7. Real anomaly acceptance | complete | `coworker-20260720-025635-a46d87ca` passes per-target rollback, video, visual, and independent verifier gates. |
| 8. Acceptance report and final audit | in progress | All attempts and evidence are reported; full tests and final audits remain. |
| 9. Final code review | pending | One reviewer reports findings; main agent dispositions them and runs targeted verification. |

## Failed Attempts To Preserve

- `coworker-20260720-022516-8c773877`: real normal business and video success,
  but attempt failed because a 20-second client timeout caused a duplicate,
  non-idempotent recording stop. This attempt is evidence for the lifecycle
  root cause, not a final accepted video.

## Current Next Action

Run the full final audit and both independent bundle verifiers, then start the
single allowed final reviewer.
