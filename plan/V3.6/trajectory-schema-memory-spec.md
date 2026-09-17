# V3.6 家庭任务可行动轨迹 Schema Memory Spec

## 0. 文档状态
- 版本：V3.6
- 日期：2026-09-17
- 状态：方案已确认，实施计划已编写，尚未实现
- 适用仓库：`/home/haodong2/weilin/red_bird/Homemaster`

本文档规定把 session finalizer 从 vanilla add 改造成 schema add 的目标、数据契约和验收标准。不重组整个 memory 系统，也不新增独立的完整轨迹存储。

## 1. 背景与问题
当前 `SessionFinalizer` 将 session 事件渲染为对话消息，再调用 MindMemOS vanilla add。该方式能够保存经历，但对家庭任务的直接帮助有限：召回结果难以直接用于寻找物体、选择位置、复用成功步骤或减少工具调用。

原始 trace 已经包含自然语言、工具调用、工具参数和工具返回，不需要在 finalizer 中复制一份完整 episode。V3.6 由 Homemaster 提供可靠、确定性的任务轨迹输入；MindMemOS 以一次 episode-level schema-add ingress 接收它。对同一个完整 episode，三个固定领域 extractor 分别尝试自己的 schema：`object_location`、`search_observation` 和 `task_procedure`。每个 extractor 都由 LLM 根据完整证据判断本类型是否存在并抽取，未识别到就返回 empty；三类可以同时成功，也可以全部为空。

## 2. 目标与非目标
### 2.1 必须达成
1. 从 trace 提供足够上下文，使 schema add 能抽取物体位置、搜索结果和可复用任务步骤。
2. 位置记忆支持物体与位置/容器的语义关系，并支持有真实来源的可选坐标。
3. 过程记忆保留工具调用顺序、真实参数、结果和失败后的修正步骤。
4. 每条写入记忆包含一句基于证据的自然语言 `summary`，方便模型理解和使用。
5. 每条记忆可回溯到 `session_id`、事件 ID 和观测时间。
6. 保持 finalizer 的幂等、失败重试和 job phase 语义。
7. 召回结果能够改变后续家庭任务的搜索位置、动作顺序或工具参数。
8. 复用 MindMemOS 现有的 schema validation、planner、merge、writer 和存储；V3.6 只增加 episode 编排和三个领域 schema 的配置/适配。

### 2.2 明确不做
- 不额外建立完整 episode/archive 数据库；原始 trace 仍是事实来源。
- 不把所有 debug 日志写入普通 recall memory。
- 不强行推断完整 `state_before/state_after`。
- 不把一次观察直接升级为永久事实或“通常位置”。
- 不由 Homemaster 复制 MindMemOS 的 extractor、planner、merge 或 writer。
- 不新增一个“从三类中选一类”的语义 router；类型判断由三个固定 schema extractor 各自完成。
- 不在 V3.6 同时重写 recall、dreaming 或其他 memory pipeline。

## 3. 核心分工

### 3.1 Homemaster 负责
Homemaster 不提前决定每条记忆最终属于哪一种 schema，而负责：

- 按稳定顺序规范化 trace 事件；
- 保留自然语言、工具调用、工具参数和工具返回；
- 用 `tool_call_id` 等稳定关联把调用和结果配对；
- 提供 session/task 上下文；
- 保留工具真实返回的 object/location ID；
- 只转发工具输入或工具返回中真实存在的坐标；
- 提供 `session_id`、事件 ID、时间和 trace hash 等 provenance；
- 在 schema-add 输入中明确家庭任务领域目标和抽取要求。

### 3.2 MindMemOS schema add 负责
MindMemOS schema-add episode ingress 负责：

- 以同一个完整 episode 作为输入，调用三个固定 schema extractor：每个 extractor 只面对自己的 schema，并返回零条或多条候选；
- 由 LLM 在各自 extractor 内判断本类型是否有证据，不要求一个 LLM 在三类之间做选择；
- 各 extractor 的候选分别校验，合并后复用一次现有 schema-add 的 planner、merge 和 writer；
- 为各类记忆生成基于证据的自然语言 summary；
- 聚合各类型 pipeline 的状态、数量和错误并返回 per-type 结果；
- 处理同一物体的新位置、冲突位置和历史记忆合并；
- 将最终 schema memory 写入实际 memory store。

