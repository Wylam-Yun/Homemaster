# MindMemOS 原生生命周期集成实施计划

状态：计划草案已整合两位独立 reviewer 的初审意见；最新修改（compile 产物样例与删除边界）尚未复审。未开始源码实施或 RoboCasa 验收。审查记录见 `mindmemos-native-lifecycle-plan-review.md`。文中的技术方案尚须通过对应真实环境门，不能把计划中的验收 checkbox 当成测试已通过。

> **For agentic workers:** 按任务顺序实施并逐项勾选。每个功能必须先通过定向真实黑盒，再通过其专属 R5；全部功能门通过后才能执行 R20。计划中的兼容清理是最终发布条件，不得保留新旧运行分支。

**Goal:** 将 HomeMaster 的记忆管理改为 MindMemOS 指定新版的原生生命周期：终结 Turn 后异步 Vanilla Add、Agent 按需 Search、显式与每日隐式 Feedback、归档后异步 Dreaming、专用 Skill Trace 与每 8 条轨迹的 Skill Evolution，并提供完整的 Web 记忆人工管理。

**Architecture:** HomeMaster 通过 MindMemOS SDK 调用独立 HTTP Server。Canonical Session Trace、terminal commit 与 Add intent 由同一个 `MemoryWorkStore` transaction 持久化；单机 backend 使用 SQLite，多主机 backend 使用共享 PostgreSQL，不能把 Trace 和 Outbox 拆到不同事务域。MindMemOS 使用共享 operation ledger、不可变 action plan 和稳定目标 ID 处理跨服务重试与 checkpoint。Agent 只等待本地 intent 被持久接受，所有远端记忆工作由可恢复 Worker 完成；完成只以外部业务回执和独立客户端对原始存储的逐项读回判定。

**Tech Stack:** Python 3.11+、uv、FastAPI、MindMemOS HTTP API 与 sync/async Python SDK、SQLite（单机 HomeMaster Trace/Outbox transaction）、PostgreSQL（多主机共享 HomeMaster Trace/Outbox transaction）、Neo4j operation ledger、Qdrant/Neo4j 原始读回、React/TypeScript、RoboCasa RPent Target50 manifest。

---

## 依据与冻结输入

- 需求真理源：`plan/V3.8/mindmemos-native-lifecycle-spec.md`。
- HomeMaster 基线：`e8d90f206c4ef2f1f8f52cb4758024c779c8733c`。实施前检查 `git status`，保留并整合现有未提交内容；不覆盖已有修改。
- MindMemOS 起始源码：`/Users/wylam/Documents/workspace/MindMemOS`，当前审计提交 `186db4a75122b1d8691933f280bec10191c82c28`。先在该仓库按本计划补齐原生 API，再记录最终不可变提交；HomeMaster 只能依赖最终提交，不能回退到 `1364c0866203ab6d6585e4ef22cbadb5a60ae307` 或旧 HomeMaster vendor 补丁。
- RoboCasa 远端：`hkust4:/home/haodong2/weilin/red_bird/RPent`。实施时重新断言 RPent commit 为 `0ae9f9edc67cd5c184915e83a80b32c1ec241526`，manifest `robots/robocasa/eval/target50_v2.json` 的 SHA-256 为 `fca5eba7de3635930fced5b11d5a1dff35c81ae141988753a6db80eb729d1575`；任何不一致都停止验收。
- 固定 R5 顺序：atomic / target / seed 1，`CloseBlenderLid`、`CloseFridge`、`CloseToasterOvenDoor`、`CoffeeSetupMug`、`NavigateKitchen`。
- 固定 R20 顺序：manifest 中全部 18 个 atomic task 的 seed 1，然后 `CloseBlenderLid` 和 `CloseFridge` 的 seed 2。必须写入冻结的有序 cell manifest，不能用当前 `load_cells()` 的 task-major seed 展开推导它。
- 只使用隔离的临时 Project、Qdrant、Neo4j 和 Skill Store；不对现有真实记忆库做清除、迁移或覆盖。旧 job 只生成清单并标记 stale。

## 实施前决策门

以下项目没有在规格中最终锁定。准备工作和不依赖这些选择的实现可继续；会改变用户可见能力、启动生产调度或执行外部验收的任务必须等 ADR 记录明确决定后才能继续：

1. 隐式 Feedback 的每日运行时刻和时区。规格暂定 `03:00 Asia/Shanghai`，实施以可配置的 IANA 时区与本地时刻表达，不把该默认值当成用户最终批准。
2. MindMemOS HTTP Server 的部署/运维 owner、可达 endpoint、认证方式、project/actor 映射和密钥注入位置。HomeMaster 端点不得硬编码 loopback；至少验证本机 Server 和远端 Server 两种拓扑。
3. **ALFWorld trajectory compile（独立待决能力）：** 当前 compiler 只接受 ALFWorld trajectory，不存在对应的 RoboCasa compiler。成功输入生成结构化 Procedure，失败/unknown 输入生成不可执行诊断；`validation_status="verified"` 只说明来源 hash 与结构校验通过。真实样例及 receptacle 丢失问题见 `trajectory-compile-current-output-example.md`。该能力不属于九个 V3.8 核心生命周期功能，RoboCasa R5/R20 也不能验收它。保留现状、只读预览、人工编译写入或退役各有不同的源码/API/UI、存储和 ALFWorld 验收范围，目前没有选定分支。实施前须由 ADR 锁定；在此之前不得将其默认冻结、改写为 Vanilla Add 或从条件文件清单中删除。与 canonical Turn Add 重复的普通对话/benchmark Add sink 和 ALFWorld compile source 是两条独立数据流，分别处理。

Session DELETE 属于现有本地会话管理：移除本地会话文件、运行时对象和上传附件，不删除 MindMemOS 长期记忆。用户确认这不是本轮重点，V3.8 保持该行为，不新增软删除、回收站、级联清理或专门验收；MindMemOS 记忆管理 CRUD 仍属于本轮范围。

### 关键设计选择与代价

| 决策 | 候选 | 代价 | 计划选择 |
|---|---|---|---|
| HomeMaster 到 MindMemOS 的调用 | 进程内直接调用 Pipeline；Agent 直接调用 Kafka async API；HomeMaster Worker 经 SDK/HTTP 调用独立 Server | 进程内调用会复制服务合同；Kafka 把队列语义带入 Agent 运行链；独立 Server 需要部署与认证配置 | 采用 SDK/HTTP。Server 返回同步业务终态，HomeMaster Worker 在 Agent 之外等待；本地 Outbox 承担恢复。 |
| 跨 MindMemOS Worker 的幂等/claim 账本 | Qdrant upsert；HomeMaster 单机文件锁/SQLite；额外部署 SQL 服务；MindMemOS 所用 Neo4j 的唯一约束与事务 | Qdrant upsert 不是 compare-and-set；本机锁不能服务多主机；SQL 增加依赖和运维；Neo4j 会增加运维记录与约束 | 采用 Neo4j 事务账本，唯一键覆盖 operation/job/action/trace identity，并用 lease/fencing 保护过期 Worker。启动 readiness 验证约束真实存在。若部署拓扑无法提供共享事务性 Neo4j，停止发布并先作架构决策，不以 Qdrant upsert 替代。 |
| HomeMaster 内 MindMemOS 代码分发 | 把 Server/Pipeline 整体塞进 HomeMaster wheel；只在开发环境使用本地路径包；依赖固定 Git commit 的 SDK | 整体打包会重新嵌入引擎；仅路径依赖不满足发行安装；固定 SDK 引用需要可访问的不可变远端提交 | `third_party/MindMemOS` 改为跟踪最终上游提交的 gitlink/submodule；HomeMaster wheel 只依赖固定提交的 `mindmemos-sdk`，不再打包 `mindmemos` Server/Pipeline。开发环境可指向该检出中的 SDK，但发布 wheel 的 `Requires-Dist` 必须保留标准 PEP 508 不可变 source reference，并用空 venv 验证。 |
| HomeMaster Outbox 拓扑 | 每个主机独立 SQLite；单机 SQLite + 多主机共享 PostgreSQL；所有安装强制 PostgreSQL | 独立 SQLite 不能跨主机协调；PostgreSQL 增加部署依赖；强制 PostgreSQL 会增加单机启动成本 | 支持 SQLite 单机和 PostgreSQL 多主机两种 backend，均实现 `MemoryWorkStore` 并跑并发/恢复门。`memory.work_store=sqlite` 只允许一个主机拥有 Worker；配置多主机 Worker 时必须用共享 PostgreSQL，否则 readiness fail closed。 |
| ALFWorld trajectory compile | A. 暂时维持现有独立行为；B. 增加只读预览；C. 保留人工触发的编译与写入；D. 明确退役活动路径并保留原始证据和已有 Memory。 | A 避免在用途未定前改变现有行为，但不能把旧 writer 当成新生命周期兼容层；B 需新增预览入口并证明不写外部 Memory；C 需定义新版 lineage/写入合同并做 ALFWorld 专属验收；D 失去现有能力并需审计历史结果可读性。所有分支均不得把 ALFWorld 数据伪装成普通 Turn Add。 | 未选定。须在实施 ADR 中选择分支；产物样例与已知缺陷见 `trajectory-compile-current-output-example.md`。 |

