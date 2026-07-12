# ALFWorld Harness 架构

## 目标边界

`AlfredThorEnv` benchmark 评测高层规划，不评测连续坐标控制、路径搜索或机械轨迹。模型选择准确对象、receptacle 和动作；Harness 只执行该语义目标所需的确定性 grounding、主要导航和同目标局部位姿。

MVP 只把 `action=put` 路由到新执行器。`take/open/close/use/heat/cool/clean/slice` 保留 legacy 路径，不能据此宣称全部 manipulation 已具备相同终态契约。

## 数据流

```text
LLM tool call
  -> ToolDispatcher terminal gate
  -> ALFWorld ToolSpec executor
  -> SceneObjectIndex (一次解析并锁定 objectId)
  -> AlfworldEnvAdapter
       robot_go_to -> NavigationSearchContext -> THOR
                    -> 四项观察门 -> PoseContext
       put         -> ManipulationExecutor -> THOR move/PutObject
                    -> 外部终态分类
  -> 稳定模型投影 + 最终 event 图片
  -> EpisodeOutcome
  -> Episode/taskset classification + coverage
```

三个接收方严格分离：

- 模型只看语义 label、inventory、object state、state change、稳定 error、安全 detail 和最新图片；
- 内部 JSONL trace 保存 objectId、pose、候选 hash、外部调用、返回码、耗时与 raw event 引用；
- 评测 summary 保存互斥分类、计分资格、Agent/Harness 计数和 coverage。

## Grounding

reset 后从同一个 scene metadata snapshot 建立 `SceneObjectIndex`。canonical label 按 `(normalized object type, objectId)` 稳定排序。

```text
显式 pencil 2 -> 只做 canonical exact lookup
通用 pencil   -> 按固定排序选一次并锁定
```

显式目标在权威索引中不存在是 `target_not_found`；权威索引存在但 resolver 无法得到同一 objectId 是 terminal `harness_grounding_failure`。后续导航和 put 只传锁定 objectId，不重新解析原字符串。

## 导航

导航候选只生成一次，存入单次 `NavigationSearchContext`。生产预算：

```text
max candidates       = 65
max backend actions  = 66
max elapsed          = 34804 ms
```

`GetReachablePositions` 计一个 backend action，每次 `TeleportFull` 再计一个。成功候选必须在同一最终 event 中同时满足：

1. THOR return success；
2. requested pose 与 actual agent pose 匹配；
3. 准确 objectId 的 `metadata.visible=true`；
4. 准确 objectId 的检测框存在且面积大于零。

保存给模型的图片必须逐像素来自这个成功 event。成功后以该 event 的实际 pose 为起点，重新生成并锁定独立 `PoseContext`；导航候选池和局部候选池不共享可变状态。

## Put 状态机

`ManipulationExecutor` 锁定 held objectId、target receptacleId 和完整 `PoseContext`。生产预算：

```text
max pose candidates  = 9
max backend actions  = 17
max elapsed          = 5669 ms
```

当前姿态先执行 PutObject；后续候选依次执行同一目标的 `TeleportFull -> PutObject`。第一个满足终态门的候选立即停止。

Put 成功门：

```text
PutObject return success
and exact object not in inventory
and exact object isPickedUp=false
and target id in exact object's parentReceptacles
and object id in exact target's receptacleObjectIds
```

Put 明确失败时，只有完整动作状态向量不变才允许下一候选。返回码和终态矛盾、读取缺失、部分变化或异常统一进入 `execution_state_uncertain`。

移动成功使用更窄的不变量：held ID、完整 inventory、准确对象存在、`isPickedUp=true`，并独立核对 actual pose。真环境证明携带物体经过 Shelf 时 parent/child membership 可能变化，因此成功移动不能要求完整父子集合不变；移动失败仍要求完整状态和 pose 不变。

## Context 生命周期

`PoseContext` 包含 scene/goal generation、source event sequence、source frame hash、anchor objectId、actual pose、固定候选 tuple 和候选 hash。

以下事件使其失效：reset、goal advance、非 context 移动、anchor 消失、相关 manipulation 状态变化、pose/return 矛盾或 event/frame 身份无法核对。普通模型文本、progress check 和不改变环境的 verify 不重建上下文。

## 终止与评分

Runner 持有单一 `EpisodeOutcome`。首个 Harness terminal 原子写入 classification、tool call id、计分资格、Agent tool count、backend action count 和 evidence ref。同一 assistant turn 中后续 robot 工具在 dispatcher 前置门被跳过，不触碰 Adapter/THOR。

只有 `agent_success` 和 `agent_model_failure` 计分。Harness、provider/runtime/artifact 和 cancelled 分类均 `score_eligible=false`。taskset 任一基础设施 terminal 会终止整条链，剩余 subtask 标为 `not_run_due_to_infrastructure_failure`。

```text
harness_valid_coverage = agent_scored_episodes / total_episodes
formal_score_available = coverage == 1 and unclassified failures == 0
```

## 可观测性

导航和 put 在内部 trace 记录 context 创建、候选开始、move/put 调用与返回、状态读取、context 失效和 terminal。每条事件包含 context/generation、候选 hash、attempt、requested/actual pose、raw event ref/hash、分阶段耗时、预算上限/用量和 stop reason。

模型投影递归移除 objectId、坐标、候选、全场景对象和专家答案。原始 THOR detail 仍完整保存在内部证据；模型只收到安全直通或确定性脱敏版本。

## 固定运行时证据

当前契约在 Python 3.11.15、ALFWorld 0.5.0、ai2thor 2.1.0 和固定 valid-unseen trial 上核对。六个 Shelf exploration 全部达到导航、put 和 goal `1/1`；Shelf 3/4/6 又由产品 Adapter 在独立 Xvfb 进程中通过 return code、inventory、`isPickedUp`、parent/child、goal 与图片像素黑盒门。

换 ALFWorld/ai2thor 版本后必须重新运行 characterization，不能把当前外部 enum、字段或容差直接视为跨版本保证。成功 toggle/use 的终态字段仍未核对，不在 MVP 声明内。
