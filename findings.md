# Current Coworker Demo Findings

## 2026-07-20 Recording Stop Root Cause

- Failed real normal attempt: `var/coworker-demo/coworker-20260720-022516-8c773877/`.
- Model/provider path was real Mimo `mimo-v2.5`; business state reached 24/24 trajectory nodes, 14/14 result checkpoints, overall 100, and terminal complete.
- `video/video_manifest.json` proves FFmpeg return code 0, H.264 1920x1080/yuv420p, 362.066667 seconds, 5431 frames, verified named frames, and SHA-256 `b545af36773eeae1a9629c2e582a5859453e91151bd7ddd94dbc6ec6da377f87`.
- `environment/process/service.stdout.log` records the first `POST .../recording/stop` as 200 OK and the cleanup retry as 500 Internal Server Error.
- `environment/process/service.stderr.log` locates the retry failure at `DemoRecorder.stop -> self.process.stdin.flush -> BrokenPipeError`.
- Root cause: the client inherited a 20-second generic request timeout for a long video verification operation; the caller marked recording stopped only after response receipt; the non-idempotent cleanup retry touched an already-closed FFmpeg process.
- Fix contract: 180-second dedicated stop timeout plus lock-protected cached stop result at the service session boundary.
- Orthogonal black-box gate `recording-stop-gate-20260720-024549` started the real service, TigerVNC, and FFmpeg, then issued two HTTP stop requests. Both returned 200 with FFmpeg code 0 and SHA-256 `d9f4c807743cff81596ea0f9a9cae4775b2af1aa1bf950293c27bc53a9186835`, matching the 340606-byte MP4 on disk.

## 2026-07-20 Final Real Acceptance

- Accepted normal: `coworker-20260720-024949-b7004546`, Mimo `mimo-v2.5`, 42 provider calls, 43 tool calls, 2 rejected calls, 24/24 nodes, 14/14 checkpoints, complete, video SHA-256 `9e4ae3e59e63eecbc586367a6224b7955d1a2571ce9d4f45e1c1c200ea3ac37c`, independent verifier PASS.
- Accepted anomaly: `coworker-20260720-025635-a46d87ca`, Mimo `mimo-v2.5`, 44 provider calls, 44 tool calls, 6 rejected calls, 22/22 nodes, 11/11 checkpoints, rolled_back, video SHA-256 `5308921986a4997413de0ee68d5f99e8c37093920048c96274cd0d2650fe3715`, independent verifier PASS.
- Normal external state retains the exact four-field config record and both add/business jobs returned 0. Anomaly state binds the causal alarm to the exact add job, add/remove returned 0, grep changed from 0 to 1 with empty rollback stdout, and final config is `{}`.
- Every named frame was inspected. The anomaly first-action frame catches Chrome during navigation, but the right observer identifies `browser_navigate`; an independent frame from the same MP4 at 10 seconds shows the ticket loaded and success result.

# Historical ALFWorld Harness Findings

## 2026-07-12 Initial State

- Repository: `/home/haodong2/weilin/red_bird/Homemaster` on `hkust4`.
- Branch: `visualagentloop`, tracking `origin/visualagentloop` at `98c3908`.
- Existing dirty state: modified `.gitignore`; untracked `docs/record/` and `plan/V1.7/`.
- No pre-existing root planning files were present.
- Required source documents and failed episode are named in the user-provided task; their contents are not yet assessed.

## Authoritative Design State

- The evaluation spec says the design direction and fifteen listed decisions were already confirmed by the user; implementation remained forbidden only because independent review and real-environment gates were deferred.
- The selected architecture is one public `robot_manipulate` backed by a shared executor and a `put` action profile. The upstream decision eliminates per-action public tools and per-action retry loops while preserving future `take/open/toggle` profiles.
- A `put` call locks the held objectId, target receptacle objectId, `PoseContext` candidate list, and order once. It starts from the current pose, retries only after an explicit THOR failure with fully unchanged relevant state, and stops at the first return-code plus terminal-state success.
- Success requires all three: THOR success, the exact object leaving inventory, and the exact object's parent containing the exact receptacle. Contradiction or partial change is `execution_state_uncertain` and stops immediately.
- Explicit numbered targets are strict. A missing `pencil 2` is `target_not_found`; it must never be normalized and reparsed into `pencil 1`.
- Harness grounding/navigation/operation/uncertain/unclassified failures terminate the Episode, do not increment model invalid-action count, and are excluded from Agent scoring while remaining visible in Harness coverage.
- `robot_inspect_view` is removed rather than repaired; model-visible output must include stable error, inventory, object state, state change, raw THOR detail, and latest image without objectIds, poses, scene inventory, or expert answers.

