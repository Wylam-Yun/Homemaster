# ALFWorld Oracle 位姿执行与权威反馈契约

日期：2026-07-16

状态：第一次真实 Gate A 已证明原 `controller.receptacles` 单独作为 pose 源不可行；用户已批准 bounded reset-time scene scan、单一 immutable snapshot 和 all-target current strict-visible authorization。`discovery-run-006` 的 post-Open 失败已由冻结 raw artifact 重建验证，`discovery-run-007` 也真实跨过该失败点，但第 5 个 trial 暴露出 scan 期间 THOR `ObjectTemperature` 随模拟时间自然变化的新边界。用户已批准在 setup scan 外围使用固定 `ChangeTimeScale(0.01) -> ... -> ChangeTimeScale(1.0)` 事务，不删减完整 world digest，不改变可见性授权。本文写入该用户主导 delta，等待用户复核冻结字节；`discovery-run-007` 保持不可变，Gate helper/产品实现继续冻结，新组合在 fresh `discovery-run-008`、case run 和 Gate B 逐实例通过前保持 `UNVERIFIED`。

关联证据：

- 真实运行：`alfworld-valid_unseen-v17-realapi-20260713-001`
- 基线提交：`0fdfeaa00b921d8ea347655ecbd4c32b9ff30d6d`
- 被替代的运行策略：V1.7 导航候选枚举与 Put 局部姿态枚举
- 第一次真实 Gate A 失败：`var/alfworld-evidence/20260713-v18-gate-a/discovery-run-001/`
- V2 schema adapter 失败：`var/alfworld-evidence/20260713-v18-gate-a/discovery-run-002/`
- Run-003 sole-pose 证据：`var/alfworld-evidence/20260713-v18-gate-a/discovery-run-003/`
- Run-006 immutable post-Open 失败与重建回放：`var/alfworld-evidence/20260713-v18-gate-a/discovery-run-006/`，summary SHA-256 `36bcfe4baef404df4f60452a67091111ea4513d21d1d385846ada64688c63b34`
- Run-007 immutable 温度漂移证据：`var/alfworld-evidence/20260713-v18-gate-a/discovery-run-007/`，summary SHA-256 `9633c3d94c16f5345288f53f022457e6c73fd960fcc3ffb9fdac9f8c3fe07de2`，失败 result SHA-256 `abd45bc37a76e9d9f98120364da8bdacab99dad8b8174321d021065c593188af`
- 锁定 ai2thor 2.1.0/Unity build 的独立时间控制：`PausePhysicsAutoSim` 无法阻止温度演化；`timeScale=0.01` 覆盖 query + 26 scan Teleports + pose restore 时完整 world 稳定，恢复 `1.0` 后同一温度演化重新发生
- 用户提供并批准的导航模块 contract：所有物理 target 必须 current strict-visible；真实产品组合在 Gate B 前仍 `UNVERIFIED`

## 一、决策摘要

本项目评测模型的高层规划、语义目标选择和基于可见观察的探索，不评测低层机器人位姿搜索。ALFWorld 的 `controller.receptacles` 只覆盖静态 receptacle，真实 FloorLamp 没有 direct entry 或 parent；因此正式运行采用一次 target-independent setup scan 建图、动作阶段单 pose 的唯一执行路线：

```text
exact trial reset
-> 在第一次模型请求前冻结 bounded scene scan plan
-> 通过唯一 gateway 把 simulation time 锁定为 0.01
-> 完整执行 query/scan 并恢复准确初始 pose/world
-> 通过同一 gateway 恢复 simulation time=1.0，验证该返回 event 的 pose/world/frame
-> 原子发布每个 exact ID 的 one-pose-or-typed-absence OraclePoseSnapshot
-> 模型选择语义目标和动作
-> Harness 只从当前 event 的 strict-visible exact objects 确定性解析准确实例
-> 任一物理目标当前不可见时返回零动作、非终止 target_not_visible
-> Harness 从同一 immutable snapshot 取得准确 target 或唯一 parent anchor 的单一位姿
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

唯一允许的 pose 枚举发生在 reset transaction：scan policy/config、固定 slow/restore time-scale 值与顺序在第一次 setup action 前冻结；唯一 `GetReachablePositions` 返回后、第一次 scan Teleport 前冻结完整 Teleport plan。两级锁都与 task 文本、requested target、action profile 和专家字段无关，找到对象后仍完整执行。时间控制只为防止 Harness scan 自己消耗的引擎时间改写 world digest，不允许删除 raw `ObjectTemperature`、改写 ALFWorld heat/cool sets 或将温度差异降级为无关字段。动作阶段不得生成候选、早停、因失败换 pose、动态扩大计划或 fallback。每次 `robot_go_to` 必须先从当前 event 锁定 strict-visible exact target，未通过该门时不得查询 snapshot 或 parent pose。通过后，current direct row=`ok` 使用 sole target pose；`unobserved/relocated/absent` 只允许为同一个 current-visible target确定性选择唯一 reciprocal parent。parent pose 到达后若准确 target 不可见，Episode 作为 Harness navigation failure 终止，不再尝试第二个 pose。

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

本设计不再为 Open 增加另一套候选循环，而是统一使用 reset transaction 发布的 sole snapshot pose。

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

### 2.6 Oracle receptacle map 不是通用对象 pose map

第一次真实 Gate A 在 `episode-0001-candidate-1` 的无测试动作 discovery 阶段退出 2。raw reset 中两盏 FloorLamp 均存在且 `toggleable=true`，但 `visible=false`、无 bbox、无 parent，也不在 24-entry `controller.receptacles`；四个 Statue 均 `pickupable=true` 且有唯一 reciprocal Oracle parent，但 reset 时全部 strict-invisible。该失败不是对象缺失或 action 字段错误，而是 pose-source/当前可见性契约与真实场景不相容。

安装源码确认 Oracle controller 只把 `STATIC_RECEPTACLES` 写入/加载 `receps.json`；FloorLamp、DeskLamp 和 Statue 都不属于该集合。V1.7 真实 API Episode 0002 又提供正交控制：FloorLamp pose `(-1.5, 0.9010564, 3.75, 90, 30)` 两次移动均返回成功、准确可见且 bbox area=`13455`，但该完整 pose 不在 exact trial `receps.json` 或 `FloorPlan219-openable.json` 中。它是旧 deterministic geometry 的第一个候选，因此证明 cache-only 方案覆盖不足，而不是证明 V1.8 Toggle 终态已经通过；旧 artifact 未保存 raw Toggle before/after，Use 仍为 `UNVERIFIED`。

同一真实 run 还证明 bounded geometry 不需要动作时重试：已知 FloorLamp 的第一个 canonical geometry pose 即成功。新设计只把这一确定性生成规则移到 scene-wide setup，并对每个 cache-missing addressable exact ID 至多生成一个 geometry pose；`required_coverage_set` 中任一 Gate A coverage miss 仍停止设计，不扩大为 full lattice。

## 三、目标与非目标

### 3.0 适用范围

V1.8 的正式评分路径只指 `AlfredThorEnv + visual_eval`；本文 all-target current strict-visible 门也只指该视觉路径。`AlfredTWEnv` 没有 THOR 相机，不执行 scene scan、setup count 固定为 0，并按自身当前文本 observation翻译同一个 `robot_go_to`，不在本文 THOR snapshot/visibility 正确性声明内；但不得保留 `robot_find_object` 或 `robot_navigate` 作为 Dispatcher 可执行旁路。

`AlfworldStepResult.execution_feedback` 对两种 env 都必填。TextWorld 成功/普通无效动作使用同一个 typed envelope，但 THOR 专属值为 `None` 且对应 status 为 `not_applicable`；TextWorld 外部异常直接进入 Dispatcher 注入的 Runtime failure observer，不伪造 THOR 状态。

### 3.1 目标

1. 让模型负责高层语义动作、目标选择和所有使目标进入当前画面的探索；任何物理目标只有 current strict-visible 后才可导航。
2. 让一次可审计的 bounded reset scan 生成当前 scene/trial 的唯一低层 pose snapshot；动作时不再搜索。
3. 保证任何成功导航的准确目标在最终 event 中真实可见。
4. 禁止 Harness 利用隐藏对象的 parent 信息替模型完成搜索。
5. Setup scan、Take/Open/Close/Put/Use/Slice 与抽象 Heat/Cool/Clean 全部通过同一个外部动作网关；正式动作只使用一次锁定的 pose。
6. 所有正式 THOR robot 工具的模型可见执行状态来自一个强类型、权威的数据结构。
7. Provider/runtime/Harness/Agent 失败具有互斥且可审计的评分语义。
8. 任何外部调用同时通过返回码门和外部终态门。

### 3.2 非目标

- 不评测模型生成坐标、旋转角或相机俯仰角的能力。
- 不让模型读取 objectId、坐标、scan plan/snapshot、专家动作或 pose 排名。
- 不自动告诉模型隐藏 Mug 位于哪个 Drawer。
- 不为任何当前不可见目标查询 snapshot/parent、自动搜索、导航其他目标或执行 Open；`robot_go_to` 只返回零动作、非终止 `target_not_visible`。
- 不读取 `traj_data.plan.high_pddl` 或 `low_actions` 选择目标实例或动作。
- 直接 Put 不自动打开容器，直接 Open/Close/Put 的顺序由模型决定。模型显式选择的抽象 Heat/Cool/Clean 保留现有高层动作语义；其内部子动作是该高层动作的实现，不是 Put 的隐式 fallback，并且每个子动作都必须经过同一返回码/终态门。
- 不在 snapshot lookup 或正式动作失败后回退到第二个 pose、240 候选搜索、full reachable lattice 或 twin environment。
- 不在本设计中实现未来记忆模块。
- 不声明未真环境核对的 ALFWorld 内部属性为稳定公开 API。

## 四、候选方案与取舍

### 4.1 方案一：current-visible authorization + bounded scan 单一 snapshot（采用）

reset 后先锁定初始 pose/world/frame，调用一次 `GetReachablePositions`，把合法 exact cache pose 与每个 cache-missing addressable exact ID 的至多一个 canonical geometry pose 合并、去重、排序并冻结 hash。完整扫描后恢复初始状态，只有所有 setup 返回/pose/world/restore 门通过才原子发布 `{exact_id -> one pose or typed absence}`。cache 与 geometry 只是构建输入，动作时必须先由当前 event 的 `VisibleObjectView` 授权并锁定 exact ID，之后 snapshot 才是唯一 pose 权威源。

优点：

- 补齐 parentless FloorLamp/DeskLamp 等 receptacle map 的结构性缺口；
- setup 计划有界、target-independent，动作阶段不需要候选搜索或经验预算；
- 不读取专家任务答案；
- reset 恢复、setup 计数、失败分类和证据由 HomeMaster 同一产品边界掌控；
- 所有正式动作共享一个 snapshot authority；
- 可见性只来自 current event，snapshot/object map/containment 都不能替代授权，所有 target type 使用同一条规则。

代价：

- reset 会增加数十个 setup backend actions，Gate A/B 成本显著上升；
- `GetReachablePositions`、cache shape、geometry 规则、反复 Teleport 和精确 restore 都必须真环境逐 trial 核对；
- bounded plan 仍可能出现 per-ID coverage miss；visible target 无 pose 时按 typed Harness failure 处理，不动态扩展；
- reset scan 即使已经知道屏外 exact ID/pose，也必须等待模型或未来记忆/探索模块让目标重新进入当前画面，可能降低当前 Agent 成功率。

### 4.2 方案二：current-visible 后现场生成/尝试候选（拒绝）

当前 event 先通过 strict-visible 门，再根据对象位置、reachable set、rotation/horizon 生成并尝试一组候选。它对被移动对象更灵活，也可减少 reset setup，但会恢复 V1.7 的 action-time 搜索、多次真实移动、候选预算和失败后世界一致性问题；即使每次调用一次锁定候选顺序，也明显扩大状态机和验证面，因此拒绝。

### 4.3 方案三：暂停 V1.8，等待记忆模块与导航一起重做（拒绝）

未来记忆模块可以记录目标曾出现的位置并规划探索，让目标重新进入当前画面；但它仍需要一个低层执行位姿方案。等待整体重做能一次统一职责，却会阻断当前 V1.8 的操作位姿、强类型反馈和归责修复，因此本轮不等待。记忆模块未来只能帮助获得 current-visible observation，不能绕过本设计的可见性门。

### 4.4 方案四：cache-only / twin-env / 离线 pose catalog（拒绝）

FloorPlan 10/219/308 的 cache/static basis 只有约 13-20 个 unique poses，已知成功 FloorLamp pose 不在 exact `receps.json` 或 `openable_points`，cache-only 覆盖不足。twin-env/离线 catalog 又要求 exact trial、RNG、object IDs、物体姿态、引擎/Unity build 和 world state 完全相等，并把返回码/计数移出当前 gateway 证据边界。只保留未来 `ScenePoseSnapshotProvider` 接口扩展位，不作为 fallback。

## 五、外部运行时契约

### 5.1 历史证据与当前声明的边界

安装源码和真实 `valid_unseen` 数据中已经观察到 Oracle controller、receptacle 导航记录和标准高层 GOTO 调用链。V1.7 的独立探针还验证过部分 `TeleportFull`、`OpenObject`、`PutObject`、pose、可见性、bbox、inventory、`isPickedUp`、parent/child 和 frame 原语；Shelf 3/4/6 通过过旧候选路径的产品黑盒。

这些是“单个原语在旧路径中曾工作”的历史证据，不是“V1.8 scan/snapshot 接线可用”的证据。新的 time-scale bracket、scan plan、world digest/restore、snapshot lookup、single-pose target/anchor 组合和 HomeMaster 数据流在产品黑盒通过前全部保持 **UNVERIFIED**。验证身份不能只记 package version：Gate A/B 还必须锁定实际 import origin、相关源码字节 hash、Unity build/scene asset identity、scan policy/geometry code hash、cache 原始字节/hash、reachable 原始 payload hash和 canonical reachable-set hash。

### 5.2 V1.8 产品路径的 UNVERIFIED 外部符号

以下外部符号或组合只确认存在或有局部历史证据，评审不为其在 V1.8 产品路径中的可用性背书：

```text
controller.type = oracle
  batch_env.envs[0].controller
  controller.receptacles
  controller.receptacles[exact_object_id]["locs"]（仅 scan seed）
  GetReachablePositions / actionReturn / reachablePositions
  batch_env.envs[0].traj_data
  batch_env.envs[0].task_file / traj_root

