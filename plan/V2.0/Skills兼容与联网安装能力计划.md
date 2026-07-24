# HomeMaster V2.0：OpenHarness Skills 与默认 Tools 完整移植计划

状态：**已完成：实施、测试、真实联网安装黑盒门、文档同源更新和最终代码评审整改均通过**
日期：2026-07-23
上游基线：`OpenHarness@9b2efd795c6aa09f88b0c257d269a9e518da6ae7`

## 完成记录（2026-07-23）

- Home profile 已逐项接入锁定上游的 39/39 默认工具；MCP 资源入口使用
  `list_mcp_resources`、`read_mcp_resource`，远端工具使用 `mcp__<server>__<tool>`。ALFWorld 6 项与
  Coworker 11 项工具面保持不变。
- Skills 已切换为 OpenHarness instruction-document 语义，不再要求 `tool_names`，也不通过 Skill 扩大
  ToolView 或 capability。八份 bundled Markdown 已进入 wheel；标准 `skill(name=...)`、兼容
  `skill_view(skill_name=...)`、Available Skills 摘要和 `/<skill-name>` 调用共用动态 registry。
- application-owned config/plan/Cron/task/team/child/MCP 服务已接通。远程 `ask_user_question` 会持久化
  `WAITING_USER`，下一条 channel 输入以同一 session 完整历史恢复；默认 child worker 显式继承父应用
  config path。
- 锁定上游测试 `151 passed`；正确排除 live marker 后的非 live 回归为
  `1405 passed, 7 deselected, 1 warning`。用户原有 Coworker 文件造成的 V1.9 contract-hash 快照漂移
  单独排除且未覆盖。manifest check、installed-wheel、真实 child-worker/provider、`uv lock --check`、
  compileall、相关 Ruff 和 `git diff --check` 均通过。
- `scripts/v20/verify_skill_installation.py` 已在真实网络与隔离目录完成 Skill 迁移、GitHub clone/checkout、
  `skill-creator` 初始化/校验、zip/tar、Python/Shell、临时 venv `packaging==24.2`、npm
  `is-number@7.0.0`、HTTPS/curl 双客户端哈希和第二 CLI 进程发现；结果为 `status=PASS`，Git HEAD 精确
  为锁定 SHA，HTTPS raw SHA-256 为
  `db58ae9b4bc03a8bd17181ab1dafda2e294aada9d9f753dc747533d2145a5345`。
- 最终代码只读评审提出 3 项：配置展示泄露 credential、管理工具缺少独立 capability、data-only
  Plugin Skill adapter 未落地。三项均已采纳：公开配置在工具/模型/JSONL 三个出口脱敏；Cron、配置、
  MCP、task/agent/team 分别增加独立 capability；Plugin manifest 只读发现支持 enablement、project opt-in、
  containment 与精确 builtin 覆盖授权，且不导入可执行 plugin 代码。按仓库纪律整改后只做针对性验证，
  不追加评审。
- 评审整改回归为 `30 passed`；扩展的 Skills/service/config/factory/permission/Feishu/Telegram/child/Cron/
  installed-package 切片为 `132 passed`。相关 Ruff/format、compileall、lock、port manifest 和 diff 门通过。
- reviewer 提醒的 wheel 残余风险也已关闭：增强门首次真实发现 Pillow 未列核心依赖以及 MCP extra 被 eager
  import；修复后 wheel 在源码目录外安装声明依赖并构造 39/39 工具（`1 passed`），相关上游 registry/MCP
  回归为 `28 passed`。

## 一句话目标

不再维护 HomeMaster 自己发明的 Skills 语义，而是把锁定版本的 OpenHarness Skills 核心、
39 个默认工具及其必需服务和原始测试直接移植过来。HomeMaster 只在外围增加目录、权限、审计、
终态验证和现有接口兼容。

改完后，Codex/OpenHarness 格式的 `SKILL.md` 不需要改写；用户把完整 Skill 目录显式移植到
HomeMaster 自己的 Skills 目录后即可使用。Home agent 同时具备 OpenHarness 默认工具面，可以用
`bash` 执行 `git clone`、解压、Skill 附带脚本和项目依赖安装，也可以使用文件、检索、联网、MCP、
任务、子 agent、团队、Cron、Worktree、LSP、Notebook 和图片等工具。

## 一、为什么要重写，而不是继续修补

当前 HomeMaster 把 Skill 理解成“绑定一组 Home 工具的任务模板”：

```text
加载 SKILL.md
  -> 强制要求 tool_names
  -> 检查 tool_names 是否属于当前 ToolView
  -> 保存一份启动时 Registry
  -> skill_view 只返回部分 Home 字段
```

OpenHarness/Codex 的定义不同：

```text
Skill = 给模型按需读取的操作说明书
Tool  = 真正执行联网、读写文件、机器人动作的能力
权限  = 在 Tool 真正执行时检查
```

因此当前实现会出现以下问题：

1. 标准 `skill-creator` 没有 `tool_names`，会被拒绝。
2. 模型上下文没有 Available Skills 清单，不知道有哪些 Skill。
3. `skill_view` 使用旧 Registry，新写入的 Skill 不能立即读取。
4. `skill_view` 没有返回完整原始 `SKILL.md`。
5. Skills 被错误地依赖于 MCP 最终 ToolView，两个本应独立的模块耦合在一起。
6. 原移植清单把 loader 标成近似原样移植，但实际核心行为已经被改写。
7. 原 V2.0 草稿只准备增加 `read_file`、`web_fetch`、`write_file`，没有移植 OpenHarness 的
   `bash` 和其他默认工具；模型即使读懂 Skill，也无法执行其中的命令、脚本和依赖安装步骤。

这次不再给旧模型继续打补丁，直接恢复 OpenHarness 的上游定义。

## 二、方案选择

### 方案 1：继续修改当前 `SkillSpec`，只补少量工具

改动表面较小，但仍然需要自己重写 loader、registry、上下文和调用协议；只补文件/联网工具也会让
复杂 Skill 停在脚本执行步骤。以后上游更新时还会再次发生语义漂移。**不采用。**

### 方案 2：只复制 OpenHarness 的 39 个工具文件

看起来最直接，但 `agent/team/task` 依赖 swarm 和 task manager，Cron 依赖 scheduler，LSP、MCP、
图片和 sandbox 也依赖各自服务。只复制工具类会得到一批能注册、调用就报错的空壳。**不采用。**

### 方案 3：直接移植 Skills、39 个默认工具及其依赖图，HomeMaster 只做 adapter

公开工具名、输入 schema 和核心执行行为与上游一致；按依赖层移植，但 39 个默认工具全部属于交付
范围。原始上游测试作为门禁，HomeMaster 的权限、证据和终态验证继续保留。**采用。**

### 方案 4：把 OpenHarness 整个 runtime、plugin、UI 和 CLI 一起搬入

