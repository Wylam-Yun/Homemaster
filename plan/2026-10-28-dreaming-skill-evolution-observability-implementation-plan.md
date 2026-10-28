# Dreaming / Skill Evolution 观测页实施计划（2026-10-28）

**Goal：** 按 `plan/2026-10-28-dreaming-skill-evolution-observability-design.md` 落两个
只读观测页——`Dreaming`（session × archive 维度看 dreaming 改动前后）与
`Skill Evolution`（skill 维度看 trace/批次/draft diff）。

**Architecture：** 后端全部走既有 `application_services` 只读装配（store 新方法 +
`feedback_run_inspector` 复用 + `mindmemos_client` 新增两个薄 `_get` 封装）；
前端复刻 `FeedbackRunsPage` 骨架（左列表右详情、注入式 loader、轮询）。

**两个独立 slice**：任务 1-6 = Dreaming，任务 7-11 = Skill Evolution，任务 12 = 收尾。
Slice B 不依赖 Slice A 的产物（仅共享 ActionDiffCard——若并行开发，
B 先写自己的渲染，A 落地后再去重即可；串行则直接复用）。

**测试命令：** 后端 `pytest tests/homemaster/... -x`（pytest addopts 已排除
live_api/live_memory/live_alfworld marker）；前端 `cd web && npm test`（vitest run）、
`npm run build` 刷新 `src/homemaster/web/static_dist`。

---

## Slice A — Dreaming 页

### Task 1：`MemoryWorkStore` 只读查询方法

**Files:**
- Modify: `src/homemaster/memory/memory_work_store.py`（Protocol ~L291 档案区 +
  `SQLiteMemoryWorkStore` 实现 + `SkillEvolutionBatch` dataclass 放在
  `SkillInvocation` 定义附近 ~L164）
- Test: `tests/homemaster/memory/test_memory_work_store.py`

协议与实现（四个方法，全部只读 SELECT）：

```python
@dataclass(frozen=True)
class SkillEvolutionBatch:
    batch_id: str
    cloud_skill_id: str
    trace_ids: tuple[str, ...]
    job_id: str | None
    status: str
    created_at: float

# Protocol（archives 区块内）:
def list_archives(self, *, limit: int = 500) -> list[ArchiveSnapshot]: ...

# Protocol（skill 区块内）:
def skills_with_invocations(self) -> list[dict[str, Any]]: ...
def skill_invocations_for_skill(
    self, cloud_skill_id: str, *, limit: int = 100
) -> list[SkillInvocation]: ...
def skill_evolution_batches_for_skill(
    self, cloud_skill_id: str
) -> list[SkillEvolutionBatch]: ...
```

```python
# SQLiteMemoryWorkStore 实现
def list_archives(self, *, limit: int = 500) -> list[ArchiveSnapshot]:
    rows = self._db().execute(
        "SELECT * FROM archives ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [self._archive_from_row(r) for r in rows]
    # 复用现有 row→ArchiveSnapshot 的反序列化 helper（archives_for_session
    # 内已有同款转换；若无独立 helper 则从该行取字段构造，禁止复制两份逻辑——
    # 把转换提成 _archive_from_row 私有方法并让 archives_for_session 共用）

def skills_with_invocations(self) -> list[dict[str, Any]]:
    rows = self._db().execute(
        "SELECT cloud_skill_id, MIN(skill_id) AS skill_id, "
        "COUNT(*) AS invocation_count, MAX(sealed_at) AS last_sealed_at "
        "FROM skill_invocations GROUP BY cloud_skill_id "
        "ORDER BY last_sealed_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]

def skill_invocations_for_skill(
    self, cloud_skill_id: str, *, limit: int = 100
) -> list[SkillInvocation]:
    rows = self._db().execute(
        "SELECT * FROM skill_invocations WHERE cloud_skill_id = ? "
        "ORDER BY COALESCE(sealed_at, created_at) DESC LIMIT ?",
        (cloud_skill_id, limit),
    ).fetchall()
    return [self._invocation_from_row(r) for r in rows]  # 同样复用既有转换

def skill_evolution_batches_for_skill(
    self, cloud_skill_id: str
) -> list[SkillEvolutionBatch]:
    rows = self._db().execute(
        "SELECT * FROM skill_evolution_batches WHERE cloud_skill_id = ? "
        "ORDER BY created_at DESC", (cloud_skill_id,)
    ).fetchall()
    return [
        SkillEvolutionBatch(
            batch_id=r["batch_id"], cloud_skill_id=r["cloud_skill_id"],
            trace_ids=tuple(_load(r["trace_ids_json"]) or ()),
            job_id=r["job_id"], status=r["status"], created_at=r["created_at"],
        )
        for r in rows
    ]
```

