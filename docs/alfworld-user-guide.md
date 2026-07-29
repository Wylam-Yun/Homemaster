# ALFWorld Benchmark 用户指南

## 适用范围

HomeMaster V1.8 的 `AlfredThorEnv` benchmark 用真实 THOR scene state 评测模型的公开语义决策。Harness 在模型动作前验证 trial 和 reset transaction，在动作后验证准确外部终态；Harness 失败不会伪装成模型失败。

模型可用工具：

```text
robot_go_to(target)
robot_manipulate(action=..., ...)
robot_verify(...)
task_progress_check(...)
observe()
```

`robot_inspect_view` 已删除。默认情况下，`robot_go_to` 不执行探索或多候选搜索，但可以把 frozen scene
index 中的离屏语义目标映射到 reset snapshot 的唯一 pose，并尝试一次导航；返回后仍必须看到准确目标。
设置 `alfworld_gateway.allow_offscreen_object_navigation: false` 后，当前不可见且
`receptacle=false` 的目标在任何 THOR 动作前返回 `target_not_visible`；当前不可见的 receptacle
仍可作为搜索锚点导航。该模式用于验证模型是否能搜索并记住物体所在锚点，不删除默认点导航能力。

`observe({})` 返回当前 frame 的一张 PNG，供模型自行确认画面；没有文字或状态 payload，也不会步进环境、改变
评分状态，或成为 `robot_go_to` / `robot_manipulate` / `robot_verify` 的前置条件。

## 飞书 Gateway 模式

Gateway 进程继续运行在 HomeMaster 的项目 `.venv`；ALFWorld、AI2-THOR 与 Torch 留在
`alfworld_gateway.python_executable` 指向的既有环境。HomeMaster 只通过 loopback HTTP 与受管 worker
交换 JSON，不需要统一两套依赖。

在 ignored 的 `config/homemaster.yaml` 填写 `alfworld_gateway`（字段模板见
`config/homemaster.example.yaml`），然后运行：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --alfworld \
  --config config/homemaster.yaml
```

位置记忆实验同时设置：

```yaml
memory:
  enabled: true
alfworld_gateway:
  allow_offscreen_object_navigation: false
```

模型可自主使用 HomeMaster 结构化 `add_memory`、`search_memories`、`get_memory` 和
`update_memory`。运行时只把当前环境操作产生的 opaque evidence ref 加入模型可见 tool result，
不披露 exact object ID、containment、pose 或内部 trace；不强制模型写入或检索。ALFWorld 的 legacy
benchmark `memory_mode` 继续保持 disabled，避免出现第二套 memory writer。

启动会验证固定 trial、reset 后状态和 worker readiness；一个 Gateway 进程只把该环境授予一个
session，其他并发 session 明确失败。每个真正尝试过 backend 的导航或 manipulation 后：

1. 飞书先收到“已导航/已拿起/已放置”等语义进度；
2. 下一次 Provider 请求只披露 `observe`；
3. 模型必须真实调用 `observe`；
4. 同一张 PNG 作为模型 image block，并作为紧随动作进度的飞书图片发送；
5. 图片有效后才允许下一个动作或最终回复。

`task_planner` 是可选工具：模型调用时展示子任务，未调用时正常执行。飞书不显示 thinking、
token usage 或 `tool.call_started/completed` 这类内部事件；完整机器轨迹仍保存在 JSONL。

## 环境与输入

项目要求 Python 3.11。THOR 需要可用 display；无桌面环境时使用 Xvfb。真实认证信息只放在本机忽略的 provider 配置或环境变量中，不写入命令、trace 或 manifest。

`AlfredThorEnv` 必须提供 `--trial-manifest`，且 entry 数必须与 `--episodes` 完全相等：

```bash
export ALFWORLD_DATA=/path/to/alfworld/data

