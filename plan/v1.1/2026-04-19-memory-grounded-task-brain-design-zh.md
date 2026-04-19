# Memory-Grounded Task Brain v1.1 中文设计方案

日期：2026-04-19

状态：中文版评审稿

来源：

- v1.0 设计文档：`plan/v1.0/2026-04-19-memory-grounded-task-brain-design.md`
- v1.0 实施计划：`plan/v1.0/2026-04-19-memory-grounded-task-brain.md`
- v1.1 评审意见：记忆结构、感知抽象、恢复策略、检索策略、测试验收等补充意见

## 1. 总体定位

v1.1 不推翻 v1.0 的主线。v1.0 的核心骨架仍然正确：

- MVP 先做 CLI-first 的 memory-grounded task brain。
- 先验证高层闭环，不急着接真实机器人或完整仿真。
- 执行顺序坚持 retrieve memory before planning。
- 成功判断坚持 verification before success。
- LangGraph 只管阶段级编排，不把图拆得过碎。
- Recovery 作为主闭环的一部分保留。

v1.1 的目标是把 v1.0 从“能跑的 demo plan”升级成“接口更清晰、记忆更合理、恢复更聪明、后续更容易接仿真”的工程方案。

当前 MVP 的 perception 输入是 mock observation / symbolic observation，不是真实 RGB 图像上的 VLM 推理。本阶段验证的是 memory-grounded decision loop，而不是真实视觉理解能力。

## 2. 项目边界

### 2.1 v1.1 包含

- 独立 Python 项目形式的 CLI MVP。
- 真实 compiled LangGraph，但保持粗粒度阶段图。
- 规则优先的 instruction parser。
- 结构化优先的 memory retrieval。
- 统一 Observation schema。
- Mock world / mock perception / mock VLN / fake RoboBrain / mock atomic executor。
- Object memory、category prior memory、task-scoped negative evidence、episodic memory 分层。
- Deterministic high-level planner 先打穿主链，LLM high-level planner 作为 Phase B 增强。
- Plan validation。
- Subgoal verification 和 final task verification。
- Failure-type-aware recovery。
- 可审计 CLI trace 和 JSONL trace。
- 面向后续 AI2-THOR、BEHAVIOR、Habitat、真实 VLM 的 adapter 边界。

### 2.2 v1.1 不包含

- 真实机器人控制。
- 完整 AI2-THOR / BEHAVIOR / Habitat 集成。
- RGB 图像上的真实 VLM 推理。
- 训练 VLA 或端到端 robot policy。
- 产品级安全、权限、取消、长任务调度。
- 复杂容器搜索，如打开柜子、抽屉。
- 多机器人协作。
- RoboOS 式完整分布式运行时。
- Hermes agent runtime 深集成。

Hermes 只参考 gateway / message intake / reply 的边界设计。Hermes 不进入 high-level planning、memory update、recovery decision 或 robot execution。

RoboOS 只作为架构参考。HomeMaster 不应该基于 RoboOS 直接改造。

### 2.3 Phase A / Phase B 范围

v1.1 必须拆成两个阶段实施，避免“每块都做一点，但主链不够硬”。

Phase A 是必须先打穿的闭环 MVP：

```text
CLI 主链
3 个核心场景
rule-first parser
deterministic planner
object memory
task negative evidence
verification
failure analysis
recovery
```

Phase A 的目标是得到一个稳定、可演示、可测试的 CLI 闭环。只要 Phase A 没稳定，不启动 Phase B 增强项。

Phase B 是主链稳定后的增强：

```text
richer category prior
episodic retrieval 加权
LLM planner
simulator-style wrapper
optional gateway
```

Phase B 不应该反向阻塞 Phase A。比如 category prior 可以先有 schema 或最小字段边界，但 richer ranking 和 episodic weighting 不进入 Phase A 必做范围；LLM planner 可以保留接口，但 Phase A 的默认 planner 必须是 deterministic planner。

## 3. 项目基线

实现基线应是：

```text
/Users/wylam/Documents/workspace/HomeMaster
```

这是一个新的独立 Python 项目，不在 RoboOS 内部改，不把 Hermes 作为主工程运行时。

本地参考项目：

```text
/Users/wylam/Documents/workspace/RoboOS
/Users/wylam/.hermes/hermes-agent
```