这里的新增组件应是一个很薄的 episode orchestrator，而不是新的 MindMemOS memory pipeline：

```text
one episode ingress
  -> object_location extractor    (LLM, fixed schema)
  -> search_observation extractor (LLM, fixed schema)
  -> task_procedure extractor     (LLM, fixed schema)
  -> reuse schema_add validation / planner / merge / writer
  -> aggregate per-type result
```

三个 extractor 可以并行运行；它们不是互斥分类器。Homemaster 不根据关键词、工具名或规则提前判断类型，也不负责从自然语言总结位置、搜索结果或任务步骤。

实现前必须以当前 checkout 的可用接口、DTO 和配置为准，不能猜测 API 名称或签名。

## 4. 抽取目标 schema

以下是给 MindMemOS schema add 的**领域抽取目标**，不是要求 Homemaster 手动拼出最终对象。三类顶层目标足够覆盖 V3.6。

### 4.1 `object_location`
描述物体被观察到、找到或放置后所在的位置。

```json
{
  "type":"object_location",
  "summary":"最近一次搜索中，药品在浴室柜内偏左后方被找到。",
  "object":{"id":"medicine","label":"药品"},
  "relation":"inside",
  "location":{"id":"bathroom_cabinet","label":"浴室柜"},
  "observation_kind":"found",
  "position":{
    "frame":"bathroom_cabinet",
    "coordinates":{"x":0.32,"y":0.68,"z":0.15},
    "unit":"normalized",
    "region":"left_back",
    "source":"tool_result"
  },
  "source":{"session_id":"s1","event_ids":["evt-12","evt-13"]},
  "observed_at":"2026-09-13T10:00:00Z",
  "confidence":0.92
}
```

`relation` 至少覆盖 `inside`、`on`、`near`、`left_of`、`right_of` 等语义关系；`observation_kind` 至少覆盖 `observed`、`found`、`placed`。

### 4.2 `search_observation`
描述针对目标物体的一次有明确结果的搜索观察。

```json
{
  "type":"search_observation",
  "summary":"本次任务已检查客厅桌面，但没有找到药品。",
  "object":"medicine",
  "location":"living_room_table",
  "result":"not_found",
  "search_action":{"tool":"inspect","arguments":{"location":"living_room_table"}},
  "source":{"session_id":"s1","event_ids":["evt-02","evt-03"]},
  "observed_at":"2026-09-13T09:59:00Z"
}
```

`result` 至少覆盖 `found`、`not_found`、`blocked`、`error`。只有工具返回或明确自然语言确认结果时才生成；不能把缺少证据当成 `not_found`。

### 4.3 `task_procedure`
描述一次任务轨迹中的实际操作顺序、结果和可复用经验，不保存无关 debug 事件。它不只表示成功 SOP：失败、部分成功和最终结果未知的轨迹也可以被记录，但这些轨迹默认不能直接作为可执行 SOP。

```json
{
  "type":"task_procedure",
  "summary":"寻找药品时，先检查客厅桌面；未找到后打开浴室柜，再在柜内拿取药品。",
  "task":"find_object",
  "target":"medicine",
  "outcome":"success",
  "is_executable":true,
  "steps":[
    {"index":1,"tool":"inspect","arguments":{"location":"living_room_table"},"result":"not_found","status":"success","evidence_event_ids":["evt-02","evt-03"]},
    {"index":2,"tool":"open","arguments":{"container":"bathroom_cabinet"},"result":"success","status":"success","evidence_event_ids":["evt-12"]},
    {"index":3,"tool":"pick","arguments":{"object":"medicine"},"result":{"status":"success","held_object":"medicine"},"status":"success","evidence_event_ids":["evt-13"]}
  ],
  "reusable_steps":[1,2,3],
  "failure_lesson":null,
  "outcome_evidence_event_ids":["evt-13"],
  "source":{"session_id":"s1","event_ids":["evt-02","evt-03","evt-12","evt-13"]}
}
```