最接近上游，但会重复 HomeMaster 已有的 runtime、permission、session、extension 和 CLI，范围
失控。**不采用。**

最终结构：

```text
OpenHarness Skills + 默认 Tools（上游公开行为真理源）
  -> HomeMaster 来源与安全 adapter
  -> HomeMaster Context adapter
  -> HomeMaster Tool/Result adapter
  -> HomeMaster Permission/ExecutionPipeline
  -> HomeMaster runtime/session/channel
```

## 三、直接移植哪些上游代码

锁定以下 OpenHarness 文件和 SHA-256。实现时先复制，再只做 import、配置入口和 Home 工具协议
所必需的机械适配；所有行为差异必须写进 port manifest，不能藏在代码里。

| 上游文件 | SHA-256 | 处理方式 |
|---|---|---|
| `skills/_frontmatter.py` | `668a128511d47e172d9cfc785917e2e52d03914a2f982df7f273a4329f4a5920` | 直接移植 |
| `skills/types.py` | `c4cd32af6b987c6a152c03d988e0c6ea9f986dad0c108083d42cd199d5b5e4c3` | 直接移植，成为唯一 Skill 内部数据结构 |
| `skills/registry.py` | `93c9068a41e56297c74bbc742b3c7fd55658c2302b4597ca96c82055e05126e1` | 直接移植 |
| `skills/loader.py` | `f8981e8f900fdec99dc48af9680190c7a0fc8ac25c1ef6a5b9c2245e1a16ee11` | 移植发现顺序，配置来源接 Home adapter |
| `skills/bundled/__init__.py` | `5d23b0b392557aee102c1a7f6e8f28f137f6ae3123be5a8a0de6c3bcb683fc66` | 连同八份 bundled Markdown 直接移植 |
| `tools/base.py` | `30402d4c8f4530455c621bcdd7b5a243c1f8aaa039f1daf4af9749481e7bb7a7` | 保留公开 schema，接 Home RegisteredTool adapter |
| `tools/bash_tool.py` | `a75ab060c6ffb8afd2dd9ed255c175346b6498b70e486bf596591e8e997a1fb3` | 连同 shell/sandbox 依赖移植，接 Home Permission |
| `tools/skill_tool.py` | `6e7a0cb1b1a81b58cbcd13b83617f901f8cea5e10a9224ac06958972e7ec6a78` | 移植标准 `skill(name=...)` 行为 |
| `tools/file_read_tool.py` | `a30bd4bc1793e2e002be1d66c28d8c3a38348974690555e0362d922471924608` | 移植后接 Home 路径权限 |
| `tools/file_write_tool.py` | `b065600978b0df9ae46ad442ab8db30f40fe2fd02fe6eff90e6ce8a2764eef66` | 移植后增加原子写和终态核对 |
| `tools/file_edit_tool.py` | `72b48afcb9218086071f161ddbe57666fd889b3c749771fd289e3540241681ac` | 移植后增加原子编辑和终态核对 |
| `tools/glob_tool.py` | `379b58b21d73a1b53f9dcce5eafbe87e86c063b2811f87793af49fa7200d9d79` | 连同原测试移植，接 Home 路径权限 |
| `tools/grep_tool.py` | `63931f9f01e234d1414127ab50c895fe263effc606f5f76a42cbcb668ac142bd` | 连同原测试移植，接 Home 路径权限 |
| `tools/web_fetch_tool.py` | `ab34077ac39c39e477d7d90cc6357c897e74dede547f86412edc7a89880ab53c` | 移植控制流，返回结构接 Home contract |
| `tools/web_search_tool.py` | `a71afa9f64e3d7c3c07fd50c9885f6645a7d238cd7fa0d5d840791161f960364` | 连同网络保护和原测试移植 |

完整默认工具面固定为锁定 commit 的 39 项；不能只实现上表列出的 linchpin：

```text
执行与文件：
  bash, read_file, write_file, edit_file, notebook_edit, glob, grep, lsp

联网与模型媒体：
  web_fetch, web_search, image_to_text, image_generation

Skills 与交互：
  skill, tool_search, ask_user_question, brief, sleep, todo_write

工作区与运行模式：
  enter_worktree, exit_worktree, enter_plan_mode, exit_plan_mode, config

定时与后台任务：
  cron_create, cron_list, cron_delete, cron_toggle, remote_trigger,
  task_create, task_get, task_list, task_stop, task_output, task_update

子 agent 与团队：
  agent, send_message, team_create, team_delete

MCP 配置：
  mcp_auth
```

MCP 启用时还必须提供 OpenHarness 同名入口 `list_mcp_resources`、`read_mcp_resource` 和动态
`mcp__<server>__<tool>`；它们接 HomeMaster 已有 MCP client，不再复制
第二套连接管理器。

所有 39 个工具文件、直接依赖文件和对应上游测试的 SHA-256 在实施第 1 步生成并锁入
`upstream-port-manifest.json`。没有 manifest 条目、原始测试或明确 test-gap 的工具不得标记完成。

同时移植：

- `prompts/context.py` 中 Available Skills section 的行为。
- `tests/test_skills/test_loader.py` 和上述工具的原始测试。
- `tests/test_tools/` 中 39 个默认工具及集成 flow 的原始测试。
- `utils/shell.py`、`sandbox/`、`services/lsp/`、`services/cron*.py`、`tasks/`、`swarm/` 和
  `coordinator/` 中被默认工具真实引用的依赖图；只移植可达依赖，不搬 UI 和无关 CLI。
- OpenHarness bundled `commit/debug/diagnose/plan/review/simplify/skill-creator/test` 八份 Markdown、
  package-data 配置和 installed-wheel smoke。
- OpenHarness plugin Skills 的发现语义和相关测试，但通过下文定义的 skills-only adapter 接入。
- 用户直接输入 `/<skill-name>` 时的 Skill 展开规则，包括 `user-invocable`、base directory 和参数。

原始上游测试只允许做路径/import 的机械修改。需要改变断言时，必须证明是已批准的 HomeMaster
外围差异，并另加一条 Home 测试。

### Bundled 与现有 Home builtin 的关系

两组内容都保留，不二选一：

```text
OpenHarness bundled
  < HomeMaster 现有 builtin
  < user / extra / project / plugin
```

当前两组名称没有冲突。以后任一外部来源要覆盖 OpenHarness bundled 或 Home builtin，都必须命中
`allowed_builtin_overrides` 的准确名称；未经授权时保留原定义并形成诊断。所有 bundled Markdown
进入 wheel package data，不能只在源码 checkout 中可见。

## 四、HomeMaster 当前逻辑如何处理

### 直接删除的冲突逻辑

以下逻辑与 OpenHarness 定义冲突，直接删除，不保留 legacy mode：

