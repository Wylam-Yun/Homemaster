# Skills 与配置用户指南

## CLI 实时输出与管道

`homemaster -p PROMPT` 默认实时输出纯文本。文本 delta 会立即 flush，进程结束时
不会再回显完整答案，因此重定向文件就是一次完整、精确保真的最终文本：

```bash
homemaster -p "列出可用工具" > answer.txt
homemaster -p "列出可用工具" --output-format json | jq .final_reply
homemaster -p "列出可用工具" --output-format stream-json | jq -c .
```

`json` 是结束后一次输出的单文档；`stream-json` 是 UTF-8 JSON Lines，事件逐行
flush，最后恰好一行 `type=result`。若启动阶段无法形成 `RunResult`，则最多输出
一行 typed `type=error`，不伪造 result。交互模式使用 Rich 展示模型等待、助手
Markdown、工具开始/结束、错误、状态与压缩进度；机器输出 stdout 不含 ANSI。
Rich 完整显示 terminal command，成功只显示状态，失败详情最多 500 字符并带明确截断标记；机器结果不截断。

## Provider 配置

HomeMaster 只读取 YAML 真理源。新环境先创建被 Git 忽略的真实配置：

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/homemaster.yaml
```

wheel 安装不依赖源码目录；用 `HOMEMASTER_CONFIG_PATH=/absolute/path/homemaster.yaml` 指向部署配置。
未设置时保持源码 checkout 的 `config/homemaster.yaml` 默认值。

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

### V2.1 记忆 embedding 配置

默认 Home profile 还要求一个独立的 `MemoryEmbedding` provider。它只供结构化记忆的 semantic
写入和搜索使用，不替代聊天 provider，也不复制或复用聊天凭证：

```yaml
providers:
  items:
    - name: MemoryEmbedding
      kind: embedding
      api_format: openai
      transport: openai_sdk
      base_url: https://api.siliconflow.cn/v1
      embedding_url: https://api.siliconflow.cn/v1/embeddings
      model: Qwen/Qwen3-Embedding-8B
      api_keys: ["<your-memory-embedding-key>"]

memory:
  enabled: true
  data_root: ~/.homemaster/memory
  embedding_provider_name: MemoryEmbedding
  embedding_dimensions: 4096
```

`search_memories` 的 query 与版本化 search text 会发送到这个第三方 endpoint；API key、opaque
evidence ref、procedure URL query/fragment 和真实 input value 会在出站前拒绝或剔除。结构化记录通过
embedded MindMemOS 原生 schema pipeline 写入本地 Qdrant 和 Neo4j。运行 `uv run homemaster doctor --json` 可查看
`memory_backend` 的静态配置与迁移状态；doctor 不打开 backend。实际启动失败时五个结构化工具统一返回
`memory_backend_unavailable`，文件 `memory` 工具仍可用。
`memory.enabled: false` 会一起移除六工具和 frozen 文件上下文，不会静默切换到第二个 backend。完整用法见
[记忆用户指南](memory-user-guide.md)。

`doctor`、dry-run、config tool、日志和事件各自只输出 typed schema 选中的字段；经过本地权限和 ownership
边界后，这些字段的运行时文本按 candidate 2 保持原值，包括认证字段、URL userinfo/query 和路径。
真实配置必须保持 mode 0600 且 Git ignored，仓库模板仍只能使用占位值。

## Skill 来源与优先级

Home profile 按以下顺序发现 `SKILL.md`：

1. 安装包内八份 OpenHarness bundled Skills。
2. HomeMaster builtin Skills。
3. `~/.homemaster/skills`。
4. 从 Git root 到当前目录逐层发现 `.homemaster/skills`，越靠近当前目录优先级越高。
5. `skills.explicit_dirs` 中显式配置的目录。
6. `skills.plugin_roots` 中显式配置的 data-only plugin，以及明确启用的项目 plugin。

`.codex/skills`、`.claude/skills` 和 `.agents/skills` 都不会被自动扫描。要使用其中的 Skill，迁移
完整目录到 `~/.homemaster/skills`，或把受信目录显式加入 `skills.explicit_dirs`。

Plugin 来源支持根目录下 `<plugin>/plugin.json` 或 `<plugin>/.claude-plugin/plugin.json`。adapter
只读取 manifest 的 `name`、`enabled_by_default`、`skills_dir` 和其中的 `SKILL.md`，不会导入 Python、
tools、hooks、commands、agents 或 MCP。项目 `<git-root>/.homemaster/plugins` 默认关闭：

```yaml
skills:
  plugin_roots:
    - ~/.homemaster/plugins
  enabled_plugins:
    disabled-by-default-plugin: true
    unwanted-plugin: false
  allow_project_plugin_skills: false
