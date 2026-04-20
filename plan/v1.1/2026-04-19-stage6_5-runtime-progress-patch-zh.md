# Stage 6.5 双层运行时进度状态兼容补丁

## 1. 补丁目标

当前项目已完成到 Stage 6，高层计划器、计划校验器、runtime/task-scoped state 的基本边界已经成立。现在要补齐三类运行时状态槽位：

- 高层任务进度：让高层计划器和重规划知道大任务做到哪一步。
- 具身动作进度：让局部操作规划知道当前技能执行到哪一步，以及局部世界假设是否仍成立。
- 当前任务内对象状态变化：记录目标掉落、移位、遮挡等只在当前任务内发生的对象变化。

这不是引入一个新的“任务进度系统”，而是在现有 `RuntimeState` 里补齐更细的运行时状态。`RuntimeState` 仍然是当前任务执行中的唯一状态主语。

## 2. 硬原则

必须保持不变：

- 不改 Phase A / Phase B 总边界。
- 不改 Stage 6 已完成的 deterministic 高层计划器主逻辑。
- 不改 `PlanValidator` 主逻辑。
- 不改长期 `ObjectMemory` 主表结构。
- 不引入第二个当前任务状态源。
- 不把运行时进度写入长期 memory。
- 不把 trace 当作当前执行状态源。

本补丁只做：

- `domain.py` 增加三个新结构。
- `RuntimeState` 增加三个新字段。
- `TaskContext` 继续只透传 `runtime_state`。
- 增加最小兼容测试。
- 更新计划文档中的前后阶段说明。

现在不做：

- 不重写 deterministic 高层计划器。
- 不重写计划校验器。
- 不提前实现“开抽屉、拿药、关抽屉”的完整局部行为。
- 不让长期记忆主表承担运行时状态职责。

## 3. 两个 Planner 的命名边界

后续文档和代码里要避免只写含糊的 `planner`。默认应区分：

### 高层计划器

负责：

- 先去哪里。
- 先观察什么。
- 先验证什么。
- 什么时候调用拿取。
- 失败后换候选、重试还是重规划。

Phase A 的 deterministic planner 属于高层计划器。

### 具身动作规划器

负责：

- 拿起杯子。
- 打开抽屉。
- 取出药盒。
- 关闭抽屉。

Phase A 里 fake RoboBrain / atomic executor 属于这个边界的 mock 能力层。高层计划器不能直接生成 atomic robot actions。

## 4. 新增数据结构

### HighLevelProgress

字段：

- `current_subgoal_id`
- `current_subgoal_type`
- `completed_subgoal_ids`
- `pending_subgoal_ids`
- `execution_phase`
- `replan_count`

作用：

- 回答当前大任务做到哪一个高层步骤。
- 记录哪些高层步骤已经完成、哪些还没完成。
- 标识当前是在执行中、验证中、恢复中还是重规划中。
- 记录已经重规划过几次。

### EmbodiedActionProgress

字段：

- `active_skill_name`
- `current_action_phase`
- `completed_action_phases`
- `pending_action_phases`
- `local_world_state_flags`

`local_world_state_flags` 第一版只保留：

- `container_opened`
- `holding_target`
- `target_dropped`
- `target_location_changed`

校验约束：

- 仅允许上述四个键，出现额外键时报错。

作用：

- 回答局部操作做到哪一步了。
- 记录抽屉是否已经打开。
- 记录目标是否已经拿起。
- 记录目标是否掉落或位置变化。
- 判断局部计划假设是否失效。

### RuntimeObjectUpdate

字段：

- `object_ref`
- `new_anchor`
- `new_relative_relation`
- `source`
- `timestamp`
- `reason`

作用：

- 记录当前任务内刚发生的对象状态变化。
- 例如杯子从桌上掉到地上，药盒从抽屉里掉到地面，目标位置发生变化。
- 作为后续 memory reconciliation 的输入候选。

关键边界：

- `RuntimeObjectUpdate` 是当前任务状态，不是长期记忆。
- 是否写回长期 `ObjectMemory`，仍然取决于后续 verified observation / verified evidence 和 memory reconciliation rules。