- `SkillSpec.tool_names` 必填。
- Skill 加载时按 frozen ToolView 校验工具名。
- MCP 连接前创建空 Skill Registry、连接后再按 ToolView 重载。
- `candidate_summaries()` 存在但不进入模型上下文的断链状态。
- Home `skill_view` 只返回描述、工具名、约束和 success criteria。
- “Skill 声明某工具就可能获得该工具”的错误假设。

### 移到外围 adapter 的能力

以下能力有价值，但不能再修改上游 Skill 核心语义：

- 真实路径解析、`..` 和 symlink 越界检查。
- builtin 同名覆盖授权。
- 来源 provenance、覆盖链和 secret-safe diagnostics。
- HomeMaster 配置中的显式 Skills 目录。
- HomeMaster ToolView、Permission 和 JSONL 审计。

这些信息放在 `SkillSourcePolicy`、`SkillDiagnostics` 或 registry 外围句柄中，不继续扩充
`SkillDefinition`。`SkillDefinition` 保持 OpenHarness 字段：完整 `content`、来源、路径、base dir、
别名和调用元数据。

### 兼容现有 Home Skills

现有内置文件里的 `tool_names`、`constraints`、`success_criteria` 不需要立即删除。上游 parser 会
保留完整原文并忽略不认识的 frontmatter 字段，因此旧文件仍能加载；这些字段不再影响 ToolView
和权限。

后续文档把它们标为普通扩展元数据，不再宣传为 Skill 能力声明。

### Coworker 和 ALFWorld

- Coworker 保留固定 11 工具和它自己的两名称 `skill_view`，不接入新的 Home Skills 动态来源。
- ALFWorld 工具清单不变。
- OpenHarness 39 个默认工具和 MCP 动态入口只进入 Home profile。
- Home 现有机器人、记忆和任务领域工具继续保留；新工具使用 `openharness.<name>.v1` stable id，
  模型侧保留上游工具名。与 Home 现有 alias 冲突时 composition 明确失败，不静默覆盖。
- 不用 OpenHarness `BaseTool` 替换 Home canonical `ToolDefinition/RegisteredTool`；统一通过一个 adapter
  注册，确保所有新工具仍经过 Home 的 ToolView、Permission、ExecutionPipeline 和 verification。

## 五、改完后的完整 Pipeline

```text
1. 发现来源
   OpenHarness bundled / Home builtin
   + ~/.homemaster/skills
   + Git root 到当前目录的 .homemaster/skills
   + 显式配置的 extra dirs / plugin skills
                 ↓
2. Home 来源策略预检查
   路径边界 / builtin 覆盖授权 / provenance / diagnostics
                 ↓
3. OpenHarness Loader
   读取完整 SKILL.md，生成原版 SkillDefinition
                 ↓
4. OpenHarness Registry
   按 name / command_name / display_name / aliases 建索引
                 ↓
5. Available Skills 上下文
   模型只看到名称和简介
                 ↓
6. 按需读取
   skill(name="skill-creator")
   或旧兼容 skill_view(skill_name="skill-creator")
                 ↓
7. 调用前重新发现
   返回完整原始 SKILL.md，同时提供 base_dir 结构化信息
                 ↓
8. Skill 指导模型调用普通工具
   OpenHarness 39 个默认工具 / MCP / Home robot tools
                 ↓
9. HomeMaster 执行边界
   ToolView -> Permission -> ExecutionPipeline -> Executor -> Verification
```

Skill 始终只是说明书。即使正文写了一个不存在或未授权的工具，真正调用时仍会被 ToolView 或
Permission 拒绝。

## 六、标准入口与旧接口兼容

### 标准入口

Home profile 增加 OpenHarness 标准接口：

```text
skill(name="skill-creator")
```

它在每次调用前重新加载来源，并返回完整 `SKILL.md`。如果
`disable-model-invocation: true`，模型调用会被拒绝。

### HomeMaster 旧入口

继续保留：

```text
skill_view(skill_name="skill-creator")
```

它只是参数名和工具名兼容层，内部调用同一个标准 Skill 执行逻辑，不能再维护第二套查询代码。

### 用户直接调用

交互 CLI 支持：

```text
/skill-creator 创建一个日志分析 Skill
```

执行前检查 `user-invocable`，将 Skill base directory 和用户参数按 OpenHarness 规则展开，然后进入
普通 HomeMaster run。`disable-model-invocation` 只限制模型自动调用，不等于禁止用户直接调用。

one-shot 和 interactive 使用同一个 slash-command resolver，不能只改交互循环。命令优先级固定为：

```text
HomeMaster 内置命令（/exit、/doctor 等）
  > 用户可调用 Skill command_name
```

Skill 与内置命令或另一个 Skill 重名时，启动/刷新阶段形成明确冲突诊断，不随机选择。

为承载上游 `model` 语义，`RunRequest` 增加 run 级 `model_override`，provider factory 使用现有配置
解析。只有当该 model 能精确映射到一个已配置 chat provider/model 时才执行；未配置或映射不唯一
时，在任何外部模型请求前明确拒绝。当前真实 provider 是否接受 Skill 声明的其他 model 名称属于
**UNVERIFIED**，实施前必须用真实 provider 配置核对，不能用“字符串已传到 transport”代替。

## 七、Skills 来源与刷新

默认用户来源只认 HomeMaster 自己的目录：

```text
~/.homemaster/skills
```

项目来源从 Git root 向当前目录逐层发现：

```text
.homemaster/skills
```

不会自动扫描 `~/.codex/skills`、`~/.claude/skills`、`~/.agents/skills` 或对应项目目录。兼容
Codex/OpenHarness 格式只代表迁入后不需要改写，不代表读取其他 agent 的私有运行目录。

另外保留 extra/explicit dirs 和 plugin Skills，但它们只有被 HomeMaster 配置明确声明后才参与发现，
不能靠探测外部目录自动加入。标准目录结构统一为：

```text
<skills-root>/<skill-name>/SKILL.md
```

### Plugin Skills 的明确边界

不调用 OpenHarness 完整 `load_plugins()`，因为它还会导入 Python tools 并加载 commands、agents、
hooks 和 MCP。新增 `PluginSkillSourceAdapter`，只做以下只读数据操作：

1. 在配置的 plugin roots 中寻找 OpenHarness plugin manifest。
2. 只解析 manifest 的名称、`enabled_by_default` 和 `skills_dir`。
3. 按 `skills.enabled_plugins` 决定是否启用；project plugin Skills 默认关闭，只有
   `skills.allow_project_plugin_skills=true` 才发现。
4. 只读取 `skills_dir` 下的 `SKILL.md`，绝不 import plugin Python 文件。
5. 来源优先级保持 plugin 最后，但覆盖任一 bundled/builtin 仍需
   `allowed_builtin_overrides` 精确授权。

