# HomeMaster 浏览器操作说明与交接

> 适用代码版本：`c6c89ccd7e8dcae4677d66cd6a02f123fb0c3757`  
> 远端仓库：<https://github.com/Wylam-Yun/Homemaster>  
> 范围：只说明 Web Console 驱动的通用浏览器操作；不说明机器人、ALFWorld 和飞书业务。

配套可视化图：[browser-operation-handoff.html](browser-operation-handoff.html)。交接时只需要阅读本说明文档和这份 HTML，不需要了解图的生成工具。

## 0. 这份文档应该怎么读

这不是“文件目录说明”，而是按一个真实任务解释系统：用户在网页输入一句话，模型判断要调用什么浏览器工具，工具操作真实网页，系统再把结果显示回网页。

阅读时只需要抓住三件事：

1. **控制面**：网页、Web API、Runtime、模型和工具 Registry，负责接收意图、做决策和组织调用。
2. **执行面**：Playwright session、Chromium、目标网站，负责真正执行动作并产生页面状态。
3. **证据面**：DOM/URL 读回、事件、trace、截图和 artifact，负责证明动作是否真的生效。

不要把模型的文字回答当成网页成功证明；成功必须来自执行面读回的外部状态。

## 0.1 术语表：每个东西到底是什么

| 名称 | 白话含义 | 在逻辑中的作用 |
| --- | --- | --- |
| Web Console | 浏览器里看到的 React 页面 | 负责输入任务、显示过程、发审批，不直接碰目标网页 |
| FastAPI Web Adapter | Python 的 HTTP/WebSocket 门面 | 把网页请求转换成 `RunRequest`，把内部事件转换成网页事件 |
| Session | 一段可恢复的对话 | 保存历史消息；同一个 session 可以连续提交多次任务 |
| Run | Session 中的一次执行轮次 | 一次用户消息对应一个 run；浏览器资源按 run 隔离 |
| AgentRuntime | 模型循环控制器 | 反复执行“请求模型 -> 得到 tool call -> 执行工具 -> 把结果给模型” |
| Provider | LLM 连接器 | 只负责把上下文和工具 schema 发给模型、接收模型输出 |
| ToolRegistry | 当前可用工具清单 | 告诉模型有哪些工具、参数是什么，并映射到执行器 |
| ToolExecutor | 工具总闸门 | 做 schema 校验、权限判断、资源串行化，然后调用具体工具 |
| Browser tool | 一个有明确输入输出的浏览器动作 | 如 `browser_navigate`、`browser_inspect`、`browser_fill`、`browser_click` |
| BrowserSession | 一个 run 独占的浏览器控制对象 | 持有 Playwright page/context，统一做目标解析、动作和读回 |
| Page / Tab | Chromium 中的页面/标签页 | `tab_ref` 用来避免操作到错误标签页 |
| Frame | 页面中的 iframe 文档 | `frame_ref` 用来限定元素属于哪个 iframe |
| Snapshot | 某一时刻的 DOM/AX 元素快照 | 给模型看页面结构，并登记可复用的目标引用 |
| `target_ref` | 快照中某个元素的稳定引用 | 动作时优先使用；页面重绘或导航后会被重新验证 |
| Generation | 页面导航/状态代次 | 防止把旧页面的元素引用用于新页面 |
| Actionability | 元素是否真的可操作 | 检查 visible、enabled、obscured、editable、readonly 等状态 |
| Receipt | 一次动作的结构化回执 | 记录期望值、实际读回、URL/DOM 变化、匹配级别等 |
| EventBus | 内部 Runtime 事件总线 | 让执行逻辑与网页显示解耦；同一事件可以被多个 sink 消费 |
| WebEventProjection | 内部事件到网页事件的投影器 | 只挑允许字段并补齐 request/session/run 关联 |
| ArtifactStore | 截图、下载等二进制产物存储 | 网页只拿受权限保护的 opaque handle，不直接暴露路径 |

## 0.2 总体思路：为什么要这样分

浏览器自动化最容易出问题的地方不是“能不能点一下”，而是“点的是不是正确元素、页面是不是真的变了、变更能不能追溯”。所以项目把逻辑拆成四步：

```text
表达意图：用户文字 + 模型 tool call
    -> 约束调用：schema + permission + run/resource ownership
    -> 执行动作：semantic target -> Playwright -> 页面
    -> 证明结果：DOM/URL/readback + RuntimeEvent + artifact
```

这四步分别由不同层负责，任何一步失败都要保留明确错误，不能由下一层“猜着继续”。

## 0.3 先看这几个对象，才能看懂调用链

浏览器模式启动后，真正长期存在的对象关系如下。括号内是所有权，不是继承关系：

```text
FastAPI app
  ├─ application: BrowserApplication -> ApplicationRuntime
  ├─ run_registry: WebRunRegistry
  ├─ event_hub: WebEventHub
  │    └─ projection: WebEventProjection
  └─ confirmation_handler: WebConfirmationHandler

ApplicationRuntime（进程级）
  ├─ SessionManager（持久会话）
  ├─ EventBus（内部事件）
  ├─ base ToolRegistry（通用工具）
  ├─ application_services（FileMemoryStore、MindMemOS、EvidenceLedger）
  └─ provider_factory / context_assembler_factory

一次 ApplicationRuntime.run（run 级）
  ├─ SessionRuntime（session generation 的锁定视图）
  ├─ PlaywrightBrowserSession（唯一浏览器 owner）
  ├─ browser run ToolRegistry（base registry 替换为 browser tools）
  ├─ ApplicationToolExecutor（把 tool result 接回 Runtime）
  ├─ FrozenMemoryContext + automatic recall（本 run 的上下文输入）
  ├─ Provider / ContextAssembler / AgentRuntime
  └─ RunResourceScope（统一释放 browser/provider 等资源）
```

最容易误判的地方是：`BrowserApplication` 不是第二个 Runtime，它只是一个 wrapper；`BrowserGatewayApplication` 只是兼容别名；真正的 run 循环仍由 `ApplicationRuntime` 执行。

## 1. 先给结论：有几层

按一次“网页输入 -> 记忆参与决策 -> 页面发生变化 -> 网页看到结果”的主执行链，项目可交接为 **8 层**：

| 层 | 名称 | 主要代码 | 解决的问题 |
| --- | --- | --- | --- |
| 1 | 浏览器前端展示层 | `web/src/` | 会话列表、输入框、工具卡片、审批框、实时状态展示 |
| 2 | Web 接入/协议层 | `src/homemaster/web/` | HTTP API、session WebSocket、请求去重、断线与关闭 |
| 3 | 应用运行时编排层 | `src/homemaster/application/` | `RunRequest`、Session、run generation、取消、资源生命周期 |
| 4 | 记忆与上下文层 | `src/homemaster/memory/`、`agent/context.py` | 文件记忆快照、MindMemOS 召回/写入、证据绑定、上下文注入 |
| 5 | Agent 决策层 | `src/homemaster/agent/` + `providers/` | 组装上下文，调用 LLM，解析回答和 typed tool call，循环直到终态 |
| 6 | 工具治理与浏览器工具层 | `src/homemaster/tools/`、`tools/browser/` | schema 校验、权限门、资源键串行化、浏览器操作分发、结果/证据封装 |
| 7 | 浏览器会话适配层 | `src/homemaster/browser/` | 每 run 一个 Playwright 会话，语义目标解析、frame/tab、actionability、DOM 读回 |
| 8 | 外部执行层 | Chromium + 目标网站 | 真正改变 DOM、URL、表单、弹窗、下载、网络和截图状态 |

