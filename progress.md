# Current Coworker Demo Progress

## 2026-07-20

- Presentation v2、五区观察面板、Planner/公开回复/工具/结果/异常恢复展示已实现并提交。
- Controlled normal/anomaly failure gates 已通过，12 张 incident 帧已逐张人工核对。
- 全量内部验证已通过：791 passed、1 skipped；preflight 确认 Mimo `mimo-v2.5` 配置可用。
- 第一条真实 normal attempt `coworker-20260720-022516-8c773877` 已由 Mimo 完成 24/24、14/14 和 100 分，并产出通过视频验证的 362 秒 MP4。
- 该 attempt 因 20 秒录屏停止客户端超时及清理路径重复停止被标记失败；stdout 证明第一次 stop 为 200，第二次为 500，stderr 为已关闭 FFmpeg stdin 的 `BrokenPipeError`。
- 已用 RED 测试锁定专用停止超时与会话幂等约束；实现后聚焦测试 21 passed，lint/format/diff check 通过。
- 真实服务黑盒门 `recording-stop-gate-20260720-024549` 录制外部 MP4 后连续停止两次，两个 HTTP 返回码均为 200、FFmpeg 返回码 0、响应和磁盘视频哈希一致。
- 接受 normal `coworker-20260720-024949-b7004546`：Mimo 42 次调用，24/24、14/14、100 分、终态 complete、独立 verifier PASS，7 张关键帧人工通过。
- 接受 anomaly `coworker-20260720-025635-a46d87ca`：Mimo 44 次调用，22/22、11/11、100 分、终态 rolled_back、add/remove/grep/config 外部终态与独立 verifier PASS，12 张命名帧人工通过。
- 验收报告已写入 `docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md`，保留并拒绝生命周期失败的首个真实 normal attempt。
- 下一步：运行全量测试、两条 bundle 最终复验、文档/secret/interface 审计；全部完成后启动唯一一次最终代码评审。

# Historical ALFWorld Harness Progress

## 2026-07-12

