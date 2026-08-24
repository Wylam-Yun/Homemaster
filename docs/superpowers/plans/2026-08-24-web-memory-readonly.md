# Web Read-Only Memory Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chinese, read-only memory management page to the existing loopback Web Console, with complete active/archived counts, exact session grouping, search/filtering, details, and version history.

**Architecture:** Extend the application-owned MindMemOS boundary with typed cursor-based reads and add a pure-read SessionManager message API. A new `MemoryManagementService` composes those sources into tenant-scoped snapshot/history DTOs; the existing FastAPI adapter exposes only GET routes, and React renders/filter/groups the returned snapshot without any mutation controls.

**Tech Stack:** Python 3.11, asyncio, Pydantic, FastAPI, embedded MindMemOS/Qdrant/Neo4j, pytest, React 18, TypeScript, Vitest, Testing Library, Vite.

---

## File structure

- `third_party/MindMemOS/src/mindmemos/mindmemos/typing/memory.py`: add the missing typed archive timestamp.
- `third_party/MindMemOS/src/mindmemos/mindmemos/mappers/result.py`: map `status_changed_at` from existing Qdrant payloads.
- `src/homemaster/application/session.py`: expose a message read that never resumes or caches a historical runtime.
- `src/homemaster/memory/mindmemos_runtime.py`: expose cursor-complete raw memory reads and reuse existing history reads.
- `src/homemaster/memory/management.py`: own grouping, counts, title resolution, safe structured projection, and history DTOs.
- `src/homemaster/web/schemas.py`: define stable browser response DTOs.
- `src/homemaster/web/app.py`: add two GET-only routes.
- `src/homemaster/web/serve.py`: explicitly inject the same read service in Home and ALFWorld compositions.
- `web/src/api/http.ts`: define memory response types and GET clients.
- `web/src/components/MemoryPage.tsx`: render stats, filters, tabs, and session accordions.
- `web/src/components/MemoryDetailDialog.tsx`: render one read-only record and lazy history.
- `web/src/App.tsx`: add top-level conversation/memory navigation and collapsible session history.
- Scoped CSS/test files live beside their components; global shell layout changes stay in `web/src/styles.css`.

### Task 1: Preserve the archive timestamp in typed MindMemOS views

**Files:**
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/typing/memory.py:582-612`
- Modify: `third_party/MindMemOS/src/mindmemos/mindmemos/mappers/result.py:92-126`
- Test: `third_party/MindMemOS/tests/mappers/test_mappers.py`

- [ ] **Step 1: Write the failing mapper test**

```python
def test_memory_view_preserves_status_changed_at() -> None:
    changed = datetime(2026, 8, 24, 8, 30, tzinfo=UTC)
    view = to_memory_view(
        {
            "memory_id": "memory-01",
            "project_id": "local",
            "content": "archived content",
            "mem_type": "fact",
            "status": "archived",
            "status_changed_at": changed.isoformat(),
        }
    )
    assert view.status_changed_at == changed
```

- [ ] **Step 2: Run the test and verify the missing-field failure**

Run: `.runtime/venv/bin/python -m pytest third_party/MindMemOS/tests/mappers/test_mappers.py -k status_changed_at -v`

Expected: FAIL because `MemoryView` has no `status_changed_at` attribute.

- [ ] **Step 3: Add the minimal typed field and mapper projection**

```python
class MemoryView(BaseModel):
    # existing fields stay unchanged
    created_at: datetime | None = None
    update_at: datetime | None = None
    status_changed_at: datetime | None = None
