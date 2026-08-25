# Ops Monitor Agent Semantic Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch subagents; the repository instructions require single-agent execution.

**Goal:** Resume the interrupted Ops Monitor Agent work, expose both evidence-row actions through HomeMaster's semantic browser contract, and complete one fresh end-to-end run with independently verified external state and a deterministic private evidence bundle.

**Architecture:** Keep HomeMaster's generic `[data-browser-action]` discovery boundary and make the Ant Design demo expose evidence actions as real accessible buttons with unique names. Strengthen the change-ticket skill so missing semantic controls fail closed instead of falling back to terminal/JavaScript browser automation. Preserve the failed run as negative evidence, then accept only a fresh run whose process return code, browser trajectory, fixture transition, asset readback, and evidence records all pass per target.

**Tech Stack:** Python 3.11, Playwright, pytest, React 18, Ant Design 5, TypeScript, Vitest, Biome, Umi dev server.

---

## File map

- Create `ant-design-pro/src/pages/ops/components/SemanticEvidenceAction.tsx`: one accessible, inspectable evidence action used by both consoles.
- Create `ant-design-pro/src/pages/ops/components/SemanticEvidenceAction.test.tsx`: locks the button role, unique accessible name, and `data-browser-action` marker.
- Modify `ant-design-pro/src/pages/ops/alarm-query/index.tsx`: replace the non-semantic row anchor with the shared action.
- Modify `ant-design-pro/src/pages/ops/asset-check/index.tsx`: use the same action for the post-change evidence row.
- Modify `Homemaster/src/homemaster/skills/builtin/change-ticket-executor/SKILL.md`: forbid terminal, raw JavaScript, CDP, alternate Playwright sessions, and coordinate fallbacks for page interaction.
- Modify `Homemaster/tests/homemaster/skills/test_change_ticket_executor_evidence.py`: lock the fail-closed instruction.
- Modify `Homemaster/docs/pitfalls.md` and `Homemaster/CLAUDE.md`: record the failed live-run root cause and the positive semantic-control rule.
- Modify `Homemaster/plan/V3.0/2026-08-24-ops-monitor-agent-browser-handoff.md`, `Homemaster/docs/browser-gateway-user-guide.md`, `Homemaster/README.md`, and `Homemaster/CHANGELOG.md`: synchronize current capability, usage, evidence, and run disposition.

### Task 1: Preserve and classify the interrupted live run

- [ ] **Step 1: Prove cancellation and cleanup terminal state**

Run:

```bash
ssh hkust4 'test ! -e /proc/2422820 && \
  test -z "$(ps -u "$USER" -o command= | rg "ops-monitor-real-20260824-02|playwright.*run-driver|chrome-headless.*Homemaster" || true)" && \
  grep -Fxq "agent_version=0.9.0" /home/haodong2/weilin/red_bird/ant-design-pro/mock/fixtures/monitor-agent/agent.conf && \
  test ! -s /tmp/ops-monitor-real-20260824-02.stderr.log'
```

Expected: exit code 0. The run is failed negative evidence: it was cancelled after trying to bypass the semantic browser contract; it did not mutate the fixture.

- [ ] **Step 2: Preserve the terminal probe with the failed run**

Run:

```bash
ssh hkust4 'if test -f /tmp/find_evidence_button.js; then \
  mv /tmp/find_evidence_button.js \
    /tmp/homemaster/runs/ops-monitor-real-20260824-02/failed_terminal_browser_probe.js; \
fi'
```

Expected: the temporary probe is no longer a loose `/tmp` file and remains auditable beside the failed run.

### Task 2: Expose row evidence actions semantically in Ant Design

- [ ] **Step 1: Write the failing component test**

Create `src/pages/ops/components/SemanticEvidenceAction.test.tsx`:

