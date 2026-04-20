# 2026-04-19 Stage1~Stage6.5 兼容约束与决策记录

## 1. 文档目的

用于沉淀 Stage1~Stage6.5 中“当下先不做、后续阶段必须兼容/接续”的事项，避免后续实现偏离已确认决策。

适用范围：

- Stage 6（planner/validator，已完成）
- Stage 6.5（runtime progress patch，当前完成）
- Stage 10（scenario fixtures）
- Stage 13（memory reconciliation 集成）
- Stage 15（richer memory / LLM）

---

## 2. 选择题决策记录（用户已确认）

### 决策 A：Stage 1 schema 范围

- 题目：Stage 1 的 domain schema 落地范围
- 选择：`Phase A最小闭环 (Recommended)`
- 约束：Stage 1 仅实现 Phase A 主链所需类型，不提前混入 Phase B richer memory 复杂字段。

### 决策 B：Stage 2 fixture 策略

- 题目：Stage 2 的 MockWorld fixture 放置方式
- 选择：`仅测试夹具 (Recommended)`
- 约束：Stage 2 只在 `tests/fixtures` 放最小 fixture，不提前创建 `data/scenarios/*`；场景数据统一在 Stage 10 建设。

### 决策 C：Stage 4 TaskContext 字段口径

- 题目：TaskContext 字段范围
- 选择：`全量占位集`
- 约束：`TaskContext` 保留 `category_prior_hits`、`recent_episodic_summaries` 占位字段，但当前不参与实际检索/决策。

### 决策 D：Stage 6 memory capability 命名兼容

- 题目：`memory.update` 与 `memory.reconcile` 如何兼容
- 选择：`只做入参归一化到 memory.reconcile`
- 约束：
  - 默认注册器只暴露 `memory.reconcile`。
  - `validate_capability_registry(...)` 接受 `memory.update` 入参并归一化为 `memory.reconcile`。
  - 若两者同时传入且 contract 不一致，校验失败。

### 决策 E：Stage 6 解析失败处理

- 题目：rule-first parser 未命中时 planner 行为
- 选择：`PlannerService 降级 ask_clarification`
- 约束：`PlannerService.plan_from_request(...)` 接管 `ValueError`，不再继续上抛到调用方。

### 决策 F：Stage 6 澄清计划 intent 默认值

- 题目：`ask_clarification` 降级计划的 intent
- 选择：`check_object_presence`
- 约束：保持 Stage 1 `TaskIntent` 边界不扩面。

### 决策 G：Stage 6.5 运行时进度状态归属

- 题目：三类运行时进度放在哪一层
- 选择：`仅扩 RuntimeState，不新增第二状态源`
- 约束：
  - `RuntimeState` 增加 `high_level_progress`、`embodied_action_progress`、`runtime_object_updates`。
  - `TaskContext` 只透传 `runtime_state`，禁止新增顶层 `task_progress/current_object_changes/embodied_progress`。

---

## 3. 前置阶段“已延期到后续”的事项

### Stage 1 延期项

- Phase B 扩展记忆结构（如 richer category prior / episodic weighting）未落地。
- 后续要求：Stage 15 引入 richer memory 时必须保持对现有 Phase A 类型兼容，不破坏已通过测试的字段语义。

### Stage 2 延期项

- 未创建 `data/scenarios/*` 正式场景数据。
- 后续要求：Stage 10 创建 `world.json / memory.json / failures.json` 时，`world.json` 需兼容当前 `MockWorld` 读取结构（`rooms/viewpoints/furniture/objects/visibility/symbolic_predicates`）。

### Stage 3 延期项

- `memory` 模块中的更新/降级接口已实现，但尚未接入 graph 主流程。
- 后续要求：Stage 13 将 `update/downgrade/mark` 与 verified evidence 路径集成，禁止未验证结果更新长期 memory。

### Stage 4 延期项（已在 Stage 5 收敛）

- Stage 4 时 capability registry 仅作为可传入结构，未落地真实注册器。
- 当前状态：Stage 5 已落地默认注册器与校验入口，并将 `build_task_context` 升级为默认注入 + 强校验。

