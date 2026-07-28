# HomeMaster V2.1 记忆系统改造实施计划

## 0. 状态

- Owner：主 agent
- 日期：2026-07-27
- 基线提交：`bb927d4c6e11f1fe291db0e1d9b0ecb7adfd34d1`
- 当前阶段：WP1-WP6、真实外部终态、隔离 wheel 安装和完整非 live 回归已完成；等待最终只读代码评审
- 旧讨论记录：`plan/V2.1/homemaster-memory-system-discussion.md` 仅保留历史讨论，不再作为实施依据
- 正式实施真理源：本文档
- 计划评审：唯一一次只读 reviewer subagent 评审已完成；9 项发现全部采纳，处置见 §21
- 最终代码评审：实现、测试、外部终态验证和文档更新全部完成后启动一次只读 reviewer subagent
- 工作树状态：仓库已有用户修改；本任务不得覆盖、回退或顺带整理这些修改

计划评审门已满足；用户 review 并明确授权实施以前，禁止开始产品代码实施。

## 1. 目标与完成形态

为 HomeMaster 增加一套单用户、可解释、可持久化、由 Agent 显式调用工具维护的通用记忆系统：

1. `SOUL.md` 保存 HomeMaster 人格、身份、价值观、表达方式和长期行为原则；
2. `USER.md` 保存用户当前稳定画像、偏好和协作方式；
3. `MEMORY.md` 保存近期重要事件、决定、结果和跨会话未完成事项；
4. mem0 `fact` 保存外部世界当前事实；
5. mem0 `procedure` 保存已经真实执行成功、逐步可验证的结构化操作流程；
6. `SOUL.md -> USER.md -> MEMORY.md` 以每个 live session 的冻结快照确定性拼接到 system prompt；
7. mem0 不自动预取、不整库注入上下文，由 Agent 通过五个 HomeMaster 原生工具按需检索和维护；
8. mem0 通过 Python SDK 直接成为 HomeMaster application-owned 组件，不经过 MCP；
9. Qdrant 使用本地持久化模式，embedding 复用 HomeMaster 现有 `MemoryEmbedding` provider；
10. 第一版不实现时间衰减、置信度、过期、自动遗忘、自动归档、多用户或多家庭作用域。

最终公开记忆工具固定为六个：

```text
memory
add_memory
search_memories
get_memory
update_memory
delete_memory
```

`memory` 管理 `USER.md` 和 `MEMORY.md`；其余五个工具管理 mem0 中的 `fact/procedure`。
`SOUL.md` 不向 Agent 提供写工具。

## 2. 上游决策与候选方案

### 2.1 总体存储架构

候选方案：

1. 所有记忆都存进 mem0。检索统一，但人格和用户偏好不再保证每轮可见，且 Markdown 人工维护能力消失。
2. 所有记忆都存 Markdown。实现简单，但事实和流程缺少向量召回、稳定 ID 和结构化 CRUD。
3. `SOUL/USER/MEMORY` 文件快照 + mem0 `fact/procedure`。职责明确，固定上下文与按需召回各用合适存储。
4. 文件记忆 + mem0 MCP。能复用远程工具发现，但增加服务边界、MCP 工具别名和本项目不需要的部署模式。

锁定方案 3。用户已明确要求 mem0 与 HomeMaster 产品级耦合，因此不得重新引入 MCP、云端 mem0、
可插拔 memory provider 或运行时 backend mode。

### 2.2 Agent 工具组织

候选方案：

1. 每个文件操作一个工具。描述直观，但工具面膨胀。
2. 一个 `memory(action, target, ...)` 管理文件记忆，五个薄工具直接映射 mem0 CRUD。
3. 一个超级 `memory(store, type, action, ...)` 包装所有后端。模型工具少，但 HomeMaster 会重新实现 mem0 路由。
4. 只暴露 mem0 工具。无法管理 `USER.md/MEMORY.md`。

锁定方案 2。它与 Hermes 的单 `memory` 文件工具接近，同时保留 mem0 原生 CRUD 语义。

### 2.3 向量数据库

候选方案：

1. Qdrant embedded：支持持久化、metadata 精确过滤、关键词/向量混合检索和 CRUD，不需要独立服务。
2. Chroma：原型简单，但本项目需要的过滤、生命周期和后续并发路线不如 Qdrant 清晰。
3. PostgreSQL + pgvector：并发和运维成熟，但第一版没有既有 PostgreSQL 部署可复用。
4. FAISS：本地轻量，但 metadata 过滤、更新、删除和持久化管理不适合当前需求。

锁定方案 1。第一版只有一个 HomeMaster 进程持有 embedded Qdrant；未来如果出现多进程并发需求，
用户再主导迁移到独立 Qdrant Server，本计划不提前增加两种 mode。

## 3. 范围与非目标

### 3.1 第一版范围

- 单用户、单 HomeMaster 身份；不设计公开 `user_id/home_id/agent_id` 参数；
- 一个 application-owned `Mem0MemoryStore`；
- 一个 application-owned `FileMemoryStore`；
- 每个 live session 首次组装上下文时冻结一次文件记忆快照；
- `USER.md` 1,375 字符上限；
- `MEMORY.md` 2,200 字符上限；
- 超限时不自动删除，返回当前条目，由 Agent 使用原子 batch 合并/删除后再写；
- mem0 使用 `infer=False`，HomeMaster 自己验证并序列化结构；
- 事实采用“同一事实最新有效值覆盖当前值”；
- procedure 只有在完整流程成功并有当前 run 的成功证据后才能新增或更新；
- 用户明确告知和环境实际观察是唯二来源。

### 3.2 明确不做

- 多用户、家庭共享、租户级记忆作用域；
- 自动从每轮对话抽取记忆；
- 自动把历史 session 全量导入 mem0；
- `confidence`、importance、TTL、expiration、decay、遗忘曲线；
- 按时间自动删除或归档 `MEMORY.md`；
- mem0 graph memory、reranker、entity graph、自动 LLM fact extraction；
- 网站 DOM、snapshot id、element id、屏幕坐标、XPath、Cookie 或 session token 的长期保存；
- 把失败或未经验证的网页路径保存成 procedure；
- procedure 自动执行引擎；第一版只负责存取结构化步骤，执行仍调用现有通用工具并逐步验证；
- mem0 MCP、OpenMemory Server、Mem0 Platform 和云端数据同步；
- 为旧对象记忆和新 mem0 记忆保留两套默认公开工具。

## 4. 当前基线事实

### 4.1 HomeMaster

- 当前 `HomeMasterConfig` 没有正式 memory 配置段；
- `config/homemaster.yaml` 已有 gitignored、mode-0600 的 `MemoryEmbedding` provider；
- 该 provider 已写为：
  - base URL：`https://api.siliconflow.cn/v1`
  - endpoint：`https://api.siliconflow.cn/v1/embeddings`
  - model：`Qwen/Qwen3-Embedding-8B`
- 2026-07-27 已按普通 OpenAI-compatible 请求以及 mem0 2.0.13 的真实请求形状
  `encoding_format=float + dimensions=4096` 各做一次 live 请求；两次均 HTTP 200，服务端返回准确模型名和
  4096 维有限非空向量；