```tsx
import { isValidElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { semanticEvidenceAction } from './SemanticEvidenceAction';

describe('semanticEvidenceAction', () => {
  it('exposes a unique semantic browser action without coordinates', () => {
    const rendered = semanticEvidenceAction('monitor_agent_config_drift', vi.fn());
    expect(isValidElement<Record<string, unknown>>(rendered)).toBe(true);
    if (!isValidElement<Record<string, unknown>>(rendered)) return;
    expect(rendered.props['aria-label']).toBe('取证 monitor_agent_config_drift');
    expect(rendered.props['data-browser-action']).toBe('evidence-open');
    expect(rendered.props.type).toBe('link');
  });
});
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
cd /home/haodong2/weilin/red_bird/ant-design-pro
npx vitest run src/pages/ops/components/SemanticEvidenceAction.test.tsx
```

Expected: FAIL because `SemanticEvidenceAction` does not exist.

- [ ] **Step 3: Add the minimal shared semantic action**

Create `src/pages/ops/components/SemanticEvidenceAction.tsx`:

```tsx
import { Button } from 'antd';
import type { ReactElement } from 'react';

export const semanticEvidenceAction = (
  subject: string,
  onClick: () => void,
): ReactElement => (
  <Button
    type="link"
    aria-label={`取证 ${subject}`}
    data-browser-action="evidence-open"
    onClick={onClick}
  >
    取证
  </Button>
);
```

- [ ] **Step 4: Replace both non-semantic anchors**

In `alarm-query/index.tsx`, import `semanticEvidenceAction` and use:

```tsx
render: (_: unknown, row: AlarmRow) =>
  semanticEvidenceAction(row.alarm_name, () => setSelectedRow(row)),
```

In `asset-check/index.tsx`, import the same helper and use:

```tsx
render: (_, row) =>
  semanticEvidenceAction(row.hostname, () => setSelectedLog(row.evidence_log)),
```

- [ ] **Step 5: Run focused tests and formatting**

Run:

```bash
cd /home/haodong2/weilin/red_bird/ant-design-pro
npx vitest run \
  src/pages/ops/components/SemanticEvidenceAction.test.tsx \
  src/pages/ops/alarm-query/semanticPicker.test.tsx \
  src/pages/ops/query-consoles.test.ts
npx biome check \
  src/pages/ops/components/SemanticEvidenceAction.tsx \
  src/pages/ops/components/SemanticEvidenceAction.test.tsx \
  src/pages/ops/alarm-query/index.tsx \
  src/pages/ops/asset-check/index.tsx
```

Expected: all tests and Biome checks pass.

### Task 3: Make the execution skill fail closed on missing semantic controls

- [ ] **Step 1: Add a failing instruction regression**

Append to `tests/homemaster/skills/test_change_ticket_executor_evidence.py`:

```python
def test_missing_semantic_control_stops_without_terminal_browser_fallback() -> None:
    skill = Path(
        "src/homemaster/skills/builtin/change-ticket-executor/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Stop the run when a required page control is not exposed" in skill
    for forbidden_fallback in ("terminal", "raw JavaScript", "CDP", "alternate Playwright"):
        assert forbidden_fallback in skill
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.venv/bin/pytest -q \
  tests/homemaster/skills/test_change_ticket_executor_evidence.py::test_missing_semantic_control_stops_without_terminal_browser_fallback
```

Expected: FAIL because the fail-closed sentence is absent.

- [ ] **Step 3: Add the exact fail-closed rule**

Add under `Execute And Verify` in the skill:

```markdown
Stop the run when a required page control is not exposed by `browser_inspect`,
`browser_select`, or `browser_click`. Never use `terminal`, raw JavaScript, CDP,
an alternate Playwright/Puppeteer session, or coordinates to inspect or mutate the
page. Report the missing semantic control and preserve the current evidence.
```

