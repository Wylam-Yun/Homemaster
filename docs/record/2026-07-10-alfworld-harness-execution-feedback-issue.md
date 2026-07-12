# 2026-07-10 ALFWorld Harness 执行与反馈问题单

- 问题编号：`ALFWORLD-HARNESS-20260710-001`
- 状态：2026-07-12 已修复；单测、六 Shelf characterization 与 production 黑盒门通过
- 影响范围：`AlfredThorEnv` + `visual_eval`
- 严重程度：高。当前结果不能直接解释为纯 Agent 规划与反思能力
- 关联运行：`alfworld-valid_unseen-thor-objectid-20260707-001`
- 关联 Episode：`valid_unseen/pick_and_place_simple-Pencil-None-Shelf-308`

## 修复结果

已完成：

- `robot_go_to` 锁定准确 objectId，并要求 THOR 返回成功、实际 pose 一致、准确目标 `metadata.visible=true`、同 event 正面积 bbox 四门同时通过；
- `robot_manipulate(action=put)` 复用导航后锁定的 `PoseContext`，当前姿态优先，只在同一准确 Shelf 的固定局部候选内重试；
- Put 成功同时核对返回码、Pencil 离开 inventory、`isPickedUp=false`、准确 parent/child membership；
- 显式实例 miss 不再退化到类型级 fallback；
- Harness terminal 失败立即停止 Episode/taskset、排除 Agent 评分并报告 coverage；
- 模型获得结构化 inventory/object state/state change/detail，禁止内部 objectId/坐标泄露；
- 删除不产生新观察的 `robot_inspect_view`。

真环境生产预算：

```text
navigation: 65 candidates / 66 backend actions / 34804 ms
local put:  9 candidates / 17 backend actions / 5669 ms
```

`shelf-characterization-v3` 的 Shelf 1-6 exploration 全部达到 Put 外部终态和 goal `1/1`；Shelf 3/4/6 又在生产预算下逐实例复验通过。随后三个独立 Xvfb 进程使用产品 `AlfworldEnvAdapter` 完成 Pencil 导航/take/Shelf 导航/put，进程均 exit 0，并逐实例通过返回码、inventory、`isPickedUp`、parent/child、goal 与图片像素门。

证据：

- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/`
- `var/alfworld-evidence/20260712-preimplementation/product-harness-v2/`

## 一句话结论

模型正确完成了“找到铅笔、拿起铅笔、前往 Shelf、调用 put”的高层规划；失败首先由 Harness 导航误报和 THOR 物理放置失败触发，随后 Harness 又没有把完整执行状态返回模型，最终模型自身的状态判断和反思也开始失控。

因此，这条轨迹是 Harness 问题和模型恢复能力问题的混合失败，不能标成单纯的模型规划失败。

## 问题现象

任务要求：

```text
put some pencil on shelf
```

模型实际执行：

1. 前往 `desk 1`。
2. 成功拿起 `pencil 1`。
3. 前往 `shelf 1`。
4. 调用：

```json
{
  "action": "put",
  "object": "pencil 1",
  "target_receptacle": "shelf 1"
}
```

该调用失败后，模型又尝试了 `shelf 2`，仍然失败。之后模型反复检查图片、重复 verify，并大量尝试重新拿取实际上仍在手里的 pencil，最终达到 50 步上限。

## 已确认的执行链

`shelf 1` 并没有解析失败。完整链路如下：

```text
模型输出 put(pencil 1, shelf 1)
  -> Harness grounding 成功
  -> pencil 解析为 Pencil|-01.57|+00.88|+00.83
  -> shelf 1 解析为 Shelf|-01.29|+00.87|+00.41
  -> shelf 1 被确认是 receptacle
  -> Harness 调用 THOR PutObject
  -> THOR 返回 lastActionSuccess=false
  -> pencil 仍在 inventory
  -> ALFWorld goal 仍为 0/1