## 1.1 每一层的具体含义和逻辑

### 第 1 层：React Web Console

**是什么**：用户操作的页面，代码在 `web/src/App.tsx`、`web/src/api/` 和 `web/src/state/`。

**为什么存在**：把复杂的异步 run 变成可观察的对话界面。它要处理连接状态、历史 session、thinking 流、工具卡片、审批框、停止按钮和 artifact 预览。

**输入/输出**：输入是用户文字、Stop、Approve/Reject；输出是 HTTP 请求和一个当前 session 的 WebSocket 订阅。它不保存浏览器 page，也不执行 click/fill。

**具体逻辑**：页面先 `listSessions/createSession`，再加载 history，接着建立 `/api/events?session_id=...`；连接状态为 `connected` 才允许提交消息。事件按 `tool_call_id` 合并到对应工具卡片，断线则重新拉 history 并退避重连。

### 第 2 层：FastAPI Web 接入层

**是什么**：`src/homemaster/web/app.py` 和 `serve.py`，是浏览器与 Python 应用的协议边界。

**为什么存在**：浏览器不应该直接知道 Runtime 的内部对象；这一层负责鉴别 session、校验请求、控制并发和关闭顺序。

**输入/输出**：

- `POST /api/sessions` -> 返回 `session_id`；
- `POST /api/sessions/{id}/messages` -> 登记 `request_id`，返回 `202 accepted`；
- `POST /api/sessions/{id}/cancel` -> 请求取消；
- `POST /api/approvals/{approval_id}` -> 返回审批结果；
- `GET /api/events` -> 持续发送 WebEvent；
- `GET /api/artifacts/{handle}` -> 按 tenant/session/run 读取二进制。

**具体逻辑**：消息接口先检查 session 存在、request 是否重复、该 session 是否已有活动 run、WebSocket 是否已经订阅；通过后才把 `RunRequest` 交给 Runtime。WebSocket 断开时会移除订阅，并让未完成审批 fail closed。

### 第 3 层：ApplicationRuntime

**是什么**：`src/homemaster/application/runtime.py`，整个应用的生命周期 owner。

**为什么存在**：保证一次 run 使用一致的 session generation、取消令牌、provider、工具 Registry 和资源范围，避免不同请求互相污染。

**输入/输出**：输入是 `RunRequest(text, session_id, permission_subject, dependencies)`；输出是 `RunResult`，并同步产生内部 `RuntimeEvent`。

**具体逻辑**：

1. 打开或恢复 Session，分配 `run_id` 和 generation。
2. 发现 `browser_session_factory` 后，在 `_run_tool_view()` 内创建一个 run-scoped `PlaywrightBrowserSession`。
3. 用该 session 生成本 run 的 browser tool Registry 和 ToolExecutor。
4. 创建 provider、ContextAssembler、AgentRuntime，执行模型循环。
5. run 结束时提交 session 快照，关闭浏览器 session 和其他 run 资源。

### 第 4 层：记忆与上下文层

**是什么**：由 `src/homemaster/memory/` 和 `src/homemaster/agent/context.py` 共同组成的“长期知识 + 当前上下文”层。它不是简单把历史消息拼回 prompt，而是决定哪些记忆可以进入当前 run、什么时候进入、能否作为写入依据，以及页面操作结果如何沉淀为下一次可检索的经验。

**为什么是独立层**：浏览器操作通常不是孤立的一次 click/fill。模型可能需要知道用户偏好、上次使用的网页流程、某个页面字段的历史含义；反过来，本次操作的真实结果也可能需要写入长期记忆。记忆层负责这条闭环，但不拥有浏览器 page，也不授予浏览器权限。

**这一层实际包含什么**：

- `FileMemoryStore`：维护本地 `SOUL.md`、`USER.md`、`MEMORY.md`，负责加锁、威胁扫描、原子写入和写后回读。
- `FrozenMemoryContextService`：第一次组装某个 session 时读取上述文件，生成不可变的 Assistant Identity、User Profile、Persistent Memory 快照；同一 session 后续 run 继续使用这份快照。
- `EmbeddedMindMemOS`：长期结构化/经验记忆的搜索、添加、更新、删除、反馈和 dreaming 后端，底层使用本地 BM25/Qdrant，并按配置连接 Neo4j。
- `MemoryEvidenceLedger`：记录当前 tenant/session/run/turn 中已确认的用户陈述和工具证据；它是内部证据账本，不能由模型伪造或跨 run 搬运。
- `MemoryAddQueue`、`MemoryEnrichmentQueue`、`SessionFinalizer`：把 session 结束后的经验沉淀和向量/实体增强放到 application-owned 生命周期中。

**输入/输出**：输入是 `session_id`、当前用户消息、已持久化的 Session 消息、`TaskState` 和本 run 的 verified tool evidence；输出是两类上下文：一份注入首个 Provider 请求的 `<memory-context>`，以及供 `context_memory`/`mindmemos_*` 工具使用的结构化结果和 memory ID。

**自动召回的具体逻辑**：

1. `SessionRuntime` 持有 generation-fenced 的 `require_recall` 标志；新 session 的第一条消息会消耗一次标志，Compact 真正生成摘要后下一条用户消息会重新挂起一次。
2. `ApplicationRuntime._automatic_recall()` 调用 `build_automatic_recall_query()`。新 session 直接使用当前用户文本；Compact 后则确定性拼接 Compact Summary、Task State（排序后的 JSON）和当前用户消息。
3. 通过 `application_services["mindmemos"]` 调用 `search(query, top_k=5, search_pipeline="vanilla", rerank=False, filters=None)`，结果最多保留 5 条。
4. `build_automatic_recall_context()` 把原生 memory type、ID、正文、时间和 lineage 序列化成 `<memory-context>`，绑定到本 run 的 `ContextAssembler`，只在首个 Provider 请求前出现。
5. 召回失败或后端不可用会发出 `memory.automatic_recall` 事件，但本轮仍可继续；召回内容是历史参考，不是用户指令，也不能替代当前页面重新 inspect。

**浏览器结果如何写回记忆**：

1. 每个 run 开始时，Runtime 为当前用户陈述向 `MemoryEvidenceLedger` 登记 scope evidence。
2. 浏览器工具必须先完成 DOM/URL/selected state/download 等外部读回；只有已确认的工具结果才可作为记忆写入证据。
3. `mindmemos_add`、`mindmemos_update`、`mindmemos_feedback` 在工具层校验当前 scope、memory ID、类型和证据；不接受模型自行提交 evidence ref。
4. MindMemOS 写入后要求按 memory ID 做原始记录、向量/索引和 Neo4j provenance/lineage 回读；终态不确定时返回 `memory_outcome_unknown`，禁止自动重试。
5. Web Console 的一条消息只是一个 run/turn，不会每条消息都触发 Session Finalizer。明确结束 session 时，`SessionFinalizer` 才把筛选后的会话经验排入共享 FIFO；正常关闭会 drain，进程强杀仍可能丢失未完成的增强任务。

