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