```

因此可以排除以下猜测：

- 不是模型没有调用 put。
- 不是 Tool 不支持“把 X 放到 Y”。
- 不是 `shelf 1` 没有解析出来。
- 不是 Harness 把 Shelf 当成了普通不可放置物体。

## 已确认的 Harness 问题

### 1. 导航成功存在误报

当前导航实现把以下任意一种情况都当成“目标可见”：

```text
THOR metadata.visible=true
或
instance_detections2D 中存在目标框
```

但“图片里检测到 Shelf”不等于“THOR 允许对该 Shelf 执行交互”。

同一 trial 的黑盒复现结果：

```text
robot_go_to(shelf 1) = success
instance_detections2D = true
THOR metadata.visible = false
PutObject = failed
THOR error = No valid Receptacle found
```

Harness 向模型返回了 `Reached shelf 1`，但真实状态并未满足后续操作条件，因此这是导航成功误报。

相关代码：

- `src/homemaster/benchmarking/alfworld/env_adapter.py:1520`
- `src/homemaster/benchmarking/alfworld/env_adapter.py:1977`

### 2. 物理放置失败仍被计入“大脑评测”

固定同一个 trial，移除 LLM，只调用 HomeMaster Adapter 执行相同动作，结果如下：

| 目标 | 导航后的状态 | PutObject 结果 | 最终 Goal |
|---|---|---|---|
| `shelf 1` | 修正视角后 `visible=true` | 无合法放置点 | `0/1` |
| `shelf 2` | `visible=true` | 无合法放置点 | `0/1` |
| `shelf 3` | `visible=true` | 成功 | `1/1` |
| `shelf 4` | 通用导航姿态不可交互；专家位姿可成功 | 取决于位姿 | 专家位姿为 `1/1` |
| `shelf 5` | `visible=true` | 无合法放置点 | `0/1` |
| `shelf 6` | `visible=true` | 成功 | `1/1` |

这说明同一个高层语义动作会受具体层板、相机姿态、现有物体和 THOR 落点采样影响。当前 Benchmark 实际同时测试了大脑能力和低层物理执行。

### 3. 模型没有收到完整的执行结果

Harness 内部 trace 已经记录了：

```text
inventory = You are carrying: pencil.
target = shelf 1
target objectId = Shelf|-01.29|+00.87|+00.41
state_changed = false
THOR error = No valid Receptacle found
goal = 0/1
```

但 `visual_eval` 模式实际只向模型发送：

```json
{"success": false, "error": "action_failed"}
```

模型无法区分以下情况：

- 目标不存在；
- 目标存在但不可交互；
- 目标可交互但没有放置空间；
- 手里没有物体；
- THOR 或 Harness 内部错误。

相关代码：

- `src/homemaster/benchmarking/alfworld/tools.py:334`
- `src/homemaster/benchmarking/alfworld/tools.py:381`

### 4. `robot_inspect_view` 没有产生新观察

当前 `robot_inspect_view(focus=...)` 只返回当前已有的 `frame_path`：

- 不转动相机；
- 不靠近目标；
- 不裁剪或放大 `focus`；
- 不查询 inventory；
- 不检查目标是否可交互；
- 不生成新图片。

本 Episode 的 `frame-0003.png`、`frame-0004.png`、`frame-0005.png` SHA256 完全相同。模型多次“重新检查”，实际一直在看同一张图片。

相关代码：

- `src/homemaster/benchmarking/alfworld/tools.py:124`

## 已确认的模型问题

模型的初始规划正确，第一次失败后检查图片、调用 verify，并尝试换到 `shelf 2`，这些行为合理。

但后续存在明确的反思和状态跟踪问题：

- 两次 put 都收到 `success=false` 后，仍一度把任务标记为 completed。
- 声称要“更具体地重试”，实际发送的 put 参数没有变化。
- 在环境没有变化时重复 inspect 和 verify。
- 没有继续系统尝试其他 Shelf；黑盒验证表明 `shelf 3` 和 `shelf 6` 可以成功。
- 后续出现 33 次失败的 take；内部状态始终显示 pencil 仍在手里。
- 后期目标漂移到 `pen`、`chopstick`。

不过，模型没有看到权威 inventory 和原始错误，且第一人称画面会显示手持 pencil。模型把手中 pencil 误判为已经位于 Shelf、地板或桌面上的物体。因此重复 take 不能完全归因于模型，Harness 的信息缺失放大了该问题。

## 当前责任划分

| 环节 | 当前判断 |
|---|---|
| 高层任务规划 | 模型正确 |
| put 参数表达 | 模型正确 |
| Pencil/Shelf grounding | Harness 正确 |
| 导航成功判定 | Harness 存在误报 |
| 具体物理放置 | 受 THOR 位姿和落点影响，不符合“默认成功”假设 |
| 失败状态反馈 | Harness 信息不足 |
| 失败后的恢复 | 模型存在真实反思和状态跟踪问题 |

建议为该 Episode 使用以下问题标签：

```text
harness_navigation_false_positive
physical_placement_failure
incomplete_tool_feedback
model_recovery_failure
```

不建议标记为：

```text
shelf_grounding_failure
initial_planning_failure
```

## 对当前实验结果的影响

当前运行的成功率是 `9/10`，但失败 Episode 同时包含 Harness 和模型问题。因此 `90%` 不能直接解释为 Agent 大脑规划与反思能力的准确分数。

在问题解决前，至少需要在结果中区分：

- 模型高层决策错误；
- Harness grounding 错误；
- 导航或操作执行错误；
- Harness 反馈不足；
- 模型恢复失败。

## 当前难点

### 1. “操作默认成功”的边界需要定义清楚

不能让所有动作无条件成功，否则错误对象、错误目标也会通过。需要区分：

- 高层语义正确，但低层位姿或碰撞失败；
- 高层语义本身错误，例如没拿物体就执行 put。

### 2. 不能让 Harness 替模型完成高层规划

Harness 可以负责寻找可交互位姿和执行物理动作，但不能直接告诉模型“请选择 shelf 3”，否则会替模型完成反思。

### 3. 反馈既要充分，又不能泄露答案

Harness 应返回真实执行事实，例如 inventory、状态是否变化、目标是否可交互和失败类型；但下一步选择仍应由模型完成。

### 4. 成功必须由真实外部终态确认

不能只返回 `success=true`。放置成功至少需要确认：

```text
inventory 已清空
pencil 的 parentReceptacles 包含 Shelf
ALFWorld goal_conditions = 1/1
```

### 5. 需要决定是否保留物理失败作为反思题

如果 Harness 自动寻找任意可用 Shelf，评测更接近纯规划能力；如果把某个 Shelf 不可放置返回给模型，则可以测试反思，但会引入具体场景和 THOR 物理因素。该边界需要在设计阶段明确。

## 后续工作

本问题单只记录诊断结果，当前未修改代码。下一阶段需要先完成独立设计评审，再决定：

1. 导航成功的严格判据；
2. 操作执行的“默认成功”契约；
3. 模型可见的结构化状态字段；
4. `robot_inspect_view` 是删除还是改成真正产生新信息；
5. Benchmark 如何分别统计 Harness 失败和模型失败。

## 证据位置

- 运行汇总：`var/alfworld-trace/test/alfworld-valid_unseen-thor-objectid-20260707-001/summary.json`
- 失败轨迹：`var/alfworld-trace/test/alfworld-valid_unseen-thor-objectid-20260707-001/episode-0006/trajectory.md`
- 环境 trace：`var/alfworld-trace/test/alfworld-valid_unseen-thor-objectid-20260707-001/episode-0006/trace.jsonl`
- 模型可见 trace：`var/alfworld-trace/test/alfworld-valid_unseen-thor-objectid-20260707-001/episode-0006/model_trace.jsonl`
- 执行 Adapter：`src/homemaster/benchmarking/alfworld/env_adapter.py`
- Tool 反馈构造：`src/homemaster/benchmarking/alfworld/tools.py`
- ALFWorld Goal 判定：`/home/haodong2/weilin/red_bird/alfworld/alfworld/env/tasks.py:150`
