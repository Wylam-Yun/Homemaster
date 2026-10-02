# Phase 3 设计：GoalPipeline / TeamPipeline 采纳面 —— 定稿

状态：**已定稿**（用户裁决候选 A，独立评审 45112b79 细化为 Tier-1）。
对应 `implementation-plan.md` Phase-3 任务 ①。

## 0. 裁决结论

用户已确认：存在合法的"无环境谓词"goal 判定场景（开放式任务/SOP 验收），
按候选 A 接——但**不直接用 vendored `GoalPipeline` 类**。

最终形态（Tier-1，评审修订）：

- **verdict 通道**：`AsLLMClient.complete_json()`（无工具、无 Agent
  生命周期），产出 HM 自有 `VerdictResult`（typed），不走
  `GoalPipeline.reply_stream`。
- **verifier 协议**：HM-owned `VerdictVerifier` Protocol + typed
  `VerdictResult{result: pass|fail|impossible, message,
  verdict_provenance}`，挂在 `benchmarking/` 层作为可选 verifier 工具
  （stop_condition 实现面），不进 runtime。
- **provenance**：`verdict_provenance ∈ {"env","typed","llm"}` 必须落到
  artifact/result——env/typed 与 LLM 判定在审计面上可区分。
- **纪律红线**：ALFWorld/任何 env-authoritative benchmark 的终态只认
  环境 `won=true`；LLM verdict **永不**映射成 `won`。

## 1. 为什么不接 vendored GoalPipeline（评审核实）

`src/agentscope/pipeline/_goal_pipeline.py` 的结构性缺陷（全部已对源码
核实，VERIFIED）：

1. **两个无界 while**：`while execution_report is None`（L177，executor
   输出非结构化时永久悬挂）与 `while final_msg is None`（外层重试）—
   `max_iters` 只在 verifier `fail` 时递增，这两层循环完全绕过它。
2. **`max_retries` 是死参数**：存而未用（全文件无第二处引用）。
3. **取消语义吞没**：`reply_stream` 无 `CancelledError` 处理；内部走
   `Agent._reply_impl` —— 该实现捕获取消并按
   `interruption_raise_cancelled_error=False` 默认值转成
   "interrupted reply"，pipeline 外层会把取消当成"继续重试"的信号，
   与 HM 的取消传播纪律正面冲突（取消必须穿透而非变成非终态回复）。
4. **executor/verifier 生命周期与 HM 双层嵌套**：turn/generation/
   快照/deadline 语义要被 pipeline 的 generator 边界穿透——六相取消
   与 generation fence 难以保持，治理成本远超收益。

结论：GoalPipeline 的"executor loop + verifier retry"整体接管模式与
HM 的 runtime 主权根本冲突；只有它的**判定 schema 形状**
（`pass|fail|impossible + message`）值得借用——已移植为
`VerdictResult`。

## 2. 采纳实现（Tier-1）

### 2.1 `VerdictVerifier` 协议（`benchmarking/` 层）

```python
class VerdictResult(BaseModel):
    result: Literal["pass", "fail", "impossible"]
    message: str = ""
    verdict_provenance: Literal["env", "typed", "llm"] = "llm"

class VerdictVerifier(Protocol):
    async def verdict(
        self, *, goal: str, transcript: Sequence[Message],
        evidence: Mapping[str, Any], deadline: Deadline | None,
    ) -> VerdictResult: ...
```

- **env verifier**：读环境终态（如 ALFWorld `won`）→ provenance="env"。
- **typed verifier**：如 `WriteTerminalVerifier`（receipt 核对）→
  provenance="typed"。
- **LLM verifier**：`LLMVerdictVerifier(client: AsLLMClient)` —
  `complete_json(prompt, temperature=0.0)`，prompt 内嵌 schema 约束，
  返回 `LLMJsonResponse.json_payload` 校验成 `VerdictResult`；
  provenance 强制 "llm"。

### 2.2 LLM verdict 的生命周期纪律

- **预算**：单次 `complete_json` 一个绝对 deadline（默认 30s，可注入），
  至多一次重试；超限/解析失败 → `VerdictResult(result="impossible",
  message=...)`，**绝不悬挂**。
- **取消**：`asyncio.CancelledError` 直接向上抛——verifier 不转成
  impossible/fail（取消语义归 run 层，与 GoalPipeline 的吞没相反）。
- **无工具面**：verdict 路径不挂任何 tool / toolkit / permission —
  `complete_json` 是纯文本调用，`on_check_permission` 零调用是
  黑盒断言之一。

### 2.3 边界

- LLM verdict 只作为 `run_policy.stop_condition` 的**一种实现**或
  benchmark harness 的 sidecar——永远不进入 runtime、不改
  `won` 的 env-authoritative 语义。
- ALFWorld runner：`stop_condition` 继续读 `state.won`；若接
  LLMVerdictVerifier，它只能补充"模型主观以为完成但环境未判胜"的
  诊断信息，不能替代 `won`。

## 3. TeamPipeline：推迟（同原候选 C）

"管家 leader + 设备/记忆/日程 member"依赖 member session/generation
隔离、leader 分派审计、取消扇出、成员上下文 reset——每项都是独立设计。
`TeamPipeline` 无 `max_iters`/`max_retries` 顶层参数（VERIFIED），其
`while True` 主循环的预算责任天然外置——与 HM 的 deadline 纪律可接
但无现成接缝。Phase 3 不阻塞于其落地；独立提案时重审。

## 4. 验收门

- [ ] `LLMVerdictVerifier` 用 `AsLLMClient.complete_json` 产出
  typed `VerdictResult`；schema 缺字段/超 JSON 修复范围 → impossible。
- [ ] provenance：env/typed/llm 三值落 artifact 字段，审计可查。
- [ ] deadline：注入过期 deadline → 返回 impossible 且总耗时 < 预算；
  `CancelledError` 原样上抛（不成 impossible）。
- [ ] 无工具断言：verdict 路径 `on_check_permission` 调用计数为 0。
- [ ] ALFWorld 回归：最终 `won` 来源仍只有 `adapter.current_state.won`；
  LLM verdict 不进入 success 判定路径。
- [ ] 事件走 `RuntimeEvent` sink（`benchmark.verdict` 类型）+ public
  projection allowlist 核对。
