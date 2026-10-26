# Web Console 前端 overhaul 实施计划

对应设计：`plan/2026-10-25-web-console-frontend-overhaul-design.md`
执行方式：新分支 `feat/web-console-overhaul`，按批次 1→8 顺序提交。
验证命令（每批结束跑对应的）：
- 前端：`cd web && npm test && npm run typecheck`（最终批 `npm run build`）
- 后端：`uv run pytest tests/homemaster/web/ tests/homemaster/test_skills_registry.py -x -q`

---

## 批次 1 — 回复顺序修复（WS-1）

### Task 1.1 `applyTextSnapshot` 改为"末尾结算"

**Files:** `web/src/state/conversation.ts`（`TurnSegment` 36-39、`appendDelta` 112-118、`applyTextSnapshot` 120-126）

设计要点：thinking/answer 段加 `settled?: boolean` 标记。
- delta 只并入"末尾且未结算"的同类段；
- snapshot 是该轮该类文本的权威值：**删除所有未结算同类段**（覆盖 kind 交错
  如 `thinking.delta→answer.delta→thinking.delta` 造成的散落残段），在最后一个
  被删位置插入 `{kind,text,settled:true}`；若无未结算段则追加到末尾。
这样：流式轮次原位结算；无 delta 只有 snapshot 的轮次按序追加、绝不覆盖前轮
已结算段；snapshot 后残余 delta 另起新段（已结算段不回写）。

```ts
export type TurnSegment =
  | { kind: 'thinking'; text: string; settled?: boolean }
  | { kind: 'answer'; text: string; settled?: boolean }
  | { kind: 'tool'; toolCallId: string }

function appendDelta(segments: TurnSegment[], kind: 'thinking' | 'answer', text: string): TurnSegment[] {
  const last = segments.at(-1)
  if (last !== undefined && last.kind === kind && last.settled !== true) {
    return [...segments.slice(0, -1), { kind, text: last.text + text }]
  }
  return [...segments, { kind, text }]
}

function applyTextSnapshot(segments: TurnSegment[], kind: 'thinking' | 'answer', text: string): TurnSegment[] {
  let lastUnsettled = -1
  const kept = segments.filter((segment, index) => {
    if (segment.kind === kind && segment.settled !== true) {
      lastUnsettled = index
      return false
    }
    return true
  })
  if (lastUnsettled === -1) return [...segments, { kind, text, settled: true }]
  // 被删段之后的元素在 kept 中下标前移；插入点 = kept 中"原下标 < lastUnsettled"的元素数
  const insertAt = segments.slice(0, lastUnsettled).filter(segment => !(segment.kind === kind && segment.settled !== true)).length
  return [...kept.slice(0, insertAt), { kind, text, settled: true }, ...kept.slice(insertAt)]
}
```

注：每个 `assistant.reply` 是 per-round 新 block 文本（非累计），因此**绝不能**
删除已 settled 段；`run.completed` 的 `final_reply` 仅在 `current.answer` 为空时
appendDelta，不会重复。

**Test:** `web/src/state/conversation.test.ts` 新增用例（构造 `WebEvent` 序列喂 `reduceWebEvent`，再过 `projectTurn` 断言 steps 顺序与文本）：

```ts
// 轮1: thinking.delta + answer.delta → tool → 轮2: answer.snapshot
// 断言: [reasoning, answer(轮1文本), tool_group, answer(轮2文本)] 顺序且轮1文本仍在
// 无 delta 只有连续两次 answer.snapshot → 两个 answer 段按序追加、互不覆盖
// snapshot 后又来 answer.delta → 新 answer 段，不并入已 settled 段
// 交错残段: thinking.delta→answer.delta→thinking.delta→thinking.snapshot
//   → 两个未结算 thinking 段坍缩为单个 settled 段，answer 段不动
```

- [ ] 改 `TurnSegment`/`appendDelta`/`applyTextSnapshot`
- [ ] 写失败测试 → 实现 → 绿
- [ ] `cd web && npm test && npm run typecheck`
- [ ] commit `fix(web): answer/thinking snapshot settles trailing segment instead of hoisting`

