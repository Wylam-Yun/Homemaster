# HomeMaster Coworker Demo Implementation Plan

> **执行约束：** 本计划由主 agent 在当前专用 worktree 中逐项 inline 执行。用户提供的 `AGENTS.md` 禁止把实现、调试、测试、外部验证或文档派给 subagent；只允许在本计划完整后启动一次只读 plan reviewer，并在全部代码、测试、外部终态、视频和文档完成后启动一次只读 final reviewer。所有 checklist 使用 `- [ ]` 跟踪。

**Goal:** 保留现有 `homemaster shell` 和默认 Agent 行为，让用户把真实 `case_02` 工单路径作为对话消息发送后，HomeMaster 自主完成真实 DOM 与 tmux/Bash 变更闭环，并交付可审计轨迹、DAG 匹配、双域评分、observer、H.264 录屏和完整 run artifact bundle。

**Architecture:** CLI-facing router 只识别并验证单一 ticket bundle；命中后由独立 coworker turn adapter 创建 child session，按现有 ALFWorld 的依赖注入形状组装未修改的 `GenericAgentRuntime`。Agent 只获得专用浏览器、终端、决策、规划和 skill 工具；业务状态由独立 FastAPI 环境通过真实 DOM、后台 job 和 tmux/Bash 改变，evaluator 只消费同一 `run_id` 的外部证据。TigerVNC display 同时承载 Agent Chrome、observer 和 transcript，FFmpeg 在首次 provider 调用前开始捕获该真实 display。

**Tech Stack:** Python 3.11、HomeMaster GenericAgentRuntime/ToolRegistry、Pydantic、httpx、FastAPI/Uvicorn、Playwright + system Chrome、HTML/CSS/vanilla JS、tmux/Bash/bubblewrap、TigerVNC/X11、FFmpeg/libx264、pytest/Ruff、uv lock。

**Design source:** `plan/change-coworker-demo-design.md` at commit `c6b8c46aee44c5e39d692d05feffb32409b8820f`.

---

## 1. Locked Decisions And Alternatives

| Candidate | Benefit | Cost / risk | Decision |
|---|---|---|---|
| 1. FastAPI + Playwright system Chrome | Typed OpenAPI, real DOM actions, SSE support, concise service/test surface | Both packages and configured-display composition are `UNVERIFIED` until Gate L1 | **MVP recommendation**, contingent on L1 |
| 2. Starlette + Playwright | Smaller HTTP layer while retaining browser driver | Recreates validation/schema conveniences already needed by evaluator | Reject unless FastAPI itself, rather than Playwright, is the proven linchpin failure |
| 3. stdlib HTTP + direct Chrome DevTools Protocol | Minimal Python dependencies | Hand-written CDP session/event handling raises browser and audit risk | Replacement `BrowserDriver` only if L1 proves Playwright unusable; no second business mode |
| 4. Run HomeMaster and environment on Mac | Native visible desktop | Diverges from hkust4 files, provider config, tmux/bubblewrap, and target execution environment | Reject; Mac remains a VNC observer only |

One upstream architecture removes mode proliferation: all Agent/business/evaluator work remains on `hkust4`; only the `BrowserDriver` implementation may be replaced after a documented linchpin failure. OpenEnv and BrowserGym are not dependencies for this MVP.

**Post-review user override (2026-07-16):** Mac Screen Sharing is optional and is not a completion gate for this delivery. The server-side headed display, localhost-only TigerVNC, DOM/backend/X11 evidence, FFmpeg recording and verified H.264 files remain mandatory. No second plan review is created because this is an explicit user scope decision after the locked review.

External symbols such as `sync_playwright`, FastAPI lifespan/SSE composition, `Xtigervnc` flags, FFmpeg x11grab options and uv commands remain **UNVERIFIED** until their exact command returns success and the independent external state gate passes.

## 2. File Map

### HomeMaster integration

- Create `src/homemaster/cli/coworker_router.py`: deterministic utterance-to-ticket routing; no environment or provider side effects on no-match.
- Modify `src/homemaster/cli/interactive_shell.py`: one router branch and coworker result rendering; ordinary path remains the current `run_agent_turn()` call.
- Create `src/homemaster/benchmarking/coworker_demo/__init__.py`: public coworker adapter exports only.
- Create `src/homemaster/benchmarking/coworker_demo/config.py`: independent gitignored YAML config loader and path validation.
- Create `src/homemaster/benchmarking/coworker_demo/types.py`: route/turn/tool result contracts and outcome classifications.
- Create `src/homemaster/benchmarking/coworker_demo/budget.py`: one monotonic deadline, action counters and deadline-aware provider wrapper.
- Create `src/homemaster/benchmarking/coworker_demo/ticket_bundle.py`: `CaseRepository`, manifest/hash/schema/scenario lock.
- Create `src/homemaster/benchmarking/coworker_demo/environment_client.py`: typed HTTP client and environment process lifecycle.
- Create `src/homemaster/benchmarking/coworker_demo/browser_driver.py`: `BrowserDriver` protocol and Playwright implementation.
- Create `src/homemaster/benchmarking/coworker_demo/browser_tools.py`: six browser `ToolSpec` factories.
- Create `src/homemaster/benchmarking/coworker_demo/terminal_tools.py`: allowlisted terminal tool adapter.
- Create `src/homemaster/benchmarking/coworker_demo/decision_tools.py`: persisted SOP decision adapter and stop outcome.
- Create `src/homemaster/benchmarking/coworker_demo/registry.py`: exactly eleven coworker tools plus wrapped planner/progress/skill tracing.
- Create `src/homemaster/benchmarking/coworker_demo/skills.py`: load only two coworker skills.
- Create `src/homemaster/benchmarking/coworker_demo/tracing.py`: local raw/runtime/transcript sinks and HTTP mirror.
- Create `src/homemaster/benchmarking/coworker_demo/prompt.py`: coworker-only system/task prompt without case answers.
- Create `src/homemaster/benchmarking/coworker_demo/turn.py`: child session orchestration, cleanup, classification, scoring/video handoff.
- Create `src/homemaster/benchmarking/coworker_demo/skills/change_execution/SKILL.md`.
- Create `src/homemaster/benchmarking/coworker_demo/skills/evidence_discipline/SKILL.md`.

### Case02 environment

- Create `apps/case02_openenv/pyproject.toml` and `apps/case02_openenv/uv.lock`: independent service dependencies.
- Create `apps/case02_openenv/src/case02_openenv/__init__.py` and `__main__.py`: package and Uvicorn entrypoint.
- Create `apps/case02_openenv/src/case02_openenv/config.py`: service/display/artifact configuration.
- Create `apps/case02_openenv/src/case02_openenv/models.py`: API, state, event, job and scoring models.
- Create `apps/case02_openenv/src/case02_openenv/public_views.py`: explicit Agent-visible projections with no hidden scenario/evaluator fields.
- Create `apps/case02_openenv/src/case02_openenv/artifacts.py`: atomic JSON/manifest/hash ownership.
- Create `apps/case02_openenv/src/case02_openenv/episode_store.py`: run-scoped state machine and append-only evidence store.
- Create `apps/case02_openenv/src/case02_openenv/automation.py`: accepted/running/succeeded/failed jobs and external config mutation.
- Create `apps/case02_openenv/src/case02_openenv/terminal/policy.py`: exact `grep -A 3` parser and target lock.
- Create `apps/case02_openenv/src/case02_openenv/terminal/executor.py`: real tmux/Bash/bubblewrap process and evidence capture.
- Create `apps/case02_openenv/src/case02_openenv/evaluation/trajectory.py`: raw-event join and effective-action normalization.
- Create `apps/case02_openenv/src/case02_openenv/evaluation/matcher.py`: per-node DAG matcher and safety violations.
- Create `apps/case02_openenv/src/case02_openenv/evaluation/results.py`: independent 16-checkpoint external-state evaluator.
- Create `apps/case02_openenv/src/case02_openenv/evaluation/scoring.py`: trajectory/result/overall and formal gate.
- Create `apps/case02_openenv/src/case02_openenv/recording/display.py`: dedicated TigerVNC display and fixed window layout.
- Create `apps/case02_openenv/src/case02_openenv/recording/recorder.py`: FFmpeg lifecycle and first-packet gate.
- Create `apps/case02_openenv/src/case02_openenv/recording/verifier.py`: ffprobe and region-aware frame validation.
- Create `apps/case02_openenv/src/case02_openenv/api.py`: exact HTTP routes, pages and SSE.
- Create `apps/case02_openenv/templates/{ticket,monitor,automation,observer}.html`: visible run-scoped DOM.
- Create `apps/case02_openenv/static/app.css` and `apps/case02_openenv/static/{ticket,monitor,automation,observer}.js`: compact operations UI and backend calls.
- Create `apps/case02_openenv/openapi.json`: generated contract snapshot.