RoboOS 可参考：

- brain / cerebellum 分层思想。
- capability registry / skill library 思想。
- shared state / memory 思想。
- error correction / verification 思想。

Hermes 可参考：

- gateway 入口结构。
- message event 到内部 request 的转换。
- session / source key 的组织方式。
- Feishu allowlist / reply 的工程边界。

但 HomeMaster 的 Task Brain 应保持独立。

## 4. 高层架构

v1.1 使用三层结构：

```text
Instruction Source
  -> Task Brain / LangGraph
  -> Adapter Layer
  -> Mock World / External Simulator / RoboBrain / Runtime
  -> Verification / Recovery / Memory Update
  -> Trace / Response
```

### 4.1 Task Brain / LangGraph

Task Brain 负责：

- 解析用户指令。
- 检索记忆。
- 构造 task context。
- 生成 high-level plan。
- 校验 plan。
- 执行 subgoal loop。
- 验证 subgoal 和 final task。
- 分析失败类型。
- 决定恢复动作。
- 更新长期记忆和 episodic memory。
- 输出 trace 和最终响应。

LangGraph 只负责阶段级顺序和状态传递。具体 retry、candidate switch、re-observe、局部 recovery 可以留在 `execute_subgoal_loop` 内部。

### 4.2 Adapter Layer

Adapter 负责把外部系统变成 Task Brain 的标准接口。

第一版 adapter：

- `MockVLNAdapter`
- `MockPerceptionAdapter`
- `FakeRoboBrainClient`
- `MockAtomicExecutor`

未来 adapter：

- `AI2ThorPerceptionAdapter`
- `BehaviorPerceptionAdapter`
- `HabitatPerceptionAdapter`
- `VLMPerceptionAdapter`
- `RealRobotExecutionAdapter`

Task Brain 不直接依赖 mock world、simulator event、RGB frame、segmentation mask 或 robot middleware。

### 4.3 Skill-Compatible Capability Contract

所有 adapter-facing capability 都必须按 skill-compatible 方式设计。Task Brain 核心只能依赖稳定能力契约，不能依赖某个具体 adapter 的内部实现。

每个 capability 必须声明：

```text
- stable capability name
- typed input schema
- typed output schema
- failure modes
- timeout
- evidence-carrying result
```

这个约束的目标是后续可以平滑替换能力层实现：

```text
mock_vln -> team VLN skill
fake robobrain / mock executor -> team VLA skill
mock_perception -> real perception / VLM / simulator skill
```

只要 capability contract 不变，Task Brain 的 parser、retrieval、planning、verification、recovery 主体都不应该改。

### 4.4 State and Memory Layer

状态和记忆必须拆开：

- `world truth`：mock world 或 simulator 的真实状态。
- `runtime state`：当前任务运行中的机器人状态、当前 observation、选中目标、retry budget。
- `long-term memory`：object memory、category prior、episodic memory。
- `task-scoped memory`：当前任务内的 negative evidence 和 candidate exclusion state。
- `trace`：任务执行过程的可审计事件流。

当前任务执行中的唯一状态主语是 runtime / task-scoped state。以下信息必须统一由 runtime state 或 task state 维护：

```text
current observation
selected candidate
selected object
retry budget
recent failure analysis
task negative evidence
candidate exclusion state
```

长期 memory 只负责跨任务记忆，不作为当前执行状态源。Trace 只负责记录，不作为当前执行状态源。World truth 只作为 mock / simulator 的事实来源，不直接变成 Task Brain 的状态主语。

## 5. LangGraph 主流程

v1.1 的主图显式写成：

```text
input_instruction
  -> parse_instruction
  -> retrieve_memory
  -> build_task_context
  -> generate_plan
  -> validate_plan
  -> execute_subgoal_loop
  -> final_task_verification
  -> update_memory
  -> respond_with_trace
```

关键顺序规则：

- `retrieve_memory` 必须早于 `generate_plan`。
- `build_task_context` 必须早于 `generate_plan`。
- `validate_plan` 必须早于 `execute_subgoal_loop`。
- 每个 subgoal 成功前必须先经过 subgoal verification。
- 任务成功前必须经过 final task verification。
- `update_memory` 只能基于 verification evidence、negative evidence、execution result 和 trace summary。

