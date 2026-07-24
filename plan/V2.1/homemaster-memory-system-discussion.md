# HomeMaster V2.1 记忆系统讨论记录

> 状态：核心分层已确认，细节讨论中，尚未授权实施
> 更新日期：2026-07-24
> 目标仓库：`/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`
> 相关参考项目：`/hpc2hdd/home/wyuan140/weilin_workspace/mem0`

## 1. 文档目的

本文档记录 HomeMaster V2.1 记忆系统讨论中已经确认的方向、现有代码调查结果、
仍待确认的设计问题和下一轮讨论入口，避免后续会话重新梳理上下文。

本文档是高频更新的讨论活文档，不是正式设计规格。标为“候选”或“待确认”的内容
不得作为实施依据。

## 2. 当前范围

本轮只讨论 HomeMaster 的通用记忆系统。Coworker 的网站 DOM 不保存为长期记忆：
Agent 每次访问页面时读取当前 Live DOM，并只在当前页面快照内使用临时元素引用。
因此 DOM 获取与点击属于浏览器环境观测和工具能力，不属于本记忆系统。

第一阶段部署范围已经确认：

- 单用户。
- 单 Agent。
- 所有会话属于同一个 HomeMaster 身份。
- 当前不实现多租户、多用户或多 Agent 隔离。

## 3. 已确认的记忆分层

### 3.1 `SOUL.md`：人格记忆

`SOUL.md` 保存 HomeMaster 稳定的人格信息，例如：

- 人格与身份。
- 价值观和长期行为原则。
- 表达风格。
- 与用户相处的基本方式。
- 不随单次任务变化的行为边界。

已确认要求：

- `SOUL.md` 每轮完整、确定性地进入模型上下文。
- `SOUL.md` 不依赖语义检索才能被模型看到。
- `SOUL.md` 不属于物体位置或事件流水。

尚未确认：

- `SOUL.md` 是否只允许用户维护。
- Agent 是否可以通过受控工具提出或执行人格修改。
- 人格修改是否需要用户确认。

### 3.2 `MEMORY.md`：活跃事件记忆

用户明确把 `MEMORY.md` 定义为事件记忆，而不是用户画像或结构化世界状态。

事件记忆回答：

- 最近发生了什么。
- 何时发生。
- 谁参与了。
- 做出了什么决定。
- 得到了什么结果。
- 哪些经历会影响后续行为。

已确认要求：

- `MEMORY.md` 每轮完整、确定性地进入模型上下文。
- Agent 使用工具写事件记忆，不依赖后台自动抽取。
- 第一版事件继续保存在 `MEMORY.md`，暂不实现自动归档。
- 如果后续文件实际变大，再单独设计旧事件归档，而不在第一版提前实现。

事件示例：

```markdown
## 2026-07-24

- 用户决定 HomeMaster 记忆系统第一阶段采用单用户、单 Agent。
- 用户确认 `SOUL.md` 和 `MEMORY.md` 每轮固定进入上下文。
- 用户把 `MEMORY.md` 定义为事件记忆。
- 用户决定第一版 mem0 只保存环境中的物品位置和状态。
- 用户决定 Coworker 不保存历史 DOM，每次使用当前 Live DOM。
```

尚未确认：

- 事件进入 `MEMORY.md` 的准入标准。
- Agent 提交事件后，工具是否允许模型直接编辑 Markdown，或只接受结构化参数并由系统渲染 Markdown。
- 事件更正、撤回和冲突如何表达。

### 3.3 mem0：物品位置与状态记忆

第一版 mem0 只保存 ALFWorld 或真实环境中的：

- 物品身份。
- 物品最后确认的位置。
- 少量与定位有关的当前状态。
- 最近确认时间和记忆可信状态。

mem0 不是“把所有信息写成一句自然语言后只做向量搜索”。调用层必须提供类型化 schema、
metadata、过滤条件和稳定实体标识。

第一版只需要一个逻辑类型：

```text
object_location
```

物体位置示例：

```json
{
  "memory_id": "object:home_main:cup_blue_01",
  "memory": "蓝色杯子位于客厅茶几上",
  "metadata": {
    "memory_type": "object_location",
    "environment_id": "home_main",
    "object_id": "cup_blue_01",
    "location_id": "living_room:coffee_table",
    "observed_at": "2026-07-24T11:20:00+08:00",
    "confidence": 0.97,
    "belief_state": "confirmed",
    "source": "robot_observation"
  }
}
```

