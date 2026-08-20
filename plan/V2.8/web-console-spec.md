# HomeMaster V2.8 Web Console Spec

## 0. 文档状态

- 日期：2026-08-20
- 状态：已定稿，等待实施
- 真理源：本文件定义 V2.8 Web Console 的产品范围、系统边界、公开协议、前端状态语义、第三方源码复用边界与验收标准
- 实施步骤：见 `web-console-implementation-plan.md`
- 目标用户：通过浏览器与 HomeMaster 对话、观察运行过程并处理人工确认的本地单用户操作者

## 1. 目标

V2.8 为 HomeMaster 增加一个独立的浏览器 Agent Console：

1. 浏览器可以创建、恢复和查看 HomeMaster session；
2. 浏览器可以异步发送 prompt，并在同一页面取消对应运行；
3. 回答文本和模型 thinking 都必须逐 delta 实时显示；
4. 工具调用、工具结果、错误、artifact 和人工确认以结构化 UI 展示；
5. React 前端只依赖稳定的 HTTP/WebSocket JSON，不 import 或理解 Python Runtime 对象；
6. FastAPI 层只翻译 Web 请求和 Runtime 事件，不重写 `ApplicationRuntime`、Session 或工具执行逻辑；
7. 选择性移植 DeepSeek Harness 和 Hermes Agent 的成熟前端实现，减少代码量，但不引入它们的 Runtime、插件系统或消息协议。

V2.8 的核心关系固定为：

```text
Browser React/Vite
  -> HTTP commands
  <- WebSocket events
FastAPI Web Adapter
  -> RunRequest / ApplicationRuntime
  <- RuntimeEvent / EventBus
HomeMaster Runtime
```

## 2. 已验证基线

### 2.1 HomeMaster 已有能力

当前代码已经提供：

- `ApplicationRuntime.run(RunRequest)`、`cancel(session_id)`、`status(session_id)`；
- `SessionManager` 的 session 创建、恢复、持久化与枚举能力；
- application-owned `EventBus` 及有界异步 `stream()`；
- `RuntimeEvent` 中的 session、run、turn、tool call 和时间字段；
- `transport.delta`、`assistant.reply`、`assistant.thinking`、工具、usage、权限和 runtime 终态事件；
- `PublicEventProjection` 与 `public_gateway_stream()` 形成的远程公开边界；
- `confirmation_handler` 人工确认入口，以及 CLI handler 已使用的权限确认事件名。现有 `ToolExecutor` 只与 `confirmation_handler.confirm() -> bool` 协程契约耦合；它不暴露也不持有任何 Future，也不关心 `confirm()` 在何处等待用户回答。V2.8 不复用 CLI 的 `CliConfirmationHandler`（其内部阻塞读 stdin），而是新建 `WebConfirmationHandler`：每次 `confirm()` 内部生成 `approval_id`、登记一个 `asyncio.Future`、发 `permission.confirmation_requested`（payload 内含 `approval_id`）、`await future` 挂起，由 `POST /api/approvals/{approval_id}` 解析后再发 `permission.confirmation_completed` 并返回 bool。执行器一行不改；
- FastAPI 与 Uvicorn 项目依赖。

### 2.2 Thinking 当前缺口

Provider transport 已经产生流式 thinking：

- OpenAI-compatible transport 将 `reasoning_content` 解码为 `reasoning_delta`；
- Anthropic transport 将 `thinking_delta` 解码为 `reasoning_delta`；
- `TransportDelta` 和 `aggregate_deltas()` 均保留 reasoning。

缺口发生在两个下游边界：

1. `AgentRuntime._publish_text_delta()` 只发布 `text_delta`，丢弃逐 chunk 的 `reasoning_delta`；
2. 当前 `PublicEventProjection` 有意拒绝 `transport.delta` 和 `assistant.thinking`，因为它服务于飞书等既有远程 Gateway，而不是 Web Console。

因此 V2.8 必须补齐 Web 专用 thinking 投影；不能把“最终存在完整 `assistant.thinking`”误判为 thinking 已经流式公开。

## 3. 架构候选与锁定选择

### 3.1 候选 1：复制 Hermes Web Dashboard

优点：现成应用壳、主题、路由和通用组件多。