```

```python
return MemoryView(
    # existing keyword arguments stay unchanged
    created_at=_dt_from_payload(payload.get("created_at")),
    update_at=_dt_from_payload(payload.get("update_at")),
    status_changed_at=_dt_from_payload(payload.get("status_changed_at")),
)
```

- [ ] **Step 4: Run focused and adjacent MindMemOS tests**

Run: `.runtime/venv/bin/python -m pytest third_party/MindMemOS/tests/mappers/test_mappers.py -q`

Expected: PASS.

- [ ] **Step 5: Commit only the vendored typed projection**

```bash
git add third_party/MindMemOS/src/mindmemos/mindmemos/typing/memory.py third_party/MindMemOS/src/mindmemos/mindmemos/mappers/result.py third_party/MindMemOS/tests/mappers/test_mappers.py
git commit -m "fix(memory): expose archive timestamps in memory views"
```

### Task 2: Add a non-activating SessionManager message read

**Files:**
- Modify: `src/homemaster/application/session.py:380-575`
- Test: `tests/homemaster/application/test_session_manager.py`

- [ ] **Step 1: Write failing tests for persisted and active sessions**

```python
@pytest.mark.asyncio
async def test_read_session_messages_does_not_resume_persisted_runtime(tmp_path) -> None:
    writer = SessionManager(session_root=tmp_path)
    runtime = await writer.open_or_resume("session-01")
    runtime.session.append(UserMessage.from_text("first request"))
    await writer.save("session-01")

    reader = SessionManager(session_root=tmp_path)
    before_ids = tuple(item.session.session_id for item in reader.sessions)
    messages = reader.read_session_messages("session-01")

    assert messages[0].content[0].text == "first request"
    assert tuple(item.session.session_id for item in reader.sessions) == before_ids == ()


@pytest.mark.asyncio
async def test_read_session_messages_returns_copy_for_active_runtime() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("session-01")
    runtime.session.append(UserMessage.from_text("first request"))

    messages = manager.read_session_messages("session-01")
    assert messages[0] is not runtime.session.messages[0]
    assert messages[0].content[0].text == "first request"
```

- [ ] **Step 2: Run the tests and verify the method is missing**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/application/test_session_manager.py -k read_session_messages -v`

Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'read_session_messages'`.

- [ ] **Step 3: Implement the narrow pure-read method**

```python
def read_session_messages(self, session_id: str) -> tuple[Message, ...]:
    _validate_session_id(session_id)
    runtime = self._sessions.get(session_id)
    if runtime is not None:
        with runtime.state_lock:
            return tuple(copy.deepcopy(runtime.session.messages))
    if self._backend is None:
        raise KeyError(f"unknown session: {session_id}")
    try:
        snapshot = self._backend.load(session_id)
    except (FileNotFoundError, KeyError) as exc:
        raise KeyError(f"unknown session: {session_id}") from exc
    session, _agent_state, _task_state = AgentSession.from_snapshot_dict(snapshot.payload)
    return tuple(copy.deepcopy(session.messages))
```

Add `import copy`; the existing `Message` import already supplies the return type. Do not call `_runtime_from_snapshot`, `resume`, `open_or_resume`, or `save`.

- [ ] **Step 4: Run focused and complete session manager tests**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/application/test_session_manager.py tests/homemaster/application/test_session_file_backend.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the pure-read boundary**

```bash
git add src/homemaster/application/session.py tests/homemaster/application/test_session_manager.py
git commit -m "feat(session): read persisted messages without resume"
```

### Task 3: Read every active and archived memory page

**Files:**
- Modify: `src/homemaster/memory/mindmemos_runtime.py:358-414,1236-1296`
- Test: `tests/homemaster/memory/test_mindmemos_runtime.py`

- [ ] **Step 1: Write failing cursor and de-duplication tests**

```python
@pytest.mark.asyncio
async def test_list_raw_memories_consumes_all_cursors_and_filters_status() -> None:
    pages = {
        None: ([memory("m1", "active"), memory("m2", "archived")], "cursor-2"),
        "cursor-2": ([memory("m2", "archived"), memory("m3", "active")], None),
    }
    reader = FakeReader(pages)
    runtime = object.__new__(EmbeddedMindMemOS)
    runtime._reader = reader

    rows = await runtime.list_raw_memories(MEMORY_CONTEXT)

    assert [row.memory_id for row in rows] == ["m1", "m2", "m3"]
    assert reader.cursors == [None, "cursor-2"]