**和其他状态的边界**：

| 对象 | 保存什么 | 生命周期 | 能否直接定位网页元素 |
| --- | --- | --- | --- |
| File memory snapshot | 人格、用户偏好、持久事项 | session 首次组装后冻结 | 不能，只能影响模型上下文 |
| MindMemOS memory | fact、procedure、experience、tool trace 等长期记录 | application 持有，可跨 session 检索 | 不能，记忆不授予 browser capability |
| Session messages/TaskState | 当前对话和本轮任务状态 | session/run | 不能 |
| Browser SnapshotStore | 当前 page 的 DOM 元素、`target_ref`、generation、fingerprint | run/page 生命周期 | 可以，但只能在当前 page/generation 校验后使用 |

源码入口：[`src/homemaster/memory/automatic_recall.py`](../../src/homemaster/memory/automatic_recall.py:20)、[`src/homemaster/memory/context_service.py`](../../src/homemaster/memory/context_service.py:9)、[`src/homemaster/application/runtime.py`](../../src/homemaster/application/runtime.py:458)、[`src/homemaster/cli/composition.py`](../../src/homemaster/cli/composition.py:370)、[`src/homemaster/tools/memory_tools.py`](../../src/homemaster/tools/memory_tools.py:1319)。

### 第 5 层：AgentRuntime 与 Provider

**是什么**：`src/homemaster/agent/generic_runtime.py` 是循环控制器，`src/homemaster/providers/` 是模型传输层。

**为什么存在**：模型只做“下一步决策”，不直接拥有浏览器能力；所有能力都通过本轮提供给它的 typed schema 暴露。

**具体逻辑**：ContextAssembler 组合 system prompt、历史消息、任务状态和当前工具 schema；Provider 流式返回文本 delta 或 tool call；AgentRuntime 把 tool call 交给 ToolExecutor，再把 ToolResultMessage 追加回上下文，继续下一轮，直到模型没有 tool call 或命中失败/取消条件。

**关键边界**：Provider 重试使用冻结的 request body；它可以重发模型请求，但不能自行重放已经执行过的浏览器写操作。

### 第 6 层：ToolRegistry / ToolExecutor

**是什么**：`src/homemaster/tools/` 的通用工具治理层，以及 `src/homemaster/tools/browser/` 的浏览器工具适配层。

**为什么存在**：把“模型想做什么”和“系统允许做什么”分开。模型提供参数，治理层决定参数是否合规、工具是否可用、是否需要审批、是否能占用浏览器资源。

**固定顺序**：

```text
schema validation
  -> 参数归一化
  -> PermissionChecker
  -> 可选 confirmation
  -> resource key=browser:backend 串行化
  -> BrowserToolExecutor
  -> ToolExecutionResult / VerificationRecord
```

输入 schema 错误不会进入浏览器；权限拒绝不会取得资源；执行结果必须保留 `backend_attempted`、错误码、证据引用和 certainty。

### 第 7 层：PlaywrightBrowserSession

**是什么**：`src/homemaster/browser/playwright_session.py`，唯一真正持有 Playwright page/context 的对象。

**为什么存在**：集中处理浏览器状态和所有安全细节，避免 27 个工具各自实现一套不一致的定位、超时和错误逻辑。

**具体逻辑**：启动时创建 Chromium context/page，安装 origin route、网络/下载监听、trace/video；每个公开方法再通过 `_execute()` 统一加锁、超时、日志和 fenced 状态。

**它向上层提供什么**：导航回执、快照、语义查找结果、DOM/HTML/Markdown、表单值、截图、下载和动作 readback。它向下层调用 Playwright，并通过 `OpenCLIPageAdapter` 执行纯页面表示算法。

### 第 8 层：Chromium 与目标网站

**是什么**：真正承载 DOM、JavaScript、网络和用户界面的外部系统。

**为什么存在**：这是唯一能证明“网页真的发生变化”的地方。代码里的日志和 receipt 只能说明调用走到了某个分支，不能替代页面状态。

**可观察终态**：URL、HTTP status、DOM hash、表单 value、selected option、checked state、popup/tab、download、截图内容和网络响应。

## 1.2 开发实现说明：每层内部到底怎么跑

下面按“进入点 -> 内部步骤 -> 离开点”的方式说明。代码阅读时建议从这些进入点开始，而不是从浏览器工具文件随机跳转。

### 1.2.1 前端层：从页面动作到后端请求

#### 初始化和切换 Session

`web/src/App.tsx` 的初始化 effect 先调用 `api.listSessions()`。如果 URL 中有 `session_id`，必须先确认它在列表中；否则显示不存在错误。如果没有指定 ID，就选择第一个 session，没有任何 session 才调用 `api.createSession()`。

选中 session 的 `selectSession()` 有固定顺序：

1. 停掉旧的 `EventConnection`，把 UI 状态改成 `connecting`。
2. 调用 `api.history(sessionId)`，将后端历史消息映射到左侧对话内容。
3. 创建 `new EventConnection(sessionId, ..., sinks)`，将事件交给 `reduceWebEvent`，再调用 `connection.start()`。

这解释了为什么页面刷新后不会从 WebSocket 重放旧 delta：历史由 HTTP 获取，实时增量由新连接获取。

#### 发送消息

`send()` 只有在 `connectionState === 'connected'`、当前有 session、当前没有活动 turn、文本非空时才继续。它生成浏览器端 `request_id = crypto.randomUUID()`，先把文本放进本地 `submitted`，再调用 `POST /api/sessions/{id}/messages`。

前端的 `request_id` 不是 run ID：后端接受请求时还没有 Runtime run ID；等 Runtime 发出 `runtime.turn_started` 后，`WebRunRegistry` 才把 request 绑定到 run。

#### reducer 如何保证一条 turn 不串线

`conversation.ts` 使用 `${session_id}:${request_id}` 作为 turn key。收到 `run.started` 才写入 `runId`；之后如果同一 request 收到不同非空 `run_id`，只记录 `run_id_conflict`，不继续覆盖状态。

工具状态使用 `tool_call_id` 作为二级 key：`tool.started` 创建卡片，`tool.completed/tool.failed` 只更新同一张卡片。终态事件会清除 approval 并把 turn 标记为 completed/failed/cancelled。

源码入口：[`web/src/App.tsx`](../../web/src/App.tsx:47)、[`web/src/api/http.ts`](../../web/src/api/http.ts:56)、[`web/src/state/conversation.ts`](../../web/src/state/conversation.ts:51)。

### 1.2.2 Web 接入层：请求为什么不会重复执行

#### `create_web_app()` 创建的长期对象

`create_web_app()` 每次构造一个 `WebRunRegistry`、一个 `WebEventHub` 和一个 `WebConfirmationHandler`。它们挂在 `app.state`，由 FastAPI lifespan 统一启动和关闭。`close_resources()` 用 `close_lock` 保证重复关闭不会释放两次，关闭顺序是 confirmation -> run registry -> event hub -> application。

