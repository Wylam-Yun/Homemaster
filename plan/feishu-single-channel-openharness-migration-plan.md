# HomeMaster 单飞书 OpenHarness 迁移实施计划

状态：`LOCKED_AFTER_PLAN_REVIEW`

日期：2026-07-22

目标仓库：`/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`

参考实现：`/hpc2hdd/home/wyuan140/weilin_workspace/OpenHarness`

## 1. 决策摘要

本计划锁定的产品决策是：HomeMaster Gateway 只运行一个飞书 Channel，不同时运行 Telegram、Slack、
钉钉或其他 IM，不建设 `ChannelRegistry`，不增加多通道路由分支。

目标数据流固定为：

```text
Feishu SDK
  -> FeishuChannel
  -> HomeMaster InboundMessage
  -> existing GatewayRuntime / ChannelBridge
  -> Application.run(RunRequest)
  -> existing Runtime / EventBus / RunResult
  -> HomeMaster OutboundMessage
  -> FeishuChannel
  -> Feishu SDK
```

迁移策略不是重写飞书，也不是复制 OHMO Gateway。实施时以 OpenHarness
`src/openharness/channels/impl/feishu.py` 为主体迁移飞书 SDK、消息解析、静态渲染、附件、reaction、
线程和群操作代码；把输入输出边界改成 HomeMaster 的 typed identity、principal、generation、
attachment 和 public projection 契约。

本次不重写 AgentRuntime、Provider stream 或 RuntimeEvent 命名；只允许在 ApplicationRuntime tool dispatch
边界接入 ArtifactPublisher。不新增一套 OHMO `GatewayStreamUpdate`，不让飞书直接订阅 CLI
`StreamEvent`，不在 Gateway 核心加入 `if channel == "feishu"` 的多通道分支。

## 2. 为什么仍然保留现有 Gateway

飞书 SDK 输入只有外部平台身份和消息数据，例如 `open_id`、`chat_id`、`message_id`、消息正文和附件；
HomeMaster Runtime 需要的是可信 `RunRequest`，包括固定 session、tenant、principal、capability、附件根和
取消 generation。

现有 Gateway 已经拥有以下不变量，不能在 `FeishuChannel` 内重写一份：

- `ChannelIdentity -> ChannelRoute -> session_id` 的确定性会话映射；
- exact `AuthenticatedPrincipal` 到 `PermissionSubject` 的权限转换；
- 新消息取消旧 generation，并拒绝旧 progress/final 回流；
- `RunResult` terminal final 的唯一所有权，避免重复 final；
- 公开事件投影、脱敏、队列背压、关闭排空和 shutdown deadline；
- 附件进入 Runtime 前的允许根校验。

因此 GatewayRuntime 保留。需要修改的是 composition root 和飞书边界，不是 Gateway 执行模型。

## 3. 候选路线与选择

### 方案 1：单飞书直接装配，推荐并锁定

`build_gateway_assembly()` 只构造一个 `FeishuChannel`，`GatewayRuntime.serve(channel)` 保持单通道接口。

代价：不支持 Telegram 与飞书同时运行；公开 Gateway 配置从 Telegram 迁移为飞书。

收益：改动最小，不增加 registry、dispatcher 和跨通道故障隔离。

### 方案 2：单通道 selector

配置选择 `telegram` 或 `feishu`，仍只启动一个 Channel。

代价：需要处理 mutually-exclusive 配置、迁移、分支测试和两套依赖；用户已决定只做飞书，没有必要。

### 方案 3：ChannelRegistry 多通道

一个消费者按 `identity.channel` 分发到多个 Channel。

代价：增加多通道生命周期、故障隔离、配置和防串消息验证；明确不在本次范围。

### 方案 4：FeishuChannel 绕过 Gateway 直接调用 `Application.run()`

表面代码少，但会把 session、principal、generation、取消、脱敏和 terminal ownership 重写进飞书文件。

结论：拒绝。它只是把 Gateway 隐藏进 Channel，增加重复和安全风险。

## 4. 交付范围

### 4.1 必须交付