event.metadata.objects / inventoryObjects
  objectId / position / rotation / visible / isOpen / openable / receptacle / isPickedUp
toggle/slice/heat/cool/clean action-specific state fields（真实名称待 Gate A 核对）
parentReceptacles / receptacleObjectIds
event.instance_detections2D / event.frame
lastAction / lastActionSuccess / errorMessage
  agent.position / agent.rotation / agent.cameraHorizon
  sceneName / isSceneAtRest
  cleaned_objects / heated_objects / cooled_objects
  task goal_idx / finished / step_num / goal_finished

TeleportFull / PickupObject / OpenObject / CloseObject / PutObject
ToggleObjectOn / ToggleObjectOff / SliceObject
ChangeTimeScale / timeScale
forceAction / placeStationary / rotateOnTeleport / horizon
```

上述符号“存在”不表示组合可用。reachable/cache 的真实 shape、有限数值约束、canonical geometry、bounded plan、逐 pose world-state 不变、准确 restore、单 pose 选择、scene/trial 绑定，以及 snapshot pose 对每种目标是否同时满足可见和可操作，都属于 UNVERIFIED contract，而不是实现细节。

对锁定的 ai2thor 2.1.0/Unity build，独立真环境控制已证明 `ChangeTimeScale(0.01)` 与 `ChangeTimeScale(1.0)` 请求都返回成功：前者使 query + 26 scan Teleports + exact pose restore 期间的完整 world projection 稳定，后者之后原温度演化在第 7 个 post-control Teleport 重新出现。这只解除两个原语在当前 build 的底层行为疑问；它们在 probe/controller/verifier 真实入口中的顺序、失败恢复、计数、evidence 和最终 model-visible event 组合仍为 `UNVERIFIED`，必须由 fresh run-008 解除；产品组合仍必须由 Gate B 解除。

Logical scene 与 runtime scene 不做通用字符串归一化。当前唯一允许验证的映射是锁定 ai2thor/runtime build 下的精确 `FloorPlanN -> FloorPlanN_physics`；不得 strip 任意 suffix、接受双 suffix 或把同编号的未知 asset 当成功。版本、build 或 advertised asset 集变化后，该映射重新变为 UNVERIFIED，必须先过 Gate A。

### 5.3 两级解除门

外部符号不得被一次独立 probe 直接宣布为“产品已验证”：

1. **Gate A，实施前 runtime feasibility**：独立 probe 不 import HomeMaster，核对 current runtime、exact trial、logical/runtime scene identity、import/source/Unity identity、time-scale enter/restore 行为、bounded scan/restore、snapshot lookup、单 pose 导航、外部返回码和逐实例终态。Gate A 通过只允许开始实现。
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

产品部署不得硬编码 `hkust4`、仓库绝对路径或 `localhost`。ALFWorld root、config、backend endpoint 和 evidence root 都由 Runner/Adapter 配置注入；服务端地址允许本机或远端。跨边界保存的 evidence ref 必须是相对当前 evidence root 的路径或 opaque ID，模型投影和可移植结果中不得出现绝对路径。Gate B 用两个不同 root/host 的 fake 配置做契约测试；真实远端路径只允许出现在运行命令和受控内部证据中。

Portable ID 只接受 canonical POSIX root-relative string：拒绝绝对路径、空/`.`/`..` segment、NUL、反斜线或混合/歧义 separator；`resolve(root / id)` 必须仍位于注入 root 内，symlink escape 直接 artifact failure。trial/source identity hash 使用 canonical relative ID bytes + 文件内容 bytes，不使用 resolved absolute path、root 或 host。evidence ref 应用同一 containment/escape 规则；opaque ID 由 store 接口解析，不作为 filesystem path 拼接。

## 六、组件边界

### 6.0 Typed reset transaction

普通 Runner 和 taskset 第一 subtask 都必须在构造 `GenericAgentRuntime`、Provider client 或第一次模型请求前完成 reset transaction。Adapter 不再只返回 `AlfworldEnvState`，而返回 frozen `AlfworldResetResult`：

```python
SetupFailureCode = Literal[
    "external_reset_failed",
    "initial_state_unreadable",
    "reset_identity_unreadable",
    "expected_manifest_mismatch",
    "runtime_scene_mismatch",
    "addressability_unreadable",
    "cache_input_malformed",
    "scan_time_scale_enter_rejected",
    "scan_time_scale_enter_unreadable",
    "reachable_query_rejected",
    "reachable_query_unreadable",
    "scan_plan_missing",
    "scan_plan_malformed",
    "scan_pose_rejected",
    "scan_pose_mismatch",
    "scan_observation_unreadable",
    "scan_world_drift",
    "scan_restore_rejected",
    "scan_restore_mismatch",
    "scan_time_scale_restore_rejected",
    "scan_time_scale_restore_unreadable",
    "snapshot_invariant_failed",
    "scan_evidence_failed",
    "scan_cleanup_failed",
    "setup_runtime_failed",
    "setup_unexpected",
]

SetupRecoveryStatus = Literal["not_applicable", "not_needed", "restored", "unverified", "failed"]
SetupCleanupStatus = Literal["not_applicable", "not_needed", "succeeded", "unverified", "failed"]
EnvironmentDisposition = Literal["ready", "not_started", "closed", "quarantined"]
AlfworldBackendKind = Literal["thor", "textworld"]

@dataclass(frozen=True)
class AlfworldResetResult:
    backend_kind: AlfworldBackendKind
    ready: bool
    state: AlfworldEnvState | None
    scene_generation: int | None
    goal_generation: int | None
    scene_reset_fingerprint: str | None
    goal_trial_fingerprint: str | None
    snapshot_sha256: str | None
    snapshot_ref: str | None
    setup_trigger: SetupFailureCode | None
    setup_failure: SetupFailureCode | None
    classification: EpisodeClassification | None
    score_eligible: bool
    setup_backend_action_count: int
    recovery_status: SetupRecoveryStatus
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None

GoalAdvanceFailureCode = Literal[
    "expected_goal_trial_mismatch",
    "goal_scene_mismatch",
    "goal_identity_unreadable",
    "goal_advance_rejected",
    "goal_state_unreadable",
    "goal_world_drift",
    "goal_cleanup_failed",
    "goal_runtime_failed",
    "goal_advance_unexpected",
]

@dataclass(frozen=True)
class AlfworldGoalAdvanceResult:
    backend_kind: AlfworldBackendKind
    ready: bool
    state: AlfworldEnvState | None
    scene_generation: int | None
    goal_generation: int | None
    scene_reset_fingerprint: str | None
    goal_trial_fingerprint: str | None
    snapshot_sha256: str | None
    before_scene_state_sha256: str | None
    after_scene_state_sha256: str | None
    advance_trigger: GoalAdvanceFailureCode | None
    advance_failure: GoalAdvanceFailureCode | None
    classification: EpisodeClassification | None
    score_eligible: bool
    benchmark_control_action_count: int
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None

@dataclass(frozen=True)
class AlfworldControlTerminalRecord:
    phase: Literal["reset_setup", "goal_advance"]
    trigger_code: SetupFailureCode | GoalAdvanceFailureCode
    final_code: SetupFailureCode | GoalAdvanceFailureCode
    classification: EpisodeClassification
    worker_process_return_code: int | None
    timed_out: bool
    recovery_status: SetupRecoveryStatus
    cleanup_status: SetupCleanupStatus
    quarantine_required: bool
    environment_disposition: EnvironmentDisposition
    evidence_ref: str | None
```

Reset 结果只有以下合法组合：

- THOR `ready=true`：`state`、两个 generation/fingerprint、snapshot hash/ref 均非空，`setup_trigger/setup_failure/classification=None`，`score_eligible=true`，`recovery_status=restored`，`cleanup_status=not_needed`，`quarantine_required=false`，`environment_disposition=ready`；此处 `restored` 同时表示初始 pose/world/frame 和正常 `timeScale=1.0` 都已恢复，hash/ref 指向已原子发布的同一个完整 snapshot；
- TextWorld `ready=true`：`state`、`goal_generation/goal_trial_fingerprint` 非空，scene/snapshot 字段为 `None`，`setup_trigger/setup_failure/classification=None`，`score_eligible=true`，setup count=0，recovery/cleanup 均为 `not_applicable`，`quarantine_required=false`，`environment_disposition=ready`；
- `ready=false`：`state=None`，`setup_trigger/setup_failure/classification` 均非空，`score_eligible=false`，snapshot 未发布，environment 只能是 `not_started/closed/quarantined`。`setup_trigger` 保留最早失败，`setup_failure` 是按 §7.6 优先级归一化后的最终原因；fingerprint 只允许在外部 reset 或 identity capture 尚未可靠完成时为 `None`。

Terminal disposition 组合固定为：`not_started` 只能配 recovery/cleanup=`not_needed`；reset 后的确定性失败只有 `recovery=restored + cleanup=succeeded + closed + quarantine_required=false` 才能保持 Harness/artifact/internal 分类；任何 state/identity/restore uncertainty 都设 `quarantine_required=true`，cleanup成功可最终 `closed`，cleanup unverified/failed 则必须 `quarantined` 并升级 Runtime。其他组合 constructor 直接拒绝。Runner 在 worker/cleanup 边界补齐 `AlfworldControlTerminalRecord`，因此进程返回码、timeout 和 trigger/final code 不依赖 Adapter 提前猜测；普通 in-process 终态的 return code 可为 `None`，Gate worker 必填。

普通 Runner 与 taskset root 必须在调用 Adapter reset 前创建内部 Trace/Outcome builder；`ready=true` 后才允许写入模型可见 `episode_started/subtask_started`、构造 `GenericAgentRuntime`/Provider client、组装首个 prompt 和调用 transport。setup terminal 时 Runner 使用 `tool_call_id=None` 写完整 Episode/Taskset 终态，模型请求、Provider factory/send/API、Agent tool call、env step 和 invalid action 全为 0。TextWorld 返回 `ready=true`、setup count=0、THOR setup 字段 `not_applicable`。

reset transaction 固定为：

```text
external reset
-> capture initial pose/event/frame/scene-reset/goal-trial/world digest
-> freeze scan policy/config and exact slow=0.01/restore=1.0 sequence hash
-> execute and verify ChangeTimeScale(timeScale=0.01)
-> execute and verify exactly one GetReachablePositions
-> freeze complete Teleport SceneScanPlan and hash
-> execute every scan Teleport through OracleActionGateway
-> stage per-exact-ID entries
-> one restore TeleportFull
-> verify initial pose/world restoration while time remains slow
-> execute and verify ChangeTimeScale(timeScale=1.0)
-> verify the normal-time return event matches initial pose/world/visibility/bbox/frame
-> atomically publish OraclePoseSnapshot
-> return ready AlfworldResetResult
```

任何中途失败都先停止新 scan request；只要 slow-time 请求已经发送，失败收敛就必须在 `finally` 语义下先尽力用锁定的初始 pose 恢复相机，再尽力发送 `ChangeTimeScale(1.0)`，即使 pose restore 已失败也不得跳过 time-scale restore。两类恢复的实际请求/返回都进入 append-only setup ledger 并计数；不得发布 partial snapshot。只有 pose/world/frame 与 normal time 均证明恢复才可 `recovery_status=restored`；任一返回身份/pose/world/time-scale restore 不确定，最终 classification 必须升级为 `execution_state_uncertain`，覆盖此前可确定的 Harness/artifact failure；env 先 quarantine，再尝试关闭，且绝不复用。worker crash、hard timeout 或 cleanup 未证明则按 §7.6 升级为 `runtime_failure`。`setup_backend_action_count` 计算实际发送的 approved time-control/query/scan/pose-restore/time-restore 请求；成功值严格为 `1 slow + 1 query + N(send_teleport=true) + 1 pose restore + 1 normal-time restore = N + 4`，initial observation 为 0，external reset lifecycle call 另记 trace 而不混入该计数。

成功路径上，pose-restore Teleport 返回 event 只是 slow-time 中的恢复证据，不得直接发布给模型。随后成功的 `ChangeTimeScale(1.0)` 返回 event 必须再次通过准确初始 pose、完整 world digest、visibility/bbox map 和 frame 像素相等门；它是 `restored_event_ref`、`AlfworldEnvState`、模型 phase `event_sequence=0` 和 `frame-0000` 的唯一权威源。

普通 multi-Episode Runner 对 fixed manifest 每个 entry 构造一个 fresh pinned Adapter；setup terminal 关闭/quarantine 当前 Adapter 后，下一 Episode 只能使用新实例，绝不对已关闭 env 再 reset。Taskset 只在首次 reset ready 后共享同一 Adapter/snapshot；任何 setup、goal-control 或 subtask terminal 都结束该 taskset并按 §10.5 标记剩余项。

Taskset 后续 `advance_goal()` 不 reset、不 query、不 scan，setup count 固定为 0，并只返回 `AlfworldGoalAdvanceResult`。调用外部 `set_task` 前先验证 expected/root-relative trial identity 和 logical scene，并捕获 current agent pose、scene-only exact existence/transform/containment/physical state、inventory 的 before digest；调用后只允许 goal/task fields 改变，独立重算的 after digest 必须逐字段相同。成功分支必须 state、scene/goal identity、snapshot hash、before/after digest 非空且相等，`advance_trigger/advance_failure/classification=None`，goal generation 恰好 +1、scene generation/snapshot hash 不变、control count=1、cleanup=`not_needed`、env=`ready`，并 invalid 当前 execution context。TextWorld 使用同一 tagged type但 THOR scene/snapshot digest 为 not-applicable。

`expected_goal_trial_mismatch/goal_scene_mismatch/goal_identity_unreadable` 都在调用前归 `artifact_failure`、control count=0；调用后的 `goal_advance_rejected/goal_state_unreadable/goal_world_drift` 归 `execution_state_uncertain`，按实际调用记 control count=1、`quarantine_required=true`；`goal_advance_unexpected` 归 `unclassified_execution_failure`；`goal_cleanup_failed/goal_runtime_failed` 和未闭合异常归 `runtime_failure`。terminal 分支必须 `state=None`、trigger/failure/classification 非空、score-ineligible，cleanup/disposition服从上面的 closed/quarantined 组合；trigger保留首因，failure保存按 `runtime > uncertainty > internal > artifact` 归一化的最终 code。失败发生在该 subtask 第一次 Provider factory/request 前，后续 subtasks按 §10.5 标为 `not_run`，不得把失败伪装成一次新的 setup scan。Reset/advance 的所有真实、Fake/Mock 构造点、合法/非法组合都进入 tagged-result exhaustive audit。

Goal advance 只会发生在已 ready 的 taskset env 上，因此 terminal disposition 绝不允许 `not_started`：preflight artifact failure 也必须 cleanup成功后 `closed`；cleanup unverified/failed 则 final code=`goal_cleanup_failed`、classification=`runtime_failure`、disposition=`quarantined`。THOR success 必须 `cleanup_status=not_needed/quarantine_required=false/ready`；TextWorld 对应字段为 `not_applicable/false/ready`。

Identity 字节组成固定分离：`scene_reset_fingerprint` 覆盖 logical/runtime asset、pinned runtime/Unity build 与 reset 后 scene-only exact existence/transform/containment/physical-state digest；排除 goal/task counters、trial ID/path、root/host 和 generation。`goal_trial_fingerprint` 覆盖 root-relative trial ID、trial bytes hash 和 goal identity；排除绝对 root/host。scan policy/cache/config/reachable/plan/snapshot hash 各自单列，不塞进任一 fingerprint。两个 monotonic generation 单独参与 context/store freshness。

### 6.1 `SceneScanPlan`、`OraclePoseSnapshot` 与 `OraclePoseStore`

新增窄接口，隐藏 ALFWorld 内部对象结构，并保留构建/读取状态：

```python
OracleReadStatus = Literal[
    "ok", "unobserved", "coverage_miss", "relocated", "absent", "malformed", "stale", "error"
]
ScanPoseSource = Literal["cache", "geometry"]
SnapshotPoseSource = Literal["cache", "geometry", "incidental", "none"]
AddressabilityReason = Literal[
    "public_semantic", "not_public_semantic", "inventory", "closed_ancestor"
]