**测试要点（per-instance，禁聚合断言）：** 造 2 个 session × 3 个 archive 断言
`list_archives` 每行字段精确且倒序；造 2 个 skill、一个 3 条 sealed + 1 条未 sealed、
一个 1 条，断言 `skills_with_invocations` 每行 count/skill_id/last_sealed_at 精确；
`skill_invocations_for_skill` 断言 sealed 在前且未 sealed 用 created_at 参与排序；
`skill_evolution_batches_for_skill` 断言 trace_ids 反序列化与 job_id 关联。

- [ ] Step: `pytest tests/homemaster/memory/test_memory_work_store.py -x` 全绿后 commit
      `feat(memory): read-only list queries for dreaming/evolution observability`

### Task 2：归档状态词汇上移 `_shared`

**Files:** Modify `src/homemaster/web/_shared.py`、`src/homemaster/web/routes/session_ops.py`

`_ARCHIVE_STATE_MAP`（`session_ops.py:30`）原样搬入 `_shared.py`；
`session_ops.py` 改为 `from homemaster.web._shared import _ARCHIVE_STATE_MAP`。
行为零变化，跑 `pytest tests/homemaster/web/test_app.py -k archive -x` 确认。

- [ ] Step: commit `refactor(web): hoist archive state vocabulary for reuse`

### Task 3：Dreaming 端点（`web/routes/memory.py`）

**Files:** Modify `src/homemaster/web/routes/memory.py`（两个端点紧跟
feedback-runs 区块，保证在 `/{memory_id}` 之前注册）、
Test: `tests/homemaster/web/test_app.py`

```python
def _aggregate_archive_state(archives: list[object]) -> str:
    states = [
        _ARCHIVE_STATE_MAP.get(getattr(a, "status", ""), "unknown")
        for a in archives
    ]
    if not states:
        return "unarchived"
    if "processing" in states:
        return "processing"
    if "failed" in states:
        return "failed"
    return "completed"

def _turn_user_preview(store: Any, session_id: str, turn_id: str) -> str | None:
    """最后一条 role=='user' 的 message content，截断 200 字；无则 None。"""
    try:
        events = store.events_for_turn(session_id, turn_id)
    except Exception:
        return None
    for event in reversed(events):
        payload = getattr(event, "payload", None) or {}
        if payload.get("role") == "user" and payload.get("content"):
            return str(payload["content"])[:200]
    return None
```

`GET /api/memories/dreaming-sessions`：service/store/list_archives 缺失分支同
feedback-runs（`memory_unavailable` / `dreaming_unavailable`）。遍历
`application.session_manager.session_summaries()`，join `by_session` 映射：

```python
{
    "session_id": summary.session_id, "title": summary.title,
    "updated_at": summary.updated_at,
    "archive_state": _aggregate_archive_state(entries),
    "archive_count": len(entries),
    "last_archive_at": max((a.created_at for a in entries), default=None),
}
```

`GET /api/memories/dreaming-sessions/{session_id}`：
- `session_id not in _session_ids(application.session_manager)` → 404
  `session_not_found`（沿用 sessions.py 同款 `_error`）；
- `archives_for_session` 逐 archive 投影：
  - `job`：`store.job(a.dreaming_job_id)` → `{job_id, status, attempts,
    last_error(→error), dreaming_record_id, raw_verified, missing_ids}`（后三个
    从 `job.result` 取）；`dreaming_job_id` 为空或 job 缺失 → `job: null`；
  - `seeds`：`a.add_operation_ids` 逐条 `store.job_by_operation_key` →
    `turn_id` → `_turn_user_preview`；op_key 查无 job 或 turn_id 空 →
    `{"operation_id": op, "missing": True, "preview": None}`；
  - `actions`：`job.result.actions` 喂 `inspector.action_diffs()`——
    inspector 缺失 → `actions: None, diffs_unavailable: True`（区分于
    空列表）；job 无 result/actions → `actions: []`；
- `session_excerpts`：`turns_for_session` 末 5 个 turn 的 user preview
  （未归档 session 的详情页也要有内容看）。

**路由测试**（沿用 test_app.py 桩模式：`_FakeApplication` + `application_services` dict）：
逐字段断言列表聚合（无 archive/在途/failed/completed 四态各一 session + `×N`）；
详情断言 seeds 的 missing 分支、actions=null+diffs_unavailable、
session_not_found 404；per-archive 字段逐条断言。