- `ContextAssembler` 当前只动态组装 conversation/task/failure/budget/skills，不加载 `SOUL/USER/MEMORY`；
- `ApplicationRuntime` 每次 run 新建 `ContextAssembler`，但同一个 session 可以跨 run 继续存在；
- application composition 已有 application-owned service 注入和 ResourceScope 生命周期；
- `ToolRegistry` 支持普通名称原子注册，重复名称 fail closed；
- Home profile 已有旧 benchmark/object-memory `memory_retriever`、`memory_writer` 路径，迁移时必须审计，
  不能让默认 Home Agent 同时看到语义重叠的两套长期记忆工具。

### 4.2 mem0 OSS

- 本地参考源码：`/hpc2hdd/home/wyuan140/weilin_workspace/mem0`
- 版本：`mem0ai==2.0.13`
- 锁定参考提交：`ca2abca2b884e038d3e525070e79d3057ef2012c`
- 已从本地源码核对 `Memory.from_config()`、`add(..., infer=False, metadata=...)`、`get()`、`get_all()`、
  `search()`、`update(text=..., metadata=...)`、`delete()`；
- `search/get_all` 要求 filters 至少包含 `user_id/agent_id/run_id` 之一；第一版内部固定使用一个技术性
  `user_id="homemaster"`，不向模型或用户暴露作用域参数；
- `infer=False` 会直接嵌入并保存输入文本，不调用 LLM 做抽取；
- `update` 支持同时更新 text 和 metadata，身份字段不可变；
- Qdrant config 支持本地 `path`、`collection_name`、`embedding_model_dims` 和 `on_disk`；
- mem0 默认启用匿名 PostHog telemetry。HomeMaster 家庭记忆不得默认产生这条外联，实施必须在首次 import
  mem0 以前关闭 `MEM0_TELEMETRY`，并用 socket/HTTP 黑盒门证明无 PostHog 请求；
- 当前 HomeMaster venv 尚未安装 `mem0ai` 和 `qdrant-client`，不能把源码存在当成运行时可用证据。

外部符号存在不等于 installed wheel/runtime 可用。上述 API 在完成项目 venv 安装、真实 Qdrant CRUD 和
HomeMaster 顶层运行验证以前均属于本地源码已核对、目标环境尚待验收。

## 5. 目标架构

```text
ApplicationRuntime.start
  -> FileMemoryStore 初始化目录/文件/锁
  -> Mem0MemoryStore 从 HomeMaster config 构造
       -> mem0 Memory 2.0.13
       -> Qdrant embedded
       -> HomeMaster MemoryEmbedding provider
  -> 六个 canonical memory tools 已在 Home ToolRegistry

每个 live session 第一次 ContextAssembler.prepare
  -> FrozenMemoryContextService 按 session_id 读取一次
       -> SOUL.md
       -> USER.md
       -> MEMORY.md
  -> 生成冻结 system prompt suffix
  -> 后续同 session run/iteration 始终复用相同 suffix

模型调用 memory
  -> FileMemoryTool executor
  -> action 级权限
  -> 文件锁 + reload + drift 检查 + 容量检查
  -> mode-0600 临时文件 + fsync + atomic replace
  -> 独立重新打开最终文件验证

模型调用 add/search/get/update/delete_memory
  -> 对应薄 executor
  -> Mem0MemoryStore 统一验证、序列化、锁和错误映射
  -> asyncio.to_thread 调用同步 mem0 SDK
  -> Qdrant/SQLite 外部终态重新读取
  -> 返回规范化结构化结果
```

产品级锁定 mem0，不增加通用 provider Protocol。代码仍通过 `Mem0MemoryStore` 集中拥有 SDK 实例、配置转换、
序列化、错误映射和生命周期，避免五个工具分别 import mem0 或复制规则。

## 6. 文件记忆设计

### 6.1 路径和权限

默认目录：

```text
~/.homemaster/memories/
  SOUL.md
  USER.md
  MEMORY.md
```

要求：

- 目录 mode `0700`；文件 mode `0600`；
- 路径来自 validated HomeMaster config，不依赖 cwd；
- 缺失文件在 application start 时创建；已有文件绝不覆盖；
- `SOUL.md` 初始内容从 package data Markdown 模板复制，模板不含用户数据；
- `USER.md/MEMORY.md` 初始为空；
- 所有变更在同目录临时文件完成，flush + fsync 后 atomic replace；
- 使用独立 lock file 做跨 session/process 的 read-modify-write 锁；
- 写前在锁内重新读取，不能以 application 启动时的缓存覆盖其他 session 的新内容；
- 读取失败必须拒绝写入，不能把 unreadable 当 empty；
- 检测到无法 round-trip 的手工编辑时拒绝破坏性 update/delete/batch，并保存 mode-0600 备份供人工修复；
- add 也必须在成功读取原文件后执行，不能以“追加安全”为由从空状态重写未知文件。

### 6.2 Entry 格式

`USER.md/MEMORY.md` 使用 Markdown entry，entry 之间采用 Hermes 已验证的明确分隔符：

```text
\n§\n
```

entry 可以多行；工具读写只操作完整 entry，不按字符截断。文件内容本身保持可人工阅读。

输入 canonicalization 固定为：CRLF/CR 统一为 LF，保留正常多行和 Unicode；拒绝 NUL、非换行/制表的控制字符，
并拒绝内容中出现完整 delimiter `\n§\n`，不做可能改变含义的自动转义。add 与已有 canonical entry 完全相同时返回
幂等成功，不重复追加。容量按 canonical serialization 的 Unicode code points 计数。

`USER.md` 示例：

```markdown
- 沟通偏好：回答保持简洁，先给结论，再给必要依据。
§
- 工作方式：涉及方案设计时先讨论并确认，再修改文件。
```

`MEMORY.md` 示例：

```markdown
- [进行中][2026-07-27] HomeMaster V2.1 记忆系统：工具和存储分层已确定，等待实施计划 review。
§
- [决定][2026-07-27] 第一版不实现自动遗忘、衰减和过期。
```

### 6.3 内容边界

`SOUL.md`：

- 人格、身份、价值观、表达方式、长期行为原则；
- Agent 只读；用户或开发者通过文件维护；
- 不保存用户偏好、近期工作、物体位置或操作流程。

`USER.md`：

- 当前仍成立的用户身份、稳定偏好、沟通方式、工作习惯和长期约束；
- 不保存偏好变化历史；纠正时原地更新旧 entry；
- “这次先不要改”不写；“以后方案都先讨论再改”可写；
- 不保存 fact/procedure/近期任务。

`MEMORY.md`：

- 跨会话仍需继续的重要事项；
- 最近完成且值得回顾的结果；
- 会影响后续工作的明确决定；
- 失败后需避免重复的关键结论；
- 同一事项只保留一条当前状态，`进行中 -> 已完成` 使用 update，不做过程流水追加；
- 不保存每次工具调用、完整聊天、临时 task planner、外部当前事实或可复用步骤。

### 6.4 字符上限

与 Hermes 默认值一致：

```text
USER.md   = 1,375 Unicode code points
MEMORY.md = 2,200 Unicode code points
```

计数对象为序列化后的完整 entry 内容加分隔符，不按 UTF-8 bytes 计数。`SOUL.md` 不由 Agent 写，本计划不为它
增加自动整理策略。

超限行为：