OpenHarness-format plugin Skills 属于 data-only 来源，不映射成 HomeMaster executable extension，也
不要求 extension code approval。现有 Home `ExtensionContributions` 本期不新增 Skills 字段，避免为了
Skill 读取扩大可信代码执行面。这个 data-only 差异写入 port manifest。

刷新规则：

- 构建 Available Skills 上下文前加载当前来源。
- 每次 `skill`/`skill_view` 调用前重新加载。
- 新候选完整通过后才发布，不能让模型看到半份 Registry。
- 自动来源的单个坏文件形成诊断，不隐藏其他正常 Skill。
- 显式配置来源错误 fail closed。
- Skills 不再等待 MCP，也不在 MCP 连接后按工具名重验。

## 八、执行上下文和路径事务基础改造

这是所有文件、工作区和进程工具共用的基础，不允许每个 executor 各自调用 `Path.cwd()`：

- `ToolExecutionContext` 增加不可变 `working_directory`，在 application composition 时解析并锁定。
- 更新该接口的全部实现、测试 fake 和 14 个已知构造点，并增加接口一致性 audit。
- Permission、executor、verifier 和 resource-key resolver 都调用同一个
  `resolve_tool_path(working_directory, raw_path)`。
- `RegisteredTool` 增加可选的动态 resource-key resolver；文件写入以 canonical absolute path 作为
  key，不使用一个全局静态字符串。
- 调整 `ToolExecutionPipeline`：同一 resource lease 必须覆盖 executor 和随后 verifier，不能在
  executor 返回后先释放锁。设备、MCP、legacy adapter 等全部注册实现要跑一致性回归。
- 写入失败、超时或取消时在 finally 释放 lease；已尝试写但终态无法确认时返回
  `outcome_unknown` 或 `verification_pending`，不能普通失败后自动重试覆盖证据。

这样两个并发 run 写同一路径时才是同一条事务队列，上一笔读回验证完成后下一笔才能进入。

## 九、OpenHarness 默认工具如何完整接入

“拥有 OpenHarness tools”的验收口径不是文件存在或名字能注册，而是：公开工具名和输入 schema
一致、真实调用进入 executor、依赖服务可用、返回码正确，并且外部终态真实发生。39 个默认工具
全部加入 Home profile；ALFWorld 和 Coworker 的固定 ToolView 不变。

### A. 直接移植核心行为

下列工具以原文件和原测试为主，只把 BaseTool/ToolResult、cwd、权限和结果接到 Home adapter：

```text
bash, read_file, write_file, edit_file, notebook_edit,
glob, grep, web_fetch, web_search, brief, sleep, todo_write,
enter_worktree, exit_worktree
```

`bash` 同时移植 OpenHarness `utils.shell` 和 sandbox adapter。它是 `git clone`、`tar/unzip`、
`python/bash` 脚本以及 `uv/npm` 等项目依赖命令的通用执行入口，不再另造四个专用安装工具。

### B. 工具用法保持 OpenHarness，实际操作 HomeMaster

这组工具不是独立的小函数，它们会读取配置、使用模型连接或改变当前运行状态。如果把源码原封不动
复制过来，它们会操作 `~/.openharness` 和 OpenHarness runtime：工具可能返回“成功”，但正在运行的
HomeMaster 没有任何变化。因此固定采用下面的规则：

```text
模型侧：工具名称、参数格式、说明和成功/失败含义保持 OpenHarness
执行侧：读取和修改 HomeMaster 当前真正使用的配置、连接、权限和状态
```

逐项含义：

| 工具 | 模型调用后实际使用或改变什么 |
|---|---|
| `skill` | 从 HomeMaster Skills Registry 读取完整 `SKILL.md` |
| `tool_search` | 只搜索当前 Home run 已获授权的 ToolView，不泄露被禁用工具 |
| `ask_user_question` | 问题发回当前入口；CLI 在 CLI 询问，远程入口写入当前 session 等待下一条回复 |
| `config` | 查看或修改 HomeMaster 配置，不创建或修改 `~/.openharness` 配置 |
| `enter_plan_mode` | 把当前 Home session 切到只规划、不执行修改的状态 |
| `exit_plan_mode` | 退出当前 Home session 的只规划状态，恢复允许执行 |
| `mcp_auth` | 配置 HomeMaster 使用的 MCP server 和凭据 |
| `list_mcp_resources` | 通过 HomeMaster 已连接的 MCP client 列出资源 |
| `read_mcp_resource` | 通过同一个 Home MCP client 读取资源 |
| `mcp__*` | 通过同一个 Home MCP client 调用远端 MCP 工具 |
| `image_to_text` | 使用当前 Home provider 识别图片，结果进入 Home result/artifact 链 |
| `image_generation` | 使用当前 Home provider 生成图片，并存入 Home artifact store |

例如模型调用 `enter_plan_mode` 后，下一次 `bash`/`write_file` 必须真的被 Home Permission 拦住；调用
`config` 后，第二个 HomeMaster 进程必须读到修改后的配置。不能只验证工具自己返回了一句成功。
每一项与 OpenHarness 的内部接线差异都写入 port manifest。

远程 `ask_user_question` 不在一个 webhook 请求中无限等待：当前 run 返回 `WAITING_USER`，把问题和
tool call id 原子保存进 session；下一条同 session 用户消息作为答案恢复该 tool result，然后继续 agent
loop。`enter/exit_plan_mode` 同样写入 session 级 permission state，立即影响当前 run 的后续调用并随
session resume 保留，但不会把另一个用户的并发 session 一起切换。以上是 Home 多会话必须增加的
明确差异。

### C. 连同后台服务一起移植

以下工具若只复制 tool 文件一定是假实现，必须连服务、生命周期和 CLI 管理入口一起完成：

```text
lsp
  -> services/lsp

cron_create / cron_list / cron_delete / cron_toggle / remote_trigger
  -> services/cron.py + cron_scheduler.py
  -> homemaster cron start/status/stop 的真实进程生命周期

task_create / task_get / task_list / task_stop / task_output / task_update
  -> BackgroundTaskManager + shell/agent subprocess backend

agent / send_message / team_create / team_delete
  -> agent definitions + coordinator + swarm backend + mailbox + task manager
  -> child command 改为 HomeMaster worker 入口，复用当前 provider 配置
```

任务、子 agent、团队和 Cron 的状态目录迁到 `~/.homemaster`；不能读写 `~/.openharness`。状态文件
schema 保持上游兼容，Home 路径差异写入 manifest。

下面三个工具是 Skills 安装链的关键 linchpin，单独写清加强后的 Home 行为。

### 1. `read_file`

用途：读取 Skill 的 `references/`、脚本源码和其他 UTF-8 文本。

```text
read_file(path="/完整/skill/base_dir/references/openai_yaml.md")
```

规则：

