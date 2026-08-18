# HomeMaster V2.6 经验级自纠错 Spec

## 0. 文档状态

- 日期：2026-08-18
- 状态：已实施并通过真实 Qdrant、Neo4j 与 provider 终态验证
- 目标阶段：STAGE 03 · 会改 —— 经验级自纠错
- 运行方式：HomeMaster embedded/local MindMemOS，同步 pipeline，不引入 Kafka
- 真理源：本文件定义 V2.6 的实施范围、触发条件、行为边界和验收标准

## 1. 目标

V2.6 在现有记忆新增、召回和 CRUD 能力之上补齐经验纠错与后台整理闭环：

1. 用户给出明确修改结论时，继续使用确定性的 `update` / `delete`；
2. 用户给出可执行反馈、但具体记忆动作尚未确定时，使用 MindMemOS explicit feedback 规划
   `add`、版本化 `update`、`delete` 或 `noop`；
3. 会话结束并完成现有经验保存后，使用 MindMemOS implicit feedback 从近期会话记录中识别纠正、不满、
   偏好变化和可复用规则，并规划相应记忆动作；
4. 每累计 8 条普通会话产生的有效新增 raw memory，自动触发一次 MindMemOS dreaming；
5. 所有 feedback 和 dreaming 只有在真实 raw memory 终态得到回读验证后才算成功。

本轮不重新实现 `add_vanilla`，不引入 Skill、Skill Evolution 或每日定时任务。

## 2. 当前基线

### 2.1 已有能力

`src/homemaster/memory/mindmemos_runtime.py::EmbeddedMindMemOS` 已提供：

- `add()`；
- `add_vanilla()`；
- `search()`；
- `get()`；
- `get_raw()`；
- `update()`；
- `delete()`。

`src/homemaster/experience/finalizer.py::SessionFinalizer` 已在会话结束时：

1. 从 runtime trace 收集当前 session 的事件；
2. 排除 `transport.delta`；
3. 渲染为 MindMemOS dialogue messages；
4. 调用 `EmbeddedMindMemOS.add_vanilla()`；
5. 持久化幂等 job 结果。

因此 V2.6 不新增第二套经验抽取器，不复制 MindMemOS add 算法，也不改变现有 session trace 真理源。

### 2.2 当前缺口

- `EmbeddedMindMemOS` 尚未构造和公开 feedback pipeline；
- `EmbeddedMindMemOS` 尚未构造和公开 dreaming pipeline；
- implicit feedback 尚未接入 session finalization；
- 没有“距上次成功 dreaming 新增了多少条有效 raw memory”的持久化水位；
- 当前 dreaming 在 Mimo/Anthropic 路由下存在仅 system message 的兼容问题；
- 当前 MindMemOS dreaming 可能返回 `status="ok"` 但没有实际 action，不能作为成功判据；
- schema search 的聚合视图 ID 与 raw memory ID 仍必须严格区分。

## 3. 锁定的语义边界

### 3.1 Direct update/delete

直接调用既有 `update()` 或 `delete()` 的前提是调用方已经拥有确定结论：

```text
唯一 raw memory ID
+ 完整替换内容，或明确删除意图
```

HomeMaster direct update 读取 raw memory 后按 `record_json` 自动分流：

- `record_json` 不存在：调用 `DefaultUpdatePipeline` 对 active Vanilla memory 原地修改 content/vector；
- `record_json` 存在且通过 HomeMaster Schema 校验：要求完整 replacement record，确定性创建新版本、同步
  metadata、memory/entity vectors 和图关系，归档旧版本并建立 `DERIVED_FROM`，不重新运行 Schema Add；
- `record_json` 存在但损坏：fail closed，不得当作 Vanilla 更新。

结构化 replacement 的 identity 不得变化。历史版本通过 `mindmemos_history(memory_id)` 查询，普通搜索继续只返回
active memory；没有真实 lineage 的旧归档记录不得猜测性拼接。

### 3.2 Explicit feedback