1. 单条 entry 自身超过目标总上限：拒绝；
2. add/update/batch 最终状态超过上限：整次事务拒绝；
3. 错误结果返回 `current_entries`、当前/上限字符数和可执行的合并/删除说明；
4. 系统不自动删最旧 entry；Agent 明确选择 update/delete；
5. batch 只按最终状态检查容量，允许同一事务先删/压缩再新增；
6. batch 任一步匹配不唯一、校验失败或最终超限时全部不写。

### 6.5 冻结快照

- `FrozenMemoryContextService` 以 live `session_id` 为 key；
- 第一次为该 session 组装上下文时，从磁盘读取并生成不可变字符串；
- 顺序固定为 base system prompt、`SOUL.md`、`USER.md`、`MEMORY.md`；
- 空文件不生成空标题块；
- 同 session 中 `memory` 工具写盘立即持久化，但冻结 system prompt 不改变；
- 工具成功结果返回 live 终态摘要；显式 `read` 返回 live entries；
- 新 session 或应用重启后的首次 session 组装读取最新文件；
- 冻结快照必须计入 ContextAssembler token estimate 和 compaction 后预算；
- conversation compaction 不得移除 system prompt 中的三类记忆块；
- 多个 session 的快照独立；写盘不能回写其他 session 已冻结的字符串。

## 7. mem0 结构化记录

### 7.1 物理存储形态

Agent 工具接收结构化对象，HomeMaster 同步派生三份同源数据并在一次 mem0 add/update 中提交：

1. `memory` text：由结构确定性生成的自然语言检索文档，用于 embedding/关键词搜索；
2. metadata `record_json`：canonical compact JSON string，保存完整结构化记录。
3. metadata 扁平索引字段：由完整结构确定性生成，用于 dedupe 和精确过滤。

选择 `record_json` 字符串而不是依赖任意深度 nested metadata，避免不同 vector store payload/filter 对嵌套 JSON
语义不一致。以下字段作为扁平 metadata 单独保存，供精确过滤。字段按 memory type 固定，不能由 Agent 任意追加：

```text
schema_version
memory_type
dedupe_key
source
record_json
subject_type              # fact
subject_id                # fact，可选
subject_name_normalized   # fact
predicate                 # fact
procedure_name_normalized # procedure
entry_url_normalized      # procedure
provenance_seq            # 内部持久化单调序号
```

另外由 mem0 内部写入固定技术性 `user_id="homemaster"`。第一版不向工具公开任何 scope 字段。

HomeMaster 是 search text、扁平索引字段与 `record_json` 的唯一生成者；Agent 不直接提交任意 metadata、dedupe key、
时间戳、provenance sequence、mem0 identity 或序列化 JSON。update 必须从完整结构重新生成三份数据，禁止只改
其中一份。normalized 字段的 Unicode normalization、case 和 URL canonicalization 规则版本化并做同输入同输出测试；
原始可读值仍只以 record 为准。

### 7.2 `fact` schema

```json
{
  "schema_version": 1,
  "memory_type": "fact",
  "subject": {
    "type": "object",
    "name": "苹果"
  },
  "predicate": "location",
  "value": {
    "container": "冰箱",
    "position": "第二层"
  },
  "source": "environment_observation"
}
```

`subject`：

- required：`type`、`name`；
- optional：`id`，仅用于真实设备或具有稳定外部 ID 的对象；
- `type` 第一版允许 `object/device/room/place/service/account/other`；
- 没有真实稳定 ID 时禁止模型发明 ID。

`predicate`：

- 使用 lowercase snake_case；
- 优先使用标准词：`location`、`power_state`、`open_state`、`temperature`、`status`；
- 标准词无法表达时允许新的 snake_case predicate；
- 同一语义不得混用 `location/place/position` 等别名；工具描述和测试固定标准示例。

`value`：

- 允许 string、number、boolean 或 JSON object；
- 禁止 null、二进制、凭证、任意工具结果 dump 和不可解释字符串；
- location object 只保存稳定、可解释的容器/位置字段，不保存屏幕坐标。

`source` 固定枚举：

```text
user_statement
environment_observation
```

外部工具或设备接口返回属于 `environment_observation`，不另设 `tool_result`。Agent 推测不能写 fact。

fact `dedupe_key` 由 HomeMaster 生成：

```text
subject.id 存在：sha256("fact\0" + subject.type + "\0id\0" + subject.id + "\0" + predicate)
否则：sha256("fact\0" + subject.type + "\0name\0" + subject.name + "\0" + predicate)
```

正文不保存 `environment`、`record_key`、`confidence` 或人工时间戳。mem0 自己的 created/updated timestamp
作为持久化时间；来源观测证据进入 HomeMaster trace/evidence ledger，不复制大 payload 到记忆。

### 7.3 `procedure` schema

```json
{
  "schema_version": 1,
  "memory_type": "procedure",
  "name": "查询当前告警",
  "entry_url": "https://monitor.example.com/alarms/current",
  "steps": [
    {
      "order": 1,
      "action": "open",
      "target": {
        "url": "https://monitor.example.com/alarms/current"
      },
      "expect": {
        "visible_text": "当前告警"
      }
    },
    {
      "order": 2,
      "action": "click",
      "target": {
        "role": "button",
        "name": "查询"
      },
      "expect": {
        "visible": {
          "role": "table",
          "name": "告警列表"
        }
      }
    },
    {
      "order": 3,
      "action": "extract",
      "target": {
        "role": "table",
        "name": "告警列表"
      },
      "output": "alarms"
    }
  ],
  "success": {
    "output_exists": "alarms"
  },
  "source": "environment_observation"
}
```

必要字段：

- `schema_version`：结构升级；
- `memory_type`：fact/procedure 分流；
- `name`：人和模型可理解的流程目标；
- `entry_url`：真实稳定入口；
- `steps`：结构化流程；
- `success`：整个流程成功判据；
- `source`：procedure 第一版只允许 `environment_observation`。

按需字段：

- `inputs`：流程确实需要外部参数时才出现；每项定义 name/description/required，不保存真实敏感值；
- `preconditions`：只有特殊前置条件时出现；普通“已经登录”不重复写到每条流程；
- step `output`：该步骤确实产生后续结果时才出现。

不得保存：

- 抽象 `environment="monitoring_web"`；
- Agent 自填 `procedure_key`；
- session/token/userinfo/signed query 参数；
- snapshot-specific element ID、DOM ref、CSS、XPath、屏幕坐标；
- 未验证的自然语言猜测步骤。

`entry_url` 必须是 http/https 绝对 URL，不得含 userinfo；含 credential/session/token 类 query 参数时拒绝保存，
由 procedure 输入或运行时认证处理。工具不擅自重写一个已接受 URL 的文本。

procedure `dedupe_key` 由 HomeMaster 根据 canonical `entry_url + name` 自动计算并只放 metadata。Agent 不感知。
精确查询必须同时具备 `entry_url + name` 才计算 key；仅提供其中一个 hint 时只按对应扁平字段缩小候选，不误当
唯一 identity。

steps：

