# Automatic Write-Tool Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch subagents; repository instructions require single-agent execution.

**Goal:** Replace MiMo-selected follow-up `observe` calls with one runtime-owned automatic PNG observation after each browser write/interaction tool.

**Architecture:** Keep `requires_model_observation` as the tool-definition classification bit, but execute its observation inside `AgentRuntime` after the action backend attempt and before the next provider request. Merge the validated PNG into the original action result so the model transcript keeps one result per selected tool call. Browser navigate/wait/query tools do not set the bit.

**Tech Stack:** Python 3.11, Pydantic messages, async tool dispatcher, Playwright browser tools, pytest, Ruff.

---

## File map

- Modify `src/homemaster/agent/model_observation.py`: provide deterministic automatic-observation call creation and result merging helpers.
- Modify `src/homemaster/agent/generic_runtime.py`: dispatch observation internally, retry only the screenshot, publish the original action once, and stop after evidence failure.
- Modify `src/homemaster/browser/tools.py`: classify only write/interaction browser tools with `requires_model_observation=True`.
- Modify `src/homemaster/skills/builtin/change-ticket-executor/SKILL.md`: describe runtime-owned observation and remove the model-selected follow-up requirement.
- Modify `tests/homemaster/application/test_model_observation_barrier.py`: replace next-model-turn barrier expectations with automatic observation, failure, and resume compatibility tests.
- Modify `tests/homemaster/browser/test_contracts_and_tools.py`: lock the six write/interaction browser tool classifications.
- Modify `tests/homemaster/skills/test_change_ticket_executor_evidence.py`: lock the new Skill contract.
- Modify `docs/pitfalls.md`, `CLAUDE.md`, `docs/browser-gateway-user-guide.md`, `README.md`, `CHANGELOG.md`, and the Ops Monitor handoff after acceptance.

### Task 1: Lock browser tool classification

- [ ] **Step 1: Change the browser classification test to the approved set**

In `tests/homemaster/browser/test_contracts_and_tools.py`, assert:

```python
assert observation_actions == {
    "browser_fill",
    "browser_select",
    "browser_check",
    "browser_uncheck",
    "browser_click",
    "browser_backfill",
}
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
.venv/bin/pytest -q tests/homemaster/browser/test_contracts_and_tools.py
```

Expected: FAIL because navigation and wait are still classified.

- [ ] **Step 3: Narrow `_registered` classification**

In `src/homemaster/browser/tools.py`, remove `browser_navigate` and
`browser_wait` from the `requires_model_observation` set.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Expected: browser contract tests pass.

### Task 2: Prove runtime-owned automatic observation

- [ ] **Step 1: Write failing runtime tests**

Update the scripted runtime tests so the transport emits:

```python
[
    [ToolCall(id="action-1", name="robot_go_to")],
    [ToolCall(id="inspect-1", name="read_state")],
    "done",
]
```

The dispatcher must record `robot_go_to`, then runtime-generated `observe`, then
`read_state`. Assert:

```python
assert dispatcher.calls == ["robot_go_to", "observe", "read_state"]
assert len(transport.requests) == 3
assert all(
    request["tools"] != [{"name": "observe"}]
    for request in transport.requests
)
assert not any(event.type == "model_observation.protocol_rejected" for event in result.events)
```

Also assert the action result contains one image and
`automatic_observation.source_tool_call_id == "action-1"`.

- [ ] **Step 2: Add failing capture-failure tests**

Use an invalid screenshot dispatcher and assert three `observe` dispatches, one
action dispatch, `result.error_code == "automatic_observation_failed"`, and no
second action call.

- [ ] **Step 3: Run the new tests and confirm RED**

Run only the named tests and confirm current runtime asks the model for observe.

- [ ] **Step 4: Add automatic observation helpers**

In `model_observation.py`, add helpers equivalent to:

```python
def automatic_observation_call(source: ToolCall, attempt: int) -> ToolCall:
    return ToolCall(
        id=f"auto-observe-{source.id}-{attempt}",
        name="observe",
        arguments={},
    )

def attach_observation(
    action_result: ToolResultMessage,
    observation_result: ToolResultMessage,
    evidence: ObservationImageEvidence,
) -> None:
    action_result.content.extend(
        block for block in observation_result.content if block.type == "image"
    )
    data = dict(action_result.data or {})
    data["automatic_observation"] = {
        "status": "success",
        "source_tool_call_id": action_result.tool_call_id,
        "content_sha256": evidence.content_sha256,
        "pixel_sha256": evidence.pixel_sha256,
    }
    action_result.data = data
```