- `lark-oapi` WebSocket 长连接接收飞书事件；
- 国内飞书与国际 Lark domain 配置；
- 私聊、群聊、群内 mention 策略和 thread/root 回复；
- 文本、post、interactive/share 等入站内容解析；
- 图片、音频、视频、普通文件入站下载；
- 短文本、富文本 post、Markdown、代码块、表格和静态 interactive card 出站渲染；
- 图片、音频、视频、普通文件出站上传和发送；
- 仅在消息确定会处理后添加 reaction；
- message/event 确定性去重；
- 飞书创建群、修改群名的 typed Channel capability；
- exact `open_id -> ChannelPrincipalConfig` 映射；用户可把自己的 `open_id` 配成高权限 principal；
- HomeMaster Gateway 单飞书装配、配置、依赖、测试、文档和真飞书终态验收。

### 4.2 明确不做

- Telegram 与飞书并行运行；
- `ChannelRegistry`、动态插件发现或多通道路由；
- OHMO `OhmoSessionRuntimePool`、`GatewayStreamUpdate`、OHMO Bridge；
- OHMO workspace、cwd 和 managed-group registry 所有权模型；
- 飞书消息正文逐 token 刷新、CardKit 原地更新和 `transport.delta` Gateway 放行；
- CLI/Rich 界面调整；
- 用自由 metadata 覆盖 session、principal、role 或 capability；
- 通配符 principal；
- 未经真实飞书环境验证就宣称 SDK WebSocket stop、API enum 或 emoji 常量可用。

### 4.3 群操作能力边界

创建群和改群名必须迁移为可测试的 typed `FeishuGroupOperations` 能力，并由 application-owned 共享
Feishu API service 持有，Gateway/Channel/工具只借用该资源。
本计划不复制 OHMO managed-group registry，也不把群聊自动绑定到 OHMO workspace。

实施阶段在现有 Tool Catalog 中增加模型可调用的显式工具，分别声明
`channel.feishu.group.create` 和 `channel.feishu.group.rename` capability；工具只通过
`FeishuGroupOperations` 抽象取资源，不直接 import `FeishuChannel`。这两个工具属于本计划必须交付项，
只在 Gateway/飞书配置启用时注册到 Home profile；是否允许执行由现有 PermissionPolicy 在调用时根据
principal 的准确 capability 判定，不能使用 metadata 临时提权或绕过 Tool Catalog/PermissionPolicy。

## 5. 当前代码事实

### HomeMaster

- `GatewayAssembly.channel` 当前写死为 `TelegramChannel`；
- `build_gateway_assembly()` 使用 `config.telegram.attachment_root` 并直接构造 Telegram；
- `serve_gateway()` 要求 `gateway.telegram.enabled`；
- `GatewayRuntime.serve(channel)`、ingress、egress、generation fence 和 public event loop 已经是
  `BaseChannel` 形态，可以复用；
- `InboundMessage` 已包含可信 `ChannelIdentity`、`AuthenticatedPrincipal`、attachments 和
  correlation id；
- `OutboundMessage` 当前只有 identity、session、generation、kind、content、correlation 和 metadata，
  缺少 typed outbound attachments/reply target；
- `PublicEventProjection` 不允许 `transport.delta`，因此本计划不会意外把逐字正文发到飞书；
- terminal final 由 `ChannelBridge` 根据 `RunResult` 唯一发布。

### OpenHarness

- `FeishuChannel` 已包含 SDK client、WebSocket、解析、reaction、Markdown/post/card/table、上传下载、
  thread reply、创建群和改名；
- OpenHarness `InboundMessage`/`OutboundMessage` 使用自由字段和 metadata，并允许
  `session_key_override`；不能原样进入 HomeMaster；
- OpenHarness 授权发生在 `BaseChannel._handle_message()`，而飞书处理器在此前可能解析联系人或下载资源；
  HomeMaster 必须把 exact principal 检查前移到任何外部资源调用之前；
- OpenHarness WebSocket 运行在线程和独立 loop 中，`stop()` 只设置 `_running=False`；是否能在
  HomeMaster shutdown deadline 内真实结束仍为 `UNVERIFIED`；
- OpenHarness `send()` 部分失败只记录日志并返回，不能满足 HomeMaster 对返回码和外部终态的要求。

## 6. 目标契约

### 6.1 Feishu 配置

新增 `FeishuChannelConfig`，公开字段固定如下；实现可增加纯内部派生值，但不得新增第二种通道选择模式：

