# ALFWorld Harness 导航与 Put 执行修复报告

日期：2026-07-13

状态：修复、单元测试、逐实例真环境 characterization、产品黑盒验收均已完成。

## 一、报告结论

本次改动修复的不是模型最初不会规划，而是 ALFWorld Harness 把底层执行问题错误地混入了 Agent 能力评分。

原失败 Episode 中，模型已经正确完成“找到 Pencil、拿起 Pencil、前往准确 Shelf、调用 put”的高层决策。真正的问题链是：

```text
导航把 detection 当成成功
-> Harness 在不合格姿态报告 Reached shelf
-> PutObject 只尝试一次且失败
-> Harness 只向模型返回 action_failed
-> 模型看不到准确 inventory 和对象状态
-> Harness 失败被继续累计成模型 invalid action
-> 最终分数同时混入模型能力和底层执行偶然性
```

修复后，Harness 只在准确目标、固定候选和固定预算内处理底层姿态；任何成功都必须同时通过外部返回码和真实世界终态门。Harness 自身失败会立即终止 Episode 并排除 Agent 正式评分，不再要求模型修复基础设施问题。

## 二、原问题与根因

### 2.1 导航成功误报

旧实现把以下两个事实混成一个布尔值：

```text
准确目标 metadata.visible=true
或 instance_detections2D 中存在检测框
```

真环境已经证明，同一个 event 可以出现准确 Shelf 有检测框，但 `metadata.visible=false`。旧 Harness 仍会返回 `Reached shelf 1`，因此 detection 覆盖了更严格的目标状态。

### 2.2 目标发生二次解析漂移

工具层 grounding miss 后会把原字符串继续下传；Adapter 再次解析时会去掉末尾实例编号。

```text
pencil 2 不存在
-> 去掉编号成为 pencil
-> 匹配全部 Pencil
-> 选择 pencil 1
```

这违反了模型选择准确实例、Harness 不替换目标的责任边界。

### 2.3 Put 只信任一次返回码

旧路径只在当前姿态调用一次 `PutObject`，并仅使用 `lastActionSuccess` 作为动作结论。它没有：

- 复用导航后准确目标的局部位姿上下文；
- 验证准确 held objectId；
- 验证对象是否离开 inventory；
- 验证 `isPickedUp`；
- 验证准确 parent/child membership；
- 区分安全重试与部分状态变化。

### 2.4 模型反馈丢失执行事实

内部 trace 已经拥有 THOR error、inventory、goal 和目标信息，但模型投影把多数失败统一压缩成：

```json
{"success": false, "error": "action_failed"}
```

模型无法区分对象未持有、目标不存在、需要重新导航、Harness 候选耗尽或外部状态矛盾。

### 2.5 Harness 失败污染 Agent 评分

旧 Runner 没有独立的 Harness terminal/score eligibility 控制面。低层失败会增加模型 invalid action，Episode 继续调用模型，最终仍进入 Agent 成功率分母。

### 2.6 真环境修正了一个设计假设

第一版状态门曾要求携带物移动前后的完整 parent/child 集合不变。真环境表明 Pencil 被持有并经过 Shelf 时，THOR 可能更新 `parentReceptacles` 和 `receptacleObjectIds`。

因此最终规则是：

- 成功移动：锁定 held ID、完整 inventory、对象存在、`isPickedUp=true`，并核对实际 pose；
- 失败移动：完整动作状态和 pose 都必须不变，才允许继续；
- 失败 Put：完整动作状态必须不变，才允许下一个候选；
- Put 成功：必须明确 `isPickedUp=false` 并通过准确 parent/child membership。

## 三、主要改动

### 3.1 单次权威 Grounding

新增确定性的 `SceneObjectIndex`：

- reset 后从同一个 scene metadata snapshot 建立 canonical label 到 objectId 的映射；
- 显式实例只允许 exact lookup；
- 通用类型按固定 objectId 排序选择一次；
- 导航和 Put 后续只使用锁定 objectId；
- 显式实例 miss 返回 `target_not_found`，禁止类型级 fallback。

### 3.2 严格导航门

`robot_go_to` 的成功候选现在必须在同一个最终 event 中同时满足：