- `order` 必须从 1 连续递增；
- `action` 第一版为 `open/click/fill/select/wait/extract`；
- `open/click/fill/select/wait` 必须有可机器检查的 `expect`；
- `fill/select` 使用 input 引用，不保存一次执行的真实值；
- `extract` 必须声明 `output`；
- `success` 必须引用实际 step output 或最终页面状态；
- procedure 是可解释执行提示，不绕过实时页面观察、权限或动作后验证。

## 8. mem0 写入、召回和更新

### 8.1 新增

`add_memory` 不盲目调用 SDK add：

1. Pydantic/JSON Schema 校验结构；
2. 校验 source 与当前证据类型；
3. 生成 canonical record、search text、dedupe key；
4. 先用固定内部 scope + memory_type + dedupe_key 做精确查询；该 key 完全由完整 record 生成，不依赖语义搜索；
5. 已存在且完整结构相同：幂等成功，不新增；
6. 已存在但不同：返回 `memory_conflict` 和准确 memory_id，要求模型使用 `update_memory`；
7. 不存在：调用 mem0 `add(... infer=False ...)`；
8. 检查 SDK 返回包含一个 ADD id；
9. 按 id 重新 get，逐字段核对 text、record_json、全部扁平索引字段；
10. 终态一致才返回成功。

### 8.2 混合召回

公开仍只有一个 `search_memories`。工具内部执行：

```text
可提取准确结构线索
  -> metadata 精确过滤候选

所有请求
  -> mem0/Qdrant hybrid search（关键词 + 语义向量）

合并
  -> memory_id 去重
  -> 精确匹配优先，其后按 score
  -> 限制 memory_type 和 top_k
  -> parse + schema validate record_json
  -> 返回结构化记录和匹配来源
```

fact 搜索优先使用扁平 `subject_type/subject_id/subject_name_normalized/predicate/dedupe_key`；procedure 优先使用扁平
`entry_url_normalized/procedure_name_normalized/dedupe_key`；缺少完整 identity 时将部分 exact hint 与 hybrid 结果合并，
不得通过解析 `record_json` 做无界全表扫描。缺少精确信息时使用 hybrid 召回。Agent 不需要分别调用“关键词工具”
和“语义工具”。

损坏、缺字段或 schema_version 不支持的记录不得作为正常命中悄悄返回；进入结构化 diagnostics，并且不授权
任何设备或网页动作。

默认 `limit=5`，允许 1..20。threshold 在完成真实中文 query fixture 标定以前保持 config 参数且标
`UNVERIFIED`，不得凭单一示例拍值。

### 8.3 get

- 只接受 search/add 返回的准确 memory_id；
- 读取后解析并验证 `record_json`；
- 返回 memory_id、memory_type、record、created_at、updated_at；
- 不接受任意 scope 或 metadata filter；
- ID 不存在返回稳定 `memory_not_found`，不能返回空成功。

### 8.4 最新事实覆盖

同一 fact dedupe identity：

```text
新 value 与当前 value 相同
  -> 幂等成功，不更新时间或向量

新 value 不同且来源有效
  -> update 原 memory_id
  -> source 替换为本次来源
  -> mem0 updated_at 变化

Agent 推测、失败工具结果或找不到证据
  -> 拒绝覆盖
```

第一版不保存旧 value 版本、不比较 confidence、不做来源优先级；“最新有效值”指当前 evidence 在持久化
`MemoryEvidenceLedger` 获得的 `provenance_seq` 大于当前记录，且 write 已通过来源和证据门，不是模型声称“更新”。
较早产生但较晚提交的观测必须返回 `memory_stale_observation`，不能回滚新值。

### 8.5 procedure 更新

- 第一次完整执行成功：add；
- 再次执行成功且结构未变：幂等，不更新；
- 页面变化后，新路径完整执行成功：update 原 memory_id；
- 一次失败、页面未加载或仍未找到新路径：不得覆盖旧 procedure；
- update 必须保持原 memory_id，重新生成 search text、record_json 和全部扁平索引字段；
- 如果 name/entry_url 改变导致 dedupe identity 与另一条记录冲突，拒绝并返回冲突 ID，不自动合并。

### 8.6 删除

- 只允许准确 memory_id；
- 仅在用户明确要求忘记、记录确认错误、确认重复或流程永久失效时调用；
- 第一版不得按时间、相关度或低使用频率自动删除；
- delete 后必须 get 返回 not found，并以同一底层 Qdrant client 的 raw point API 检查 ID 不存在；store 关闭后
  再由独立进程 reopen 做持久化复核，不在 live mutation 时打开同路径第二 client；
- 调用超时或异常且无法确认外部状态时返回 `outcome_unknown`，禁止自动重试删除。

## 9. 来源与证据门

### 9.1 `user_statement`

- 只用于用户当前消息明确陈述的 fact；Agent 声明 source 但必须同时提交由 runtime 为当前 canonical user turn
  注册的 opaque evidence ref；
- 不能把模型总结、推理或常识标为用户陈述；
- executor 验证 ref 属于同 session/run/turn、角色确为 user、发生在 memory tool call 以前，并从 ledger 取得
  `provenance_seq`；模型不能自报字符串绕过；
- 工具 trace 记录触发写入的 session/run/turn/tool_call 和 provenance ref hash，不复制整段用户隐私到 metadata；
- USER 偏好不进 mem0，应路由到 `memory(target="user")`。

### 9.2 `environment_observation`

- fact/procedure 写工具必须携带当前 run 已提交成功的 evidence refs；
- 新增 application-owned、ref 按 run 隔离的 `MemoryEvidenceLedger`，只接受 ToolExecutor 已完成且 status=success、
  verification=passed 或工具契约明确为 read observation 的 canonical result；
- memory tool executor 只按 opaque ref 查询 ledger，模型不能用任意字符串伪造证据；
- 证据必须属于同 tenant/session/run，且发生在 memory tool call 以前；
- procedure refs 必须覆盖按 order 执行的全部动作和最终 success 观测；
- 同一批并行 tool calls 中尚未提交的动作不能作为 procedure 证据；模型必须在后续 iteration 再保存；
- 外部工具/API 返回统一归类为 `environment_observation`，不增加第三个 source。

`MemoryEvidenceLedger` 的序号由 application-owned 私有 SQLite 表在 evidence 进入 ledger 时用单事务自增分配，并持久化
跨 session/restart；事实 mutation 在 mem0 写锁内比较当前 `provenance_seq` 后再写。用户 turn 和工具 observation 使用
同一序列域，保证“最新”按证据产生顺序而不是按谁最后抢到写锁。

`MemoryEvidenceLedger` 只解决“确实观察/执行过”，不让 memory 工具替代设备权限、浏览器 stale-ref、业务终态或
benchmark scorer。

## 10. 六个公开工具协议

所有工具使用 canonical `RegisteredTool/ToolDefinition`，不新增 legacy ToolSpec。

### 10.1 `memory`

职责：管理固定注入上下文的精选文件记忆。

参数：

```text
target: user | memory
action: read | add | update | delete       # 单操作
content: string                            # add/update
match: string                              # update/delete 的唯一子串
operations: [{action, content?, match?}]   # 原子 batch；与单操作互斥
```

描述必须明确：

- `target=user` 保存用户长期稳定身份、偏好、沟通和使用习惯；
- `target=memory` 保存近期事件、决定、结果和跨会话未完成事项；
- 物体位置、设备状态和可复用流程必须使用 mem0 工具；
- 两个文件在 session 首次上下文组装时已冻结注入，通常不需要 read；
- read 只用于本 session 写入后的 live 磁盘状态、更新前确认和用户审计；
- 写入立即持久化，但当前 session system prompt 快照不改变。