```yaml
gateway:
  enabled: true
  bus_capacity: 128
  per_tenant_capacity: 64
  per_session_capacity: 32
  shutdown_deadline_s: 5.0
  feishu:
    enabled: true
    # app_id: cli_replace_with_app_id
    # app_secret: replace_with_app_secret
    app_id_env: HOMEMASTER_FEISHU_APP_ID
    app_secret_env: HOMEMASTER_FEISHU_APP_SECRET
    encrypt_key_env: HOMEMASTER_FEISHU_ENCRYPT_KEY
    verification_token_env: HOMEMASTER_FEISHU_VERIFICATION_TOKEN
    tenant_id: local
    domain: feishu
    bot_open_id: ou_replace_with_bot_open_id
    bot_names: [HomeMaster]
    group_policy: mention
    react_emoji: EYES
    attachment_root: ~/.homemaster/attachments/feishu
    principals:
      ou_replace_with_owner_open_id:
        principal_id: owner
        roles: [admin]
        capabilities:
          - tool.read
          - tool.mutate
          - tool.auto
          - device.read
          - device.control
          - mcp.call
          - channel.feishu.group.create
          - channel.feishu.group.rename
```

约束：

- 用户在 2026-07-22 明确要求真实飞书凭证直接保存在 ignored YAML 中。`app_id/app_secret` 因此作为
  首选来源，旧 `*_env` 字段只保留部署兼容。解析真值表锁定为：YAML 两项非空则只用 YAML；YAML 两项
  均为空时 env 两项非空才使用 env；任一来源只有一项、纯空白、或试图跨来源拼接都启动失败。tracked
  example 中两个直读字段必须保持注释占位，不能用非空占位符遮蔽真实 env；
- `app_secret` 使用 Pydantic `SecretStr`。只允许 `FeishuApiService` credential resolver 和
  `configured_sensitive_values()` 两个受控边界调用 `get_secret_value()`：前者返回成对凭证及无敏感值的
  `file|env` 来源，后者只注册 sanitizer。配置 repr、str、两种 model_dump、model_dump_json、validation
  error、public summary、traceback、日志和事件不得出现原值；
- `domain` 使用内部 enum `feishu|lark`，Channel 内映射 URL，避免任意 URL 配置；
- enabled 时必须有至少一个 exact principal；拒绝 `"*"`；
- `group_policy` 第一版只允许 `mention|open`。`managed_or_mention` 依赖 OHMO registry，不迁移；
- `open` 只决定群消息是否进入授权检查，不绕过 sender principal；未映射 sender 仍拒绝；
- `mention` 必须配置并在启动 probe 中核对真实 bot `open_id`；策略门只接受 SDK 结构化 mention 中精确
  匹配的 bot id。`bot_names` 只用于展示和清理正文，不能作为授权依据；
- 示例配置只含注释占位符，真实配置保持 gitignored 和 mode 0600；测试逐项覆盖上述真值表、最终
  credential source、`configured_sensitive_values()` 收集直读 secret 和 Pydantic 输出面。交付 shell gate
  还必须证明 `git check-ignore` 成功、mode 恰为 0600、`git ls-files` 为空，并扫描全部 tracked 文件、普通
  diff 与 staged diff 均不含真实 secret。

### 6.2 Inbound 映射

飞书事件必须确定性映射为：

```text
tenant_id = config.feishu.tenant_id
channel = "feishu"
sender_id = sender.open_id
chat_id = group ? message.chat_id : sender.open_id
thread_id = message.thread_id or message.root_id or None
chat_type = private | group
correlation_id = stable message_id/event_id
principal = config.principals[sender.open_id]
attachments = verified files under configured attachment_root
```

`correlation_id` 对消息事件固定使用 `message_id`；event id 只记录为审计证据，不能在重连时切换 dedup key。

新增 immutable `ChannelDeliveryContext`，由通过认证后的 SDK envelope 构造：

```text
receive_id_type = open_id | chat_id
receive_id
source_message_id
root_id
thread_id
chat_type
```

它进入 `InboundMessage`，由 Gateway 按 `session_id + generation` 保存，并复制到每个 progress、media、final、
error 和 cancel `OutboundMessage`。`thread_id` 只用于 session 分区；飞书 Reply API 必须使用准确
`source_message_id`，禁止用 chat id 前缀猜 `receive_id_type`，也禁止从 renderer metadata 覆盖 reply target。

群会话继续使用现有 `ChannelRouter` 的 sender 隔离语义：同一群/thread 的不同 sender 拥有不同 session，
不共享历史或 capability。共享群会话不在本计划范围。

顺序固定：

