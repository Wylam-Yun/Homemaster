# Phase 3 设计草案：记忆双轨 middleware 化

状态：草案（待独立评审）。对应 `agentscope-migration-design.md` §5.3
"双轨记忆注入边界（两轨并存已锁）"。

## 现状盘点（已核实，非猜测）

注入通道**已经在 assembler 内**，AS 路径自动继承：

- `ContextAssembler`（`agent/context.py`）把
  `frozen_memory_context.snapshot(session_id)` 拼进 system prompt parts
  （context.py:375-378），`bind_automatic_memory_context` /
  `bind_automatic_recalled_memories` 绑定的召回结果进 prelude
  （414-415、527-528）。`ContextAssemblyMiddleware._render` 用
  `prepared.system_prompt` 重建 messages[0]——所以**记忆注入已随
  assembler 到达 AS 引擎，无需 middleware 另开注入通道**。
- MindMemOS 召回在 app 层 `_automatic_recall`
  （`application/runtime.py:645`）于每次 `agent.run` 之前跑：generation
  fencing（`consume_recall`）、共享 deadline、unavailable/error 事件。
  compaction 后 `on_compaction → require_recall_after_compaction` 只
  重挂 flag，实际召回**拖到下一轮 run 开始**才发生。
- 写回：`memory_add`/`memory_link` 走普通工具链（已自动进 AS path
  经 `HomeToolAdapter`）；session-end finalizer 是 app-owned
  （`experience/session_finalization.py`），不属于引擎生命周期。

## 关键约束（review 轮①教训）

1. `ContextAssemblyMiddleware._render` 丢弃 AS `_system_prompt` 链的
   产物（H1 修复：assembler 是唯一上下文权威）。因此 AS
   `on_system_prompt` hook（AgenticMemoryMiddleware 的注入点）**在本
   架构下是死通道**——注入必须经 assembler 或在 `_render` 之后。
2. `inject_runtime_state=False`，HintBlock 不进 canonical history；
   记忆文本走 system prompt/prelude（现状语义），不改投 HintBlock。
3. generation fencing / deadline 共享 / `automatic_recall` 事件契约
   不能丢——middleware 内要能拿到 `AsRunHandle`（session/generation/
   deadline/event emit 都有）。

## 方案

### 候选 A（推荐）：middleware 拥有"时机"，assembler 保留"通道"

新增 `substrate/middleware_memory.py`：

```python
class MindMemOSMiddleware(MiddlewareBase):
    """接管 _automatic_recall 的触发时机与写回重挂；注入仍走
    assembler.bind_automatic_*（唯一上下文权威）。"""

    async def on_reply(self, agent, input_kwargs, next_handler):
        # 平移 _automatic_recall：consume_recall(generation) →
        # build_automatic_recall_query → mindmemos.search(共享 deadline)
        # → assembler.bind_automatic_memory_context/recalled_memories
        # → emit automatic_recall 事件（契约不变）
        # 完成后才 yield from next_handler —— recall 结果必须在首个
        # on_model_call 渲染前落地
        ...

    # compaction 后同 run 内重召回（新能力）：
    # 订阅 ContextAssemblyMiddleware 的 compaction 通知 → 立即重跑
    # recall + rebind，不等下一 turn（现行 flag 语义只能到下一 turn）
```

`FileMemoryMiddleware`：file_store 快照已由 `frozen_memory_context`
经 assembler 注入且每 prepare 重取——**无新逻辑需要 middleware 承载**；
只保留 `memory_add`/`memory_link` 工具（已在链上）。即 file 轨
"middleware 化"是空壳，YAGNI——文档里把"常驻身份层注入"的所有权
记在 assembler/`FrozenMemoryContextService`，不造空 middleware。

### 候选 B：middleware 拥有"通道"（on_model_call 里在 _render 后追加）

MindMemOS 召回结果不走 assembler，由 middleware 在
`on_model_call` 拿到 rendered messages 后追加一条 system Msg。

代价：两套注入通道并存（assembler + middleware），"assembler 是唯一
权威"被打破；屏障/压缩路径要多管一条注入源；快照看 assembled
context 的行为要重审计。**不推荐**——除非评审认为 assembler 不该再
聚合记忆职责。

### 候选 C：不做 middleware，保持 app 层召回

`_automatic_recall` 已是 engine-agnostic（两引擎共用同一段代码），
唯一损失是"同 run 内 compaction 后立即重召回"。可以小修：让
`on_compaction` 回调里直接重跑 recall（app 层回调已有全部上下文），
而不是只挂 flag。改动最小，middleware 层不增。

## 推荐与开放问题

推荐 **A**：拿到"同 run 重召回"的真实语义升级 + middleware 边界与
设计文档一致；注入通道不动摇（H1 纪律不破）。C 是诚实备选——如果
评审认定"同 run 重召回"价值低，C 以 1/5 工作量拿同等正确性。

开放问题（评审须裁决）：

1. `consume_recall/require_recall` flag 是 `SessionRuntime` 字段（持久
   化到快照 payload `require_recall`）。middleware 化后 flag 归属不
   变（仍 runtime-owned、generation-fenced）还是迁进 middleware 状态？
   ——迁移会改变快照 payload 形状，需 schema 裁决。
2. `on_reply` 在 AS 里是 async generator hook；召回 `await` 会推迟整个
   reply_stream 首事件——deadline 语义等价但取消语义要核实
   （SIGINT 在 recall await 中 → CancelledError 穿透链路）。
3. `automatic_recalled_memories`/`recalled_memories_by_tool_call_id`
   进了 `RunContext.deps`（application/runtime.py:507-509）供工具
   消费；middleware 化后 deps 的装配时序要重新对齐（deps 在
   `agent.run` 前构造，recall 结果那时还没有）。
4. finalizer（session-end 写回）维持在 app 层——中间件不持有
   session 生命周期，确认无争议。

## 验证门（若采纳 A）

- 定点测试：middleware 内 recall → assembler prelude 出现召回文本；
  compaction 触发同 run 重召回（断言第二次 search 调用发生且
  bind 刷新）；`consume_recall` 为 false 时不发 search。
- 契约等价：`automatic_recall` 事件 status/count 词表与 app 层一致；
  recall 中 SIGINT → cancelled，无 traceback 泄漏。
- 回归：application/test_as_runtime_wiring + memory 相关套件全绿。
