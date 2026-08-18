# HomeMaster V2.6 记忆经验自纠错实施计划

> 状态：已实施；direct update/history follow-up 按 owner 后续决策并入本计划。
>
> 需求真理源：`plan/V2.6/memory-experience-self-correction-spec.md`
>
> 代码基线：`b65d9417632da8fda39acaf9ca09cead0b660917`

## 1. 目标

在不引入 Skill Evolution、Kafka 或定时 cron 的前提下，完成 self-correction 三条链路，并将 direct update
扩展为 Schema/Vanilla 自动分流且提供版本历史查询：

1. 模型在对话中调用一个新增工具 `mindmemos_feedback`，提交用户给出的具体但尚不能直接映射为一次精确 update/delete 的反馈；HomeMaster 自动附上导致本次调用的冻结 provider messages 和其中实际展示过的 raw memories。
2. Session 结束时，现有 `add_vanilla` 成功落库后自动调用 MindMemOS implicit feedback，从 `add_record_v1` / `search_record_v1` 读取尚未处理的会话记录。
3. 每个 `project_id + user_id` 累积 8 条经 raw readback 确认的普通 session 新增 active memories 后，同步运行一次 MindMemOS dreaming；失败保留 pending，成功或真实 `no_action` 才消费本批水位。

V2.6 不采集 Task outcome 或 Trajectory Score，不因普通任务失败或用户称赞自动修改记忆。

## 2. 已锁定架构与候选取舍

实施前不再重新选择功能形态。候选及代价记录如下：

| 方案 | 代价 | 结论 |
| --- | --- | --- |
| ① 一个显式 feedback 工具 + session-end implicit + 持久化 8 条水位 | 跨 runtime、tool、finalizer 和 MindMemOS result contract，但边界清楚 | **V2.6 采用** |
| ② 把 explicit 和 implicit 都暴露成模型工具 | 模型需要判断 session-end 时机，还会重复现有 finalizer | 不采用 |
| ③ 每日 cron 运行 dreaming | HomeMaster 不是常驻服务，时钟触发不可靠，还引入第二套调度语义 | 不采用 |
| ④ HomeMaster 自己实现 feedback/dreaming 算法 | 与 MindMemOS 重复，后续行为分叉 | 不采用 |

MVP 采用方案 ①；接口只预留按 `project_id + user_id` 隔离的能力，不在 V2.6 重构当前 `local/local` 单用户身份映射。

## 3. 不变量

- 模型只给 `mindmemos_feedback` 传 `feedback`；不能传 messages、tenant/project/session、raw ID 或 mutation action。
- direct update 仍要求唯一 raw memory ID；结构化结果传完整 replacement record，Vanilla 结果传完整 content，
  backend 以旧记录是否存在合法 `record_json` 为准自动分流。
- direct delete 的边界不变：用户明确要求忘掉已唯一定位的 raw memory 时使用 `mindmemos_delete`。
- 只有具体反馈足以支持纠正、缩小适用范围或声明过时时，才调用 `mindmemos_feedback`；“你记错了”但没有正确事实或删除意图时先澄清。
- explicit feedback 使用成功 provider attempt 的准确 `frozen_messages`，不重新读取 live session，不固定截取最近 N 轮。
- recalled memories 只来自 automatic recall 或仍在该 provider 输入中的 `mindmemos_search` tool result，不从自由文本解析 ID。
- implicit feedback 的业务输入来自 MindMemOS operation-record 数据库；HomeMaster 只传 `MemoryRequestContext`。
- dreaming 在 user/project scope 运行，`session_id=None`。第 8 条 memory 只触发流程，不是 dreaming 的唯一输入。
- feedback/dreaming 生成的 memory 不参与 8 条计数。
- pipeline 返回 `status="ok"`、日志写“completed”或 `actions=0` 都不能单独清除 pending。
- 所有 mutation 按 action/target 逐条验证 raw memory；不能用全局 `any`、最佳样本或聚合 min/max 作为通过条件。
- JSONL 是取证旁路，不是成功判据。

## 4. 工作区与提交纪律

当前 HEAD 就是锁定基线 `b65d941`，但工作区包含大量用户修改，且 `plan/V2.6/` 当前未跟踪。实施不得 reset、checkout 或覆盖这些改动。

开始每个阶段前执行：

```bash
git rev-parse HEAD
git status --short
git diff -- <本阶段将修改的已有文件>
```

若本阶段文件已包含用户改动，先记录现有 diff，只在其上追加；无法无冲突区分时停止该文件的实施并让 owner 先做 checkpoint。提交时禁止 `git add -A`，只使用列出的路径；已有文件有混合改动时使用 `git add -p`。

每个 commit 前固定执行：

```bash
git diff --check
git status --short
git diff --cached --name-only
```

先把本阶段“改了什么、为什么、影响什么”写进 `CHANGELOG.md`，再使用同义的完整 commit message。本文中的 commit 是检查点，不授权自动提交；实际提交仍按 owner 当时的工作区安排执行。

## 5. 最终文件映射

### 5.1 HomeMaster 新增文件