- [ ] **Step 4: Run the skill tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/homemaster/skills/test_change_ticket_executor_evidence.py
```

Expected: all tests pass.

### Task 4: Prove the real Ant page exposes both evidence actions

- [ ] **Step 1: Run the focused HomeMaster browser suite**

Run:

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.venv/bin/pytest -q \
  tests/homemaster/browser/test_playwright_session.py \
  tests/homemaster/browser/test_application.py \
  tests/homemaster/browser/test_trajectory_bundle.py \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/skills/test_change_ticket_executor_evidence.py
.venv/bin/ruff check \
  src/homemaster/browser \
  src/homemaster/cli/app.py \
  src/homemaster/cli/run_command.py \
  tests/homemaster/browser \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/skills/test_change_ticket_executor_evidence.py
```

Expected: pytest and Ruff exit 0.

- [ ] **Step 2: Run a real-page semantic probe**

Use `PlaywrightBrowserSession` with the configured `BrowserPolicy` to navigate to the live Ant page, submit the known safe alarm query, then assert separately:

```python
alarm = await session.inspect({"name": "取证 monitor_agent_config_drift"})
assert alarm.total_matches == 1
assert alarm.elements[0].control_type == "button"
await session.click(alarm.snapshot_id, alarm.elements[0].element_id)
drawer = await session.inspect({"name": "确认取证"})
assert drawer.total_matches == 1
```

Repeat on `/ops/asset-check` after the fixture is `1.0.0`, asserting exactly one `取证 fixture-node-01` action. The probe must use only semantic inspect/click/select operations.

### Task 5: Run one fresh end-to-end acceptance with durable return-code evidence

- [ ] **Step 1: Lock a new unique run and preconditions**

Use label `ops-monitor-real-20260824-03`. Before launch, assert the run directory, stdout, stderr, and rc paths do not exist; assert the Ant URL returns HTTP 200 and the fixture is exactly `0.9.0`.

- [ ] **Step 2: Launch once and persist the true process return code**

Run the one-shot HomeMaster command with stdout/stderr separated. In the same shell, write the child return code atomically to `/tmp/ops-monitor-real-20260824-03.rc`; never reuse or clear an existing run label.

- [ ] **Step 3: Enforce live safety while monitoring**

Stop immediately if runtime events show a failed precheck/verification, a non-semantic page workaround, a coordinate action, or an alternate browser session. Do not edit source or restart the Ant server during the run.

- [ ] **Step 4: Verify every external target independently**

After process exit, assert per target:

1. rc file is exactly `0` and stderr is empty;
2. fixture line changed from the recorded `0.9.0` precondition to exact `agent_version=1.0.0`;
3. an independent HTTP asset query returns `fixture-node-01 / running / 1.0.0 / fixture-region-01`;
4. the browser action log's final page reads contain distinct `WSO-` evidence IDs for precheck and postcheck with the exact ticket, SOP step, and field;
5. runtime events contain no `tool.call_failed`, no coordinate-like tool, and no terminal command that operates a browser;
6. HomeMaster, Playwright, Chromium, and run-specific processes all return to zero.

- [ ] **Step 5: Materialize and verify the private deterministic bundle**

Generate exact `terminal_verification.json` and `final_state.json` from the independent checks, then run `scripts/ops_monitor_agent/materialize_trajectory.py`. Run `verify_trajectory_bundle()` in a fresh process and separately hash/read every artifact.

### Task 6: Postmortem, documentation, and final repository gates

- [ ] **Step 1: Record the non-obvious failure**

Add the failed-run symptom, root cause, fix, and run reference at the top of `docs/pitfalls.md`. Add a positive rule to `CLAUDE.md`: clickable custom controls must expose a semantic role, unique accessible name, and stable browser marker; a missing semantic control must stop rather than trigger a second browser channel.

- [ ] **Step 2: Synchronize user-facing documentation**

Update the V3.0 handoff status, Browser Gateway user guide, README capability list/example, and CHANGELOG with the one-shot browser route, semantic popup/evidence controls, private trajectory artifacts, failed run `-02`, and accepted run `-03` evidence.

