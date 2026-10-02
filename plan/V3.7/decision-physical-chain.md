# 裁决③：物理执行链路映射点表

状态：已裁决（Phase 0 先决文档）
问题：HomeMaster 的物理许可链（prepare→store→confirm→revalidate→claim→execute→
observe→finish）落到 AgentScope 的哪些 hook/生命周期上，哪些不映射、留在工具体内。

依据：两侧源码实测（`src/homemaster/tools/executor.py`、
`src/agentscope/agent/_agent.py::_execute_tool_call`/`_acting_impl`、
`middleware/_base.py`、`permission/`、`message/_block.py` 状态机）。

## 0. 两条链路的真实形状

**AgentScope `_execute_tool_call`（实测顺序）：**

```
check_tool_available → json_loads_with_repair(input) → jsonschema.validate
  → _check_permission ─ middleware chain ─→ engine.check_permission
  → ALLOW ─外部?─→ RequireExternalExecutionEvent(SUBMITTED)
        └─本地─→ _acting → on_acting middleware chain → toolkit.call_tool
                 → ToolChunk* → ToolResultEndEvent → context write
    ASK/PASSTHROUGH → RequireUserConfirmEvent(ASKING) → reply 挂起
                     → UserConfirmResultEvent 恢复 → 重跑本函数
    DENY → ToolResultBlock(DENIED) → 继续下一个 call
```

**HomeMaster `ToolExecutor._execute_physical`（实测顺序）：**

```
adapter.prepare → store.create_request → evaluate_physical
  → missing? mark_awaiting_approval → confirmation_handler.confirm(逐item)
  → re-prepare + binding_signature 比对 (TargetChanged)
  → matching_grants → submit(ApprovalSubmission protocol-v2)
  → per step: PhysicalGateOwner.linearize → grant recheck → claim_step
    → resource lease → adapter.execute [backend_started]
    → _observe_best_effort → store.finish_step → 结果映射
  → finally: release_adapter
```

## 1. 核心裁决：物理链整体留在工具体内

**HM 物理链（§0 下）原样保留为工具函数体**，挂在 substrate 的 `ToolBase` 包装里。
AS 层把它当成一个普通本地工具走完 ALLOW→on_acting→call_tool 路径。

理由（为什么不映射到 AS 的 ASK/SUBMITTED 状态机）：

| AS 能力 | HM 需求 | 差距 |
|---|---|---|
| ASK = 单次 per-call 挂起，`UserConfirmResultEvent` 整批确认 | HM protocol-v2 **逐项** ItemDecision（allow_once/reject/…）+ missing_item_ids 驱动 | 粒度不符，逐项决策卡无对应物 |
| 挂起 = reply 终结、等外部事件恢复 | HM `confirmation_handler.confirm()` 在 run 内 async 等待，CLI/web 通道自行把提示桥接出去 | 用 AS 挂起会改变 run 的生命周期语义，session 恢复路径全部要重写 |
| `ToolCallBlock.state` 五态 | HM `PermissionStore`：request/approval/step claim/revision/finish_step，全部有持久化事务 | AS 状态机是内存态，SQLite claim/revoke 才是 HM 的线性化点 |
| SUBMITTED→ExternalExecutionResultEvent | HM adapter.execute 内嵌 lease+claim+observe+attempt-budget，per-step 而非 per-call | 切开会把租约/claim 边界撕到错误位置 |

**推论**：AS 侧 `ToolCallBlock.state` 对物理工具只走 `PENDING→ALLOWED→FINISHED`；
ASKING/SUBMITTED 语义由 HM PermissionStore 表达。resume 场景（进程重启后有待决
approval）由 store 的 `mark_awaiting_approval` 持久态支撑，不依赖 AS 内存状态机。

## 2. HM 步骤 → AS 挂点逐项映射