字段约束：

- `outcome` 只能是 `success`、`failure`、`partial` 或 `unknown`。
- `steps` 保留任务相关的完整实际操作序列（不是完整 debug trace），按真实调用顺序保存；每步至少包含 `index`、`tool`、真实 `arguments`、`result`、`status` 和 `evidence_event_ids`。已知状态为 `success`、`failed`、`blocked` 或 `skipped`；缺少结果时 `result=null,status=null`，不得伪造失败或跳过。`skipped` 仅用于有明确跳过事件的动作，不能补入模型计划但从未尝试的步骤。
- `is_executable` 不是 LLM 自行决定的信任字段。只有当任务有明确的外部成功证据，且推荐步骤中的每一步都能由对应工具结果或外部终态验证时，系统才允许将其置为 `true`。
- `reusable_steps` 只包含经过最终成功验证的推荐步骤索引。失败尝试和修正动作仍保留在完整 `steps` 中，但未经最终成功验证的路径不能进入 `reusable_steps`。
- `outcome_evidence_event_ids` 指向任务目标的外部结果证据；没有对应证据时为空数组。工具调用完成、退出会话、模型说“完成了”都不等于任务目标达成。`is_executable` 在写入 planner 之前确定；非成功或路径未验证时固定为 `false`，`reusable_steps=[]`。
- 最终任务成功不能证明任意删减后的路径正确：去掉失败尝试后，必须保留实际依赖的观察和修正动作。无法确认删减路径的前置条件或存在失败动作的未知副作用时，不发布该路径为可执行路径。这里的“可执行”仅表示历史路径已验证，不授予当前任务权限，不绕过对象绑定、前置条件和现场检查。
- `failure_lesson` 在 `failure` 或 `partial` 时可存在，至少包含 `observed_failure`、`lesson`、`corrective_action` 和 `evidence_event_ids`；每个字段都必须能回溯到工具结果或明确的任务反馈。`unknown` 时不得猜测失败原因，`failure_lesson` 必须为空。
- 当轨迹是“失败尝试 -> 修正动作 -> 最终成功”时，`steps` 保留失败尝试和修正动作，`failure_lesson` 说明错误及修正方式，`reusable_steps` 只包含最终成功验证的路径。这样召回既能避免重复错误，又不会把失败动作当成 SOP。
- `failure_lesson` 无证据时为 `null`；能确认失败但不能确认原因时，只总结可观察的失败和保守教训，不猜根因。`corrective_action` 未实际验证时为 `null`。例如“拿取失败，打开柜门后再次拿取成功”可记录已验证修正；仅有报错不能推出同一修法必然有效。
- `success` 必须有外部成功证据；`failure` 必须有明确失败或终止证据；`partial` 表示有部分动作完成但任务未完整成功；`unknown` 只能保存观察到的动作和结果，不能推断任务结论。
- 步骤中的 `arguments` 必须来自真实工具调用。失败导致的后续修正步骤要保留，因为它们可能表达有效的前置条件或重试策略。

示例中的 `inspect` 返回 `not_found` 是一次成功执行的搜索观察，不是工具执行失败；以上工具名和结果字段只是领域示例，不是新增工具 API。真实实现必须使用当前 trace 中的工具名和结果结构。`partial` 需要已证实的部分完成和未完整达成证据；若只是日志截断、缺少最终反馈，使用 `unknown`。

`task_procedure` 与现有 `ProcedureRecord` 的边界：`ProcedureRecord` 仍然只表示经过验证的成功路径 SOP，供现有可执行流程消费；V3.6 的 `task_procedure` 是一次 episode 的轨迹经验，允许包含失败和未知结果。失败教训不能强行构造成 `ProcedureRecord`，也不能因为写入了 schema memory 就自动进入可执行 SOP 列表。

