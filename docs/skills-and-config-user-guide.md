# Skills 与配置用户指南

## Provider 配置

HomeMaster 只读取 YAML 真理源。新环境先创建被 Git 忽略的真实配置：

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/homemaster.yaml
```

每个 provider 必须声明 `name`、`kind`、`api_format`、`transport`、`base_url` 和
`model`。Anthropic SDK provider 的认证类型显式写为 `api_key` 或 `auth_token`：

```yaml
providers:
  default: Mimo
  items:
    - name: Mimo
      kind: chat
      api_format: anthropic
      transport: anthropic_sdk
      auth_type: auth_token
      base_url: https://provider.example/anthropic
      model: your-chat-model
      api_keys: ["<your-api-key>"]
```

有效优先级固定为 `defaults < file < provider-specific env < limited CLI`。provider 名先
转成大写，并把非字母数字字符转成下划线；例如 `Mimo` 支持：

```bash
export HOMEMASTER_MIMO_API_KEY='...'
export HOMEMASTER_MIMO_BASE_URL='https://provider.example/anthropic'
export HOMEMASTER_MIMO_MODEL='your-chat-model'
export HOMEMASTER_MIMO_AUTH_TYPE='auth_token'
```

通用的 `ANTHROPIC_*` 环境变量不会覆盖 HomeMaster 配置，避免同一 shell 中其他工具的
ambient credential 改变运行身份。CLI 只允许选择已配置 provider 和临时覆盖 model：

```bash
uv run homemaster -p '检查药盒状态' \
  --provider-name Mimo \
  --model your-chat-model
```

先用无外部连接的 dry-run 核对 model、字段来源、工具和 skill：

```bash
uv run homemaster --dry-run -p '检查药盒状态' \
  --provider-name Mimo \
  --model your-chat-model \
  --output-format json
```

`doctor`、dry-run、日志和事件只输出来源、有效性和认证数量；认证字段及 Bearer/Basic
值会递归脱敏。

## Skill 来源与优先级

Home profile 按以下顺序发现 `SKILL.md`：

1. 安装包内 builtin。
2. `~/.homemaster/skills`、`~/.agents/skills`、`~/.claude/skills`。
3. 从 Git root 到当前目录逐层发现 `.homemaster/skills`、`.agents/skills`、
   `.claude/skills`，越靠近当前目录优先级越高。
4. `skills.explicit_dirs` 中显式配置的目录。

一个 skill 目录的最小格式如下：

```text
~/.homemaster/skills/check_inventory/
└── SKILL.md
```

```yaml
---
name: check_inventory
description: Check current inventory and report it.
tool_names: [observe, robot_verify]
user-invocable: true
disable-model-invocation: false
---

Use a fresh observation before reporting inventory state.
```

`tool_names` 必须全部存在于当前 Home frozen ToolView。自动 user/project 来源若格式不兼容、
路径越界或引用 disabled tool，会被拒绝并出现在 dry-run 的 `skill_diagnostics`，builtin 仍保持
可用；`explicit_dirs` 的错误则直接失败。

同名 builtin 默认不可覆盖。确需替换时，同时配置目录和精确名称授权：

```yaml
skills:
  explicit_dirs:
    - ~/.homemaster/approved-skills
  allowed_builtin_overrides:
    - fetch_object
```

Skill 文件和它引用的资源在读取前都会解析真实路径，并要求仍位于授权 root。绝对路径、`..`
以及通过 symlink 逃出 root 的资源都会被拒绝。`disable-model-invocation: true` 的 skill 可保留为
用户元数据，但 `skill_view` 不会向模型返回它。

## MCP 配置与诊断

先安装可选依赖：

```bash
uv sync --extra dev --extra mcp
```

stdio 与 streamable HTTP 配置示例：

```yaml
mcp:
  servers:
    local_tools:
      transport: stdio
      command: python
      args: ["/absolute/path/to/server.py"]
      env:
        MCP_TOKEN: "<your-mcp-token>"
      enabled: true
    remote_tools:
      transport: http
      url: https://mcp.example/mcp
      headers:
        Authorization: "Bearer <your-mcp-token>"
      enabled: true
  artifact_root: ~/.homemaster/artifacts/tool-output
  artifact_quota_bytes: 67108864
  artifact_ttl_seconds: 604800
  preview_chars: 4000