## 5. RuntimeState 目标形态

Stage 6.5 后，`RuntimeState` 至少包含：

- `current_observation`
- `selected_candidate_id`
- `selected_object_id`
- `retry_budget`
- `recent_failure_analysis`
- `task_negative_evidence`
- `candidate_exclusion_state`
- `high_level_progress`
- `embodied_action_progress`
- `runtime_object_updates`

这仍然符合原设计：

- 当前任务执行状态只归 `RuntimeState` 管。
- 长期 memory 只保存跨任务记忆。
- trace 只记录事实，不驱动状态。

## 6. TaskContext 兼容规则

`TaskContext` 不新增第二份进度字段。

允许：

```text
TaskContext.runtime_state.high_level_progress
TaskContext.runtime_state.embodied_action_progress
TaskContext.runtime_state.runtime_object_updates
```

不允许：

```text
TaskContext.task_progress
TaskContext.current_object_changes
TaskContext.embodied_progress
```

原因：

- 高层计划器只读 `TaskContext`。
- `TaskContext` 只透传 `runtime_state`。
- 如果在 `TaskContext` 顶层再复制一份进度，就会产生第二个当前任务状态源。

## 7. 兼容矩阵

### Stage 1

只是在已有 `RuntimeState` 上扩字段，不改变唯一状态主语定义。兼容。

### Stage 2

不改 `Observation` / `VerificationEvidence` 接口。对象状态变化仍然通过 observation / evidence 进入当前任务状态。兼容。

### Stage 3

不影响 object memory、task negative evidence、retrieval 规则。`runtime_object_updates` 不进入长期 memory 主表。兼容。

### Stage 4

`TaskContext` 只透传 richer runtime state，不新增顶层进度副本。兼容。

### Stage 5 / Stage 6

不重写 capability registry、deterministic 高层计划器和计划校验器。Stage 6.5 只增加未来可读的状态槽位。兼容。

### Stage 7 / Stage 8

后续 adapter、verification、failure analysis 可以读取新状态，尤其是：

- 目标仍可见但拿取失败。
- 目标掉落。
- 目标位置变化。
- 目标被遮挡。

这些判断会帮助 recovery，但不改变长期 memory 写入规则。兼容。

### Stage 10 / Stage 11 / Execute Subgoal Loop

在 implementation plan 里这是 Task 10，在 execution plan 里对应 Stage 11；核心位置都是 `execute_subgoal_loop`。

`execute_subgoal_loop` 是三类新状态真正开始推进的位置：

- 推进当前高层步骤。
- 推进当前局部动作阶段。
- 记录当前任务内对象状态变化。

这是最自然的接法，因为它已经负责 adapter call、evidence collection、verification、failure analysis 和 recovery。

### Stage 13

长期 memory 更新仍然只来自 verified evidence。`runtime_object_updates` 只是输入候选，不能直接写长期 `ObjectMemory`。兼容。

### Stage 15

后续 richer memory / LLM 高层计划器只通过 `TaskContext.runtime_state` 读取更丰富的运行时状态，不新增状态源。兼容。

## 8. 测试要求

在 `tests/test_domain.py` 新增：

- `test_runtime_state_can_hold_high_level_progress`
- `test_runtime_state_can_hold_embodied_action_progress`
- `test_runtime_state_can_hold_runtime_object_updates`
- `test_runtime_progress_is_not_long_term_memory`

在 `tests/test_parser_context.py` 新增：

- `test_task_context_passes_runtime_progress_without_creating_second_state_source`

验收命令：

```bash
./.venv/bin/pytest tests/test_domain.py tests/test_parser_context.py -q
./.venv/bin/pytest -q
./.venv/bin/ruff check .
```

核心断言：

- `RuntimeState` 能持有这些新状态。
- 这些状态不属于长期 memory。
- `TaskContext` 只透传，不复制。
- Stage 6 高层计划器和计划校验器不需要重写。

## 9. 后续实现提交建议

如果只提交文档补丁：

```bash
git commit -m "docs: add stage6.5 runtime progress patch"
```

如果同时提交代码实现：

```bash
git commit -m "feat: add runtime progress state slots"
```
