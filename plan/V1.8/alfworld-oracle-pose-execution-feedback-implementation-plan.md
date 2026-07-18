# ALFWorld V1.8 Current-Visible Controlled-Time Oracle Execution Implementation Plan

> **Execution status:** The main agent executed and dispositioned this plan directly under the active no-subagent constraint. Checked steps mean completed or explicitly dispositioned; they do not turn failed Gate rows or unavailable external runs into PASS. The planned independent final reviewer was replaced by one main-agent complete-diff review and is recorded as such.

**Goal:** Replace ALFWorld's action-time navigation search and ambiguous feedback with a current-model-view authorization gate, one immutable reset-time pose snapshot, one exact execution context/gateway, closed typed outcomes and independently verified external terminal states.

**Architecture:** Gate A first proves the external `reset -> ChangeTimeScale(0.01) -> query/full scan -> exact pose restore -> ChangeTimeScale(1.0)` transaction, final normal-time event authority, visibility fixture, sole-pose and action contracts on `hkust4` without importing HomeMaster. Product code then separates pure scan/snapshot state in `pose_snapshot.py`, reset orchestration in `reset_transaction.py`, successful Provider-image commits in `model_view.py`, and exact navigation/manipulation in `execution.py`; `AlfworldEnvAdapter` binds those contracts to the real runtime but does not own a second policy layer. Runner completes reset/control before Provider construction, while tools and Dispatcher forward one `AlfworldExecutionFeedback` payload without reconstructing state.

**Tech Stack:** Python 3.11, frozen dataclasses and `Protocol`, pytest, Ruff, ALFWorld 0.5.0, ai2thor 2.1.0, NumPy 2.4.6, Pillow 12.3.0, Xvfb, canonical JSON/JSONL, Typer, the existing `/data0/yuqiao/envs/hm_alfworld` environment.

---

## Locked Execution Rules

- Authoritative spec: `plan/V1.8/alfworld-oracle-pose-execution-feedback-spec.md`, 1,652 lines, SHA-256 `92ef48a68a30f021aa81cadf5177e07ecf9d12ecda924744a50a4c6f612a9f49`, committed as `61b76dc` and byte-identical on `hkust4`.
- User-approved upstream rule: every physical `robot_go_to` target must be strict-visible in the current event and that event's RGB must equal the image in the successful Provider request that produced the current assistant tool call.
- Snapshot `ok`, reset observation, semantic type, addressability, containment and Gate data never authorize an invisible target. The miss is non-terminal `target_not_visible` with zero snapshot, parent, context and backend calls.
- Generic labels select the first current-visible exact ID in frozen full-set order. Explicit ordinals bind the frozen full set; missing or invisible ordinals return the same error without fallback.
- Preserve discovery runs 001-007, `matrix-v1.json`, `matrix-v2.json` and every existing helper archive byte-for-byte. The time-control policy changes `matrix-v3.json` and all helper identities; new evidence uses the updated `matrix-v3.json`, a newly created `exact-cases-v3.json`, `discovery-run-008` and `case-run-008` only.
- Canonical runtime roots are `/data1/haodong2/weilin/red_bird/Homemaster` and `/data1/haodong2/weilin/red_bird/alfworld`; `/home/haodong2/...` resolves to the same directories. Product identities never include either absolute root or the host.
- Gate A failures are preserved before product/config/test edits, but the user explicitly removed perfect Gate A as a prerequisite on 2026-07-17. Failures remain `UNVERIFIED` evidence and never create a fallback mode.
- Every external symbol and composition remains `UNVERIFIED` until its real Gate row passes process return code, external return status and per-instance terminal-state assertions.
- Tests are RED-first. Each interface change updates every real/Fake implementation and runs an implementation audit.
- Successful THOR setup is exactly `1 slow-time + 1 query + N scan Teleports + 1 pose restore + 1 normal-time restore = N+4`. Every post-enter failure records actual recovery sends, attempts pose restoration before normal-time restoration, and closes/quarantines an environment whose normal speed is not proven.
- The pose-restore Teleport event is never the published initial model event. Only the successful `ChangeTimeScale(1.0)` return event may become `restored_event_ref`, `AlfworldEnvState`, model `event_sequence=0` and `frame-0000` after pose/world/visibility/bbox/pixel equality gates.
- No action-time candidate list, second pose, target switch, hidden-parent search, expert trajectory, legacy `env.step("go to ...")`, local Put retry or automatic container Open is permitted.
- Provider/model-view state advances only after a complete assistant response from the exact outbound request. Multiple tool calls in one assistant response share one committed view; a frame produced by an earlier call in that batch is not visible to later calls in that batch.
- The main agent performs all implementation work. The only subagents are the already-authorized one-time plan reviewer and, after all code/evidence/docs, one-time final code reviewer; reviewers are read-only and cannot delegate.
- Do not push. A local commit and runtime-worktree synchronization are permitted; pushing requires separate user authorization.
- Preserve unrelated modified `CLAUDE.md`, `docs/pitfalls.md` and any user bytes. Edit those files only when this implementation produces a qualifying new pitfall/rule, and merge rather than replace existing content.

### 2026-07-17 User Execution Override

- Gate A is no longer a perfect-pass prerequisite for product edits. Preserve every failed run and use the verified rows as evidence, but do not expand the standalone helpers to absorb unrelated engine settling.
- `discovery-run-015` completed all 20 workers; 19 passed and `supplemental-slice-contract` exposed reset-time Apple settling after `ChangeTimeScale(0.01)`. The case manifest was correctly not created. This failure remains visible and `UNVERIFIED`.
- Product TDD begins from the frozen `pre-discovery-run-015` helper bytes. Gate B is best-effort external verification: execute as much of the real product matrix as possible and record every failing or incomplete row without treating a non-perfect result as a reason to hide or defer the product implementation.
- Internal tests and static checks remain required in proportion to the changed code. Gate failures are evidence, not success, and must be reported in the final documentation and commit body.

### 2026-07-18 Delivery Disposition

- Tasks 2-10 product implementation and internal regression work are complete. The formal V1.8 call graph uses the new gateway/snapshot/exact execution path; physical V1.7 compatibility implementations remain and are recorded as a structural residual rather than deleted late.
- Gate A stays frozen at `discovery-run-015` with 19/20 workers passing and no `exact-cases-v3.json`.
- Gate B `run-001` exposed a pinned-Adapter keyword-only call defect, which is fixed with a strict regression. Fresh `run-002` reached real THOR and ended score-ineligible at `scan_pose_mismatch -> scan_time_scale_restore_rejected`, with five setup actions and zero Provider/model actions. Direct review then added exact runtime-scene validation; final production-affecting rerun `run-003` passed that gate on FloorPlan219 and reproduced the same honest terminal/counts. Independent verification exits 2 because the complete matrix is unavailable.
- The fixed ten-Episode manifest is constructed from the six frozen `historical_exact` rows plus each unresolved Episode's pre-Gate `candidate-1`, explicitly labeled `deterministic_replacement`. The ignored Mimo profile passes a real `LLMClient` request. Run `alfworld-valid_unseen-v18-realapi-20260718-001` executes all ten pinned resets and exposes ten identical `scan_pose_mismatch -> scan_time_scale_restore_rejected` setup terminals, 50 setup requests and zero Provider/model requests; this is a completed failed run, not a PASS.
- The planned independent final reviewer cannot be used under the active no-subagent constraint. One main-agent direct complete-diff review is used instead and this deviation is reported explicitly.
- Documentation, final tests, changed-file hashes and one local `hkust4` commit complete the delivery. No push is authorized.

## Recorded Baseline

```text
remote product HEAD                         = 22cb122e1b186c8e3877cd6504f805685e1bbfc7
focused ALFWorld/Runner/Dispatcher          = 145 passed
all tests excluding known cleanup guard     = 351 passed, 1 skipped
full pytest                                 = 351 passed, 1 skipped, 1 known failure
compileall                                  = PASS
Ruff baseline                               = 39 lint findings, 41 format files
```

The one full-pytest failure is present on clean `22cb122`: `scripts/guard_no_legacy_terms.py` globally blocks the ordinary word `deterministic`, which already occurs in unchanged V1.7 files. Task 10 narrows that guard with a RED regression; unrelated Ruff findings are not bulk-formatted. Before product edits, rerun and record the baseline on the exact remote Python/runtime.

## Gate-A Locked Decisions

- Slice exact-ID behavior is currently `UNVERIFIED`; Tasks 2-12 remain blocked until Task 1 Step 12 records exactly one source-level behavior from real `case-run-008` evidence.
- The runtime contract records the proven value only for startup identity checking. Product code must not switch behavior dynamically from that JSON value or retain both implementations.
- After Gate A, this section is changed once to either `preserved -> rebase the same exact context` or `replaced_unique -> consume the old context and never auto-select a successor`, with the opposite source branch and its tests removed from the executable plan. This evidence lock is main-agent disposition of an external linchpin, not an additional review round.

## Authoritative File Map

| File | Responsibility |
|---|---|
| `src/homemaster/benchmarking/alfworld/types.py` | Closed reset/control/feedback/result/classification/counter types and legal-combination validation. |
| `src/homemaster/benchmarking/alfworld/trial_selection.py` | Strict portable `TrialSelectionManifest`; no Gate target, pose, fixture or answer fields. |
| `src/homemaster/benchmarking/alfworld/runtime_contract.py` | Load the Gate-A-proven runtime contract and reject build/version/Slice drift before reset. |
| `src/homemaster/benchmarking/alfworld/pose_snapshot.py` | Pure canonical scan policy/plan, addressability, provenance, immutable snapshot/store and lookup overlays. |
| `src/homemaster/benchmarking/alfworld/model_view.py` | Frame ledger, successful Provider-view commit, strict current observation and model/event pixel binding. |
| `src/homemaster/benchmarking/alfworld/reset_transaction.py` | Same-environment slow-time/query/full-scan/pose-restore/normal-time transaction and atomic publication from the final event. |
| `src/homemaster/benchmarking/alfworld/execution.py` | One backend/gateway, current-visible target and anchor resolution, exact context, navigation and manipulation transitions. |
| `src/homemaster/benchmarking/alfworld/env_adapter.py` | Runtime binding/lifecycle/event capture; only backend implementation may send THOR actions. |
| `src/homemaster/providers/attempts.py` | Immutable Provider request/attempt identity, actual image-byte hashes and commit state. |
| `src/homemaster/providers/{errors,llm_client}.py` | Closed provider cause codes and one explicit stream attempt at a time. |
| `src/homemaster/agent/generic_runtime.py` | One bounded safe retry, successful model-view commit and tool-dispatch commit ordering. |
| `src/homemaster/benchmarking/alfworld/{tools,registry,prompt,translator,tracing}.py` | Public tool surface, sole safe feedback serializer and trace-only internal evidence. |
| `src/homemaster/tools/dispatcher.py` | Generic injected dispatch observer, typed call ordering and terminal exception handling; no ALFWorld import. |
| `src/homemaster/benchmarking/alfworld/runner.py` | Fresh Adapter lifecycle, pre-Provider reset/control terminals, root ledgers, metrics and exact trial pinning. |
| `config/alfworld_v18_runtime_contract.json` | Gate-A-proven runtime/build/scene/Slice contract without secret, root or host. |
| `config/alfworld_v18_regression_trials.json` | Product-safe ordered ten-trial selection only. |
| `src/homemaster/benchmarking/alfworld/object_vocabulary.json` | Committed task-independent public semantic vocabulary. |