@dataclass(frozen=True)
class ScanPoseProvenance:
    exact_object_id: str
    source_kind: ScanPoseSource
    source_record_sha256: str

@dataclass(frozen=True)
class ScanPoseStep:
    index: int
    pose: OraclePose
    send_teleport: bool
    provenances: tuple[ScanPoseProvenance, ...]

@dataclass(frozen=True)
class SceneScanPlan:
    scene_generation: int
    scene_reset_fingerprint: str
    algorithm_version: str
    scan_policy_sha256: str
    reachable_payload_sha256: str
    reachable_canonical_sha256: str
    steps: tuple[ScanPoseStep, ...]
    canonical_sha256: str

@dataclass(frozen=True)
class OraclePoseSnapshotEntry:
    exact_object_id: str
    status: OracleReadStatus
    addressable: bool
    addressability_reason: AddressabilityReason
    pose: OraclePose | None
    pose_sha256: str | None
    pose_freshness_sha256: str
    source_kind: SnapshotPoseSource
    evidence_ref: str | None

@dataclass(frozen=True)
class OraclePoseSnapshot:
    scene_generation: int
    scene_reset_fingerprint: str
    algorithm_version: str
    scan_policy_sha256: str
    reachable_payload_sha256: str
    reachable_canonical_sha256: str
    scan_plan_sha256: str
    initial_event_ref: str
    restored_event_ref: str
    initial_world_sha256: str
    restored_world_sha256: str
    entries: tuple[OraclePoseSnapshotEntry, ...]
    snapshot_sha256: str

@dataclass(frozen=True)
class OraclePoseLookup:
    status: OracleReadStatus
    scene_generation: int
    scene_reset_fingerprint: str
    snapshot_sha256: str
    pose: OraclePose | None
    pose_sha256: str | None
    pose_freshness_sha256: str | None
    source_kind: SnapshotPoseSource
    evidence_ref: str | None

class OraclePoseStore(Protocol):
    def get_pose(
        self,
        *,
        scene_generation: int,
        scene_reset_fingerprint: str,
        exact_anchor_id: str,
    ) -> OraclePoseLookup: ...
```

`addressable` 是 reset 时一次锁定、与 task/target/action 无关的结构谓词：对象类型属于 committed public semantic vocabulary、不在 inventory/`isPickedUp`，且经完整 reciprocal containment chain 证明没有 closed openable ancestor。receptacle/fixture 与位于 surface、open container 或 floor 的 movable 可以 addressable；初始 strict visibility 不是生成 geometry 的条件。closed-container descendant、held object 和非公开语义类型不生成 geometry，row 固定为 `unobserved`，即使某个 scan frame 偶然给出 bbox 也不得升级、选 pose 或进入模型授权。containment/inventory 读取损坏使整个 setup malformed/uncertain，不能伪装成 non-addressable。单值 reason 的固定优先级为 `inventory > closed_ancestor > not_public_semantic > public_semantic`，同一输入不得因集合顺序改变 hash。

plan/snapshot 构建算法固定如下：

1. reset exact IDs、每个 ID 的 addressability/reason、完整 public vocabulary、scan algorithm/version/code hash、cache bytes/config、scene-reset identity、`slow_time_scale=0.01`、`restore_time_scale=1.0` 和固定 control 顺序先排序锁定并产生 `scan_policy_sha256`；绝对 path/host 不进入 canonical bytes；
2. 通过 gateway 发送且只发送一次 `{"action":"ChangeTimeScale","timeScale":0.01}`，要求 request/action identity、`lastActionSuccess=true`、可读返回和 agent pose/world digest 不变；该 event 不作为 scan observation；
3. 通过同一 gateway 发送且只发送一次 `GetReachablePositions`，要求返回成功、agent pose/world digest 不变；
4. cache entry 逐项验证 exact ID、单 `TeleportFull` shape 和有限数；malformed cache 使 transaction 失败，不允许 geometry 掩盖；
5. 对没有 exact cache pose 的 addressable exact ID，按当前对象 position、canonical reachable `(distance^2,x,z)` 顺序和 versioned yaw/horizon 量化规则至多生成一个 geometry pose；
6. 初始 pose 固定为 `steps[0]`、`send_teleport=false`，reset event 作为零动作 observation；cache/geometry pose canonical 去重、排序后形成其余 step。若某个输入 pose 与初始 pose 或另一输入相同，所有排序后的 `(exact_id, source_kind, source_record_sha256)` provenance 合并到同一 step，不能降成 incidental；
7. step index 必须从 0 连续；恰好 step 0 不发送且等于锁定初始 pose，所有后续 step 都发送；canonical pose 全集唯一，provenance tuple 排序去重，除纯 initial-incidental step 可为空外都非空。cache provenance hash 绑定输入 record canonical bytes；geometry provenance hash 绑定 exact ID、对象 position、被选 reachable 值和 yaw/horizon policy version，不绑定 path；
8. `reachable_payload_sha256` 只 hash 原始 `actionReturn/reachablePositions` payload bytes，供独立 parser 对照；完整 raw event 另存 evidence hash。`reachable_canonical_sha256` hash 通过有限数验证、negative-zero normalization、去重和排序后的 reachable set；plan identity只使用 canonical hash，原始 payload 顺序差异单独报告，不能使同一 canonical plan 漂移；
9. plan hash 覆盖 `scan_policy_sha256`、`reachable_canonical_sha256` 以及每个 step 的 index、完整 canonical `(x,y,z,rotation,horizon)`、`send_teleport` 和 provenance。第一次 `send_teleport=true` 后不得增删、重排、按 task/target 早停或扩大计划；
10. 每个 `send_teleport=true` step 的返回 event 必须单独通过 action identity、return success、requested/actual pose、完整容器可读和 world digest 不变门；step 0 直接读取锁定的 reset event，不伪造 Teleport return；
11. 同一 event 的准确 observation 只在 `metadata.visible=true` 且正规化四个有限 bbox 数得到正面积时合格；NumPy ndarray 与 list/tuple 必须按数值内容统一处理；
12. 对某 exact ID，若 observation 所在 step 带该 ID 的 cache provenance，则 source=`cache`；否则带该 ID 的 geometry provenance时为 `geometry`；否则为 `incidental`。全部 scan 完成后只对 addressable ID 按 `(source_rank, -bbox_area, x, y, z, rotation, horizon)` 唯一选 pose，rank 固定 cache、geometry、incidental；
13. clean full scan 中 addressable ID 从未 strict-visible 才是 `coverage_miss`；non-addressable reset ID 始终是 `unobserved`；`absent` 只用于 lookup 当前 exact ID 不在 reset snapshot（例如 runtime 若产生新的 sliced successor ID），三者不得互换；
14. 扫描完成后通过 gateway 恢复初始 pose，在 slow time 中通过 return/actual pose/完整 world 门；紧接着发送且只发送一次 `{"action":"ChangeTimeScale","timeScale":1.0}`，其返回 event 再次通过 action identity、`lastActionSuccess=true`、初始 pose/world/visibility/bbox/frame 门；
15. 两种 restore 全绿后才原子发布每个 reset exact ID 恰好一行的 immutable snapshot；`restored_event_ref/restored_world_sha256` 必须指向第 14 步的 normal-time return event，不是前一个 pose-restore event。动作阶段不保留 scan observation/candidate list，不重新计算，也不读取 cache 作为第二权威。

Published row 只允许三种组合：`ok => addressable=true + pose/pose_sha256 非空 + source_kind!=none`；`coverage_miss => addressable=true + pose/pose_sha256=None + source_kind=none`；`unobserved => addressable=false + pose/pose_sha256=None + source_kind=none`。`relocated/absent/malformed/stale/error` 是 immutable snapshot 之上的 current lookup overlay，不是初始 published row；verifier 对每个 ID 穷尽断言，非法组合使整个 snapshot invariant failure。

World digest 至少覆盖 logical/runtime scene identity、完整 exact-ID 集、每对象 position/rotation、reciprocal parent/child、open/toggle/picked/sliced/dirty/filled/cooked/raw THOR `ObjectTemperature`/broken/used 状态、inventory IDs、ALFWorld clean/heat/cool sets、goal condition 和 task counters。它排除 agent-relative visible/distance/bbox、lastAction/actionReturn/currentTime；这些观察字段在最终 normal-time return event 中另与初始 map/frame 像素逐项比较。引擎 time scale 不是可被丢进 world hash 的可信 object field，而由固定 control request/return/effect evidence 独立验证。数值容差必须 versioned、记录真实 delta，不能只比较自报 hash，也不得为让 scan 通过而删减温度字段。

`pose_freshness_sha256` 只绑定 exact existence、transform 和 containment，不包含 `isOpen/isToggled/dirty/temperature` 等不改变导航 pose 的动作状态。Open/Close/Use/Clean 等成功状态变化只 rebase context，不使 pose entry stale。Take/Put 或其他 gateway 已证明的预期 transform/containment 变化不修改 immutable row，而让 current lookup overlay 返回 `relocated`；当前 target 再次 strict-visible 时可按 6.3 解析当前唯一 parent。未知来源的 transform/containment drift、generation/binding mismatch 才让 overlay 返回 `stale` 并 terminal uncertain。

lookup 约束：

- 只按锁定的准确 anchor ID 查询 snapshot，不接受语义类型；
- 同一 scene generation、scene-reset fingerprint、snapshot hash、exact ID 必须以一次 atomic store read 返回确定性相同的 status/pose/source/freshness；executor 不得从另一 side channel 拼 context hash；
- `status=ok` 时必须恰有一个 pose，所有位置/旋转/俯仰值均为有限数；
- unobserved/coverage miss、已知 relocated、post-reset successor ID absent、shape 损坏、未知 pose freshness drift、跨 reset stale 和访问异常不得合并为 `None`；Slice 是否保留 exact ID 必须先由 Gate A 取证，不能由该状态名预设；
- movable direct entry 还必须匹配当前 existence/transform/containment fingerprint；`relocated` lookup 不得继续使用旧 direct pose，只能在当前可见门后重新解析唯一 parent；
- 不替模型选实例；
- 不读取专家 trajectory；
- 返回值只供 Adapter 内部使用；
- reset 后废弃上一场景 snapshot；`advance_goal` 只更新 context/trace 的 `goal_trial_fingerprint/goal_generation`，snapshot 只绑定稳定的 `scene_reset_fingerprint/scene_generation`。gateway 已证明的移动为 `relocated`，只有未知 drift 才为 `stale`。

`ScenePoseSnapshotProvider`、`OraclePoseStore` 或 gateway backend 若存在多个实现，接口审计测试必须枚举所有真实/测试实现并断言公开方法完整。未来 remote/twin/cache provider 只有在真实实现并通过等价 Gate 后才可加入，当前不提供 fallback。

### 6.2 `VisibleObjectView`

reset 时建立的 `SceneObjectIndex` 只提供内部稳定 label/objectId 映射和 frozen full-set ordinal；reset addressability 只服务于 scan pose 生成，不能授权任何 `robot_go_to`。所有 receptacle、fixture、toggle、movable 和 appliance 共用同一个动作前门：准确 requested target 必须在**当前 event** strict-visible。模型永远看不到 exact ID、hidden parent 或 pose。

读取结果同样是强类型：

```python
ObservationReadStatus = Literal["ok", "absent", "malformed", "stale", "error"]

@dataclass(frozen=True)
class ObjectObservationRead:
    status: ObservationReadStatus
    event_sequence: int
    exact_object_id: str | None
    event_frame_sha256: str | None
    model_view_frame_sha256: str | None
    frame_matches_model_view: bool | None
    visible: bool | None
    bbox_area: float | None
    strict_visible: bool | None
