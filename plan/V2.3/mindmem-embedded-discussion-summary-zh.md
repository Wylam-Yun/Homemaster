# V2.3 MindMemOS 嵌入 Homemaster 讨论总结

> 状态：讨论中，非最终设计，非实施计划
> 日期：2026-08-04
> Homemaster 分支：`mindmem`
> Homemaster 起点：`7d35d716c8f0e53e6c39c726c36b7549f3227935`
> MindMemOS 基线：`main@1364c0866203`

## 1. 目标与已澄清前提

目标是评估并逐步用 MindMemOS 替换 Homemaster 当前由 mem0 承担的长期记忆能力。

讨论已经澄清以下前提：

1. MindMemOS 只为 Homemaster 工作，不作为公共记忆服务。
2. 目标接入方式是把 `mindmemos` Python 包直接安装/嵌入 Homemaster，由 Homemaster 进程直接调用 pipeline。
3. 不使用 MindMemOS FastAPI、Uvicorn、HTTP POST 或 `mindmemos-sdk` 作为主要运行边界。
4. 每台设备拥有自己的本地记忆；不做跨设备同步。用户换设备时如需保留记忆，使用显式 export/import。
5. 产品未来需要面向 Linux、macOS 和 Windows 用户，不能把正式架构绑定到 HPC、Docker 或 Apptainer。
6. 普通用户应获得一键安装体验，不要求手工安装 Java、Neo4j、Qdrant 或配置数据库。
7. 可以在平台安装包中内置经过锁定和验证的第三方 runtime。

## 2. 已在服务器核实的现状

### 2.1 Homemaster 不是只有一套记忆

当前 Homemaster 至少有三类相互独立的记忆/状态：

1. `FileMemoryStore` + `FrozenMemoryContextService`
   - 管理 `SOUL.md`、`USER.md`、`MEMORY.md`。
   - 每个 session 冻结后注入 agent context。
2. `Mem0MemoryStore`
   - 管理结构化 `FactRecord` / `ProcedureRecord`。
   - 提供 add/search/get/update/delete、精确过滤、去重和写后验证。
3. `RuntimeMemoryStore`
   - 管理每个 run 的 object-memory overlay。

此外，`MemoryEvidenceLedger` 独立保存记忆写入证据与 provenance。

因此，本次“替换 mem0”当前只指替换 `Mem0MemoryStore` 这一层，不自动包含 file memory、runtime object memory 或 evidence ledger。

### 2.2 当前 mem0 数据规模

服务器只读盘点结果：

- Qdrant collection：`homemaster_memory_qwen3_4096_v1`
- embedding 维度：4096
- Qdrant points：2
- mem0 history rows：10
- evidence ledger entries：220

当前数据规模很小，但迁移仍应基于逻辑记录和一致性验证，不能直接把旧 Qdrant 目录交给 MindMemOS 使用。

### 2.3 Homemaster 当前 model-facing memory tools

当前工具层包含：

- `file_memory`
- `add_memory`
- `search_memories`
- `get_memory`
- `update_memory`
- `delete_memory`

`add_memory` 当前要求模型提交完整的 `FactRecord` 或 `ProcedureRecord`，并提供 `evidence_refs`。`Mem0MemoryStore` 使用 `infer=False` 保存 canonical record，并负责 dedupe、provenance sequence、冲突和终态验证。

### 2.4 MindMemOS 的公开输入与内部 memory type

MindMemOS add 的主要输入是消息：

- `DialogueMessage`
- `TextMessage`
- `UrlMessage`
- `FileMessage`

调用者通常不直接指定 memory type；add pipeline/extractor 根据内容和 schema 生成记忆。

MindMemOS 声明的 `MemoryType` 包含：

- `profile`
- `fact`
- `experience`
- `episodic`
- `tool_trace`
- `skill_candidate`
- `file_knowledge`

当前代码中明确由 schema extractor 分类的主要是：

- `user/person` -> `profile`
- `task_experience` -> `experience`
- `episode/episodes/episodic` -> `episodic`
- 其他 schema entity/property -> `fact`

`tool_trace`、`skill_candidate`、`file_knowledge` 在当前基线中更多是已声明的类型空间，尚未观察到与前四类同等完整的生产写入路径。

### 2.5 Procedure 的最新理解

