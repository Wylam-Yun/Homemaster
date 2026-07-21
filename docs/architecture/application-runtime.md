# Application Runtime 架构

## Ownership

CLI、Interactive、ALFWorld 和 Coworker 通过同一个 `ApplicationRuntime` 执行。application 持有
Catalog、无 session 状态的执行链、ObservationService、EventBus 和 SessionManager；每个 run
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

## Skill Data Flow

```text
package builtin + user roots + git-bounded project roots + explicit roots
  -> YAML frontmatter parser
  -> resolved-path containment
  -> source precedence / named builtin override authorization
  -> current Home ToolView alias capability gate
  -> immutable run dependency: SkillRegistry
  -> skill_view progressive disclosure
```

Skill 只声明元数据和 prompt fragment，不拥有 executor、permission 或 robot capability。一个 skill
不能把 ToolView 中 disabled 的工具变为 enabled。自动来源的单项失败记录为 secret-safe issue，避免
破坏 builtin；显式来源 fail-closed。所有替换保留完整 provenance chain。

Home one-shot 与 Interactive 在 composition root 创建同一份 registry，并经 `RunRequest.dependencies`
传给 legacy-compatible `skill_view` executor。`disable-model-invocation` 在读取时再次执行，防止仅靠
候选列表过滤后被模型按名称绕过。

ALFWorld 没有公开 `skill_view`，Coworker 继续使用固定的两份 benchmark skill 和严格十一项
ToolView；CL-17 不改变这两个 release profile 的 manifest 或 scorer 输入。

## Package Boundary

Builtin `SKILL.md` 和 nested resources 是 wheel package data。发布门会实际构建 wheel、安装进隔离
venv，并从源码 checkout 外使用 `importlib.resources` 读取文件；源码树上的测试通过不能替代该门。