---

## 批次 2 — Provider 默认可见 + Skills 刷新（WS-5 + WS-2 刷新半）

### Task 2.1 `list_profiles` 加 `is_default`

**Files:** `src/homemaster/services/provider_config.py`（`list_profiles` 520-524）

```python
def list_profiles(self) -> list[dict[str, Any]]:
    default_name = self._default_name()
    profiles = [_profile_summary(item) for item in self._current_items()]
    for profile in profiles:
        profile["is_default"] = (
            default_name is not None and profile["name"].casefold() == default_name.casefold()
        )
    return profiles
```

新增私有 `_default_name()`：优先 `getattr(getattr(self._runtime_config, 'providers', None), 'default', None)`，否则 `_read_existing()` 的 `providers.default`；取不到返回 `None`。字符串 strip 后空串当 `None`。

**已知测试需同步改（评审发现，必炸项）：** `tests/homemaster/web/test_providers_api.py:96-106` 的 `entry == {...}` 精确形状断言要加 `is_default`；并全文件 grep 其他精确形状断言。`is_default` 只进 list 响应（POST/PATCH 的 `_profile_summary` 不变——页面总是重拉列表，可接受）。

**Test:** 构造含 `providers.default` 的配置，`GET /api/providers` 断言恰一项 `is_default: true`。

### Task 2.2 前端默认徽标 + 设为默认

**Files:** `web/src/api/http.ts`（`ProviderProfile` 加 `is_default?: boolean`）；`web/src/pages/SettingsPage.tsx`

- 卡片 `cardTitle` 区 `{provider.is_default && <span className={styles.defaultBadge}>默认</span>}`
- `cardActions` 区非默认 chat provider 加按钮"设为默认"→ `api.patchProvider(name, { set_default: true })` → `toast` + `refresh()` + `onProvidersChanged?.()`
- `SettingsPage.module.css` 加 `.defaultBadge`（复用现有 badge token）

### Task 2.3 导航改名

**Files:** `web/src/App.tsx`（导航按钮 822 行附近 `设置`→`Provider`；topbar 标题 map `'设置'`→`'Provider'`）
**必改测试：** `web/src/App.test.tsx:543` 点击 `button { name: '设置' }` → 改 `'Provider'`。

### Task 2.4 Skills 列表真刷新 + issues

**Files:** `src/homemaster/web/routes/skills.py`（`list_skills`）

```python
try:
    await asyncio.to_thread(registry.refresh)  # 同步文件 I/O 不阻塞 loop
except Exception:
    logger.exception("skill_registry_refresh_failed")
    return _error(503, "skill_registry_unavailable",
                  "The skill registry could not be refreshed.", retryable=True)
skills: list[dict[str, object]] = []
for skill in registry.list_skills(): ...  # 现有逻辑
return {
    "skills": skills,
    "issues": [
        {"source": i.source, "code": i.code, "detail": i.detail, "path": i.path}
        for i in registry.issues
    ],
}
```

**SkillLoadIssue 补定位信息**（`skills/registry.py` + `skills/loader.py`）：
dataclass 加可选 `path: str | None = None`；`_load_automatic_sources`（loader.py:299-301）
record 时传 `path=str(path)`（那里有 SKILL.md 路径变量），plugin 分支同理。
前端警示条才能说清"哪个技能加载失败"。

**前端** `web/src/api/http.ts`：`listSkills` 返回 `{skills, issues?: {source,code,detail,path?}[]}`；`SkillsPage` 顶部渲染 issues 警示条（每条显示 path + detail）。

**Test:** 新 `tests/homemaster/web/test_skills_api.py`——registry fake（带 `refresh()` 计数/`issues`/`provenance_for`），断言路由调用 refresh、issues 透传、refresh 异常→503 envelope。前端 `SkillsPage.test.tsx` 加 issues 渲染断言。

