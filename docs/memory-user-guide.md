# HomeMaster V2.1 记忆用户指南

## 记忆分层

HomeMaster 使用三层本地文件记忆和一个结构化检索库：

- `SOUL.md`：稳定人格，由部署者维护，Agent 没有写工具。
- `USER.md`：稳定身份、偏好、沟通和使用习惯。
- `MEMORY.md`：近期事件、决定、结果和跨会话未完成事项。
- mem0 + embedded Qdrant：外部世界 fact 和可复用 procedure。

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

`Qdrant/bm25` 的锁定工件随 HomeMaster 源码和 wheel 分发。启动时会先将工件无网络写入
`memory.mem0.fastembed_cache_path`（默认项目 `.cache/homemaster/fastembed`，已被 Git 忽略），再只从该
目录加载并核对 commit、文件集合、SHA-256 和中文编码。换服务器或新 clone 后不需要复制
`/tmp/fastembed_cache`，也不会下载模型；只需按本页完成项目依赖安装。若部署目录不可写，可将
`memory.mem0.fastembed_cache_path` 设为该部署可持久化写入的绝对目录。

缓存损坏会在启动时从随包工件重建；随包工件本身校验失败或目录不可写时，五个 mem0 工具会返回
`memory_backend_unavailable`，不会退化为 semantic-only。文件 `memory` 工具仍可用。

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
用户身份、偏好、习惯、健康建议和长期安排必须写入 `memory(target=user)`，不能作为 mem0 fact。

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

`search_memories` 合并 metadata exact 与 mem0 的 semantic + Qdrant BM25 hybrid 结果，并返回 `exact`/`hybrid` 来源标签。默认 limit
为 5，最大 20；可提供 subject/predicate 或 entry_url/name exact hints。search text 和 query 会发送给配置的
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

文件记忆目录为 0700，文件、lock、backup、Qdrant/history/evidence DB 为本机私有且不进入 Git。USER/MEMORY
写入会拒绝 prompt injection、credential/exfiltration 模式、控制字符和内部分隔符；人工改坏文件时拒绝写入并
生成 mode-0600 drift backup。磁盘中命中威胁规则的内容不会被静默删除，prompt 中显示 blocked marker。

mem0 telemetry 在 import 前关闭，LLM extraction 永远使用 `infer=False`，构造用的 LLM sink 不使用真实 key。正常
网络出站只有配置的 embedding endpoint；本地 Qdrant/BM25 不联网。

## 常见错误

- `memory_evidence_missing/invalid`：ref 缺失、伪造、跨 tenant/session/run/turn，或 procedure 证据不完整。
- `memory_conflict`：同一 identity 已存在不同记录；先 search/get，再 update。
- `memory_stale_observation`：较早证据不能覆盖较新值。
- `memory_outbound_blocked`：待 embedding 文本包含禁止出站内容。
- `memory_backend_unavailable`：embedding provider、BM25 cache 或 Qdrant 初始化失败；文件记忆仍可用。
- `memory_outcome_unknown`：mutation 已可能开始但终态无法确认；禁止自动重试，先 get/search/raw 诊断。
- `memory_record_corrupt`：记录结构损坏或版本不支持；search 会隔离并报告脱敏 diagnostic，get 会拒绝。
- `memory_external_drift`：人工编辑后的文件无法按规范无损往返；查看 drift backup 后修复。

诊断命令：

```bash
uv run homemaster doctor --json | jq '.checks[] | select(.name == "memory_backend")'
```