```

`status=ok` 要求两个 frame hash非空、`frame_matches_model_view=true`，且 visible/bbox/strict_visible组合合法；hash缺失、模型图像不可回读或像素不等不能返回 `ok`。

`VisibleObjectView` 负责：

- 读取当前 event 中准确对象的 `metadata.visible`；
- 读取同一个 event 的准确 bbox；
- 断言该 event 的 RGB 像素/hash 与本次 tool call 对应的最新模型可见图片完全一致；存在未交付给模型的中间外部 event、frame 缺失或像素不一致时返回 `stale/error`，不得授权；
- 只列出当前 event 同时满足 `visible=true` 和正面积 bbox 的 exact object；
- 对同一个 event 和输入返回确定性顺序；
- exact object 没有 detection entry 是合法观察：`status=ok, bbox_area=None, strict_visible=false`；
- metadata/detection 容器损坏、bbox shape 非法、stale 或读取异常不能变成“普通不可见”；只有 `status=ok` 且没有 strict-visible requested target 时，才可安全返回可恢复的 `target_not_visible`。

导航 target resolution 先用 committed public semantic vocabulary 校验类型，再把 frozen canonical exact-ID 顺序与当前 `VisibleObjectView` 组合：

- 无 ordinal 的 `mug` 只从当前 strict-visible matching IDs 中按 frozen canonical full-set 顺序选择第一个；本次调用一旦锁定，不因 lookup 或移动失败换另一个 Mug；
- 显式 `mug N` 永远对应 frozen 完整 matching exact-ID 排序中的稳定 ordinal。ordinal 不存在或该 exact ID 当前非 strict-visible时，都返回同一个 `target_not_visible`；不得 fallback 到其他 ordinal或当前可见 peer；
- 当前没有 strict-visible candidate、但 matching object 已在 inventory 时返回 `object_already_held`；否则返回 `target_not_visible`；
- 非公开 semantic type 或非法 label 语法才返回 `target_not_found`；
- exact ID、full/visible matching count、snapshot row、parent membership 和 ordinal 选择理由只进内部 trace，不进 prompt/tool payload；
- `target_not_visible` 分支不得调用 `OraclePoseStore.get_pose()`、解析 parent、生成 context 或发送 backend request。

因此 snapshot 中存在 `status=ok` 的 pose、reset scan 曾观察到 target、公开类型合法或 containment 无 closed ancestor，都不能替代 current strict visibility。所有物理目标当前不可见时统一降成 `target_not_visible`。

### 6.3 `NavigationAnchorResolver`

Pose availability 与 navigation authorization 分离。锁定 requested target 后按以下唯一规则解析 anchor：

1. target resolution 必须先按 6.2 从 current event 得到一个 strict-visible exact target；失败立即返回零动作结果，resolver 不得读取 snapshot/parent；
2. 锁定后必须从同一 current event 强类型读取 inventory、containment 和必要 observation；任一必要读取 malformed/stale/error 都是 `execution_state_uncertain`，不能伪装成不可见；
3. inventory/`isPickedUp` target 返回 non-terminal `object_already_held`，零移动；
4. target 自身 `OraclePoseLookup(status=ok)` 且 fingerprint current 时，anchor 就是 exact target；
5. direct lookup=`coverage_miss` 时按 7.5 terminal `oracle_pose_missing`，不得通过 parent 或其他同类型实例掩盖 setup coverage failure；malformed/stale/error 同样直接按 7.5 terminal；
6. direct row=`unobserved/relocated/absent` 时才允许为**同一个已 strict-visible target**解析 parent anchor；containment 的 open/closed 状态不参与导航授权，但继续进入后续操作 precondition；
7. parent 必须同时满足当前 target parent membership、reciprocal child membership、snapshot lookup成功和稳定 state fingerprint；只接受唯一最内层 parent，不得按集合顺序、距离、outer ancestor 或 `.first` 猜测；
8. 所有读取均为 ok 时，零个或多个最内层候选返回 terminal `oracle_anchor_unresolved`；该结果零移动且不 fallback。

Parent resolution 只服务于已经通过 current strict-visible 门的 `unobserved/relocated/absent` target，不得用于定位任何当前不可见对象，也不是 direct pose 执行失败后的 retry。一旦 exact target 或 parent anchor 锁定，其 pose 是本次唯一 pose；移动被拒、实际 pose mismatch 或移动后准确 child 不可见都按 7.5 terminal，不得尝试 direct、第二 parent、outer ancestor 或新 geometry。anchor 只进入内部 context/trace，模型投影不返回 parent label、objectId 或选择理由。

### 6.4 `OracleNavigationExecutor`

职责：

1. 从 current event 读取并锁定 strict-visible requested target；
2. 只有第 1 步成功后才锁定 navigation anchor；
3. 从 published snapshot 读取一个 pose；
4. 最多发送一次移动请求；
5. 核对返回码、actual pose、准确 requested target 最终可见门；
6. 创建单一 `OracleExecutionContext`。

它不生成、排序或重试候选；setup plan builder 与正式 executor 是不同生命周期和接口，产品 call graph guard 必须证明 action-time executor 不可达 scan builder。

### 6.5 `OracleExecutionContext`

替代带候选列表的 `PoseContext`：

```text
scene_generation
goal_generation
source_event_sequence
current_event_sequence
  requested_target_id
  navigation_anchor_id
  oracle_snapshot_hash
  oracle_pose_hash
  anchor_state_hash