### Data, configuration and verification

- Modify `pyproject.toml`; create root `uv.lock`: root coworker optional dependencies and packaged skills.
- Modify `.gitignore`; create `config/coworker_demo.example.yaml`; keep real `config/coworker_demo.yaml` ignored.
- Populate `data/coworker_demo/case_02/{README.md,schema,test_set,ground_truth,sop_log_mapping.yaml}` from the authoritative Hawkeye bundle.
- Modify `data/coworker_demo/case_02/dataset_manifest.json`: retain all source hashes/counts and add overlay/DAG paths and hashes.
- Create `data/coworker_demo/case_02/scenarios/{normal,post_change_anomaly}.yaml`.
- Create `data/coworker_demo/case_02/agent_trajectory_ground_truth.yaml` and generated `agent_trajectory_ground_truth.md` review snapshot.
- Create `scripts/coworker_demo/{verify_dataset_bundle,linchpin_browser_vnc,linchpin_recording,linchpin_terminal,scripted_shell_gate,verify_run_bundle,preflight}.py`.
- Create `scripts/coworker_demo/__init__.py`: importable pure probe parsers without running external commands at import time.
- Create focused tests under `tests/coworker_demo/`, `tests/homemaster/benchmarking/coworker_demo/` and `tests/case02_openenv/`; modify `tests/homemaster/test_cli_interactive.py`.
- Create `plan/change-coworker-demo-review-disposition.md`: main-agent record of the one plan review, one final review and every disposition; reviewers do not edit it.
- Modify `README.md`, `CHANGELOG.md`; create `docs/coworker-demo-user-guide.md` and `docs/architecture/coworker-demo.md`.
- Update `docs/pitfalls.md` and `CLAUDE.md` only if a non-obvious false-green or real-environment failure meets the repository's serious-bug rule; otherwise record that the condition did not occur in the execution log.

## 3. Stable Contracts

### 3.1 Router result

```python
class TicketRouteKind(str, Enum):
    NO_MATCH = "no_match"
    VALID_TICKET = "valid_ticket"
    INVALID_TICKET_INTENT = "invalid_ticket_intent"

@dataclass(frozen=True)
class ValidTicketRoute:
    kind: Literal[TicketRouteKind.VALID_TICKET]
    ticket_path: Path
    case_root: Path
    scenario_id: Literal["normal", "post_change_anomaly"]
    locked_hashes: dict[str, str]

@dataclass(frozen=True)
class InvalidTicketRoute:
    kind: Literal[TicketRouteKind.INVALID_TICKET_INTENT]
    error_code: str
    message: str
```

`NO_MATCH` never loads coworker YAML, creates a directory, opens a network connection or calls a provider. A message with zero path candidates is no-match; `.json` intent with a missing path, more than one candidate, wrong schema, escaping manifest path, bad hash, absent overlay/DAG or ambiguous scenario is invalid before provider invocation.

### 3.2 Tool-visible result

```python
class CoworkerToolPayload(BaseModel):
    success: bool
    run_id: str
    action_id: str
    backend_status: Literal["not_applicable", "accepted", "running", "succeeded", "failed"]
    page_state_version: int
    visible_observation: dict[str, object]
    evidence_refs: list[str]
    retryable: bool = False
    failure_reason: str | None = None
```

Every executor returns this mapping inside `ToolResult.data`; the independent environment store must be able to return each evidence ID. An evidence string present only in model context is rejected by normalization.

Decision values are closed to `proceed`, `block`, `rollback`, `complete`, `rolled_back`, `escalate` and `insufficient_evidence`. `proceed`/`rollback` are nonterminal; all others stop the run after their persisted decision result is appended. Every executor checks the shared terminal outcome before making an external call, so a second tool emitted in the same model batch after a terminal decision is rejected without side effects.

### 3.3 Environment state

```python
class EpisodePhase(str, Enum):
    CREATED = "created"
    PRECHECKING = "prechecking"
    READY_TO_CHANGE = "ready_to_change"
    CHANGE_SUBMITTED = "change_submitted"
    CHANGE_APPLIED = "change_applied"
    VERIFYING = "verifying"
    ANOMALY_DETECTED = "anomaly_detected"
    ROLLBACK_SUBMITTED = "rollback_submitted"
    ROLLED_BACK = "rolled_back"
    COMPLETED = "completed"
    ROLLBACK_VERIFIED = "rollback_verified"
    BLOCKED_PRECHECK = "blocked_precheck"
    ESCALATED = "escalated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ENVIRONMENT_FAILED = "environment_failed"
    AGENT_BUDGET_EXHAUSTED = "agent_budget_exhausted"

class AutomationJob(BaseModel):
    job_id: str
    action_id: str
    operation: Literal["add", "remove", "business_verify"]
    status: Literal["accepted", "running", "succeeded", "failed"]
    business_return_code: int | None
    submitted_payload: dict[str, str]
```

Only a succeeded job with `business_return_code == 0` may mutate the episode config file. Variables, Region, cluster, business bucket and business timestamp are resolved exactly once during reset and remain immutable for the run.

### 3.4 Evidence and matching

```python
class RawActionEnvelope(BaseModel):
    schema_version: Literal[1]
    run_id: str
    action_id: str
    tool_call_id: str | None
    source: Literal["runtime", "browser", "backend", "terminal", "state", "decision", "task_state"]
    timestamp: datetime
    stage: str
    kind: str
    arguments: dict[str, object]
    status: str
    evidence_refs: list[str]

class EffectiveAction(BaseModel):
    schema_version: Literal[1]
    effective_action_id: str
    run_id: str
    action_id: str
    tool_call_id: str
    tool_name: str
    stage: str
    normalized_arguments: dict[str, object]
    evidence_refs: list[str]
    raw_event_ids: list[str]
    raw_event_hashes: list[str]
```

`PRE_CONFIG` is one composite DAG node with two required variants (`ticket-query-extension-config`, `ticket-query-upstream-ready`). A node may own multiple unique actions; each `EffectiveAction` may belong to at most one node. Fill/select/observe actions remain effective and auditable but only the submit click with its backend receipt matches `ADD_SUBMIT`, `BUSINESS_SUBMIT` or `REMOVE_SUBMIT`.

`stage` in an effective action is never accepted from the model, runtime event, browser argument or page request. The environment assigns it from the persisted EpisodeStore phase and state version at action consumption; normalizer requires that trusted receipt. `ActionLedger` enforces one reservation and one consumption for each `(run_id, action_id)`, while permitting multiple append-only source events to cite that action. Replayed, foreign and stale action IDs are rejected without business mutation.

Required trajectory node counts are locked per scenario:

