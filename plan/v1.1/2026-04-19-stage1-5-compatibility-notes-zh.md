# 2026-04-19 Stage1~Stage5 兼容约束与决策记录

## 1. 文档目的

用于沉淀 Stage1~Stage5 中“当下先不做、后续阶段必须兼容/接续”的事项，避免后续实现偏离已确认决策。

适用范围：

- Stage 6（planner/validator）
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

### Stage 4 延期项（已在 Stage 5 部分收敛）

- Stage 4 时 capability registry 仅作为可传入结构，未落地真实注册器。
- 当前状态：Stage 5 已落地默认注册器与校验入口，并将 `build_task_context` 升级为默认注入 + 强校验。

### Stage 5 延期项

- 本阶段未实现 deterministic planner 与 plan validator（按计划留给 Stage 6）。
- `memory.reconcile` 为当前 canonical capability 名称。
- 后续要求：若后续阶段使用 `memory.update` 命名，需做兼容映射，避免破坏已存在 contract。

---

## 4. 当前接口基线（后续实现需遵守）

### 4.1 TaskContext 约束

- `build_task_context(...)` 默认注入 `default_capability_registry()`。
- 显式传入 registry 时必须通过 `validate_capability_registry(...)`。
- `TaskContext` 禁止持有 `memory_store/world/trace` 等底层对象（`extra="forbid"`）。

### 4.2 Memory 主链约束

- 检索路径固定：`category -> alias -> location hint -> confidence -> negative evidence exclusion`。
- `searched_not_found` 默认排除，只有 `allow_revisit=True` 才可重新纳入。
- `task_negative_evidence` 不应写入长期 object memory 主表。

### 4.3 Parser 约束

- 仍是 rule-first，仅支持 MVP 两类意图：`check_object_presence` / `fetch_object`。
- 未命中规则输入当前直接抛 `ValueError`（后续若接入 clarification/replan，需显式兼容此行为）。

---

## 5. 后续阶段重点校验清单

### Stage 6（Planner/Validator）

- Planner 只读 `TaskContext`，不直接读 memory/world/trace。
- Plan grounding 必须能消费 Stage 3 产出的候选结构（`memory_id/object_category/anchor/confidence_level/score/reasons`）。
- Planner/validator 不得绕过 capability contract。

### Stage 10（Scenario Fixtures）

- 一次性补齐 `data/scenarios/*` 五个场景三件套（`world/memory/failures`）。
- fixtures 字段需兼容当前 `MockWorld` / `MemoryStore`。

### Stage 13（Memory Reconciliation）

- 将 verified evidence 更新链路接到 graph。
- 严格执行：未验证证据不能更新长期 memory。
- stale/contradicted 标记与 task negative evidence 的职责边界不能混淆。

### Stage 15（Richer Memory/LLM）

- 让 `category_prior_hits`、`recent_episodic_summaries` 从占位字段转为真实输入。
- 新能力接入必须维持 Stage 5 capability contract 稳定性。

---

## 6. 值得关注的风险点（建议优先盯）

- 风险 1：后续阶段若直接改 capability 名称，容易导致 context/planner 接口断裂。
- 风险 2：Stage 10 fixture 结构若与当前 world/memory 读入逻辑不一致，会造成大量测试噪音。
- 风险 3：Stage 13 若把 task-scoped negative evidence 写回长期 memory 主表，会破坏“状态/记忆分层”设计。
- 风险 4：Stage 15 若跳过占位字段直接新增新结构，容易导致 Stage 4/5 的上下文契约失配。