- 端口保留 OpenHarness 的 `path`、`offset`、`limit`。
- 读取前经过 Home path permission，受 protected paths 和 deny rules 约束。
- 拒绝目录和二进制文件。
- 返回行号和是否还有剩余内容。
- Skill 工具的结构化结果提供 `base_dir`，模型据此解析 Skill 内的相对引用。

### 2. `web_fetch`

用途：读取 HTTP/HTTPS 文本资源。

```text
web_fetch(url="https://example.com/SKILL.md")
```

返回必须分开：

```text
content  = 可原样写入的正文，不含 URL、状态和安全横幅
summary  = URL、HTTP 状态、Content-Type、外部内容提示
metadata = 原始字节数、原始字节 SHA-256、是否完整
```

关键规则：

- `content` 保留解码后的原始换行，不做 `.strip()`。
- 固定请求头 `Accept-Encoding: identity`；若服务端仍返回非 identity `Content-Encoding`，本期拒绝，
  不对“wire bytes”做模糊声明。
- `raw_response_bytes` 明确定义为 httpx `Response.aiter_raw()` 在 identity 响应中依次产生的 bytes；
  SHA-256 和 byte count 都基于这些 bytes。
- 只接受严格 UTF-8 文本；缺少 charset 时按 UTF-8，声明其他 charset 或严格解码失败时返回失败。
  `content` 是 `raw_response_bytes.decode("utf-8", errors="strict")`，不 strip、不重新拼接横幅。
- 使用流式读取，在累计 bytes 超过硬上限的当次 chunk 立即中止并失败；不能先 `client.get()` 把
  整个响应载入内存，也不能返回截断正文。
- 验收时写入哈希另按 `content.encode("utf-8")` 计算，两个哈希不混用。
- 限制超时、响应大小和重定向次数，禁止 URL 内嵌用户名密码。
- `trust_env=false`，不偷偷继承 shell 代理和凭据。

项目锁定环境已真机核对 `httpx==0.28.1` 存在 `AsyncClient.stream()` 和
`Response.aiter_raw()`；实现后仍需用真实响应验证 identity、重定向和超限关闭行为。

你已明确接受通用联网工具的高风险，因此本期不宣传“只允许公网”。工具可能访问运行机器能够
访问的内网 HTTP 服务。Python 层的一次 DNS 检查不能防 DNS rebinding，也不能冒充网络沙箱。
不可信远程部署必须使用容器、出口代理或网络策略限制。

### 3. `write_file`

用途：创建或覆盖完整 UTF-8 文本文件。

```text
write_file(
  path="~/.homemaster/skills/example/SKILL.md",
  content="完整正文",
  create_directories=true
)
```

关键规则：

- 应用启动时锁定一个工作目录。
- Permission 和 executor 调用同一个 canonical path resolver，使用同一个锁定工作目录。
- 即使进程中途改变 `cwd`，检查路径和实际写入路径也必须一致。
- PLAN 禁止写；DEFAULT 按现有规则确认；FULL_AUTO 可自动写。
- 使用目标同目录临时文件、flush 后原子替换，避免留下半个文件。
- 返回最终路径、UTF-8 byte count 和 SHA-256。
- application-owned per-path transaction lock 覆盖写入和 verifier 读回，两个并发 Home run 写同一
  路径时逐个完成，不能只依赖单次 `execute_many()` 的 serialized 标记。
- verifier 独立重新打开目标文件，逐字节核对 byte count 和 SHA-256，核对失败不返回 confirmed
  success。

### 4. `bash`

```text
bash(command="git clone --depth 1 https://example/repo.git skill-src", cwd="...")
bash(command="python scripts/init_skill.py example", cwd="...")
bash(command="uv sync --frozen", cwd="...")
```

规则：

- 保留 OpenHarness `command`、`cwd`、`timeout_seconds` schema、非交互预检、PTY 优先、合并输出、
  timeout 终止和截断行为。
- 使用锁定的 `working_directory` 解析 cwd；路径越界和 protected path 在进程启动前拒绝。
- 返回真实子进程 return code；非零、超时、取消或 sandbox unavailable 都不能报告成功。
- 取消和超时必须杀死完整 process group，不能留下 `git`、安装器或脚本子进程继续修改磁盘。
- 继承 OpenHarness sandbox adapter；本机 sandbox 是否可用在 startup 暴露明确状态。未启用 sandbox
  时按用户已接受的高风险策略执行本机命令，但仍经过 Home Permission、审计和 timeout。
- 命令产生的任意磁盘/依赖变化无法逐文件预知，因此通用 `bash` 只能权威报告子进程 return code，
  不能仅凭 stdout 宣称业务完成。Skill 工作流必须随后用独立工具检查预期文件/状态；Skills 安装
  黑盒门固定检查目标目录、文件哈希、依赖命令返回码和第二进程可加载状态。

### 5. `edit_file` 及其他文件工具

- `edit_file` 与 `write_file` 共用 canonical path resolver、per-path lease、原子替换和读回 verifier。
- `glob`、`grep`、`notebook_edit`、`lsp` 和 Worktree 工具全部在调用前经过同一 Home 路径策略。
- `notebook_edit` 的外部终态是独立重新解析 `.ipynb` JSON 并核对目标 cell；LSP 的外部终态是
  真实 language server 返回成功响应，不用 mock 代替。
- `enter_worktree`/`exit_worktree` 以独立 `git worktree list --porcelain` 和目标路径存在性作为黑盒门。

### 独立 capability

新增能力仍使用独立 capability，便于审计每类高风险操作：

```text
read_file  -> tool.read   + filesystem.read
web_fetch  -> tool.read   + network.http
write_file -> tool.mutate + filesystem.write
edit_file  -> tool.mutate + filesystem.write
bash       -> tool.mutate + process.exec
任务/agent -> tool.mutate + process.spawn
Cron       -> tool.mutate + scheduler.manage
配置/MCP   -> 对应 config.mutate / mcp.manage
```

用户已明确要求完整 agent 能力同时进入本地和远程 Home 入口。因此本地默认 principal 以及
Gateway/Telegram/飞书等 Home channel principal 默认增加 OpenHarness 工具所需的
filesystem/network/process/spawn/scheduler/config/MCP capability。每次调用仍经过 Permission 和 JSONL
审计，但不要求部署者逐 channel 追加授权。`skill`/`skill_view` 仍只需要 `tool.read`。

## 十、完整 Skill 安装与执行能力

本期可以完成：

```text
web_fetch 或 bash/git clone 获取 Skill
  -> 必要时 bash 解压完整目录
  -> 检查目标目录和 SKILL.md
  -> 移入 ~/.homemaster/skills/<name>
  -> 当前进程立即发现
  -> 读取 SKILL.md、references、scripts 和 assets
  -> 按 Skill 说明运行 Python/Shell 脚本
  -> 使用项目 package manager 安装 Python/npm 等依赖
  -> 通过返回码和外部终态确认是否真的可用
```