权限：ToolDefinition 至少要求 `tool.read`；executor 对 add/update/delete/batch 另行强制 `tool.mutate`。
因为一个公开工具同时读写，不能只靠静态 capability 给 read 用户开放 mutation。

### 10.2 `add_memory`

参数：

```text
memory_type: fact | procedure
record: FactRecord | ProcedureRecord
evidence_refs: [opaque ref]
```

`evidence_refs` 对两种 source 都必需：`user_statement` 绑定当前 user turn，`environment_observation` 绑定当前成功观测；
procedure 只接受后者。描述必须明确正反路由、source 约束、procedure 成功证据和 `infer=False` 原样存储。Agent 不传 metadata、scope、
dedupe key、memory_id 或 confidence。

### 10.3 `search_memories`

参数：

```text
query: non-empty string
memory_type: fact | procedure | omitted
limit: 1..20, default 5
subject: optional exact hint
predicate: optional exact hint
entry_url: optional exact hint
name: optional exact procedure hint
```

描述必须要求：当问题依赖外部当前事实或可复用操作流程而当前会话没有答案时主动调用；不搜索
`SOUL/USER/MEMORY`；多部分问题可用不同 query 多次检索；后续 get/update/delete 只能使用真实返回 ID。

### 10.4 `get_memory`

参数：`memory_id`。

描述必须要求：仅使用真实 ID 获取完整结构、执行 procedure、检查 fact 或修改/删除前确认；不承担语义搜索，
不得猜 ID。

### 10.5 `update_memory`

参数：

```text
memory_id
record: 完整的新 FactRecord | ProcedureRecord
evidence_refs
```

描述必须要求：通常先 search/get；fact 变化或用户纠正时原地更新；procedure 只有新路径完整成功后更新；
不得用未经确认信息覆盖，不允许 partial patch 造成 search text/record_json/扁平索引分叉。两种 source 都必须提交
与 §9 相符的 evidence refs，并由 executor 校验 provenance sequence。

### 10.6 `delete_memory`

参数：`memory_id`。

描述必须要求：只在用户明确要求、确认错误/重复或永久失效时删除；第一版无自动遗忘；先 search/get 确认，
不得猜 ID、不得暴露 delete-all。

### 10.7 输出和错误

成功输出使用稳定 JSON，至少包含：

```text
success
operation
memory_id（适用时）
memory_type（适用时）
record（read/get/search 适用时）
entry_count/usage（file memory 适用时）
verified_terminal_state
```

稳定错误码至少包括：

```text
memory_invalid_input
memory_permission_denied
memory_content_blocked
memory_outbound_blocked
memory_match_not_found
memory_match_ambiguous
memory_capacity_exceeded
memory_external_drift
memory_read_failed
memory_not_found
memory_conflict
memory_evidence_missing
memory_evidence_invalid
memory_stale_observation
memory_backend_unavailable
memory_backend_rejected
memory_outcome_unknown
memory_record_corrupt
```

写工具不得把“SDK 方法返回未抛异常”等同于终态成功。

## 11. 配置与依赖

### 11.1 依赖

在项目 venv/lock 中增加并锁定：

```text
mem0ai==2.0.13
mem0ai[nlp] 对应的 spaCy
en_core_web_sm==3.8.0（锁定 wheel URL 与 hash）
fastembed（实施时锁定与目标 wheel 兼容的精确版本）
```

`mem0ai` 自带 `qdrant-client>=1.12.0`，但基础依赖不包含 Qdrant BM25 所需 `fastembed`；不得安装体积和依赖面更大的
整个 `mem0ai[extras]`。使用 `uv add`/`uv lock` 锁定最小直接依赖，禁止裸 `pip install -U`。安装后核对
`openai/httpx/protobuf/pydantic` 没有被意外降级，并运行既有 provider/MCP/Gateway 相关回归。

安装阶段预取并校验准确 `Qdrant/bm25` artifact，记录 checksum/cache path，运行时进入 offline/no-download 模式；
冷缓存断网启动必须明确 fail fast，不能静默退成纯语义。公开 `Memory.search()` 使用的 spaCy 与
`en_core_web_sm` 必须作为锁定依赖随环境安装，禁止首次搜索运行时下载。fastembed、artifact 加载及 spaCy
行为必须在目标 venv 真验。

计划使用 PyPI wheel；本地 mem0 checkout 只作为已锁定源码参考，不以绝对路径成为运行依赖。2026-07-27
真环境核对发现 PyPI 2.0.13 wheel 与同版本 tag 仅在 Chroma、OpenSearch、PGVector 三个未选 backend 文件存在
差异，Qdrant 路径和本计划依赖的 mem0 core 文件一致。Owner 锁定以发布 wheel 的实际部署行为为准，不要求未使用
backend 全字节一致；实施必须增加只构造 Qdrant、无 backend selector 的门，且不得从本地 checkout 混入文件。

### 11.2 HomeMaster 配置 schema

在 `HomeMasterConfig` 增加一个明确、无 backend mode 的 `memory` 配置：

```yaml
memory:
  enabled: true
  root: ~/.homemaster/memories
  soul_file: SOUL.md
  user_file: USER.md
  memory_file: MEMORY.md
  user_char_limit: 1375
  memory_char_limit: 2200
  embedding_provider_name: MemoryEmbedding
  mem0:
    qdrant_path: ~/.homemaster/memory/qdrant
    collection_name: homemaster_memory_qwen3_4096_v1
    history_db_path: ~/.homemaster/memory/history.sqlite3
    embedding_dimensions: 4096
    search_limit: 5
    search_threshold: 0.1
```

约束：

- 不重复保存 embedding API key/base URL/model；按 `embedding_provider_name` 通过
  `HomeMasterConfig.get_provider(kind="embedding")` 获取；
- 真实 key 继续只在 gitignored `config/homemaster.yaml` 或既有 provider env override；
- `config/homemaster.example.yaml` 只含占位值；
- qdrant/history/file roots 展开后必须是明确绝对路径，并创建为私有目录；
- collection name 含模型/维度版本，防止用 4096 维模型误打开旧维度 collection；
- `enabled=false` 只用于测试/显式部署禁用，不产生第二套 memory backend；禁用时不注册六个工具也不注入文件；
- search threshold 必须经过真实 fixture 标定；配置存在不代表默认值已验收。

### 11.3 mem0 配置转换

`Mem0MemoryStore` 从上述 HomeMaster config 唯一派生：

```text
vector_store.provider = qdrant
vector_store.config.path = qdrant_path
vector_store.config.collection_name = collection_name
vector_store.config.embedding_model_dims = 4096
vector_store.config.on_disk = true

embedder.provider = openai
embedder.config.model = MemoryEmbedding.model
embedder.config.api_key = MemoryEmbedding.api_keys[0]
embedder.config.openai_base_url = MemoryEmbedding.base_url
embedder.config.embedding_dims = 4096

llm.provider = openai
llm.config.model = homemaster-infer-disabled
llm.config.api_key = non-secret-static-sentinel
llm.config.openai_base_url = http://127.0.0.1:9/v1

history_db_path = configured path
reranker = none
```