- [ ] 后端 + 前端 + 测试
- [ ] commit `feat(web): default-provider visibility, skills live refresh`

---

## 批次 3 — 设计 token 基线（WS-7）

### Task 3.1 扩展 `:root` token + 通用类

**Files:** `web/src/styles.css`

新增（保持现有变量不动）：
```css
:root {
  --space-1: .25rem; --space-2: .5rem; --space-3: .75rem; --space-4: 1rem; --space-5: 1.5rem; --space-6: 2rem;
  --radius-s: .4rem; --radius-m: .65rem; --radius-l: 1rem;
  --text-xs: .68rem; --text-s: .8rem; --text-m: .95rem;
}
```

### Task 3.2 `components/ui/` 小组件

**Files:** 新建 `web/src/components/ui/{PageHeader.tsx,Card.tsx,Badge.tsx,Button.tsx,ui.module.css,index.ts}`

- `PageHeader({eyebrow,title,description,actions})`——复刻各页 `headingRow` 模式
- `Badge({tone:'accent'|'muted'|'warn'|'danger'|'success',children})`
- `Button({variant:'primary'|'ghost'|'danger',size?})`
- `Card` 薄封装
- CSS 全部引用 token，不加新颜色

无行为逻辑 → 各一个渲染 smoke test 合在 `ui.test.tsx`。

- [ ] 实现 + 测试 + commit `feat(web): shared design tokens and ui primitives`

---

## 批次 4 — 记忆页重做（WS-3）

### Task 4.1 后端 `GET /api/memories/graph`（混合数据源——评审修正）

**关键事实（评审核实）：** Neo4j `Memory` 节点只有 `content`/`status`/`status_changed_at`/`delete_reason`——**没有** mem_type/session_id/update_at（那些在 Qdrant payload）；`Source.source_id` 是 hash 不是 session id，Source 节点没有 title。因此节点字段必须走 `list_raw_memories`（Qdrant），Neo4j 只查边。

**Files:** `src/homemaster/web/memory_views.py` + `src/homemaster/web/routes/memory.py` + `src/homemaster/memory/mindmemos_lifecycle.py`

- `MindMemOSLifecycleMixin` 加只读 property：
  ```python
  @property
  def neo4j_store(self) -> Any | None:
      return self._neo4j
  ```
- `MemoryManagementService.graph(*, tenant_id) -> MemoryGraphView`（新 dataclass 或直接 dict）：
  - `neo4j_store is None` → 抛 `MemoryUnavailableError`（新异常，路由映 503 `memory_unavailable`，`logger.exception` 与其他路由同风格）。
  - `context = _request_context("web-memory-graph", tenant_id)`；`project_id = context.project_id`。
  - **节点**：`await self._mindmemos.list_raw_memories(context)` → 每行投影
    `{id: memory_id, preview: content[:160], memory_type, status, session_id, updated_at}`
    （preview **服务端截断**，不回全文）。
  - **边**（单条带 LIMIT 的 cypher）：
    ```cypher
    MATCH (m:Memory {project_id:$pid})-[r]->(n)
    WHERE type(r) IN ['MENTIONS','EXTRACTED_FROM','RELATES_TO','DERIVED_FROM']
      AND n.project_id = $pid
    RETURN m.memory_id AS src, type(r) AS rel,
           coalesce(n.memory_id, n.entity_id, n.source_id) AS dst,
           labels(n)[0] AS dst_label, n.entity_name AS entity_name
    LIMIT 2000
    ```
  - 聚合 `dst_label='Entity'` 出 `entities:[{id:entity_id, name:entity_name}]`；
    `sessions:[{id:session_id, title:_title_for_session(sid)}]`（仅引用到的 session）。
  - 返回 `{nodes, entities, edges, sessions}`；edges 里 MENTIONS 的 dst 指 entity id，
    其余指向 memory/source 各自 key（前端按 dst_label 解析目标节点）。

**Route:** `routes/memory.py` 注册 `@app.get("/api/memories/graph")`：
- service None / `MemoryUnavailableError` → 503 `memory_unavailable`
- 其他异常 → `logger.exception` + 503 `memory_read_failed`