1. THOR 外部返回成功；
2. requested pose 与 actual pose 一致；
3. 准确 objectId 的 `metadata.visible=true`；
4. 准确 objectId 的检测框存在且面积大于零；
5. 交付图片逐像素来自该成功 event。

候选列表一次生成并锁定，不在重试中重算漂移目标。

### 3.3 PoseContext 与 Put 执行器

导航成功后创建独立 `PoseContext`，保存：

```text
scene/goal generation
source event sequence
source frame hash
准确 anchor objectId
current actual pose
固定局部候选 tuple
候选 hash
```

`robot_manipulate(action=put)` 只使用该 context：当前姿态优先，后续只尝试同一准确 receptacle 的有限局部候选。第一个通过外部终态门的候选立即停止。

### 3.4 Put 外部终态门

Put 成功必须全部满足：

```text
PutObject return success
准确对象不在 inventory
准确对象 isPickedUp=false
准确目标在对象 parent membership 中
准确对象在目标 child membership 中
```

返回码与终态矛盾、字段缺失、部分状态变化或异常统一归类为 `execution_state_uncertain`，立即停止且不得继续重试。

### 3.5 固定生产预算

预算由六个 Shelf 的逐实例真环境实验按“最大观察值 + 预先固定 margin”推导：

| 范围 | 最大 candidates | 最大 backend actions | 最大时间 |
|---|---:|---:|---:|
| 主要导航 | 65 | 66 | 34.804 秒 |
| 局部 Put | 9 | 17 | 5.669 秒 |

`GetReachablePositions` 也计一个 THOR backend action。预算到达后不允许 N+1 请求，也不允许针对失败 Episode 临时扩大预算。

### 3.6 模型反馈与安全投影

Put 结果向模型提供稳定最小状态：

```text
action
object
target
inventory
object_state
state_changed
error
detail
最新图片
```

内部 objectId、坐标、候选列表、完整 scene 对象和专家信息不会进入模型上下文。THOR detail 中若出现这些内容，会进行确定性局部脱敏；投影自身失败会转为 terminal `unclassified_execution_failure`。

### 3.7 Episode 与 Taskset 终止控制

新增共享 `EpisodeOutcome`，记录：

```text
terminal classification
terminal tool call id
score eligibility
agent tool call count
backend action count
terminal evidence ref
```

首个 Harness terminal 发生后：

- 同一 assistant turn 的后续 robot 工具不再触碰 Adapter/THOR；
- Runner 不再调用下一轮模型；
- Episode 排除 Agent 正式分数；
- taskset 立即终止，剩余子任务标记为 `not_run_due_to_infrastructure_failure`。

只有 `agent_success` 和 `agent_model_failure` 进入 Agent 分数。

### 3.8 Coverage 与 CLI

普通 Episode 和 taskset summary 现在同时报告：

```text
agent_success_rate_on_valid
harness_valid_coverage
harness_invalid_episodes/tasksets
harness failure 分类计数
formal_score_available
```

只有 Harness coverage 为 100% 且不存在未分类执行失败时，正式分数才可用。

### 3.9 结构化执行 Trace

内部 JSONL 新增：

```text
context_created
attempt_started
move_started / move_result
put_started / put_result
state_read_started / state_read_result
context_invalidated
execution_terminal
```

事件保留锁定候选 hash、requested/actual pose、raw event ref/hash、阶段耗时、预算上限/用量和 stop reason。模型投影递归排除这些内部字段。

### 3.10 删除无效观察工具

删除 `robot_inspect_view` 的 registry、prompt、factory、executor 和对应行为测试。该工具过去不移动、不转相机、不查询状态，只重复返回旧图片。

### 3.11 真实 API 配置退出版本控制

服务器继续在 `config/homemaster.yaml` 保存本次真实 API 测试所需配置，但该文件已从 Git 索引移除并加入 `.gitignore`。仓库只保留字段齐全、认证值为占位符的 `config/homemaster.example.yaml`，新环境通过复制模板创建本地配置。

普通提交无法删除历史版本中已经出现过的认证信息；本次真实 API 测试结束后应轮换现有令牌。本文、CHANGELOG、提交信息和测试命令均不记录真实令牌。

