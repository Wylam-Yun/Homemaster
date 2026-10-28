# V3.8 MindMemOS 原生生命周期集成 ADR

状态：实施期工作 ADR。带「待用户确认」标记的决定按推荐默认推进实现；用户拍板
后以此文件为准修订。日期：2026-10-27。

基线：HomeMaster `e8d90f206c4ef2f1f8f52cb4758024c779c8733c`（分支
`feature/v3.8-implementation`）；MindMemOS `186db4a75122b1d8691933f280bec10191c82c28`
（分支 `feature/v3.8-native-lifecycle`）。

## ADR-1 MindMemOS Server 拓扑与接入方式

**决定**：HomeMaster 通过 `mindmemos_sdk` 调用独立 HTTP Server（spec §3 方式③）。

- Server 由用户自行部署/运维（本机或远端均可）；HomeMaster 不内嵌、不托管
  MindMemOS Server/Pipeline，也不经 Kafka。
- 配置：`mindmemos.server.url`（无默认值，full 模式下缺省 fail closed）+
  `mindmemos.server.api_key` 或 `mindmemos.server.api_key_env` 指向环境变量。
  禁止硬编码 loopback；本机部署用显式 `http://127.0.0.1:<port>` 配置值。
- readiness：composition 启动时对 `GET /v1/health`（或等价 health endpoint）做
  一次真实探测，失败则 memory_full_tier 不 ready。
- 凭据只从被 gitignore 的真实配置或环境变量读取；example 模板只含占位符。

**待用户确认**：正式 Server 部署 owner、endpoint、密钥注入位置。

## ADR-2 Actor / project 身份映射

**决定**：沿用当前 `build_mindmemos_request_context` 的语义，迁到 SDK 边界：

- `user_id = <tenant_id>`（HomeMaster permission subject 的 tenant）；
  `app_id = "homemaster"`；`agent_id = "homemaster"`；
  `session_id = <HomeMaster session_id>`。
- `account_id` / `project_id` 由 MindMemOS Server 按 API key 解析（auth 边界），
  HomeMaster 不伪造；API key 必须映射到 `vanilla` memory_algorithm（部署核对项）。

## ADR-3 隐式 Feedback 调度

**决定**：`memory.implicit_feedback` 配置块：

- `schedule_time`（默认 `"03:00"`）、`timezone`（IANA，默认 `"Asia/Shanghai"`）、
  `enabled`（默认 `true`，仅 full tier 生效）。`window_hours`（默认 24）。
- 采用 spec 暂定的 `03:00 Asia/Shanghai` 为实现默认值，**不代表用户最终批准**。
- Job 身份 = `(project, actor, window_start)`；启动时按持久 watermark 补跑到期窗口；
  多实例经 Outbox lease 保证单 owner。

**待用户确认**：正式运行时刻/时区。

## ADR-4 ALFWorld trajectory compile（产品分支）

**决定（推荐默认，待用户确认）**：选项 A→C 复合 —— 保留 compile 能力与数据流，
但写入路径迁移到新版 MindMemOS 合同：

- 保留 `AlfworldTrajectoryWriter`（原始轨迹 Memory）、`AlfworldCompileJobService`
  （Procedure/诊断 Memory）、Web 人工重编译入口；均改为经 MindMemOS 原生管理
  写入合同（SDK create）写 `experience`/`procedure` 记录，保留 source lineage 与
  `source_trace_sha256`；**不经过** Turn Vanilla Add，也不混入普通对话去重。
- 不删除 compiler/job/API/UI、原始 evidence 或既有 Memory；不把 ALFWorld 输入
  伪装成普通 Turn Add。RoboCasa 仍不验收该能力。
- 若用户改选预览/退役，按所选分支再改 Task 10 文件清单与验收。

## ADR-5 Trace / Outbox 存储

**决定**：`memory.work_store`：

- `sqlite`（默认，单主机）：`data_root/memory_work.db`，WAL + FULL sync；
  多主机 Worker 误配时 readiness fail closed。
- `postgresql`（可选，多主机共享）：`dsn` 从配置/环境读取。同一
  `MemoryWorkStore` Protocol；两 backend 跑同一套合同测试。
- canonical Session Trace、terminal commit、Archive Snapshot 边界、Outbox intent、
  Feedback watermark、Skill invocation 全部在同一事务域；禁止跨库双写。

## ADR-6 MindMemOS 操作账本与跨库可见性

**决定**：按计划推荐方案① —— 不可变 version point + Neo4j 事务性可见指针：

- 新增 Neo4j `operation_ledger`：唯一键 `(project_id, actor_key, operation_id)`；
  reservation 记 `payload_hash`/`status`/`add_record_id`/`fencing`。相同 ID+相同
  hash 重放返回原状态；相同 ID+不同 hash → 409。无共享事务性 Neo4j 时 fail
  closed（readiness 拒绝），不以 Qdrant upsert 顶替。
- 每个变更 action 在副作用前持久不可变 action plan + 稳定 action ID + 目标 ID；
  `outcome_unknown` 先对账。Skill trace claim 用 Neo4j 条件写实现原子 8 条。
- 范围说明：本 ADR 锁定协议；完整"所有读取入口的权威可见性"（含 Search/Get/
  recall 统一过 pointer）为上游较大改造，实施按 spec 门逐步落地并在验收报告里
  区分"已实现的权威可见范围"。

## ADR-7 包分发

**决定**：`mindmemos-sdk` 依赖固定上游提交：

- 开发环境：`[tool.uv.sources] mindmemos_sdk = { path = "../MindMemOS/src/mindmemos_sdk" }`
  指向本地工作区（本机绝对路径仅开发用）。
- 发布：`Requires-Dist` 改为不可变 source reference（`mindmemos-sdk @ git+https://...<pinned sha>`），
  在 Task 12 Gate 0 上游最终提交冻结后锁定并构建 wheel 空 venv 验证。
- `third_party/MindMemOS` 旧 vendor 在迁移完成后整体移除并改为跟踪最终提交的
  gitlink/submodule；wheel `packages.find` 不再包含 `mindmemos*`。
- HomeMaster 代码 `import mindmemos` 全部移除；仅允许 `import mindmemos_sdk`。

## ADR-8 旧 job 处置

**决定**：启动审计对 `experience_jobs/`、旧 add/enrichment queue 遗留、Finalizer
job 只读生成 stale 清单（`data_root/mindmemos/stale_legacy_jobs.json`），停止自动
恢复/执行；不转换、不删除、不覆盖既有真实 Memory 数据。

## ADR-9 Session DELETE

**决定**：保持现状（本地会话文件/运行时对象/附件删除），不做软删除、级联清理
或 MindMemOS 联动，不新增验收。已按用户反馈移出产品决策门。

## ADR-10 配置变更面

新增/修改 `memory.*` / `mindmemos.*` 配置：

- `mindmemos.server.{url, api_key, api_key_env, timeout_s}`
- `mindmemos.actor.{app_id, agent_id}`（默认 `homemaster`/`homemaster`）
- `memory.work_store`（`sqlite`|`postgresql`）+ `memory.work_store_dsn`
- `memory.implicit_feedback.{enabled, schedule_time, timezone, window_hours}`
- 移除：`memory.dreaming_memory_threshold`（阈值语义改为归档驱动）、
  managed neo4j/qdrant 仅在"HomeMaster 托管本地 Server 依赖"语义下保留——
  本地 Server 拓扑时 MindMemOS Server 进程自行持有 DB，HomeMaster 不再托管
  这两个进程（managed_neo4j/managed_qdrant 保留源码供 Server 侧复用，但从
  HomeMaster compose 路径移除）。