## Root-Cause Evidence Already Captured by Specs

- Navigation currently allows a 2D detection to override `metadata.visible=false`, producing a false `robot_go_to` success.
- Tool grounding can pass an unresolved explicit label to the adapter, whose second parsing strips the instance suffix and selects another instance.
- Internal manipulation results contain richer state, but the visual projection collapses most failures to `action_failed`.
- The failed trace established correct first `put` semantics and a real THOR failure with unchanged inventory and goal; it did not establish that the target Shelf is permanently unavailable.

## Failed Episode Direct Inspection

- `summary.json` reports `benchmark_env_step_limit`, 50 environment steps, 37 invalid actions, goal rate 0.0, and no win.
- The model first navigated to `desk 1`, took `pencil 1`, navigated to `shelf 1`, and issued the correct `put(pencil 1, shelf 1)` call.
- `robot_inspect_view` repeatedly returned the same prior frame path and no new backend step.
- After the put failure, the model visually misread the held pencil as placed, marked its own plan complete, and repeatedly called `robot_verify`; this is downstream model recovery behavior after the Harness failure/feedback loss, not evidence that the initial semantic action was wrong.
- The latest commit `98c3908` is a large objectId manipulation change touching adapter, prompt, registry, tools, and tests; its 2,333-line scope is relevant to the double-resolution regression investigation.
- Remote JSONL files are valid JSON-per-line. `jq` is unavailable, so subsequent structured extraction will use Python standard-library JSON parsing.
- Exact frame hashes prove `frame-0003.png`, `frame-0004.png`, and `frame-0005.png` are byte-identical across Shelf 1 navigation and both failed puts. The tool did not create a changed visual terminal state.
- The actual `model_trace.jsonl` manipulation record contains only a text block such as `{"error":"action_failed","success":false}` plus the image path; its rich internal `data` is absent. This is the authoritative model-visible projection, not merely an internal helper assumption.

## Independent Root-Cause Review

- The single most likely root cause is a cross-component contract mismatch: navigation accepted a render/detection pose, while direct THOR manipulation assumed that pose was sufficient and attempted exactly one put. The exact Shelf rejected the action.
- Orthogonal external-state evidence for the failed puts is consistent per instance: THOR error detail, exact Pencil still in inventory, goal still `0/1`, and unchanged RGB frame. Internal labels such as `Reached shelf 1`, `invalid_action`, or `action_failed` are not external terminal-state proof.
- A working control in the same benchmark run (`BaseballBat -> Bed`, episode 0009) used the same direct objectId/PutObject plumbing and ended with empty inventory, goal `1/1`, and `won=true`. This rules out a globally unusable PutObject API and isolates receptacle/pose feasibility.
- The initial Harness failure is distinct from later model recovery failure and from the late `pencil 2 -> pencil 1` grounding drift. The latter is a real secondary Harness defect but did not cause the first put rejection.
- Evidence still missing from the historical run: raw complete THOR events, per-candidate poses and visibility facts, exact parent/child receptacle fields, and an old-commit A/B run. Those claims must be established by new real-environment evidence rather than inferred from trace prose.
- The task directory contains three trial subdirectories; the exact historical trial must be identified before any environment reset or experiment.
- Object-pose comparison identifies the historical trial as `trial_T20190908_122154_042763`: its single Pencil pose `(-1.57117891, 0.882609248, 0.832937)` matches the held objectId recorded in episode 0006.

## First Independent Design Review

- Verdict was `FIX`; no product implementation was approved.
- All eleven findings were dispositioned in evaluation spec section 20.11. Ten were accepted directly. The recommendation to prefilter exact targets or elevate selection to a semantic Shelf type was not adopted because it conflicts with the user-confirmed rule that the model selects the exact instance and Harness never swaps Shelf.
- The revised design now includes exhaustive move/put/read transitions, separate navigation and operation contexts, a deterministic scene index, an authoritative terminal outcome gate, explicit legacy-action boundaries, safe raw-detail projection, all-six-Shelf experiments, fixed navigation/operation budgets, richer evidence events, exhaustive scoring outcomes, taskset propagation, and the expanded UNVERIFIED symbol list.
- Navigation spec section 14 now requires its own candidate/backend-action/time budget and actual-pose evidence.