代价：Hermes 的 ChatPage 核心是 xterm + PTY + TUI 字节流，并绑定 Hermes session token、profile、plugin、OAuth 和专用 API。删除这些耦合的工作量高，而且会丢失 HomeMaster 已有的结构化 RuntimeEvent。

结论：不采用整套复制。

### 3.2 候选 2：直接接入 DeepSeek Harness Client Runtime

优点：原生支持 reasoning delta、assistant block、工具卡片、审批、问题、重连和 session projection。

代价：组件深度依赖 Cordis、DeepSeek Client Runtime、slot/plugin 注册、workspace package 与其 session event model。接入等于让 HomeMaster 前端拥有第二套 Runtime 和状态真理源。

结论：不采用整套依赖。

### 3.3 候选 3：选择性移植成熟组件和算法

做法：HomeMaster 定义唯一 Web JSON 和轻量 React 状态；从 DeepSeek 移植流式对话 reducer、Thinking UI 与重连算法，从 Hermes 移植少量独立通用组件。

代价：移植时需要替换第三方类型和样式依赖，并自行维护来源记录。

结论：**锁定采用**。它保留 HomeMaster 的 Runtime 真理源，同时复用最难正确处理的前端细节。

### 3.4 候选 4：全部从零实现

优点：没有第三方内部依赖。

代价：重复实现 thinking 流归并、流式 Markdown、WebSocket 重连、工具展示和无障碍交互，代码量和回归风险最高。

结论：不采用。

## 4. 系统所有权与解耦边界

### 4.1 HomeMaster Runtime 拥有

- session/run/turn 生命周期；
- Provider 请求与流式 delta；
- 工具执行、权限和确认等待；
- cancellation；
- canonical message、event、artifact 与 session 持久化；
- tenant、permission subject 和资源访问判断。

### 4.2 FastAPI Web Adapter 拥有

- HTTP 请求解析和返回状态码；
- WebSocket 连接生命周期；
- Web request ID 与 run 的关联；
- `RuntimeEvent -> WebEvent` 的字段 allowlist、命名翻译和 JSON 序列化；
- Web session 范围过滤；
- Web confirmation Future 的注册、一次性解决与清理；
- 静态前端构建产物托管。

它不得：

- 复制 agent loop；
- 从 trace 猜测 Runtime 状态；
- 绕过 `ApplicationRuntime` 直接执行工具；
- 用网页状态覆盖 canonical session；
- 修改飞书/Telegram 等 Channel 的投影行为。

### 4.3 React 前端拥有

- HTTP command client 和单条 WebSocket client；
- WebEvent reducer 及当前页面的派生视图状态；
- streaming thinking、answer、工具、确认与连接状态显示；
- composer draft 等纯 UI 状态。

刷新或断线后，前端必须从后端 history/snapshot 恢复；localStorage 不得成为 session 或 run 真理源。

## 5. Web 公开数据模型

### 5.1 设计原则

Web 数据模型是 Runtime 到浏览器的显示适配，不是第二个 EventBus。它只做：

1. 将 Python RuntimeEvent 转成 JSON；
2. 将内部事件名翻译成清晰的 UI 动作；
3. 只发送 Web UI 所需字段。

V2.8 不加入尚无消费者的 `protocol_version`、replay `sequence` 或 `server_generation`。如果未来实现断线增量回放、多版本客户端或多实例服务，再通过新的 spec 增加。

### 5.2 请求标识

浏览器为每次发送生成不可变 `request_id`。Web adapter 在启动后台 run task 前，把它与 authoritative session ID 一次性登记到 application-owned request registry。由于同一 session 同时只允许一个 active run，registry 用该 session 的首个 `runtime.turn_started` 锁定 Runtime 生成的 run ID，之后只按 run ID 查找 request ID。

```text
POST request_id
  -> request registry: session_id -> pending request_id
  -> runtime.turn_started(run_id): lock run_id -> request_id
  -> later RuntimeEvent(run_id): resolve locked request_id
  -> WebEvent.request_id
```

`RunRequest.metadata.web_request_id` 可以同时用于内部 audit，但当前 Runtime 不会自动把 request metadata 复制到每个 RuntimeEvent，因此它不能充当唯一关联机制。模型、工具参数和前端后续事件不得改写 request registry。`RuntimeEvent.tool_call_id/event_id` 不能代替 `request_id`。

