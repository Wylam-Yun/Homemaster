# 裁决⑥：Phase 2 出口裁决 — AgentScope 引擎壳收口

状态：已裁决
依据：`implementation-plan.md` Phase 2 验收门逐条对照实测结果（远端
worktree `Homemaster-v35`，v35-architecture 分支）。

## 落地形态

`AsAgentRuntime`（`substrate/runtime.py`）驱动 vendored
`agentscope.Agent.reply_stream()`；HM runtime 契约由三个 middleware +
shell 投影层共同保住：

| HM 语义 | AS 落点 | 证据 |
|---|---|---|
| 取消 / SIGINT | `InterruptController` → `_StreamAbortShim.close()` 取消挂起的 `__anext__()` task，runtime 再 `aclose()` 并 join | `test_as_runtime_sigint_aborts_model_call`（真 SIGINT） |
| deadline | `_await_with_deadline`（绝对剩余 + ensure_future + wait + 显式 cancel/join） | `test_as_runtime_cancel_with_deadline` |
| 每迭代 context 组装 / 压缩 | `ContextAssemblyMiddleware.on_model_call` 重写 `messages`/`tools`；context-length 错误触发反应式压缩重试 | wiring + e2e |
| transport 三相 commit | `ProviderObservabilityMiddleware`：request_started/response_completed/request_failed + `ProviderAttemptRecord`；流式响应包装到消费完才记 latency/usage | 全部 AS 测试的 transport.* 断言 |
| 许可 / 物理链 | `HomeToolAdapter.call` → HM `ToolExecutor`（含 `ApplicationToolExecutor.execute_for_substrate` 单发缝）；`check_permissions` 委托 `permission_checker.evaluate_tool` | permission_denied 回 `error_code=permission_denied`；app 级 device.* 能力门 |
| 观察屏障 | `ObservationBarrierMiddleware`：pending 时工具裁剪到 observe + `on_system_prompt` 注入提示；`on_check_permission` 批量/协议围栏；`on_acting` 后钩跑 `MAX_OBSERVE_FAILURES` 重试的 runtime-owned auto-observe；barrier 清除/手动观察记账 | `test_as_runtime_automatic_observation`、`test_as_runtime_pending_barrier_resume`、app 级 `automatic_observation` |
| 预算 | `ReActConfig.max_iters = max_tool_iterations + 8`（宽限），shell 用 `normal_iterations` 只计非 followup 轮 | budget_exhausted 映射测试 |
| 快照 | schema-v2：`build_snapshot_payload(engine_state, run_state, task_state_store, …)`；屏障/`unconsumed` 随 `agent_state` 子树往返 | `test_as_runtime_resume_from_v2_snapshot`、resume 测试 |
| session 镜像 | 引擎 context → `from_agent_scope` 单向投影（水位线防重/防漏 assistant.reply） | dual-run 等价 + wiring 测试 |

## 过程中的真实缝（已修）

1. `_round_schemas` NameError 让 auto-observe 抛 ExceptionGroup → 改用
   `observation_tool_name(handle.all_tool_schemas)`。
2. `auto-observe-*` runtime 自有调用会被自家 `on_check_permission` 当
   mixed batch 拒掉 → 按 id 前缀豁免。
3. `ToolCallBlock.input` 是 JSON 字符串不是 dict → 建块时 `json.dumps`。
4. `on_check_permission` 里 `call_id` 未定义 → 补上。
5. `ApplicationToolExecutor` 缺 `permission_checker` 属性 → 委托到内层
   `ToolExecutor`。
6. `ExceptionGroup` 吞真错误 → `_flatten_error` 展开叶子进 turn_failed。
7. `HintBlock`（AS 每轮注入的 runtime-state 提示）投影告警刷屏 → 降为
   DEBUG（引擎内部注释，每轮重算，非 canonical 内容）。
8. vendored `src/agentscope` 触发 legacy-terms 守卫 → SKIP_DIRS 加排除。
9. HM `unconsumed_observation_tool_call_id` 语义修正：auto 路径记 source
   action id（非 observe call id）；屏障清除的 observe 结果盖
   `observation_of_tool_call_id` 戳。

## 与 HM 语义仍有意的差异

- AS 事件序：`ModelCallEndEvent` 可先于 assistant Msg 落 context → 用
  watermark 重扫代替"读 context[-1]"。
- 自动观察的 `tool.call_*` 事件由 middleware 自发（AS 引擎不会为嵌套的
  `call_tool` 发 acting 事件）——保持 HM 事件面可见性。
- `max_tool_iterations=None` 时 AS 默认 50 封顶；HM 原语义是无界。MVP
  接受（HM 侧该值始终有数）。

## 出口结论

Phase 2 验收门达成：AS 引擎在 `ApplicationRuntime` 生产路径上可用且
HM 安全语义未被旁路。`generic_runtime.py` 保留为回退位，删除清单留
Phase 3（`implementation-plan.md` 已勾完对应项并附偏差记录）。

回归账：远端全量 1633 passed / 33 failed —— 逐条归因全部落在
环境或基线（用户级 Neo4j 占 7687 → memory/mcp/cli_streaming 段；
v19 缺兄弟仓 OpenHarness；web×10 基线 `metadata=` harness；
browser/v20 各 1 个 flake）。substrate 涉及目录定点 295/295 绿。