mem0 仍会在构造时创建 LLM client，即使 HomeMaster 永远使用 `infer=False`。第一版显式配置确定性不可外联的 sink：
本机 discard port、非密钥 sentinel 和不可用 model；绝不复用真实 embedding/provider 凭证。HomeMaster store 边界
只暴露硬编码 `infer=False` 的内部方法，且 outbound guard 只允许准确 embedding origin/path，拒绝
`/chat/completions` 和其他 host。集成测试断言 add/search/update/delete 全流程无 LLM 请求；任何 infer=True 路径在
发包前 fail closed。目标 wheel 能否仅构造该 sink 而不请求网络仍为 `UNVERIFIED`；若构造失败，停止并回到设计，
不得 patch 未公开 mem0 internal factory。

在首次 import mem0 以前设置 `MEM0_TELEMETRY=False`。这项行为必须由 HomeMaster 启动边界确定性完成，
不能只依赖开发者 shell；同时记录配置诊断但不输出密钥。

## 12. 生命周期、并发和可观测性

- `FileMemoryStore`、`FrozenMemoryContextService`、`Mem0MemoryStore`、`MemoryEvidenceLedger` 均由 application
  composition 创建并注入 `application_services`；工具通过 `context.services` 获取，禁止从全局 import 单例；
- application 必须先初始化文件服务；mem0 初始化失败时，`Mem0MemoryStore` 进入带 sanitized cause 的显式
  `unavailable` 状态（不是另一种 backend/provider），五个工具仍注册但统一返回 `memory_backend_unavailable`，文件上下文
  和 `memory` 工具继续可用；doctor/status 必须显示故障，不得伪装为空 store；
- mem0 SDK 是同步 API，所有调用用 `asyncio.to_thread` 或独立 bounded executor，禁止阻塞 Runtime event loop；
- 第一版用 application-owned async lock 串行化 mem0 mutation，避免 embedded Qdrant/SQLite 并发写；
- read/search 是否可并发必须以 qdrant-client + mem0 真实 stress 验证为准，验证前同样走统一锁；
- tool cancellation/timeout 只能 fence 返回，不能假称撤销已经进入同步线程的 Qdrant 写；超时后重新读终态，
  无法确定时返回 outcome_unknown，禁止自动重试 mutation；
- application close 必须等待已启动调用到 deadline，关闭 mem0/Qdrant/SQLite 的真实资源；准确 close API 在
  installed wheel 真环境核对前标 `UNVERIFIED`；没有 public close 时必须拥有并关闭底层 client，而不是只删引用；
- 文件、mem0 调用均写结构化 JSONL：operation、memory_type、memory_id hash、elapsed_ms、return status、
  terminal verification、error code、session/run/tool_call；不记录 API key、完整 USER/SOUL 文本或 procedure 输入值；
- mem0 SDK 自带 telemetry 必须关闭；验证用本地拦截器断言没有 PostHog DNS/HTTP 连接；
- 写操作 trace 与外部终态验证分开记录，不能用自身 success 日志验证自身。

## 13. 安全边界

- `SOUL/USER/MEMORY` 进入 system prompt，写入内容必须做严格 prompt-injection/credential/exfiltration 扫描；
- threat patterns 作为共享数据文件维护，文件 store 加载冻结快照时再次扫描，不能只信写入时扫描；
- 磁盘中被扫描命中的 entry 不静默删除，冻结快照中替换为 blocked marker，live read 返回诊断供用户删除；
- Agent 不可写 SOUL；
- `memory` action 级权限必须区分 read/mutate；
- mem0 mutation 工具要求 `tool.mutate`，search/get 要求 `tool.read`；
- procedure URL 不保存 credential；procedure inputs 不保存一次执行的 secret 值；
- fact/procedure 的版本化 search-text 模板只包含召回必需字段：fact 的 subject/predicate/value 摘要，procedure 的
  name、无 query/fragment 的 origin+path、step action/semantic target/success 摘要；禁止 query、userinfo、真实 input
  值、token、cookie、内部 evidence 和任意原始工具 payload；
- search text 和 search query 都会发往第三方 SiliconFlow embedding 服务。写入/搜索前执行 outbound policy 与
  credential/内部地址扫描；命中禁止字段时拒绝外发并返回稳定错误，不能先发后报；用户指南必须明确这不是全本地方案；
- outbound allowlist 只允许配置中准确 SiliconFlow embedding origin + `/v1/embeddings`，并核对请求体不含禁止字段；
- Qdrant/history/memory 文件均为本机私有路径，不进入 Git；
- tool schema 不接受任意 metadata/filter/user_id/path；
- 任何 mem0 record_json parse/schema failure fail closed；
- 检索到的记忆是执行提示，不是设备/网页操作授权；执行仍走原工具权限和实时终态门。

## 14. 与现有对象记忆的关系

当前 `src/homemaster/memory/index.py`、`retrieval.py`、`runtime_store.py` 和 domain
`memory_retriever/memory_writer` 主要服务 ALFWorld/benchmark 对象定位。实施前做调用图和 profile 审计：

1. 默认 Home profile 移除语义重叠的旧公开长期记忆工具；
2. benchmark 若仍依赖旧 deterministic fixture，可在 benchmark 专属 profile 内保留，不能进入默认 Home Agent；
3. 新 `search_memories` 不复用旧 JSON fixture 第一项作为当前事实；
4. 不在同一 CL 顺手删除仍被 benchmark 使用的底层模块；先完成 profile 隔离和回归，再由后续独立迁移删除；
5. 工具面快照、README 能力清单和测试必须准确区分“默认 Home mem0”与“benchmark fixture memory”。

## 15. 实施工作包

### WP1：依赖与配置

修改：

- `pyproject.toml`
- `uv.lock`
- `src/homemaster/config/config.py`
- `config/homemaster.example.yaml`
- gitignored `config/homemaster.yaml` 只补正式 memory 段，不复制 key

测试：

- 配置默认值、路径展开、维度/上限/threshold 校验；
- embedding provider 必须存在且 kind=embedding；
- example 无真实 secret；真实配置 mode 0600 且 gitignored；
- isolated wheel 安装后 import `mem0/qdrant`；
- fastembed 精确版本、BM25 artifact checksum/cache/offline load 和冷缓存 fail-fast；
- spaCy/full 与 lemma pipeline 均从已安装模型加载，无运行时模型/artifact 下载；
- 关键共享依赖版本审计。

### WP2：文件 store 与冻结上下文

新增候选文件：

- `src/homemaster/memory/file_store.py`
- `src/homemaster/memory/context_service.py`
- `src/homemaster/prompts/soul.md`
- `src/homemaster/memory/threat_patterns.json`

修改：

- `src/homemaster/agent/context.py`
- `src/homemaster/application/factory.py`
- `src/homemaster/cli/composition.py`
- package data 配置

测试：

- 文件创建、mode、delimiter、dedupe、unique substring、CRLF normalization、NUL/control/delimiter injection 拒绝；
- add/update/delete/read/batch、最终容量、全-or-nothing；
- unreadable、drift、并发 session、原子替换失败；
- threat write/load 双重扫描；
- session A 冻结后写入，A prompt 不变，session B 能看到新值；
- system prompt 顺序和空块；
- context token 估算包含快照；
- application restart 后读取最新值。

