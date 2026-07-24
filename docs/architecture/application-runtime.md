# Application Runtime 架构

## Ownership

### 实时公开事件流

```text
Provider TransportDelta
  -> Generic AgentRuntime（聚合原始最终消息；逐 delta 立即精确发布）
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
ordinary-name `ToolRegistry`、无 session 状态的 `ToolExecutor`、EventBus、SessionManager、
config/plan/Cron/task/team/child 服务、device connection pool、physical-device lease manager 和可选
MCP manager；每个 run 冻结 provider request、generation 和 borrowed environment binding，不冻结工具子集。

`observe` 是普通的 canonical tool，隐藏 stable id 为 `homemaster.observe.v1`。它从借用 backend 的 `ScreenshotSource` 取得当前 PNG，验证图片后
以 `ResultProjection.IMAGE_ONLY` 交给 provider；模型消息恰好只有一个 image block。截图不进入 action
执行链的授权、freshness、completion 或 provider-binding 状态，因此不会影响 Coworker DOM 或 ALFWorld
动作是否可执行。

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
诊断投影使用各入口的 typed schema；schema 选中的运行时值按 candidate 2 保持原值。真实 config 始终是
ignored mode-0600 文件，提交的 `.example` 只含占位值。

## Permission And Device Control Flow

```text
Bearer credential (remote only)
  -> configured AuthenticatedPrincipal(tenant, principal, roles, capabilities)
  -> immutable RunRequest.permission_subject
  -> ToolExecutor ordinary-name lookup + PermissionChecker
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
OpenHarness bundled < Home builtin < user root < git-bounded project roots < explicit roots < plugin roots
  -> YAML frontmatter parser
  -> resolved-path containment
  -> source precedence / named builtin override authorization
  -> dynamic SkillRegistry handle（不等待 MCP，不校验 tool_names）
  -> Available Skills name/description summary
  -> skill(name) / compatible skill_view(skill_name) / slash invocation
  -> complete original content + base_dir
```

Skill 是 OpenHarness-compatible instruction document，不拥有 executor、permission 或 robot capability；
`tool_names` 即使存在也只是未解释扩展元数据。一个 Skill 不能修改 Registry 或授予能力。
自动来源只扫描 HomeMaster 自己的 user/project 目录；外部 agent 目录需显式迁入或配置。
自动来源的单项失败记录为 bounded typed issue，显式来源 fail-closed，所有替换保留 provenance chain。
Plugin 来源是 data-only adapter：只解析 `plugin.json`/`.claude-plugin/plugin.json` 与受 containment
保护的 `skills_dir`，不调用 executable extension loader、不导入 Python、不注册 tools/hooks/MCP。
项目 plugin 默认关闭；plugin 即使优先级最后也不能绕过 builtin 精确覆盖授权。

Home one-shot、Interactive 与 Gateway 在 composition root 创建同一 registry handle。标准 `skill` 与
兼容 `skill_view` 每次读取前刷新，因此新写入的 Skill 在同一进程立即可见；slash resolver 复用同一
索引并把已配置的 Skill model 写入 run 级 override。MCP 只向 application Registry 原子追加工具，
不重载或验证 Skills。
八份 bundled Markdown 使用 package data 进入安装 wheel，不能依赖源码 checkout 路径。
隔离 wheel 门会安装核心依赖并在源码 checkout 外构造 universal Registry、逐项核对 ordinary-name 工具。Pillow
属于默认 `observe` 的核心依赖；MCP-only adapter 使用 manager 分支内 lazy import，未安装 `mcp` extra
时不得阻断 Registry 构造。

## Universal Tool 与 Service Flow

```text
Home + ALFWorld + Coworker tools + optional MCP dynamic tools
  -> one application ToolRegistry keyed by ordinary name
  -> PermissionChecker / immutable working_directory
  -> application-owned config/plan/Cron/task/team/child/MCP service
  -> executor and external return-code check
  -> verifier inside the same per-resource lease
  -> typed result / JSONL audit / session snapshot
