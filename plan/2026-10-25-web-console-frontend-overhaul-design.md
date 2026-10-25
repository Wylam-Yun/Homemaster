# Web Console 前端 overhaul 设计文档

日期：2026-10-25
状态：已评审确认（用户逐条确认问题清单与方案选项）
范围：`web/` 前端 + `src/homemaster/web/` Web adapter 的少量只读/管理端点扩展

## 背景与确认的痛点

用户在真机上使用 Web Console（`scripts/homemaster serve`，full 记忆档）后确认以下问题，
逐条已核实根因：

1. 多轮工具调用的回合里，最终回复渲染到工具卡片上方（顺序颠倒）。
2. 向 `~/.homemaster/skills/` 添加 skill 后，技能页"刷新"无效（路由只读内存快照）。
   技能页无增删管理。
3. 记忆管理页：按 memory_type 分组不直观、来源标签是截断 session ID 且过小、
   裸 memory_id/session_id 无意义、缺少知识图谱视图。
4. "会话"页与侧栏历史会话列表功能重复。
5. 导航"设置"实为 Provider 设置。
6. mimo（404）/step（401）的 `{base}/v1/models` 枚举不可用或受限；
   枚举结果未接入 composer 的 ModelPicker。
7. 默认 provider 在 UI 中不可见（`GET /api/providers` 无 is_default）。
8. 页面风格不统一。

明确不做：主动 dreaming 按钮（用户决定暂缓）；记忆页写操作（本期仍只读+compile）；
飞书通道改动；`?record=1` 录制模式行为变更。

## WS-1 回复顺序修复（真 bug，最高优先）

**根因**：`substrate/runtime.py:_emit_assistant_events` 每个推理轮次发一个
`assistant.reply` → `web/event_projection.py` 投影为 `answer.snapshot`（thinking 同理）。
前端 `web/src/state/conversation.ts:applyTextSnapshot` 删除**所有**同类段并把新文本
插入**第一个**同类段位置——多轮工具穿插时，后续轮的回复被提到工具段之前，
且中间轮回复被整体删除。

**修法**：snapshot 语义改为"替换**最后一个**同类段；不存在则追加到末尾"。
- delta 已流到的回合：原位被 snapshot 结算（位置不动）。
- 重连后只有 snapshot 的客户端：按到达顺序追加，顺序正确。
- `run.completed` 的 `final_reply` 维持 `appendDelta`（仅 answer 为空时兜底）。

**边界**：函数 `applyTextSnapshot`（`web/src/state/conversation.ts:120`）。
**测试**：`conversation.test.ts` 增加用例——
- thinking/answer 各两轮 + 工具段穿插：每轮 snapshot 后各段顺序与文本正确、中间轮文本不丢；
- 无 delta 仅有连续 snapshot（重连场景）：按序追加；
- snapshot 后同轮继续 delta：追加到最后一个同类段（不新建段）。

## WS-2 Skills 刷新 + 管理

**后端**（`src/homemaster/web/routes/skills.py`）：
- `GET /api/skills`：list 前调用 `registry.refresh()`（refresher 已在
  `application/composition/skills.py:30` 接线）；响应增加 `issues` 数组
  （`registry.issues` 的 source/code/detail），前端显示加载失败条目。
- `POST /api/skills/import`：`multipart/form-data` 上传（前端
  `<input type="file" webkitdirectory>` 选文件夹，逐文件上传）。
  服务端校验：必须含 `SKILL.md`，frontmatter 经 loader 同款解析；
  目标目录固定 `~/.homemaster/skills/<skill-dir-name>/`（skill name 与目录名校验，
  拒绝路径穿越）；重名 → 409，`?overwrite=true` 覆盖。成功后 `registry.refresh()`，
  返回新 skill 摘要。
- `DELETE /api/skills/{name}`：仅允许 provenance 为 user/project 来源的；
  bundled/builtin 一律 403。删除目录 + rescan。

**前端**（`pages/SkillsPage.tsx`）：
- "导入技能"按钮 → `webkitdirectory` 文件选择 → FormData 上传 → toast 结果；
- 非内置 skill 卡片显示"删除"（confirm 后 DELETE）；
- 加载失败 issue 以警示条列出；刷新按钮现在真实重扫。

**测试**：后端 pytest——import 合法/缺 SKILL.md/路径穿越/重名 409/overwrite；
delete user skill 成功、delete builtin 403；前端组件测试刷新调用与删除流。

## WS-3 记忆页重做

**后端新增** `GET /api/memories/graph`（`routes/memory.py`）：
- 直查 managed Neo4j（full 档；files 档 503 `memory_unavailable`，与 snapshot 一致）。
- nodes：`Memory`(memory_id, memory_type, status, 摘要标题, session_id, updated_at)、
  `Entity`(name)、`Source`(session_id, title)。
- edges：`EXTRACTED_FROM`(Memory→Source)、`MENTIONS`(Memory→Entity)。
- 不返回记忆全文/原始 JSON；字段走显式 allowlist。