### 4.4 领域 schema 与 MindMemOS native memory type
`object_location` 和 `search_observation` 是领域层 schema，默认映射到 MindMemOS native `fact` memory；`task_procedure` 默认映射到 native `experience` memory。领域类型仍必须保留在 entity/property metadata 中，不能依赖 `schema_memory_type()` 自动识别未知类型，因为该函数对未知 entity type 默认回退为 `fact`。`episodes` 配置继续保留给原生 schema pipeline，但 V3.6 的三个 extractor 不生成 episode entity。

## 5. 实体身份与自然语言 summary

### 5.1 `id` 与 `label`
- `id` 是稳定的机器身份，用于跨任务查询、关系连接和 merge。
- `label` 是本次日志中的自然语言名称，用于模型理解和展示。
- 工具输入或环境返回已有 ID 时直接使用。
- 只有自然语言名称时，可使用确定性的规范化名称作为临时 ID，并降低身份置信度。
- 无法确认两个名称是否指向同一物体时不得强行合并。

例如：

```json
{"id":"medicine","label":"那盒药"}
```

### 5.2 `summary`
每条 actionable memory 都应有一句短的自然语言 summary。summary 必须由结构化证据支持，不得引入 schema 中没有的永久性结论，例如“永远放在这里”。

结构化字段用于精确查询、过滤和 merge；summary 用于模型快速理解召回结果。summary 可以由 MindMemOS schema extractor 生成，Homemaster 只需在 schema-add instruction 中明确要求这一点。

对于 `task_procedure`，summary 必须同时说明任务目标、关键成功路径和必要的失败修正；如果 outcome 不是 `success`，必须明确标记未完成、阻塞或失败，不能使用“可直接执行”“已验证”等措辞。summary 不得把一次 episode 观察写成永久规则。

## 6. 坐标设计与来源

坐标是 `object_location.position` 的可选字段，不能由模型凭空猜测。

允许的来源：

1. 工具调用参数中明确提供的坐标，标记 `source: tool_input`；
2. 工具返回中明确提供的坐标，标记 `source: tool_result`；
3. 自然语言只能提供诸如 `left_back` 的区域描述，不得生成精确数值坐标，标记 `source: language`。

优先保存相对于容器、家具或房间的坐标。世界坐标只有在 `frame` 或地图版本明确时才写入。必须同时保存：

- `frame`：坐标相对于谁或哪个地图版本；
- `coordinates`：至少支持 `x/y/z`，缺失维度可省略；
- `unit`：例如 `normalized` 或 `meter`；
- `source`、`observed_at` 和 provenance。

坐标是最近一次观测提示，不是永久真值。没有合法 `frame` 时忽略坐标而保留语义位置。

## 7. Finalizer 改造流程

当前流程：

```text
trace -> render messages -> add_vanilla(messages, context, metadata)
```

改造后，Homemaster 只发起一次 episode-level schema-add 请求：

```text
collect trace
  -> normalize events
  -> pair tool calls with results
  -> attach task context and provenance
  -> submit one schema-add episode ingress
  -> episode orchestrator
       ├─ object_location extractor       (LLM; empty if absent)
       ├─ search_observation extractor    (LLM; empty if absent)
       └─ task_procedure extractor         (LLM; empty if absent)
  -> shared schema_add validation / planning / merge / write
  -> aggregate result
```

一次 trace 不由 Homemaster 预先拆成三次 episode ingress。orchestrator 接收一次完整 episode，再把同一输入交给三个固定 schema extractor；未检测到的类型返回 empty / `not_detected`，不算失败。

其中：

- `normalize events` 只做确定性格式统一，不做开放式知识总结；
- `build schema-add input/instructions` 提供完整 episode，并分别给三个 extractor 指定自己的 schema、证据要求、坐标来源和 summary 要求；
- 三个固定 schema extractor 各自判断自己的类型是否有证据；一个 episode 可以产生零到三种类型；
- 没有 actionable 内容时不伪造记忆；
- finalizer 只在外部 schema-add 成功后把 `add` phase 标记为 completed。

