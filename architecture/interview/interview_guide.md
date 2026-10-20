# HomeMaster 技术面试讲解提纲

> 配套材料：`homemaster_architecture.html`（交互式架构图）、`architecture_analysis.md`（调研报告）。
> 本文所有断言均可追溯源码，引用格式 `file:line`（相对 `src/homemaster/` 前缀省略）。

---

## 1. 三十秒电梯陈述

> "HomeMaster 是一个 **LLM-first 的通用 Agent 运行时**。所有入口——CLI、Web Console、飞书 Gateway、ALFWorld benchmark、浏览器——都收敛到**同一个 `ApplicationRuntime`**；决策循环复用 vendored 的 **AgentScope ReAct 引擎**，但引擎之上套了一层我们自己的 **Substrate 外壳**，把 deadline、取消、上下文工程、权限、持久化的权威全部留在 HomeMaster 手里。一句话：**我们借引擎的循环，不借引擎的语义。**"

如果面试官只记住一件事，就是这句——它定义了整个项目的架构立场：Harness（宿主运行纪律）与 Engine（模型决策循环）解耦。

---

## 2. 3–5 分钟讲解稿（按图讲）

打开 `homemaster_architecture.html`，页面分三级：**Level 1 总体架构**（默认首页，9 节点）、**Level 2 核心子系统**（点击一级模块展开，7 个）、**Level 3 完整架构图**（46 节点源码级细节）。

最省事的方式：直接点顶部**「讲解模式」**（`#tour/0`），按 ①–⑩ 的顺序自动逐段高亮，每一步右侧栏给出对应的口播稿。下面的文字稿与讲解模式的步骤一一对应。

### ① 系统定位（约 30 秒）

> "HomeMaster 是一个 **LLM-first 的通用 Agent 运行时**。这张图讲一件事：用户给出任务后，系统经过哪几个环节把它完成。所有渠道——命令行、Web、飞书、ALFWorld 评测、浏览器——都收敛到**同一个 ApplicationRuntime**；决策循环用 vendored AgentScope ReAct，但生命周期、取消、权限、上下文的权威全部留在 HomeMaster 自己手里。"

### ② 主执行链路（约 40 秒）

> "主干五个环节：**入口适配器**把外部输入统一成 RunRequest；**应用运行时**管会话和整个生命周期；**决策引擎**跑 ReAct 循环决定调什么工具；**统一工具门**是所有工具执行的唯一通道——参数、权限、租约、超时都在这；最后**执行环境**把动作落到真实世界。"

### ③ 反馈闭环（约 20 秒）

> "注意顶部两条虚线：**观察/回执**从环境回到引擎做下一轮决策——这就是 ReAct 循环；任务结束后的**最终回答+事件流**经投影推回入口。系统不是一条直线，是一个带观测反馈的环。"

### ④ 横向支撑面（约 30 秒）

> "主干之下四个支撑面，正好对应最值得讲的设计：**状态与持久化**管快照与断点恢复；**记忆系统**负责召回注入和受治理的写入；**权限风控**给工具执行加了一道人可介入的门；**事件平面**把全链路结构化事件投影给外部观测。接下来逐个展开。"

### ⑤ Harness：借引擎不借语义（约 60 秒，Level 2）

> "这层是项目核心立场。`RunDriver` 每次 run 现装配依赖（工具视图/provider/工作副本/deadline）；`AsAgentRuntime` 把 HM 的权威注入 vendored ReAct，但 AS 原生的上下文改写、注入和许可模型全部显式关闭——**引擎可替换，宿主语义不变**。这就是 Harness 存在的理由：它不卖'让模型跑起来'，卖'每一步可观测、可审批、可恢复、可归因'。"

### ⑥ 决策→工具执行（约 40 秒，Level 2）

> "模型说要调工具，到真正执行之间有完整一道链：**协议围栏**核对调用名∈本轮供出集（越界整批拒绝）；**适配器**把 HM 权限语义穿透引擎边界；**统一执行器**做校验、权限判定、租约、deadline；结果按契约分类——尤其 `OUTCOME_UNKNOWN`：外部有没有被改动，类型上就分清楚。"