## Task 1: Add The Controlled-Time Transaction And Complete Fresh Gate A Run-008

**Files:**
- Modify ignored runtime/local mirror: `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- Modify ignored runtime/local mirror: `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- Modify ignored runtime/local mirror: `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`
- Modify ignored runtime/local mirror: `var/alfworld-evidence/20260713-v18-gate-a/matrix-v3.json`
- Modify ignored runtime/local mirror: `var/alfworld-evidence/20260713-v18-gate-a/README.md`
- Create ignored after final helper freeze: `var/alfworld-evidence/20260713-v18-gate-a/helper-archive/pre-discovery-run-008/`
- Create ignored only after verified discovery: `var/alfworld-evidence/20260713-v18-gate-a/exact-cases-v3.json`
- Create ignored runtime evidence: `var/alfworld-evidence/20260713-v18-gate-a/time-scale-effect-control-run-001/`
- Create ignored runtime evidence: `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-008/`
- Create ignored runtime evidence: `var/alfworld-evidence/20260713-v18-gate-a/case-run-008/`
- Create tracked only after complete Gate PASS: `config/alfworld_v18_runtime_contract.json`
- Update: `task_plan.md`, `findings.md`, `progress.md`

- [x] **Step 1: Re-prove immutable inputs before editing**

On `hkust4`, require HEAD `22cb122e1b186c8e3877cd6504f805685e1bbfc7`, run-006 summary SHA-256 `36bcfe4baef404df4f60452a67091111ea4513d21d1d385846ada64688c63b34`, run-007 summary SHA-256 `9633c3d94c16f5345288f53f022457e6c73fd960fcc3ffb9fdac9f8c3fe07de2`, and run-007 failed result SHA-256 `abd45bc37a76e9d9f98120364da8bdacab99dad8b8174321d021065c593188af`. Require `exact-cases-v3.json`, `discovery-run-008`, `case-run-008` and `helper-archive/pre-discovery-run-008` to be absent. Record the current five formal hashes before any edit:

```text
probe      a120d634c6172905896d8c0a0f3fe76b6b334f873dcba3f43b772ff102a97e70
controller 7bf0db62176610f316455826e3a5e661e4d2804c35845f0857cbf8157c6ea1ac
verifier   cefe847a524ac7a4befcc71e69d605251c9ca5878fc2d3a80dae8baadfd7a22a
matrix-v3  5a0c78167ece3d1586a62bccc828c44ef77a55d9ce2d1884c4f4d460459be03f
```

Print the README hash rather than assuming it. A mismatch stops for root-cause investigation; do not copy local bytes over an unexplained remote difference.

- [x] **Step 2: Freeze the matrix time-control policy and write RED matrix guards**

Parse the current matrix and add only these canonical fields; keep all twenty trial rows and the complete public vocabulary byte-equivalent as parsed values:

```python
matrix.update(
    {
        "setup_time_control_version": "change-time-scale-bracket-v1",
        "setup_slow_time_scale": 0.01,
        "setup_restore_time_scale": 1.0,
        "successful_setup_action_offset": 4,
        "time_scale_effect_control": {
            "trial_id": "episode-0003-candidate-1",
            "sentinel_exact_ids": [
                "Egg|+00.77|+00.95|-00.56",
                "Egg|+00.86|+00.95|-01.88",
                "Lettuce|+01.03|+00.99|-01.79",
            ],
            "initial_temperature": "Cold",
            "changed_temperature": "RoomTemp",
            "scan_teleport_count": 26,
            "post_restore_prefix_count": 7,
        },
    }
)
```

Controller and verifier matrix validators independently require exact keys/types/finite values, exact `0.01/1.0/4`, sorted unique sentinel IDs, the frozen trial ID and counts. Add self-tests that delete each field, swap values/order, use NaN, change one sentinel, or drift one legacy trial row. Run controller/verifier self-tests and require named RED failures `time_control_policy_missing`, `time_control_policy_value`, `time_control_effect_binding` and the existing v2/v3 bijection guard; an unrelated schema exception is not accepted.

- [x] **Step 3: Add RED real-entrypoint and persisted-artifact tests in all three helpers**

Before production logic, add actual-handler AST/behavior assertions for these named contracts:

```text
time_control_enter_before_query
time_control_restore_after_pose_restore
time_control_final_event_authority
time_control_failure_pose_then_time_recovery
time_control_restore_attempted_after_pose_recovery_failure
time_control_setup_count_n_plus_4
time_control_raw_artifact_independent_derivation
time_control_self_consistent_value_order_final_ref_count_mutations
```

The valid synthetic setup sequence with one scan Teleport is exactly:

```json
[
  {"phase":"time_scale_enter","action":"ChangeTimeScale","timeScale":0.01},
  {"phase":"query","action":"GetReachablePositions"},
  {"phase":"scan","action":"TeleportFull","step_index":1},
  {"phase":"restore","action":"TeleportFull"},
  {"phase":"time_scale_restore","action":"ChangeTimeScale","timeScale":1.0}
]
```

Its setup count is 5. The final restored raw event/ref/state/frame must bind the fifth row, never the fourth. Self-consistent mutations rehash all shallow refs before changing slow value, phase order, final event ref, missing recovery row and count; verifier failure must name the intended independent category. Run `py_compile` first, then all three `self-test-v3` commands and require RED only for these new contracts while every older check remains GREEN.

- [x] **Step 4: Extend the producer schema and scan policy minimally**

In the probe, extend setup phase typing to:

```python
SetupPhase = Literal[
    "time_scale_enter", "query", "scan", "restore", "time_scale_restore"
]

def _time_scale_request(value: float) -> dict[str, Any]:
    return {"action": "ChangeTimeScale", "timeScale": value}
```

`_build_scan_policy()` copies the four matrix policy scalars into the hashed policy payload. `_setup_action_row()` accepts all five phases, records the complete locked request/return/raw event/frame/world hashes, increments the counter before the send, and never treats either time-control event as a scan observation. `_validate_setup_return()` maps enter rejection to `scan_time_scale_enter_rejected`, unreadable enter identity/state to `scan_time_scale_enter_unreadable`, restore rejection to `scan_time_scale_restore_rejected`, and unreadable restore identity/state to `scan_time_scale_restore_unreadable`.

- [x] **Step 5: Implement one failure-aware transaction instead of an outer wrapper**

Refactor `execute_reset_snapshot_transaction()` so the same owner sends and records every setup/recovery action. Preserve `setup_trigger` as the first failure and compute `setup_failure` after recovery precedence. The control skeleton is:

```python
slow_request_sent = False
pose_restored = False
normal_time_restored = False
try:
    slow_request_sent = True
    enter = send_and_verify_time_scale(0.01, phase="time_scale_enter")
    query = send_and_verify_query()
    plan = freeze_plan(query)
    observations = execute_every_scan_step(plan)
    entries = derive_entries(observations)
    pose_restore = send_and_verify_pose_restore(initial_pose, initial_world)
    pose_restored = True
    final = send_and_verify_time_scale(1.0, phase="time_scale_restore")
    normal_time_restored = True
    verify_final_normal_event(final, initial_state)
    return publish_from_final_normal_event(final, entries)
except SetupTransactionFailed as primary:
    terminal = recover_pose_then_normal_time(
        primary=primary,
        slow_request_sent=slow_request_sent,
        pose_restored=pose_restored,
        normal_time_restored=normal_time_restored,
    )
    raise SetupTransactionFailed.from_terminal(terminal) from primary
```

Once the enter request was attempted, recovery stops new query/scan work, best-effort sends the exact initial pose unless already proven restored, and always attempts `ChangeTimeScale(1.0)` even if pose recovery raises, returns failure or mismatches. Every recovery send remains in `writer.setup_actions` and `setup_request_count`. No failure publishes snapshot/oracle/cases. `SetupTransactionFailed` carries a frozen `SetupTerminal` with trigger/final code, classification, combined recovery status and issue; `discovery_main()` and `case_main()` serialize that terminal rather than guessing from the first code.

- [x] **Step 6: Make the successful final event authoritative**

After the ordinary pose restore succeeds, send exactly one normal-time restore. Recompute normalized state from that return event and compare initial pose, complete world including raw `ObjectTemperature`, inventory, goal/task state, visibility/bbox projection and frame pixels. Build `restored-state.json`, `restored_event`, `restored_capture`, snapshot `restored_event_ref/restored_world_sha256`, visibility oracles and model-view fixtures only from this fifth-phase event. The pose-restore event remains raw evidence only.

Require:

```python
expected_count = 4 + sum(int(step["send_teleport"] is True) for step in plan["steps"])
assert len(setup_actions) == expected_count
assert setup_actions[-2]["phase"] == "restore"
assert setup_actions[-1]["phase"] == "time_scale_restore"
assert restored_state["raw_event_ref"] == setup_actions[-1]["raw_event_ref"]
```

- [x] **Step 7: Update controller and independent verifier from raw artifacts**

Controller `validate_v2_setup_actions()` and every discovery/case schema require the exact five-phase order, action names, timeScale values, nullable step-index rules and `send_count + 4`. Replace every `>=2`, `1 + send_count + 1`, query/restore-only fixture and count total in real handlers and self-tests.

Verifier extends `V2_SETUP_PHASES`, loads every action raw event/frame, independently checks request/action identity/return/world/pose, proves the final restored state/event/frame ref equals the `time_scale_restore` row, and rejects a pose-restore ref even when hashes/counts are made self-consistent. `verify_restore()`, `_verify_v2_setup_counts()`, `verify_v2_discovery_artifacts()`, `verify_v3_case_setup_artifacts()` and both schema validators must consume the same contract. No consumer may trust producer `recovery_status`, count or final-event booleans without raw derivation.

- [x] **Step 8: Add and externally verify the build-scoped effect-control mode**

Add probe CLI mode `time-scale-effect-control-v3` that uses the matrix's frozen trial, sentinel IDs and 26-step plan in a fresh isolated runtime. It records raw requests/events/frames/world rows for enter, query, all scans, pose restore, normal-time restore and the first seven frozen post-control Teleports. It creates no snapshot or case manifest and always cleans up.

