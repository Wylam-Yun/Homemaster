# HomeMaster V2.1 记忆用户指南

## 记忆分层

HomeMaster 使用三层本地文件记忆和一个结构化检索库：

- `SOUL.md`：稳定人格，由部署者维护，Agent 没有写工具。
- `USER.md`：稳定身份、偏好、沟通和使用习惯。
- `MEMORY.md`：近期事件、决定、结果和跨会话未完成事项。
- embedded MindMemOS：外部世界 fact 和可复用 procedure；原生 schema pipeline 使用本地 Qdrant 与 Neo4j。

SOUL、USER、MEMORY 在 session 第一次组装上下文时冻结。`memory` 写入会立即持久化，但当前 session 的
system prompt 不变；新 session 才看到新快照。结构化检索不授予设备或网页权限，执行时仍必须实时观察并走
对应工具的权限和终态验证。

## 配置

复制模板并保持真实配置私有：

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/homemaster.yaml
uv sync --all-extras
```

`providers.items` 中必须有 `kind: embedding`、`name: MemoryEmbedding` 的 provider。正式配置使用
`Qwen/Qwen3-Embedding-8B`、4096 维和准确 `/v1/embeddings` endpoint。`memory.enabled: false` 会同时移除
六个公开工具和固定记忆上下文；它不会选择另一个 backend。

持久数据只有一个外部根目录：

```yaml
memory:
  enabled: true
  data_root: ~/.homemaster/memory
  embedding_provider_name: MemoryEmbedding
  embedding_dimensions: 4096
  neo4j:
    mode: managed_local
    home: /absolute/path/to/neo4j-community
    java_home: /absolute/path/to/jdk-21
    uri: bolt://127.0.0.1:7687
    username: neo4j
    password: replace-with-private-password
    database: neo4j
```

目录固定派生为：

```text
~/.homemaster/memory/
  files/SOUL.md
  files/USER.md
  files/MEMORY.md
  mindmemos/qdrant/
  mindmemos/cache/jieba/
  mindmemos/neo4j/runtime/
  evidence.sqlite3
```

`neo4j.mode: managed_local` 由 HomeMaster 管理同一节点上的私有 Neo4j。第一个 HomeMaster 进程启动服务，每个
进程持有独立 lease；中间进程退出不会停止服务，最后一个进程退出才停止。异常退出遗留的 lease 会在下次启动
时按 PID 和进程启动标识清理。若首次启动恰好在取得 DBMS 身份前崩溃，后续 HomeMaster 会复用已就绪服务，
但为避免误停外部替换服务，不会自动取得它的 stop 所有权。安装目录、Java 21 和认证信息来自上述私有 YAML；
密码使用 `SecretStr`，不会进入 doctor 输出或对象 repr。`config/homemaster.yaml` 必须保持 mode 0600 且不提交 Git。

`neo4j.mode: external` 保留原行为：HomeMaster 只连接 `uri`，既不启动也不停止外部 Neo4j。托管模式只支持同一
节点共享；不要让不同节点用同一组 Neo4j 数据目录。Neo4j 的 `server.directories.data` 应指向该节点专属的
持久目录，尤其不能与另一节点上仍在运行的 Neo4j 共用 `store_lock`。图数据仍按 Neo4j 的备份流程单独备份。

全新 Neo4j 数据目录在第一次启动前，还要把 YAML 中的同一个密码写入 Neo4j（已启动过的数据库应使用正常的
密码修改流程，不能再用 initial-password）：

```bash
JAVA_HOME=/absolute/path/to/jdk-21 \
  /absolute/path/to/neo4j-community/bin/neo4j-admin \
  dbms set-initial-password 'replace-with-private-password'