#### `send_message()` 的真实控制流

```text
检查 session 是否存在
  -> (session_id, request_id) 是否已经在 registry
      是：直接返回 accepted，不创建 task
      否：检查当前 session 是否有 WebSocket subscriber
  -> registry.accept() 原子占用 session
  -> 发布 request.accepted
  -> 放开 start_gate
  -> run_owned() 调用 application.run(RunRequest)
```

`WebRunRegistry.accept()` 在一把锁内完成三件事：检查重复 request、检查 `_active_by_session`、创建并登记 asyncio task。这样两个并发 HTTP 请求不会同时启动同一个 session 的 run。

Runtime 发出 `runtime.turn_started` 后，`WebEventHub._pump()` 调用 `registry.correlate(event)`：把 `event.run_id` 记到 `_started_by_run`，并返回原始 `request_id`。终态事件到达时，它清理 `_active_by_session` 和 `_started_by_run`，所以 session 才能接受下一条消息。

如果 application 在 `turn_started` 前抛异常，`_run_and_report_prestart_failure()` 只有在 request 仍未绑定 run 时才发布 `run.failed`；已经绑定 run 的异常不能再伪造一个无 run 的失败覆盖它。

#### WebSocket 为什么同时等待两个任务

`_stream_events()` 同时等待 `queue.get()` 和 `websocket.receive()`。前者保证有事件就发送，后者保证“长时间没有新事件但客户端已经断开”时也能退出；不能等下一次 `send_json()` 才发现断线。退出时两个 task 都会 cancel/join。

源码入口：[`src/homemaster/web/app.py`](../../src/homemaster/web/app.py:209)、[`src/homemaster/web/run_registry.py`](../../src/homemaster/web/run_registry.py:68)、[`src/homemaster/web/app.py`](../../src/homemaster/web/app.py:360)。

### 1.2.3 Runtime 层：一次 run 如何组装出来

#### `BrowserApplication.run()` 做的事情

`BrowserApplication.run()` 不执行工具。它复制 `request.dependencies`，把 factory 放入 `browser_session_factory`，根据 capability 决定本 run 是否允许 `browser.eval`，然后把 `profile` 改成 `browser`、把 `max_tool_iterations` 设为 `None`，再转调底层 `ApplicationRuntime.run()`。

所以 browser profile 的差异是“工具和 prompt 的组合方式”，不是一套新的 agent loop。

#### `ApplicationRuntime.run()` 的阶段

1. `await self.start()` 和 `event_bus.start()`，确保应用级依赖可用。
2. 从 request 取 borrowed backend（浏览器模式一般为空），创建/恢复 session。
3. 进入 `session_manager.turn()`，拿到 `SessionRuntime`、generation 和取消状态。
4. 创建 `_GenerationFencedEventSink`，所有 RuntimeEvent 都带当前 generation。
5. 调用 `_execute_run()`；离开 context 时提交或丢弃本代状态。

#### `_run_tool_view()` 为什么必须在 run 内

没有 `browser_session_factory` 时，Runtime 直接复用 application registry；有 factory 时：

```text
factory.create(run_id)
  -> audit_browser_session_implementation(session)
  -> scope.bind(browser-session:run_id, session)
  -> build_browser_run_registry(base_registry, session)
  -> 创建同一 permission/confirmation/resource_manager 的 ToolExecutor
  -> yield (run_registry, run_executor)
  -> context 退出，RunResourceScope.aclose() 调 session.aclose()
```

这里的关键是 registry 和 executor 都是 run 级的，而 permission checker、confirmation handler、resource manager 仍来自应用级对象；这样浏览器句柄隔离，但权限语义保持一致。

#### `_execute_run()` 给 AgentRuntime 准备的输入

它创建 provider 和 ContextAssembler，复制 `agent_state`，复制 task state，构建 `ApplicationToolExecutor`，再创建 `RunContext`。`RunContext.deps` 中包含 cancellation、task state、自动召回结果和 feedback binder；每个 tool call 还会再复制一份 deps 并补充 `tool_call_id`、`session_id`、`run_id`、`backend` 和 deadline。

源码入口：[`src/homemaster/browser/application.py`](../../src/homemaster/browser/application.py:18)、[`src/homemaster/application/runtime.py`](../../src/homemaster/application/runtime.py:342)、[`src/homemaster/application/runtime.py`](../../src/homemaster/application/runtime.py:405)。

### 1.2.4 记忆层：上下文何时读、何时写

这层在一次 browser run 中的进入点是 `ApplicationRuntime._execute_run()`：创建 `ContextAssembler` 和 `ApplicationToolExecutor` 后，先执行 `_automatic_recall()`，再让 AgentRuntime 发出首个 Provider 请求。也就是说，自动召回发生在模型决策之前，不是模型调用某个工具后才补上的背景信息。

进入点到离开点的顺序如下：

```text
SessionRuntime.consume_recall(generation)
  -> build_automatic_recall_query(user text + optional compact summary + TaskState)
  -> application_services["mindmemos"].search(top_k=5, vanilla, no rerank)
  -> build_automatic_recall_context()
  -> ContextAssembler.bind_automatic_memory_context()
  -> 首个 Provider request 可见 <memory-context>
```

同一个 run 的浏览器结果回写则走另一条路径：`ApplicationToolExecutor` 将已读回的工具证据登记到 `MemoryEvidenceLedger` 的当前 scope；记忆工具从 `ToolExecutionContext.services` 取 ledger 和 MindMemOS，校验证据后执行 mutation；写入后按 memory ID 回读真实记录，成功才返回 `stored`，不确定则返回 `memory_outcome_unknown`。因此，浏览器 readback 是记忆写入的前置条件，记忆文本不能反过来替代页面验证。

文件记忆和 MindMemOS 的所有权在 application 级，`<memory-context>` 和自动召回 tuple 在 run 级；前者可以跨 session 持久化，后者只绑定当前 run。`context_memory` 对 `USER.md`/`MEMORY.md` 的写入不会改变当前 session 已冻结的 system prompt，下一次 session 才读取新快照。

### 1.2.5 Agent 层：模型循环不是“调用一次就结束”

`AgentRuntime.run()` 每轮都做以下事情：

```text
session.append(UserMessage)
  -> ContextAssembler.prepare/aprepare()
  -> project_model_tool_schemas()
  -> 深拷贝 messages/tools（冻结本轮 provider 输入）
  -> transport.stream()
  -> 聚合 delta 为 AssistantMessage
  -> 无 tool_calls：turn_completed，返回 final_reply
  -> 有 tool_calls：协议检查 -> dispatch tools -> 追加 ToolResultMessage
  -> iteration += 1，回到 ContextAssembler
```

几个开发时必须知道的分支：

