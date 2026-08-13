# HomeMaster V2.5 经验召回与使用 Spec

Date: 2026-08-13
Status: 初步设计，核心方向已确认

---

## 一、目标

让 HomeMaster Agent 在新 Session 开始和上下文压缩后主动召回历史经验，并把召回结果作为正常工具结果放入模型上下文，供模型后续规划、重新规划和动作判断使用。

本阶段只负责打通“召回时机 -> Agent 生成 Query -> MindMemOS Search -> 结果进入上下文”这条链路。

本阶段不替模型判断经验是否有用，不限制模型如何继续搜索，也不要求 Runtime 证明某个后续动作一定由某条记忆导致。

## 二、设计原则

### 2.1 Runtime 只负责确定的召回时机

Runtime 不做“用户是否换了任务”“一次失败是否需要重新规划”等语义判断。

系统只在两个确定时机要求召回：

1. 新 Session 收到第一条用户消息时；
2. Compact 完成后，收到第一条新的用户消息时。

除此之外，是否召回全部由 Agent 自主决定。

### 2.2 Query 由 Agent 生成

Runtime 只告诉 Agent 为什么当前需要召回，不替 Agent 拼接或生成检索词。

Agent 根据以下模型可见信息自主生成 Query：

- 当前用户消息；
- 当前 TaskState；
- Compact Summary 和最近对话；
- 当前环境、工具结果和任务目标。

### 2.3 给 Agent 保留搜索自由

Runtime 不设置以下限制：

- 不限制一个 Session 内的搜索次数；
- 不禁止重复 Query；
- 不因搜索无结果而禁止继续搜索；
- 不规定搜索失败后的固定退出路径；
- 不在失败或 Replan 时强制搜索。

搜索返回为空、Query 不理想或工具调用失败时，Agent 可以自行修改 Query、再次搜索、改用其他方法或向用户求助。

### 2.4 Compact 与长期记忆写入分离

Compact 只负责压缩上下文。

本阶段不在 Compact 前自动调用 `mindmemos_add`，不自动把 Compact 前的执行轨迹写入 MindMemOS，也不复用 Session 结束时的经验沉淀逻辑。

Session 结束后的自动经验沉淀保持现状，与本阶段召回机制相互独立。Agent 在正常执行过程中仍可自主使用已有记忆工具，但 Compact 本身不触发长期记忆写入。

## 三、核心链路

### 3.1 新 Session 首次召回

```text
创建新 Session
-> recall_pending = true
-> 用户发送第一条消息
-> Runtime 向 Agent 提供 Recall Trigger
-> Agent 根据当前消息自主生成 Query
-> Agent 调用 mindmemos_search
-> MindMemOS 执行普通 Search
-> 返回 Top-3
-> 结果作为 ToolResultMessage 写入 session.messages
-> recall_pending = false
-> 下一轮模型基于当前上下文继续规划或执行
```

新 Session 的强制召回发生在第一条用户消息到达之后。这样 Agent 能看到真实用户目标，并据此生成有意义的 Query。

### 3.2 Compact 后首次召回

```text
ContextAssembler 完成 Compact
-> recall_pending = true
-> 当前 Compact 流程结束
-> 用户发送 Compact 后的第一条新消息
-> Runtime 向 Agent 提供 Recall Trigger
-> Agent 自主生成 Query 并调用 mindmemos_search
-> 搜索结果进入 session.messages
-> recall_pending = false
-> 下一轮恢复正常执行
```

Compact 完成后不立即发起额外模型调用。召回等待下一条真实用户消息，以便 Agent 结合 Compact Summary、当前任务状态和新消息生成 Query。

### 3.3 Agent 自主召回

当 `recall_pending = false` 时，`mindmemos_search` 仍作为普通工具提供给 Agent。

例如以下情况，Agent 可以自行决定是否召回：

- 用户在同一 Session 中切换任务；
- 不知道下一步怎么做；
- 工具选择困难；
- 环境与预期不一致；
- 动作失败或重复失败；
- 需要寻找替代路径；
- Agent 判断历史经验可能减少试错。

这些只是模型的判断依据，不是 Runtime 的强制触发规则。

## 四、职责划分

### 4.1 HomeMaster Runtime

负责：

- 在新 Session 创建时设置 `recall_pending = true`；
- 在 Compact 实际完成后设置 `recall_pending = true`；
- 在待召回的用户轮提供 Recall Trigger；
- 在一次要求的召回调用完成后清除 `recall_pending`；
- 保存和恢复 Session 级召回状态。

Runtime 不负责：

- 理解用户任务并生成 Query；
- 判断用户是否在同一 Session 中切换了任务；
- 判断一次失败是否应该召回；
- 规定 Agent 后续是否继续搜索；
- 自动写入长期记忆。

### 4.2 HomeMaster Agent

负责：

- 理解当前用户目标和上下文；
- 自主生成适合当前情况的 Query；
- 在非强制时机自主决定是否搜索；
- 阅读 ToolResult 中的召回结果；
- 自主决定如何规划、重新规划或继续执行。

### 4.3 MindMemOS