关于编译结果，当前源码的“编译”不是把轨迹编译成程序文件。样例中的成功结果是结构化 Procedure：第一步导航至 cabinet 1，第二步把 mug 放入，但当前输出遗漏了 receptacle `cabinet`；失败或 unknown 只得到不可执行诊断。它当前会把结果另写为带来源 lineage 的 MindMemOS experience Memory。`verified` 只证明来源 hash 与结构校验通过，不证明字段完整或 Procedure 已在新任务中复用成功。是否继续这条 ALFWorld 独立数据流由实施 ADR 决定；当前没有批准暂停写入或退役。

## 接口与状态合同

实现任务均使用以下共享合同；新增字段在 MindMemOS 的 API schema、mapper、service、持久层、sync SDK、async SDK 与 worker 之间同步。同步/异步 SDK 的公开方法集合必须有 parity audit。

| 对象 | 稳定身份 | 持久内容/状态 | 允许推进的终态 |
|---|---|---|---|
| Runtime Turn | `(session_id, turn_id)`；`event_seq` 单调递增 | canonical、按序 user/assistant/tool-call/tool-result 与唯一 terminal commit | 在同一 `MemoryWorkStore` transaction 提交 Trace、terminal 状态和唯一 Add intent；任何一项失败都整体回滚 |
| Turn Add | `operation_id = stable(session_id, turn_id)` + 服务端 `payload_hash` | 预留的 `add_record_id`、提取/写入计划、memory IDs、每个目标写入状态 | `completed` 且 API 业务码成功、operation 对账唯一、每个 Memory ID raw readback；零 Memory 也须有成功 AddRecord |
| HomeMaster Outbox | `job_id`，唯一 operation key | `accepted / processing / completed / no_action / failed / outcome_unknown / stale`、attempt、request hash、回执 | 外部状态对账后完成；unknown 先 reconcile |
| Explicit Feedback | `feedback_operation_id` + 每 action `action_id` | 精确 provider-visible 对话、已验证 memory IDs、逐 action receipt/checkpoint | 每 action 外部终态和 raw/lineage readback 后完成 |
| Implicit Feedback | `(project, actor, window_start)` | 原生 activity snapshot/cursor、job ID、每 action checkpoint、watermark | 所有 action terminal/readback 后才推进活动处理水位 |
| Archive/Dreaming | `archive_id`，以 session 与锁定 `event_seq` 边界唯一 | Turn 区间、Add operation IDs、成功 seed AddRecord IDs、每 entity/action 状态 | 只消费锁定 seed；`completed` 或经验证的 `no_action` |
| Skill invocation | `invocation_id` | local/cloud skill ID、准确版本和 SKILL.md hash、Turn、真实关联工具步、score/outcome | 封存后有效；仅完整封存的 trace 可 claim |
| Skill Evolution | `(cloud_skill_id, batch_id)` 与恰好 8 个 trace IDs | 原子 claim、parent version、draft version、逐条 consumed 状态 | draft 真实可读回且恰好 claim/消费 8 条之后完成 |

Qdrant upsert 不能为跨存储副作用提供 compare-and-set，因此 Neo4j fencing 不能被当作 Qdrant 写入的 fencing。每种变更操作先持久化不可变 action plan、稳定 action ID、全部目标 ID 和确切写入内容。跨库协议作为 Task 2 的前置技术验证：候选为①不可变版本 point ID + Neo4j 事务性 current-version/visibility 指针，②能在真实存储边界拒绝旧写者的 fenced writer，③严格单写者且恢复前证明旧 writer 和所有在途写均已终止。①增加所有读取入口的版本可见性检查；②需要存储层支持与独立部署组件；③牺牲故障恢复可用性且远程断连不能证明旧请求消失。**推荐验证①**，但不得未经验证就声称现有 native writer 已满足；将选定协议、涉及数据结构/API 的差异与代价写 ADR，若需要偏离 spec 的用户语义，先更新 spec 再实施。

采用①时，同一逻辑 resource 的每个 action 只写该 action 专属的不可变 version point；禁止原地覆盖另一个 action 的内容/status。当前有效 version、archive visibility 与 operation checkpoint 在 Neo4j 同一事务内以 expected parent revision + fencing token 条件提交；旧 token 不能推进 pointer。Search/Get/List、Add 内部 recall、Feedback/Dreaming 邻域读取和 Skill Version 读取都必须核对这一权威可见性，不能只按 Qdrant `active` payload 过滤；旧 version 仅在 history 中可见。一个 action 进入 `outcome_unknown` 后先读回其 version 与 pointer，再继续后继 action。真实双 Worker 门固定验证：A 的 Qdrant 写延迟，B 接管并完成 A 对账及后继更新，A 最后抵达；raw 历史完整、pointer 仍指向 B、所有公开读取/搜索与内部 recall 都返回 B，绝不复活 A。此门不过不得进入 Task 3 及后续变更功能实施；每个 ledger 状态变更保留业务码与原始存储证据。

## 文件地图

### HomeMaster Runtime / composition / 持久队列

- 新建 `src/homemaster/memory/session_trace_store.py`：canonical Session/Turn/event store facade，使用 `MemoryWorkStore` 的同一事务实现 terminal commit、event sequence、immutable snapshot 和 replay 查询。
- 新建 `src/homemaster/memory/memory_work_store.py`：`MemoryWorkStore` Protocol、SQLite 单机 backend 与 PostgreSQL 共享 backend；同一 backend transaction 管理 canonical Trace、terminal commit、Archive boundary、session delete marker 和 durable Outbox/lease/retry/status，不暴露 DB 路径给业务调用方。
- 新建 `src/homemaster/memory/mindmemos_client.py`：SDK sync/async client facade、actor/project 注入、response-code 校验与 operation reconciliation。
- 新建 `src/homemaster/memory/memory_workers.py` 与 `src/homemaster/memory/memory_jobs.py`：Add/Feedback/Dreaming/Evolution job dispatch、幂等和结构化审计字段。
- 修改 `src/homemaster/application/runtime.py`、`run_driver.py`、`session.py`、`session_manager.py`、`tool_executor.py`、`composition/base.py`：权威 RunResult terminal commit、Trace ownership、非阻塞 admission、worker readiness/close、tool-call invocation 关联。
- 修改 `src/homemaster/application/composition/skills.py`、`src/homemaster/skills/types.py`、`loader.py`、`registry.py`：实际加载版本登记和 invocation context。
- 新建/修改 `src/homemaster/config/config.py`、`src/homemaster/resources/homemaster.example.yaml`（`config/homemaster.example.yaml` 现为指向该资源的 symlink）与 setup/doctor：MindMemOS endpoint、凭据来源、project/actor mapping、调度时区/时间、Trace/Outbox backend 配置；真实配置保持忽略，提交字段齐全且无真实密钥的模板。

### MindMemOS 上游源码（相对 `/Users/wylam/Documents/workspace/MindMemOS`）