- [ ] **Step 3: Run final gates**

Run the focused suites above, `git diff --check` in both repositories, Ant TypeScript checks with inherited failures distinguished, and a scoped dirty-worktree audit. Do not stage or alter unrelated HomeMaster dead-code-audit deletions, story assets, real ignored configs, or Ant user-owned files.

- [ ] **Step 4: Commit only exact owned paths after all gates pass**

Write matching CHANGELOG and commit bodies. Stage explicit task-owned paths only; inspect `git diff --cached --name-status` before each commit. Never include ignored secrets, `src/app.tsx.orig`, old audit deletions, or run artifacts.

### Task 7: Keep only the current browser reference lease in model context

Run `ops-monitor-real-20260825-19` reached 225,501 provider input tokens. Its latest
`browser_inspect` returned the confirm button as `s-...3d1c / e1`, but the next MiMo
response copied the immediately older second-column reference `s-...b291 / e3`.
`SnapshotStore` correctly rejected it as `stale_ref`; the run was cancelled with RC 130
before configuration mutation. Canonical trace retention is correct, but executable
references and full 200-element review snapshots must not accumulate in model context.
Run `ops-monitor-real-20260825-20` then proved that context cleanup alone is insufficient:
after successful date/hour clicks, MiMo twice selected `browser_click` again without an
intervening inspect. The Runtime still exposed every mutation schema on every turn, so
the inspect-before-write rule remained advisory instead of tool-type enforced. `-20`
was cancelled at the first observed `stale_ref`, RC 130, before configuration mutation.
Run `ops-monitor-real-20260825-21` proved that schema gating also needs an execution
fence: MiMo emitted a hidden `browser_fill` anyway, and the Runtime dispatched it to
`SnapshotStore`, which correctly returned `stale_ref`. After a fresh inspect the exact
same value filled successfully, confirming the page/fill implementation was healthy and
the missing pre-dispatch reference fence was the cause. `-21` was cancelled with RC 130
before configuration mutation.

- [x] **Step 1: Add failing projection tests from the immutable `-19` sequence**

Build canonical assistant/tool pairs for an old `browser_inspect`, a newer
`browser_inspect`, and browser mutations with review snapshots. Assert the provider
projection exposes executable `snapshot_id`/`element_id` only from the immediately
preceding single inspect pair, keeps only the latest review snapshot in full, and does
not mutate the canonical session messages.

- [x] **Step 2: Add a browser-specific model-context projection**

Before token estimation and provider request freezing, deep-copy browser tool results.
Remove executable IDs and element bodies from expired inspect results, retaining a small
typed `expired_review_only` summary. Collapse superseded mutation `next_snapshot`
payloads to metadata while preserving the most recent review snapshot. In addition,
expose the six mutation schemas only when the immediately preceding single tool pair is
a successful `browser_inspect` containing at least one executable reference. Hide them
after every mutation, wait, navigation, progress update, or other query. Never rewrite a
model-selected old target to the current target and never change the append-only trace.
Because a provider may still emit a tool absent from its schema, add a pre-dispatch fence:
a mutation without the current inspect lease, or with a different snapshot/element pair,
must produce a non-executing protocol correction with `backend_attempted=false`; it must
not enter the browser executor, create a screenshot, or appear as an external tool error.

- [x] **Step 3: Prove both sync and async context assembly use the projection**

Run focused context/runtime tests and assert the serialized provider messages contain
the current confirm-button pair and no older executable pair. Assert provider tool lists
hide mutations initially, expose them immediately after a valid inspect, and hide them
again immediately after the mutation. Force a hidden mutation and a mismatched old pair,
then assert the dispatcher call count remains zero until a new inspect supplies the exact
pair. Also assert non-browser profiles and canonical session persistence are unchanged.

- [x] **Step 4: Re-run focused browser/runtime gates and a fresh live run**