## Current Code Boundary Evidence

- `tools._exec_go_to()` grounds a label, then calls `adapter.go_to_target()` with the grounded string. The adapter calls `_resolve_navigation_target()` again, so object selection is not authoritative-once.
- `env_adapter._object_query_key()` strips every trailing digit after normalization. An exact miss on `pencil 2` can therefore fall through to type-level Pencil matching and deterministic selection of another instance.
- `_target_visibility_score()` initializes from exact-object `metadata.visible`, then sets `visible=True` whenever any target id has an `instance_detections2D` entry, even if the metadata value was false. This directly implements the observed navigation false positive.
- `_teleport_to_targets()` returns as soon as that combined boolean is true. It does not separately require external teleport success, exact metadata visibility, and a positive-area exact-object detection.
- `manipulate_with_thor()` increments `AlfworldEnvState.invalid_action_count` for any backend manipulation failure. This violates the required model/Harness accounting once a semantically correct put exhausts Harness execution.
- `_visual_tool_result()` emits only `{success, error}` for manipulation failure, and `_visual_error()` maps nearly every non-validation/non-verify failure to `action_failed`, discarding the model-visible inventory/object-state/detail contract.
- Current navigation candidates are generated from up to 12 nearest reachable positions with deterministic rotation/horizon lists, but candidate generation depends on still-UNVERIFIED `GetReachablePositions` behavior.
- Runtime event counts show 69 tool calls in the failed Episode, separate from the 50 ALFWorld environment steps, confirming that the current trace already distinguishes model/runtime tool traffic from environment-step limits at a raw-event level.
- `AlfworldEpisodeResult` and `AlfworldSummary` currently expose only one failure reason, environment steps, invalid actions, and success rate. They cannot yet represent score eligibility, Harness failure classes, backend action counts, or Harness coverage.
- `AlfworldBenchmarkRunner._stop_condition()` only stops for won, invalid-action limit, environment-step limit, or environment done. There is no immediate Harness-failure stop signal.
- The THOR `put` path resolves the target from a string again, takes whichever object is currently first in `inventoryObjects`, calls `PutObject(forceAction=True, placeStationary=True)`, and trusts only `lastActionSuccess`; it does not verify exact held-object identity or terminal parentage.
- `model_trace.jsonl` is not schema-identical to `runtime_events.jsonl`: model tool records have a top-level `name` but no `type`, while runtime events use `type`. Future assertions must parse each source's actual schema separately.
- Repository-wide search found no persisted raw per-Shelf experiment artifact outside the prose specs/issue record. Existing tests contain mocked external fields and therefore are not authoritative evidence for THOR runtime contracts.

## Real-Environment Gates At Initial Discovery

- `GetReachablePositions` request/return/failure contract.
- `parentReceptacles` and `receptacleObjectIds` exact formats and event timing.
- `instance_detections2D` bbox format/coordinates/positive-area rule and correspondence to the same event RGB.
- `event.frame` availability, pixel format, and saved-image identity.
- Any interactive-pose query API.
- Exact `open` and `toggle/use` terminal-state fields and values.
- Per-Shelf fixed-order candidate experiments must determine candidate count, backend action count, and wall-clock budget; every known positive instance must pass independently.

## Runtime Environment Discovery

- The default remote interpreter is `/usr/bin/python3` 3.10.12, while HomeMaster declares Python `>=3.11`; it is not an acceptable project test/runtime interpreter.
- No `conda`, `uv`, `python3.11`, or `python3.12` command is currently on the non-interactive SSH PATH, and no virtual environment was found under `/home/haodong2/weilin/red_bird`.
- The ALFWorld source tree is `/home/haodong2/weilin/red_bird/alfworld` with `configs/base_config.yaml` and project metadata present.
- Three user-owned long-running Xvfb servers exist on displays `:99`, `:100`, and `:101`. Their health/authentication and the Python environment used by prior ALFWorld runs remain unverified.
- No THOR/Unity episode process was active during inspection.
- The dedicated environment is `/data0/yuqiao/envs/hm_alfworld/bin/python`, Python 3.11.15.
- Installed runtime versions: ALFWorld 0.5.0, ai2thor 2.1.0, NumPy 2.4.6, OpenCV 5.0.0.93, Pillow 12.3.0, HomeMaster 0.1.0, PyYAML 6.0.3.
- `alfworld` and `homemaster` import from the current `/data1/haodong2/weilin/red_bird/...` source trees (the target path resolves into the same storage), while ai2thor imports from the dedicated environment.
- `DISPLAY=:99` with `/tmp/xvfb-run.fRevQI/Xauthority` passes `xdpyinfo`, proving the X server is currently reachable.
- `python -m pip check` is not clean: it reports `textworld 1.7.0 is not supported on this platform`. No dependency mutation has been made.

