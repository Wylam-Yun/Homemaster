# V3.5 Phase 5 Evidence

## Gateway smoke manifest

`gateway-smoke-live/summary.json` and its per-trial JSON were produced on hkust4
by `scripts/verify_v35_gateway_smoke_live.py`. The single configured
`gateway_smoke_trials.json` entry completed reset, Drawer navigation, a real
`SaltShaker` manipulation, and close. Both actions and close returned
`external_return_code=0`; the worker exited with code `0`, cleanup was observed,
and persistent stderr had no traceback. The record includes request IDs, raw
state before and after each action, object identity, worker identity, close
receipt, and terminal state.

## Setup and release gates

`setup-gates-20260926.json` records PASS for both setup scripts on hkust4. The
general `scripts/setup.sh` gate used `uv 0.11.19`, imported `homemaster` and
`mindmemos`, and finished with doctor `16/16 PASS`, including MindMemOS read/write,
ALFWorld binding, isolated worker IPC, and ignored-runtime checks.

The local `scripts/verify_v35_release.py --json` gate passed wheel/sdist build,
package data, sdist reproducibility inputs, `homemaster.alfworld` import from
the wheel, isolated worker protocol import, git-tracked lock files, and private
runtime/config ignore rules.

After the final worker grounding fix, the remote reruns passed independently:
gateway smoke, Oracle worker use, continuous taskset worker, failure black-box,
and action/close SIGINT cleanup. The remote focused regression is `150 passed,
2 deselected`; the local equivalent is `151 passed, 2 deselected`.

The remote `v35-architecture` checkout was run with the installed ALFWorld/AI2-THOR
environment on Xvfb `:99`. Evidence is per instance; summaries are not used as a
substitute for state readback.

## Single episode

- Trial: `valid_unseen/pick_and_place_simple-Mug-None-Desk-308/trial_T20190908_125200_737896/traj_data.json`
- Record: `valid_unseen/pick_and_place_simple-Mug-None-Desk-308/trial_T20190908_125200_737896/traj_data.json/episode.json`
- Reset, PNG digest/readback, two navigations, `take`, `put`, and close each returned
  code `0`; the independent worker state ended with `won=true` and terminal owner
  `alfworld_thor`.
- Worker exit code was `0`; the persistent `worker.stderr.log` contains no traceback.

## Continuous taskset

### Isolated worker path

The same two-subtask fixture was also run through the continuous isolated-worker
adapter. The complete record is under
`taskset-worker-easy_living_room_219/`, including per-subtask frame artifacts,
the trial manifest, request/response history, raw state digests, persistent
stderr, and cleanup evidence.

- `reset` returned `external_return_code=0` and `scene_generation=1`.
- `set_task` returned `status=ok`, `external_return_code=0`, advanced
  `goal_generation` from 1 to 2, and preserved the scene digest byte-for-byte.
- The exercised navigation was rejected by the real environment and recorded as
  `harness_operation_failure`; it did not become a false success.
- `close` returned code `0`, the worker exit code was `0`, and stderr contained no traceback.

Command (remote host):

```bash
DISPLAY=:99 PYTHONPATH=/home/haodong2/weilin/red_bird/Homemaster-v35/src \
HOMEMASTER_LIVE_ALFWORLD_ROOT=/home/haodong2/weilin/red_bird/Homemaster/.runtime/alfworld \
HOMEMASTER_PHASE5_EVIDENCE_ROOT=/home/haodong2/weilin/red_bird/Homemaster-v35/plan/V3.5/evidence/phase-5 \
/home/haodong2/weilin/red_bird/Homemaster/.runtime/alfworld-venv/bin/python \
scripts/verify_v35_taskset_live.py
```

- `taskset-easy_living_room_219/subtask-01/episode.json`: reset snapshot,
  `scene_generation=1`, four successful THOR actions, and `won=true`.
- `taskset-easy_living_room_219/subtask-02/episode.json`: `goal_generation=2`,
  `set_task` returned code `0`, before/after scene digests are identical, and
  every action has an independent before/after state record. The retained
  RemoteControl makes the real `take CellPhone` precondition fail; this is recorded
  as `agent_model_failure`, not as a Harness success.
- `taskset-easy_living_room_219/cleanup.json`: THOR close `succeeded`, return code
  `0`, and the simulator process was no longer alive after close.
- `taskset-run.stdout` and `taskset-run.stderr` preserve the command output; stderr
  is empty for this run.

## SIGINT shutdown

`sigint-20260926/sigint.json` contains two independent live runs. The action
case received a real `SIGINT` during an action, reconciled the late action
receipt, then closed with external return code `0`; the close case received a
real `SIGINT` while waiting for the close receipt, still produced the close
receipt with return code `0`, recorded `close_sigint_count=1`, exited the worker
with code `0`, left no descendants, and emitted no traceback. The persistent
action and close stderr logs are included beside the JSON record.

## Oracle-grounded worker action

`live-worker-use-20260926/worker-use.json` is a deterministic real-worker check
for `look_at_obj_in_light-AlarmClock-None-DeskLamp-323`. The worker identity
reported `allow_offscreen_object_navigation=true`. It completed dresser
navigation, `take alarmclock`, exact DeskLamp navigation, and `use desklamp`;
each protocol action returned code `0`. Independent THOR metadata ended with
the selected alarm clock `isPickedUp=true` and the selected lamp
`isToggled=true`; `close` returned code `0`, the worker exited with code `0`,
and stderr contained no traceback. The `use` receipt recorded three backend
actions because it included frozen-pose visibility navigation.

## Provider-backed CLI success

`live-cli-grounding2-20260926/` is a formal launcher run using the same trial
manifest and the checkout `src/` through `PYTHONPATH`. The per-instance and
aggregate summaries report `agent_success`, `won=true`, goal condition rate
`1.0`, zero invalid actions, and provider/runtime availability `1.0`. The
worker record has action and close return code `0`, worker exit code `0`, no
descendants after close, and no traceback in stderr.

## Provider-backed taskset CLI success

`taskset-cli-single-20260926c/` is a bounded formal
`benchmark-alfworld-taskset` run using the same isolated worker path. It checks
one fixed `valid_seen` taskset subtask (`AlarmClock` under `DeskLamp`) so the
provider-backed CLI gate is independent of the model-sensitive five-subtask
fixture. The summary reports `agent_success`, chain success, goal condition rate
`1.0`, provider/runtime/harness coverage `1.0`, and CLI exit code `0`.

The same run's `worker.json` records close `external_return_code=0`, worker exit
code `0`, `worker_exited=true`, and traceback-free stderr. The taskset runner now
writes this lifecycle record in its `finally` block, matching the single-episode
worker evidence contract.