若后台 task 在产生 `runtime.turn_started` 前失败，Web adapter 直接用 pending registry 产生 `run.failed`，此时 `run_id` 允许为空字符串；该例外不得用于已经启动的 run。

### 5.3 下行 envelope

所有 WebSocket 下行事件使用同一 envelope：

```json
{
  "type": "thinking.delta",
  "session_id": "session-01",
  "run_id": "run-01",
  "request_id": "request-01",
  "payload": {
    "text": "先检查当前环境"
  }
}
```

字段语义：

| 字段 | 必需 | 语义 |
| --- | --- | --- |
| `type` | 是 | 前端 reducer 的稳定动作名 |
| `session_id` | 是 | authoritative session 归属 |
| `run_id` | 是 | authoritative run 归属；`request.accepted` 和 run 启动前 adapter 失败时允许空字符串 |
| `request_id` | 是 | 浏览器提交与异步 run/event 的关联 |
| `payload` | 是 | 每种事件明确 allowlist 的 JSON object |

### 5.4 Web 事件集合

| Web event | Runtime 来源 | Reducer 语义 |
| --- | --- | --- |
| `request.accepted` | Web adapter 接受 POST | 建立 pending turn，不表示 Runtime 已启动 |
| `run.started` | `runtime.turn_started` | 绑定 `run_id`，状态转为 running |
| `thinking.delta` | `transport.delta.reasoning_delta` | 追加到 thinking buffer |
| `answer.delta` | `transport.delta.text_delta` | 追加到 answer buffer |
| `thinking.snapshot` | `assistant.thinking` | 替换 thinking buffer，完成终态校准 |
| `answer.snapshot` | `assistant.reply` | 替换 answer buffer，完成终态校准 |
| `tool.started` | `tool.call_started` | 以 `tool_call_id` 建立 running tool row |
| `tool.completed` | `tool.call_completed` | 更新对应 tool row 的结果与 artifacts |
| `tool.failed` | `tool.call_failed` | 更新对应 tool row 为 failed |
| `approval.requested` | `permission.confirmation_requested` | 显示一次性人工确认 UI |
| `approval.resolved` | `permission.confirmation_completed` | 关闭确认并记录 outcome |
| `usage.updated` | `usage.update` | 替换本 run 的 usage snapshot |
| `context.compacted` | `context.compaction` | 增加上下文压缩提示，不追加回答正文 |
| `run.completed` | `runtime.turn_completed` | 生命周期完成，不再次追加 final reply |
| `run.failed` | `runtime.turn_failed` / `runtime.budget_exhausted` | 标记失败并展示稳定错误码 |
| `run.cancelled` | `runtime.cancelled` | 标记取消，不覆盖已收到的 partial 内容 |

`assistant.reply`、`assistant.thinking` 和 `runtime.turn_completed.final_reply` 可能包含重复文本。语义固定为：

```text
*.delta     = append
*.snapshot  = replace/calibrate
run.*       = change lifecycle only
```

### 5.5 Thinking 强制流式

如果 Provider 产生非空 `reasoning_delta`，WebSocket 必须在对应 provider stream 尚未完成时发送 `thinking.delta`。禁止只在结束后从 `assistant.thinking` 模拟流式播放。

Runtime 的 delta publish callback 对同一个 `TransportDelta` 分别发布存在的字段：

```python
payload = {}
if delta.text_delta:
    payload["text_delta"] = delta.text_delta
if delta.reasoning_delta:
    payload["reasoning_delta"] = delta.reasoning_delta
if payload:
    await emit("transport.delta", payload=payload)
```

正常 provider stream 中只含 reasoning 的单个 delta 不是失败。既有 `_reasoning_only_delta()` 仍仅用于“部分 reasoning 后 transport 失败是否允许安全重试”的判定，不得因 Web 投影改变其重试语义。

### 5.6 Tool payload

`tool.started` 最少包含：

```json
{
  "tool_call_id": "call-01",
  "name": "search_files",
  "arguments": {"query": "..."}
}
```

`tool.completed` / `tool.failed` 最少包含：

```json
{
  "tool_call_id": "call-01",
  "name": "search_files",
  "status": "completed",
  "output": "...",
  "artifacts": []
}
```

