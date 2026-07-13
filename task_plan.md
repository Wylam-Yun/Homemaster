# ALFWorld Put Local-Pose Feedback Harness Plan

## Goal

Preserve the delivered V1.7 baseline and complete the user-approved V1.8 Oracle-pose/typed-feedback design as a reviewed, design-only commit. Product implementation remains out of scope until the written design is approved and its Phase 0 runtime gate passes.

## Non-Negotiable Gates

- No V1.8 product implementation before an independent design review, point-by-point disposition, user approval of the revised design, and real-environment verification of every `UNVERIFIED` ALFWorld/THOR runtime symbol.
- Root cause and boundary evidence precede fixes; failing tests precede implementation.
- Completion requires unit tests, interface implementation audit, THOR return-code checks, and per-target black-box terminal-state assertions.
- A design-only commit and GitHub push are authorized; product-code changes are not.

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
| 8. Recover V1.8 context | complete | User decisions, real-run root causes, subagent evidence, canonical remote commit and clean worktree are reconciled. |
| 9. Independent V1.8 design review | complete | Reviewer returned FIX; the separate code-boundary audit confirmed the concrete bypasses without endorsing runtime symbols. |
| 10. Review disposition and self-audit | complete | All findings were accepted and edited; contract wording, code fences, whitespace and secret scans pass. |
| 11. Design documentation commit | complete | Spec, CHANGELOG and live handoff docs agree; secret scan and `git diff --check` pass; design-only commit is pushed. |
| 12. User design approval | in_progress | User reviews the committed V1.8 specification before any implementation planning begins. |

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
| Existing V1.7 local staging was behind remote HEAD and carried the whole prior delivery as uncommitted changes | 1 | Treat `hkust4` commit `0fdfeaa` as canonical and create a fresh isolated V1.8 clone; never upload the stale tree. |
| Initial SSH-backed `git clone` was denied by the local network sandbox | 1 | Re-ran the same scoped clone with explicit approval; the isolated clone completed. |
| Combined remote doc check embedded Markdown backticks in nested shell quoting and ended with an unexpected EOF | 1 | Do not repeat that command form; the local balanced-fence check already passed and the remote spec hash is identical. Run the remote secret scan separately without backticks. |

## Current Next Action

Wait for the user's review and approval of `plan/V1.8/alfworld-oracle-pose-execution-feedback-spec.md`. Do not start implementation planning or product-code changes before that approval, and do not claim the Oracle integration works before Gate A/B.