## 4. 写入原则

### 4.1 Agent 显式调用工具

已经确认不采用“每轮结束后后台自动猜测应该记住什么”的方案。

预期数据流：

```text
Agent 判断需要记忆
  -> 显式调用记忆工具
  -> 工具校验结构、权限、证据和去重
  -> 工具写入目标存储
  -> 工具重新读取外部终态
  -> 工具返回实际写入结果
```

“Agent 调用了写入工具”和“记忆已经持久化”必须分开验证。工具成功必须以重新读取后的
真实终态为准，不能只返回模型提交的参数。

### 4.2 不给模型任意底层写权限

当前推荐但尚未最终确认：

- Agent 不直接调用 mem0 的任意文本写入接口。
- Agent 不直接自由改写整个 `MEMORY.md`。
- Agent 调用按领域划分的类型化工具。
- 工具拥有稳定 ID 解析、schema 校验、原子写入、历史记录和终态验证。

候选工具接口：

```text
event_memory_write
event_memory_correct

memory_find_object
memory_upsert_object
memory_update_location
memory_mark_location_unknown
memory_get_object_history
```

这些名称只是讨论用占位符，尚未核对或锁定为公开 API。

## 5. 物体位置变化

用户已经明确：物品位置发生变化时，模型应当更新记忆。

当前共识是使用稳定实体 ID 更新同一条“当前位置”记忆，而不是每次移动都新增一条互相冲突的
当前状态。

示例：

```text
environment_id = home_main
object_id      = cup_blue_01
memory_id      = object:home_main:cup_blue_01

旧值：厨房餐桌
新观察：客厅茶几
动作：更新同一个 memory_id
当前值：客厅茶几
历史：保留从厨房餐桌移动到客厅茶几的更新记录
```

预期更新流程：

```text
Agent 调用 memory_update_location
  -> 工具按 environment_id + object_id 确定性解析唯一 memory_id
  -> 工具检查观察来源、时间、置信度和证据
  -> 工具调用 mem0 update
  -> 工具重新读取该 memory_id
  -> 工具逐字段验证新位置、时间和状态
  -> 返回真实更新终态
```

重要约束：

- 稳定 `memory_id` 由工具生成和维护，不能由模型每轮临时选择。
- 同一物体的定位必须确定性，不能依赖向量搜索结果的第一项。
- 语义检索用于“可能在问哪个对象”，不能单独决定要更新哪个对象。
- 未确认的推测不能覆盖已确认位置。
- 观察时间旧于当前记录时，默认不能覆盖较新的当前状态。
- 无法确认位置时应转为 `unknown`、`stale` 或 `contradicted` 等显式状态，不能编造位置。
- 更新失败或返回码异常时必须报告失败或结果不确定，不能对模型宣称已经记住。

## 6. 读取与上下文组装

每轮基础上下文候选顺序：

```text
系统与安全指令
  -> SOUL.md 全文
  -> MEMORY.md 全文
  -> 当前任务和会话
  -> 按当前任务检索得到的 mem0 记忆
```

不同问题采用不同读取路径：

- 人格和稳定行为：直接使用 `SOUL.md`。
- 最近发生的事件和重要经历：直接使用 `MEMORY.md`。
- “某类物品通常在哪里”：可以使用语义检索发现候选。
- “准确物体现在在哪里”：按 `environment_id + object_id + memory_type` 确定性读取当前记录。

mem0 检索结果属于参考上下文，不自动成为执行授权或当前事实。涉及物理动作时，仍需用环境观察
验证对象身份和当前状态。

## 7. 第一版明确不做的能力

为了保持第一版简单，当前明确不实现：

- `MEMORY.md` 旧事件自动归档到 mem0。
- 复杂的重要性评分、时间淘汰和 token 淘汰。
- mem0 中的事件记忆、用户画像和网站 DOM。
- 网站历史 DOM、DOM 指纹和网站操作地图。
- Canvas、远程桌面或桌面应用的视觉操作记忆。

如果 `MEMORY.md` 后续实际增长到影响上下文，再基于真实使用数据设计归档，不提前建设。

Coworker 后续只需要解决实时观测：

```text
打开页面
  -> 获取当前 Live DOM 的可操作元素
  -> 为当前快照生成临时 element_ref
  -> Agent 选择 element_ref
  -> 点击前重新验证元素仍存在、唯一、可见、可用
  -> 页面变化后重新观察
```

