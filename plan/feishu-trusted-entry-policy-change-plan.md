# Feishu 完全信任入口策略变更计划

状态：`LOCKED_AFTER_PLAN_REVIEW`

日期：2026-07-22

目标仓库：`/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`

## 1. 用户决策与目标

用户明确接受远程入口风险，并决定不配置 `bot_open_id`、sender `open_id` 白名单或
`open_id -> principal` 映射。飞书是唯一 remote channel，所有合法的非 bot 飞书消息统一视为同一个
完全信任的 owner。此决策覆盖
`plan/feishu-single-channel-openharness-migration-plan.md` 中 exact principal、禁止 wildcard、群 mention
门和相关配置约束；其他 Gateway、生命周期、附件 containment、事件脱敏和 generation fence 不变。

目标数据流固定为：

```text
Feishu event sender/open_id
  -> reject malformed event; best-effort reject sender_type=bot
  -> fixed trusted Feishu owner principal
  -> sender open_id remains routing/reply data only, never authorization data
  -> existing GatewayRuntime / ChannelBridge / ApplicationRuntime
```

## 2. 候选路线与锁定选择

### 方案 1：所有 sender 映射到固定 owner，用户已选择

不做白名单和 mention gate。固定 owner 拥有 Home 本地入口的通用能力，加上飞书建群/改名能力。

代价：任何能向机器人发消息或在机器人所在群发消息的人都能触发工具、设备、MCP 和飞书群操作。
不同 sender 的 session 仍由现有 router 隔离，但权限完全相同。

### 方案 2：首次私聊自动绑定 owner

无需手工查询 open_id，但仍要保存绑定状态、处理抢占、重置和恢复。安全性更好，开发量更大，且用户
明确不需要此门。

### 方案 3：保留 OpenHarness `allow_from=["*"]`

表面允许所有人，但仍保留无实际作用的 allowlist mode 和配置分支，与用户要求的单一信任模型相悖。

### 方案 4：exact open_id principal

安全性最高、现有代码已实现，但要求部署者查询 ID，已被用户明确拒绝。

锁定方案 1。上游决策是“飞书 transport 本身就是 trusted owner boundary”，因此 exact/open/mention 多个
mode 全部消失，不保留隐藏 selector。

## 3. 公开配置和权限契约

从 `FeishuChannelConfig` 删除以下公开字段：

- `bot_open_id`
- `bot_names`
- `group_policy`
- `principals`

enabled 校验只要求成对的 app credential；不再要求 sender 或 bot ID。真实 ignored YAML 和 tracked
example 同步删除上述字段。Telegram 的历史 config/source 不在本次范围。

所有被接收的飞书事件构造同一个 `AuthenticatedPrincipal`：

```text
tenant_id    = config.tenant_id
principal_id = feishu-owner
channel      = feishu
roles        = admin
capabilities = tool.read, tool.mutate, tool.auto,
               device.read, device.control, mcp.call,
               channel.feishu.group.create,
               channel.feishu.group.rename
```

这组值在一个模块级常量中定义并由测试锁定，不从消息 metadata、sender 名称或自由配置派生。sender
`open_id` 仍用于 session partition、私聊回复 receive id 和“当前发送者”建群成员，不能覆盖固定 principal。

## 4. 入站行为

`FeishuChannel.accept_event()` 顺序调整为：

1. 拒绝缺失 sender/message id 的 malformed event；
2. 当 SDK envelope 报告 `sender_type == "bot"` 时拒绝机器人消息；
3. 创建固定 trusted owner principal；
4. 接受 private/group，不检查 mention；
5. 按 message id 去重；
6. 解析正文、下载资源、reaction、发布到 bus。

删除 `principal_for_sender()` 的 lookup 语义和 `_mentions_open_id()`。不新增 fallback mode。群消息无论是否
@机器人都会被处理，这是用户选择的预期行为，不是残留风险。真实 `im.message.receive_v1` 是否把机器人
自身消息回投、回投时 `sender_type` 的准确值尚未核对，因此该检查只作为 best-effort 防护并标记
`UNVERIFIED`，不能宣称已证明不会形成自响应循环。

## 5. SDK 真环境门

使用 ignored YAML 的真实 app credential 和已安装 `lark-oapi==1.7.1` 执行两阶段黑盒探测：

1. 调用 SDK endpoint discovery，要求飞书业务返回码为 `0`；
2. 使用 SDK 返回的 URL 完成真实 WebSocket handshake，要求连接对象已建立并能关闭。

探测输出不得包含 Secret、tenant token 或带 query 的 WebSocket URL。SDK 私有 `_get_conn_url()` 只允许在
一次性诊断探测中使用，并标 `UNVERIFIED` for production API；生产路径仍使用 SDK public `Client.start()`
和 HomeMaster 的可终止子进程隔离。endpoint 返回码和 WebSocket 外部终态必须分别记录，不能用进程存活
或日志替代。

2026-07-22 计划前 linchpin 证据：SDK endpoint return code `0`，真实 WebSocket handshake connected，
627ms 内 socket close 完成。SDK 自带 cache cron 在一次性 event loop 退出时产生 pending-task warning；
生产子进程隔离不依赖该探测器退出行为。

## 6. 测试、文档和验收

先把现有“enabled 必须 principals/bot id”和“未知 sender 拒绝”测试改成 RED 目标：

- enabled config 在完全没有 bot/user ID 时通过；
- 两个不同 sender 都得到相同 fixed owner principal，但 identity/session sender 仍不同；
- 未 mention 的普通群消息被接受；
- 合成 `sender_type=bot` envelope 仍被拒绝；这只锁定 HomeMaster contract，不替代真实事件语义；
- fixed owner 覆盖通用 Home 能力和两个飞书群能力；
- 配置、repr、日志和事件仍不泄漏 credential；
- 既有附件 containment、去重、generation、流式 CLI 回归不变。

完成代码后运行飞书/config/Gateway focused suite、完整 non-live suite、Ruff、format、compileall、lock、
`git diff --check`、secret containment 和上述真实 SDK 两阶段探测。用户可感知的配置与安全语义同步更新
README、用户指南、架构文档、CHANGELOG 和 `progress.md`。

全部实现、测试、文档和 SDK 外部门完成后，按仓库规则启动一次 final code reviewer；处理发现后只做
针对性复验，不追加评审。

## 7. 非目标

- 不修改 CLI/Runtime 流式输出；
- 不新增 ChannelRegistry 或 Telegram/飞书 selector；
- 不实现 pairing、allowlist、managed-group 或 mention mode；
- 不把 credential、URL query 或原始消息写入审计；
- 不宣称消息发送、媒体、reaction、群操作终态已通过；本次外部门只回答 SDK endpoint 和 WebSocket
  是否可连，其他 Phase 9 项仍保持 `UNVERIFIED`；
- 不宣称真实平台不会回投机器人自身消息。由于当前没有 receive target，无法独立制造并观察一条 bot
  出站消息；用户明确选择不提供 bot/user ID 且接受开放入口风险，本次不把该事件语义作为 SDK 建连门。

## 8. 计划评审处置

唯一 plan reviewer 发现：计划把 `sender_type == "bot"` 当成自响应循环的已验证保证，但真实事件取值和
平台回投行为没有外部证据。采纳该发现：实现保留合成 contract 的 best-effort 拒绝，但计划、文档和交付
结论均不再声称它保证无循环；真实语义继续标记 `UNVERIFIED`。不引入 bot ID 自动探测或额外权限 mode，
因为这会偏离用户锁定的“无需任何 ID、只验 SDK 是否可连”范围。