准确边界：

- OpenHarness 没有专用 `skill_install`；HomeMaster 同样由模型组合 `bash/read/write/edit/skill` 完成，
  不额外发明安装协议。
- 可以运行非交互命令；需要人工输入密码、选择菜单或交互式 TTY 的安装器仍会被 `bash` 预检拒绝，
  模型必须改用 `--yes`、`--non-interactive` 等参数。
- “允许运行”不等于“所有第三方 Skill 必然安装成功”。仓库不存在、依赖冲突、系统缺少编译器、
  命令返回非零或 Skill 自身错误都必须如实失败。
- Python 依赖只能通过项目虚拟环境和项目依赖管理器安装，不执行裸全局 `pip install -U`；npm 等
  依赖同样在目标项目/Skill 自己的隔离目录中安装。
- 二进制 assets 可以通过 `bash` 下载和保存，但是否能解析取决于当前工具和系统程序；不能仅因
  文件下载成功就宣称整个 Skill 可用。

### 改造完成后如何新增 Skill

用户级 Skill：

```text
~/.homemaster/skills/<skill-name>/SKILL.md
```

项目级 Skill：

```text
<project>/.homemaster/skills/<skill-name>/SKILL.md
```

可以手工复制、`git clone`，也可以让 agent 调用迁入后的 `skill-creator` 创建。文件完整落盘后，
下一次 Available Skills 上下文构建自动出现；已知名称也可以立即调用 `skill(name)`，无需重启。
修改 `SKILL.md` 同样动态生效。Skill 只提供说明，不自动获得正文提到的 Tool 或 capability。

### 改造完成后如何新增 Tool

有三条正式入口：

1. **HomeMaster 专用或本地 Python Tool：走 trusted extension。** 扩展目录包含 `manifest.json` 和
   `extension.py`；factory 返回一个或多个 `RegisteredTool`。每个 Tool 声明稳定 internal id、模型可见
   名称、JSON schema、executor、verifier、required capabilities 和 provenance。
2. **已有外部服务：优先走 MCP。** 配置 MCP server 后，由 Home MCP adapter 动态提供
   `mcp__<server>__<tool>`，不为每个 API 重写本地 Tool。
3. **要成为 Home 默认内置能力：修改 builtin tool composition。** 加入源码、上游/本地测试、
   port manifest、Home profile、capability 和黑盒终态门，随版本发布。

本地 trusted extension 的启用流程固定为：

```text
编写 manifest + extension.py + 测试
  -> HomeMaster 计算整份扩展 canonical SHA-256
  -> 部署配置 extensions.approvals 固定 id/version/hash/grants/enabled_tool_ids
  -> 重启 HomeMaster
  -> Tool 进入 Catalog 和 Home ToolView
  -> 模型下一次构建上下文时看到 Tool schema
```

Tool 代码、版本、capability、provenance 或工具面发生变化会改变 hash，必须更新 approval 并重启；
不能像文本 Skill 一样热加载。agent 可以生成扩展文件和测试，但不能在同一运行中自行批准 hash、
扩大 principal capability 并执行刚写入的 trusted code，否则等于让 Tool 绕过权限系统给自己提权。

## 十一、实施步骤

### 第 1 步：先建立失败测试和上游基线

- 生成 39 个默认工具、MCP 动态 adapter、Skills 及其可达依赖的 port manifest 和 SHA。
- 原样移植 OpenHarness Skills、tools 和相关集成测试，先只改 import/path。
- 加入一个完全不含 `tool_names` 的标准 Skill fixture。
- 记录 Home、ALFWorld、Coworker 当前 ToolView 基线。
- 用当前代码证明标准 `skill-creator`、动态刷新、完整 content、`bash` 和默认工具 parity 测试先失败。

### 第 2 步：替换 Skills 核心

- 用 OpenHarness `SkillDefinition` 替换 Home `SkillSpec` 内部数据结构。
- 移植 frontmatter、bundled 内容、loader、registry 和 data-only plugin Skill 发现。
- 删除 `allowed_tool_names` 和 MCP/Skills 耦合。
- 接入 Home bundled/user/project 来源，以及显式配置的 extra/plugin 来源；不自动扫描其他 agent 目录。
- 保留外围 path、override、provenance 和 diagnostics adapter。

### 第 3 步：接入模型和调用入口

- 接入 Available Skills context。
- 注册标准 `skill(name)`。
- 将 `skill_view(skill_name)` 改成同一执行器的兼容 adapter。
- 接入 one-shot/interactive 共用的 `/<skill-name>` resolver、base directory、参数和 run 级
  `model_override` 映射。

### 第 4 步：移植核心执行工具

- 移植 `bash/read/write/edit/notebook/glob/grep/web/brief/sleep/todo/worktree` 等核心工具。
- 给执行上下文接入锁定 working directory、共享 canonical path resolver 和 Home Permission。
- 修改 Pipeline，使动态 per-path lease 覆盖 executor 与外部终态 verifier。
- 增加原子写、流式 HTTP byte limit、process-group cleanup、sandbox 状态和独立 capability。

### 第 5 步：移植有服务依赖的默认工具

- `skill/tool_search/ask/config/plan/MCP/image` 保留上游 schema，接 Home 现有服务。
- 移植 LSP、Cron scheduler、BackgroundTaskManager、agent/team/swarm/mailbox 的可达依赖。
- 增加 `homemaster cron start/status/stop` 和 Home child-worker 入口，确保工具不是空壳。
- 39 个默认工具只加入 Home profile；ALFWorld 和 Coworker 工具面保持锁定基线。

### 第 6 步：验证和文档

- 跑 Skills、39 个默认工具及依赖服务的上游原始测试、Home adapter 测试、接口一致性审计和回归。
- 跑真实 `git clone`、脚本、隔离依赖安装、`skill-creator`、HTTPS、磁盘、子进程、Cron、子 agent、
  MCP 和第二进程发现黑盒门。
- 同步 README、用户指南、架构、CHANGELOG、pitfalls、CLAUDE 规则、port manifest 和 progress。
- 全部完成后启动唯一一次最终代码 reviewer，处理发现并针对性复验。

## 十二、验收清单

### A. 上游一致性

- 锁定版本的 OpenHarness Skills 原始测试通过。
- `SkillDefinition` 字段和完整 content 行为一致。
- OpenHarness bundled 八份内容、Home builtin、user/project/extra/plugin 分别按计划顺序发现。
- name、command name、display name、aliases 查询一致。
- `user-invocable`、`disable-model-invocation`、`model`、`argument-hint` 被保留并在对应入口生效；
  未配置的 model 在外部请求前拒绝。
- Available Skills 只放摘要，按需调用返回完整正文。