### ⑦ 权限与人工审批（约 40 秒，Level 2）

> "权限分两层：普通工具**即时判定**；物理工具走**精确授权**——审批精确到(环境,资源,动作)三元组，人审之后还要**重 prepare+绑定签名比对**防 TOCTOU，逐步 grant 原子领取。飞书刻意 fail-closed：异步消息满足不了精确授权语义，宁可拒绝。"

### ⑧ 记忆：召回·写入·治理（约 40 秒，Level 2）

> "记忆两条路：**读**——每个 generation 自动召回一次，冻结后注入 prelude，天然防重复注入；**写**——运行中 memory_add 与收尾 schema episode 都过证据门，写完**必须读回验证**（active+内容+图边+float32 容差）：receipt 不等于终态。底下 Neo4j、Qdrant、本地文件三层存储。"

### ⑨ 状态与恢复（约 40 秒，Level 2）

> "恢复靠一个 generation 计数器做的**全量写屏障**：每推进或取消都自增代际，旧代际的写直接抛错；快照走不可变 revision+CAS 指针，是唯一恢复源；session.json 只是观测旁路。取消一个 run 不会污染会话。"

### ⑩ 一次完整任务（约 30 秒，回 Level 1 收束）

> "最后串起来：请求从入口进来→运行时开新代际→装配时召回记忆、组装上下文→引擎循环决策→工具过权限门落到环境→回执回流直到 stop_condition（ALFWorld 里**只认环境 won=true**）→终局提交、快照落盘、记忆收尾固化→回答经事件投影推回入口。每个环节都能展开到源码。"

**收尾一句话**："这十个环节每一个都对应源码里的真实模块——右侧面板点开任意模块能看到 `file:line`，完整架构图里有全部 46 个模块和 63 条真实调用关系。"

---

## 3. 演示动线（按什么顺序点图）

| 顺序 | 视图 / 深链 | 讲什么 | 时长 |
|------|-------------|--------|------|
| 0 | `#tour/0` 讲解模式 | **推荐**：10 步自动高亮+口播稿，覆盖①–⑩全程 | 4–5min |
| 1 | `#l1` 总体架构 | 5 节点主链路 + 4 支撑面 + 2 条反馈弧 | 30s |
| 2 | `#sub/harness` | Harness/Runtime 内部：Session→RunDriver→AsAgentRuntime→ReAct | 60s |
| 3 | `#sub/tools` → `#sub/perm` | 工具执行单门链 → 物理审批展开 | 60s |
| 4 | `#sub/memory` → `#sub/state` | 记忆读写两路 → gen 栅栏+CAS 恢复 | 60s |
| 5 | `#full`（Level 3） | 46 节点源码级全图，面试官追问时再进 | 按需 |
| 6 | `#full/d-0` 15步演示 | ALFWorld 单 episode 真实调用链逐步高亮 | 60–90s |
| 7 | `#cards` 亮点与边界 | 收尾：5 个设计亮点 + 实现边界（诚实清单） | 30s |

**全程话术锚点**：每讲一段都说"这条链上每个框和每条线都有 `file:line`，右侧详情可以点开"——可追溯性是本项目最大的可信度来源。

---

## 4. Harness 的价值怎么讲（面试官最关心）

**问题**：框架自己写 Agent loop 不难，为什么值得做一层 Harness？
**答**（分三层递进）：

