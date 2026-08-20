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

## Session 结束与自动沉淀

`ApplicationRuntime.run()` 只执行一轮交互，不代表 session 结束。具有明确结束点的入口统一通过
`application.session(session_id)` 声明 session 生命周期：Shell 的 `/new`、EOF、interrupt 和 `/exit`，one-shot
调用结束、普通 ALFWorld episode 结束、continuous ALFWorld taskset 整体结束，以及 LoCoMo source conversation
结束时都会关闭该 scope。关闭操作幂等，只把 Session Finalizer 放入 application-owned FIFO，马上返回；因此
Shell `/new` 不等待旧 session 的 Vanilla Add，可以直接开始下一段对话。

正常 application shutdown 会先 seal/drain FIFO，再关闭 embedded MindMemOS 和 Neo4j。只有 FIFO 已启动且
MindMemOS 确实 available 时才接收 finalization；memory runtime 未启动时 session close 是 no-op，不会把晚到的
memory traceback 混入主任务结果。进程强杀仍可能丢失尚未执行的内存任务。Gateway 的单条消息只是 turn，当前
没有 reset/expiry/end 事件，因此不会错误地逐消息 finalize。

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

持久数据只有一个服务器本地根目录，由一次性 setup 绑定到 checkout 的 `.runtime`：

```yaml
memory:
  enabled: true
  data_root: ../.runtime/memory
  embedding_provider_name: MemoryEmbedding
  embedding_dimensions: 4096
  dreaming_memory_threshold: 8
  neo4j:
    mode: managed_local
    home: ../.runtime/neo4j
    java_home: ../.runtime/java
    uri: bolt://127.0.0.1:7687
    username: neo4j
    password: replace-with-private-password
    database: neo4j
```

以上三个相对路径都相对 `homemaster.yaml` 所在目录解析，与启动 cwd 无关；绝对路径仍保持原值。真实 YAML
继续 gitignore 且 mode 0600，仓库只提交占位的 example。迁移机器时优先使用干净的 Java/Neo4j 发行包，
不要复制正在使用的整个 Neo4j 安装目录：其 `conf/neo4j.conf`、`data/`、`logs/` 或 `run/` 可能已经夹带
源机器绝对路径、PID、锁和数据库状态。启动前必须在目标机执行 `neo4j-admin server validate-config --verbose`
并检查退出码为 0。

目录固定派生为：

```text
<memory-home>/
  files/SOUL.md
  files/USER.md
  files/MEMORY.md
  mindmemos/qdrant/
  mindmemos/cache/jieba/
  mindmemos/dreaming_state/
  mindmemos/neo4j/runtime/
  evidence.sqlite3
```

推荐的迁移流程是：在每台服务器分别执行一次 `scripts/setup_memory_runtime.py setup`，将
`--memory-home` 指向该服务器已有的 memory 数据（新部署可以省略，setup 会创建空目录），并提供本机的
Neo4j、Java 和 Python 路径。之后使用 `scripts/homemaster ...`，不需要每次手动设置环境变量。
若 ALFWorld 的 Torch/THOR 依赖位于独立环境，同时传 `--alfworld-python` 与 `--alfworld-root`；后者必须包含
`configs/base_config.yaml` 和 `data/json_2.1.1`。普通 HomeMaster 命令继续使用 `--python` 绑定的主环境，只有
ALFWorld benchmark 会切换到专用环境并从 `.runtime/alfworld` 使用本机 config/dataset。

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
备份互不包含对方。Neo4j 数据只按其备份/恢复流程迁移；Java 和 Neo4j binary distribution 在目标机重新铺设，
不能把源机的 active installation directory 当成可移植发行包。

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

已有 `homemaster-memory-migration-v1` 状态会在首次 mutating migrate 或应用启动时原地升级为 `v2`。升级前会
按 v1 的原始两组件或四组件契约验证，并把旧 manifest/journal 保存为带原 migration ID 的审计副本；已有
memory 文件、Qdrant、history 和 Evidence 不会被删除或复制。`/home/...` 与其真实挂载路径等价时允许升级，
但指向不同真实目录、旧结构未知、已发布目标缺失或审计副本冲突时仍拒绝启动。

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

显式 Add 只接受非空 `content` 和 `memory_type=fact|procedure`。正文按模型提交的原字符串保存；模型不能提交
tenant/session/run、metadata、时间戳、provenance 或 evidence ref。工具从当前 execution scope 内部选择最新的
`user_statement` 或 `environment_observation` evidence。用户身份、偏好、习惯、健康建议和长期安排仍应写入
`context_memory(target=user)`。

```json
{
  "memory_type": "fact",
  "content": "苹果在冰箱第二层。"
}
```

