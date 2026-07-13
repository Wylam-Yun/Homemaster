# ALFWorld Oracle 位姿执行与权威反馈契约

日期：2026-07-13

状态：用户已确认架构边界；独立评审结论为 FIX，意见已逐条处置。本文是待用户批准的设计稿，只定义方案，不授权修改产品代码。

关联证据：

- 真实运行：`alfworld-valid_unseen-v17-realapi-20260713-001`
- 基线提交：`0fdfeaa00b921d8ea347655ecbd4c32b9ff30d6d`
- 被替代的运行策略：V1.7 导航候选枚举与 Put 局部姿态枚举

## 一、决策摘要

本项目评测模型的高层规划、语义目标选择和基于可见观察的探索，不评测低层机器人位姿搜索。因此正式运行采用以下唯一执行路线：

```text
模型选择语义目标和动作
-> Harness 确定性解析当前允许寻址的准确实例
-> Harness 从 ALFWorld 通用 Oracle receptacle map 取得标准位姿
-> 单次移动
-> 核对外部返回码、实际 pose 和准确目标可见终态
-> 全部操作在同一个 Oracle 执行上下文和动作网关中执行
-> 核对外部返回码和真实世界终态
-> 通过强类型反馈返回模型
```

以下运行机制从正式路径删除：

- 12 个可达位置乘以旋转和俯仰角形成的导航候选枚举；
- `65 candidates / 66 backend actions / 34804 ms` 导航预算；
- Put 局部候选枚举；
- `9 candidates / 17 backend actions / 5669 ms` Put 预算；
- 从专家 `high_pddl` 或 `low_actions` 读取目标、动作、实例或答案；
- 从通用字典或人类文本反向猜测 inventory 和对象状态；
- `robot_find_object` 自动读取隐藏 parent 并遍历 receptacle 的搜索路径；
- `robot_navigate` 直接进入 legacy `env.step()` 或 `virtual_navigate()` 的旁路。

Oracle map 缺失或标准位姿不能兑现外部终态时，Episode 作为 Harness coverage failure 终止。正式评分路径不回退到候选暴力搜索。

## 二、问题证据

### 2.1 真实 10 条运行结果

```text
raw success                         = 5 / 10
agent success on score-eligible     = 5 / 6
harness coverage                    = 6 / 10
formal_score_available              = false
```

这里的 `harness coverage` 是 V1.7 summary 的旧混合名称，实际等于当时的 score-eligible coverage；V1.8 按 §10.5 拆为 evaluation、Harness、Provider 和 Runtime 指标，不能直接沿用该字段解释责任。

五条失败不是同一种原因：

| Episode | 表面分类 | 已取证的决定性原因 |
|---|---|---|
| 0001 | `agent_model_failure` | 单次 Anthropic-compatible SSE 顺序异常被错误归入 Agent 分数 |
| 0003 | `harness_navigation_failure` | Shelf 第 83 个候选成功，但生产预算在第 65 个停止 |
| 0004 | `harness_operation_failure` | Drawer 可见姿态不能 Open；模型后来对关闭 Drawer 执行 Put |
| 0006 | `harness_navigation_failure` | 最早是 Drawer Open 位姿缺口；随后错误 inventory 投影使模型跑偏到隐藏 SaltShaker |
| 0007 | `harness_navigation_failure` | 与 0003 相同 Shelf，同样第 83 个候选才成功 |

### 2.2 候选预算并非通用导航能力

V1.7 的导航预算只来自一个 `Pencil -> Shelf` trial 中六个 Shelf。最慢观察值为第 57 个候选，因此加 margin 后设为 65。

真实 run 的 FloorPlan 10 Shelf 独立精确 trial 回放证明：

```text
locked candidates       = 240
first strict success    = candidate 83
backend actions         = 84
elapsed                 = 36.49s / 39.15s
production limit        = 65 / 66 / 34.804s
```

前 65 次 `TeleportFull` 全部返回成功。27 个姿态已经存在准确 bbox，但 `metadata.visible=false`；严格可见门正确拒绝。目标不是不可达，失败来自运行预算在可成功姿态之前截断。

另一方面，Episode 0006 后期错误选择的 SaltShaker 位于关闭的 Drawer 2 内。完整 240 候选、241 backend actions、107.38 秒后仍然 `visible=false`、无检测框。该对照证明“把预算统一提高到 240”不是正确方案。

### 2.3 可见姿态不等于可操作姿态

Episode 0004 和 0006 中，准确 Drawer 已通过导航可见门，但 `OpenObject` 被 THOR 拒绝。V1.7 只为 Put 实现了局部恢复，Open 仍走单次 legacy 路径。

精确 trial 独立探针证明：同一 Drawer 的候选 61 同时满足导航可见门和 `OpenObject` 成功终态。最早断点是“导航观察门通过，但执行姿态不能操作；Open 没有可靠位姿来源”。

本设计不再为 Open 增加另一套候选循环，而是统一使用 benchmark Oracle 位姿。

### 2.4 模型反馈读取了错误的数据层

真实 Adapter 结果形状为：

```text
AlfworldEnvState.inventory        = "You are carrying: peppershaker."
step_result.tool_args.inventory   = ["peppershaker 1"]
step_result.tool_args.object_state = "held"
```

V1.7 `_put_visible_base()` 只读取顶层 `data.inventory/object_state/state_changed`。字符串 inventory 不是 list，因此被替换为 `[]`；其余字段变成 `null/false`。

Episode 0006 的外部世界仍持有 PepperShaker，但模型看到 `inventory=[]`，据此误判物体掉落，随后反复尝试从地面拾取并最终改找错误的 SaltShaker。单测手工构造了顶层 list，复制了投影层的错误假设，因此形成假阳性。

### 2.5 Provider 错误码与评分分类不一致

真实 run 共发起 159 次模型请求，158 次完成。唯一故障为收到 `message_start` 前先收到 `message_delta`。无 401/403、认证、Base URL 或模型 ID 错误。

Runner 的基础设施集合包含 `provider_failure/runtime_failure`，实际 runtime error code 为 `transport_error`。未命中集合后落入默认 `agent_model_failure`，错误进入 Agent 分母。

## 三、目标与非目标

### 3.0 适用范围

V1.8 的正式评分路径只指 `AlfredThorEnv + visual_eval`。`AlfredTWEnv` 不依赖 THOR Oracle map，不在本文 Oracle 正确性声明内；但公开工具面仍统一为 `robot_go_to`，不得保留 `robot_find_object` 或 `robot_navigate` 作为 Dispatcher 可执行旁路。

### 3.1 目标

1. 让模型负责高层语义动作、目标选择和基于当前可见观察的探索。
2. 让 ALFWorld benchmark Oracle map 负责低层 receptacle 位姿。
3. 保证任何成功导航的准确目标在最终 event 中真实可见。
4. 禁止 Harness 利用隐藏对象的 parent 信息替模型完成搜索。
5. Take/Open/Close/Put/Use/Slice 与抽象 Heat/Cool/Clean 全部通过同一个 Oracle 执行上下文和外部动作网关，不再搜索姿态。
6. 所有正式 THOR robot 工具的模型可见执行状态来自一个强类型、权威的数据结构。
7. Provider/runtime/Harness/Agent 失败具有互斥且可审计的评分语义。
8. 任何外部调用同时通过返回码门和外部终态门。

### 3.2 非目标

