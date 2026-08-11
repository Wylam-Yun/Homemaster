结论：底层已经跑通，接下来确实是“套进 agent 框架、匹配调用逻辑、做完整测试”。但“匹配调用逻辑”是主要工作，不能直接把 Mem0MemoryStore 类名替换成 EmbeddedMindMemOS。
当前底层基础大约完成了 70%，真正替换 Homemaster 生产记忆链路大约完成了 30%。风险主要在业务语义、ID、数据外发和生命周期，不在 Qdrant 或 API 能不能调用。
一、建议的最终调用结构
不要让 agent/tool 直接调用 MindMemOS 类型，保持这一层边界：
Homemaster Agent
  ↓
memory tools / 自动记忆触发器
  ↓
Homemaster MemoryStore 接口
  ↓
MindMemOSMemoryStore / EmbeddedMindMemOS
  ↓
MindMemOS 原生 pipelines
  ↓
Qdrant Local + Neo4j + LLM/Embedding API
建议把调用逻辑分成两条：
Homemaster 场景	应该调用什么
add_memory：已经经过 evidence 校验的结构化 fact/procedure	确定性写入 MindMemOS DB，不要再让 LLM 猜一次
普通对话自动提取长期记忆	schema_add.add_sync()
search_memories	search_pipeline.search()，然后转成 Homemaster 返回格式
get_memory	default_get 或 raw memory reader
update_memory	default_update，必须使用 raw memory ID
delete_memory	default_delete，语义是 archive
用户明确说“记错了”	feedback_sync() explicit 模式
后台发现记忆冲突/重复	dreaming，修复后再启用
agent 使用了某个 skill	写入 skill binding + trajectory
轨迹达到阈值	SkillEvolver.evolve()

二、依次需要解决的阻断点
P0-1：确定替换边界
建议这次只替换：
Mem0MemoryStore → MindMemOSMemoryStore
暂时保留：
FileMemoryStore
FrozenMemoryContextService
MemoryEvidenceLedger
六个现有 memory tools 的输入输出契约
这些都是 Homemaster 自己的业务和安全机制，不属于 mem0。把 mem0 删掉不代表它们也应该删掉。
这是最稳妥的第一版：外部 agent 工具基本不变，只替换底层 structured memory backend。
P0-2：建立后端无关的 MemoryStore 接口
目前工具层直接依赖：
Mem0MemoryStore
Mem0StoreError
StoredMemory
例如服务名也是：
mem0_memory_store
需要改成后端无关名称，例如：
class StructuredMemoryStore(Protocol):
    async def add(...)
    async def search(...)
    async def get(...)
    async def update(...)
    async def delete(...)
配套改名：
Mem0StoreError       → MemoryStoreError
mem0_memory_store    → structured_memory_store
StoredMemory         → 保留为 Homemaster 领域对象
然后实现：
MindMemOSMemoryStore implements StructuredMemoryStore
这样 memory tools 不需要知道底层是 mem0 还是 MindMemOS。
这是正式接入的第一个代码阻断点。
P0-3：处理现有结构化记忆和 MindMemOS 类型差异
Homemaster 当前只有：
fact
procedure
MindMemOS 是：
profile
fact
experience
episodic
tool_trace
skill_candidate
file_knowledge
推荐第一版映射：
Homemaster	MindMemOS	保留字段
fact	fact	subject、predicate、value、source、provenance_seq
procedure	experience	完整 ProcedureRecord 放 metadata/结构化内容里
USER/MEMORY 文件	暂不迁移	继续使用 FileMemoryStore
可进化 skill	后续单独进入 SkillVersionStore	不要直接把所有 procedure 都变成 skill

为什么 procedure 暂时映射成 experience：它是经过环境验证的操作经验，但不一定已经是可以进化和版本管理的正式 skill。
同时必须保留 Homemaster 现有语义：
fact 的确定性 dedupe key
subject/predicate 精确过滤
procedure 的 name/entry_url 精确过滤
provenance_seq 新旧判断
evidence 校验
完整结构化 record_json
写后回读验证
这些不能依赖 schema_add 的 LLM 自动完成。
P0-4：分开“确定性写入”和“LLM 记忆抽取”
这是最重要的设计调整。
当前 add_memory 已经由 agent 提交完整的 FactRecord/ProcedureRecord，并且 evidence ledger 已经验证过。此时再调用：
schema_add.add_sync(TextMessage(...))
会产生几个问题：
LLM 可能改变内容。
不保证只生成一条 memory。
不保证 identity/dedupe key 不变。
不保证 procedure 结构完整。
增加一次不必要的 LLM API 调用。
影响现有 evidence 安全语义。
所以推荐：
结构化 add_memory
    → Homemaster 验证
    → 转换成 MindMemOS MemoryWrite/EntityWrite
    → MemoryDbWriter 确定性写入