```

`skills_dir` 必须是 plugin 内的相对目录；绝对路径、`..` 和 symlink 逃逸会被拒绝。Plugin 来源优先级
最后，但替换 bundled/builtin 仍需在 `allowed_builtin_overrides` 中精确授权。

一个 skill 目录的最小格式如下：

```text
~/.homemaster/skills/check_inventory/
└── SKILL.md
```

```yaml
---
name: check_inventory
description: Check current inventory and report it.
user-invocable: true
disable-model-invocation: false
---

Use a fresh observation before reporting inventory state.
```

Skill 是给模型按需读取的说明文档，不是 capability 声明；`tool_names` 不是必填字段，旧文件中的该
字段只作为未解释的扩展元数据保留。Skill 正文提到某个工具不会修改 universal Registry，也不会授予
权限。自动 user/project 来源若格式不兼容或路径越界，会被拒绝并出现在 dry-run 的
`skill_diagnostics`，builtin 仍保持可用；`explicit_dirs` 的错误则直接失败。

同名 builtin 默认不可覆盖。确需替换时，同时配置目录和精确名称授权：

```yaml
skills:
  explicit_dirs:
    - ~/.homemaster/approved-skills
  allowed_builtin_overrides:
    - fetch_object
```

Skill 文件和它引用的资源在读取前都会解析真实路径，并要求仍位于授权 root。绝对路径、`..`
以及通过 symlink 逃出 root 的资源都会被拒绝。模型上下文只列名称和简介；唯一的
`load_skill(name="check_inventory")` 会在调用时重新
发现并返回完整原文与 `base_dir`。用户也可输入 `/check_inventory 参数`；`user-invocable`、
`disable-model-invocation`、`argument-hint` 和已配置的 `model` 覆盖按 OpenHarness 调用语义生效。

## 安装和运行外部 Skill

外部 Skill 必须完整迁入，不能只复制 `SKILL.md` 而遗漏 `references/`、`scripts/` 或 `assets/`：

```bash
git clone https://github.com/OWNER/REPO.git /tmp/skill-repo
cp -a /tmp/skill-repo/path/to/example-skill ~/.homemaster/skills/
uv run homemaster --dry-run -p '列出 Skills' --output-format json
```

也可以直接让 HomeMaster 使用 bundled `skill-creator` 处理 GitHub repository URL 或
`blob/<ref>/<path>/SKILL.md` URL。它会 clone 一次、先枚举并校验全部目标冲突，再在
`~/.homemaster/skills` 同文件系统 staging；任一冲突会在复制前阻塞，发布或 fresh Registry 验证失败会
回滚本次目录。成功后仍应从新进程逐名调用 `load_skill(name=...)`，并按相对文件列表与 SHA-256 对比上游。

新增或修改 `SKILL.md` 后会动态发现，不需要重启。Home profile 的 `terminal`、`search_files`、文件和联网工具可以按 Skill
说明执行非交互脚本、解压、Git 以及项目隔离依赖安装，但必须遵守项目依赖管理：Python 使用临时/项目
虚拟环境和 lock 工具，禁止裸全局 `pip install -U`；npm 安装在目标项目或 Skill 自己的隔离目录。
命令返回码为 0 只证明进程成功，工作流还必须用独立文件、版本或外部状态读取确认终态。

## 终端与文件搜索

`terminal` 是模型可完全控制的命令入口。模型自己选择 `rg`、`grep`、`find` 或系统中的其他程序；HomeMaster
不会因为某个程序不可用而偷偷替换命令，失败时会返回真实退出码，模型可以根据结果改用其他程序。

`search_files` 是普通搜索的结构化快捷入口。`target=content` 搜索内容，`target=files` 搜索文件名；
`include_hidden`、`respect_gitignore`、`file_glob`、`limit` 和 `timeout_seconds` 都可以明确指定。Runtime
优先使用 `rg`，再按搜索目标回退到 `grep` 或 `find`，并通过与 `terminal` 相同的进程组监督层执行，因此超时
会真正终止搜索及其子进程，而不是只返回一条超时文字。

例如：

```json
{"pattern":"银杏-4827-KM","path":".","target":"content","timeout_seconds":120}
```

后台 Cron scheduler 由以下命令管理：

```bash
uv run homemaster cron start
uv run homemaster cron status
uv run homemaster cron stop
```

远程入口调用 `ask_user_question` 时，本轮返回等待态并持久化问题；用户下一条消息会恢复同一 session
及完整工具历史。子 agent 的默认 worker 会显式继承父应用使用的 config path，因此 provider/model/
endpoint 与父进程保持同源。

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

`env`、`headers` 和 URL userinfo 在 config summary、状态、异常和 audit 的已选文本字段中保持原值。WebSocket
配置当前只返回 `unsupported_transport`，不会尝试连接。普通 dry-run 不产生外部 I/O：

```bash
uv run homemaster --dry-run -p '检查外部工具' --output-format json
```

只有显式 probe 才连接配置中的 server，完成 discovery 后立即关闭：

```bash
uv run homemaster --dry-run --probe -p '检查外部工具' --output-format json
```

probe 生命周期写入 mode-0600 `observability.trace_dir/mcp_probe_audit.jsonl`，与真实 run 的
`mcp_audit.jsonl` 分开。Resource discovery 对模型使用 opaque `resource_id`；授权 read 的 URI/content 和
audit 文本保持原值，binary 及仅用于 transport 的宿主路径仍只通过 ACL artifact/opaque reference 暴露。

发现的工具 alias 形如 `mcp__<server>__<tool>`，连字符会规范化为下划线。Skill 可以在正文中说明
该 alias，但不会因此获得该工具；Skills 的发现和读取也不等待 MCP discovery。application Registry
统一提供已批准工具，每次调用仍必须通过 PermissionChecker；run metadata 不能筛选或扩张工具面。

在 MCP SDK read-only/mutation annotation 完成真环境核对前，discovered tool 默认视为 mutating：
PLAN 模式拒绝，DEFAULT 模式要求确认，连接后 timeout/call failure 返回 `outcome_unknown` 且不自动
重试。只有 host 提供的 `list_mcp_resources`、`read_mcp_resource` 明确保持只读；相关外部 annotation
当前为 `UNVERIFIED`。

MCP tool/resource 返回的原始 JSON 会写入 `artifact_root`，按 tenant/session/run 精确 ACL、tenant quota 和
TTL 管理。授权文本 result/preview 在 `preview_chars` 边界内保持原值，并带内容哈希和 opaque handle；
`list_mcp_resources` 返回 opaque `resource_id` 用于后续读取，binary 不直接进入文本上下文。
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

交互式 CLI 把本地审批公开为 `full_auto|confirm|plan` 三个易读选项。默认仍为 `full_auto`，便于自动化测试；
只有显式运行 `homemaster --permission-mode confirm` 或
`homemaster shell --permission-mode confirm` 才把内部策略映射为 `default` 并移除本地 principal 的
`tool.auto`。此时 mutating tool 会在终端显示工具名、工作目录和校验后的参数，只有输入 `y` 或 `yes`
才执行；空输入、其他输入、EOF、Ctrl+C 或输入错误均拒绝。只读工具和 `allowed_tools` 不询问，`plan`
直接拒绝写操作也不询问。审批拒绝发生在 resource lease 和 backend 调用之前。

`--permission-mode` 仅属于交互式入口，不能与 `-p/--print`、`--dry-run`、`--gateway` 或其他子命令组合。
非交互入口和配置文件的既有权限行为不变。

常用 capability 为 `tool.read`、`tool.mutate`、`tool.auto`、`device.read`、`device.control`、
`filesystem.read`、`filesystem.write`、`network.http`、`process.exec` 和 `mcp.call`。任务/agent/team、
Cron、配置修改和 MCP 凭证管理还分别要求 `process.spawn`、`scheduler.manage`、`config.mutate`、
`mcp.manage`；`allowed_tools` 不能绕过其中任何一项。`denied_tools`、受保护 credential 路径、显式
path deny 和 command deny 始终先拒绝。

设备连接属于 application。每个 `RunRequest.environment` 在 provider 执行前自动绑定到首次调用者的
tenant；同一个 physical backend 不能被另一个 tenant 重新绑定。断线、重复断线、急停和 application
close 都从同一个 generation owner 单调取下一代并 fence；waiter 获准后仍会在进入 backend 前锁内
复核 generation/state。已经执行的写动作返回 `outcome_unknown`，调用方不得自动重放。

emergency-stop 要求 `device.control`。Adapter 必须把真实 control 返回与独立状态查询分别规范化为
`DeviceControlReceipt` 和 `DeviceStateObservation`；raw 字符串或 dict 不会被当作成功。两个 typed
return code 都写入结果与完成事件。具体机器人 SDK 的 enum/状态字符串在 hkust4 真机核对前均为
`UNVERIFIED`。审计位于
`observability.trace_dir/device_audit.jsonl`，包含 lease/fence/stop 状态，不记录 credential。

## 飞书 Gateway

Gateway 默认关闭且只支持一个飞书/Lark channel。先安装依赖；`app_id/app_secret` 可以直接放在
mode 0600、Git ignored 的真实 YAML 中，加密键和验证 token 仍可来自环境变量：

```bash
uv sync --extra dev --extra gateway
export HOMEMASTER_FEISHU_ENCRYPT_KEY='...'
export HOMEMASTER_FEISHU_VERIFICATION_TOKEN='...'
```

真实 `config/homemaster.yaml` 保持 mode 0600。飞书入口完全信任，无需配置任何 bot/user `open_id`：

```yaml
gateway:
  enabled: true
  bus_capacity: 128
  per_tenant_capacity: 64
  per_session_capacity: 32
  shutdown_deadline_s: 5.0
  feishu:
    enabled: true
    app_id: cli_xxx
    app_secret: replace_with_real_secret
    app_id_env: HOMEMASTER_FEISHU_APP_ID
    app_secret_env: HOMEMASTER_FEISHU_APP_SECRET
    encrypt_key_env: HOMEMASTER_FEISHU_ENCRYPT_KEY
    verification_token_env: HOMEMASTER_FEISHU_VERIFICATION_TOKEN
    tenant_id: local
    domain: feishu
    react_emoji: EYES
    attachment_root: ~/.homemaster/attachments/feishu