- `normal`: 15 shared nodes + 9 normal nodes = 24.
- `post_change_anomaly`: 15 shared nodes + 7 anomaly/rollback nodes = 22.

Result evaluator always emits the historical 16 checkpoint IDs. Required denominators are:

- `normal`: 14 required, two rollback checkpoints `not_applicable`.
- `post_change_anomaly`: 11 required; four remaining healthy postchecks and business verification are optional after the causal alarm.

### 3.5 HTTP surface

```text
GET    /healthz
POST   /api/runs
POST   /api/runs/{run_id}/reset
GET    /api/runs/{run_id}/state
GET    /api/runs/{run_id}/audit
POST   /api/runs/{run_id}/runtime-events
POST   /api/runs/{run_id}/action-events
POST   /api/runs/{run_id}/decisions
POST   /api/runs/{run_id}/ticket/config-check
POST   /api/runs/{run_id}/monitor/query
POST   /api/runs/{run_id}/automation/jobs
GET    /api/runs/{run_id}/automation/jobs/{job_id}
POST   /api/runs/{run_id}/terminal
POST   /api/runs/{run_id}/recording/start
GET    /api/runs/{run_id}/recording
POST   /api/runs/{run_id}/recording/stop
POST   /api/runs/{run_id}/finalize
GET    /api/runs/{run_id}/scores
GET    /api/runs/{run_id}/events
GET    /ticket/{run_id}
GET    /monitor/{run_id}
GET    /automation/{run_id}
GET    /observer/{run_id}
```

All mutating page requests carry the browser-generated `action_id`; the page JavaScript obtains it from the current DOM action context set immediately before Playwright dispatches the real event. Agent navigation is allowlisted only to ticket/monitor/automation routes for the locked run; `/observer`, ground-truth files and evaluator APIs are denied to the Agent browser.

### 3.6 Artifact bundle

```text
var/coworker-demo/{run_id}/
  run_manifest.json
  input/{item_change_ticket.json,dataset_manifest.json,scenario.yaml,ground_truth_hashes.json}
  agent/{runtime_events.jsonl,session.json,task_state.json,cli_transcript.log}
  environment/{audit_events.jsonl,state_snapshots.jsonl,evaluator_inputs.json}
  trajectory/{raw_actions.jsonl,effective_trajectory.jsonl,trajectory_match.json}
  scores/{trajectory_score.json,result_score.json,summary.json}
  browser/playwright_trace.zip
  browser/screenshots/
  terminal/{commands.jsonl,stdout,stderr,file_snapshots}/
  video/{demo.mp4,poster.png,extracted_frames,video_manifest.json}
```

`run_manifest.json` is the only artifact index and does not hash itself. Core files must be complete and hash-valid before formal success; missing/hash-drift artifacts set `artifact_failure` rather than silently producing an Agent zero. The service owns manifest updates under a per-run lock. Agent and environment producers register files through the typed artifact API after atomic publication.

The observer shown during recording labels trajectory/result numbers as frozen and displays `video_verification=pending` plus `formal_success=pending`. Only after FFmpeg stops and independent ffprobe/frame/hash verification passes may `scores/summary.json` contain final artifact status and `formal_success=true`. The delivered video is required to show the frozen numeric scores and the honest pending label; it is not claimed to contain the post-recording artifact verdict.

## 4. Task Plan

### Task 0: Freeze Inputs, Toolchain And Baseline

**Files:**
- Modify: `pyproject.toml`
- Create: `uv.lock`
- Create: `apps/case02_openenv/pyproject.toml`
- Create: `apps/case02_openenv/uv.lock`
- Modify: `.gitignore`
- Create: `config/coworker_demo.example.yaml`
- Create: `scripts/coworker_demo/__init__.py`
- Create: `scripts/coworker_demo/verify_dataset_bundle.py`
- Test: `tests/coworker_demo/test_verify_dataset_bundle_stdlib.py`
- Local-only: `.venv/`, `apps/case02_openenv/.venv/`, `config/homemaster.yaml`, `config/coworker_demo.yaml`, `var/tooling/`

- [x] **Step 0.1: Reconfirm source state and hashes before any patch**

Run on `hkust4`:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster-coworker-demo
git status --short --branch
git rev-parse HEAD
sha256sum plan/change-coworker-demo-design.md data/coworker_demo/case_02/test_set/item_change_ticket.json
```

Expected: branch `feature/coworker-demo`, HEAD `c6b8c46...`; intended pre-implementation untracked files are the copied `data/coworker_demo/`, this implementation plan and `plan/change-coworker-demo-review-disposition.md`; spec hash `90daf925e5fadd55c43094e072f8af6adf466d900a0d1a493da836fffa921928`, ticket hash `e76b2d5...f49bb`. Any other path stops execution for ownership inspection.

- [x] **Step 0.2: RED-test and implement the standalone dataset verifier**

The standard-library `unittest` fixture creates a minimal valid manifest/bundle and mutations for missing file, byte/hash mismatch, record-count mismatch, UTF-8 BOM and path escape. It imports only `scripts.coworker_demo.verify_dataset_bundle`, never product `CaseRepository`.

Run before implementation:

```bash
PYTHONPATH=. python3 -m unittest tests.coworker_demo.test_verify_dataset_bundle_stdlib -v
```

Expected RED: `ModuleNotFoundError` for `scripts.coworker_demo.verify_dataset_bundle`. Implement the dependency-free verifier, rerun and require all cases PASS under the available system Python without installing packages.

- [x] **Step 0.3: Import the complete Hawkeye bundle without normalizing bytes**

Copy the bundle byte-for-byte from the Mac orchestrator:

```bash
scp -3 -r \
  HPC2_Outside:/hpc2hdd/home/wyuan140/weilin_workspace/hawkeye/validation_dataset/case_02/. \
  hkust4:/home/haodong2/weilin/red_bird/Homemaster-coworker-demo/data/coworker_demo/case_02/
ssh hkust4 'cd /home/haodong2/weilin/red_bird/Homemaster-coworker-demo && python3 scripts/coworker_demo/verify_dataset_bundle.py data/coworker_demo/case_02'
```

`verify_dataset_bundle.py` is independent of `CaseRepository`: it reads `dataset_manifest.contract.file_sha256`, recomputes every declared file and validates all record counts with `utf-8-sig` JSON decoding.

Expected: every declared source file is present; 16 historical checkpoints; all hash/count checks PASS. Historical logs remain input fixtures only and the run audit directory is empty after reset.

- [x] **Step 0.4: Create isolated Python 3.11 environments from locks**

Add root optional dependency ranges:

```toml
[project.optional-dependencies]
coworker = [
  "fastapi>=0.115,<1.0",
  "httpx>=0.27,<1.0",
  "jinja2>=3.1,<4.0",
  "pillow>=10,<13",
  "playwright>=1.45,<2.0",
  "pydantic>=2.7,<3.0",
  "pyyaml>=6,<7",
  "uvicorn>=0.30,<1.0",
]
dev = ["pytest>=8.2,<10", "ruff>=0.6,<1.0"]

[tool.pytest.ini_options]
pythonpath = ["src", "apps/case02_openenv/src"]

[tool.setuptools.package-data]
homemaster = [
  "prompts/*.md",
  "prompts/*.txt",
  "benchmarking/coworker_demo/skills/*/SKILL.md",
]
```

Add app dependencies:

```toml
[project]
name = "case02-openenv"
requires-python = ">=3.11,<3.13"
dependencies = [
  "fastapi>=0.115,<1.0",
  "httpx>=0.27,<1.0",
  "jinja2>=3.1,<4.0",
  "pillow>=10,<13",
  "pydantic>=2.7,<3.0",
  "pyyaml>=6,<7",
  "uvicorn>=0.30,<1.0",
]