After pytest, Ruff, real Ant integration, and `git diff --check`, allocate a new unique
run label. Apply all existing hard gates and accept only the external terminal state and
deterministic bundle checks in Task 5.

### Task 8: Reject provider-invented tools before dispatch

Run `ops-monitor-real-20260825-22` reached the pre-change alarm time-range picker with
no external tool errors until MiMo emitted `browser_press_key`, a tool that was absent
from the current provider schema and the HomeMaster registry. The generic executor
classified it as `unknown_tool` with `is_error=true`; the run was cancelled with RC 130
before configuration mutation. This proves that dynamic schema hiding and the six known
browser-mutation reference fence are insufficient: a provider can invent a different
tool name that bypasses both and reaches the executor.

- [x] **Step 1: Preserve the failure and identify the exact boundary**

Assert `-22` has RC 130, empty stderr, unchanged `agent_version=0.9.0`, no active
`observe`, and exactly one hard tool result: `browser_press_key / unknown_tool`. Confirm
the current provider request did not offer that tool and that no browser backend action
started for it.

- [x] **Step 2: Add a current-schema availability fence**

After the existing exact-reference fence and before any `tool.call_started` event or
executor dispatch, compare every emitted tool name with the frozen schemas actually sent
for that provider request. If any name was not offered, reject the whole batch as
`protocol_blocked`, `is_error=false`, and `backend_attempted=false`. Emit a structured
protocol-rejection event and return one result per call so mixed batches cannot partially
execute. Keep runtimes without a tool registry backward compatible by skipping this fence
when no provider tool schema exists.

- [x] **Step 3: Prove zero dispatch and live recovery**

Add a transport test that invents `browser_press_key` despite receiving only
`browser_inspect`. Assert the dispatcher receives zero calls, the result is non-error,
and a later valid model response can complete. Re-run the focused gates, then allocate
`ops-monitor-real-20260825-23` and accept it only through the Task 5 external black-box
checks.

### Task 9: Enforce exact terminal verification commands

Run `ops-monitor-real-20260825-23` reached the pre-change alarm date picker with no hard
tool errors, but MiMo substituted `terminal(command='echo "test"')` while reasoning about
closing the picker. The command was not the ticket's `operate_verified` terminal command;
because the default full-auto permission policy had no exact allowlist, it executed with
return code 0. The run was cancelled with RC 130 and the fixture remained exactly 0.9.0.

Prompt-only prohibition is not an execution boundary. Hiding `terminal` for the whole run
would also prevent the required independent post-change verification, while parsing one
ticket schema inside the generic runtime would make it environment-specific. Use a generic
configured exact-command allowlist instead.

- [x] **Step 1: Add typed exact terminal-command permission configuration**

Add `permissions.allowed_terminal_commands` as an optional tuple of nonblank, unique,
whitespace-exact strings. An empty tuple preserves existing behavior. When nonempty, the
permission checker must deny every `terminal` command not exactly equal to one entry; deny
rules continue to take precedence. Do not apply the allowlist to unrelated tools that happen
to carry a `command` field.

- [x] **Step 2: Add a non-error pre-dispatch fence**

Expose the resolved permission settings to `AgentRuntime`. Before `tool.call_started`, reject
the whole batch when any terminal command misses the exact allowlist. Return one
`protocol_blocked`, `is_error=false`, `backend_attempted=false` result per call, emit a
structured rejection event, and never reveal the allowed command strings to the provider.
The permission checker remains the executor-side defense in depth.

- [x] **Step 3: Verify both layers and run `-24`**

Test exact match versus one-character/whitespace changes at the permission layer. At the
runtime layer, force `echo "test"`, assert zero dispatch and no allowlist leakage, then issue
the exact allowed command and assert it alone reaches the dispatcher. Re-run all gates and
launch `ops-monitor-real-20260825-24` with the single terminal command independently audited
from the Agent's `read_file` trace.

