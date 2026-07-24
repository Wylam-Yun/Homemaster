# HomeMaster Universal Tool Execution Handoff

## Current State

- Date: 2026-07-24
- Repository: `/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`
- Canonical plan: `plan/V2.0/homemaster-skill-identity-raw-output-remediation-plan.md`
- State: universal-tool implementation, the single final-review remediation, documentation, and all locally
  available final verification are complete
- Worktree: heavily dirty with pre-existing Skills/raw-output/renderer changes; preserve them and do not use them as
  evidence for this mainline

## Locked Mainline

1. One ordinary-name `ToolRegistry -> PermissionChecker -> ToolExecutor` path for every application entry point.
2. No runtime Profile, ToolView, ToolCatalog, ToolExecutionPipeline, or per-run `enabled_tool_ids` filtering.
3. Keep principal capabilities, command/path deny rules, plan mode, confirmation/`tool.auto`, deadlines,
   cancellation, resource leases, and terminal-state verification.
4. Keep extension `enabled_tool_ids` only as a load-time approval list for third-party exports. It is not a
   request/runtime filter and removing it would broaden extension authority.
5. Do not continue Skills, raw-output, Rich renderer, event, or generic naming cleanup in this execution.

## Completed

- Deleted `src/homemaster/tools/catalog.py` and `src/homemaster/tools/pipeline.py`; removed production references to
  `ToolCatalog`, `ToolView`, `ToolExecutionPipeline`, `HomePermissionPolicy`, and the `tool_view` context property.
- All built-in, Home, ALFWorld, Coworker, task, MCP, Feishu, and approved extension tools register atomically into
  the same ordinary-name Registry. Hidden `homemaster.<name>.v1` values are metadata only.
- Environment adapters only bind Backends. ALFWorld owns the canonical `robot_go_to({"target": ...})` implementation.
- Ported required service/task runtime into HomeMaster modules. Background tasks use independent process groups;
  stop/close kill the shell and its real child workload.
- Preserved permission, confirmation, deadline, cancellation, resource-key serialization, and device lease gates.
- Resolved all three final-review findings: undeclared cross-source ordinary-name collisions now fail closed;
  deadlines cover lease acquisition/execution/release; and mutating timeout, exception, or cancellation after backend
  start returns observable `outcome_unknown` instead of a false terminal failure.
- Cancellation of a mutating synchronous worker returns promptly, records `backend_attempted=true` in the run-local
  `tool.call_failed` event, does not publish that stale-generation event to the shared bus, and does not commit the
  cancelled run's task/session state. A black-box test independently observes the worker's later file mutation.
- Historical V1.9 artifacts are checked for snapshot integrity instead of being regenerated from V2 runtime APIs.

## Verification Evidence

- Final post-review `tests/homemaster`: `1134 passed, 1 skipped` (two expected legacy-ID migration warnings).
- Full repository: `1555 passed, 1 skipped, 3 failed`; the three failures are the pre-existing external Coworker
  gates (two missing `/usr/bin/google-chrome`, one tmux/bubblewrap terminal-state failure).
- Focused post-removal application/tool suite: `64 passed`.
- Post-review focused gates: application `83 passed`; generic AgentRuntime `25 passed`; universal Registry/Executor
  `26 passed` (two expected legacy-ID migration warnings). Focused Ruff and `git diff --check`: PASS.
- Ruff, compileall, `git diff --check`, and production legacy-symbol audit: PASS.
- Clean wheel: 211 entries; no `homemaster/tools/catalog.py`, `homemaster/tools/pipeline.py`, or `openharness/` package.
- Isolated wheel install outside the checkout: CLI help PASS; universal Registry contains 58 unique ordinary names;
  removed modules and `openharness` are not importable.
- Real Bash canary through the installed Registry/Executor: `status=success`, `returncode=0`; independent file read
  returned `universal-registry-canary-20260724-final-review` exactly.
- Task stop black box: both the shell PID and independently recorded `sleep` child PID disappear.

## Remaining

1. Real ALFWorld terminal-state verification is `UNVERIFIED`: the current environment has no `alfworld` module or
   dataset. Do not claim engine-level success from fake Backend tests.
2. Do not commit until CHANGELOG and the eventual commit message describe the same universal-tool change.