每个 tool result 必须按 `tool_call_id` 更新对应实例，不能按工具名或“最近一个工具”聚合。二进制只通过已有 opaque artifact handle 暴露；不得发送本地宿主存储路径。

### 5.7 错误模型

HTTP 非 2xx 和 Web `run.failed` 使用稳定的 `code`、用户可显示的 `message` 与 `retryable`：

```json
{
  "code": "session_busy",
  "message": "This session already has an active run.",
  "retryable": true
}
```

生产模式不得向浏览器发送 Python traceback、Provider 原始请求、credential 或未 allowlist 的 metadata。

## 6. HTTP 与 WebSocket API

### 6.1 Session API

```text
POST /api/sessions
GET  /api/sessions
GET  /api/sessions/{session_id}/history
```

`POST /api/sessions` 创建新 session，或在显式提供 session ID 时恢复已存在 session。恢复失败必须返回非 2xx，不能静默创建同名新 session。

### 6.2 Message API

```http
POST /api/sessions/{session_id}/messages
Content-Type: application/json

{
  "request_id": "request-01",
  "text": "检查厨房"
}
```

成功接受返回 HTTP 202：

```json
{
  "accepted": true,
  "session_id": "session-01",
  "request_id": "request-01"
}
```

202 只表示 adapter 已验证请求并拥有后台 run task，不表示模型或外部工具成功。重复 `request_id` 必须幂等返回原接受结果或明确 conflict，不能启动第二次 run。

### 6.3 Cancel API

```text
POST /api/sessions/{session_id}/cancel
```

返回值必须反映 `ApplicationRuntime.cancel()` 是否找到可取消的 active run。HTTP 成功后仍以 `run.cancelled` 或实际 run terminal event 为外部终态。

### 6.4 Approval API

```http
POST /api/approvals/{approval_id}

{
  "outcome": "approve"
}
```

允许值由 typed schema 锁定。approval ID 一次性消费；重复、过期、其他 session 或已经 terminal 的 run 必须拒绝，不能影响 Future。

### 6.5 Event WebSocket

```text
WS /api/events?session_id={session_id}
```

连接只接收 authoritative session ID 匹配的 WebEvent。WebSocket 必须先成功订阅，再允许 UI 发送第一条 prompt；否则第一批 fast provider delta 可能在订阅建立前丢失。

MVP 断线重连恢复方式是重新获取 session history/snapshot 后继续接收 live event，不承诺按 cursor 回放全部 delta。最终 `thinking.snapshot` 和 `answer.snapshot` 校准本轮内容，但运行中断线窗口内的实时动画可能缺失。

## 7. Runtime 与并发模型

### 7.1 Runtime 生命周期

Web 进程使用一个长生命周期 `ApplicationRuntime`，不为每个 HTTP 请求重新构造 Provider、registry、EventBus 和 stores。每个 session 仍由现有 `SessionManager` generation 与 ownership 规则隔离。

### 7.2 Session 并发

同一个 session 同一时间最多一个 active run。不同 session 是否并行由现有 `ApplicationRuntime` 能力决定，Web adapter 不引入全局串行锁。

后端为每个接受的 request 缓存一次不可变目标：

```text
pending: session_id -> request_id + owned task
started: run_id -> request_id + session_id + owned task
```

`runtime.turn_started` 到达时执行唯一一次 pending -> started 转换。若 session 已有另一个 pending/started request，或后续事件的 session/run 关系不一致，adapter 必须 fail closed。轮询、重连和审批过程中不得重新推导或更换该关联；run terminal 后再清理 active ownership，并按幂等保留期保存接受结果。

### 7.3 EventBus 订阅

Web adapter 订阅 application-owned EventBus，并使用独立 `WebEventProjection(include_thinking=True)`。不得全局修改 `PublicEventProjection` 默认 allowlist。

需要投影的内部事件为：

```text
runtime.turn_started
transport.delta
assistant.thinking
assistant.reply
tool.call_started
tool.call_completed
tool.call_failed
permission.confirmation_requested
permission.confirmation_completed
usage.update
context.compaction
runtime.turn_completed
runtime.turn_failed
runtime.budget_exhausted
runtime.cancelled
```

其他 RuntimeEvent 默认拒绝。新增公开事件需要同时更新本 spec、Python projector tests 和 TypeScript reducer exhaustiveness test。