- provider stream 失败时，只有在没有提交 assistant/tool/external action 的情况下才允许 retry；retry 使用同一份 frozen request body。
- tool call 中包含需要 model observation 的动作时，必须是该 batch 唯一调用，否则返回 `model_observation_batch_rejected`，不进入 backend。
- tool result 的 ID 集合必须与 assistant tool call ID 集合完全相等；缺 ID 直接 `tool_result_id_mismatch` 结束 run。
- cancellation 发生在 LLM 或工具执行中时，先发布已有结果，再返回 cancelled；不能把取消伪装成正常 final。

这也是为什么“模型说完成了”不等于 run completed：只有 agent loop 没有剩余 tool call，并且 `_commit_result()` 成功，Runtime 才提交最终 session 状态。

源码入口：[`src/homemaster/agent/generic_runtime.py`](../../src/homemaster/agent/generic_runtime.py:121)、[`src/homemaster/agent/generic_runtime.py`](../../src/homemaster/agent/generic_runtime.py:329)、[`src/homemaster/agent/generic_runtime.py`](../../src/homemaster/agent/generic_runtime.py:769)。

### 1.2.6 Tool 层：参数、权限、资源、异常的精确顺序

`ToolExecutor.execute()` 是所有 browser tool 的共同入口。顺序不能交换：

1. `registry.get(call.name)`；找不到返回 `unknown_tool`。
2. `tool.input_model.model_validate(call.arguments)`；失败返回 `invalid_tool_arguments`，并记录收到的键、缺少的必填项和逐项 issue。
3. `tool.is_read_only(arguments)`；注意这是基于归一化参数的判断。
4. `permission_checker.evaluate_tool(...)`；拒绝在 backend 前结束。
5. 若 `requires_confirmation`，调用 `confirmation_handler.confirm(tool, normalized_arguments, context, decision)`；没有批准返回 `permission_denied`。
6. 计算 resource key；browser tool 的固定 key 是 `browser:backend`。
7. 进入 `_lease()`；拿到 lease 后才把 `backend_started=True` 并调用 `tool.execute()`。
8. 根据 deadline 等待；异常按 read-only/mutating 和 backend_started 分类。

`execute_many()` 不是简单 `gather`：它先按 `concurrency_policy` 分组。不同 resource key 可以并行，同一个 resource key 串行；结果最后按原始 call index 排序，保证模型收到的 tool result 顺序稳定。

#### Browser tool 的下一层包装

`BrowserToolExecutor.execute()` 把 BrowserSession 的返回值转成 `ToolExecutionResult`：

- `BrowserSnapshot` 转为 public dict；
- screenshot 的 base64 PNG 转成 `ResultImage` 并计算 SHA-256；
- `BrowserSessionError` 转成稳定 code、`backend_attempted` 和 `OutcomeCertainty`；
- 每次成功结果补一个 evidence ref。

`ApplicationToolExecutor.dispatch()` 再把这些结果变成 `ToolResultMessage`，记录 completion guard/evidence，并调用 artifact publisher。也就是说，`ToolExecutor` 管“能不能执行”，`BrowserToolExecutor` 管“怎么调用 BrowserSession”，`ApplicationToolExecutor` 管“怎么回到 Agent/Provider”。

源码入口：[`src/homemaster/tools/executor.py`](../../src/homemaster/tools/executor.py:76)、[`src/homemaster/tools/executor.py`](../../src/homemaster/tools/executor.py:180)、[`src/homemaster/tools/browser/_common.py`](../../src/homemaster/tools/browser/_common.py:191)、[`src/homemaster/application/tool_executor.py`](../../src/homemaster/application/tool_executor.py:79)。

### 1.2.7 BrowserSession 层：定位、动作、读回的内部算法

#### 页面元素如何变成 `BrowserElement`

`inspection.collect_elements()` 遍历 `page.frames`。每个 frame 分配 `f0/f1/...`，在 frame 内用 role 对应的 CSS 集合收集候选，再执行页面内的 `_ELEMENT_STATE_JS` 获取 tag、role、name、label、text、value、visible、enabled、editable、readonly、checked、selected、expanded、obscured 等状态。

每个元素保存两种信息：

- 给模型看的普通字段（`to_public_dict()`）；
- 只供执行器使用的真实 Playwright `handle` 和 fingerprint。

因此返回给模型的 JSON 不包含 page handle；handle 只留在当前进程的 SnapshotStore 中。

#### `inspect/find` 与 `click/fill` 的关系

`inspect/find` 是观察操作，会创建/更新 `SnapshotStore`，给元素补 `target_ref`。动作可以直接使用 semantic target，也可以使用之前保留的 `target_ref`。两条路径最后都汇合到 `_resolve_target()`。

`_resolve_target()` 的 target_ref 分支会检查 snapshot generation、当前 URL、元素是否仍 connected、owner frame 是否仍属于当前 page、fingerprint 是否一致；不一致时最多尝试 `_reidentify()`，且必须唯一命中。semantic 分支则重新 collect 当前页面元素，再调用 `resolve_semantic()` 做 exact/contains/regex 和 ambiguity 判断。

#### `_prepare_actionable()` 为什么在 scroll 之后

动作执行前会先 `scroll_into_view_if_needed()`，再重新读取 state。这样 `obscured` 判断基于元素实际滚入 viewport 后的状态，不会因为 inspect 时元素在首屏外就错误拒绝。随后依次检查 visible、enabled、readonly（仅 editable 操作）、obscured。

#### 每种写操作的读回

- `fill`：Playwright fill 后读取 value；不一致时 keyboard fallback；仍不一致返回 `readback_mismatch`。
- `select`：原生 select 读取 selected value；ARIA combobox 打开 option，点击唯一匹配项，再检查选中 option。
- `click`：记录 before URL/DOM hash/pages，点击后读取 after URL/hash/popup；URL 改变才递增 generation。
- `navigate/history`：检查响应状态、DOM stable、最终 origin，并 invalidate snapshot current pointer。
- `backfill`：截图后进行 clipboard 回填，再核对源图/预览/目标内容的哈希和字节一致性。

所有公开操作最终都经过 `_execute()`：获取 session lock、检查 session 可用、施加 timeout、写 `browser_actions.jsonl`；mutating 操作在取消/超时/未知异常时会 fence session，结果是 `outcome_unknown`，不能自动重试。