`mindmemos_add` 在上述校验完成后同步写入不可变 content/type、provenance、本地 BM25、Qdrant Memory 以及
Neo4j Memory/Source/`EXTRACTED_FROM`。两个数据库逐项回读成功后返回领域状态 `stored`、真实 `memory_id` 和
`verified_terminal_state=true`；统一 provider/stream envelope 表示为
`status=success, domain_status=stored`。模型只提交 `content + memory_type`，不提交 embedding、Entity 或 pending
状态。随后应用内最多两个 worker 为同一 ID 补远程 dense embedding，并复用原生 Vanilla Entity 抽取、稳定
Entity ID、entity vector 和 `MENTIONS` 写入。增强失败不会把已经确认存储的 Add 伪装成失败并诱发重复添加。
`fact` 原生存为 `fact`，`procedure` 原生存为 `experience`。

显式 flat Add 不再进入 Finalizer 的串行 FIFO，因此耗时的旧 Session Finalizer 不会挡住新 Add。Finalizer 的
Vanilla extractor 与 Add config 都启用 Entity，然后按顺序运行 implicit feedback 和可选 dreaming，确保
Dreaming 能获得 `Memory-[:MENTIONS]->Entity` scope。`/new` 排入旧 Session Finalizer 后随即创建新 Session。
正常 one-shot/Gateway/Application 退出会封住新入队并 drain 增强与 Finalizer；当前 run 被 Ctrl+C 取消不会
取消已经入队的后台工作。进程崩溃、断电或 `kill -9` 仍可能丢失进程内任务。

`mindmemos_update` 先读取准确 ID：历史记忆存在且能校验 `record_json` 时要求完整 `record`，创建新 active 版本、归档
旧版本并写入 `DERIVED_FROM`，同时更新 metadata、memory/entity 向量和图关系，不重新运行 Schema Add；不存在
`record_json` 时要求完整 `content`，复用 MindMemOS 原生原地更新并保持同一 ID。`record_json` 存在但损坏时
直接报错，不能降级成 Vanilla。结构化更新不能改变 subject/predicate 或 procedure identity；这种变化应新增
新记忆并归档旧记忆。

```json
{"memory_id":"<raw-id>","content":"Aurora-A18 now uses uv."}
```

### `mindmemos_search` / `mindmemos_history` / `mindmemos_delete`

`mindmemos_search` 按语义搜索长期记忆，返回按相关性排序的原生 MindMemOS memory。它调用
MindMemOS 原生 search pipeline，并可按 `profile`、`fact`、`experience`、`episodic`、`tool_trace`、
`skill_candidate`、`file_knowledge` 过滤；省略 `memory_type` 时搜索全部类型。默认 limit
为 5，最大 20；可提供 subject/predicate 或 entry_url/name hints。query 会发送给配置的
SiliconFlow embedding endpoint，因此不要搜索凭证、token、cookie、内部地址或 evidence ref；产品边界会在发包前
拒绝明显敏感内容。
所有 active、正文非空、无损坏结构副本的原生记录都返回原文 `content` 和原生 `memory_type`。显式
procedure 与 Session 自动沉淀的 Vanilla experience 都以 `experience` 返回；旧结构化 procedure 仍在内部
`record.memory_type` 保留 `procedure`，供结构化 update 使用。检索工具轨迹时使用
`memory_type=tool_trace`。
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

这七个 MindMemOS 工具按需执行。模型调用工具后，完整 JSON（包括 Add 与其他 mutation 的 memory ID、
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
`stored + memory_id` receipt，再独立打开真实 Qdrant 按该 ID 回读并比较 exact JSON content、native fact type
和 direct-flat marker；两道门都通过才更新 checkpoint。dense/Entity 增强由应用后台完成，不再为初次存储
追加 Schema extraction chat；总耗时仍包含每次公开
CLI 的 agent routing。脚本严格串行，不会并发打开本地 Qdrant，也不会自动重试已经触达 backend 但终态未知的
mutation。

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
- `/new`，旧 Session 收尾入队后立即创建新 Session。

Run 执行期间按 Ctrl+C 只取消当前 Run，不结束 Session。HomeMaster 从当前 Application 的
`runtime_events.jsonl` 按 `session_id` 收集事件，构造仅驻留内存的 `TaskTraceEnvelope`，再精选用户输入、
模型思考、助手回复和工具结果作为带角色的 MindMemOS 输入。内部 ID、transport、usage 和重复终态不会
发送给模型，也不会另存 `task_trace.json`。

`/exit`、EOF 或提示符 Ctrl+C 触发终态 drain 后，HomeMaster 会等待 FIFO 中所有显式 flat Add 和 Session
finalization，再关闭 application；此时重复 Ctrl+C 被忽略，第一次会显示提示。由 `/new` 入队的后台
finalization 不拥有 SIGINT：如果它与一个普通 Run 同时推进，Ctrl+C 仍只取消该 Run，不取消记忆任务。
`kill -9`、断电和进程崩溃仍会强制终止。`zh_core_web_sm` 缺失日志是可选中文 NER 的降级警告，不是进程
被结束的原因。

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
