# HomeMaster V2.5 Automatic Memory Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically run one MindMemOS Vanilla Search before the first model request of a new Session and before the first model request after a completed Compact, then expose the real Top-3 results as run-scoped model context without creating tool messages.

**Architecture:** `SessionRuntime.require_recall` is the durable latch. `ApplicationRuntime` consumes it once per real user run, calls the existing `EmbeddedMindMemOS.search()` entry directly, and binds a rendered Memory Context to that run's `ContextAssembler`. `AgentRuntime` reports actual compaction through a narrow callback so the Application layer can re-arm the latch for the next user run; manual compaction re-arms it in `ApplicationRuntime.compact()`.

**Tech Stack:** Python 3.11+, asyncio, Pydantic, pytest/pytest-asyncio, existing HomeMaster Application Runtime and embedded MindMemOS.

---

## Scope and invariants

- Do not change MindMemOS implementation or its database schema.
- Do not route automatic recall through `ApplicationToolExecutor.dispatch()`.
- Do not create an `AssistantMessage.tool_calls` entry or `ToolResultMessage` for automatic recall.
- Call the existing `mindmemos.search()` entry with `top_k=3`, `search_pipeline="vanilla"`, `rerank=False`, and `filters=None`.
- Do not add a recall-specific timeout. If an existing run deadline applies, automatic recall shares it; otherwise recall is not independently truncated.
- Search success, empty results, missing service, or ordinary error all consume the latch and do not block the Provider request.
- Do not persist the injected Memory Context in `session.messages`; it is run-scoped.
- Provider retry reuses the frozen request body and never repeats Search.
- Compaction in the current run only re-arms the latch. Search waits for the next real user run.
- Automatic recall does not filter by HomeMaster `fact`/`procedure` or any MindMemOS `mem_type`.
- Agent-initiated `mindmemos_search` retains its current optional type filters and real ToolResult path.
- Existing dirty-worktree changes belong to the owner. Before implementation, create an isolated worktree from a selected checkpoint. If overlapping edits in `generic_runtime.py` and its tests remain uncommitted, stop and ask the owner to checkpoint or select the baseline; do not stage them accidentally.

## File map

- Create `src/homemaster/memory/automatic_recall.py`: deterministic Query construction, request identity, and result rendering.
- Create `tests/homemaster/memory/test_automatic_recall.py`: pure helper tests.
- Modify `src/homemaster/application/session.py`: durable latch and snapshot compatibility.
- Modify `tests/homemaster/application/test_session_manager.py`: state and resume tests.
- Modify `src/homemaster/agent/context.py`: run-scoped Memory Context prelude.
- Modify `tests/homemaster/application/test_context_assembler_scope.py`: injection and isolation tests.
- Modify `src/homemaster/application/runtime.py`: direct Search, latch consumption, trace event, manual Compact re-arm.
- Modify `src/homemaster/agent/generic_runtime.py`: compaction-completed callback.
- Modify `src/homemaster/events/runtime_events.py`: internal recall event registration.
- Modify `tests/homemaster/application/test_application_runtime.py`: end-to-end Provider-boundary tests.
- Modify `tests/homemaster/test_generic_agent_runtime.py`: automatic/reactive compaction callback tests.

## Task 1: Add deterministic automatic-recall helpers

**Files:**
- Create: `src/homemaster/memory/automatic_recall.py`
- Create: `tests/homemaster/memory/test_automatic_recall.py`

- [ ] **Step 1: Write failing Query construction tests**

