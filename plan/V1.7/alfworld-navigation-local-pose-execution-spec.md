# HomeMaster V1.7 ALFWorld 导航与局部位姿执行方案

Date: 2026-07-10

Status: 2026-07-12 设计评审、真环境 linchpin 和逐实例固定预算门均已完成；导航实现及 production 验证已通过

## 一、目标与范围

HomeMaster 使用 ALFWorld `AlfredThorEnv` 验证 Agent 在居家场景中的高层规划与反思能力。

本评测不准备衡量：

- 连续坐标控制；
- 路径搜索；
- 相机朝向和俯仰角控制；
- 机械操作轨迹；
- 碰撞规避和物理落点搜索。

模型负责：

- 选择目标物体；
- 选择目标位置、容器或设备；
- 决定动作和动作顺序；
- 根据真实工具结果调整高层计划。

Harness 负责：

- 把模型给出的语义名称解析到准确环境对象；
- 完成主要导航；
- 选择底层位置、朝向和视角；
- 在操作前进行必要的同目标局部位姿调整；
- 执行动作并核对外部真实终态。

本文分为两层：

1. **阶段 1，先进入真环境 linchpin 核对**：修正 `robot_go_to` 的成功门、失败归责、观察交付和验收漏洞；外部符号核对通过后才能写实施计划。
2. **阶段 2，只记录已确认不变量**：`robot_manipulate` 可以围绕同一个动作目标做局部位姿微调，但具体重试条件、耗尽后的反馈和评分归责留到下一问题讨论。

## 二、已确认的当前问题

关联运行：

```text
alfworld-valid_unseen-thor-objectid-20260707-001
```

关联 Episode：

```text
valid_unseen/pick_and_place_simple-Pencil-None-Shelf-308
```

模型调用：

```json
{"target": "shelf 1"}
```

Harness 已正确解析到准确 Shelf objectId。真环境同一个 event 中出现：

```text
TeleportFull 返回成功
instance_detections2D 中存在 shelf 1 的检测框
shelf 1 的 metadata.visible=false
```

当前 `_target_visibility_score()` 在检测框存在时把 `visible` 强制改成 `true`，随后 `_teleport_to_targets()` 立即返回成功。模型最终收到：

```json
{"success": true, "target": {"resolved_label": "shelf 1"}}
```

当前实现混淆了四个事实：

```text
底层传送命令执行成功
!= 目标在渲染图片中出现
!= 目标满足 robot_go_to 的成功契约
!= 后续操作一定成功
```

该问题不是：

- Shelf 名称解析失败；
- `robot_go_to` 不支持 Shelf；
- Harness 把 Shelf 当成普通物体；
- 模型没有调用导航或放置动作。

## 三、已经确认的总体分工

### 3.1 公开工具不拆分

模型可见工具保持：

```text
robot_go_to(target)

robot_manipulate(
  action="take|put|open|toggle",
  ...
)
```

不新增：

```text
robot_take
robot_put
robot_open
robot_toggle
robot_adjust_pose
```

模型不得输出坐标、旋转角度、相机俯仰角或 THOR 底层动作。

### 3.2 主要导航与局部微调

`robot_go_to` 负责：

```text
从当前位置完成主要导航
-> 到达模型指定目标附近
-> 让模型获得该准确目标的当前观察
```

`robot_manipulate` 负责：

```text
在模型已有当前目标观察的前提下
-> 先使用当前姿态执行动作
-> 后续阶段可围绕同一个动作目标做有界局部微调
```

操作执行器不得从场景任意位置自动完成全程导航，也不得替模型更换目标实例。

### 3.3 模型不必机械地重复 `robot_go_to`

“模型已有当前目标观察”不等于“模型刚刚必须调用过 `robot_go_to`”。有效观察也可能来自：

- Episode reset 返回的初始图片；
- 上一个工具返回的最新图片；
- `robot_go_to` 返回的图片。

如果初始图片已经清楚包含 Pencil，模型可以直接执行 `take`，不需要为了满足形式要求重复调用 `robot_go_to`。