### 7.1 LLM 与规则边界

```text
规则 / 确定性代码：
  trace normalization
  tool_call_id 配对调用和结果
  顺序、时间、事件 ID、session ID、trace hash
  输入校验、幂等键、结果聚合

LLM：
  object_location extractor：从完整 episode 总结物体与位置关系
  search_observation extractor：判断搜索是否找到、未找到、阻塞或报错
  task_procedure extractor：识别可复用步骤、失败后的修正和最终结果
  三个 extractor 各自生成 summary 和结构化候选

MindMemOS 既有能力：
  schema validation、planner、merge、writer、Qdrant、Neo4j、operation record
```

规则层不能从工具名或关键词推断“这是一条位置记忆”；LLM 必须看到完整的 paired episode 后再总结。三个 extractor 都必须被调用，分别返回自己的候选或 empty，不存在一个负责三类之间择一的语义 router。规则层也不能补写工具结果、坐标、步骤参数、失败原因或成功结论。

LLM 只能提出候选的 `outcome`、`reusable_steps` 和 `failure_lesson`；最终 `is_executable` 由确定性的外部终态验证结果约束。若没有外部成功证据，系统必须把 `is_executable` 降为 `false`，即使 LLM 输出了 `true` 也不能照写。

## 8. 抽取与过滤规则
1. 工具调用必须与对应工具结果按稳定的 `tool_call_id` 或事件关系配对。
2. 只从明确存在的实体、位置、参数、结果和坐标生成事实字段。
3. 自然语言可补充目标名、位置名和任务意图；不确定内容降低置信度或标记为 derived。
4. 一次失败不自动产生永久错误规则；只有明确失败原因才能产生前置条件候选。
5. 无关 transport/debug/内部 ID 不进入 actionable memory。
6. 等价事件确定性去重，但不能删除理解搜索顺序所需的失败和重试。
7. 同一物体的新位置必须保留时间和证据，不能静默覆盖历史事实。
8. schema extractor 生成的 summary 必须能由对应结构化字段和事件证据解释。
9. 三个固定 extractor 的输出必须显式声明自己的 `entity_type`；未知类型、跨类型污染或缺失固定 schema 配置必须报错，不能依赖 normalizer 静默回退。合法的空 `entities`/`edges` 是 `not_detected`；格式错误和重试耗尽不是 empty。
10. `task_procedure` 的 `outcome`、步骤状态和 failure lesson 必须与 paired tool result 一致；没有明确证据时使用 `unknown` 或 empty，而不是猜测。
11. 失败轨迹和成功轨迹可以在同一个 `task_procedure` 中共存，但只有最终外部终态验证通过的步骤索引进入 `reusable_steps`。
12. `object_location`、`search_observation` 和 `task_procedure` 的候选必须先合并为一个 episode 的 raw entities/edges，再调用一次现有 `SchemaAddPlanner.build_write_plan()` 和一次 writer mutation；不得为三类各调用一次顶层 add pipeline。

## 9. Job 与幂等
现有 `add` phase 继续作为一次 episode-level schema add ingress 的边界。job 增加：

```json
{"add":{"status":"completed","algorithm":"schema_add_v1","memory_types":["object_location","search_observation","task_procedure"],"memory_count":3}}
```

相同 session、规范化输入和 extractor version 复用同一 job；Homemaster 不为三种类型创建三个顶层 add job。schema add 返回的 per-type 结果写入 job：

```json
{
  "add": {
    "status": "completed",
    "ingress": "episode",
    "algorithm": "schema_add_v1",
    "types": {
      "object_location": {"status": "completed", "memory_count": 2},
      "search_observation": {"status": "not_detected", "memory_count": 0},
      "task_procedure": {"status": "completed", "memory_count": 1}
    }
  }
}
```

schema add 成功后才标记 completed；某个已检测类型失败时保留 per-type 失败状态并支持内部或顶层重试；未检测类型为 `not_detected`，不是失败。job metadata 不能替代外部存储终态。实现上优先保留一个 episode-level add job，由 orchestrator 汇总三个 extractor 的结果；不要为三类各建一套 Homemaster finalizer/job/writer。