- `src/homemaster/memory/feedback_context.py`：不可变 `FeedbackContextSnapshot`、messages 转换、按 provider 输入筛选 recalled memories。
- `src/homemaster/experience/dreaming_state.py`：8 条水位、pending batch、跨进程 scope lock、原子状态文件。
- `tests/homemaster/memory/test_feedback_context.py`：冻结上下文和 raw-memory provenance 单测。
- `tests/homemaster/experience/test_dreaming_state.py`：持久化、并发、崩溃恢复和批次消费测试。
- `tests/homemaster/memory/test_feedback_dreaming_integration.py`：真实 MindMemOS feedback/dreaming 黑盒门，标记 `live_api`。

### 5.2 HomeMaster 修改文件

- `src/homemaster/memory/automatic_recall.py`
- `src/homemaster/memory/mindmemos_runtime.py`
- `src/homemaster/agent/context.py`
- `src/homemaster/agent/generic_runtime.py`
- `src/homemaster/agent/normalized.py`
- `src/homemaster/application/runtime.py`
- `src/homemaster/application/tool_executor.py`
- `src/homemaster/tools/memory_tools.py`
- `src/homemaster/adapters/profiles.py`
- `src/homemaster/experience/finalizer.py`
- `src/homemaster/experience/__init__.py`
- `src/homemaster/cli/composition.py`
- `src/homemaster/cli/interactive_shell.py`
- `src/homemaster/events/runtime_events.py`
- `tests/homemaster/memory/test_automatic_recall.py`
- `tests/homemaster/memory/test_memory_tools.py`
- `tests/homemaster/memory/test_mindmemos_runtime.py`
- `tests/homemaster/application/test_context_assembler_scope.py`
- `tests/homemaster/application/test_application_runtime.py`
- `tests/homemaster/test_generic_agent_runtime.py`
- `tests/homemaster/test_tool_dispatcher.py`
- `tests/homemaster/experience/test_finalizer.py`
- `tests/homemaster/memory/test_managed_neo4j_composition.py`
- `tests/homemaster/test_cli_interactive.py`
- `tests/homemaster/tools/test_universal_registry.py`
- `tests/homemaster/skills/test_installed_package.py`

### 5.3 Vendored MindMemOS 修改文件

- `third_party/MindMemOS/src/mindmemos/mindmemos/typing/service.py`
- `third_party/MindMemOS/src/mindmemos/mindmemos/typing/__init__.py`
- `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/feedback/explicit.py`
- `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/feedback/implicit.py`
- `third_party/MindMemOS/src/mindmemos/mindmemos/pipelines/dreaming/default.py`
- `third_party/MindMemOS/src/mindmemos/mindmemos/prompts/EN/dreaming/relation_detection.py`
- `third_party/MindMemOS/src/mindmemos/mindmemos/prompts/EN/dreaming/action_planning.py`
- `third_party/MindMemOS/tests/pipelines/feedback/test_explicit.py`
- `third_party/MindMemOS/tests/pipelines/feedback/test_implicit.py`
- `third_party/MindMemOS/tests/pipelines/dreaming/test_default.py`

### 5.4 文档

- `docs/architecture/memory-system.md`
- `docs/architecture/application-runtime.md`
- `docs/memory-user-guide.md`
- `README.md`
- `CHANGELOG.md`
- 只有发现新的非显而易见失败时才修改 `docs/pitfalls.md` 和 `CLAUDE.md`。

## 6. Phase 0：锁定原生缺陷与接口形状

### 6.1 先写失败测试

在 vendored MindMemOS 测试中增加以下 characterization：

1. relation detection provider 抛异常时，`dream_sync()` 不能返回可消费的成功结果，不能把对应 add records 标成 `consolidation_status=done`。
2. action planning provider 抛异常时，同样保留 pending records。
3. 任意 dreaming DB action 失败时，结果必须指出失败 action/target，未完成 add records 不能标 done。
4. 合法无 cluster、cluster 小于阈值或 planner 返回空 actions 时，返回 `status="ok"` 且 `outcome="no_action"`，并明确列出已审阅的 add record IDs；这与 provider/parse error 不同。
5. feedback 任一 action `status="error"` 时，顶层 result 必须为 error；implicit handler 不得把该 session 的 add records 标成 `feedback_processed=true`。
6. Mimo/Anthropic 兼容 transport 的测试替身拒绝只有 system message 的请求；relation detection 和 action planning 修复后都必须发出一个非空 user message。

运行并确认新测试先失败：

```bash
uv run pytest \
  third_party/MindMemOS/tests/pipelines/feedback/test_explicit.py \
  third_party/MindMemOS/tests/pipelines/feedback/test_implicit.py \
  third_party/MindMemOS/tests/pipelines/dreaming/test_default.py -q
```

### 6.2 扩充原生 DTO

在 `typing/service.py` 中保持 `ServiceResultStatus` 不变，避免给所有 pipeline 增加第四种 status；给 dreaming 增加独立结果字段：