@pytest.mark.asyncio
async def test_list_raw_memories_rejects_repeated_cursor() -> None:
    reader = FakeReader({None: ([memory("m1", "active")], "same"), "same": ([], "same")})
    runtime = object.__new__(EmbeddedMindMemOS)
    runtime._reader = reader
    with pytest.raises(RuntimeError, match="cursor repeated"):
        await runtime.list_raw_memories(MEMORY_CONTEXT)
```

The local `memory()` helper must return a `MemoryView`; `FakeReader.list_memories()` records cursors and returns the supplied page.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_mindmemos_runtime.py -k list_raw_memories -v`

Expected: FAIL because `list_raw_memories` does not exist.

- [ ] **Step 3: Implement cursor-complete, project-scoped reads**

```python
async def list_raw_memories(
    self,
    context: Any,
    *,
    statuses: frozenset[str] = frozenset({"active", "archived"}),
    page_size: int = 50,
) -> list[Any]:
    if self._reader is None:
        raise RuntimeError("embedded MindMemOS is not started")
    cursor: Any | None = None
    seen_cursors: set[str] = set()
    seen_ids: set[str] = set()
    rows: list[Any] = []
    while True:
        page, next_cursor = await self._reader.list_memories(
            context, limit=page_size, cursor=cursor
        )
        for memory in page:
            if memory.status in statuses and memory.memory_id not in seen_ids:
                seen_ids.add(memory.memory_id)
                rows.append(memory)
        if next_cursor is None:
            return rows
        cursor_key = repr(next_cursor)
        if cursor_key in seen_cursors:
            raise RuntimeError("MindMemOS list cursor repeated")
        seen_cursors.add(cursor_key)
        cursor = next_cursor
```

The request context remains the only project/tenant filter; do not accept project IDs from the browser.

- [ ] **Step 4: Run runtime memory tests**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_mindmemos_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the raw-list boundary**

```bash
git add src/homemaster/memory/mindmemos_runtime.py tests/homemaster/memory/test_mindmemos_runtime.py
git commit -m "feat(memory): list all raw memory pages"
```

### Task 4: Build the tenant-scoped MemoryManagementService

**Files:**
- Create: `src/homemaster/memory/management.py`
- Create: `tests/homemaster/memory/test_management.py`

- [ ] **Step 1: Write failing snapshot tests**

```python
@pytest.mark.asyncio
async def test_snapshot_counts_groups_titles_and_unassigned() -> None:
    mindmemos = FakeMindMemOS(
        [
            raw("m1", session_id="s1", status="active", content="alpha"),
            raw("m2", session_id="s1", status="archived", content="beta"),
            raw("m3", session_id=None, status="active", content="gamma"),
        ]
    )
    sessions = FakeSessions({"s1": [UserMessage.from_text("first user request")]})
    service = MemoryManagementService(mindmemos=mindmemos, sessions=sessions)

    snapshot = await service.snapshot(tenant_id="local")

    assert snapshot.stats == MemoryStats(2, 1, 3, 1)
    assert [(group.session_id, group.title) for group in snapshot.groups] == [
        ("s1", "first user request"),
        (None, "未关联会话"),
    ]


@pytest.mark.asyncio
async def test_snapshot_keeps_content_but_hides_invalid_record_json() -> None:
    item = raw(
        "m1",
        session_id="s1",
        status="active",
        content="visible content",
        metadata={"request_metadata": {"record_json": "not-json"}},
    )
    service = MemoryManagementService(FakeMindMemOS([item]), FakeSessions({}))
    memory = (await service.snapshot(tenant_id="local")).groups[0].memories[0]
    assert memory.content == "visible content"
    assert memory.record is None
    assert memory.structure_status == "invalid"
```

Also add tests for fallback title, archive reason, native unknown type preservation, stable sort, and history scope forwarding.

- [ ] **Step 2: Run the new test file and verify import failure**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_management.py -v`

Expected: FAIL because `homemaster.memory.management` does not exist.

- [ ] **Step 3: Implement focused immutable DTOs and service methods**

```python
@dataclass(frozen=True)
class MemoryStats:
    active_count: int
    archived_count: int
    total_count: int
    session_group_count: int


