# Application Runtime 架构

## Ownership

### 实时公开事件流

```text
Provider TransportDelta
  -> Generic AgentRuntime（聚合原始最终消息；逐 delta 状态化脱敏）
  -> private RuntimeEvent / EventBus（trace、审计、生命周期）
  -> exact seven StreamEvent projection（公开 UI 边界）
  -> Rich / text / stream-json sink
```

Provider 只负责 yield delta；通用运行时负责在 provider 完成前发布安全文本。
`AssistantTurnComplete` 每次成功模型轮次只出现一次，并在工具开始前出现。
`RunResult.final_reply`、会话持久化和 Gateway terminal final 仍是原有权威所有者；
公开 completion 或 delta 不产生第二个 Gateway 终态。推理、部分工具 JSON、provider
元数据、密钥、主机路径和资源 URI 均不进入七事件 UI 协议。

CLI、Interactive、ALFWorld 和 Coworker 通过同一个 `ApplicationRuntime` 执行。application 持有
Catalog、无 session 状态的执行链、ObservationService、EventBus、SessionManager、device connection
pool、physical-device lease manager 和可选 MCP manager；每个 run
冻结自己的 ToolView、provider request、generation 和 borrowed environment binding。

环境 adapter 继续拥有领域 schema、executor、verifier 和 scorer。通用 runtime 不导入 benchmark
实现，也不把 public event 或模型声明当作领域成功证据。

## Config Resolution

```text
typed defaults
  -> config/homemaster.yaml
  -> HOMEMASTER_<PROVIDER>_{API_KEY,BASE_URL,MODEL,AUTH_TYPE}
  -> request-scoped CLI model override
  -> validated HomeMasterConfig + per-field source labels
```

每一层合并后重新经过 `ProviderProfileConfig.model_validate()`；不能用不执行 validator 的 copy
路径写入 env/CLI 值。provenance 只保存 `default/file/env/cli` 标签，不保存 credential 或宿主机路径。
诊断投影使用 provider public summary 和递归 redaction，真实 config 始终是 ignored mode-0600 文件，
提交的 `.example` 只含占位值。

## Permission And Device Control Flow

```text
Bearer credential (remote only)
  -> configured AuthenticatedPrincipal(tenant, principal, roles, capabilities)
  -> immutable RunRequest.permission_subject
  -> ToolExecutionPipeline permission policy
  -> application connection pool binds borrowed backend to authoritative tenant
  -> physical-device FIFO lease (tenant/device/backend + normalized backend domain)
  -> locked generation/state recheck immediately before backend
  -> one canonical executor
  -> generation/state recheck before release
  -> authoritative device event + mode-0600 device_audit.jsonl
```

prompt、metadata、skill 和 tool arguments 都不是身份 authority。显式 tool allow 只在 capability 已满足
后生效。`:backend` resource key 归并到同一个 physical-device domain，因此别名或环境前缀不能绕过
串行门；不同 identity 保持并发。

connection pool 和 lease manager 都是 application-owned，并在 factory 中绑定为同一个 generation
owner。Runtime 在 session/provider 建立前调用 `bind_borrowed()`；无 `device_identity` 的 adapter 由
首次 typed tenant 建立 authoritative binding，同一 physical backend 的跨 tenant 重绑 fail closed。
disconnect、重复 disconnect、stop 和 close 都使用原子 `fence_next()`，不能从 immutable registration
generation 重算。waiter future 获准后在 registry lock 内二次核对，保证 stop/fence 抢先时不会进入
backend；活动 backend 已被调用时，lease 退出再次核对并产出 `outcome_unknown`。

emergency-stop 不排普通 lease 队列，先提升 generation、拒绝等待者，再调用独立 control path。
Control adapter 必须返回内部 `DeviceControlReceipt`，独立状态查询必须返回
`DeviceStateObservation`；两者都成功且状态为 `DeviceState.STOPPED` 才发布成功，return code 保留在
result、authoritative event 和 JSONL。外部 SDK enum/常量到内部类型的映射在 hkust4 核对前为
`UNVERIFIED`，内部 fake 只能证明 HomeMaster contract，不能证明真设备接受某个符号。

## Skill Data Flow

```text
package builtin + user roots + git-bounded project roots + explicit roots
  -> YAML frontmatter parser
  -> resolved-path containment
  -> source precedence / named builtin override authorization
  -> MCP discovery 完成后的 final Home ToolView alias capability gate
  -> immutable run dependency: SkillRegistry
  -> skill_view progressive disclosure
```