```text
parse minimum event identity
  -> reject bot/invalid sender
  -> exact local principal lookup
  -> group mention policy
  -> deterministic dedup claim
  -> external contact/media calls
  -> safe attachment persistence
  -> reaction
  -> publish InboundMessage
```

未授权 sender 的联系人查询、附件下载、reaction 和入站发布次数必须全部为零。

### 6.3 Outbound 消息扩展

不把媒体、reply target 或操作塞进无约束 metadata。新增 typed 值：

```text
OutboundArtifactRef
  artifact_handle
  run_id
  filename
  media_type
  content_sha256

OutboundMessage
  existing fields
  attachments: tuple[OutboundArtifactRef, ...]
  delivery_context: ChannelDeliveryContext
```

附件来源固定为 application-owned artifact store：

- Tool 输出的 `ResultImage`/`ResultAttachment` 先进入 application-owned store，得到 `hm-artifact:` opaque
  handle；
- `ArtifactPublisher` 在 `ApplicationRuntime` tool dispatch 后使用
  `permission_subject.tenant_id + session_id + run_id` 写入 `ToolOutputStore`，只返回供 Gateway 公共投影
  使用的 handle/hash/filename refs；它不得改写 canonical result 或 provider-facing message content；
- 私有 provider-facing ToolResultMessage 可继续按现有契约携带模型确实需要的 image bytes，但
  RuntimeEvent 的公开投影、PublicGatewayEvent、OutboundMessage 和日志只能出现 opaque refs，不能出现
  base64 原文或宿主路径；
- 现有 `tool.call_completed` 事件只公开经过格式验证的 opaque handle，不新增第二套媒体事件名；
- Gateway/FeishuChannel 通过注入的 artifact resolver，使用 authoritative identity tenant、OutboundMessage
  session 和 ref run id 读取 bytes 和 metadata；不得把 store 校验降级成 tenant-only；
- 原始宿主路径不进入 RuntimeEvent、PublicGatewayEvent、OutboundMessage、日志或模型上下文；
- 禁止从模型文字、Markdown 链接或自由 metadata 解析任意宿主路径；
- 发送前再次核对 artifact ownership、TTL、长度和 hash，再在飞书 attachment staging root 下生成受控临时
  文件供 SDK 上传；
- staging 文件必须是 regular file、非 symlink、位于固定 root，并在发送完成/失败后按 ownership 清理；
- Telegram 实现仍存在时，接口新增字段必须有一致性 audit，不能漏实现。

新增 `ChannelEventKind.MEDIA`，它是 critical、不可合并、不可被 progress eviction。带 artifact refs 的
`tool.call_completed` 映射为一个或多个 MEDIA outbound；普通工具状态仍是可合并 PROGRESS。连续产生多个
文件时必须逐 artifact 保留，不能只留下最后一个。

### 6.4 Delivery receipt

`BaseChannel.send()` 改为返回 typed `DeliveryReceipt`，至少包含：

```text
status = confirmed_success | confirmed_failure | partial_success | outcome_unknown
operation
platform_ids
api_code
api_message
sent_count
failed_count
```

Gateway egress 固定行为：

- `confirmed_success`：完成消费；
- 非成功 PROGRESS：脱敏记录，不自动重试，继续服务；
- 非成功 MEDIA/FINAL/ERROR/CANCEL：抛带 receipt 的 `ChannelDeliveryError`，由 supervisor fail-fast；
- `partial_success` 和 `outcome_unknown` 永不自动重试，避免重复文件、卡片、reaction 或 final；
- Telegram 实现同步接口并有 audit test，即使它不再进入 active Gateway composition。

### 6.5 出站渲染

FeishuChannel 内部保留 OpenHarness 的静态自动选择：

```text
short plain text -> text
medium links/rich text -> post
code/table/long markdown -> interactive card
image -> image upload + image message
audio/video -> file upload + media message
other file -> file upload + file message
```

所有 `lark-oapi` request builder、message type、domain、emoji type 和 response 字段在真环境核对前标
`UNVERIFIED`。发送 API 非成功必须抛 typed delivery failure，不能只打日志后让 Gateway 当成成功。

### 6.6 去重

为保持本次单飞书迁移成本与 OpenHarness 行为一致，第一版只使用带 TTL 和容量上限的进程内 dedup cache，
key 固定为 app id hash、tenant 和 `message_id`。

要求：