actual_pose
final_event_hash/ref
state = active | consumed | invalid
```

不包含 scan observations、候选列表、候选排名或动态预算。状态机固定如下：

| 事件 | context 转移 |
|---|---|
| 成功 Oracle 导航 | 创建 `active`，锁定 target/anchor/pose/event |
| `target_closed`、`navigation_required` 等零动作结果 | pose/event 未变，原 context 保持；失败请求不得重算目标 |
| 同 target、同 pose 的幂等 Open/Close | 核对当前读取后保持 `active` |
| 同 target、同 pose 的成功 Open/Close/Use | 返回码和终态均通过后，把 successor event sequence/hash 原子 rebase 到同一 `active` context |
| 成功 Slice 且 Gate A 证明 exact ID 保留 | 双门通过后 rebase 同一 `active` context |
| 成功 Slice 且 Gate A 证明产生 successor ID | 原 exact context `consumed`；不得自动锁定或 rebase 到 successor |
| 成功 Take | 精确 movable context 变为 `consumed` |
| 成功 Put 或完成 Heat/Cool/Clean macro | context 变为 `consumed` |
| 成功 `advance_goal()` | scene snapshot 保留，旧 goal-bound context 变为 `invalid` |
| reset、成功新导航、任何 pose 改变、无关外部动作、event gap、返回/终态矛盾 | 变为 `invalid` |

因此 `go_to(drawer) -> Put(target_closed) -> Open -> Put` 使用同一锁定 pose：第一次 Put 不改 context，Open 成功后只 rebase event，最终 Put 消费 context。所有外部动作必须通过统一 `OracleActionGateway` 更新 event sequence；helper 不得绕过 gateway 直接调用 THOR。

`SliceObject` 是否保留 exact ID 或替换为 successor IDs 是 Gate A linchpin，在当前 runtime 实证前保持 `UNVERIFIED`，设计和评审都不得预先承诺 rebase。Gate A 为锁定的 current runtime/build 选出**唯一**实现分支并写入后续 implementation plan；产品不长期保留动态双 mode。若证明 ID 保留，采用上表 rebase；若证明替换，旧 context 消费/失效，新 successor 不由 Harness 自动替模型选择。只有模型之后通过当前画面解析出的 strict-visible successor 才可按普通 `absent + unique parent` 规则导航。runtime/build 变化后该分支重新 UNVERIFIED，启动前阻断并重跑 Gate A。

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

所有 `AlfworldStepResult` 生产构造点和 Fake/test double 必须显式提供 domain-specific typed feedback，不允许 default factory。构造点 audit 是接口一致性门的一部分。

## 七、导航语义

### 7.1 唯一公开导航入口

正式 registry 只向 Dispatcher 注册 `robot_go_to`。`robot_find_object` 和 `robot_navigate` 的 ToolSpec、executor、Adapter 方法与 legacy helper 从生产路径删除；即使直接向 Dispatcher 提交这两个旧名称，也只能得到 `unknown_tool`，不得触发环境调用。

`robot_go_to` 在 `AlfredThorEnv` 只能进入 `OracleNavigationExecutor`；不得调用 textual `env.step("go to ...")`、`virtual_navigate()`、admissible-command 遍历、`SceneScanPlanBuilder` 或候选生成。`AlfredTWEnv` 可在自身非 THOR 路径翻译同一个公开工具名，但不共享 V1.8 snapshot 正确性声明。

### 7.2 Receptacle / fixture 目标

示例：

```text
robot_go_to("drawer 2")
```

流程：

1. 只从当前 event 的 `VisibleObjectView` 确定性解析准确 Drawer ID，并要求 `visible=true` 与正面积 bbox；
2. 当前不可见立即返回 `target_not_visible`，不得查询 snapshot；
3. 可见后按该 ID 查询当前 published snapshot，并要求 lookup `status=ok`；
4. 单次移动；
5. 校验 THOR return success；
6. 校验 requested pose 与 actual pose；
7. 校验准确 Drawer 在同一最终 event 中仍 `visible=true` 且 bbox 面积大于零；
8. 保存最终 event 图片并创建 context。

成功必须指向请求的准确实例。其他同类型实例可见不能代替。

Receptacle、fixture、toggle、movable 和 appliance 使用同一 current-visible authorization：当前画面不可见一律阻断导航，即使 snapshot 已有 `ok` direct row。DeskLamp/FloorLamp 等 parentless fixture 在变为 current strict-visible 后，才可使用 geometry-backed sole pose；可见但 required direct pose 缺失时 terminal `oracle_pose_missing`，不尝试第二个同类型实例或 action-time geometry。

### 7.3 Movable object 目标

示例：

```text
robot_go_to("mug")
```

预检按 6.2 的 frozen `SceneObjectIndex` 顺序与 current `VisibleObjectView` 解析 movable object：

- 无 ordinal `mug` 只选择当前 strict-visible matching exact IDs 中 canonical 顺序的第一项；
- 显式 `mug 2` 锁定 frozen 完整 matching 集合中的稳定 ordinal；ordinal 不存在或该 exact ID 当前不可见时都返回 `target_not_visible`，不得 fallback 到 mug 1或其他可见 Mug；
- 当前没有可见 match、但 matching object 已在 inventory 时返回 `object_already_held`；否则返回 `target_not_visible`；
- 锁定后不得因 lookup/移动失败切换实例；
- current-visible target direct lookup=`ok` 时使用 sole snapshot pose；`coverage_miss` 是 terminal Harness coverage failure；`unobserved/relocated/absent` 只走同一 visible target 的 unique-parent 分支。

只有 requested target 已在当前 event strict-visible 时，才允许按 6.3 解析唯一 reciprocal parent anchor。该 parent 信息只参与本次内部执行，不用于定位当前不可见对象，也不返回给模型。

移动到 locked direct/parent pose 后，最终成功仍必须断言准确 movable 本身 `visible=true` 且 bbox 面积大于零。只看到 parent 不算成功；失败直接是 Harness navigation terminal，不换另一个 pose。

### 7.4 当前不可见目标边界

无论 Mug 位于关闭 Drawer、画面外的桌面或地面，只要当前不可见：

```text
robot_go_to("mug")
-> {"success": false, "error": "target_not_visible", "target": "mug"}
```

必须满足：

- 不移动；
- 不查询 target/parent snapshot pose、不导航 parent、不自动 Open；
- 不读取 containment 来覆盖可见性结论，不把 hidden exact ID、parent、pose、snapshot row 或 ordinal 选择理由返回模型；
- 不增加 Harness failure；
- 不增加环境 invalid action；
- 不创建、替换或清空已有 context；
- 模型继续利用当前可见目标和未来记忆/探索能力改变视野；
- 模型重复错误调用会由现有 `max_consecutive_tool_errors`（默认 5）先行归为 Agent failure；穿插探索但持续无进展时由 `max_no_progress_iterations`（默认 20）或 ALFWorld `max_tool_iterations`（当前 1000）归为 Agent failure。三个值都来自运行配置，不由 Harness 为 hidden target 另设终止分支。

目标通过任何模型探索、显式 Open 或未来记忆模块重新进入**当前 event**并变为 strict-visible 后，下一次 `robot_go_to` 才可读取 snapshot/parent pose；若仍不可见，继续返回同一个零动作 `target_not_visible`。Harness 永远不提示搜索路线或正确容器。

未来记忆模块负责学习搜索顺序，不属于 Harness。

### 7.5 Lookup 与移动结果穷尽门

lookup 结果映射：

| lookup status | ToolExecutionError | EpisodeClassification | model backend action |
|---|---|---|---:|
| current view 无 strict-visible generic match，或显式 ordinal 不存在/对应 exact ID 当前不可见 | `target_not_visible` | `None` | 0 |
| visible target `coverage_miss` | `oracle_pose_missing` | `harness_navigation_failure` | 0 |
| `unobserved/relocated/absent` 且 target 当前 strict-visible、无唯一 parent | `oracle_anchor_unresolved` | `harness_navigation_failure` | 0 |
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

`target_not_visible` 只表示**移动前的 current event**没有可授权准确实例：generic public label 无 strict-visible match，或显式 ordinal 不存在/对应 exact ID 当前不可见。它必须是零动作、非 terminal，不暴露对象是否存在于屏外、exact ID、parent、pose 或 snapshot status。所有屏外物理目标都返回该错误。移动后 exact target 不可见是 Oracle contract failure，不能复用该错误让模型继续，也不能落入 Agent failure。

准确 target 或 anchor 无 Oracle pose 时的模型安全结果示意：

```text
error = oracle_pose_missing
classification = harness_navigation_failure
terminal = true
score_eligible = false
```

不得 fallback 到专家 trajectory、同类型其他实例、第二个 direct/parent pose、outer ancestor 或候选搜索。

### 7.6 Setup scan/restore 穷尽门

Setup 发生在模型和 tool call 之前，不得伪造 `ToolExecutionError`。`AlfworldResetResult.setup_failure` 使用以下闭合映射；表中 0 指 Provider/tool/model env step，setup backend 仍按实际发送数单独记录：

| SetupFailureCode / 外部事实 | EpisodeClassification | provider/tool/model env step |
|---|---|---:|
| `expected_manifest_mismatch` | `artifact_failure` | 0 |
| `external_reset_failed/setup_runtime_failed`，Unity/Xvfb/worker crash 或 hard timeout | `runtime_failure` | 0 |
| `initial_state_unreadable/reset_identity_unreadable/runtime_scene_mismatch/addressability_unreadable`（reset 已发生） | `execution_state_uncertain` | 0 |
| `cache_input_malformed` 且外部状态与 restore 均已证明 | `artifact_failure` | 0 |
| `scan_time_scale_enter_rejected/reachable_query_rejected/scan_pose_rejected`，且完整 world、pose 与 normal time 均已证明恢复 | `harness_navigation_failure` | 0 |
| `scan_time_scale_enter_unreadable/reachable_query_unreadable/scan_pose_mismatch/scan_observation_unreadable/scan_world_drift/scan_restore_rejected/scan_restore_mismatch/scan_time_scale_restore_rejected/scan_time_scale_restore_unreadable` | `execution_state_uncertain` | 0 |
| `scan_plan_missing/scan_plan_malformed/snapshot_invariant_failed/setup_unexpected` | `unclassified_execution_failure` | 0 |
| `scan_evidence_failed` 且外部状态与 restore 均已证明 | `artifact_failure` | 0 |
| `scan_cleanup_failed` 或 cleanup 状态未证明 | `runtime_failure` | 0 |

Logical `FloorPlanN` 与 expected trial/manifest 不一致是输入 artifact failure；runtime asset 不是锁定版本映射出的精确 `FloorPlanN_physics` 是外部 state uncertainty。不得通过任意 suffix stripping 把后者降成成功。

分类按 `cleanup/runtime > state/identity/pose-or-time-restore uncertainty > internal invariant/unexpected > artifact > deterministic Harness` 的终态优先级归一化。也就是说，后续不确定 world/pose/time-scale restore 事实必须覆盖先前 `scan_pose_rejected` 等 Harness 结论；cleanup 不完整再升级为 Runtime。`setup_trigger` 保留首因，`setup_failure` 保存该优先级选出的最终 code，不存在 Literal 之外的“unknown code”。任何 setup terminal 都记录实际 `setup_backend_action_count`，Runner 再用 `AlfworldControlTerminalRecord` 补齐进程返回码、timeout、recovery/cleanup/environment disposition，并关闭或 quarantine env。只有 clean time bracket + scan + restore 后某 addressable exact ID 从未 strict-visible 才能标为 per-ID `coverage_miss`；rejected/malformed/uncertain setup 不能伪装成 coverage miss。

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

所有 THOR 请求，包括 setup time-control/query/scan/restore、navigation 和 macro 子动作，都只能通过一个 gateway：

1. 请求前锁定 action、exact object/target、context 和 before snapshot；
2. 发送一次请求并把 backend action、event sequence、耗时和 raw event ref 写入 JSONL；
3. 读取 after snapshot；
4. 同时判断外部返回码和 action-specific 终态；
5. 只有二者一致成功才允许 context rebase/consume；
6. 返回失败且完整动作状态不变为 `harness_operation_failure`；
7. 部分变化、读取缺失或返回/终态矛盾为 `execution_state_uncertain`；
8. setup 请求按冻结 plan 顺序执行；action-time 请求不在 gateway 内搜索 pose、换 target 或重试动作。

Scanner 与导航都必须调用 gateway 的封闭 phase-specific 入口；`SceneScanner/OracleNavigationExecutor` 不得直接调用 gateway backend。只有 gateway 可以调用 backend 的 THOR 发送方法，guard 同时审计 AST call site 和运行时调用链。Setup scan 即使内部有多条请求也不创建模型 execution context。

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

Use 和 Slice 都要求 exact requested target context，只发送一次对应外部动作。Use 同时核对准确 toggle 状态改变；Slice 同时核对准确对象的 benchmark slice 终态，并先按 §6.5 的 Gate A identity contract 判断 exact ID 是保留还是被 successor 替换。实际外部 action/field/identity 符号在 Gate A 前保持 UNVERIFIED。

失败映射与 8.2 相同。不得把同类型其他灯、开关、刀具或对象的状态变化当成功；ID 替换时不得把任一 successor 自动写成原 context target。

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
| `slice` | requested object | 1 | exact slice transition + verified ID preserve/replace contract | rebase 或 consume，按 Gate A 锁定契约 |
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

`setup_backend_action_count`、`benchmark_control_action_count`、`agent_tool_call_count`、`backend_action_count`、`env_step_count` 和 `invalid_action_count` 是不同计数器。保持既有语义：`total_backend_action_count = setup_backend_action_count + backend_action_count`；另定义不漏 `set_task` 的 `total_external_action_count = setup_backend_action_count + benchmark_control_action_count + backend_action_count`：

| 结果 | setup backend | benchmark control | agent tool | model backend | env step | invalid action | context |
|---|---:|---:|---:|---:|---:|---:|---|
| THOR reset time-control/query/scan Teleport/pose+time restore | 成功=`1 + 1 + N(send=true) + 1 + 1 = N + 4`；失败=实际发送数，含 recovery attempts | 0 | 0 | 0 | 0 | 0 | initial observation 0 action；normal-time final event 全绿后发布 snapshot |
| TextWorld reset | 0 | 0 | 0 | 0 | 0 | 0 | not applicable |
| taskset `advance_goal()` preflight mismatch | 0 | 0 | 0 | 0 | 0 | 0 | root control terminal，env close |
| taskset `advance_goal()` 实际 `set_task` | 0 | 1 | 0 | 0 | 0 | 0 | success invalid context；failure close/quarantine |
| 零动作 precondition/不可见 | 0 | 0 | +1 | 0 | 0 | 0 | 保留原 context |
| snapshot lookup/anchor terminal | 0 | 0 | +1 | 0 | 0 | 0 | Episode terminal |
| 单次移动或操作 | 0 | 0 | +1 | 1 | 1 | 0 | 按 6.5 转移 |
| Heat/Cool/Clean macro | 0 | 0 | +1 | 实际 N | 实际 N | 0 | 每步 rebase，最终 consume |
| Provider retry attempt | 0 | 0 | 0 | 0 | 0 | 0 | 不触碰环境/context |

metadata、inventory、pose 和 snapshot 的当前 event 读取不算 backend action。Setup/control 绝不进入 Agent/tool/env/invalid 预算。普通 `EpisodeResult` 的 control 固定 0，并分别存 setup、model backend、`total_backend_action_count` 和 `total_external_action_count`。

Taskset 只在 root `TasksetResult` 记录首次 reset setup和 append-only control ledger；每个 `SubtaskResult` 只记录该 subtask 的 model-triggered backend requests，setup/control 固定为0，禁止塞进第一/current subtask或在 subtask sum 中重复。每次 `advance_goal()` 的 typed result 按实际调用给出 control 0/1，Runner只累加到 root `benchmark_control_action_count`；preflight mismatch为0。root model backend等于 executed subtask model backend之和，root backend total=setup+model，root external total=setup+control+model。summary、CLI 和 JSON schema都输出四者。

内部 global external sequence 包含 setup、control 和 model 请求，并为三 phase 保留独立 sequence；恢复正常 time scale 的最终成功 event 同时成为模型 phase `event_sequence=0` 和唯一 frame-0000 来源，pose-restore Teleport event 不得替代它。Harness 选出的 pose 导致外部拒绝是 Harness terminal，不增加 Agent invalid action。

### 10.3 Runtime/Provider 归一化

Runner 实际收到的是 `GenericAgentRuntime` 的规范 status、finish reason 和 error code，而不是异常类名。共享分类器的闭合输入为 `RuntimeTermination(status, finish_reason, error_code)`、环境 success 和已有 terminal classification；只看 error code 的函数无法区分正常未完成与无 code Runtime 异常。封闭映射如下：

| runtime error code | EpisodeClassification |
|---|---|
| `transport_error` | `provider_failure` |
| `context_length_exceeded_after_compact` | `runtime_failure` |
| `tool_result_id_mismatch` | `runtime_failure` |
| `model_output_truncated` | `agent_model_failure` |
| `max_tool_iterations_exceeded` | `agent_model_failure` |
| `max_consecutive_tool_errors` | `agent_model_failure` |
| `max_no_progress_iterations` | `agent_model_failure` |
| `benchmark_env_step_limit` | `agent_model_failure` |
| `benchmark_invalid_action_limit` | `agent_model_failure` |
| `benchmark_done_without_won` | `agent_model_failure` |
| `user_interrupted` | `cancelled` |
| 已有 terminal Harness classification | 原样保留 |
| 未知 code | `unclassified_execution_failure` |

`provider_error` 是 provider 层诊断 subtype，`LLMProviderError` 是异常类名，都只进内部 trace，不作为 Runner error code 比较。Dispatcher executor 异常必须归一化为 terminal `runtime_failure`，不能吞成普通 ToolResult 后落入 Agent failure。普通 Runner、taskset Runner、Outcome、summary 和 CLI 共用同一映射函数。

无 code 且 `success=false` 只有在 `status=replied + finish_reason=normal_reply` 时是 `agent_model_failure`。不一致或未知的 status/reason/code 组合是 `unclassified_execution_failure`。

### 10.4 Provider retry 提交边界

安全重试只能在仍掌握流状态的 `GenericAgentRuntime`/transport 边界实现，Runner 不做盲重试。每次模型请求记录 `model_attempt_id`、输入消息 snapshot hash、assistant-response commit 状态和 tool-dispatch commit 状态。

Provider 层先把异常映射成闭合 internal `error_type + cause_code`。允许重试的集合只包括 transient network、rate limit，以及由真实历史 SSE shape 核对出的 `stream_protocol_error/message_delta_before_message_start`；generic `provider_error` 和未知 cause 不可重试。仅当**当前 attempt** 同时满足以下条件时可用同一消息 snapshot 重试一次：

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

`harness contract failures` 是 `harness_grounding_failure/harness_navigation_failure/harness_operation_failure`，以及 setup time/scan/restore、`advance_goal` control 或 robot action 产生的 `execution_state_uncertain`；`unclassified_execution_failure` 单独报告。artifact/runtime/provider 仍只降低各自 availability/evaluation coverage，不能混入 Harness 分子。Cancelled Episode 不冒充 Runtime failure，但会降低 evaluation valid coverage 并使正式分数不可用。

taskset 的 control terminal 与“未运行”使用独立闭合 schema：

```text
TasksetTerminalPhase = reset_setup | goal_advance | subtask_execution
SubtaskExecutionStatus = executed | not_run
NotRunReason = taskset_setup_failure | goal_advance_failure | prior_infrastructure_failure

