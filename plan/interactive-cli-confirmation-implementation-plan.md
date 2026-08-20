# Interactive CLI Confirmation Implementation Plan

## Goal

Add opt-in local confirmation to the interactive CLI while keeping the default and every
non-interactive entry fully automatic. Reuse the existing `PermissionChecker` decision and
`ToolExecutor.confirmation_handler` boundary; do not introduce task authorization, remote approval,
risk levels, or persistent approval receipts.

## Behavior

- `homemaster` and `homemaster shell` accept `--permission-mode full_auto|confirm|plan`.
- The default is `full_auto`; only an explicitly selected `confirm` mode prompts for approval.
- `confirm` maps to the existing `PermissionMode.DEFAULT`, removes `tool.auto` from the local
  interactive subject, and preserves configured `allowed_tools` as pre-approved tools.
- Read-only calls remain automatic. `plan` rejects mutations without prompting.
- A confirmation displays the validated arguments and working directory. Only `y` or `yes` allows
  execution; every other input, EOF, or input failure denies before resource acquisition/backend use.
- Confirmation prompts are serialized and requested/completed decisions enter the runtime JSONL trace.

## Verification

- Exercise CLI option routing and reject the option on non-interactive entry points.
- Verify approval, denial, EOF, exception, pre-approved tools, read-only tools, plan mode, and
  concurrent prompt serialization.
- Use the real application/executor path with a temporary file to prove approved external mutation
  and zero external mutation/backend calls after denial.
- Run focused CLI/permission/executor/factory tests, Ruff, compileall, and `git diff --check`.

## Worktree Protection

Preserve all pre-existing portable-memory work. Do not edit its scripts, tests, plan, or example
configuration. Merge documentation additions into the live README/CHANGELOG without reverting their
current uncommitted content.