v1.0 中 `build_task_context` 和 validation 有些实现细节藏在 planning node 中。v1.1 应把它们作为显式主阶段，方便 trace、测试和后续替换。

## 6. Subgoal Loop

Subgoal loop 内部流程：

```text
select_next_subgoal
  -> execute_adapter_or_planner
  -> collect_observation_or_execution_evidence
  -> verify_subgoal
  -> analyze_failure
  -> decide_recovery
  -> continue_or_replan_or_stop
```

`execute_subgoal_loop` 可以作为 LangGraph 的一个粗粒度 node，但内部必须记录结构化 trace。

subgoal loop 的职责：

- 对 navigate subgoal 调用 VLN adapter。
- 对 observe subgoal 调用 perception adapter。
- 对 verify object presence subgoal 调用 verification engine。
- 对 embodied manipulation subgoal 调用 RoboBrain client，再交给 executor。
- 对 return_to_user subgoal 调用 navigation / runtime adapter。
- 对每个 subgoal 收集标准化 evidence。
- 对失败结果调用 `analyze_failure` 和 `decide_recovery`。

## 7. Observation Abstraction

### 7.1 设计原则

Task Brain 不直接读取 mock world 内部状态。Perception adapter 输出统一 `Observation`，verification engine 和 planner 只消费标准化 observation / evidence。

这保证后续从 mock world 切换到 AI2-THOR、BEHAVIOR、Habitat 或真实 VLM 时，Task Brain 主接口不需要重写。

### 7.2 Observation Schema

推荐 schema：

```text
Observation
- observation_id
- source
- viewpoint_id
- room_id
- timestamp
- visible_objects
- visible_anchors
- scene_relations
- raw_ref
```

`source` 可取：

```text
mock_world
ai2_thor
behavior
habitat
vlm
real_robot
```

`raw_ref` 只保存 raw payload 的引用或摘要，用于 debug 和后续训练，不进入核心决策逻辑。

### 7.3 ObservedObject Schema

```text
ObservedObject
- observation_object_id
- category
- aliases
- attributes
- detector_id
- memory_id
- confidence_level
- state_summary
- spatial_relation
```

ID 边界：

- `detector_id` 是一次观测里的临时检测 ID。
- `observation_object_id` 是 observation 内部归一化后的局部 ID。
- `memory_id` 是长期 object memory ID。

三者不能混用。一次 VLM / detector 输出的临时 ID 不能被直接当作长期记忆 ID。

### 7.4 Verification Evidence

Verification engine 消费：

```text
VerificationEvidence
- observation
- execution_result
- robot_runtime_state
- task_negative_evidence
```

verification engine 不应该直接读 raw simulator state。mock world 第一版可以作为 evidence 生成来源，但 verification 的 public interface 应该面向 `Observation` 和 `VerificationEvidence`。

## 8. Memory v1.1

v1.1 将记忆拆成四层：

- Object memory：实例级长期记忆。
- Category prior memory：类别级位置先验。
- Task negative evidence：当前任务负证据。
- Episodic memory：任务过程记忆。

### 8.1 Object Memory

Object memory 记录某个长期物体实例或实例候选通常在哪里、上次确认是什么情况。

推荐字段：

```text
ObjectMemory
- memory_id
- object_category
- aliases
- anchor.room_id
- anchor.anchor_id
- anchor.anchor_type
- anchor.viewpoint_id
- anchor.display_text
- relative_relation.type
- relative_relation.target
- relative_relation.text
- last_observed_state
- evidence_source
- confidence_level
- last_confirmed_at
- description
- belief_state
```

设计要点：

- `memory_id` 是长期 memory id，不是 detector id。
- anchor 底层必须结构化，不能只存 `"厨房/餐桌_2"`。
- `anchor.display_text` 可以保留，方便 CLI trace 和人工审查。
- relative relation 同时保留规范关系和自然语言。
- `last_observed_state` 只存长期记忆里最近一次确认状态摘要。
- 当前是否 visible / reachable / graspable / occluded 属于 runtime state 或 observation，不直接写入长期 object memory 主表。

### 8.2 Anchor

```text
Anchor
- room_id
- anchor_id
- anchor_type
- viewpoint_id
- display_text
```

示例：

```json
{
  "room_id": "kitchen",
  "anchor_id": "kitchen_table_2",
  "anchor_type": "table",
  "viewpoint_id": "kitchen_table_viewpoint",
  "display_text": "厨房餐桌"
}
```