`procedure` 不一定需要成为 MindMemOS 新的底层 `MemoryType`。

MindMemOS 已有 schema add 模式，并通过 `algo_config.add.schema.entity_modeling_path` 加载 entity schema。Homemaster 的 procedure 可以被重新表达为一种自定义 schema，例如包含：

- procedure name
- entry URL
- ordered steps
- preconditions
- expected states
- success condition

如果只增加自定义 schema、但不扩展 `schema_memory_type()`，相关 property memory 默认可能显示为 `fact`。是否需要公开显示为 `procedure` 仍是待决策问题，与能否保存结构化 procedure schema 是两个不同问题。

### 2.6 MindMemOS 当前是可 import 的 pipeline 架构，但不是现成 embedded facade

已核实：

- pipeline 可通过 `create_pipeline(type=..., name=...)` 直接构造。
- 支持用 `@register(type="add", name=...)` 注册自定义 pipeline。
- `MemoryService`、add/search/get/update/delete pipeline 都可从 Python 直接调用。
- HTTP API 和 SDK 只是已有的一种外层调用方式，不是强制要求。

但当前没有面向宿主应用的正式 `EmbeddedMindMemOS` 生命周期 facade；配置、LLM client 和 DB client 仍由 MindMemOS 自己的全局/事件循环级组件管理，需要在 Homemaster 中建立清晰的 application-owned boundary。

## 3. 当前 MindMemOS 数据库约束

### 3.1 Qdrant

当前 MindMemOS 代码使用：

```text
AsyncQdrantClient(url="http://localhost:6333")
```

即当前实现默认连接 Qdrant server。

讨论方向是新增 Qdrant Local Mode：

```text
QdrantClient(path=<device-local-memory-path>)
```

目标是让 Qdrant 在 Homemaster 进程内运行，不占用网络端口，也不要求 Docker。

需要用 MindMemOS 的真实 collection schema 和操作黑盒验证 Local Mode 是否支持所需能力，至少包括：

- named dense vector
- sparse/BM25 vector
- payload indexes
- hybrid search
- batch write
- update/delete
- persistence/reopen
- cancellation/concurrency boundary

在这些测试通过前，不能直接声称 Local Mode 与 Qdrant server 完全等价。

### 3.2 Neo4j

当前 MindMemOS 使用：

```text
AsyncGraphDatabase.driver(uri="bolt://localhost:7687")
```

`ensure_database_schema()` 当前会同时初始化 Qdrant 和 Neo4j，因此原版 MindMemOS 仍要求独立 Neo4j DBMS 进程。

Neo4j 是当前桌面安装中最大的额外 runtime，但不是架构阻断：Homemaster 安装包可以内置 Neo4j Community Edition 和兼容 JRE，并由 Homemaster 以普通用户权限管理本机子进程。

## 4. 当前达成的候选架构方向

```text
Homemaster ApplicationRuntime
├── FileMemoryStore                    保留，是否长期合并待讨论
├── MemoryEvidenceLedger               保留
├── EmbeddedMindMemOS                  新增 application-owned facade
│   ├── MindMemOS add pipeline
│   ├── MindMemOS search pipeline
│   ├── get/update/delete pipeline
│   ├── chat/embed clients
│   ├── Qdrant Local Mode
│   └── Neo4j process/client owner
├── RuntimeMemoryStore                 保留
└── Agent memory tools
```

该方向明确不包含：

- MindMemOS FastAPI server
- Uvicorn
- HTTP POST bridge
- `mindmemos-sdk`
- 公共 API key 系统
- Docker 作为普通用户依赖
- 跨设备实时同步

HPC 上的 Apptainer 只可作为开发/验证环境，不进入正式产品架构。

## 5. Add 与 agent 调用边界

当前已确认：直接把现有 Homemaster `AddMemoryInput(FactRecord|ProcedureRecord)` 原封不动转给标准 MindMemOS add 并不等价，因为标准 MindMemOS add 是消息抽取、schema 建模和可能的 merge/update/delete 流程。

有两个候选方向，尚未最终决定：

### 方向 A：保留模型显式 `add_memory` 工具

重做工具输入，使其更接近 MindMemOS：

```text
messages
session context
optional metadata
```