## 8. 权限与部署边界

### 8.1 MVP 身份

V2.8 是单用户 Web Console，但不能把“单用户”解释为任意远程访问者都是 `local_operator`。

默认 bind 使用 loopback。若 bind 到非 loopback 地址，启动必须 fail closed，直到配置了明确认证方案；V2.8 不以 `--insecure` 作为默认部署方式。

### 8.2 Permission subject

Web request 使用专门的稳定 permission subject，不读取客户端提交的 tenant、capability 或 subject。危险操作保持 confirmation handler 门，网页只能响应服务端已登记的 approval ID。

### 8.3 文本与敏感字段

经过既有 typed public schema 选中的 user/provider/tool 文本按 HomeMaster V2.0 精确保真规则保持原值。过滤发生在字段集合，而不是对已选择文本做猜测性 credential/path 改写。

## 9. 前端产品与状态模型

### 9.1 MVP 页面

```text
+----------------+---------------------------------------+
| Sessions       | Conversation                          |
|                |                                       |
| New chat       | User message                          |
| Session A      | Thinking (collapsed, streaming)       |
| Session B      | Tool rows                             |
|                | Assistant answer                      |
|                |                                       |
|                | Composer                       Stop   |
+----------------+---------------------------------------+
```

移动端将 Session sidebar 放入 side sheet；Conversation 和 composer 保持主流程，不做管理 Dashboard 卡片堆叠。

### 9.2 Turn 状态

```ts
type TurnState = {
  requestId: string;
  runId: string | null;
  thinking: string;
  answer: string;
  tools: Record<string, ToolCallState>;
  approval: ApprovalState | null;
  usage: Usage | null;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
};
```

turn key 固定为 `session_id + request_id`，收到 `run.started` 后锁定 run ID。后续相同 request ID 但 run ID 不一致的事件必须拒绝并记录结构化客户端诊断，不能把两个 run 合并。

### 9.3 Thinking UI

- Provider reasoning 首个 delta 到达即创建 Thinking row；
- 默认折叠，但折叠摘要随流式内容更新；
- 展开时显示截至当前收到的完整文本；
- `thinking.snapshot` 替换 accumulated delta；
- run failed/cancelled 时保留 partial thinking 并标记相应状态；
- 没有 reasoning 的模型不显示空 Thinking row。

### 9.4 Answer UI

- `answer.delta` 流式追加；
- 流式 Markdown 必须容忍未闭合 fence、列表和 emphasis；
- `answer.snapshot` 替换 delta buffer；
- `run.completed` 不重复追加 `final_reply`；
- 用户消息、thinking、tool rows 和 answer 按事件语义稳定排序，动态内容不得使 composer 或 toolbar 跳位。

### 9.5 Connection UI

状态至少包括：

```text
connecting / connected / reconnecting / offline
```

重连期间禁止发送新 prompt，但允许阅读现有内容。旧 WebSocket generation 的迟到事件必须丢弃。

## 10. 第三方源码复用清单

### 10.1 DeepSeek Harness

来源：`/hpc2hdd/home/wyuan140/deepseek-harness`，MIT，Copyright (c) 2026 DeepSeek。

优先移植：

| 源文件 | 移植内容 | 处理方式 |
| --- | --- | --- |
| `packages/client/ui-conversation/src/client/chat/ReasoningRow.tsx` | 折叠 thinking、流式摘要和无障碍状态 | 复制后替换 DeepSeek primitive/locale 类型 |
| 同目录 `ReasoningRow.module.css` | Thinking row 样式 | 按 HomeMaster theme token 调整 |
| `conversation-nodes/assistant.ts` | text/reasoning delta 累积和 snapshot 校准算法 | 只抽取纯 reducer，禁止复制 Cordis registration |
| `packages/client/connection/src/client/connection.ts` | backoff、generation isolation、sink failure isolation | 简化为一条 WebSocket |
| `packages/client/connection/src/client/web-api-client.ts` | WS async reader、abort、malformed frame handling | 替换 DeepSeek schema/path/RPC envelope |
| `packages/client/ui-tool/.../GenericToolCard.tsx` | 通用工具卡展示 | 替换 slot 和 Client Runtime 类型 |

