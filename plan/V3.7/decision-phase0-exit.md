# 裁决⑤：Phase 0 出口裁决 — 三条语义缝复核

状态：已裁决
依据：设计 §7 风险登记列的三条语义缝，逐条以真实测试证据复核。

## 缝①：`Msg.metadata` 承载 HM `data` / `provider_metadata` / 机器字段

**裁决：成立。**

- `ToolResultMessage.data` 整树挂在 `ToolResultBlock.metadata["hm"]["data"]`
  下往返无损（`result_to_chunk` + `from_agent_scope` 还原），
  `backend_attempted` 在 `metadata["hm"]` 显式位保留——
  `test_substrate_messages.py` 12/12、`test_substrate_spike.py` 断言
  `hm["data"]["backend_attempted"] is True`。
- 派生字段（`DataBlock.name`、tool_call `state`）不回流，避免噪音；
  空 assistant 段经 `segments[i]` 侧袋（含 `n` 块数）按原位恢复。
- 限制已写明：metadata 是 jsonable 直通袋，凡入袋字段须为 JSON 可序列化；
  HM 侧已是（`data` 经 `_freeze_json_object`）。

## 缝②：`reply_stream` 事件粒度够 HM 公共投影

**裁决：成立（粒度足够；投影适配器属 Phase 2 工作）。**

`agentscope.event` 提供 30 种 typed event，覆盖 HM 投影所需全部面：
`TextBlock{Start,Delta,End}`、`ToolCall{Start,Delta,End}`、
`ToolResult{Start,TextDelta,DataDelta,End}`、`ThinkingBlock*`、
`DataBlock*`、`Reply{Start,End}`、`ExceedMaxIters`、
`RequireUserConfirm`/`RequireExternalExecution`/`UserInterrupt`。
相对现状只多不少（thinking/usage/结构化输出皆有独立事件，投影
allowlist 天然能把它们挡在公共面外）。

剩余风险（已登记 Phase 2）：`KNOWN_EVENT_TYPES` 清单陈旧需同步；
`Msg` 终态事件的投影等价 golden 测试待建。

## 缝③：确认后重放路径下物理安全语义完整

**裁决：成立（spike 实测）。**

`test_substrate_spike.py` 5/5，全部黑盒断言设备子进程终态：

| 路径 | 证据 |
|---|---|
| 批准→执行 | enter/take 逐项决策卡 → revalidate → lease → `adapter.execute` → observe → release；`held=True`/`ops` 计数核对 |
| AS 层拒绝 | `evaluate_tool` deny → `PermissionDecision.DENY`，`exec_attempts` 为空 |
| 审批取消 | `ApprovalCancelled` → `denied` + `error_code=permission_denied`，设备零动作 |
| backend 起爆 | `on_execute` 抛错 → `outcome_unknown` + `backend_attempted` + release 照跑 |
| 两步导航 | move+pick_up 串行单审批，终态 `area=bedroom`+`held=True` |

`tool_call_id` 经 `RunScopeMiddleware.on_acting` → contextvar 桥接进
`ToolExecutionContext`（`Toolkit.call_tool` 不透传 block id 的缺口由
中间件补），测试断言 `context.tool_call_id == "block-42"`。

**已知降级（已登记）**：`resource_key` 并发策略在 AS `is_concurrency_safe`
布尔上退化为 sequential——资源内串行语义由 HM `ToolExecutor` 内部
lease 兜底，未丢失，仅并发度变保守。

## Phase 0 出口结论

三条语义缝均有真测试证据兜底，无悬空裁决。**Phase 0 通过**，
进入 Phase 1（provider/消息层替换）。

残留跟进项（不阻塞出口）：
- `sys.modules` 白名单回归测试（core-only 不拉起 app/rag/tui）——
  已手工验证过，固化为测试属 Phase 1 顺手项
- `launch_console` 演示壳 smoke——留待 Phase 1 CLI 换引擎时一并验收