**前端 `MemoryPage` 重写**（仍只读）：
- 视图切换：**列表 / 图谱**（`?view=` 或组件内 state；默认列表）。
- 列表：默认**按来源 session 分组**（snapshot.groups 已按 session 分且带 title），
  切换"按类型"回到现有 groupByType；卡片 = 摘要标题 + 格式化时间 + 类型 chip；
  **不显示裸 memory_id/session_id**。组头显示会话标题 + 条数。
- 图谱视图（新组件 `components/graph/MemoryGraph.tsx`）：
  - 依赖 `d3-force`（力模拟 + drag；~30KB，唯一新增 runtime 依赖）。
  - 记忆节点：圆形、按 memory_type 着色（图例同色），半径略大；
    实体节点：小号胶囊/标签形、中性色。
  - 无 MENTIONS 边的记忆按 session 聚簇兜底（虚拟 session 锚点，弱力向心，
    不作为可见节点渲染，避免空图散沙）。
  - 交互：滚轮缩放、拖拽平移、节点 drag、hover tooltip（摘要标题+类型+时间）、
    点击节点 → 现有 `MemoryDetailDialog`。
  - 顶部过滤条：按类型多选过滤 + 图例。
- 详情弹窗：保留 ID（可复制按钮）、版本历史、compile；文案整理。
- files 档降级卡维持现状。

**测试**：`memory_views.py` 新增 graph 视图函数的 pytest（mock Neo4j 记录→投影）；
前端 `MemoryGraph` 的 nodes/edges→SVG 渲染冒烟测试（jsdom，力模拟步数手推或 mock）。

## WS-4 会话管理页

`pages/SessionsPage.tsx` 重做为管理页（无新端点，复用现有 props/数据）：
- 搜索框（按标题过滤）；
- 排序切换：最近更新（默认）/ 标题 / 消息数；
- 每行：状态点、标题、相对时间+条数、归档徽标 + 操作按钮
  （打开 / 重命名 inline / 归档 / 删除——行为与侧栏完全一致，复用同一回调）。
- 侧栏历史列表**保持不变**；该页是"全量管理"视角。

## WS-5 Provider 设置与默认可见

- `provider_config.list_profiles()` 每项加 `is_default`（与 `providers.default` 比对）。
- 前端 `ProviderProfile` 加 `is_default?: boolean`；卡片显示"默认"徽标；
  非默认 chat provider 行内"设为默认"按钮 → `PATCH /api/providers/{name}`
  `{set_default:true}` → toast 显示新默认。
- 侧栏导航"设置"→"Provider"；topbar 标题同步。

## WS-6 模型枚举接入

- `ModelPicker`：provider 选中后懒加载 `api.providerModels(name)`
  （Map 缓存，同 provider 不重复请求）；
  - `source === 'live'`：models 进入 Model 下拉（含"自定义…"项）；
  - `source === 'fallback'`：UI 显示"该 provider 不支持在线枚举"，
    下拉仅保留配置模型 + 自定义输入。
- Settings 枚举区：`fallback` 徽标文案改为"在线枚举不可用，显示兜底列表"。

## WS-7 风格统一基线

- `styles.css :root` 扩充 token：间距阶（--space-1..6）、圆角阶（--radius-s/m/l）、
  字阶、阴影、z-index 层、按钮/徽标/卡片/页头的统一规范类。
- 新增 `components/ui/`：PageHeader、Card、Badge、Button(variant) 小组件，
  新页面（记忆页/会话页重做）直接用；旧页面在 WS-8 收编。
- 保持现有深色基调与 --accent 色系，不换新主题。

## WS-8 收尾一致性

逐页面对照 token 收编（PermissionsPage/OnboardingPage/SettingsPage/composer 各 module.css），
消除任意像素值与重复定义。

## 实施顺序

| 批次 | 内容 | 依赖 |
|---|---|---|
| 1 | WS-1 回复顺序 | 无 |
| 2 | WS-5 + WS-2 刷新部分 | 无 |
| 3 | WS-7 token 基线 | 无 |
| 4 | WS-3 记忆页（列表重做 → graph 端点+视图） | 批3 token |
| 5 | WS-6 模型枚举 | 无 |
| 6 | WS-2 导入/删除管理 | 无 |
| 7 | WS-4 会话管理页 | 批3 token |
| 8 | WS-8 全站收编 | 全部 |

## 验证

- `web/`：`npm test`、`npm run typecheck`、`npm run build`。
- 后端：`pytest` 相关 web/服务测试目录。
- 真机走查（loopback serve）：多轮工具回复顺序、skill 导入/删除、
  记忆页图谱交互、provider 默认徽标与设为默认、ModelPicker 枚举、会话页管理操作。
- 每批交付更新 CHANGELOG（与 commit message 同源）。

## 明确不做（本期）

- 主动 dreaming 触发按钮与阈值配置 UI。
- 记忆的新增/编辑/删除/归档/恢复写操作（仍只读 + compile）。
- 图谱节点上直接编辑关系。
- 会话页批量多选操作。
- 更换深色主题基调。