如果当前有效观察不包含动作所需目标，`robot_manipulate` 不得从远处偷偷完成全程导航。后续阶段应返回稳定的语义状态，例如 `navigation_required`；该公开反馈格式在下一问题中设计。

## 四、外部符号核对状态

### 4.1 已有真环境证据

以下字段或动作已在当前问题的真环境 event、trace 或黑盒脚本中实际出现：

| 外部符号 | 当前可确认事实 |
|---|---|
| `TeleportFull` | 真环境接受该动作，并出现成功返回 |
| `metadata.lastActionSuccess` | 真环境返回成功/失败状态 |
| 对象 `metadata.visible` | 与检测框发生过真实冲突 |
| `instance_detections2D` | 真环境包含准确 Shelf objectId 的检测框 |
| `PutObject` | 真环境接受该动作，并返回过失败 |

这些事实只证明当前安装环境中的已观察行为，不自动证明它们在所有版本、所有对象和所有动作中具有更强语义。

### 4.2 仍需真环境核对

以下内容在作为发布 linchpin 前均标记为 **UNVERIFIED**：

- `GetReachablePositions` 作为稳定外部运行时契约及其完整返回格式；
- `parentReceptacles` 作为 `put` 成功的稳定终态字段；
- `instance_detections2D` 检测框的准确格式、坐标含义和正面积判据；
- `instance_detections2D` 与同一个 event RGB frame 的对应关系；
- `event.frame` 在当前运行时中的可用性、像素格式和保存后的一致性；
- `open` 的准确状态字段和取值；
- `toggle` 的准确状态字段和取值；
- 任何尚未在当前环境执行过的“可交互姿态查询”API。

阶段 1 实施前必须保存一份真环境证据表，至少包含：

```text
环境和依赖版本
实际请求
外部返回码
真实返回字段
目标 objectId
外部终态
证据文件路径
```

## 五、阶段 1：`robot_go_to` 契约

### 5.1 Grounding 与责任归属

模型给出目标名称后，Harness 先尝试解析：

```text
requested_target
resolved_label
resolved_kind
resolved_object_id
```

不能把所有 grounding 失败都归责给模型。评测器必须使用模型不可见的权威场景状态进行归因：

```text
目标在场景中确实不存在，或模型参数缺失/不合法
-> model_target_error
-> 模型可见并允许更正

目标在场景中存在，但解析器无法解析、解析错误或解析结果不确定
-> harness_grounding_failure
-> 基础设施失败，不扣模型分
```

权威场景状态只用于归责和评测，不得把隐藏对象列表直接泄露给模型。

### 5.2 准确目标锁定

Grounding 成功后，本次调用锁定 `resolved_object_id`。

例如模型选择 `shelf 1`：

- `shelf 2` 更清晰，不能把 `shelf 1` 判定成功；
- `shelf 3` 更适合放置，不能自动改成 `shelf 3`；
- 目标切换只能由模型下一次工具调用明确提出。

### 5.3 候选列表必须有限、确定且一次生成

阶段 1 不新增第二套候选生成算法，沿用当前 `_teleport_candidates()` 的有限搜索空间：

```text
最多 12 个邻近可达位置
x 最多 8 个朝向
x 最多 8 个俯仰角
= 每个准确目标最多 768 个原始候选
```

实际候选数可因去重和角度过滤而减少。

要求：

1. 目标 objectId 只解析一次并锁定；
2. 候选列表只生成一次并锁定；
3. 重试不能重新计算一个漂移目标；
4. 不得增加无上限循环；
5. 每次调用记录总候选数、实际尝试数和总耗时。

`GetReachablePositions` 的真环境返回契约仍为 **UNVERIFIED**，必须先完成第四节规定的证据核对；核对失败则不得把当前候选生成器视为可发布方案。

### 5.4 成功门必须同时满足三个条件

`robot_go_to` 的目的既包括导航，也包括向模型交付准确目标观察。因此成功候选必须同时满足：