```python
class DreamingPipelineInput(BaseModel):
    mode: AddMode = "async"
    seed_add_record_ids: list[str] = Field(default_factory=list)

class DreamingActionReceipt(BaseModel):
    action: Literal["create", "update", "merge", "archive", "link"]
    target_memory_ids: list[str] = Field(default_factory=list)
    result_memory_ids: list[str] = Field(default_factory=list)
    status: ServiceResultStatus
    reason: str | None = None
    error: str | None = None

class DreamingPipelineResult(BaseModel):
    status: ServiceResultStatus
    outcome: Literal["actions", "no_action", "failed"]
    message: str | None = None
    scopes: int = 0
    clusters: int = 0
    actions: list[DreamingActionReceipt] = Field(default_factory=list)
    reviewed_add_record_ids: list[str] = Field(default_factory=list)
    completed_add_record_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
```

`typing/__init__.py` 同步导出新增公开类型。增加 DTO round-trip 测试，防止 installed wheel 缺字段。

### 6.3 修复 provider 消息和假成功

- 把 dreaming prompt 的固定规则作为 system message，把具体 cluster/group 内容作为 user message；不能把动态 memory 内容拼进唯一 system message。
- `_call_relation_detection_llm()` 和 `_call_action_planning_llm()` 不再把 provider/parse 异常转换成 `None`。返回 typed stage result，或让 `_consolidate_memory()` 捕获后生成 `outcome="failed"`。
- `_apply_actions()` 返回每个 create/update/merge/archive/link 的 receipt；底层写入返回失败、异常或 target 不存在都生成 error receipt，不能静默 `continue` 后仍计数成功。
- 只有一个 scope 的所有 action 成功，才标记该 scope add records done。
- exact duplicate 的确定性 archive 也必须产生 receipt 和 raw target/result IDs。
- 传入 `seed_add_record_ids` 时，只以这些 pending records 作为 hot seeds；Neo4j 仍可拉取共享 entity 的旧 active memories作为上下文。空列表保留原生默认扫描行为，供既有 API 兼容。
- 对 seed 无 entity、cluster 太小或确实无需 action 的 records，返回真实 `no_action` 并标记 reviewed/completed；provider/parse/DB/timeout 失败不进入该分支。

### 6.4 Phase 0 验收与提交点

```bash
uv run pytest third_party/MindMemOS/tests/pipelines/feedback third_party/MindMemOS/tests/pipelines/dreaming -q
uv run python -m compileall -q third_party/MindMemOS/src/mindmemos/mindmemos
git diff --check
```

外部 linchpin 门在此阶段先跑一次最小真实 provider 请求：相同 endpoint/model 分别执行 relation detection 和 action planning，断言 HTTP/SDK 返回成功、parser 产生 typed result；不能只检查 prompt fixture。

建议检查点：`fix(mindmemos): make feedback and dreaming completion truthful`。

## 7. Phase 1：建立准确的 explicit feedback 快照

### 7.1 先写失败测试

新增/修改测试，覆盖：

- automatic recall helper 同时返回渲染文本和原始 `MemorySearchItem` tuple，空结果两者都为空。
- `ComposedContext` 保留本次 automatic recalled items，但不把它们写进 `session.messages`。
- 未压缩长会话的 snapshot 等于本次 provider `frozen_messages`；压缩后等于“来源说明 + 压缩摘要 + 近期对话”，不是原始完整 session，也不是固定最近一轮。
- provider 第一次请求失败、使用同一 frozen body 重试成功时，snapshot 只绑定成功 attempt 的同一份 deep-frozen messages。
- automatic recall records 总是进入 snapshot；手动 `mindmemos_search` records 只有对应 tool-result call ID 仍存在于 frozen messages 时才进入。
- 被 compaction 移除的旧 search result、仅在 assistant 文本里复述的 ID、未展示的 search candidates 均不进入。
- snapshot 创建后修改 live session 或 capture map，不改变 snapshot。
- generic dispatcher 的两个生产实现 `ApplicationToolExecutor` 和 `ToolDispatcher` 都仍接受 `tool_calls`、`run_context`；在 `test_tool_dispatcher.py` 增加接口 audit，不能只测一个实现或测试替身。

先运行：

```bash
uv run pytest \
  tests/homemaster/memory/test_feedback_context.py \
  tests/homemaster/memory/test_automatic_recall.py \
  tests/homemaster/application/test_context_assembler_scope.py \
  tests/homemaster/test_generic_agent_runtime.py -q
```

预期：缺少 snapshot/capture contract 而失败。

### 7.2 实现数据流

`feedback_context.py` 定义：

```python
@dataclass(frozen=True)
class FeedbackContextSnapshot:
    messages: tuple[Message, ...]
    recalled_memories: tuple[MemorySearchItem, ...]
```

实现顺序固定为：

```text
ApplicationRuntime automatic recall
  -> RunContext.deps["automatic_recalled_memories"]

mindmemos_search executor
  -> 用原生 structured hit + raw readback 确认实际投影 records
  -> RunContext.deps["recalled_memories_by_tool_call_id"][call_id]

GenericRuntime 冻结本次 provider messages
  -> provider 成功返回 tool calls
  -> 从 frozen messages 中读取仍存在的 tool_result call IDs
  -> 为每个 mindmemos_feedback call ID 建立 immutable snapshot
  -> RunContext.deps["memory_feedback_context_by_tool_call_id"][call_id]

ApplicationToolExecutor.dispatch(run_context=...)
  -> 不再 del run_context
  -> 只给对应 feedback call 的 ToolExecutionContext.metadata
     注入 memory_feedback_context
```