[dependency-groups]
dev = ["pytest>=8.2,<10", "ruff>=0.6,<1.0"]
```

Install uv only under `var/tooling`, then use it to install/find Python 3.11, generate both locks, and sync both venvs. Never run `pip install -U` and never modify system site-packages:

```bash
mkdir -p var/tooling/uv
curl --proto '=https' --tlsv1.2 -fsSLo var/tooling/uv/install.sh https://astral.sh/uv/install.sh
sh -n var/tooling/uv/install.sh
UV_INSTALL_DIR="$PWD/var/tooling/uv" sh var/tooling/uv/install.sh
UV="$PWD/var/tooling/uv/uv"
"$UV" python install 3.11
"$UV" lock
"$UV" sync --extra coworker --extra dev
"$UV" --directory apps/case02_openenv lock
"$UV" --directory apps/case02_openenv sync --all-groups
```

The installer URL/behavior and uv subcommands are UNVERIFIED until this gate. Persist the installer SHA-256 and every command return code; abort on TLS, syntax or executable-location mismatch.

Expected gates:

```bash
var/tooling/uv/uv --version
var/tooling/uv/uv python find 3.11
var/tooling/uv/uv lock --check
var/tooling/uv/uv sync --extra coworker --extra dev --frozen
var/tooling/uv/uv --directory apps/case02_openenv lock --check
var/tooling/uv/uv --directory apps/case02_openenv sync --all-groups --frozen
.venv/bin/python --version
apps/case02_openenv/.venv/bin/python --version
```

Each command exits 0 and both Python versions begin with `3.11`. Record resolved package versions in `var/coworker-demo/project-state/progress.md`.

- [x] **Step 0.5: Copy secret config safely and establish a real baseline**

Copy the existing gitignored `config/homemaster.yaml` from the adjacent worktree without printing it; create real `config/coworker_demo.yaml` from the example using absolute paths for this worktree. Verify both are ignored. Then run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
```

Expected: pre-change repository tests reproduce the prior baseline (`352 passed, 1 skipped`, or a documented count change caused solely by test collection/runtime version); Ruff exits 0. If the count differs, inspect individual node IDs rather than accepting a global count.

### Task 1: Run Linchpin Gates Before Dependent Product Code

**Files:**
- Create: `scripts/coworker_demo/linchpin_browser_vnc.py`
- Create: `scripts/coworker_demo/linchpin_recording.py`
- Create: `scripts/coworker_demo/linchpin_terminal.py`
- Test: `tests/case02_openenv/test_linchpin_helpers.py`
- Artifacts: `var/coworker-demo/linchpin/{browser-vnc,recording,terminal}/`

- [x] **Step 1.1: RED-test deterministic helper parsing**

Tests assert exact parsing of Playwright click receipt, VNC RFB banner, FFmpeg progress, ffprobe JSON and terminal exit files. Run:

```bash
.venv/bin/python -m pytest tests/case02_openenv/test_linchpin_helpers.py -q
```

Expected initial failure: imports under `scripts.coworker_demo` are missing. Add pure parsers, rerun to PASS before invoking external processes.

- [x] **Step 1.2: L1 Browser + DOM + backend + localhost-only VNC gate**

The probe starts a minimal local HTTP service on a configured non-hardcoded port, launches a dedicated localhost-only `Xtigervnc` display at `1920x1080`, starts system Chrome through Playwright with `headless=False`, clicks one unique visible/enabled `data-bid`, reads the changed DOM value, and independently GETs backend state. It persists process return codes, X11 window tree, browser screenshot, HTTP state and VNC endpoint.

Mac Screen Sharing may be connected later through an SSH local forward, but the user explicitly deferred live Mac observation and does not need to intervene for this delivery.

PASS requires, independently: Playwright call success, backend state changed, DOM readback changed, X11 window exists, screenshot nonblank, `Xtigervnc` only listens on loopback and an RFB handshake succeeds. Merely importing Playwright or seeing a Chrome process is FAIL.

- [x] **Step 1.3: L2 FFmpeg x11grab/H.264 gate**

Record the actual L1 display for at least 4 seconds while the page visibly changes. Require first-frame progress and growing fragmented MP4 before declaring recorder ready. Stop normally and independently run ffprobe.

PASS requires FFmpeg exit 0; codec `h264`; width/height `1920/1080`; `pix_fmt=yuv420p`; duration >= 4 seconds; `nb_read_frames > 0`; first/middle/last images each have nonblack pixels and variance in the expected content region. Persist `demo.mp4`, ffprobe JSON, frames and `video_manifest.json`.

- [x] **Step 1.4: L3 tmux + Bash + bubblewrap absolute-path gate**

Create a run-scoped episode root, bind it to `/opt/app`, execute the exact add-state grep in a dedicated tmux session, capture exit `0` and matching stdout, remove the record outside the terminal executor as the simulated successful backend job, then execute the same grep in a new command record and require exit `1`, empty stdout and independently readable target file.

PASS requires both tmux process return paths, two distinct evidence IDs, exact original command strings, independent host-side file assertions and no writes to host `/opt`. No later product task begins until L1/L2/L3 are PASS or a root-cause-backed implementation substitution is documented.

### Task 2: Freeze Dataset Overlay, DAG And Configuration Contracts

**Files:**
- Modify: `data/coworker_demo/case_02/dataset_manifest.json`
- Create: `data/coworker_demo/case_02/scenarios/normal.yaml`
- Create: `data/coworker_demo/case_02/scenarios/post_change_anomaly.yaml`
- Create: `data/coworker_demo/case_02/agent_trajectory_ground_truth.yaml`
- Create: `data/coworker_demo/case_02/agent_trajectory_ground_truth.md`
- Create: `src/homemaster/benchmarking/coworker_demo/ticket_bundle.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_ticket_bundle.py`

- [x] **Step 2.1: Write RED tests for byte/hash/schema/containment rules**

Cover valid source bundle, BOM JSON, one-byte mutation, manifest path escape, ticket symlink escape, missing scenario, scenario hash mismatch, missing DAG, two candidate paths, wrong `sop_type`, wrong input ticket, deterministic repeated resolution and exact scenario token selection.

Run:

```bash
.venv/bin/python -m pytest tests/homemaster/benchmarking/coworker_demo/test_ticket_bundle.py -q
```

Expected initial failure: `CaseRepository` and route bundle types do not exist.

- [x] **Step 2.2: Add immutable scenario overlays**

Both overlays lock:

```yaml
schema_version: 1
variables:
  TenantId: tenanttenanttenant000198
  ItemCode: read
  SpecCode: ext.read.type1
  ExtensionName: read-ext
target:
  region: cn-test-1
  cluster: billing-control-01
  resource_bucket: resource-bucket
  business_timestamp: "2025122716"
precheck:
  upstream_ready: true
postcheck:
  anomaly: null
automation:
  add_result: success
  remove_result: success
  business_verify_result: success
```

`post_change_anomaly.yaml` differs only by `scenario_id` and `postcheck.anomaly: A-9001201-metric-delay`.

- [x] **Step 2.3: Externalize the exact trajectory DAG**

Encode the 15 shared nodes and the 9/7 scenario nodes from the design with explicit tool name, normalized argument predicates, evidence requirements, predecessors, stage, scenario requirement and historical case IDs. `PRE_CONFIG` uses `match_policy: all_variants` with the two required bids. Mark remove in normal, arbitrary URL/shell, cross-run evidence and pre-gate submit as prohibited predicates.

Generate the Markdown snapshot deterministically from YAML and assert the snapshot contains every node ID exactly once. Do not hand-maintain a second semantic source.

- [x] **Step 2.4: Extend manifest and implement `CaseRepository`**

