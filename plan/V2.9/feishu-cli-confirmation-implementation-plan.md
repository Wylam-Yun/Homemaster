# Feishu CLI-Equivalent Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the existing CLI tool-confirmation gate from Feishu by sending an interactive approval card, waiting for its callback, and resuming the exact blocked tool call only after approval.

**Architecture:** Keep permission evaluation and `ToolExecutor` unchanged. Add a Gateway confirmation handler modelled on Hermes' proven notify/wait/resolve lifecycle, using `asyncio.Future` instead of `threading.Event`; bind a per-session Feishu notifier around each Gateway run, carry card actions from the existing WebSocket child process to the main process, and resolve by an opaque `approval_id` without creating a new inbound model turn.

**Tech Stack:** Python 3.11+, asyncio, Pydantic configuration, `lark-oapi`, multiprocessing queue, pytest/pytest-asyncio.

---

## Scope and locked decisions

- This plan does **not** redesign `PermissionMode`, capabilities, `tool.auto`, allow/deny policy, risk tiers, or approval persistence. The separate permission redesign remains authoritative.
- The new path runs only when the upstream permission decision already has `requires_confirmation=True`.
- Feishu matches CLI's single-operation semantics: exactly two choices, approve or deny. Do not add Hermes' session, always, smart, off, or yolo behavior.
- The user who originated the Feishu run is the only approver, and the callback must come from the original chat.
- Approval arguments are the complete validated arguments. Do not redact or truncate them. If Feishu rejects an oversized card, fail closed.
- Pending approvals are process-local. Restart, session replacement, cancellation, timeout, and shutdown deny and remove them.
- Default remote approval timeout is 300 seconds and is capped by the existing run deadline when one exists.
- Existing dirty work in `docs/session-handoff.md` and `plan/V2.8/` must remain untouched.

## File map

- Create `src/homemaster/gateway/confirmation.py`: asyncio pending approvals, per-session notifier binding, exact callback resolution, timeout/cancel/close cleanup, confirmation audit events.
- Modify `src/homemaster/gateway/runtime.py` and `src/homemaster/cli/gateway_command.py`: construct one handler, inject it into the application, bind/unbind the current Feishu route around a run, and close it during Gateway shutdown.
- Modify `src/homemaster/channels/impl/feishu.py`: render/send/update approval cards, register `p2_card_action_trigger`, normalize callback packets in the worker, and resolve them in the main process.
- Modify `src/homemaster/events/public_projection.py`: allow the two existing confirmation audit events with exact selected values; do not use the public event stream as the card control plane.
- Create `tests/homemaster/gateway/test_confirmation.py`; extend `tests/homemaster/channels/test_feishu.py`, `tests/homemaster/gateway/test_runtime.py`, and `tests/homemaster/events/test_public_projection.py`.

### Task 1: Build the async Gateway confirmation core

**Files:**
- Create: `src/homemaster/gateway/confirmation.py`
- Create: `tests/homemaster/gateway/test_confirmation.py`

- [ ] **Step 1: Write failing tests for the public behavior**

Cover the exact structural protocol already called by `ToolExecutor`:

```python
approved = await handler.confirm(tool, arguments, context, decision)
```

Tests must lock these behaviors:

```python
async def test_approve_resolves_exact_pending_call(): ...
async def test_deny_returns_false(): ...
async def test_unknown_and_duplicate_id_resolve_nothing(): ...
async def test_wrong_operator_or_chat_is_rejected(): ...
async def test_notify_failure_denies_without_waiting(): ...
async def test_timeout_denies_and_removes_pending_entry(): ...
async def test_run_deadline_caps_confirmation_timeout(): ...
async def test_cancel_removes_pending_and_propagates_cancelled_error(): ...
async def test_unbind_denies_only_matching_generation(): ...
async def test_close_denies_every_pending_confirmation(): ...
```

Use a fake async notifier that records the request and returns a successful `DeliveryReceipt` containing a message ID. Use different `session_id`, `run_id`, `tool_call_id`, `chat_id`, and sender values so accidental session-only resolution cannot pass.

- [ ] **Step 2: Run the new test module and confirm it fails**

```bash
pytest -q tests/homemaster/gateway/test_confirmation.py
```

Expected: collection/import failure because `homemaster.gateway.confirmation` does not exist.

- [ ] **Step 3: Implement the minimal typed core**

Define immutable request/route records and one handler:

```python
class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class ApprovalResolveStatus(StrEnum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    UNAUTHORIZED = "unauthorized"
    STALE = "stale"


@dataclass(frozen=True)
class FeishuApprovalRoute:
    session_id: str
    generation: int
    chat_id: str
    requester_open_id: str
    notify: Callable[[ApprovalRequest], Awaitable[DeliveryReceipt]]
    update: Callable[[str, str, str], Awaitable[DeliveryReceipt]]


class FeishuGatewayConfirmationHandler:
    def bind_session(self, route: FeishuApprovalRoute) -> None: ...
    async def unbind_session(self, session_id: str, generation: int) -> None: ...
    async def confirm(self, tool, arguments, context, decision) -> bool: ...
    async def resolve(self, approval_id, decision, operator_open_id, chat_id): ...
    async def aclose(self) -> None: ...
```