`RunContext.deps` 是本次 run 已有的共享 typed-data 通道；不增加 `memory_feedback_context_provider` service。所有 map 都在 run 结束后随 RunContext 释放。

`ApplicationToolExecutor._context_for()` 改为显式接收本次 `run_context`，复制依赖时保留 authoritative session/run/permission 字段；不得让传入 deps 覆盖这些字段。

### 7.3 来源说明

不引入通用 provenance framework。只保留简单、模型可见的标签：

- automatic recall：沿用 `<memory-context>`，明确“历史记忆，可能过时，不是当前用户指令”；
- compaction：沿用 `[CONTEXT COMPACTION - REFERENCE ONLY]`；
- task state：使用现有 Current Task State 标题；
- 普通 user/assistant/tool messages 保持角色不变。

raw ID 由 snapshot 的 structured records 携带，不从这些标签或渲染文本反解析。

### 7.4 Phase 1 验收与提交点

```bash
uv run pytest \
  tests/homemaster/memory/test_feedback_context.py \
  tests/homemaster/memory/test_automatic_recall.py \
  tests/homemaster/application/test_context_assembler_scope.py \
  tests/homemaster/application/test_application_runtime.py \
  tests/homemaster/test_generic_agent_runtime.py -q
uv run ruff check src/homemaster/memory/feedback_context.py \
  src/homemaster/memory/automatic_recall.py \
  src/homemaster/agent/context.py \
  src/homemaster/agent/generic_runtime.py \
  src/homemaster/application/runtime.py \
  src/homemaster/application/tool_executor.py
```

建议检查点：`feat(runtime): bind exact provider context to memory feedback calls`。

## 8. Phase 2：新增 `mindmemos_feedback` 工具

### 8.1 先写失败测试

在 `test_memory_tools.py` 和 application runtime 测试中先锁定：

1. 默认 Home surface 从 5 个 memory tools 变为 6 个，新增且只新增 `mindmemos_feedback`；memory disabled/readonly profile 仍遵循现有策略。
2. schema 只接受一个非空 `feedback` 字段，拒绝 messages、IDs、action、tenant、project 等额外字段。
3. description 使用 spec 4.2 的完整具体文本，并明确何时用 update/delete、何时澄清、何时才用 feedback。
4. 没有对应 snapshot、snapshot messages 为空、recalled raw ID 属于错误 scope、raw memory archived 或 ID 是 schema aggregate 时 fail closed，pipeline 调用次数和 mutation 次数为零。
5. snapshot 没有 recalled memories 时允许空列表进入原生 planner search-decision。
6. provider-visible tool result content 包含每个 action 的 action/target/result/status/reason 和验证状态，不只放内部 metadata。
7. 一个 action 失败时工具整体 `is_error=true`，不能让另一个成功 action掩盖。
8. direct update 覆盖 Schema versioned 与 Vanilla in-place 两条路径；delete 既有测试保持不变。

工具 description 锁定为以下文本，实施时不要缩写成抽象的 `Submit feedback`：

```text
Review concrete user feedback about long-term memory and let MindMemOS decide
whether to add a new memory, create a corrected version of an existing memory,
archive an incorrect memory, or leave memory unchanged. Use this when the user
has corrected a fact, changed the scope of a preference or procedure, or said
that remembered information is outdated, but the correct memory action is not
already determined by one exact memory ID and one complete replacement record.
Pass the user's concrete correction, scope change, or instruction. Do not use
this when you already have an exact memory ID and a complete replacement record;
use mindmemos_update instead. Do not use this when the user explicitly asks to
forget one exact memory; use mindmemos_delete instead. Do not use it for a vague
complaint with no correction, ordinary task failure, or praise with no requested
memory change.
```

输入字段 description 同样锁定：

```text
The user's concrete correction, scope change, or instruction about remembered
information. Preserve the user's actual meaning and include the corrected fact,
applicable condition, or explicit obsolete/forget instruction. Do not submit
only vague text such as 'that was wrong'.
```

### 8.2 `EmbeddedMindMemOS` 薄包装

`start()` 使用同一组 application-owned reader/writer/recorder/search/LLM/embed clients 创建 `default_feedback` 和 `default_dreaming` pipeline；`close()` 清空二者并继续按现有顺序关闭 Neo4j/Qdrant/router config。

增加三个内部入口：

```python
async def feedback_explicit(
    self,
    *,
    feedback: str,
    messages: list[DialogueMessage],
    recalled_memories: list[MemorySearchItem],
    context: MemoryRequestContext,
) -> FeedbackPipelineResult: ...

async def feedback_implicit(
    self,
    context: MemoryRequestContext,
) -> FeedbackPipelineResult: ...

async def dream(
    self,
    *,
    seed_add_record_ids: list[str],
    context: MemoryRequestContext,
) -> DreamingPipelineResult: ...
```