```text
1. TeleportFull 的 metadata.lastActionSuccess=true
2. 最终 event 中准确 resolved_object_id 的 metadata.visible=true
3. 同一个最终 event 的 instance_detections2D 包含准确 resolved_object_id，且检测框面积大于 0
```

三个条件的职责不同：

- 条件 1：证明底层传送命令被外部引擎接受；
- 条件 2：证明准确目标通过 Harness 采用的严格导航可见性门；
- 条件 3：证明返回给模型的渲染观察中确实包含准确目标区域。

`metadata.visible=true` 不能命名为 `interaction_ready`，也不能承诺后续 `PutObject` 成功。

第三项成功门依赖的检测框格式、检测框与 RGB 的对应关系及 `event.frame` 仍为 **UNVERIFIED**。阶段 1 只能先执行真环境证据核对；三项门全部核对通过后，才能进入代码实现。若核对结果否定该对应关系，必须回到设计评审，不能用当前假设继续实现。

### 5.5 搜索行为

候选按锁定顺序依次执行：

```text
候选 A：传送失败
-> 继续

候选 B：传送成功、检测框存在、metadata.visible=false
-> 继续

候选 C：传送成功、metadata.visible=true、准确检测框存在
-> 成功并停止
```

当前实现因候选 B 提前返回成功，正是阶段 1 要修复的根因。

### 5.6 模型观察必须来自同一个成功 event

`robot_go_to` 返回成功时，模型收到的图片必须来自满足三项成功门的同一个最终 event。

本节是目标契约，不代表 `event.frame` 相关外部行为已经验证。其真环境 linchpin 状态见第四节。

必须保证：

```text
保存的 frame_path 真实存在且文件非空
保存图片的像素对应最终成功 event 的 frame
模型 tool_result 引用了该 frame_path
model_trace 中记录了同一 frame_path 或同一内容哈希
```

不能出现：

```text
引擎终态已经正确
但模型仍收到上一个候选或旧步骤的图片
```

### 5.7 失败分类

```text
模型目标确实不存在或参数不合法
-> model_target_error
-> 模型可见并允许更正

目标存在但 Harness grounding 失败
-> harness_grounding_failure
-> 基础设施无效

Grounding 成功，但所有有限候选都无法满足三项成功门
-> harness_navigation_failure
-> 基础设施无效
```

`harness_grounding_failure` 和 `harness_navigation_failure`：

- 不增加模型 `invalid_action_count`；
- 不映射为普通 `action_failed`；
- 不要求模型修复底层执行；
- 不进入正式 Agent 分数分母；
- 不通过重新调用一次 LLM 并取最好结果来掩盖失败。

### 5.8 搜索耗尽后的状态

阶段 1 中，基础设施无效 Episode 不再交给模型继续执行。因此搜索耗尽后不重放所谓“最佳姿态”。

Harness 只需要保存诊断证据：

```text
最佳候选的动作参数
最佳候选 event 的目标状态
最佳候选图片或图片哈希
最终候选的外部返回
失败分类
```

只有未来明确允许模型在导航失败后继续时，才另行设计姿态恢复和恢复后的外部终态门。

## 六、阶段 2 预留：操作时的同目标局部位姿微调

本节只记录用户已经确认的结构性不变量，不批准具体重试算法。

### 6.1 不变量

1. 公开层仍只有一个 `robot_manipulate`；
2. 操作首先使用当前姿态；
3. 模型必须已有当前有效目标观察；
4. 局部调整不得变成从任意位置重新做全场景导航；
5. 一次操作调用内锁定动作和所有语义目标；
6. Harness 不得替模型更换物体、容器、设备或动作；
7. 模型不控制坐标、朝向和俯仰角；
8. 任何操作成功都必须由外部真实终态证明。

### 6.2 动作的位姿锚点

“同一个目标”指动作的位姿锚点，而不是把所有参数错误地与最后一次导航 objectId 比较。