| HM 步骤 | AS 挂点 | 裁决 |
|---|---|---|
| `registry.get` + `input_model.model_validate` | AS `check_tool_available` + `jsonschema.validate` | 双层并存：AS 先做粗校验，HM input_model 在工具体内再验（见 §5 偏差1） |
| `evaluate_tool`（非物理 allow/deny/confirm） | `on_check_permission` middleware | **映射**。HM PermissionChecker 包成 middleware，返回 `ALLOW`/`DENY`/`ASK` |
| `requires_confirmation`（非物理） | **裁决：工具体内阻塞确认**，不映射 RequireUserConfirmEvent | 保 run 内语义（同 §1 推论）。AS ASK 事件留给 Phase 2 统一通道评估 |
| 物理链全部（§0 下） | 工具体 = `_execute_physical` 包成 `ToolBase.call` | 不映射到任何 AS hook；`on_check_permission` 对物理工具返回 ALLOW（HM gate 是权威） |
| `resource lease` | 留在工具体内 step 级 | 不映射。AS 无租约概念 |
| `backend_started`→`outcome_unknown` 分类 | 工具体内 try/except 原样 | 不映射。`ToolExecutionResult.backend_attempted`→`ToolResultBlock.metadata["hm"]["data"]` |
| `concurrency_policy` | AS `_ToolCallBatch`：`parallel`→`is_concurrency_safe=True`；`serialized`/`resource_key`→`False` | **有损映射**：resource_key 分组降级为全串行。吞吐损失记录，不丢正确性（同 key 仍串行）。真资源分组是 Phase 2 优化项 |
| `execute_many` 分组 + cancel 传播 | AS sequential/concurrent 批次 | 映射。AS 并发批有等价取消语义（cancel→INTERRUPTED 冲刷），但**无 resource_key 分组**；取消时 `uncancel()` 后靠 INTERRUPTED 事件判断——HM 取消纪律（`backend_attempted` 复核）在工具体内兜底 |
| deadline（`context.deadline`） | 无对应物 | 留在 RunScope 桥接对象上，工具体内 `asyncio.wait_for` 照旧 |

## 3. HM 状态 → AS ToolResultState 映射表

`ToolResponse.state` 只有 4 值（SUCCESS/ERROR/INTERRUPTED/DENIED），HM `ToolExecutionStatus` 有 7 值。
**裁决：AS state 是粗投影，HM 精确状态永远走 `metadata["hm"]["data"]["status"]`**。

| HM status | AS state | 备注 |
|---|---|---|
| `success`/`ok`/`no_effect` | `SUCCESS` | — |
| `permission_denied`/`approval_*`/`target_changed`/`target_unresolved` | `DENIED` | 目标漂移归并到 denied（未执行）；精确状态在 data |
| `cancelled`/`execution_cancelled`（backend 未动） | `INTERRUPTED` | — |
| `outcome_unknown`（含 backend_attempted 的 timeout/cancel/exception） | `ERROR` | **不得**标 INTERRUPTED——INTERRUPTED 暗示"未发生"，与 outcome_unknown 语义相反 |
| `tool_error`/`invalid_tool_arguments`/`unknown_tool`/`resource_key_*`/`deadline_exceeded` | `ERROR` | — |
| `verification_pending` | `SUCCESS` | 工具已执行完，等验证；模型可见文本解释待验状态 |
| `execution_failed`（attempt budget 耗尽） | `ERROR` | — |

模型可见 output 永远只装 `ToolExecutionResult.text` + images；全部 machine 字段
（status/error_code/backend_attempted/data/verification/terminal）进
`ToolResultBlock.metadata["hm"]`，与裁决①§4 一致。

## 4. 全故障路径对照表（spike 必须逐条黑盒验证）