可参考但暂不移植整文件：`AssistantMarkdown.tsx`、`ToolCallTree.tsx`、`ToolDetails.tsx`、`SidebarRoot.tsx`。只有当其依赖可以被 HomeMaster 已选依赖替代且确实减少 owned code 时才移植。

禁止带入：

- `@deepseek-ai/cordis`；
- DeepSeek Client Runtime/session projection；
- UI slot/plugin registration；
- `MuxFrame` 作为 HomeMaster 公开协议；
- 双 WebSocket host/mux 拓扑。

### 10.2 Hermes Agent

来源：`/hpc2hdd/home/wyuan140/weilin_workspace/hermes-agent`，MIT，Copyright (c) 2025 Nous Research。

候选移植：

| 源文件 | 移植内容 | 处理方式 |
| --- | --- | --- |
| `web/src/components/ConfirmDialog.tsx` | modal focus、Escape、scroll lock 与确认交互 | 替换 Nous UI 和 Hermes theme 类型 |
| `web/src/lib/clipboard.ts` | copy fallback | 保留相关测试 |
| `web/src/lib/chat-title.ts` | session 标题规范化 | 仅在 API 标题语义一致时使用 |

禁止移植：

- `ChatPage.tsx` 的 xterm/PTY 主体；
- `/api/pty`、Hermes JSON-RPC 和 auth ticket 协议；
- Hermes profile/plugin/OAuth/dashboard API client；
- 依赖 `@nous-research/ui` 的整套应用壳，除非未来明确将该库选为 HomeMaster 设计系统。

### 10.3 许可证落地

每个实质复制或派生文件头注明源项目和原文件；HomeMaster `THIRD_PARTY_NOTICES.md` 增加 DeepSeek Harness 与 Hermes Agent 条目，并保留两份 MIT 文本所要求的版权与许可声明。

未复制源码、只借鉴行为的部分在实现文档记录参考，不伪装成直接复制。

## 11. 项目结构

```text
Homemaster/
  src/homemaster/web/
    app.py                  # FastAPI composition and lifespan
    schemas.py              # HTTP/WebSocket typed DTO
    event_projection.py     # RuntimeEvent -> WebEvent
    run_registry.py         # request ownership and task cleanup
    confirmations.py        # pending approval registry
    static.py               # built SPA serving
  web/
    src/
      api/http.ts
      api/events.ts
      protocol/events.ts
      state/conversation.ts
      components/ReasoningRow.tsx
      components/ToolCallCard.tsx
      components/ApprovalDialog.tsx
      features/chat/
      features/sessions/
      App.tsx
    package.json
    vite.config.ts
```

Web 源码独立使用 React + Vite + TypeScript。Python wheel 只包含生产构建产物，不包含 `node_modules`。

## 12. 可观测性

Web adapter 的关键数据流写结构化 JSONL：

- HTTP command：method、route、request/session ID、status、duration；
- run ownership：accepted、started、terminal、cleanup；
- WebSocket：connect、disconnect、session scope、发送失败；
- approval：requested/resolved/expired，不记录未 allowlist 的工具 payload；
- event projection rejection：内部 event type 和稳定 rejection reason，不记录敏感 payload。

日志不是成功判据。浏览器实际接收和外部 Runtime 终态仍是验收依据。

## 13. 验收标准

### 13.1 协议和投影

1. 每个公开 Web event 有 Python schema、TypeScript type 和 reducer case；
2. 未知 RuntimeEvent 默认不投影；
3. private Provider metadata、traceback、credential 和宿主 artifact path 不出现在真实 WS frame；
4. 同一 `request_id` 从 HTTP 202 到 terminal event 保持不变；
5. 每个 tool_call_id 独立断言 started/completed/failed，禁止聚合 `any()` 通过。

### 13.2 Thinking 黑盒门

使用真实支持 reasoning streaming 的已配置 Provider：

1. 建立真实 WebSocket 并确认 open；
2. POST 一次能产生多段 reasoning 的 prompt，核对 HTTP 202；
3. 在 `thinking.snapshot` 和 run terminal 之前，逐条收到至少两个非空 `thinking.delta`；
4. 浏览器 DOM 中展开 Thinking 后，内容随 delta 增长；
5. `thinking.snapshot` 到达后 DOM 文本与 snapshot 精确一致；
6. 本 run 最终 `run.completed`，不得因 reasoning-only delta 被误分类为失败；
7. 飞书 Gateway 使用默认 projection 时仍收不到 thinking，既有行为不变。