临时 `element_ref` 页面变化后立即失效，不写入 `SOUL.md`、`MEMORY.md` 或 mem0。

## 8. 对现有 HomeMaster 代码的调查结果

### 8.1 已有对象记忆实现

HomeMaster 已有以下相关模块：

```text
src/homemaster/memory/runtime_store.py
src/homemaster/memory/retrieval.py
src/homemaster/memory/index.py
src/homemaster/domain/tools.py
```

已有能力包括：

- 对象记忆 JSON。
- BM25、embedding 和 metadata 融合检索代码。
- `memory_retriever` 和 `memory_writer` 工具。
- 运行期对象记忆 overlay。
- 对 stale、contradicted 等 belief state 的部分表达。

### 8.2 当前实现不能直接作为 V2.1 最终方案

调查中发现的边界问题：

- `domain/tools.py` 中的实际 `memory_retriever` 目前仍是简单关键词匹配，没有接入已有的完整 RAG。
- 一部分代码使用顶层 `objects`，另一部分 RAG 代码使用 `object_memory`，schema 尚未统一。
- `memory_writer` 只更新少数字段。
- `memory_writer` 对持久化异常采用 best-effort 吞错，但仍可能返回 `committed=true`，不能证明真实落盘。
- 当前 runtime overlay 按 run 写入，不等于跨会话长期记忆。
- 尚未发现 `SOUL.md` 和 `MEMORY.md` 的固定上下文注入路径。

V2.1 设计必须明确复用、替换或迁移这些旧能力，不能另建一套平行且互相冲突的记忆真理源。

### 8.3 mem0 当前可用能力

本地参考仓库的 mem0 Python 包版本为 `2.0.13`，代码中存在：

- `add`
- `search`
- `get`
- `get_all`
- `update`
- `delete`
- `history`
- `user_id`、`agent_id`、`run_id` 和 metadata filters
- 向量、关键词和 entity 等检索路径

因此“稳定 memory ID + 显式 update + history”在当前代码层面有可参考接口。

但外部 API、配置项和运行时行为在实施前仍需真环境核对，当前讨论不能仅凭符号存在就断言可用。
尤其需要验证：

- 使用目标 LLM 和 embedding provider 时能否正常初始化。
- 实际 vector store 是否支持所需的 metadata filters 和 keyword search。
- `update` 后 `get`、`search` 和 `history` 的真实外部终态。
- 并发写入、进程重启和异常恢复。
- mem0 版本与 HomeMaster Python 依赖的兼容性。

## 9. 暂定整体模型

```text
                     每轮固定上下文
                +----------------------+
                | SOUL.md              |
                | 活跃 MEMORY.md       |
                +----------+-----------+
                           |
                           v
用户请求 ----------> Context Assembler ----------> Agent
                           ^                         |
                           |                         | 显式工具调用
                           |                         v
                    mem0 按需检索 <---------- Memory Tools
                           ^                         |
                           |                         |
                 +---------+-------------------------+
                 | object_location 物品位置与状态     |
                 +-----------------------------------+
```

这张图表达的是当前讨论方向，不是最终组件或接口承诺。

## 10. 尚未解决的核心问题

下一轮建议按顺序逐个讨论：

1. `SOUL.md` 的维护权限和修改流程。
2. `MEMORY.md` 允许写入哪些事件。
3. `MEMORY.md` 写入、更正和删除的最小工具 schema。
4. mem0 物品记录的最小 schema 与稳定 ID 规则。
5. 同类多个物品的实例识别与别名规则。
6. 哪些观察允许 Agent 更新物品位置。
7. mem0 查询、更新和回读验证的最小工具接口。
8. 现有 HomeMaster 对象记忆向 V2.1 的最小迁移方式。
9. `SOUL.md`、`MEMORY.md` 和 mem0 检索结果在上下文中的顺序。

## 11. 下一轮讨论入口

核心分层已经确认：

```text
SOUL.md   = 人格，固定进入上下文
MEMORY.md = 事件，固定进入上下文
mem0      = ALFWorld 或真实环境中的物品位置与状态
Coworker  = 每次读取当前 Live DOM，不保存 DOM 记忆
```

下一轮从以下问题继续：

> 第一版 `SOUL.md` 只允许用户维护，还是允许 Agent 通过受控工具修改？