Explicit feedback 接收：

```text
feedback text
+ 触发本次工具调用时，模型实际看到的完整有效 messages
+ 这些 messages 中实际展示给模型的 recalled memories，保留真实 raw memory ID
```

它把“反馈证据和意图”交给 MindMemOS planner，由 planner 决定：

```text
add / update / delete / noop
```

MindMemOS feedback executor 的 `update` 继续使用 MindMemOS 原生版本化实现。Direct structured update 也保留
版本，但由 HomeMaster 将已经验证的 typed record 确定性映射成 DB plan，避免再次调用 Schema Add 的 LLM 阶段。

以下情况不得自动执行 mutation：

- 反馈只有“你记错了”等否定表达，且 messages 中没有替代事实或明确删除意图；
- 目标只能定位到 schema 聚合视图 ID，无法映射到 raw memory ID；
- planner 返回的 `target_memory_id` 不属于已验证的 recalled raw memory 集合；
- planner 的新内容没有来自 feedback/messages 的可追踪依据。

证据不足时返回 typed `noop` / `needs_clarification`，不得让 LLM 猜测事实。

### 3.3 Implicit feedback

Implicit feedback 不接收 feedback text，也不由 HomeMaster 再传一次 session messages。调用只传
`MemoryRequestContext`，提供 `project_id`、`user_id`、`session_id` 等身份。原生 collector 默认按 project/user 查询，
再用 record 中的稳定 `session_id` 分组；真正的业务输入已经由前面的 `add_vanilla` 和 `search` 写入 MindMemOS 数据库：

```text
add_record_v1
  - session_id
  - add_vanilla 使用的 user / assistant / tool messages
  - 本次 add 写入的 memory payloads
  - status
  - feedback_processed

search_record_v1
  - session_id
  - search query
  - 实际召回的 raw memory ID 和内容
  - status
```

输入的产生和消费顺序为：

```text
SessionFinalizer 从 canonical JSONL trace 渲染 messages
  -> add_vanilla(messages)
  -> MindMemOS recorder 写入 add_record_v1，并在 add 完成后补充 memory payloads

每次 EmbeddedMindMemOS.search(query)
  -> MindMemOS recorder 写入 search_record_v1 和实际召回结果

feedback_implicit(context)
  -> 读取同一 project/user 下近期未处理的 add_record_v1 和相关 search_record_v1
  -> 按稳定 session_id 分组，禁止把不同 session 的 rounds 合并
  -> 每轮压缩为第一个 user message 和最后一个 assistant message
  -> 用 LLM 识别 actionable feedback signals
  -> 把 signal 分类为 task_temporary、scenario_specific 或 long_term
  -> 使用同一 session 去重后的新增/召回 memories 作为候选池，按 signal 与 round 内容规划
     add、版本化 update、delete 或 noop
  -> 成功处理后将对应 add records 标记为 feedback_processed
```

MindMemOS 当前原生 collector 默认查看最近 3 天、最多 100 条记录，并以 user scope 处理所有尚未完成的 session backlog，
不只读取刚结束的一个 session。V2.6 保留这一行为，但必须保持 session 分组隔离和幂等处理。

它识别的是会话中的纠正、不满、修改要求、偏好变化和未来规则，不把纯粹的“谢谢”“做得很好”作为反馈信号。
它不是第二个 add，也不等价于通用任务评分器。

### 3.4 Dreaming

Dreaming 是批量维护能力，不是模型在普通任务中自由调用的工具。它使用 MindMemOS 原生同步 dreaming pipeline，
负责检测和处理重复、冲突及可合并记忆。

本轮不设置每日定时任务。唯一自动触发条件为：

```text
同一 project_id + user_id 作用域内，
自上次真实成功 dreaming 后，
累计产生 8 条有效普通会话 raw memory。
```

阈值 `8` 必须配置化，默认值为 8。

## 4. V2.6 修改范围

### 4.1 EmbeddedMindMemOS pipeline 接入