### 8.3 Relative Relation

```text
RelativeRelation
- type
- target
- text
```

示例：

```json
{
  "type": "left_of",
  "target": "kettle_1",
  "text": "在热水壶左边"
}
```

### 8.4 Category Prior Memory

很多用户说的是类别，而不是实例：

- “拿个水杯”
- “看看药盒”

因此必须有 category-level prior。

推荐字段：

```text
CategoryPriorMemory
- object_category
- candidate_locations
- prior_source
- updated_at
```

`candidate_locations`：

```text
CandidateLocationPrior
- anchor.room_id
- anchor.anchor_id
- anchor.anchor_type
- anchor.viewpoint_id
- prior_rank
- confidence_level
- reason
```

`prior_source` 可取：

```text
experience
user_provided
task_summary
statistical_prior
```

### 8.5 Task Negative Evidence

negative evidence 不并入长期 object memory 主表。它是 task-scoped memory。

推荐字段：

```text
TaskNegativeEvidence
- task_request_id
- object_category
- searched_location
- status
- timestamp
- evidence
```

硬规则：

```text
当前任务中已经搜索未找到的位置，默认从候选列表中排除，
除非 replanning 明确设置 allow_revisit=true。
```

这条规则是 stale-memory recovery 的核心。

### 8.6 Episodic Memory

Episodic memory 记录任务过程、失败原因、恢复动作和总结。

推荐字段：

```text
EpisodicMemory
- episode_id
- task_request_id
- user_id
- intent
- target_object_category
- outcome
- failure_types
- recovery_actions
- final_summary
- trace_ref
- created_at
```

Episodic memory 第一版不作为硬约束。它用于 trace summary、后续 ranking、debug 和训练数据积累。

### 8.7 Evidence Source 与 Confidence Level

v1.1 继续使用高 / 中 / 低，不改成 0 到 1 分数。

需要区分：

```text
evidence_source: direct_observation / user_provided / inferred_experience
confidence_level: high / medium / low
```

默认映射：

```text
direct_observation -> high
user_provided -> medium
inferred_experience -> low
```

动态修正规则：

- 时间久未确认 -> 降级。
- 当前任务已搜索未找到 -> 降级。
- 新观测再次确认 -> 升级。
- 与新证据冲突 -> 降级。

### 8.8 Memory Reconciliation Rules

字段设计不是最容易出错的地方，更新、合并和冲突处理才是。v1.1 必须明确 memory reconciliation 硬规则。

#### 8.8.1 什么时候更新旧 Object Memory

满足以下条件时，更新旧 object memory：

- 新 evidence 来自 verified observation 或 verified execution evidence。
- 新 evidence 与旧 memory 的 `memory_id` 明确匹配；或 category、anchor、relative relation 足够一致，可以被 reconciliation 规则判定为同一长期实例。
- 新 observation 的 object category 与旧 memory 一致。
- 新位置只是 anchor / relative relation 的更新，不是完全无法解释的冲突。

更新内容可以包括：

- `anchor`
- `relative_relation`
- `last_observed_state`
- `evidence_source`
- `confidence_level`
- `last_confirmed_at`
- `description`
- `belief_state`

禁止基于 planner 猜测更新旧 object memory。

#### 8.8.2 什么时候新建 Object Memory

满足以下条件时，新建 object memory：

- 新 evidence 已验证。
- 没有可匹配的旧 `memory_id`。
- 与旧 memory 的 anchor / relative relation 冲突较大，且无法确认是同一实例移动。
- 用户或 observation 明确表明这是另一个实例。

新建 memory 时，`memory_id` 必须是长期 ID，不能直接使用 detector id。

#### 8.8.3 旧记忆与新证据冲突时如何处理

如果旧 memory 指向的位置在当前任务中被搜索且 verified not found：

- 当前任务内：立即写入 task negative evidence，并排除该 candidate。
- 长期 memory：不直接删除，不直接污染 object memory 主表。
- 如果冲突来自 verified evidence，则将旧 memory 降级，`belief_state` 标为 `stale` 或 `contradicted`。
- 如果只是一次弱证据或不完整 observation，则保留旧 memory，降低 confidence 或等待更多证据。