```

YAML 中的 `app_id/app_secret` 必须成对填写并优先使用。两项都省略时才回退到旧的 `*_env`；任一来源
只有一项，或试图用 YAML 一项加环境变量一项拼接，都会启动失败。按锁定的 candidate 2，配置输出、
SDK 日志和 `FeishuApiService.__repr__` 的已选字段保持原值；真实文件不得提交到 Git。

`domain` 只允许 `feishu|lark`。所有非 bot sender 自动映射为内置 `feishu-owner`，拥有 Home 通用能力和
飞书建群/改名能力；私聊、群聊均不检查 allowlist 或 mention。创建群只接受群名并从当前 sender route
派生成员；改名目标只取当前群 route，模型不能传 chat/member id 覆盖。

```bash
uv run homemaster --gateway --config config/homemaster.yaml
```

`homemaster --gateway` 仅运行飞书/Lark Gateway，不启动交互 shell。兼容入口
`homemaster gateway --config ...` 保持可用。

### 飞书工具确认卡片

只有 canonical `PermissionChecker` 对一个已通过 schema 校验的工具调用返回
`requires_confirmation=True` 时，Gateway 才发送审批卡片。例如策略要求确认 `write_file` 时，原请求者会在
原 chat/thread 看到工具名、校验后的参数、工作目录和确认原因；点击“批准”后同一个 tool call 继续执行，点击
“拒绝”则在取得 resource lease 或调用 backend 之前结束。按钮只携带 opaque `approval_id` 和动作，不携带
session、用户、chat 或 message 等可伪造授权字段。

确认严格绑定请求 session、Gateway generation、请求者 `open_id`、源 `open_chat_id` 和审批卡片
`open_message_id`。只接受原卡片的一次回调；未知、重复、错误用户/chat/message 和发送期间的提前回调都不
执行工具。拒绝、发送失败、超时、取消、session 替换、进程重启及关闭均 fail closed，并尽力把同一张卡片改为
不可操作的终态。卡片更新失败不会反转已经锁定的决定，但会留下结构化 warning。确认回调由 Gateway 的独立
control path 消费，不发布 `InboundMessage`，不创建新模型 turn，也不会重放原 tool call。

当前部署边界必须明确：内置飞书 trusted principal `feishu-owner` 仍具有 `tool.auto`，所以现有正式权限策略会
直接允许 mutation，不会自然触发卡片。不要为了演示临时削弱正式 principal；只有部署策略本身产生
`requires_confirmation=True` 时这项能力才进入主链路。`lark-oapi==1.7.1` 的 callback response 和 message
patch 符号已在安装环境核对；安全 live harness 的两次发送和两次 patch 均返回业务 `code=0`，随后逐卡
`message.get code=0` 读回 `Status: closed` 且不含 action/button value。因为当前旧 Gateway 同时运行，两张
卡在时限内都没有被 harness 收到 callback；所以真实 callback operator/chat/message 身份闭环，以及
批准后 backend exactly-once 的 live 主链仍为 `UNVERIFIED`。

### Browser Gateway

安装独立 Browser extra，并为当前 Playwright 版本安装 Chromium：

```bash
uv sync --extra dev --extra browser
.venv/bin/playwright install chromium
```

在同一份 ignored 配置中增加允许的 Ant Design Pro Mock UI 入口：

```yaml
browser_gateway:
  start_url: http://127.0.0.1:8000/dashboard/automation
  allowed_origins:
    - http://127.0.0.1:8000
  headless: true
  action_timeout_ms: 15000
  navigation_timeout_ms: 30000
  wait_timeout_ms: 10000