Add verifier CLI mode `time-scale-effect-control-v3` that independently requires both control return codes, exact runtime/build identity, all three sentinels individually `Cold` through the normal-time return event, all three individually `RoomTemp` by post-control step 7, every requested/actual pose, process exit 0 and cleanup success. It must reject one-sentinel-only success, any/best aggregation, missing row and a self-consistently changed sentinel ID. Write output only under `time-scale-effect-control-run-001/`.

- [x] **Step 9: Turn RED GREEN and run immutable compatibility replay**

Run local compile and three self-tests, then copy candidates plus both matrices into one isolated `hkust4:/tmp` directory. From a fail-fast shell run project-config `py_compile`, Ruff lint, Ruff format-check and all self-tests. If formatting changes bytes, synchronize them back and restart every gate from compile.

Run `/tmp/hm_gate_a_post_open_replay.py` with the candidate helpers against immutable run-006 trial 4. Require 15 visibility-oracle rows, nine closed containers, 46 cases, nine post-Open cases with one unique-parent/eight other-container, zero issues and controller/verifier PASS. Replay immutable run-007 trial 5 through the new verifier and require it to remain rejected specifically as temperature-only `scan_world_drift`; adding new schema fields must never reinterpret its old RoomTemp raw events as a successful controlled transaction. Only the separate fresh effect-control run and run-008 may prove the corrected external behavior. Neither replay may modify the original runs.

- [x] **Step 10: Freeze final helper bytes and rewrite executable documentation to run-008**

Rewrite the Gate README so runs 006/007 are immutable failure evidence, successful count is `N+4`, failure recovery/final-event/effect-control gates are explicit, and every executable discovery/case path uses run-008. Create `helper-archive/pre-discovery-run-008` only after formatting and self-tests; copy probe/controller/verifier/matrix/README, record all five hashes, and require archive/formal equality.

Synchronize only those five bytes and the archive to formal `hkust4`. Formal preflight requires HEAD/status whitelist, run-006/run-007 hashes, run-008/manifest absence, archive equality, compile, project Ruff, all self-tests, effect-control verifier PASS and both immutable replays.

- [x] **Step 11: Run and independently verify discovery-run-008 before any case**

Use the current controller CLI with the README's exact arguments, output `discovery-run-008`, and preserve it atomically. Require 20/20 workers, each worker exit 0, no timeout, cleanup success, zero tested/model actions, setup count `N+4`, exact setup phase order, both post-Open outcome envelopes and no issue. Then run `verify_gate_a.py discover`; require exit 0 and an independently derived manifest SHA equal to the new `exact-cases-v3.json`. Do not start any case worker before this verifier passes.

- [x] **Step 12: Disposition case-run after incomplete discovery without inventing a manifest**

Pass the exact independently accepted manifest SHA to controller `run`, output `case-run-008`, and run verifier `run`. Require expected/result case-ID bijection, every fresh setup time/scan/restore transaction, every return code/external terminal state, one Slice identity mode, one Unity build, process exit 0, no timeout and cleanup success per case. A single instance failure stops before Task 2.

Create `config/alfworld_v18_runtime_contract.json` only from `case-run-008/independent-verification.json`; include the time-control policy/version and Gate evidence SHA, but no host/root/target/pose/fixture. Update `Gate-A Locked Decisions` to exactly one proven Slice branch and remove the opposite executable branch before product work.

**Final disposition:** Gate A recovery advanced through `discovery-run-015`, where 19/20 workers passed and the supplemental Slice worker failed. No `exact-cases-v3.json`, case run or runtime contract was created. The user then explicitly removed perfect Gate A as a prerequisite; the checked Step 12 records that closed failure disposition and does not claim a Gate A PASS.

## Historical Appendix: Pre-Run-008 Visible-Only V3 Plan

This appendix preserves the already executed/superseded planning record that led through runs 004-007. It is not an execution checklist: do not run its commands, recreate its paths, or use its stale helper hashes. Task 1 above is the only current Gate A procedure.

**Files:**
- Modify ignored remote: `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- Modify ignored remote: `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- Modify ignored remote: `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`
- Create ignored remote: `var/alfworld-evidence/20260713-v18-gate-a/matrix-v3.json`
- Create ignored remote: `var/alfworld-evidence/20260713-v18-gate-a/exact-cases-v3.json`
- Modify ignored remote: `var/alfworld-evidence/20260713-v18-gate-a/README.md`
- Create tracked after PASS: `config/alfworld_v18_runtime_contract.json`
- Update: `findings.md`, `progress.md`, `task_plan.md`

- **Historical Step 1: Archive current helper bytes and write v3 schema RED tests**

Archive the three current helper files and `matrix-v2.json` under `helper-archive/pre-discovery-run-004/`; record the current hashes `1b1ca9e...`, `58b0b43...`, `7cb32fd...`, and `bb9d3e7...`. Do not alter runs 001-003.

Create `matrix-v3.json` with this exact structured transform; the v2 vocabulary and all twenty trial rows are copied as parsed values, not retyped:

```python
def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


v2 = json.loads(Path("matrix-v2.json").read_text(encoding="utf-8"))
matrix_v3 = {
    "schema_version": "v18-current-visible-v3",
    "kind": "gate_a_trial_matrix",
    "visibility_authorization_version": "current-model-view-strict-visible-v1",
    "visibility_fixture_version": "single-frozen-teleport-v1",
    "scan_algorithm_version": "v18-bounded-scan-1",
    "geometry_policy_version": "v18-nearest-yaw-horizon-1",
    "logical_runtime_scene_rule": "ai2thor-2.1.0:FloorPlanN->FloorPlanN_physics",
    "required_pose_coverage": True,
    "slice_identity_contract_required": True,
    "public_semantic_vocabulary": v2["public_semantic_vocabulary"],
    "public_vocabulary_sha256": v2["public_vocabulary_sha256"],
    "trials": v2["trials"],
}
Path("matrix-v3.json").write_bytes(canonical_json_bytes(matrix_v3) + b"\n")
```

Add a controller self-test that compares all twenty rows on `trial_id`, `traj_data`, `trial_fingerprint`, `source_episode`, `expected_floor_plan`, `identity_status`, `requested_semantic_types`, `required_action_profiles`, and `required_negative_lookup_statuses`; any drift is a named failure.

Add RED mutations that must initially fail for the intended reason:

```text
legacy public_witnesses/public_witnesses_ref accepted
missing same_target_pair_id
visible/invisible rows bind different exact ID, snapshot row or pose freshness
invisible row reaches snapshot lookup, parent resolver, context creation or tested backend
invisible row changes pose/world or becomes terminal
visible fixture lacks return success, actual pose, strict visibility, positive bbox or raw frame
visible fixture frame differs from committed model-view frame
fixture exact ID/pose appears in public result
visible row sends zero or more than one sole-pose navigation request
generic/ordinal case changes frozen full-set order or falls back
post-Open target remains invisible but lookup/backend occurs
same assistant batch advances model-view frame between calls
```

Run all three `self-test-v3` commands and require RED with the named v3 checks, not an unrelated missing-field exception.

- **Historical Step 2: Replace discovery witness artifacts with paired visibility case oracles**

Delete `public-witnesses.json`, `public_witnesses`, `public_witnesses_ref`, `verify_public_witness()` and every required-witness coverage check from all real handlers and synthetic fixtures. Discovery continues the proven reset/query/full-scan/restore transaction and writes one row per reset exact ID.

Freeze a Gate-only record per required physical exact ID:

```python
@dataclass(frozen=True)
class VisibilityCaseOracle:
    same_target_pair_id: str
    trial_id: str
    exact_target_id: str
    public_target_label: str
    frozen_full_set_index: int
    snapshot_entry_sha256: str
    pose_freshness_sha256: str
    invisible_fixture: "VisibilityFixture"
    visible_fixture: "VisibilityFixture | None"
    expected_invisible_error: str
    expected_visible_backend_actions: int


@dataclass(frozen=True)
class VisibilityFixture:
    role: Literal["invisible", "visible"]
    source: Literal["restored_event", "scan_pose"]
    pose: dict[str, float] | None
    expected_strict_visible: bool
    expected_frame_pixel_sha256: str
    expected_world_sha256: str
```

The fixture is GateCase-only. When restored state already has the requested role, fixture action count is zero; otherwise use exactly one frozen scan pose. Every fixture action records request, return, actual pose, world digest, raw event/frame and process status separately from setup/tested/model counts. Direct/addressable targets get same-row invisible and visible cases. Closed descendants stay `unobserved`: their negative case is ordinary invisible, while their positive transition is produced only by the separately frozen public Open sequence in Step 3.

Discovery must finish all twenty trials, even after finding a usable fixture. Any required pose coverage miss, missing paired role, ambiguous ordinal or fixture return/world/frame failure makes that exact trial fail without widening the scan.

- **Historical Step 3: Freeze target-independent post-Open and grounding cases**

Before reading `parentReceptacles`, freeze canonical public container labels from the committed vocabulary and frozen full object order. For each selected closed-child transition, persist the exact public call sequence and its hash:

```json
{
  "calls": [
    {"tool": "robot_go_to", "arguments": {"target": "drawer 1"}},
    {"tool": "robot_manipulate", "arguments": {"action": "open", "object": "drawer 1"}},
    {"tool": "robot_go_to", "arguments": {"target": "mug"}}
  ]
}
```

The chosen container label/order may depend only on public type/full-set order and a committed matrix rule, never hidden child containment. The verifier must mutate child exact ID, pose and parent evidence while requiring the call sequence bytes to stay identical. Wrong-container or still-occluded Open returns the same zero-action `target_not_visible`; only an Open return event that itself makes the exact child strict-visible may enable one unique-parent pose read and one move.

Generate explicit cases for generic labels with multiple visible peers and ordinals whose exact member is visible, invisible and missing. The expected public failure bytes are identical for hidden/missing ordinals except enumerated transport metadata.

- **Historical Step 4: Wire v3 through controller, case worker and independent verifier**

Update actual CLI/handler call graphs, not only pure validators:

```text
discover -> fresh reset transaction -> visibility-case-oracles -> exact-cases-v3
verify discover -> reload every raw setup/fixture artifact -> independently derive manifest
run -> verify explicit manifest SHA -> fresh reset transaction per case -> fixture -> tested action
verify run -> reload raw setup/fixture/action artifacts -> independently derive every terminal fact
```

`case_main()` must consume `matrix-v3.json`, repeat reset/query/all scan Teleports/restore, and start from the verified restore event. For invisible cases it emits `target_not_visible` before any lookup. For visible cases it applies the Gate fixture, marks that exact frame as the simulated latest model-visible observation, then reads and sends the one snapshot pose. The independent verifier imports no HomeMaster serializer/resolver/classifier and never trusts worker-reported counts or booleans without raw evidence.

On complete run PASS, `independent-verification.json` must include one normalized runtime object, one `slice_identity_modes` list, the three algorithm/authorization version strings, logical/runtime scene rule, expected/result case IDs, per-case evidence refs and the independently recomputed counts/hashes. Task 1 Step 7 reads only those defined fields.