### B. HomeMaster 有意差异

- 默认自动来源只包含 HomeMaster 自己的 user/project 目录。
- Codex、Claude、Agents 目录不会被自动扫描；外部 Skill 必须先显式迁入或配置为 extra source。
- builtin 覆盖必须经过 Home policy。
- 路径逃逸被外围 adapter 拒绝。
- provenance 和 diagnostics 正确但不改变 `SkillDefinition`。
- 标准 `skill` 与旧 `skill_view` 返回同一正文。
- 标准 `skill` 额外提供结构化 `base_dir` 是明确记录在 port manifest 的 Home 差异。
- Skill 不能增加 ToolView 或 capability。

### C. OpenHarness 默认工具完整性

- Home profile 的 OpenHarness 默认工具集合逐项对比锁定上游 registry，39/39 全部存在；每项分别
  核对公开名称、description、input schema 和 read-only 判定，不用总数相等代替逐项检查。
- MCP 开启后 `mcp_auth/list_mcp_resources/read_mcp_resource/mcp__*` 使用 Home client 返回真实服务结果。
- 每个工具都有 port manifest SHA、原测试 ID 或明确 test-gap、Home adapter 差异和 capability。
- 39 个默认工具始终按上游进入 Home ToolView。内部必需服务必须能启动；外部可选依赖或凭据缺失
  时，调用返回结构化 `unavailable` 和明确原因，不能抛未处理异常，也不能用静默隐藏工具凑 parity。
- `bash` 真实执行 `git --version` 和一个带 stdout/stderr 的非零命令，分别断言 return code 0/非 0；
  timeout/cancel 后用操作系统进程表确认父子进程全部消失。
- 对 file/edit/notebook/worktree/LSP/image/config/plan/Cron/task/agent/team 每个目标分别设置至少一条
  外部终态黑盒门；上游 mock 和 Home adapter 单测不能代替。

### D. 显式移植服务器现有 `skill-creator`

源目录只作为待移植输入，不作为 HomeMaster 自动发现来源：

```text
/hpc2hdd/home/wyuan140/.codex/skills/.system/skill-creator/
```

在隔离临时 HOME 中，把整个目录显式迁入：

```text
$HOME/.homemaster/skills/skill-creator/
```

逐项断言：

- 迁入前 Available Skills 和 `skill(name="skill-creator")` 都不能从 `.codex` 源目录发现它。
- 迁入后动态发现准确名称 `skill-creator`。
- `skill(name="skill-creator")` 返回正文与迁入后 HomeMaster 目录中的文件 bytes 一致。
- `skill_view(skill_name="skill-creator")` 返回同一正文。
- 结构化结果给出 HomeMaster 目标目录，而不是 `.codex` 源目录。
- `read_file` 能读取 `references/openai_yaml.md`。
- protected path、`..` 和 symlink 逃逸仍被拒绝。

### E. 真实完整 Skill 安装黑盒门

在隔离临时 HOME 和临时工作目录中，首先通过 HomeMaster 的 `bash` 完成完整仓库链：

```text
git clone https://github.com/HKUDS/OpenHarness.git
  -> bash return code = 0
git checkout 9b2efd795c6aa09f88b0c257d269a9e518da6ae7
  -> bash return code = 0
独立执行 git rev-parse HEAD
  -> 精确等于锁定 SHA
复制一个完整 Skill 目录到 $HOME/.homemaster/skills/<name>
  -> 目标 SKILL.md、references/scripts/assets 分别核对 hash
skill(name)
  -> 当前进程发现并返回完整正文
```

再使用迁入后的服务器 `skill-creator` 真脚本创建一个新 Skill。Home 调用上下文明确提供安装根目录
`$HOME/.homemaster/skills`；模型调用 `init_skill.py --path` 时必须使用这个目录，不能沿用正文中的
`~/.codex/skills` 默认值：

```text
bash: python scripts/init_skill.py generated-skill --path $HOME/.homemaster/skills
  -> return code = 0
  -> generated-skill/SKILL.md 真实存在
bash: python scripts/quick_validate.py $HOME/.homemaster/skills/generated-skill
  -> return code = 0
skill(name="generated-skill")
  -> 同进程动态发现
第二个 HomeMaster 进程
  -> 再次发现 generated-skill
```

压缩包、脚本和依赖分别设置独立黑盒门：

- 用固定 hash 的 zip 和 tar fixture 调用系统解压命令，逐文件核对路径、bytes 和禁止路径穿越。
- Python 和 Shell fixture 脚本分别写一个 sentinel；断言命令返回码为 0，再由测试进程独立读取
  sentinel 内容。脚本返回非零时不得产生 success。
- 在临时 Python venv 中用项目包管理器安装一个锁定版本的小型真实依赖；核对安装命令返回码，
  再由第二个 Python 进程 import 并核对实际版本。绝不写全局 site-packages。
- 若真机存在 Node/npm，则在临时项目中安装锁定版本的小型真实依赖，再由第二个 Node 进程加载并
  核对版本；不存在时明确报告 unsupported，不能拿 Python 成功代替 npm 成功。

同时保留一条与 `bash/git` 正交的 `web_fetch -> write_file` 网络证据。使用固定 commit 的公开 HTTPS
原始 `SKILL.md`：

```text
web_fetch
  -> 真实 HTTP 状态 200
  -> content 完整、非空
  -> 原始 response bytes SHA-256 匹配固定值

write_file
  -> Pipeline 返回 success + verification passed
  -> 测试进程独立打开文件
  -> 文件 UTF-8 bytes 与 content 完全一致

skill
  -> 同一应用进程立即发现并返回完整正文
```

每一个目标分别断言，不使用“任意一个实例成功就算 PASS”的聚合条件。mock HTTP 只能算单测，不能
替代真实 HTTPS 状态和真实文件终态。

独立网络证据使用不同客户端按同一口径执行：

```text
curl --fail --silent --show-error --location --max-redirs 5 \
  --header 'Accept-Encoding: identity' URL --output remote.bin
sha256sum remote.bin
```

分别断言 curl 返回码为 0、HTTP 工具状态为 success、两边 raw byte SHA-256 相等。

第二进程证据使用真实 CLI，而不是再次调用 loader helper：

```text
HOME=<临时目录> uv run homemaster --dry-run -p '列出 Skills' --output-format json
```

断言 subprocess 退出码为 0、stdout 是单个合法 JSON 文档，并且 `skills` 数组包含准确名称和来源。

### F. 路径和并发

- 应用启动后改变进程 `cwd`，Permission 与 executor 仍解析成同一绝对路径。
- deny rule 命中时文件不存在，executor 调用次数为 0。
- 两个并发 run 写同一路径时，per-path transaction 逐个完成，每次 verifier 都在锁内核对自己的
  内容，不出现第一个写入被第二个覆盖后仍假报成功。