```python
import json

from homemaster.agent.compact import build_compaction_summary_message
from homemaster.agent.messages import UserMessage
from homemaster.memory.automatic_recall import build_automatic_recall_query
from homemaster.task_state.store import TaskStateStore


def test_new_session_query_is_exact_user_text() -> None:
    assert build_automatic_recall_query(
        current_user_message="  保留两侧空格  ",
        messages=[],
        task_state_store=TaskStateStore(run_id="new"),
    ) == "  保留两侧空格  "


def test_post_compact_query_has_stable_sections() -> None:
    store = TaskStateStore(run_id="compact")
    snapshot = store.create_or_replace_plan(
        goal="恢复告警处理",
        subtasks=[{"id": "inspect", "description": "检查当前告警"}],
        current_subtask="inspect",
        next_focus="读取告警详情",
    )
    query = build_automatic_recall_query(
        current_user_message="继续处理",
        messages=[build_compaction_summary_message("已经登录监控后台。")],
        task_state_store=store,
    )
    state = json.dumps(
        snapshot.to_model_visible_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert query == (
        "[Compact Summary]\n已经登录监控后台。\n\n"
        f"[Current Task State]\n{state}\n\n"
        "[Current User Message]\n继续处理"
    )


def test_post_micro_compact_query_keeps_empty_summary_section() -> None:
    store = TaskStateStore(run_id="micro")
    query = build_automatic_recall_query(
        current_user_message="继续",
        messages=[UserMessage.from_text("压缩后保留的历史")],
        task_state_store=store,
    )
    assert query == (
        "[Compact Summary]\n\n\n"
        "[Current Task State]\n{}\n\n"
        "[Current User Message]\n继续"
    )
```

- [ ] **Step 2: Write failing identity and native-result rendering tests**

```python
from types import SimpleNamespace

from homemaster.memory.automatic_recall import (
    build_automatic_recall_context,
    build_mindmemos_request_context,
)


def test_request_context_uses_tenant_and_session() -> None:
    context = build_mindmemos_request_context(
        request_id="automatic-recall:run-1",
        tenant_id="tenant-a",
        session_id="session-a",
    )
    assert (context.account_id, context.project_id, context.user_id) == (
        "tenant-a", "tenant-a", "tenant-a"
    )
    assert context.session_id == "session-a"


def test_context_preserves_unknown_native_memory_types() -> None:
    memories = [
        SimpleNamespace(
            id="profile-1",
            memory="用户偏好中文回答",
            memory_type="profile",
            last_update_at="2026-08-13 10:00:00",
            event_time=None,
            source_timestamp=None,
            lineage=None,
        ),
        SimpleNamespace(
            id="episode-1",
            memory="上次部署在验证阶段失败",
            memory_type="episodic",
            last_update_at="2026-08-13 11:00:00",
            event_time=None,
            source_timestamp=None,
            lineage=None,
        ),
    ]
    text = build_automatic_recall_context(memories)
    assert text is not None
    assert '"memory_type":"profile"' in text
    assert '"memory_type":"episodic"' in text
    assert "not as user instructions" in text


def test_empty_recall_does_not_create_context() -> None:
    assert build_automatic_recall_context([]) is None
```

- [ ] **Step 3: Run the helper tests and confirm the missing module failure**

```bash
uv run pytest tests/homemaster/memory/test_automatic_recall.py -q
```

Expected: collection fails with `ModuleNotFoundError` for `homemaster.memory.automatic_recall`.

- [ ] **Step 4: Implement the pure helper module**

Create the module with imports for `json`, `Sequence`, `Any`, `Message`, and
`TaskStateStore`. Implement `build_automatic_recall_query()`,
`build_mindmemos_request_context()`, and `build_automatic_recall_context()` with the
exact signatures exercised by Steps 1 and 2.

`build_automatic_recall_query()` must return the current message byte-for-byte only when
`messages` is empty, which is the new-Session case. When prior messages exist, this invocation
is post-Compact: strip the newest exact wrapper created by `build_compaction_summary_message()`
when present, use an empty summary string for a micro-compaction that produced no summary
message, serialize current TaskState with `ensure_ascii=False`, `sort_keys=True`,
`separators=(",", ":")`, and render all three exact spec labels.

`build_mindmemos_request_context()` must set `account_id`, `project_id`, and `user_id` from the authoritative `tenant_id`; set `session_id` exactly; set `api_key_uuid="embedded-local"`, `app_id="homemaster"`, and `agent_id="homemaster"`.

`build_automatic_recall_context()` must serialize each MindMemOS result without `get_raw()` using these keys when present:

```python
{
    "id": item.id,
    "memory": item.memory,
    "memory_type": item.memory_type,
    "last_update_at": item.last_update_at,
    "event_time": item.event_time,
    "source_timestamp": item.source_timestamp,
    "lineage": item.lineage.model_dump(mode="json") if item.lineage else None,
}
```