TasksetResult.root_terminal = {
  phase, classification, subtask_index_or_none, control_terminal_record,
  setup/control/model/total counts
} | None
```

reset setup terminal 时，root 记 `phase=reset_setup` 和一次责任，全部 subtask 为 `not_run/taskset_setup_failure`；goal advance terminal 时，root 记 `phase=goal_advance` 和当前 index，当前 subtask 为 `not_run/goal_advance_failure`，后续为 `not_run/prior_infrastructure_failure`；已开始的 model subtask terminal 则为 `phase=subtask_execution`，当前 row 保持 executed/classification，后续 not-run。所有 not-run row 必须 `classification=None`、setup/control/model/env/tool/invalid count=0，只通过 `blocked_by_classification` 指向 root，责任和 coverage 分母在 taskset root 只记一次。Reset/goal terminal 均发生在 `subtask_started` 和 Provider factory 前。

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
  oracle_scan_plan_frozen
  oracle_scan_started
  oracle_scan_time_scale_enter_result
  oracle_scan_action_result
  oracle_scan_snapshot_staged
  oracle_scan_restore_result
  oracle_scan_time_scale_restore_result
  oracle_snapshot_published
  oracle_reset_terminal
  goal_advance_started
  goal_advance_result
  control_terminal
oracle_visibility_gate_started
oracle_visibility_gate_result
oracle_pose_lookup_started
oracle_pose_lookup_result
oracle_move_started
oracle_move_result
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

对一次导航调用，事件因果顺序必须是 `visibility_gate_started/result -> pose_lookup_started/result -> move_started/result -> context_created`。visibility failure 后不得出现本次 call 的 lookup/move/context 事件；JSONL sequence 与 runtime spy 双重断言该顺序，不能只检查最终 ToolResult。

内部事件保留：

- exact requested target ID；
- actual import origin/source hash、Unity build/asset identity、scan algorithm/version/code hash、cache hash、reachable payload/canonical hash；
- plan/snapshot/world hashes、setup sequence/count、slow/restore time-scale policy 和两个 control request/return/raw-event evidence；
- 每个 scan step 的 index/send/provenance、返回码、requested/actual pose、world delta 和 raw event/frame ref；
- exact navigation anchor ID；
- Oracle pose hash；
- requested/actual pose；
- 外部返回状态；
- authorization event/model-view sequence、frame hash/pixel equality和 target visible/bbox 门；
- 每个 read 的 `ok/absent/malformed/stale/error`；
- before/after snapshot hash 和 context state transition；
- raw event ref/hash；
- 单次耗时、setup/control/model/total backend、total external 与 env action count；
- model attempt ID、message snapshot hash 和 assistant/tool commit flags。

模型投影递归禁止：

```text
objectId
坐标和旋转/俯仰角
scan plan、scan observations、scan frame、time-scale control、snapshot/cache/reachable 原始记录
scene/goal trial fingerprint、plan/snapshot/world/source hash、setup/control count
setup-only hidden label/count/parent membership
Gate visibility fixture exact target/pose/expected state/raw evidence
全场景对象列表
专家 trajectory 字段
raw THOR error/stack
绝对路径
API token/认证 header
```

No-leak gate 检查整个 Provider-bound surface：system/user prompt、session history、ToolSpec/schema、tool call/result、图片内容和图片路径，而不是只 grep `model_trace.jsonl` 的 result block。模型 phase 的 `frame-0000` 必须逐像素来自成功 normal-time restore event，使用不编码 hidden/internal ID 的中性文件名；任何 scan frame 或 time-control detail 都不得进入 Provider session。Trace 必须记录 Runner 提供的当前 trial 稳定 fingerprint，解决现有 artifact 无法事后唯一恢复 trial 的问题；不得从专家 trajectory 推导 fingerprint。内部 trace 可以保存受控 raw external detail 和相对/opaque evidence ref，但模型 serializer 只允许固定字段与 allowlisted detail template。

## 十二、代码迁移边界

### 12.1 删除正式路径

实施完成后删除或隔离以下生产能力：

- `_teleport_candidates()` 和 `_single_target_teleport_candidates()`；
- `_navigation_budget_stop()`；
- navigation candidate/action/time 默认常量；
- Put local candidate 构建、排序和预算循环；
- action-time candidate hash/list 和模型阶段预算状态；
- 所有依赖上述预算的产品测试和文档说明；
- `robot_find_object` 的 ToolSpec、registry、executor、Adapter `find_object()`、`_search_visible_object_source()`、隐藏 parent/source resolver 和 admissible `go to` 循环；
- `robot_navigate` 的 ToolSpec、registry、executor、`virtual_navigate()` 以及 THOR 正式路径的 direct legacy `env.step()`；
- `_last_go_to_object_id` 和操作时按类型重新选择 exact instance 的 fallback；
- `PoseContext` 的 candidate list/hash、本地 Put retry executor、budget 类型/默认值/trace 字段和相关 Protocol；
- `_put_visible_base/_put_visible_payload/_go_to_visible_payload/_visual_error` 等 action-specific 模型投影和 dict fallback；
- Adapter 把 inventory/object state 塞入 `tool_args` 的结果通道；
- Dispatcher 对 typed result 再 `setdefault` 或覆盖 success/error 的路径；
- 所有绕过 `OracleActionGateway` 的直接 THOR action helper。

旧 V1.7 geometry/candidate 代码不得原样保留。只允许把“从一个 exact ID、当前 object position、已验证 reachable set 生成一个 canonical geometry pose”的纯函数迁入 `SceneScanPlanBuilder`；它无循环重试、无 task/action 输入、至多返回一个 pose，并且从 action-time call graph 不可达。

历史 evidence helper 可以保留在 ignored `var/` 中用于复盘，但不能被产品模块 import。

### 12.2 保留并复用

- `SceneObjectIndex` 的 canonical label 与 exact ID 锁定，但 hidden object 不进入 movable/toggle 可见解析；
- `GetReachablePositions` 的 strict parsing、requested/actual pose 比较和可达点数值 canonicalization，但只在 reset scanner 使用；
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
- 除本地 `SceneScanPlanSource` 实现外不得访问 `controller.receptacles/locs`；`OraclePoseStore` 只读 published snapshot；
- 除 `OracleActionGateway` 外不得发出 Gate A 列出的 THOR action；
- `SceneScanPlanBuilder` 不得接收 task text、requested target、action profile、expert field 或模型输入；
- `OracleNavigationExecutor` 的 AST call graph 与 runtime spy 必须证明 `VisibleObjectView` strict-visible success支配 exact lock、`OraclePoseStore`、parent resolver、context creation和 gateway send；任一 invisible/missing分支对这些下游调用计数全为零；
- action-time candidate/budget/projection symbol 在产品模块中搜索结果为零，唯一 geometry helper 只在 reset scan 模块可达。
- 产品配置/源码不得硬编码 `hkust4`、ALFWorld/evidence 绝对 root 或 `localhost`；root、backend endpoint 和 evidence store 只经注入接口取得，跨进程 evidence ref 为 relative/opaque。
- Runner 构造只接受 `TrialSelectionManifest`；产品 import/file-open/serializer 不得引用 `GateCaseManifest`、Gate helper 或 exact-case 文件，CLI 参数类型也必须不兼容。

同步普通 Runner、taskset Runner、两套 summary、CLI 消费者、trace renderer、所有 Fake Adapter/ExecutionBackend 和顶层 Put 假 fixture。接口 audit 必须枚举全部实现并断言公开方法和 typed return 完整，不能只测一个 mock。

通用 Dispatcher 不 import ALFWorld。Runner 通过 `RunContext` 注入 `ToolDispatchObserver`：每个输入 call 恰好一次计数，executed typed result 才增加 backend/env count，executor exception 由 observer 构造 terminal `runtime_failure`，第一条 terminal 提交获胜。validation、unknown、cancelled、terminal-blocked 和同批后续 call 的顺序/零 backend 语义都必须测试；observer 的真实实现和 Fake 同样进入接口审计。

部署契约测试至少用两个不同 root/host 配置实例化相同 Runner/Adapter/Fake backend，断言请求目标来自注入值、相同 relative IDs/content 产生逐字节相同 scene/goal/plan/snapshot portable identity、无 localhost 假设且 evidence 可在另一 root 回读。固定远端路径只属于 Gate 命令/内部证据，不进入产品默认值或 portable result。

## 十三、验证计划

### 13.1 Gate A：实施前 Oracle runtime feasibility

在改产品代码前，使用独立、不可 import HomeMaster 产品逻辑的 probe，对固定 exact trial 逐实例核对。Discovery 的**tested/model action 数为 0**，但必须真实执行 approved `ChangeTimeScale(0.01)`、setup query、冻结 scan Teleports、pose restore 和 `ChangeTimeScale(1.0)`；不能再把 `worker_external_action_count=0` 当通过条件。它把 scene-reset/goal-trial identity、time-scale/scan policy/plan、每个 setup raw request/event/frame、initial/slow-restored/normal-restored world evidence、完整 per-ID snapshot、exact requested/anchor/object/target ID、current-visibility precondition、action profile、state/goal/frame gates 和稳定 `case_id` 冻结到 canonical exact-case manifest。

Discovery 同时持久化每个 `traj_data.json` 的实际字节哈希、logical/runtime scene mapping、cache/config/helper 字节哈希、实际 import origin 与相关源码 hash、scan algorithm/geometry/policy/plan/snapshot hashes，以及 Python/ALFWorld/ai2thor/NumPy/Pillow/Unity build/scene asset identity。reachable 分别保存完整 raw event evidence hash、原始 actionReturn/reachable payload hash和 canonical finite sorted-set hash，不让 canonical parser 自证，也不让 volatile raw event字段进入 plan identity。每个 reset exact ID 必须有 typed snapshot row；每个 relevant exact ID 必须逐项映射到 frozen case，不能信 worker 自报的 relevant 集合。

对每个矩阵要求的物理 exact ID，Discovery 必须冻结 snapshot row、sole pose/provenance/freshness 以及 current-visible/current-invisible 两种 case oracle。若 restored event 当前不可见，即使 direct row=`ok`，negative case 也必须在 snapshot lookup 前返回 `target_not_visible`、零 tested/model backend action。positive case 只允许使用 Gate-owned visibility fixture：Run worker 完成 fresh reset/同一 time bracket/scan/restore 后，driver 在产品调用边界外执行 0 或 1 个 manifest-frozen fixture Teleport，把同一 exact target 置于 strict-visible event；fixture 必须单独记录请求/返回/actual pose/frame/world、action count和 raw evidence，不进入模型或产品 backend count。driver 随后必须把该 exact event frame作为最新模型可见测试观察发布，并证明 Adapter current event、Provider/session image和 persisted image逐像素相同，之后才调用公开 target并恰好一次使用 snapshot sole pose。fixture exact ID/pose 只在 GateCase 内部，产品/Provider payload不得读取。

reset 时位于 closed container 的 child 仍发布 `unobserved`，但它不再拥有特殊导航授权规则。它与屏外 FloorLamp、Drawer、Shelf 等所有 current-invisible target 使用同一个 negative case：`target_not_visible`、零 snapshot/parent lookup、零 model backend action、pose/world不变、Episode nonterminal、payload 不含 exact ID/parent/containment/pose。另用显式、与 hidden grounding 不相连的普通 container Open/Close cases验证操作能力。

另冻结 post-open transition case，但它不得把 hidden parent 变成探索答案：public container label/order 与完整探索调用序列先写入 committed matrix，controller/verifier 在读取 trial containment 证据前锁定其 hash；paired fixtures 改变 child exact ID/pose/parent 时仍执行逐字节相同的 public `go_to(container) -> Open -> go_to(target)` 顺序。打开错误容器或 Open 后 target 仍非 strict-visible 时，target call 继续是同一零动作 `target_not_visible`；只有某次 Open 的**同一返回 event**让 exact child strict-visible 后，下一次 target call 才可锁定该 exact ID并解析 sole direct/unique-parent pose。独立 verifier 必须证明探索 prefix/hash 不读取 GateCase hidden parent；这只验证模型显式动作能够改变 current view，不是 Harness 自动搜索。

Gate A controller 必须是两个不可合并的阶段：`discover` 完成 reset+time bracket+scan+pose/time restore 并冻结 manifest 后退出且不得启动任何 tested-action worker；独立 verifier 从 raw setup events 重算 time-control policy/order/returns、plan、world delta、pose/time restore、final model event、snapshot、profile、identity、count/hash 并退出零，之后 `run` 才能用显式 expected manifest SHA 消费同一 manifest。每个 run worker 仍 fresh reset、执行同一 policy/time bracket/scan/restore，再消费 frozen case；不得从 discovery 复用 live env 或在动作时重新 discovery。

`discovery-run-006` 与 `discovery-run-007` 都是 immutable 失败证据，不得修改、补写或用新 helper hash 覆盖。time-scale contract 使 helper/matrix policy/hash 发生变化，因此实现必须先将最终五个运行字节归档为 `helper-archive/pre-discovery-run-008`，再从不存在的 fresh `discovery-run-008` 开始；只有独立 discovery verifier 成功后才可创建/consume `case-run-008`。任何可执行 README/command 不得仍把 run-007 当作可写输出路径。

在 formal run-008 前，独立 effect-control worker 必须在与 Gate 完全相同的 Python/ALFWorld/ai2thor/Unity build、固定 trial 和最大 26-pose plan 上黑盒验证时间控制真正生效，不只看 `lastActionSuccess`：所有冻结温度哨兵 exact ID 在 `0.01` 的 query/26 Teleports/pose restore/normal-time return event 内逐实例不变，随后在固定 post-control Teleport prefix 中逐实例重新演化；两个 control 返回码、全部 pose/world rows、进程返回码和 cleanup 都必须通过。不得使用 any/best 哨兵聚合。该 worker 是 build-scoped 正交外部终态证据，不生成 snapshot/case，不进入产品/setup 计数，也不能替代每个 discovery/run worker 自己的逐请求 return/world/final-event 门。

Gate 与产品使用两个不兼容 schema/process owner：

```text
GateCaseManifest                 # 仅独立 Gate controller/test driver
  exact target/anchor/object IDs, action profile, terminal oracle,
  visibility precondition/fixture, raw evidence/hash, case_id

TrialSelectionManifest           # 唯一允许传入产品 Runner
  canonical root-relative trial ID, trial content hash,
  expected logical scene identity, expected goal identity/fingerprint