## Direct THOR Runtime Contract Evidence

- Evidence roots: `var/alfworld-evidence/20260712-preimplementation/runtime-contract`, `runtime-contract-v2`, and `runtime-contract-v3`. Each run used a fresh controlled `xvfb-run`; the new Unity/Xvfb processes exited cleanly.
- The direct probe imports ALFWorld/ai2thor but no HomeMaster implementation or resolver. Process exit code was 0 for all three runs.
- Reset resolves exactly six Shelves in deterministic objectId order matching historical labels. Reset return code is successful; inventory is empty and goal is `0/1`.
- `GetReachablePositions` succeeds and returns the same 121 positions in both `metadata.actionReturn` and `metadata.reachablePositions`. Both representations normalize identically. The pre-action reset event has an empty `reachablePositions` list.
- Passing `gridSize="invalid"` is silently ignored: the action still succeeds with the same 121 positions. The current runtime therefore does not provide parameter-level failure semantics for this query.
- A `TeleportFull` to the first returned position succeeds. The actual event agent pose matches requested x/y/z/rotation/horizon within the recorded float tolerance, and the state vector remains unchanged.
- Candidate external pose fields are present as `metadata.agent.position`, `metadata.agent.rotation.y`, and `metadata.agent.cameraHorizon`; their exact request/actual contract is now observed for one pose but still requires per-candidate coverage.
- `event.frame` is `uint8` with shape `[300, 300, 3]`. PNG save/reload preserves exact pixels and hash. Exact Shelf detections are `[x1, y1, x2, y2]` lists with positive areas in the same event.
- UNVERIFIED probe action `GetInteractablePoses` raises `ValueError('Invalid action: GetInteractablePoses')`. The current design must not depend on it.
- Replaying the exact expert prefix picks up only `Pencil|-01.57|+00.88|+00.83`. The expert `PutObject` request with exact object/receptacle IDs, `forceAction=true`, and `placeStationary=true` returns success; inventory becomes empty; goal becomes `1/1`; Pencil parents contain exact Shelf 4; Shelf 4 children contain the Pencil.
- On successful put, `parentReceptacles` retains the original Desk and adds Shelf 4 rather than becoming a single-parent list. The terminal gate must test membership of the exact Shelf, not equality to a singleton.
- Two independent runs produce identical reset, pickup, and put frame hashes and identical exact terminal states.
- A visible Drawer probe returns `lastActionSuccess=true` and changes exact `isOpen` from false to true. This verifies the open state field/value/timing for that target in this runtime.
- Hidden toggleable targets return `lastActionSuccess=false`; raw `errorMessage` includes exact objectId, coordinates, and a Unity stack trace. This is direct evidence that raw detail can leak prohibited data. No successful `isToggled` transition has yet been observed, so toggle remains UNVERIFIED.
- Failure events may have `errorCode=null`; a failed `OpenObject` on Blinds even had an empty `errorMessage`. Stable classification must use return status plus terminal state, never require a nonempty error code/detail.

## Repository Documentation Gap

- `docs/pitfalls.md` does not exist. If this repair confirms a non-obvious pitfall, delivery must create it with the newest entry first.

## Evidence Rules

- Record external or runtime observations here, not in `task_plan.md`.
- Distinguish code-path/trace evidence from authoritative THOR return codes and terminal world state.
- Report each target instance independently; never pass an aggregate because one instance succeeded.

## Characterization Implementation Review

