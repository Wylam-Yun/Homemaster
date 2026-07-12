# HomeMaster V1.7 ALFWorld Put 局部位姿、反馈与评测契约

Date: 2026-07-10

Status: 2026-07-12 设计评审意见已处理，真环境 linchpin/逐实例预算门已通过，MVP 已实现并进入最终验收

关联文档：

- `plan/V1.7/alfworld-navigation-local-pose-execution-spec.md`
- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`

## 一、目标

HomeMaster 使用 ALFWorld `AlfredThorEnv` 验证居家 Agent 的高层规划、状态判断和反思能力。

本方案解决以下混合失败：

```text
模型给出语义正确的 put
-> Harness 正确解析 Pencil 和 Shelf
-> THOR 因底层姿态或放置约束执行失败
-> Harness 只向模型返回 action_failed
-> 模型无法确认 Pencil 是否仍在手中
-> 后续状态判断和反思被底层故障污染
```

本方案要求：

1. Harness 负责同一语义目标上的低层局部位姿执行；
2. Harness 不得替模型更换对象、容器或动作；
3. 动作成功必须由外部真实终态确认；
4. 模型获得足够但不泄露答案的动作相关状态；
5. 模型错误、Harness 错误和未知错误互斥分类；
6. 排除 Harness 失败时仍必须报告覆盖率，防止 Agent 分数虚高。

## 二、当前证据与根因

关联运行：

```text
alfworld-valid_unseen-thor-objectid-20260707-001
```

关联 Episode：

```text
valid_unseen/pick_and_place_simple-Pencil-None-Shelf-308
```

### 2.1 模型的第一次 put 正确

模型调用：

```json
{
  "action": "put",
  "object": "pencil 1",
  "target_receptacle": "shelf 1"
}
```

已确认执行链：

```text
Pencil grounding 成功
Shelf grounding 成功
Shelf 被识别为 receptacle
Harness 调用 THOR PutObject
THOR 返回失败
Pencil 仍在 inventory
任务 goal 未变化
```

THOR 原始反馈：

```text
Could not place the held object on/in shelf. No valid Receptacle found
```

模型实际只看到：

```json
{"error": "action_failed", "success": false}
```

因此第一次失败不是初始规划错误、参数表达错误或 Shelf grounding 失败。

### 2.2 放置结果受具体实例和姿态影响

固定同一 trial、移除 LLM 后，已有实验结果：

| 目标 | 已观察结果 |
|---|---|
| `shelf 1` | 放置失败 |
| `shelf 2` | 放置失败 |
| `shelf 3` | 放置成功，goal `1/1` |
| `shelf 4` | 通用姿态失败，专家姿态成功 |
| `shelf 5` | 放置失败 |
| `shelf 6` | 放置成功，goal `1/1` |

`shelf 4` 已直接证明姿态可以改变 `PutObject` 结果。

该结果尚未证明 `shelf 1/2/5` 永远不可放置，也不能把 THOR 原始字符串直接解释为 Shelf 已满。

### 2.3 错误信息丢失在模型可见投影层

内部 `AlfworldStepResult` 已包含 inventory、feedback、failure reason、目标解析信息和 goal 状态。

`visual_eval` 通过 `_visual_error()` 把绝大多数失败统一映射为：

```text
action_failed
```

因此问题不是只有 THOR 报错不详细，而是模型可见结果丢失了 Harness 已掌握的状态。

### 2.4 目标发生双层解析漂移

轨迹中模型请求：

```text
robot_go_to(target="pencil 2")
```

模型却收到：

```json
{
  "success": true,
  "target": {
    "target": "pencil 2",
    "resolved_label": "pencil 1"
  }
}
```

已确认根因：

```text
工具层 grounding 找不到 pencil 2
-> method=unchanged，原字符串继续下传
-> Adapter 第二次解析字符串
-> _object_query_key() 删除末尾实例编号
-> pencil 2 退化为 pencil
-> _objects_by_type() 匹配所有 Pencil
-> _choose_object_target() 选择 pencil 1
```

这是 Harness 错误。显式选择不存在的 `pencil 2` 应返回模型错误，不能偷偷替换为 `pencil 1`。

### 2.5 `robot_inspect_view` 没有新观察

当前工具不移动、不转相机、不裁剪或放大 `focus`、不查询 inventory，只返回上一张图片并报告成功。

它向模型提供了错误的“已经重新检查”信号，放大了重复观察和状态误判。

## 三、范围与非目标

### 3.1 MVP 范围

MVP 只完整实现并验证 `put` 的局部位姿执行策略，但公共架构必须允许未来复用到：

```text
take
open
toggle/use
```

MVP 不是为每种动作分别实现一套执行循环。

### 3.2 非目标

MVP 不负责：

- 让模型控制坐标、朝向或相机俯仰角；
- 在 `put` 内重新完成全场景主要导航；
- 自动把 `shelf 1` 换成 `shelf 3`；
- 通过专家轨迹向运行时提供任务答案；
- 实现 `take/open/toggle/use` 的完整局部恢复策略；
- 修改 `task_progress_check` 的模型自我判断职责；
- 为未核对的 THOR 外部字段或 API 背书。

## 四、总体架构

### 4.1 模型可见工具

保留：

```text
robot_go_to(target)
robot_manipulate(action=..., ...)
robot_verify(...)
task_progress_check(...)
```

删除：

```text
robot_inspect_view
```

不新增 `robot_adjust_pose`、`robot_put`、`robot_query_state` 等公开工具。

`task_progress_check` 继续记录模型自己的计划和判断。判断错误本身属于反思能力；完整任务成功仍只由 `robot_verify` / ALFWorld `won=true` 确认。

### 4.2 公共执行器与动作规则

概念结构：

```text
ManipulationExecutor
  |- 公共：目标锁定、PoseContext、候选管理、重试、trace、错误分类
  |- PutActionProfile：MVP
  |- TakeActionProfile：未来扩展
  |- OpenActionProfile：未来扩展
  `- ToggleActionProfile：未来扩展
```

