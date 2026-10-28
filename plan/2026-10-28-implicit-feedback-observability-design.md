# 隐性整理观测页设计（2026-10-28）

## 背景

隐性 feedback 当前是完全黑盒：定时 job 和手动「隐性整理」按钮都只暴露 job 状态，
用户无法知道一次整理扫描了哪些 session、改了哪些记忆、改前改后分别是什么。

本设计在 HomeMaster 侧提供只读观测页，**不改 vendored MindMemOS**（结构化理由/
信号证据上游不在 wire 上，持久化 feedback_record 属另立项工作）。

## 数据可得性审计（已对真实部署核实）

可拿（全部只读、现有访问路径）：

| 数据 | 来源 |
|---|---|
| run 列表：触发方式/状态/窗口/时间/错误 | `outbox_jobs` 中 `kind='implicit_feedback'` 行；`operation_key` 含 `manual:` → 手动触发 |
| 扫描的 session 及对话证据 | Qdrant `add_record_v1`：`request_submitted_at ∈ window` 且 `feedback_processed` 的记录；`messages` payload 含完整 user/assistant 对话 |
| 每条记忆改动 before/after | `memory_item_v1`：`content`/`status`/`parent_ids` 血缘；update 新版本 `parent_ids` 指向归档旧版本 |
| session 标题 | 前端 `/api/sessions` 列表 join，后端零改动 |

拿不到（仅存在于服务端进程内/日志）：per-action 理由文本、action→被扫 session
的精确归属、`feedback_processed` 无 run id（重叠窗口扫描归属只能近似）。

## 范围

- IN：run 列表 + run 详情（扫描 session + 对话节选 + 记忆改动前后对照）+ 进行中轮询
- OUT：结构化理由/信号、上游 MindMemOS 改动、定时窗口方向 bug 修复、dreaming run 观测

## 架构

### 新只读组件 `FeedbackRunInspector`

挂在 `application_services["feedback_run_inspector"]`，与 `QdrantNeo4jVerifier`
同一份 qdrant 连接配置（只读 scroll/retrieve，绝不写）：

- `scanned_sessions(project_id, window) -> list[ScannedSession]`：
  scroll `add_record_v1`，客户端过滤 `request_submitted_at ∈ [window_start, window_end]`
  且 `feedback_processed` truthy（实测 payload 存字符串 `"True"`，过滤须容忍字符串/布尔）；
  按 `session_id` 聚合，每条记录产出 `{add_record_id, submitted_at, preview}`，
  preview 取最后一条 user message 截断 ~200 字。
- `action_diffs(actions) -> list[ActionDiff]`：对每个 `{action_id, action, memory_id}`，
  retrieve `memory_item_v1` 得 `{content, status, session_id, parent_ids}`：
  - `update`：after=该点（active）；before=`parent_ids[0]` 点（archived）
  - `delete`：before=该点内容（此前 active）；after=同 id，status=archived
  - `add`：before=null；after=该点
  - 点不可读 → `before/after` 均 null + `missing: true`，前端显示"已不存在"

### 端点（`web/routes/memory.py`，全只读）

`GET /api/memories/feedback-runs`：
```json
{"runs": [{"job_id", "trigger": "manual"|"scheduled", "status", "window",
           "created_at", "updated_at", "actions_count", "error"}]}
```
数据：`memory_work_store` 新增 `list_jobs(kind, limit)`（无则加小查询方法），
`actions_count` 从 `result.actions` 非 noop 计数。

`GET /api/memories/feedback-runs/{job_id}`：
```json
{"job_id", "trigger", "status", "window", "created_at", "finished_at", "error",
 "sessions": [{"session_id", "records_count",
               "excerpts": [{"add_record_id", "submitted_at", "preview"}]}],
 "actions": [{"action_id", "action", "memory_id", "memory_session_id",
              "before": {"content", "status"}|null,
              "after": {"content", "status"}|null, "missing": bool}]}
```

- job 不存在或非 implicit_feedback → 404 `feedback_job_not_found`
- `memory_management_service`/`feedback_run_inspector` 缺失 → 503 `memory_unavailable` /
  `feedback_runs_unavailable`
- 进行中 run：detail 直接返回当前快照——`mark_feedback_processed` 逐 session 落 flag，
  sessions 列表实时增长，前端轮询即可见进度

### 前端 `FeedbackRunsPage`

- `App.tsx` route kind `feedback`；主导航加「记忆整理」入口（位于记忆管理相邻位置）
- 布局：左列 run 卡片列表（触发 badge、时间、状态 chip、"M 条改动"、耗时；session 数
  仅 detail 给出，列表不查 qdrant），右列选中 run 详情（状态/窗口/耗时/error；扫描
  session 卡片含记录数+可展开对话节选；记忆改动列表：类型 badge + before/after 双栏对照）
- 选中 run 非终态时 2s 轮询 detail，终态停
- 对话节选标注"本窗口内被消费的对话"（归属近似，feedback_processed 无 run id）
- `MemoryPage` 的「隐性整理」旁加「整理记录」链接跳到此页（小改）

### 诚实性约束

- 定时 run（03:00 窗口方向 bug 未修）detail 的 sessions 自然为空——如实展示，
  不伪造；文案不做解释性粉饰
- `no_action` run：sessions 可能有记录但 actions 为空，正常展示
- 归属近似仅在重叠窗口时偏离，页面文案明确口径

## 错误处理

- inspector qdrant 查询异常 → 503 typed error，不吞
- job.result 非 dict/actions 缺失 → actions 空列表，sessions 照常返回
- detail 端点全程只读，任何步骤失败不影响 outbox/外部状态

## 测试

- `test_feedback_runs`：list_jobs 过滤/排序/limit；inspector 客户端过滤（窗口边界、
  `"True"` 字符串容忍、按 session 聚合）；action_diffs 三种动作+missing
- `test_app.py`：runs 列表/详情 200、404、503；路由表 golden 加两条
- `FeedbackRunsPage.test.tsx`：列表渲染、选中详情、sessions/actions 展示、
  进行中轮询到终态、missing memory 显示
- 复用现有 `feedbackPollMs` 注入模式

## 文件改动预估

- `src/homemaster/memory/feedback_runs.py`（新：inspector，`~150` 行）
- `src/homemaster/memory/memory_work_store.py`（`list_jobs`，`~20` 行）
- `src/homemaster/application/composition/base.py`（挂 inspector，`~15` 行）
- `src/homemaster/web/routes/memory.py`（两端点，`~90` 行）
- `web/src/components/FeedbackRunsPage.tsx` + `.module.css` + `.test.tsx`（新，`~350` 行）
- `web/src/api/http.ts`（2 方法 + 类型，`~40` 行）；`App.tsx`（route+nav，`~30` 行）；
  `MemoryPage.tsx`（链接，`~5` 行）
- `tests/`：`test_feedback_runs.py`、`test_app.py`、page test（`~300` 行）

## 遗留/后续

- MindMemOS `feedback_record` 持久化 + 查询 API（理由/信号/精确归属）——另立项
- 定时窗口方向 bug——另修，本页会把它的空扫描如实暴露
- dreaming run 观测——同构可扩展，不在本次