- Read the user-provided task attachment.
- Loaded the planning, design-brainstorming, and implementation-planning workflows.
- Confirmed SSH access to `hkust4` and inspected branch, recent commits, and dirty worktree.
- Confirmed that no previous root planning files existed.
- Created this planning state; no product code has been modified.
- Read both V1.7 specifications and the issue record in full.
- Mapped the approved architectural invariants and all explicit implementation gates.
- Started three parallel read-only investigations: independent design review, failed-episode root cause, and runtime/symbol map.
- Inspected the Episode summary, trajectory, model/runtime JSONL schemas, implementation entry points, and the latest relevant commit scope.
- Logged the missing `jq` utility and selected a dependency-free structured parsing fallback.
- Read the main navigation, target resolution, candidate generation, manipulation, visual projection, and inspect-view implementations.
- Confirmed concrete code-level sources for navigation false positives, double parsing, invalid-action misaccounting, and feedback collapse.
- Mapped the THOR put call, result/summary dataclasses, Runner stop condition, registry, and documentation/test surfaces.
- Confirmed that prior per-Shelf claims lack a persisted raw evidence artifact in the repository and must be rerun/persisted.
- Began live-runtime discovery; ruled out the default Python and recorded the existing Xvfb displays without assuming they are usable.
- Located and inspected the dedicated `hm_alfworld` Conda environment and verified access to X display `:99`.
- Recorded exact package versions and the pre-existing `pip check` failure without changing dependencies.
- Received and incorporated the independent read-only root-cause investigation.
- Verified the model-visible manipulation JSON and identical frame hashes directly.
- Located all three candidate trial directories for the target task; exact trial identification remains pending.
- Identified the exact historical trial as `trial_T20190908_122154_042763` from the Pencil pose encoded in the trace objectId.
- Received the first independent design review with verdict `FIX`.
- Dispositioned every review item and revised both V1.7 specs without changing product code.
- Reduced the design alternatives to four and retained the already approved same-target bounded local-pose approach.
- Built and linted a direct-ALFWorld runtime contract probe under ignored `var/alfworld-evidence`; it imports no HomeMaster product logic.
- Ran three controlled-Xvfb contract probes with exit code 0 and persisted JSONL, raw events, frame hashes, and PNGs.
- Verified reachable/Teleport/frame/Pickup/Put/open contracts and recorded the rejected interactive-pose action.
- Preserved toggle as UNVERIFIED after three real failures; the failure detail proved that raw THOR strings can leak objectIds and coordinates.
- Built and linted the all-six-Shelf characterization script; pure-function tests prove its candidate ordering is independent of input list order. Independent review is in progress before execution.
- Completed the independent pre-execution characterization review with verdict `FIX` and dispositioned all eight original blockers plus the two-stage-budget supplement; every finding was accepted.
- Split implementation ownership to avoid concurrent writes: this batch modifies only `independent_alfworld_probe.py`, while a separate state-machine batch owns `alfworld_pose_characterization.py`.
- Updated the probe evidence writer to reject nonempty output directories and artifact overwrites, reject NaN/Infinity canonical JSON, and record separate canonical-content and persisted-file hashes.
- Added a unified tri-state external read covering return status, inventory, exact Pencil/all Shelves, parent/child state, actual pose, detections, frame identity, reachable fields, and goal status without collapsing missing data into empty state.
- Made every returned `thor.step` persist a raw event; exceptions and missing events now preserve explicit call/read status and make the probe exit nonzero unless the exception was declared as the expected result of a negative contract probe.
- Ran the project-environment Ruff and bytecode checks successfully, then passed pure negative tests for NaN rejection, dual-hash recomputation, nonempty-directory rejection, returned-event raw evidence, and exception-to-missing-state handling. THOR was not started.
- Received the helper negative review and accepted all five blockers: metadata-safe projections, strict bbox/frame observations, stale-action rejection, real terminal-state gates, and nonzero Teleport proof.
- Added the pre-existing ALFWorld test/Ruff baselines to findings so later product work cannot misattribute one Runner failure or 37 lint findings to this evidence helper.
- Recorded and corrected the failed remote-test invocation pattern: remote scripts run through SSH stdin rather than using a remote path as a local `workdir`.
- Re-ran the corrected six-Shelf characterization in a fresh `shelf-characterization-v3` directory; all six exploration instances passed exact navigation and put terminal gates.
- Derived the fixed production budgets as navigation `65/66/34804 ms` and local put `9/17/5669 ms`, then passed Shelf 3/4/6 production independently under those limits.
- Integrated the common put executor into the Adapter with strict exact-instance resolution, PoseContext validation, current-pose-first retry, exhaustive terminal classification, and correct `isPickedUp` reads.
- Added model-visible structured put feedback, deterministic detail redaction, terminal dispatch gating, ordinary Episode outcome/coverage, and long-horizon taskset outcome propagation with remaining subtasks marked not-run.
- Removed `robot_inspect_view` from the public registry and prompt.
- Corrected backend action accounting so `GetReachablePositions` counts as a THOR action and Adapter results propagate navigation/put counts into `EpisodeOutcome` and trace data.
- Fixed the production budgets in product defaults and wrote the final evidence back into both V1.7 specifications and the issue record.
- Ran three independent product-Harness Xvfb processes for Shelf 3/4/6; all exited 0 and passed THOR return, inventory, `isPickedUp`, exact parent/child, goal `1/1`, and final-image pixel gates.
- Added CLI coverage/formal-score output with RED-first tests; CLI tests pass.
- Recovered one shared-worktree synchronization error that temporarily overwrote newer taskset type fields, re-synced the authoritative remote file, and reran all affected tests.
- Added README guidance, an ALFWorld user guide, an architecture document, CHANGELOG entries, and launched the required independent serious-bug postmortem for `docs/pitfalls.md` and `CLAUDE.md`.
- Added per-candidate navigation/put JSONL trace events, raw event refs/hashes, budget usage and terminal events while recursively excluding internal execution fields from model-visible results.
- Tightened held-state proof to require `isPickedUp=true` and put success to require `isPickedUp=false`; missing values now terminate uncertain. Added an `ExecutionBackend` implementation audit.
- Removed the residual tools-side `robot_inspect_view` factory, executor and tests; only historical trace fixtures and absence assertions remain.
- Re-ran the final product black-box gate after all trace/state changes in `product-harness-v2`; Shelf 3/4/6 each exited 0 and independently passed return/state/goal/frame assertions.
- Cleaned an ignored Python 3.10 `__pycache__` directory that alone violated an import-boundary test, and renamed a blocked legacy cleanup term without changing grounding behavior.
- Final repository verification: `352 passed, 1 skipped`; focused ALFWorld benchmark `121 passed`; Ruff check, Ruff format check, compileall, cleanup guard and `git diff --check` pass.

## 2026-07-13

- Recovered the interrupted V1.8 continuation and checked both existing pose-investigation subagents; both were complete and had made no file changes.
- Confirmed the canonical `hkust4` worktree is clean at `0fdfeaa` and that the older local staging tree is stale and unsafe for synchronization.
- Created a fresh isolated clone from the canonical remote commit.
- Added the V1.8 Oracle-pose and typed-feedback design draft without modifying product code.
- Marked all direct HomeMaster uses of observed ALFWorld runtime internals `UNVERIFIED` pending the Phase 0 true-environment gate.
- Began the required independent design-review phase.
- Completed the independent V1.8 design review with verdict `FIX` and a separate read-only code-boundary audit.
- Accepted and dispositioned every review item; no finding was rejected.
- Revised the design to remove hidden-object/legacy navigation bypasses, make typed feedback the sole Adapter-to-Dispatcher authority, split runtime feasibility from product black-box verification, define context rebase/consume/invalid transitions, cover all public actions, and separate responsibility metrics.
- Started the final design-document self-audit; product code remains untouched.
- Completed the revised-spec self-audit: Markdown fences are balanced, `git diff --check` passes, and the changed documents contain no API token/credential pattern.
- Added a Chinese CHANGELOG entry that states the problem, selected design, impact and explicit design-only/UNVERIFIED boundary.
- Synced the five-file design scope to `hkust4`; local and remote SHA-256 values match for every file, and the remote worktree contains no other change.
- Logged and isolated one non-mutating remote check quoting error; the equivalent local fence check already passed and the remote secret check is rerun separately.
- Completed remote staged-scope verification and pushed the five-file, design-only V1.8 commit on `visualagentloop`; no product source, tests or runtime configuration changed.
- Handoff state is now user design review. Implementation planning and product changes remain blocked on explicit approval, followed by Gate A.