动作之间共用执行循环，只在位姿锚点、前置条件和终态门上不同。

| 动作 | 位姿锚点 | 动作前条件 | 动作终态 |
|---|---|---|---|
| `put` | 目标 receptacle | 指定物体在 inventory | 物体离开 inventory，并归属于准确目标 |
| `take` | 被拿取物体 | 物体可拿且手部状态允许 | 物体进入 inventory |
| `open` | 被打开对象 | 对象支持打开 | 准确对象进入打开状态 |
| `toggle/use` | 被切换对象 | 对象支持切换 | 准确对象进入预期开关状态 |

`take/open/toggle/use` 的准确外部字段不属于 MVP，目前均未完成真环境核对。

## 五、单次权威 Grounding 与目标锁定

### 5.1 只解析一次

模型目标必须经过唯一权威 Resolver，生成：

```text
ResolvedTarget
  requested_label
  resolved_label
  resolved_object_id
  resolved_kind
  grounding_method
```

`robot_go_to` 和 `robot_manipulate` 后续只使用已锁定的 `resolved_object_id`，不得再次根据模型字符串选择对象。

### 5.2 通用类型与显式实例

模型输入通用类型 `pencil` 时，Harness 可以按确定性规则选择实例，但必须返回实际选择，例如 `pencil 1`，随后锁定其 objectId。

模型输入显式实例 `pencil 2` 时，只能解析为 `pencil 2`：

```text
场景存在 pencil 2
-> 锁定 pencil 2 objectId

场景不存在 pencil 2
-> target_not_found / model_target_error
-> 返回模型更正
```

禁止：

```text
pencil 2 -> pencil -> pencil 1
```

### 5.3 Grounding 责任

```text
权威场景中确实不存在请求目标
-> model_target_error
-> 模型可见并允许更正

权威场景中存在请求目标，但 Resolver 未解析或解析错误
-> harness_grounding_failure
-> 终止 Episode 并排除评分
```

权威场景对象列表只用于归责，不直接泄露给模型。

## 六、PoseContext 与局部候选

### 6.1 一个候选池，两种成功门

在准确目标 grounding 后，Harness 为准确 objectId 一次性生成有限候选，并保存：

```text
PoseContext
  anchor_object_id
  resolved_label
  current_pose
  observation_source
  locked_candidates
  created_step
```

`robot_go_to` 使用观察成功门，`robot_manipulate(put)` 使用放置真实终态门。候选池可以复用几何候选生成能力，但两种成功门不能混为一谈。

有效 `PoseContext` 不要求模型刚刚调用过 `robot_go_to`。如果 Episode reset、上一个工具结果或当前最新图片已经通过准确目标观察门，Harness 可以基于该观察建立 `PoseContext`，不得强迫模型为了形式要求重复导航。

如果当前没有准确目标的有效观察，则返回 `navigation_required`；`robot_manipulate` 不得从远处偷偷完成主要导航。

### 6.2 固定候选顺序

```text
1. 当前姿态
2. 与当前姿态变化最小的同目标候选
3. 仍与准确目标绑定的其他附近候选
4. 达到固定候选或时间预算后停止
```

第一个通过返回码和真实终态双重验证的候选立即停止。最大候选数是上限，不是每次必须尝试的数量。

### 6.3 候选不变量

一次 `put` 调用内必须锁定：

```text
action=put
held_object_id
target_receptacle_id
PoseContext candidate list
candidate order
```

禁止每轮重新解析目标、重新生成候选、切换 Shelf、扩大成全场景导航或临时扩大正式运行预算。

### 6.4 专家姿态边界

专家姿态只用于独立正向验证。正式运行不得读取专家目标选择、专家动作序列或专家成功姿态作为当前 Episode 的答案。

## 七、Put 执行状态机

### 7.1 前置阶段

```text
1. 验证工具参数
2. 单次 grounding 并锁定 Pencil objectId
3. 单次 grounding 并锁定 Shelf objectId
4. 验证 Shelf 是 receptacle
5. 验证准确 Pencil 在 inventory
6. 验证存在与准确 Shelf 绑定的有效 PoseContext
7. 记录动作前状态
```

动作前状态至少包含 inventory、held objectId、target objectId、目标相关状态、goal 和当前姿态。

### 7.2 当前姿态优先

```text
当前姿态调用 PutObject
-> 返回码成功且外部终态成功：立即返回 success
-> 返回码失败且相关状态完全未变化：允许局部候选
-> 返回码与外部状态矛盾：立即停止
-> 发生部分状态变化：立即停止
```

### 7.3 局部候选循环

```text
移动到锁定候选
-> 核对外部移动返回码
-> 核对准确目标仍满足已验证的观察条件
-> 调用同一个 PutObject
-> 核对 PutObject 返回码
-> 核对准确 put 外部终态
-> 成功立即停止
-> 失败且状态完全未变化则继续
-> 状态矛盾或部分变化则立即停止
```

### 7.4 安全重试判据

只有以下条件全部满足才允许下一个姿态：

```text
外部 PutObject 明确失败
准确 Pencil 仍在 inventory
准确 Shelf 归属状态未变化
目标 objectId 未变化
held objectId 未变化
没有相关部分状态变化
```