**Test:** `tests/homemaster/web/test_memory_readonly.py`——fake mindmemos（`list_raw_memories` + `neo4j_store.run_read` 记录调用）断言节点来自 raw rows、边来自 cypher、preview 截断、entity/session 聚合；store None → 503。

**必改测试：** `tests/homemaster/web/test_app.py::test_web_route_table_is_stable`（~510-570 精确路由表 golden）expected 加 `GET /api/memories/graph`。

### Task 4.2 前端 graph API + 依赖

- `web/src/api/http.ts`：`MemoryGraph = {nodes, entities, edges, sessions}`，`api.memoryGraph()`。
- 依赖（只引子包，不引 d3 伞包）：`npm add d3-force d3-zoom d3-drag d3-selection && npm add -D @types/d3-force @types/d3-zoom @types/d3-drag @types/d3-selection`

### Task 4.3 MemoryPage 重写

**Files:** `web/src/components/MemoryPage.tsx` 重写 + `MemoryPage.module.css`；新建 `web/src/components/graph/MemoryGraph.tsx` + `.module.css`

页面结构（视图切换用**组件 state**，不用 `?view=`——hash router 的 parseHash 会把 `memory?view=graph` 整体当 head 落到 chat，评审确认）：
```
headingRow(共用 PageHeader): eyebrow 长期记忆 / h1 记忆管理 / actions[刷新, 视图切换 列表|图谱]
stats 行保留
[列表] 分组切换(按会话|按类型) + 搜索 → 会话组(title=group.title, count) → 卡片(摘要标题+时间+类型chip)
[图谱] 过滤条(类型多选+图例) → MemoryGraph
```

`MemoryGraph`：
- 输入 `graph` 数据 + `onSelect(memory)`。
- d3-force：`forceSimulation` + `forceLink`(id 键) + `forceManyBody` + `forceCollide` + `forceX/Y` 向心；
  无 MENTIONS 边的 memory 节点加虚拟 `session:<sid>` 锚点（不渲染，link 弱 strength 聚簇）。
- SVG 渲染：memory=`<circle r=10>` 按 `memory_type` 着色（图例）；entity=`<rect rx>` 小号灰；edge=`<line>`。
- 缩放/平移：`d3-zoom`（经 `d3-selection` 绑定 svg）；节点拖拽：`d3-drag`。
- 点击 memory 节点 → `onSelect` → 现有 `MemoryDetailDialog`。
- tooltip：`<title>` 元素即可（摘要+类型+时间），不做自定义浮层（YAGNI）。

**Test:** `MemoryGraph` 渲染 smoke（jsdom 给 mock 数据断言节点/边元素数）；`MemoryPage` 分组/搜索测试更新。

### Task 4.4 列表层去 ID + 会话分组

含在 4.3 重写内：卡片不再出现 `来源 session-xxx`；组头即会话标题；详情弹窗保留可复制 ID。

- [ ] 后端端点 + 前端重写 + 测试
- [ ] `npm test && npm run typecheck && npm run build` + pytest
- [ ] commit `feat(web): memory page redesign with knowledge graph view`

---

## 批次 5 — 模型枚举接入（WS-6）

### Task 5.1 ModelPicker 懒加载枚举

**Files:** `web/src/components/composer/ModelPicker.tsx`、`web/src/components/composer/ComposerPanel.tsx`（透传）、`web/src/App.tsx`（传 `loadModels`）

- `ModelPicker` 新 prop `loadModels?: (name:string)=>Promise<ProviderModels>`；内部 `modelsCache`（`useRef<Map<string,ProviderModels>>`）。
- provider 选中 → `loadModels(name)` → loading 态 → `source==='live'` 时 models 进 Model 下拉（每项 option + `自定义…`）；`'fallback'` → 下拉只剩默认模型+自定义，并显示提示"该 provider 不支持在线枚举"。
- **修正现有 bug（评审发现）：** `pickModel` 现在 `onChange({ provider_name })` 会丢弃所选 model——枚举项接入后必须 `onChange({ provider_name: value.provider_name, model: raw })`。
- App：`loadModels={name => api.providerModels(name)}`。