@dataclass(frozen=True)
class ManagedMemory:
    memory_id: str
    content: str
    memory_type: str
    status: str
    session_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    archived_at: datetime | None
    archive_reason: str | None
    record: dict[str, object] | None
    structure_status: Literal["plain", "valid", "invalid"]
    has_history: bool
```

```python
class MemoryManagementService:
    def __init__(self, mindmemos: EmbeddedMindMemOS, sessions: SessionManager) -> None:
        self._mindmemos = mindmemos
        self._sessions = sessions

    async def snapshot(self, *, tenant_id: str) -> MemorySnapshot:
        context = build_mindmemos_request_context(
            request_id=f"web-memory-{uuid4().hex}",
            tenant_id=tenant_id,
            session_id="web-memory-management",
        )
        raw_rows = await self._mindmemos.list_raw_memories(context)
        items = tuple(_project_memory(row) for row in raw_rows)
        return _group_snapshot(items, self._title_for_session)

    async def history(self, memory_id: str, *, tenant_id: str) -> tuple[ManagedMemory, ...]:
        _validate_memory_id(memory_id)
        context = build_mindmemos_request_context(
            request_id=f"web-memory-history-{uuid4().hex}",
            tenant_id=tenant_id,
            session_id="web-memory-management",
        )
        versions = await self._mindmemos.get_history(memory_id, context)
        if not versions:
            raise MemoryNotFoundError(memory_id)
        return tuple(_project_memory(row) for row in versions)
```

`_project_memory()` must take `archive_reason` only from `metadata["delete_reason"]`, validate `record_json` with `MEMORY_RECORD_ADAPTER`, and never return invalid raw JSON. `_title_for_session()` calls only `read_session_messages()`, selects the first `UserMessage`, and joins its text `ContentBlock.text` values; `UserMessage` has no `.text` shortcut.

- [ ] **Step 4: Run management and adjacent memory tests**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/memory/test_management.py tests/homemaster/memory/test_mindmemos_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the management service**

```bash
git add src/homemaster/memory/management.py tests/homemaster/memory/test_management.py
git commit -m "feat(memory): compose read-only management snapshots"
```

### Task 5: Expose GET-only Web routes in both compositions

**Files:**
- Modify: `src/homemaster/web/schemas.py`
- Modify: `src/homemaster/web/app.py:40-290`
- Modify: `src/homemaster/web/serve.py:30-90`
- Modify: `tests/homemaster/web/test_app.py`
- Modify: `tests/homemaster/web/test_serve.py`

- [ ] **Step 1: Write failing API and composition tests**

```python
def test_memory_snapshot_and_history_are_get_only() -> None:
    service = FakeMemoryManagementService()
    app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=service,
    )
    with TestClient(app) as client:
        snapshot = client.get("/api/memories")
        history = client.get("/api/memories/memory-01/history")
        assert snapshot.status_code == 200
        assert snapshot.json()["stats"]["active_count"] == 2
        assert history.status_code == 200
        for method in (client.post, client.put, client.patch, client.delete):
            assert method("/api/memories").status_code == 405
```

Add serve tests asserting Home and ALFWorld pass the explicit service created from `bundle.mindmemos` and `bundle.application.session_manager`.

- [ ] **Step 2: Run focused Web tests and verify route/signature failure**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/web/test_app.py tests/homemaster/web/test_serve.py -k memory -v`

Expected: FAIL because the routes and injection parameter do not exist.

- [ ] **Step 3: Add response schemas, routes, and explicit injection**

```python
@app.get("/api/memories")
async def memories() -> object:
    service = app.state.memory_management_service
    if service is None:
        return _error(503, "memory_unavailable", "Memory service is unavailable.", retryable=True)
    try:
        snapshot = await service.snapshot(tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id)
    except Exception:
        return _error(503, "memory_read_failed", "Memory data could not be read.", retryable=True)
    return MemorySnapshotResponse.from_domain(snapshot).model_dump(mode="json")


@app.get("/api/memories/{memory_id}/history")
async def memory_history(memory_id: str) -> object:
    service = app.state.memory_management_service
    if service is None:
        return _error(503, "memory_unavailable", "Memory service is unavailable.", retryable=True)
    try:
        versions = await service.history(memory_id, tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id)
    except MemoryNotFoundError:
        return _error(404, "memory_not_found", "The memory does not exist.", retryable=False)
    return MemoryHistoryResponse.from_domain(memory_id, versions).model_dump(mode="json")
```