| 故障注入点 | HM 期望终态 | AS 层落点 | spike 断言 |
|---|---|---|---|
| permission DENY | `permission_denied`，无 backend 调用 | `on_check_permission`→DENY | adapter.execute 调用数=0；result state=DENIED；data.status 精确 |
| 用户确认超时/取消 | `ApprovalCancelled`→`permission_denied` | 工具体内 | store request 终态 cancelled；无 step claim |
| 确认后 binding 变 | `target_changed`，request cancel | 工具体内 re-prepare | store.cancel 被调；release_adapter 被调 |
| grant 在 step 前撤销 | `permission_denied` | 工具体内 linearize 内 | claim_step 未调 |
| backend 已开始+Cancel | `outcome_unknown` + backend_attempted | 工具体内（AS 层会把 cancel 收成 INTERRUPTED chunk） | **result.data.status==outcome_unknown 且 backend_attempted==True**，即使 AS state=INTERRUPTED |
| backend timeout 后 observe 到成功 | `success`（verified after timeout） | 工具体内 | observe 证据进 finish_step |
| backend timeout observe 不到 | `outcome_unknown` | 工具体内 | finish_step(outcome=unknown) 已写；**无自动重试** |
| adapter.execute 异常（backend 已动） | `outcome_unknown` | 工具体内 | 同上 |
| adapter.execute 异常（backend 未动） | `tool_error` | 工具体内 | backend_attempted==False |
| storage 不可用 | `permission_storage_unavailable` | 工具体内 | fail closed |
| Ctrl+C/外部取消 | AS 批次取消→INTERRUPTED 冲刷 | AS `_execute_concurrent_tool_calls` | in-flight 工具体内的 CancelledError→outcome_unknown 路径仍被走到 |

## 5. 已识别偏差（进入风险登记）

| # | 偏差 | 处置 |
|---|---|---|
| 1 | AS 用 `_json_loads_with_repair` **静默修复**畸形 JSON input，HM 原语义是 `invalid_tool_arguments` 拒绝 | 真差异。修复产物会进入 HM input_model 再验——能挡类型错，但"模型本来发了垃圾、被修成合法"的语义丢失。裁决：接受为文档化偏差，substrate 工具体内 HM input_model 仍是 authority；若 spike 发现 provider 实测发畸形 JSON 依赖 repair，再升级处理 |
| 2 | `PermissionDecision.updated_input` 是**死字段**（AS 不消费）；HM 原本就没有输入改写语义 | 无冲突。HM 也从不改模型入参 |
| 3 | `kept_rules` 批内去重只对 concurrent 批生效；sequential 批遇 ASK 即停 | HM 不用 ASK 路径（§2），无影响 |
| 4 | AS 并发批"一个失败不取消其余" + ExceptionGroup 汇聚 | 与 HM `execute_many` 的 `return_exceptions` 语义等价；HM resource_key 语义降级见 §2 |
| 5 | `has_awaiting_tool_calls` 只检查 tail assistant Msg；HM resume 语义依赖 ASKING/SUBMITTED 状态持久在 context 里 | HM 物理确认不产 ASKING 状态（§1 裁决），resume 后无悬挂 ASKING；SUBMITTED 同理不产生。**这意味着 AS 自带的跨进程确认恢复在 HM 模式下为空集**——记录为已知，HM approval resume 走 PermissionStore |

## 6. Spike 验收门（Phase 0 §关灯切片）

黑盒断言，不许 mock 掉 PermissionStore/adapter：

1. §4 表逐行跑通（每一行 = 一个独立测试用例 + 逐实例断言）
2. `ToolResultBlock.metadata["hm"]["data"]["backend_attempted"]` 在每个失败路径上精确为真/假
3. 序列化 envelope 上断言 machine 字段存活（CLAUDE.md：`backend_attempted` 纪律）
4. 消息序列顺序：`[user, assistant(tool_call), assistant(tool_result+终态)]`——
   **不**出现 review 抓到的倒置序 `[tool_call, observe_msg, tool_result]`
5. 观察证据走 `ToolResponse.content` 追加，不走 `agent.observe()`（裁决①的时序约束）