三个入口都固定 `mode="sync"`。不暴露 async/Kafka mode。

`add_vanilla()` 需要让 finalizer 获得本次预分配的 `add_record_id`。新增 HomeMaster adapter receipt：

```python
@dataclass(frozen=True)
class RecordedAddResult:
    add_record_id: str
    result: AddPipelineSyncResult
```

只修改 `SessionFinalizer` 这个 `add_vanilla` 消费者；不要改变模型可见 `mindmemos_add` 的 schema add 返回。

### 8.3 feedback executor

`FeedbackMemoryInput` 只定义 spec 中的 `feedback`。executor 执行：

1. 从 `context.metadata["memory_feedback_context"]` 读取 snapshot。
2. 按原顺序把 snapshot 中 user/assistant/tool 的文本 blocks 转为 `DialogueMessage`；跳过 system prompt 参数、reasoning、图片和二进制。
3. 对每个 recalled item 的 raw ID 调 `get_raw()`，逐条确认 `project_id/user_id` scope、`status="active"` 和内容相符。
4. 构造 explicit feedback 输入并调用 `feedback_explicit()`。
5. 检查顶层 status 和每个 action status。
6. 对 add/update/delete action 逐条 raw readback：add 结果 active；update 旧 ID archived、新 ID active 且有 `DERIVED_FROM`；delete 目标 archived；noop 不产生 mutation。
7. 输出有界 JSON，并写 explicit started/completed/failed JSONL event。

权限沿用 memory mutation 的 `tool.mutate`；不新增 capability。

### 8.4 Phase 2 验收与提交点

```bash
uv run pytest \
  tests/homemaster/memory/test_memory_tools.py \
  tests/homemaster/memory/test_mindmemos_runtime.py \
  tests/homemaster/application/test_application_runtime.py \
  tests/homemaster/tools/test_universal_registry.py -q
```

再通过真实 `ApplicationRuntime` 跑一次 provider 选择工具的集成用例，而不是直接调用 executor。下一次真实 transport 请求中解析 tool-result `content`，逐字段断言 action receipts。

建议检查点：`feat(memory): add context-bound explicit feedback tool`。

## 9. Phase 3：session-end implicit feedback

### 9.1 先写失败测试

扩充 `test_finalizer.py`：

- `add_vanilla` 返回非 ok、没有 operation record receipt 或 raw readback 失败时，不运行 implicit feedback。
- add 成功后固定顺序为 `add_vanilla -> raw readback -> feedback_implicit`。
- implicit 只收到 `MemoryRequestContext`；不收到 finalizer messages 或 feedback text。
- context 仍使用当前 `local/local`，session ID 稳定；collector 可以处理同 user scope 的旧未处理 session backlog，但不同 session rounds 不混合。
- 三类信号 `task_temporary/scenario_specific/long_term` 分别进入正确 planner 边界；纯称赞不 mutation。
- implicit action 部分失败时 finalizer receipt 指出具体 action；已经成功的 add 不回滚，失败 add records 保持未处理以便重试。
- 重复 finalize 从 job 的 phase receipt 继续，不重复 `add_vanilla`，也不重复已确认 mutation。

### 9.2 把 finalizer job 改成阶段状态机

现有 `job.json` 不能在 `add_vanilla` 后立即写总 `completed`。升级为兼容旧记录的 schema，至少保存：

```json
{
  "schema_version": 2,
  "job_id": "...",
  "status": "pending|completed|failed",
  "session_id": "...",
  "add": {"status": "completed", "add_record_id": "...", "operations": []},
  "implicit_feedback": {"status": "pending|completed|failed", "actions": []},
  "dreaming_counter": {"status": "pending|completed"},
  "dreaming": {"status": "not_due|completed|no_action|failed"}
}
```

每个阶段完成后用现有 `_write_json()` 的临时文件、file fsync、`os.replace`、directory fsync 语义持久化；若现有 helper 缺 directory fsync，先加失败测试再补齐。

固定执行顺序：

```text
collect/render
-> add_vanilla
-> 顶层返回码 + 每个新增 raw memory readback
-> feedback_implicit
-> 顶层返回码 + per-action raw/lineage readback
-> dreaming state register
-> due/pending 时 dream
-> final job receipt
```

implicit 失败不会把 add phase 改回 pending；重试只从 implicit phase 开始。

### 9.3 可观测性

将以下事件加入 `KNOWN_EVENT_TYPES`，只进入内部 JSONL，不进入公共 progress projection：

```text
memory.feedback.explicit.started
memory.feedback.explicit.completed
memory.feedback.explicit.failed
memory.feedback.implicit.started
memory.feedback.implicit.completed
memory.feedback.implicit.failed
memory.dreaming.threshold_reached
memory.dreaming.started
memory.dreaming.completed
memory.dreaming.no_action
memory.dreaming.failed
```

测试 typed 字段和错误分类，同时断言日志写失败不会改变已经确认的 memory 外部终态。