- HTTP 契约与业务入口：`src/mindmemos/mindmemos/api/schemas.py`、`mappers.py`、`routes.py`、`services/memory_service.py`、`services/skill_service.py`、`skill_schemas.py`、`skill_routes.py`。
- SDK 契约：`src/mindmemos_sdk/mindmemos_sdk/memory/models.py`、`core.py`、`client.py`、`async_client.py`，以及 Skill client/model modules；同步和异步入口共享字段语义。
- Durable identities / visibility：`src/mindmemos/mindmemos/infra/db/neo4j.py`、`infra/db/schema.py`、`infra/db/registry.py`、`infra/db/models.py`、`infra/db/collections/add_record.py`、`pipelines/memory_db/add_record_store.py`、`pipelines/memory_db/operation_records.py`、`writer.py`、`reader.py`、`catalog.py` 与 Search/Skill Version read paths；新增 operation ledger repository、unique constraint 和权威版本可见性合同。
- Turn/tool preservation：`typing/memory.py`、`components/chunker/vanilla/turn_grouper.py`、`pipelines/add/vanilla/vanilla_add.py`。
- Implicit feedback：`typing/activity.py`、`components/activity/collector.py`、`typing/service.py`、`pipelines/feedback/implicit.py`、`feedback/executor.py`、相关 memory repositories。
- Dreaming：`typing/service.py`、`api/schemas.py`、`api/mappers.py`、`api/services/memory_service.py`、`pipelines/dreaming/default.py`、`workers/memory_dreaming.py`。
- Skill trace/evolution：`typing/skill.py`、`infra/db/collections/skill.py`、`pipelines/skill/evolution.py`、`pipelines/skill/version_store.py`、`workers/skill_evolve.py`。
- Memory administration：`api/schemas.py`、`api/routes.py`、`api/services/memory_service.py`、SDK memory CRUD models/clients；所有修改仍走 MindMemOS native writer/version/graph semantics。

### HomeMaster Agent tools / Web / legacy paths

- 修改 `src/homemaster/tools/memory_tools.py`、`tools/contracts.py`、`application/tool_executor.py`、`src/homemaster/adapters/profiles.py`：保留按需 Search、显式 Feedback、记忆维护工具，新增专用 Skill Trace；删除普通 Add 注册和所有自动召回注册。
- 修改 `src/homemaster/memory/feedback_context.py` 与其 provider-attempt 数据来源：只提交成功 attempt 中真实可见对话和按 actor 重读通过的 raw IDs。
- 修改 `src/homemaster/web/memory_views.py`、`web/routes/memory.py`、`web/schemas.py`、`web/serve.py`、`web/app.py`：调用 SDK/API；plain 与 structured Create/Update 都由 MindMemOS contract 支持，删除 `add_flat` update fallback 和 HomeMaster DB 写入。
- 修改 `web/src/api/http.ts`、`web/src/components/MemoryPage.tsx`、`MemoryDetailDialog.tsx`、`memorySummary.ts`、`App.tsx` 与对应 `*.test.tsx`：完整 CRUD、结构化内容编辑/历史、typed 错误及成功/归档后的读回刷新。
- 删除旧 HomeMaster 专用生命周期路径：Finalizer/extractor/schema episode、进程内 Pipeline、automatic recall、旧闭包队列，以及对应旧测试和 prompt/profile/config。删除前先把 Runtime user evidence registration 从 `memory_recall.py` 解耦并保留其授权语义。普通重复 trajectory Memory writer 从 runner 断开，新对话输入统一走 canonical Turn Add；原始 benchmark trace/evidence 保留。ALFWorld compiler、job/source DTO、既有 Memory 和历史查看不在默认删除清单中。按待确认的冻结方案，仅断开 source writer 的自动接线、compile admission/recovery；不把其旧依赖重新接入新 Runtime，不引入新旧兼容运行分支。永久退役须另有用户决定。
- 修改 CLI/Web session entrypoints：`cli/interactive_shell.py`、`cli/shell_commands.py`、`cli/run_command.py`、`web/routes/session_ops.py`、`web/app.py`，统一提交 Archive Snapshot；`/new` 不 drain，正常 shutdown 才按 owner 规则 drain。

### RoboCasa / docs / release

- 修改 `src/homemaster/robocasa/benchmark/cells.py`、`benchmark/runner.py`、`robocasa/types.py`、`cli/benchmark_robocasa.py`、`cli/app.py`：接受锁定顺序的 cell manifest，维持 application-owned durable worker 跨 cell 生命周期恢复。
- 新建 `scripts/verify_v38_manifest.py`、`scripts/verify_v38_mindmemos_acceptance.py` 和对应 `tests/homemaster/robocasa/test_v38_acceptance_manifest.py`、`test_v38_independent_verifier.py`：从原始 source trace 独立重建期望操作集合，并用独立只读 Qdrant/Neo4j/Skill Store 客户端在业务进程退出后直接读回，输出 per-cell/per-operation JSON 报告。
- 更新 `docs/architecture/memory-system.md`、`docs/memory-user-guide.md`、`README.md`、`CHANGELOG.md`、`docs/session-handoff.md` 与本计划旁的 ADR/验收报告。历史方案可保留为历史记录，但标明 superseded，不得留下指导运行旧生命周期的现行文档。

## 执行任务

以下命令中的新测试文件由对应 Task 创建；它们不是现有测试已经通过的声明。普通 pytest 命令用于内部回归。HomeMaster 默认排除 `live_memory/live_api`，真实外部门必须另用显式命令覆盖该默认值；所有真实门须记录 collected/selected/passed/skipped/deselected 数量，选中用例大于零且该门所需用例全部执行、无 skip/xfail 才算通过。

### Task 1：锁定集成 ADR、实施选择与上游起始 revision

**Files:**
- Create: `plan/V3.8/mindmemos-native-lifecycle-adr.md`
- Modify: `src/homemaster/resources/homemaster.example.yaml`、`pyproject.toml` / `uv.lock`（只配置可在开发中使用的 MindMemOS workspace source override；最终不可变 pin 延后至 Task 12 Gate 0）
- Test: `tests/homemaster/test_memory_runtime_setup.py`、新增 package boundary audit

- [ ] 记录 Server owner/topology、endpoint、auth、actor/project mapping、隐式 Feedback schedule，以及用户对 trajectory compile 的决定；将已决定配置写入去敏模板，不提交密钥。未决定前不改 compile writer/compiler/job/API/UI 行为，也不把旧 compile pipeline 接成新版生命周期的一部分。
- [ ] 核对 MindMemOS 工作区 clean、HEAD 精确为本计划起始提交、remote branch 与依赖版本；上游工作只从这个基线延伸，不 checkout 旧 HomeMaster vendor 逻辑。
- [ ] 配置仅供开发的 MindMemOS workspace source override，使 HomeMaster 集成测试能调用当前本地上游工作区；此阶段不声明最终不可变提交，不构建发布 wheel，也不将 SDK source override 当作发布验收。
- [ ] 跑 `uv run pytest tests/homemaster/test_memory_runtime_setup.py -q` 与相关 config test。预期：缺少 MindMemOS endpoint/identity 时 setup/doctor 明确失败关闭。

### Task 2：实现 MindMemOS 事务账本与公开操作合同

**Files:**
- MindMemOS: 上述 `infra/db/neo4j.py`、schema bootstrap、`infra/db/registry.py`、新增 `infra/db/collections/operation_ledger.py`、API schemas/services/routes、SDK models/client modules
- Test: 新建 `tests/infra/db/test_operation_ledger.py`、`tests/infra/db/test_version_visibility_recovery.py`、`tests/api/test_operation_status.py`；扩展现有 `tests/mindmemos_sdk/test_memory_client_parity.py`