1. **生命周期权威**：ReAct 循环只是"每轮要不要调工具"的决策问题；而 run 的 deadline、取消、`session.json` 持久化、generation fencing、资源 scope、终态分类——这些是**宿主问题**。`AsAgentRuntime` 借引擎的循环，但把这些权威全部留在 HM：AS 的 context mutator/injection/ASK 显式关闭，`max_iters` 只作 +2 缓冲，真实上限在 `RunPolicy`（`substrate/runtime.py`）。
2. **可恢复性 = 全量写屏障**：单一 `generation` 计数覆盖 session 写、事件发布、revision commit——`cancel()` 自增 gen，旧 writer 再写就抛 `SessionGenerationError`（`application/session.py`）。断连重放不会把上一代的事件贴进新代际。
3. **外部终态 > 中间轨迹**：工具契约强制 `backend_attempted`/`outcome_unknown`；ALFWorld 每个物理 step 后 observe 回读、`won/done` 以环境为准、close 前做终态核对。**"模型说成功了"和"外部真的变了"在类型上就是两回事。**

一句话收束：**Harness 卖的不是"让模型跑起来"，而是"让跑起来的每一步都可观测、可审批、可恢复、可归因"。**

---

## 5. 最强 5 个技术亮点（每个按"问题→设计→实现→收益"）

### ① 借引擎不借语义（Substrate 外壳模式）
- **问题**：自研 ReAct 循环要重写大量胶水，但直接用 AS 会把 HM 的语义让渡给框架默认值。
- **设计**：`AsAgentRuntime` 包装 vendored `Agent`+`ReActConfig`，关闭 AS 全部隐式行为，HM 中间件在 tool_call 边界接管。
- **实现**：`substrate/runtime.py`、`substrate/toolkit.py`、`substrate/middleware_runtime.py`。
- **收益**：引擎可替换（换 AS 版本或换自研 loop 时，HM 的 deadline/取消/上下文/权限一字不改）。

### ② Generation Fencing = 全量写屏障
- **问题**：长任务中取消/重连后，旧 run 的迟到事件与写操作会污染新 session。
- **设计**：`session.generation` 单调递增；事件 emit、session commit、turn 追加、resource commit 全部比对当前 generation。
- **实现**：`application/session.py`。
- **收益**：取消语义诚实（旧 writer 异常退出 → honest terminal），重放不串代际，多入口共用同一 fencing。

### ③ `OUTCOME_UNKNOWN` 一等状态
- **问题**：工具调用"超时"时，外部世界可能已变也可能没变——传统框架要么当失败（可能重复副作用）要么当成功（可能谎报）。
- **设计**：`backend_attempted`（有没有真正发给外部）× certainty（能不能证明未变更）→ 三态结果；mutating + attempted + 无法证明未变更 = `outcome_unknown`，**禁止自动重试**。
- **实现**：`tools/contracts.py`、`tools/executor.py`。
- **收益**：把"未知"从异常变成可推理的契约字段——审批流、终态核对、审计都消费它。

### ④ 物理工具精确授权 + TOCTOU 双防线
- **问题**：物理动作（机械臂/环境动作）需要人工批准，但"批准时的参数"和"执行时的参数"可能被 TOCTOU 篡改。
- **设计**：审批绑定到 (环境, 资源类型, 资源ID, 动作) 的精确元组 + binding signature；执行前 prepare 绑定签名、linearize 内第三次 grant 复查、原子 claim。
- **实现**：`permissions/store.py`、`tools/executor.py`、`alfworld/permission_adapter.py`。
- **收益**：每步 linearize 防目标漂移；批准的边界 = 执行的边界。

### ⑤ 记忆写路径：receipt ≠ 终态
- **问题**：记忆写库"返回成功"≠ 真的可查——向量维度错、图边没建、稀疏索引漏写都可能静默发生。
- **设计**：所有写路径以读回为准：active raw + 内容相等 + 图边存在 + float32 容差逐维收尾；写前过 `tenant/session/run/turn` 证据门 + 不可变 `dedupe_key`。
- **实现**：`memory/mindmemos_runtime.py`、`memory/memory_tools.py`。
- **收益**：后台管线幂等可重入；记忆治理（防污染/防冲突）落在证据层而非 prompt 层。

---

## 6. 面试官可能的深入问题（预判 Q&A）