### 9.4 Phase 3 验收与提交点

```bash
uv run pytest \
  third_party/MindMemOS/tests/pipelines/feedback \
  tests/homemaster/experience/test_finalizer.py \
  tests/homemaster/test_event_sinks.py -q
```

建议检查点：`feat(experience): process implicit feedback after session add`。

## 10. Phase 4：持久化 8 条水位与 dreaming owner

### 10.1 状态文件的准确位置和 schema

状态存放在：

```text
<memory.data_root>/mindmemos/dreaming_state/
  <sha256(project_id NUL user_id)>.json
  <sha256(project_id NUL user_id)>.lock
```

JSON 内仍保存明文 scope 供校验，文件名只用于避免路径注入。V2.6 当前实际 scope 是 `local:local`。

状态至少为：

```json
{
  "schema_version": 1,
  "scope": {"project_id": "local", "user_id": "local"},
  "new_active_memory_count": 0,
  "pending": false,
  "pending_add_records": [
    {
      "add_record_id": "...",
      "memory_ids": ["raw-1", "raw-2"],
      "confirmed_at": "..."
    }
  ],
  "inflight": null,
  "last_successful_watermark": null,
  "last_attempt_at": null,
  "last_success_at": null,
  "last_error": null
}
```

`pending_add_records` 是为了解决两个真实问题：计数可能跨 7 天才达到 8，不能受 MindMemOS 默认 lookback 丢失；dreaming 运行期间又可能新增 memories，不能把它们随旧批次一起扣掉。

### 10.2 先写失败测试

`test_dreaming_state.py` 先覆盖：

1. 7 条 confirmed raw memories 后不 due；第 8 条后 `pending=true` 且只产生一个 inflight batch。
2. 一个 add record 产生多条有效 memories 时按 memory 条数计数，但 batch seed 使用唯一 add record ID。
3. 只计普通 session `add_vanilla` 的 `operation="add"`、raw readback active；update/delete/noop、重复 ID、反馈和 dreaming memory 不计。
4. `project-a/user-a` 与另一 scope 完全隔离；当前生产 composition 仍只构造 `local/local`。
5. 两个线程、两个 asyncio task 和两个独立进程同时跨阈值，只有一个 owner 获得 inflight claim。
6. 写临时文件后进程退出、JSON 截断、owner 超时和重启恢复均不会丢 pending；损坏状态 fail closed 并保留原文件供诊断。
7. dreaming 期间新加入的 add record 不在当前 inflight，成功后仍留在 pending count。
8. provider/parse/DB/timeout failure 调 `fail_batch()` 后保留完整 inflight records 并 `pending=true`。
9. `outcome="actions"` 或真实 `outcome="no_action"` 且所有终态验证通过后，`complete_batch()` 只消费已 claim records并推进 watermark。

状态写入使用 scope lock、同目录临时文件、file fsync、`os.replace`、directory fsync。Linux 采用 `fcntl.flock` 做跨进程互斥；测试不能只验证进程内 `asyncio.Lock`。

### 10.3 coordinator

`DreamingCoordinator` 固定阈值：

```python
DREAMING_MEMORY_THRESHOLD = 8
DREAMING_TIMEOUT_SECONDS = 300.0
```

不加入配置 mode。流程：

1. finalizer 从已验证 add operations 取得 `(add_record_id, active raw memory_ids)`。
2. 在 scope lock 中去重并 register。
3. count 达 8 或原状态 pending 时原子 claim 当前 `pending_add_records`；锁定目标后本轮不再重算。
4. 用新的 request ID 和 `session_id=None` 构造 `MemoryRequestContext`。
5. `asyncio.timeout(300)` 内调用 `EmbeddedMindMemOS.dream(seed_add_record_ids=...)`。
6. 验证 native receipt 后，对每个 action target/result 调 `get_raw()`，并用 Neo4j read query 验 lineage/link。
7. 核对 claimed add records 的 consolidation status。
8. 全部通过才 complete；任一失败 fail_batch 并保存 typed error。

application composition 创建一个 application-owned coordinator，并通过现有 bundle/shell 参数注入 `interactive_shell.py` 内构造的 `SessionFinalizer`；不能在 shell 里再创建第二个 coordinator。增加 CLI wiring 测试，覆盖 `user_exit`、`eof`、`new_session` 和 `shell_interrupt` 的既有 finalizer 入口。`EmbeddedMindMemOS.start()` 后调用一次 `retry_pending()`；该重试失败写 typed event但不伪装成功，也不创建 cron。每次 session finalization 仍会再次 retry。

### 10.4 Phase 4 验收与提交点

```bash
uv run pytest \
  tests/homemaster/experience/test_dreaming_state.py \
  tests/homemaster/experience/test_finalizer.py \
  tests/homemaster/memory/test_managed_neo4j_composition.py \
  tests/homemaster/test_cli_interactive.py -q
```

用临时 `memory.data_root` 启动、累计 8 条、故意让 provider 失败、关闭并重启 application，断言 JSON 仍 pending；恢复 provider 后再次启动，断言同一批被消费且状态文件 watermark 前进。这是持久化黑盒门，不能只 mock state methods。