- The pre-execution review verdict was `FIX`; the six-Shelf characterization must not run until every blocker below is closed.
- Timeout isolation: accepted. Each Shelf must execute in a killable process boundary; a timed-out THOR call may not remain alive while another Shelf starts.
- External reads: accepted. Return status, metadata, pose, inventory, exact Pencil/Shelf, parent/child state, detections, frame identity, reachable results, and goal reads must preserve `ok/error/missing`; missing evidence cannot become an empty collection.
- Failure provenance: accepted. External uncertainty, script defects, and artifact failures remain distinct in summaries and exit status.
- Per-instance gates: accepted. Navigation must pass separately for all six Shelves; put must pass separately for known-positive Shelves 3, 4, and 6. Other put outcomes remain visible and cannot be hidden by `any`/best aggregation.
- Budget scopes: accepted. Navigation and operation each require candidate, backend-action, and wall-time limits with explicit stop reasons and terminal-priority ordering.
- Unified evidence calls: accepted. Reset/load/goal boundaries and every prefix, reachable, teleport, and put request require timing and raw evidence or an explicit missing/exception record.
- Local context identity: accepted. The locked local pool must include the current pose first and bind its hash to the successful source event/frame/raw artifact, anchor, and actual pose.
- Numeric canonicalization: accepted. Reachable coordinates must be finite, rounded into the stored canonical value, and normalize negative zero; canonical JSON rejects NaN and Infinity.
- Two-stage budget selection: accepted. Characterization uses predeclared safety ceilings over the complete locked pool, derives production limits by a fixed recorded rule, then validates known-positive instances again with those derived limits. One CLI cap cannot prove itself.
- Helper batch completed: `independent_alfworld_probe.py` now rejects nonempty output directories, refuses artifact overwrite, records both canonical-content and persisted-file SHA256 values, rejects non-standard JSON floats, writes a raw event for every returned `thor.step`, and exposes tri-state `external_read`. Exceptions and missing events retain `raw_event=null` and produce a nonzero probe result unless explicitly expected.
- The separate characterization state-machine batch owns process isolation, per-Shelf summaries/gates, context pools, and two-stage budgets; the helper batch does not claim those items complete.

## Probe Negative Review Follow-Up

- A synthetic returned event with `metadata=None` exposed a projection-layer crash after `external_read` had correctly classified the event as missing. The projection helpers must never re-read `event.metadata` unsafely; otherwise an external missing-state result is misreported as a script failure.
- An exact-target detection entry with malformed bbox `['bad']` previously left `external_read.status=ok`. Existing detections now require exactly four finite numeric values and a computable area. A missing exact-object detection remains a valid observation result with `detected_2d=false`, not a missing read.
- Frame evidence is authoritative only for a nonempty `uint8` RGB ndarray. Wrong dtype, rank, channel count, or empty spatial shape is an external read error. Each exact Pencil/Shelf observation now records metadata visibility, bbox presence/area, frame validity, and the combined strict observation result.
- `metadata.lastAction` is part of the return contract. Missing, malformed, or request-mismatched action names are errors, preventing a stale successful event from satisfying `require_success`.
- Probe process success now depends on external terminal-state gates, not trace presence: expert put requires exact inventory/parent/child plus goal `1/1`; open requires exact `isOpen: false -> true`; toggle remains `UNVERIFIED` and makes the combined probe nonzero until an exact successful toggle transition is captured.
- The Teleport contract now selects a reachable point with nonzero distance from the source pose and requires returned success, tri-state read success, requested/actual pose agreement at the observed tolerance, and actual nonzero movement.
- Pre-product-change ALFWorld test baseline: 49 relevant tests pass and one pre-existing Runner test fails because it expects `robot_navigate` while the current registry does not expose that tool. The repository-wide ALFWorld Ruff baseline has 37 pre-existing errors. Neither baseline is attributable to this ignored evidence-script change.
- The first main-agent negative-test attempt incorrectly used a remote repository path as a local tool `workdir` and failed before running. The corrected test used SSH with Python source on stdin and ran successfully; future remote negative tests must use that established invocation.

## Final Characterization And Production Budgets

- `shelf-characterization-v3` completed with controller status `pass`, no hard-gate failures, and zero hard failures.
- All six Shelf exploration instances independently passed navigation, Put return status, exact inventory/`isPickedUp`/parent/child terminal state, and goal `1/1`; no best/any aggregation was used.
- Exploration navigation candidates/backend actions were: Shelf 1 `51/52`, Shelf 2 `57/58`, Shelf 3 `57/58`, Shelf 4 `3/4`, Shelf 5 `1/2`, Shelf 6 `1/2`.
- Exploration local put candidates/backend actions were: Shelf 1 `2/3`, Shelf 2 `3/5`, Shelf 3 `1/1`, Shelf 4 `1/1`, Shelf 5 `2/3`, Shelf 6 `1/1`.
- The predeclared derivation rule selected navigation `65 candidates / 66 backend actions / 34804 ms` and local put `9 / 17 / 5669 ms`.
- Shelf 3/4/6 all passed a second production run under those exact budgets.
- Evidence: `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/summary.json` and `production_budget.json`.