- 不评测模型生成坐标、旋转角或相机俯仰角的能力。
- 不让模型读取 objectId、坐标、Oracle map、专家动作或候选排名。
- 不自动告诉模型隐藏 Mug 位于哪个 Drawer。
- 不读取 `traj_data.plan.high_pddl` 或 `low_actions` 选择目标实例或动作。
- 直接 Put 不自动打开容器，直接 Open/Close/Put 的顺序由模型决定。模型显式选择的抽象 Heat/Cool/Clean 保留现有高层动作语义；其内部子动作是该高层动作的实现，不是 Put 的隐式 fallback，并且每个子动作都必须经过同一返回码/终态门。
- 不在 Oracle 失败后回退到 240 候选搜索。
- 不在本设计中实现未来记忆模块。
- 不声明未真环境核对的 ALFWorld 内部属性为稳定公开 API。

## 四、候选方案与取舍

### 4.1 方案一：通用 Oracle receptacle map（采用）

模型先选择语义目标；Harness 将允许寻址的目标解析为准确实例，并只用该准确 receptacle objectId 查询当前场景的通用 Oracle 位姿。

优点：

- 与 `controller.type=oracle` 的高层评测定位一致；
- 不需要候选搜索和经验预算；
- 不读取专家任务答案；
- 目标选择与低层执行责任清晰。

代价：

- 当前运行时接入字段是 ALFWorld 内部结构，必须先做真环境契约探针；
- movable object 需要当前可见性和可见 parent anchor 规则；
- Oracle map 缺项会降低 Harness coverage。

### 4.2 方案二：专家 trajectory 姿态和实例（拒绝）

`traj_data.json` 的 `high_pddl.planner_action.location` 和交互 objectId 足以直接重放专家实例和姿态。

拒绝理由：它会把专家选择的具体实例和任务路径带入运行时。旧实现还曾强制 Pickup，实质上替模型完成任务。即使成功率高，也改变了评测对象。

### 4.3 方案三：保留候选搜索并扩大预算（拒绝）

优点是避免依赖 Oracle 内部结构。代价是运行慢、预算依赖场景、隐藏对象无解，并继续把低层姿态偶然性混入高层评测。

### 4.4 方案四：Oracle 优先、候选搜索兜底（拒绝）

该方案表面提高 coverage，但保留两套语义和大量分支。Oracle 缺失时继续暴力搜索还会掩盖 benchmark 接入缺口，使不同 Episode 使用不同低层能力。正式评分路径只允许一种执行语义。

## 五、外部运行时契约

### 5.1 历史证据与当前声明的边界

安装源码和真实 `valid_unseen` 数据中已经观察到 Oracle controller、receptacle 导航记录和标准高层 GOTO 调用链。V1.7 的独立探针还验证过部分 `TeleportFull`、`OpenObject`、`PutObject`、pose、可见性、bbox、inventory、`isPickedUp`、parent/child 和 frame 原语；Shelf 3/4/6 通过过旧候选路径的产品黑盒。

这些是“单个原语在旧路径中曾工作”的历史证据，不是“V1.8 Oracle 接线可用”的证据。新的 exact map lookup、单 pose 解析、target/anchor 组合和 HomeMaster 数据流在产品黑盒通过前全部保持 **UNVERIFIED**。

### 5.2 V1.8 产品路径的 UNVERIFIED 外部符号

以下外部符号或组合只确认存在或有局部历史证据，评审不为其在 V1.8 产品路径中的可用性背书：

```text
controller.type = oracle
batch_env.envs[0].controller
controller.receptacles
controller.receptacles[exact_object_id]["locs"]
batch_env.envs[0].traj_data
batch_env.envs[0].task_file / traj_root

event.metadata.objects / inventoryObjects
objectId / visible / isOpen / openable / receptacle / isPickedUp
toggle/slice/heat/cool/clean action-specific state fields（真实名称待 Gate A 核对）
parentReceptacles / receptacleObjectIds
event.instance_detections2D / event.frame
lastAction / lastActionSuccess / errorMessage
agent.position / agent.rotation / agent.cameraHorizon

TeleportFull / PickupObject / OpenObject / CloseObject / PutObject
ToggleObjectOn / ToggleObjectOff / SliceObject
forceAction / placeStationary / rotateOnTeleport / horizon
```

`locs` 的真实 shape、有限数值约束、单 pose 选择、reset/trial 绑定，以及 Oracle pose 对每种目标是否同时满足可见和可操作，都属于 UNVERIFIED contract，而不是实现细节。

### 5.3 两级解除门

外部符号不得被一次独立 probe 直接宣布为“产品已验证”：

1. **Gate A，实施前 runtime feasibility**：独立 probe 不 import HomeMaster，核对当前 runtime、exact trial、exact instance、map lookup、单 pose 解析、外部返回码和逐实例终态。Gate A 通过只允许开始实现。
2. **Gate B，实施后 product integration**：使用真实 `AlfworldEnvAdapter -> tools -> Dispatcher` 产品链逐实例黑盒，核对同样的返回码和外部终态，并核对模型投影。Gate B 通过才解除 V1.8 产品接线的 UNVERIFIED 状态。

Gate A 任一 linchpin 失败，停止实现并回到设计。Gate B 任一实例失败，产品不得发布、不得增加候选或 legacy fallback。

### 5.4 禁止的数据源

产品运行时不得读取：

```text
traj_data.plan.high_pddl
traj_data.plan.low_actions
expert_plan
专家 objectId / receptacleObjectId
专家动作顺序
```

增加静态和运行时 audit，防止后续通过别名或 helper 重新引入。

产品实现不依赖 `traj_data`、`task_file` 或 `traj_root` 取得姿态或实例。Gate A 可以把这些字段作为 trial 对照证据读取，但读取必须与产品模块隔离；产品 trace 使用 Runner 已知的 episode/trial fingerprint，不通过 trajectory 内容恢复任务答案。

## 六、组件边界

### 6.1 `OraclePoseStore`

新增窄接口，隐藏 ALFWorld 内部对象结构，并保留读取状态：

```python
OracleReadStatus = Literal["ok", "absent", "malformed", "stale", "error"]

@dataclass(frozen=True)
class OraclePoseLookup:
    status: OracleReadStatus
    scene_generation: int
    trial_fingerprint: str
    pose: OraclePose | None
    evidence_ref: str | None

class OraclePoseStore(Protocol):
    def get_pose(
        self,
        *,
        scene_generation: int,
        trial_fingerprint: str,
        exact_anchor_id: str,
    ) -> OraclePoseLookup: ...
```

约束：

- 只按锁定的准确 anchor ID 查询 map，不接受语义类型；
- 同一 scene generation、trial fingerprint、exact ID 必须返回确定性相同结果；
- `status=ok` 时必须恰有一个 pose，所有位置/旋转/俯仰值均为有限数；
- map 缺项、shape 损坏、跨 reset stale 和访问异常不得合并为 `None`；
- 不替模型选实例；
- 不读取专家 trajectory；
- 返回值只供 Adapter 内部使用；
- reset 后清空上一场景缓存。

若接口存在多个实现，接口审计测试必须枚举所有实现并断言公开方法完整。

### 6.2 `VisibleObjectView`

reset 时建立的 `SceneObjectIndex` 继续提供稳定 label/objectId 映射，但可见性必须来自当前 event，不能复用 reset snapshot。

读取结果同样是强类型：

```python
@dataclass(frozen=True)
class ObjectObservationRead:
    status: OracleReadStatus
    event_sequence: int
    exact_object_id: str | None
    visible: bool | None
    bbox_area: float | None
    strict_visible: bool | None
```