建议检查点：`feat(memory): trigger recoverable dreaming every eight memories`。

## 11. Phase 5：真实外部终态黑盒门

### 11.1 环境前提

使用项目虚拟环境和真实 HomeMaster config，不在仓库写入密钥。真实配置继续 gitignore；只允许占位 `.example` 入库。需要：

- 真实 chat provider；
- 真实 embedding provider；
- Qdrant Local，位于独立临时 `memory.data_root`；
- 真实 Neo4j（优先当前 managed-local composition）；
- 与生产一致的 `EmbeddedMindMemOS.start()/close()`。

### 11.2 Explicit feedback 黑盒门

通过真实 pipeline 写入“用户使用 conda”，再通过真实 `ApplicationRuntime -> provider tool call -> mindmemos_feedback` 提交“用户使用 uv”。逐项断言：

- provider 返回成功且实际选择唯一 `mindmemos_feedback`；
- tool result return code 成功；
- 旧 raw ID `status=archived`；
- 新 raw ID `status=active` 且 content 准确包含 uv、不包含猜测内容；
- Neo4j 存在 `new-[:DERIVED_FROM]->old`；
- 一个 unrelated raw memory 内容和 status 前后完全不变；
- schema aggregate ID 和 vague “你记错了”用例 mutation 数为零。

### 11.3 Implicit feedback 黑盒门

用真实 `SessionFinalizer -> add_vanilla` 和真实 `EmbeddedMindMemOS.search()` 产生同 session 的 add/search records，再加一个不同 session。逐项断言：

- collector 从数据库重建正确 rounds，不混 session；
- task-temporary、scenario-specific、long-term 三个 signal 分别有准确类别；
- 每个 action return code；
- 每个 target/result raw memory 终态；
- 成功 session 的 add records `feedback_processed=true`；
- 失败 session 的 records 仍未处理；
- 纯称赞 session mutation 数为零。

### 11.4 Dreaming 黑盒门

独立准备三组，不能混成一个聚合判据：

1. exact/semantic duplicate 组；
2. 明确同 subject/property、不同时间值的 conflict 组；
3. 无需动作组。

每一组分别断言：

- native return status 和 `outcome`；
- scopes、clusters、每个 action receipt 和 errors；
- 每个输入 raw ID 最终 status/content；
- 每个输出 raw ID content；
- 每条需要的 Neo4j lineage/link；
- 每个 seed add record consolidation status；
- unrelated scope 和 unrelated memory 前后不变。

再注入一次真实 provider 拒绝或不可解析响应，断言 return 为 failed、add records 未 done、HomeMaster state 仍 pending。此用例是修复“provider 被拒但 dream_sync 返回 ok”的正交证据。

执行入口：

```bash
uv run pytest tests/homemaster/memory/test_feedback_dreaming_integration.py \
  -m live_api -q -s
```

测试结束必须核对进程返回码为 0，并读取 Qdrant raw points、Neo4j query rows 和 state JSON；只看 pytest 内部日志不算通过。

## 12. Phase 6：安装产物、全量回归与文档同源

### 12.1 Wheel 门

先扩充 `test_installed_package.py`，断言 wheel 含修改后的 vendored modules，并能从源码 checkout 外 import：

```python
from mindmemos.typing import (
    DreamingActionReceipt,
    DreamingPipelineInput,
    DreamingPipelineResult,
    FeedbackPipelineInput,
)
from homemaster.memory.feedback_context import FeedbackContextSnapshot
from homemaster.experience.dreaming_state import DreamingStateStore
```

从空 build 目录构建，不复用陈旧 setuptools 输出：

```bash
tmp_build="$(mktemp -d)"
uv build --out-dir "$tmp_build/dist"
uv run pytest tests/homemaster/skills/test_installed_package.py -q
python -m zipfile -l "$tmp_build"/dist/*.whl
```

按现有 installed-package test 的隔离 venv 流程，从 repo 外安装 wheel、核对依赖解析返回码、import、构造默认 Home profile，并断言模型工具列表包含 `mindmemos_feedback` 且没有另一个同义 feedback 工具。

### 12.2 文档更新

- `docs/architecture/memory-system.md`：新增 explicit snapshot、operation-record implicit、8 条水位、result/terminal invariants 和真实数据流。
- `docs/architecture/application-runtime.md`：说明 frozen provider attempt 如何通过 `RunContext.deps` 绑定到一个 feedback tool call。
- `docs/memory-user-guide.md`：用具体对话例子教用户 direct update/delete 与 feedback 的边界；说明 session-end 和 8 条触发、state 文件位置、失败恢复和无 cron。
- `README.md`：能力清单从五个模型可见 memory tools 更新为六个，并简述后台 implicit/dreaming。
- `CHANGELOG.md`：按最终真实行为写一条完整 V2.6 记录。
- 若实施中发现非显而易见坑，先把“症状 -> 根因 -> 修法/教训 -> ref”加到 `docs/pitfalls.md` 顶部，再把可执行正向规则加到 `CLAUDE.md` 对应章节；没有新坑则不改这两个文件。