模型不再手工构造完整底层 `MemoryWrite`，memory type 和 schema entity 由 MindMemOS extractor 决定。

### 方向 B：框架自动写入和召回

- 每轮开始前由框架自动 search 并注入相关记忆。
- 每轮结束后由框架自动将本轮 dialogue 送入 MindMemOS add。
- 模型不需要普通召回用 search 工具，也不一定需要 add 工具。
- 只为明确的修正、反馈、删除或管理动作保留工具。

该选择将影响模型工具 schema、context 构建、成本、延迟和错误恢复，需要继续讨论。

## 6. 一键安装与跨平台运行方向

### 6.1 已确定的产品约束

- 每台设备保存独立本地记忆。
- 不负责自动把记忆从一台设备同步到另一台。
- 提供显式 export/import 即可。
- 普通用户不应手动安装数据库。

### 6.2 候选安装包结构

每个平台构建独立安装包：

```text
Homemaster
├── application/python runtime
├── MindMemOS
├── Qdrant client local runtime
└── runtime/
    ├── java/       Eclipse Temurin JRE 21
    └── neo4j/      Neo4j Community Edition
```

候选平台至少包括：

- Windows x86_64
- macOS arm64
- macOS x86_64（是否继续支持待产品决策）
- Linux x86_64

### 6.3 Neo4j 生命周期

Homemaster 首次/每次启动负责：

1. 验证内置 runtime manifest 和文件摘要。
2. 创建设备本地数据、日志和 secret 目录。
3. 取得单实例锁。
4. 生成/读取本机数据库 credential。
5. 只绑定 loopback。
6. 启动 Neo4j 用户态子进程。
7. 等待 Bolt health check。
8. 初始化/验证 MindMemOS schema。
9. Agent 进入 ready。

关闭和失败时需要处理：

- graceful shutdown
- absolute deadline
- orphan process 检测
- crash recovery
- 端口冲突
- 数据目录锁
- schema/runtime version mismatch
- 磁盘不足
- 部分升级回滚

### 6.4 Runtime 与数据分离

安装包中的 Neo4j/JRE 是可替换 runtime；用户记忆数据必须位于独立、持久的 per-user data root。

升级或卸载默认不能删除记忆。只有用户明确选择清除数据时才删除 memory data root。

### 6.5 内置基线与独立更新

当前倾向：

- 安装包内置一套离线可用的 Temurin JRE 21 + Neo4j CE 基线。
- runtime manifest 锁定平台、架构、版本、来源和 SHA-256。
- 后续可独立更新 runtime，不强制重新下载整个 Homemaster。
- 数据格式升级前先产生可恢复备份。
- 更新失败继续使用上一套已验证 runtime。

### 6.6 第三方许可证

初步核实：

- Qdrant：Apache License 2.0。
- Neo4j Community Edition：GPLv3。
- Eclipse Temurin：GPLv2 with Classpath Exception。

正式分发前需要专项许可证审查，并在安装包中提供第三方许可证、NOTICE、准确版本、来源和适用的源码获取信息；同时遵守 Neo4j 商标使用边界。

## 7. Export/Import 方向

因为不做跨设备同步，需要提供显式命令，例如：

```text
homemaster memory export <bundle>
homemaster memory import <bundle>
```

导出包候选内容：

- Qdrant 逻辑记录/一致性快照
- Neo4j 逻辑记录/一致性快照
- schema version
- runtime/data format version
- embedding model identity 与维度
- record counts
- content digests
- manifest 与完整性哈希

不能在数据库活跃写入时直接复制底层目录并把它宣称为一致性备份。具体导出协议仍待设计。

## 8. 当前服务器环境事实

HPC2_weilin 当前：

- Homemaster 已切换到 `mindmem` 分支。
- Docker 已安装，但当前用户无 Docker daemon 权限。
- Apptainer 1.4.5 module 可加载运行。
- Singularity module 可用。
- Java 21 module 可用。
- user systemd 不可用。
- Qdrant/Neo4j 默认端口当前空闲。

这些事实只决定 HPC 上如何验证，不决定最终桌面产品架构。

## 9. 当前阻断点与成立条件

### 9.1 直接嵌入成立条件