而：
普通对话消息
    → DialogueMessage/TextMessage
    → schema_add.add_sync()
两者最终写入同一 MindMemOS 数据库，但输入路径不同。
这不是重写 MindMemOS，而是正确使用它的底层 writer 和上层 pipeline。
P0-5：解决 raw memory ID 和聚合视图 ID
这是已经实际遇到的问题。
schema search 可能返回：
聚合实体/属性视图 ID
而以下功能需要：
raw memory ID
update
delete
explicit feedback
dreaming
operation records
get raw memory
必须在结果模型里显式分开，例如：
class HomemasterMemoryHit:
    display_id: str
    raw_memory_ids: tuple[str, ...]
    content: str
    memory_type: str
对 agent 暴露的可修改 ID，必须是 raw ID；聚合 ID只能用于展示和搜索解释。
否则 feedback、dreaming 和 CRUD 会继续出现“搜索到了，但修改找不到”的问题。
P0-6：补齐配置和生命周期
当前 EmbeddedMindMemOS 还没有进入正式 application composition。
需要在 composition.py 中：
创建 EmbeddedMindMemOS/MindMemOSMemoryStore。
绑定到 application resource scope。
application starter 中 await start()。
application 关闭时自动 await close()。
注册为 structured_memory_store service。
删除 Mem0MemoryStore.start() 和相关 service 注入。
现有 resource scope 支持 async close()，所以这部分很顺。
但 Neo4j 当前仍依赖默认环境变量：
MINDMEMOS_NEO4J_URI
MINDMEMOS_NEO4J_USERNAME
MINDMEMOS_NEO4J_PASSWORD
Homemaster YAML 中还没有显式 Neo4j 配置。建议加入：
memory:
  mindmemos:
    neo4j_uri: bolt://localhost:7687
    neo4j_username: neo4j
    neo4j_password: ${...}
然后由 build_mindmemos_config() 明确映射，不要隐式依赖 MindMemOS 默认密码。
还需要启动健康检查：
Qdrant Local path 可写。
Neo4j 可连接。
schema 初始化成功。
chat/embedding 配置存在。
P0-7：补上数据外发安全策略
这是当前做法里最容易忽略的问题。
旧 mem0 使用：
infer=False
所以主要把序列化文本发给 embedding API，不会把内容交给 chat LLM 推理。
MindMemOS schema_add 会把对话正文发给：
Chat API
Embedding API
因此接入普通对话自动记忆前，需要明确：
哪些消息允许发送到外部 Chat API。
工具输出是否允许进入记忆抽取。
credentials、cookie、token、API key 如何过滤。
File/URL 正文是否允许外发。
system prompt、内部 evidence ID 是否禁止写入记忆。
现有 memory.outbound_policy 主要针对 embedding，需要扩展到 MindMemOS chat extraction。
如果第一版只处理 agent 主动调用的、已经过约束的 add_memory，这个风险会小很多。
P0-8：决定旧 mem0 数据是否迁移
如果旧数据不重要，可以：
新 MindMemOS 数据库干净启动
如果旧数据需要保留，则要迁移：
mem0 FactRecord
    → MindMemOS fact

mem0 ProcedureRecord
    → MindMemOS experience + 原始 record_json