Omit `None` fields. Wrap the stable JSON array with the exact `<memory-context>` wording from the spec. Return `None` for no memories.

- [ ] **Step 5: Run and commit**

```bash
uv run pytest tests/homemaster/memory/test_automatic_recall.py -q
git add src/homemaster/memory/automatic_recall.py tests/homemaster/memory/test_automatic_recall.py
git commit -m "feat(memory): add deterministic automatic recall context"
```

Expected: all helper tests pass before commit.

## Task 2: Persist the Session recall latch

**Files:**
- Modify: `src/homemaster/application/session.py:83,552,570`
- Modify: `tests/homemaster/application/test_session_manager.py`

- [ ] **Step 1: Write failing new/resume/legacy tests**

```python
@pytest.mark.asyncio
async def test_new_session_starts_with_recall_required() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("new-recall")
    assert runtime.require_recall is True
    async with manager.turn("new-recall") as (_, generation, _):
        assert runtime.consume_recall(generation) is True
        assert runtime.consume_recall(generation) is False
        runtime.require_recall_after_compaction(generation)
        assert runtime.require_recall is True


@pytest.mark.asyncio
async def test_recall_latch_round_trips_snapshot(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("persist-recall")
    async with manager.turn("persist-recall") as (_, generation, _):
        assert runtime.consume_recall(generation) is True
        await manager.save("persist-recall", generation=generation)
    restored = await SessionManager(session_root=tmp_path).resume("persist-recall")
    assert restored.require_recall is False
```

Also create a legacy snapshot payload with no `require_recall` key and assert `_runtime_from_snapshot()` restores `False`.

- [ ] **Step 2: Run and confirm failure**

```bash
uv run pytest tests/homemaster/application/test_session_manager.py -q
```

Expected: new tests fail on missing field/methods.

- [ ] **Step 3: Implement the generation-fenced latch**

Add to `SessionRuntime`:

```python
require_recall: bool = True

def consume_recall(self, generation: int) -> bool:
    with self.state_lock:
        self.assert_generation(generation)
        required = self.require_recall
        self.require_recall = False
        return required

def require_recall_after_compaction(self, generation: int) -> None:
    with self.state_lock:
        self.assert_generation(generation)
        self.require_recall = True
```

Add `"require_recall": runtime.require_recall` to `_snapshot_payload()`. Restore it in `_runtime_from_snapshot()` with `False` only when the key is absent, so old snapshots do not unexpectedly recall after upgrade.

- [ ] **Step 4: Run and commit**

```bash
uv run pytest tests/homemaster/application/test_session_manager.py -q
git add src/homemaster/application/session.py tests/homemaster/application/test_session_manager.py
git commit -m "feat(session): persist automatic recall latch"
```

Expected: all Session Manager tests pass.

## Task 3: Inject one run-scoped Memory Context

**Files:**
- Modify: `src/homemaster/agent/context.py:325-575`
- Modify: `tests/homemaster/application/test_context_assembler_scope.py`

- [ ] **Step 1: Write failing injection and isolation tests**

```python
@pytest.mark.asyncio
async def test_automatic_memory_context_is_prelude_not_history() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("recall-context")
    runtime.session.append(UserMessage.from_text("当前任务"))
    assembler = _assembler()
    assembler.bind_automatic_memory_context(
        "<memory-context>\n[{\"id\":\"memory-1\"}]\n</memory-context>"
    )
    composed = assembler.prepare(
        session=runtime.session,
        agent_state=runtime.agent_state,
        task_state_store=runtime.task_state_store,
        tools=[],
    )
    assert _text(composed).count("<memory-context>") == 1
    assert runtime.session.messages[-1].content[0].text == "当前任务"
    assert composed.messages[-1].content[0].text == "当前任务"


def test_automatic_context_does_not_leak_between_assemblers() -> None:
    first = _assembler()
    second = _assembler()
    first.bind_automatic_memory_context("<memory-context>first</memory-context>")
    assert first._automatic_memory_context is not None
    assert second._automatic_memory_context is None
```

- [ ] **Step 2: Run and confirm the missing method failure**

```bash
uv run pytest tests/homemaster/application/test_context_assembler_scope.py -q
```