- 写入异常不会留下半个目标文件或临时文件。
- 全部 `ToolExecutionContext` 构造点和 `RegisteredTool` 实现通过接口一致性 audit。

### G. 回归

- Home profile 逐项新增 OpenHarness 39 个默认工具，保留 Home 领域工具和旧 `skill_view`；公开名称
  冲突在 composition 阶段明确失败，不能静默覆盖。
- ALFWorld ToolView 摘要和顺序不变。
- Coworker 11 工具、两名称 `skill_view` 和 scorer 输入不变。
- MCP 开关不再改变 Skills 是否加载。
- 本地 CLI、Gateway、Telegram、飞书等 Home 入口分别实测默认 ToolView 和 principal capability，
  均能调用 OpenHarness 默认工具；每个 channel 单独断言，不能用任一入口成功代表全部入口。
- dry-run、one-shot、interactive、session resume、installed wheel discovery 和全量非联网测试通过。

## 十三、文档、记录与 port manifest

实施完成时同步修改：

- `README.md`：标准 Skills、来源、入口、39 个 OpenHarness 默认工具和依赖服务要求。
- `docs/skills-and-config-user-guide.md`：完整仓库安装、`/<skill>`、脚本/依赖执行和风险边界。
- `docs/architecture/application-runtime.md`：完整 Skills、Tools、后台服务、Permission 和 verification 数据流。
- `CHANGELOG.md`：说明行为变化、原因和新增的网络、文件、进程、任务及调度权限面。
- `docs/pitfalls.md`：记录“移植测试绿，但真实上游格式不可用”的假兼容问题。
- `CLAUDE.md`：兼容移植必须保留原始上游 fixture/test，并做未经修改的真实格式黑盒门。
- `progress.md`：当前状态、下一步、阻塞项和最终证据。
- `plan/V1.9/upstream-port-manifest.json`：纠正旧 loader 的错误模式，加入所有直接移植文件的锁定
  SHA、机械差异、Home adapter 差异和测试 ID。
- `THIRD_PARTY_NOTICES.md`：同步直接移植文件和许可证归属。

commit 前 CHANGELOG 条目与 commit message 使用同一份“改了什么、为什么、影响什么”内容。

## 十四、回滚方式

- 产品修改保持为一个可整体回滚的 change set。
- 回滚代码不会自动删除 agent 已写入用户 HOME 的外部 Skill 文件。
- 不做 Skill 数据迁移；现有 `SKILL.md` 仍是普通 Markdown 文件。
- 不引入 `legacy/new` 双 mode。发生问题时回滚本次 change set，而不是让用户选择两套语义。

## 十五、独立计划评审处理结果

本计划只进行了一次独立只读评审，结论为“不通过，5 个 P1 和 3 个 P2”。全部采纳：

1. **遗漏 bundled Skills**：已补 OpenHarness bundled loader、八份 Markdown、加载顺序、覆盖规则、
   package data 和 wheel smoke。
2. **plugin 边界不可实施**：已改成独立 skills-only data adapter，明确不调用完整 plugin loader，
   不导入 plugin 代码，也不扩充 Home executable extension contribution。
3. **cwd 和跨 run 写锁未闭合**：已明确新增 immutable `working_directory`、动态 canonical path
   resource key，并修改 Pipeline 让同一 lease 覆盖 executor 与 verifier。
4. **slash model 没有承载接口**：已明确 one-shot/interactive 共用 resolver、内置命令优先级、
   `RunRequest.model_override` 和“仅映射已配置模型，否则请求前拒绝”。真实 provider 支持仍标
   **UNVERIFIED**，实施前真环境核对。
5. **HTTP bytes/上限不清**：已固定 identity、`aiter_raw()` bytes、严格 UTF-8、流式硬上限和独立
   curl/hash 黑盒门；httpx 0.28.1 API 已在项目虚拟环境真机核对。
6. **base_dir 是 Home 差异**：已要求写入 port manifest。
7. **计划残留错误文本**：已删除。
8. **第二进程验收同源**：已改为真实 `homemaster --dry-run` subprocess、JSON stdout 和退出码门。

上述独立评审发生在“Skills + 三个通用工具”范围。用户随后明确把范围扩大为 OpenHarness 39 个默认
工具及其依赖服务，本稿已据此重写工具范围、步骤和验收；按“一份计划只进行一次独立计划评审”的
仓库纪律不追加第二次 reviewer。扩大后的范围由用户本轮人工审核。

## 十六、开始实施前需要用户确认的决定

用户已经确认：

1. **Skills 逻辑以 OpenHarness 为准。** HomeMaster 删除自己那套要求 Skill 声明 `tool_names` 的
   通用 Skills 逻辑。同一个 Skill 只解析一次、只保存一份，不维护新旧两套引擎。Coworker 的
   benchmark 专用 `skill_view` 不在这次替换范围内。
2. **只自动扫描 HomeMaster 自己的目录。** 自动来源只有 HomeMaster bundled、
   `~/.homemaster/skills` 和项目 `.homemaster/skills`。extra/plugin 必须显式配置；Codex、Claude、
   Agents 的目录永不自动扫描。外部好用的 Skill 先把完整目录迁入 HomeMaster，再动态发现。
3. **完整移植 OpenHarness 默认工具。** 目标是锁定版本默认 registry 的 39 个工具及 MCP 动态入口，
   不是只增加三个文件/联网工具；有后台服务依赖的工具必须连服务一起移植。
4. **本地 Home agent 接受高风险通用执行能力。** `bash` 可以执行 `git clone`、解压、Python/Shell
   脚本和项目内 Python/npm 依赖安装；网络不承诺仅能访问公网，未启用 sandbox 时可在本机执行。
5. **远程 Home agent 也默认获得完整能力。** 飞书、Telegram、Gateway 等 Home channel principal
   默认获得 `bash`、文件写入、联网、任务、子 agent、Cron 和配置修改所需 capability，不要求逐个
   channel 额外授权。

下面是实施约束，不需要用户选择：

- 模型构建上下文时只看到 Skill 名称和简介；调用 `skill(name)` 时才返回完整 `SKILL.md`。
- 保留旧 `skill_view(skill_name)`，但它和 `skill(name)` 使用同一执行器，不形成第二套逻辑。
- HomeMaster 现有路径保护、权限、来源记录和诊断继续有效，只放在 Skills 核心外围。
- 文件写入必须经过同一条权限、锁和终态验证链，不能只根据内部日志报告成功。
- `bash` 以真实 return code、进程清理和后续外部终态检查验收；stdout 出现“成功”不算完成。
- 39 个 OpenHarness 默认工具只加入 Home profile；ALFWorld 和 Coworker 固定工具面不变。

所有产品范围决定已经确认；用户明确批准本计划开始实施后，才修改产品代码。