`VisibleObjectView` 负责：

- 读取当前 event 中准确对象的 `metadata.visible`；
- 读取同一个 event 的准确 bbox；
- 只列出同时满足 `visible=true` 和正面积 bbox 的 movable/toggle object；
- 对同一个 event 和输入返回确定性顺序；
- exact object 没有 detection entry 是合法观察：`status=ok, bbox_area=None, strict_visible=false`；
- metadata/detection 容器损坏、bbox shape 非法、stale 或读取异常不能变成“普通不可见”；只有 `status=ok` 且 `strict_visible=false` 才是可恢复的 `target_not_visible`。

movable/toggle 查询先用公开 ALFWorld 类型词表校验语义，再只从当前 `VisibleObjectView` 解析实例。不得先从全场景 `SceneObjectIndex` 锁定隐藏 objectId。类型合法但当前没有可见实例时统一返回 `target_not_visible`；非法类型或非法 label 语法才返回 `target_not_found`。显式 `mug 9` 不通过隐藏 scene index 区分“存在但隐藏”和“不存在”，避免泄露隐藏实例。

### 6.3 `NavigationAnchorResolver`

锁定 requested target 后按以下唯一规则解析 anchor：

1. receptacle 目标要求自身 `OraclePoseLookup(status=ok)`，否则按 7.5 terminal，不允许 parent fallback；
2. movable/toggle/fixture 目标自身有 `OraclePoseLookup(status=ok)` 时，anchor 就是 exact target；
3. 这三类自身 lookup 为 `absent` 时，exact target 必须在当前 event 严格可见，才允许解析 parent anchor；lookup 为 malformed/stale/error 时直接按 7.5 terminal；
4. parent 候选必须同时满足 target 的 parent membership、parent 的 reciprocal child membership、parent 当前严格可见和 parent Oracle lookup 成功；
5. 只接受 reciprocal containment 图中唯一的最内层候选；不得按集合顺序、距离猜测或 `.first`；
6. 所有读取均为 `ok` 时，零个或多个最内层候选返回 terminal `oracle_anchor_unresolved`；任一必要读取 malformed/stale/error 则为 `execution_state_uncertain`。两者都零移动、不读取隐藏 parent、不 fallback。

anchor 只进入内部 context/trace，模型投影不返回 parent label、objectId 或选择理由。物体在地面、已在 inventory、parent 读取损坏和 toggle/fixture 无 map 的分支均通过上述状态返回，不另开搜索 mode。

### 6.4 `OracleNavigationExecutor`

职责：

1. 锁定准确 requested target；
2. 锁定准确 navigation anchor；
3. 读取一个 Oracle pose；
4. 最多发送一次移动请求；
5. 核对返回码、actual pose、准确 requested target 可见门；
6. 创建单一 `OracleExecutionContext`。

它不生成、排序或重试候选。

### 6.5 `OracleExecutionContext`

替代带候选列表的 `PoseContext`：

```text
scene_generation
goal_generation
source_event_sequence
current_event_sequence
requested_target_id
navigation_anchor_id
oracle_pose_hash
actual_pose
final_event_hash/ref
state = active | consumed | invalid
```

不包含候选列表、候选排名或动态预算。状态机固定如下：

| 事件 | context 转移 |
|---|---|
| 成功 Oracle 导航 | 创建 `active`，锁定 target/anchor/pose/event |
| `target_closed`、`navigation_required` 等零动作结果 | pose/event 未变，原 context 保持；失败请求不得重算目标 |
| 同 target、同 pose 的幂等 Open/Close | 核对当前读取后保持 `active` |
| 同 target、同 pose 的成功 Open/Close/Use/Slice | 返回码和终态均通过后，把 successor event sequence/hash 原子 rebase 到同一 `active` context |
| 成功 Take | 精确 movable context 变为 `consumed` |
| 成功 Put 或完成 Heat/Cool/Clean macro | context 变为 `consumed` |
| reset、成功新导航、任何 pose 改变、无关外部动作、event gap、返回/终态矛盾 | 变为 `invalid` |

因此 `go_to(drawer) -> Put(target_closed) -> Open -> Put` 使用同一锁定 pose：第一次 Put 不改 context，Open 成功后只 rebase event，最终 Put 消费 context。所有外部动作必须通过统一 `OracleActionGateway` 更新 event sequence；helper 不得绕过 gateway 直接调用 THOR。

### 6.6 `AlfworldExecutionFeedback`

新增强类型执行结果。请求参数与执行结果不再混用；缺失读取用显式 status 表达：

```python
ExecutionReadStatus = Literal["ok", "not_applicable", "absent", "malformed", "stale", "error"]
ObjectExecutionState = Literal[
    "held", "not_held", "placed", "heated", "cooled", "clean", "dirty", "sliced"
]
TargetExecutionState = Literal[
    "visible", "not_visible", "open", "closed", "toggled_on", "toggled_off"
]

@dataclass(frozen=True)
class AlfworldExecutionFeedback:
    success: bool
    action: AlfworldAction
    object: str | None
    target: str | None
    inventory: tuple[str, ...] | None
    inventory_status: ExecutionReadStatus
    object_state: ObjectExecutionState | None
    object_state_status: ExecutionReadStatus
    target_state: TargetExecutionState | None
    target_state_status: ExecutionReadStatus
    state_changed: bool | None
    state_read_status: ExecutionReadStatus
    error: ToolExecutionError | None
    terminal: bool
    classification: EpisodeClassification | None
    score_eligible: bool
    detail_code: SafeDetailCode | None
```

字段来源：

| 字段 | 唯一权威来源 |
|---|---|
| `inventory` | 当前 THOR `inventoryObjects` 经 SceneObjectIndex 映射 |
| `object_state` | 准确对象 `isPickedUp`、inventory membership 和 parent membership |
| `target_state` | 准确目标 `visible/isOpen` 等已验证字段 |
| `state_changed` | 外部动作前后结构化状态比较 |
| `error` | 本文稳定错误分类 |
| `*_status` | 对应外部读取的成功/缺失/损坏/stale/异常状态 |
| `terminal/classification/score_eligible` | 本文封闭映射，不由投影层重算 |
| `detail_code` | allowlist code；serializer 映射为固定安全模板，不接收 raw THOR 文本 |

类不变量：`success=true` 必须 `error=None`；必需读取不是 `ok` 时必须 `success=false`、`terminal=true`，且 classification 为 `execution_state_uncertain` 或 `unclassified_execution_failure`，不得伪造值。`None` 只有在对应 status 明确不是 `ok`/为 `not_applicable` 时合法。

唯一产品数据流固定为：

```text
Adapter 从 before/after 外部快照构造 AlfworldExecutionFeedback
-> AlfworldStepResult.execution_feedback（必填）
-> 唯一 to_model_payload() 安全序列化
-> tools 原样转发
-> Dispatcher 只绑定 tool_call_id 并序列化，不覆盖 success/error
```

删除 action-specific 二次拼装和 dict fallback。`tool_args` 只保留模型请求/grounding，进入内部 trace，不进入 visual model JSON。`AlfworldEnvState.inventory` 和字符串 `feedback` 只供 debug trace；正式 THOR 模型投影不得解析或暴露它们。

## 七、导航语义

### 7.1 唯一公开导航入口