THOR 原始错误字符串不能单独决定是否安全重试。

### 7.5 搜索耗尽

固定候选全部失败且状态始终未变化：

```text
final_classification=harness_operation_failure
模型 invalid_action_count 不增加
Episode 立即终止
Episode 不进入正式 Agent 分数
```

MVP 不把候选耗尽解释为 `target_unavailable`。

## 八、Put 外部真实终态门

### 8.1 成功条件

`put` 成功必须同时满足：

```text
1. THOR PutObject 返回成功
2. 准确 Pencil 不再位于 inventory
3. 准确 Pencil 的外部归属状态包含准确 Shelf objectId
```

### 8.2 状态矛盾

以下任一种情况统一分类为 `execution_state_uncertain`，立即停止且不得重试：

```text
PutObject 成功，但 Pencil 仍在 inventory
PutObject 成功，但 Pencil 不属于准确 Shelf
PutObject 失败，但 Pencil 已离开 inventory
PutObject 失败，但对象归属已经变化
```

### 8.3 动作成功与任务成功分离

```text
put 动作成功
-> 返回码 + inventory + 对象归属

完整任务成功
-> robot_verify / ALFWorld won=true
```

当前 Pencil-on-Shelf 验收可以额外断言 goal `1/1`，但 goal 不属于所有 `put` 的通用成功门。

## 九、错误分类与责任边界

### 9.1 模型可纠正错误

| 模型可见错误 | 语义 | 处理 |
|---|---|---|
| `invalid_tool_arguments` | 参数缺失或格式错误 | 返回模型更正 |
| `target_not_found` | 权威场景中确实不存在显式目标 | 返回模型更正 |
| `object_not_held` | 指定物体不在 inventory | 返回模型更正 |
| `target_not_receptacle` | 目标存在但不是可放置容器 | 返回模型更正 |
| `navigation_required` | 没有与准确目标绑定的有效 PoseContext | 返回模型先获取目标观察 |
| `action_not_applicable` | 动作与对象语义不匹配 | 返回模型更正 |

这些错误可以继续 Episode，并用于评测模型反思。

### 9.2 Harness 与未知错误

| 内部分类 | 语义 | 处理 |
|---|---|---|
| `harness_grounding_failure` | 目标存在但 Resolver 失败或漂移 | 终止、排除评分 |
| `harness_navigation_failure` | grounding 成功但导航观察门耗尽 | 终止、排除评分 |
| `harness_operation_failure` | 请求正确但局部 put 候选耗尽 | 终止、排除评分 |
| `execution_state_uncertain` | 返回码与真实终态矛盾或部分变化 | 终止、排除评分 |
| `unclassified_execution_failure` | 证据无法完成互斥归类 | 终止、排除评分并阻塞正式分数 |

Harness 失败不得映射为普通模型 `action_failed` 后继续运行。

### 9.3 MVP 禁用 `target_unavailable`

THOR 原始字符串：

```text
No valid Receptacle found
```

不能单独证明准确 Shelf 没有空间或永久不可用。

在没有独立外部证明方法前，MVP 不得生成 `target_unavailable`。未来如找到经过真环境验证的独立证据，必须重新设计并独立评审后才能启用。

## 十、模型可见反馈契约

### 10.1 三个接收方分离

```text
模型反馈
-> 稳定语义、动作相关最小状态、原始 detail

内部 trace
-> 完整 objectId、候选、坐标、外部返回、耗时、前后状态

评测记录
-> 责任分类、是否计分、覆盖率分类
```

### 10.2 成功示例

```json
{
  "success": true,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 1",
  "inventory": [],
  "object_state": "placed",
  "state_changed": true
}
```

### 10.3 模型可纠正失败示例

```json
{
  "success": false,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 1",
  "inventory": [],
  "object_state": "not_held",
  "state_changed": false,
  "error": "object_not_held",
  "detail": "No object is currently held."
}
```

### 10.4 放置失败示例

```json
{
  "success": false,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 1",
  "inventory": ["pencil 1"],
  "object_state": "held",
  "state_changed": false,
  "error": "placement_failed",
  "detail": "No valid Receptacle found"
}
```

若最终分类为 `harness_operation_failure`，Runner 必须在模型获得下一次决策机会前终止 Episode。

### 10.5 最小状态范围

模型可见：

- 当前 inventory；
- 本次动作的对象和目标；
- `object_state`；
- `state_changed`；
- 稳定 `error`；
- THOR 原始 `detail`；
- 动作后的最新图片。

模型不可见：

- 全场景对象列表；
- 隐藏对象位置；
- 候选 Shelf 排名；
- 推荐下一步动作；
- 坐标、旋转角和 THOR objectId；
- 专家目标或专家轨迹；
- 直接泄露答案的 goal 条件明细。

### 10.6 Schema 与模型说明同源

当前 `ToolSpec` 虽然存在 `output_schema`，但 ALFWorld Runner 只把工具名、description 和输入 schema 传给模型。只填写 `output_schema` 不会让模型理解错误。

实现必须同时提供：

```text
机器可验证的 output schema
严格符合 schema 的实际结果
模型可见的简短结果契约
```

模型说明可以位于工具 description 或 Episode prompt，但必须与错误目录同源生成或接受一致性测试。`detail` 只能补充底层信息，不能覆盖结构化状态和稳定错误码。

## 十一、删除 `robot_inspect_view`

MVP 删除：

- 模型可见 ToolSpec；
- Registry 注册；
- Prompt 调用指导；
- 只服务该空工具的实现和测试；
- 用户文档中的公开能力说明。