### Task 5.2 Settings 兜底文案

**Files:** `web/src/pages/SettingsPage.tsx`：**两处** `'离线兜底列表'`（~358 和 ~482）都改成 `'在线枚举不可用，显示兜底列表'`。

**Test:** `ComposerPanel.test.tsx`/`ModelPicker` 新测试：live/fallback 两路。

- [ ] 实现 + 测试 + commit `feat(web): live model enumeration in composer picker`

---

## 批次 6 — Skills 导入/删除（WS-2 管理半）

### Task 6.1 `POST /api/skills/import`（JSON 上传，不引 python-multipart）

**Files:** `src/homemaster/web/routes/skills.py` + `src/homemaster/web/schemas.py`

Body（带大小上限与 extra 禁令，风格同 schemas.py）：
```python
class SkillImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=512)     # 相对路径，如 "my-skill/SKILL.md"
    content: str = Field(max_length=1_000_000)  # 单文件 ≤1MB 文本
class SkillImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[SkillImportFile] = Field(min_length=1, max_length=200)
    overwrite: bool = False
```
另在路由内总字节核对：`sum(len(f.content.encode('utf-8')) for f in files) > 4 MiB` → 413 `payload_too_large`。

逻辑：
1. 归一化每个 `path`：`PurePosixPath`，拒绝绝对路径、`..` 段；`files` 非空；全部文件必须在同一顶层目录 `top = path.parts[0]`；多于一个顶层目录 → 422。
2. 必须存在 `<top>/SKILL.md`；`parse_skill_metadata(top, content)` 解析，`ValueError` → 422 `invalid_skill`（name 缺失会回落 default_name，不算失败——与 loader 同语义；目录名与 name 不强制一致）。
3. **冲突预检（评审 B6-4）**：`registry.get(parsed.name)` 或 command_name 冲突且目标不是同一个 user 目录 → 409 `skill_conflict`（防止写入后被 builtin 影子屏蔽的假成功）。
4. 目标根：`config.skills.user_dirs[0]`（`ctx.config` 可能为 None；回落 `homemaster.config.config._default_skill_user_dirs()`——它读 `HOMEMASTER_HOME`，保证与 refresher 扫描同位置；空 tuple 同样回落）/`top`。已存在且非 overwrite → 409。
5. `dest_root = (user_root / top).resolve()`；写前 `user_root_resolved` 也 resolve，每个文件 `(dest_root / rel).resolve()` 校验 `relative_to(dest_root)`（防 `<top>` 内既有 symlink 逃逸）；overwrite 时**先整目录删再重写**（不留陈文件）。
6. 写文件（mkdir 父目录、UTF-8）→ `await asyncio.to_thread(registry.refresh)` → **回读验证**：`registry.get(name)` 命中且 `provenance[-1].source=='user'` 且 `base_dir` 在 `dest_root` 内 → 返回 `{skill, status:'imported'}`；否则从 `registry.issues` 找对应 issue 返回 422 带 detail（shadowed/failed 绝不假成功）。

### Task 6.2 `DELETE /api/skills/{name}` + `deletable` 标志

- 列表项在现有字段上加 `deletable: bool`——服务端按 **raw provenance**（`_SKILL_SOURCE_LABELS` 会把 explicit 也映射成 'user'，前端不能靠 label 猜）：`provenance[-1].source == 'user'`。
- `registry.get(name)` → None → 404 `skill_not_found`；`skill.base_dir is None` 或 `provenance[-1].source != 'user'` → 403 `skill_protected`。
- `base_dir = Path(skill.base_dir).resolve()`，校验 `base_dir` 在 resolved `user_dirs` 任一根内 → 否则 403；`shutil.rmtree(base_dir)` → `refresh()` → 204。

### Task 6.3 前端