Skill 只声明元数据和 prompt fragment，不拥有 executor、permission 或 robot capability。一个 skill
不能把 ToolView 中 disabled 的工具变为 enabled。自动来源的单项失败记录为 secret-safe issue，避免
破坏 builtin；显式来源 fail-closed。所有替换保留完整 provenance chain。

Home one-shot 与 Interactive 在 composition root 创建同一 registry handle。未配置 MCP 时立即装载；
配置 MCP 时在 application start 完成 discovery、Catalog 注册和 final ToolView freeze 后原子替换其
validated snapshot，再经 `RunRequest.dependencies`
传给 legacy-compatible `skill_view` executor。`disable-model-invocation` 在读取时再次执行，防止仅靠
候选列表过滤后被模型按名称绕过。

ALFWorld 没有公开 `skill_view`，Coworker 继续使用固定的两份 benchmark skill 和严格十一项
ToolView；CL-17 不改变这两个 release profile 的 manifest 或 scorer 输入。

## MCP Lifecycle And Data Flow

```text
sync build (no provider/MCP connection)
  -> first ApplicationRuntime.start() on owner event loop
  -> connect each configured stdio/streamable-HTTP server independently
  -> preserve full discovered JSON Schema + server provenance
  -> preflight all internal-id/model-alias conflicts
  -> atomically register into canonical Catalog
  -> refreeze Home profile with builtin + connected MCP ids
  -> revalidate SkillRegistry against final aliases
  -> every run freezes its own requested subset
  -> application close: artifact/store -> MCP manager -> event resources
```

`start()` 使用 application-owned lock 幂等化，并发首个 run 不会重复创建 MCP subprocess/session。
单个 server 初始化失败只更新该 server typed status；Catalog 仍保留 builtin 与其他成功 server。
注册失败会在 run/provider 创建前关闭整个 application scope，且预检保证 Catalog 不出现部分注册。
断线会移除 active connection、清空其 discovery 状态并 fence 后续调用；timeout 与 caller cancellation
保留不同语义。

MCP SDK 的 mutation annotation 在 hkust4 真环境核对前保持 `UNVERIFIED`。因此 discovered tool 默认
带 `mcp.remote_state` effect，PLAN/default policy 按 mutating fail closed；resource list/read 是显式
只读 host adapter。连接后已尝试的 tool call 若 timeout 或抛出 `McpCallError`，外部是否已修改状态
不可证明，canonical result 必须是 `OUTCOME_UNKNOWN`，不能落成 confirmed failure 后自动重试。

MCP input schema 不投影成浅层 Pydantic model，而是原样进入 `ToolDefinition` 的 canonical JSON
Schema validator。原始 output 在任何 redaction 前写入 `ToolOutputStore`；store 用 opaque random handle、
tenant/session/run 哈希分区、0600 原子文件、quota 和 TTL。模型投影只有 bounded preview、handle、
hash 和 media type。Resource URI 只存在于 application adapter map，模型使用由 server+URI 派生的
opaque `resource_id`。resource raw payload 中的 URI 保留在 tenant ACL artifact 内，但 model preview
会递归移除 URI 字段。生命周期与协议边界另写 `mcp_audit.jsonl`，其中不包含 headers/env 值，
resource 事件只记录 URI hash。Audit sink 故障累积为 typed `McpAuditFailure`，不得改变连接状态或
中止其余连接清理；显式 dry-run probe 使用独立的 mode-0600 `mcp_probe_audit.jsonl`。

## Package Boundary

Builtin `SKILL.md` 和 nested resources 是 wheel package data。发布门会实际构建 wheel、安装进隔离
venv，并从源码 checkout 外使用 `importlib.resources` 读取文件；源码树上的测试通过不能替代该门。

## Gateway、Channel 与公共事件流

```text
Telegram numeric sender id
  -> exact configured principal (tenant/roles/capabilities)
  -> ChannelIdentity(tenant/channel/chat/thread/sender)
  -> deterministic gw-<sha256> session id (metadata is never authority)
  -> attachment realpath containment
  -> Gateway generation + cancel-and-join
  -> existing ApplicationRuntime.run(RunRequest)
  -> private RuntimeEvent appended to application EventBus ledger
  -> events.public_gateway_stream / PublicEventProjection
  -> bounded priority outbound bus
  -> Telegram send
```