- [ ] **Step 3: Implement the binding in sync and async assembly**

Initialize `self._automatic_memory_context: str | None = None`, add:

```python
def bind_automatic_memory_context(self, text: str | None) -> None:
    normalized = text.strip() if isinstance(text, str) else ""
    self._automatic_memory_context = normalized or None
```

In both `prepare()` and `aprepare()`, append this value to `prelude_texts` before runtime evidence and other providers. Do not append it to `session`; continue using `_render_messages()`.

- [ ] **Step 4: Run and commit**

```bash
uv run pytest tests/homemaster/application/test_context_assembler_scope.py tests/homemaster/test_context_assembler.py -q
git add src/homemaster/agent/context.py tests/homemaster/application/test_context_assembler_scope.py
git commit -m "feat(context): inject run-scoped recalled memories"
```

Expected: selected tests pass.

## Task 4: Search before the first Provider request

**Files:**
- Modify: `src/homemaster/application/runtime.py:347-455`
- Modify: `src/homemaster/events/runtime_events.py:15-39`
- Modify: `tests/homemaster/application/test_application_runtime.py`

- [ ] **Step 1: Add recording Search and Provider fakes**

```python
class _AutomaticRecallStore(EmbeddedMindMemOS):
    def __init__(self, order, *, memories=(), error=None) -> None:
        self.order = order
        self.memories = list(memories)
        self.error = error
        self.calls = []

    async def search(self, query, context, **kwargs):
        self.order.append("search")
        self.calls.append((query, context, kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status="ok", memories=list(self.memories))


class _AutomaticRecallTransport(_FakeTransport):
    def __init__(self, order) -> None:
        super().__init__([_text("完成")])
        self.order = order

    async def stream(self, *args, **kwargs):
        self.order.append("provider")
        async for delta in super().stream(*args, **kwargs):
            yield delta
```

- [ ] **Step 2: Write the failing Provider-boundary test**

Run a new Session with native `profile` and `experience` `MemorySearchItem` results. Assert:

```python
assert order == ["search", "provider"]
query, context, kwargs = store.calls[0]
assert query == request.text
assert context.account_id == request.permission_subject.tenant_id
assert context.session_id == request.session_id
assert kwargs == {
    "top_k": 3,
    "search_pipeline": "vanilla",
    "rerank": False,
    "filters": None,
}
first_messages = transport.calls[0]["messages"]
request_text = _request_text(first_messages)
assert "profile-1" in request_text
assert "experience-1" in request_text
assert request_text.count("<memory-context>") == 1
assert first_messages[-1].content[0].text == request.text
assert all(message.role != "tool" for message in first_messages)
assert runtime.require_recall is False
assert all(not getattr(message, "tool_calls", []) for message in runtime.session.messages)
```

- [ ] **Step 3: Write empty/error/unavailable-service tests**

For empty results, `RuntimeError("recall backend failed")`, and missing `mindmemos` service, assert the Provider still returns normally, no Memory Context is injected, and the latch becomes false. For the error case assert one internal `memory.automatic_recall` event with `status="error"`; do not expose it through public projection.

- [ ] **Step 4: Run and confirm failures**

```bash
uv run pytest tests/homemaster/application/test_application_runtime.py -k automatic_recall -q
```

- [ ] **Step 5: Implement direct automatic recall**

Add `memory.automatic_recall` to `KNOWN_EVENT_TYPES`. Add a private `ApplicationRuntime._automatic_recall()` with parameters `request`, `runtime`, `generation`, `run_id`, cloned `task_state_store`, and `event_sink`.

Its exact flow:

```python
if not runtime.consume_recall(generation):
    return None
service = settings.application_services.get("mindmemos")
if service has no callable search:
    emit status="unavailable", count=0
    return None
query = build_automatic_recall_query(
    current_user_message=request.text,
    messages=runtime.session.messages,
    task_state_store=task_state_store,
)
context = build_mindmemos_request_context(
    request_id=f"automatic-recall:{run_id}",
    tenant_id=request.permission_subject.tenant_id,
    session_id=runtime.session.session_id,
)
try:
    result = await service.search(
        query,
        context,
        top_k=3,
        search_pipeline="vanilla",
        rerank=False,
        filters=None,
    )
except ordinary Exception as exc:
    emit status="error", count=0, error=str(exc)
    return None
memories = list(result.memories)[:3]
emit status="ok" or "empty", count=len(memories)
return build_automatic_recall_context(memories)
```