## 四、行为变化示例

显式不存在实例：

```text
旧：pencil 2 -> pencil -> pencil 1
新：pencil 2 -> target_not_found，零 THOR 请求
```

缺少准确 Shelf 上下文：

```json
{
  "success": false,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 1",
  "inventory": ["pencil 1"],
  "object_state": "held",
  "state_changed": false,
  "error": "navigation_required"
}
```

成功 Put：

```json
{
  "success": true,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 3",
  "inventory": [],
  "object_state": "placed",
  "state_changed": true
}
```

## 五、验证结果

### 5.1 自动化测试

```text
全仓：352 passed, 1 skipped
ALFWorld benchmark 聚焦套件：121 passed
Ruff check：PASS
Ruff format --check：PASS
compileall：PASS
cleanup guard：PASS
git diff --check：PASS
```

skip 是需要显式环境开关的 live ALFWorld smoke；唯一 warning 是现有 `jieba` 对 `pkg_resources` 的弃用提示。

### 5.2 六 Shelf Characterization

同一个固定 trial、每个 Shelf 独立 reset：

| Shelf | 导航 candidates/actions | Put candidates/actions | 外部终态 | Goal |
|---|---:|---:|---|---:|
| 1 | 51 / 52 | 2 / 3 | PASS | 1/1 |
| 2 | 57 / 58 | 3 / 5 | PASS | 1/1 |
| 3 | 57 / 58 | 1 / 1 | PASS | 1/1 |
| 4 | 3 / 4 | 1 / 1 | PASS | 1/1 |
| 5 | 1 / 2 | 2 / 3 | PASS | 1/1 |
| 6 | 1 / 2 | 1 / 1 | PASS | 1/1 |

Shelf 3/4/6 又在推导出的生产预算下独立复验通过。

### 5.3 最终产品黑盒

所有代码完成后，三个独立 Xvfb 进程使用产品 `AlfworldEnvAdapter` 执行：

```text
go_to(pencil 1)
-> take
-> go_to(准确 Shelf)
-> put
```

Shelf 3/4/6 三个进程均 exit 0，并分别断言：

- THOR return success；
- Pencil 离开 inventory；
- `isPickedUp=false`；
- 准确 parent/child membership；
- goal `1/1`；
- 导航与 Put 保存图片逐像素等于最终 event frame。

## 六、评分解释变化

修复前，ALFWorld 成功率可能同时包含模型决策错误、Harness grounding 错误、导航误报和 THOR 物理执行失败，不能直接解释为模型大脑能力。

修复后：

- 模型语义错误继续返回模型反思；
- Harness/外部状态失败立即终止并排除评分；
- 有效子集成功率必须与 Harness coverage 一起报告；
- coverage 不足时，CLI 明确显示正式分数不可用。

## 七、范围与剩余边界

本次完整交付范围是 `put`。现有 `take/open/close/use/heat/cool/clean/slice` 仍走 legacy 路径。

成功 `toggle/use` 的准确终态字段尚未通过真环境正向契约，因此不能声明所有 manipulation 都已经获得相同的局部恢复和正式评分保证。更换 ALFWorld、ai2thor 或 Unity 版本后，也必须重新运行 runtime contract 和逐实例 characterization。

## 八、关键文件与证据

实现：

- `src/homemaster/benchmarking/alfworld/execution.py`
- `src/homemaster/benchmarking/alfworld/env_adapter.py`
- `src/homemaster/benchmarking/alfworld/tools.py`
- `src/homemaster/benchmarking/alfworld/runner.py`
- `src/homemaster/benchmarking/alfworld/types.py`
- `src/homemaster/tools/dispatcher.py`

设计与说明：

- `plan/V1.7/alfworld-navigation-local-pose-execution-spec.md`
- `plan/V1.7/alfworld-put-local-pose-feedback-evaluation-spec.md`
- `docs/alfworld-user-guide.md`
- `docs/architecture/alfworld-harness.md`
- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`
- `docs/pitfalls.md`

真环境证据：

- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/`
- `var/alfworld-evidence/20260712-preimplementation/product-harness-v2/`