长期 memory 的删除不在 MVP 默认路径内。第一版只做降级和 stale / contradicted 标记。

#### 8.8.4 Task Negative Evidence 如何影响长期 Memory

Task negative evidence 先影响当前任务排序，默认不直接影响长期 memory。

只有在以下条件满足时，才影响长期 memory：

- negative evidence 来自 verified observation。
- 搜索位置与某条 object memory 的 anchor 明确对应。
- 当前任务完成后进入 memory reconciliation 阶段。
- 没有后续 verified observation 重新确认该旧 memory。

影响方式是降级或标记 stale / contradicted，不是直接删除。

#### 8.8.5 Category Prior 与 Object Memory 冲突时谁优先

Object memory 优先于 category prior，前提是 object memory 没有被当前任务 negative evidence 排除，也没有被标记为强 stale / contradicted。

优先级：

```text
verified current observation
  > task negative evidence / candidate exclusion
  > active object memory
  > category prior
  > episodic weak hint
```

如果 category prior 与 object memory 冲突：

- active object memory 优先。
- stale / contradicted object memory 不应压过 category prior。
- 当前任务 negative evidence 可以临时排除两者指向的同一 location。

## 9. Retrieval v1.1

第一版 retrieval 采用 structured retrieval first，不做 embedding-first。

Phase A 检索只做主链必需的结构化检索：

```text
parse target object
  -> object_category filter
  -> alias match
  -> explicit location hint boost
  -> object memory candidates
  -> apply task negative evidence exclusion
  -> ranked candidate list
```

Phase B 再加入更丰富的 category prior 和 episodic retrieval 加权：

```text
parse target object
  -> object_category filter
  -> alias match
  -> explicit location hint boost
  -> object memory candidates
  -> category prior candidates
  -> recent successful episodes boost
  -> user habit / task history boost
  -> apply task negative evidence exclusion
  -> ranked candidate list
```

排序第一版可以是 deterministic scoring / tiered ranking。每个候选必须有可解释 reason，写入 trace。

Task negative evidence 优先级最高：

```text
如果某个 location 在当前任务中已经 searched_not_found，
默认从候选列表排除，除非 replanning 显式设置 allow_revisit=true。
```

## 10. Parser v1.1

Parser 第一版继续 rule-first，不改成 LLM-first。

Parser 只负责抽取结构，不负责生成计划。

推荐输出：

```text
ParsedTask
- intent
- target_object.category
- target_object.aliases
- target_object.attributes
- quantity
- explicit_location_hint
- delivery_target
- requires_navigation
- requires_manipulation
```

LLM 预算留给：

- high-level planning。
- replanning。
- clarification。
- optional trace summary。

## 11. Task Context Builder

`build_task_context` 是 v1.1 的显式主阶段。

推荐结构：

```text
TaskContext
- request
- parsed_task
- ranked_candidates
- object_memory_hits
- category_prior_hits
- task_negative_evidence
- recent_episodic_summaries
- current_observation
- robot_runtime_state
- capability_registry
- adapter_status
- constraints
```

Planner 只通过 `TaskContext` 获取上下文。Planner 不直接读 memory store、mock world 或 raw simulator state。

## 12. Planning 与 Validation

v1.1 仍然支持 LLM planner + safe fallback。

规则：

- Planner 只能生成 high-level subgoals。
- Planner 不能生成 atomic robot actions。
- Planner 必须引用 memory grounding / candidate grounding。
- Planner 不能跳过 verification。
- Plan validator 必须在执行前拦截非法 plan。
- Fallback deterministic planner 必须覆盖两个 MVP task：
  - check object presence。
  - fetch object。

Phase A 默认只使用 deterministic planner。LLM planner 只保留接口和测试边界，不作为 Phase A 主链依赖。Phase B 才接入真实 LLM provider、invalid-plan fallback 和 replanning prompt。

Replanning context 必须注入：

```text
- task_negative_evidence
- candidate_exclusion_state
- recent_failure_type
- recent_observation_summary
- rejected_target_info
- retry_budget_state
```

## 13. Failure-Type-Aware Recovery

v1.1 将 recovery 从 attempt-based 升级为 failure-type-aware。

失败类型：

```text
navigation_failure
object_presence_failure
manipulation_failure
final_task_failure
```

流程：

```text
verify_subgoal
  -> analyze_failure
  -> decide_recovery
```