- 同一进程内的重复投递和重连补发只执行一次；
- claim 必须在下载附件、reaction 和 Runtime 执行之前完成；
- claim 后保留到 TTL 到期；处理失败或外部结果不确定也不能立即删除并自动重试；
- 测试逐 key 断言，禁止用任意一条成功掩盖重复执行。

明确限制：进程重启后的 exactly-once 不在本计划承诺范围，飞书重新投递可能再次执行。若以后要求跨重启
exactly-once，必须单独建设 durable inbox 与 run idempotency，不能把文件 claim 当成原子事务。

### 6.7 群创建和改名

定义 typed receipt，至少保留：

```text
operation_id
action
chat_id
requested_name
api_success
api_code
api_message
outcome_certainty
verified_state
```

创建群第一次决定 operation id 后必须锁定，重试期间不得重新生成目标。若请求超时且无法查询确认，返回
`outcome_unknown`，禁止自动再次创建。改名同样核对返回码，并通过独立读取群信息验证最终群名。

群工具不接受任意 `user_open_id` 或 `chat_id`：

- create 输入只有规范化 group name，成员 open id 从当前 authenticated sender 派生；
- rename 输入只有新名称，目标固定为当前 group route；private route 调用 rename 在任何 API 前拒绝；
- `FeishuGroupOperations` 维护 `session_id -> authenticated ChannelIdentity` 的当前 generation binding，
  Gateway submit/cancel/reconnect 时同步更新或清理；
- 两个工具都声明 mutating state effect、准确 required capability、显式确认策略和 verifier；
- execution-time `PermissionPolicy` 是授权门。工具可以存在于 profile，但 capability 缺失必须在任何飞书
  API 前返回 typed denied，计划不修改 Runtime 做 principal-aware 工具隐藏。

composition root 固定为：`serve_gateway()` 先创建 application-owned `FeishuApiService/
FeishuGroupOperations`，把群工具注册进 Home profile 后创建 Application，再用同一个 API service 构造
`FeishuChannel` 和 Gateway assembly。Application 关闭前由 resource scope 统一释放 API service；Channel
不能各自创建第二套 REST ownership。

## 7. 实施阶段

### Phase 0：基线和外部 linchpin 核对

在修改产品代码前完成：

1. 记录 git status、当前 Gateway/配置/通道测试基线；
2. 在 HomeMaster 项目虚拟环境中加入锁定的 `lark-oapi` 候选依赖，不使用裸 `pip install -U`；
3. 核对实际安装版本、OpenHarness 使用的 request/response symbol 是否存在；存在不代表可用，仍标
   `UNVERIFIED`；
4. 使用真实飞书测试应用核对 WebSocket 建连、事件订阅、鉴权返回和可用 app scopes；
5. 最关键：证明 WebSocket worker 可以在 deadline 内真实停止并 join；
6. 核对文本发送 API 的成功返回码，并在飞书客户端确认消息真实出现；
7. 如果 SDK 没有可用停止能力，停止编码并在以下替代中选择：
   - verified public stop/close API；
   - 将长连接隔离到可终止子进程，通过 typed IPC 进入主进程；
   - 改用 webhook 部署。

推荐顺序为 public stop，其次子进程。不得交付残留线程方案。

Phase 0 失败属于真实 blocker；不能用 mock 或 import 成功跳过。

### Phase 1：依赖和配置

- `gateway` optional extra 从 `python-telegram-bot` 迁移为 `lark-oapi` 并更新 lock；
- 新增 `FeishuChannelConfig`、环境变量名、domain/group policy validator；
- `GatewayConfig` 删除 active `telegram` 字段并新增唯一 active `feishu` 字段；CLI enabled 检查只认飞书；
- `GatewayAssembly.channel` 改为 `BaseChannel`；
- example 配置、config provenance、secret redaction 和 validator 测试同步更新；
- `TelegramChannel`/`TelegramChannelConfig` 源文件和针对性单测本次不顺手删除，但它们不再进入
  Gateway composition、optional extra、公开配置或文档；CHANGELOG 明确这是 Gateway 配置 breaking
  migration，后续如要恢复 Telegram 必须另立计划。

### Phase 2：HomeMaster 飞书适配器骨架