源码入口：[`src/homemaster/browser/inspection.py`](../../src/homemaster/browser/inspection.py:127)、[`src/homemaster/browser/targets.py`](../../src/homemaster/browser/targets.py:40)、[`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:2083)、[`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:2219)、[`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:2785)。

### 1.2.8 外部执行层：代码怎样知道页面真的变了

Playwright context 在 `start()` 阶段安装：

- `context.route("**/*", _route_request)`：拦截并拒绝越过 allowed origin 的主导航；
- request/response/download/page listeners：记录网络、下载和新 tab；
- tracing：保存 screenshots/snapshots；
- `record_video_dir`：保存操作视频。

真正的页面状态由 Playwright API 和页面 JavaScript 返回，不由日志推断。比如 `click` 的 `dom_changed` 只来自 before/after DOM hash；`fill` 的成功只来自 element handle 的当前 value；导航成功还要同时满足 HTTP status、DOM stable 和 final origin。

如果外部操作已经开始但超时，代码无法证明页面没有变化，就必须返回 unknown 并 fence session。这是外部边界的语义，不是 Python 异常包装问题。

### 横切支撑能力（不另算主链层数）

- **配置与安全策略**：`BrowserGatewayConfig` 读取 `start_url`、`allowed_origins`、超时和 headless；`BrowserPolicy` 在初始 URL、重定向后的最终 URL 和每次操作上执行边界检查。
- **事件与可观测性**：Runtime 产生 `RuntimeEvent`，`EventBus -> WebEventHub -> WebEventProjection` 做 session 关联和字段 allowlist，再发到 `/api/events`；Playwright 另写 `browser_actions.jsonl`、trace 和视频。
- **会话/产物持久化**：SessionManager 保存对话快照；ArtifactStore 按 tenant/session/run 分区保存截图等二进制产物，网页只拿 opaque `artifact_handle`。记忆数据不由 SessionManager 代管，归 `FileMemoryStore`/MindMemOS 及其队列所有。

“8 层”是浏览器动作的串行责任边界，不代表 8 个进程。默认 Web Console、Runtime、记忆后端、Playwright 和 Chromium 都由同一服务编排；LLM provider、MindMemOS 的底层数据库和目标网站是外部边界。

## 2. 层与层之间的关系

```text
网页 React
  -- HTTP /api/sessions、/api/messages；WebSocket /api/events -->
FastAPI Web Adapter
  -- RunRequest(session_id, text, permission_subject) -->
ApplicationRuntime
  -- 创建 run、session turn、browser_session_factory -->
Memory / Context layer
  -- automatic recall + frozen file context -->
AgentRuntime + LLM Provider
  -- typed browser_* tool call(target, value, expectation) -->
ToolExecutor / PermissionChecker / resource key browser:backend
  -- BrowserToolExecutor -->
PlaywrightBrowserSession
  -- Playwright API + OpenCLI page algorithms -->
Chromium / 目标网站
```

返回方向分成两条：

1. **模型结果回路**：浏览器工具结果 -> `ToolResultMessage` -> AgentRuntime 下一轮上下文；没有更多 tool call 时形成最终回答，提交到 Session。
2. **网页观察回路**：RuntimeEvent -> EventBus -> WebEventHub（按 session fan-out）-> WebEventProjection（allowlist/correlation）-> WebSocket -> React reducer/UI。

因此，WebSocket 不是执行器，React 也不直接操作浏览器；记忆层也不直接操作浏览器。它们分别负责输入/显示和上下文复用，真实页面状态只能由第 7/8 层的执行与读回证明。

## 3. 启动和组装逻辑

### 3.1 配置入口

浏览器配置样例见 [`config/homemaster.browser.yaml.example`](../../config/homemaster.browser.yaml.example)：

- `browser_gateway.start_url`：每个 run 创建后首先打开的页面。
- `browser_gateway.allowed_origins`：允许的 origin 白名单；`start_url` 的 origin 必须在其中。
- `headless`、`action_timeout_ms`、`navigation_timeout_ms`、`wait_timeout_ms`：Playwright 运行策略。
- provider 配置：LLM 只负责决策，不持有浏览器句柄。

`create_browser_web_app()` 使用 `create_home_application(..., tool_environment="browser")` 组装通用应用，再通过
`create_browser_application()` 包装。浏览器模式会切换到 browser system prompt，并把 `BrowserSessionFactory`
放入每次请求的 dependencies；见 [`src/homemaster/cli/composition.py`](../../src/homemaster/cli/composition.py:162) 和
[`src/homemaster/browser/application.py`](../../src/homemaster/browser/application.py:18)。

### 3.2 服务启动顺序

1. `run_web_server()` 先拒绝非 loopback host，并探测端口是否被占用。
2. FastAPI lifespan 调用 `application.start()`，再启动 `WebEventHub`；事件流就绪前，消息接口会返回 `event_stream_not_ready`。
3. 前端列出 session；没有 session 就 `POST /api/sessions` 创建一个。
4. 前端先连接 `GET /api/events?session_id=...` 的 WebSocket，再允许发送消息。

服务入口和 loopback 约束见 [`src/homemaster/web/serve.py`](../../src/homemaster/web/serve.py:27)；Web API、消息接受和 WebSocket 见
[`src/homemaster/web/app.py`](../../src/homemaster/web/app.py:105)。

## 4. 一次浏览器操作的完整时序

以“在页面中点击确定按钮”为例：

```text
用户在 React 输入任务并发送
  -> POST /api/sessions/{id}/messages (request_id)
  -> WebRunRegistry 检查 session 是否已有 run，接受后返回 202
  -> ApplicationRuntime.run(RunRequest)
  -> _run_tool_view() 创建 PlaywrightBrowserSession(run_id)
  -> build_browser_run_registry() 注入 browser_* 工具
  -> _automatic_recall()（新 session/Compact 后触发）
  -> FrozenMemoryContext + <memory-context> 绑定 ContextAssembler
  -> AgentRuntime 组装上下文并请求 LLM
  -> LLM 返回 browser_click(target={role:"button", name:"确定"})
  -> schema validation
  -> PermissionChecker + confirmation（若策略要求）
  -> BrowserToolExecutor.dispatch()
  -> PlaywrightBrowserSession._resolve_target()
       先检查 target_ref 或 semantic role/name/label/text/testid
       再检查 snapshot/page generation、tab/frame、元素连接状态和 fingerprint
       滚入 viewport，检查 visible/enabled/obscured/editable 等 actionability
  -> Playwright element.click()
  -> 读取 URL、DOM hash、popup/tab 变化，生成 receipt
  -> ToolResultMessage 回到 AgentRuntime；同时发 tool.completed WebEvent
  -> LLM 继续下一轮，或输出最终回答
  -> runtime.turn_completed -> WebEventProjection -> WebSocket -> React
```

ApplicationRuntime 的 run/tool-view 边界在 [`src/homemaster/application/runtime.py`](../../src/homemaster/application/runtime.py:342) 和
[`src/homemaster/application/runtime.py`](../../src/homemaster/application/runtime.py:682)；记忆召回在
[`src/homemaster/application/runtime.py`](../../src/homemaster/application/runtime.py:586)；Agent loop 的“模型 -> tool -> 结果”在
[`src/homemaster/agent/generic_runtime.py`](../../src/homemaster/agent/generic_runtime.py:121)；浏览器工具注册表在
[`src/homemaster/tools/browser/registry.py`](../../src/homemaster/tools/browser/registry.py:67)。

### 4.1 具体例子：填写一个输入框

假设用户说“把用户名改成 `alice`”。模型不应该猜页面坐标，而是形成类似下面的调用：

```json
{
  "name": "browser_fill",
  "arguments": {
    "target": {"role": "textbox", "name": "用户名"},
    "value": "alice"
  }
}
```

执行过程是：

1. schema 层确认 `target` 和 `value` 字段齐全，没有未知字段。
2. 权限层确认本 run 有浏览器 capability；若当前策略要求审批，先把 approval 发给网页并暂停。
3. `BrowserSession._resolve_target()` 在当前 snapshot 或页面元素中按 role/name 找唯一元素；多匹配返回 `target_ambiguous`。
4. 解析后检查元素仍连接在当前 page、generation/url 没变、元素可见且 `editable=true`、不是 readonly。
5. Playwright 执行 `fill("alice")`；如果控件不接受直接 fill，会走受控 keyboard fallback。
6. 再次读取 DOM value；只有实际值等于 `alice` 才返回成功 receipt，否则返回 `readback_mismatch`。
7. 结果一方面回给模型决定下一步，另一方面形成 `tool.completed` 显示在网页工具卡片中。

这个例子体现了项目的基本思想：**先确认“操作谁”，再执行“怎么操作”，最后确认“是否真的变了”。**

### 4.2 页面变化后的处理

如果点击按钮后页面重新渲染，旧的元素句柄可能还存在但已经不是同一控件。系统会比较 fingerprint；如果身份不再可靠就返回 `stale_ref`，要求重新 `browser_inspect`。如果元素仍能由稳定身份唯一恢复，才会标记为 `stable` 并继续。这是为了避免“旧引用碰巧还能点”造成误操作。

## 5. 浏览器工具层怎么分类

工具以 ordinary name 注册，统一挂到 `ToolRegistry`。每个工具都声明输入 schema、所需 capability、`browser:backend`
资源键和 execution proof；注册表会在 run 内移除旧浏览器工具并注入当前 session 的实现。

| 类别 | 典型工具 | 作用 |
| --- | --- | --- |
| 导航/会话 | `browser_navigate`、`browser_history`、`browser_tabs` | 打开 URL、前进后退刷新、切换 run-owned tab |
| 观察/定位 | `browser_inspect`、`browser_find`、`browser_read`、`browser_extract`、`browser_analyze` | DOM/AX/hybrid 快照、语义候选、文本/HTML/Markdown 读取 |
| 页面交互 | `browser_click`、`browser_fill`、`browser_type`、`browser_select`、`browser_check`、`browser_uncheck`、`browser_hover`、`browser_focus`、`browser_press`、`browser_scroll`、`browser_drag` | 点击、输入、选择、键盘和滚动等操作 |
| 文件/媒体 | `browser_upload`、`browser_download`、`browser_screenshot`、`browser_backfill` | 上传、下载、截图和图片回填；截图/附件经过产物发布 |
| 等待/页面状态 | `browser_wait`、`browser_dialog`、`browser_network`、`browser_console` | 等待文本、URL、DOM、XHR、弹窗、网络和 console 条件 |
| 受控扩展 | `browser_eval` | 高权限页面脚本；仅在 capability 明确允许时注册，默认不提供 |

注册表当前包含 27 个 browser tool builder；`browser_eval` 由 policy 条件追加。共同分发和结果封装见
[`src/homemaster/tools/browser/_common.py`](../../src/homemaster/tools/browser/_common.py:191)。

## 6. 目标定位与安全闭环

### 6.1 目标不是 CSS/XPath

动作 target 支持 `role`、`name`、`label`、`text`、`testid`、`nth`、`frame_ref`、`tab_ref` 和 retained `target_ref`，默认 exact
匹配。未知或多匹配时先 `browser_inspect`/`browser_find`，再使用返回的完整 ref；不要猜坐标或自行拼 CSS。

`SnapshotStore` 为快照分配 `snapshot_id` 和元素 `target_ref`，最多保留有限数量；页面导航会增加 generation。引用跨 generation、跨 URL、跨 tab 或元素 fingerprint 变化时会拒绝为 `stale_ref`，要求重新 inspect。
实现见 [`src/homemaster/browser/targets.py`](../../src/homemaster/browser/targets.py:40) 和
[`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:2083)。