Extend `create_web_app(..., memory_management_service: MemoryManagementService | None = None)` and store it on `app.state`. In `serve.py`, create the service from the base bundle before wrapping with `AlfworldGatewayApplication`, then pass the same service explicitly.

- [ ] **Step 4: Run all Python Web tests**

Run: `.runtime/venv/bin/python -m pytest tests/homemaster/web -q`

Expected: PASS.

- [ ] **Step 5: Commit Web API wiring only**

```bash
git add src/homemaster/web/schemas.py src/homemaster/web/app.py src/homemaster/web/serve.py tests/homemaster/web/test_app.py tests/homemaster/web/test_serve.py
git commit -m "feat(web): expose read-only memory endpoints"
```

### Task 6: Add typed browser HTTP clients

**Files:**
- Modify: `web/src/api/http.ts`
- Modify: `web/src/api/http.test.ts`

- [ ] **Step 1: Write failing URL and response-shape tests**

```typescript
it('reads memory snapshots and encoded history ids', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(memorySnapshot), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(memoryHistory), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const api = new HomeMasterApi()

  await api.memories()
  await api.memoryHistory('memory/01')

  expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/memories', expect.anything())
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/memories/memory%2F01/history', expect.anything())
})
```

- [ ] **Step 2: Run the test and verify missing methods**

Run: `cd web && npm test -- src/api/http.test.ts`

Expected: FAIL because `memories()` and `memoryHistory()` do not exist.

- [ ] **Step 3: Add exact TypeScript DTOs and GET methods**

```typescript
export type MemoryStats = {
  active_count: number; archived_count: number; total_count: number; session_group_count: number
}
export type ManagedMemory = {
  memory_id: string; content: string; memory_type: string; memory_type_label: string
  status: 'active' | 'archived'; session_id: string | null
  created_at: string | null; updated_at: string | null; archived_at: string | null
  archive_reason: string | null; record: Record<string, unknown> | null
  structure_status: 'plain' | 'valid' | 'invalid'; has_history: boolean
}
export type MemoryGroup = {
  session_id: string | null; title: string; active_count: number; archived_count: number
  memories: ManagedMemory[]
}
export type MemorySnapshot = { stats: MemoryStats; groups: MemoryGroup[] }
export type MemoryHistory = { memory_id: string; versions: ManagedMemory[] }
```

```typescript
memories(): Promise<MemorySnapshot> { return this.request('/api/memories') }
memoryHistory(memoryId: string): Promise<MemoryHistory> {
  return this.request(`/api/memories/${encodeURIComponent(memoryId)}/history`)
}
```

- [ ] **Step 4: Run HTTP tests and typecheck**