- [ ] 先为 Add operation reservation 编写失败测试：两个独立 service client 同时 reserve 同 `(project_id, actor_id, operation_id)` 只返回同一 ledger row；相同 payload hash 重放返回原状态；不同 hash 返回 HTTP 409；lease 过期后旧 fencing token 不能 commit。
- [ ] 在 Neo4j 迁移/bootstrap 中创建 operation key 的唯一约束并验证约束可查询；reservation 使用 Neo4j transaction 原子 `MERGE` 与 payload-hash compare。数据库不支持约束或事务时 Server readiness 失败，不自动降级到 Qdrant upsert。
- [ ] 增加 SDK model `OperationStatusResult`，其字段为 `operation_id`、`status`、`add_record_id`、`memory_ids`、`request_id`、`code`；增加 `GET /v1/memory/add/operations/{operation_id}` sync/async SDK 同名方法。未知 operation 返回明确的 not-found 业务码，不伪造空 Add。
- [ ] 为 Add/explicit Feedback/implicit Feedback/Dreaming/Skill Evolution 与每个变更 action 定义统一状态枚举 `accepted | processing | completed | no_action | failed | outcome_unknown | stale`。每个业务 operation 均须有公开 submit/status 合同：稳定调用方 operation ID、payload hash、服务端状态查询、action plan/action IDs、每 action checkpoint、业务码与 reconciliation receipt。重复 ID/相同 hash 返回原 operation，不同 hash 冲突；未知状态不伪造成成功。
- [ ] sync 与 async SDK 为所有 submit/status/action-history 方法提供相同语义与必填字段；`async` 仅表示 HTTP I/O 异步，不代表业务 operation 已终态。显式 Feedback 工具等待所有 action 到可验证终态，Implicit Feedback、Dreaming 和 Evolution 由后台 worker 轮询/恢复。
- [ ] 每个外部变更首次发生前持久化不可变 action plan 与目标 ID。测试“外部写已生效、HTTP 回执丢失”后 status/reconcile 可从原始存储恢复对应 action，不重复创建版本/Memory；测试租约转移后旧 Worker 晚到只可重放相同 action，并且不会启动后续 action。
- [ ] 先完成上述跨库协议的真实技术验证与 ADR，再接入全套变更 writer/reader；新增 `tests/infra/db/test_version_visibility_recovery.py`，逐项覆盖 Qdrant 晚到写、Neo4j pointer CAS、archive 不复活、Search/Get/List/内部 recall 的可见性，成功不能仅靠 ledger 拒绝 commit 来断言。
- [ ] 让 sync/async SDK 的公开方法、required fields 和错误映射由 parity test 对比；运行 MindMemOS API、ledger、SDK 定向测试及 `uv run ruff check src tests`。预期：多进程唯一键、冲突码和 stale fencing 行为逐实例正确。
- [ ] 从 MindMemOS 仓库根目录运行 `uv run pytest tests/infra/db/test_operation_ledger.py tests/infra/db/test_version_visibility_recovery.py tests/api/test_operation_status.py tests/mindmemos_sdk/test_memory_client_parity.py -q` 与 `uv run ruff check src tests`；预期每个 operation key、状态和冲突独立断言，不存在全局聚合掩盖失败。真实跨库门由 Task 12 的 live suite 显式运行，不能以 mock-only 通过代替。

### Task 3：MindMemOS Vanilla Add 保存 operation reservation、tool messages 与可恢复 write plan

**Files:**
- MindMemOS: `api/schemas.py`、`api/mappers.py`、`api/services/memory_service.py`、`typing/memory.py`、`typing/service.py`、`components/chunker/vanilla/turn_grouper.py`、`pipelines/add/vanilla/vanilla_add.py`、`pipelines/memory_db/add_record_store.py`、`workers/memory_add.py`
- SDK: `mindmemos_sdk/memory/models.py`、`core.py`、`client.py`、`async_client.py`
- Test: MindMemOS Vanilla Add/API/worker tests and SDK parity tests

- [ ] 添加端到端合同测试：标准 `assistant` tool-call 消息与 `tool` result 消息按 timestamp/输入顺序进入 Vanilla Turn 分组；`tool_call_id` 可在两侧 JSON 内容中对应；内部 reasoning 不存在于 Add payload。
- [ ] AddRequest 接收一等 `operation_id`；服务端在提取前 reserve AddRecord ID，提取结果得到后、任何业务存储副作用前持久化确定性 write plan 和所有 Memory/version IDs。提取结果/plan 已持久化后重试不得重新生成；结果尚未持久化的提取崩溃可恢复计算，但复用同一 AddRecord reservation 且不能提前写 Memory。`enable_entities=true` 的实际运行 config 必须由 server schema/diagnostic 可读。
- [ ] 用相同 operation ID 重放时复用原 AddRecord、提取结果和 Memory IDs；hash 不同时 409。为 LLM 提取后、write plan 保存后、每个 Qdrant/Neo4j write 后、业务回执前分别注入进程崩溃并恢复。
- [ ] 同步 Add 最终回执包含 `operation_id`、`add_record_id`、状态/业务码和全部 Memory IDs/version IDs；在 Add 完成时逐个读回 scope、当时的 visibility、content、type 与 Entity/MENTIONS 关系。后续 Feedback/Dreaming 可合法更新/归档：最终验收核对 Add 对应不可变版本及后续 lineage、最终有效 pointer，不要求早期 Memory 永远 active 或正文不变。部分外部写入失败时状态是 failed/unknown，重试复用已保存 plan。
- [ ] 运行 MindMemOS Vanilla Add、API、worker、Qdrant/Neo4j integration tests；真实隔离 DB crash suite 中每个 crash point 都断言 operation 唯一和逐条 raw readback。
- [ ] 从 MindMemOS 仓库根目录运行 `uv run pytest tests/pipelines/add tests/pipelines/memory_db tests/api/test_memory_service_async_add_record.py tests/api/test_turn_add_idempotency.py tests/workers/test_memory_add_recovery.py -q`；真实 DB crash suite 每个注入点单独输出结果。

### Task 4：HomeMaster canonical Session Trace、terminal commit 与 durable Add Outbox

**Files:**
- Create: `src/homemaster/memory/session_trace_store.py`、`memory_work_store.py`、`memory_jobs.py`、`memory_workers.py`
- Modify: `application/runtime.py`、`run_driver.py`、`session.py`、`session_manager.py`、`composition/base.py`、`config/config.py`
- Test: 新建 `tests/homemaster/memory/test_session_trace_store.py`、`test_memory_work_store.py`、`test_memory_workers.py`；扩展 `tests/homemaster/application/test_session_manager.py` 与 runtime tests

- [ ] 先写 Trace Store contract tests：唯一 `(session_id, turn_id)` terminal commit、event_seq 单调性、重复 terminal 等价返回、冲突 terminal 拒绝、进程重启后完整 replay、活动 Turn 不会被误判为 interrupted。以 SQLite transaction + WAL/FULL sync 保证 admission fsync；Trace payload 不包含 reasoning/private permission/credential/binary body。
- [ ] 在权威 `RunResult` 成功、失败、预算耗尽、取消、guard-triggered 和被恢复逻辑明确标记 interrupted 的全部路径上写 terminal commit；Provider retry 不创建第二个 turn_id。先将 `register_user_evidence` 移到独立 Runtime service，再删除 automatic-recall owner。
- [ ] 对单机 SQLite 与共享 PostgreSQL，terminal commit 将 Trace snapshot、terminal 状态和 Add intent 写入同一数据库 transaction；不允许跨数据库或“先 Trace 后 Outbox”的双写。SQLite/PostgreSQL 持久化错误、磁盘满、事务冲突时整体回滚，Runtime 返回 typed failure，绝不返回 `accepted` 或伪报 Add 已排队。
- [ ] 启动审计 terminal Trace/Outbox 唯一键一致性；新版本正常路径只能同事务提交，不能引入第二种双写模式。发现差集时记录异常并幂等补建 intent，测试该恢复不会重复 Add；不通过时不对外 ready。
- [ ] Outbox payload 指向 canonical Trace snapshot，不复制另一份整 Session；Trace 与 intent 原子持久接受后 RunResult 正常返回，不 await SDK/HTTP/LLM/embedding/远端 DB。
- [ ] Worker readiness 同时检查 Outbox lease worker、SDK endpoint/auth、MindMemOS health 和必要存储；未 ready 时不接收会产生无法处理的 intent。后台处理以同一 operation ID 调 SDK sync Add，并在未知 HTTP 结果后先 GET operation status。
- [ ] Worker 只在 API 业务码成功且 AddRecord/全部 Memory ID 从真实隔离 DB 读回后写 completed；0 Memory 时也要求 AddRecord 成功。日志记录 operation/session/turn/request/status/attempt/duration/IDs，不记完整敏感 payload。
- [ ] 对 admission 后立即终结用户 Run 的路径设置延迟 HTTP server：Run 必须在后台请求释放前返回 `accepted`；随后 release server 并验证真实 AddRecord 及每个 Memory raw readback。杀 Worker 在 Trace/Outbox transaction 提交前、提交后、HTTP request 前后重启并断言提交后恰好一条唯一 Add、提交前无 `accepted`。
- [ ] 从 HomeMaster 根目录运行 `uv run pytest tests/homemaster/memory/test_session_trace_store.py tests/homemaster/memory/test_memory_work_store.py tests/homemaster/memory/test_memory_workers.py tests/homemaster/application/test_session_manager.py -q`；Trace/intent 同事务故障注入、backend 并发与持久错误结果逐 case 验证。