- `api.importSkill(files:{path,content}[], overwrite?)`、`api.deleteSkill(name)`。
- SkillsPage：`导入技能`按钮 → `<input type="file" webkitdirectory>` 隐藏控件 → 逐个 `file.text()` 收集 → POST → toast/refresh；409 时 confirm 覆盖重试。
- 卡片 `skill.deletable` 才显示"删除"；confirm → DELETE → refresh。
- **`webkitdirectory` 不在 React 18 类型里**：用 `{...({ webkitdirectory: '' } as object)}` spread 或 ref.setAttribute；测试直接喂 `importSkill` stub（jsdom 无 webkitRelativePath）。

**Test:** pytest——import 合法（落盘+refresh+回读）、缺 SKILL.md 422、`..`/绝对路径 422、多顶层目录 422、空 files 422、总量超限 413、重名 409+overwrite（旧文件被清）、builtin 名冲突 409、delete user 204、delete builtin/explicit 403、route table golden 更新。前端 SkillsPage 测试（deletable 渲染、import 调用）。

- [ ] 实现 + 测试 + commit `feat(web): skill import/delete management`

---

## 批次 7 — 会话管理页（WS-4）

**Files:** `web/src/pages/SessionsPage.tsx` 重写 + `.module.css`；`App.tsx` 传操作回调

- **重命名复用方案（评审要求拍板）：** `commitRename` 依赖 App 的 `renameDraft` 闭包——选 props 透传方案：`renamingId`/`renameDraft`/`setRenameDraft`/`startRename`/`commitRename` 全部作为 props 传入 SessionsPage（共享单 draft，和侧栏同一份 state，行为天然一致）；另传 `archivingIds` 供归档按钮 disabled。
- 工具条：搜索 input + 排序 select（`updated_at` 默认 / `title` / `message_count`）
- 行组件：状态点、标题（或 inline rename input）、meta（相对时间+条数+归档徽标）、操作组（打开→Link、重命名、归档、删除——与侧栏同回调、同 confirm/toast 行为）
- 用 `ui/` 的 PageHeader/Badge/Button。

**Test:** `SessionsPage` 现有 test 重写：过滤、排序、操作回调触发。

- [ ] 实现 + 测试 + commit `feat(web): session management page`

---

## 批次 8 — 全站一致性收编（WS-8）

- `PermissionsPage/OnboardingPage/SettingsPage/SkillsPage/composer/*.module.css`：任意像素值→token 阶；重复按钮/徽标/卡片定义收编到 ui 组件或共享类。
- 逐文件 diff 审查，视觉无回退为准。
- `npm run build` 全量 + 真机走查清单（设计文档"验证"节）。

- [ ] 收编 + build + commit `refactor(web): unify page styles on shared tokens`

---

## 风险与注意（含评审修正）

- `test_web_surface_golden.py` 锁定 web 事件序列——WS-1 只改前端 reducer，不动投影，golden 不受影响；若测试意外红，先核对是否触碰了 `event_projection`。
- **`test_app.py::test_web_route_table_is_stable` 是精确路由表 golden**：批次 4（graph）与批次 6（import/delete）新增路由必须同步更新 expected。
- **`test_providers_api.py:96-106` 精确 dict 断言**会随 `is_default` 变红，同步更新；`App.test.tsx:543` 导航名 `'设置'`→`'Provider'`。
- d3 子包：`d3-force d3-zoom d3-drag d3-selection` + 对应 `@types/*`；jsdom 下 force 模拟 `simulation.tick(N)` 手推或注入 fake。
- Skill 删除限定 raw provenance `==='user'`（列表下发 `deletable`）；import 走 JSON 上传有大小上限；写入后必须回读验证（防 builtin shadow 假成功）。
- `is_default` 需同时覆盖 `_runtime_config` 缺失场景（auth-only 组合）。
- `webkitdirectory` 无 React 18 类型 → cast spread；jsdom 无 `webkitRelativePath` → import 测试喂 `importSkill` 桩。