| 动作 | 位姿锚点 | 其他必须锁定的对象 |
|---|---|---|
| `take` | 被拿取物体 | 可选来源容器 |
| `put` | 目标 receptacle | 被放置物体，且该物体应在 inventory |
| `open` | 被打开对象 | 无 |
| `toggle` | 被切换对象 | 无 |

例如：

```text
put(pencil 1, shelf 1)
```

局部位姿围绕 `shelf 1` 调整；`pencil 1` 单独锁定并检查持有状态。不得拿 `pencil 1` 与 `robot_go_to("shelf 1")` 的 Shelf objectId 做相等比较。

### 6.3 局部范围的结构约束

阶段 2 的设计必须引入内部 `PoseContext`，至少保存：

```text
位姿锚点 objectId
当前有效观察来源
当前姿态
与该锚点关联的有限候选集合
创建步骤
```

局部微调只能使用：

- 当前姿态；
- 当前 `PoseContext` 中与同一位姿锚点绑定的有限候选。

不得在 `robot_manipulate` 内重新读取全场景并生成一套新的主要导航计划。

如果 reset 或当前工具图片已经包含动作位姿锚点，可以从当前观察建立 `PoseContext`，不要求模型重复调用 `robot_go_to`。

如果没有匹配的有效 `PoseContext`，操作执行器不得偷偷全程导航；模型可见反馈格式留到下一问题决定。

### 6.4 下一问题必须决定的内容

在阶段 2 进入实施前，还必须独立设计并评审：

1. 什么外部失败可以判定为“位姿相关且可安全重试”；
2. 局部候选的最大数量、空间范围和时间预算；
3. 动作产生部分状态变化时如何停止和恢复；
4. 所有局部候选失败后，是反馈模型换目标还是判 Harness 失败；
5. `take`、`put`、`open`、`toggle` 各自的真实终态字段；
6. 模型可见的 inventory、目标状态和失败分类。

这些内容不属于阶段 1 实施范围。

## 七、模型与 Harness 责任表

| 场景 | 责任归属 | 处理原则 |
|---|---|---|
| 模型选择确实不存在的目标 | 模型输入 | 返回明确错误，允许模型更正 |
| 目标存在但解析器失败或不确定 | Harness | Grounding 基础设施失败 |
| 模型选择真实存在的具体目标 | 模型高层决策 | Harness 锁定目标，不得替换 |
| 主要导航的位置、朝向和俯仰角 | Harness | 内部有限搜索 |
| 目标存在但 Harness 找不到合格导航终态 | Harness | 导航基础设施失败 |
| 操作前的同目标局部位姿微调 | Harness | 阶段 2 当前只确认边界 |
| 模型选择错误动作或错误语义对象 | 模型 | 返回真实语义错误，允许反思 |
| Harness 返回成功但外部世界未变化 | Harness 严重错误 | 必须由黑盒终态门阻止 |

## 八、可观测性

### 8.1 导航候选日志

每个导航候选写入结构化 JSONL，至少记录：

```text
episode_id
tool_call_id
requested_target
resolved_object_id
candidate_index
candidate_count
candidate_action
external_action_success
exact_target_metadata_visible
exact_target_detected_2d
detection_bbox_area
external_error_message
elapsed_ms
final_classification
```

导航候选日志不重复记录 inventory 和 goal；这些字段与单个导航候选无关。

### 8.2 操作日志

操作阶段未来单独记录：

```text
inventory_before/after
目标状态 before/after
动作返回码
state_changed
goal_before/after
是否安全重试
最终分类
```

### 8.3 模型反馈与内部 trace 分离

- 内部 trace 保留完整引擎事实；
- 模型反馈使用稳定的语义状态；
- 不要求模型理解底层坐标和引擎 objectId；
- 不把所有失败压缩成同一个 `action_failed`。

## 九、阶段 1 验收标准

### 9.1 冲突可见性回归

构造：

```text
instance_detections2D 包含准确目标
metadata.visible=false
```

断言：

```text
robot_go_to 不得成功
必须继续搜索后续候选
```