### Task 5：按需 Search 与显式 Feedback Agent tools

**Files:**
- Modify: `src/homemaster/tools/memory_tools.py`、`tools/contracts.py`、`application/tool_executor.py`、`memory/feedback_context.py`、`src/homemaster/adapters/profiles.py`、`src/homemaster/application/composition/profiles.py` 与相关 tool registries
- Delete after replacement: `src/homemaster/memory/automatic_recall.py`、`src/homemaster/application/memory_recall.py` 自动召回职责及 latch/context-prelude 接线
- Test: `tests/homemaster/memory/test_memory_tools.py`、`test_feedback_context.py`、`test_automatic_recall.py`/integration replacements；新建 `tests/homemaster/application/test_tool_executor.py` 跨层合同测试与 tool profile/registry audit

- [ ] Search 工具通过 MindMemOS SDK 显式发送当前 project/user/app/agent scope。成功 response 返回每个准确 raw ID；空命中是 success-empty，transport/API failure 保留 typed error。
- [ ] 用 provider 前、compact 后和连续 turn 三个跨层场景断言没有自动 Search API 调用、SearchRecord 或注入 latch。显式 Search tool 每次恰有一个对应 MindMemOS SearchRecord，输出只属于该次 ToolResult，不写进历史 Session。
- [ ] FeedbackContext 由成功 Provider attempt 的真实可见消息与本次 Search 工具实际可见 raw IDs 构建；逐 ID 重新读取并校验 actor scope、active 和原正文后才调用 SDK。自由文本 ID、前次 run ID、失败 Provider attempt 和隐藏 reasoning 均不能进入候选。
- [ ] `mindmemos_feedback` 使用稳定 `feedback_operation_id`；MindMemOS API/SDK 提交后可用 operation ID 查询总状态与逐 action 计划/checkpoint。工具显式等待终态并返回每条 add/update/delete/noop action；每个变更 action 首次执行前固定 action ID、write target 与不可变 payload，逐个读回 Qdrant/Neo4j version/lineage。HTTP 回执丢失后必须对账；任一 action 未 terminal/readback 时工具返回 typed failure/unknown，不声称成功。
- [ ] 删除普通 `mindmemos_add` 的模型 schema、allowlist、profile help 与 `AddMemoryExecutor`；保留 Search、Feedback 和所需 history/update/delete 工具。执行跨层 Provider schema → Dispatcher → SDK server test；旧工具名在 provider schema 中为零次。
- [ ] 从 HomeMaster 根目录运行 `uv run pytest tests/homemaster/memory/test_memory_tools.py tests/homemaster/memory/test_feedback_context.py tests/homemaster/application/test_tool_executor.py -q` 与 provider/tool registry audit。

### Task 6：每日原生 Implicit Feedback scheduler 与 action watermark

**Files:**
- MindMemOS: `typing/activity.py`、`components/activity/collector.py`、`typing/service.py`、`pipelines/feedback/implicit.py`、`pipelines/feedback/executor.py`、activity ledger repositories
- HomeMaster: 新建 `src/homemaster/memory/feedback_scheduler.py`；修改 `composition/base.py`、`config/config.py`、startup/doctor
- Test: MindMemOS implicit feedback integration tests；HomeMaster 新建 `tests/homemaster/memory/test_feedback_scheduler.py`

- [ ] 用真实 Add/Search activity fixture 先断言原生 collector 输入包含角色对话、Add memory IDs、Search query 与实际 recall IDs；普通 tool failure 不作为用户纠错信号。HomeMaster 只提交窗口、scope 和 durable cursor，不重写 collector/detector/planner。
- [ ] MindMemOS collector 按窗口游标读取/登记 activity snapshot，而非无限重放固定最近 3 天；同一 scope/window 有唯一 job。Service 对每个 planned action 持久 action ID，执行后读回并 checkpoint，所有目标都 terminal 才推进 activity watermark。
- [ ] HomeMaster scheduler 使用配置的 IANA timezone 与本地时间，使用 operation ledger 的唯一 `(scope, window)` job ID；启动时检查 watermark 并补跑到期窗口；多 scheduler 实例通过 lease/fencing 只运行一个 owner。时间未锁定时禁止生产 scheduler start。
- [ ] 在隔离环境跑固定时间窗口、错过窗口后启动、action 之间杀 worker、重复启动两 scheduler、外部写成功但 HTTP 回执丢失、旧 worker 延迟晚到、普通 tool failure negative control。逐 action 核对公开 status/业务码、raw version/relationship 和 watermark；已完成 action 不可重复产生新版本，unknown action 阻止 watermark 与后续 action 推进。
- [ ] 分别从 MindMemOS 根目录运行 `uv run pytest tests/components/activity tests/pipelines/feedback tests/api/test_implicit_feedback_recovery.py -q`、从 HomeMaster 根目录运行 `uv run pytest tests/homemaster/memory/test_feedback_scheduler.py -q`；真实集成命令和隔离服务 endpoint/DB fixture 写入验收报告。

### Task 7：Archive Snapshot 与按 session seed 的 MindMemOS Dreaming

**Files:**
- MindMemOS: `typing/service.py`、API schemas/mappers/service/routes、`pipelines/dreaming/default.py`、`workers/memory_dreaming.py`、ledger repositories
- HomeMaster: 新建 `src/homemaster/memory/archive_service.py`；修改 `session.py`、`session_manager.py`、`composition/base.py`、`cli/interactive_shell.py`、`cli/shell_commands.py`、`cli/run_command.py`、`web/routes/session_ops.py`、`web/app.py`
- Test: 新建 `tests/homemaster/memory/test_archive_service.py`、`tests/homemaster/web/test_session_ops.py`；扩展 MindMemOS dreaming tests、`tests/homemaster/test_cli_session_command.py` 和 `tests/homemaster/application/test_session_manager.py`

- [ ] Archive Store 在同一个 `MemoryWorkStore` transaction 中锁定 `(session_id, max_event_seq, turn range)`、唯一 `archive_id`、区间内 Add operation IDs 和 Dreaming Outbox intent（先持久引用 Add operation IDs，成功 seed 列表由后台等 Add 对账后一次冻结）；任何一项失败全部回滚。重复提交同一边界返回相同 ID。启动审计 snapshot/intent 差集并幂等补建；注入保存 snapshot 后、插入 intent 前进程退出，证明重启后整体未提交或唯一完整提交，绝不漏 Dreaming。Archive enqueue 不等待 Dreaming；单 Turn 完成不归档；crash 不伪造正常 close。
- [ ] MindMemOS Dreaming submit/status API/SDK/worker 必须接收稳定 `archive_id`、payload hash 与精确 `seed_add_record_ids`，并公开每 Entity/action checkpoint。先核对 Add 状态；只把 completed 且有 active raw memories 的 AddRecord 当 seed。Add unknown 先 reconcile；failed/0-memory 不伪装成 seed。
- [ ] Dreaming 仅从给定 AddRecord 解析 memory/entity，按唯一共享 entity 建 scope；可以读同 actor 邻接 Memory，但任何边界外 AddRecord 不能被消费/标记。每个 entity/action 第一次副作用前持久完整不可变 plan、action ID、Memory/Entity 目标 ID 和预期写入值；HTTP 回执未知时 status/reconcile 先读回 Qdrant/Neo4j。后继 action 等前项对账完成；同 actor/entity batch 串行，测试两个 Worker 的 lease 过期与旧 Worker 晚到。
- [ ] `/archive`、Session 正式关闭、`/new` 关闭旧 Session 与正常退出都请求 Snapshot；Turn 完成不触发；archive 后新增 Turn 进入下一 snapshot。`/new` 不等待 Outbox idle；只有 terminal shutdown owner 执行受限 drain。
- [ ] 用 10-turn + 2-turn 真实隔离 session 验证两个 Snapshot 的 seed 边界分别是 10 和 2；重复第一边界同 archive ID；边界外 Session/Turn 数据不变；每个 Entity/action 均核对 submit/status API code、Qdrant raw 与 Neo4j graph。注入 action 已落库但 HTTP 回执丢失、Worker lease 转移和旧 Worker 晚到；恢复后每 action 只生效一次。包含 no seeds/entity/action 的真实 no_action 情形。
- [ ] 从 MindMemOS 根目录运行 `uv run pytest tests/pipelines/dreaming tests/components/dreaming tests/workers/test_memory_dreaming_recovery.py -q`；从 HomeMaster 根目录运行 `uv run pytest tests/homemaster/memory/test_archive_service.py tests/homemaster/web/test_session_ops.py tests/homemaster/application/test_session_manager.py -q`。新增测试文件需按仓库发现规则加入，不允许以不存在的文件路径跳过命令。