Add AST/call-graph gates proving `discover_cases()`, `case_main()`, `verify_discovery_run_artifacts()` and `verify_case_bundle()` all invoke the v3 validators and cannot reach public-witness code. A valid complete fixture must produce zero failures; independently tamper snapshot, model-view hash, fixture event and action count and require the precise rejection category.

- **Historical Step 5: Run helper compile, lint, format and self-tests**

Run on `hkust4`:

```bash
env -C /data1/haodong2/weilin/red_bird/Homemaster \
  /data0/yuqiao/envs/hm_alfworld/bin/python -m py_compile \
  var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py \
  var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py \
  var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py
env -C /data1/haodong2/weilin/red_bird/Homemaster \
  /data0/yuqiao/envs/hm_alfworld/bin/python -m ruff check \
  var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py \
  var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py \
  var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py
env -C /data1/haodong2/weilin/red_bird/Homemaster \
  /data0/yuqiao/envs/hm_alfworld/bin/python -m ruff format --check \
  var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py \
  var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py \
  var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py
```

Then run probe, controller and verifier `self-test-v3`. Expected: all commands exit 0; mutation totals and wiring totals equal their explicit expected counts.

- **Historical Step 6: Run real discovery-run-004 and case-run-004**

Use a new empty output directory and canonical paths:

```bash
env -C /data1/haodong2/weilin/red_bird/Homemaster -u PYTHONPATH \
  PYTHONNOUSERSITE=1 ALFWORLD_DATA=/data1/haodong2/weilin/red_bird/alfworld/data \
  /data0/yuqiao/envs/hm_alfworld/bin/python \
  var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py discover \
  --matrix var/alfworld-evidence/20260713-v18-gate-a/matrix-v3.json \
  --case-manifest var/alfworld-evidence/20260713-v18-gate-a/exact-cases-v3.json \
  --alfworld-root /data1/haodong2/weilin/red_bird/alfworld \
  --config /data1/haodong2/weilin/red_bird/alfworld/configs/base_config.yaml \
  --expected-home-head 22cb122e1b186c8e3877cd6504f805685e1bbfc7 \
  --output var/alfworld-evidence/20260713-v18-gate-a/discovery-run-004
```

Run `verify_gate_a.py discover`, require exit 0 and its independently computed manifest hash to equal `exact-cases-v3.json`. Pass that exact hash to `run_gate_a.py run`, write `case-run-004`, then run `verify_gate_a.py run`. Require every expected/result row one-to-one, every worker exit 0, no timeout, complete cleanup and per-instance PASS. A single failed target/action stops before Task 2 product edits.

- **Historical Step 7: Freeze only the proven runtime contract**

Create `config/alfworld_v18_runtime_contract.json` from the verified `case-run-004/independent-verification.json` with this closed transform:

```python
verified_bytes = Path("case-run-004/independent-verification.json").read_bytes()
verified = json.loads(verified_bytes)
slice_modes = tuple(sorted(set(verified["slice_identity_modes"])))
if len(slice_modes) != 1 or slice_modes[0] not in {"preserved", "replaced_unique"}:
    raise ValueError("Gate A did not prove exactly one Slice identity mode")
runtime_contract = {
    "schema_version": 1,
    "scan_algorithm_version": verified["scan_algorithm_version"],
    "geometry_policy_version": verified["geometry_policy_version"],
    "visibility_authorization_version": verified["visibility_authorization_version"],
    "alfworld_version": verified["runtime"]["alfworld_version"],
    "ai2thor_version": verified["runtime"]["ai2thor_version"],
    "logical_runtime_scene_rule": verified["logical_runtime_scene_rule"],
    "slice_identity_mode": slice_modes[0],
    "unity_build_sha256": verified["runtime"]["unity_build_sha256"],
    "gate_a_evidence_sha256": sha256(verified_bytes).hexdigest(),
}
Path("config/alfworld_v18_runtime_contract.json").write_bytes(
    canonical_json_bytes(runtime_contract) + b"\n"
)
```

Do not retain both Slice modes. The output contains no path, host, credential, exact target, fixture or pose.

- **Historical Step 8: Stop and lock one source-level Slice behavior before product work**

Read the independently verified `slice_identity_modes` value and update `Gate-A Locked Decisions` to the one proven branch. Add the matching exact test names to Task 7 and delete the opposite branch wording. Task 2's runtime loader exposes a source constant `EXPECTED_SLICE_IDENTITY_MODE` with that one literal and verifies the JSON value equals it; no production `if mode == ... else ...` behavior switch is permitted. Keep all Slice action/field symbols `UNVERIFIED` if Gate A does not produce exactly one mode, and stop before Task 2.

## Task 2: Establish Current Baseline, Closed Types And Safe Manifests

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/types.py:1-659`
- Modify: `src/homemaster/benchmarking/alfworld/__init__.py`
- Create: `src/homemaster/benchmarking/alfworld/trial_selection.py`
- Create: `src/homemaster/benchmarking/alfworld/runtime_contract.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_trial_selection.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_runtime_contract.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_types.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_outcome.py`

- [x] **Step 1: Re-run and record the unmodified product baseline**

On `hkust4`, before syncing product changes, run focused tests, full pytest, compileall and Ruff report-only checks against HEAD `22cb122`. Require results to match the recorded baseline or investigate the difference before editing. Store commands, process return codes and complete logs under a new ignored `var/alfworld-evidence/20260713-v18-baseline/pre-product-001/` directory.

- [x] **Step 2: Write RED exhaustive closed-type tests**

Add constructor tables for every `SetupFailureCode`, including `scan_time_scale_enter_rejected`, `scan_time_scale_enter_unreadable`, `scan_time_scale_restore_rejected` and `scan_time_scale_restore_unreadable`, plus every `GoalAdvanceFailureCode`, `ToolExecutionError`, `EpisodeClassification`, recovery/cleanup/disposition and THOR/TextWorld ready/terminal combination. `recovery_status=restored` for THOR means both exact pose/world/frame and normal `timeScale=1.0`; either uncertain dimension rejects a ready result. Assert totals directly:

```python
assert result.total_backend_action_count == (
    result.setup_backend_action_count + result.backend_action_count
)
assert result.total_external_action_count == (
    result.setup_backend_action_count
    + result.benchmark_control_action_count
    + result.backend_action_count
)
```

Test `AlfworldExecutionFeedback` invariants: success forbids error/classification; non-`ok` required reads force terminal uncertain; non-terminal errors have no Episode classification; `failure_reason` is a read-only projection. Test taskset root terminal/not-run ownership and zero counts per blocked subtask.

- [x] **Step 3: Write RED portable selection and runtime-contract tests**

`TrialSelectionEntry` accepts only canonical POSIX-relative trial ID, content hash, expected logical scene, goal identity/fingerprint and identity status. Reject unknown keys, absolute paths, backslashes, NUL, empty/dot/dot-dot segments, duplicate/order drift, wrong bytes hash and symlink escape. Explicitly reject `target`, `anchor`, `pose`, `action_profile`, `visibility_fixture`, `terminal_oracle`, `snapshot` and `containment` keys.

Load the runtime contract with a supplied runtime identity and reject version/build/scene-rule/Slice mismatch before external reset. Two different injected roots with identical relative IDs and bytes must yield identical portable fingerprints.

- [x] **Step 4: Run the RED type tests**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_types.py \
  tests/homemaster/benchmarking/test_alfworld_outcome.py \
  tests/homemaster/benchmarking/test_alfworld_trial_selection.py \
  tests/homemaster/benchmarking/test_alfworld_runtime_contract.py
```

Expected: failures are missing closed types/loaders, not unrelated imports.

- [x] **Step 5: Implement the closed types and loaders**

Implement frozen reset/control/feedback records exactly from spec sections 6.0, 6.6, 10.1, 10.2 and 10.5. The key public shapes are:

```python
@dataclass(frozen=True)
class AlfworldResetResult:
    backend_kind: AlfworldBackendKind
    ready: bool
    state: AlfworldEnvState | None
    scene_generation: int | None
    goal_generation: int | None
    scene_reset_fingerprint: str | None
    goal_trial_fingerprint: str | None
    snapshot_sha256: str | None
    snapshot_ref: str | None
    setup_trigger: SetupFailureCode | None
    setup_failure: SetupFailureCode | None
    classification: EpisodeClassification | None
    score_eligible: bool
    setup_backend_action_count: int
    recovery_status: SetupRecoveryStatus
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None


@dataclass(frozen=True)
class AlfworldExecutionFeedback:
    success: bool
    action: AlfworldAction
    object: str | None
    target: str | None
    inventory: tuple[str, ...] | None
    inventory_status: ExecutionReadStatus
    object_state: ObjectExecutionState | None
    object_state_status: ExecutionReadStatus
    target_state: TargetExecutionState | None
    target_state_status: ExecutionReadStatus
    state_changed: bool | None
    state_read_status: ExecutionReadStatus
    error: ToolExecutionError | None
    terminal: bool
    classification: EpisodeClassification | None
    score_eligible: bool
    detail_code: SafeDetailCode | None
```

Define the closed feedback/reset/control/result types and add backward-compatible zero-valued result counters so current consumers remain runnable during the migration. Do not add `AlfworldStepResult.execution_feedback` yet; Task 7 switches every real/test constructor in one atomic migration and then makes the field required with no default factory. This task must leave the pre-existing focused suite green.

- [x] **Step 6: Implement strict manifest/runtime loading and run GREEN**

Use `Path.resolve()` plus `relative_to(root.resolve())` containment, reject symlinks escaping root, and hash canonical relative ID bytes plus file bytes. Runtime contract loading validates exact JSON keys and the Gate evidence hash. Run Step 4; expected all selected tests PASS.

## Task 3: Implement Pure Scan Plan, Snapshot And Lookup State

**Files:**
- Create: `src/homemaster/benchmarking/alfworld/pose_snapshot.py`
- Create: `src/homemaster/benchmarking/alfworld/object_vocabulary.json`
- Modify: `pyproject.toml`
- Create: `tests/homemaster/benchmarking/test_alfworld_pose_snapshot.py`

- [x] **Step 1: Write RED canonical plan/addressability tests**

Cover finite reachable parsing, negative-zero normalization, separate raw-payload/canonical hashes, addressability reason priority, reciprocal-containment corruption, malformed cache, step-0 zero-action semantics, contiguous indices, equal-pose provenance merge, complete pose hash including `y`, at most one geometry pose per cache-missing addressable ID and deterministic input shuffles.

Use this invariant in fixtures:

```python
assert [step.index for step in plan.steps] == list(range(len(plan.steps)))
assert plan.steps[0].send_teleport is False
assert all(step.send_teleport for step in plan.steps[1:])
assert len({step.pose for step in plan.steps}) == len(plan.steps)
```

