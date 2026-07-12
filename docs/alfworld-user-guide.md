# ALFWorld Benchmark 用户指南

## 适用范围

HomeMaster 的 `AlfredThorEnv` benchmark 用真实 THOR scene state 验证 Agent 的高层对象选择、动作顺序和反思。Harness 负责准确 grounding、主要导航和 `put` 的有界局部位姿执行；MVP 不承诺 `take/open/use` 具有同等级局部恢复能力。

模型可用工具：

```text
robot_go_to(target)
robot_manipulate(action=..., ...)
robot_verify(...)
task_progress_check(...)
```

`robot_inspect_view` 已删除。需要目标的新观察时调用 `robot_go_to`。

## 环境

项目要求 Python 3.11。ALFWorld/THOR 运行还需要可用的 X display；无桌面环境时使用独立 Xvfb。

```bash
export ALFWORLD_DATA=/path/to/alfworld/data

PYTHONPATH=src .venv/bin/python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /path/to/alfworld \
  --alfworld-config /path/to/alfworld/configs/base_config.yaml \
  --trace-root var/alfworld-trace \
  --env-type AlfredThorEnv \
  --split valid_unseen \
  --episodes 10 \
  --observation-mode visual_eval
```

服务器示例：

```bash
env ALFWORLD_DATA=/path/to/alfworld/data \
  xvfb-run -a -s '-screen 0 1280x1024x24' \
  .venv/bin/python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /path/to/alfworld \
  --alfworld-config /path/to/alfworld/configs/base_config.yaml \
  --trace-root var/alfworld-trace \
  --env-type AlfredThorEnv \
  --split valid_unseen
```

## Put 行为

典型调用：

```json
{
  "action": "put",
  "object": "pencil 1",
  "target_receptacle": "shelf 3"
}
```

Harness 只解析一次并锁定准确 objectId。显式 `pencil 2` 不存在时返回 `target_not_found`，不会退化成 `pencil` 后选择 `pencil 1`。Harness 也不会把模型选择的 `shelf 1` 自动改成其他 Shelf。

有效 `PoseContext` 来自准确目标通过导航观察门的最终 event。没有该上下文时，put 返回：

```json
{
  "success": false,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 1",
  "inventory": ["pencil 1"],
  "object_state": "held",
  "state_changed": false,
  "error": "navigation_required",
  "detail": "A current observation of shelf 1 is required."
}
```

成功结果：

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

成功不只依赖代码返回值。Harness 同时断言 THOR `PutObject` 成功、准确对象离开 inventory、`isPickedUp=false`，并具有准确 receptacle 的 parent/child membership。

## 固定预算

生产预算来自六个 Shelf 的逐实例真环境实验：

| 范围 | candidates | backend actions | wall time |
|---|---:|---:|---:|
| 主要导航 | 65 | 66 | 34.804 s |
| 局部 put | 9 | 17 | 5.669 s |

`GetReachablePositions` 和每个 `TeleportFull` 都计入导航 backend action。预算在一次调用开始后不会动态扩大，候选列表也不会重新生成。

## 失败与评分

模型可纠正错误（例如 `target_not_found`、`object_not_held`、`navigation_required`）会返回模型继续决策。

以下基础设施分类立即终止 Episode，不再调用模型，也不进入 Agent 正式分数：

```text
harness_grounding_failure
harness_navigation_failure
harness_operation_failure
execution_state_uncertain
unclassified_execution_failure
```

CLI 和 `summary.json` 同时报告：

```text
agent_success_rate_on_valid
harness_valid_coverage
harness_invalid_episodes
formal_score_available
```

只有 coverage 为 100% 且没有未分类执行失败时，`formal_score_available=true`。taskset 中出现基础设施 terminal 后，剩余子任务标记为 `not_run_due_to_infrastructure_failure`。

## Trace 与证据

模型 trace 只包含模型实际看到的稳定字段和图片引用。内部 trace 保留准确 objectId、锁定候选 hash、每次 move/put 返回码、实际 pose、预算用量和原始 event 引用；THOR detail 中的 objectId、坐标、候选列表或专家标记会在模型投影中确定性脱敏。

当前固定运行时和逐实例证据见：

- `plan/V1.7/alfworld-put-local-pose-feedback-evaluation-spec.md`
- `plan/V1.7/alfworld-navigation-local-pose-execution-spec.md`
- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`