### Task 8：Skill Invocation Trace、8 条原子 claim 与 Skill draft evolution

**Files:**
- MindMemOS: Skill schemas/routes/service、`typing/skill.py`、Skill trace ledger/repository、`pipelines/skill/evolution.py`、`pipelines/skill/version_store.py`、`workers/skill_evolve.py`、SDK skill models/clients
- HomeMaster: `src/homemaster/skills/types.py`、`loader.py`、`registry.py`、`application/composition/skills.py`、`application/tool_executor.py`、`tools/memory_tools.py`；新增 `src/homemaster/memory/skill_invocations.py`
- Test: 新建 `tests/homemaster/skills/test_skill_invocations.py`、Skill tool contract tests；扩展 MindMemOS skill evolution tests

- [ ] 当且仅当 HomeMaster 实际加载已登记云端 Skill 时打开 invocation，并绑定 local ID、cloud ID、loaded version、精确 SKILL.md SHA-256 和 start Turn。未登记或 hash 不匹配时不生成 candidate；将 invocation ID 作为该 Skill 执行上下文公开给模型。
- [ ] Runtime 依执行上下文把真实 tool_call/result 关联至唯一 invocation；同一调用最多归属一个 invocation。Skill 跨 Turn 分开记录。封存调用不属于被测 steps；中断且未封存的 invocation 不进入候选。
- [ ] `mindmemos_skill_trace` 只接 invocation ID、outcome、score 与用户可见 completion；tool steps/task input 从 Runtime trace 导出，不接受 Agent 伪造。结构化 result 先用 canonical compact JSON，再按 Unicode code point 留最后 500 字符；499/500/501 和多字节边界测试在写库前检查持久 payload 不含被截前缀。
- [ ] 校验 score `0..1`、outcome 独立保存、success 只允许 `[0.5, 1]`、failure 只允许 `[0, 0.5]`，0.5 两种均可。trace 按稳定 invocation ID 幂等写入专用 Store，绝不写 AddRecord。
- [ ] MindMemOS Evolution 每个 skill 只原子 claim 恰好 8 条有效未消费 trace，固定 min/max aggregate 8；并发 worker、重启和重复 admission 下每个 trace 只有一个 batch。7/8/9/16 条分别得到 0/1/1/2 batch，余数保留。
- [ ] 第 8 条 trace 已落库但 Evolution job 尚未创建时可能崩溃。MindMemOS 启动和每次 trace admission 后都扫描有效未消费候选；以确定顺序（sealed time + invocation ID）原子 claim 8 条，并在同一 ledger transaction 创建唯一 batch/可恢复 job intent。跨 HomeMaster/MindMemOS 的投递用该稳定 batch ID reconcile；不依赖一次性阈值 callback。精确注入 trace 落库后、job 创建前崩溃，重启后仍恰好触发一次，余数保留且 trace 不被重复消费。
- [ ] Skill trace submit/status 与 Evolution submit/status 对外公开稳定 operation IDs、action checkpoints、业务码和 reconciliation。SkillEvolver 仅使用专用 trace source，先固定 8 个 trace IDs、parent version 与 draft target，再生成独立 `SKILL.md` draft；版本 store raw readback 确认 draft、parent 和 consumed trace IDs。HTTP/LLM 回执丢失后从 operation 状态恢复，不重复生成 draft；不得改 HomeMaster active Skill 或包内 tools/scripts/resources。
- [ ] 从 MindMemOS 根目录运行 `uv run pytest tests/pipelines/skill tests/components/skill tests/api/test_skill_routes.py tests/workers/test_skill_evolve_recovery.py -q`；从 HomeMaster 根目录运行 `uv run pytest tests/homemaster/skills/test_skill_invocations.py tests/homemaster/memory/test_memory_tools.py -q`。

### Task 9：MindMemOS 原生 CRUD 与 Web plain/structured 人工更正

**Files:**
- MindMemOS: API schemas/routes/service、native get/list/search/create/update/delete/history pipelines、SDK memory models/clients
- HomeMaster: `src/homemaster/web/memory_views.py`、`web/routes/memory.py`、`web/schemas.py`、`web/serve.py`、`web/app.py`
- Frontend: `web/src/api/http.ts`、`components/MemoryPage.tsx`、`MemoryDetailDialog.tsx`、`memorySummary.ts`、`App.tsx` 和相应 tests
- Test: 新建 `tests/homemaster/web/test_memory_management.py` 与 MindMemOS CRUD real-store tests；扩展 `tests/homemaster/web/test_memory_readonly.py`、`web/src/components/MemoryPage.test.tsx`

- [ ] 先通过 API contract test 锁定 plain 与 structured record 的 create/update payload、history/version、delete archive 语义；schema 有缺口时先补 MindMemOS service/SDK/持久 writer，不让 HomeMaster 直连 Qdrant/Neo4j。所有写入在副作用前固定 operation/action ID 与不可变 write plan，支持公开 status/readback 解决写成功但 HTTP 回执丢失。
- [ ] API service 使用 authenticated project/actor context 做 list/search/get/history/create/update/delete；每次变更返回 operation ID、业务码与资源 ID。Update 保留原生 version/graph 语义，delete 按原生 archive 语义。
- [ ] HomeMaster `MemoryManagementService` 只映射 SDK 类型和授权。删除 plain-only 限制与 `add_flat + delete old` fallback；写入异常返回 typed 失败且旧记忆保持完整。
- [ ] UI 支持 plain 与结构化正文/字段编辑、历史 lineage、筛选、详情、创建、软删除；变更后重新请求服务端详情，不仅更新本地乐观状态。所有错误显示 server typed response。
- [ ] 对新建 plain、结构化更新与归档分别从 Web 请求一路通过 Server 到 raw Qdrant/Neo4j readback；检查 code、project/actor、active/archived、正文/结构/版本/关系逐项一致。
- [ ] 从 MindMemOS 根目录运行 `uv run pytest tests/api/test_memory_mutation_contracts.py tests/pipelines/get tests/pipelines/update tests/pipelines/delete tests/mindmemos_sdk/test_memory_client.py -q`；从 HomeMaster 根目录运行 `uv run pytest tests/homemaster/web/test_memory_readonly.py tests/homemaster/web/test_memory_management.py -q`，并在 `web/` 目录运行 `npm test -- --run` 与 `npm run typecheck`。

### Task 10：迁移 benchmark trajectory 入口并删除冲突的旧生命周期

**Files:**
- Modify: `src/homemaster/alfworld/trajectory_memory.py`、`alfworld/benchmark/runner.py`、`robocasa/trajectory_memory.py`、`robocasa/benchmark/runner.py`、`experience/finalizer.py`、`experience/session_finalization.py`、`experience/dreaming_state.py`、`experience/schema_episode.py`、`memory/mindmemos_lifecycle.py`、`memory/mindmemos_writes.py`、`memory/mindmemos_queries.py`、`memory/mindmemos_feedback.py`、`memory/add_queue.py`、`memory/automatic_recall.py`、`application/memory_recall.py`、`memory/schema_episode_validation.py`、`memory/schema_episode_prompts.json`、`application/composition/base.py`
- Compile branch wiring (conditional on ADR): `application/composition/base.py`、`alfworld/trajectory_memory.py`、`web/routes/memory.py`、`web/src/components/MemoryDetailDialog.tsx` 及对应 tests。Task 1 必须先选定维持现状、预览、人工写入或退役；本计划不预选冻结或删除。所选行为若进入 V3.8，必须使用新版 MindMemOS 合同并定义独立 ALFWorld 外部验收；若退役，保留原始 evidence 和既有 MindMemOS Memory。不得用 RoboCasa 替代 ALFWorld 验收。
- Modify/Delete: old lifecycle tests, prompts, profiles and config; replace tests with the selected new contracts
- Test: 所有替代合同测试；旧路径源码/import/tool registry audit