- [x] **Step 2: Write RED snapshot/store overlay tests**

Test one published row per reset exact ID and only these combinations: `ok/addressable/pose`, `coverage_miss/addressable/no pose`, `unobserved/non-addressable/no pose`. Test atomic lookup identity and distinct overlays for `relocated`, `absent`, `malformed`, `stale` and `error`; known gateway movement is relocated, unknown drift is stale. Shuffle object/cache/containment inputs and require identical canonical bytes/hash.

- [x] **Step 3: Run RED**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_pose_snapshot.py
```

Expected: import failure for the new module.

- [x] **Step 4: Implement pure contracts**

Implement these immutable boundaries:

```python
@dataclass(frozen=True, order=True)
class OraclePose:
    x: float
    y: float
    z: float
    rotation: float
    horizon: float


@dataclass(frozen=True)
class ScanPoseStep:
    index: int
    pose: OraclePose
    send_teleport: bool
    provenances: tuple[ScanPoseProvenance, ...]


class OraclePoseStore(Protocol):
    def get_pose(
        self,
        *,
        scene_generation: int,
        scene_reset_fingerprint: str,
        exact_anchor_id: str,
    ) -> OraclePoseLookup: ...


@dataclass(frozen=True)
class OraclePoseSnapshotEntry:
    exact_object_id: str
    status: OracleReadStatus
    addressable: bool
    addressability_reason: AddressabilityReason
    pose: OraclePose | None
    pose_sha256: str | None
    pose_freshness_sha256: str
    source_kind: SnapshotPoseSource
    evidence_ref: str | None


@dataclass(frozen=True)
class OraclePoseSnapshot:
    scene_generation: int
    scene_reset_fingerprint: str
    scan_plan_sha256: str
    initial_event_ref: str
    restored_event_ref: str
    entries: tuple[OraclePoseSnapshotEntry, ...]
    snapshot_sha256: str