正式 registry 只向 Dispatcher 注册 `robot_go_to`。`robot_find_object` 和 `robot_navigate` 的 ToolSpec、executor、Adapter 方法与 legacy helper 从生产路径删除；即使直接向 Dispatcher 提交这两个旧名称，也只能得到 `unknown_tool`，不得触发环境调用。

`robot_go_to` 在 `AlfredThorEnv` 只能进入 `OracleNavigationExecutor`；不得调用 textual `env.step("go to ...")`、`virtual_navigate()`、admissible-command 遍历或候选生成。`AlfredTWEnv` 可在自身非 Oracle 路径翻译同一个公开工具名，但不共享 V1.8 正确性声明。

### 7.2 Receptacle / fixture 目标

示例：

```text
robot_go_to("drawer 2")
```

流程：

1. 语义 label 确定性解析为准确 Drawer ID；
2. 按该 ID 查询 Oracle map，并要求 lookup `status=ok`；
3. 单次移动；
4. 校验 THOR return success；
5. 校验 requested pose 与 actual pose；
6. 校验准确 Drawer 在同一最终 event 中 `visible=true`；
7. 校验准确 Drawer bbox 面积大于零；
8. 保存最终 event 图片并创建 context。

成功必须指向请求的准确实例。其他同类型实例可见不能代替。

fixture/toggle 自身没有 Oracle pose 时，不猜测 pose：只有其当前严格可见，才允许进入 `NavigationAnchorResolver`。DeskLamp/FloorLamp 等目标必须纳入 Gate A/B；无唯一 anchor 时 terminal `oracle_anchor_unresolved`。

### 7.3 Movable object 目标

示例：

```text
robot_go_to("mug")
```

预检只允许从当前 `VisibleObjectView` 解析 movable object：

- 没有当前可见 Mug：返回 `target_not_visible`，零 THOR action；
- 显式 `mug 2` 当前不可见：返回 `target_not_visible`，不得 fallback 到 mug 1；
- 多个 Mug 当前可见：按稳定 canonical label/objectId 顺序选择一次并锁定；
- 锁定后不得中途切换实例；
- 目标已经在 inventory：返回非 terminal `object_already_held`，零动作，不创建新导航 context。

准确 movable 已经可见后，才允许按 6.3 的 reciprocal containment 规则解析唯一 navigation anchor。该 parent 信息只参与内部执行，不用于定位隐藏对象，也不返回给模型。

移动到 parent Oracle pose 后，最终成功仍必须断言准确 movable 本身 `visible=true` 且 bbox 面积大于零。只看到 parent 不算成功。

### 7.4 隐藏对象边界

若 Mug 位于关闭 Drawer 内且当前不可见：

```text
robot_go_to("mug")
-> {"success": false, "error": "target_not_visible", "target": "mug"}
```

必须满足：

- 不移动；
- 不锁定隐藏 Mug objectId；
- 不读取或返回隐藏 Mug 的 parent Drawer；
- 不增加 Harness failure；
- 不增加环境 invalid action；
- 不创建、替换或清空已有 context；
- 模型继续选择 CounterTop、Cabinet、Drawer 等可导航位置探索；
- 模型重复无进展调用最终由既有 no-progress/tool-iteration 规则归为 Agent failure。

未来记忆模块负责学习搜索顺序，不属于 Harness。

### 7.5 Lookup 与移动结果穷尽门

lookup 结果映射：

| lookup status | ToolExecutionError | EpisodeClassification | backend action |
|---|---|---|---:|
| `absent` | `oracle_pose_missing` | `harness_navigation_failure` | 0 |
| `malformed` | `oracle_pose_malformed` | `harness_navigation_failure` | 0 |
| `stale/error` | `execution_state_uncertain` | `execution_state_uncertain` | 0 |

发送唯一移动请求后的结果映射：

| 外部返回 | pose | exact target 可见/bbox | 结果 |
|---|---|---|---|
| success | match | pass | 导航成功并创建 context |
| failure | unchanged | 任意 | `oracle_navigation_failed -> harness_navigation_failure` |
| failure | changed/unknown | 任意 | `execution_state_uncertain` |
| success | mismatch/unknown | 任意 | `oracle_pose_mismatch -> execution_state_uncertain` |
| success | match | `status=ok` 但不可见/无正面积 bbox | `oracle_target_not_visible -> harness_navigation_failure` |
| success | match | read malformed/stale/error | `execution_state_uncertain` |

`target_not_visible` 只表示**移动前**当前可见集合中没有 movable/toggle，必须是零动作、非 terminal。移动后 exact target 不可见是 Oracle contract failure，不能复用该错误让模型继续，也不能落入 Agent failure。

准确 target 或 anchor 无 Oracle pose 时的模型安全结果示意：

```text
error = oracle_pose_missing
classification = harness_navigation_failure
terminal = true
score_eligible = false
```

不得 fallback 到专家 trajectory、同类型其他实例或候选搜索。

## 八、操作语义

### 8.1 通用前置门

Take/Open/Close/Put/Use/Slice 和 Heat/Cool/Clean 必须具有与准确 requested target 绑定的 `active OracleExecutionContext`。没有 context、context stale 或 target 不一致时返回非 terminal `navigation_required`，零操作请求。Take 必须使用 context 中已锁定的 exact movable ID，不得重新按类型 ground；旧 `_last_go_to_object_id` 字符串旁路删除。

操作前必须重新读取：

- context scene/goal generation；
- actual pose；
- 准确 target 是否存在且可见；
- inventory；
- 准确对象/目标状态；
- 当前 event 与 context final event 的允许关系。

任一必需读取为 absent/malformed/stale/error 时，零外部动作并 terminal `execution_state_uncertain`。precondition 不满足但读取完整时，返回对应非 terminal ToolExecutionError。

### 8.2 `OracleActionGateway`

所有 THOR 请求，包括 macro 子动作，都只能通过一个 gateway：

1. 请求前锁定 action、exact object/target、context 和 before snapshot；
2. 发送一次请求并把 backend action、event sequence、耗时和 raw event ref 写入 JSONL；
3. 读取 after snapshot；
4. 同时判断外部返回码和 action-specific 终态；
5. 只有二者一致成功才允许 context rebase/consume；
6. 返回失败且完整动作状态不变为 `harness_operation_failure`；
7. 部分变化、读取缺失或返回/终态矛盾为 `execution_state_uncertain`；
8. 不在 gateway 内搜索 pose、换 target 或重试动作。

### 8.3 Take

Take 使用 `requested_target_id` 的 active context，并要求准确 movable 当前可见。只发送一次 `PickupObject`。成功必须同时满足：

```text
外部返回 success
准确对象进入完整 inventory
准确对象 isPickedUp=true
准确对象仍存在
actual pose 与 context pose 匹配
```

成功后 context consumed。其他同类型对象进入 inventory 不能代替准确实例。

### 8.4 Open / Close

Open 流程：

```text
目标不可见或 context 不匹配 -> navigation_required
目标不是 openable              -> action_not_applicable
isOpen=true                     -> 幂等成功，零 OpenObject
isOpen=false                    -> 单次 OpenObject
```

发送 Open/Close 请求时，成功必须同时满足：

```text
外部返回 success
准确 target.isOpen = true
准确 target 仍存在
actual pose 可读且未发生未声明漂移
```

Close 对称要求 `isOpen=false`。

幂等 Open/Close 不发送外部请求，因此没有伪造 return code；它依赖当前 event 的完整 `status=ok` 状态读取、exact target 和 pose 门证明结果已经成立。