- [ ] 对 ALFWorld/RoboCasa benchmark runner 做 event provenance test：真实工具调用/结果和 terminal outcome 均进入 canonical Turn Trace；新普通对话记忆经 Turn Add 生成。ALFWorld compile source DTO、轨迹与派生 experience 是独立数据流，按 ADR 分支处理，不得将其伪装或重复转换为普通 Turn Add。
- [ ] 保留 immutable benchmark trajectory JSON、run evidence、task outcome、raw trace 与已有 Memory。删除重复普通 trajectory flat sink 的活动接线；ALFWorld compiler/source writer/job/API/UI 仅按已选 ADR 修改。选择预览/写入时用真实 ALFWorld 输入完成定向黑盒和独立连续五例；选择退役时核验活动路径不可达且历史结果仍可读。任何分支均不自动删除原始 evidence 或已存 Memory。
- [ ] 在旧 job root 只读生成 operation/job 清单，按 scope 标注 stale 并停止自动恢复。绝不转换、删除或覆盖现有 Qdrant/Neo4j memory 数据。
- [ ] 删除 SessionFinalizer 的三个固定 extractor/schema episode、Finalizer Add→Feedback→threshold Dreaming waterfall、旧 SessionFinalizationController、旧 run-directory-only finalizer identity、自动 Pre-turn/compact Search、context latch、普通 Agent Add、旧 `record_json`/SDK fallback 与双写。
- [ ] 从 `application/memory_recall.py` 抽离 user-evidence registration 与 recall 后再删除 recall module；保留 permission/actor、canonical Runtime evidence、file `context_memory` 和按需 memory maintenance。
- [ ] 全仓 `rg`/import graph/Provider schema audit 逐条断言旧符号、prompt、工具名、feature flag、job type 与 `add_flat` lifecycle caller 不再可达；替代路径测试过后再删旧测试。运行 `uv run pytest tests/homemaster -q` 和 `uv run ruff check src tests scripts` 中被 touched 的完整适用集合。

### Task 11：RoboCasa 固定 manifest、跨 cell Worker ownership 与独立 verifier

**Files:**
- Modify: `src/homemaster/robocasa/benchmark/cells.py`、`benchmark/runner.py`、`robocasa/types.py`、`cli/benchmark_robocasa.py`、`cli/app.py`
- Create: `scripts/verify_v38_manifest.py`、`scripts/verify_v38_mindmemos_acceptance.py`、`tests/homemaster/robocasa/test_v38_acceptance_manifest.py`、`test_v38_independent_verifier.py`

- [ ] 增加冻结的 `--cell-manifest` 输入。解析前核对文件 SHA、RPent commit、atomic/target/seed 属性；以清单顺序执行并禁止 `tasks/seeds/limit` 重新排序或截断。写出 manifest digest、ordered cell IDs、完整运行参数 hash。
- [ ] 修改 runner ownership，使每个 cell 的 lifecycle close 不丢失持久 Outbox/Archive/Feedback/Skill jobs：跨 cell 可由 runner/application owner 复用 Worker，或者由下一 cell 的 worker 从同一 durable Store 恢复；run 结束 drain 仅限 terminal owner。每种状态由 process-level 测试独立证明。
- [ ] Independent verifier 在 HomeMaster/MindMemOS 业务 Worker 已停止后，使用独立只读凭据和 Qdrant、Neo4j、Skill Version Store 原生客户端直接查询原始存储；不得读取被测 Worker/API 生成的“readback 导出”作为终态证据。另读取 frozen run manifest、原始 RoboCasa event bundle、provider attempts 与 HTTP/business receipts，从 source event 重新构造 terminal Turn/Add/Archive/Skill/Feedback 期望集合并逐 instance 比较，不依赖内部 summary 的 aggregate count。
- [ ] 最终 verifier 按每个 operation 的不可变 version/action IDs、parent lineage、archive visibility 和最终 current pointer 验证；后续合法 Feedback/Dreaming 导致旧版本不再 active 不算 Add 丢失。额外检测无已授权 action 的正文变更、错误 lineage、旧版本复活及 operation 版本缺失。
- [ ] verifier 对每个 cell 分开判定 `valid`、环境 `success`、worker/API exit、stderr tracebacks、所有目标操作、raw storage readback、scope 与 PID cleanup。`success=false` 但 `valid=true` 的真实失败轨迹仍可通过记忆功能门。
- [ ] 单测覆盖 R20 的准确 20 cell 次序、seed 1 task 顺序后 seed 2 两项、manifest hash 变化 fail closed、duplicate/missing cell 拒绝、source event 少一条 Add 时该 cell 单独 fail、summary aggregate 不能掩盖一个失败。

### Task 12：按功能跑定向外部门、R5 和最终 R20

所有外部测试使用真实 MindMemOS Server/SDK、临时 Qdrant/Neo4j/Skill Store；mock 仅限 unit tests。每个功能的顺序都是「定向黑盒 → 专属空白 scope 的完整 R5」，任一 feature R5 失败则修复后从 R5 第一个 cell 重跑。R20 只能在所有 feature gate 通过之后运行。

新增 HomeMaster `tests/homemaster/memory/test_v38_live_acceptance.py`，全部真实用例标记 `live_memory`（需要真实 LLM 同时标记 `live_api`）；从 HomeMaster 根目录先执行 `mkdir -p plan/V3.8/evidence`，再运行 `uv run pytest -m 'live_memory or live_api' tests/homemaster/memory/test_v38_live_acceptance.py -vv --strict-markers --junitxml=plan/V3.8/evidence/homemaster-live.xml` 显式覆盖默认排除。MindMemOS 新增 `tests/integration/test_v38_live_acceptance.py`，从其根目录运行 `uv run pytest -o addopts='' tests/integration/test_v38_live_acceptance.py -vv --junitxml=/Users/wylam/Documents/workspace/HomeMaster/plan/V3.8/evidence/mindmemos-live.xml`。两套都须 collected/selected > 0 且必要用例零 skip/xfail；配置缺失应使门失败，不能 skip 后宣称完成。R5/R20 另由真实 benchmark CLI 执行，独立 verifier 与上述 JUnit 共同组成证据。

| 功能门及顺序 | 定向真实黑盒 | R5 每个 cell 必须逐项证明的终态 |
|---|---|---|
| Turn Add/Outbox | 10 terminal turns（成功、工具失败、partial）、Agent admission latency、四处 worker/server crash | 5 cell 中每 terminal Turn 唯一 operation/AddRecord；逐个 memory raw readback，或成功空 Memory AddRecord；至少一个 response unknown reconcile 无重复 |
| MindMemOS Add Entity 图关系 | 至少一个可抽实体的 Vanilla Add 输入，另有 tool call/result 配对证据 | 每 cell Add 原始 AddRecord 保留调用与结果配对；raw Memory、Entity、Neo4j `MENTIONS` 关系逐条存在 |
| 按需 Search | new session/compact/连续 Turn 无请求；Agent Search 正向请求 | 每 cell 通过真实 Search tool 命中预期 raw IDs/SearchRecord/scope；没有 tool 调用的 run 无 SearchRecord |
| Web CRUD | plain create、structured update、history、archive deletion | 每 cell 通过 Web/API create/list/detail/history/update/delete；R5 总集合至少一次 plain 与一次 structured；每一操作真实 readback |
| Explicit Feedback | 已见旧 Memory 的成功 provider attempt 提交用户纠错，另有普通 tool failure negative control | 每 cell 通过 Agent Feedback 工具；每 action 业务码/version/lineage/raw state 核验；failure 不能改记忆 |
| Implicit Feedback | 到期 window、missed startup、action crash/replay、tool-failure negative control | 每 cell 进入独立固定测试 window；activity cursor、job/action、水位外部读回；重启不重复 mutation |
| Archive Dreaming | 10-turn archive 重复、归档后 2-turn 再归档、no-seed/no-action | 每 cell 是独立 session；snapshot seed IDs 和每 Entity/action 读回准确，其他 session/Add 未消费 |
| Skill Trace/Evolution | score/outcome edges、499/500/501 Unicode tail、7/8/9/16 并发 claim | 每 cell 真实 skill trace 精确归属；R5 总计至少 8 条同 Skill invocation；第 8 条唯一 job/draft readback，active skill 不变 |
| 删除旧路径 | import/registry/config/static prompt/source audit 和生产 Runner/provider schema | 每 cell Finalizer waterfall、普通 Add 工具、自动 Search、重复 flat trajectory sink、AddRecord Skill candidate 均不可达；ALFWorld compile 按 ADR 分支单独审计，不能把冻结或删除当作默认 |
| ALFWorld compile（未来 ADR 批准后另立验收） | 当前不纳入 RoboCasa 九门/R20；若纳入预览或写入，先更新 spec 和验收集 | 使用真实 ALFWorld 成功及失败/unknown 输入，定向黑盒后连续五例；预览逐例证明 Memory/AddRecord 无变化，写入逐例核对内容、来源 hash、API 返回码与原始存储；不得声称 RoboCasa 已验证此编译器 |

