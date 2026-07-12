# Engineering Pitfalls

最新记录放在最上方。

## 2026-07-12 - ALFWorld Harness 把内部执行回声当成外部成功

严重程度：高。该问题曾让含 Harness 执行失败的 Episode 进入 Agent 评分，并让 `9/10` 的汇总结果无法直接解释为模型能力。

### 症状

- 模型正确选择并执行 `put(pencil 1, shelf 1)`，但 Harness 先把仅有 2D detection、准确对象 `metadata.visible=false` 的姿态报告成 `Reached shelf 1`，随后只尝试一次 `PutObject`。
- THOR 明确返回失败，Pencil 仍在 inventory，goal 仍为 `0/1`；模型却只收到 `{"success": false, "error": "action_failed"}`，无法判断对象是否仍被持有或失败属于模型、Harness 还是引擎。
- `robot_inspect_view` 重复返回同一图片。Episode 最终耗尽 50 个环境步骤并累计 37 次 invalid action，掩盖了最初的 Harness 失败。
- 修复期间又发现一个相反方向的假设：携带物体成功移动时，THOR 可能随空间重叠更新该物体的 `parentReceptacles` 和 Shelf 的 `receptacleObjectIds`。若要求完整父子集合不变，真实成功的移动会被误判为 `execution_state_uncertain`。

### 根因链

1. 导航把“画面中存在检测框”误当成“准确目标已达到严格观察/交互姿态”，允许 detection 覆盖准确对象的 `metadata.visible=false`。
2. 目标标签在工具层和 Adapter 层重复解析；显式实例 miss 还可能被去掉编号后退化成类型级匹配，导致锁定的语义目标漂移。
3. `put` 没有复用导航成功 event 创建的局部 `PoseContext`，只在当前姿态调用一次 THOR，也没有用准确 inventory 与父子归属证明终态。
4. 内部 trace 虽记录 inventory、THOR error 和 goal，模型投影却把信息压成 `action_failed`；无新观察的 inspect 又制造了“已经复查”的假象。
5. Runner 没有独立的 Harness terminal/score eligibility 控制面，于是低层执行失败被累计为模型 invalid action 并进入 Agent 分数。
6. 第一版移动门把“完整动作状态不变”同时用于成功和失败移动，没有先在真环境核对派生父子字段的移动语义；同源 mock 无法揭示这个假设错误。

### 为什么单测和 trace 会假绿

- mock event、分类器断言和设计文字可以共享同一个错误假设；三者一致只是内部自洽，不是独立证据。
- `lastActionSuccess`、`Reached ...`、`action_failed` 或一条 `put_result` 日志只证明代码走到某处，不能证明准确对象的外部终态发生了预期变化。
- 2D bbox 证明目标出现在渲染中，不证明准确对象 `metadata.visible=true`，更不证明后续 `PutObject` 可用。
- 内部 trace 中存在丰富状态，不代表模型实际收到了这些字段；历史 `model_trace.jsonl` 只保留了通用错误和旧图片。
- 按多个 Shelf 的 best/any 结果验收会让一个可成功实例遮住其他实例失败。候选预算和终态必须逐实例断言。

### 修法与教训

- 每个外部动作同时核对外部返回状态和独立读取的真实终态。导航成功要求同一 event 的 `TeleportFull` 成功、requested/actual pose 一致、准确对象 `visible=true`、准确对象正面积 bbox 和可保存且像素一致的 RGB frame。
- Put 成功要求 `PutObject.lastActionSuccess=true`、准确 Pencil 离开完整 inventory、`isPickedUp=false`、准确 Shelf 属于 Pencil parent membership、Pencil 属于准确 Shelf child membership；真环境验收再独立要求 goal `1/1`。任何返回/终态矛盾都立即停止为 `execution_state_uncertain`。
- 成功携物移动只锁定 held ID、完整 inventory、准确对象仍存在、`isPickedUp=true` 并核对实际 pose；不得要求 parent/child 集合不变。失败移动只有在完整动作状态和 pose 都不变时才能继续；失败 Put 只有在完整动作状态不变时才能换下一个候选。
- 准确 objectId、目标 objectId、候选集合、顺序和 hash 在一次调用开始时锁定。重试只幂等执行锁定候选，不重新解析实例、不重算漂移目标。
- 所有发给 THOR 的请求都计入 backend action，包括 `GetReachablePositions` 这类 query；每次请求前检查固定候选数、backend action 数和 wall-clock 三预算，禁止 N+1 请求。
- 用不 import 产品 resolver、候选生成器或分类器的真环境 probe 做正交黑盒门，并对每个 Shelf 独立 reset、执行和断言。`shelf-characterization-v3` 的 Shelf 1-6 均通过；产品 Harness 又对 Shelf 3/4/6 分别证明返回成功、准确外部放置终态和 goal `1/1`。

### 参考

- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`
- `plan/V1.7/alfworld-navigation-local-pose-execution-spec.md`
- `plan/V1.7/alfworld-put-local-pose-feedback-evaluation-spec.md`
- `src/homemaster/benchmarking/alfworld/env_adapter.py`
- `src/homemaster/benchmarking/alfworld/execution.py`
- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/summary.json`
- `var/alfworld-evidence/20260712-preimplementation/product-harness-v2/shelf-{3,4,6}/result.json`
