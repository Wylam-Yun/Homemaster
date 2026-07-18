# Change Coworker Architecture

## Scope

The coworker capability is a narrow route inside the existing interactive shell. It does not add a second CLI command and does not modify `GenericAgentRuntime`, the default home registry, provider/session behavior, or ALFWorld. Only a valid locked `case_02` ticket creates the isolated coworker composition.

## Data Flow

```text
shell utterance
  -> deterministic ticket router and CaseRepository hash lock
  -> child session / GenericAgentRuntime / exact eleven-tool registry
  -> Playwright headed Chrome -> ticket, monitor, automation DOM
  -> FastAPI EpisodeStore -> action ledger, jobs, state, append-only audit
  -> sanitized presentation projection -> exact SOP task, tool and result ledger
  -> tmux + Bash + bubblewrap -> run-local /opt/app config readback
  -> raw-event join -> effective trajectory -> scenario DAG matcher
  -> independent 16-checkpoint result evaluator -> three scores
  -> Agent Chrome + read-only observer -> TigerVNC -> FFmpeg H.264
  -> independent presentation/ffprobe/frame/hash verification
  -> run manifest and formal-success summary
```

One generated `run_id` owns every URL, action reservation, job, evidence reference, task snapshot, process, file, score, and video. Cross-run IDs and stale state versions are rejected before mutation.

## Runtime Boundaries

The model receives exactly eleven tools: planner, progress, two-name `skill_view`, six browser tools, restricted terminal execution, and `sop_decide`. It has no business API, arbitrary URL, arbitrary shell, observer, audit, score, artifact, scenario, or ground-truth tool.

The three Agent pages receive explicit Pydantic public projections. Observer-only fault fields, evaluator state, causal IDs, scores, and formal verdicts do not enter their HTML, JavaScript config, browser observations, success payloads, or errors. The observer route is unavailable to the Agent's navigation tools. It shows exact locked SOP source text and allowlisted runtime tool/result projections, never raw prompts or chain-of-thought, and it does not claim to independently confirm real monitoring truth. `/openapi.json`, `/docs`, and `/redoc` are disabled at runtime; `apps/case02_openenv/openapi.json` is an offline drift snapshot.

Browser mutations use a unique action ID and current page state version. The environment reserves the action before dispatch and consumes it exactly once. Read-only browser actions also reserve/consume so replay and stale versions have one contract. The browser can navigate only to the locked run's ticket, monitor, and automation pages.

Terminal execution accepts only the exact ticket `grep -A 3` command and parses it structurally. Each call gets a dedicated tmux session and bubblewrap namespace; the run-local episode root is mounted at `/opt/app`, while the host `/opt/app` is neither required nor mutated.

## Evidence And Gate Invariants

Runtime intent and model narration are not external evidence. An effective action must join persisted receipts for the same run/action and carry the trusted EpisodeStore stage. Each action can match at most one DAG node.

The environment rejects invalid ordering before the successor side effect or audit append:

- Ticket read precedes a real planner event; operational browser work cannot start without `PLAN_CREATED`.
- All seven prechecks precede precheck proceed; `PRE_PROGRESS` precedes change submission.
- The exact add job wait precedes terminal grep; add+grep precede implementation proceed; `IMPLEMENT_PROGRESS` precedes postchecks.
- Normal progress requires all five postchecks plus the exact business job wait.
- A current-run add success and grep arm the anomaly. A causal alarm authorizes rollback; exact remove wait and grep absence precede rollback progress and `rolled_back`.
- Terminal decisions stop later model-selected external tools, while fixed finalize/recording cleanup remains available.

No layer synthesizes missing actions, changes event order, chooses a later event to hide an earlier invalid node, or accepts evidence from another run.

## Scoring And Artifacts

Trajectory and result evaluation are separate. The normal denominator is 24 DAG nodes and 14 result checkpoints; anomaly uses 22 nodes and 11 required checkpoints. Historical 16 checkpoint IDs remain stable, with scenario-inapplicable items marked separately.

`run_manifest.json` is the sole artifact index and hashes every registered artifact except itself. `presentation/events.jsonl`, `presentation/snapshot.json`, and `presentation/verification.json` are required, and the independent bundle verifier rederives SOP hashes and tool/action correlation without importing the product evaluator. Finalization first freezes numeric scores while recording is active. After the display holds those scores, Playwright closes, FFmpeg exits normally, and independent checks cover presentation completeness, Observer process health, H.264/1920x1080/yuv420p, duration, frame count, first/middle/last content, and SHA-256. A presentation failure leaves trajectory and result scores unchanged but makes formal success false. Only a fully verified run can become formally successful.

## Process Lifecycle And Deployment

The FastAPI service binds to configured loopback port 8765. TigerVNC selects an available configured display and exposes RFB only on loopback. Recording startup runs in FastAPI's worker thread so the async service loop remains free to serve the Observer HTML, CSS, JavaScript, presentation stream, and score polling while startup waits. FFmpeg starts only after the display has sufficient non-black pixels, dark pixels, and luminance variance, which rejects a white Chrome loading page.

Observer Chrome fills the recording background; Agent Chrome occupies the fixed left region and leaves the right observer region and top stage strip visible. Observer Chrome, Agent Chrome, FFmpeg, tmux, and service processes are run-owned and cleaned in `finally` paths.

The service executable path preserves the venv Python symlink using an absolute path without `resolve()`. Resolving that symlink would launch the base interpreter and lose venv packages. Ordinary data roots are still resolved for containment checks.

All provider credentials remain in mode-0600 gitignored config. Preflight reports only provider name/model/format and key count. Runtime traces sanitize secret fields and do not expose raw provider credentials.

Mac Screen Sharing is an optional view of the server-owned VNC display. It does not host the environment, execute actions, produce evidence, or gate completion.
