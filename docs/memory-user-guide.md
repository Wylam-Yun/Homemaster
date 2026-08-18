# HomeMaster V2.6 记忆用户指南

## 自动经验召回

Runtime 只在两个时机自动召回：新 Session 的第一条消息，以及 Compact 真正产生压缩结果后的
下一条用户消息。新 Session 直接用原始用户文本搜索；Compact 后的 Query 由 Compact Summary、
TaskState 和当前用户消息确定性组成。

自动搜索固定为 `top_k=3`、`search_pipeline=vanilla`、`rerank=false`、`filters=None`，
因此不会只搜索 `fact` 或 `procedure/experience`。结果只在当前 run 的首个 Provider 请求中
作为 `<memory-context>` 出现，不会写入 Session 历史。搜索结果为空、服务不可用或普通错误时，
本轮模型请求仍继续。自动召回不替代 Agent 主动的 `mindmemos_search`；后者仍可指定
`memory_type` 并产生真实工具结果。

## 记忆分层

HomeMaster 使用三层本地文件记忆和一个结构化检索库：

- `SOUL.md`：稳定人格，由部署者维护，Agent 没有写工具。
- `USER.md`：稳定身份、偏好、沟通和使用习惯。
- `MEMORY.md`：近期事件、决定、结果和跨会话未完成事项。
- embedded MindMemOS：外部世界 fact 和可复用 procedure；原生 schema pipeline 使用本地 Qdrant 与 Neo4j。

SOUL、USER、MEMORY 在 session 第一次组装上下文时冻结。`context_memory` 写入会立即持久化，但当前 session 的
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
  dreaming_memory_threshold: 8
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
  mindmemos/dreaming_state/
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

## 七个工具

### `context_memory`

原子修改 USER/MEMORY。`target=user|memory`；单操作使用 `action=add|update|delete`，`update/delete` 用唯一
`match`；多操作使用 `operations` batch。USER/MEMORY 的冻结快照已经进入当前 session 上下文，因此模型工具
不提供 read action。写入会在底层独立读回磁盘终态，并要求 `tool.read` 和 `tool.mutate`。

```json
{"target":"user","action":"add","content":"用户偏好简洁的中文回答"}
```

### `mindmemos_add` / `mindmemos_update`

保存完整 `FactRecord` 或 `ProcedureRecord`。模型不能提交 tenant/session/run、metadata、dedupe key、时间戳或
provenance，也不能提交 evidence ref。工具从当前 execution scope 内部选择 evidence：`user_statement` 只绑定
当前用户 turn；`environment_observation` 只绑定当前 run/turn 已成功提交的工具结果。procedure 仅接受环境
证据，并要求按顺序覆盖每一步和最终成功观测。更新是完整替换，不支持 partial patch。
工具参数说明与执行校验来自同一组 Pydantic 模型；`predicate` 使用英文小写 snake_case，例如 `location`。
用户身份、偏好、习惯、健康建议和长期安排必须写入 `context_memory(target=user)`，不能作为结构化 fact。

```json
{
  "memory_type": "fact",
  "record": {
    "memory_type": "fact",
    "subject": {"type": "object", "name": "苹果"},
    "predicate": "location",
    "value": {"container": "冰箱", "position": "第二层"},
    "source": "environment_observation"
  }
}
```

`mindmemos_add` 在上述校验完成后把不可变 record、provenance 和 request context 放入应用内 FIFO，并立即
返回领域状态 `accepted`、不透明 `job_id` 和 `verified_terminal_state=false`；此时没有 `memory_id`，紧接着搜索
也可能尚不可见。统一 provider/stream envelope 表示为 `status=success, domain_status=accepted`。单个
application 只有一个 worker，严格串行执行原有 Schema Add 和 Qdrant raw 精确回读。
任务失败写入 `memory.data_root/mindmemos/add_jobs.jsonl`，不会自动重试，也不会阻断队列后续任务。

正常 one-shot/Gateway/Application 退出会封住新入队并 drain 已 accepted 任务。交互 Shell 在 Session Finalizer
之前先 drain，所以顺序固定为结构化 Add 清空，再执行 Vanilla Add、implicit feedback 和可选 dreaming。
当前 run 被 Ctrl+C 取消不会取消已经 accepted 的任务；进程崩溃、断电或 `kill -9` 仍可能丢失内存队列。
这一实现不需要 Kafka、Docker、SQLite 队列、并发 worker 或 retry。