不提供改名后的旧图工具，也不新增状态查询工具。

职责归并为：

```text
robot_go_to
-> 获取准确目标的新观察

robot_manipulate
-> 执行动作并返回动作后图片和最小状态

robot_verify
-> 检查完整任务是否被 ALFWorld 确认完成
```

## 十二、模型步骤与 Harness 步骤分账

### 12.1 模型计数

一次模型调用 `robot_manipulate(put)` 只计：

```text
agent_tool_call_count += 1
```

### 12.2 Harness 计数

内部姿态移动和 `PutObject` 分别计入：

```text
backend_action_count
pose_candidates_attempted
put_attempt_count
harness_elapsed_ms
```

内部失败不增加 Agent step 或模型 `invalid_action_count`。

### 12.3 固定预算

局部执行必须同时受以下固定预算约束：

```text
最大候选数
最大底层动作数
最大总耗时
```

生产预算已由 `shelf-characterization-v3` 的六 Shelf exploration 按固定规则推导，并由 Shelf 3/4/6 production 逐实例复验：

```text
导航：max_candidates=65, max_backend_actions=66, max_wall_ms=34804
局部 put：max_candidates=9, max_backend_actions=17, max_wall_ms=5669
```

`GetReachablePositions` 计入导航 backend action；局部 put 的 backend action 只统计实际发出的 `TeleportFull` 和 `PutObject`。

正式运行开始后，预算不得根据单个 Episode 的失败临时扩大。

## 十三、Episode 终止与评分

### 13.1 Harness 失败立即终止

一旦分类为：

```text
harness_grounding_failure
harness_navigation_failure
harness_operation_failure
execution_state_uncertain
unclassified_execution_failure
```

必须立即终止 Episode、不再调用模型、不增加模型错误、排除正式 Agent 分数，并保留完整诊断 trace。

不对 Harness 失败后的模型行为计算正式反思分数。

### 13.2 模型可纠正错误继续运行

以下错误返回模型后继续：

```text
invalid_tool_arguments
target_not_found
object_not_held
target_not_receptacle
navigation_required
action_not_applicable
```

模型能否根据这些真实语义错误修正计划，属于正式反思能力。

### 13.3 分数与覆盖率同时报告

至少输出：

```text
total_episodes
agent_scored_episodes
agent_successes
agent_success_rate_on_valid
harness_invalid_episodes
harness_grounding_failures
harness_navigation_failures
harness_operation_failures
execution_state_uncertain_count
unclassified_execution_failures
harness_valid_coverage
formal_score_available
```

定义：

```text
harness_valid_coverage = agent_scored_episodes / total_episodes
```

正式、可比较的 Agent 分数要求：

```text
harness_valid_coverage = 100%
unclassified_execution_failures = 0
```

覆盖率不足时可以报告非正式结果，但必须同时显示覆盖率，不能只显示有效子集上的 Agent 成功率。

### 13.4 MVP 声明边界

只完成 `put` 后，只能声明 `put` 已具备经过验证的低层局部执行能力。不能声明所有操作默认成功，也不能声明整个 ALFWorld 已获得正式 brain-only 覆盖。

## 十四、可观测性

每次 `put` 调用写入结构化 JSONL，至少包含：

```text
episode_id
tool_call_id
requested_action
requested_object
requested_target
resolved_object_label
resolved_object_id
resolved_target_label
resolved_target_id
grounding_method
inventory_before
inventory_after
goal_before
goal_after
candidate_index
candidate_count
candidate_pose
external_move_success
external_put_success
external_error_message
object_parent_before
object_parent_after
state_changed
safe_to_retry
elapsed_ms
final_classification
score_eligible
```

候选列表只生成一次，同一次调用内 `candidate_count` 不得变化。

模型 trace 只保存模型实际看到的稳定结果和图片引用；内部 objectId、坐标和完整候选不进入模型上下文。

## 十五、真环境 Linchpin 与验收

### 15.1 外部符号状态

已在当前真环境证据中出现：

| 外部符号或行为 | 当前可确认事实 |
|---|---|
| `PutObject` | 真环境接受该动作，并出现过成功和失败 |
| `metadata.lastActionSuccess` | 真环境返回动作成功/失败状态 |
| `No valid Receptacle found` | 当前失败 trace 中真实出现 |
| inventory 状态 | 当前 trace 能区分 Pencil 是否仍被持有 |

以下仍为 **UNVERIFIED**：

| 外部符号或行为 | 实施前必须核对的内容 |
|---|---|
| `parentReceptacles` | 是否稳定表达准确 put 父容器及完整格式 |
| `receptacleObjectIds` | 是否可作为独立反向归属证据及完整格式 |
| `GetReachablePositions` | 当前运行时是否稳定接受、返回格式和失败语义 |
| 任何可交互姿态查询 API | 是否真实存在、引擎是否接受、是否适合当前对象 |
| `open` 终态字段 | 准确字段、取值和事件时序 |
| `toggle/use` 终态字段 | 准确字段、取值和事件时序 |

外部符号能 import 或出现在文档中，不等于当前引擎运行时可用。

### 15.2 Put 父容器字段核对

必须在同一 trial 中保存至少以下原始 event：

```text
shelf 3 成功
shelf 6 成功
shelf 4 专家姿态成功
shelf 1 失败
```

逐实例核对 PutObject 返回码、inventory 前后状态、Pencil 父容器相关原始字段、Shelf 子对象相关原始字段、准确 objectId 和 goal 前后状态。

不能使用“任意 Shelf 成功”代替 per-instance 断言。

### 15.3 候选预算特征实验