现有测试把检测框状态和 `metadata.visible` 同时设成相同值，无法覆盖真环境冲突，必须拆开构造。

### 9.2 独立正向基准

不能只验证“成功时没有撒谎”，还必须证明实现能够成功导航。

在使用新导航逻辑前，通过独立真环境脚本为已知可达目标建立正向基准。基准脚本不得 import 或复用 HomeMaster 的以下实现：

```text
grounding/resolver
_teleport_candidates()
_target_visibility_score()
_teleport_to_targets()
robot_go_to 的结果解析或失败分类
```

基准使用提前固化的 scene、objectId 和成功位姿，直接调用真环境并持久化外部结果。每个目标分别保存：

```text
scene/episode id
准确 objectId
独立记录的成功位姿
TeleportFull 外部返回
metadata.visible
instance_detections2D
frame hash
证据路径
```

对每个已知正向 objectId，新的 `robot_go_to` 必须返回成功，并满足三项成功门。任何一个正向目标失败都算阶段 1 验收失败，不能用其他目标成功抵消。

### 9.3 成功外部终态门

每次 `robot_go_to success=true` 后，独立读取真实终态并确认：

```text
准确 resolved_object_id 存在
准确目标 metadata.visible=true
准确目标检测框存在且面积大于 0
最后一次底层导航动作返回成功
```

调用 `shelf 1` 时，即使 `shelf 2` 可见，也不得通过。

### 9.4 观察交付门

对每次成功调用分别确认：

```text
frame_path 存在且非空
文件像素与最终成功 event.frame 一致
tool_result 引用了该 frame_path
model_trace 包含同一 frame_path 或同一内容哈希
```

### 9.5 Grounding 归责门

至少覆盖：

```text
场景中确实不存在目标 -> model_target_error
场景中存在目标但 resolver 失败 -> harness_grounding_failure
```

两种失败不得使用同一个评分归类。

### 9.6 Harness 失败门

目标存在且 grounding 成功，但所有候选都失败时：

```text
failure_reason=harness_navigation_failure
模型 invalid_action_count 不增加
模型不从失败姿态继续执行
Episode 标记为基础设施无效
```

### 9.7 导航 Harness 覆盖率门

每次运行必须报告：

```text
total_episodes
agent_scored_episodes
navigation_harness_invalid_episodes
harness_grounding_failures
harness_navigation_failures
navigation_harness_valid_coverage
unclassified_execution_episodes
```

定义：

```text
navigation_harness_valid_coverage =
  (total_episodes - navigation_harness_invalid_episodes) / total_episodes
```

阶段 1 只证明导航层是否有效。导航层可比较结果要求：

```text
navigation_harness_valid_coverage = 100%
```

即使导航覆盖率为 100%，阶段 1 也不能单独证明整个 brain-only 分数有效，因为操作层物理失败仍未完成归责。

正式发布整体 Agent 分数还必须满足：

```text
navigation_harness_valid_coverage = 100%
unclassified_execution_episodes = 0
每个 Episode 都进入互斥且完备的最终分类
操作层 Harness 失败与模型失败已经完成阶段 2 黑盒验收
```

在阶段 2 完成前，可以输出导航诊断结果和非正式实验结果，但不得称为正式、可比较的 brain-only Agent 分数。

### 9.8 导航与操作边界门

保留真环境反例：

```text
robot_go_to 成功
准确目标 metadata.visible=true
准确目标检测框存在
后续 PutObject 仍然失败
```

该反例用于防止未来把导航成功错误升级成“操作一定成功”。

## 十、明确不在阶段 1 解决的问题

1. Shelf 已经满足导航成功门，但所有 `PutObject` 候选都没有合法放置点时，应该让模型换 Shelf，还是判为 Harness 失败；
2. 如何区分“目标真实没有空间”和“底层落点采样偶然失败”；
3. 局部位姿重试的准确触发条件和预算；
4. 操作失败时向模型返回哪些 inventory、目标状态和错误分类；
5. `robot_inspect_view` 是删除还是改造成真正产生新观察的工具；
6. 操作层的 Harness 失败和模型恢复失败如何分别计分。