- [ ] Step: commit `feat(web): dreaming session list + per-archive detail routes`

### Task 4：抽共享 `ActionDiffCard`

**Files:** Create `web/src/components/ActionDiffCard.tsx` +
`ActionDiffCard.module.css`；Modify `FeedbackRunsPage.tsx`（L373-393 diff 块换成
`<ActionDiffCard>`）+ 其 css 移除已搬走样式

Props：`{ action: FeedbackActionDiff; beforeLabel: string; afterLabel: string;
sessionLabel?: string }`。Badge/memory id/missing/双栏原样搬；
feedback 页传 `beforeLabel="整理前" afterLabel="后"`——dreaming 传"改动前/改动后"。
`FeedbackRunsPage.test.tsx` 现有断言须全绿（零回归）。

- [ ] Step: `cd web && npm test -- FeedbackRunsPage` 绿后 commit
      `refactor(web): extract shared ActionDiffCard`

### Task 5：`DreamingPage` + API client + 路由/侧栏

**Files:** Create `web/src/components/DreamingPage.tsx` + `.module.css` + `.test.tsx`；
Modify `web/src/api/http.ts`、`web/src/router.tsx`、`web/src/App.tsx`

- http.ts：类型 `DreamingSessionRow`/`DreamingArchive`/`DreamingSessionDetail`
  按 Task 3 响应形状；方法 `dreamingSessions()`、
  `dreamingSessionDetail(sessionId)`。
- router.tsx：union 加 `{ kind: 'dreaming' }`；parseHash 加 `'dreaming'` 分支；
  `routeHash` 的 `` `#/${route.kind}` `` 分支已自动覆盖（确认 kind 进 default 分支）。
- App.tsx：导航按钮 `Dreaming` 放「记忆整理」之后；
  `route.kind === 'dreaming'` 渲染 `<DreamingPage loadSessions={...} loadDetail={...}
  pollMs={2000} unavailable={memoryUnavailable} />`。
- 页面：左列 session 卡（title、状态徽章、`×N`、updated_at、徽章筛选 checkbox 组）；
  右列：元信息 + `session_excerpts` 区 + 每 archive 一卡（状态/turn_range/error/
  `raw_verified` 红色警示 + 左 seeds 右 ActionDiffCard 列表）。非终态轮询沿用
  feedback 页 generation ref 模式。
- 测试：徽章判定表驱动（unarchived/processing/failed/completed/×N）、detail 渲染
  seeds+diff、unavailable 占位。

- [ ] Step: commit `feat(web): dreaming observability page`

### Task 6：web build 刷 static_dist

`cd web && npm run build`，确认 `src/homemaster/web/static_dist` 资产更新并入提交。

- [ ] Step: 并入 Task 5 的 commit 或单独 `chore(web): rebuild static_dist`

---

## Slice B — Skill Evolution 页

### Task 7：`MindMemOSClient` 版本读封装

**Files:** Modify `src/homemaster/memory/mindmemos_client.py`；
Test: `tests/homemaster/memory/test_mindmemos_client.py`

```python
@dataclass(frozen=True)
class SkillVersionInfo:
    version_id: str
    parent_version_id: str | None
    status: str
    version_label: str | None
    content_hash: str | None

@dataclass(frozen=True)
class SkillVersionContent:
    version: SkillVersionInfo
    content: str

async def list_skill_versions(self, *, cloud_skill_id: str) -> list[SkillVersionInfo]:
    data = await self._get(f"/v1/skills/{cloud_skill_id}/versions")
    return [
        SkillVersionInfo(
            version_id=str(v.get("version_id") or ""),
            parent_version_id=v.get("parent_version_id"),
            status=str(v.get("status") or ""),
            version_label=v.get("version_label"),
            content_hash=v.get("content_hash"),
        )
        for v in data.get("versions") or []
        if isinstance(v, dict)
    ]

async def get_skill_version_content(
    self, *, cloud_skill_id: str, version_id: str
) -> SkillVersionContent:
    data = await self._get(
        f"/v1/skills/{cloud_skill_id}/versions/{version_id}/content"
    )
    version = data.get("version") or {}
    return SkillVersionContent(
        version=SkillVersionInfo(
            version_id=str(version.get("version_id") or ""),
            parent_version_id=version.get("parent_version_id"),
            status=str(version.get("status") or ""),
            version_label=version.get("version_label"),
            content_hash=version.get("content_hash"),
        ),
        content=str(data.get("content") or ""),
    )
```