- 新增 `src/homemaster/channels/impl/feishu.py`；
- 复制 OpenHarness 与平台有关的纯函数和静态渲染逻辑；
- 删除 OHMO workspace/path/managed registry import；
- 先构造 application-owned `FeishuApiService/FeishuGroupOperations`，再让 Channel 复用；
- constructor 改用 HomeMaster config、共享 API service 和 `BoundedPriorityBus`；
- 实现 exact `principal_for_sender(open_id)`；
- 实现单通道 `start/stop/send` 接口；
- 对所有 SDK 外部调用记录脱敏结构化 JSONL：action、message/chat hash、耗时、return code、certainty；
- 不记录 app secret、token、原始附件路径或未脱敏消息全文。

### Phase 3：入站文本、群聊和线程

- 迁移 text/post/share/interactive parser；
- 先完成 principal 和 group policy，再做联系人查询、下载和 reaction；
- mention 只信结构化 bot open id，增加纯文本伪造、同名用户和错误 open id 拒绝；
- 将 source message、receive address、root/thread、chat type 映射到 typed identity/delivery context；
- 不允许 `session_key_override`；session 只由 `ChannelRouter` 决定；
- 接入进程内 TTL dedup；
- 私聊、群聊、thread 分别做单元和 Gateway 顶层集成测试。

### Phase 4：入站附件

- 迁移图片、音频、视频、文件下载；
- attachment root 在 assembly 创建并交给 `AttachmentPolicy`；
- 拒绝绝对文件名、`..`、分隔符、symlink 和 root escape；
- 下载完成后独立核对 regular file、root containment、非零字节和 SDK 返回码；
- 未授权 sender 和重复事件不得触发任何下载。

### Phase 5：出站文本、卡片和媒体

- 迁移 text/post/card/table 选择和渲染；
- 扩展 typed outbound attachment/reply contract；
- 在 ApplicationRuntime tool dispatch 后调用 `ArtifactPublisher`，将 `ToolExecutionResult` 的
  image/attachment 持久化为 opaque artifact handle；原始结果继续生成 provider-facing message，refs
  只进入现有 `tool.call_completed` 公共投影并转成不可合并 MEDIA `OutboundArtifactRef`；
- `ChannelBridge` 保持唯一 terminal final，不从 `assistant.reply` 再发一份；
- 迁移图片/文件/音视频上传发送；
- partial delivery 返回 typed error/receipt，不能吞掉失败；
- progress 可以发送思考/工具摘要，但本计划不放行 `transport.delta`；
- 保持 progress 可丢弃/合并，MEDIA/final/error/cancel 不可丢。

### Phase 6：reaction 和群操作

- reaction 只在授权、group policy 和 dedup claim 全部通过后执行；
- 创建 `FeishuGroupOperations` 和 typed receipt；
- 迁移 create/rename SDK 调用，加入以 tool_call_id/operation_id 锁定目标的进程内 operation ledger 和
  outcome certainty；跨进程 exactly-once 同样不承诺；
- 注册模型可调用的 create/rename 工具；工具只接受 name，目标从当前 session/generation 的可信 route
  派生，PermissionPolicy 在外部调用前检查 capability；
- 外部成功必须同时通过 API success 和飞书侧状态读取。

### Phase 7：单飞书装配和生命周期

- `build_gateway_assembly()` 使用 `config.feishu.attachment_root` 并构造 `FeishuChannel`；
- `serve_gateway()` 收集 app secret、encrypt key、verification token 以及其他非空凭证真实 env 值，注入
  `PublicEventProjection` 和结构化日志 sanitizer；app id 只作为标识，不按 secret 记录原文；
- 对 `lark-oapi` HTTP/WS logger 设定安全级别和过滤器；测试让异常故意包含每项 secret、authorization
  header 和 URL query，断言 repr、日志、progress、final 和 doctor 均不泄漏；
- 保留现有 ingress/egress/public-event/active worker supervisor；
- Feishu WebSocket worker completion/fatal error 必须回传到 `channel.start()`，使现有 supervisor 能
  fail-fast；后台线程死亡不能只写日志；
- 修改 `GatewayRuntime.aclose()`：无论 outbound drain 成功、失败或超时，都必须在同一个 absolute
  deadline 内请求 `channel.stop()` 并 join worker；close success 仍要求 active、drain、channel、service
  全部完成，drain 失败时返回 false 但不能留下长连接；
- 证明 WebSocket worker、SDK资源、outbound drain 和 application 全部结束；
- 不新增多通道 selector 或 registry。

### Phase 8：测试和内部门

至少增加：