```

`TrialSelectionManifest` 禁止 target/anchor/object ID、pose、action profile、terminal oracle、visibility fixture 和 scan/snapshot内容。Gate B driver 在产品进程/构造边界外持有 `GateCaseManifest`，只把派生并独立验 hash 的 `TrialSelectionManifest` 传给 Runner，再从外部按公开 ToolSpec 发起测试调用。Runner/Adapter/Provider 的构造参数、serializer、import graph 和 file-open spy 必须证明无法读取 GateCase schema/file；产品 module 对 Gate helper/import/path 的 AST/runtime guard 为零。

Probe 全生命周期安装 `homemaster` import blocker，结束时复查 `sys.modules`；独立 verifier 还要递归审计 probe/controller 本地 import，防止未执行的传递 import 形成同源回声。

| 类别 | 至少覆盖 |
|---|---|
| surface/receptacle | Shelf 低/高层与全部同场实例、Drawer、Cabinet、CounterTop，以及固定 10 条涉及的 Desk/Table/Bed 等全部 exact instance |
| tool/appliance | CoffeeMachine、Microwave、Fridge、SinkBasin/Faucet |
| toggle/fixture | DeskLamp、FloorLamp 的 direct geometry snapshot row，以及同一 exact instance 的 current-visible/invisible case |
| movable | 表面、打开容器、关闭容器、地面、inventory 中的 exact object及 visible/invisible case |
| action | Take/Open/Close/Put/Use/Slice 及 Heat/Cool/Clean 每个 macro 子动作 |

每个 trial/instance 独立 reset，并分别断言：

1. root-relative exact trial ID、trial bytes hash和 goal identity 只绑定 `goal_trial_fingerprint`；logical/runtime asset、pinned build和 scene-only reset digest 只绑定 `scene_reset_fingerprint`。matrix expected、traj logical `scene_num/floor_plan` 和 version/build-scoped runtime asset identity一致；只允许精确 `FloorPlanN -> FloorPlanN_physics`，expected/trial mismatch 与 runtime wrong-scene 分别得到 typed artifact/uncertain 结果；
2. initial pose/frame、完整 exact-ID set、包含 raw `ObjectTemperature` 的 world digest、inventory、goal 和 task counters 可独立重算；
3. scan policy/config/cache/exact-ID inputs、slow=`0.01`、restore=`1.0` 与 control 顺序在第一次 setup action 前冻结；slow-time request 恰好一次、在 query 之前且返回身份/成功/pose/world 不变；`GetReachablePositions` 恰好一次且返回身份/成功、agent pose/world 不变；
4. canonical reachable 与 geometry 规则独立重算同一 `ScanPoseStep` 序列；step 0 为 initial pose/zero action，dedup 后完整 provenance 不丢失，plan hash/count 相同；第一条 Teleport 后 plan 无增删/重排/早停；
5. 每个 setup Teleport 都有请求/返回/raw event/frame，action identity、返回成功、actual pose、完整 world digest不变逐条通过；任何一个失败不能被其他 pose 覆盖同一对象而掩盖；
6. verifier 从 raw events 对每个 reset exact ID 重建 addressability、strict observations、typed unobserved/coverage_miss、per-target provenance/source rank、唯一 pose、object-state hash 和完整 snapshot hash；reset addressability只影响 pose row，不得进入 action-time visibility oracle；
7. pose restore 恰好一次并先在 slow time 中通过 return、准确 initial pose 和完整 world 门；紧接的 normal-time restore 恰好一次，其返回 event 通过 action identity/success、准确 initial pose、完整 object/world/inventory/goal/task counters、visibility/bbox map 和 reset-frame 像素相等门，并成为唯一 restored/model-visible event；cleanup 无残留进程；
8. frozen exact case lookup 只绑定同一 scene-reset/snapshot/object-state；goal-trial 只绑定 case context/trace；任何 target 必须先 current strict-visible，parent resolution 只允许同一个 visible exact target且 reciprocal innermost 唯一；
9. 同一 exact target/snapshot row 的 invisible case 必须 `target_not_visible`、零 snapshot/parent lookup、零 model backend action、nonterminal且不泄露内部字段；visible fixture 独立通过 return/actual pose/current target visibility/bbox/frame/world 门，且该 exact frame成为调用前最新模型可见观察后，positive case才可恰好一次读取并发送 sole pose。closed child另以 child-parent independent 的 public exploration sequence证明 Open 后仍不可见继续失败、同 event strict-visible 后才可 unique-parent 单次成功；
10. locked direct/parent pose 到达后准确 child 不可见必须 per-instance FAIL，不得尝试第二个 pose、outer parent 或 geometry；
11. 对该实例适用的动作逐个核对外部返回码、准确终态、inventory、goal/goal-related state 和同 event frame 像素；Slice 另锁定 current runtime 的 exact ID preserve/replace contract，未核对前保持 UNVERIFIED；
12. snapshot lookup `unobserved/coverage_miss/relocated/absent/malformed/stale/error` 与 setup rejected/uncertain/restore failure 的 raw evidence、status、worker return code各自可区分；
13. setup/control/model backend、env/invalid/total counts 与真实请求逐条一致；successful setup=`1 slow + 1 query + N send Teleports + 1 pose restore + 1 normal-time restore = N + 4`，initial observation=0，失败按实际发送数并包含 finally recovery attempts；Gate visibility fixture 另有独立 count/sequence，不得混入产品/model count或从 total evidence 消失；
14. evidence 记录 root-relative trial ID、exact instance/action、source/runtime/Unity identity、raw event ref/hash、进程退出码、timeout/restore/cleanup结果；
15. runtime mutation gates 分别制造 expected manifest/trial mismatch、absolute/traversal/NUL/symlink-escape ID、runtime wrong-scene、slow-time rejected/unreadable、query rejected/unreadable、pose mismatch、world drift（含只改 raw temperature）、pose restore mismatch、normal-time restore rejected/unreadable、snapshot invariant、evidence failure和 cleanup failure，逐项命中 §5.4/§7.6 唯一 typed terminal 与优先级；独立 persisted-artifact mutations 再把 time-scale wrong value/order、错误 final-event ref 和 `N+4` count drift 分别改成自洽 tamper，必须被 verifier 在对应 policy/order/authority/count 门拒绝，不冒充 worker 真实 typed terminal。

`required_pose_coverage_set` 由 committed matrix rules、public semantic vocabulary 与完整 reset object inventory 在 scan 前独立重算，包含每个要求 snapshot direct pose 的 addressable exact ID；closed descendants、inventory 和非公开语义 row 不得混入。不得信 worker 自报集合。该集合任何 `coverage_miss` 都使对应 visible-positive case/controller FAIL；非 required row 仍保留真实 typed status，不能借此扩大通过集合。该集合只约束 pose feasibility，绝不授权 current-invisible product call。

不得用 best/any 聚合。expected case manifest 与 result 必须恰好一一对应；missing/duplicate/skipped/timeout/nonzero case 都使 Gate A controller FAIL。一个实例失败即该实例 contract 未通过，不能用同类型成功实例抵消。独立 verifier 即使对 synthetic artifact 返回 PASS，也必须保持外部符号 `UNVERIFIED`；只有主 agent 核对真实 runtime 身份、进程返回码和逐实例外部终态后，才能解除相应 Gate A 底层符号。Gate A 只解除底层 feasibility，不解除产品接线 UNVERIFIED。

### 13.2 Gate B：实施后产品接线黑盒

实现后，用真实 `AlfworldEnvAdapter.reset -> AlfworldResetResult` 和 `AlfworldEnvAdapter -> AlfworldStepResult.execution_feedback -> tools -> Dispatcher` 重跑 Gate A 的 exact matrix。每个实例独立 reset，并同时断言：

- typed reset 成功时先完成同一 policy/time bracket/scan/pose+time restore 并发布唯一 snapshot；失败时 zero Provider/API/tool call、typed Episode terminal 和 env quarantine；
- multi-Episode 注入 Episode 1 setup terminal 后，Episode 2 必须取得全新 Adapter/env identity（或被显式 not-run），不得在 closed/quarantined实例上 reset；
- snapshot lookup 只绑定当前 scene reset、snapshot hash和 object-state fingerprint；当前 goal trial/generation 只绑定 execution context、trace 与 outcome；
- taskset `advance_goal()` 成功时 setup=0、control=1、scene physical digest/snapshot hash不变、goal generation递增、旧 context invalid；identity/scene/state失败时当前 subtask在 Provider factory/request 前 typed terminal，后续 subtask not-run；
- 每个 physical target 无论 snapshot row 是否 `ok`，current event 非 strict-visible时第一次 `robot_go_to(target)` 都返回零动作、nonterminal `target_not_visible`，且 `OraclePoseStore/parent resolver/backend` 调用计数为零；
- Gate-owned visibility fixture 在产品调用边界外把同一 exact target 置为 current strict-visible并通过自身 return/actual pose/frame/world 门；该 exact frame必须先成为 Adapter/Provider/session一致的最新模型可见观察，下一次公开调用才可锁定 exact ID、读取 sole direct/unique-parent pose并发送恰好一次导航；fixture action独立计数且内部字段不得进入产品/模型 payload；
- post-open paired fixtures 由外部 driver 执行预先 committed、与 child parent 无关且两边相同的 public container 探索序列；每次显式 Open 后再调用 target。target 未在该 Open event strict-visible时仍零动作失败，strict-visible 后才可绑定同一个 exact ID、使用 sole direct/unique-parent pose并通过单次导航终态门；
- 产品只发送一次锁定移动或预定 macro 子动作；
- 返回码、actual pose、exact visibility/bbox、外部动作终态全部通过；
- persisted frame 像素等于最终 event frame；
- 模型 JSON 等于 typed feedback serializer，且不含任何 exact ID、pose、snapshot、containment、hidden parent 或 Gate fixture 字段；
- lookup 各种非 ok status 分别映射到 §7.5/§10.1 的产品分类；
- trace 中 setup/control/model/total external、env、tool、invalid、global/setup/control/model event sequence 与真实请求逐条一致；
- 真实 run 的 canonical pre-transport Provider body（system/initial prompt/messages/tool schemas/results/images/retry payload）通过 allowlist/no-leak audit。invisible case 的 exact ID/pose/snapshot/Gate fixture不得进入 outbound bytes；visible fixture 后只允许当前真实 frame 显示 target，内部 pose/snapshot仍不得泄露。hidden/offscreen state不同的 paired invisible cases要求同一 `target_not_visible` payload，除枚举的 transport nonce/timestamp 外逐字节相同；
- expected case 与 result 恰好一一对应；missing/duplicate/skipped/timeout/nonzero case 或 process 非零都使 controller FAIL，失败实例不能被聚合隐藏。
- Gate B preflight 锁定 base HEAD、包含 untracked 新产品文件的 source-tree manifest/hash、ALFWorld config content hash、root-relative input manifest ID/hash、actual import origin/source hash、Unity build/asset identity、scan policy/geometry code hash、cache hash、reachable payload/canonical hash和 Python/ALFWorld/ai2thor 版本；绝对 root/host 不进入 portable fingerprint/manifest。独立 verifier 重新计算，防止对错误 checkout 产出 PASS。

Gate B 全过才可把 V1.8 产品接线标为 VERIFIED。

### 13.3 当前可见性与旧旁路黑盒门

为每类物理目标构造同一 exact ID、同一合法 snapshot row 的成对 case：A 的 current event 不含 strict-visible target；B 通过 Gate-owned fixture得到 target strict-visible 的 current event。另以 closed-container Mug 覆盖 `unobserved + unique parent` 分支：

```text
A: robot_go_to(target_label)  # current invisible
B: robot_go_to(target_label)  # same exact target current visible
```

逐项断言：

- A 无论 direct row 是 `ok` 还是 `unobserved`，都返回 `target_not_visible`、snapshot/parent/backend action=0、THOR pose/world不变、Episode nonterminal；模型结果不包含 objectId、pose、snapshot、parent 或搜索提示；
- A 的 paired cases改变 offscreen target ID/pose/parent/snapshot row后，公开失败 payload 除 transport nonce/timestamp 外逐字节相同；Harness 不自动调用其他 `robot_go_to`、Open 或 search；
- B 的 fixture 在产品调用前独立通过返回码、actual pose、准确 target current visible、正面积 bbox、frame/world门，并把该 exact frame发布为最新模型可见测试观察；产品调用必须证明授权 event frame与模型图片像素相同，随后恰好读取一次锁定 snapshot row、发送一个 backend navigation request，并以返回成功、actual pose match、准确 target最终 visible/bbox/frame通过；
- B 在工具调用前的 Provider body 只能包含真实 current frame，不包含 fixture exact ID/pose、snapshot或预期答案；
- closed Mug 打开错误容器或正确容器打开后仍不可见时继续与 A 相同；某次显式 Open 的返回 event 让 Mug strict-visible 后，下一次调用才可锁定该 Mug并使用唯一 reciprocal parent pose；
- generic label 在多个 visible peer 中按 frozen canonical顺序锁定第一项；显式 ordinal只接受对应 exact ID current-visible，ordinal missing/hidden不得 fallback到可见 peer且返回与 A 相同；
- `robot_find_object` 和 `robot_navigate` 不在 registry/tool specs；直接提交旧名称返回 `unknown_tool` 且 Adapter/THOR 调用计数为零；
- hidden parent/source helper 和 legacy `env.step("go to ...")` 的生产 import/call guard 为零。

### 13.4 Context 状态机门

至少逐条覆盖：

```text
go_to(any current-invisible target) -> target_not_visible(0 snapshot/parent/backend action)
go_to(mug hidden) -> target_not_visible(0 action) -> public container exploration/Open ->
  Mug current strict-visible ? go_to(mug, unique parent pose) : target_not_visible(0 action)