```

所有入口看到相同 Registry；环境只提供 Backend，缺失能力返回明确错误。文件路径由 composition 时锁定的 working directory
统一解析；写入的 lease 覆盖 executor 和独立 readback verifier。默认 child worker argv 显式包含父应用
config path，避免子进程退回仓库默认 provider。Cron scheduler 由 `homemaster cron start/status/stop`
管理；task/team/plan/config 均使用 application-owned store。后台 shell/agent 任务在独立进程组中运行，
stop、close 和超时必须终止整个进程组并等待真实子进程退出。
管理面除 `tool.mutate` 外按职责独立要求 `process.spawn`、`scheduler.manage`、`config.mutate` 或
`mcp.manage`。`config(action="show")` 仍受 registered tool 权限和 typed schema 约束；进入模型消息与
JSONL trace 的已选字段保持原值，不按 key、URL 或配置字面值改写。

远程 `ask_user_question` 不保持 webhook task：executor 返回嵌套在 canonical `ToolResultMessage` 中的
`waiting_user` marker，ApplicationRuntime 持久化包含 assistant tool call 与 tool result 的 snapshot，
ChannelBridge 结束当前 run 且不发送重复 terminal。下一条 inbound 自动设置 `resume=True`，恢复同一
session 后把用户答案追加到完整历史。停止判据解析实际序列化 envelope，而不是 executor 的中间 dict。

ALFWorld 和 Coworker 不再创建环境 ToolView，也不缩窄模型可见工具；它们只绑定各自 Backend。
固定 benchmark skill、manifest 与 scorer 输入仍由 benchmark owner 管理，不参与运行时工具授权。

## MCP Lifecycle And Data Flow

```text
sync build (no provider/MCP connection)
  -> first ApplicationRuntime.start() on owner event loop
  -> connect each configured stdio/streamable-HTTP server independently
  -> preserve full discovered JSON Schema + server provenance
  -> preflight all ordinary-name conflicts
  -> atomically register into the application ToolRegistry
  -> every run uses the same Registry handle
  -> application close: artifact/store -> MCP manager -> event resources
```

`start()` 使用 application-owned lock 幂等化，并发首个 run 不会重复创建 MCP subprocess/session。
单个 server 初始化失败只更新该 server typed status；Registry 仍保留 builtin 与其他成功 server。
注册失败会在 run/provider 创建前关闭整个 application scope，且原子注册保证 Registry 不出现部分写入。
断线会移除 active connection、清空其 discovery 状态并 fence 后续调用；timeout 与 caller cancellation
保留不同语义。

MCP SDK 的 mutation annotation 在 hkust4 真环境核对前保持 `UNVERIFIED`。因此 discovered tool 默认
带 `mcp.remote_state` effect，PLAN/default policy 按 mutating fail closed；resource list/read 是显式
只读 host adapter。连接后已尝试的 tool call 若 timeout 或抛出 `McpCallError`，外部是否已修改状态
不可证明，canonical result 必须是 `OUTCOME_UNKNOWN`，不能落成 confirmed failure 后自动重试。

MCP input schema 不投影成浅层 Pydantic model，而是原样进入 `ToolDefinition` 的 canonical JSON
Schema validator。原始 output 写入 `ToolOutputStore`；store 用 opaque random handle、tenant/session/run
哈希分区、0600 原子文件、quota 和 TTL。授权文本 result/preview 保持原值，并带 handle、hash 和 media
type；binary 只通过 artifact/opaque reference 投影。Resource discovery 使用由 server+URI 派生的 opaque
`resource_id`，读取后的 URI/content 在授权 typed result 和 `mcp_audit.jsonl` 中保持原值。Audit sink
故障累积为 typed `McpAuditFailure`，不得改变连接状态或
中止其余连接清理；显式 dry-run probe 使用独立的 mode-0600 `mcp_probe_audit.jsonl`。

## Package Boundary

Builtin `SKILL.md` 和 nested resources 是 wheel package data。发布门会实际构建 wheel、安装进隔离
venv，并从源码 checkout 外使用 `importlib.resources` 读取文件；源码树上的测试通过不能替代该门。

## Gateway、Channel 与公共事件流

```text
Feishu SDK WebSocket subprocess
  -> typed IPC event envelope
  -> reject malformed / best-effort sender_type=bot
  -> fixed trusted feishu-owner principal (admin + canonical capabilities)
  -> ChannelIdentity(tenant/channel/chat/thread/sender)
  -> immutable ChannelDeliveryContext(source message/reply target)
  -> deterministic gw-<sha256> session id (metadata is never authority)
  -> attachment realpath containment
  -> Gateway generation + cancel-and-join
  -> existing ApplicationRuntime.run(RunRequest)
  -> private RuntimeEvent appended to application EventBus ledger
  -> events.public_gateway_stream / PublicEventProjection
  -> bounded priority outbound bus
  -> Feishu REST send/upload/reply