修改 `src/homemaster/memory/mindmemos_runtime.py`：

1. 在 `start()` 中使用既有 reader、writer、recorder、LLM、embedding、search pipeline 构造原生
   `DefaultFeedbackPipeline` 所需的 explicit/implicit handlers；
2. 在 `start()` 中构造原生同步 dreaming pipeline 和 `RecentActivityCollector`；
3. 增加薄包装方法：

```python
async def feedback_explicit(
    self,
    *,
    feedback: str,
    messages: list[Any],
    recalled_raw_memories: list[Any],
    context: Any,
) -> Any: ...

async def feedback_implicit(self, context: Any) -> Any: ...

async def dream(self, context: Any) -> Any: ...
```

4. `close()` 清理新增 pipeline 引用，不新增第二套数据库或模型客户端；
5. 全部使用同步原生调用，不启用 Kafka，不通过 HTTP 自调用。

### 4.2 Explicit feedback 工具与路由

新增一个面向模型的 typed explicit feedback 工具，公开名称锁定为：

```text
mindmemos_feedback
```

工具只接受反馈语义，不接受模型任意指定内部 mutation action。模型可见 description 锁定为以下具体文本，实施时不得
缩写成 `Submit memory feedback`、`Correct memory` 等无法判断调用边界的抽象说明：

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

工具输入至少包含：

```python
class FeedbackMemoryInput(_MemoryToolInput):
    feedback: _NonEmptyText = Field(
        description=(
            "The user's concrete correction, scope change, or instruction about "
            "remembered information. Preserve the user's actual meaning and include "
            "the corrected fact, applicable condition, or explicit obsolete/forget "
            "instruction. Do not submit only vague text such as 'that was wrong'."
        )
    )
```

HomeMaster 直接提供触发本次调用的冻结 provider messages，以及其中实际展示给模型的 recalled memories；模型不得
伪造 tenant、project、session、raw memory ID 或 recalled memory 内容。

#### 4.2.1 可信上下文注入

模型只传 `feedback`，不传 `messages`、`recalled_memories` 或任何 raw memory ID。HomeMaster 为每次
`mindmemos_feedback` 调用直接注入一份不可变的 `FeedbackContextSnapshot`：

```python
@dataclass(frozen=True)
class FeedbackContextSnapshot:
    messages: tuple[Message, ...]
    recalled_memories: tuple[MemorySearchItem, ...]
```

这里的 `messages` 不是整个原始 session，也不是固定的上一轮或最近 N 轮，而是触发该 tool call 的成功 provider
attempt 实际收到的完整有效 messages：未压缩时包含当时保留的全部对话；发生压缩时包含压缩摘要和近期对话。
`GenericRuntime` 已在 provider 请求前创建 `frozen_messages`，V2.6 直接复用这份快照，不在工具执行时重新读取
`runtime.session.messages`。

ContextAssembler 写入模型 messages 的非普通对话内容必须带人类可读的来源说明，例如“自动召回的历史记忆，可能
过时”“较早对话的压缩摘要”“Application 当前任务状态”。这些说明随正文一起发给模型，帮助模型区分历史记忆、
摘要、运行状态和当前用户指令。真实 raw memory ID 不依赖这些说明传递，而是保存在 snapshot 的
`recalled_memories` 字段中；V2.6 不为此引入新的通用 context-entry 或 projection 框架。

锁定的数据来源和顺序为：

```text
ContextAssembler 组装本次 provider messages，并保留其中 recalled memories 的结构化结果
  -> GenericRuntime 冻结最终 provider messages
  -> provider 基于该快照返回 mindmemos_feedback({"feedback": "..."})
  -> GenericRuntime 将同一份 frozen messages 和其中实际可见的 recalled memories
     绑定到该 tool call
  -> ApplicationToolExecutor 将 FeedbackContextSnapshot 直接放入
     ToolExecutionContext.metadata["memory_feedback_context"]
  -> mindmemos_feedback 将文本 messages 转换为 MindMemOS DialogueMessage
  -> 对每个 memory_id 调用 get_raw()，确认它是当前 tenant/project 下的
     active raw memory
  -> 构造 FeedbackPipelineInput
  -> feedback_sync()
```