外部返回失败且状态不变时分类 `harness_operation_failure`；返回码与状态矛盾、字段缺失或部分状态变化时分类 `execution_state_uncertain`。不尝试第二个姿态。成功或幂等结果按 6.5 rebase/保留同一 context，使后续 Put 不需要再次导航。

### 8.5 Put

Put 前置条件：

1. 准确 held object 在 inventory；
2. `isPickedUp=true`；
3. 准确 target 是 receptacle；
4. target 与有效 context 一致；
5. target 当前可见；
6. 若 target openable，则 `isOpen=true`。

关闭容器：

```text
put(object, closed drawer)
-> target_closed
-> inventory 保持权威真实值
-> 零 PutObject
-> 非 terminal，由模型决定 open
```

所有前置条件满足后只发送一次 `PutObject`。成功必须同时满足：

```text
外部返回 success
准确对象离开 inventory
准确对象 isPickedUp=false
准确 target 在对象 parent membership
准确对象在 target child membership
```

返回失败且完整动作状态不变时分类 `harness_operation_failure`；任何部分变化或返回码/终态矛盾分类 `execution_state_uncertain`。不进行局部姿态重试。

### 8.6 Use / Slice

Use 和 Slice 都要求 exact requested target context，只发送一次对应外部动作。Use 同时核对准确 toggle 状态改变；Slice 同时核对准确对象的 benchmark slice 终态。实际外部 action/field 符号在 Gate A 前保持 UNVERIFIED。

失败映射与 8.2 相同。不得把同类型其他灯、开关、刀具或对象的状态变化当成功。

### 8.7 Heat / Cool / Clean 抽象动作

这三个名称是模型显式选择的公开高层动作，保留现有抽象语义，不要求模型展开内部 Open/Put/Close/Pickup。它们不是直接 Put 的自动开容器 fallback。

macro 必须满足：

- context 绑定模型选择的准确 Microwave、Fridge 或清洗 fixture；
- 整个 macro 只使用该 Oracle pose，不做局部 pose 搜索；
- 子动作序列在开始时一次规划并锁定，中途不得根据失败改写 target 或重排；
- 每个子动作逐一经过 `OracleActionGateway` 的返回码和外部终态门；
- 每个成功状态子动作原子 rebase context；
- 任一失败立即停止，已发生部分变化时分类 `execution_state_uncertain`，不得补偿性猜测；
- 最终还要核对准确 held object 的 benchmark heat/cool/clean 终态、inventory 和 goal 相关状态。

Gate A 必须先核对这些 action-specific 外部字段的真实名称与时序；核对前一律 UNVERIFIED。若 macro 无法在单 Oracle pose 下逐步完成，回到设计，不恢复候选或 legacy env command。

### 8.8 动作覆盖矩阵

| 公开 action | exact lock | 最大外部动作 | 必需终态 | context |
|---|---|---:|---|---|
| `go_to` | requested target + anchor | 1 | pose + exact visibility/bbox | create |
| `take` | requested movable | 1 | inventory + `isPickedUp` | consume |
| `open/close` | requested target | 0 或 1 | exact open state | keep/rebase |
| `put` | held object + requested target | 0 或 1 | inventory + picked-up + reciprocal parent/child | keep on `target_closed`, consume on success |
| `use` | requested toggle | 1 | exact toggle transition | rebase |
| `slice` | requested object | 1 | exact slice transition | rebase |
| `heat/cool/clean` | held object + requested tool fixture | 锁定 macro 长度 | 每步双门 + 最终准确对象状态 | consume |
| `verify` | 当前 Episode | 0 | benchmark goal read | unchanged |

没有列入矩阵的 legacy manipulation 不得通过 generic router 执行。所有行都生成 `AlfworldExecutionFeedback`；无法构造权威 typed snapshot 的 action 在 V1.8 中不能声明可用。

## 九、模型反馈契约

模型只接收 `AlfworldExecutionFeedback` 的安全投影。示例：

```json
{
  "success": false,
  "action": "put",
  "object": "peppershaker 1",
  "target": "drawer 1",
  "inventory": ["peppershaker 1"],
  "inventory_status": "ok",
  "object_state": "held",
  "object_state_status": "ok",
  "target_state": "closed",
  "target_state_status": "ok",
  "state_changed": false,
  "state_read_status": "ok",
  "error": "target_closed",
  "terminal": false,
  "classification": null,
  "score_eligible": true,
  "detail": "Open drawer 1 before putting the object."
}
```

禁止：

- 读取 `data.inventory` 人类字符串并转换；
- 从 `tool_args` 猜结果；
- 在 Adapter、tools 和 Dispatcher 各自复制一份状态拼装；
- 缺字段时静默替换为 `[]`、`null` 或 `false`；
- 把 objectId、Oracle pose、隐藏 parent 或专家信息放入 detail；
- 把 raw THOR `errorMessage` 做正则替换后直接发送给模型。

`detail` 只能由 `detail_code -> allowlisted template` 生成。raw THOR detail、异常栈和绝对路径只进入内部证据。

任何必需权威字段缺失时，不得伪造默认值：对应值为 `null` 且 status 明确为 absent/malformed/stale/error，分类 `execution_state_uncertain` 并终止。只有代码违反 typed contract、Serializer 收到非法组合或未知错误码时才用 `unclassified_execution_failure`；它表示集成缺陷，不表示外部状态未知。

`AlfworldStepResult.failure_reason` 不再是第二权威来源：保留时只能是 `execution_feedback.error/classification` 的只读派生属性。tools/Dispatcher 不得 `setdefault`、覆盖或从字符串恢复 success/error。

## 十、失败分类与评分

### 10.1 两层封闭类型

`ToolExecutionError` 只描述一次工具结果：

```text
invalid_tool_arguments
unknown_tool
target_not_found
target_not_visible
object_already_held
object_not_held
target_not_receptacle
target_closed
action_not_applicable
navigation_required
oracle_anchor_unresolved
oracle_pose_missing
oracle_pose_malformed
oracle_navigation_failed
oracle_pose_mismatch
oracle_target_not_visible
harness_operation_failure
execution_state_uncertain
unclassified_execution_failure
```

`EpisodeClassification` 只描述 Episode 责任域：

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

不允许任意字符串进入任一集合。terminal 工具错误必须映射为恰好一个 EpisodeClassification：

| ToolExecutionError | terminal | EpisodeClassification | score eligible |
|---|---:|---|---:|
| `invalid_tool_arguments/unknown_tool/target_not_visible/target_closed/navigation_required/object_already_held/object_not_held/target_not_found/target_not_receptacle/action_not_applicable` | 否 | `None` | 是 |
| `oracle_anchor_unresolved/oracle_pose_missing/oracle_pose_malformed/oracle_navigation_failed/oracle_target_not_visible` | 是 | `harness_navigation_failure` | 否 |
| `harness_operation_failure` | 是 | `harness_operation_failure` | 否 |
| `oracle_pose_mismatch/execution_state_uncertain` | 是 | `execution_state_uncertain` | 否 |
| `unclassified_execution_failure` | 是 | `unclassified_execution_failure` | 否 |

grounding 层无法形成合法 exact target 时映射 `harness_grounding_failure`；模型传入语义合法但当前不可见/前置条件不满足时使用上表非 terminal 错误。未知错误绝不 default 为 `agent_model_failure`。

### 10.2 计数矩阵

`agent_tool_call_count`、`backend_action_count`、`env_step_count` 和 `invalid_action_count` 是不同计数器：