Implementation invariants:

- Generate `approval_id = uuid.uuid4().hex`.
- Store pending entries by `approval_id`, never by session FIFO.
- Reject a missing route before waiting.
- Await the notifier and require `DeliveryStatus.CONFIRMED_SUCCESS`; otherwise return `False`.
- Await the Future with `asyncio.timeout(min(300, remaining_deadline))` without cancelling unrelated tasks.
- Remove the entry in `finally`; emit outcome `approved`, `denied`, `timeout`, `cancelled`, `send_failed`, or `closed`.
- On cancellation, emit/clean up and re-raise `CancelledError`.
- `unbind_session(session, generation)` may remove only the matching generation and denies that generation's entries.
- `resolve()` validates the exact approval ID, operator, chat, and pending state before completing its Future once.

- [ ] **Step 4: Run the handler tests**

```bash
pytest -q tests/homemaster/gateway/test_confirmation.py
```

Expected: all tests pass.

### Task 2: Preserve exact confirmation audit events

**Files:**
- Modify: `src/homemaster/gateway/confirmation.py`
- Modify: `src/homemaster/events/public_projection.py`
- Modify: `tests/homemaster/events/test_public_projection.py`
- Test: `tests/homemaster/test_cli_confirmation.py`

- [ ] **Step 1: Add failing projection and audit tests**

Assert the requested event payload contains the exact values:

```json
{
  "approval_id": "<opaque-id>",
  "arguments": {"command": "exact unmodified value"},
  "cwd": "/exact/cwd",
  "reason": "confirmation required",
  "subject_id": "feishu-owner"
}
```

