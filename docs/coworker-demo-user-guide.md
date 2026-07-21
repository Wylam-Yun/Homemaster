# Change Coworker Demo User Guide

The coworker demo runs entirely on `hkust4`. Mac Screen Sharing is optional and is not a completion requirement. The headed Agent Chrome, read-only observer, terminal execution, scoring, and recording all belong to one server-side run ID.

## Setup

From the project root:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python ".[dev,coworker]"

uv venv --python 3.11 apps/case02_openenv/.venv
uv pip install --python apps/case02_openenv/.venv/bin/python \
  -e apps/case02_openenv

cp config/coworker_demo.example.yaml config/coworker_demo.yaml
test -f config/homemaster.yaml || cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/coworker_demo.yaml config/homemaster.yaml

realpath data/coworker_demo/case_02/test_set/item_change_ticket.json
```

Keep real provider keys only in `config/homemaster.yaml`; the committed `.example` files contain placeholders only. Both real config files are gitignored. The coworker config must point to the project data root, artifact root, app venv Python, system Chrome, TigerVNC, FFmpeg/ffprobe, tmux, Bash, and bubblewrap.

Run the secret-safe readiness check before every real recording:

```bash
.venv/bin/python scripts/coworker_demo/preflight.py \
  --coworker-config config/coworker_demo.yaml \
  --provider-config config/homemaster.yaml
```

PASS means the provider is configured without printing its key, both configs have mode `0600`, all executables and libx264 exist, port 8765 is free, the dataset bundle is locked, and at least 2 GiB is free.

## Run From The Existing Shell

Start the normal HomeMaster entrypoint:

```bash
.venv/bin/homemaster shell
```

For the normal scenario, send only the absolute ticket path printed by `realpath`:

```text
<HomeMaster project absolute path>/data/coworker_demo/case_02/test_set/item_change_ticket.json
```

For the anomaly and rollback scenario, prefix the same path with the stable scenario token:

```text
post_change_anomaly <HomeMaster project absolute path>/data/coworker_demo/case_02/test_set/item_change_ticket.json
```

Do not send API calls, shell commands, hidden scenario values, or evaluator hints. A valid route creates a child coworker run; an ordinary message still follows the default HomeMaster path.

For non-interactive acceptance, use the same shell entrypoint without setting
`HOMEMASTER_COWORKER_PROVIDER_CONFIG`:

```bash
TICKET=/home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json