**Q1：为什么不直接用 AgentScope 的 runtime / session？**
A：因为 AS 的语义（context mutator、injection、ASK、自身迭代上限）与 HM 的宿主纪律冲突——HM 要的是"循环"这一个能力，而不是把 deadline/取消/上下文/权限也让渡出去。所以我们 vendor 了 AS 引擎但显式关闭其隐式行为（`substrate/runtime.py`），HM 的 `RunPolicy`/`RunDriver` 才是权威。

**Q2：generation fencing 具体防什么？**
A：三种串代际污染——① 旧 run 迟到的 event emit 进新 session 的事件流；② 旧 writer 把上一代的 `session.json` 覆盖掉新代际的快照；③ CAS revision 在取消后仍然 commit。`generation` 计数 + `SessionGenerationError` 把这三类统一成"写屏障"（`application/session.py`）。

**Q3：取消正在执行的物理动作怎么办？**
A：分两段——`backend_attempted=false`（还没发出去）→ 正常取消；`attempted=true` 且无法证明外部未变更 → `outcome_unknown`，**禁止自动重试**，由审批/审计/终态核对链路消化（`tools/executor.py`）。ALFWorld 侧每步后 observe 回读，`won/done` 以外部环境为准。

**Q4：记忆召回为什么每 generation 只一次？**
A：`AutomaticRecallService` 是 generation-scoped——同一 generation 内重复召回会注入重复 prelude 浪费 token 且可能引入不一致；compaction 后 rearm 才允许再次召回。这是"上下文工程"的纪律，不是性能优化（`memory/automatic_recall.py`）。

**Q5：记忆写路径 `add_flat` vs `add_schema_episode` 区别？**
A：`add_flat` 是 run 中的直写（agent 主动 `memory_add`），走证据门 + 读回；`add_schema_episode` 是 session 收尾时 `SessionFinalizer` 从轨迹抽取 schema episode 再写入。两条都过同一个 `MemoryEvidenceLedger`；**MindMemOS 原生 `add()`/`add_record()` 是无调用者的死路径**，不能当作架构能力讲。

**Q6：审批的 binding signature 防什么？**
A：防 TOCTOU——批准时看到的参数（比如"去厨房"）和执行时实际参数必须 byte-level 一致；linearize 内复查 grant 防"批准后被并发改掉目标"。`prepare` 时绑定签名、`linearize` 内第三次 grant 复查（`permissions/store.py` + `tools/executor.py`）。

**Q7：ALFWorld worker 为什么要独立 venv 子进程？**
A：AI2-THOR/Torch 的依赖树与主运行时不兼容（版本地狱）。解法是**物理隔离**：worker 是 `python -m workers.alfworld_worker` 子进程，独立 `.runtime/alfworld-venv`，只装 ALFWorld/AI2-THOR，**不允许 import homemaster/MindMemOS**；两边用版本化 NDJSON stdin/stdout 通话（`alfworld/worker_client.py`）。收益：worker 崩了不影响主进程，依赖升级互不干扰。

**Q8：事件投影为什么是"信任边界"？**
A：内部 `RuntimeEvent` 含全部字段（含敏感 payload）；公开给 Web/CLI/Feishu 前必须过 `Projection`——字段 allowlist + generation 核对 + artifact 用 opaque ref（不泄漏内部路径）+ tenant/ownership 过滤。投影器挂了只影响展示，JSONL sink 挂了不影响业务——**旁路永远 fail-open，主路永远 fail-closed**（`events/public_projection.py`）。

**Q9：`RunPolicy.max_turns` 存在但没被消费，怎么说？**
A：诚实承认——字段定义了但当前 `RunDriver`/`AsAgentRuntime` 没读它；真实上限在 AS 的 `max_iters`（HM 侧 +2 缓冲）和 deadline。这是一个"API 先于实现"的已知边界，属于调研报告里明确列出的实现边界，不是 bug 但值得指出。