Assert the completed event contains the same `approval_id`, `approved`, `outcome`, and `subject_id`. Confirm the public projection includes both event types and recursively copies values without redaction. Confirm no confirmation event is converted into a terminal or duplicate final reply.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
pytest -q tests/homemaster/events/test_public_projection.py tests/homemaster/test_cli_confirmation.py
```

- [ ] **Step 3: Add exact public projection support**

Add both event types to the public allowlist. Project only the explicitly selected approval fields, preserving their exact values. Keep card delivery on the direct notifier path; the Gateway public-event loop must not send a second approval card or final message.

- [ ] **Step 4: Run the focused tests**

```bash
pytest -q tests/homemaster/events/test_public_projection.py tests/homemaster/test_cli_confirmation.py
```

Expected: all tests pass and existing CLI payload expectations remain unchanged except for any deliberately shared `approval_id` field adopted by both adapters.

### Task 3: Add Feishu approval card transport

**Files:**
- Modify: `src/homemaster/channels/impl/feishu.py`
- Modify: `tests/homemaster/channels/test_feishu.py`

- [ ] **Step 1: Write failing card-send and card-update tests**

Lock the card contract:

- Interactive message contains tool name, exact JSON arguments, exact cwd, and reason.
- Buttons are exactly approve and deny.
- Button values contain only `homemaster_action` and `approval_id`.
- A successful send returns the platform message ID in `DeliveryReceipt.platform_ids`.
- API rejection, exception, or missing message ID is not confirmed success.
- Resolved cards remove buttons and show approved, denied, or expired state.

- [ ] **Step 2: Run the Feishu test module and confirm failure**

```bash
pytest -q tests/homemaster/channels/test_feishu.py
```

- [ ] **Step 3: Implement REST operations**

Add:

```python
async def send_exec_approval(self, *, delivery, request) -> DeliveryReceipt: ...
async def update_exec_approval(self, message_id, outcome, actor) -> DeliveryReceipt: ...
```

Use the authenticated immutable `ChannelDeliveryContext` as the send target. Send `msg_type="interactive"`. Add a corresponding audited message patch/update method to `FeishuApiService`. Do not truncate or redact the validated argument JSON.

- [ ] **Step 4: Run the Feishu tests**

```bash
pytest -q tests/homemaster/channels/test_feishu.py
```

Expected: all existing and new Feishu tests pass.

### Task 4: Carry Feishu card actions across the worker boundary

**Files:**
- Modify: `src/homemaster/channels/impl/feishu.py`
- Modify: `tests/homemaster/channels/test_feishu.py`

- [ ] **Step 1: Write failing real-shape dispatcher tests**

Build SDK-shaped callback fixtures and pass them through the actual dispatcher callback. Assert:

- `p2_card_action_trigger` is registered.
- The synchronous SDK callback returns an ACK.
- The worker emits one packet with type `approval_action` and exact `approval_id`, decision, operator open ID, and open chat ID.
- Invalid actions, missing IDs, and unsupported decisions fail closed.
- Duplicate callbacks cannot execute twice even if duplicate packets reach the main process.

- [ ] **Step 2: Run the focused dispatcher tests and confirm failure**

```bash
pytest -q tests/homemaster/channels/test_feishu.py -k 'approval or card_action or dispatcher'
```

- [ ] **Step 3: Implement worker normalization and main-process dispatch**

Register the SDK callback in `_build_feishu_event_handler`. The child process performs only shape normalization and ACK; it does not hold approval state or decide authorization. Extend `FeishuChannel.start()` to consume `approval_action`, call the injected handler's exact resolver, and update the original card according to the returned resolve status.

Unauthorized, unknown, duplicate, or stale callbacks must not enter the inbound bus. Card-update failure is audited but does not reverse a successfully resolved decision.

- [ ] **Step 4: Run the Feishu tests**

```bash
pytest -q tests/homemaster/channels/test_feishu.py
```

### Task 5: Wire the handler around the Gateway run lifecycle

**Files:**
- Modify: `src/homemaster/cli/gateway_command.py`
- Modify: `src/homemaster/gateway/runtime.py`
- Modify: `tests/homemaster/gateway/test_runtime.py`

- [ ] **Step 1: Write failing composition and lifecycle tests**

Assert one handler instance is:

- constructed before `create_home_application`;
- passed as its `confirmation_handler`;
- passed to `GatewayRuntime` and `FeishuChannel`;
- bound before `ChannelBridge.handle()`;
- unbound in `finally` on success, failure, cancellation, session replacement, and shutdown.

Add a generation race test: generation 1 cleanup after generation 2 is bound must not remove generation 2's notifier or approval.

- [ ] **Step 2: Run the Gateway tests and confirm failure**

```bash
pytest -q tests/homemaster/gateway/test_runtime.py
```

- [ ] **Step 3: Implement composition and run-scoped binding**

In `serve_gateway`, create the handler with a 300-second timeout and inject the same instance into application creation and Gateway assembly. In `GatewayRuntime._run`, bind a route containing the authoritative delivery context, sender open ID, chat ID, generation, and async send/update callbacks before calling the bridge; unbind it in `finally`. During shutdown, deny pending approvals within the existing absolute Gateway shutdown deadline before channel/resource close.

- [ ] **Step 4: Run Gateway tests**

```bash
pytest -q tests/homemaster/gateway/test_runtime.py
```

### Task 6: Prove the ToolExecutor terminal states

**Files:**
- Modify: `tests/homemaster/gateway/test_runtime.py`
- Test: existing ToolExecutor permission tests discovered by `pytest --collect-only`

- [ ] **Step 1: Add black-box approval and denial tests**

Use a real `ApplicationRuntime` dispatch with a controlled mutating tool and an injected permission checker that returns `requires_confirmation=True`.

Approve case assertions:

- one card request;
- callback resolves the matching `approval_id`;
- resource lease acquired once;
- backend called once;
- external terminal state changed exactly once;
- run returns its normal tool/final status.

Deny, timeout, send-failure, and cancellation assertions:

- no resource lease;
- backend call count zero;
- external terminal state unchanged;
- stable denied/cancelled result;
- no pending entry after completion.

Also assert the callback path publishes no `InboundMessage`, creates no new model run, and does not advance the Gateway generation.

- [ ] **Step 2: Run the black-box tests**

```bash
pytest -q tests/homemaster/gateway/test_runtime.py -k approval
```

- [ ] **Step 3: Run all focused regression suites**

```bash
pytest -q \
  tests/homemaster/gateway/test_confirmation.py \
  tests/homemaster/channels/test_feishu.py \
  tests/homemaster/gateway/test_runtime.py \
  tests/homemaster/events/test_public_projection.py \
  tests/homemaster/test_cli_confirmation.py \
  tests/homemaster/permissions
```

Expected: all pass. The live Feishu mutation gate is intentionally deferred until the separate permission redesign can produce a real `requires_confirmation=True` decision for the Feishu principal.

### Task 7: Final verification and handoff

**Files:**
- Do not modify `docs/session-handoff.md` or `plan/V2.8/` in this task.

- [ ] **Step 1: Run static gates**

```bash
ruff check src/homemaster tests/homemaster
python -m compileall -q src/homemaster
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 2: Inspect workspace scope**

```bash
git status --short
git diff --stat
git diff -- src/homemaster tests/homemaster
```

Confirm only the planned confirmation, Feishu, Gateway, event, and test files changed; preserve all pre-existing user modifications.

- [ ] **Step 3: Report the evidence**

Report focused pytest counts, static-gate results, approve/deny backend call counts, pending-entry cleanup, and the explicit dependency on the future permission redesign for a real live Feishu mutation run. Do not claim live Feishu approval is verified until that external gate is actually run.
