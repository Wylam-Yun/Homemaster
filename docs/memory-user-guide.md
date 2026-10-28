# HomeMaster V3.8 记忆用户指南

V3.8 用独立部署的 MindMemOS HTTP 服务替换了内嵌式记忆运行时。长期记忆的写入、检索、反馈、合并与
Dreaming 全部由服务端原生管线负责；HomeMaster 进程只负责把每个终态 Turn 规范落盘、排队、投递并做
原始存储回读验收。

## 记忆分层

- `SOUL.md`：稳定人格，由部署者维护，Agent 没有写工具。
- `USER.md`：稳定身份、偏好、沟通和使用习惯。
- `MEMORY.md`：近期事件、决定、结果和跨会话未完成事项。
- MindMemOS 服务端：外部世界 fact 与可复用 experience，由远端 Qdrant + Neo4j 持久化。

SOUL、USER、MEMORY 在 session 第一次组装上下文时冻结。`context_memory` 写入会立即持久化，但当前 session 的
system prompt 不变；新 session 才看到新快照。

## Turn 级自动沉淀（替代旧 Session Finalizer）

`ApplicationRuntime.run()` 结束后，每个终态 Turn（completed / failed / cancelled 都算终态）在同一事务里
写入 canonical trace 事件和恰好一条 `turn_add` 投递意图——终态确认之前意图已持久化，崩溃不会丢。
后台 `MemoryWorkerPool` 逐条取出，经 `MindMemOSClient` 调用服务端 Vanilla Add；写入以服务端
operation receipt + HomeMaster 的 Qdrant/Neo4j 原始回读为准，SDK 回执本身不算完成证据。

因此不再有"Session 结束才统一沉淀"的 FIFO Finalizer：`/new`、EOF、Ctrl+C、`/exit` 只结束 Session，
不阻塞等待记忆任务；turn 级 Add 早已在各个终态入队。进程强杀后，未完成的任务在下次启动由 store
审计回收重投；已发出但结果未知的调用标记 `outcome_unknown`，先经 operation ledger 对账再决定，
绝不盲目重试。

## 召回方式

自动召回已移除。新 Session 第一条消息和 Compact 之后不再自动注入 `<memory-context>`；长期记忆只能由
Agent 主动调用 `mindmemos_search` 召回（见工具节），结果作为真实工具结果进入上下文。

## Session Archive 与 Dreaming

`ArchiveService` 按 Session 生成不可变 Archive Snapshot：锁定 turn 范围、最大事件序号和当时已入队的
Add operation 种子；同一不可变边界重复归档返回同一个快照，不会重复触发 Dreaming。
归档本身入队一条 `archive_dreaming` 任务：worker 先把每个种子 operation id 经服务端 operation ledger
解析为 AddRecord id（仍在途的种子重试而不是丢弃），再以 `seed_add_record_ids` 调用服务端 Dreaming，
最后对每个产出 memory 做原始回读。仅 turn 自然终态不会产生快照；崩溃恢复也只补终态、不造快照。

## 反馈

- 显式：`mindmemos_feedback` 工具。调用时把当前可见的消息窗口和该次 `mindmemos_search` 实际可见的
  结果深拷贝绑定，服务端 feedback 管线只能作用于这些可见记忆。
- 隐式：`ImplicitFeedbackScheduler` 每天按配置时区（默认 Asia/Shanghai 03:00）为每个 scope  enqueue 一条
  `implicit_feedback` 任务，携带 `{window_start, window_end}` 活动窗口；每条窗口有 durable watermark，
  启动时会追赶漏跑的窗口。反馈动作若改动了 memory，worker 对改动后的 ID 做原始回读才置完成。

## Skill 轨迹与进化