- [ ] **Gate 0: SDK/upstream package 前置门。** 所有 MindMemOS API/SDK 源码改动完成后固定最终不可变提交 SHA，更新 `third_party/MindMemOS` pin、PEP 508 SDK source dependency 和 `uv.lock`。构建 HomeMaster wheel，从源码目录之外用空 venv 安装；断言 SDK import 来自固定提交且 wheel 不含 MindMemOS Server/Pipeline。运行 `uv lock --check`、`uv run pytest tests/homemaster/test_memory_runtime_setup.py -q`、MindMemOS `uv run pytest tests/mindmemos_sdk/test_package_boundary.py tests/mindmemos_sdk/test_memory_client_parity.py -q` 和 package audit。全部通过后才允许启动任何 RoboCasa R5；该前置门不占 9 个 R5 功能门之一。
- [ ] 每个功能门建全新空白 scope，运行定向真实黑盒并归档公开 response code/status 与由独立客户端直接取得的原始存储 readback 证据；未通过时不开始该功能 R5。
- [ ] 按上述表逐个运行 9 个基础功能门的 RoboCasa R5，配置固定为 `profile=robocasa`、`memory_mode=full`。每个专属 scope 顺序执行五个 cell，不清库、不换样本、不跳过失败；每个 `valid` cell 的该功能所有目标 operation 分别 PASS。ALFWorld compile 当前不计入九门；若未来纳入，先更新 spec 再另跑其 ALFWorld 五例验收。
- [ ] R5 与 R20 均采用同一污染规则：若无效 attempt 在任何被测外部副作用前终止，保留证据并允许原 scope 对同一 cell/task/split/seed 新建 attempt；若已产生 Add/Feedback/Dreaming/CRUD/Skill 等任何被测副作用，该整个功能/集成 scope 判废，保留全量证据并创建新的空 scope，从该 R5 或 R20 的第一个 cell 重跑。无法证明未发生副作用时也判废；不得清空后伪装未发生。
- [ ] R5/R20 中 provider/worker 环境 `valid=false` 的 attempt 保留原证据；环境 `success=false` 不自动等于无效，也不得用容易样本替换失败 cell。
- [ ] 所有独立门和 R5 完成后新建独立 integration scope，按冻结 manifest 跑 R20。20 个 cell 均有效，所有实际目标 operation 各自通过；任一 unknown/missing/readback mismatch/late traceback/exit code failure 使 R20 不通过。
- [ ] 验收报告列每个 R5/R20 cell 的 attempt、result/valid/success/exit code、MindMemOS HTTP code、AddRecord/Memory/action/archive/skill version IDs、raw Qdrant/Neo4j/Skill Store readback、stderr、process cleanup 与 independent verifier 结果。

### Task 13：文档、发布审计与最终交接

**Files:**
- Modify: `docs/architecture/memory-system.md`、`docs/memory-user-guide.md`、`README.md`、`CHANGELOG.md`、`docs/session-handoff.md`、Web user guide、`third_party/MindMemOS/VENDORED_SOURCE.md`
- Create: `plan/V3.8/mindmemos-native-lifecycle-acceptance-report.md`

- [ ] 将所有现行文档切换到 canonical Turn Trace → async Vanilla Add/Outbox、按需 Search、显式 tool Feedback、每日隐式 Feedback、Archive Snapshot → seed-specific Dreaming、专用 Skill Trace → 8 条 Evolution draft、Web CRUD。
- [ ] README、用户指南、架构数据流/不变量和 CHANGELOG 同步；按 compile ADR 分支记录该能力的真实用户行为和限制。每次实现提交前在 CHANGELOG 先写变更、原因与影响，commit message 保持同一条完整记录。
- [ ] 完成 source/import/Provider schema/package audit：wheel 只有一套 MindMemOS SDK；无内嵌 Pipeline、旧 vendor 运行分支、新旧 fallback、Finalizer、automatic recall、普通 Agent Add、flat update fallback 或普通 AddRecord Skill candidate。ALFWorld compile 按已选 ADR 分支验收；若保留或预览，核对新版接口和外部行为；若退役，核对活动路径不可达与历史可读性。不把 compiler 删除或冻结作为默认门。
- [ ] 跑完所有指定 Python、MindMemOS、Web unit/integration tests；`git diff --check`、Ruff、Web typecheck/Vitest、wheel metadata/install gate 和独立 verifier 均通过。
- [ ] 将真实 R5/R20 验收报告、固定 commit/hash、每项黑盒回执和逐项 storage readback 放入 `plan/V3.8/`。更新 `docs/session-handoff.md` 当前状态与后续操作，保留文件中已有其他项目记录。

## 最终完成判据

- 每项 MindMemOS feature 都有定向外部门和独立空 scope R5；R20 在新的独立 scope 按锁定顺序 20/20 逐项核对通过。
- 每个外部调用同时核对 API/HTTP 业务返回码和独立外部终态；每个 operation/target 分别判定，不能用 aggregate、best 或 any 遮蔽失败。
- Agent Add admission 不等待远端；所有后端操作在 worker 退出、重启和未知结果后可恢复且不重复写入。
- MindMemOS 同/异步 SDK 接口 parity 通过，HomeMaster wheel 无 MindMemOS Server/Pipeline 复制包，部署无关端点和 actor scope 经本机/远端两拓扑验证。
- 旧冲突 lifecycle/tool/prompt/config/test/fallback 均已移除；旧 job stale 清单留存；任何已有真实 Memory 未被计划外删除或迁移。
- 代码、测试、架构文档、真实使用指南、README、CHANGELOG、handoff 和逐项外部验收证据同源。

## 自审清单

- [x] Turn Add 包含失败、取消、guard、budget、interrupted、幂等、tool 配对、terminal Trace/Outbox 原子 admission 和服务端写计划恢复、原始 Memory 读回。
- [x] Search 仅按需；Add 内部 recall 与 Agent Search 分开；自动 Pre-turn/compact 注入删除。
- [x] Explicit Feedback 和每日 Implicit Feedback 输入/触发/负向控制/action checkpoint 分开覆盖。
- [x] Archive Snapshot 边界、重复归档、后续 Turn、Add 状态等待、精确 AddRecord seeds、Entity 邻接、no_action、冲突控制覆盖。
- [x] Skill 注册版本身份、invocation tool 归属、截尾持久化、评分边界、8/9/16 原子 claim、draft-only 和不自动激活覆盖。
- [x] SDK/API/Server operation submit/status/action checkpoint、HomeMaster wheel、sync/async parity、用户端 CRUD 与旧逻辑删除有明确任务映射。
- [x] Qdrant late writer 由不可变版本与权威可见性 pointer 处理；Archive Snapshot/Dreaming intent 同事务；第 8 条 Skill trace 的 job 有启动扫描补调；各自的精确崩溃/迟到测试已列出。
- [x] RoboCasa 九个基础 R5 功能门与独立 SDK/package Gate 0 数量一致；ALFWorld compile 暂不计入，后续纳入须先更新 spec 和 ALFWorld 专属连续五例验收；R5/R20 的顺序、隔离、逐 operation readback、返回码及进程清理均列入执行门。
- [x] HomeMaster live-marked tests 明确覆盖默认 pytest 排除规则；真实门要求至少一个被选用例且不得 skip/xfail；R5/R20 的有副作用无效尝试均废弃整个 scope。
- [x] 尚未锁定的 schedule、Server owner/topology 与 trajectory compile 明确作为实施前决策门；Session DELETE 保持现状，不作为新的产品决策门。
- [x] 路径经 HomeMaster 与 MindMemOS 实际仓库目录核对；计划中的测试命令须在对应仓库根目录执行。