Do not catch `asyncio.CancelledError` or `SessionGenerationError`. Call this after cloning TaskState and creating the per-run assembler, but before `AgentRuntime.run()`. Bind a non-empty result via `assembler.bind_automatic_memory_context()`.

Do not add a numeric timeout. Move construction of `ApplicationToolExecutor` before
automatic recall and pass its existing `executor.deadline` into `_automatic_recall()`.
Add a private `AutomaticRecallRunDeadlineExceeded(TimeoutError)` and
`_await_with_remaining_deadline(awaitable, deadline)` helper in
`application/runtime.py`. Call `deadline.remaining_s()` once, await directly when it returns
`None`, raise `AutomaticRecallRunDeadlineExceeded` when it is non-positive, otherwise use
`asyncio.timeout(remaining)` and translate only that context manager's expiry into
`AutomaticRecallRunDeadlineExceeded`. This shares the executor's existing absolute expiry
without adding a recall-specific duration. Pass the same `executor.deadline` to
`AgentRuntime.run()` as today.

Re-raise `AutomaticRecallRunDeadlineExceeded` before the ordinary `Exception` handler: the whole
Run deadline has expired, so no Provider time remains. A `TimeoutError` raised directly by the
MindMemOS service before the Run deadline is treated like any other best-effort recall backend
error and the Provider request continues.

After `_automatic_recall()` returns normally (success, empty, unavailable, ordinary error, or
existing-deadline error), call `_save_if_configured(session_id, generation)` before
`AgentRuntime.run()`. This durably records the consumed latch before the first Provider request.
Do not perform this save if cancellation or stale-generation fencing aborts the recall await.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/homemaster/application/test_application_runtime.py -k automatic_recall -q
git add src/homemaster/application/runtime.py src/homemaster/events/runtime_events.py tests/homemaster/application/test_application_runtime.py
git commit -m "feat(runtime): recall memories before first model request"
```

Expected: success, empty, error, and unavailable tests pass.

## Task 5: Re-arm after manual, threshold, and reactive Compact

**Files:**
- Modify: `src/homemaster/agent/generic_runtime.py:115-305`
- Modify: `src/homemaster/application/runtime.py:418-438,536-580`
- Modify: `tests/homemaster/test_generic_agent_runtime.py`
- Modify: `tests/homemaster/application/test_application_runtime.py`

- [ ] **Step 1: Write failing callback tests**

Add `on_compaction` tests around existing compacting fixtures:

```python
notices = []
result = asyncio.run(
    runtime.run(
        compactable_session,
        "continue",
        force_compact="manual",
        on_compaction=lambda metrics: notices.append(metrics.compaction_kind),
    )
)
assert result.status == "replied"
assert notices == ["manual_summary"]
```

Add a paired no-op Compact test and assert `notices == []`.

- [ ] **Step 2: Run and confirm the unknown argument failure**

```bash
uv run pytest tests/homemaster/test_generic_agent_runtime.py -k compaction -q
```

- [ ] **Step 3: Implement the narrow callback**

Add to `AgentRuntime.run()`:

```python
on_compaction: Callable[[ContextMetrics], Any] | None = None,
```

Inside the existing `if composed.metrics.compaction_triggered:` block, after emitting
`context.compaction`, invoke and await the callback when necessary. Do not call it for no-op
compaction. Invoke the callback before the existing `save_snapshot()` call. This one point
covers threshold, forced, and reactive/aggressive paths.

- [ ] **Step 4: Wire Application-level re-arming**

Define and pass an async Application callback:

```python
async def rearm_recall_after_compaction(_metrics: ContextMetrics) -> None:
    runtime.require_recall_after_compaction(generation)
    await self._save_if_configured(session_id, generation)