写实施计划前，必须对每个准确 Shelf 使用固定候选顺序，逐次记录外部返回码、真实终态、首个成功候选、底层动作数和耗时。

候选上限必须满足：

```text
所有独立已知正向实例在固定预算内成功
不存在用其他实例成功掩盖当前实例失败
运行时和动作数有明确上限
```

如果已知正向实例不能达到 100% 覆盖，必须回到设计评审。

### 15.4 独立正向基准

独立基准不得 import 或复用 HomeMaster 的 grounding resolver、PoseContext 候选顺序、put 结果分类器或 put 终态解析器。

基准使用固化的 scene、准确 objectId 和已知成功姿态，直接调用真环境并保存原始返回和终态。

### 15.5 必测冲突

至少覆盖：

```text
返回成功 + 终态成功 -> success
返回失败 + 状态完全未变化 -> 可继续候选
返回成功 + 终态失败 -> execution_state_uncertain
返回失败 + 终态已变化 -> execution_state_uncertain
显式 pencil 2 不存在 -> target_not_found
显式 pencil 2 存在但 Resolver 失败 -> harness_grounding_failure
通用 pencil -> 确定性选择并锁定实例
候选耗尽 -> harness_operation_failure，模型不再继续
```

### 15.6 模型可见反馈验收

必须检查真实发送给模型的 `model_trace`，断言模型看到稳定 error、当前 inventory、`object_state`、`state_changed`、原始 detail 和最新图片；同时看不到 objectId、坐标、专家答案和全场景对象列表。

只验证内部 `data` 字段不算通过。

### 15.7 接口一致性审计

若修改公共 ToolResult 或 ToolSpec 输出契约，必须同步所有实现、所有 provider/runtime 投影、模型可见 prompt/description，并增加 audit 测试验证所有实现覆盖完整公开字段。

## 十六、候选方案与取舍

### 16.1 同目标有界局部位姿执行（推荐并采用）

保持模型选择的准确对象、准确 Shelf 和动作不变，Harness 只处理低层姿态；代价是必须完成真环境预算、终态验收和 Harness 覆盖统计。MVP 只为 `put` 启用新执行器，其余公开动作保持现有路径。

### 16.2 原始错误直通并由模型恢复

改动最小，但把低层物理偶然性混入模型反思，且字符串不能证明安全重试或目标永久不可用。结论：原始错误只作为经过安全投影的 `detail`，不作为执行策略。

### 16.3 Harness 自动更换目标实例

容易提高成功率，但替模型完成 Shelf 实例选择并破坏已锁定语义目标。结论：禁止。

### 16.4 预认证目标集或提升为语义类型

可以减少 Harness invalid Episode，但会过滤模型可寻址实例，或把准确实例选择从模型职责移交给 Harness。结论：不采用；保留准确实例由模型选择，覆盖不足时如实令正式分数不可用。

## 十七、实施硬门（已完成）

以下硬门已于 2026-07-12 完成，实施按用户指示开始：

进入实施计划前必须完成：

1. 独立设计评审及逐条 disposition；
2. `PutObject`、父子归属、inventory、`isPickedUp`、实际 pose、bbox/frame 的真环境核对；
3. 六 Shelf exploration 与 Shelf 3/4/6 production 的逐实例候选预算实验；
4. 明确候选数、动作数和时间预算写回；
5. 独立证据脚本不 import HomeMaster resolver/执行器；
6. 普通 Episode、taskset、provider/runtime 投影和终止控制面测试。

证据根目录：`var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/`。

## 十八、用户已确认的决策

截至 2026-07-10，用户已确认：

1. MVP 先完整解决 `put`，公共接口预留其他动作；
2. Harness 在操作时围绕同一目标做局部位姿调整；
3. 第一个真实成功姿态立即停止；
4. 模型反馈包含稳定语义、最小状态和 THOR 原始 `detail`；
5. `target_unavailable` 在没有独立证明前禁用；
6. Harness 失败立即终止并排除正式评分；
7. 删除 `robot_inspect_view`；
8. inventory 随每次操作结果返回；
9. 保留 `task_progress_check` 的模型自我判断职责；
10. 显式实例编号严格匹配，目标只解析一次并锁定 objectId；
11. 一次模型工具调用只计一个模型步骤，内部动作单独计数；
12. Agent 分数必须与 Harness 覆盖率同时报告；
13. `put` 成功要求返回码、inventory 和准确对象归属一致；
14. 局部执行复用准确目标 `PoseContext` 的锁定候选池；
15. 独立评审暂缓，但实施前仍为硬门。

## 十九、相关代码与证据

- `src/homemaster/benchmarking/alfworld/env_adapter.py`
  - `manipulate_with_thor()`
  - `_resolve_navigation_target()`
  - `_objects_by_type()`
  - `_object_query_key()`
  - `_single_action_result()`
- `src/homemaster/benchmarking/alfworld/tools.py`
  - `_exec_manipulate()`
  - `_ground_target()`
  - `_visual_tool_result()`
  - `_visual_error()`
  - `make_alfworld_robot_manipulate()`
  - `make_alfworld_robot_inspect_view()`
- `src/homemaster/benchmarking/alfworld/grounding.py`
- `src/homemaster/benchmarking/alfworld/prompt.py`
- `src/homemaster/benchmarking/alfworld/registry.py`
- `src/homemaster/tools/spec.py`
- `var/alfworld-trace/test/alfworld-valid_unseen-thor-objectid-20260707-001/episode-0006/`

## 二十、第一轮独立评审后的可执行性修订