负责：

- 执行单轮普通 Search；
- 返回最多 Top-3 结果；
- 不在本阶段启用原生 Agentic Search；
- 不在本阶段增加 Rerank。

第一版沿用现有 Vanilla Search。Agentic Search 可在后续根据真实召回效果单独评估。

### 4.4 Agent Loop

负责：

- 执行 `mindmemos_search` 工具调用；
- 把真实搜索结果写成 `ToolResultMessage`；
- 将该消息追加到 `session.messages`；
- 在下一次模型请求中携带该工具结果。

不新增 Recall Cache。召回内容以正常会话消息作为唯一模型可见来源。

## 五、召回状态

第一版只新增一个 Session 级状态：

```text
recall_pending: bool
```

状态转换：

```text
新建 Session                 -> true
要求的 mindmemos_search 完成  -> false
Compact 实际完成             -> true
普通后续对话                 -> 保持不变
Session resume               -> 恢复持久化值
/new                         -> 新 Session，重新为 true
```

`recall_pending` 属于 `SessionRuntime`，并进入 Session snapshot。进程重启或 Session resume 后必须恢复原值，避免丢失 Compact 后尚未执行的首次召回。

具体字段序列化位置、失败调用是否立即清除状态以及协议纠正次数属于实施细节，不在本 Spec 中提前锁死；实现时应保持不限制 Agent 后续搜索自由这一原则。

## 六、Recall Trigger

Recall Trigger 是临时 Runtime Context，只在 `recall_pending = true` 且收到用户消息时出现。

它只表达召回原因，例如：

```text
Memory recall is required because this is the first user message in a new session.
Generate a search query from the current user request and context, then call mindmemos_search.
```

或：

```text
Memory recall is required because context was compacted before this user message.
Use the compact summary, current task state, and user request to generate a query, then call mindmemos_search.
```

Recall Trigger 不包含 Runtime 生成的 Query，不预选记忆类型，也不替 Agent 总结当前任务。

## 七、检索方案

第一版采用：

```text
Agent 自主生成 Query
-> mindmemos_search
-> MindMemOS Vanilla Search
-> Top-K = 3
-> 不启用 Rerank
-> 不启用 Agentic Search
```

Top-3 控制单次返回到模型上下文的结果规模，不限制 Agent 后续再次调用搜索。

MindMemOS 原生 Agentic Search 会在首次搜索后由内部模型判断信息是否充分，并可能生成补充 Query。该能力留作后续增强，本阶段保持 Query 决策权在 HomeMaster Agent。

## 八、上下文结构

召回机制接入后的模型上下文保持现有分层：

```text
System Prompt
+ SOUL / USER / MEMORY

Runtime Context
|- TaskState
|- Recall Trigger（仅待召回用户轮出现）
`- Available Skills

Conversation
|- Compact Summary
|- 最近对话
|- 工具调用
`- 工具真实结果（包括 mindmemos_search ToolResult）

Tools
`- 通过模型 API 的 tools 参数独立传入
```

本阶段不因为召回机制自动改写 Compact Summary，也不把召回结果复制成第二份 Runtime Context。

## 九、不做

本阶段明确不做：

- 不在每次失败后强制召回；
- 不在 Replan 前设置强制召回屏障；
- 不识别同一 Session 内的“新任务”；
- 不限制搜索总次数；
- 不禁止重复 Query；
- 不实现 Recall Cache；
- 不启用 MindMemOS Agentic Search；
- 不启用 Rerank；
- 不在 Compact 前自动写长期记忆；
- 不要求 Runtime 评判模型是否真正采纳某条经验；
- 不改动 Session 结束后的自动经验沉淀逻辑。

## 十、验收边界

本阶段完成时至少证明：

1. 新 Session 的第一条用户消息会得到 Recall Trigger，Agent 能基于该消息自主生成 Query 并调用 `mindmemos_search`；
2. 普通后续用户消息不会因为 Runtime 固定规则再次强制召回，但 Agent 仍可自主搜索；
3. Compact 完成后不会自动写长期记忆；
4. Compact 后的第一条新用户消息会触发召回；
5. `mindmemos_search` 的真实 Top-3 结果作为模型可见 ToolResult 进入下一次 Provider 请求；
6. `recall_pending` 随 Session snapshot 持久化，并在 resume 后保持准确；
7. `/new` 创建的新 Session 重新进入首次召回状态；
8. 不因本功能限制 Agent 后续再次搜索、修改 Query 或选择其他处理方式。

验收只证明召回结果真实进入模型上下文。模型如何使用经验属于 Agent 决策能力，不在本阶段建立额外的因果判定框架。

## 十一、后续增强方向

以下能力根据真实运行数据再决定是否增加：

- MindMemOS Agentic Search；
- Rerank；
- Query 质量分析；
- 搜索成本与循环治理；
- 更细粒度的召回触发信号；
- 召回效果评估和对照实验。

这些增强不得反向改变本阶段的核心职责边界：Runtime 提供确定时机，Agent 理解任务并决定如何搜索，MindMemOS 执行检索，ToolResult 进入正常上下文。