Preserve original source hashes/counts. Add a versioned `coworker_demo` section containing scenario paths/hashes, DAG path/hash and supported stable scenario tokens. Resolve all manifest paths against `case_root`, require containment after `resolve()`, parse JSON with `utf-8-sig`, parse YAML safely, and return an immutable bundle with locked hashes.

Rerun the focused tests; expected PASS. Recompute the complete manifest audit with an independent script that does not import `CaseRepository`.

### Task 3: Implement Episode Store, State Machine, Jobs And Artifacts

**Files:**
- Create: `apps/case02_openenv/src/case02_openenv/{config,models,artifacts,episode_store,automation}.py`
- Test: `tests/case02_openenv/test_episode_store.py`
- Test: `tests/case02_openenv/test_automation.py`
- Test: `tests/case02_openenv/test_artifacts.py`
- Test: `tests/case02_openenv/test_anomaly_trigger.py`

- [x] **Step 3.1: RED-test per-run isolation and target locking**

Create two runs with different IDs. Assert each has its own immutable variables, file root, audit sequence, job namespace and state version. Assert reset yields empty current-run audit evidence and deterministic initial file hash. Mutating the overlay object after reset must not change stored targets.

Expected initial failure: package imports are absent.

- [x] **Step 3.2: Implement atomic artifact and append-only evidence ownership**

Use same-directory temporary files plus `os.replace` for JSON, canonical JSON (`sort_keys=True`, no NaN/Infinity) for hashes, a per-run lock for manifest updates, and append+flush+fsync for JSONL. Each manifest entry contains relative path, SHA-256, producer, schema version and completion status. Reject absolute paths, `..`, missing files and hash drift.

- [x] **Step 3.3: Implement the explicit episode transition table**

The store accepts only named domain events and verifies current phase plus required evidence before transitions. A page label or Agent decision alone cannot set `CHANGE_APPLIED`, `COMPLETED` or `ROLLED_BACK`. Persist every transition with before/after phase, event, return status, evidence IDs and duration.

- [x] **Step 3.4: Implement asynchronous automation jobs**

Submission creates a unique accepted job. Worker transition is accepted -> running -> succeeded/failed. On successful add, write the four-variable JSON record atomically; on successful remove, delete only the locked key and keep the file readable; on business verify, persist expected `itemKey` and `factor` evidence. Rejected/failed jobs leave file and business state byte-identical.

- [x] **Step 3.5: Arm the anomaly only from current-run add verification**

`post_change_anomaly` starts with `causal_anomaly_armed=false`. It becomes true only when a trusted terminal event proves the current run's exact `ADD_GREP` exited 0, contained all four locked values and cites that run's succeeded add job. Persist the causal add job ID and grep evidence ID. Tests assert no alarm before add, after add but before grep, for failed/wrong-target grep, with old/cross-run evidence or after replay; only the exact first accepted grep makes the next post-change alarm return `A-9001201-metric-delay` and `caused_by_current_change=true`.

Run all four focused test files; expected PASS, including per-run, per-job and causal-trigger assertions without `any()`/best aggregation.

### Task 4: Implement Real Pages And Typed HTTP API

**Files:**
- Create: `apps/case02_openenv/src/case02_openenv/{__init__,__main__,api}.py`
- Create: `apps/case02_openenv/src/case02_openenv/public_views.py`
- Create: `apps/case02_openenv/templates/{ticket,monitor,automation,observer}.html`
- Create: `apps/case02_openenv/static/app.css`
- Create: `apps/case02_openenv/static/{ticket,monitor,automation,observer}.js`
- Create: `apps/case02_openenv/openapi.json`
- Test: `tests/case02_openenv/test_api_contract.py`
- Test: `tests/case02_openenv/test_pages.py`
- Test: `tests/case02_openenv/test_sse.py`
- Test: `tests/case02_openenv/test_agent_view_security.py`

- [x] **Step 4.1: RED-test the frozen HTTP surface and failures**

Assert the exact route/method set from section 3.5, OpenAPI request/response models, unknown run 404, run-id mismatch 409, stale page version 409, rejected transition with unchanged state, stable error codes and deployment on both loopback and a configurable bind host.

- [x] **Step 4.2: Build the operations UI**

Use compact unframed page bands, max 6px radii, neutral white/charcoal surfaces, teal healthy state, amber pending and red failure. Avoid gradients, decorative cards and marketing sections. Ticket shows the full SOP and locked variables; monitor has five real query tabs; automation has script/operation/parameter controls and job table; observer is read-only with state, timeline, DAG and scores. All controls have stable dimensions and loading/disabled/error states.

- [x] **Step 4.3: Enforce the DOM contract**

Every actionable element has one unique `data-bid`. Tests parse all templates and assert uniqueness, required bids, visible labels and no observer mutation control. JavaScript includes the current action ID on backend mutation, updates visible receipt/state version and never directly changes business-success state before the backend response.

The page consumes an action ID once and clears it after the DOM handler finishes; a later click cannot inherit an earlier action ID. Tests dispatch two clicks and require distinct backend receipts.

- [x] **Step 4.4: Prove every Agent-visible channel is a public projection**

Create explicit ticket/monitor/automation public view models. Inject sentinel values into hidden scenario ID/fault flag, DAG node/checkpoint, evaluator verdict and observer-only state. Assert zero sentinel occurrence in Agent HTML, linked JavaScript/config payloads, permitted page API responses, browser observations, tool results and all success/error bodies. The runtime app disables `/openapi.json`, `/docs` and `/redoc`; Agent navigation also rejects observer, events, audit, state, scores, evaluator, artifacts and ground-truth routes. Page JavaScript receives only its route-specific public projection.

- [x] **Step 4.5: Implement SSE snapshot + replay**

`/events` first emits a current snapshot then strictly ordered new audit events. Disconnect/reconnect cannot mutate or stop an episode. Tests reconnect from the last event ID and require no gap/duplication.

- [x] **Step 4.6: Generate and diff OpenAPI**

Generate `openapi.json` from the actual app, assert every required route and schema, and fail tests on an unreviewed contract drift. Run focused API/page/SSE tests and the service via Uvicorn; require `/healthz` HTTP 200 and process return code 0 on normal shutdown.

### Task 5: Implement Real Terminal Execution

**Files:**
- Create: `apps/case02_openenv/src/case02_openenv/terminal/__init__.py`
- Create: `apps/case02_openenv/src/case02_openenv/terminal/policy.py`
- Create: `apps/case02_openenv/src/case02_openenv/terminal/executor.py`
- Test: `tests/case02_openenv/test_terminal_policy.py`
- Test: `tests/case02_openenv/test_terminal_executor.py`

- [x] **Step 5.1: RED-test exact command policy**

Use `shlex.split` and require tokens exactly equivalent to:

```text
grep -A 3 "tenanttenanttenant000198:read" /opt/app/service_layer/component/config/extension_item_mapping.json
```

Reject changed tenant/item, another path, added pipe/semicolon/substitution/redirection, different grep context, more than one command and any non-grep executable. Repeated parsing of identical input must return identical normalized tokens.

- [x] **Step 5.2: Start a real command in a dedicated tmux session**

Generate a run-owned wrapper, invoke tmux with bubblewrap binding only the run root to `/opt/app`, record PID/session/start/end/return code, and store stdout/stderr separately. Poll the locked session/command ID; never switch to a latest session. On timeout terminate the session and mark it unusable.

- [x] **Step 5.3: Verify external terminal states**

For add grep require exit 0 and all four locked values. For rollback grep require exit 1, empty stdout and a host-side independent parse proving the file exists and target key does not. Each command receives a distinct evidence ID. Run product executor on `hkust4`, not a mock, and retain the real evidence directory.

### Task 6: Implement Raw/Effective Trajectory, DAG Matching And Dual Scores