1. 为 MindMemOS 提供稳定的 embedded facade，而不是让 Homemaster 到处直接引用内部 singleton。
2. Homemaster `ApplicationRuntime` 成为该 facade、LLM clients、DB clients 和 Neo4j process 的唯一生命周期 owner。
3. MindMemOS 作为锁定、可构建、可在源码外安装验证的依赖进入 Homemaster wheel/installer。
4. 完成 Qdrant Local Mode 的真实能力对照测试。
5. 明确 Neo4j 初始化、健康、失败和关闭协议。

### 9.2 记忆语义成立条件

1. 明确采用 MindMemOS schema add 后，现有 `FactRecord/ProcedureRecord` 工具契约哪些保留、哪些废弃。
2. 为 Homemaster procedure 定义 MindMemOS entity schema，并验证 add/search/update/delete 行为。
3. 明确 memory type 是展示分类还是外部稳定业务类型。
4. 决定记忆是由模型显式写入还是由框架自动摄取。
5. 保留 tenant/user/agent/session 的可信隔离映射。

### 9.3 部署成立条件

1. 每个平台构建独立安装产物并在真实 OS/架构验收。
2. 内置 JRE/Neo4j 使用固定版本、固定摘要和可追溯来源。
3. 数据目录与 runtime 分离。
4. loopback-only、credential、文件权限和日志边界经过验证。
5. 升级、卸载、导出、导入和故障恢复不丢数据。

### 9.4 迁移成立条件

1. 旧 mem0 保持可回滚，不能在首次切换时直接删除。
2. 逻辑导出两条现有 memory points 及必要 metadata。
3. 通过 MindMemOS schema pipeline 或确定的迁移入口导入。
4. 分别验证 Qdrant、Neo4j 和公开 search 终态。
5. 保留 evidence ledger；mem0 history 作为旧审计数据归档。
6. shadow-read/对照通过后再切换默认 backend。

## 10. 已达成共识

1. 不采用公共 MindMemOS 服务。
2. 不以 HTTP/SDK 作为 Homemaster 与 MindMemOS 的主要边界。
3. MindMemOS 作为本地 Python 能力直接嵌入 Homemaster。
4. procedure 优先按 MindMemOS schema 能力建模，而不是立即新增底层 memory type。
5. 不做跨设备记忆同步，只做显式 export/import。
6. 产品架构不能绑定 HPC/Apptainer/Docker。
7. 普通用户需要一键安装。
8. 当前倾向内置 Temurin JRE 21 与 Neo4j CE，Qdrant 尝试 Local Mode。
9. runtime 与用户数据必须分离。
10. 旧 mem0 在验证完成前保留为回滚源。

## 11. 仍待继续讨论的关键问题

1. `add_memory` 继续作为模型显式工具，还是改为框架每轮自动摄取？
2. search 是每轮自动 context recall，还是保留模型可调用的普通 search 工具？
3. Homemaster 的 `FileMemoryStore` 是否继续独立存在，还是逐步并入 MindMemOS profile/schema memory？
4. procedure schema 的准确 entity/property/edge 设计是什么？
5. MindMemOS 的 schema add 与 vanilla add 在 Homemaster 中如何选择？
6. 是否始终启用 Neo4j graph，还是还要提供 graph-disabled 轻量模式？
7. 本地 identity 应如何从 Homemaster authoritative tenant/user/agent/session 映射到 `MemoryRequestContext`？
8. 导出/导入采用逻辑记录、数据库原生 dump，还是带独立 verifier 的组合 bundle？
9. 首批正式支持哪些 OS/架构？
10. Neo4j/JRE 是完全随安装包内置，还是“内置基线 + 可独立更新”？当前倾向后者。

## 12. 建议的下一步讨论顺序

1. 先确定运行时调用时序：自动 recall/自动 add 与显式工具的分工。
2. 再确定 procedure 及通用 memory schema。
3. 再定义 `EmbeddedMindMemOS` 的最小稳定接口和生命周期。
4. 然后确定 Qdrant Local Mode/Neo4j 完整图的存储组合。
5. 最后形成正式设计、迁移计划和跨平台安装计划。

## 13. 本轮补充讨论：Local Mode、身份字段与高级能力适配

### 13.1 “没有 embedded 入口”不等于“没有 embedding”

这里需要纠正术语混淆：

