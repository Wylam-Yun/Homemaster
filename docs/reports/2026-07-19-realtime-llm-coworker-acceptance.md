# 实时 LLM Coworker 可观测演示验收报告

## 结论

验收通过。最终接受的两条连续视频均由真实 Mimo `mimo-v2.5` 在
`homemaster shell` 中现场规划、选择工具并根据环境返回恢复，不是 scripted
轨迹回放：

| 场景 | 接受 run | 终态 | 分数 | 独立验证 |
|---|---|---|---|---|
| normal | `coworker-20260720-024949-b7004546` | `complete` | 100 / 100 / 100 | PASS |
| post_change_anomaly | `coworker-20260720-025635-a46d87ca` | `rolled_back` | 100 / 100 / 100 | PASS |

两个 run 的 provider identity 均为 `Mimo`、模型 `mimo-v2.5`、
`anthropic_sdk` transport、远程 HTTPS host，且
`provider_config_override=false`。独立 verifier 同时要求成功 provider
response；配置文件中的模型名字本身不能满足该门。

## 全部真实尝试

| Run | 场景 | 模型调用 | 工具调用 / 被拒 | 业务结果 | 视频 | 处置 |
|---|---|---:|---:|---|---|---|
| `coworker-20260720-022516-8c773877` | normal | 67 | 67 / 5 | 24/24、14/14、100；终态 complete | H.264 验证完成 | 拒绝：stop 客户端超时后重复停止，attempt=failed |
| `coworker-20260720-024949-b7004546` | normal | 42 | 43 / 2 | 24/24、14/14、100；终态 complete | PASS | 接受 |
| `coworker-20260720-025635-a46d87ca` | anomaly | 44 | 44 / 6 | 22/22、11/11、100；终态 rolled_back | PASS | 接受 |

“模型调用”来自 `transport.request_started`；“工具调用 / 被拒”来自
presentation v2 的 `tool.call_started` / `tool.call_failed`。normal 中一次
model response 并行选择了两个 skill tool，因此模型调用数比工具调用数少一。

首次 normal 的独立 verifier 返回 FAIL：

```json
{"failures":["attempt_manifest_status","formal_success_not_true","video_not_passed","summary_video_sha256"],"pass":false}
```

该 run 的真实业务状态和视频本体均成功，但顶层 attempt 没有收到第一次 stop
的 HTTP 响应，不能作为最终接受证据。stdout 证明第一次 stop 为 200、清理重试
为 500；stderr 证明重试向已退出 FFmpeg stdin 写入时触发
`BrokenPipeError`。修复 `6d25b30` 增加 180 秒专用 stop timeout 和服务端
lock/cache 幂等返回；真实两次 stop 黑盒门与后续两条长视频均通过。

## 接受视频

### Normal

- 绝对路径：`/data1/haodong2/weilin/red_bird/Homemaster-coworker-demo/var/coworker-demo/coworker-20260720-024949-b7004546/video/demo.mp4`
- SHA-256：`9e4ae3e59e63eecbc586367a6224b7955d1a2571ce9d4f45e1c1c200ea3ac37c`
- 媒体：H.264、1920x1080、yuv420p、171.133333 秒、2567 帧、FFmpeg 返回码 0。
- 外部配置终态：准确 key `tenanttenanttenant000198:read` 存在，且
  `TenantId`、`ItemCode`、`SpecCode`、`ExtensionName` 四字段完全匹配工单。
- add job `job-add-8445ced7c3` 与 business job
  `job-business_verify-2f69db3464` 均 `succeeded`，业务返回码均为 0。
- 锁定 grep 返回码 0，stdout 是准确四字段记录；terminal outcome 为
  `complete`。
- presentation v2：128 个展示事件、43 次工具调用、2 次真实拒绝、无验证失败。

独立 verifier 输出：

```json
{"failures":[],"observed_required_nodes":24,"pass":true,"required_checkpoints":14,"required_nodes":24,"terminal_exit_codes":[0],"video_sha256":"9e4ae3e59e63eecbc586367a6224b7955d1a2571ce9d4f45e1c1c200ea3ac37c"}
```