- config validation、env secret、example config 和 provenance；
- Feishu parser、mention/group policy、domain、card/table renderer；
- exact principal、未授权零外部调用；
- message dedup 和重连补发；明确测试进程重启不承诺 exactly-once；
- attachment traversal/symlink/root containment；
- outbound text/post/card/image/audio/video/file；
- API failure、partial failure、timeout/outcome_unknown；
- typed delivery context、thread reply 和 private chat 不误进 thread；
- MEDIA 不被 progress coalesce/eviction，多文件逐个保留；
- ArtifactPublisher 不改写 provider-facing message；公共事件只携带 opaque refs，并按
  tenant/session/run 回读；
- generation fence、duplicate final、progress coalescing；
- interface audit：所有 `BaseChannel` 实现覆盖公开方法；
- delivery receipt 的 egress 行为；
- outbound drain 超时仍执行 channel stop，shutdown deadline 后无残留 worker；
- group create/rename idempotency和 typed receipt；
- create member/current rename target 不能被模型参数覆盖，private rename 零 API 调用；
- worktree side-effect audit。

内部测试通过不等于完成，必须继续 Phase 9。

### Phase 9：真实飞书外部终态验收

每个目标逐项记录 API return code、message/chat id，并通过独立 REST read/query 重新读取最终状态；不得用
发送 helper 自己返回的 success 作为唯一证据：

1. 私聊纯文本输入触发一次 Runtime，飞书收到唯一 final；
2. 群聊无 mention 按 policy 忽略，mention 后只处理一次；
3. thread/root 输入回复到正确 thread，私聊不错误创建话题回复；
4. text、post、代码块、Markdown 表格、interactive card 分别由独立消息读取确认类型与内容正确；
5. 图片、音频、视频和普通文件逐实例上传、发送，再通过独立 resource download 核对 hash/大小；
6. reaction 通过独立 reaction list/read 确认真正在目标 message 上出现；
7. 同一进程内重复投递同一 message 不重复执行 Runtime、下载、reaction 或发送；
8. 创建群只创建一个，独立 chat/member query 确认返回 chat_id、目标成员和群名正确；
9. 改名后独立读取到准确新名称；
10. 未映射 open_id 被拒绝，且联系人/附件/reaction/Runtime 调用全部为零；
11. SDK 断线后重连可恢复；主动停止和 outbound drain 超时两种路径都没有残留线程/子进程；
12. Feishu/Lark 两个 domain 配置至少完成可用性核对；没有对应真实租户的一侧标未验收，不能声称通过。

所有多媒体类型按类型分别断言，不能用“任意一个媒体发送成功”作为聚合 PASS。

### Phase 10：文档、变更记录和交付

- 更新 `README.md` 能力清单和启动命令；
- 更新 `docs/skills-and-config-user-guide.md` 的真实配置示例；
- 更新 `docs/architecture/application-runtime.md` 的单飞书数据流、安全边界和 terminal ownership；
- 更新 `config/homemaster.example.yaml`；
- 更新 `THIRD_PARTY_NOTICES.md`；
- 更新 `CHANGELOG.md`，内容与最终 commit message 同源；
- 若发现非显而易见 SDK/真环境坑，追加到 `docs/pitfalls.md` 最上方，并在 `CLAUDE.md` 提炼正向规则；
- 更新 `progress.md` 的当前状态、下一步、阻塞项和关键环境事实；
- 完成所有代码、测试、外部终态和文档后，启动唯一一次 final code reviewer。

## 8. 预期文件范围

预计新增或修改：

```text
pyproject.toml
uv.lock
src/homemaster/config/config.py
src/homemaster/config/__init__.py
src/homemaster/application/runtime.py             # ArtifactPublisher 接入点
src/homemaster/cli/composition.py                 # 共享 Feishu service/tool composition
src/homemaster/channels/contracts.py
src/homemaster/channels/impl/base.py              # 仅接口确需时
src/homemaster/channels/impl/feishu.py            # 新增，主体迁移
src/homemaster/channels/bridge.py
src/homemaster/channels/router.py                 # 仅 thread/attachment 映射确需时
src/homemaster/gateway/runtime.py
src/homemaster/cli/gateway_command.py
src/homemaster/artifacts/...                      # ArtifactPublisher/typed resolver
src/homemaster/tools/contracts.py                 # artifact replacement contract
src/homemaster/...feishu group operations/tools
tests/homemaster/channels/test_feishu.py
tests/homemaster/gateway/test_runtime.py
tests/homemaster/...feishu live gate
config/homemaster.example.yaml
README.md
docs/skills-and-config-user-guide.md
docs/architecture/application-runtime.md
THIRD_PARTY_NOTICES.md
CHANGELOG.md
progress.md
```