不新增 `memory_feedback_context_provider`。Snapshot 是本次 tool call 的数据，不是长期 service；feedback 工具直接从
`ToolExecutionContext.metadata` 读取。当前 `ApplicationToolExecutor.dispatch()` 会丢弃收到的 `run_context`，实施时必须
改为读取其中与本次 provider attempt 绑定的 snapshot，再注入对应 tool context。

Messages 直接来自 `frozen_messages`，转换边界为：

- 保留原顺序，并转换模型实际看到的 user、assistant 和 tool 文本；
- 保留 ContextAssembler 已生成的来源说明、压缩摘要和近期对话，因此长会话不按固定轮数截断；
- 不传单独的 system prompt、tool schema、内部 reasoning、transport delta、audit、图片或二进制内容；
- 当前 `mindmemos_feedback` assistant tool call 是 provider 输出，不在导致该调用的 provider 输入快照中，也不为它合成
  虚假 assistant 文本；
- snapshot 建立后，feedback 的搜索、规划和执行阶段不得重新读取可能继续变化的 session。

Recalled memories 使用同一快照旁边保存的结构化结果，不从渲染文本中解析 ID：

- automatic recall 在生成模型可见的来源说明和记忆文本时，同时保留实际 `MemorySearchItem`；当前只返回渲染字符串的
  路径必须改为同时返回这些结构化 records；
- `mindmemos_search` 成功后，按 tool call ID 保存实际进入模型 tool-result 的 `MemorySearchItem`；建立 provider 快照时，
  只纳入仍然出现在该 `frozen_messages` 中的 search tool results；
- 每条 record 保留真实 raw memory ID。调用 feedback 前逐条 `get_raw()`，确认 tenant/project、active 状态和内容；
- 未展示给模型的搜索候选、模型复述的 ID、自由文本中出现的 ID 均不得进入 `recalled_memories`。

若快照中没有可信 recalled memories，则传空列表，让 MindMemOS explicit planner 按原生 search-decision 流程决定是否
补充搜索。

禁止以下方案：

- 把完整 messages 或 memory IDs 加进模型工具参数；
- 让模型复制它看到的聊天记录再传回工具；
- 从 JSONL trace、渲染日志或自由文本中猜测当前 canonical messages；
- 把整个长期 session 原样发送给 feedback planner；
- 固定只取上一轮或最近 N 轮；
- 只传 search 候选全集而不区分实际提供给模型的 records；
- 在 feedback 执行过程中每一步重新读取 live session，造成上下文漂移。

模型应调用的具体例子：

```json
{"feedback":"用户明确说明自己使用 uv 而不是 conda；修正与 Python 环境管理相关的旧记忆。"}
{"feedback":"用户说明保持方法部分简短只适用于实现简单的论文，不应作为所有论文的通用规则。"}
{"feedback":"用户说明旧住址不再是当前住址，相关当前地址记忆已经过时。"}
```

模型不应调用的具体例子：

```text
已知 raw memory ID 和完整替换 record
  -> mindmemos_update

用户要求删除一个已经唯一定位的 raw memory
  -> mindmemos_delete

用户只说“你记错了”，messages 中没有正确事实、适用范围或删除意图
  -> 请求澄清，不调用 memory mutation

某个工具命令失败，但用户没有表达可复用纠正或记忆修改要求
  -> 按普通工具失败处理，不调用 mindmemos_feedback

用户只说“谢谢，这次做得很好”
  -> 不调用 mindmemos_feedback
```

触发条件：

```text
用户给出可执行反馈
AND 反馈上下文中存在足够的纠正、范围或删除依据
AND 具体动作尚未确定
```

不触发条件：