迁移必须验证：
数量一致。
dedupe identity 一致。
get/search 可读。
procedure 结构无损。
provenance_seq 保留。
重跑迁移幂等。
这个不阻断开发测试，但阻断最终切换。
三、核心接入完成后要做的测试
第一组：兼容层单元测试
复用现有 test_mem0_store.py 的业务场景，替换 backend：
add 后可 get。
重复 add 幂等。
identity 冲突。
exact + semantic search。
memory type 过滤。
stale provenance 拒绝。
update 后回读一致。
delete/archive 后默认不可搜索。
corrupt record 诊断。
outbound policy。
重点不是复制 mem0 实现，而是保证 Homemaster 工具行为没有退化。
第二组：生命周期测试
application 启动自动启动 MindMemOS。
Neo4j 不可用时有明确错误。
Qdrant path 持久化。
关闭后资源释放。
启动失败不会泄漏 Qdrant/Neo4j client。
重启后原有记忆仍可搜索。
一个进程只创建一个 embedded runtime。
第三组：真实数据库/API 集成测试
按顺序验证：
deterministic fact add/get/search/update/delete。
deterministic procedure add/search/get。
schema_add 对话抽取。
schema 和 vanilla search。
operation records 中保存 raw IDs。
explicit feedback。
agent 完整 memory tool 调用。
第四组：agent 端到端测试
至少要有这些场景：
agent 从工具观察获得 evidence。
agent 调用 add_memory 保存 fact。
新任务调用 search_memories 找回。
使用 raw ID 调用 get/update/delete。
旧 evidence 更新被拒绝。
USER/MEMORY 文件记忆仍正常进入新 session prompt。
没有 evidence 时不能保存结构化环境事实。
敏感内容不能进入外部 LLM/embedding。
四、高级能力的接入顺序
这些不应该阻塞 mem0 核心替换：
P1：Explicit feedback
核心 CRUD 稳定后接。
用户说“你记错了”时，Homemaster 收集：
反馈文本。
当前对话。
实际召回的 raw memories。
然后调用 feedback_sync()。
P1：Skill trajectory 和 evolution
需要在 agent 执行 skill 时记录：
cloud skill ID
version ID
usage=injected
完整任务 trajectory
score/task_id
数据正确后再开放 SkillEvolver.evolve()。
P2：Dreaming
现在不应该自动启动，因为：
operation record 仍有 aggregate/raw ID 问题。
Mimo/Anthropic 不接受当前只有 system message 的 relation-detection 请求。
内部失败后仍可能返回 status="ok"。
修好 prompt 和状态语义后再做定时任务。
P2：Implicit feedback
它依赖高质量 add/search operation records 和稳定 session ID。等主链路运行一段时间、有真实记录后再接。
P3：Kafka
目前不需要。
只有需要以下能力时再引入：
后台异步 add。
feedback worker。
dreaming worker。
skill evolution worker。
多进程削峰。
五、对现在做法的评价
做得对的地方
MindMemOS 放在 third_party/MindMemOS：简单直接。
作为正式 Python package 安装：正确。
uv lock --check 当前通过。
Qdrant 使用独立 MindMemOS path：不会破坏旧 mem0 数据。
Qdrant Local Mode：适合当前单进程开发。
复用 Homemaster chat/embedding provider：正确。
直接调用原生 pipeline：正确。
Kafka 暂时关闭：正确。
runtime 负责 config/router/client 生命周期：边界合理。
add/search operation recorder 没被跳过：方向正确。
当前相关测试刚刚重新运行：19 passed, 1 warning。
现在存在的问题
EmbeddedMindMemOS 还没挂进 application composition。
memory tools 仍然直接依赖 Mem0MemoryStore。
wrapper 只有 add/search，没有 CRUD、feedback、skill。
没有结构化 FactRecord/ProcedureRecord 到 MindMemOS 的确定性映射。
没有解决 raw/aggregate ID。
Neo4j 配置隐藏在 MindMemOS 环境变量默认值中。
没有覆盖新的 Chat API 数据外发边界。
真实 add/search 验证存在，但还没变成可重复的正式集成测试。
Qdrant Local 给出了“payload indexes 无效”的警告；单机功能不受影响，但数据变大后过滤性能会下降。
Qdrant Local 更适合单进程。多个 Homemaster 进程同时打开同一路径可能产生锁冲突；多进程部署时应换 Qdrant Server。
pyproject.toml 引入了 FastAPI、Uvicorn、Kafka、OTel 等 embedded 第一阶段不一定使用的依赖。它不阻断运行，但依赖面偏大，后续可以再精简。
六、建议实际执行顺序
按照依赖顺序做：
抽象 StructuredMemoryStore，解除 tools 对 Mem0MemoryStore 的直接依赖。
定义 fact/procedure 到 MindMemOS 的确定性存储映射。
实现 MindMemOS 版 add/get/search/update/delete 和返回值转换。
解决 raw ID 与 aggregate ID。
加入 MemoryRequestContext 统一构造器。
明确 Neo4j YAML 配置和启动健康检查。
把 runtime 接进 application start/close/service registry。
让现有六个 memory tools 全部改走新 store。
迁移并扩展原 test_mem0_store.py 和 test_memory_tools.py。
跑真实 agent E2E 和记忆重启持久化测试。
确认是否迁移旧 mem0 数据，然后删除 mem0 依赖和 vendor。
再接 explicit feedback。
再接 skill trajectory/evolution。
最后修 dreaming、implicit feedback；Kafka 继续后置。
所以答案是：**现在可以进入 agent 框架接入阶段了，底层没有新的技术未知项；但第一步不是直接替换 import，而是先做兼容接口和结构化语义映射。**完成第 1～11 项，才算真正把 mem0 从 Homemaster 主链路替掉。