**Files:**
- Create: `apps/case02_openenv/src/case02_openenv/evaluation/{__init__,trajectory,matcher,results,scoring}.py`
- Test: `tests/case02_openenv/test_trajectory_normalizer.py`
- Test: `tests/case02_openenv/test_trajectory_matcher.py`
- Test: `tests/case02_openenv/test_result_evaluator.py`
- Test: `tests/case02_openenv/test_scoring.py`

- [x] **Step 6.1: RED-test effective-action evidence gates**

For every tool type, include one positive joined sample and negatives for intent-only call, missing browser dispatch, fill/select readback mismatch, missing backend receipt, unstarted process, absent exit code, wrong wait job, missing decision persistence, unchanged task snapshot, cross-run evidence and bad raw-event hash.

Add mutations where runtime/browser claims a forged pre/post stage, a pre receipt is substituted for post, an action ID is replayed, one action ID is consumed twice or a stale state version is supplied. All must be rejected before matching and must not change state.

- [x] **Step 6.2: Normalize only externally proven actions**

Join runtime result action IDs to browser/backend/terminal/state/decision/task events. Derive stage only from the trusted EpisodeStore receipt and its state version; never copy runtime/browser stage text. Emit explicit rejected records with reason for non-effective intents. Keep raw evidence immutable; each effective action cites all event IDs and hashes.

- [x] **Step 6.3: Mutation-test the DAG matcher**

Positive fixtures match exactly 24 normal nodes and 22 anomaly nodes. Mutations remove each node one at a time, swap each dependency boundary, alter every locked parameter, reuse add grep evidence for rollback, use cross-run evidence, submit pre-gate, remove in normal and call arbitrary URL/shell. Assert the exact affected node or safety violation; never accept aggregate best/any behavior.

- [x] **Step 6.4: Evaluate 16 result checkpoints independently**

Result evaluator reads only external state/audit/job return/terminal/file/decision evidence. Test job-success/file-missing contradiction as `environment_failure`; real click with business failure earns trajectory but fails result; platform access alone fails composite execution; normal rollback checkpoints are N/A; anomaly requires causal alarm, rollback decision, remove success, absent config and new rollback grep.

- [x] **Step 6.5: Write three score files without recomputing evidence**

`trajectory_score.json` reads match output; `result_score.json` reads checkpoint output; `summary.json` combines numeric scores and failure gates. Tests require exact formula, 100/100 for positive fixtures, preserved diagnostic numbers on artifact failure, and `formal_success=false` for any safety/environment/artifact failure.

### Task 7: Implement Display, Recorder And Video Verification

**Files:**
- Create: `apps/case02_openenv/src/case02_openenv/recording/{__init__,display,recorder,verifier}.py`
- Test: `tests/case02_openenv/test_display_layout.py`
- Test: `tests/case02_openenv/test_recorder.py`
- Test: `tests/case02_openenv/test_video_verifier.py`

- [x] **Step 7.1: RED-test process arguments and cleanup**

Assert loopback-only TigerVNC, `1920x1080`, no reused live display, fixed xterm/Agent Chrome/observer geometry, FFmpeg x11grab input matching the allocated display, H.264/libx264, 15 fps, CRF 20, veryfast, yuv420p and no audio. Every partial startup failure must terminate already-started children and persist return codes.

- [x] **Step 7.2: Start the real display and transcript/observer windows**

Display manager allocates and locks one display number, starts `Xtigervnc`, waits for `xdpyinfo`, starts the left xterm tailing this run's transcript/evidence and a separate observer Chrome app window. It returns display/port only after loopback and X11 window checks pass.

- [x] **Step 7.3: Gate provider on FFmpeg first packet**

Write to `video/demo.mp4.part` with explicit `-f mp4` and `-movflags +frag_keyframe+empty_moov+default_base_moof`. Parse FFmpeg progress, require a rendered frame plus increasing file bytes, then return recorder-ready. A startup failure aborts before provider. Mid-run failure sets artifact failure but leaves other evidence intact.

- [x] **Step 7.4: Verify and atomically publish video**

After the frozen trajectory/result numbers and explicit `video_verification=pending` / `formal_success=pending` labels are visible for 5 seconds, stop FFmpeg normally, require exit 0, rename to `demo.mp4`, run ffprobe with counted frames, extract first/middle/last frames, test whole-frame and layout-region nonblack/variance, generate `poster.png`, and write a manifest binding run ID, duration and core artifact hashes. Only then publish the final artifact verdict and formal result outside the captured video. Run the real recorder gate again using product classes.

### Task 8: Implement Coworker Clients, Browser Driver And Eleven Tools

**Files:**
- Create: `src/homemaster/benchmarking/coworker_demo/{config,types,budget,environment_client,browser_driver,browser_tools,terminal_tools,decision_tools,registry,skills,tracing}.py`
- Create: two coworker `SKILL.md` files
- Test: `tests/homemaster/benchmarking/coworker_demo/test_environment_client.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_browser_driver.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_tools.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_registry.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_skills.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_tracing.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_budget.py`

- [x] **Step 8.1: RED-test client return status plus external state**

For every mutating client call require HTTP success and independent state/evidence GET. Test non-2xx, business rejection, stale version, timeout and receipt/state contradiction. Service base URL is configured and may be non-localhost.

- [x] **Step 8.2: Implement headed Playwright driver against the L1 contract**

Launch system Chrome on the recorder display with fixed top-right geometry and a run-specific profile/context. Navigation accepts only the three Agent pages for this run. Observe returns URL/title/visible body SOP text/visible controls/values/tables/errors. Click/fill/select verify unique target, visibility, enabled state, actual event dispatch and readback. Wait locks the supplied job ID and observes its DOM row; it never calls a latest-job API.

Before a backend-mutating DOM event, reserve the action ID in the environment ledger. The page may consume it once. Driver completion records can cite the consumed ID but cannot consume it again. Replay, stale state version and another run's action ID return stable failures and unchanged external state.

- [x] **Step 8.3: Build exactly eleven model-selectable tools**

Registry order is:

```text
task_planner
task_progress_check
skill_view
browser_navigate
browser_observe
browser_click
browser_fill
browser_select
browser_wait
terminal_execute
sop_decide
```

Wrap the three existing tool specs to add action IDs and before/after task/skill evidence without changing their public input schema or default registry implementation. Browser/terminal/decision executors use only `RunContext.deps` protocols. An audit test introspects every declared protocol method against every production implementation.

A run-scoped `CoworkerBudget` stores its monotonic start once and checks terminal outcome, 1200-second wall time, 64 browser actions and 4 terminal actions immediately before every external request. It increments only when the request/process is actually started and refuses N+1. It never recomputes or changes locked run/target/job identifiers. HTTP timeouts, Playwright waits and tmux polls receive `min(configured_timeout, remaining_time)`.

- [x] **Step 8.4: Add two generic skills and secret/answer scans**

`change_execution` teaches precheck -> proceed -> implement -> verify -> rollback/complete discipline. `evidence_discipline` teaches return code, external readback, evidence references and no self-report. Tests require exactly these two names and zero occurrence of locked tenant/item/spec/extension values, scenario IDs, ground-truth node IDs, evaluator verdicts, API keys or credentials. The broader Agent-view sentinel suite in Task 4 covers HTML, JavaScript, browser observation, tool payloads and errors rather than treating skill scans as the security boundary.

- [x] **Step 8.5: Mirror runtime events without losing local evidence**

Local JSONL is written before HTTP mirror. Mirror failure marks environment outcome but cannot delete or truncate local runtime/CLI transcript. Planner/progress wrappers compare canonical before/after snapshots and assign a coworker snapshot version only when real state changes.

### Task 9: Implement Coworker Turn And Minimal Shell Router