### 6.2 动作前门与动作后门

- **动作前**：origin、session 可用性、target 身份、元素连接状态、frame/tab 所属、viewport/actionability、readonly/editable 维度逐项检查。
- **动作中**：单个 `PlaywrightBrowserSession` 用锁串行执行；每个操作有 wall-clock timeout。
- **动作后**：
  - `fill` 读取 DOM value，必须与期望值一致；不一致返回 `readback_mismatch`。
  - `select` 读取 selected value/label；复合 combobox 还核对 option selected 状态。
  - `click` 读取 URL、DOM hash、popup/tab 变化，并返回 page generation。
  - `navigate/history` 检查 HTTP 状态、DOM stable 和最终 origin。
  - 超时或不确定写入会把 session 置为 fenced，结果标记 `outcome_unknown`，不能盲目重试。

核心实现见 [`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:284)、
[`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:573) 和
[`src/homemaster/browser/playwright_session.py`](../../src/homemaster/browser/playwright_session.py:2785)。

## 7. OpenCLI、Playwright 与真实页面的分工

Playwright 是唯一浏览器 owner：启动 Chromium、创建 context/page、监听 request/response/download/page、录制 trace/video，并执行 click/fill/select 等动作。

vendored OpenCLI 不另起浏览器；`OpenCLIPageAdapter` 只把 DOM snapshot、AX snapshot、cleaned HTML、form state 等纯页面算法注入同一个 Playwright page 执行，见
[`src/homemaster/browser/opencli_adapter.py`](../../src/homemaster/browser/opencli_adapter.py:11)。因此：

- OpenCLI 负责“如何观察/清洗页面表示”；
- Playwright session 负责“在哪个 run/page 执行、是否允许、怎样读回”；
- 目标网站/Chromium 才是外部终态。

## 8. 网页实时显示和审批逻辑

前端 `EventConnection` 为一个 session 维护 WebSocket generation；断开后按指数退避重连，解析失败的 frame 丢弃，不污染状态。React reducer 按 `tool_call_id` 聚合工具卡片，并独立显示 thinking、answer、approval、artifact 和 run 终态，见
[`web/src/api/connection.ts`](../../web/src/api/connection.ts:29) 与 [`web/src/App.tsx`](../../web/src/App.tsx:117)。

后端事件链是：

```text
RuntimeEvent
  -> EventBus（单一消费）
  -> WebEventHub（按 session 关联/广播）
  -> WebEventProjection（字段 allowlist，不改写选中文本）
  -> WebSocket /api/events
```

工具审批不是前端直接执行。Runtime/permission 层先产生 `approval.requested`，网页调用
`POST /api/approvals/{approval_id}` 回传 approve/reject；断线、超时、session 替换和关闭都会 fail closed。事件投影实现见
[`src/homemaster/web/event_hub.py`](../../src/homemaster/web/event_hub.py:13) 和
[`src/homemaster/web/event_projection.py`](../../src/homemaster/web/event_projection.py:27)。

### 8.1 内部事件如何变成前端状态

`WebEventProjection.project()` 不把所有 RuntimeEvent 原样透传，而是按事件类型做明确映射：