```

Pass `rearm_recall_after_compaction` as `on_compaction` to `AgentRuntime.run()`. The immediate
save makes a re-armed latch survive cancellation or Provider failure later in the same run. In
`ApplicationRuntime.compact()`, call `runtime.require_recall_after_compaction(generation)` only
when `composed.metrics.compaction_triggered` is true, before the existing snapshot save.

- [ ] **Step 5: Write manual and reactive timing tests**

Manual path assertions:

```python
assert compact.triggered is True
assert store.search_call_count == 1  # compact itself did not search
assert runtime.require_recall is True
```

On the next real message, assert Search count becomes two and the Query contains the three spec sections. Add a no-op Compact case that does not change a false latch.

Reactive path assertions:

```python
assert order == [
    "initial-auto-search",
    "provider-context-length-error",
    "provider-reactive-retry",
]
assert store.search_call_count == 1
assert runtime.require_recall is True
```

Then run the next user message and assert Search count becomes two.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/homemaster/test_generic_agent_runtime.py -k compaction -q
uv run pytest tests/homemaster/application/test_application_runtime.py -k 'automatic_recall or compact' -q
git add src/homemaster/agent/generic_runtime.py src/homemaster/application/runtime.py tests/homemaster/test_generic_agent_runtime.py tests/homemaster/application/test_application_runtime.py
git commit -m "feat(runtime): rearm recall after context compaction"
```

Expected: all focused compaction tests pass.

## Task 6: Lifecycle, isolation, real smoke measurement, and release gates

**Files:**
- Modify: `tests/homemaster/application/test_application_runtime.py`
- Modify: `tests/homemaster/application/test_session_manager.py`
- Create: `tests/homemaster/memory/test_automatic_recall_integration.py`
- Modify: `plan/V2.5/automatic-recall-implementation-plan.md` only to record measured results

- [ ] **Step 1: Add repeated-turn and resume tests**

Run two messages in one Session and assert only the first triggers automatic Search and only its first Provider request contains `<memory-context>`. Save/resume both `require_recall=True` and `False` snapshots and assert the resumed next message searches only in the true case.

- [ ] **Step 2: Add authoritative tenant isolation test**

Use permission subjects whose `subject_id` differs from `tenant_id`. Assert each `MemoryRequestContext.account_id`, `project_id`, and `user_id` equals the exact tenant, never the subject, and Session IDs never cross.

- [ ] **Step 3: Prove Agent Search remains independent**

Adapt the existing `_MemoryRecallTransport` test. The store must record two distinct calls:

```python
assert automatic_call.kwargs["filters"] is None
assert automatic_call.kwargs["top_k"] == 3
assert agent_call.kwargs["filters"] == {"mem_type": "fact"}
```

Assert exactly one real `ToolResultMessage(name="mindmemos_search")` appears in the second Provider request, belonging only to the Agent call.

- [ ] **Step 4: Prove Provider retry does not repeat recall**

Use the existing retryable transport fixture. Assert Search count is one across two Provider attempts and the Memory Context text is byte-identical in both captured request bodies.

- [ ] **Step 5: Run the complete focused suite**

```bash
uv run pytest tests/homemaster/application/test_application_runtime.py tests/homemaster/application/test_session_manager.py tests/homemaster/application/test_context_assembler_scope.py tests/homemaster/memory/test_automatic_recall.py tests/homemaster/test_generic_agent_runtime.py -q
```

Expected: zero failures.

- [ ] **Step 6: Add and run an opt-in real MindMemOS smoke benchmark**

The integration test must use the existing supported embedded composition, insert at least one `fact` and one native `experience`, call:

```python
await store.search(
    query,
    context,
    top_k=3,
    search_pipeline="vanilla",
    rerank=False,
    filters=None,
)
```

Assert at most three results and tenant isolation. Measure one cold and five warm calls with `time.perf_counter()` and print a single JSON record containing `cold_ms`, `warm_ms`, and `result_counts`. Do not enforce a latency threshold.

Run it using the exact environment command documented by the existing MindMemOS integration tests. If a real dependency is unavailable, record `NOT RUN` and the missing dependency; do not replace it with a fake.

- [ ] **Step 7: Record measured latency without choosing a timeout**

After the real run, append one subsection containing the exact backend/config identifier, the
measured cold value, all five warm values, computed warm p50 and maximum, followed by the
sentence `Timeout decision: deferred; no recall-specific timeout added in V2.5.` If the run
cannot start, append `NOT RUN:` followed by the exact dependency or service error emitted by
the command. Do not add synthetic measurements.