| 结果 | agent tool | backend action | env step | invalid action | context |
|---|---:|---:|---:|---:|---|
| 零动作 precondition/不可见 | +1 | 0 | 0 | 0 | 保留原 context |
| Oracle lookup/anchor terminal | +1 | 0 | 0 | 0 | Episode terminal |
| 单次移动或操作 | +1 | 1 | 1 | 0 | 按 6.5 转移 |
| Heat/Cool/Clean macro | +1 | 实际 N | 实际 N | 0 | 每步 rebase，最终 consume |
| Provider retry attempt | 0 | 0 | 0 | 0 | 不触碰环境/context |

metadata、inventory、pose 和 map 的当前 event 读取不算 backend action。Harness 选出的 pose 导致外部拒绝是 Harness terminal，不增加 Agent invalid action。无进展 spam 由 no-progress/tool-iteration guard 处理，不能用虚假的环境步数或 invalid count 代替。

### 10.3 Runtime/Provider 归一化

Runner 实际收到的是 `GenericAgentRuntime` 的规范 error code，而不是异常类名。封闭映射如下：

| runtime error code | EpisodeClassification |
|---|---|
| `transport_error` | `provider_failure` |
| `context_length_exceeded_after_compact` | `runtime_failure` |
| `tool_result_id_mismatch` | `runtime_failure` |
| `model_output_truncated` | `agent_model_failure` |
| `max_tool_iterations_exceeded` | `agent_model_failure` |
| `max_no_progress_iterations` | `agent_model_failure` |
| `benchmark_env_step_limit` | `agent_model_failure` |
| `benchmark_invalid_action_limit` | `agent_model_failure` |
| `benchmark_done_without_won` | `agent_model_failure` |
| `user_interrupted` | `cancelled` |
| 已有 terminal Harness classification | 原样保留 |
| 未知 code | `unclassified_execution_failure` |

`provider_error` 是 provider 层诊断 subtype，`LLMProviderError` 是异常类名，都只进内部 trace，不作为 Runner error code 比较。Dispatcher executor 异常必须归一化为 terminal `runtime_failure`，不能吞成普通 ToolResult 后落入 Agent failure。普通 Runner、taskset Runner、Outcome、summary 和 CLI 共用同一映射函数。

### 10.4 Provider retry 提交边界

安全重试只能在仍掌握流状态的 `GenericAgentRuntime`/transport 边界实现，Runner 不做盲重试。每次模型请求记录 `model_attempt_id`、输入消息 snapshot hash、assistant-response commit 状态和 tool-dispatch commit 状态。

仅当**当前 attempt** 同时满足以下条件时可用同一消息 snapshot 重试一次：

```text
错误明确可重试
assistant response 尚未写入 session
当前 attempt 没有 tool call 被 Dispatcher 接收
当前 attempt 没有外部动作
提交状态完全可判定
```

重试使用新 attempt ID，最多一次并完整记录。出现 partial session commit、tool dispatch 或状态未知时不重试；直接 terminal `provider_failure`。

### 10.5 指标与正式分数

分别报告：

```text
raw_success_rate = successes / total
evaluation_valid_coverage = agent-score-eligible episodes / total
agent_success_rate_on_valid = agent successes / agent-score-eligible episodes
harness_coverage = 1 - harness contract failures / total
provider_availability = 1 - provider failures / total
runtime_availability = 1 - runtime/artifact failures / total
cancelled_episodes = count(cancelled)
```

`harness contract failures` 是 `harness_grounding_failure/harness_navigation_failure/harness_operation_failure` 和 robot action 产生的 `execution_state_uncertain`；`unclassified_execution_failure` 单独报告。Cancelled Episode 不冒充 Runtime failure，但会降低 evaluation valid coverage 并使正式分数不可用。

旧名 `harness_valid_coverage` 混合了所有 score-ineligible 原因，停止作为权威指标；若为兼容保留，只能作为 `evaluation_valid_coverage` 的 deprecated alias。

正式分数要求：

```text
evaluation_valid_coverage = 100%
harness_coverage = 100%
provider_availability = 100%
runtime_availability = 100%
unclassified_execution_failures = 0
formal_score_available = true
```

## 十一、可观测性与安全

正式 Oracle/执行/Provider 链写入结构化 JSONL：

```text
oracle_context_created
oracle_pose_lookup_started
oracle_pose_lookup_result
oracle_move_started
oracle_move_result
oracle_visibility_gate_result
oracle_action_started
oracle_action_result
external_state_read
oracle_context_invalidated
oracle_context_rebased
oracle_context_consumed
model_attempt_started
model_attempt_failed
model_attempt_retried
execution_terminal
```

内部事件保留：

- exact requested target ID；
- exact navigation anchor ID；
- Oracle pose hash；
- requested/actual pose；
- 外部返回状态；
- target visible/bbox 门；
- 每个 read 的 `ok/absent/malformed/stale/error`；
- before/after snapshot hash 和 context state transition；
- raw event ref/hash；
- 单次耗时、backend/env action count；
- model attempt ID、message snapshot hash 和 assistant/tool commit flags。

模型投影递归禁止：

```text
objectId
坐标和旋转/俯仰角
Oracle map 原始记录
隐藏 parent membership
全场景对象列表
专家 trajectory 字段
raw THOR error/stack
绝对路径
API token/认证 header
```

Trace 必须记录 Runner 提供的当前 trial 稳定 fingerprint，解决现有 artifact 无法事后唯一恢复 trial 的问题；不得从专家 trajectory 推导 fingerprint。内部 trace 可以保存受控 raw external detail 和本地 evidence ref，但模型 serializer 只允许固定字段与 allowlisted detail template。

## 十二、代码迁移边界

### 12.1 删除正式路径

实施完成后删除或隔离以下生产能力：

- `_teleport_candidates()` 和 `_single_target_teleport_candidates()`；
- `_navigation_budget_stop()`；
- navigation candidate/action/time 默认常量；
- Put local candidate 构建、排序和预算循环；
- candidate hash/list 的模型无关生产状态；
- 所有依赖上述预算的产品测试和文档说明；
- `robot_find_object` 的 ToolSpec、registry、executor、Adapter `find_object()`、`_search_visible_object_source()`、隐藏 parent/source resolver 和 admissible `go to` 循环；
- `robot_navigate` 的 ToolSpec、registry、executor、`virtual_navigate()` 以及 THOR 正式路径的 direct legacy `env.step()`；
- `_last_go_to_object_id` 和操作时按类型重新选择 exact instance 的 fallback；
- `PoseContext` 的 candidate list/hash、本地 Put retry executor、budget 类型/默认值/trace 字段和相关 Protocol；
- `_put_visible_base/_put_visible_payload/_go_to_visible_payload/_visual_error` 等 action-specific 模型投影和 dict fallback；
- Adapter 把 inventory/object state 塞入 `tool_args` 的结果通道；
- Dispatcher 对 typed result 再 `setdefault` 或覆盖 success/error 的路径；
- 所有绕过 `OracleActionGateway` 的直接 THOR action helper。

历史 evidence helper 可以保留在 ignored `var/` 中用于复盘，但不能被产品模块 import。

### 12.2 保留并复用

