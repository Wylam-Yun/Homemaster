# ALFWorld Benchmark 用户指南

## 适用范围

HomeMaster V1.8 的 `AlfredThorEnv` benchmark 用真实 THOR scene state 评测模型的公开语义决策。Harness 在模型动作前验证 trial 和 reset transaction，在动作后验证准确外部终态；Harness 失败不会伪装成模型失败。

模型可用工具：

```text
robot_go_to(target)
robot_manipulate(action=..., ...)
robot_verify(...)
task_progress_check(...)
```

`robot_inspect_view` 已删除。`robot_go_to` 只能移动到模型当前成功 Provider 请求中已经 strict-visible 的目标，不能用来搜索屏外对象。

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
-> publish snapshot
```

成功 setup action 数为 `N+4`。中途失败会尝试恢复 pose 和 normal time；失败或无法读取会关闭/quarantine 环境，产生 score-ineligible setup terminal，并保持 Provider request count 为 0。此时不要把结果解释为模型没有完成任务。

## Current-Visible 导航

目标必须同时满足：

- 来自当前已提交给模型的准确 event；
- frame bytes 和 decoded pixels 与成功 Provider request 绑定；
- `metadata.visible=true`；
- 准确 objectId 有正面积 bbox；
- snapshot 有该 exact target 的唯一 direct/unique-parent pose。

generic `mug` 只在当前可见 Mug 中稳定选择；显式 `mug 2` 绑定 reset 时 frozen full set。`mug 2` 不存在或当前不可见时返回 `target_not_visible`，不降级到 `mug 1`，也不发 THOR 请求。

导航没有 V1.7 的 65-candidate search。通过授权后只发送 snapshot 给出的一个 `TeleportFull`，再核对返回 action、success、actual pose、world 和准确目标可见性。

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
frames/
```

内部 trace 可保存 objectId、pose、snapshot/attempt hash 和 raw event ref；Provider body 与模型 payload 不含这些内部 authority。evidence ref 只是引用，判定时仍需确认对应 artifact 实际存在并可独立核验。

当前 V1.8 外部证据不完整：Gate A 为 19/20 worker，Gate B best-effort 切片在 reset 恢复阶段终止，`exact-cases-v3.json` 和固定十 Episode manifest 都不存在。因此十 Episode 真实 API 结果不可用，不能把旧 V1.7 Shelf 结果或其他 trial 当作 V1.8 PASS。

设计、架构和交付状态见：

- `plan/V1.8/alfworld-oracle-pose-execution-feedback-spec.md`
- `plan/V1.8/alfworld-oracle-pose-execution-feedback-implementation-plan.md`
- `docs/architecture/alfworld-harness.md`
- `docs/reports/2026-07-16-alfworld-v18-current-visible-report.md`