人工画面检查覆盖首模型动作、两张 incident open、两张 incident resolved、
middle 和 terminal。模型曾提前提交 add、又用不完整 evidence 请求 proceed；两次
都被环境拒绝并明确显示原因，随后模型重新读取 monitor、补齐证据并恢复。终态帧
同时显示 Planner 完成、`sop_decide complete`、环境 terminal complete，以及
add/business job 的 `succeeded/0`。

### Post-Change Anomaly

- 绝对路径：`/data1/haodong2/weilin/red_bird/Homemaster-coworker-demo/var/coworker-demo/coworker-20260720-025635-a46d87ca/video/demo.mp4`
- SHA-256：`5308921986a4997413de0ee68d5f99e8c37093920048c96274cd0d2650fe3715`
- 媒体：H.264、1920x1080、yuv420p、154.2 秒、2313 帧、FFmpeg 返回码 0。
- add job `job-add-f1989ec59f` `succeeded/0`；首次 grep 返回码 0。
- post-change 告警为 `A-9001201-metric-delay`，
  `caused_by_current_change=true`，`causal_add_job_id` 精确绑定上述 add job。
- rollback decision 在 remove 前被环境接受；remove job
  `job-remove-015d979fed` `succeeded/0`。
- rollback grep 返回码 1、stdout 0 bytes；最终配置文件为 `{}`，准确目标记录
  不存在；terminal outcome 为 `rolled_back`。
- presentation v2：89 个展示事件、44 次工具调用、6 次真实拒绝、无 open
  incident、无验证失败。

独立 verifier 输出：

```json
{"failures":[],"observed_required_nodes":22,"pass":true,"required_checkpoints":11,"required_nodes":22,"terminal_exit_codes":[0,1],"video_sha256":"5308921986a4997413de0ee68d5f99e8c37093920048c96274cd0d2650fe3715"}
```

人工画面检查覆盖首模型动作、红色因果告警、六张 incident open、三张
incident resolved 和 terminal。画面逐次显示 `progress_required`、
`invalid_decision_for_stage`、`rollback_decision_required`，以及模型补进度、
修正决策、成功授权 rollback 后的折叠恢复。终态帧同时显示 add/remove 两个 job
的 `succeeded/0`、Planner 最终项、`sop_decide rolled_back` 和环境 terminal
rolled_back。

首个 `browser_navigate` 命名帧在工具刚启动后 0.35 秒捕捉到左侧 Chrome 的
加载白屏，右侧模型工具调用仍清晰。对同一原始视频第 10 秒做独立临时取帧，工单
页面已完整加载且工具结果为 success，证明这是连续真实导航的短暂加载态，不是
录屏白屏或点击未到位。临时帧未写入或修改已哈希的 run bundle。

## 与 7 月 16 日失败模式的对照

- Planner 晚于 precheck：两条接受 run 均未复现；Planner 在首个必需 precheck
  前创建。
- 跳过 exact job wait：两条接受 run 均未复现；add、business/remove 都等待
  本 run 返回的准确 job ID。
- 过早写 terminal progress：未形成有效 DAG 污染。normal 曾用叙述提前宣称
  precheck 完成，但后继 add/proceed 被环境拒绝，模型补齐真实 evidence 后才通过；
  anomaly 也自然触发一次 `progress_required` 并恢复。
- 异常后错误完成：未复现。anomaly 明确选择 rollback，最终配置消失并以
  `rolled_back` 结束。

## 展示与安全边界

视频持续显示模型 Planner、公开模型输出、每个模型工具调用及参数、环境返回、
确定性事实/判断/下一步、异常和恢复历史。隐藏的 `assistant.thinking`、prompt、
凭证和原始证据正文不进入观察面板；面板数据不回流给模型。

Controlled/scripted normal 与 anomaly 仅用于 presentation failure matrix 和画面
回归，未被列为最终接受视频，也不能替代上述真实 Mimo run。