### 12.3 最终回归

先跑聚焦测试，再跑全量非 live：

```bash
uv run pytest \
  tests/homemaster/memory \
  tests/homemaster/experience \
  tests/homemaster/application/test_application_runtime.py \
  tests/homemaster/test_generic_agent_runtime.py \
  tests/homemaster/tools/test_universal_registry.py \
  tests/homemaster/skills/test_installed_package.py -q

uv run pytest -m 'not live_api and not live_alfworld and not live_mcp and not live_coworker' -q
uv run ruff check src tests
uv run python -m compileall -q src tests
git diff --check
```

最后再次运行 Phase 11 的真实黑盒门。全量单测绿不能替代它。

建议最终检查点：`feat(memory): deliver V2.6 experience self-correction`。

## 13. 回滚策略

代码回滚按 Phase commit 逆序进行，但不删除或物理清理已经写入的用户 memory：

- 工具 surface 回滚后，既有 add/search/update/delete 继续可用；`mindmemos_feedback` 从 registry 移除。
- implicit/dreaming composition 回滚后，保留 `job.json`、dreaming state 和 add/search records，不能把 pending 标成功。
- 已归档/版本化的 feedback memory 通过 raw lineage 诊断；未经用户确认不得自动反向 mutation。
- DTO 回滚前必须确认没有新代码读取新增 wheel contract；vendored MindMemOS 和 HomeMaster adapter 应在同一 release 回滚。
- 状态 schema 采用版本字段；旧代码不认识新 schema 时 fail closed，不能重置计数为 0。

## 14. Definition of Done

只有以下项目全部满足，V2.6 才算完成：

- `mindmemos_feedback` 的调用边界、schema 和完整 description 通过真实 provider tool routing。
- explicit feedback 收到准确 frozen messages 和实际可见 raw memories，未展示/伪造 ID 无法 mutation。
- direct update 以 `record_json` 自动分流，Schema 版本化且不重跑 Schema Add，Vanilla 原地复用原生 update；
  direct delete 语义和回归不变。
- session-end add 成功后才触发 implicit feedback，输入只来自 MindMemOS operation records。
- 8 条计数持久、跨进程互斥、重启可恢复，当前批目标在 claim 时锁定且不会漂移。
- dreaming 用 `session_id=None` 扫 scope，以 pending add records 为 seeds，并可带入相关旧 active memories。
- legitimate no-action 可消费批次；provider/parse/DB/timeout failure 不能消费。
- feedback/dreaming 每个 action 都有返回码、raw Qdrant readback 和需要时的 Neo4j lineage 验证。
- exact duplicate、conflict、no-action、failure 四种真实环境用例逐组通过，无 aggregate 假阳性。
- installed wheel 在源码外包含并运行新 HomeMaster 和 vendored MindMemOS contract。
- 全量非 live 测试、ruff、compileall、`git diff --check` 和真实外部终态门全部通过。
- README、用户指南、两份架构文档和 CHANGELOG 与最终代码一致。
- 最终 `git status --short` 中没有测试意外生成的 untracked 文件，也没有误暂存用户原有改动。

## 15. Schema feedback 与 Runtime-owned evidence follow-up

2026-08-18 的真实交互验收发现：native feedback update 能把 Schema memory 的 `content` 改成新结论，
同时原样复制旧 `record_json`；HomeMaster 又只验证 content/status/lineage，因此错误报告成功。模型随后从 history
发现正文与 record 冲突，并在旧 evidence ref 被拒后误删了新版本。这一链路属于发布阻断问题。

本 follow-up 锁定以下不变量：

1. Schema memory 以完整 `record_json` 为唯一真理源，`content` 只能由 `serialize_record()` 确定性生成；
   Vanilla memory 没有 `record_json`，继续以 content 为真理源。
2. Feedback planner 看到 raw memory 对应的完整 structured record；更新 Schema target 时必须返回完整
   `replacement_record`。缺失、无效、改变 identity 或 source/evidence 不匹配时 fail closed，不能退回自由文本更新。
3. Schema feedback 复用 HomeMaster `update_versioned()`，并以生成后的 content、完整 record、old archived、
   new active 和 `DERIVED_FROM` 全部回读通过为成功门。
4. `evidence_refs` 从模型可见的 add/update 参数和 provider context 删除。Evidence ledger 保留，但 executor 按当前
   tenant/session/run/turn 和 record source 自动选择真实 evidence；不得读取上一轮 ref，也不得放宽 scope 校验。
5. Vanilla direct update 使用当前用户陈述 evidence。Procedure add/update 只使用当前轮已验证的
   `environment_observation` evidence，数量、顺序和最终成功要求保持不变。

实施顺序：先加入正文正确但 `record_json` 陈旧的失败测试、Schema feedback 缺 replacement 的拒绝测试和旧 ref
不再出现在工具 schema/provider context 的测试；再扩展 MindMemOS action/search DTO、planner prompt、structured
update handler、HomeMaster verifier 和 ledger scope lookup；最后运行真实 provider + Qdrant + Neo4j 黑盒门。