- [ ] **Step 8: Run static and full verification**

```bash
uv run ruff check src/homemaster tests/homemaster
uv run pytest -q
git diff --check
git status --short
```

Expected: Ruff exits 0, full offline pytest has zero failures, `git diff --check` has no output, and status contains no unplanned generated files.

- [ ] **Step 9: Audit all fifteen spec acceptance criteria**

Map every item in `plan/V2.5/plan.md` to an exact passing test name. Criterion 7 requires success, empty, error, unavailable-service, and existing-deadline coverage. Criterion 15 requires captured `filters is None` plus a native/unknown `mem_type` visible in the first Provider request.

- [ ] **Step 10: Commit final coverage and measurement**

```bash
git add tests/homemaster/application/test_application_runtime.py tests/homemaster/application/test_session_manager.py tests/homemaster/memory/test_automatic_recall_integration.py plan/V2.5/automatic-recall-implementation-plan.md
git commit -m "test(runtime): verify automatic recall lifecycle"
```

If the real benchmark was not run, include the recorded missing dependency rather than fabricated numbers.

## Completion criteria

- All fifteen acceptance criteria in `plan/V2.5/plan.md` map to passing tests.
- The first real Provider request contains the actual Top-3 Memory Context.
- Automatic recall leaves no fake tool call or ToolResult in `session.messages`.
- New Session and post-Compact latches survive snapshot/resume.
- Every real compaction path re-arms only the next real user run.
- Automatic Search uses `filters=None` and preserves native MindMemOS types.
- Agent-initiated `mindmemos_search` behavior remains unchanged.
- No recall-specific numeric timeout is introduced.
- Ruff and the full offline test suite pass.
- Real latency is measured and recorded, or explicitly marked `NOT RUN` with the missing dependency.

## Measured real MindMemOS smoke result

Backend/config: `embedded-mindmemos-managed-local-real-config`, using the private
`config/homemaster.yaml`, HomeMaster-managed local Neo4j, persistent local Qdrant, and the configured
SiliconFlow embedding endpoint. The search returned status `ok` on all six calls with result counts
`[0, 0, 0, 0, 0, 0]`; every individual call therefore satisfied the Top-3 bound and tenant/session
identity assertions.

- Cold: `6088.888904079795 ms`
- Warm: `[274.6669854968786, 241.82595405727625, 228.53021509945393, 443.8064293935895, 242.58476588875055] ms`
- Warm p50: `242.58476588875055 ms`
- Warm maximum: `443.8064293935895 ms`

Timeout decision: deferred; no recall-specific timeout added in V2.5.

## Acceptance criteria audit

1. `test_automatic_recall_precedes_first_provider_request`
2. `test_new_session_query_is_exact_user_text`
3. `test_manual_compact_rearms_only_the_next_real_user_run` and
   `test_runtime_reports_only_actual_compaction[True]`
4. `test_manual_compact_rearms_only_the_next_real_user_run` and
   `test_post_compact_query_has_stable_sections`
5. `test_automatic_recall_precedes_first_provider_request`
6. `test_automatic_recall_precedes_first_provider_request`
7. `test_automatic_recall_best_effort_outcomes_do_not_block_provider`,
   `test_blank_run_request_is_rejected_before_automatic_recall`, and
   `test_automatic_recall_uses_existing_run_deadline`
8. `test_new_session_starts_with_recall_required`
9. `test_recall_latch_round_trips_snapshot`,
   `test_required_recall_latch_round_trips_snapshot`, and
   `test_legacy_snapshot_without_recall_latch_restores_false`
10. `test_new_session_starts_with_recall_required`
11. `test_automatic_recall_runs_once_per_session_and_survives_resume`
12. `test_runtime_projects_memory_search_records_into_model_tool_content`
13. Existing Session finalization and compact integration tests
14. `test_runtime_projects_memory_search_records_into_model_tool_content`
15. `test_automatic_recall_precedes_first_provider_request`,
    `test_context_preserves_unknown_native_memory_types`, and
    `test_runtime_projects_memory_search_records_into_model_tool_content`