```

`env`、`headers` 和 URL userinfo 在 config summary、状态、异常和 audit 中都会脱敏。WebSocket
配置当前只返回 `unsupported_transport`，不会尝试连接。普通 dry-run 不产生外部 I/O：

```bash
uv run homemaster --dry-run -p '检查外部工具' --output-format json
```

只有显式 probe 才连接配置中的 server，完成 discovery 后立即关闭：

```bash
uv run homemaster --dry-run --probe -p '检查外部工具' --output-format json
```

probe 生命周期写入 mode-0600 `observability.trace_dir/mcp_probe_audit.jsonl`，与真实 run 的
`mcp_audit.jsonl` 分开。Resource audit 只记录 URI 的 opaque SHA-256 引用，不记录 URI、query token
或本地路径。

发现的工具 alias 形如 `mcp__<server>__<tool>`，连字符会规范化为下划线。MCP skill 必须引用该
最终 alias；application 会在 discovery 和 Home ToolView 冻结完成后再校验 skill。每个 run 仍可
通过自己的 enabled tool ids 禁用 MCP 工具。

在 MCP SDK read-only/mutation annotation 完成真环境核对前，discovered tool 默认视为 mutating：
PLAN 模式拒绝，DEFAULT 模式要求确认，连接后 timeout/call failure 返回 `outcome_unknown` 且不自动
重试。只有 host 提供的 `mcp_list_resources`、`mcp_read_resource` 明确保持只读；相关外部 annotation
当前为 `UNVERIFIED`。

MCP tool/resource 返回的原始 JSON 会先写入 `artifact_root`，按 tenant/session/run 精确 ACL、
tenant quota 和 TTL 管理。模型只看脱敏且最多 `preview_chars` 的 preview、内容哈希和 opaque handle；
resource URI 字段不进入模型上下文，`mcp_list_resources` 返回的 opaque `resource_id` 用于后续读取。
同一 tenant 的不同 principal 使用同一个 tenant quota/ACL domain，但仍受各自的权限策略约束。

## 权限模式与远程身份

权限配置示例：

```yaml
permissions:
  mode: full_auto
  allowed_tools: []
  denied_tools: []
  path_rules:
    - pattern: "/restricted/**"
      allow: false
  denied_commands:
    - "rm -rf *"
```

`plan` 拒绝写操作，`default` 要求写操作具有 `tool.auto` 或经过确认，`full_auto` 不额外要求确认。
这三个 mode 只约束已有 capability，绝不会授予 capability。默认本地 `RunRequest` 为兼容既有 CLI/
benchmark 拥有完整本地能力；远程入口必须由 Bearer credential 映射到配置中的 tenant、principal、
roles 和 capabilities，不能从请求 metadata 或 prompt 读取这些字段。

常用 capability 为 `tool.read`、`tool.mutate`、`tool.auto`、`device.read`、`device.control` 和
`mcp.call`。`allowed_tools` 也不能绕过缺失 capability；`denied_tools`、受保护 credential 路径、
显式 path deny 和 command deny 始终先拒绝。

设备连接属于 application。每个 `RunRequest.environment` 在 provider 执行前自动绑定到首次调用者的
tenant；同一个 physical backend 不能被另一个 tenant 重新绑定。断线、重复断线、急停和 application
close 都从同一个 generation owner 单调取下一代并 fence；waiter 获准后仍会在进入 backend 前锁内
复核 generation/state。已经执行的写动作返回 `outcome_unknown`，调用方不得自动重放。

emergency-stop 要求 `device.control`。Adapter 必须把真实 control 返回与独立状态查询分别规范化为
`DeviceControlReceipt` 和 `DeviceStateObservation`；raw 字符串或 dict 不会被当作成功。两个 typed
return code 都写入结果与完成事件。具体机器人 SDK 的 enum/状态字符串在 hkust4 真机核对前均为
`UNVERIFIED`。审计位于
`observability.trace_dir/device_audit.jsonl`，包含 lease/fence/stop 状态，不记录 credential。

## Telegram Gateway

Gateway 默认关闭。先安装可选依赖，并把真实 token 放在 ignored 配置之外的环境变量：

```bash
uv sync --extra dev --extra gateway
export HOMEMASTER_TELEGRAM_BOT_TOKEN='真实 token'
```

在 `config/homemaster.yaml` 中只填写 sender 的 numeric Telegram id 与 capability：

```yaml
gateway:
  enabled: true
  bus_capacity: 128
  per_tenant_capacity: 64
  per_session_capacity: 32
  telegram:
    enabled: true
    token_env: HOMEMASTER_TELEGRAM_BOT_TOKEN
    tenant_id: home
    attachment_root: ~/.homemaster/attachments/telegram
    principals:
      "123456789":
        principal_id: operator
        roles: [operator]
        capabilities: [tool.read, tool.mutate, device.read]