**UNVERIFIED 标注**：docstring 注明端点行为以 vendored `api/skill_routes.py` 为准、
真机门见 Task 12。测试用 `_CaptureTransport` 钉死两个请求路径 +
envelope→dataclass 映射 + `not_found`→`OperationNotFoundError` 分类。

- [ ] Step: commit `feat(memory): skill version read wrappers on MindMemOSClient`

### Task 8：Skill Evolution 端点（`web/routes/skills.py`）

**Files:** Modify `src/homemaster/web/routes/skills.py`；
Test: `tests/homemaster/web/test_skills_management.py`

三个端点按设计 JSON 形状；要点：
- 统一前置：`memory_work_store` 缺失 → 503 `skill_evolution_unavailable`；
- detail 端点 `versions` 拉取：`mindmemos_client` 缺失或 `list_skill_versions`
  抛 `MindMemOSClientError`/`OutcomeUnknownError` → `versions: None,
  versions_unavailable: True`，本地 traces/batches 照常；
- batch 的 `draft_version_id`/`job` 状态：`store.job(batch.job_id)` →
  `result.draft_version_id` + job status；`unconsumed_count` 走
  `store.unconsumed_trace_count`；
- content 端点：先 `list_skill_versions` 校验 version 属该 skill（防跨 skill
  越权读），不属 → 404 `skill_version_not_found`；parent 查无 → `parent: null`。

- [ ] Step: commit `feat(web): skill evolution observability routes`

### Task 9：行级 diff 工具

**Files:** Create `web/src/lib/lineDiff.ts` + `web/src/lib/lineDiff.test.ts`

经典 LCS（两行数组 → `Array<{type:'same'|'add'|'del', text:string}>`），
~50 行，含空串/单行/全异边界测试。不引依赖。

- [ ] Step: commit `feat(web): minimal line-diff utility`

### Task 10：`SkillEvolutionPage` + API client + 路由/侧栏

**Files:** Create `web/src/components/SkillEvolutionPage.tsx` + `.module.css` +
`.test.tsx`；Modify `web/src/api/http.ts`、`web/src/router.tsx`、`web/src/App.tsx`

- router.tsx：union 加 `{ kind: 'skill-evolution' }`，`parseHash` 加
  `'skill-evolution'` 字面分支（`routeHash` 的 `` `#/${route.kind}` `` 默认分支
  自动产出 `#/skill-evolution`）；导航按钮 `Skill Evolution` 放「技能」之后。
- 左列 skill 卡：`skill_id` + cloud 短码、`unconsumed_count/8` 进度条、
  invocation/batch 计数；右列 `Traces`（score/outcome/task_input、
  可展开 steps）+ `Evolution`（批次卡 → 点击拉 `skillVersionContent` →
  `<LineDiff parent content>` 渲染，del 红 add 绿）。
- 测试：进度条 n/8、batches→job 状态映射、diff 渲染 add/del 行、
  versions_unavailable 占位。

- [ ] Step: commit `feat(web): skill evolution observability page`

### Task 11：web build 刷 static_dist

同 Task 6（若 A/B 都改了前端，一次 build 覆盖）。

### Task 12：收尾

- [ ] CHANGELOG Unreleased 加两条（dreaming 观测页、skill evolution 观测页，
      含 UNVERIFIED→已核对的记录位置说明）
- [ ] 全量 `pytest tests/homemaster -x` + `cd web && npm test && npm run build`
- [ ] **真机门**（design UNVERIFIED 项）：对真 MindMemOS server 跑
      注册→8 trace→evolution batch→`list_versions`/`content` 读回，
      断言 draft 真实返回、content 完整、parent 可 diff；
      stderr 无迟到 traceback；结果写回 CHANGELOG/pitfalls
- [ ] commit `feat(web): dreaming + skill evolution observability pages`（聚合收尾）

## 风险与注意

- `dreaming-sessions`/`skill-evolution` 均为字面段，注册顺序必须在参数化路由之前
  （feedback-runs 已有同款注释约束）；
- 聚合徽章是 session 级近似，多 archive 混合态必须保留逐 archive 分解入口（tooltip 或展开），
  不许只显示聚合态——否则掩盖 per-instance 失败；
- diff 只读：`action_diffs`/`get_skill_version_content` 绝不引入任何写调用；
- 未 sealed 的 invocation（`sealed_at` NULL）排序用 `COALESCE(sealed_at, created_at)`；
- `skill_invocations.status` 全集未冻结——UI 未知状态一律原样显示 + muted 徽章，
  不做枚举兜底静默。