本节补齐原方案的控制面和穷尽契约。若与前文的概念性描述冲突，以本节为准。本节不改变用户已确认的上游边界：模型仍选择准确实例，Harness 不换目标，MVP 只完整实现 `put`。

### 20.1 Put 状态向量与穷尽转移表

每次读外部状态必须返回：

```text
ExternalRead
  status = ok | error | missing
  raw_event_ref
  raw_event_hash
  inventory_object_ids
  held_object_id
  exact_object_present
  object_parent_ids
  target_child_ids
  actual_agent_pose
  goal_summary
  exact_object_is_picked_up
```

以下字段在真环境核对完成前全部为 **UNVERIFIED**：准确 inventory objectId 及事件时序、准确对象是否仍存在、`parentReceptacles`、`receptacleObjectIds`、实际 agent pose 字段和时序。

Put 失败重试使用的完整动作状态向量固定为：

```text
S = (
  exact held objectId,
  full inventory objectId tuple,
  exact object's parent tuple,
  exact target's child tuple,
  exact object present flag
)
```

只有 `before_read.status=ok`、`after_read.status=ok` 且整个 `S_after == S_before` 时，Put 失败才叫“外部状态完全未变化”。goal、图片和错误字符串只作正交诊断，不替代该等式。

成功移动使用更窄的持有不变量：准确 held ID、完整 inventory、准确对象仍存在、`isPickedUp=true`，再独立核对 actual pose。真环境已证明，携带 Pencil 经过 Shelf 时 `parentReceptacles/receptacleObjectIds` 可能变化，因此成功移动不得要求完整父子集合不变；失败移动仍要求完整 `S` 与 pose 都不变。

Put 转移表：

| 外部调用 | 动作后读取 | 准确终态 | 分类 | 是否重试 |
|---|---|---|---|---|
| 明确成功 | `ok` | 返回成功、准确物体离开 inventory、准确父容器包含准确 Shelf | `success` | 否，立即停止 |
| 明确成功 | `ok` | 三项终态任一不满足 | `execution_state_uncertain` | 否 |
| 明确失败 | `ok` | `S_after == S_before` | 当前候选失败 | 是，仅可进入下一个锁定候选 |
| 明确失败 | `ok` | `S_after != S_before` | `execution_state_uncertain` | 否 |
| 抛异常、超时、无 event 或无返回码 | 任意 | 任意 | `execution_state_uncertain` | 否 |
| 任意 | `error` 或 `missing` | 无法证明 | `execution_state_uncertain` | 否 |
| 任意 | `ok` | 准确对象、held ID 或目标对象无法读取 | `execution_state_uncertain` | 否 |

候选移动转移表：

| 移动返回 | 实际 pose | `S` | 处理 |
|---|---|---|---|
| 成功 | 与请求 pose 满足真环境核对后的容差 | held ID、完整 inventory、对象存在、`isPickedUp=true` | 才允许调用 PutObject；父子集合可因空间重叠变化 |
| 失败 | 与移动前 pose 相同 | 完整 `S` 未变化 | 记录该候选 move failure，可继续下一个锁定候选 |
| 成功但实际 pose 不符，或失败但实际 pose 已变 | 任意 | 任意 | `execution_state_uncertain`，停止 |
| 异常、超时、无返回、pose/状态读取失败 | 不可证明 | 不可证明 | `execution_state_uncertain`，停止 |

移动请求、返回、实际落点依赖的 THOR 字段、容差与事件时序均为 **UNVERIFIED**，必须由第十五节实验确定。任何未列出的组合进入 `unclassified_execution_failure` 并立即停止，不能默认重试。

### 20.2 `NavigationSearchContext` 与 `PoseContext` 生命周期

主要导航和操作局部执行使用两个不同上下文，避免把导航前候选池误当作导航后局部候选池：

```text
NavigationSearchContext
  context_id
  scene_generation
  goal_generation
  anchor_object_id
  locked_navigation_candidates
  candidates_hash

PoseContext
  context_id
  scene_generation
  goal_generation
  source_event_sequence
  source_frame_hash
  anchor_object_id
  current_actual_pose
  locked_local_candidates
  candidates_hash
  created_tool_call_id
```

`PoseContext` 只可从通过准确目标观察门的同一个 event 创建。候选池在创建时一次生成并排序；执行期间只更新“当前实际 pose”和已尝试索引，不重算候选、不改变 hash。

以下事件立即使 `PoseContext` 失效并写 `context_invalidated`：Episode reset、scene generation 改变、`advance_goal` 导致 goal generation 改变、任何不属于当前 context 的后端移动、anchor/object 消失、任何相关 manipulation 状态变化、实际 pose 与请求矛盾、frame/event 身份无法核对。普通模型文本、`task_progress_check` 和不改变环境的 verify 不会续期或重建 context。

`metadata.agent` 中实际位置/旋转/horizon 的准确字段与时序为 **UNVERIFIED**；核对前不能把上述名字视为可用实现符号。

### 20.3 权威场景索引与一次解析

Episode reset 后从模型不可见的同一 scene metadata 建立版本化 `SceneObjectIndex`：

```text
SceneObjectIndex
  scene_generation
  snapshot_event_sequence
  canonical_label -> exact objectId
  normalized_type -> deterministically ordered canonical labels
```

canonical label 的实例编号来自同一 snapshot 内按 `(normalized object type, objectId string)` 的稳定排序。显式 `pencil 2` 只允许 exact canonical-label lookup；不存在即以该索引的独立查询结果生成 `target_not_found`。通用 `pencil` 只按固定排序选一次并锁定。可见性、当前时间、集合迭代顺序和 LLM 输出不得参与身份选择。