- `SceneObjectIndex` 的 canonical label 与 exact ID 锁定，但 hidden object 不进入 movable/toggle 可见解析；
- requested/actual pose 比较；
- 精确可见/bbox 门；
- inventory、`isPickedUp`、parent/child 外部终态门；
- EpisodeOutcome 和 terminal dispatch gate 的结构，但改用 10.1 的封闭分类；
- 模型安全投影中的内部字段排除机制；
- JSONL raw event ref/hash；
- `robot_go_to` 作为唯一公开导航名，以及 `robot_manipulate/robot_verify` 公共契约。

`AlfworldStepResult` 新增必填 `execution_feedback`；旧字符串 `feedback` 若保留只能更名为 internal debug 字段。`AlfworldEnvState.to_model_visible_dict()` 不再成为正式 THOR tool result 的状态源。

### 12.3 接口一致性

新增或修改 Protocol 后，必须同步所有真实实现、Fake/Mock 和测试 double，并运行公开方法覆盖审计。单测不得只使用与产品 Adapter 数据形状不同的手工 dict。

增加 cleanup/import guards：

- 正式 registry 和 Dispatcher 直接调用均不存在 `robot_find_object/robot_navigate`；
- 产品模块不得 import ignored evidence helper；
- 产品代码不得访问 `high_pddl/low_actions/expert_plan`；
- 除 `OraclePoseStore` 实现外不得访问 `controller.receptacles/locs`；
- 除 `OracleActionGateway` 外不得发出 Gate A 列出的 THOR action；
- 旧 candidate/budget/projection symbol 在产品模块中搜索结果为零。

同步普通 Runner、taskset Runner、两套 summary、CLI 消费者、trace renderer、所有 Fake Adapter/ExecutionBackend 和顶层 Put 假 fixture。接口 audit 必须枚举全部实现并断言公开方法和 typed return 完整，不能只测一个 mock。

## 十三、验证计划

### 13.1 Gate A：实施前 Oracle runtime feasibility

在改产品代码前，使用独立、不可 import HomeMaster 产品逻辑的 probe，对固定 exact trial 逐实例核对：

| 类别 | 至少覆盖 |
|---|---|
| surface/receptacle | Shelf 低/高层与全部同场实例、Drawer、Cabinet、CounterTop，以及固定 10 条涉及的 Desk/Table/Bed 等全部 exact instance |
| tool/appliance | CoffeeMachine、Microwave、Fridge、SinkBasin/Faucet |
| toggle/fixture | DeskLamp、FloorLamp 及其 visible-parent anchor |
| movable | 表面、打开容器、关闭容器、地面、inventory 中的 exact object |
| action | Take/Open/Close/Put/Use/Slice 及 Heat/Cool/Clean 每个 macro 子动作 |

每个实例独立 reset，并分别断言：

1. exact requested target 或锁定 anchor 的 map ID 可查询；
2. 对应 locs 解析确定性；
3. 移动返回成功；
4. actual pose 等于请求；
5. 准确目标 `visible=true`；
6. 准确 bbox 面积大于零；
7. 最终图片像素等于 event frame；
8. 对该实例适用的动作逐个核对外部返回码和准确终态；
9. lookup `absent/malformed/stale/error` 负向输入在 raw evidence 中可区分并使 probe 非零退出；Gate A 不冒充产品分类测试；
10. evidence 记录 exact trial/instance/action、raw event ref/hash 和进程退出码。

不得用 best/any 聚合。一个实例失败即该实例 contract 未通过，不能用同类型成功实例抵消。Gate A 只解除底层 feasibility，不解除产品接线 UNVERIFIED。

### 13.2 Gate B：实施后产品接线黑盒

实现后，用真实 `AlfworldEnvAdapter -> AlfworldStepResult.execution_feedback -> tools -> Dispatcher` 重跑 Gate A 的 exact matrix。每个实例独立 reset，并同时断言：

- Oracle lookup 绑定当前 reset/trial；
- 产品只发送一次锁定移动或预定 macro 子动作；
- 返回码、actual pose、exact visibility/bbox、外部动作终态全部通过；
- persisted frame 像素等于最终 event frame；
- 模型 JSON 等于 typed feedback serializer，且不含任何内部字段；
- lookup 各种非 ok status 分别映射到 §7.5/§10.1 的产品分类；
- trace 中 backend/env/event 计数与真实请求逐条一致；
- 进程退出码为 0，失败实例不能被聚合隐藏。

Gate B 全过才可把 V1.8 产品接线标为 VERIFIED。

### 13.3 隐藏对象与旧旁路黑盒门

构造准确 Mug 位于关闭 Drawer：

```text
robot_go_to("mug")
```

逐项断言：

- 返回 `target_not_visible`；
- backend action count 为 0；
- THOR pose 和 scene state 不变；
- 模型结果不包含 Drawer label、parent membership 或 objectId；
- Episode 不 terminal；
- 随后模型可正常调用 `robot_go_to("drawer")`；
- `robot_find_object` 和 `robot_navigate` 不在 registry/tool specs；直接提交旧名称返回 `unknown_tool` 且 Adapter/THOR 调用计数为零；
- hidden parent/source helper 和 legacy `env.step("go to ...")` 的生产 import/call guard 为零。

### 13.4 Context 状态机门

至少逐条覆盖：

```text
go_to(drawer) -> Put(target_closed, 0 action) -> Open(success) -> Put(success)
go_to(mug 2) -> Take(mug 2)，不得换成 mug 1
幂等 Open/Close 保留 context
成功 Open/Close/Use/Slice 只 rebase successor event
成功 Take/Put/macro consume context
reset/新移动/pose drift/event gap/无关动作 invalid context
```

每一步断言 context state、target/anchor ID、pose、event sequence/hash 和 backend action count；不得只看 trace 事件名称。

### 13.5 操作黑盒门

每个适用实例同时断言返回码和外部终态：

- Take：准确对象进入 inventory 且 `isPickedUp=true`；
- Open：`isOpen false -> true`；
- Close：`isOpen true -> false`；
- 关闭 target 的 Put：零请求、inventory 不变、`target_closed`；
- 成功 Put：inventory、`isPickedUp`、准确 parent/child、goal；
- Use/Slice：准确实例的 action-specific state transition；
- Heat/Cool/Clean：每个锁定子动作和最终准确对象状态；
- 失败/异常：完整动作状态不变或分类 uncertain，禁止继续执行。

### 13.6 反馈投影集成门

测试必须从真实 Adapter 生成 `AlfworldStepResult`，再经过与产品一致的 tools/Dispatcher 投影。禁止直接把期望字段放在顶层伪造输入。

至少覆盖：

```text
target_not_visible
navigation_required
target_closed
object_not_held
Take success/failure
Open success/failure
Put success/failure
Use/Slice success/failure
Heat/Cool/Clean success/failure/partial change
terminal Harness failure
```

每条断言模型 JSON 的 success、status、inventory、object/target state、state_changed、error、terminal/classification 与独立外部读取一致。缺失读取测试必须得到 `null + 非 ok status + terminal uncertain`，不得得到默认空值。测试还要证明 tools/Dispatcher 不读取 `tool_args`、字符串 inventory 或 debug feedback 重建结果。

### 13.7 Provider/runtime 分类注入门

用真实 `RuntimeResult.error_code` shape 参数化注入 10.3 的每个 code，断言封闭分类、score eligibility 和两套 Runner/summary 一致。对 `transport_error` 另断言：

- classification=`provider_failure`；
- score_eligible=false；
- 当前 attempt 无 assistant/tool commit 时最多一次有界重试，并产生两个 attempt ID；
- 当前 attempt 已提交 assistant/tool、状态未知或第二次失败时不重试；
- Provider availability 降低，Harness coverage 不变，evaluation valid coverage 降低，Agent 分母不包含该 Episode。

