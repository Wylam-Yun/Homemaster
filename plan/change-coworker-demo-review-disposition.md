# HomeMaster Coworker Demo Review Disposition

## Review Ledger

| Gate | Reviewer | Verdict | Scope | Status |
|---|---|---|---|---|
| Formal implementation plan | `/root/coworker_plan_review` | CHANGES REQUIRED | spec `c6b8c46`, plan SHA-256 `d96dd7825b486eac683984f03f1bd991550d022ce7395e836810e1def90ea66d` | completed once; all findings disposed |
| Final code | main agent read-only audit | CHANGES REQUIRED | complete working-tree diff, tests, both external runs/videos and docs | completed once; all findings disposed |

The plan reviewer was read-only, edited no files and spawned no agents. It was the only plan review. The main agent applied the changes below; repository rules prohibit an automatic plan re-review.

## Plan Findings

| ID | Severity | Finding | Disposition | Plan change |
|---|---|---|---|---|
| P1 | Blocker | Dataset verifier was invoked before any creation/RED step, and the expected dirty scope omitted plan/review files. | Accepted | Task 0 now creates a standard-library verifier with a RED `unittest` before import; Step 0.1 lists the exact intended pre-implementation paths. |
| P2 | Major | Skill-only scans did not protect all Agent-visible HTML/JS/observation/tool/error/API channels from hidden scenario/DAG/evaluator leakage. | Accepted | Added explicit public view models, sentinel injection across every Agent-visible channel, disabled docs/OpenAPI runtime routes and denied observer/state/audit/score/evaluator/artifact/ground-truth navigation. |
| P3 | Major | DAG stage lacked a trusted source and action IDs lacked replay/uniqueness rules. | Accepted | Stage now comes only from persisted EpisodeStore phase/state-version receipts. `ActionLedger` enforces one reservation/consumption per `(run_id, action_id)` and mutation tests cover forged stage, replay, stale version and pre/post receipt exchange. |
| P4 | Major | The anomaly overlay did not prove the causal alarm remains hidden until the current run's successful add and first valid add grep. | Accepted | Added `causal_anomaly_armed=false`, exact add-job/grep evidence linkage and negatives for pre-add, pre-grep, failure, wrong target, old/cross-run evidence and replay. |
| P5 | Major | `verify_run_bundle.py` independence was not enforced, so final verification could echo product matcher/evaluator assumptions. | Accepted | Added AST import boundary and a product-free verifier that independently re-derives actions, DAG coverage, result checkpoints, process returns, external file state, ffprobe and manifest hashes from raw evidence. |
| P6 | Major | Video could not truthfully contain a post-recording artifact/formal verdict. | Accepted | Frozen trajectory/result numbers are recorded with `video_verification=pending` and `formal_success=pending`; final artifact/formal result is published only after FFmpeg/ffprobe/frame/hash checks and is verified outside the video. |
| P7 | Moderate | Ordinary shell golden coverage omitted `/debug`, resumed shell, EOF and KeyboardInterrupt. | Accepted | Task 9 now requires all four paths and object-identity/snapshot behavior in addition to existing slash commands. |
| P8 | Moderate | Tool-only budget checks did not enforce the 1200-second deadline across provider calls. | Accepted | Added a shared monotonic deadline created before service startup and `DeadlineAwareTransport`; provider/summary, HTTP, Playwright and tmux use remaining-time bounded calls and persist timeout status. |

## Post-Review Plan Audit

- Post-disposition plan SHA-256: `40fb18706b04b2bfdbe61dca224c6d7552c4591ec9a63da675c784989aeea4b9`.
- Task sequence: 0 through 13 exactly once.
- Markdown fences: 44, balanced.
- Placeholder scan: PASS.
- Reviewer finding coverage terms: PASS.
- `git diff --check`: PASS.
- Product code modified before plan gate: no.
- External Playwright/FastAPI/uv/TigerVNC/FFmpeg compositions remain `UNVERIFIED` until Tasks 0-1 persist return codes plus independent terminal states.