**Files:**
- Create: `src/homemaster/benchmarking/coworker_demo/{prompt,turn}.py`
- Create: `src/homemaster/cli/coworker_router.py`
- Modify: `src/homemaster/cli/interactive_shell.py`
- Test: `tests/homemaster/benchmarking/coworker_demo/test_turn.py`
- Test: `tests/homemaster/test_coworker_router.py`
- Modify: `tests/homemaster/test_cli_interactive.py`

- [x] **Step 9.1: RED-test router side-effect boundaries**

Cover plain Chinese/English messages, valid absolute path, quoted path, natural-language single path, missing path, multiple paths, wrong JSON/schema/hash, source-layout mismatch, symlink escape, default normal, exact anomaly token and ambiguous tokens. Spy on config/environment/provider factories and assert zero calls for no-match and invalid intent.

- [x] **Step 9.2: RED-test ordinary shell golden behavior**

For no-match, assert `run_agent_turn(session, utterance, run_id=..., progress=True, console_show_replies=False, agent_state=..., task_state_store=...)` is called once with the same object identities and coworker turn is never called. `/new`, `/compact`, `/status`, `/debug`, `/events`, `/doctor` and `/exit` retain current behavior. Add golden tests for `resume_session_id`, EOF and KeyboardInterrupt snapshot/pause behavior, including object identity after resume. Help gains one ticket sentence and no new slash command.

- [x] **Step 9.3: Compose the child coworker runtime**

Create a unique `coworker-YYYYMMDD-HHMMSS-<suffix>` run ID, child `AgentSession`, independent `TaskStateStore`, 80 iteration/64 browser/4 terminal/1200-second budgets, dedicated event sinks, registry, skill registry and stop condition. Clone observability settings so child session snapshots live under this run's `agent/` directory. Reuse existing provider transport/config/context/session machinery without modifying `GenericAgentRuntime`, `agent/turn.py`, default home registry or ALFWorld.

Create the shared monotonic deadline before service startup. A `DeadlineAwareTransport` checks remaining time before provider and summary calls, constructs each underlying `LLMClient` with `min(provider_timeout, remaining_time)`, wraps streamed iteration to reject an expired deadline, and records provider completion/timeout status. This closes the provider gap without changing `GenericAgentRuntime`; service health calls, HTTP tools, Playwright and tmux use the same deadline object.

Startup order is fixed: bundle lock -> service process -> create/reset -> display/observer/xterm -> FFmpeg first packet -> Agent Chrome -> runtime. The model task contains only run ID, ticket URL and instruction to autonomously handle the whole ticket. A raw reply without terminal `sop_decide` is `premature_reply`.

- [x] **Step 9.4: Finalize in a cleanup-safe `try/finally`**

Always persist agent/session/task/runtime/raw artifacts. Generate frozen effective/match/result numbers, show them with video/formal status explicitly pending for 5 seconds, stop/verify video, register hashes, then generate the final formal summary outside the recording. Close Chrome, terminal sessions, display and service with recorded return codes. Ctrl-C preserves partial artifacts and classifies cancelled.

- [x] **Step 9.5: Render the coworker result in the existing shell**

Print model summary, trajectory/result/overall, formal success, artifact path and video path, then return to `homemaster>`. Set shell `last_status/run_id/trace_path` from coworker result but do not replace outer home session, agent state or task store.

Run router/turn/interactive tests and the existing `test_agent_turn_cli_adapter.py`; expected all PASS and `EXPECTED_HOME_TOOLS` unchanged.

### Task 10: Deterministic Full-Stack Black-Box Gates From Existing Shell

**Files:**
- Create: `scripts/coworker_demo/scripted_shell_gate.py`
- Create: `scripts/coworker_demo/verify_run_bundle.py`
- Create: `scripts/coworker_demo/preflight.py`
- Test: `tests/case02_openenv/test_scripted_shell_gate.py`
- Test: `tests/case02_openenv/test_independent_bundle_verifier.py`
- Artifacts: `var/coworker-demo/scripted/{normal,post_change_anomaly}/`

- [x] **Step 10.1: Build a local protocol-correct scripted provider**

Serve deterministic provider SSE responses that call the public eleven tools in valid DAG order using values learned from browser observations, not imported evaluator data. Point a temporary ignored HomeMaster config at this provider. Do not add a benchmark CLI or a product scripted mode.

Implement `verify_run_bundle.py` as an orthogonal verifier. An AST boundary test forbids imports from `case02_openenv` and `homemaster.benchmarking.coworker_demo`. The verifier directly reads the machine DAG, runtime/browser/backend/terminal/state/decision/task raw evidence, host-side episode file and process-return records; independently re-derives effective actions, 24/22 node coverage and 14/11 result checkpoints; invokes ffprobe itself; and validates every manifest hash. Mutations that forge product match/score files while leaving raw/external evidence bad must still FAIL.

- [x] **Step 10.2: Drive the actual shell process for normal**

Start `.venv/bin/homemaster shell`, send only the absolute ticket path, wait for completion, send `/exit`, and capture process return code. Run the product-free verifier and independently require all 24 trajectory nodes, 14 result checkpoints, no remove job, config present, all failure gates false, every subprocess return code, every core manifest hash, H.264 video and per-region frame checks.

- [x] **Step 10.3: Drive the actual shell process for anomaly**

Start a fresh shell and send `post_change_anomaly` plus the same ticket path. Run the product-free verifier and independently require all 22 nodes, 11 result checkpoints, causal alarm armed only after the current add grep, rollback decision, remove success, config absent, rollback grep exit 1 with a new evidence ID, all subprocess return codes, all failure gates false and the independent video/run bundle.

- [x] **Step 10.4: Assert per-scenario isolation**

Compare normal and anomaly run IDs, artifact roots, browser profiles, tmux session IDs, display IDs, job IDs and evidence IDs. Every pair must be distinct. Report each scenario separately; an aggregate 2/2 line is informational only and cannot replace the per-scenario assertions.

### Task 11: Real-Model Full Runs And Final Demo Video

**Files:**
- Real artifacts: `var/coworker-demo/{normal_run_id}/` and `var/coworker-demo/{anomaly_run_id}/`
- Evidence index: `var/coworker-demo/final-verification.json`

- [x] **Step 11.1: Run preflight without exposing secrets**

Check config presence/mode, provider public summary/key count, service port, Chrome, display allocation, loopback VNC, tmux/bwrap, FFmpeg encoders, disk space and all input hashes. Require every subprocess return code 0 and no secret values in output.

- [x] **Step 11.2: Run normal with the real configured model**

Use only the existing shell interaction and ticket path. Do not intervene through Mac or call business APIs manually. If it fails, follow root-cause evidence across model/tool/DOM/backend/terminal boundaries, add a RED regression, make one targeted change and rerun a fresh run ID.

PASS requires trajectory 100, result 100, overall 100, formal success true, 24/24 nodes, 14/14 checkpoints, external config present, no rollback, all process return codes, verified video and complete manifest.

- [x] **Step 11.3: Run anomaly with the real configured model**

Use a fresh shell and explicit stable scenario token. PASS requires 22/22 nodes, 11/11 checkpoints, trajectory/result/overall 100, formal success true, no erroneous completion, successful remove, independent file absence and rollback grep evidence, plus verified video and manifest.

- [x] **Step 11.4: Inspect both videos and choose the delivered video**

Independently inspect first/middle/last extracted frames from both videos. Require each video to show frozen trajectory/result numeric scores plus `video_verification=pending` and `formal_success=pending`; verify the final formal-success verdict from the post-recording summary and manifest, not from pixels that predate ffprobe. The real-model normal video is the fixed primary demo and the anomaly video is the secondary rollback proof; neither may be replaced with a scripted/replayed recording. Record both run paths and both video SHA-256 values in `final-verification.json`. Mac Screen Sharing remains optional and is not required for completion.

### Task 12: Documentation, Full Regression And Completion Evidence