```text
用户已经给出唯一目标和完整替换内容 -> direct update
只有否定、没有替代事实或删除意图 -> clarification/noop
普通成功、感谢或无修改要求 -> 不调用
```

工具返回每个 action 的类型、目标 raw memory ID、结果 memory ID、status 和 reason，并在模型可见 content 中提供有界、
结构化结果。

### 4.3 Implicit feedback 的 session-end 触发

修改 `SessionFinalizer` 或其唯一 composition owner，使固定顺序为：

```text
collect trace
-> add_vanilla
-> 验证 add 返回和 raw memory 终态
-> feedback_implicit
-> 验证每个 feedback action 和 raw memory 终态
-> 更新 dreaming 水位
-> 必要时 dream
```

Implicit feedback 只在本次会话存在成功持久化的 add operation record 时运行。它不在每个 turn 后运行，也不暴露为
模型自由选择的普通工具。调用 `feedback_implicit()` 时只传 `MemoryRequestContext`，不传当前 messages 或 feedback
text；MindMemOS collector 从 operation-record 数据库读取输入。当前 session 的成功 add 是触发条件，不是把读取范围
强制缩窄为当前 session；同一 user scope 内以前未处理的 session backlog 也可以在本次运行中被处理。

Implicit feedback 失败不得回退已经成功持久化的会话经验；失败必须写入 typed finalization result 和 JSONL trace，
并保留可重试状态。不得因为部分 action 失败而把本轮报告为全部成功。

### 4.4 Dreaming 水位与 pending 状态

增加一个 application-owned 持久化状态存储，默认位于 memory `data_root` 下，不依赖进程内变量。状态按
`project_id + user_id` 隔离，至少保存：

```json
{
  "schema_version": 1,
  "scope": "<project_id>:<user_id>",
  "new_active_memory_count": 0,
  "pending": false,
  "last_successful_watermark": null,
  "last_attempt_at": null,
  "last_success_at": null,
  "last_error": null
}
```

状态写入必须使用临时文件、`fsync` 和原子替换；并发 session finalization 必须使用同一 scope 的互斥锁或等价原子协议。

有效新增 memory 的计数条件必须全部满足：

```text
来源为普通 session add_vanilla
AND add operation == "add"
AND memory_id 为 raw memory ID
AND get_raw 回读存在
AND status == "active"
```

以下不计数：

- `update`、`delete`、`noop`；
- feedback 生成的新版本；
- dreaming 生成的维护记录；
- 重复、失败或无法回读的 add；
- schema 聚合视图。

达到阈值后先原子设置 `pending=true`，再运行 dreaming。Dreaming 真实成功后才推进 watermark 并扣除已消费计数；
运行期间新到达的 memory 必须保留在下一批计数中。失败、超时或进程退出时保持 pending，下一次 HomeMaster 启动或
session finalization 重试。

V2.6 默认在触发阈值的 session finalization 末尾使用当前进程同步执行一次 dreaming，并设置硬超时。当前实现不创建
每日 cron，也不要求 HomeMaster 主进程常驻。

### 4.5 Dreaming provider 兼容与完成判据

V2.6 必须修复或适配当前 relation-detection 请求只有 system message、Mimo/Anthropic 路由拒绝的问题。修复必须位于
MindMemOS 合理的 prompt/transport 边界，不能在 HomeMaster 中伪造成功结果。

以下条件必须同时满足，dreaming 才能清除 pending：

1. 调用返回成功状态；
2. pipeline summary 不含未处理错误；
3. 每个实际 action 都有成功状态；
4. 对每个 target/result raw memory 独立回读，确认预期 active/archived/lineage 终态；
5. 相关 activity/add records 的 consolidated 状态与本次结果一致。

`status="ok"`、`actions=0`、日志中打印完成或内部 trace 走到末尾，均不能单独证明 dreaming 成功。若本批数据没有形成
可处理 scope/cluster，必须返回明确的 typed `no_action`，并定义该批是否推进 watermark；禁止把 provider 异常伪装成
`no_action`。