## Final Review Disposition

The final-review gate opened only after both real-model scenarios, both H.264 videos, complete run bundles, documentation and the then-current full suite had passed. The active collaboration constraint prohibited spawning a subagent, so the main agent performed one read-only final audit and then returned to the implementation role for accepted fixes. No independent reviewer is claimed or fabricated.

Gate evidence:

- Normal run `coworker-20260716-154711-853f071d`: 24/24 nodes, 14/14 checkpoints, 100/100/100, formal success, terminal exit `[0]`, video SHA-256 `a6cd33f1b3c62ca3820ea870c5ffcbe8f236cfb5c66090332f46ae707593755e`.
- Anomaly run `coworker-20260716-160128-c4f0faa9`: 22/22 nodes, 11/11 checkpoints, 100/100/100, formal rollback, terminal exits `[0, 1]`, video SHA-256 `d00f19c7b699cc5d832f349eb86a9ab2e0b0aa2a050f7e99b6e335fcfd64cfcd`.
- Both bundles pass the strengthened product-independent verifier, including manifest completeness/hash checks, evidence-reference ownership, fresh ffprobe and raw-RGB first/middle/last frame checks.
- Post-disposition full suite: 477 passed, 1 skipped, one upstream Starlette/httpx deprecation warning.

| ID | Severity | Finding | Disposition | Verification |
|---|---|---|---|---|
| F1 | Blocker | `artifact_failure` was hard-coded false, and neither product finalization nor the independent verifier rejected a required core artifact omitted from the manifest. A missing or drifted artifact could retain `formal_success=true`. | Accepted. Added a locked required-artifact set, missing-entry/complete/hash checks, and recomputation of formal success only after registration and verification. | RED coverage in `test_artifacts.py` and `test_scoring.py`; both real bundles pass the strengthened verifier. |
| F2 | Major | A pre-reserved action could be consumed after a terminal decision, runtime events lacked an active-run gate, and built-in planner/progress/skill tools did not check the shared terminal outcome. | Accepted. Consumption, terminal execution and runtime-node validation now require an active run; all built-in coworker tools check the shared outcome before work. | Terminal pre-reservation/runtime/tool regressions pass in `test_episode_store.py` and `test_registry.py`. |
| F3 | Major | Decision `evidence_refs` accepted invented or cross-run strings, and trajectory normalization treated them as externally grounded evidence. | Accepted. Decisions now accept only prior evidence persisted for the same run; accepted-job evidence is stored explicitly; normalization and the offline verifier independently reject unknown references. | Unknown/cross-run and forged-normalization regressions pass; both historical bundles remain independently verifiable. |
| F4 | Major | The independent bundle verifier trusted the product-generated frame verdict and did not check FFmpeg exit, first-packet growth or independently decoded frame content. | Accepted. It now checks FFmpeg return, video hashes, first-packet growth, reruns ffprobe, extracts raw RGB at first/middle/last timestamps and independently calculates nonblack ratio, variance and first/last change. | Raw-RGB metric unit test plus fresh verification of both delivered videos pass without changing their hashes. |

After these dispositions, the final audit found no remaining actionable correctness, isolation, secret, default-framework, artifact or video-gate findings. Mac Screen Sharing remains explicitly out of scope; the server-side headed display and recorded evidence are unchanged.

## Post-Review User Scope Override

- On 2026-07-16 the user explicitly removed Mac live Screen Sharing from the completion gate and asked the main agent to run and record entirely on `hkust4` without user intervention.
- Server-side headed Chrome, localhost-only TigerVNC, real DOM/backend/X11/RFB evidence, FFmpeg capture, H.264 verification and stored videos remain mandatory.
- This is a user-owned scope decision after the completed plan review, not an additional reviewer finding; no second plan review is started.