**Files:**
- Modify: `README.md`
- Create: `docs/coworker-demo-user-guide.md`
- Create: `docs/architecture/coworker-demo.md`
- Modify: `CHANGELOG.md`
- Conditional serious-bug files: `docs/pitfalls.md`, `CLAUDE.md`

- [x] **Step 12.1: Write user-facing operation docs from verified commands**

Document environment setup, ignored config creation, starting `homemaster shell`, sending the normal/anomaly ticket messages, opening observer/VNC tunnel, artifact layout, score meanings, video location, failure responsibility and exact preflight/verification commands. Every example must use the actual delivered paths and observed output shape.

- [x] **Step 12.2: Write architecture and invariants**

Document shell route, child runtime, HTTP/DOM/terminal boundaries, run ID ownership, evidence join, DAG/result separation, recorder lifecycle, security/secret boundary, deployment variants and why default home/ALFWorld remain unchanged.

- [x] **Step 12.3: Update README and CHANGELOG in the same change**

README lists the visible coworker capability and links both docs. CHANGELOG records what changed, why, impact, exact shell interaction, scoring/artifact/video behavior and external verification. The final commit message will use equivalent wording.

- [x] **Step 12.4: Run complete internal verification**

```bash
.venv/bin/python -m pytest tests/homemaster/benchmarking/coworker_demo tests/case02_openenv tests/homemaster/test_coworker_router.py tests/homemaster/test_cli_interactive.py tests/homemaster/test_agent_turn_cli_adapter.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests scripts apps/case02_openenv/src
.venv/bin/python -m ruff format --check src tests scripts apps/case02_openenv/src
.venv/bin/python -m compileall -q src apps/case02_openenv/src scripts/coworker_demo
git diff --check
git status --short
```

Also run the default home registry audit, ALFWorld focused suite, secret scan, OpenAPI drift test, all protocol implementation audits and two independent `verify_run_bundle.py` invocations. Record exact counts/return codes; do not accept a single aggregate when one scenario fails.

- [x] **Step 12.5: Complete serious-bug discipline when triggered**

If implementation exposed a false-green or real-environment failure meeting the repository rule, write the symptom/root cause/fix/lesson/ref at the top of `docs/pitfalls.md` and add one imperative preventive rule to the matching `CLAUDE.md` section. If none met the threshold, state that explicitly in the project progress log without changing those files.

### Task 13: Unique Final Review, Finding Disposition And Commit

**Files:** all implementation, tests, data, configuration templates and docs above.

- [x] **Step 13.1: Prove the final-review gate is open**

Before spawning the reviewer, require evidence that both real-model scenario runs, both videos, complete artifacts, all tests, external black-box gates and docs are finished. `git status` must show only this feature's intended tracked changes plus ignored run/config artifacts. If any item is pending, do not start the reviewer.

- [x] **Step 13.2: Start exactly one read-only final code reviewer**

Reviewer instructions: inspect the committed spec, locked implementation plan, complete diff, tests, exact external evidence paths and docs; prioritize correctness, bypasses, evidence self-references, per-scenario false positives, secret leakage, default-framework regressions and missing terminal/video gates. Reviewer must not edit, run mutating commands or spawn agents. External symbols remain UNVERIFIED unless the persisted real gate proves return code plus terminal state.

- [x] **Step 13.3: Disposition every finding**

For accepted findings, main agent writes a RED regression or evidence probe, fixes the root cause and runs targeted plus impacted external verification. For rejected findings, record concrete code/evidence rationale. Do not request automatic re-review.

- [x] **Step 13.4: Final commit and clean audit**

Stage only intended tracked files. Ensure CHANGELOG wording and commit message are equivalent, then commit once after final review as required by repository process. Verify:

```bash
git diff --cached --check
git status --short
git show --stat --oneline --decorate HEAD
git diff c6b8c46..HEAD --name-status
```

Final completion requires the feature commit, clean tracked worktree, primary video path/hash, both run bundle paths, exact test counts, per-scenario external verdicts, plan/final reviewer findings and dispositions.

## 5. Spec Coverage Matrix

| Design section | Implementation tasks | Independent proof |
|---|---|---|
| 1-2 decisions/goals/non-goals | 0-13 locked boundaries | default-framework regression, route/tool absence audits |
| 3 two episodes | 2, 3, 6, 10, 11 | separate normal/anomaly shell processes and bundles |
| 4 dataset/overlay/ground truth | 0, 2 | independent source hash/count verifier and DAG snapshot |
| 5 architecture/run ID/artifacts | 3, 4, 8, 9 | per-run isolation and manifest hash verifier |
| 6 four pages/DOM | 4, 8 | real headed clicks plus backend/store and screenshots |
| 7 runtime/tools/effective trajectory/scoring | 6, 8, 9 | mutation suite and exact 24/22 node gates |
| 8 Chrome/VNC/video | 1, 7, 11 | server DOM/X11/RFB evidence, ffprobe and per-region frames; Mac observation optional |
| 9 tmux/Bash/absolute path | 1, 5 | return codes plus host-side file reads |
| 10 episode/job state machines | 3 | explicit transition and rejected-state tests |
| 11 API/deployment | 4 | OpenAPI snapshot, loopback and configurable bind tests |
| 12 evaluator | 6 | 16 per-checkpoint positive/negative/mutation tests |
| 13 observability | 3, 4, 8 | append-only JSONL, SSE replay and local-first mirror tests |
| 14 errors/responsibility | 3-9 | stable error classifications and contradiction tests |
| 15 completion definition | 10-12 | per-scenario black-box gates and complete regression |
| 16 dependencies/UNVERIFIED | 0, 1 | isolated locks and L1/L2/L3 real compositions |
| 17 implementation order | 0-13 in listed order | plan tracker prevents later phase from bypassing gates |
| 18 approved design points | all tasks | final spec-to-artifact audit before completion |

## 6. Plan Self-Review Checklist

- [x] Every one of the 18 design decisions maps to a task and verification gate.
- [x] All original 16 historical labels remain unchanged; interactive overlays add new current-run evidence.
- [x] The plan adds no `benchmark-coworker` command and no business API tool.
- [x] `GenericAgentRuntime`, `agent/turn.py`, default home registry/provider/session/compaction and ALFWorld behavior remain unchanged.
- [x] Ordinary shell no-match has a strict side-effect golden test.
- [x] Every external call requires return status plus independent terminal state.
- [x] Browser, terminal, scorer, video and two scenarios use per-instance assertions.
- [x] Agent cannot access observer, ground truth, scenario fault flags or arbitrary URL/shell.
- [x] Video is captured from the real live display and cannot be replaced by trace replay.
- [x] Root and app environments are locked and isolated; secret configs remain ignored.
- [x] Plan reviewer occurs now and once; final reviewer occurs only after implementation/video/docs and once.
- [x] Final commit happens after final reviewer and uses CHANGELOG-equivalent wording.

## 7. Reviewer Gate

The plan is complete only after the main agent runs the following self-checks and fixes any result inline:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("plan/change-coworker-demo-implementation-plan.md").read_text(encoding="utf-8")
forbidden = ["T" + "BD", "T" + "ODO", "implement " + "later", "fill " + "in", "similar " + "to"]
hits = [word for word in forbidden if word in text]
assert not hits, hits
PY
rg -n '^### Task ' plan/change-coworker-demo-implementation-plan.md
rg -n 'UNVERIFIED|GenericAgentRuntime|run_agent_turn|homemaster shell|trajectory_score|result_score|demo.mp4' plan/change-coworker-demo-implementation-plan.md
git diff --check
```

Expected: forbidden-placeholder scan has zero matches; task list is 0 through 13; all locked boundary terms exist; diff check exits 0. Then and only then start the single plan reviewer.