```

## 换服务器与旧目录迁移

HomeMaster wheel 包含 vendored MindMemOS runtime。换服务器时先关闭所有 HomeMaster/Gateway 进程，再备份
`memory.data_root` 中的文件、Evidence 和 Qdrant 数据，并按 Neo4j 的备份流程单独备份图数据。代码升级和数据
备份互不包含对方。

旧版本若仍有 `~/.homemaster/memories`，先保留旧目录并运行：

```bash
uv run homemaster doctor --json
uv run homemaster memory migrate --config config/homemaster.yaml
```

迁移命令只对旧 SOUL/USER/MEMORY 文件和现有 Evidence 分组件校验、暂存和原子发布，返回 JSON receipt；旧源不会自动删除。
中断后再次运行会按 `migration-journal.json` 恢复同一个计划。目标已有不同数据、SQLite 损坏或 Qdrant 被其他
进程占用时命令非零退出且不合并数据。旧 mem0 Qdrant/history 数据不会读取或迁移。`doctor` 始终只读：
需要迁移时返回 `WARN migration_required`，不会创建目标、journal 或数据库，也不会打开 MindMemOS。应用、one-shot CLI 和 Gateway 启动会调用同一个
coordinator 自动完成或恢复迁移。

旧 `memory.root` 仅作为文件记忆的一次迁移输入兼容；不要与新 `memory.data_root` 同时配置。
`memory.mem0` 已删除且配置会被拒绝。

## 六个工具

### `memory`

原子修改 USER/MEMORY。`target=user|memory`；单操作使用 `action=add|update|delete`，`update/delete` 用唯一
`match`；多操作使用 `operations` batch。USER/MEMORY 的冻结快照已经进入当前 session 上下文，因此模型工具
不提供 read action。写入会在底层独立读回磁盘终态，并要求 `tool.read` 和 `tool.mutate`。

```json
{"target":"user","action":"add","content":"用户偏好简洁的中文回答"}
```

### `add_memory` / `update_memory`

保存完整 `FactRecord` 或 `ProcedureRecord`。模型不能提交 tenant/session/run、metadata、dedupe key、时间戳或
provenance。两种 source 都必须使用 Runtime 发放的 opaque evidence ref：`user_statement` 只绑定当前用户
turn；`environment_observation` 只绑定当前 run/turn 已成功提交的工具结果。procedure 仅接受环境证据，并要求
按顺序覆盖每一步和最终成功观测。更新是完整替换，不支持 partial patch。
工具参数说明与执行校验来自同一组 Pydantic 模型；`predicate` 使用英文小写 snake_case，例如 `location`。
用户身份、偏好、习惯、健康建议和长期安排必须写入 `memory(target=user)`，不能作为结构化 fact。

```json
{
  "memory_type": "fact",
  "record": {
    "memory_type": "fact",
    "subject": {"type": "object", "name": "苹果"},
    "predicate": "location",
    "value": {"container": "冰箱", "position": "第二层"},
    "source": "environment_observation"
  },
  "evidence_refs": ["memory-evidence-<opaque>"]
}
```

### `search_memories` / `get_memory` / `delete_memory`

`search_memories` 调用 MindMemOS 原生 search pipeline，并在工具边界按 fact/procedure 字段过滤。默认 limit
为 5，最大 20；可提供 subject/predicate 或 entry_url/name hints。query 会发送给配置的
SiliconFlow embedding endpoint，因此不要搜索凭证、token、cookie、内部地址或 evidence ref；产品边界会在发包前
拒绝明显敏感内容。
候选的 `record_json` 缺字段、损坏或 schema version 不支持时，该条不会作为正常命中返回；响应的
`diagnostics` 只包含稳定错误码、脱敏 ID hash 和命中分支，不回显损坏 payload。准确 ID 的 `get_memory`
仍会 fail closed 返回 `memory_record_corrupt`。

`get_memory` 只接受 add/search 返回的准确 ID。`delete_memory` 只用于用户明确要求、已确认错误/重复或永久失效
的记录；没有 delete-all，也没有第一版自动遗忘。

这五个结构化工具按需执行，不会在每轮用户消息前自动搜索。模型调用工具后，完整 JSON（包括 memory ID、
records、value、match sources 和错误）会作为 tool result 进入下一次模型上下文；不是只返回一句 succeeded。
文件 `memory` 工具同样把 entries/usage 等完整结果放进模型可见的 tool result。

## 文件与隐私

data root 和文件记忆目录为 0700，文件、lock、backup、Qdrant/evidence DB 为本机私有且不进入 Git。USER/MEMORY
写入会拒绝 prompt injection、credential/exfiltration 模式、控制字符和内部分隔符；人工改坏文件时拒绝写入并
生成 mode-0600 drift backup。磁盘中命中威胁规则的内容不会被静默删除，prompt 中显示 blocked marker。

MindMemOS telemetry 和 Kafka 均关闭。schema add 会调用配置的 chat provider，搜索和写入会调用 embedding
provider；Qdrant 为本地存储，Neo4j 使用已配置连接。

## 常见错误

- `memory_evidence_missing/invalid`：ref 缺失、伪造、跨 tenant/session/run/turn，或 procedure 证据不完整。
- `memory_conflict`：同一 identity 已存在不同记录；先 search/get，再 update。
- `memory_stale_observation`：较早证据不能覆盖较新值。
- `memory_outbound_blocked`：待 embedding 文本包含禁止出站内容。
- `memory_backend_unavailable`：chat/embedding provider、Qdrant 或 Neo4j 初始化失败。托管模式会在进入 shell
  前终止启动并报告原因；external 模式维持原有降级行为。
- `memory_outcome_unknown`：mutation 已可能开始但终态无法确认；禁止自动重试，先 get/search/raw 诊断。
- `memory_record_corrupt`：记录结构损坏或版本不支持；search 会隔离并报告脱敏 diagnostic，get 会拒绝。
- `memory_external_drift`：人工编辑后的文件无法按规范无损往返；查看 drift backup 后修复。

诊断命令：

```bash
uv run homemaster doctor --json | jq '.checks[] | select(.name == "memory_backend")'
```

该检查只报告导入、配置和文件迁移是否 ready，并以 `probe=not_opened` 明示没有启动 backend；它会显示
`neo4j_mode`、URI 和托管安装路径，但不显示密码。实际 Qdrant、Neo4j、chat 和 embedding 可用性由
HomeMaster 启动边界验证。