## 5. 固定执行顺序

### 5.1 会话运行中

```text
明确目标 + 完整新内容
  -> direct update

明确删除意图 + 唯一目标
  -> direct delete

存在可执行反馈，但具体 mutation 未确定
  -> explicit feedback
```

### 5.2 会话结束

```text
SessionFinalizer 收集 canonical trace
  -> add_vanilla（现有能力）
  -> raw memory 回读
  -> implicit feedback
  -> feedback action 逐条回读
  -> 统计本次有效普通新增 memory
  -> 原子更新水位
  -> count >= 8 或已有 pending 时运行 dreaming
  -> dreaming action 逐条回读
  -> 持久化 finalization receipt
  -> close EmbeddedMindMemOS
```

任一步骤失败都必须保留前面已经确认的外部终态，不得用重跑整个 finalizer 造成重复 mutation。各阶段需要独立、幂等的
job 状态或请求 ID。

## 6. 非目标

V2.6 明确不做：

- Skill 注册、版本管理、Skill Evolution；
- Kafka、feedback async worker、dreaming async worker；
- 每日或固定时钟 cron；
- 把纯正向称赞转换为强化分数；
- Task outcome 和 Trajectory Score 的采集、评分或 memory 学习；
- 一次失败即自动删除或改写经验；
- 在证据不足时由 LLM 猜测正确事实；
- 重写现有 add/search/get/update/delete；
- 第二套 HomeMaster 自研 feedback/dreaming 算法；
- 物理删除 archived memory。

## 7. 可观测性

新增结构化 JSONL 事件，字段受既有 allowlist、tenant/session/run ownership 和文本保真规则约束：

```text
memory.feedback.explicit.started/completed/failed
memory.feedback.implicit.started/completed/failed
memory.dreaming.threshold_reached
memory.dreaming.started/completed/no_action/failed
```

事件至少记录：

```text
request_id
project_id
user_id
session_id（适用时）
duration_ms
action_count
per_action status
raw target/result memory IDs
threshold/count/watermark（dreaming）
typed error
```

日志是取证旁路，不是成功判据。成功必须来自 pipeline 返回码和 raw memory 外部终态回读。

## 8. 验收标准

### 8.1 Direct update 回归

1. 无 `record_json` 的 Vanilla raw ID 和新 content 调用原生 `update()`，同一 ID 保持 active 且正文准确；
2. 有合法 `record_json` 的 raw ID 和完整 replacement record 创建新 active ID，旧 ID archived；
3. structured 新记录的 content、record_json、provenance、memory/entity vectors 和图关系同步更新；
4. Neo4j 存在 `new-[:DERIVED_FROM]->old`，`mindmemos_history` 从任一端返回两个真实版本；
5. `record_json` 存在但损坏时零 mutation；
6. unrelated memory 不发生变化。

### 8.2 Explicit feedback

1. 用真实 MindMemOS pipeline 写入旧 memory“用户使用 conda”；
2. 让 provider 在包含多轮对话或“压缩摘要 + 近期对话”的有效上下文中调用 explicit feedback“不是 conda，用户使用
   uv”；断言 feedback 收到同一份 frozen messages 和实际可见、带真实 ID 的旧 raw memory；
3. pipeline 返回 update action；
4. 旧 memory 逐条回读为 archived；
5. 新 memory 逐条回读为 active、内容为“用户使用 uv”；
6. Neo4j/lineage 真实存在 `DERIVED_FROM`；
7. 传 schema 聚合 ID 时 fail closed，不产生 mutation；
8. 只有“记错了”且无替代内容时，不生成猜测性 update。

### 8.3 Implicit feedback