PYTHONPATH=src xvfb-run -a -s '-screen 0 1280x1024x24' \
  .venv/bin/python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /path/to/alfworld \
  --alfworld-config /path/to/alfworld/configs/base_config.yaml \
  --trace-root var/alfworld-trace \
  --env-type AlfredThorEnv \
  --split valid_unseen \
  --episodes 1 \
  --trial-manifest /path/to/trial-manifest.json \
  --observation-mode visual_eval
```

manifest schema：

```json
{
  "schema_version": "alfworld-trial-selection-v1",
  "entries": [
    {
      "trial_id": "valid_unseen/example/trial_1/traj_data.json",
      "trial_sha256": "<64 lowercase hex>",
      "expected_logical_scene": "FloorPlan1",
      "goal_identity": "<canonical goal JSON>",
      "goal_fingerprint": "<64 lowercase hex>",
      "identity_status": "historical_exact"
    }
  ]
}
```

manifest 只允许上述字段。trial ID 必须是 trial root 下的 canonical POSIX 相对路径；文件 hash、logical scene、goal identity 或 fingerprint 不匹配时运行在环境构造前失败。不要手写未知 trial 身份，也不要根据成功率替换失败 trial。

## Reset 行为

THOR Episode 的模型循环只在以下 transaction 完整 ready 后创建：

```text
reset
-> ChangeTimeScale(0.01)
-> GetReachablePositions
-> bounded scan x N
-> restore initial pose
-> ChangeTimeScale(1.0)
-> persist reset ledger, snapshot and event projections
-> publish snapshot
```

成功 setup action 数为 `N+4`。中途失败会尝试恢复 pose 和 normal time；失败或无法读取会关闭/quarantine 环境，产生 score-ineligible setup terminal，并保持 Provider request count 为 0。此时不要把结果解释为模型没有完成任务。

## Frozen-Snapshot 单次导航

每次导航必须同时满足：

- 当前 THOR event 的 frame bytes 和 decoded pixels 与成功 Provider request 绑定；
- 语义目标存在于 reset 时冻结的完整 scene index；
- generic label 优先选择当前 strict-visible 实例，否则稳定选择冻结顺序中的第一个离屏实例；
- 显式 ordinal 始终绑定冻结完整集合中的对应实例，不 fallback；
- 离屏目标必须有自己的唯一 direct snapshot pose；只有当前 strict-visible 的目标才允许解析 unique-parent pose；
- `TeleportFull` 返回后，准确 objectId 必须 `metadata.visible=true` 且有正面积 bbox。

generic `mug` 会优先选择当前可见 Mug；全部离屏时选择 frozen full set 中第一个可用实例。显式 `mug 2` 可在离屏时消费自己的 snapshot pose；ordinal 不存在时返回 `target_not_found`，不会降级到 `mug 1`。

导航没有 V1.7 的 65-candidate search。解析后只发送 snapshot 给出的一个 `TeleportFull`，再核对返回 action、success、actual pose、physical world、ALFWorld control state 和准确目标可见性。physical world 投影忽略视角字段以及 `isPickedUp=true` 对象随 agent 改变的 position/rotation/bounds，但继续哈希 inventory、`isPickedUp`、containment 和其他对象状态，所以持物导航不会误报，真正的拿取、放下或场景变化仍可检测。缺失 pose、状态漂移或移动后仍不可见属于 score-ineligible Harness terminal，不再耗尽模型工具预算。

## Manipulation 与反馈

`robot_manipulate` 支持 `take/open/close/put/use/slice/heat/cool/clean`。每个动作使用准确 grounding、当前 `OracleExecutionContext`、唯一 gateway 和动作专用终态门。动作可能 preserve、rebase、consume 或 invalidate context；状态读取不确定时不会继续尝试。

模型收到的反馈具有固定 shape：

```json
{
  "success": false,
  "action": "put",
  "object": "pencil 1",
  "target": "shelf 1",
  "inventory": ["pencil 1"],
  "inventory_status": "ok",
  "object_state": "held",
  "object_state_status": "ok",
  "target_state": null,
  "target_state_status": "not_applicable",
  "state_changed": false,
  "state_read_status": "ok",
  "error": "navigation_required",
  "terminal": false,
  "classification": null,
  "score_eligible": true,
  "detail": "navigation_required"
}
```

`target_not_found`、`target_not_visible`、`object_not_held`、`navigation_required` 等是模型可纠正的 non-terminal error。Harness navigation/operation、状态不确定、provider/runtime/artifact 和 cancelled 会产生 terminal classification；同一 assistant batch 的后续 robot call 不再触碰 Adapter。

## Provider 重试

每个 LLM attempt 都记录独立 attempt ID、准确 serialized request hash 和 ordered image hashes。LLM client 本身不轮换 key、不剥离图片重试。Runtime 最多重试一次，且只接受 transient network、rate limit 或历史已知的 `message_delta_before_message_start` stream protocol error；认证错误、partial response、普通 provider error 和请求 bytes 漂移不会重试。

## Taskset 行为

Taskset 会在构造 Adapter 前验证整条链的所有 trial。setup failure 时全部 subtask 为 `not_run/taskset_setup_failure`；goal advance failure 时当前行为 `goal_advance_failure`，后续行为 `prior_infrastructure_failure`。

not-run 行不拥有 classification 或 action count。root 单独报告：

```text
setup_backend_action_count
benchmark_control_action_count
model_backend_action_count
total_backend_action_count
total_external_action_count
```

## 失败与评分

CLI 和 `summary.json` 必须一起读取：

```text
raw_success_rate
agent_success_rate_on_valid
evaluation_valid_coverage
harness_coverage
provider_availability
runtime_availability
cancelled_episodes
formal_score_available
```

`formal_score_available=false` 表示证据覆盖或基础设施可用性不足，此时不能只报告 Agent success rate。setup/control/model 的 action count 分开持有，避免把 Harness 自身动作算进模型行为。

## Trace 与证据

每个 Episode 可能包含：

```text
model_trace.jsonl
provider_attempts.jsonl
trace.jsonl
summary.json
trajectory.md
reset-transaction.json
oracle-pose-snapshot.json
events/*.json
frames/
```

内部 trace 可保存 objectId、pose、snapshot/attempt hash 和 raw event ref；Provider body 与模型 payload 不含这些内部 authority。成功 reset 会在 snapshot 发布前原子落盘 ledger、snapshot 和每个 setup event；失败 reset 也会落盘原始失败与恢复请求。event artifact 保存 canonical metadata 和 base64 frame bytes，因此 raw event、frame、request、physical world、control state 和 snapshot hash 都可独立重算。

当前 V1.8 外部证据不完整：Gate A 为 19/20 worker，`exact-cases-v3.json` 仍不存在。固定十 Episode manifest 由 6 条 historical exact 和 4 条 Gate 前锁定的 deterministic replacement 构成。早期十条运行全部在 reset recovery 终止；修复后的 `alfworld-valid_unseen-v18-offscreen-fix-20260718-002` 完整执行十条，产生 52 次 Provider attempt、29 次模型 backend action、1 个真实 Agent success、5 个 score-eligible Episode 和 5 个 Harness invalid。4 个 FloorPlan10 行在第一次导航后检测到 physical-world drift，1 行在手持 Basketball 时收到 THOR 对 DeskLamp frozen pose 的明确拒绝；coverage 为 0.5，`formal_score_available=false`，不是完整 Gate B PASS。十条 reset 的 10 个 snapshot、311 个 request/event hash 和 321 个 event artifacts 已独立重算通过。

设计、架构和交付状态见：

- `plan/V1.8/alfworld-oracle-pose-execution-feedback-spec.md`
- `plan/V1.8/alfworld-oracle-pose-execution-feedback-implementation-plan.md`
- `docs/architecture/alfworld-harness.md`
- `docs/reports/2026-07-16-alfworld-v18-current-visible-report.md`（修复前历史报告）
