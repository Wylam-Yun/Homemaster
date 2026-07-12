# ALFWorld Put Local-Pose Feedback Harness Plan

## Goal

Finish and verify the ALFWorld Harness `put` repair described by the V1.7 specifications while preserving the existing dirty worktree and public tool contract.

## Non-Negotiable Gates

- No implementation before an independent design review, point-by-point disposition, real-environment verification of every `UNVERIFIED` THOR symbol, per-instance pose experiments, and a second independent review of the revised design.
- Root cause and boundary evidence precede fixes; failing tests precede implementation.
- Completion requires unit tests, interface implementation audit, THOR return-code checks, and per-target black-box terminal-state assertions.
- No commit or push unless the user separately requests it.

## Phases

| Phase | Status | Exit Criterion |
|---|---|---|
| 1. Recover context | complete | Specs, issue record, failed episode, code paths, dirty worktree, and prior evidence are mapped. |
| 2. Design alternatives and first review | complete | 3-4 candidates and recommendation are reviewed independently; every comment is dispositioned. |
| 3. Real-environment verification | complete | All MVP linchpins and per-instance pose budgets have authoritative evidence. |
| 4. Revised design review | complete | Review findings were dispositioned; the user approved implementation without another review round. |
| 5. TDD implementation | complete | Navigation, put, feedback, outcome, taskset and coverage RED tests now pass. |
| 6. Verification | complete | Unit, interface-audit, six-Shelf characterization, and Shelf 3/4/6 product black-box gates pass. |
| 7. Documentation and final regression | complete | Architecture, guide, README, CHANGELOG, pitfalls, handoff state and final test run agree. |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Remote planning files were absent | 1 | Initialize them before further investigation. |
| `jq` is not installed on `hkust4` | 1 | Use Python's standard `json` parser for JSONL evidence; do not install or change project dependencies. |
| Multiline `python3 -c` arrived over SSH with literal `\\n` characters | 1 | Switched to newline-free one-line parsers; do not repeat the multiline invocation form. |
| Broad `/tmp` `find` predicate emitted unrelated permission noise | 1 | Stop scanning `/tmp`; use the already identified Xvfb auth paths directly. |
| Project environment `pip check` reports `textworld 1.7.0 is not supported on this platform` | 1 | Do not change dependencies; first determine whether current ALFWorld runtime/experiments execute and record the check as an environment caveat. |
| Assumed the target task directory directly contained `traj_data.json` | 1 | It contains three trial subdirectories; identify the exact trial from scene/object-pose evidence before loading one. |
| Invalid-type `gridSize` was silently ignored by `GetReachablePositions` | 1 | Record this as the real contract; do not use it as a failure probe or assume request validation. Exceptions/missing events remain terminal-uncertain paths. |
| First open probe selected hidden `Blinds` and returned failure with empty detail | 1 | Re-ran from an independent reset with a visible closed Drawer; return code and `isOpen` transition passed. |
| Toggle probes against hidden CellPhone, DeskLamp, and LightSwitch returned `object not found` | 1 | Keep toggle terminal-state contract UNVERIFIED; a later focused probe must first satisfy an exact-target navigation/observation gate. |
| Local staging `types.py` overwrote newer taskset fields during remote sync | 1 | Restored the taskset patch from its owner, preserved `backend_action_count`, re-synced remote back to staging, and reran outcome/runner/CLI plus the full benchmark suite. |

## Current Next Action

All put-MVP implementation and verification gates are complete. No code, documentation, or verification blockers remain. Successful `toggle/use` remains explicitly outside the put MVP and requires a separate real-environment contract before future implementation.