```

每个 sender 必须显式列出；不支持 `"*"` wildcard。Telegram bot 通过 long polling 启动，不需要
公网 webhook：

```bash
uv run homemaster gateway --config config/homemaster.yaml
```

Gateway 把 private/group/thread 消息按 tenant、channel、chat、thread、sender 生成稳定 session；
消息 metadata 不能覆盖这些字段。sender 必须先命中 exact principal mapping，未授权消息不会触发
Telegram 文件查询或下载。通过认证后的图片/文件必须落在 `attachment_root`，symlink 或 `..`
逃逸会被拒绝。progress 可能合并，终态 final 只发送一次，error/cancel 会保留；关闭时会在
`shutdown_deadline_s` 内 drain。普通 `homemaster --dry-run` 不启动 Telegram、不读取 token、
不产生网络 I/O。

当前 Telegram 运行时库的具体 API 返回语义仍标记为 `UNVERIFIED`，需在用户指导的 hkust4 真机门
核对；HPC2 non-live 测试只证明 HomeMaster typed boundary、queue、routing、recovery 与 redaction。

## Trusted Extensions

扩展默认关闭；只有部署者明确批准的本地 manifest 才会加载。配置示例：

```yaml
extensions:
  approvals:
    - manifest_path: /opt/homemaster/extensions/audit/manifest.json
      extension_id: example.audit
      version: 1.0.0
      expected_sha256: <host-computed-64-lowercase-hex>
      granted_capabilities: [hook.lifecycle, tool.register, extension.audit.read]
      enabled_tool_ids: [plugin.audit.query.v1]
```

`expected_sha256` 由 HomeMaster 对 canonical manifest JSON、entrypoint 和声明依赖精确字节重新计算；不信任
扩展自报 hash。plugin tool 必须声明 `required_capabilities`，实际调用能力是 manifest requested、
部署 grant 与当前 principal capabilities 的交集。request 中的 `enabled_tool_ids` 只能从 profile
已有集合中删减，CLI/Gateway metadata 不能扩张工具面；省略该字段继承 profile，显式空集合禁用全部
工具。exact tool/hook token 不能替代 plugin/hook 的 canonical required capability。

manifest 可选声明 flat dependency files：

```json
{
  "schema_version": 1,
  "extension_id": "example.audit",
  "version": "1.0.0",
  "requested_capabilities": ["hook.lifecycle"],
  "entrypoint": "extension.py",
  "dependencies": ["helper.py"]
}
```

host digest 覆盖 canonical manifest、entrypoint 和排序后的 dependency bytes；声明依赖只从已验证 bytes
import，修改 `helper.py` 会使 approval hash 失效。依赖当前只接受同目录、非 `__init__.py` 的 flat
Python 文件。未声明的同目录 import 会拒绝，真实批准目录也不会通过 `__file__` 暴露；但这是 trusted
code 内容锁定，不阻止硬编码任意外部绝对路径，也不是 hostile-code sandbox。

MVP 只接受 trusted local async Python hooks，支持 application/run start/end/stop、matcher、priority、
cooperative timeout/cancel 和 blocking result。deadline 会立即 fence 返回结果；若可信 callback 抗取消，
它仍被计为 active，reload、stop cleanup 都会拒绝越过它。它不是 hostile-code sandbox，不能撤销 callback
已做的任意副作用，也不能取代 permission、device safety、terminal、verifier 或 scorer。reload 只允许
hooks-only 变化；extension version、requested/granted capability、工具面或 provenance 变化需要重启。
HPC2 non-live 会验证这些边界，hkust4 真机和
外部 provider/device gate 等全部代码完成后再按用户指导执行。