Validate exact one-PNG evidence with the existing validator.

- [ ] **Step 5: Dispatch automatic observation inside `AgentRuntime`**

After the action dispatch and before appending/publishing results:

1. select the sole tool with `requires_model_observation=True`;
2. if its result has `backend_attempted=True`, dispatch `observe` internally;
3. retry only observe up to `MAX_OBSERVE_FAILURES`;
4. merge one valid PNG into the action result;
5. emit `model_observation.automatic_started`, `.automatic_completed`, or
   `.automatic_failed` events;
6. do not set a new `pending_model_observation` barrier.

If all captures fail, append and publish the original action receipt, emit
`runtime.turn_failed` with `automatic_observation_failed`, save the snapshot, and
return a failed `GenericRunResult` before the next provider call.

- [ ] **Step 6: Preserve old snapshot compatibility**

Leave the existing pending barrier resume path readable, but ensure newly
executed actions never create it. Existing resume tests must remain green.

- [ ] **Step 7: Run runtime tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest -q \
  tests/homemaster/application/test_model_observation_barrier.py \
  tests/homemaster/application/test_model_observation_resume.py
```

### Task 3: Synchronize the execution Skill

- [ ] **Step 1: Add a failing Skill contract test**

Assert the Skill contains:

```python
assert "HomeMaster automatically attaches one observation" in skill
assert "Do not schedule a follow-up `observe`" in skill
```

- [ ] **Step 2: Confirm RED**

Run the named Skill test.

- [ ] **Step 3: Replace the manual observation instruction**

State that write/interaction tool results already carry the runtime observation,
while manual `observe` is only for a user-requested review image and never a
required action follow-up.

- [ ] **Step 4: Confirm all Skill tests GREEN**

### Task 4: Focused and orthogonal verification

- [ ] **Step 1: Run focused pytest and Ruff**

Run the runtime, browser, CLI, trajectory, and Skill suites plus Ruff on every
changed Python path.

- [ ] **Step 2: Run a scripted black-box agent sequence**

Use a real browser fixture page and a scripted transport that clicks a button then
immediately inspects. Assert one click backend receipt, one attached PNG, no
protocol rejection, successful inspect, and zero model-selected observe calls.

- [ ] **Step 3: Run the real Ant semantic probes**

Re-prove alarm and asset evidence controls through HomeMaster semantic tools only.

### Task 5: Fresh Ops Monitor end-to-end acceptance

- [ ] **Step 1: Allocate a never-used run label and verify preconditions**

Require absent run/stdout/stderr/rc paths, fixture exactly `0.9.0`, HTTP 200, and
zero old HomeMaster/Playwright/Chromium processes.

- [ ] **Step 2: Launch once with durable return-code capture**

Use the approved semantic-browser-only prompt and the memory-disabled private
acceptance config that isolates the unrelated pending dreaming batch.

- [ ] **Step 3: Monitor structured lifecycle events**

Reject model-selected `observe` immediately after an action, any failed tool call,
coordinate/terminal/CDP browser fallback, validation failure, or target drift.

- [ ] **Step 4: Verify every external terminal independently**

Assert RC 0, empty stderr, fixture `1.0.0`, independent HTTP asset readback,
distinct precheck/postcheck WSO evidence IDs, automatic observation metadata on
write tools only, no failed lifecycle, and zero residual processes.

- [ ] **Step 5: Materialize and verify the deterministic private trajectory bundle**

Require the trajectory verifier and independent artifact/hash audit to pass.

### Task 6: Documentation and final audit

- [ ] **Step 1: Record the non-obvious barrier pitfall and positive rule**

Add the symptom, root cause, fix, and reference to `docs/pitfalls.md`; add a
forward rule to `CLAUDE.md` requiring deterministic runtime follow-ups to execute
inside the runtime rather than through model compliance.

- [ ] **Step 2: Update user and release documentation**

Update browser guide, README, CHANGELOG, V3.0 handoff, and this plan with exact
run labels and verified results.

- [ ] **Step 3: Run final tests and dirty-worktree audit**

Run all task-relevant tests, inspect exact diffs against HEAD, and include only
task-owned paths in any commit. Do not reset or stage unrelated user changes.
