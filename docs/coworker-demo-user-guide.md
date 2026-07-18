# Change Coworker Demo User Guide

The coworker demo runs entirely on `hkust4`. Mac Screen Sharing is optional and is not a completion requirement. The headed Agent Chrome, read-only observer, terminal execution, scoring, and recording all belong to one server-side run ID.

## Setup

From the project root:

```bash
cp config/coworker_demo.example.yaml config/coworker_demo.yaml
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/coworker_demo.yaml config/homemaster.yaml
```

Keep real provider keys only in `config/homemaster.yaml`. Both real config files are gitignored. The coworker config must point to the project data root, artifact root, app venv Python, system Chrome, TigerVNC, FFmpeg/ffprobe, tmux, Bash, and bubblewrap.

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

For the normal scenario, send only the absolute ticket path:

```text
/home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json
```

For the anomaly and rollback scenario, prefix the same path with the stable scenario token:

```text
post_change_anomaly /home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/test_set/item_change_ticket.json
```

Do not send API calls, shell commands, hidden scenario values, or evaluator hints. A valid route creates a child coworker run; an ordinary message still follows the default HomeMaster path.

The shell prints the final reply, three scores, formal success, run root, and video path. A successful normal run has 24/24 trajectory nodes and 14/14 required result checkpoints. A successful anomaly run has 22/22 nodes, 11/11 checkpoints, an add grep exit 0, a remove grep exit 1, and terminal outcome `rolled_back`.

## Observe And Record

The run automatically starts a localhost-only TigerVNC display, then records the full 1920x1080 display with FFmpeg/libx264. The left region is the real Agent Chrome and the right region is a read-only executive observer; the persistent stage strip remains visible above the Agent window. No manual observer is needed.

The observer's current task is the exact source text from the locked ticket, with its source hash retained in the presentation ledger. Tool and result cards are allowlisted projections of runtime events, not raw prompts, assistant thinking, or chain-of-thought. The observer is unavailable to the Agent and summarizes workflow evidence only; it does not independently confirm real monitoring truth.

For optional remote viewing, use the RFB port recorded in the run's display artifacts and forward it over SSH; do not expose the VNC listener publicly. Mac Screen Sharing behavior is not part of the delivery gate.

Each delivered run contains one original, continuous, unedited recording:

```text
var/coworker-demo/{run_id}/video/demo.mp4
```

The video intentionally shows frozen numeric scores with `video_verification=pending` and `formal_success=pending`. FFmpeg must stop before ffprobe, frame, and hash verification can set the final summary to formal success.

## Artifacts And Scores

Each `var/coworker-demo/{run_id}` contains locked inputs, Agent runtime events and task state, environment audit/state snapshots, browser trace/screenshots, terminal command evidence, normalized trajectory and DAG match, three score files, video frames/manifest, and `run_manifest.json` hashes. The required presentation artifacts are `presentation/events.jsonl`, `presentation/snapshot.json`, and `presentation/verification.json`.

- `trajectory_score`: required externally grounded actions matched in valid DAG order.
- `result_score`: required external state checkpoints passed for the selected scenario.
- `overall_score`: mean of trajectory and result scores.
- `formal_success`: true only when both scores are 100, no safety/environment/artifact/presentation failure exists, and independent video verification passed. A presentation failure can make formal success false without changing either business score.

Verify a run without importing the product evaluator:

```bash
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  var/coworker-demo/{run_id}
```

The verifier rechecks required nodes/checkpoints, terminal exit codes, final config presence or absence, every manifest hash, SOP source hashes, presentation action/result correlation, the product presentation report, H.264 stream properties, frame count/duration, and the verified video manifest.

## Accepted Fresh Runs

The final implementation was exercised through the existing shell on 2026-07-18. Both bundles passed the independent verifier and manual first/middle/last frame review.

| Scenario | Run ID | Trajectory | Results | Terminal exits | Outcome | Video SHA-256 |
| --- | --- | ---: | ---: | --- | --- | --- |
| normal | `coworker-20260718-114159-20d39698` | 24/24 | 14/14 | `[0]` | `complete` | `ec33476aeb44ef872729c10f85dfeb2dfc6bcc701fd4a7da30c27d8e6169ebb0` |
| post-change anomaly | `coworker-20260718-114422-a848e90d` | 22/22 | 11/11 | `[0, 1]` | `rolled_back` | `31f89d350244f98ab563e006c25dd2c17bfbe27dc0e47c014fe4d563840d6001` |

Both summaries have `formal_success=true`. The normal recording begins on the rendered Observer rather than a Chrome loading page and ends with all four frozen score fields visible. The anomaly recording visibly shows the causal active alarm, enters the rollback stage, selects `remove`, proves grep absence, and ends with `rolled_back` plus the frozen scores.

## Failure Ownership

- Router/config/bundle failures happen before provider invocation and do not create a business run.
- DOM/backend rejections leave the mutation uncommitted and return a stable recovery reason.
- A submitted job is not success; the exact visible row must reach terminal status before terminal or progress gates open.
- Provider premature replies, budget exhaustion, service/display/terminal failures, video or presentation failures, safety violations, missing artifacts, and any score below 100 make formal success false.
- Never repair a failed trajectory by editing audit files, reordering events, or copying evidence from another run. Fix the gate, add a regression, and use a fresh run ID.