未知 runtime code 必须成为 `unclassified_execution_failure`，不能落入 Agent failure。Dispatcher executor exception 单独注入并断言 `runtime_failure`。

### 13.8 回归与产品验收

1. 全仓测试、聚焦 ALFWorld 测试、接口 audit、Ruff、format、compileall、cleanup guard、`git diff --check`；
2. 同一真实 API 配置重跑原 10 条 valid_unseen；
3. evaluation valid coverage、Harness coverage、Provider availability、Runtime availability 必须分别为 100%；
4. raw success、Agent 成功率和四项 availability/coverage 分开报告；
5. 按 look-at、simple place、heat、cool 等任务族逐类报告；
6. 每个失败 Episode 保留唯一分类和可追溯外部证据；
7. 成功率不是设计验收的替代品，外部终态逐实例门必须全部通过。

## 十四、实施顺序

设计批准后的实施顺序固定为：

1. 用户批准本文后，先写精确 implementation plan 并按项目纪律做实施前独立评审；
2. 完成 Gate A standalone runtime feasibility；只解除底层 feasibility，不声称产品集成 VERIFIED；
3. 先写 RED tests：typed feedback 唯一链、封闭错误映射、计数矩阵、旧工具/legacy 调用负向 guard；
4. 新增 `OraclePoseStore/VisibleObjectView/NavigationAnchorResolver` 强类型接口及全实现 audit；
5. 新增 `OracleExecutionContext/OracleActionGateway`，覆盖 Take/Open/Close/Put/Use/Slice/Heat/Cool/Clean；
6. 将 `robot_go_to` 切到唯一 Oracle 路径，并删除 find/navigate/candidate/local-put/budget/projection 旧路径；
7. 在 GenericRuntime/Runner/Outcome/taskset/summary/CLI 同步 Provider/runtime/Harness/Agent 分类、attempt retry 和指标；
8. 跑全仓内部验证、接口 audit、cleanup/import guards；
9. 跑 Gate B 产品逐实例黑盒，解除或否决产品集成 UNVERIFIED；
10. 用同一真实 API 配置跑原 10 条 valid_unseen 回归；
11. 同步架构、用户指南、README、CHANGELOG、pitfalls 和正向纪律。

任何 Gate A/B linchpin 失败、外部状态矛盾或接口数据形状不一致，都必须回到设计，不得用 fallback mode 掩盖。

## 十五、设计不变量

1. 成功导航的准确 requested target 必须在最终 event 中真实可见并有正面积 bbox。
2. 隐藏 movable object 不得由 Harness 自动定位到 parent。
3. 模型选择目标和动作；Oracle map 只提供低层 receptacle 位姿。
4. 正式运行不得读取专家任务轨迹。
5. 同一 exact target、scene generation 的 Oracle pose 必须确定性且一次锁定。
6. 所有正式动作使用一次锁定的 Oracle pose；Open/Put 不搜索姿态、不重试、不动态扩大预算。
7. 关闭容器 Put 必须在外部调用前返回 `target_closed`。
8. 每个正式 THOR robot 工具的模型执行反馈只能来自 `AlfworldExecutionFeedback` 的唯一 serializer。
9. 缺失权威字段必须携带非 ok status；不得静默替换为空列表、null 或 false 并冒充有效读取。
10. Provider/Harness/runtime 失败不得进入 Agent 分数。
11. 所有外部调用同时核对返回码和外部终态。
12. 多实例验收逐实例断言，不得聚合取最好结果。
13. `robot_go_to` 是唯一公开导航入口；旧 find/navigate、legacy env command 和候选搜索不可达。
14. Take 和所有操作使用 context 中锁定的 exact ID，不重新按类型选择实例。
15. Open/Close 等同 pose 状态动作只可在双门成功后原子 rebase context；移动、event gap 和不确定状态必须 invalid。
16. Oracle lookup/visibility 的 absent、malformed、stale、error 与真实不可见必须保持可区分。
17. Agent、Harness、Provider、Runtime 指标和责任分类分别计算，未知错误不得默认归 Agent。

## 十六、独立评审意见处置

独立设计评审 verdict 为 `FIX`；代码边界审计同样只读且未修改文件。主 agent 对意见逐条处理如下。本轮按用户要求不再发起第二轮评审，修订稿由用户最终批准。

| # | 评审意见 | 处置 | 文档修改 |
|---:|---|---|---|
| 1 | `robot_find_object/robot_navigate` 是真实导航旁路 | 采纳 | §1、§7.1、§12、§13.3 删除 registry/Dispatcher/Adapter/legacy 路径并加负向门 |
| 2 | Feedback 缺 success、tri-state 和唯一数据流 | 采纳 | §6.6、§9 定义完整 typed envelope、read status 和 Adapter 到 Dispatcher 唯一链 |
| 3 | standalone probe 不能解除产品接线 UNVERIFIED | 采纳 | §5.3、§13.1-13.2 拆为 Gate A feasibility 与 Gate B product integration |
| 4 | Open 新 event 会让后续 Put context 冲突 | 采纳 | §6.5、§8.4、§13.4 定义 keep/rebase/consume/invalid 状态机 |
| 5 | visible movable 多历史 parent 时 anchor 不确定 | 采纳 | §6.3 只接受唯一 reciprocal 最内层 anchor；零个/多个 terminal，不 `.first` |
| 6 | Tool error 与 Episode classification 混用且不穷尽 | 采纳 | §7.5、§10.1 定义两个封闭集合、组合门和总映射 |
| 7 | typed feedback/双门未覆盖全部公开 manipulation | 采纳 | §8.3-8.8、§13.5-13.6 覆盖 Take/Use/Slice/Heat/Cool/Clean/Verify |
| 8 | env/backend/invalid 计数和 coverage 混淆 | 采纳 | §10.2、§10.5 分开四类计数和五个指标 |
| 9 | Oracle/visibility 用 `None` 混合缺失、损坏、stale、不可见 | 采纳 | §6.1-6.2、§7.5 引入强类型 read status 和负向映射 |
| 10 | Provider retry 提交边界不可审计 | 采纳 | §10.3-10.4 使用真实 runtime code、attempt ID 和当前 attempt commit 门 |
| 11 | Take 未使用 exact movable lock | 采纳 | §8.1、§8.3 删除 `_last_go_to_object_id` 字符串旁路并使用 context exact ID |
| 12 | “不自动 Open/Put”与抽象 Heat/Cool/Clean 冲突 | 采纳 | §3.2、§8.7 明确 direct Put 与模型显式 macro 的单一路线，不设 fallback mode |
| 13 | toggle、地面/held movable、TextWorld 范围未定义 | 采纳 | §3.0、§6.3、§7.2-7.3、§13.1 明确 scope、统一 anchor 失败和验证矩阵 |
| 14 | 删除、接口实现、summary/CLI/测试同步边界不完整 | 采纳 | §12.1-12.3 列全生产 symbol、消费者、Fake 和 audit |
| 15 | 外部符号标注不足 | 采纳 | §5.1-5.3 区分历史原语证据与 V1.8 全链 UNVERIFIED，并禁止评审背书 |

没有拒绝项。Residual risk 仍是 Oracle pose 可能只可见不可操作、visible target 可能没有唯一 anchor；Gate A/B 失败时必须回到设计，不能新增 candidate、legacy 或专家 fallback。