## 十一、分阶段路线

### 阶段 1：导航成功门与观察交付

- 真环境核对外部 linchpin；
- 分离检测框和 `metadata.visible`；
- 使用三项导航成功门；
- 锁定准确 objectId 和有限候选列表；
- 区分模型目标错误、Harness grounding 失败和 Harness 导航失败；
- 确保模型图片来自成功 event；
- 建立完全脱离 HomeMaster 导航代码的独立正向基准；
- 建立 100% 导航 Harness 覆盖率门，但不提前宣称整体 Agent 分数有效。

### 阶段 2：操作执行器局部位姿能力

- 保持单一公开 `robot_manipulate`；
- 建立动作位姿锚点和 `PoseContext`；
- 当前姿态优先；
- 只允许同目标、有界的局部候选；
- 不允许操作执行器重新做全场景主要导航；
- 在下一问题中完成失败反馈、终态字段和评分边界设计。

## 十二、独立评审意见处理

本文经过两轮独立评审。

### 12.1 第一轮评审

| 评审意见 | 处理 |
|---|---|
| `metadata.visible=true` 不能命名为 `interaction_ready` | 采纳，只定义为导航门的一部分 |
| 必须锁定准确 objectId | 采纳，写入阶段 1 不变量 |
| 检测框不能单独决定成功 | 采纳，改为三项成功门 |
| Harness 导航失败不能增加模型错误计数 | 采纳，增加独立失败分类 |
| 导航成功不能承诺 `PutObject` 成功 | 采纳，保留真环境反例 |

### 12.2 第二轮评审

| 评审意见 | 处理 |
|---|---|
| 只检查 success 的真实性会允许实现永远返回 Harness 失败 | 采纳，增加独立正向基准和 100% 覆盖率发布门 |
| 局部微调边界不够可执行 | 采纳，增加 `PoseContext` 和禁止全场景重导航的不变量；具体预算留到阶段 2 独立设计 |
| `put` 的操作目标与导航目标可能比较错误 | 采纳，明确动作位姿锚点 |
| 没验证模型收到的图片来自成功 event | 采纳，增加观察交付黑盒门 |
| 阶段 1 与操作重试细节混淆 | 采纳，阶段 1 只实现导航；操作部分只保留已确认不变量 |
| 未取证外部字段被写成已验证 | 采纳，集中列出 **UNVERIFIED** 并要求真环境证据表 |
| Grounding 失败被过度归责给模型 | 采纳，区分 `model_target_error` 与 `harness_grounding_failure` |
| 搜索失败后重放最佳姿态没有业务价值 | 采纳，删除重放，只保存诊断证据 |
| 导航候选日志包含无关 inventory/goal | 采纳，拆分导航日志与操作日志 |

### 12.3 第三轮评审

| 评审意见 | 处理 |
|---|---|
| 导航覆盖率被错误写成整体 Agent 分数发布门 | 采纳，改为 `navigation_harness_valid_coverage`；整体分数等待操作层完成归责 |
| 正向基准仍可能复用同源导航逻辑 | 采纳，禁止基准脚本 import HomeMaster grounding、候选生成、可见性评分和导航结果代码 |
| 检测框、RGB 对应关系和 `event.frame` 缺少真环境证据 | 采纳，全部标记 **UNVERIFIED**，先核对 linchpin，核对不过则回到设计评审 |

## 十三、相关代码与证据

- `src/homemaster/benchmarking/alfworld/env_adapter.py`
  - `go_to_target()`
  - `_teleport_to_targets()`
  - `_target_visibility_score()`
  - `_teleport_candidates()`
- `src/homemaster/benchmarking/alfworld/tools.py`
  - `_exec_go_to()`
  - `_visual_tool_result()`
  - `make_alfworld_robot_go_to()`