go_to(drawer) -> Put(target_closed, 0 action) -> Open(success) -> Put(success)
go_to(mug 2) -> Take(mug 2)，不得换成 mug 1
幂等 Open/Close 保留 context
成功 Open/Close/Use rebase successor event
成功 Slice 按 Gate A 锁定的唯一 current-runtime branch rebase 或 consume，不保留双 mode
成功 Take/Put/macro consume context
成功 advance_goal 保留 scene snapshot、before/after physical digest相等并 invalid旧 context
reset/新移动/pose drift/event gap/无关动作 invalid context
```

每一步断言 context state、snapshot/target/anchor/object-state/pose hashes、event sequence/hash、setup/control/model backend count；不得只看 trace 事件名称。

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
typed reset ready/setup terminal
scan plan malformed/pose rejected/world drift/restore mismatch/evidence failure
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

1. 全仓测试、聚焦 ALFWorld 测试、reset/store/backend/observer 全实现接口 audit、Ruff、format、compileall、cleanup/import/call-graph guard、`git diff --check`；
2. 同一真实 API 配置重跑原 10 条 valid_unseen；
3. evaluation valid coverage、Harness coverage、Provider availability、Runtime availability 必须分别为 100%，每条 setup time/scan/pose+time restore per-instance PASS；
4. raw success、Agent 成功率和四项 availability/coverage 分开报告；
5. 按 look-at、simple place、heat、cool 等任务族逐类报告；
6. 每个失败 Episode 保留唯一分类和可追溯外部证据；
7. 成功率不是设计验收的替代品，外部终态逐实例门必须全部通过。

历史 V1.7 artifact 只保存到 task directory，丢弃了 `extra.gamefile` 中的 exact `trial_*` fingerprint；同时当前 ALFWorld `seed()` 没有给 `reset()` 实际使用的模块全局 RNG 设种子，因此不能从 `seed=42` 可信重放原 exact 10。V1.8 验证不得猜 trial：Gate A/B 对已唯一取证的 6 条 exact trial 加未解 4 条各自全部 3 个候选，形成必含原集合的 18-trial candidate-complete superset，并另加 Clean/Slice action contract trial。真实 API 的 10 条回归固定原 10 个 task directory；6 条使用已证明的历史 exact trial，4 条使用明确标记为 deterministic replacement 的固定 trial，报告中不得冒充已恢复原 exact trial。

Runner 只增加 `TrialSelectionManifest` 输入，用于确定 Episode 选择并持久化 expected/observed scene/goal fingerprint；产品 Oracle pose、target instance、action 和 visibility fixture 不得从该 manifest 或 trajectory 内容取得。target authorization只能来自 current event，pose只能来自当前 reset transaction/snapshot。GateCase manifest 不在产品构造参数、import graph 或可读文件集合中。selection 条目数、顺序、canonical relative ID/content hash、identity status 和 observed fingerprint 必须在第一个模型请求前逐项验证。

## 十四、实施顺序

修订设计批准后的实施顺序固定为：

1. 提交并由用户复核本次 major delta spec；调用 `writing-plans` 替换已失效 plan，并在任何实现前完成项目纪律要求的一次独立 implementation-plan review和逐条处置；
2. 先写 Gate A time-scale enter/restore、scan/pose-restore、`N+4` 计数、final-event authority 的 RED fixtures/mutation gates和独立 verifier，再完成 standalone runtime feasibility；保留 run-006/run-007，从 fresh run-008 解除底层 feasibility，不声称产品集成 VERIFIED；
3. 先写产品 RED tests：typed reset、sole snapshot、all-target current-visible authorization、same-row invisible zero-action/visible single-move pair、generic/ordinal grounding、visible direct/unique-parent pose、typed feedback、封闭错误映射、计数矩阵和旧旁路 guard；
4. 新增 `SceneScanPlan/OraclePoseSnapshot/OraclePoseStore/VisibleObjectView/NavigationAnchorResolver` 强类型接口及全实现 audit；
5. 新增 `AlfworldResetResult/OracleExecutionContext/OracleActionGateway`，统一 setup/navigation/Take/Open/Close/Put/Use/Slice/Heat/Cool/Clean；
6. 将 `robot_go_to` 切到 current-visible-first -> sole snapshot 路径，并删除 find/navigate/action-time candidate/local-put/budget/projection 旧路径；
7. 在 GenericRuntime/Runner/Outcome/taskset/summary/CLI 同步 setup/control/model counters、Provider/runtime/Harness/Agent 分类、attempt retry 和指标；
8. 跑全仓内部验证、接口 audit、cleanup/import/call-graph guards；
9. 跑 Gate B 产品逐实例黑盒，解除或否决产品集成 UNVERIFIED；
10. 同步架构、用户指南、README、CHANGELOG、pitfalls 和正向纪律；完成一次 final code review及针对性修复验证；
11. 用同一真实 API 配置跑固定 10 条 valid_unseen 回归并提交/推送。

任何 Gate A/B linchpin 失败、外部状态矛盾或接口数据形状不一致，都必须回到设计，不得用 fallback mode 掩盖。

## 十五、设计不变量

1. 成功导航的准确 requested target 必须在最终 event 中真实可见并有正面积 bbox。
2. 所有物理 target 当前不可见时不得由 Harness 查询 snapshot/parent、自动搜索、导航其他目标或执行 Open；其 direct call 必须零动作、nonterminal。只有 exact target 在 current event strict-visible，且该 event frame与本次调用对应的最新模型可见图片逐像素一致后，才允许 pose resolution。
3. 模型选择语义目标和动作并负责让目标进入当前画面；setup snapshot 只为已经 current-visible 的 target 提供 sole pose，exact ID/pose/containment 永不进入模型 payload。
4. 正式运行不得读取专家任务轨迹。
5. 同一 exact target、scene reset、snapshot/object state 的 pose 必须确定性且一次锁定。
6. Setup 只允许两级冻结的 target-independent bounded scan；扫描必须被唯一固定 `0.01 -> 1.0` time-scale bracket 包围，且 normal-time return event 全绿后才能发布。所有正式动作使用一次锁定的 snapshot pose，不搜索、不重试、不动态扩大计划。
7. 关闭容器 Put 必须在外部调用前返回 `target_closed`。
8. 每个正式 THOR robot 工具的模型执行反馈只能来自 `AlfworldExecutionFeedback` 的唯一 serializer。
9. 缺失权威字段必须携带非 ok status；不得静默替换为空列表、null 或 false 并冒充有效读取。
10. Provider/Harness/runtime 失败不得进入 Agent 分数。
11. 所有外部调用同时核对返回码和外部终态。
12. 多实例验收逐实例断言，不得聚合取最好结果。
13. `robot_go_to` 是唯一公开导航入口；旧 find/navigate、legacy env command 和 action-time 候选搜索不可达。
14. Take 和所有操作使用 context 中锁定的 exact ID，不重新按类型选择实例。
15. Open/Close 等同 pose 状态动作只可在双门成功后原子 rebase context；移动、event gap 和不确定状态必须 invalid。
16. Snapshot lookup/visibility 的 unobserved、coverage_miss、relocated、absent、malformed、stale、error 与真实不可见必须保持可区分。
17. Parent 只可用于当前 event 已 strict-visible 的 `unobserved/relocated/absent` target，并按 reciprocal containment、唯一 innermost 和 valid pose 解析一次；不得用于定位任何当前不可见 target。锁定 pose 不可用时 terminal Harness failure，不换 pose。
18. Snapshot 只有完整 time-scale enter + scan + pose restore + normal-time restore + world/frame 门全部通过后才原子发布；partial snapshot 永不可用。任何失败都必须 best-effort 恢复 pose 和 `timeScale=1.0`，无法证明正常时间的 env 必须关闭/quarantine。
19. Setup/control/model backend/env/tool/invalid/total counts 分离，setup 成功计数恰好为 `N+4`，失败计入实际 recovery requests；setup/control 不进入 Agent 预算或责任，total external 不漏 `set_task`。
20. `scene_reset_fingerprint/scene_generation` 与 `goal_trial_fingerprint/goal_generation` 分离；长任务不得因 goal 变化误用或误废弃 snapshot。
21. Agent、Harness、Provider、Runtime 指标和责任分类分别计算，未知错误不得默认归 Agent。
22. Reset/goal advance 只能返回合法 tagged ready/terminal 组合；terminal 在 Provider factory/request 前提交，closed/quarantined env 永不复用。
23. Generic label 只锁定 current strict-visible matching IDs 中 frozen canonical顺序的第一项。显式 ordinal 永远绑定 frozen 完整 matching 集合且不 fallback；ordinal不存在或对应 exact ID 当前不可见都返回同一个零动作 `target_not_visible`。snapshot `ok` row、reset observation、公开类型或 containment 都不能授权当前不可见 target。
24. Slice exact-ID 语义由 Gate A 对 current runtime/build 锁定为单一分支；核对前 UNVERIFIED，build变化后阻断重验。
25. 产品只能读取 `TrialSelectionManifest`；含 exact target/anchor/action/visibility fixture/containment evidence 的 `GateCaseManifest` 永远留在产品进程边界外。

## 十六、独立评审意见处置

原 V1.8 设计评审 verdict 为 `FIX`；第一次真实 Gate A 后，用户批准的 bounded-scan replacement 属于 map-only 评审未覆盖的重大外部数据流，因此又执行一次 scoped delta review。所有 reviewer 都只读、未修改文件、未对任何外部符号可用性背书。以下逐项处置后，冻结字节只做 contradiction audit，不以循环 review 追加新方案。

### 16.1 原 V1.8 review

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

### 16.2 Bounded-scan delta review

| # | 评审意见 | 处置 | 文档修改 |
|---:|---|---|---|
| D1 | dedup pose list 丢 per-ID provenance，且初始 event 未观察 | 采纳 | §6.1 改为带 index/send/provenance 的 `ScanPoseStep`，step 0 零动作并纳入 plan hash |
| D2 | addressable/unobserved/coverage_miss/absent 边界不闭合 | 采纳 | §6.1 定义 task-independent 结构谓词、reason 优先级、published/overlay 组合与 required coverage 门 |
| D3 | reset result 无法表达早失败、restore/cleanup 优先级和 quarantine | 采纳 | §6.0、§7.6 增加 tagged reset、trigger/final code、合法 disposition、best-effort restore和 terminal record |
| D4 | logical/runtime scene、scene/goal fingerprint 与 portable path 混合 | 采纳 | §5.2/5.4、§6.0、§13.1 分离精确 versioned scene mapping、scene/goal bytes、root-relative containment和 typed mismatch |
| D5 | 初始不可见 direct target 缺少不泄漏的公开发现路径 | 历史采纳；§16.3 曾废止，现由 §16.4 取代 | 当前不可见本身就是统一零动作 precondition failure；未来记忆/探索负责让目标进入画面，不设 public witness 或 screen-off direct mode |
| D6 | closed descendant 被错误要求 direct pose，nested chain 与 receptacle规则冲突 | 历史采纳；由 §16.4 统一 | closed child 与其他 current-invisible target 同一失败；strict-visible 后才允许 direct/unique-parent pose，不设 hidden-parent-driven Open prefix |
| D7 | Slice 是否替换 exact ID 未真机核对却预承诺 rebase | 采纳 | §6.5、§8.6、§13.4-13.5 保持 UNVERIFIED，Gate A 锁定 current-runtime 单一分支，build变化重验 |
| D8 | `advance_goal` 只看 snapshot hash，且 control action/terminal 无结果账 | 采纳 | §6.0、§10.2/10.5、§13.2/13.4 增加 before/after live scene digest、control count和 root-owned terminal/not-run schema |
| D9 | snapshot 被错误绑定 changing goal trial，lookup hash来自 side channel | 采纳 | §6.0-6.1、§13.1-13.2 让 store atomic read携带 snapshot/pose/freshness，snapshot只绑定 scene reset |
| D10 | reachable raw event/payload/canonical identity 混合 | 采纳 | §6.1、§11、§13.1 分离 raw event evidence、payload parser hash与 canonical plan hash |
| D11 | no-leak 只检查 tool JSON，可能遗漏 prompt/session/schema/image path | 采纳 | §11、§13.2-13.3 检查 canonical pre-transport body、restore frame-0000和合法 hidden-state differential |
| D12 | 部署硬路径/localhost、relative ID逃逸与双 root identity 未封闭 | 采纳 | §5.4、§12.3、§13.1-13.2 使用 injected root/endpoint、escape/symlink guard和双 root/host契约门 |
| D13 | Gate case/result 聚合和 identity 证据仍可假绿 | 采纳 | §5.1、§13.1-13.2 锁定 import/source/Unity/policy/cache/reachable身份，并要求 expected/result严格双射 |
| D14 | setup terminal 后 ordinary Runner 可能复用 closed Adapter | 采纳 | §6.0、§13.2 固定每 Episode fresh Adapter并注入跨 Episode lifecycle门 |
| D15 | Gate hidden-answer manifest 仍可被产品持有 | 采纳 | §12.3、§13.1/13.8 拆为不兼容 `GateCaseManifest`/`TrialSelectionManifest`，加构造/import/file-open/serializer guard |

### 16.3 Run-003 后的用户主导 authorization delta（已被 16.4 取代）

以下三段仅保留当时的决策审计，不是现行规范，任何实现/测试不得引用其 screen-off authorization。

`discovery-run-003` 对 `FloorLamp|-00.65|+00.27|+04.43` 完整执行 1 query、25 scan Teleports和 1 restore；目标只在自己的 target-independent geometry step strict-visible，bbox area=`13455`，该 step 没有 non-target public receptacle provenance。原 D5 public-witness 规则因此按设计拒绝一个 sole pose 已被真实 setup 证明有效的屏外对象。

用户明确本 benchmark 不评测开放房间内的低层视觉寻找：不在当前画面不构成导航阻断，reset snapshot 可以把 addressable target 带到唯一位置；只有仍被 closed container 封住的对象 direct call 才零动作失败，Harness 不知道/不提示正确容器，模型自行探索并可能最终因 no-progress/tool limit 失败。实现机制锁定为 reset-time pre-scan + one frozen pose，不恢复 V1.7 action-time candidate search。

该上游决定废止 D5 的 public-receptacle witness 授权层，并收窄 D6：closed descendant 仍发布 `unobserved`，但 Gate 不再用 hidden parent 驱动 container/Open prefix。模型 Open 后的 newly-exposed child 只有在 current event strict-visible 时才进入 stable current-visible fallback；reset `closed_ancestor` 不能冒充当前仍关闭。本文 §6.2-6.3、§7.2-7.5、§13.1-13.4 和不变量已同步；历史 review 表明确标记 superseded，不冒充 reviewer 对新外部组合的背书。Gate helper/product 实现与外部符号继续 `UNVERIFIED`，直到新版 RED、`discovery-run-004`、case run 和 Gate B 逐实例通过。

Residual risk 是 required target 可能 coverage miss、selected pose可能只可见不可操作，或 post-open/relocated current-visible target没有唯一 parent；这些都由 Gate A/B 逐实例 FAIL 并回到设计，不能新增 candidate、legacy、专家或 hidden-manifest fallback。

### 16.4 导航模块 current-visible 硬约束

在 §16.3 写入并提交后，用户从导航模块 owner 处确认了更上游的真实约束：目标不在当前画面时，导航模块无法导航过去；该约束适用于 Drawer、Cabinet、Shelf、CounterTop、Mug、FloorLamp 等**所有物理目标**。因此 §16.3 的 containment-based screen-off authorization 在任何产品实现前被明确废止，不形成兼容 mode。

用户批准保留 reset-time bounded scan 和 one frozen pose，但重新划分职责：current `VisibleObjectView` 是唯一动作授权源；`OraclePoseSnapshot` 只在 exact target 已 strict-visible 后回答“怎么过去”。snapshot `ok`、reset scan observation、public semantic type、addressability 或 containment 都不能覆盖 current-view miss。generic label 只从 current strict-visible peers 中稳定选择；显式 ordinal绑定 frozen full set，missing/invisible 返回同一个 `target_not_visible` 且不 fallback。

`discovery-run-003` 因而被重新解释：它证明 FloorLamp sole pose 的 setup feasibility，不证明 restored current event 可以导航该 FloorLamp。新的 Gate A/B 对同一 target/snapshot row做 paired proof：invisible event必须零 snapshot/parent/backend action；Gate-owned fixture独立建立 visible event后，才允许一次 sole-pose navigation。fixture不进入产品、Provider或模型，不冒充搜索能力。未来记忆模块负责规划探索并让目标进入画面，但不能绕过 current-visible gate。

本节是用户基于新外部事实主导的上游修订，不冒充既有 reviewer 对新组合的背书。run-004 至 run-007 只部分解除了 Gate helper/fixture 组合并持续保留不可变失败证据；当前 time-scale 后的 Gate 组合仍需 fresh run-008/case-run-008，产品接线仍需 Gate B 逐实例解除 `UNVERIFIED`。

### 16.5 Run-007 模拟时间 delta

`discovery-run-007` 真实跨过 run-006 的 post-Open 失败点，然后在第 5 个 trial 的第 3 个成功 scan Teleport 后以 `scan_world_drift` 停止。raw before/after 投影只有两个 Egg 和一个 Lettuce 的 `ObjectTemperature: Cold -> RoomTemp`；exact IDs、transform、containment、boolean action state、inventory、ALFWorld `heated_objects/cooled_objects/cleaned_objects` 和 goal 全部不变。这证明旧 transaction 让 Harness 的建图耗时与引擎自然时间演化混在一起，不是导航 Teleport 拒绝或 pose 错误。

上游选项和取舍如下：

1. **固定 time-scale bracket（采用）**：仅在 reset setup 内降到 `0.01`，扫描与 pose restore 完成后恢复 `1.0`。它保留完整 world digest 和单一 snapshot 架构，代价是每次 setup 增加两个真实 external action，并必须将失败恢复与计数扩展为一个完整事务。
2. **拆分 semantic/physical digest（拒绝）**：忽略 raw THOR 温度可让 run-007 通过，但会修改已锁定的“完整 world 不变”含义，也可能掩盖未来与 heat/cool 有关的真正 setup 副作用。
3. **reset 后先等待温度 settle（拒绝）**：可以把已变化状态当新 baseline，但会在模型看到世界前主动改写 trial 初始状态，等待时长也受机器速度影响，不确定且难以重放。
4. **暂停 physics 或暂停 Gate（拒绝）**：真环境已证明 `PausePhysicsAutoSim` 虽返回成功却不阻止温度演化；暂停整个 Gate 边界最干净，但会直接阻断已批准的 V1.8 交付。

用户批准选项 1。现行 transaction 因此固定为 `initial zero-action event -> ChangeTimeScale(0.01) -> query -> all scan Teleports -> exact pose restore -> ChangeTimeScale(1.0) -> atomic publish`。任何失败均先停止新 scan，再 best-effort 恢复 pose 与 normal time；最后一个成功 normal-time event 才是模型初始 event/frame。该 delta 不改变任何 current-visible 授权、snapshot pose 解析、post-Open 或 target-not-visible 语义，也不冒充既有 reviewer 对新外部组合的背书。