禁止为了迁移顺手重构 Runtime、EventBus、CLI renderer 或其他 Channel。

## 9. 失败语义和可观测性

所有外部操作统一区分：

```text
not_attempted
confirmed_success
confirmed_failure
outcome_unknown
partial_success
```

以下情况不得自动重试：

- 创建群请求已发出但响应超时；
- 文件/卡片部分发送后后续调用失败；
- reaction 返回不确定且无法查询；
- WebSocket 事件已经 claim，但 Runtime 是否执行完成无法确认。

结构化日志必须覆盖：入站 event claim、principal decision、group policy、下载/上传、消息发送、reaction、
群操作、WebSocket connect/reconnect/stop、Gateway generation 和耗时。日志只保留稳定 hash/opaque id，
不得泄漏 secret、credential、宿主路径或原始附件内容。

## 10. 完成定义

只有以下全部满足才算完成：

- Phase 0 外部 linchpin 通过，所有实际使用的 SDK symbol 从 `UNVERIFIED` 转为有证据的 verified；
- 单飞书 Gateway 从真实飞书消息进入真实 HomeMaster Runtime；
- 私聊、群聊、mention、thread、reaction、静态卡片和所有媒体类型逐项通过；
- create/rename 同时通过 API return code 和独立飞书最终状态；
- 未授权 sender 的外部资源调用为零；
- 同一进程内 duplicate message 只执行一次；跨进程 exactly-once 明确不承诺；
- typed delivery context 确保 private/group/thread 回复到准确目标；
- MEDIA 不被 progress 合并或驱逐，多个 artifact 逐个送达；
- delivery receipt 非成功按 critical/progress 规则处理且不自动重复外部副作用；
- terminal final 唯一，旧 generation 不回流；
- shutdown 无残留 worker；
- 测试、lint、format、interface audit 和 worktree side-effect audit 全绿；
- README、用户指南、架构、example config、第三方通知、CHANGELOG 和 progress 同源更新；
- final reviewer 的发现逐条处理并完成针对性验证。

## 11. 实施顺序约束

1. 本计划完成唯一一次 plan review 并锁定前，不开始产品代码实现；
2. Phase 0 的 SDK 生命周期和真实文本发送 linchpin 未通过，不进入大规模复制；
3. 先失败测试，再单一实现；不得一边复制一边猜外部符号；
4. 身份检查必须先于外部资源调用；mention 必须使用结构化 bot open id；
5. 先接通私聊文本端到端，再逐项加入群聊、附件、渲染、reaction、群操作；
6. 每一类外部能力都保留返回码和独立终态门；
7. 全部实现、测试、真环境和文档完成后才允许 final code review。

## 12. 计划评审处理记录

唯一一次 plan review 结论为 `CHANGES_REQUIRED`，发现均已处理：

1. 群操作目标不可信：create 成员改为 authenticated sender，rename 只允许当前 group route，模型不接收
   `open_id/chat_id`；
2. 群工具装配与 principal hiding 不可落地：锁定共享 API service 先于 Application 创建，工具注册到 profile，
   现有 PermissionPolicy 做 execution-time capability 门，不修改 Runtime 做工具隐藏；
3. thread 回复缺 source message：新增 typed `ChannelDeliveryContext` 并按 session/generation 传播；
4. artifact ownership 不完整：新增旁路 ArtifactPublisher，ref 携带 run id，读取继续核对
   tenant/session/run，且不改写 provider-facing tool result；
5. 媒体会被 progress 合并：新增 critical `ChannelEventKind.MEDIA`；
6. drain 失败不 stop Channel：锁定无论 drain 结果都在同一 deadline 内 stop/join，并传播 WebSocket worker
   fatal error；
7. delivery receipt 无接口：锁定 `BaseChannel.send() -> DeliveryReceipt` 和 egress 非成功行为；
8. persistent dedup 崩溃语义未定义：为符合最低成本单飞书目标，明确降级为进程内 TTL dedup，不承诺跨
   进程 exactly-once。

Reviewer 未发现外部符号误背书；所有 `lark-oapi` builder、message type、domain、emoji、response 和
WebSocket stop 行为继续保持 `UNVERIFIED`，直到 Phase 0 真环境核对。