**Q10：浏览器那条路 `max_tool_iterations=None` 什么意思？**
A：browser profile 的装配把 AS 迭代上限设为 None，等于**禁用了正常的 tool-call 轮次上限**——这是已记录的行为差异，意味着 browser 路径只受 deadline/取消约束。讲的时候要主动指出这不是疏忽而是设计选择，但也是边界之一。

**Q11：Feishu 审批为什么不能交互？**
A：当前 `PermissionChecker` 对 Feishu 通道是 **fail-closed**——不是没接，而是刻意不接：飞书消息的异步性和确认语义不满足 binding signature 的精确授权要求，所以宁可拒绝也不降级成"发个消息等回复"的弱确认。

**Q12：如果让你加一个真实机器人后端，改哪里？**
A：只动三处——① `domain/home_backend.py` 加一个 `RobotBackend` 实现（复用 `HomeWorldBackend` 的接口）；② `domain/tools.py` 的 `robot_*` 工具不动（它们只调 backend）；③ `alfworld/permission_adapter.py` 的审批映射照抄。**不动** Harness、不动工具门、不动事件流——这就是分层解耦的回报。

---

## 7. 不该说什么（诚实清单）

面试中主动声明这些边界，反而加分——证明你读的是代码不是 README：

| 边界 | 事实 | 出处 |
|------|------|------|
| 没有 `AgentRuntime` 类 | 活跃运行时是 `AsAgentRuntime`（`substrate/runtime.py`）；`agent/generic_runtime.py` **不存在**——旧架构文档有误 | `substrate/runtime.py` |
| `RunPolicy.max_turns` 未消费 | 字段存在，`RunDriver` 不读 | `application/run_driver.py` |
| MindMemOS 原生 `add`/`add_record` | 无调用者；活路径是 `add_flat` + `add_schema_episode` | `memory/mindmemos_runtime.py` |
| AgentScope 长期记忆中间件 | vendored 存在但**未接线**到 HM | `substrate/middleware_runtime.py` |
| `context_projection.py` | 当前是 no-op pass-through | `context_projection.py` |
| Reranking | 自动召回 `rerank=False`、`search_pipeline="vanilla"` | `memory/automatic_recall.py` |
| 非物理工具确认 | 当前 policy 未启用 | `permissions/policy.py` |
| 飞书交互审批 | fail-closed（刻意） | `permissions/policy.py` |
| 文件系统 jail | 无 chroot/sandbox，靠 policy+workdir | `tools/executor.py` |
| 真实机器人/VLA/VLM | 未接，HomeWorld 是 file-backed 模拟 | `domain/home_backend.py` |

---

## 8. 一页速查表（面试前最后 30 秒）

```
L1 主链路   任务入口 → 应用运行时 → 决策引擎 → 统一工具门 → 执行环境
           （顶部 2 条虚线：观察回流 + 最终回答回流 = ReAct 闭环）
L1 支撑面   状态持久化(gen栅栏·CAS) · 记忆(召回·证据门写) · 权限(审批·租约) · 事件(投影)
L2 展开     Harness · 工具执行 · 权限HITL · 记忆 · 状态 · 环境 · 事件（点击 L1 模块进入）
L3 细节     46 节点源码级全图 + file:line 追溯（追问时再进）

引擎立场    vendored AgentScope ReAct 借循环不借语义——AS mutator/injection/ASK 全关
工具立场    唯一工具门 ToolExecutor：校验→权限→租约→deadline→分类（无第二路径）
安全立场    物理精确授权(环境,资源,动作)+签名复查防TOCTOU；飞书刻意fail-closed
记忆立场    receipt≠终态：写完必须读回验证(active+内容+图边+float32)
状态立场    generation=全量写屏障；revisions+CAS=唯一恢复源；session.json仅观测

5 大亮点   借引擎不借语义 · generation fencing · outcome_unknown
           物理精确授权+TOCTOU防线 · 记忆写=读回终态
1 句话     Harness 卖的不是"让模型跑起来"，是"每一步可观测/审批/恢复/归因"
```