`mindmemos_update` 先读取准确 ID：存在且能校验 `record_json` 时要求完整 `record`，创建新 active 版本、归档
旧版本并写入 `DERIVED_FROM`，同时更新 metadata、memory/entity 向量和图关系，不重新运行 Schema Add；不存在
`record_json` 时要求完整 `content`，复用 MindMemOS 原生原地更新并保持同一 ID。`record_json` 存在但损坏时
直接报错，不能降级成 Vanilla。结构化更新不能改变 subject/predicate 或 procedure identity；这种变化应新增
新记忆并归档旧记忆。

```json
{"memory_id":"<raw-id>","content":"Aurora-A18 now uses uv."}
```

### `mindmemos_search` / `mindmemos_history` / `mindmemos_delete`

`mindmemos_search` 按语义搜索长期记忆，返回按相关性排序的外部事实、已验证流程和历史 Session 经验。它调用
MindMemOS 原生 search pipeline，并在工具边界按 fact/procedure 字段过滤。默认 limit
为 5，最大 20；可提供 subject/predicate 或 entry_url/name hints。query 会发送给配置的
SiliconFlow embedding endpoint，因此不要搜索凭证、token、cookie、内部地址或 evidence ref；产品边界会在发包前
拒绝明显敏感内容。
Session 自动沉淀的 Vanilla experience 在公开工具中作为 `procedure` 返回；检索这类可复用经验时使用
`memory_type=procedure`，不确定是事实还是经验时省略 `memory_type`。
候选的 `record_json` 缺字段、损坏或 schema version 不支持时，该条不会作为正常命中返回；响应的
`diagnostics` 只包含稳定错误码、脱敏 ID hash 和命中分支，不回显损坏 payload。搜索结果已经包含完整 record
或完整 Vanilla experience 正文，因此不再提供单独的模型可见 get 工具；底层准确 ID 读取仍由搜索、更新和
写后终态验证内部使用。

`mindmemos_history` 接受 `mindmemos_search` 或 update receipt 返回的准确 ID，沿 Neo4j `DERIVED_FROM` 查询整个
已链接分量，再逐个回读 Qdrant，按新到旧返回 active/archived 正文和结构化 record。普通 search 仍只返回
active 记忆。升级前已经归档但没有 lineage 的旧记录不会被猜测性拼进版本链。

`mindmemos_delete` 只接受 `mindmemos_search` 返回的准确 ID，并只用于用户明确要求、已确认错误/重复或永久失效
的记录；如果信息只是变化，优先使用 `mindmemos_update`。没有 delete-all，也没有第一版自动遗忘。

### `mindmemos_feedback`

当用户给出明确纠正但没有指定准确 raw ID 或具体 add/update/delete 动作时，模型使用这个工具。例如：

```json
{"feedback":"不是所有环境都统一用 uv：在线开发用 uv，离线交付继续用 Poetry"}
```

工具没有 `memory_id` 参数。它只使用本次成功 provider request 中实际可见的自动召回和
`mindmemos_search` raw records；已经被压缩掉的搜索结果、文本中手写的 ID、schema 聚合 ID、wrong-scope、
archived 或内容已变化的记录都不能作为 mutation 目标。反馈 pipeline 可规划 `add`、版本化 `update`、
`delete` 或 `noop`。每个 action 都返回 `status`、target/result IDs 和 `terminal_verified`；任一 action
失败时整个工具返回错误。已知准确 ID 和确定替换内容时，仍优先使用确定性的 `mindmemos_update`。

如果被纠正的是结构化记忆，feedback planner 必须给出完整的新 record。系统不会把一句新正文和旧
`record_json` 拼在一起：新正文固定由新 record 生成，并在成功前同时回读比较正文、完整 record、旧/新状态和
`DERIVED_FROM`。例如原来内部 `value="uv"`，上述纠正成功后内部 value 也必须同时表达“在线 uv、离线
Poetry”，不能只让显示文字变长。evidence ID 不属于工具参数，模型看不到也不需要填写；Runtime 只从当前
tenant/session/run/turn 自动选择匹配的用户陈述或环境观察。

这六个 MindMemOS 工具按需执行。模型调用工具后，完整 JSON（包括 Add 的 job ID、其他操作的 memory ID、
records、value、match sources 和错误）会作为 tool result 进入下一次模型上下文；不是只返回一句 succeeded。
`context_memory` 同样把 entries/usage 等完整结果放进模型可见的 tool result。

## 100 条召回基准

仓库提供 `scripts/memory_recall_benchmark.py`，通过公开的 `homemaster -p` 入口串行写入 100 条合成网页操作
事实，再分别测试强制检索、改写检索、近似干扰项和自然工具路由。测试流程被保存为普通
`source=user_statement` fact；它们不是经过浏览器验证的 procedure，不能当成真实网站操作说明。

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster

PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py \
  generate --run-id hm100-20260810

PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py \
  write --run-id hm100-20260810

# 中断后只继续尚未确认的序号
PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py \
  resume --run-id hm100-20260810

PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py \
  evaluate --run-id hm100-20260810

PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py \
  status --run-id hm100-20260810

# 无人值守：先完成 100 条写入，再执行恰好 100 次精确召回
PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py \
  overnight --run-id hm100-20260810 --recall-cases 100
```

每条写入都会启动一次独立 `homemaster -p --output-format stream-json`，解析真实 `mindmemos_add`
`accepted` receipt。one-shot 正常退出 drain 后，脚本按 `job_id` 核对 completed job，再独立打开真实 Qdrant
按 `memory_id` 回读并比较完整 record；两道门都通过才更新 checkpoint。按当前 schema pipeline 的实测速度，100 条可能
耗时 5–6 小时并产生约百万级 chat tokens。脚本严格串行，不会并发打开本地 Qdrant，也不会自动重试已经触达
backend 但终态未知的 mutation。

`overnight` 只有在 checkpoint 达到 100/100 后才开始召回；写入失败或终态未知时以非零状态停止，不会带着
不完整数据继续评分。默认的 100 个 case 是每条记录一次精确 fact 检索。

运行产物位于 `~/.homemaster/benchmarks/<run-id>/`，目录权限为 0700，trace/checkpoint/report 为 0600。
当前版本没有 cleanup/delete 子命令；测试记录会保留在当前配置的真实记忆库中。自然问题没有调用
`mindmemos_search` 时会计为 agent routing failure，而不是 MindMemOS retrieval miss。

## 文件与隐私

data root 和文件记忆目录为 0700，文件、lock、backup、Qdrant/evidence DB 为本机私有且不进入 Git。USER/MEMORY
写入会拒绝 prompt injection、credential/exfiltration 模式、控制字符和内部分隔符；人工改坏文件时拒绝写入并
生成 mode-0600 drift backup。磁盘中命中威胁规则的内容不会被静默删除，prompt 中显示 blocked marker。

MindMemOS telemetry 和 Kafka 均关闭。schema add 会调用配置的 chat provider，搜索和写入会调用 embedding
provider；Qdrant 为本地存储，Neo4j 使用已配置连接。

## 常见错误

- `memory_evidence_missing/invalid`：当前 execution scope 没有匹配证据，或 procedure 证据不完整。
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
# Session 结束后自动沉淀经验

交互运行 `homemaster` 时，以下操作会结束当前 Session 并自动调用 MindMemOS Vanilla Add：

- `/exit`；
- 输入 EOF；
- 在提示符处按 Ctrl+C；
- `/new`，旧 Session 沉淀完成后创建新 Session。

Run 执行期间按 Ctrl+C 只取消当前 Run，不结束 Session。HomeMaster 从当前 Application 的
`runtime_events.jsonl` 按 `session_id` 收集事件，构造仅驻留内存的 `TaskTraceEnvelope`，再精选用户输入、
模型思考、助手回复和工具结果作为带角色的 MindMemOS 输入。内部 ID、transport、usage 和重复终态不会
发送给模型，也不会另存 `task_trace.json`。

同目录的 `job.json` 分阶段记录 Vanilla Add、implicit feedback、dreaming 计数和 dreaming 结果。Add 成功
后会自动处理 operation-record 中同一用户尚未处理的纠正/不满/偏好变化；失败只重试未完成阶段，不会重复
已经确认的 Add。Vanilla Add 自主决定执行 `add/reinforcement/update/merge/skip`，因此一个 Session 可能
产生零条、一条或多条 Memory。

同一 project/user 自上次成功 dreaming 后，每累计 `dreaming_memory_threshold` 条已回读 active 的普通
Session 新增 raw memory（默认 8）触发一次批量去重、冲突处理和合并。批次先持久化为 pending；只有 pipeline
返回成功、每个 action 的 raw/lineage 终态和每个 add record 的 consolidation 状态都通过，才推进水位。
失败或进程退出会在下次 HomeMaster 启动或 Session finalization 重试，不会创建每日定时任务。

调试时使用：

```bash
homemaster --debug
```

该选项显示收集事件数、排除的 delta 数、渲染消息数、处理耗时以及每条实际 Memory 的完整内容；它与
显示完整模型思考和工具结果的 `--verbose` 不是同一功能。