### 13.1 Object Presence Failure

含义：到达候选位置，但目标类别或实例不可见 / 不可用。

默认动作：

- 写入 task negative evidence。
- 排除当前 candidate。
- switch_candidate。

候选耗尽时：

- ask_clarification。
- 或 report_failure。

禁止 fabricate success。

### 13.2 Manipulation Failure，目标仍可见

含义：RoboBrain / executor 尝试后，目标没有达到预期状态，但目标仍可见。

默认动作：

- retry_same_subgoal。
- MVP retry budget 最多 1 次。

### 13.3 Manipulation Failure，目标状态已变化

含义：目标消失、位置变化、被遮挡，或 observation 与旧计划假设冲突。

默认动作：

- re_observe。
- replan。

### 13.4 Navigation Failure

含义：目标 viewpoint 不可达、无效或 adapter 返回导航失败。

默认动作：

- switch_candidate。
- 或 replan。

### 13.5 Final Task Failure

含义：subgoal 执行过，但最终用户目标没有满足。

默认动作：

- high-level replan。
- 或 failure path。

禁止把 final verification failure 伪装为 success。

### 13.6 必须 Replan 的条件

至少包括：

- 原 top candidate 被证伪后，剩余候选很弱。
- manipulation failure 超过 retry budget。
- 出现 distractor 或目标歧义。
- final task verification failed。
- 当前 observation / runtime state 让旧 plan 假设失效。

## 14. Trace 与 Memory Update

Trace 是一等产物。

必须记录：

- 输入指令。
- parse result。
- memory retrieval result。
- candidate ranking reason。
- task context summary。
- high-level plan。
- plan validation result。
- adapter call。
- observation。
- verification evidence。
- failure analysis。
- recovery decision。
- replan context。
- memory update。
- final response。

Memory update 只基于证据：

- successful final verification。
- object presence verification。
- task negative evidence。
- execution result。
- final trace summary。

不要因为 planner 预测或未验证的执行结果直接更新长期 object memory。

## 15. 测试策略

保留 v1.0 scenario tests：

- `check_medicine_success`
- `check_medicine_memory_stale_recover`
- `fetch_cup_success_with_robobrain_plan`
- `fetch_cup_grasp_retry_success`
- `object_not_found_candidate_exhausted`
- `distractor_object_rejected`

v1.1 新增测试：

- Perception abstraction test：Task Brain 依赖标准 Observation schema，不依赖 mock world 私有字段。
- Failure-analysis test：同样 verification failed，在不同 evidence 下触发不同 recovery action。
- Simulator-readiness test：simulator-style wrapper 输出 Observation 后，planner / verification 接口不需要改。
- Memory retrieval test：task negative evidence 优先排除已搜索未找到的位置。
- Category prior test：实例记忆缺失时，类别先验提供候选。
- Confidence update test：stale memory 被新负证据降级，新确认观测可升级。
- Detector ID test：临时 detector id 不会被当作长期 memory id。
- Memory reconciliation test：verified observation 才能更新长期 object memory，task negative evidence 默认只影响当前任务。
- Capability contract test：所有 adapter-facing capability 都有 stable name、typed input/output、failure modes、timeout、evidence-carrying result。

## 16. 验收标准

v1.1 MVP 完成时至少满足：

- Phase A 先打穿，Phase B 不阻塞 CLI 主链。
- CLI 能跑通核心场景。
- Trace 显示 retrieve memory before planning。
- Trace 显示 verification before success。
- LangGraph 是真实 compiled graph，且主阶段显式。
- Perception 输入是标准 Observation。
- Memory 分层清楚：object memory、category prior、task negative evidence、episodic memory。
- 当前任务执行状态统一由 runtime / task-scoped state 维护。
- Retrieval 第一版是 structured retrieval first。
- Parser 第一版是 rule-first。
- Phase A 默认 planner 是 deterministic planner。
- Recovery 按 failure type 决策。
- Stale memory 不会在同一任务 replan 中被重复选中。
- Candidate exhausted 不会伪造成功。
- Final task verification failed 必须进入 replan 或 failure path。
- 长期 memory 更新只能基于 verified observation / verified evidence。
- Adapter-facing capability 满足 skill-compatible contract。
- Hermes / Feishu 仍为可选桥接，不阻塞 CLI MVP。