## Product Harness Black-Box Gate

- Three independent fresh-Xvfb processes used the product `AlfworldEnvAdapter`, not the independent characterization state machine, to run `go_to(pencil 1) -> take -> go_to(exact Shelf) -> put` for Shelf 3/4/6. Every process exited 0.
- Product navigation backend actions were Shelf 3 `58`, Shelf 4 `4`, Shelf 6 `2`; each stayed within the fixed production budget. Every put succeeded on the current pose with one backend action.
- An independent terminal parser asserted THOR return success, Pencil absent from inventory, `isPickedUp=false`, exact Shelf membership in Pencil parents, Pencil membership in exact Shelf children, goal `1/1`, and saved navigation/put PNG pixels equal to their final THOR events for each instance.
- The same three gates were repeated after the final structured-trace and strict `isPickedUp` changes; all three fresh-Xvfb processes again exited 0 with identical pass criteria.
- Final evidence: `var/alfworld-evidence/20260712-preimplementation/product-harness-v2/`.

## Delivered Product Boundary

- `robot_go_to` now uses authoritative exact-object grounding, locked candidates, actual-pose verification, strict exact visibility/bbox gates, fixed budgets, and final-event image delivery.
- `robot_manipulate(action=put)` now uses the locked `PoseContext`, a common execution core, exact preconditions, fixed local budgets, exhaustive return/state classification, and exact external terminal gates.
- Harness terminal outcomes stop ordinary Episodes and long-horizon tasksets, exclude infrastructure failures from Agent scoring, mark remaining subtasks not-run, and report valid coverage/formal-score availability.
- `robot_inspect_view` is absent from registry and prompt. Put feedback exposes stable minimum state and deterministically redacts forbidden THOR detail.
- Toggle/use remains UNVERIFIED for successful terminal transitions and is not part of the put MVP claim.

## Shared-Workspace Sync Incident

- Uploading a stale local `types.py` briefly removed a newer taskset constant and fields from the remote dirty worktree. Collection failed immediately on the missing import, so no false-green result escaped.
- The taskset patch owner reconstructed the lost delta on top of the latest file, preserved the new backend counter, and passed outcome/runner/CLI tests. The remote file was then copied back to staging before further edits.
- Shared remote synchronization must compare ownership/current file state before upload; a local dirty clone is not automatically the authoritative copy.

## Final Verification

- Focused ALFWorld benchmark suite: `121 passed`.
- Full repository suite after generated-cache cleanup: `352 passed, 1 skipped`; the skip is the opt-in live ALFWorld smoke test. The only warning is the existing `jieba` import of deprecated `pkg_resources`.
- Touched-file Ruff check and Ruff format check pass; compileall, cleanup guard and `git diff --check` pass.
- JSONL smoke independently read the persisted events and confirmed ordered internal execution records retain objectId/raw/pose evidence while the model-visible projection recursively excludes those fields.

## 2026-07-13 V1.8 Design Continuation

- Canonical repository state is the clean `visualagentloop` branch on `hkust4` at `0fdfeaa00b921d8ea347655ecbd4c32b9ff30d6d`; the older local V1.7 staging tree is not authoritative.
- The real 10-Episode run produced raw success `5/10`, Agent success `5/6` on score-eligible Episodes, and Harness coverage `6/10`; formal score was unavailable.
- Exact-trial probes established three separate Harness defects: navigation successes beyond the 65-candidate budget, visible-but-inoperable Drawer poses, and model feedback reading the wrong integration layer. A provider SSE ordering failure was also misclassified as Agent failure.
- The user selected one upstream architecture: use ALFWorld's generic Oracle receptacle map for low-level poses, remove navigation/Put pose search, keep hidden movable-object exploration under model control, require the exact requested target to be visible after navigation, use single-shot Open/Put, project only typed execution feedback, and exclude Provider/runtime/Harness failures from Agent scoring.
- Standard Oracle source/data references resembling `controller.receptacles[exact_object_id]["locs"]` were observed in the installed environment, but direct HomeMaster integration and per-instance terminal-state behavior remain `UNVERIFIED`. Review may require the Phase 0 gate but must not endorse these symbols.
- V1.8 is design-only. No product implementation is authorized until independent review findings are dispositioned, the user approves the revised spec, and Phase 0 verifies runtime return codes plus external terminal states per instance.