Skill 命令被解析进入 turn 时，`SkillInvocationTracker` 打开一条调用记录；turn 终态时把该 turn 内真实的
工具调用步骤（tool_call_id、参数、结果尾部、截断标记）固化并封印（seal），成败分数由终态独立推导。
每累计恰好 8 条已封印轨迹，原子认领一批并入队 `skill_evolution` 任务：worker 先把每条轨迹经专用
Skill Trace 接口提交到服务端（`invocation_id` 幂等），再提交 evolution batch，服务端产出 draft-only
新版本。本地 seal 与服务端批次是同一个持久任务，重试收敛而不会重复建批。

## Web 记忆管理

Web 记忆管理接口改为直连 MindMemOS 原生管理 API：create / list / search / detail / history / update /
delete，plain 与 structured memory 都支持，仍走原有 typed `tenant_id` ACL。图数据读取经原始 Neo4j
校验器，不可用时 fail closed（503）而不是降级成旧本地实现。Archive 状态路由返回
`{archive_id, job_id, status}`，由 `ArchiveService` 提供。

## 配置

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/homemaster.yaml
uv sync --extra memory   # 或 --all-extras；base 安装可用 files 模式
```

```yaml
memory:
  mode: full                     # files=仅文件记忆；full=接入 MindMemOS 服务
  data_root: ../.runtime/memory
  mindmemos:
    base_url: http://127.0.0.1:8765   # full 模式必填，绝对 http(s)
    api_key: "..."                    # 也可用环境变量
  qdrant:                          # 仅供原始回读校验器
    mode: server
    url: http://127.0.0.1:6333
  neo4j:                           # 仅供原始回读校验器
    mode: server
    uri: bolt://127.0.0.1:7687
    username: neo4j
    password: "..."
  workers:
    count: 2
    poll_interval_seconds: 1.0
  implicit_feedback:
    enabled: true
    timezone_name: Asia/Shanghai
    hour: 3
```

`qdrant`/`neo4j` 段只喂给 `QdrantNeo4jVerifier` 做写后终态验收，HomeMaster 不再启动或管理这两个存储；
真实连接由 MindMemOS 服务按其自身配置持有。`memory.mode` 为 `files` 时完全不开 SDK、work store
和 worker。`mindmemos_sdk` 是 `memory` extra 的可选依赖：未安装时 base 包仍可 import，构造 full 模式
客户端会抛出指名 `homemaster[memory]` 的 typed 错误。

## 六个工具

### `context_memory`

原子修改 USER/MEMORY，行为与此前一致。

### `mindmemos_search`

按语义搜索长期记忆，返回按相关性排序的原生记录及 `search_record_id`。自动召回已移除，这是唯一召回
入口；query 会发往服务端 embedding，不要搜索凭证、token、cookie 或内部地址。

### `mindmemos_history` / `mindmemos_update` / `mindmemos_delete`

原生客户端管理接口：history 沿服务端 lineage 返回版本链；update 走服务端原生版本化写入（旧的
`add_flat` 降级路径已删除）；delete 仅用于明确要求或确认错误的记录。

### `mindmemos_feedback`

显式反馈，见上文"反馈"节。

`mindmemos_add` 已移除：写入只由每个终态 Turn 的 Vanilla Add 完成，Agent 不能主动 Add。

## 文件与隐私

data root 和文件记忆目录为 0700；USER/MEMORY 写入继续拒绝 prompt injection、credential 模式、控制
字符和内部分隔符，人工改坏文件时生成 mode-0600 drift backup。发往 MindMemOS 服务的文本包括 turn
dialogue、search query、feedback 正文和 skill trace——按部署边界评估出站内容。

## 常见错误

- `memory_trace_unavailable`：canonical trace admission 失败，run 在模型调用前终止。
- `memory_outcome_unknown`：外部调用可能已发出但终态无法确认；worker 会先经 operation ledger 对账，
  禁止自动重试。同名 operation id 的不同 payload 会返回 409 并使任务显式失败。
- `archive_unavailable`：非 full 模式访问 archive 路由。
- `memory_backend_unavailable`：MindMemOS 服务不可达或 SDK 未安装（提示 `homemaster[memory]`）。

诊断：

```bash
uv run homemaster doctor --json | jq '.checks[] | select(.name == "memory_backend")'
```