```

Gateway assembly 接收 composition root 已创建的同一个 `ApplicationRuntime`；不创建 per-session
QueryEngine、provider client、工具视图或 backend。CLI、benchmark 与 Gateway 因此共享 Registry、
permission boundary、SessionManager 和 application-owned resources。session key 只由 typed identity 的
canonical JSON 哈希产生；group/thread 路由始终包含 sender，不能用 metadata 中的 tenant、sender 或
`session_key_override` 覆盖。

channel bus 同时限制 global、per-tenant 和 per-session occupancy。progress 以 session 合并，满载时
可淘汰；final/error/cancel 不丢，队列全为 critical 时 producer 等待 consumer。close 先拒绝新
producer，丢弃尚未开始处理的 inbound，并在 deadline 内保留 egress 排空 outbound，之后才 stop channel。
同一个 absolute deadline 覆盖 active-run cancel/join、bus drain、channel stop 和全部 service-task join；
join 使用 `asyncio.wait` hard bound，抗取消 worker 只会让 close 返回 false，不会把 deadline 拖成无界。
后台 service task 任一真实异常都会让 supervisor fail-fast。飞书 transport 是部署者明确选择的 trusted
owner boundary：任意非 bot sender 都得到同一个固定 principal/capabilities，sender `open_id` 只用于回复、
建群成员和 sender-isolated session，不参与授权。Feishu 的顺序固定为 malformed/bot best-effort reject、
固定 principal、message-id dedup、外部资源、安全落盘、reaction、入站 publish；群消息无需 mention。
下载后与 bridge 消费前仍做 realpath containment，`..` 与 symlink escape 都 fail closed。真实平台是否回投
机器人自身消息以及回投时 `sender_type` 的值仍为 `UNVERIFIED`，不能把合成测试当作无循环证明。

Gateway restart 通过 application `SessionBackend` 恢复纯数据 snapshot，删除没有配对结果的
assistant tool-call tail 与 orphan tool result；下一次 application turn 增加 generation。取消会先请求
application cancellation、join worker，再增加 Gateway generation；旧 worker 即使吞掉 cancellation，
其 final 也因 generation 不匹配被拒绝。live backend、Registry 和 provider client 从不进入 snapshot。

`RuntimeEvent` 在任何投影前先进入 application ledger。Gateway generation 在 ChannelBridge 创建
RunRequest 时确定，并由 run event sink 写入每个 RuntimeEvent；public backlog 消费时只接受事件自带且
仍为 current 的 generation，绝不把旧事件贴上当前代际。Gateway 本身不接收 private event；events
模块只输出带 session/run/turn/tool-call correlation 的 `PublicGatewayEvent`。投影只允许已知事件，
按 event type 及字段 allowlist 复制结构，并保持已选文本原值。`assistant.reply` 不作为 Gateway progress 转发；terminal outbound 由
generation-fenced `RunResult` 统一发布，且与 cancel/error 共用 projection，因此不会 duplicate final。

`ChannelDeliveryContext` 只从认证后的 SDK envelope 构造，并复制到 progress、MEDIA、final、error 和
cancel；renderer metadata 不能覆盖 receive/reply target。thread 分区仍包含 sender，Reply API 使用
准确 source message id，私聊不会因 chat id 猜测而误创建 thread。ArtifactPublisher 在 tool dispatch 后
把 image/attachment bytes 写入 tenant/session/run ACL store，但不改写 canonical result 或模型消息；
provider-facing content 保留模型需要的图片，opaque artifact refs 只进入 `tool.call_completed` 公共投影，
Gateway 将每个 ref 独立变成不可合并 MEDIA。

composition root 先创建唯一 application-owned `FeishuApiService` 和 `FeishuGroupOperations`，注册两个
typed 群工具，再让 application、channel 和 Gateway 借用同一资源。群 create 从当前 route sender 派生
成员，rename 只作用于当前 group chat；operation id 锁定 target，timeout 为 `outcome_unknown`，外部成功
还必须经独立 chat/member read 验证。

`lark-oapi` 没有已验证的 public stop/close API，因此 WebSocket client 被隔离到 spawn 子进程；stop 在
同一 deadline 内 terminate/join，必要时 kill/join，不交付残留线程。子进程 completion/fatal 通过 typed
queue 回传 `channel.start()`。REST 与 SDK logger 及 `FeishuApiService.__repr__` 按 candidate 2 保持运行时
文本原值；结构化调用日志仍只包含既有 allowlist 字段。`app_id/app_secret` 优先来自 ignored、mode-0600
的真实 YAML，旧环境变量仅作兼容回退。

真实 `lark-oapi==1.7.1` 已验证 chat list、message create 和独立 message get：业务返回 code 0，唯一
canary 回读精确一致。媒体、reaction、群操作、重连和 `lark` domain 仍为 `UNVERIFIED`，不能由 import
成功或 helper receipt 替代。

## Trusted Extension Data Flow

```text
deployment extensions.approvals
  -> pinned manifest-directory fd + per-component no-symlink source reads
  -> canonical manifest + exact entrypoint/dependency bytes SHA-256
  -> declared flat dependencies imported only from verified bytes
  -> synchronous factory(context) -> validated hooks/tools
  -> requested ∩ deployment grants ∩ run principal capabilities
  -> approved contributions adapted and atomically registered by ordinary name
  -> application_start / run_start / run_end / application_stop hooks
  -> generation-fenced, field-limited exact-text JSONL lifecycle events
```

The CL-21 MVP is an explicitly approved trusted local Python tier, not an OS sandbox for hostile code.
Only async callbacks are accepted. A callback runs in a separately tracked task; deadline expiry fences its
result immediately, while cancellation-resistant code remains active and blocks reload/cleanup. This still
cannot revoke arbitrary side effects. `RunRequest` has no tool-filter field; deployment approval determines which
extension contributions enter the Registry, and validation happens before run hooks. Reload fixes extension
id/version/requested/granted capabilities and the complete tool plane; only hook bytes may change without an
application restart. Async reload awaits failed/partial candidate cleanup before returning; sync composition
retains rollback ownership from factory success until ApplicationRuntime construction completes. Failed/partial
candidates release cleanup ownership and never partially mutate the Registry or
the current generation. Close seals reload, quiesces active callbacks, runs `APPLICATION_STOP`, then extension
cleanup before ordinary application resources; diagnostics retain exact text with the existing length bound.
Real source paths are not exposed through module `__file__`, but this trusted-code tier does not prevent code that
hard-codes unrelated absolute paths and must not be described as an OS sandbox.