语义 judge 只能提出数据文件中已审核 alias 的候选，不能返回 executable objectId，也不能覆盖显式实例 exact 失败。若索引包含目标而 resolver 没有给出相同 objectId，分类为 `harness_grounding_failure`。索引和 resolver 的查询结果分别写 trace，避免用 resolver 自己证明“场景不存在”。

### 20.4 权威 Episode 终止控制面

Runner 持有单一 `EpisodeOutcome`：

```text
EpisodeOutcome
  terminal
  classification
  terminal_tool_call_id
  score_eligible
  agent_tool_call_count
  backend_action_count
  terminal_evidence_ref
```

首个 Harness terminal classification 原子写入该对象。工具调度器在执行每一个环境工具前检查它；同一 assistant turn 中排在其后的工具调用不得触碰 Adapter/THOR，只返回内部 `episode_terminated` 结果，且不增加 Agent invalid、环境 step 或 backend action。Generic runtime 在首个 terminal tool result 后停止派发并由 Runner 读取同一个 `EpisodeOutcome` 结束 Episode。

互斥最终分类及计分规则：

```text
agent_success
agent_model_failure
harness_grounding_failure
harness_navigation_failure
harness_operation_failure
execution_state_uncertain
unclassified_execution_failure
provider_failure
runtime_failure
artifact_failure
cancelled
```

只有 `agent_success` 和 `agent_model_failure` 进入 Agent 分数；其他分类均 `score_eligible=false`。`target_not_found` 等模型可纠正错误是中间工具状态，不是自动 terminal；若最终耗尽模型预算，才归 `agent_model_failure`。分类冲突按“外部状态不确定 > Harness 明确失败 > provider/runtime/artifact > Agent 结果”优先，确保每个 Episode 唯一归类。

长程 taskset 中任一 subtask 出现非 Agent terminal 时，立即终止整个 taskset；当前 subtask 记录准确基础设施分类，未执行 subtask 标记 `not_run_due_to_infrastructure_failure`，整个 taskset 不进入正式 Agent 分数。

### 20.5 MVP 与现有公开动作的闭合边界

MVP 只把 `action=put` 路由到新 `ManipulationExecutor + PutActionProfile`。现有公开 `take/open/close/use/heat/cool/clean/slice` schema 和 legacy 行为保持不变；不得在本改动中删除、改名或静默迁移。

未来 `ToggleActionProfile` 对应当前公开 action 名 `use`；MVP 不新增公开 `toggle` enum。`robot_go_to` 和 `robot_manipulate` 名称、输入入口保持不变，`robot_inspect_view` 按第十一节删除。

接口一致性审计是无条件 DoD，必须枚举并检查：benchmark `ToolSpec`、runtime `ToolSpec` 投影、`ToolResult`、`ToolResultMessage`、visual/textual provider 投影、registry、prompt/description、普通 Episode Runner、长程 Taskset Runner，以及所有 adapter/test double 实现。

### 20.6 THOR detail 安全投影

内部 trace 和 raw event artifact 保存 THOR 原始 detail 全文。模型可见 `detail` 在原文不含禁止信息时逐字返回；若检测到 objectId 形态、坐标、完整候选/对象列表或专家标记，则只对禁止片段做确定性替换，并增加：

```json
{"detail_redacted": true}
```

稳定错误码、inventory、`object_state` 和 `state_changed` 不依赖 detail 解析。外部错误字符串的所有可能格式仍为 **UNVERIFIED**；真环境样本核对之外，还必须用含 objectId、坐标、候选列表和专家答案的合成 detail 做负向泄密测试。若安全投影自身失败，模型只收到稳定错误码和通用安全 detail，Episode 分类为 `unclassified_execution_failure` 并停止。

### 20.7 全实例实验与覆盖语义

准确 Shelf 实例选择继续属于模型；不预过滤模型可寻址实例、不提升为语义 Shelf 类型、不自动换实例。这是用户已确认边界，而非待选 mode。

预算实验必须对目标 trial 的 `shelf 1` 到 `shelf 6` 逐实例独立 reset、独立执行和独立断言。已知正向 `shelf 3/4/6` 必须全部在同一固定预算内成功；其他实例的成功或失败也必须原样记录，不用 best/any 聚合。某准确实例候选耗尽仍归 `harness_operation_failure`，导致该 Episode 不计分并降低 Harness coverage；因此正式分数可能不可用，这是保留模型准确实例职责的明确代价。

### 20.8 预算与计数统一

导航和局部操作分别有固定的：最大候选数、最大 backend action 数、最大 wall-clock 时间。具体数值只能由逐实例真环境特征实验写回；在数值写回并通过第二轮评审前禁止实现。

计数语义固定为：

```text
agent_tool_call_count = 模型发出的公开工具调用数
backend_action_count = 发给 THOR 的每一个外部动作请求数
env_step_count = ALFWorld 任务层环境推进计数
pose_candidates_attempted = 实际开始的候选数
put_attempt_count = 实际发出的 PutObject 数
```

一个模型工具调用可以包含多个 backend action，但只增加一次 `agent_tool_call_count`。预算终止优先级为：外部不确定/状态矛盾、动作成功、backend action 上限、wall-clock 上限、候选上限；任何预算耗尽均停止，不动态扩大。

### 20.9 可复盘结构化事件

每次执行按顺序写 JSONL：`context_created`、`attempt_started`、`move_started`、`move_result`、`put_started`、`put_result`、`state_read_started`、`state_read_result`、`context_invalidated`、`execution_terminal`。除第十四节字段外还必须包含：