### WP3：mem0 store

新增候选文件：

- `src/homemaster/memory/models.py`
- `src/homemaster/memory/mem0_store.py`
- `src/homemaster/memory/serialization.py`
- `src/homemaster/memory/evidence.py`

职责：

- schema validation；
- search text/canonical JSON/扁平索引/dedupe key；
- Home config -> mem0 config；
- sync SDK 异步隔离；
- exact + hybrid search；
- add/update/delete 后终态 reread；
- stable errors；
- outbound policy、telemetry 禁用和 lifecycle close。

测试：

- fact/procedure schema 正反例；
- canonical serialization 同输入同输出；
- dedupe key 同输入确定、不同 identity 不冲突；
- fixed internal scope 不泄漏到工具参数；
- fake SDK 只做组件单测，不作为发布证据；
- 本地真实 Qdrant 临时目录做 CRUD、进程重开持久化、全部扁平字段精确过滤、部分 hint、中文语义搜索；
- update 保持 ID、更新 text+metadata、旧值不可检索为当前；
- delete 在线 raw point 不存在，关闭后由独立进程复核；
- semantic 与 BM25 分支分别真实贡献候选，缺 artifact 不静默降级；
- no infer、no LLM request、no PostHog request、no runtime model download。

### WP4：六个 canonical tools

新增候选文件：

- `src/homemaster/tools/memory_tools.py`

修改：

- `src/homemaster/adapters/profiles.py`
- `src/homemaster/cli/composition.py`
- 必要的 permission/action-level helper

测试：

- 六个工具 schema/description snapshot；
- descriptions 包含什么时候用、什么时候不用、调用顺序和禁止行为；
- 所有 executor 从 `context.services` 获取 store；
- static/dynamic capability denial；
- Home profile 正好包含六个新记忆工具；
- benchmark profile 不受错误迁移影响；
- 所有公开实现覆盖接口 audit；
- 通过真实 `ApplicationRuntime` dispatch，不直接调用 executor 充当接线验证。

### WP5：真实召回和 Agent 行为

使用真实 SiliconFlow Qwen embedding + 临时真实 Qdrant，逐实例验证：

1. 写入“苹果在冰箱第二层”，搜索“苹果在哪里”返回准确 fact；
2. 更新为“餐桌上”，同一 ID 返回新值，旧位置不作为当前命中；
3. 写入“查询当前告警” procedure，搜索“怎么看现在的告警”返回准确 procedure 和有序 steps；
4. 相同 procedure 幂等 add 不产生第二条；
5. 相似名称、不同 URL 的 procedure 不错误合并；
6. 无 evidence 的 environment fact/procedure 写入被拒，Qdrant 无新增；
7. 删除后 get/search/在线 raw point 均确认不存在，关闭后由独立进程 reopen 复核；
8. 五个 mem0 工具各自核对 API/SDK 成功返回和外部终态；在线用同一 Qdrant client 的 raw point API 绕过 mem0
   formatter 复核，关闭 store 后再由独立进程 reopen 做持久化复核；
9. 较早 evidence 延迟提交不能覆盖较新值；user_statement 无当前 user-turn ref 时零写入；
10. outbound capture 只看到 embeddings endpoint，请求体逐字段确认无 query string、secret、真实 procedure input 或
    evidence payload。

再用 deterministic provider 通过完整 `ApplicationRuntime` 让模型产生六种工具调用；provider schema、工具结果和
下一次模型请求必须逐步吻合。真实 LLM 是否稳定选择工具另做 live behavior gate，不用 prompt fixture 自证描述有效。

### WP6：迁移、文档和发布

更新：

- `README.md`
- `docs/architecture/application-runtime.md`
- 新增 `docs/memory-user-guide.md`
- `docs/skills-and-config-user-guide.md`
- `CHANGELOG.md`
- `progress.md`
- `plan/V2.1/homemaster-memory-system-discussion.md` 顶部标记为被正式计划取代

内容必须包含：

- 五类记忆路由表；
- 六个工具真实示例；
- frozen snapshot 行为；
- 字符上限和 batch 整理；
- fact/procedure schema；
- Qdrant 数据路径、备份和恢复；
- embedding 配置引用而非复制 secret；
- 会发送到第三方 embedding 服务的字段、禁止字段与数据流图；
- 无自动遗忘、无多用户；
- mem0/Qdrant 故障诊断；
- 明确不经过 MCP。

构建 wheel 并在源码 checkout 外安装，确认 package data 中的 SOUL template/threat patterns 完整，默认 Home profile
能够创建 application-owned stores 和六个工具。

## 16. 测试与外部终态验收矩阵

| 能力 | 内部测试 | 外部终态黑盒门 |
|---|---|---|
| 文件 add/update/delete | store 单测 | 独立进程重新打开文件，按 entry 解析并核对 mode/内容 |
| 文件 batch | all-or-nothing 单测 | 注入中途失败，最终文件 hash 保持原值 |
| 冻结快照 | ContextAssembler 单测 | 两个真实 session 顺序运行，A 不变、B 看到新值 |
| mem0 add | fake SDK + schema | 在线同一 client raw point 按 ID/metadata 读取；关闭后独立进程复核 |
| mem0 search | 排序/merge 单测 | 真实 Qwen 4096 embedding + BM25 分支分别贡献、中文 query per-case 命中 |
| mem0 update | ID/serialization 单测 | 同一 ID 新值存在、旧值不再是当前，updated_at 改变 |
| mem0 delete | 错误映射单测 | SDK get/search + 在线 raw point 不存在；关闭后独立进程复核 |
| procedure evidence | ledger 单测 | 完整 runtime 先产生成功动作证据再写；伪造/跨 run ref 零写入 |
| 工具注册 | registry snapshot | 顶层 Home application tool list 精确包含六个名称 |
| embedding | response parser | live HTTP 200、准确模型、4096 维、finite vector |
| 持久化 | restart 单测 | 关闭并新进程构造 store 后仍能 get/search |
| 隐私 | scanner/telemetry 单测 | 无 PostHog socket/request；Git 无真实 key/记忆数据 |
| 数据出境 | outbound policy 单测 | 只出现预期 embedding 请求；request body 无凭证、query、input value、内部 evidence |
| 生命周期 | cancel/close 单测 | 进程退出后 Qdrant lock/线程/socket 不残留，目录可被新进程打开 |
| 后端故障 | unavailable state 单测 | 锁冲突/坏路径启动后文件记忆可读，五工具稳定 unavailable，doctor 可见 |

每个多实例门逐条断言，不允许用 any/best/global min-max 掩盖某个 case 失败。

## 17. 真实依赖和 UNVERIFIED 清单

已在当前真实环境验证：

- SiliconFlow endpoint 接受 `Qwen/Qwen3-Embedding-8B`；
- 普通和 mem0 形状请求均 HTTP 200；
- `dimensions=4096` 返回准确 4096 维向量；
- 当前真实 HomeMaster config mode 0600 且 gitignored。

实施前/实施中仍需真环境核对：