`types.task_procedure` 还应记录候选数量、最终 `outcome` 分布以及被外部终态验证为可执行的数量。失败或 partial 轨迹写入成功不等于 SOP 验证成功；job 只能分别报告抽取/写入状态和可执行性验证状态。

一个合并的 mutation plan 不意味着 Qdrant 与 Neo4j 之间有跨库事务。抽取失败时不进入 writer；写入途中失败时保留同一请求和 mutation 身份，重试前逐库读回并补齐未完成部分，不盲目重新抽取生成一批新 ID。全部类型为空时可保留原生 episode/operation record，但三类领域记忆数量为零。不要把 episode memory 计入领域数量。

## 10. 测试与外部终态验收

### 10.1 单元/契约测试
- 真实格式 trace 能被稳定规范化；
- 工具调用/结果正确配对；
- episode-level schema-add instruction 包含家庭任务的抽取目标和 summary 要求；
- 三个固定 extractor 能分别产生 empty 或候选，并允许一个 episode 同时产生多种 memory 类型；
- 各类型独立抽取和报告，不因其他类型未检测到而失败；任一抽取失败则整个 episode 抽取重试，最终通过一个合并后的 planner/writer 写入，不宣称跨库原子性；
- schema-add pipeline 能在给定固定 schema 的情况下运行，不重复实现一套新的抽取、规划、合并和写入逻辑；
- 成功、未找到、阻塞和失败分别映射；
- `success`、`failure`、`partial`、`unknown` 及“失败后修正并最终成功”分别映射；
- 步骤顺序、参数和结果保留；
- `is_executable` 不能仅由 LLM 输出决定，`reusable_steps` 只来自外部成功验证；
- 坐标来源、frame、unit 和 relative position 正确保留；
- 缺坐标、缺结果和不确定自然语言不会伪造字段；
- 重复 finalization 幂等；
- add 失败后重试不重复成功 add；
- 所有 MindMemOS 实现覆盖 schema-add 接口公开方法。

### 10.2 黑盒终态验收
不能只断言 schema add 返回成功，必须：

1. 执行包含“搜索失败 -> 找到物体 -> 后续放置”的家庭任务；
2. 确认 schema memory 在真实 MindMemOS 存储/查询接口中存在；
3. 新 session recall 同一物体，确认返回位置、summary 和/或坐标；
4. 确认新任务实际优先使用召回的位置或步骤；
5. 对每个目标分别核对搜索次数、工具调用次数和任务结果；
6. 核对外部 add 和 recall 的成功状态。

黑盒验收必须覆盖两条不同的 task_procedure 路径：

- 失败终止：真实工具返回失败或阻塞，Neo4j/Qdrant 中可以查到完整失败前缀和有证据的 failure lesson，但召回结果不能被执行链当作可执行 SOP；
- 失败后恢复：先发生失败，再执行修正动作并最终完成任务，存储中同时保留失败教训和成功路径，后续执行只使用经过外部终态验证的 `reusable_steps`。

验收至少证明一种真实收益：减少搜索位置、减少工具调用、避免已知失败或提高任务完成率。

## 11. 交付范围
预计改动集中在：

- `src/homemaster/experience/` 的 trace normalization、schema-add input 构造和 finalizer add phase；
- MindMemOS composition/adapter 中的 schema-add 调用适配；
- finalizer、adapter 和真实 recall 闭环测试；
- 必要的架构文档、用户指南、README、CHANGELOG 同源更新。

实现完成后必须同步更新 `docs/architecture/memory-system.md`、`docs/memory-user-guide.md`、`README.md`、`CHANGELOG.md` 和 `docs/session-handoff.md`，说明三类固定 extractor、失败/恢复轨迹语义、native memory type 映射和真实存储验收结果。

V3.6 不包含新任务规划器；它提供带证据、可理解、可被现有 recall/执行链消费的结构化记忆，并用真实家庭任务验证收益。