若当前真实 Provider 不公开 chain-of-thought 或只返回完整 reasoning，不得用 mock 宣称流式功能完成；该 provider 只记录为“不支持该能力”，并另用已验证支持流式 reasoning 的 provider 完成发布门。

### 13.3 回答和工具黑盒门

对每个测试 session 独立断言：

- `answer.delta` 在 run terminal 前进入真实浏览器 DOM；
- snapshot 校准后回答不重复；
- 一个成功工具和一个失败工具各自显示准确名称、状态和结果；
- artifact URL/handle 实际可读取，HTTP 返回成功且内容 digest 与事件一致；
- Stop 调用返回成功后，Runtime authoritative status 进入 cancelled/terminal，不能只看按钮变灰。

### 13.4 Approval 黑盒门

- 真实 gated tool 产生 `approval.requested`；
- 浏览器批准后 POST 返回成功，等待中的真实 Future 被解决且工具继续执行；
- 拒绝路径工具 backend 调用次数为零；
- 重复 approval ID 返回非成功状态，不能二次执行；
- 每个 pending approval 在 run terminal、disconnect cleanup 或 shutdown 后均无泄漏。

### 13.5 浏览器终态

使用 Playwright 在桌面和移动 viewport 验证：

- session 创建、发送、thinking、tool、answer、cancel 和 approval 完整路径；
- 刷新后 history 恢复；
- WebSocket 断开后显示 reconnecting，恢复后旧 generation 事件不污染当前页面；
- 页面无重叠、横向溢出、按钮文本截断或 composer 跳位；
- copied/adapted visual assets 和字体真实加载，无 404；
- 服务端 health/API/WS 返回码与页面终态同时通过。

## 14. 测试分层

1. Python unit：schema、projection allowlist、request registry、approval registry；
2. TypeScript unit：WebEvent reducer、delta append、snapshot replace、run ID fence；
3. FastAPI integration：真实 ASGI HTTP + WebSocket + application test runtime；
4. Browser E2E：真实服务与生产构建产物；
5. Live provider gate：真实 reasoning delta 和最终 run terminal；
6. 部署变体：loopback 直接访问与 path-prefix reverse proxy；非 loopback 未认证启动 fail closed。

Mock 单测和同源 fixture 只能证明内部自洽，不能替代第 4、5 层。

## 15. 交付物

- `homemaster serve`；
- FastAPI Web adapter 与 Web schemas；
- React/Vite MVP；
- Python、TypeScript、integration、Playwright 和 live provider 验证；
- 架构文档更新；
- 用户指南中的安装、启动和真实使用示例；
- README 能力清单；
- CHANGELOG；
- `THIRD_PARTY_NOTICES.md` 的源码复用记录。

## 16. 非目标

- 完整复制 Hermes 管理 Dashboard；
- 引入 DeepSeek Cordis/Client Runtime；
- PTY 或浏览器终端；
- 多租户和公开互联网身份系统；
- 多实例 Web service；
- delta cursor replay 和跨重启 replay；
- 管理模型、配置、Skills、Plugins、Cron 和 MCP 的完整后台；
- 为不公开 reasoning 的模型伪造 thinking；
- 改变飞书/Telegram 等既有 Channel 的 thinking 隐私行为。

## 17. 决策摘要

| 决策 | 结果 |
| --- | --- |
| 前端技术栈 | React + Vite + TypeScript |
| 上行/下行 | HTTP commands + 单 WebSocket events |
| Runtime | 进程内一个长生命周期 ApplicationRuntime |
| Thinking | Provider 真实 delta 强制流式，最终 snapshot 校准 |
| Event boundary | 独立 WebEventProjection，既有 Gateway 默认不变 |
| 关联 ID | 浏览器生成 request_id，Runtime 生成 run_id |
| 前端状态 | HomeMaster 自有轻量 reducer，不引入第二套 Runtime |
| 源码复用 | 选择性移植 DeepSeek + Hermes，保留 MIT notice |
| 默认部署 | loopback；非 loopback 无认证时 fail closed |
| 断线恢复 | MVP 重取 history/snapshot，不承诺 cursor replay |