- MindMemOS 本身已经实现 embedding，包括 embed model router、向量维度校验、dense/sparse vector、Qdrant 存储、混合检索和 rerank 等代码。
- 当前缺少的不是 embedding，而是面向宿主框架的 **embedded/local facade**：即一个由 Homemaster 直接持有、负责 `start/add/search/update/delete/feedback/dream/close` 及完整资源生命周期的稳定 Python 入口。
- 因此不能把当前阻断点描述成“MindMemOS 没有向量化能力”；准确说法是“现有项目主要按服务运行形态组织，尚未提供适合 Homemaster 进程内嵌入的完整宿主接口”。

### 13.2 Qdrant Local Mode 从第一阶段开始就是硬条件

用户明确要求：第一版就做 Local Mode，不先以独立 Qdrant 服务作为产品默认方案。

建议在 MindMemOS 的 Qdrant 配置中明确区分：

```yaml
database:
  qdrant:
    mode: local          # local | server
    path: <Homemaster data root>/mindmem/qdrant
    vector_size: 4096
```

实现边界建议：

1. `mode=local` 时，由 embedded runtime 创建并注入 `AsyncQdrantClient(path=...)`。
2. `mode=server` 时才使用 `url/api_key/grpc_port` 等网络参数。
3. MindMemOS 当前 `QdrantEngine/QdrantStore` 已支持注入 client，因此不需要重写整个存储层。
4. Homemaster `ApplicationRuntime` 必须成为该 client 的唯一 owner，统一负责初始化和关闭。
5. 同一路径只能由一个有效 client/进程持有；必须阻止不同 event loop 或多个 Homemaster 进程同时打开同一 Local Mode 数据目录。
6. 不能只验证基础 dense search；必须对 named dense vector、sparse/BM25 IDF、hybrid search、所有 collection、batch writer、update/delete、持久化重开、并发与异常恢复做能力对照测试。

这意味着 Qdrant 不需要 Docker，也不需要另起 Qdrant server；Neo4j 仍是独立本地 DBMS 进程，由 Homemaster 内置 runtime 启动和管理。

### 13.3 `MemoryRequestContext` 身份字段的用途与本地默认值

这些字段不是“为了调用云 SDK 才存在”，而是 MindMemOS 内部用于数据归属、隔离、追踪和 pipeline 选择的上下文。即使完全本地运行，也应给出稳定值。

| 字段 | 作用 | Homemaster 本地建议映射 |
|---|---|---|
| `account_id` | 表示数据所属账户/安装主体，用于所有权和追踪 | 首次安装生成并持久化的 installation UUID |
| `project_id` | Qdrant/Neo4j 中的硬隔离分区，是最重要的租户边界之一 | 默认 `homemaster-local`；若未来支持多个本地 workspace，再映射为 workspace/project UUID |
| `api_key_uuid` | 记录请求来源凭证/调用主体，主要用于 provenance、审计和服务模式隔离 | `embedded-<installation UUID>`，只是稳定本地调用者 ID，不创建真实 API key |
| `memory_algorithm` | 选择 add/search 等 pipeline 算法，不是 embedding model 名称 | 默认 `schema` |
| `user_id` | 用户/owner 维度 | Homemaster 本地 profile ID |
| `app_id` | 调用应用 | 固定 `homemaster` |
| `agent_id` | 当前 agent/profile 维度 | Homemaster authoritative agent ID |
| `session_id` | 当前会话维度 | Homemaster session ID |
| `request_id` | 单次操作追踪和幂等关联 | 每次操作生成 UUID |

已确认默认使用 schema 模式。`memory_algorithm=schema` 表示选择 MindMemOS 的 schema add pipeline；它不替代 embedding 配置，也不等同于 `memory_type`。

上述 ID 必须由 Homemaster 的可信 runtime 注入，不能允许模型在工具参数中自行指定，否则会破坏用户、agent、session 和 project 的隔离边界。

### 13.4 Homemaster 工具层需要怎样适配

不能简单地把 MindMemOS 内部函数原样暴露成模型工具。建议保留 Homemaster 现有工具契约层，由适配器调用 embedded facade：