Run: `cd web && npm test -- src/api/http.test.ts && npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit the browser contract**

```bash
git add web/src/api/http.ts web/src/api/http.test.ts
git commit -m "feat(web): add typed memory read client"
```

### Task 7: Build the read-only memory page and detail dialog

**Files:**
- Create: `web/src/components/MemoryPage.tsx`
- Create: `web/src/components/MemoryPage.module.css`
- Create: `web/src/components/MemoryDetailDialog.tsx`
- Create: `web/src/components/MemoryDetailDialog.module.css`
- Create: `web/src/components/MemoryPage.test.tsx`

- [ ] **Step 1: Write failing component behavior tests**

```typescript
it('renders Chinese stats and groups the active tab by session', () => {
  render(<MemoryPage snapshot={snapshot} loading={false} error={null} onRefresh={vi.fn()} loadHistory={vi.fn()} />)
  expect(screen.getByText('生效中的记忆')).toBeInTheDocument()
  expect(screen.getByText('已归档的记忆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /first user request/ })).toBeInTheDocument()
  expect(screen.getByText('active memory body')).toBeInTheDocument()
  expect(screen.queryByText('archived memory body')).not.toBeInTheDocument()
})

it('switches tabs, filters text, and auto-expands matching groups', async () => {
  const user = userEvent.setup()
  render(<MemoryPage snapshot={snapshot} loading={false} error={null} onRefresh={vi.fn()} loadHistory={vi.fn()} />)
  await user.click(screen.getByRole('tab', { name: /已归档/ }))
  await user.type(screen.getByRole('searchbox'), 'archived memory')
  expect(screen.getByText('archived memory body')).toBeVisible()
})

it('opens a read-only detail and lazy-loads history without mutation controls', async () => {
  const loadHistory = vi.fn().mockResolvedValue(history)
  const user = userEvent.setup()
  render(<MemoryPage snapshot={snapshot} loading={false} error={null} onRefresh={vi.fn()} loadHistory={loadHistory} />)
  await user.click(screen.getByRole('button', { name: /查看记忆 active memory body/ }))
  expect(await screen.findByText('版本历史')).toBeVisible()
  expect(screen.queryByRole('button', { name: /新增|编辑|删除|恢复/ })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run component tests and verify missing components**

Run: `cd web && npm test -- src/components/MemoryPage.test.tsx`

Expected: FAIL because `MemoryPage` and `MemoryDetailDialog` do not exist.

- [ ] **Step 3: Implement focused components**

`MemoryPage` owns only presentation state: active/archived tab, query, type filter, expanded session IDs, and selected memory. Derive filtered groups with `useMemo`; when query/type filters are non-empty, render matched groups expanded without overwriting the user's stored expansion set.

```typescript
const visibleGroups = useMemo(() => snapshot.groups.map(group => ({
  ...group,
  memories: group.memories.filter(memory =>
    memory.status === tab && matches(memory, group, query, memoryType)
  ),
})).filter(group => group.memories.length > 0), [snapshot, tab, query, memoryType])
```

`MemoryDetailDialog` receives one `ManagedMemory`, calls `loadHistory(memory.memory_id)` in an effect only when `has_history` is true, uses an accessible `role="dialog"`, supports Escape/close, and renders validated `record` with `<pre>` while showing “结构信息异常” for invalid structure. It contains no mutation callback props.

- [ ] **Step 4: Run component tests and frontend typecheck**

Run: `cd web && npm test -- src/components/MemoryPage.test.tsx && npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit the isolated memory UI**

```bash
git add web/src/components/MemoryPage.tsx web/src/components/MemoryPage.module.css web/src/components/MemoryDetailDialog.tsx web/src/components/MemoryDetailDialog.module.css web/src/components/MemoryPage.test.tsx
git commit -m "feat(web): render read-only memory browser"
```

### Task 8: Integrate navigation and collapsible session history

**Files:**
- Modify: `web/src/App.tsx:1-146`
- Modify: `web/src/styles.css`
- Create: `web/src/App.test.tsx`

- [ ] **Step 1: Write failing integration tests**

```typescript
it('keeps conversation usable when memory loading fails', async () => {
  mockApi({ memories: Promise.reject(new Error('memory unavailable')) })
  render(<App />)
  expect(await screen.findByText('Conversation')).toBeVisible()
  expect(screen.getByPlaceholderText('Message HomeMaster…')).toBeEnabled()
})

it('switches to memory view and collapses history without changing the session', async () => {
  const user = userEvent.setup()
  render(<App />)
  const selectedSession = await screen.findByText('session-01')
  await user.click(screen.getByRole('button', { name: '折叠历史会话' }))
  expect(selectedSession).not.toBeVisible()
  await user.click(screen.getByRole('button', { name: '记忆管理' }))
  expect(screen.getByRole('heading', { name: '记忆管理' })).toBeVisible()
})
```

Use API/WebSocket fakes consistent with existing tests; do not create a second App runtime implementation.

- [ ] **Step 2: Run the App test and verify missing navigation**

Run: `cd web && npm test -- src/App.test.tsx`

Expected: FAIL because memory navigation and history collapse controls do not exist.

- [ ] **Step 3: Integrate independent memory state into App**

Add `view`, `memorySnapshot`, `memoryLoading`, `memoryError`, and `historyCollapsed` state. Start `refreshSessions()` and `refreshMemories()` independently in the initial effect; a memory rejection updates only `memoryError`. Persist `historyCollapsed` under `homemaster:web:history-collapsed`.

```typescript
const [view, setView] = useState<'conversation' | 'memories'>('conversation')
const [memorySnapshot, setMemorySnapshot] = useState<MemorySnapshot | null>(null)
const refreshMemories = useCallback(async () => {
  setMemoryLoading(true)
  try { setMemorySnapshot(await api.memories()); setMemoryError(null) }
  catch (error) { setMemoryError(error instanceof Error ? error.message : '记忆服务暂不可用') }
  finally { setMemoryLoading(false) }
}, [])
```

Keep the existing connection alive while viewing memories. Render `MemoryPage` only in the workspace body when `view === 'memories'`; render the current conversation/composer unchanged otherwise.

- [ ] **Step 4: Run the complete frontend test and build gates**

Run: `cd web && npm test && npm run typecheck && npm run build`

Expected: all commands exit 0; Vite refreshes `src/homemaster/web/static_dist`.

- [ ] **Step 5: Commit navigation, tests, and generated static assets**

```bash
git add web/src/App.tsx web/src/styles.css web/src/App.test.tsx src/homemaster/web/static_dist
git commit -m "feat(web): add memory management navigation"
```

### Task 9: Document and verify the complete read-only feature

**Files:**
- Modify: `docs/web-console-user-guide.md`
- Modify: `CHANGELOG.md`
- Test: all files changed in Tasks 1-8

- [ ] **Step 1: Add user-facing documentation**

Document the “记忆管理” navigation, four dynamic counts, active/archived tabs, exact session grouping, search/type filter, detail/history behavior, memory-unavailable state, and explicit absence of writes. State that remote use still requires the existing loopback SSH tunnel.

- [ ] **Step 2: Run the full targeted Python suite**

Run:

```bash
.runtime/venv/bin/python -m pytest \
  third_party/MindMemOS/tests/mappers/test_mappers.py \
  tests/homemaster/application/test_session_manager.py \
  tests/homemaster/application/test_session_file_backend.py \
  tests/homemaster/memory/test_mindmemos_runtime.py \
  tests/homemaster/memory/test_management.py \
  tests/homemaster/web -q
```

Expected: PASS with no warnings about un-awaited coroutines.

- [ ] **Step 3: Run frontend and packaging gates**

Run:

```bash
cd web
npm test
npm run typecheck
npm run build
cd ..
.runtime/venv/bin/python -m pytest tests/homemaster/web/test_static.py -q
```

Expected: every command exits 0 and the Python static test serves the new build.

- [ ] **Step 4: Run isolated zero-write and live read smoke**

Create an isolated memory config/root, start its MindMemOS runtime, seed active and archived fixtures, drain/seal all background queues, then capture a write-relevant fingerprint of every path: relative path, kind, mode, size, mtime_ns, and SHA-256 for files. Exclude atime. Call both GET routes through ASGI, fingerprint again, and require exact equality.

On the current hkust4 service, call `GET /api/memories`, assert `active_count + archived_count == total_count`, assert the flattened memory IDs are unique and equal `total_count`, and inspect full stderr after the response. Do not require the historical 101/17/118 constants.

- [ ] **Step 5: Check the dirty worktree and commit only documentation**

Run: `git status --short`

Expected: pre-existing unrelated changes remain untouched; no test-created untracked files appear.

```bash
git add docs/web-console-user-guide.md CHANGELOG.md
git commit -m "docs(web): document read-only memory management"
```

- [ ] **Step 6: Produce the final evidence summary**

Record the exact commit list, Python/frontend/build outputs, dynamic live counts, isolated tree fingerprint result, URL/tunnel command, and any unrelated pre-existing dirty files. Do not claim success if stderr contains a traceback or if either fingerprint differs.