printf '%s\n/exit\n' "$TICKET" | .venv/bin/homemaster shell
printf '%s\n/exit\n' "post_change_anomaly $TICKET" | .venv/bin/homemaster shell
```

This path uses the configured real provider. Final acceptance requires Mimo model
`mimo-v2.5`; a generated localhost provider config is a scripted override and is rejected by
the real-model verifier.

The shell prints the final reply, three scores, formal success, run root, and video path. A successful normal run has 24/24 trajectory nodes and 14/14 required result checkpoints. A successful anomaly run has 22/22 nodes, 11/11 checkpoints, an add grep exit 0, a remove grep exit 1, and terminal outcome `rolled_back`.

## Observe And Record

The run automatically starts a localhost-only TigerVNC display, then records the full 1920x1080 display with FFmpeg/libx264. The left region is the real Agent Chrome and the right region is a read-only executive observer; the persistent stage strip remains visible above the Agent window. No manual observer is needed.

The observer has five fixed regions: current locked SOP; model-authored Planner; model-selected
tool or bounded public reply; environment result plus deterministic decision summary; and
expanded open incident, one-line resolved incidents, and critical history.

The current task is exact source text from the locked ticket, with its source hash retained in
the presentation ledger. Planner is model-owned, tool results are environment-owned, and the
decision summary is reducer-owned. Tool cards are allowlisted projections, not raw prompts,
`assistant.thinking`, or chain-of-thought. Public reply is classified as `intermediate`,
`terminal`, or `premature`; hidden reasoning is never displayed. The observer has no reverse
data flow into the Agent.

For optional remote viewing, use the RFB port recorded in the run's display artifacts and forward it over SSH; do not expose the VNC listener publicly. Mac Screen Sharing behavior is not part of the delivery gate.

Each delivered run contains one original, continuous, unedited recording:

```text
var/coworker-demo/{run_id}/video/demo.mp4
```

The video intentionally shows frozen numeric scores with `video_verification=pending` and `formal_success=pending`. FFmpeg must stop before ffprobe, frame, and hash verification can set the final summary to formal success.

## Artifacts And Scores

Each `var/coworker-demo/{run_id}` contains `attempt_manifest.json`, safe
`agent/provider_identity.json`, locked inputs, Agent runtime events and task state,
environment audit/state snapshots, browser trace/screenshots, terminal command evidence,
normalized trajectory and DAG match, three score files, named frames/video manifest, and
`run_manifest.json` hashes. The presentation v2 contract requires `presentation/events.jsonl`,
`presentation/snapshot.json`, and `presentation/verification.json`; the snapshot contains the
Planner, current action, last result, public output, decision summary, incidents, and history.

- `trajectory_score`: required externally grounded actions matched in valid DAG order.
- `result_score`: required external state checkpoints passed for the selected scenario.
- `overall_score`: mean of trajectory and result scores.
- `formal_success`: true only when both scores are 100, no safety/environment/artifact/presentation failure exists, and independent video verification passed. A presentation failure can make formal success false without changing either business score.

Verify a run without importing the product evaluator:

```bash
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  var/coworker-demo/{run_id} \
  --data-root data/coworker_demo/case_02 \
  --expected-model mimo-v2.5
```

The verifier requires a fresh successful response naming `mimo-v2.5`, the expected HTTPS Mimo
host, no loopback/generated override, and no `scripted-coworker`. It independently rechecks
current-run job IDs/return codes, exact grep stdout, final config presence/absence, manifest
hashes, presentation correlation, H.264 properties, and source-correlated named frames. Failed
real attempts remain under their allocated run roots.

## Historical Scripted Presentation Gates

The 2026-07-18 bundles exercised the shell and presentation layout with a scripted provider.
They are useful UI evidence, but are not real-time LLM acceptance and cannot satisfy
`--expected-model mimo-v2.5`.

| Scenario | Run ID | Trajectory | Results | Terminal exits | Outcome | Video SHA-256 |
| --- | --- | ---: | ---: | --- | --- | --- |
| normal | `coworker-20260718-114159-20d39698` | 24/24 | 14/14 | `[0]` | `complete` | `ec33476aeb44ef872729c10f85dfeb2dfc6bcc701fd4a7da30c27d8e6169ebb0` |
| post-change anomaly | `coworker-20260718-114422-a848e90d` | 22/22 | 11/11 | `[0, 1]` | `rolled_back` | `31f89d350244f98ab563e006c25dd2c17bfbe27dc0e47c014fe4d563840d6001` |

Both summaries have `formal_success=true` under the scripted presentation gate. That proves the
scripted business trajectory and media bundle only; it does not prove that Mimo planned or
selected the displayed tools. Final real-model runs are recorded separately in the acceptance
report after expected-model verification.

## Failure Ownership

- Router/config/bundle failures happen before provider invocation and do not create a business run.
- DOM/backend rejections leave the mutation uncommitted and return a stable recovery reason.
- A submitted job is not success; the exact visible row must reach terminal status before terminal or progress gates open.
- Provider premature replies, budget exhaustion, service/display/terminal failures, video or presentation failures, safety violations, missing artifacts, and any score below 100 make formal success false.
- `scripted_shell_gate.py --profile observable_failures` validates failure display and recovery only. It can never replace a real Mimo recording.
- Never repair a failed trajectory by editing audit files, reordering events, or copying evidence from another run. Fix the gate, add a regression, and use a fresh run ID.