### Task 10: Enforce semantic target actionability before browser dispatch

Run `ops-monitor-real-20260825-24` filled the pre-change start datetime and inspected
the date-picker confirmation button. The inspect result explicitly reported
`enabled=false`, and MiMo's own text acknowledged that state, but it still called
`browser_click` with the exact current pair. `PlaywrightBrowserSession` correctly refused
the target with `target_disabled` and `backend_attempted=false`, but because actionability
was checked only inside the executor the result became `is_error=true`. The run was
cancelled with RC 130 and the fixture remained exactly 0.9.0.

- [x] **Step 1: Extend the current-inspect lease with actionability metadata**

Resolve the exact current `(snapshot_id, element_id)` to its inspected element record.
After the existing presence and exact-pair checks, reject a mutation when the record says
`enabled=false`, `visible=false`, or `obscured=true`. Return
`browser_target_not_actionable` as a non-error protocol correction with zero backend
attempt and advise waiting or inspecting a different semantic target. Missing legacy
fields remain backward compatible rather than being guessed false.

- [x] **Step 2: Prove a disabled target never reaches the browser executor**

Use a deterministic transport that inspects a disabled, visible, unobscured button and
then attempts the exact click. Assert only inspect reaches the dispatcher, click receives
`protocol_blocked/is_error=false/backend_attempted=false`, and the model can recover on
the next turn. Re-run all gates before allocating the next unique live label.

### Task 11: Keep protocol corrections out of the executed-tool trajectory

Accepted live run `ops-monitor-real-20260825-25` contains ten expected pre-dispatch
browser protocol corrections. Each deliberately emits `tool.call_completed` without
`tool.call_started`, because no executor/backend call occurred. The deterministic bundle
builder previously treated every completion without a start as corrupt, which conflicts
with the new fail-closed runtime contract.

- [x] **Step 1: Skip only proven zero-backend protocol completions**

When building the executed-tool trajectory, skip an orphan `tool.call_completed` only if
its payload proves all three conditions: `is_error=false`, `status=protocol_blocked`, and
`backend_attempted=false`. Preserve these events in the copied raw runtime JSONL. Continue
to reject every other completion/failure without a matching start.

- [x] **Step 2: Materialize and independently verify the accepted run**

Add a protocol-blocked completion to the deterministic bundle test and assert the executed
sequence remains continuous. Generate exact terminal/final-state artifacts from the `-25`
black-box audit, materialize the private bundle, verify it in a fresh process, and re-read
every manifest hash and byte count independently.

The first bundle preserved at `deterministic` is negative evidence: it was generated before
click-result route tracking and fails the independent stage gate. The repaired verifier rebuilds
the trajectory from raw runtime events and publishes only after temporary-bundle verification.
Fresh output `deterministic-verifier-v2` passes the product verifier, independent checks for all
35 artifact hashes/sizes/modes, external asset readback, RC/stderr/fixture checks, and process
cleanup. Its 81 executed calls contain 8 framework, 45 pre-change, 12 implementation, and 16
post-change rows; all ten provider protocol mistakes were blocked before backend dispatch.

### Task 12: Fail closed before publishing deterministic acceptance bundles

- [x] **Step 1: Reproduce false publication and self-consistent derived-trajectory acceptance**

Add regressions proving a final semantic failure currently leaves the formal output directory and
that changing both `trajectory.jsonl` and its manifest hash can evade the old verifier.

- [x] **Step 2: Rebuild from raw events and verify before atomic publication**

Require exact equality between the copied raw-event rebuild and the derived trajectory. Run the
complete verifier against the private temporary directory before rename; on any failure remove
only the temporary directory and leave the formal output path absent.

- [x] **Step 3: Record the incident and preventive rule**

Document the symptom/root cause/repair in `docs/pitfalls.md`, add the positive publication rule
to `CLAUDE.md`, and synchronize the Browser Gateway guide, CHANGELOG, and live progress.