### Main-Agent Self-Review Candidates

- `AlfworldExecutionFeedback` does not currently declare `success`, while the required model-visible JSON does. If feedback is the sole authority, success cannot be re-created downstream from `error` or a second generic layer.
- `action_not_applicable` appears in the Open/Close state flow but is absent from the exhaustive classification table, leaving terminal/scoring/backend-action semantics undefined.
- Context freshness is underspecified after a non-moving state action. Open must be able to refresh/rebase the same locked Oracle execution context for a following Put; treating every newer event as stale would force an unnecessary second navigation, while accepting arbitrary event drift would use stale proof.
- A visible movable may expose multiple parent memberships. The design says to choose a visible Oracle-capable anchor but does not yet define a deterministic unique rule or an explicit ambiguity failure; selecting a set-dependent first value would violate target-locking discipline.
- A successful Oracle move followed by exact-target visibility failure needs an explicit terminal Harness classification distinct from the pre-move, zero-action `target_not_visible` response.
- Provider/runtime availability and Harness coverage are conceptually separate. The current wording says a Provider failure lowers `coverage` while the formal-score gate is named `harness_valid_coverage`; the metric and denominator need one unambiguous definition.
- Current `harness_valid_coverage` is implemented as `score_eligible / total`, so Provider/runtime/artifact/cancelled failures are all counted as “Harness invalid.” The revised design should preserve a total formal-score eligibility gate while reporting separate Harness coverage and Provider/runtime availability, or explicitly rename the aggregate metric.
- The Runner receives `GenericAgentRuntime`'s actual string error code `transport_error`. `LLMProviderError` is an exception class and `provider_error` is a provider-level error type, not necessarily values that reach `_episode_classification()`. Normalization must be specified at one concrete boundary and tested with the real `RunResult` shape rather than a list of unlike symbols.
- The public registry also exposes `robot_find_object` and `robot_navigate`. Replacing only `robot_go_to` would leave possible navigation/hidden-object bypasses; the design must state whether these entry points delegate to the same Oracle executor, are removed, or remain under a proven non-moving contract.
- This is a concrete bypass, not a naming concern: `tools._exec_find_object()` calls `env_adapter.find_object()`, whose `_search_visible_object_source()` iterates known receptacles and sends environment navigation commands until the object appears. That behavior directly violates the approved hidden-object boundary and must be removed from the formal path with registry/call-boundary tests. `robot_navigate` separately reaches the legacy environment command path and must not remain an alternative low-level navigation mode.
- `robot_manipulate` supports take/open/close/put/heat/cool/clean/slice/use. The design's global claim that all model-visible execution state comes from typed feedback is broader than its Open/Close/Put flow and integration-test matrix. It must either define typed feedback for every public robot action or narrow the invariant and explain the unaffected actions without permitting generic result inference.
- The design must make safe `detail` construction explicit. Regex-redacting a free-form THOR string is not a complete security boundary; model-visible detail should come from allowlisted templates while raw external detail stays internal.

### Independent V1.8 Review Result

- The independent design verdict was `FIX`; a separate read-only code-boundary audit agreed. Neither agent changed files or endorsed external runtime symbols.
- All ten design findings and five boundary-audit supplements were accepted. The revised spec now includes old navigation bypass removal, typed tri-state feedback, two-stage Gate A/B verification, a context rebase state machine, deterministic anchor failure, exhaustive tool/Episode taxonomies, all-action coverage, distinct counters/metrics, auditable provider retry, exact Take locking and full deletion/synchronization boundaries.
- The feedback and Oracle integration remain design claims only. No product code was modified and all V1.8 external compositions remain `UNVERIFIED` until the corresponding true-environment gate passes.

### Revised-Spec Self-Audit

- Initial Markdown/diff check passed with no whitespace errors.
- Corrected four contract-level wording defects found after disposition: the old 6/10 mixed metric is now explicitly historical, the summary covers the full action gateway rather than only Open/Put, typed feedback uses `terminal=true + classification` correctly, and Dispatcher `invalid_tool_arguments/unknown_tool` are included in the closed nonterminal error map.