```

`ScanPolicyInput` includes the frozen `setup_time_control_version`, slow `0.01`, restore `1.0` and successful action offset `4`; all four enter `scan_policy_sha256` before the first external action. `SceneScanPlanBuilder` accepts that one typed input and has no task text, requested target, action profile, expert, Provider or Gate fixture field. `FrozenOraclePoseStore` publishes by immutable reference swap; action-time lookup never reads cache/controller data directly. `OraclePoseSnapshot.restored_event_ref` must bind the normal-time return event rather than the pose-restore event.

- [x] **Step 5: Package vocabulary and run GREEN**

Commit the exact 25-item v3 vocabulary and its canonical hash. Extend setuptools package data with `benchmarking/alfworld/*.json`, load via `importlib.resources`, and rerun Step 3. Expected: PASS.

## Task 4: Add One Gateway, Typed Reset Transaction And Adapter Lifecycle

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/execution.py:1-1072`
- Create: `src/homemaster/benchmarking/alfworld/reset_transaction.py`
- Modify: `src/homemaster/benchmarking/alfworld/env_adapter.py:192-470`
- Modify: `src/homemaster/benchmarking/alfworld/runner.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_reset_transaction.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_gateway.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_runner.py`

- [x] **Step 1: Write RED gateway and reset sequence tests**

With a scripted backend assert exact order `capture -> ChangeTimeScale(0.01) -> query -> every frozen scan Teleport -> pose restore -> ChangeTimeScale(1.0) -> publish`, successful setup count `N+4`, no early stop, per-step action identity/return/actual/world checks, final-event frame/visibility restoration and no partial publish. Parameterize every setup code, failure at each send, pose-recovery failure followed by a still-attempted normal-time restore, uncertainty precedence, cleanup Runtime upgrade, TextWorld not-applicable and closed/quarantined reuse rejection.

The sole query is the external `GetReachablePositions` request. It must preserve agent pose/world, and query return rejection versus unreadable `actionReturn/reachablePositions` must remain distinct typed failures.

- [x] **Step 2: Define the sole backend/gateway boundary**

Add these new contracts without routing action-time navigation/manipulation through them yet:

```python
class OracleExecutionBackend(Protocol):
    def capture_event(self) -> ExternalEventRead: ...
    def send(self, request: ExternalActionRequest) -> ExternalActionResult: ...
    def close(self) -> CleanupResult: ...


class OracleActionGateway:
    def execute_setup_time_control(self, value: float) -> SetupActionResult: ...
    def execute_setup_query(self) -> SetupActionResult: ...
    def execute_setup_teleport(self, step: ScanPoseStep) -> SetupActionResult: ...
    def execute_restore(self, pose: OraclePose) -> SetupActionResult: ...
    def execute_navigation(self, request: NavigationActionRequest) -> NavigationActionResult: ...
    def execute_manipulation(self, request: ManipulationActionRequest) -> ManipulationActionResult: ...
```

Only the Adapter backend implements the new `send()` interface. During Task 4, only reset time-control/query/scan/restore may call that interface, and those setup sends must all pass through the setup gateway methods. Every new gateway call writes phase/global sequence, complete request hash including `timeScale`, raw event ref/hash, duration, return status and independent before/after state hashes.

Keep the enumerated old action-time navigation/manipulation send sites temporarily reachable only from their existing consumers until Tasks 6 and 7 atomically switch those consumers. They are not a runtime fallback from the new path: no new call site may select between old/new implementations. Task 4 enforces a setup-only sole-sender guard; Task 6 removes all old navigation sends; Task 7 removes all remaining manipulation sends and turns on the global sole-gateway guard.

- [x] **Step 3: Implement reset transaction without Adapter policy duplication**

`AlfworldResetTransaction.run(expected_selection)` captures reset identity, freezes the time/scan policy before `ChangeTimeScale(0.01)`, freezes the full plan before the first scan Teleport, executes all steps, restores the exact pose while slow, then sends `ChangeTimeScale(1.0)`. It verifies the final normal-time event against initial pose/world/visibility/bbox/frame and only then installs `FrozenOraclePoseStore` exactly once. On failure after the enter attempt it stops new scan sends, best-effort restores pose, always attempts normal time even when pose recovery fails, preserves trigger versus final code, closes/quarantines on uncertainty and returns `AlfworldResetResult` with the actual count including recovery sends.

- [x] **Step 4: Bind Adapter lifecycle and goal advance**

Change `reset()` to return `AlfworldResetResult`; add `ready/closed/quarantined` lifecycle and idempotent `close()`. Change `advance_goal()` to accept a verified `TrialSelectionEntry`, capture before/after scene-only digest around the one `set_task` control action, preserve snapshot/scene generation, increment goal generation, invalidate context and return `AlfworldGoalAdvanceResult`. Never catch a failed goal read and substitute `won=False` as ready.

In the same change, update ordinary/taskset Runner call sites to unwrap only `ready` results and to stop before Provider construction on a terminal result. Task 9 later completes root ledgers, metrics and CLI pinning; Task 4 must not leave Runner expecting the old `AlfworldEnvState` return shape.

- [x] **Step 5: Audit every implementation and run GREEN**

Enumerate real Adapter, scripted backend and all Fake/Mock implementations; assert every public Protocol method and tagged return exists. Run:

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_reset_transaction.py \
  tests/homemaster/benchmarking/test_alfworld_gateway.py \
  tests/homemaster/benchmarking/test_alfworld_env_adapter.py \
  tests/homemaster/benchmarking/test_alfworld_runner.py
```

Expected: PASS with no reset time-control/query/scan/restore send outside the new setup gateway. Existing action-time direct sends remain an explicit failing input to the not-yet-enabled global guard until Tasks 6-7 remove them.

## Task 5: Bind Successful Provider Attempts To The Model's Current View

**Files:**
- Create: `src/homemaster/providers/attempts.py`
- Modify: `src/homemaster/providers/errors.py`
- Modify: `src/homemaster/providers/llm_client.py:62-310`
- Modify: `src/homemaster/providers/transports/anthropic.py`
- Modify: `src/homemaster/agent/generic_runtime.py:95-544`
- Create: `src/homemaster/benchmarking/alfworld/model_view.py`
- Create: `tests/fixtures/providers/mimo_sse_message_delta_before_start.json`
- Create: `tests/homemaster/benchmarking/test_alfworld_model_view.py`
- Modify: `tests/homemaster/test_llm_client.py`
- Modify: `tests/homemaster/test_generic_agent_runtime.py`

- [x] **Step 1: Write RED Provider-attempt and frame-commit tests**

Test exact request snapshot identity, actual API attempt ID, image file-byte hash, stripped-image flag, assistant/session/tool/external commit flags and closed error cause. A successful assistant tool call commits the image from that exact request once. Failed, partial, stripped or unknown attempts do not authorize an image.

Test two tool calls in one assistant response: both see the same committed image even when call one creates a new event/frame. Only a later successful Provider response containing that new image advances the committed view. Add duplicated-identical PNG/pixel frames at different event sequences and a request containing multiple images; stable binding selects the last outbound ALFWorld frame by message/block order without relying on hash uniqueness.

- [x] **Step 2: Define immutable attempt and view records**

```python
@dataclass(frozen=True)
class OutboundImageBinding:
    message_index: int
    block_index: int
    frame_binding_id: str | None
    content_sha256: str


@dataclass(frozen=True)
class ProviderAttemptRecord:
    model_attempt_id: str
    request_sha256: str
    outbound_images: tuple[OutboundImageBinding, ...]
    stripped_images: bool
    response_completed: bool
    error_type: str | None
    cause_code: str | None


@dataclass(frozen=True)
class AttemptCommitState:
    assistant_committed: bool
    tool_dispatch_committed: bool
    external_action_committed: bool


class ProviderAttemptSink(Protocol):
    def record_attempt(self, record: ProviderAttemptRecord) -> None: ...


@dataclass(frozen=True)
class CommittedModelView:
    model_attempt_id: str
    request_sha256: str
    frame_binding_id: str
    frame_content_sha256: str
    frame_pixel_sha256: str
    event_sequence: int
```

`FrameLedger` assigns an opaque internal `frame_binding_id` and records event sequence, PNG content hash and decoded RGB pixel hash when Adapter persists a frame. The corresponding `ContentBlock.metadata` carries that ID locally; transport serializers must omit it from the Provider body. `ProviderAttemptRecord` preserves ordered message/block bindings while independently hashing the exact serialized image bytes sent. `AlfworldModelViewObserver` chooses the last outbound ALFWorld frame binding in canonical message/block order, resolves by binding ID, then requires content and pixel equality. Identical bytes at multiple event sequences are therefore valid and unambiguous. GenericRuntime creates one attempt sink per attempt and owns `AttemptCommitState`; LLMClient records into that explicit sink and never claims assistant/tool/external commit facts. No mutable process-global or transport-wide `last_attempt` slot is allowed.

- [x] **Step 3: Refactor transport retries to one auditable boundary**

Make one `LLMClient` stream attempt produce one `ProviderAttemptRecord` through the call-scoped sink; remove hidden multi-attempt success that GenericRuntime cannot count. Set SDK `max_retries=0` and remove the image-stripping retry because it changes request bytes and destroys the visible-image contract. Add closed `error_type + cause_code` mapping for transient network, rate limit and `stream_protocol_error/message_delta_before_message_start`. Parse the committed historical SSE fixture in `AnthropicTransport` and reject `message_delta` before `message_start` with that exact cause. GenericRuntime freezes messages/system/tools once, retries at most once with a new attempt ID only when the first attempt is explicitly retryable and all three commit flags are false/known. The retry bytes hash must equal the first request hash; attempt one uses configured key index 1 and the sole retry uses key index 2 when present, otherwise the same key.

Auth, generic provider errors, unknown causes, partial assistant/session state, tool dispatch or external action never retry. Preserve non-ALFWorld callers through the same generic contract; do not import ALFWorld into provider/runtime modules.

- [x] **Step 4: Commit model view at the correct point**

After a complete assistant response is appended and before its tool batch dispatch begins, GenericRuntime marks `assistant_committed=True` and calls the injected generic model-view observer with the successful `ProviderAttemptRecord`. It calls once per assistant response, not once per tool. Dispatcher updates tool/external commit state through its observer. Missing model-view observer is allowed for unrelated runtimes; ALFWorld visual Runner always injects one and treats missing/invalid commit as stale visibility.

- [x] **Step 5: Run GREEN**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_model_view.py \
  tests/homemaster/test_llm_client.py \
  tests/homemaster/test_generic_agent_runtime.py
```

Expected: PASS, exactly two attempts only in allowed retry cases, and no later same-batch frame authorization.

## Task 6: Implement Current-Visible-First Exact Navigation

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/model_view.py`
- Modify: `src/homemaster/benchmarking/alfworld/execution.py`
- Modify: `src/homemaster/benchmarking/alfworld/env_adapter.py:334-1064`
- Rewrite navigation cases in: `tests/homemaster/benchmarking/test_alfworld_execution.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_navigation.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`

- [x] **Step 1: Write RED ordering and zero-call tests**

Use spies for `VisibleObjectView`, `OraclePoseStore`, parent resolver, context factory and gateway. For every physical type and both `ok/unobserved` snapshot rows assert:

```python
assert result.error == "target_not_visible"
assert result.terminal is False
assert pose_store.calls == []
assert parent_resolver.calls == []
assert context_factory.calls == []
assert gateway.navigation_calls == []
```

Assert trace order is `visibility_gate_started/result` before lookup/move/context. A mismatched/missing model frame is terminal `execution_state_uncertain`, not ordinary invisibility.

- [x] **Step 2: Write RED grounding and sole-pose tests**

Cover generic first-current-visible selection in frozen order; explicit ordinal visible/invisible/missing with no fallback; matching inventory object -> `object_already_held`; invalid public label -> `target_not_found`. Cover direct `ok`, visible `unobserved/relocated/absent` unique reciprocal innermost parent, zero/multiple parent failure, direct coverage miss and malformed/stale/error lookup.

The closed zero-action lookup mappings are explicit: direct `coverage_miss -> oracle_pose_missing`, malformed -> `oracle_pose_malformed`, stale/error -> `execution_state_uncertain`, and visible `unobserved/relocated/absent` without exactly one valid parent -> `oracle_anchor_unresolved`. These errors never trigger another target, parent or pose.

For a visible target, assert one atomic lookup and at most one navigation request. External failure with full unchanged state is Harness navigation failure; changed/unknown state or pose mismatch is uncertain; success with final exact target invisible is terminal `oracle_target_not_visible`. No branch changes target or pose.

- [x] **Step 3: Implement strict observation and target lock**

`VisibleObjectView` returns `ObjectObservationRead` with event/model frame hashes, equality, metadata visibility, normalized finite bbox area and strict-visible. Only `status=ok + frame_matches_model_view + visible + positive area` authorizes. The target resolver combines this view with the frozen `SceneObjectIndex`; it never queries snapshot or containment while selecting an invisible target.

- [x] **Step 4: Implement anchor, executor and context**

Implement `OracleExecutionContext` with scene/goal generation, source/current event sequence, exact requested/anchor IDs, snapshot/pose/anchor-state hashes, actual pose, final event hash/ref and `active/consumed/invalid`. `NavigationAnchorResolver` reads parent only for the same already-visible `unobserved/relocated/absent` target. `OracleNavigationExecutor` follows exactly:

```text
read committed current view -> lock exact target -> atomic pose lookup
-> optional unique parent lookup -> one gateway move -> return/pose/final visibility gate
-> create active context
```

- [x] **Step 5: Bind Adapter and remove navigation search paths**

Route THOR `go_to_target()` only through the executor. Remove ALFWorld `virtual_navigate()`, `find_object()`, hidden source search, `_teleport_candidates`, `_single_target_teleport_candidates`, `_navigation_budget_stop`, navigation budget constants/state and `_last_go_to_object_id`. Keep generic home-domain `robot_navigate`; only ALFWorld paths are removed.

Enable a navigation-scoped sender guard proving every navigation `TeleportFull` reaches the Adapter backend only from `OracleActionGateway.execute_navigation`; manipulation sends remain explicitly outside this scoped guard until Task 7.

- [x] **Step 6: Run GREEN**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_model_view.py \
  tests/homemaster/benchmarking/test_alfworld_navigation.py \
  tests/homemaster/benchmarking/test_alfworld_execution.py \
  tests/homemaster/benchmarking/test_alfworld_env_adapter.py
```

Expected: PASS; invisible calls have zero downstream calls and visible calls send exactly one frozen pose.

## Task 7: Route Every Manipulation Through Exact Context And Typed Feedback

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/execution.py`
- Modify: `src/homemaster/benchmarking/alfworld/env_adapter.py:1065-2253`
- Modify: `tests/homemaster/benchmarking/test_alfworld_execution.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_tools.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_feedback.py`

- [x] **Step 1: Write RED context-state and terminal-state matrices**

Test navigation creates active; zero-action preconditions preserve; idempotent Open/Close keep; successful Open/Close/Use rebase; Slice follows only the single source-level behavior written in `Gate-A Locked Decisions`; Take/Put/macro consume; reset/advance/new move/event gap/unrelated action/uncertainty invalidate. The opposite Slice behavior must be absent from source and tests.

For Take/Open/Close/Put/Use/Slice and every Heat/Cool/Clean subaction cover: return+correct state success, return failure+complete unchanged state deterministic Harness failure, partial/missing/contradictory state uncertainty. Assert exact inventory, `isPickedUp`, reciprocal parent/child, open/toggle/slice/heat/cool/clean state, actual pose, goal and frame. No branch sends N+1.

- [x] **Step 2: Implement action-specific gateway evaluation**

Freeze exact action/object/target/context and complete before-state before send. The gateway performs one request, captures after-state, evaluates return status plus the spec terminal predicate, and only then rebases/consumes context. Closed Put returns `target_closed` before send with authoritative inventory. Macro request tuples are frozen once and stop on first failure; partial mutation is uncertain and never triggers compensation.

- [x] **Step 3: Construct the sole typed feedback at Adapter boundary**

Every action returns one valid `AlfworldExecutionFeedback`; `inventory`, object state, target state and state change come only from typed external reads. `None` requires an explicit non-`ok` status. `detail_code` maps through a closed safe template table keyed by the closed non-terminal/terminal error set; templates may mention only the public requested object/target labels and never raw external text. TextWorld produces the same envelope with THOR-only statuses `not_applicable`.

In the same atomic change, add required `AlfworldStepResult.execution_feedback` with no default factory and update every Adapter, TextWorld, Fake and test constructor. Make `failure_reason` a read-only projection of the typed error/classification; no constructor may supply an independent conflicting string.

Use `rg 'AlfworldStepResult\(' src tests` as the constructor inventory. The current six constructors in `test_alfworld_tools.py` are part of this task, not deferred to Task 8; the interface audit fails on any constructor without explicit typed feedback.

- [x] **Step 4: Delete old manipulation authorities**

Remove `PoseContext`, `ExecutionBudget`, `ManipulationExecutor`, local Put candidate loop/router, candidate hashes/budgets, `_put_step_result` tool-arg result channel, type-level target reselection and direct `_thor_step` call sites outside the Adapter backend. Preserve pure terminal predicates that match the new gateway and move them under exact typed names.

After the last manipulation consumer switches, enable the global sender guard: every Gate-listed THOR request, including setup, navigation and macro subactions, must enter the Adapter backend from exactly one phase-specific `OracleActionGateway` method. No legacy direct send is allowed to remain.

- [x] **Step 5: Run GREEN and interface audit**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_execution.py \
  tests/homemaster/benchmarking/test_alfworld_gateway.py \
  tests/homemaster/benchmarking/test_alfworld_feedback.py \
  tests/homemaster/benchmarking/test_alfworld_env_adapter.py \
  tests/homemaster/benchmarking/test_alfworld_tools.py
```

Expected: PASS and all public actions produce typed feedback through the sole gateway.

## Task 8: Make Typed Feedback The Only Model Path And Remove ALFWorld Bypasses

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/tools.py:1-814`
- Modify: `src/homemaster/benchmarking/alfworld/registry.py`
- Modify: `src/homemaster/benchmarking/alfworld/prompt.py`
- Modify: `src/homemaster/benchmarking/alfworld/translator.py`
- Modify: `src/homemaster/benchmarking/alfworld/tracing.py`
- Modify: `src/homemaster/tools/dispatcher.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_{tools,registry,prompt,translator,tracing}.py`
- Modify: `tests/homemaster/test_tool_dispatcher.py`

- [x] **Step 1: Write RED real-chain projection and no-leak tests**

Pass an Adapter-produced StepResult through real tools and Dispatcher. Assert model JSON equals `execution_feedback.to_model_payload()` byte-for-byte and cannot change when `tool_args`, human inventory, debug text or raw error mutates. Inspect system/initial prompt, session, ToolSpec, result blocks, image bytes/path and retry request; deny exact ID, pose, snapshot, containment, fixture, hidden counts/parents, source hashes, absolute path and raw THOR detail.

- [x] **Step 2: Write RED registry/dispatch observer tests**

Direct ALFWorld dispatch of `robot_find_object` and `robot_navigate` returns `unknown_tool` and zero Adapter/backend. Parameterize validation, unknown, cancellation, terminal-blocked, normal, executor exception and later calls in the same batch through a generic `ToolDispatchObserver`. Every input call is counted once; only an executed typed result contributes model backend/env counts; first terminal wins.

- [x] **Step 3: Implement sole serializer and observer**

Tools forward only the feedback safe payload and matching frame block. Dispatcher binds `tool_call_id` but never `setdefault`s or overwrites typed success/error. Executor exceptions become observer-owned terminal `runtime_failure`, not a normal ToolResult. Dispatcher remains domain-neutral and imports no ALFWorld module.

- [x] **Step 4: Remove only ALFWorld legacy tools**

Remove ALFWorld ToolSpecs/executors/registry/prompt/translator paths for `robot_find_object` and `robot_navigate`, action-specific `_put_visible_*`, `_go_to_visible_payload`, `_visual_error` reconstruction and dict fallbacks. Preserve unrelated generic home-domain tools and their tests.

- [x] **Step 5: Run GREEN**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_tools.py \
  tests/homemaster/benchmarking/test_alfworld_registry.py \
  tests/homemaster/benchmarking/test_alfworld_prompt.py \
  tests/homemaster/benchmarking/test_alfworld_translator.py \
  tests/homemaster/benchmarking/test_alfworld_tracing.py \
  tests/homemaster/test_tool_dispatcher.py \
  tests/homemaster/test_domain_home_tools.py
```

Expected: PASS; generic home `robot_navigate` remains, ALFWorld registry exposes only `robot_go_to` for navigation.

## Task 9: Implement Runner Control Boundary, Trial Pinning, Metrics And CLI

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/runner.py:1-755`
- Modify: `src/homemaster/benchmarking/alfworld/types.py`
- Modify: `src/homemaster/benchmarking/alfworld/tracing.py`
- Modify: `src/homemaster/benchmarking/alfworld/taskset_loader.py`
- Modify: `src/homemaster/benchmarking/alfworld/traj_index.py`
- Modify: `src/homemaster/cli/app.py`
- Modify: `src/homemaster/cli/benchmark_alfworld.py`
- Create: `config/alfworld_v18_regression_trials.json`
- Modify: `tests/homemaster/benchmarking/test_alfworld_{runner,outcome,tracing}.py`
- Modify: `tests/homemaster/test_cli_benchmark_alfworld.py`
- Modify: `tests/homemaster/test_generic_agent_runtime.py`

- [x] **Step 1: Write RED ordinary/taskset lifecycle tests**

Ordinary Runner creates a fresh pinned Adapter for every manifest entry. It creates internal trace/outcome before reset, but `episode_started`, Provider factory/send, Runtime, Dispatcher and prompt only after ready. Setup terminal has zero Provider/API/tool/model/env/invalid counts and closes/quarantines that Adapter; Episode 2 uses a new identity.

Taskset root owns the one setup ledger and append-only control ledger. Reset terminal marks every subtask `not_run/taskset_setup_failure`; goal advance terminal marks current `goal_advance_failure` and later `prior_infrastructure_failure`; each not-run row has `classification=None` and every count zero.

- [x] **Step 2: Write RED classification/count/metric tests**

Parameterize `RuntimeTermination(status, finish_reason, error_code)` over every spec code and inconsistent combination. Assert setup/control/model backend, tool, env, invalid, total backend and total external counts independently. Assert `raw_success_rate`, `evaluation_valid_coverage`, `agent_success_rate_on_valid`, `harness_coverage`, `provider_availability`, `runtime_availability`, `cancelled_episodes` and `formal_score_available`; unknown never defaults to Agent.

- [x] **Step 3: Write RED manifest/deployment/model-view integration tests**

Require exact `TrialSelectionManifest` entry before reset and reject any product file-open/import/constructor attempt to access GateCase files. Run two fake root/host configurations and require equal portable scene/goal/plan/snapshot identities and relative evidence replay. Prove Runner injects the model-view observer and first Provider image is the verified final `ChangeTimeScale(1.0)` return frame; scan and pose-restore frames never enter session.

The restored image is persisted under neutral model-phase name `frame-0000` and is pixel-identical to the normal-time return event. A mutation that binds the equally positioned pose-restore event must fail even if it rehashes all shallow refs. No scan-step name, time-control detail, exact ID, pose or source metadata may appear in that name or Provider body.

Build the ten-entry regression manifest without outcome-based selection: use each of the six `historical_exact` matrix rows, and for source Episodes 0001/0003/0007/0009 use the lexicographically first committed candidate row before reading Gate action results. Preserve Episode order 0001-0010. If any locked replacement fails Gate A/B, fail delivery rather than selecting a better-performing candidate.

- [x] **Step 4: Implement lifecycle and root ledgers**

Move Adapter construction inside the per-Episode loop. Delay Provider profile/client/runtime/prompt until reset ready. Serialize `AlfworldControlTerminalRecord` once. For tasksets, call `advance_goal()` before `subtask_started` and Provider construction, sum only executed subtask model counts, and compute root totals exactly as spec section 10.2.

- [x] **Step 5: Add exact trial manifest CLI and summaries**

Add required visual THOR `--trial-manifest` input; validate entry count/order/hash/scene/goal before first model request. Product may load goal trial bytes only through the manifest/store interface. Emit setup/control/model/total counts and all independent metrics in Episode/taskset JSON and CLI. Keep `harness_valid_coverage` only as a clearly deprecated alias of evaluation coverage if compatibility tests require it.

- [x] **Step 6: Run GREEN**

```bash
/data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q \
  tests/homemaster/benchmarking/test_alfworld_runner.py \
  tests/homemaster/benchmarking/test_alfworld_outcome.py \
  tests/homemaster/benchmarking/test_alfworld_tracing.py \
  tests/homemaster/test_cli_benchmark_alfworld.py \
  tests/homemaster/test_generic_agent_runtime.py
```

Expected: PASS for ordinary and taskset runners with no pre-ready Provider construction.

## Task 10: Add Structural Guards, Fix The Baseline Guard And Run Internal Verification

**Files:**
- Create: `tests/homemaster/benchmarking/test_alfworld_v18_guards.py`
- Create: `tests/homemaster/benchmarking/test_alfworld_interface_audit.py`
- Modify: `scripts/guard_no_legacy_terms.py`
- Modify: `tests/homemaster/test_cleanup_guard.py`
- Verify every product/test/config file changed in Tasks 2-9.

- [x] **Step 1: Write RED AST/runtime guards**

Assert action-time call graph cannot reach scan/geometry; no ALFWorld candidate/budget/find/navigate/local-Put symbols; only scan source accesses controller cache; only Adapter backend sends Gate-listed THOR actions; only gateway calls backend; product cannot import/open Gate helpers/manifests; no expert fields; visibility success dominates exact lock/store/parent/context/backend; invisible path call counts are all zero.

Enumerate all Protocol real/Fake implementations and all `AlfworldStepResult`, reset and goal-result constructors. Require complete public methods and required typed feedback/tagged results.

- [x] **Step 2: Fix cleanup guard root cause RED-first**

Add a fixture proving ordinary prose/code may contain `deterministic` while exact obsolete identifiers/paths remain rejected. Remove only the generic `deterministic` token from `BLOCKED_TEXT_PATTERNS`; keep scoped legacy symbols and paths. The clean baseline failure must turn GREEN without renaming valid design/code.

- [x] **Step 3: Run focused and full tests**

```bash
PYTHONDONTWRITEBYTECODE=1 /data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q -p no:cacheprovider \
  tests/homemaster/benchmarking \
  tests/homemaster/test_cli_benchmark_alfworld.py \
  tests/homemaster/test_generic_agent_runtime.py \
  tests/homemaster/test_llm_client.py \
  tests/homemaster/test_tool_dispatcher.py
PYTHONDONTWRITEBYTECODE=1 /data0/yuqiao/envs/hm_alfworld/bin/python -m pytest -q -p no:cacheprovider
```

Expected: both exit 0. Any new failure is diagnosed from the full traceback and fixed with one failing regression before changing production code.

- [x] **Step 4: Run static and consistency gates**

Run Ruff check and format-check on every changed Python file; run the full repository in report mode and require no new finding versus the captured baseline. Run compileall, interface audit, V1.8 guards, JSON parsing, Markdown fence/placeholder/credential scans, secret debug-asset test and `git diff --check`. Search product source for every deleted ALFWorld symbol and require zero scoped hits.

## Task 11: Run Gate B Through The Real Product Boundary

**Files:**
- Create ignored remote: `var/alfworld-evidence/20260713-v18-gate-b/product_chain_gate.py`
- Create ignored remote: `var/alfworld-evidence/20260713-v18-gate-b/verify_product_results.py`
- Create ignored remote: `var/alfworld-evidence/20260713-v18-gate-b/README.md`
- Reuse verified: `matrix-v3.json`, `exact-cases-v3.json`, runtime contract and product-safe TrialSelection manifest.

- [x] **Step 1: Synchronize an explicit changed-file manifest to `hkust4`**

Generate a sorted local changed-file list, reject paths outside approved source/test/config/docs/plan scope, and copy only those files to the runtime worktree without pushing. Compare local/remote SHA-256 per file, verify import origins resolve inside the runtime worktree, and preserve remote credentials plus ignored Gate A runs.

- [x] **Step 2: Write Gate B worker and independent verifier RED fixtures**

Each fresh-Xvfb worker runs real `AlfworldEnvAdapter.reset -> product tools -> ToolDispatcher`, using a deterministic scripted provider transport so the actual canonical outbound request body and image can be captured without exposing a real key. GateCase remains in the external driver; Runner receives only TrialSelection. The verifier imports no product serializer/resolver/classifier and checks expected/result bijection from raw events/frames.

Mutation fixtures independently tamper source-tree hash, slow/restore time values and order, pose-restore versus normal-time final event, one recovery row, `N+4` setup count, reset artifact, committed model image, visibility, snapshot row, parent, model payload, Provider body and process return code; each must fail for its target reason.

- [x] **Step 3: Implement every feasible product case without substituting trials**

Cover every exact matrix target/action, including:

```text
same exact target/snapshot: invisible -> target_not_visible + zero downstream
Gate fixture frame committed by Provider -> visible -> one sole-pose navigation
generic multiple visible peers and explicit ordinal hidden/missing without fallback
closed child: hidden failure -> target-independent public Open sequence -> still hidden failure or visible unique-parent success
setup terminal -> zero Provider factory/send and Episode 2 fresh Adapter
setup success -> exact N+4 phases and final normal-time frame committed to Provider
failure after slow enter -> pose recovery then normal-time recovery; unproven time closes/quarantines
goal-control terminal -> root-only responsibility and later not-run rows
Take/Open/Close/Put/Use/Slice/Heat/Cool/Clean return plus terminal state
old ALFWorld find/navigate names -> unknown_tool + zero backend
every snapshot/observation read status and context transition
```

Inspect the complete Provider-bound body. Invisible cases with different hidden IDs/poses/parents/snapshot rows must produce identical public failure bytes except enumerated attempt metadata. Visible cases may expose only the real current image, never fixture ID/pose or snapshot data.

- [x] **Step 4: Run and independently verify Gate B (non-blocking evidence)**

Use new empty `var/alfworld-evidence/20260713-v18-gate-b/run-001`. Attempt the complete matrix and independently verify every produced row. Record nonzero exits, timeouts, cleanup failures, source/runtime identity mismatches and per-case failures exactly; do not use best/any aggregation and do not describe an incomplete or failed row as PASS. A non-perfect Gate B result no longer blocks documentation or the local implementation commit.

**Final disposition:** `run-001` exposed and led to the keyword-only runner fix. `run-002` reached real THOR and stopped at reset recovery. Direct review added exact runtime-scene validation, and final production-affecting `run-003` passed that gate on FloorPlan219 before reproducing the same terminal and counts. The independent verifier exits 2 with `overall_status=incomplete`; the absent exact-case manifest prevents the complete matrix.

## Task 12: Update Docs, Run Ten Real Episodes, Review Once And Commit

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/alfworld-harness.md`
- Modify: `docs/alfworld-user-guide.md`
- Modify: `CHANGELOG.md`
- Create: `docs/reports/2026-07-16-alfworld-v18-current-visible-report.md`
- Modify when justified: `docs/pitfalls.md`, `CLAUDE.md`
- Update: `task_plan.md`, `findings.md`, `progress.md`
- Runtime output: `var/alfworld-trace/test/alfworld-valid_unseen-v18-realapi-20260716-001/`

- [x] **Step 1: Update documentation only from verified behavior**

Document controlled-time reset scan/snapshot, `N+4` accounting, failure recovery and final normal-time frame authority, current-model-view authorization, generic/ordinal behavior, one pose, typed reset/control/model counters, exact errors, public tools, safe manifest, retry boundary and independent metrics. Include Gate A/B per-instance evidence and keep any failed row `UNVERIFIED`. Add the run-007 temperature false-boundary postmortem to the top of `docs/pitfalls.md` and the positive controlled-simulation-time restoration/external-effect rule to `CLAUDE.md` only after the repair passes Gate A/B, without overwriting existing user edits.

- [x] **Step 2: Run final internal and non-blocking Gate B verification**

Rerun Task 10 commands and every feasible Gate B case after the last product/doc-affecting fix. Internal regressions introduced by this implementation must be fixed; Gate B failures and unavailable rows are retained and reported rather than hidden or used to stop the implementation.

- [x] **Step 3: Run the fixed ten-Episode regression when prerequisites are available**

Use the ignored real provider config, print only redacted endpoint/model identity, and run under a detached supervisor that records PID/start/exit metadata:

```bash
env -C /data1/haodong2/weilin/red_bird/Homemaster \
  ALFWORLD_DATA=/data1/haodong2/weilin/red_bird/alfworld/data PYTHONPATH=src \
  nohup xvfb-run -a -s '-screen 0 1280x1024x24' \
  /data0/yuqiao/envs/hm_alfworld/bin/python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /data1/haodong2/weilin/red_bird/alfworld \
  --alfworld-config /data1/haodong2/weilin/red_bird/alfworld/configs/base_config.yaml \
  --trace-root var/alfworld-trace --env-type AlfredThorEnv --split valid_unseen \
  --episodes 10 --trial-manifest config/alfworld_v18_regression_trials.json \
  --observation-mode visual_eval --provider-name mimo \
  --run-id alfworld-valid_unseen-v18-realapi-20260716-001
```

The supervisor redirects stdout/stderr, writes a machine-readable completion record and never prints token/header/config contents.

- [x] **Step 4: Verify the complete ten-row disposition without substituting Episodes**

Do not stop at PID existence or first progress. Wait for process exit and a complete ten-entry summary. For every Episode independently verify expected/observed trial identity, reset time/scan/pose+time restore, Provider attempt/image commit, `N+4` setup plus tool/backend/control counts, unique responsibility classification, raw external return/terminal evidence and final frame hashes. Report raw success, Agent-on-valid, evaluation coverage, Harness coverage, Provider availability and Runtime availability separately; incomplete/nonzero runs are not “10 passed.”

Attempt all ten entries when credentials/runtime are available and wait for the launched process to terminate. Report the controller/process exit, expected/result bijection, setup and terminal-state evidence, coverage/availability metrics, unclassified failures and formal-score availability exactly. Missing credentials, nonzero exit, incomplete rows or failed thresholds are exposed as failures and do not block the local implementation commit; raw Agent success is always reported separately.

**Final disposition:** The earlier environment-only credential check was incomplete: the ignored HomeMaster config already held a working Mimo profile. `config/alfworld_v18_regression_trials.json` now pins six `historical_exact` trials and the pre-Gate `candidate-1` for Episodes 1/3/7/9 as `deterministic_replacement`; no Gate outcome selected those rows. The ten-Episode process exited 0 with a complete ten-row summary, but every row is score-ineligible `execution_state_uncertain` at `scan_pose_mismatch -> scan_time_scale_restore_rejected`. Counts are 50 setup requests, zero tool/model/Provider requests, 0% evaluation/Harness coverage and `formal_score_available=false`.

- [x] **Step 5: Complete the one-time direct final code review**

Only after code, internal tests, Gate B, ten-Episode terminal evidence and docs are complete, start one read-only reviewer with the frozen spec/plan, complete diff, test logs and raw Gate evidence. The reviewer must label external symbols without real evidence `UNVERIFIED`, must not edit files and must not delegate. Record each finding once; do not request a second review.

**Final disposition:** The active no-subagent constraint replaced the planned reviewer with one main-agent complete-diff review. It found the missing runtime-scene comparison, which was fixed with two regressions and `run-003`; no further blocking defect was found.

- [x] **Step 6: Disposition findings and run targeted verification**

For each accepted behavior finding, first add a failing regression, implement one focused fix and rerun the exact affected internal tests and external Gate cases. Reject a finding only with concrete code/evidence reasoning. Update docs/evidence claims if behavior changes. This is targeted verification, not another review round.

If a final-review fix changes any product/provider/runtime/config path used by the real benchmark, resynchronize source hashes and rerun the full fixed ten-Episode regression to the Step 4 thresholds before commit. A docs-only or test-only fix needs targeted verification but cannot change the recorded product source-tree hash. Final committed product bytes must exactly match the source-tree identity in the accepted real-API evidence.

**Final disposition:** The plan's exact focused command passed `193 passed`; full pytest passed `395 passed, 1 skipped`; all lint/compile/guard/structured checks passed. The unchanged format baseline is 26 files. Gate B remains incomplete exactly as recorded above.

- [x] **Step 7: Make one local implementation commit and resynchronize runtime bytes**

Write one Unreleased CHANGELOG entry covering problem, change, reason, impact and verification. Use that entry byte-for-byte as the commit body, stage only owned verified files and commit locally. Do not push. Synchronize the final committed changed-file manifest to `hkust4`, compare every local/remote hash, verify remote import/source-tree identity and report local HEAD plus remote runtime HEAD/status separately.

**Final disposition:** The 63 implementation-owned changed paths are hashed and staged for one local `hkust4` commit. The runtime worktree is the committed worktree; no push is authorized or performed.

## One-Time Plan Review Disposition

The sole read-only plan review examined candidate SHA-256 `9af25b8314b696d43220ec8833d223b87410ea5dd079ed43da991bc4e0e66938` and returned `FIX`. The reviewer changed no file, delegated no work and did not endorse any external symbol. Main-agent disposition is final; no second plan review is permitted.

| Finding | Disposition | Locked change |
|---|---|---|
| Task 4 simultaneously retained legacy action consumers and claimed a global sole-gateway invariant | Accepted | Task 4 enforces setup-only routing; Task 6 enables navigation-scoped routing; Task 7 atomically removes remaining direct sends and enables the global guard. |
| Slice remained a runtime-selected dual behavior before Gate A evidence | Accepted | Added mandatory post-Gate-A stop, source-level `EXPECTED_SLICE_IDENTITY_MODE`, one implementation/test branch and rejection of runtime behavior switching; Slice remains `UNVERIFIED` until then. |
| Ten-Episode run could advance despite infrastructure/Harness failures | Accepted | Added exit/bijection/per-instance gates, four 100% coverage/availability thresholds, zero unclassified failures, formal-score requirement and full rerun after production-affecting review fixes. |
| Image-content hash could not disambiguate identical frames or multiple images | Accepted | Added ordered `OutboundImageBinding`, opaque frame-ledger ID, bytes/pixel verification, last-bound-image rule and duplicate/multi-image tests; binding metadata is never Provider-visible. |
| Required feedback migration omitted six tools-test constructors | Accepted | Added `test_alfworld_tools.py` to Task 7 files/GREEN command and made `rg 'AlfworldStepResult\(' src tests` the exhaustive constructor audit before the field becomes required. |

## Plan Self-Audit

- [x] Spec sections 5-15 map to a concrete implementation task and external gate.
- [x] Controlled-time Gate A v3 and real `discovery-run-008/case-run-008` precede product edits; runs 001-007 remain immutable evidence.
- [x] V2 matrix/helpers/runs remain immutable evidence; v3 removes public-witness authority from real handlers and self-tests.
- [x] Same-target invisible/visible cases bind one snapshot row; invisible authorization has zero store/parent/context/backend calls.
- [x] Successful Provider request image, current event and persisted frame are independently bound; same-batch calls cannot consume a new frame.
- [x] Reset policy/plan two-stage freeze, step 0, provenance, addressability, full scan, pose restore, normal-time restore, `N+4` counting and atomic publication from the final event are explicit.
- [x] Time-control return codes and build-scoped external effects are orthogonal gates; all three frozen temperature sentinels are asserted per instance without any/best aggregation.
- [x] Every post-enter failure attempts pose recovery before normal-time recovery, still attempts normal time after pose-recovery failure, preserves trigger/final codes and never publishes a partial snapshot.
- [x] Snapshot binds scene reset only; goal control uses before/after physical digests and root-only counting.
- [x] Generic/ordinal grounding, direct/unique-parent sole pose and no fallback are fully tested.
- [x] Every public action uses exact context, one gateway, return-code and terminal-state gates, and sole typed feedback.
- [x] GateCase and TrialSelection types/process/files are incompatible and Provider no-leak covers the whole body.
- [x] Provider retry has one frozen request, at most one retry and auditable commit state/cause.
- [x] Setup/control/model/tool/env/invalid/total counts and responsibility metrics are independently asserted.
- [x] All Protocol implementations, constructor sites, deleted symbols and direct THOR send sites are audited.
- [x] Gate B is per instance and independent; ten real Episodes are monitored to terminal completion.
- [x] Main agent performs implementation; exactly one final read-only review occurs after all evidence/docs.
- [x] The one-time implementation-plan review was already consumed; this user-approved controlled-time delta receives main-agent self-audit only and no second plan review.
- [x] No push is planned; commit body and CHANGELOG entry are identical.
- [x] Placeholder, type, path, command, fence, credential and whitespace scans are clean.