Gateway assembly 接收 composition root 已创建的同一个 `ApplicationRuntime`；不创建 per-session
QueryEngine、provider client、ToolView 或 backend。CLI、benchmark 与 Gateway 因此共享 Catalog、
permission boundary、SessionManager 和 application-owned resources。session key 只由 typed identity 的
canonical JSON 哈希产生；group/thread 路由始终包含 sender，不能用 metadata 中的 tenant、sender 或
`session_key_override` 覆盖。

channel bus 同时限制 global、per-tenant 和 per-session occupancy。progress 以 session 合并，满载时
可淘汰；final/error/cancel 不丢，队列全为 critical 时 producer 等待 consumer。close 先拒绝新
producer，丢弃尚未开始处理的 inbound，并在 deadline 内保留 egress 排空 outbound，之后才 stop channel。
同一个 absolute deadline 覆盖 active-run cancel/join、bus drain、channel stop 和全部 service-task join；
join 使用 `asyncio.wait` hard bound，抗取消 worker 只会让 close 返回 false，不会把 deadline 拖成无界。
后台 service task 任一真实异常都会让 supervisor fail-fast。附件必须先通过 exact principal mapping，随后
才允许下载；下载后与 bridge 消费前都做 realpath containment，`..` 与 symlink escape 都 fail closed。

Gateway restart 通过 application `SessionBackend` 恢复纯数据 snapshot，删除没有配对结果的
assistant tool-call tail 与 orphan tool result；下一次 application turn 增加 generation。取消会先请求
application cancellation、join worker，再增加 Gateway generation；旧 worker 即使吞掉 cancellation，
其 final 也因 generation 不匹配被拒绝。live backend、ToolView 和 provider client 从不进入 snapshot。

`RuntimeEvent` 在任何投影前先进入 application ledger。Gateway generation 在 ChannelBridge 创建
RunRequest 时确定，并由 run event sink 写入每个 RuntimeEvent；public backlog 消费时只接受事件自带且
仍为 current 的 generation，绝不把旧事件贴上当前代际。Gateway 本身不接收 private event；events
模块只输出带 session/run/turn/tool-call correlation 的 `PublicGatewayEvent`。投影只允许已知事件，
递归移除 credential/provider/private/raw/path/URI 字段，并对自由文本配置 secret、credential assignment、
宿主路径和 URL query 做脱敏。`assistant.reply` 不作为 Gateway progress 转发；terminal outbound 由
generation-fenced `RunResult` 统一发布，且与 cancel/error 共用 projection，因此不会 duplicate final。

Telegram 使用可选 `python-telegram-bot` long polling，不要求公网 webhook。token 只从
`token_env` 指向的环境变量读取，配置与 repr 不保存值；默认 disabled 且禁止 wildcard principal。
具体 python-telegram-bot 运行时调用在 hkust4 用户指导的真环境核对前保持 `UNVERIFIED`。

## Trusted Extension Data Flow

```text
deployment extensions.approvals
  -> pinned manifest-directory fd + per-component no-symlink source reads
  -> canonical manifest + exact entrypoint/dependency bytes SHA-256
  -> declared flat dependencies imported only from verified bytes
  -> synchronous factory(context) -> validated hooks/tools
  -> requested ∩ deployment grants ∩ run principal capabilities
  -> atomic Catalog registration before final Home ToolView
  -> application_start / run_start / run_end / application_stop hooks
  -> generation-fenced, redacted JSONL lifecycle events
```

The CL-21 MVP is an explicitly approved trusted local Python tier, not an OS sandbox for hostile code.
Only async callbacks are accepted. A callback runs in a separately tracked task; deadline expiry fences its
result immediately, while cancellation-resistant code remains active and blocks reload/cleanup. This still
cannot revoke arbitrary side effects. `RunRequest.enabled_tool_ids=None` inherits the selected profile; an
explicit empty tuple disables every tool, and validation happens before run hooks. Reload fixes extension
id/version/requested/granted capabilities and the complete tool plane; only hook bytes may change without an
application restart. Async reload awaits failed/partial candidate cleanup before returning; sync composition
retains rollback ownership from factory success until ApplicationRuntime construction completes. Failed/partial
candidates release cleanup ownership and never partially mutate Catalog or
the current generation. Close seals reload, quiesces active callbacks, runs `APPLICATION_STOP`, then extension
cleanup before ordinary application resources; diagnostics are recorded without exposing raw free text.
Real source paths are not exposed through module `__file__`, but this trusted-code tier does not prevent code that
hard-codes unrelated absolute paths and must not be described as an OS sandbox.