- `add_memory`：改为将自然语言消息、附件或结构化 schema 输入交给 `memory_algorithm=schema` 的 add pipeline；保留 Homemaster 的权限、证据、审计、超时、取消和终态验证。
- `search_memories`：调用 MindMemOS search pipeline，但 `project/user/agent/session` 过滤条件由 runtime 强制注入。
- `get/update/delete_memory`：通过薄适配层调用相应 pipeline，并继续使用 Homemaster 的统一错误和 `ToolExecutionResult` 语义。
- SDK 与 HTTP 都不进入主调用链；Homemaster 直接调用本地 Python facade。SDK 只对远程服务消费者有意义。

因此“工具层不兼容”是需要解决的一部分，但不是全部；同时还要处理 runtime 生命周期、身份隔离、schema 语义、双数据库一致性、迁移和失败恢复。

### 13.5 feedback 是否需要 Homemaster 适配

需要，但属于中等规模适配，不要求部署 Kafka。

第一阶段建议使用 MindMemOS 的同步 feedback pipeline（`feedback_sync`），由 Homemaster 提供：

- 当前/相关对话消息；
- 本轮实际召回并使用过的 memory IDs；
- 用户显式反馈或框架产生的结果信号；
- authoritative `MemoryRequestContext`；
- 超时、取消、错误、审计和终态检查。

触发方式可包括显式 feedback 工具和框架事件 hook。尚需继续确定：首版只接受用户显式反馈，还是同时接入任务成功/失败等自动信号。

### 13.6 dreaming 是否需要 Homemaster 适配

需要。MindMemOS 提供的是 dreaming 能力/pipeline，Homemaster 必须负责何时运行和如何安全运行。

第一阶段建议调用同步入口 `dream_sync`，不依赖 Kafka。它不应直接成为普通模型工具，而应由 Homemaster 后台调度器触发，并负责：

- 空闲、会话结束或定时触发策略；
- 输入上下文和待整理 memory 范围；
- 与前台 add/update/delete 的并发互斥；
- 运行预算、超时、取消、失败重试；
- 产生修改后的审计和终态验证。

因此，dreaming 不是“把 MindMemOS 装进环境就会自动生效”；框架调度适配是成立条件。

### 13.7 Skills 自演进是否需要 Homemaster 适配

需要，而且适配深度明显高于基础记忆、feedback 和 dreaming。MindMemOS 无法自动理解 Homemaster 的 SkillRegistry、技能文件、加载方式、权限和发布策略。

完整桥接至少包括：

1. 将 Homemaster 已有 skill 注册/同步到 MindMemOS `SkillVersionStore`。
2. add 时传递准确的 `skill_context`，使对话、记忆和使用中的 skill 建立关联。
3. 保存可用于评估的 transcript/add records。
4. 为一次执行提供 `task_id` 和可解释的 score/feedback。
5. 调用 evolve pipeline；其参数名即使叫 `cloud_skill_id`，在本地模式也可以只是本地 SkillVersionStore 中的稳定 skill ID，不代表必须访问公共云。
6. 将候选新版本送入 Homemaster 的验证、权限审查和发布流程。
7. 支持 reload/restart、版本回滚和失败隔离；不能让生成结果未经验证就覆盖正在运行的 skill。

Skill 自演进不是替换 mem0、实现基础 memory CRUD 的前置阻断点，但如果要宣称 V2.3 已支持该能力，上述桥接就是必需条件。

### 13.8 当前建议的阶段边界（尚待用户确认）

建议将 V2.3 第一阶段定义为：

1. MindMemOS 作为锁定的本地 Python 依赖进入 Homemaster。
2. 提供 `EmbeddedMindMemOS` facade 和统一生命周期。
3. Qdrant 从第一阶段即采用 Local Mode。
4. Neo4j 使用随 Homemaster 安装和管理的本地 runtime。
5. `memory_algorithm` 默认 `schema`。
6. 完成 add/search/get/update/delete 工具适配。
7. 接入同步 feedback。
8. 接入受调度的同步 dreaming。
9. 不引入 Kafka、公共服务或主调用链 HTTP/SDK。
10. 保留旧 mem0 的 shadow/rollback 路径，验证完成后再切换默认 backend。

Skill 自演进建议单独进入后续阶段，内容包括 skill 注册/版本同步、`skill_context`、transcript/score/task_id、evolve、候选补丁审查、发布、reload 和 rollback。该分期目前是建议，尚未被用户最终确认。