1. 通过真实 `SessionFinalizer -> add_vanilla` 写入含 messages 和 memory payloads 的 `add_record_v1`；
2. 通过真实 `EmbeddedMindMemOS.search()` 写入含 query 和 recalled raw memories 的 `search_record_v1`；
3. add/search records 使用相同稳定 session ID，另准备一个不同 session ID 验证 rounds 不会混合；
4. 调用 `feedback_implicit()` 时只传 `MemoryRequestContext`，不直接传 messages、memories 或 feedback text；
5. 会话包含一个 task-temporary correction、一个 scenario-specific rule 和一个 long-term correction；
6. implicit collector 从数据库重建正确 rounds 和候选 memories，detector 分别产生正确类别，planner 的 action 与类别边界
   一致；
7. 每个 action 独立验证最终 raw memory；
8. 成功处理的 add records 标记为 `feedback_processed`；
9. action 失败时返回非成功 receipt，不以全局 `status="ok"` 掩盖 per-action failure；
10. 纯正向称赞不产生 memory mutation。

### 8.4 Dreaming 阈值和重启

1. 同一 scope 新增 7 条有效 raw memory，不触发 dreaming；
2. 第 8 条成功回读后恰好触发一次；
3. 另一 scope 的计数不能帮助当前 scope 达到阈值；
4. feedback/dreaming 生成的 memory 不参与计数；
5. dreaming 失败后 pending 持久存在，重启进程后能重试；
6. dreaming 成功后只消费实际纳入本批的水位，运行期间新增记录仍留到下一批；
7. 并发跨过阈值时只有一个 dreaming owner；
8. 不创建每日 cron job，不要求主进程常驻。

### 8.5 Dreaming 真环境黑盒门

在真实 LLM、embedding、Qdrant Local 和 Neo4j 上准备至少三组独立数据：

1. 明确重复组；
2. 明确冲突组；
3. 无需动作组。

逐组断言：

- pipeline 返回码；
- scopes、clusters、actions 和错误摘要；
- 每个输入 raw memory 的最终状态；
- 每个输出 raw memory 的内容和 lineage；
- consolidated 状态；
- 无关 scope 数据不变化。

不得用三组中的最佳结果、全局 any 或聚合 min/max 代替逐组断言。

## 9. 预计修改文件

实施时预计涉及但不限于：

- `src/homemaster/memory/mindmemos_runtime.py`；
- `src/homemaster/experience/finalizer.py`；
- `src/homemaster/experience/` 下新增 dreaming state/job owner；
- memory tool registry、canonical tool schema 和 composition wiring；
- `tests/homemaster/memory/` 的 feedback/dreaming pipeline 测试；
- `tests/homemaster/experience/test_finalizer.py`；
- application runtime 工具接线和 provider-visible result 测试；
- `docs/architecture/memory-system.md`；
- `docs/memory-user-guide.md`；
- `README.md`；
- `CHANGELOG.md`；
- 如发现新的非显而易见失败模式，更新 `docs/pitfalls.md` 和 `CLAUDE.md` 正向规则。

实际实施前应另写精确 implementation plan，列出逐文件变更、失败测试、真实环境门和回滚点。本 spec 不授权顺手重构
现有 memory architecture，也不允许为了 feedback/dreaming 引入多个运行 mode。

## 10. Definition of Done

V2.6 只有同时满足以下条件才完成：

1. explicit/implicit feedback 和 dreaming 均通过 `EmbeddedMindMemOS` 薄包装调用原生 pipeline；
2. direct update/delete 的既有语义和回归保持通过；
3. implicit feedback 只在 session-end add 完成后触发；
4. dreaming 只由“每 8 条有效普通 raw memory”水位触发，不存在每日定时触发；
5. 重启、失败和并发不会丢失或重复消费 dreaming pending 水位；
6. raw ID 与 schema aggregate ID 不混用；
7. 每个 feedback/dreaming action 都有独立返回码和 raw memory 外部终态验证；
8. 真实环境下 dreaming 不再出现 provider 被拒却报告成功的假阳性；
9. 单元、集成、安装产物和真实外部终态门全部通过；
10. README、用户指南、架构文档和 CHANGELOG 与最终代码同源更新。