| 内部 RuntimeEvent | WebEvent | 前端动作 |
| --- | --- | --- |
| `runtime.turn_started` | `run.started` | 创建/启动 turn，写入真实 `run_id` |
| `transport.delta`（reasoning） | `thinking.delta` | 追加 thinking 文本 |
| `transport.delta`（text） | `answer.delta` | 追加回答文本 |
| `assistant.thinking` | `thinking.snapshot` | 用快照校准流式文本 |
| `assistant.reply` | `answer.snapshot` | 用最终回答校准流式文本 |
| `tool.call_started` | `tool.started` | 按 `tool_call_id` 创建工具卡片 |
| `tool.call_completed` | `tool.completed` | 写入 output、artifacts、completed |
| `tool.call_failed` | `tool.failed` | 写入 output、artifacts、failed |
| `permission.confirmation_requested` | `approval.requested` | 打开审批框 |
| `permission.confirmation_completed` | `approval.resolved` | 关闭对应审批框 |
| `runtime.turn_completed` | `run.completed` | turn 进入 completed |
| `runtime.turn_failed/budget_exhausted` | `run.failed` | 写入 code/message/retryable |
| `runtime.cancelled` | `run.cancelled` | turn 进入 cancelled |

投影器还会过滤 usage key、artifact handle、run ID、SHA-256 等字段。`WebEventHub` 先通过 `WebRunRegistry.correlate()` 得到 request_id，再把 projected event 放入每个 session subscriber 的 bounded queue；它不读取前端状态，也不参与工具执行。

### 8.2 Artifact 为什么不直接放进事件正文

截图和下载是二进制，工具结果内部可以携带 `ResultImage/attachments`，但 `ApplicationToolExecutor._message()` 会把它们从普通 metadata 中拆出交给 `ArtifactPublisher`。发布后 WebEvent 只含：

```json
{
  "artifact_handle": "hm-artifact:...",
  "run_id": "run-...",
  "filename": "screenshot.png",
  "media_type": "image/png",
  "content_sha256": "..."
}
```

前端点击图片时才请求 `/api/artifacts/{artifact_handle}?session_id=...&run_id=...`。后端再次用 tenant/session/run 分区读取并返回 `X-Content-SHA256`，这样事件流不会膨胀，也不会把宿主机路径暴露给浏览器。

## 9. 运行、验证和排障入口

### 启动

```bash
uv sync --extra dev --extra browser
scripts/homemaster serve --host 127.0.0.1 --port 8890 --browser \
  --config config/homemaster.browser.yaml
```

打开 `http://127.0.0.1:8890`。服务只允许 loopback；远程机器使用 SSH tunnel，不把未认证端口绑定到 LAN。

### 代码级验证

- 前端：`cd web && npm test && npm run typecheck && npm run build`
- Python 浏览器测试：`pytest -q tests/homemaster/browser tests/homemaster/application/test_browser_run_scope.py`
- 现有用户指南：[`docs/browser-gateway-user-guide.md`](../browser-gateway-user-guide.md)、[`docs/web-console-user-guide.md`](../web-console-user-guide.md)

### 常见故障定位

| 现象 | 先查哪里 | 含义 |
| --- | --- | --- |
| `event_stream_not_ready` | WebSocket 是否先连上 | 前端尚未订阅当前 session 事件流 |
| `session_busy` | `WebRunRegistry` / 当前 run | 同一 session 只能有一个活动 run |
| `origin_not_allowed` | `browser_gateway.allowed_origins` | 初始或重定向后的 origin 不在白名单 |
| `stale_ref` / `target_ambiguous` | `browser_inspect`、`SnapshotStore` | 页面重绘/导航后 ref 失效，或语义目标不唯一 |
| `readback_mismatch` | Playwright session 的动作后读回 | 外部页面没有达到期望 DOM 终态，不能只看模型回答 |
| `action_timeout` | `browser_actions.jsonl`、trace、页面性能 | 写操作超时后 session fenced，先重新建立 run |
| 图片看不到 | ArtifactStore handle、tenant/session/run | 浏览器只接收授权产物引用，检查分区和 SHA-256 |

## 10. 交接时最重要的不变量

1. 浏览器 capability 按 run 创建；不要跨 run 复用 Playwright page、element handle 或 target_ref。
2. 所有浏览器动作都经过同一个 `ToolExecutor -> PermissionChecker -> resource key browser:backend` 边界。
3. 目标定位优先语义字段和 retained ref；禁止把裸 element id、坐标或猜测 CSS 当跨层身份。
4. “工具返回成功”不等于页面成功；以 DOM/URL/selected state/download 等外部读回为准。
5. provider、WebSocket 和事件投影都不拥有浏览器资源；关闭顺序由 ApplicationRuntime 和 run resource scope 负责。
6. 高权限 `browser_eval` 默认不注册；只有显式 capability 才可进入当前 run 的工具 schema。

## 10.1 接手代码时的推荐思路

遇到一个新需求或一个浏览器 bug，按下面顺序定位，不要一上来改 Playwright：

1. **先定义外部终态**：例如“按钮变为已选中”“URL 变成某路径”“输入框 value 等于某字符串”。没有终态定义，就无法判断修复是否有效。
2. **判断是哪个边界断了**：网页没发请求看第 1/2 层；run 没启动看第 2/3 层；模型没拿到记忆上下文看第 3/4 层；模型没拿到工具看第 5/6 层；工具有调用但页面没变化看第 7/8 层。
3. **沿同一条证据链追踪**：`request_id -> run_id -> tool_call_id -> target_ref -> receipt -> DOM readback`。每个 ID 都应该能在事件或日志中找到。
4. **先看失败分类再决定动作**：`target_ambiguous/stale_ref` 是定位问题，`origin_not_allowed` 是策略问题，`readback_mismatch` 是外部终态问题，`outcome_unknown` 表示不能安全重试。
5. **修改后做黑盒验证**：不要只断言函数返回成功；要重新读取真实 DOM/URL/下载文件，并检查外部调用返回状态。

### 按需求选择修改位置

| 需求 | 首先看 | 通常不应直接改 |
| --- | --- | --- |
| 新增浏览器动作 | `tools/browser/<operation>.py`、`registry.py`、`BrowserSession` protocol | React 页面、Provider transport |
| 改目标定位规则 | `browser/targets.py`、`inspection.py` | 各个工具重复加特殊分支 |
| 改超时、origin、eval 能力 | `browser/policy.py`、`config/config.py` | 页面脚本 |
| 改网页实时显示 | `web/event_projection.py`、`web/event_hub.py`、`web/src/state/` | Playwright 动作代码 |
| 改截图/附件展示 | `ArtifactPublisher`、`/api/artifacts`、`ArtifactImagePreview` | LLM 消息正文 |
| 改 run 资源生命周期 | `application/runtime.py`、`browser/application.py`、`browser/factory.py` | 单个 browser tool |

## 11. 本次交付验证记录

- Archify `validate architecture --quality showcase`：PASS，9/9 artifact checks，composition 0 error / 0 warning。
- Archify `deliver`：PASS，已固定规格 SHA-256 `8680b23ad026b1a1d8fc15d69273332f04f24063d8ce353dab4e68f3547735a7` 和 HTML SHA-256 `205bb954fdfaff63f9fb01c4845c32fbf9b21038296a7870dd22785fe781309f`。
- Archify `visual-check`：SKIPPED；当前环境没有 Chrome/Chromium，因此没有伪造桌面截图或视觉通过结论。
- Markdown：`git diff --check` 通过。