- PyPI `mem0ai==2.0.13` wheel 与锁定源码提交等价性；
- installed mem0 + embedded Qdrant 的真实 CRUD、hybrid keyword behavior 和 metadata filter；
- Qdrant `on_disk/path` 的实际文件、锁和跨进程 reopen；
- mem0/Qdrant public close API 及退出后线程/socket/lock 终态；
- mem0 update(text+metadata) 对真实 Qdrant 的原子可见性；
- search threshold 对中文 fact/procedure fixture 的 per-case 召回率；
- mem0 telemetry 在 HomeMaster 启动边界关闭后确实零外联；
- fastembed 精确版本、`Qdrant/bm25` artifact checksum/offline load、BM25 实际贡献和 spaCy 已安装模型零下载；
- mem0 sink LLM 构造不外联，outbound guard 只放行准确 embeddings endpoint；
- procedure evidence 与尚待实施的通用浏览器工具 receipt 格式；未完成浏览器计划以前相关外部符号保持
  `UNVERIFIED`，不得由记忆计划替它背书。

任何 linchpin 核对失败先回到设计，不增加 local/cloud/MCP fallback mode 掩盖失败。

## 18. 失败语义和恢复

- 文件写前失败：外部终态不变，返回 confirmed failure；
- 文件 atomic replace 后验证失败：返回 outcome_unknown，保留 backup/hash 供诊断；
- embedding 网络失败：search/add/update 明确失败，不降级成不带向量的假成功；
- Qdrant add/update/delete SDK 异常：在线通过同一底层 client raw point API 复核；能确定终态则按实际终态返回，
  否则 outcome_unknown；关闭后的独立进程 reopen 只用于场景/持久化验收，不在 live mutation 时争抢同一路径锁；
- outcome_unknown mutation 不自动重试；
- record_json 损坏：隔离该条结果并报告，不用自然语言 text 猜结构；
- collection 维度不匹配：application start fail fast，不自动 reset/delete collection；
- memory 文件超限：事务不写，返回整理信息；
- Qdrant 不可用时 application 保持显式 unavailable store 状态，不阻止 `SOUL/USER/MEMORY` 读取，五个 mem0 工具
  明确 backend unavailable，doctor/status 必须可见；
- `SOUL/USER/MEMORY` 读取失败不能静默启动成空人格/空画像；startup 返回明确错误或 blocked marker，具体策略在
  RED 测试中锁定，禁止吞错。

## 19. 完成定义（DoD）

只有以下全部成立才能宣布完成：

1. 正式计划评审完成，发现逐条处置；
2. 六个工具协议、描述和权限与本文一致；
3. SOUL/USER/MEMORY session 冻结注入顺序和 live read 行为通过；
4. 文件容量、batch、锁、drift、atomic write 和 mode 通过；
5. mem0 真实 Qdrant fact/procedure CRUD 与 semantic + BM25 混合召回分别通过；
6. Qwen live embedding 返回码、模型、4096 维和 finite vector 通过；
7. 每个 mutation 同时核对 SDK 返回状态、在线 raw point 终态和 close 后独立进程持久化终态；
8. 两种 source 都绑定真实 evidence，procedure 无完整证据零写入，stale observation 不覆盖新值；
9. telemetry、LLM 和未授权 endpoint 零外联，embedding request body 无禁止字段；
10. application close 后无残留资源，重启仍能读取；mem0 故障时文件记忆仍可用且 unavailable 状态可观测；
11. default Home 和 benchmark profile 工具面迁移准确；
12. 配置、README、架构、用户指南、CHANGELOG、progress 同源更新；
13. wheel 外安装与 package data 门通过；
14. 聚焦、完整非 live 回归、Ruff、format、compileall、uv lock、diff check 通过；
15. 计划要求的 live API/Qdrant/ApplicationRuntime 黑盒门逐实例通过；
16. 全部实现和文档完成后唯一一次最终代码 reviewer 评审完成，发现逐条处理并做针对性验证；
17. commit 前 CHANGELOG 条目与 commit message 同源。

## 20. 实施顺序

```text
计划唯一评审
-> 处理发现并锁定计划
-> RED：配置/依赖/工具面/文件 store
-> WP1 依赖配置
-> WP2 文件 store + frozen context
-> RED：mem0 schema/CRUD/telemetry/lifecycle
-> WP3 Mem0MemoryStore
-> RED：六工具 ApplicationRuntime 接线和权限
-> WP4 canonical tools + profile 迁移
-> WP5 真实 Qwen/Qdrant/Agent 外部终态门
-> WP6 文档/CHANGELOG/wheel/完整回归
-> 唯一最终代码评审
-> 逐条整改 + 针对性验证
-> commit
```

不得在 WP3 尚未证明真实 CRUD 时先让工具返回模拟成功；不得在 WP4 工具 schema 未锁定时依靠 prompt 临时路由；
不得用单测 mock 代替 Qdrant、embedding、持久化和 ApplicationRuntime 外部终态。

## 21. 计划评审记录

2026-07-27 已完成本文唯一一次实施前只读 reviewer subagent 评审。Reviewer 未修改文件、未参与实现或验证、未派生
subagent。9 项发现处置如下，全部采纳，不追加第二次计划评审：

1. **精确检索字段缺失：采纳。** §7.1 增加 fact/procedure 扁平索引 metadata 和 normalized 规则；§8.2/§10.3
   补完整 identity、部分 hint 与 `name`；WP3 增加真实 Qdrant 精确过滤门。
2. **BM25 依赖不完整：采纳。** §11.1 显式最小锁定 fastembed、预取/校验 `Qdrant/bm25`、离线运行和 spaCy
   零下载；WP1/WP3/§16/§17 增加分别证明 semantic/BM25 贡献的终态门。目标版本和 artifact 仍标 `UNVERIFIED`。
3. **第三方 embedding 数据出境未定义：采纳。** §13 增加版本化 search-text 模板、outbound policy/allowlist 和
   用户披露；WP5/§16 增加真实 request-body 黑盒检查。
4. **mem0 LLM 非 fail-closed：采纳。** §11.3 不再复用真实凭证，改为显式不可外联 sink + store 边界硬编码
   `infer=False` + outbound guard；目标 wheel 构造行为保持 `UNVERIFIED`，失败则回到设计。
5. **embedded Qdrant 独立 client 锁冲突：采纳。** §8.6/WP3/WP5/§16/§18 区分在线同 client raw point 复核与
   close 后独立进程 reopen，不在 live mutation 中打开同路径第二 client。
6. **Qdrant 故障恢复与启动架构冲突：采纳。** §12 锁定文件服务先启动和显式 unavailable store 状态；§16/§18
   增加锁冲突/坏路径顶层黑盒门与 doctor 可见性。
7. **旧观测可能覆盖新事实：采纳。** §7.1/§8.4/§9 增加 ledger 持久化 `provenance_seq`，按证据产生顺序在锁内
   仲裁并拒绝 stale observation，不引入遗忘或用户可见历史版本。
8. **user_statement 仅靠模型自报：采纳。** §9.1/§10.2 要求 runtime 注册的当前 user-turn opaque ref，两种 source
   都必须通过 ledger 绑定；WP5 增加伪造/缺失 ref 零写入门。
9. **Markdown delimiter 注入未定义：采纳。** §6.2 固定换行 canonicalization、控制字符/delimiter 拒绝和 identical
   add 幂等；WP2 增加 round-trip 与注入测试。