```text
context_id
scene_generation
goal_generation
source_event_sequence
locked_candidates_hash
attempt_id
attempt_phase
requested_pose
actual_pose
raw_event_ref
raw_event_hash
budget_limit
budget_used
budget_stop_reason
move_elapsed_ms
put_elapsed_ms
state_read_elapsed_ms
```

`context_created` 保存完整锁定候选到内部 artifact 并在 JSONL 引用其 hash；模型 trace 不含候选、pose、objectId 或 raw artifact。

### 20.10 新增真环境 UNVERIFIED 审计项

除第 15.1 节原表外，以下实现 linchpin 均为 **UNVERIFIED**：

- `inventoryObjects` 中准确 held objectId 的字段、格式和事件时序；
- 准确对象在 metadata 中消失/存在的语义；
- 当前与动作后 actual agent position/rotation/horizon 字段；
- `TeleportFull` 完整请求、返回码、实际落点和容差；
- `PutObject` 请求中的 `objectId`、`receptacleObjectId`、`forceAction`、`placeStationary` 在当前运行时的接受状态和效果；
- 异常、超时、无 event、post-state 读取失败时当前封装层的真实行为；
- THOR `errorMessage` 是否可能包含 objectId、坐标或其他禁止信息。

代码中出现或历史运行曾接受某动作，只能证明“见过”，不能证明上述完整契约可用。

### 20.11 2026-07-12 第一轮评审意见处理

| 评审意见 | 主 Agent 处理 |
|---|---|
| `execution_state_uncertain` 转移不完备 | 采纳；20.1 增加完整状态向量、调用/读取失败和移动矛盾表，缺证据一律不重试 |
| Harness terminal 缺控制面且同 turn 后续工具仍可能执行 | 采纳；20.4 增加权威 `EpisodeOutcome`、调度前 gate 和同批取消契约 |
| `PoseContext` 生命周期和导航候选池混淆 | 采纳；20.2 分离两个 context，增加 generation、event/frame 身份、hash 和失效规则 |
| 权威 Resolver 缺确定性独立来源 | 采纳；20.3 增加 reset snapshot 的 `SceneObjectIndex`，显式实例 exact，LLM 不决定 identity |
| 公共 `robot_manipulate` 其他动作未闭合 | 采纳；20.5 明确仅 put 迁移，其余 legacy 不变，并把接口审计设为无条件 DoD |
| raw detail 与不泄密冲突 | 采纳；20.6 内部保留原文，模型侧逐字安全通过或最小确定性脱敏，并加 tainted negative gate |
| 已知正向门不能覆盖模型可寻址动作域 | 采纳问题，不采纳改变模型实例职责的推荐；20.7 明确全六实例实验及 coverage 代价，理由是用户已锁定准确实例由模型选择、Harness 不换 Shelf |
| 导航也缺 backend action/time 预算 | 采纳；20.8 与导航文档修订统一两层固定预算和计数 |
| 可观测性无法证明锁定目标和实际落点 | 采纳；20.9 增加 context/attempt/move/put/read/terminal 事件和 raw artifact hash |
| Episode outcome/coverage 非互斥完备且未覆盖 taskset | 采纳；20.4 增加穷尽分类、优先级、计分资格和 taskset 传播 |
| 额外外部字段/API 未标 UNVERIFIED | 采纳；20.10 全部补标，真环境前不背书 |

## 二十一、2026-07-12 最终真环境核对与预算写回

本节记录实施使用的最终证据；对 MVP 涉及符号的状态以本节为准，覆盖第 15.1、20.10 节的实施前 `UNVERIFIED` 标记。

运行环境：

```text
alfworld==0.5.0
ai2thor==2.1.0
Python 3.11.15
trial=valid_unseen/pick_and_place_simple-Pencil-None-Shelf-308/
      trial_T20190908_122154_042763/traj_data.json
```

已真环境核对：`GetReachablePositions`、`TeleportFull` 返回码与实际 pose、`event.frame` 的 RGB/PNG 像素一致性、准确 objectId 的正面积 bbox、`inventoryObjects`、`isPickedUp`、`parentReceptacles`、`receptacleObjectIds`、`PutObject` 请求与返回码。`LookUp/LookDown` 的返回 event 在当前版本报告底层 `TeleportFull`，stale-event 门只允许该已取证映射。`toggle/use` 成功终态仍未核对，不属于本 MVP。

六 Shelf exploration 均逐实例通过导航、Put 返回码和外部终态门：

| Shelf | 导航 candidates/actions | put candidates/actions | goal |
|---|---:|---:|---:|
| 1 | 51 / 52 | 2 / 3 | 1/1 |
| 2 | 57 / 58 | 3 / 5 | 1/1 |
| 3 | 57 / 58 | 1 / 1 | 1/1 |
| 4 | 3 / 4 | 1 / 1 | 1/1 |
| 5 | 1 / 2 | 2 / 3 | 1/1 |
| 6 | 1 / 2 | 1 / 1 | 1/1 |

每个实例均满足：`PutObject.lastActionSuccess=true`、Pencil 离开 inventory、`isPickedUp=false`、准确 Shelf 出现在 Pencil parent membership、Pencil 出现在准确 Shelf child membership、goal `1/1`。Shelf 3/4/6 又在推导出的 production 预算下独立复验通过。

生产预算选择规则是“六实例每字段最大观察值 + 预先固定 margin，并受 exploration ceiling 限制”：

```text
navigation = candidates 65, backend actions 66, wall 34804 ms
local put  = candidates 9,  backend actions 17, wall 5669 ms
```

权威 artifact：

- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/summary.json`
- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/production_budget.json`
- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/shelves/`