```

`start_url` 必须属于 `allowed_origins`。一个 Gateway 进程只选择一个环境；Browser 与
ALFWorld 不能同时启用：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --browser \
  --config config/homemaster.yaml
```

Browser profile 保留 Home 通用工具，并按 run 增加 27 个 safe typed `browser_*` 工具。
已知唯一语义目标可以直接操作；未知、多匹配或身份不确定时，使用
`browser_inspect`/`browser_find` 返回的 scoped `target_ref`。写操作不强制 observe 或截图，默认依靠
结构化回执和独立 DOM/业务终态；只有票据明确要求图片证据，或布局、图表、Canvas、图片、视觉遮挡
无法通过语义读取判断时才使用 `browser_screenshot`。不要把普通写后确认、等待或滚动变成截图检查。
不再注册 browser 同义 `observe`。`browser_eval` 默认不存在，只有 run policy 明确授予
`browser.eval` 时才加入同一 session 的 registry。

变更单任务先用 `load_skill(name="change-ticket-executor")` 加载唯一通用 Skill；具体 SOP 只从
飞书正文链接的票据读取。截图继续沿用现有 Gateway MEDIA 出站链路。详见
[Browser Gateway 用户指南](browser-gateway-user-guide.md)。

私聊、群聊和 thread 按 tenant/channel/chat/thread/sender 生成稳定 session；权限相同但会话仍按 sender
隔离。malformed、合成 bot sender 和重复消息在附件下载/reaction 前拒绝。媒体先写入受 containment、
no-follow 和非零 regular-file 校验的根目录。出站媒体使用 opaque artifact handle，base64 在形成公开
tool message 前移除，多文件逐个形成不可合并 MEDIA。

WebSocket worker 运行在可终止子进程，fatal/completion 会回传 supervisor；shutdown 的 active run、
outbound drain、channel stop 和 service join 共用一个 absolute deadline。SDK HTTP/WS 日志只保留
WARNING，文本值不改写；业务外部调用 audit 仍只记录既有 allowlist 字段。
真实 `lark-oapi==1.7.1` 已验证 chat list、message create 与独立 message get，发送返回 code 0 且唯一
canary 回读精确一致。媒体、reaction、群状态、断线恢复和 `lark` domain 仍须逐项验收；真实平台是否
回投机器人自身消息及其 `sender_type` 值仍为 `UNVERIFIED`。

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
部署 grant 与当前 principal capabilities 的交集。deployment approval 中的 `enabled_tool_ids` 只决定
哪些已验证 extension contributions 被注册；`RunRequest`、CLI 和 Gateway metadata 都不能筛选或扩张
工具面。exact tool/hook token 不能替代 plugin/hook 的 canonical required capability。

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