### Stage 5 延期项（已在 Stage 6 收敛）

- Stage 5 未实现 deterministic planner 与 plan validator（按计划留到 Stage 6）。
- 当前状态：Stage 6 已落地 `DeterministicHighLevelPlanner`、`PlanValidator`、`PlannerService`。

### Stage 6.5 预留后续集成项

- 三类运行时进度槽位已定义，但真实推进逻辑保留到 `execute_subgoal_loop`。
- 后续要求：
  - Stage 11/Task 10 在子目标执行循环里推进 `high_level_progress` 与 `embodied_action_progress`。
  - `runtime_object_updates` 只作为 Stage 13 reconciliation 输入候选，不直接写长期 `ObjectMemory`。

---

## 4. 当前接口基线（后续实现需遵守）

### 4.1 TaskContext 约束

- `build_task_context(...)` 默认注入 `default_capability_registry()`。
- 显式传入 registry 时必须通过 `validate_capability_registry(...)`。
- `TaskContext` 禁止持有 `memory_store/world/trace` 等底层对象（`extra="forbid"`）。
- `TaskContext` 禁止新增 `task_progress/current_object_changes/embodied_progress` 顶层字段。

### 4.2 RuntimeState 约束

- 当前任务执行状态唯一主语是 `RuntimeState`。
- 除 Stage 1 基础字段外，Stage 6.5 已追加：
  - `high_level_progress`
  - `embodied_action_progress`
  - `runtime_object_updates`
- `EmbodiedActionProgress.local_world_state_flags` 当前只允许四键：
  - `container_opened`
  - `holding_target`
  - `target_dropped`
  - `target_location_changed`

### 4.3 Memory 主链约束

- 检索路径固定：`category -> alias -> location hint -> confidence -> negative evidence exclusion`。
- `searched_not_found` 默认排除，只有 `allow_revisit=True` 才可重新纳入。
- `task_negative_evidence` 与 `runtime_object_updates` 都不应直接写入长期 object memory 主表。

### 4.4 Parser/Planner 约束

- parser 仍是 rule-first，仅支持 MVP 两类意图：`check_object_presence` / `fetch_object`。
- parser 未命中规则会抛 `ValueError`，由 `PlannerService.plan_from_request(...)` 统一降级 `ask_clarification`。
- planner 只读 `TaskContext`，不直接读 memory/world/trace。

---

## 5. 后续阶段重点校验清单

### Stage 10（Scenario Fixtures）

- 一次性补齐 `data/scenarios/*` 五个场景三件套（`world/memory/failures`）。
- fixtures 字段需兼容当前 `MockWorld` / `MemoryStore`。

### Stage 11（Execute Subgoal Loop）

- 将 Stage 6.5 三类运行时状态接到真实执行循环推进。
- 仍保持 runtime 是唯一状态源，trace 仅记录事实。

### Stage 13（Memory Reconciliation）

- 将 verified evidence 更新链路接到 graph。
- 严格执行：未验证证据不能更新长期 memory。
- `runtime_object_updates` 只能作为候选输入，不能绕过 verification 直接入表。

### Stage 15（Richer Memory / LLM）

- 让 `category_prior_hits`、`recent_episodic_summaries` 从占位字段转为真实输入。
- LLM 高层计划器只通过 `TaskContext.runtime_state` 读取运行时进度信息。
- 新能力接入必须维持 Stage 5/6 capability contract 稳定性。

---

## 6. 值得关注的风险点（建议优先盯）

- 风险 1：后续阶段若直接改 capability 名称，容易导致 context/planner 接口断裂。
- 风险 2：Stage 10 fixture 结构若与当前 world/memory 读入逻辑不一致，会造成大量测试噪音。
- 风险 3：Stage 11 若把运行时进度复制到 `TaskContext` 顶层，会破坏“单一状态主语”设计。
- 风险 4：Stage 13 若把 task-scoped state 直接写回长期 memory 主表，会破坏“状态/记忆分层”设计。
- 风险 5：Stage 15 若跳过占位字段直接新增新结构，容易导致 Stage 4/5 的上下文契约失配。
