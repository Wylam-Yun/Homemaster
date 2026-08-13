# Remove Coworker Routing From the Interactive Shell

## Goal

The general HomeMaster interactive shell must send every non-command utterance to the normal agent loop. Text containing `.json`, `ticket`, or `变更单` must never be intercepted by Coworker Demo routing.

## Scope

- Remove the Coworker router import and routing branch from `interactive_shell.py`.
- Remove the shell-only `_run_coworker` bridge and its now-unused imports.
- Keep the isolated Coworker benchmark implementation and its direct benchmark tests unchanged.
- Add interactive-shell regression coverage proving relative JSON paths, absolute JSON paths, and explicit ticket wording reach `Application.run` unchanged.

## Verification

Run the focused interactive-shell and Coworker benchmark/router tests, followed by the broader HomeMaster test suite if practical. No existing automatic-memory changes are included in this work.