- `tests/homemaster/benchmarking/test_alfworld_env_adapter.py`
- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`
- `var/alfworld-trace/test/alfworld-valid_unseen-thor-objectid-20260707-001/episode-0006/`

## 十四、2026-07-12 操作方案评审后的导航补充

### 14.1 上下文所有权

导航候选归 `NavigationSearchContext`，只服务一次 `robot_go_to`；导航成功 event 通过观察门后，才可另建操作 `PoseContext`。两者不得共享可变候选列表，也不得把导航前“相对旧姿态”的排序冒充导航后局部候选排序。

两类 context 都必须锁定 `scene_generation`、`goal_generation`、准确 anchor objectId、完整候选 hash 和创建 event。reset、goal generation 变化、非本 context 移动或 event/pose 身份不可核对时立即失效。实际 agent pose 字段、event 时序和请求/落点一致性已在当前固定运行时逐候选核对；换引擎版本时必须重新 characterization。

### 14.2 导航固定预算

“最多 768 个原始候选”只描述生成空间，不是可发布运行预算。`robot_go_to` 必须由 per-instance 真环境实验确定并写回三项独立上限：

```text
max_navigation_candidates
max_navigation_backend_actions
max_navigation_elapsed_ms
```

每次 `GetReachablePositions` 和 `TeleportFull` 请求各计一个 backend action；模型的一次 `robot_go_to` 仍只计一个 agent tool call。达到任一上限立即分类 `harness_navigation_failure`，不得动态扩展、重放最佳姿态或让模型承担内部动作数。

实验必须对选定 trial 中每个准确 Shelf 分别报告首个通过三项导航门的候选、backend action 数和耗时；逐实例断言，禁止 any/best 聚合。`shelf-characterization-v3` 已完成该门，生产值为：

```text
max_navigation_candidates = 65
max_navigation_backend_actions = 66
max_navigation_elapsed_ms = 34804
```

`GetReachablePositions` 计一个 backend action，其余为实际发出的 `TeleportFull`；六 Shelf production-source exploration 的首个成功候选均落在该预算内，Shelf 3/4/6 已用同一生产预算独立复验。

### 14.3 移动返回与实际落点

导航成功除原三项门外，还必须证明请求的准确 pose 与同一最终 event 的 actual agent pose 在真环境核对后的容差内一致。当前 ALFWorld 0.5.0 / ai2thor 2.1.0 真环境已核对 `TeleportFull` 完整请求、返回事件以及 `metadata.agent.position`、`metadata.agent.rotation.y`、`metadata.agent.cameraHorizon`；产品继续使用证据脚本记录的数值容差。

成功返回但实际 pose 不符、失败返回但实际 pose 已变化、调用异常/超时、event 或 pose 读取失败，均不得继续或返回普通模型错误；统一进入 `execution_state_uncertain` 并立即终止 Episode。

### 14.4 计数与 trace

导航每个候选至少写 `attempt_started`、`move_started`、`move_result`、`observation_read_result`，包含 context id、候选 hash、requested/actual pose、外部返回码、raw event ref/hash、各阶段耗时和预算用量。完整坐标/objectId 只进入内部 trace，不进入模型上下文。

## 十五、最终逐实例导航证据

`shelf-characterization-v3` 在同一固定 trial、每实例独立 reset 下得到：

| Shelf | 首个成功候选 | backend actions | elapsed ms |
|---|---:|---:|---:|
| 1 | 51 | 52 | 27147.886 |
| 2 | 57 | 58 | 29803.499 |
| 3 | 57 | 58 | 29606.963 |
| 4 | 3 | 4 | 2146.964 |
| 5 | 1 | 2 | 1424.350 |
| 6 | 1 | 2 | 1416.524 |

每个成功实例均同时满足：外部返回成功、requested/actual pose 一致、准确 objectId 的 `metadata.visible=true`、同 event 正面积 bbox、同 event RGB frame 可保存且像素一致。Shelf 3/4/6 在生产预算下再次逐实例通过。

权威 artifact：`var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/`。
