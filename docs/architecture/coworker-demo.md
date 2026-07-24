# Change Coworker Architecture

## Scope

The coworker capability is a narrow route inside the existing interactive shell. It does not
add a second CLI command or change the default home registry or ALFWorld. It reuses
`GenericAgentRuntime`; the shared provider client emits a successful response lifecycle event
so a run can prove fresh model acceptance. Only a valid locked `case_02` ticket creates the
isolated coworker composition.

## Data Flow

```text
shell utterance
  -> deterministic ticket router and CaseRepository hash lock
  -> configured real LLM / child GenericAgentRuntime / exact eleven-tool registry
  -> Playwright headed Chrome -> ticket, monitor, automation DOM
  -> FastAPI EpisodeStore -> action ledger, jobs, state, append-only audit
  -> safe presentation v2 projection -> append-only tool/public-reply ledger
  -> pure observable reducer -> atomic snapshot + SSE -> read-only observer
  -> tmux + Bash + bubblewrap -> run-local /opt/app config readback
  -> raw-event join -> effective trajectory -> scenario DAG matcher
  -> independent 16-checkpoint result evaluator -> three scores
  -> Agent Chrome + read-only observer -> TigerVNC -> FFmpeg H.264
  -> independent provider/presentation/external-state/ffprobe/frame/hash verification
  -> run manifest and formal-success summary
```

One generated `run_id` owns every URL, action reservation, job, evidence reference, task snapshot, process, file, score, and video. Cross-run IDs and stale state versions are rejected before mutation.

The observable path has no reverse edge:

```text
real LLM
  -> tool call or bounded public reply runtime event
  -> safe presentation v2 projection
  -> append-only presentation ledger
  -> pure observable reducer
  -> atomic snapshot + SSE
  -> read-only observer + continuous video
```

## Runtime Boundaries

The model receives exactly eleven tools: planner, progress, two-name `skill_view`, six browser
tools, restricted terminal execution, and `sop_decide`. The Planner snapshot is model-owned;
the environment result is environment-owned; the decision summary is deterministically
reducer-owned. It has no business API, arbitrary URL, arbitrary shell, observer, audit, score,
artifact, scenario, or ground-truth tool.

The three Agent pages receive explicit Pydantic public projections. Observer-only fault fields,
evaluator state, causal IDs, scores, and formal verdicts do not enter their HTML, JavaScript
config, browser observations, success payloads, or errors. The observer route is unavailable to
Agent navigation tools. It shows exact SOP text, safe tool/result fields, Planner and bounded
public replies; `assistant.thinking`, prompt, constraints, open questions, credentials and raw
chain-of-thought are forbidden. `/openapi.json`, `/docs`, and `/redoc` are disabled at runtime;
`apps/case02_openenv/openapi.json` is an offline drift snapshot.

Browser mutations use a unique action ID and current page state version. The environment reserves the action before dispatch and consumes it exactly once. Read-only browser actions also reserve/consume so replay and stale versions have one contract. The browser can navigate only to the locked run's ticket, monitor, and automation pages.

Terminal execution accepts only the exact ticket `grep -A 3` command and parses it structurally. Each call gets a dedicated tmux session and bubblewrap namespace; the run-local episode root is mounted at `/opt/app`, while the host `/opt/app` is neither required nor mutated.

## Evidence And Gate Invariants

Runtime intent and model narration are not external evidence. An effective action must join persisted receipts for the same run/action and carry the trusted EpisodeStore stage. Each action can match at most one DAG node.

The environment rejects invalid ordering before the successor side effect or audit append:

- `observe` may be used to inspect the ticket visually; neither it nor a planner event authorizes or blocks browser actions.
- All seven prechecks precede precheck proceed; `PRE_PROGRESS` precedes change submission.
- The exact add job wait precedes terminal grep; add+grep precede implementation proceed; `IMPLEMENT_PROGRESS` precedes postchecks.
- Normal progress requires all five postchecks plus the exact business job wait.
- A current-run add success and grep arm the anomaly. A causal alarm authorizes rollback; exact remove wait and grep absence precede rollback progress and `rolled_back`.
- Terminal decisions stop later model-selected external tools, while fixed finalize/recording cleanup remains available.

No layer synthesizes missing actions, changes event order, chooses a later event to hide an earlier invalid node, or accepts evidence from another run.

Every failed/rejected model tool opens one incident carrying a closed safe failure code. Only
the code-specific recovery rule can resolve it; `terminal_outcome` never auto-recovers. Open
incidents are expanded, resolved incidents remain as one-line history, and reconnect rebuilds
both from the append-only ledger rather than browser memory.

## Scoring And Artifacts

Trajectory and result evaluation are separate. The normal denominator is 24 DAG nodes and 14 result checkpoints; anomaly uses 22 nodes and 11 required checkpoints. Historical 16 checkpoint IDs remain stable, with scenario-inapplicable items marked separately.

`attempt_manifest.json` is created immediately after run-root allocation and survives provider,
business, presentation, recording, or verification failure. `run_manifest.json` is the artifact
index and hashes every registered artifact except itself. Presentation v2 artifacts are
required, and the independent verifier rederives SOP hashes, tool/action/incident correlation,
current-run external state, provider identity and every request/response iteration without
importing the product evaluator. Every successful Planner/progress result must carry a legal
plan, and every provider iteration must have exactly one ordered successful response before its
tool selection. With `--expected-model mimo-v2.5`, loopback/generated
overrides and `scripted-coworker` are rejected.

Finalization first freezes numeric scores while recording is active. After the score hold,
Playwright closes and FFmpeg exits normally. Recording stop has a dedicated 180-second client
timeout because long videos require multiple frame decodes. The service serializes stop and
caches its completed recorder/display result, so a retry returns the same outcome without
writing to an already-closed FFmpeg stdin. Independent checks cover observer health,
H.264/1920x1080/yuv420p, duration, first-packet evidence, first/middle/last pixels, and named
first-action/incident/causal-alarm/terminal frames. A named-frame offset must remain before the
next presentation event. Presentation failure leaves business scores unchanged but makes formal
success false.

## Process Lifecycle And Deployment

The FastAPI service binds to configured loopback port 8765. TigerVNC selects an available configured display and exposes RFB only on loopback. Recording startup runs in FastAPI's worker thread so the async service loop remains free to serve the Observer HTML, CSS, JavaScript, presentation stream, and score polling while startup waits. FFmpeg starts only after the display has sufficient non-black pixels, dark pixels, and luminance variance, which rejects a white Chrome loading page.

Observer Chrome fills the recording background; Agent Chrome occupies the fixed left region and leaves the right observer region and top stage strip visible. Observer Chrome, Agent Chrome, FFmpeg, tmux, and service processes are run-owned and cleaned in `finally` paths.

The service executable path preserves the venv Python symlink using an absolute path without `resolve()`. Resolving that symlink would launch the base interpreter and lose venv packages. Ordinary data roots are still resolved for containment checks.

All provider credentials remain in mode-0600 gitignored config. The run-owned provider identity
stores only safe provider/model/transport/host metadata, override state and a nonsecret
fingerprint. Preflight is configuration readiness only; server-side model identity remains
UNVERIFIED until a fresh successful transport response names the expected model. Runtime traces
sanitize secret fields and do not expose raw provider credentials.

Mac Screen Sharing is an optional view of the server-owned VNC display. It does not host the environment, execute actions, produce evidence, or gate completion.
