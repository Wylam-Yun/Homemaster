# HomeMaster 与 OpenHarness 通用能力对比及借鉴规格

配套执行文档：[`openharness-homemaster-implementation-plan.md`](./openharness-homemaster-implementation-plan.md)。

状态：历史 V1.9 基线。本文中的 `ObservationService`、provider/model-view binding、freshness/debt 和
环境专用 observation variants 已被
[`generic-screenshot-observe-implementation-plan.md`](./generic-screenshot-observe-implementation-plan.md)
取代，不再描述当前 observation 架构；其余 V1.9 迁移记录按历史证据保留。

日期：2026-07-20

评审对象：

- `../Homemaster`
- `../OpenHarness`

源码基线：

- HomeMaster：`5b150a9671bb087b32ed57971a39fa472e8ff1e1`
- OpenHarness：`9b2efd795c6aa09f88b0c257d269a9e518da6ae7`

本文件是本次通用层改造的唯一规范来源。`openharness-homemaster-high-level-comparison.md`
只负责解释背景和设计理由；两者冲突时以本文件为准。后续实现若更新任一仓库基线，必须先更新
这里的 commit、兼容性清单和 characterization tests，不能继续使用含义不明的“当前代码”。

## 1. 目的

本文回答四个问题：

1. OpenHarness 在哪些通用 Agent 能力上比 HomeMaster 做得更好。
2. 这些设计具体好在哪里，而不是只比较功能数量。
3. HomeMaster 维持现状时会遇到什么实际问题。
4. 哪些 OpenHarness 源码和测试应直接复制、哪些复制后适配、哪些领域 owner 必须由
   HomeMaster 保持权威。

本文不是要把 HomeMaster 改造成 coding agent，也不是要用 OpenHarness 的
`QueryEngine` 替换 HomeMaster 的具身任务内核。目标是让 HomeMaster 复用成熟的
Harness 思路，同时保留自己的任务状态、证据、环境注入和评测能力。

## 2. 总结结论

如果以“通用 Agent 平台”衡量，OpenHarness 当前明显领先，优势集中在：

- 原生异步运行时和并发工具执行。
- 明确的 runtime/session owner、start/close 和资源清理生命周期。
- MCP 的连接、发现、适配和故障隔离。
- Gateway 的多渠道消息抽象、会话路由、取消和远程权限。
- CLI 的安装、配置、诊断、预览和机器可读输出。
- 权限、hooks、plugins 和敏感信息防护。
- 多来源 skills 发现、覆盖规则和调用策略。

HomeMaster 不应替换的核心能力是：

- session-bound `TaskStateStore` 中由模型维护的 `TaskSnapshot`、subtask、constraint、evidence note
  和 completion claim；这些计划状态必须保留，但不属于 verifier/scorer 的权威证据或正式成功状态。
- `ToolResult.success/retryable/failure_reason/evidence_refs`。
- benchmark 通过统一运行入口提交任务，同时提供已经启动好的 environment backend、
  enabled tool ids、运行限制和终态读取能力；它不能再自行装配 Provider、Dispatcher 或
  AgentRuntime，也不能在 benchmark 目录定义 model-facing tool。
- 对声明 `external_terminal_owner` 的具身工具及正式 benchmark，由对应 external terminal/scorer/
  artifact verifier 决定成功；模型 completion claim 不能覆盖 declared verification policy。
- home/robot domain 与 generic agent core 的分层意图。
- ALFWorld V1.8 的 pinned runtime identity、reset transaction、OracleActionGateway、
  provider-attempt/frame authority、closed classification 和分域 action accounting。
- Coworker 的独立 run、固定十一项启用工具、环境/浏览器/录像生命周期、安全展示投影、
  artifact 与 formal-success 验证。

推荐的总体策略是：

> 以 OpenHarness 为通用 Harness 基线；凡通用逻辑契约兼容，先复制固定 commit 的实际源码和
> 对应测试，再做最小 HomeMaster 适配，禁止根据文字说明重新实现同一逻辑。AgentRuntime、资源
> 生命周期和具身执行策略保持 HomeMaster 权威，但可以调用已移植的叶子模块。保留 HomeMaster
> 的 TaskSnapshot、typed evidence、verification、外部环境终态和 benchmark backend 注入能力。

### 2.1 已确认的产品与架构决策

- V1.9 新增 `homemaster` 默认进入交互 Agent、`homemaster -p "..."` 单次任务、
  `--continue/--resume`、`text/json/stream-json` 和 `--dry-run` 契约；当前 CLI 只有
  `homemaster run --utterance`，因此这些不能被描述成已有兼容行为。
- 增加轻量 `doctor`、MCP、session、skills、gateway 控制面。
- 不增加 setup、auth、provider、cron、autopilot、swarm 和 coding-agent 命令。
- provider、model、endpoint 和 auth 继续通过 HomeMaster config/environment 修改；
  CLI 只负责验证、展示来源和脱敏，不负责管理 provider/profile/credential。
- Tool 基础接口借 OpenHarness 的 async `BaseTool`、Pydantic input model 和
  permission-before-execute；catalog/view、具身执行流水线和结果契约按 HomeMaster 需要实现。
- 所有 model-facing tool definition、schema、validator、executor wrapper 和 verifier 归
  HomeMaster Harness 所有。benchmark 目录只实现 environment backend、case/reset、评分、
  录像和 artifact 验证，不能保留 benchmark 私有 ToolRegistry 或 ToolSpec。
- Harness 启动时把 Home、ALFWorld、Coworker、MCP 等工具全部注册到带稳定 internal id、
  provenance 和 version 的 `ToolCatalog`；每次 run 用 `enabled_tool_ids` 冻结一个不可变
  `ToolView`。未启用工具既不发送给模型，也必须在执行层拒绝。
- 同一个模型可见别名允许有多个环境实现，例如不同环境的 `robot_manipulate`，但内部 id
  必须不同；同一 `ToolView` 中别名冲突必须 fail-fast，不能按注册顺序静默覆盖。
- 正式导航工具使用 `robot_go_to`。ALFWorld 不恢复旧 `robot_navigate`；Home 现有
  `robot_navigate` 只作为迁移 wrapper，入口迁移完成后删除。
- 三个环境对模型只暴露一个统一别名 `observe`；Catalog 内分别注册 `home.observe.v1`、
  `alfworld.observe.v1`、`coworker.observe.v1`，并保留各自 schema、executor、media 和 verifier。
  每个 ToolView 恰好启用一个 variant，最终 manifest、prompt、skill 和 model tool result 中不得出现
  旧 observation 别名。legacy 名只允许存在于 execution-only backend adapter。
- 模型必须主动调用 `observe` 获取环境状态。Harness 不在 run 开始、动作完成、普通 tool result、
  verifier 或 provider retry 时向模型隐式注入图片/DOM；审计和录像采集不能形成 model-visible
  ContentBlock、provider binding 或清除 observation debt。
- 导航/操作 backend 可以天然取得动作后的原生 frame，并保存为 internal action receipt、审计、录像
  或领域 verifier evidence；禁止的是 result adapter/runner/dispatcher 自动把它变成模型 ContentBlock。
  视觉验证仍由模型随后显式调用 `observe`，ObservationService 才把当前 frame 建成 model-visible
  ObservationRecord。即使 bytes 与 backend 刚产生的 action frame 相同，也必须有这次显式调用记录。
- 所有 observation variants 统一经过 Harness `ObservationService`，生成 observation id、backend id、
  backend state sequence、capture-event sequence、media type、必填 `content_sha256`、仅 raster 必填的
  `pixel_sha256` 和 evidence ref。Provider retry 必须重用冻结请求和相同 bytes，不能重新 observe 或
  重新序列化。
- 单一 `requires_verification` boolean 被 typed verification policy 取代；每个工具必须声明 execution
  proof、pre-observation、post-action observation debt 和 terminal rule，Runtime 必须执行这些字段。
- Skills YAML frontmatter helper/tests 整文件移植；目录发现和覆盖规则复制实际源码/tests 后适配
  deployment allowlist、source trust 和 capability policy，完整 resolved-path/symlink containment
  必须补写并增加恶意路径测试。
- Gateway 的 DTO、session router 和安全测试在核对 nanobot 二级来源后适配移植；MessageBus 只
  移植 producer/consumer 控制流并补 bounded queue/backpressure；SessionRuntimePool、
  GatewayBridge 与机器人控制适配按 HomeMaster 契约改写。
- 权限接口为以后复用 OpenHarness 通用规则并扩展 requester、channel、robot、area、action risk、
  confirmation、device lease 和 emergency stop 预留 typed 决策。
- 本轮只建立 `PermissionPolicy` 调用位置和 `AllowAllPermissionPolicy` 默认实现，不把完整权限
  规则作为通用层改造的交付前置；后续接 Gateway/真实机器人时替换默认策略。
- CLI、Interactive、Gateway 和 Benchmark 必须调用同一个程序内入口，本文统一写作
  `ApplicationRuntime.run(RunRequest) -> RunResult`。CLI 只是参数解析和输出渲染层；
  benchmark 不启动 CLI 子进程，也不解析 stdout，但必须经过同一条 run/session/tool 链。
- 一个独立 benchmark case/episode 默认对应一个新 session；只有明确评测跨任务连续记忆的
  taskset 才允许多个 subtask 共用 session，不能由 runner 隐式决定。
- `ToolCatalog`、tool 定义、通用 executor wrapper、`ToolExecutionPipeline` 和
  RobotResourceManager 是 application 级共享能力；每次 run 从 catalog 选择一个只读
  `ToolView`，每次调用创建独立 `ToolExecutionContext`。共享对象不得保存可被并发 session
  覆盖的 `_run_context`。
- Benchmark 不得再通过 `build_alfworld_tool_registry()` 或
  `build_coworker_tool_registry()` 拥有平行的 model-facing tool 定义。现有定义迁到 Harness，
  ALFWorld/Coworker 只提供 backend；不同环境可以启用不同工具和不同内部实现版本，共享的是
  catalog 协议、validation、pipeline、session/message 入口和事件模型，不强求所有环境暴露
  相同 schema。
- Benchmark controller 继续拥有环境启动/reset、FastAPI、浏览器、VNC、录像、评分、artifact
  验证和 close；它把已经启动的 backend 以 borrowed run dependency 交给
  `ApplicationRuntime.run()`。Harness 不接管测评环境生命周期。
- V1.9 的代码、测试、配置模板和 release manifest 只允许在 HPC2 的
  `/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster` 修改。`hkust4` 是正式 Coworker runtime
  和验收机，不在其正式 worktree 手改产品代码；HPC2 通过 Git commit/push 到 GitHub，
  `hkust4` fetch 后检出同一 immutable commit。
- 正式 ALFWorld 4/4 gate 仍在 HPC2。若在 `hkust4` 做任何 ALFWorld preflight、复现、pytest、
  import probe、runner 或 verifier，命令必须通过 `conda run -n hm_alfworld ...` 执行；禁止使用裸
  `python`、`uv run` 或 Coworker `.venv`。该次执行必须记录 Conda environment name、Python
  executable/version、`conda list --explicit` 输出 hash，以及 HomeMaster、ALFWorld、ai2thor 的
  import origin；本机 preflight/runner 对这些字段缺失必须非零退出，跨机报告合并器将缺少该证据的
  hkust4 ALFWorld 结果标记为 `UNVERIFIED`。这类结果不能替代 HPC2 gate。
- 最终验收必须在最终候选 commit 上使用真实 LLM，对该 SHA 已提交的固定 inventory 和运行前完成
  hash/identity 校验的外部 dataset bytes 做 fresh run：
  ALFWorld 固定四条数据且四条全部正式通过；Coworker 因当前只有一个 test-set item，只运行并
  通过这一条。mock、scripted/loopback provider、旧 run artifact、少跑数据或只通过 contract tests
  都不能替代这两个 live gate。
- 所有普通入口和 benchmark 都使用同一个 `SessionManager`。普通 episode/Coworker run 默认
  新 session；连续 ALFWorld taskset 只有在 taskset 显式声明时才复用 session。

### 2.2 代码迁移原则

迁移不整体复制 OpenHarness 后持续删代码，也不从文字重写已经存在的通用逻辑，而按 §18 三种
port mode 实施：

| 类型 | 处理方式 | 典型模块 |
|---|---|---|
| V：低耦合叶子模块 | 整文件与测试复制，仅机械改 import/header | skills YAML frontmatter、选定 leaf types/protocol fixtures |
| A：通用能力但需换契约 | 复制明确 symbol；存在直接上游测试时一并复制并先通过原断言，再适配 | MCP client、BaseTool/query flow、Channel/router/bus/bridge、PermissionChecker、SessionBackend、CLI dry-run |
| H：领域/runtime owner | 保持 HomeMaster 权威，并调用 V/A 叶子模块 | AgentRuntime、ApplicationRuntime、Catalog/View、ObservationService、robot policy/lease/stop、benchmark scorer |

每批代码都写 `upstream-port-manifest.json`，记录实际 source repo/commit/path/symbol/hash、目标和
测试。固定上游 commit 对某个被复制 symbol 没有直接测试时，不能编造 copied node id：manifest 的
`copied_test_ids` 为空，并必须记录 `upstream_test_gap`、检索证据和新增 HomeMaster characterization
test ids。OpenHarness 为组内可直接使用的来源，不做额外项目授权阻塞；channel 已记录的 nanobot 二级
provenance chain 仍需原样保留并写入 notices，不能抹除来源。

## 3. 比较口径

本规格只比较“通用部分”，包括：

- CLI 和安装体验。
- Provider、runtime、tool loop 和生命周期。
- Tool、skills、MCP、gateway。
- 配置、认证、权限、hooks、plugins。
- Session、context compaction、events、日志和测试。

HomeMaster 的记忆检索算法、机器人 grounding、ALFWorld 任务效果不与
OpenHarness 的 coding tools 做横向优劣比较，因为两者领域目标不同。

## 4. 总体比较矩阵

| 能力 | OpenHarness | HomeMaster 当前状态 | 借鉴价值 | 决策 |
|---|---|---|---|---|
| CLI 产品化 | setup、dry-run、多输出格式、配置子命令、入口别名 | run/shell/doctor/session/benchmark | 高 | 移植默认交互、print、dry-run、恢复和输出契约；删除 setup/auth/provider 等不需要的控制面 |
| 异步 runtime | async provider、async tools、并发调度 | 同步 generator 和顺序 dispatcher | 必须 | 原生异步化 HomeMaster 核心链路 |
| 资源生命周期 | RuntimeBundle、start/close、session runtime pool | CLI、ALFWorld 和 Coworker 分别装配 runtime；测评环境另有正确但独立的 run 生命周期 | 必须 | 新建 ApplicationRuntime/SessionManager/RunScope；统一 Harness 装配，同时让 benchmark controller 继续拥有环境 start/reset/finalize/close |
| Tool 注册 | application registry，但同名静默覆盖 | Home、ALFWorld、Coworker 各自 registry，名称和 schema 有合理差异 | 必须 | 所有 tool definition 移入 Harness ToolCatalog；按 run 冻结 ToolView，允许带 internal id 的环境版本，别名冲突 fail-fast |
| Tool 校验 | builtin 使用 Pydantic；MCP schema adapter 只覆盖浅层基础类型 | Dispatcher 只检查 required 字段，ALFWorld/Coworker 另有领域校验 | 高 | builtin 使用 strict Pydantic，动态 MCP 使用保真 JSON Schema；领域 backend 校验继续保留，统一输出 ToolExecutionResult |
| Tool 语义 | read-only 与权限联动 | 有 verification/state/failure 字段，ALFWorld/Coworker 已有更强外部验证 | 高 | 通用 pipeline 强制执行声明；具体 verifier/backend 保留最新领域逻辑 |
| Observation | coding 场景主要读取文件/终端输出 | ALFWorld runner 注入初始图片，browser/robot 观察路径分散 | 必须 | 三个环境的 model alias 统一为 `observe`，内部 variant 可不同；backend 只实现 capture，runner/result/verifier 不再组装模型图片或 DOM |
| Skills | 多来源发现、YAML、覆盖顺序、插件接入 | 两个硬编码 builtin，简易 parser | 必须 | 直接或轻度适配移植 loader/tests，扩展而不替换 HomeMaster SkillSpec |
| MCP | stdio/HTTP、状态、工具、资源、错误隔离 | 无 | 必须 | 异步内核完成后移植 client/types/tests |
| Gateway | bus、channel、router、runtime pool、取消、安全 | 无 | 必须 | 移植 DTO/bus/router/tests，重写 HomeMaster runtime pool、bridge 和机器人取消语义 |
| 权限 | 模式、工具/路径/命令规则、敏感路径、确认 | 无统一权限层 | 必须 | 在 gateway/机器人上线前落地 |
| Hooks | 生命周期事件、timeout、blocking result | 无 | 中高 | 借事件模型，安全场景优先用 policy 而非任意脚本 |
| Plugins | manifest、skills/tools/hooks/MCP/commands | 无 | 中 | MCP/gateway 稳定后再做 |
| Provider/Auth | profile、secret storage、setup、状态检查 | YAML 内含 provider/key，env override | 选择性 | 不移植管理 CLI；保留 config/env，补 schema、来源诊断和脱敏 |
| Session | backend protocol、恢复清洗、会话路由 | 有快照和 task state，但无 app 级 pool | 高 | 保留快照内容，借 backend 和路由契约 |
| Context | 自动/响应式压缩、工具输出外置 | 已有较强 domain-aware compaction | 选择性 | 不替换，补异步和统一输出策略 |
| Events | UI stream events、gateway progress | RuntimeEvent、ALFWorld ledger、provider attempts、Coworker presentation/artifact 各有职责 | 选择性 | RuntimeEvent 统一 live/UI 投影；权威领域 ledger 独立持久化，Gateway 只消费脱敏 PublicEvent |
| 安装与诊断 | 隔离 venv、全局入口、版本验证 | 本地 venv 缺声明依赖时 doctor 也无法启动 | 必须 | 先修 packaging 和 doctor 懒加载 |

## 5. CLI 与安装

### 5.1 OpenHarness 做得更好的地方

OpenHarness 在 `pyproject.toml` 中提供多个稳定入口：

- `openharness`
- `oh`
- `openh`
- `ohmo`

主入口同时支持：

- 默认交互模式。
- `--print` 单次无交互执行。
- `text/json/stream-json` 输出。
- `--dry-run` 在不调用模型和工具时解析配置、认证、skills、tools 和 MCP。
- `setup/auth/provider/config/mcp/plugin` 等控制面子命令。
- `--continue/--resume`。
- 独立安装脚本创建 venv，并将命令链接到 `~/.local/bin`。

其价值不在于命令多，而在于把以下场景变成正式契约：

- 人工演示。
- shell 脚本调用。
- CI smoke test。
- 上层 gateway/backend 调用。
- 出问题前的 readiness 检查。

相关实现：

- `../OpenHarness/src/openharness/cli.py`
- `../OpenHarness/scripts/install.sh`
- `../OpenHarness/tests/test_commands/test_cli.py`

### 5.2 HomeMaster 当前会遇到的问题

HomeMaster 已有模块化 Typer command handler，这是可保留的优点；但外部契约不足：

- 只有 `homemaster` script 和 `python -m homemaster.cli`，没有完整的
  `python -m homemaster` 入口。
- `run` 主要输出带标签的人类文本，没有稳定的 JSON/JSONL result envelope。
- 没有 dry-run，展示前无法一次确认 provider、skills、tools、session 路径是否就绪。
- provider/auth 依赖人工编辑 YAML 或 environment；这是本次确认保留的配置方式，
  但缺少 schema validation、来源报告、redaction 和 readiness 检查。
- `doctor.py` 顶层先导入 provider client；缺少 `anthropic` 时 doctor 自己先 import
  失败，无法报告缺失依赖。

具体例子：当前项目 `.venv` 中 `bm25s/jieba` 可用，但 `anthropic/openai` 缺失。
执行 CLI 相关测试时在 collection 阶段失败，`doctor` 没有机会生成诊断报告。

### 5.3 借鉴决策

借鉴意义：必须，优先级 P0。

应该借：

- 默认交互入口、`--print`、命令和输出契约。
- dry-run/readiness 思路。
- 入口与安装验证测试。
- lazy import，使 doctor 在运行依赖不完整时仍能工作。
- `--continue/--resume` 的恢复行为和错误契约。

不应该借：

- 不复制 OpenHarness 约 2500 行的 `cli.py`。
- 不移植 setup、auth、provider、cron、autopilot、swarm 和 coding-agent 命令。
- 不让 CLI 直接成为 ApplicationRuntime、Gateway 或 domain tool 的装配中心。

HomeMaster 应复制 OpenHarness 的入口行为和对应 tests，但继续保持模块化结构：

```text
cli/app.py          根入口和公共参数
cli/output.py       text/json/stream-json envelope
cli/dry_run.py      Runtime 静态装配预览
cli/doctor.py       环境、依赖与可选 live health
cli/session.py      session 管理和恢复
cli/mcp.py          MCP 配置、状态与诊断
cli/gateway.py      Gateway 启动与状态
cli/skills.py       Skills 发现与来源诊断
cli/benchmark.py    HomeMaster benchmark 子命令
```

这些 command handler 不能各自创建 runtime。它们只负责把输入转换成同一个请求：

```python
result = await application.run(
    RunRequest(
        text=text,
        session_id=session_id,
        environment=borrowed_environment,
        enabled_tool_ids=enabled_tool_ids,
        run_policy=run_policy,
        terminal_policy=terminal_policy,
        metadata=metadata,
    )
)
```

这些字段的职责不能混用：

```text
RunPolicy
  `- stop_condition / max turns / 本次运行终止规则

environment
  `- benchmark/deployment 已启动好的 borrowed backend；Harness 不负责 start/reset/close

enabled_tool_ids
  `- 本次 run 可发送给模型并可实际执行的 Harness tool internal ids

TerminalPolicy
  `- 当前 run 是否 terminal、terminal 后阻止哪些动作；不包含 benchmark 最终 scorer

metadata
  `- 只用于标签、关联 ID 和展示，不得承载 stop、tool enablement、environment 或 scorer 控制逻辑
```

`RunPolicy`、environment、enabled tools 和 `TerminalPolicy` 是本次 run 的依赖，不作为 session
snapshot 持久化；snapshot 只保存可重新绑定的 environment/profile ref。恢复 session 时由当前
CLI/Gateway/Benchmark 入口重新提供活跃 backend。Benchmark runner 在 `RunResult` 返回后读取
权威外部终态并执行 scorer；generic Runtime 只消费通用 terminal/verification 结果，不知道
ALFWorld score eligibility、Coworker formal success 或录像评分。

CLI 将 flags/argv 转成 `RunRequest`，再把 `RunResult` 渲染成 text/json/stream-json；
Interactive、Gateway 和 Benchmark 直接在进程内调用同一个 `application.run()`。这里所说
“Benchmark 经过 CLI 入口”，指复用 CLI 背后的正式 run command contract，不是每个 case
启动一个 CLI 子进程后解析 stdout。

首版入口契约：

```text
homemaster                         默认进入交互 Agent
homemaster -p "任务"               单次执行
homemaster --dry-run [-p "任务"]   静态装配预览
homemaster --continue
homemaster --resume <session-id>

homemaster doctor
homemaster mcp ...
homemaster session ...
homemaster skills ...
homemaster gateway ...
homemaster benchmark alfworld ...
```

现有 `run` 和 `shell` 可在一个迁移周期内保留为兼容别名，之后再决定是否删除。

`dry-run` 不调用模型、不执行工具，也不连接外部 MCP server；它解析最终 config、
provider/model/auth readiness、skills 来源、builtin tool catalog、MCP 静态配置、permission
mode、session 路径和 system prompt 摘要，并输出 `ready/warning/blocked`。未连接的 MCP 动态
tools 必须显示为 `unknown_until_connect`，不能伪造为已注册；需要 live 发现时使用显式
`--probe`。`doctor` 则检查
Python/依赖/目录和可选 live provider、MCP、gateway health，两者不能合并成一个命令。

## 6. 异步 Runtime 与并发

### 6.1 OpenHarness 做得更好的地方

OpenHarness 的主调用链原生异步：

- Provider 使用 async streaming。
- `BaseTool.execute()` 是 async。
- MCP client 的 connect/call/close 是 async。
- 多个工具调用可以通过 `asyncio.gather()` 并发。
- Gateway 通过 task 处理多个 session。
- 等待模型或工具期间仍能发送进度、处理取消和维护 heartbeat。

相关实现：

- `../OpenHarness/src/openharness/engine/query.py`
- `../OpenHarness/src/openharness/tools/base.py`
- `../OpenHarness/src/openharness/ui/runtime.py`

### 6.2 HomeMaster 当前会遇到的问题

HomeMaster 当前是同步链路：

```text
model stream -> for delta -> dispatcher -> executor -> next model call
```

`GenericAgentRuntime.run()`、provider stream 和 `ToolDispatcher.dispatch()` 都会占用
当前线程。Dispatcher 对多个 tool call 默认逐个执行。

这对单用户 benchmark 不一定有问题，但会直接限制计划中的能力：

- 一个远程机器人动作等待 30 秒时，同一 gateway event loop 不能直接处理其他会话。
- MCP client 需要长生命周期 async session，不能每次工具调用临时 `asyncio.run()`。
- 新消息无法可靠取消旧任务。
- provider stream、progress message、gateway heartbeat 无法自然并行。
- 多机器人、多会话和只读 observation 的并发无法表达。

### 6.3 借鉴决策

借鉴意义：必须，优先级 P0。

不直接复制 OpenHarness QueryEngine，而是把 HomeMaster 自己的调用链改成：

```python
async def run(...): ...
async def dispatch(...): ...
async def stream(...): ...
```

Tool executor 需要同时支持：

- `inline`：快速同步状态计算。
- `thread`：暂时无法异步化的阻塞 SDK。
- `async`：MCP、HTTP、gateway、远程机器人。

异步不意味着机器人动作默认并行。默认仍应串行，只有显式声明为 parallel-safe 的
工具才能并发；同一机器人需要 device lock。

## 7. Application 与 Session 生命周期

### 7.1 OpenHarness 做得更好的地方

OpenHarness 的 `RuntimeBundle` 集中持有：

- API client。
- MCP manager。
- Tool registry。
- Query engine。
- Hook executor。
- Session backend。
- Plugin/skill roots。

它提供 `build_runtime()`、`start_runtime()` 和 `close_runtime()` 这些显式函数。ohmo
gateway 又在其上维护每个 chat/thread 的 runtime bundle，并在 cwd 或配置变化时关闭、
重建资源。但这些名字不能被理解成已经实现了严格的 acquire/start/rollback 三阶段：
`build_runtime()` 内部已经会连接 MCP、可能启动 Docker sandbox，`start_runtime()` 主要
执行 session-start hook。

需要准确区分“生命周期明确”和“application 级共享”：OpenHarness 当前
`OhmoSessionRuntimePool.get_bundle()` 会为每个 session key 调用 `build_runtime()`，
而 `build_runtime()` 会创建该 session 自己的 API client 和 MCP manager。因此，
OpenHarness 值得借鉴的是 owner/start/close 契约，不是它当前的资源共享粒度。
HomeMaster 不能直接复制该 pool，否则多个聊天仍会重复创建 MCP 和 provider 资源。

关闭链也不能原样照抄：CLI/headless/print 路径会在 `finally` 中调用 `close_runtime()`，
但当前 `OhmoGatewayService.run_foreground()` 退出时只取消 bridge/channel tasks 并停止
ChannelManager；`OhmoSessionRuntimePool` 没有统一 `close_all()`，service 也没有逐个关闭池中
所有 RuntimeBundle。HomeMaster 的 ApplicationRuntime 必须把“关闭全部 session task，再关闭
共享 MCP/provider/channel/robot 资源”写成强制 shutdown contract 和测试。

初始化和关闭的失败语义也需重写：OpenHarness `build_runtime()` 在 MCP 已连接后若后续
registry/hook/engine/sandbox 构造失败，没有统一回滚；`close_runtime()` 也不是逐项
best-effort cleanup，前一个关闭步骤抛错可能跳过后续 MCP、hook 或 API client。HomeMaster
应使用 async context manager 或 cleanup stack：每成功 acquire 一个资源就立即注册对应
release，启动失败逆序回滚；关闭阶段聚合错误但继续清理其余资源。

共享资源还必须标记所有权。OpenHarness 的 bundle 虽记录 `external_api_client`，当前
`close_runtime()` 仍会关闭传入的 client；这一点不可复制。HomeMaster 的
ApplicationRuntime 对 Provider/MCP/robot/channel resource 使用 `owned/borrowed` 语义：
SessionRuntime 永远无权关闭 application-owned 或 borrowed resource，只有实际 owner 在
application shutdown 时关闭一次。

### 7.2 HomeMaster 当前会遇到的问题

`agent/turn.py::run_agent_turn()` 每一轮都会重新创建：

- Provider client。
- Dispatcher。
- Tool registry。
- Context assembler。
- GenericAgentRuntime。

这会导致：

- MCP subprocess 和 HTTP session 没有合适的持有者。
- gateway 每条消息都可能重复连接 provider/robot。
- close、reconnect、health status 无法统一实现。
- CLI、benchmark、gateway 各自复制运行时装配逻辑。
- session 虽然保存 messages/state，却没有保存活跃资源和运行状态。

### 7.3 借鉴决策

借鉴意义：必须，优先级 P0。

HomeMaster 应新增 application、session 和 turn execution 三个层级，并增加唯一的程序内
提交入口：

```text
ApplicationRuntime
  |- run(RunRequest) -> RunResult
  |- compact(session_id) -> CompactResult
  |- cancel/status(session_id)
  |- AgentRuntime
  |- ProviderFactory / ConfigResolver
  |- MCPManager
  |- ToolCatalog
  |- ToolExecutionPipeline
  |- ObservationService
  |- RobotConnectionPool
  |- PermissionPolicy（首版 AllowAll）
  |- SessionManager
  `- EventBus

SessionRuntime
  |- AgentSession
  |- AgentState
  |- TaskStateStore
  |- 可持久化 environment/profile ref
  |- turn_lock
  `- active_task / cancellation

每次 run/turn
  |- borrowed EnvironmentBackend
  |- immutable ToolView(enabled_tool_ids)
  |- RunPolicy / TerminalPolicy
  `- ToolExecutionContext
       |- session/run/turn id
       |- TaskStateStore
       |- active environment backend
       |- latest committed observation
       |- requester / permission subject
       |- event scope
       `- cancellation token
```

ToolCatalog、通用 executor wrapper、ToolExecutionPipeline、ObservationService 和机器人资源
管理器由 ApplicationRuntime 统一拥有；对话历史、task state 和 turn lock 属于 session。
活跃 ALFWorld adapter、Coworker service/browser/recording 和其他 benchmark environment 属于
benchmark run，由 benchmark controller 启动并关闭，Harness 只在 run 期间借用。

MCP/Provider/robot connection 的实际实例数量不能用“application 全局一个”写死。它们应声明
`application/tenant/session/run` scope 和 `owned/borrowed` 所有权：ApplicationRuntime 统一管理
scope，不代表跨租户共享凭据或让 SessionRuntime 关闭不属于自己的资源。

统一调用链是：

```text
CLI / Interactive / Gateway / Benchmark
  -> ApplicationRuntime.run(RunRequest)
  -> SessionManager.open_or_resume(session_id)
  -> ToolCatalog.freeze(enabled_tool_ids) -> ToolView
  -> 创建本次 ToolExecutionContext
  -> AgentRuntime.run_turn(...)
  -> 共享 ToolExecutionPipeline.execute(tool_call, context)
  -> 更新并保存 SessionRuntime
  -> RunResult
```

Benchmark controller 负责枚举 case、启动/reset/finalize/close 外部环境、创建 session id、选择
enabled tool ids，并在 `RunResult` 返回后评分；它不再创建 Provider、Dispatcher、
ContextAssembler、AgentRuntime 或 model-facing ToolSpec。一个 episode 的任务文本在运行语义上
等价于通过正式消息入口向新 session 提交一段话，但图片不再由 runner 塞入 prompt。

模型必须主动调用当前 ToolView 中唯一的 `observe`。Home、ALFWorld、Coworker 在 Catalog 中是
三个 internal variants，但 provider manifest 使用同一个 model alias。ObservationService 再调用
borrowed environment 的 `capture()`，保存 observation id、backend/environment sequence、media type、
content hash、条件化 pixel hash 和 evidence ref，并把图片或 canonical structured state 作为 tool
result 加入 session。导航/操作 executor 可以从 backend 获得 native post-action frame 并写 internal
evidence ledger，但任何初始 prompt、动作结果、普通 `frame_path`、verifier、录屏或 artifact capture
都不得隐式产生模型观察。

当前 `_run_episode()` 和 Coworker `_run_runtime()` 仍各自注册 model-facing tools。目标是把这些
ToolSpec/executor wrapper 移到 Harness ToolCatalog；`benchmarking/alfworld` 与
`benchmarking/coworker_demo` 只保留 environment backend、translator/grounding、case 生命周期、
scorer、录像和 artifact verifier。不同环境的同名工具可以有不同 internal id/schema/backend，
但都必须经过同一 catalog 选择、validation、ToolExecutionPipeline、SessionManager 和消息入口。

最新 Dispatcher 已不再包含旧版 `_terminal_alfworld_result()` 和
`_sync_alfworld_outcome()`；当前边界是通用 `ToolDispatchObserver` 与
`AlfworldToolDispatchObserver`。迁移时应把现有 observer 行为收敛为通用 terminal policy /
execution observer，而不是寻找或恢复旧函数：

```text
ToolExecutionPipeline
  -> TerminalPolicy.before_execute(tool_call)
       `- episode terminal 时拒绝新的 robot backend action
  -> execute
  -> tool-specific verifier
  -> ExecutionObserver.after_execute(tool_call, result)
       `- 同步 terminal owner / classification / terminal tool call id
```

最终 score eligibility/scorer 仍由 benchmark controller 根据权威环境终态和领域 ledger 计算。
验收必须证明 terminal 后 backend action count 不再增长，并且新入口与基线 `5b150a9` 的
classification、计数、evidence 和 scorer input 一致。

## 8. Tool 契约、校验与调度

### 8.1 OpenHarness 做得更好的地方

OpenHarness 每个工具使用 Pydantic `input_model`：

- 模型 schema 与运行时 validation 来自同一来源。
- builtin tool 可验证类型、枚举、必填字段和嵌套结构。
- `is_read_only()` 直接参与 permission decision。
- 工具统一为 async interface。

但其 `McpToolAdapter` 当前只把 MCP JSON Schema 第一层基础类型转换成动态
Pydantic model；复杂 nested schema、enum 和 additional properties 不能视为完整保真。
HomeMaster 应借 builtin tool 契约，不应原样复制这段 MCP schema 转换实现。

### 8.2 HomeMaster 当前会遇到的问题

HomeMaster 的 `ToolSpec` 具有适合机器人领域的字段：

- `output_schema`
- `requires_verification`
- `state_effects`
- `failure_semantics`
- `executor_mode`

但当前实际执行存在两个问题。

第一，Dispatcher 只检查 JSON Schema 的 `required`，不验证类型、enum、嵌套对象或
additional properties。例如模型把机器人速度传成字符串，只要字段存在就可能进入
executor。

第二，`output_schema/requires_verification/state_effects/failure_semantics` 基本没有被
Dispatcher 或 Runtime 执行。它们目前更多是声明，而不是有效策略。

第三，当前 `ToolDispatcher` 同时保存 `_specs` 和可变 `_run_context`，普通 turn 通过
`set_run_context()` 后再用 `__call__()` 执行。一个 Dispatcher 如果被并发 session 共享，
后设置的 context 可能覆盖先前 session 的 context。这个问题不表示 tools 不能共享；
它表示共享 Dispatcher 不能保存 session-bound mutable state。

具体风险：一个标记 `requires_verification=True` 的远程操作完成后，Runtime 仍可能
直接让模型结束；`state_effects` 也不会用于锁、审计或冲突检测。

### 8.3 借鉴决策

借鉴意义：高，优先级 P0/P1。

建议：

- 参考 OpenHarness async `BaseTool` 和 input model；`ToolCatalog`、`ToolView`、
  `ToolExecutionContext` 与 pipeline 按 HomeMaster 的多环境和证据契约实现，不能照搬
  OpenHarness registry 的同名覆盖语义。
- `ToolDefinition` 只保存 immutable、可序列化的 id/alias/schema/policy/provenance，不包含 executor；
  `RegisteredTool` 组合 definition、executor 和 optional verifier，provider manifest 和 session
  persistence 只能看到 definition 投影。
- 统一结果为 canonical `ToolExecutionResult`，其 `status` 使用 typed enum，并校验 status、error、
  retryability、outcome certainty、terminal 和 verification 的合法组合；保留 HomeMaster
  `success/failure_reason/retryable/evidence_refs`，并能承载 text、structured data、image/
  attachment、observation ref、verification 和 terminal 信息，不退化成只有 output/is_error。
- builtin tool 使用 Pydantic input model；动态 MCP tool 使用保真 JSON Schema validator
  或等价的完整转换器验证输入和输出。
- 增加 `execution_backend/timeout_s/concurrency_policy/resource_key`。
- 删除 legacy `requires_verification` boolean，由 Runtime 执行完整 typed `VerificationPolicy`，并把其
  pre-observation、execution proof、post-action debt 和 terminal rule 接入 termination policy。
- 把 `state_effects` 接入 device lock、审计和冲突策略。
- 把 `failure_semantics/retryable` 接入统一 retry policy。
- `ToolCatalog`、ToolDefinition/executor wrapper 和 `ToolExecutionPipeline` 由
  ApplicationRuntime 创建一次；每个 run 用 internal ids 冻结自己的 immutable `ToolView`。
  删除 `ToolDispatcher.set_run_context()` 这种绑定方式，每次 `execute()` 显式传入
  `ToolExecutionContext`。
- 共享 executor 不得通过闭包、实例字段或 factory 参数捕获 world、memory root、session、
  task store 或 environment；这些依赖只允许从显式 `ToolExecutionContext` 读取。当前部分
  HomeMaster factory 参数实际上没有进入 executor，应删除这种误导性参数或改成真正的
  context dependency。
- `homemaster.tools.spec.ToolSpec`（或其后继 immutable ToolDefinition）必须成为唯一
  canonical 类型；删除 `generic_runtime.ToolSpec` 和 `_to_tool_specs()` 投影层，AgentRuntime
  直接消费 canonical definition 生成的只读 model manifest，不能在转换时丢失
  verification、state effects、failure semantics 或 selectability。
- ToolCatalog 中每项都有稳定 `internal_id/source/provenance/version`。不同环境可以注册相同
  模型别名的不同实现；只有同一 ToolView 内出现别名冲突才 fail-fast 并报告双方来源。
  builtin、MCP、plugin 和 domain tools 使用稳定 namespace；不能复制 OpenHarness 当前静默
  覆盖 `self._tools[name]` 的行为。

目标 Tool 接口：

```python
@dataclass(frozen=True)
class ToolDefinition:
    internal_id: str
    model_alias: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    verification_policy: VerificationPolicy
    provenance: ToolProvenance

@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    executor: ToolExecutor
    verifier: ToolVerifier | None = None
```

这里的共享边界必须明确：

```text
Application 级共享
  |- ToolCatalog 中全部 definition/variant/provenance
  |- stateless executor wrapper
  |- MCP tool definition and connection manager
  |- ToolExecutionPipeline
  |- ObservationService
  `- RobotResourceManager（统一做排队、lease 和 stop）

每次 run 独立
  |- immutable ToolView(enabled_tool_ids)
  |- borrowed EnvironmentBackend
  `- RunPolicy / TerminalPolicy

每次调用独立
  |- session/run/turn id
  |- TaskStateStore
  |- 当前 environment backend 和 committed observation
  |- requester 和 permission subject
  |- event/evidence scope
  `- cancellation token
```

例如 Home 与 ALFWorld 都可以向模型暴露 `robot_go_to`，但 catalog 中可以分别是
`home.robot_go_to.vN` 与 `alfworld.robot_go_to.vN`，并保留各自 schema、validator、verifier 和
backend adapter。某个 run 只能启用其中一个同名 variant；disabled variant 不发送给模型，
即使模型或外部调用方伪造 internal id/alias，执行层也必须返回 `tool_disabled`。ALFWorld 的
ToolView 不得包含 `robot_navigate`；Home 的旧 `robot_navigate` 仅为迁移 wrapper，所有入口
切到 `robot_go_to` 后删除。

这不等于 benchmark 拥有工具定义。上述 variant 全部在 Harness ToolCatalog 内；benchmark
只提供 active borrowed backend 和 enabled ids。若两个 session 最终指向同一台真实机器人，
共享 RobotResourceManager 负责串行化和 lease，而不是共享可变 run context。

observation capability 也是 Harness tool family。Home、ALFWorld、Coworker 分别注册
`home.observe.v1`、`alfworld.observe.v1`、`coworker.observe.v1`，model alias 都是 `observe`；同一
ToolView 只能启用一个 variant。模型主动调用后，service 才调用当前 backend 的 `capture()`，生成
媒体无关的 ObservationRecord：observation id、internal tool id、backend id/run generation、backend
state sequence、capture-event sequence、
media type、必填 content hash、仅 raster image 必填的 pixel hash 和 evidence ref。Home structured
state 与 Coworker canonical DOM JSON 的 pixel hash 必须为 null。Harness 不在 run 开始、动作后、
普通 result 转换、provider retry 或 verifier 中自动补图/补 DOM。

每个 ToolDefinition 必须使用 typed verification policy，不能再靠未执行的 boolean：

```text
execution_proof: none | structured_receipt | external_state
requires_pre_observation: false | current_bound
post_action_observation: none | fresh_after_backend_advance
terminal_rule: normal | external_terminal_owner
```

- `structured_receipt` 只证明该调用得到后端回执，不等于新的观察。
- `external_state` 允许 verifier 从 typed backend state 判断，但 verifier 不拥有
  `capture_for_model()` capability。
- `fresh_after_backend_advance` 在任何实际 backend attempt 使 sequence 前进后建立 observation debt；
  成功、失败或 `outcome_unknown` 都相同。纯 validation failure 没有触碰 backend 时不建立 debt。
- 有 debt 时，要求 fresh state 的下一动作、model completion claim 或 task completion 必须返回 typed
  `observation_required`；只有显式 `observe` 产生且绑定同一 backend/run/generation 的记录才能清除。
  capture-event sequence 必须晚于 action completion event，source state sequence 必须大于等于 action
  post-state sequence；observe 不改变环境时，不能错误要求 state sequence 严格递增。
- 不需要视觉验证的工具可以凭 structured receipt/external state 继续；`none` 只允许模型从结果推理，
  不能被 Runtime 提升为 verified success。正式 benchmark success 始终由独立 scorer/verifier 决定。

ALFWorld 的观察授权状态机固定为：

```text
NEEDS_OBSERVE --observe--> OBSERVED_UNBOUND
OBSERVED_UNBOUND --successful frozen provider request binds exact bytes--> BOUND_READY
BOUND_READY --backend sequence advances--> NEEDS_OBSERVE
BOUND_READY --external terminal owner closes--> TERMINAL
```

初始 request 不带图片，`observe` 是唯一可在没有 committed frame 时执行的环境工具。同一 assistant
response 中 `observe` 后紧接 mutating action 必须拒绝，因为观察尚未进入下一次 successful frozen
provider request；provider retry 重用同一 payload，不 recapture。动作后旧 committed view 立即失效，
即使旧图片仍在历史消息中也不能重新授权。导航/操作天然返回的 frame、审计/录屏
`capture_for_audit()` 产物可进入 internal evidence，但不能直接成为 ContentBlock/provider binding 或
observation-debt 清除依据；显式 `observe` 可以读取 backend 当前 frame 并创建新 observation record。
bootstrap `observe` 的 `capture()` 是
read-only observation，不计为 OracleActionGateway 的 ALF model backend action；它仍必须经过
ToolView、terminal、validation、permission、cancel/deadline 和 observation-ledger 阶段。

首版工具策略矩阵（具体 schema 可分域，但不得弱于此表）：

| 环境/工具 | execution proof | pre-observation | post-action |
|---|---|---|---|
| Home `observe` | structured receipt | 无 | 无 |
| Home `robot_go_to` | structured backend receipt | 可无 | backend advance 后 fresh `observe` |
| Home `robot_manipulate` | structured backend receipt | current bound | backend advance/unknown 后 fresh `observe` |
| Home `robot_verify` | external state | current bound 或 external terminal override | 无 |
| ALFWorld `observe` | frame-ledger receipt | bootstrap 允许 | 无 |
| ALFWorld `robot_go_to`/`robot_manipulate` | gateway/execution receipt | current bound | backend advance/unknown 后 fresh `observe` |
| ALFWorld `robot_verify` | external `current_state.won` | 无 debt或 terminal override | 无 |
| Coworker `browser_navigate` | URL/state/evidence receipt | 无 | 必须显式 `observe` 读取 DOM |
| Coworker `observe` | canonical DOM receipt | 页面已导航 | 无 |
| Coworker click/fill/select/wait | typed receipt/readback/exact job status | 按工具要求 current DOM | 无自动观察 |
| Coworker terminal/SOP | independent/persisted structured receipt | 无 | 无 |
| task/memory/skill/read-only tools | none 或 structured receipt | 无 | 无 |

最后一行允许模型根据 tool result 作判断，但 Runtime 不得把 model judgment 标记为 verified success。
Coworker fill/select readback、click receipt 和 wait exact status 足以继续时不强制图片/DOM观察；这与
禁止环境静默把新页面状态塞给模型并不冲突。

统一执行流水线：

```text
ToolView / terminal gate
  -> input validation
  -> PermissionPolicy
  -> cancellation / deadline
  -> pre-observation gate
  -> concurrency policy / resource lock / device lease
  -> execute
  -> result-schema validation
  -> policy-applicable verifier
  -> post-action observation debt
  -> authoritative evidence/domain ledger
  -> Public RuntimeEvent projection
```

首版 `PermissionPolicy` 使用 `AllowAllPermissionPolicy`，但调用位置和 typed decision 必须存在，
后续权限实现不应再次改动工具入口。retry policy 可以保留理想化通用能力，但它是围绕一次或
多次 attempt 的策略，不得用“retry”跳过上述任一阶段；每个 attempt 的 request、result、
verification 和 evidence 都要可区分。Provider retry 不是 tool retry：它必须复用完全冻结的
model request（包括相同 observation/image bytes 与 hashes），不得重新 capture 或重建消息。

工具执行返回成功只表示调用成功，不表示工具验证或任务终态成功。导航、抓取、放置等动作必须
满足其 verification policy；视觉可验证动作必须等待模型显式 fresh `observe`，结构化可验证动作可
使用 typed backend receipt/state。执行返回、模型 completion claim 与外部终态矛盾时，以外部终态
和领域证据门为准。

硬规则：任何 ToolSpec 字段要么被执行、要么删除，不能保留装饰性架构字段。

## 9. Skills

### 9.1 OpenHarness 做得更好的地方

OpenHarness skill loader 支持：

- bundled skills。
- 用户目录 `~/.openharness/skills`。
- `~/.agents/skills` 和 `~/.claude/skills` 兼容目录。
- 项目目录，从当前路径向上发现到 git root。
- 越接近 cwd 的 skill 覆盖越上层定义。
- plugin 提供的 skills。
- 标准 YAML frontmatter，包括 folded/literal block scalar。
- `user-invocable`、`disable-model-invocation`、command name、display name。
- 非法项目相对路径过滤。

其测试明确覆盖来源、优先级、兼容目录、恶意路径和 YAML 格式。

相关实现：

- `../OpenHarness/src/openharness/skills/_frontmatter.py`
- `../OpenHarness/src/openharness/skills/loader.py`
- `../OpenHarness/tests/test_skills/test_loader.py`

### 9.2 HomeMaster 当前会遇到的问题

HomeMaster 当前：

- 只硬编码加载 `fetch_object` 和 `check_object_state`。
- frontmatter parser 是逐行拆分，数组要求写成 JSON。
- 没有 user/project/plugin 来源。
- 没有覆盖优先级、来源诊断和重复名称报告。
- 新增 builtin 后还要修改 loader 中的名称 tuple。

具体例子：用户将 skill description 写成标准 YAML `>` 多行文本时，HomeMaster parser
无法得到与标准 YAML 一致的内容；第三方 skill 放进 `.agents/skills` 也不会被发现。

### 9.3 借鉴决策

借鉴意义：必须，优先级 P1。

可以直接或轻度适配移植：

- YAML frontmatter parser。
- project discovery。
- safe relative path validation。
- 来源和覆盖顺序测试。
- `user-invocable`、`disable-model-invocation` 和 command/display name 元数据。

OpenHarness 已验证的是对绝对路径和含 `..` 的相对路径字符串进行过滤，不等于完整的文件
系统 containment。HomeMaster 必须在 `resolve()` 后再次确认路径仍位于允许的 user/project/
plugin root 内，并明确 symlink policy；需要补目录外 symlink、嵌套 symlink 和 git-root
逃逸测试。因此可移植的是 YAML/parser/discovery 控制流，路径安全实现只能作为起点。

不能直接替换 HomeMaster `SkillSpec`。HomeMaster 应继续保留：

- `tool_names`
- `constraints`
- `success_criteria`
- `system_prompt_fragment`

建议来源顺序：

```text
builtin
  < user ~/.homemaster/skills
  < compatibility ~/.agents/skills, ~/.claude/skills
  < project root .homemaster/.agents/.claude skills
  < nearest project directory
  < explicitly enabled plugin
```

## 10. MCP

### 10.1 OpenHarness 做得更好的地方

OpenHarness 已实现：

- stdio MCP config 和连接。
- streamable HTTP MCP config 和连接。
- server connection status：pending/connected/failed/disabled。
- 单个 server 初始化失败时不让整个 runtime 启动失败。
- tool/resource 发现。
- MCP tool 到 Harness ToolCatalog 的动态适配。
- JSON Schema 到 Pydantic input model 的转换。
- disconnected server 的明确错误。
- close/reconnect 和失败栈清理。
- stdio 与 HTTP 的集成测试。

相关实现：

- `../OpenHarness/src/openharness/mcp/types.py`
- `../OpenHarness/src/openharness/mcp/client.py`
- `../OpenHarness/src/openharness/tools/mcp_tool.py`
- `../OpenHarness/tests/test_mcp`

注意：OpenHarness 定义了 WebSocket config model，但当前 client manager 实际只连接
stdio 和 HTTP。HomeMaster 不应把“存在类型”误写成“支持 WebSocket”。

### 10.2 HomeMaster 当前会遇到的问题

HomeMaster 当前没有 MCP。直接复制 MCP client 会遇到：

- Dispatcher 是同步的，无法自然 await tool call。
- MCP session 与 event loop 绑定，不能每次用临时 `asyncio.run()`。
- 没有 application 级 owner 负责 connect/reconnect/close。
- 没有动态 tool 注册和重名规则。
- 没有 MCP server secret/header 的独立凭据策略。

### 10.3 借鉴决策

借鉴意义：必须，优先级 P1，前置条件是异步 runtime 和 ApplicationRuntime。

可移植：

- MCP types。
- client manager 的 stdio/HTTP 生命周期。
- connection status 和错误语义。
- integration tests。

需重写：

- `McpToolAdapter` 必须产出 canonical `ToolDefinition/ToolExecutionResult`。
- schema validation 使用 HomeMaster 统一 validator。
- manager 应按 application 共享，并明确 per-server concurrency lock。

## 11. Gateway 与 Channel

### 11.1 OpenHarness 做得更好的地方

ohmo/OpenHarness 已经形成以下分层：

```text
Channel adapter
  -> InboundMessage
  -> MessageBus
  -> GatewayBridge
  -> SessionRuntimePool
  -> OutboundMessage
  -> Channel adapter
```

具体成熟点：

- channel 与 agent core 通过 DTO/bus 解耦。
- private/group/thread 使用不同 session key。
- shared chat 加 sender identity，避免多人共享同一段记忆。
- 同一 session 的新消息会请求取消旧任务，但当前实现只等待最多 3 秒，是 best-effort，
  不能视为严格的 cancel-and-join 保证。
- `/stop`、`/restart` 和 progress/final 分离。
- remote command 默认限制，敏感本地命令不能直接从 IM 触发。
- channel allowlist 默认拒绝。
- gateway state、PID、log、heartbeat 和 restart lifecycle。
- attachment 和非视觉模型 fallback。

相关实现：

- `../OpenHarness/src/openharness/channels/bus`
- `../OpenHarness/src/openharness/channels/impl/base.py`
- `../OpenHarness/ohmo/gateway/router.py`
- `../OpenHarness/ohmo/gateway/bridge.py`
- `../OpenHarness/ohmo/gateway/runtime.py`

### 11.2 HomeMaster 当前会遇到的问题

若直接把当前同步 `run_agent_turn()` 放进飞书/Slack handler：

- 一个用户等待机器人时可能阻塞整个 channel loop。
- 多个用户没有 session isolation，可能恢复或覆盖错误会话。
- 新消息无法可靠停止旧任务。
- CLI progress event 不能直接转成 channel update。
- 远程用户可能触发本应只允许本地执行的工具。
- 同一台机器人可能被多个 session 同时控制。

### 11.3 借鉴决策

借鉴意义：必须，优先级 P2。

可以直接或轻度移植：

- InboundMessage/OutboundMessage。
- BaseChannel allowlist。
- session router。
- session isolation 和 remote command security test scenarios。

MessageBus 不能作为成熟叶子模块原样复制。OpenHarness 当前只是两个无界
`asyncio.Queue()`；HomeMaster Gateway 至少要定义 bounded capacity、tenant/session quota、
progress coalescing/drop、final/error 不丢、shutdown drain 和 overload 行为。可复用 DTO 和
producer/consumer 结构，队列实现与压力测试必须适配。

必须重写：

- 不能复制 `OhmoSessionRuntimePool`，它依赖 OpenHarness RuntimeBundle、commands、
  hooks、memory backend 和 coding tools。
- GatewayBridge 要调用 HomeMaster `SessionManager`。
- 逻辑取消必须实现 cancel-and-join、turn lock 和 run generation fencing。旧 run 即使因
  SDK/线程不响应 cancellation 而晚返回，也不得再写 messages、TaskSnapshot、RuntimeEvent
  或 session snapshot；不能复制 OpenHarness “等 3 秒后继续启动新任务”的保证级别。
- 机器人要有独立的 device lease/lock 和 emergency stop，不应只依赖 chat session lock。
- 取消分两层：先取消逻辑 Agent task，再向正在执行的机器人动作发送物理 stop/compensation；
  无论成功、失败、timeout 或 cancellation 都必须释放 lease 并记录最后物理状态。

首版只接一个真实需要的 channel。不要同时迁移十种 channel，也不要优先复制基于
`ps` 扫描命令行的 daemon fallback；生产部署优先交给 systemd、supervisor 或容器平台。
CLI 只提供轻量 `gateway run/status/stop` 和配置验证入口，不在首版实现复杂 daemon 管理器。

## 12. 权限与治理

### 12.1 OpenHarness 做得更好的地方

OpenHarness 的 permission checker 支持：

- default/plan/full_auto 模式。
- allowed/denied tools。
- path rules。
- denied command patterns。
- read-only 自动允许。
- mutating tool 交互确认。
- SSH、cloud、Docker、Kubernetes 等敏感凭据路径硬拒绝。

此外，remote gateway command 还有 `remote_invocable` 和显式 admin opt-in。

### 12.2 HomeMaster 当前会遇到的问题

HomeMaster 没有独立 policy enforcement 层。`executor_mode` 只是工具描述，不是权限。

接入 gateway 和远程机器人后，典型风险包括：

- prompt injection 诱导调用危险机器人动作。
- 未授权聊天用户控制设备。
- 一个普通 observation 请求升级成 manipulate。
- 超出允许区域、速度、重量或时间窗口。
- 远程命令读取配置和凭据。
- 模型尝试在验证缺失时直接宣布完成。

### 12.3 借鉴决策

借鉴意义：必须，优先级 P0/P2，必须先于公开 gateway 和真实机器人。

OpenHarness 的文件/命令权限模型只能作为基础，HomeMaster 需要新增具身策略：

- requester/channel/session identity。
- robot/device identity。
- action risk level。
- physical area 和 target allowlist。
- device lease。
- operation confirmation。
- policy-required verification。
- emergency stop。
- immutable audit record。

通用与具身策略通过 typed request 连接，避免在 Gateway、Dispatcher 和机器人 adapter
中分别实现权限判断：

```text
PermissionRequest
  |- requester / channel / session
  |- tool / arguments / read_only
  |- robot / resource_key
  |- action_risk / area / target
  `- requested_state_effects

PermissionDecision
  |- allow / deny / confirm
  |- reason / matched_rule
  |- required_verification
  `- lease_requirements
```

## 13. Hooks 与 Plugins

### 13.1 OpenHarness 做得更好的地方

OpenHarness hooks 支持 command、HTTP、prompt 和 agent hook，并具有：

- lifecycle event。
- matcher。
- timeout。
- block-on-failure。
- aggregated result。

Plugin manifest 可以贡献：

- skills。
- tools。
- hooks。
- MCP servers。
- commands。
- agent definitions。

这使第三方能力不必修改默认 ToolCatalog 或主 runtime。

### 13.2 HomeMaster 当前会遇到的问题

目前新增能力通常需要：

- 修改 `build_home_tool_registry()`。
- 修改 skill builtin tuple。
- 修改 CLI wiring。
- 在 runner 中手动组装依赖。

随着 MCP、远程机器人和多个 benchmark 增长，这会让默认 home domain 与外部集成相互
污染，也难以按部署启停功能。

### 13.3 借鉴决策

借鉴意义：中，优先级 P3。

先建立稳定的 Tool/Skill/MCP registration protocol，再做 plugin manifest。不要在核心
契约未稳定时先做插件系统，否则插件只会固化错误接口。

机器人安全规则应进入 typed policy engine，不应依赖可任意执行 shell 的 hook。

## 14. Provider、配置与认证

### 14.1 OpenHarness 做得更好的地方

OpenHarness 将以下概念分开：

- provider profile。
- public settings。
- credential storage。
- active profile。
- auth flow/status。
- CLI override 和持久配置。

它还提供 setup、provider use/add/edit/remove、auth status/login/logout，并在输出配置时
对 MCP header、stdio env、vision key 等嵌套 secret 做 redaction。

### 14.2 HomeMaster 当前会遇到的问题

HomeMaster 的 Pydantic 配置模型本身较清楚，也支持 environment override；但当前 YAML
仍允许直接保存 `api_keys`。这对单机研究方便，对 gateway 部署存在问题：

- 配置和 secret 生命周期混在一起。
- profile 切换依赖编辑文件。
- 无法独立报告 credential source/status。
- gateway status 或 debug dump 容易意外输出嵌套 secret。
- 多实例部署时 credential rotation 困难。

### 14.3 借鉴决策

借鉴意义：选择性，优先级 P1。

已确认不移植 OpenHarness 的 setup、auth、provider 管理命令。HomeMaster 继续以 typed
YAML config 和 environment 为唯一配置入口：

- provider、model、endpoint 和 credential reference 在 config 中编辑。
- 环境变量可以覆盖 secret；研究环境可继续兼容 config 内 `api_keys`。
- doctor/dry-run 只报告字段来源、存在性和有效性，不输出 secret 内容。
- config show、日志、RuntimeEvent 和 gateway status 必须递归 redaction。
- 不为 profile add/edit/remove、login/logout 再建立一套持久状态。

配置解析顺序必须文档化并可 dry-run：

```text
built-in defaults < config file < environment < limited CLI runtime override
```

## 15. Session、持久化与 Context

### 15.1 OpenHarness 做得更好的地方

OpenHarness 有明确的 SessionBackend 协议，并在恢复历史时处理：

- 空 assistant message。
- 未完成 tool_use 尾部。
- tool result 与 tool use 不匹配。
- cwd/session key 作用域。
- gateway per-sender session isolation。

工具输出过大时会保存为 artifact，只把 preview 放入上下文，避免单个 MCP/tool result
迅速撑爆 context。这个“截断并外置”的思路值得借，但当前文件写入全局 data 目录，缺少
tenant/session/run 分区、写前 redaction、ACL、quota 和 retention，不能把存储实现当作成熟
安全策略复制。

### 15.2 HomeMaster 的优势与问题

HomeMaster 的 task state snapshot 和 domain-aware context compaction 比 OpenHarness 更适合
长程具身任务，应保留。当前实现已经强调 active task snapshot 比旧 summary 更权威。

仍需补齐：

- app/session runtime lifecycle。
- 中断在 tool call 中间时的恢复清洗契约。
- gateway session key。
- 统一的大工具输出外置策略。
- async compaction 和取消。

### 15.3 TaskSnapshot 的实际语义与上下文组装

代码中不存在 `askSnapshot` 类型；实际类型是 `TaskSnapshot`，由 session-bound
`TaskStateStore` 持有。它是一份模型通过 `task_planner` 和 `task_progress_check` 写入的可变计划，
字段含义如下：

- `subtask` 是模型给通用计划创建的 id/description/status/evidence item。它与 ALFWorld taskset 中
  表示 benchmark goal 的 `Subtask` 不是同一类型，文档和 correlation id 必须消歧。
- `constraints` 是模型提交的计划提示，不是 PermissionPolicy、safety policy 或 benchmark rule。
- `TaskSubtask.evidence` 是模型自由填写并 append 的字符串 note，不等同于 canonical
  `ToolExecutionResult.evidence_refs`，更不等同于 ALFWorld/Coworker 权威 domain ledger。
- `completion_summary` 和 `TaskStatus.COMPLETED` 是模型的完成 claim；当前工具可直接写 completed，
  不会自动验证全部 subtask、fresh observation 或环境终态，因此不能决定 formal success。

`ContextAssembler.prepare()` 在每次 model/tool iteration 的 provider 请求准备阶段从同一个 store 读取
最新状态。启用 `task_state_snapshot` provider 时，active 状态以 `# Task State Snapshot` synthetic
runtime prelude 重新组装，每个 subtask 投影最近两条 evidence；completed 状态投影 `type`、
`snapshot_id`、`status`、`goal`、`completion_summary` 和 `updated_at_iteration`，不再投影 subtasks/
constraints。该 synthetic message 不追加到 session。一次 provider retry chain 内不重新
prepare，必须复用冻结后的同一投影和 observation bytes。task tool 自己的即时 result 当前仍包含
full snapshot JSON，不能误称模型在所有路径只看到最近两条 evidence。

当前 `task_progress_check` schema 接受 active/paused/completed/failed/cancelled，但 executor 只处理
completed，其他值会被静默忽略；cancel/interrupt 实际把 active task 改为 paused，resume 再恢复
active。V1.9 必须定义并执行 typed transition table，非法/尚不支持的 transition 返回错误，不能静默
成功。`updated_at_iteration` 当前生产 RunContext 常固定为 0；新契约分别定义 session turn index、
model iteration 和 plan revision，不能把该字段或 `snapshot_id` 当并发 revision。

Session snapshot 保存完整 TaskStateStore；compaction 只处理 conversation，之后仍会从 store 重新组装
task prelude。V1.9 删除 `ToolDispatcher._run_context` 时，SessionManager 必须把同一个 store 实例显式
传给 task tools、ContextAssembler 和 persistence，跨 session 不得共享。`snapshot_id` 只表示创建/
替换 plan 的 generation，progress 会原地更新，不能把它当 immutable revision；V1.9 另增 revision/CAS
与 run generation fencing。TaskStatus、AgentRunStatus 和 benchmark classification 是三套独立状态。

进程重启后的 resume 或 active backend rebind 不恢复观察授权。任何环境/视觉 profile 都从
`NEEDS_OBSERVE` 开始；snapshot 中旧 observation id、hash、sequence 和 model-view binding 只可作为
审计历史，不能授权动作、清除 debt 或支持 completion。非环境 task/memory 状态仍按 revision/CAS
恢复，新的显式 `observe` 必须绑定当前 run/backend，并进入新的 successful frozen request。

TaskSnapshot completion gate 是 V1.9 的明确行为收紧：当前 profile 仍有 observation debt、必要 verifier
未通过，或当前工具/profile 明确声明 `terminal_rule=external_terminal_owner` 且该 owner 未判成功时，
`task_progress_check(task_status="completed")` 返回 typed `verification_pending`/`observation_required`，
不得写 completed。普通 Home profile 按自身声明的 verification/terminal policy 完成，不额外要求外部
terminal owner。该 gate 不把模型 note 升格为权威证据。

### 15.4 借鉴决策

借鉴意义：选择性，高优先级。

直接复制固定 OpenHarness commit 的 SessionBackend protocol、每个目标文件使用 `atomic_write_text` 的
save 以及 load/list/export 控制流和对应测试，再把 payload 适配为 HomeMaster 的 message、TaskSnapshot、
AgentState、revision/generation。上游先写 `latest.json` 再写 named snapshot，不是跨文件 transaction，
也没有 CAS；HomeMaster file backend 必须增加 expected-revision compare、单写者锁、先写 immutable
revision snapshot 再原子更新 commit/latest pointer 的顺序，以及 concurrent-writer/crash-window tests。
不复制其当前全局 artifact 路径策略，也不替换 HomeMaster context provider。目标 artifact 必须按
tenant/session/run 分区，写入前 redaction，
通过 ACL/permission 检查，设置 quota/TTL，并只在 event/model context 中传播 opaque handle，
不直接暴露宿主机绝对路径。

## 16. Events、日志与展示

### 16.1 两边的差异

HomeMaster 的 `RuntimeEvent`、JSONL sink、sanitizer 和 task/trace 语义是现有优势。
OpenHarness 更强的是将 async stream event 同时用于 TUI、print mode 和 gateway progress。

### 16.2 HomeMaster 当前会遇到的问题

现有 sink 是同步 append 接口，适合写 JSONL；gateway 还需要：

- 非阻塞 event queue。
- subscriber lifecycle。
- progress/final/error 分类。
- 慢 channel 的 backpressure/drop policy。
- 每个 event 的 session/run/turn correlation。

若 CLI、gateway、SSE 分别定义事件，会出现同一运行状态三套解释。

### 16.3 借鉴决策

借鉴意义：高，优先级 P0/P2。

保留 HomeMaster `RuntimeEvent` 作为统一 live/UI stream，但不把它提升为所有领域事实的唯一
存储。ALFWorld provider attempts、model-view/frame ledger、terminal classification，以及
Coworker artifact/formal-success ledger 继续作为各自权威记录：

```text
authoritative domain ledgers / evidence stores
  |- persist for evidence/scoring/replay
  `- project -> RuntimeEvent live/UI stream
       |- JsonlTraceSink
       |- CLI renderer
       |- PublicEventProjection -> AsyncQueueSink -> Gateway renderer
       `- AsyncQueueSink -> SSE/WebSocket observer
```

`PublicEventProjection` 必须执行 Coworker 已有的展示边界、敏感信息 redaction 和 artifact handle
转换；Gateway 不得直接消费私有 provider payload、原始截图路径或 benchmark 内部 ledger。
不要直接替换成 OpenHarness coding-oriented stream events；应做 adapter。queue/backpressure 只
影响 live delivery，不得改变已持久化的 evidence、provider attempt 或评分事实。

## 17. 测试与稳定性

### 17.1 OpenHarness 做得更好的地方

相关通用能力已有针对性测试：

- CLI setup/dry-run/output。
- skills 来源、优先级和 YAML。
- MCP stdio/HTTP 真实协议流。
- disconnected MCP 和 cleanup failure。
- gateway session isolation。
- remote sensitive command blocking。
- 新消息取消同 session 旧任务。
- attachment fallback。
- channel allowlist 和路径安全。

测试价值在于覆盖生命周期和安全边界，而不只是函数返回值。

### 17.2 HomeMaster 当前会遇到的问题

HomeMaster 有 runtime、CLI、context、session、ALFWorld 等测试，但当前环境本身暴露出：

- 声明依赖与 `.venv` 实际安装状态不一致。
- doctor 无法在缺依赖时运行。
- import boundary 测试多为字符串搜索，无法完全保证依赖方向。
- 尚无并发 session、MCP lifecycle、gateway security 和 device lock 测试。

2026-07-20 在 HomeMaster `5b150a9` 上重新检查：根 `.venv` 是 Python 3.11.15、pytest 9.0.3，
仓库已有根 `uv.lock` 和 Coworker app 的独立 lock，但环境未按 extras 同步。执行
`.venv/bin/pytest -q -m 'not live_api and not live_alfworld'` 在 collection 阶段产生 38 个错误，
直接原因是 `anthropic`、`fastapi` 等声明依赖未安装；marker 不能阻止测试模块先被 import。
因此 Phase -1 必须先用 lock 同步 root 与 Coworker app 环境，记录依赖清单，再生成行为基线。
不能把 collection failure 计成功，也不能靠跳过整个 ALFWorld/Coworker 目录掩盖 packaging/import
问题。OpenHarness 的判断仍以固定 commit 的源码和现有测试内容为依据，不以 README 宣称代替。

### 17.3 借鉴决策

迁移时必须复制适用的 OpenHarness 测试源码和 fixture，先保留原断言，再追加 HomeMaster 领域断言；
不得只重述“测试意图”后重写一套较弱测试。必须新增：

- 两个 session 在一个工具等待期间仍能并发推进。
- 同 session 新消息取消旧 turn，不污染历史。
- MCP server 失败不阻止无关工具启动。
- MCP manager 只创建一次并在 application close 时关闭。
- remote user 不能调用 local-only/admin tool。
- 同一机器人动作串行，不同机器人可并发。
- timeout/cancel 后释放 device lock。
- 中断在 tool call 后仍能恢复合法消息历史。
- JSON/stream-json 输出保持向后兼容。

## 18. OpenHarness 源码移植边界

用户已确认 OpenHarness 是组内项目且本项目非商用，直接复制不是项目授权阻塞。实施采用三种 port
mode；“参考设计后自行重写相同通用逻辑”不属于允许的复用方式：

- `V`（verbatim）：整文件和对应测试复制，只机械修改 package import/header。
- `A`（adapted source port）：复制明确函数/类；存在直接上游测试时同时复制并先跑原断言，再追加
  最小 HomeMaster delta；code review 必须能从 manifest 定位上游 symbol 和本地差异。若锁定 commit
  没有该 symbol 的直接测试，仍复制源码，但按 `upstream_test_gap` 规则新增 characterization tests。
- `H`（Home authority）：领域、安全或 lifecycle owner 按 HomeMaster 契约实现，但应调用兼容的
  V/A 叶子模块，不重复实现已经移植的通用逻辑。

### 18.1 Phase 0 必须实际复制

- `A`：复制 `src/openharness/tools/base.py` 的 `BaseTool`、Pydantic schema projection 和
  `ToolRegistry` 起点；随后拆成 immutable `ToolDefinition` 与 `RegisteredTool`，把静默 alias 覆盖
  改成 stable-id 注册和 ToolView conflict fail-fast。固定 commit 没有直接覆盖
  `BaseTool.to_api_schema()`/`ToolRegistry.list_tools()` 的 schema/registry tests，因此这项
  `copied_test_ids=[]`，登记 `upstream_test_gap`，并新增 HomeMaster schema/order/overwrite
  characterization tests；不能把 query tests 伪报成不存在的直接上游测试。
- `A`：复制 `src/openharness/engine/query.py::_execute_tool_call()`、sibling gather/exception isolation
  及 input/permission/single/parallel exception pairing tests；前置 ToolView/terminal/observation gate，
  后置 output validation/verifier/evidence，删除 coding artifact/hook metadata。不得复制整个
  `run_query`。
- `V/A`：复制 `services/session_backend.py` protocol、`session_storage.py` 的 per-file atomic save 与
  load/list/export 控制流，以及 `tests/test_services/test_session_storage.py` 的三个现存 node；payload
  改为 HomeMaster message、TaskState、AgentState、revision/generation，不保存 live resource。上游
  不提供跨 `latest.json`/named snapshot 的 transaction 或 CAS；Home delta 必须增加 expected revision、
  writer lock、immutable revision-first/commit-pointer-last 顺序和并发写/crash injection tests。
- `A`：复制 `engine/stream_events.py` DTO/union 和 consumer tests 到 public stream adapter，再映射
  HomeMaster RuntimeEvent/PublicEventProjection；不能替换领域 ledger。
- `A`：复制 `cli.py::_build_dry_run_preview()`、相关 formatter 分支和
  `tests/test_commands/test_cli.py` 的 dry-run/no-REPL/json/error cases；接 HomeMaster RunRequest，删除
  coding slash/autopilot/provider-admin 分支，未显式 `--probe` 时零外部 I/O。

### 18.2 Phase 1-3 必须实际复制

- `V`：整文件复制 `skills/_frontmatter.py` 和 frontmatter inline/folded/literal/quoted/fallback/
  malformed tests。`A`：复制 skills types/registry/loader 的 discovery、precedence 和 security cases，
  再增加 provenance、resolved-path/symlink containment、builtin safety 与 ToolView capability gate。
- `V/A`：复制 `mcp/types.py`、`mcp/client.py`、fake server 及 stdio/HTTP/error tests，适配 ownership、
  redaction、deadline、partial-init 和 close aggregation。复制 `McpToolAdapter` 仅作为起点；正式实现
  必须保留原始完整 JSON Schema，不能保留浅层 schema-to-Pydantic 转换。
- `A`：复制 PermissionChecker/modes 与 allow/deny tests，再叠加 typed principal、robot capability、
  device policy；不能从自由 metadata 推断权限。
- `A`：复制 Channel DTO/base、ohmo session router、bus、bridge 及 routing/progress/media/cancel tests，
  再增加 tenant identity、bounded priority/backpressure、PublicEventProjection、generation fencing。
  第一个真实 channel 确定后复制对应 adapter 与 security tests，不从零写也不一次迁十个 channel。
- `V/A`：复制 hooks events/types/schemas/loader/executor 和 manifest/priority/block/timeout tests；复制
  plugin schemas/types 与 discovery/hook/MCP/tool loader 的相关源码/tests。atomic hot reload、capability
  enforcement、import isolation 和 late-result fencing 保持 HomeMaster 权威。

### 18.3 HomeMaster 权威实现

- canonical result/context 的领域字段、strict ToolCatalog/ToolView 和 pipeline 的 terminal/resource/
  verifier/evidence stages。
- ObservationService、frozen provider request/media binding、provider-attempt/model-view commit 顺序。
- AgentRuntime、ApplicationRuntime、ResourceScope、SessionManager/generation fencing 与 application-owned
  EventBus。
- Home/ALFWorld/Coworker profiles、backend adapter、benchmark lifecycle/scorer/ledger/artifact verifier。
- PublicEventProjection/Coworker trust boundary、保真 MCP schema adapter、device auth/lease/e-stop、
  bounded priority bus 和 atomic extension generation。

`H` 不表示忽略 OpenHarness：若其叶子函数或测试兼容，仍先按 V/A 复制并从 H owner 调用。

### 18.4 不复制的整体或不安全语义

- `src/openharness/cli.py` 整体。
- OpenHarness `QueryEngine/run_query` 整体及其 coding work-log/tool-name 特例。
- OpenHarness/ohmo 当前每个 session 一套 RuntimeBundle 的资源粒度。
- ohmo Gateway 当前缺少 RuntimePool 全量 `close_all()` 的退出链。
- OpenHarness `build_runtime/start_runtime/close_runtime` 当前缺少严格初始化回滚、逐项清理和
  borrowed API client 所有权保护。
- OpenHarness ToolRegistry 的静默重名覆盖语义。
- OpenHarness 全局 tool artifact 文件存储实现。
- coding tools、autopilot、swarm、cron。
- ohmo persona、memory、group command 逻辑。
- 所有 channel 一次性迁移。
- gateway 中依赖 `ps` 命令扫描进程的 fallback。
- 尚未真正实现连接的 WebSocket MCP 表面类型。

### 18.5 来源与维护要求

- 每个移植 CL 生成并校验 `upstream-port-manifest.json`，至少记录 port mode、source repo/commit/path/
  symbol、source SHA-256、destination、copied test ids、机械 import delta、HomeMaster delta 和后续同步/
  删除策略。`copied_test_ids` 仅允许真实存在于锁定 commit；为空时必须同时记录 `upstream_test_gap`、
  可复核的 search evidence 和新增 characterization test ids。
- OpenHarness channel 已记录 nanobot 二级来源；复制时原样保留其 commit/provenance chain 并写入
  notices，不能抹掉来源。该 provenance 要求不构成重新实现兼容通用逻辑的理由。
- 大幅改写后仍保留 provenance，避免未来误判为可直接 upstream sync。
- 以行为测试而不是逐行 diff 作为后续升级依据。
- 移植模块时必须同时移植其成功、失败、取消、清理和安全测试；上游原断言先通过，再运行
  HomeMaster delta tests。

## 19. HomeMaster 目标架构

```text
CLI / Interactive / Gateway / Benchmark Runner / API
                 |
                 v
        ApplicationRuntime
        |- run(RunRequest) -> RunResult
        |- compact(session_id) -> CompactResult
        |- AgentRuntime
        |- ProviderFactory / ConfigResolver
        |- MCPManager
        |- RobotConnectionPool
        |- PermissionPolicy（首版 AllowAll）
        |- ToolCatalog（all definitions/variants）
        |- ToolExecutionPipeline
        |- ObservationService
        |- SessionManager
        `- EventBus
                 |
                 v
          SessionRuntime
          |- AgentSession
          |- AgentState
          |- TaskStateStore
          |- rebindable environment/profile ref
          |- turn_lock
          `- active_task/cancellation
                 |
                 v
             RunScope
          |- borrowed active EnvironmentBackend
          |- immutable ToolView(enabled_tool_ids)
          |- RunPolicy / TerminalPolicy
          `- provider request/attempt scope
                 |
                 v
       ToolExecutionContext（每次调用）
       |- session/run/turn identity
       |- task/active environment backend
       |- latest committed observation
       |- requester/permission subject
       |- event/evidence scope
       `- cancellation token
                 |
                 v
       共享 ToolExecutionPipeline
       |- ToolView / terminal gate
       |- input/argument validation
       |- permission / confirmation
       |- cancellation / deadline
       |- pre-observation gate
       |- concurrency / resource lease
       |- execute（retry policy 可编排多个可审计 attempt）
       |- result-schema validation
       |- policy-applicable verifier
       |- post-action observation debt
       |- authoritative evidence/domain ledger
       `- Public RuntimeEvent projection
```

依赖方向要求：

- generic runtime 不导入 home/robot domain。
- gateway 不直接构造 domain tools，只向 SessionManager 请求 session。
- 所有 model-facing tool definitions 都在 Harness ToolCatalog；benchmark 只提供 backend、case 和
  scorer，不能定义私有 ToolSpec/registry。
- domain tool variant 通过 typed dependencies 获取环境和 robot client；每个 run 只从 catalog
  冻结所需 ToolView，未启用工具对模型不可见且执行层拒绝。
- MCP/channel/robot 是 adapter，不进入 agent state model。
- RuntimeEvent 是 CLI/gateway/SSE 的统一 live/UI stream，不取代 TaskSnapshot、provider attempt、
  ALFWorld frame/model-view ledger、Coworker artifact ledger 或外部环境终态。
- Gateway 只消费经过 redaction 的 PublicEvent；TaskSnapshot 和外部 evaluator 仍是具身任务完成
  判断的重要依据。
- `AgentRuntime` 是通用 model/tool loop 的稳定名称，不再使用
  `GenericAgentRuntime`；generic 是依赖方向，不是公开类名。
- CLI 只调用 ApplicationRuntime/SessionManager，不直接组装 provider、tools 和 domain deps。
- Benchmark 与 CLI 调用相同的 `ApplicationRuntime.run()` 和 SessionManager；runner 管理
  case、environment start/reset/finalize/close、浏览器/VNC/录像、scorer 和 artifact verification，
  并把 active backend 作为 borrowed run dependency 传入。
- ToolCatalog、stateless executor wrapper 和 pipeline 对所有 session 共享；ToolView、active
  backend 和 session-bound state 只能通过 run/call context 传入，不能写进共享对象字段。
- 模型需要图像时主动调用当前 ToolView 的 observation tool；ObservationService 绑定 observation 与 provider
  request，retry 复用冻结请求，不重新采集或替换图片。
- `run()` 是统一的用户 turn 入口，但不是把所有控制操作都塞进一种 request；现有 manual
  compaction 迁为同一 ApplicationRuntime owner 下的 `compact(session_id)`，cancel/status 也
  使用独立 typed method。它们复用 SessionManager 和事件契约，但不伪装成用户消息。

## 20. 分阶段迁移

### Phase -1：冻结当前行为基线

范围：

- 固定 HomeMaster `5b150a9671bb087b32ed57971a39fa472e8ff1e1` 与 OpenHarness
  `9b2efd795c6aa09f88b0c257d269a9e518da6ae7`。
- 跑并保存当前 CLI、Interactive、Home、ALFWorld V1.8 与 Coworker characterization results。
- 将 ALFWorld runtime identity、reset/scan/restore transaction、OracleActionGateway、provider
  attempt/request hash、committed model-view/frame authority、closed classification、setup/control/
  model action accounting、cleanup/quarantine 和 ToolDispatchObserver 行为写成不可回归测试。
- 将 Coworker child run、固定有序十一项工具、deadline/budget、FastAPI/browser/VNC/recording、
  public event projection、artifact bundle 和 formal-success/scorer 行为写成不可回归测试。

验收：测试结果、关键 ledger/artifact schema 和失败分类都绑定上述 commit；基线未冻结前不得
删除旧入口或旧 registry。

### M0：ALFWorld live runtime qualification

在架构改动前单独验证 live 基础设施，不把当前低成功率或 Provider attempt 为零的问题推迟到入口
迁移后排查。M0 固定并记录 ALFWorld/ai2thor/Unity/dataset root、`visual_eval` config、provider/model、
Python/import origin、GPU/display 和 reset fingerprint；先完成一次 fresh canary，证明真实 provider
attempt 大于零、`AlfredThorEnv` 可 start/reset/scan/restore、图片可由显式 observation adapter 捕获且
formal scorer 可运行。M0 只证明 runtime 可测，不替代最终 4/4 产品质量 gate，也不允许挑成功 trial。
M0 的权威结果在 HPC2 产生；若用 `hkust4` 交叉复现，所有 ALFWorld 子进程都必须由
`conda run -n hm_alfworld ...` 启动，并额外保存 Conda environment name、`sys.executable`、完整
Python version、`conda list --explicit` 的 SHA-256，以及 `homemaster`、`alfworld`、`ai2thor` 的
resolved import origin。任一 origin 不属于候选 worktree 或 `hm_alfworld` 环境时 fail-fast。

四条 release manifest 的 source inventory 固定为提交的
`config/alfworld_v18_regression_trials.json` 十条 manifest，并复用现有
`benchmarking.alfworld.trial_selection` 的 portable path/hash/scene/goal 校验。builder 按版本化算法对
canonical case identity 做 SHA-256 rank（固定 algorithm version 和 seed，digest 升序取前四条），在
查看任何 V1.9 live 结果前写入四条；validator 校验 source hash、algorithm/seed/rank、四条唯一性、
dataset existence 和 task identity。任何 source 内容或算法变化使旧 live evidence 失效。

算法常量固定为 `algorithm_version="sha256-rank-v1"`、`seed="homemaster-v1.9-release"`；每条
`rank_digest = SHA256(seed + NUL + canonical_json(trial_id, trial_sha256, goal_fingerprint,
expected_logical_scene))`，按 `(rank_digest, trial_id)` 升序取前四。输出 schema
`alfworld-v19-release-trials-v1` 保存 source path/SHA-256/schema version、algorithm/seed 和每条原 identity/
source rank/rank digest。builder 不接受 success/classification/score 字段，validator 对额外 gate-result
字段 fail-fast。

### Phase 0A：Canonical contracts、Catalog 与 Observation

范围：

- 定义唯一 immutable `ToolDefinition`、canonical `ToolExecutionResult`、`ToolCatalog`、
  immutable `ToolView`、`ToolExecutionContext` 和 alias/provenance/version 冲突规则。
- 实现完整 argument/result-schema validation、`AllowAllPermissionPolicy` seam、timeout/cancel、
  resource lock、execute、verification 与 event/evidence pipeline；首版保持同步 executor 兼容。
- 把 `home.observe.v1`、`alfworld.observe.v1`、`coworker.observe.v1` 定义为 Harness tools，模型别名
  全部为 `observe`，建立 ObservationService 的 backend/sequence/media/content hash、条件化 pixel
  hash、evidence ref 与 backend/provider binding。
- 在保持现有入口可运行的 adapter 后面落 contracts，不迁移 benchmark 生命周期。

验收：

- disabled tool 不进入 provider schema，直接伪造调用也返回 `tool_disabled`。
- 同一 ToolView 内 alias 冲突 fail-fast；不同环境同 alias variant 可以同时存在 catalog。
- ALFWorld 导航只暴露 `robot_go_to`，guard test 继续禁止 `robot_navigate`；Home 旧别名有明确
  删除点。
- 模型只有显式调用 `observe` 才获得环境图片或 DOM；三个 ToolView 均恰好一个该别名，Coworker
  仍是固定十一项且第五项改为 `observe`，不增加第十二项；旧别名不出现在 model surface。
- provider retry 的 request hash 和 observation bytes/content hash 不变；raster pixel hash 不变，
  structured observation 无 pixel hash；capture 次数不增加。

### Phase 0B：Application、Session 与 Run scope

范围：

- 新建 ApplicationRuntime、application-owned EventBus、SessionManager、SessionRuntime 和唯一
  `ApplicationRuntime.run(RunRequest)` 消息入口。
- 定义 typed `RunPolicy`、`TerminalPolicy`、borrowed `EnvironmentBackend` 和 run resource scope。
- ToolCatalog/ObservationService/pipeline/EventBus 属于 application；ToolView/active backend 属于 run；
  messages/TaskStateStore/turn lock/compaction request 属于 session。TaskStateStore 必须以同一实例显式
  传给 task tools、ContextAssembler 和 persistence，不能再从 Dispatcher private context 读取。
- 增加 cleanup stack、owned/borrowed、turn lock、generation 和 manual compact/cancel/status。

验收：普通 episode/Coworker run 默认新 session，只有显式连续 ALFWorld taskset 共享 session；
两个并发 session 不串 TaskState、backend、observation、event 或 cancellation；run 结束不关闭
borrowed benchmark backend，application 部分初始化/关闭失败仍正确回滚和继续清理。

### Phase 0C：逐入口和逐环境迁移

按 Home/CLI、Interactive、ALFWorld、Coworker 的顺序逐个切换；每迁移一个入口就先通过 Phase
-1 对照测试，再删除该入口的 Provider/Runtime/model-facing registry 装配。benchmark controller
仍负责环境 start/reset/finalize/close、FastAPI/browser/VNC/recording、评分和 artifact verification，
只把 active backend、enabled ids 与 terminal policy 传给同一 run 入口。

验收除 Phase -1 等价外还包括：ALFWorld terminal 后 backend model action 不增长，pinned identity、
request/frame authority、classification 和 action accounting 保真；Coworker 的 ToolView 恰为固定
有序十一项、child run/lifecycle/public projection/formal artifacts 保真；两个 benchmark 目录均
不再定义 model-facing ToolSpec/registry。

### Phase 0D：异步迁移（四个独立 gate）

- 0D1 provider/event：Provider 与 RuntimeEvent stream 原生 async，冻结 request/attempt ledger 不变。
- 0D2 agent/pipeline：Phase 0A 的 pipeline core 已由 copied async query flow 实现，legacy sync caller
  暂经唯一 bridge；本 gate 把 AgentRuntime/caller 改为 async、删除 bridge，并完善 lock/cancel/deadline。
  同步 backend 用明确 outcome semantics 的 adapter，不创建第二条 pipeline。
- 0D3 Coworker Playwright：明确选择 thread-owned sync adapter 或 Playwright async API，禁止跨线程
  使用现有 page/browser；单独验证 FastAPI/browser/VNC/recording lifecycle。
- 0D4 stress/fencing：并发 session、generation late result、queue backpressure、lease/task leak 和
  cancellation stress。

每个 gate 独立合并和回滚。异步化不得改变 frozen request、provider attempt、committed model view、
工具 evidence 或 scorer input；pytest async plugin/mode 已在 Phase 0A 首个 copied async test 前锁定。
最终验收还包括
manual `compact -> process restart -> resume` 持久化，以及 A session 请求 compact 不影响 B session。

### Phase 1：Skills、配置、MCP

范围按安全 owner 先于公开 ingress 的顺序实施：

- 多来源 skill loader 和标准 YAML。
- provider/auth config schema、优先级、来源诊断和递归 redaction；不增加管理 CLI。
- MCP stdio/HTTP。
- 动态 ToolSpec adapter。
- MCP list/add/remove/doctor。
- tool output artifact policy。

验收：

- MCP 失败不影响 builtin tools。
- MCP manager 生命周期属于 ApplicationRuntime。
- skill 覆盖顺序可 dry-run 查看。
- MCP headers/env 不出现在日志和 config show。
- tool artifact 按 tenant/session/run 隔离，经过 redaction/ACL，受 quota/TTL 管理，日志和
  model context 不出现宿主机绝对路径。
- project/plugin skill 的 resolved path 和 symlink 不得逃出允许 root。

### Phase 2：Gateway 与远程机器人

范围：

- remote/local command policy。
- RobotConnectionPool、device lease、timeout、emergency stop。
- 一个预先选定的优先 channel。
- MessageBus、session router、GatewayBridge；auth/device gate 未通过前 external ingress 默认关闭。
- progress/final/attachment rendering。

验收：

- private/group/thread/sender 会话不串线。
- 新消息可取消同 session 旧 turn。
- 旧 run 在取消或被新 generation 取代后不能写 messages/event/snapshot。
- MessageBus 满载时行为确定：progress 可合并，final/error 不丢，shutdown 可 drain。
- 未授权用户不能控制机器人。
- 同一机器人动作不会并发冲突。
- gateway restart 后可恢复合法 session snapshot。

### Phase 3：Hooks、Plugins 与更多 Channel

前置条件：Tool、Skill、MCP、Permission 和 Session 契约稳定。

范围：

- plugin manifest。
- lifecycle hooks。
- hot reload policy。
- 更多 channel adapter。
- 插件兼容和版本约束。

Release scope 固定如下：V1.9 core 必发到 Phase 0D4（implementation CL-16d），随后在同一最终 SHA
完成 ALFWorld 4/4 与 Coworker 1/1。Phase 1-3 是本规格约束下的后续路线图，不阻塞 V1.9 core，且
不能在 core live gate 前混入 release branch。未来发布任一后续 Phase 时必须先锁定具体 channel/
extension 范围，并在其新最终 SHA 重新执行完整 4+1 gate。

### 20.1 Phase 动态状态机与独立 review gate

每个可合并阶段都执行以下状态机：Phase -1、M0、0A、0B、0C 的每个入口、0D1～0D4；后续路线图
则对 CL-17～CL-21 分别执行。状态和动态子目标写入 machine-readable phase record：

```text
PLANNED
  -> CONTEXT_LOADED
  -> SUBGOALS_FROZEN
  -> IMPLEMENTING
  -> VERIFYING
  -> SUBAGENT_REVIEW
  -> REMEDIATING (发现问题时循环到 VERIFYING/SUBAGENT_REVIEW)
  -> GATE_PASSED
```

进入 `IMPLEMENTING` 前，主 agent 必须重新完整阅读 comparison spec/implementation plan 对应板块、
当前项目 owner 源码/测试、适用 AGENTS 指令和 OpenHarness source-port manifest，而不是依赖旧摘要。
根据当前 diff、依赖和 exit criteria 动态生成该 phase 的子目标、允许差异、测试命令、回滚点；依赖/
源码发生变化时退回 `CONTEXT_LOADED` 重新划分。

Phase -1 是唯一 bootstrap：其 `PLANNED` 步骤先创建只含 schema version、锁定 OpenHarness repo/commit
和空 `ports` 的 bootstrap manifest，然后 `CONTEXT_LOADED` 必须实际读取它。Phase -1/CL-01 随后提交
正式 JSON Schema/validator 并验证该空清单；从 M0 起 manifest 缺失、commit 不符或未通过 validator
一律 fail-fast，不允许继续使用 absent 例外。

`VERIFYING` 通过后必须新开至少一个独立 subagent，结合当前项目细节、该 phase 实际实现 diff、复制的
OpenHarness 源码/tests、运行结果和领域 owner 做 code review。review 结论、file/line findings、处置和
复验结果进入 phase record；未处理的 correctness/safety/contract finding 使状态回到
`REMEDIATING`，不得标 `GATE_PASSED`。subagent review 不能替代 required tests，也不能只审计划而不看
实际代码。没有 subagent review evidence 的 phase 一律未完成。

## 21. 最终决策原则

1. OpenHarness 已验证且契约兼容的通用能力必须复制实际源码与测试，不从文字重新实现。
2. 低耦合叶子模块按 V 移植，中间层按 A 移植，runtime/domain 核心按 H 保持 HomeMaster 权威；
   不整体复制 coding-agent 领域耦合。
3. HomeMaster 的 task/evidence/evaluator 语义，以及最新 ALFWorld V1.8/Coworker 契约必须保留。
4. 真实机器人接入前，异步生命周期、权限、取消和设备锁必须先完成。
5. MCP 和 robot connection 必须是 application 级长生命周期资源。
6. 同一 session 默认单 turn；同一 robot 默认单动作；并发必须显式声明安全。
7. ToolDefinition 中无法执行的字段不进入稳定 API；验证是 pipeline 的必经阶段，具体 verifier
   由工具 variant 决定。
8. 每个迁移模块必须同时迁入其生命周期、安全和故障测试。
9. CLI、gateway 和 SSE 消费同一套 RuntimeEvent live stream；RuntimeEvent 不替代权威领域
   ledger，外部入口只能读取脱敏 PublicEventProjection。
10. 先完成单渠道、单 MCP server、单远程机器人闭环，再扩展数量。
11. CLI 采用默认交互和 `--print` 入口，但不增加 setup/auth/provider 管理命令。
12. 所有 V/A 移植代码和测试记录 OpenHarness source commit/path/symbol/hash、copied test ids 和本地
    delta；组内授权不设阻塞，已有二级 provenance 不得抹除。
13. CLI、Interactive、Gateway 和 Benchmark 只能调用同一个
    `ApplicationRuntime.run(RunRequest)`；禁止入口各自装配 runtime。
14. 所有 model-facing tool definitions 位于 application ToolCatalog；每个 run 冻结自己的
    ToolView 和 borrowed backend，session 隔离状态；禁止共享对象保存可被并发 session 覆盖的
    `_run_context`。
15. 共享资源必须标记 owner；borrowed resource 不得由借用方关闭，初始化和 shutdown 必须
    在部分失败下完成回滚/继续清理。
16. Tool 注册重名默认失败并保留 provenance；任何显式 override 都必须配置授权。
17. Gateway 取消必须阻止 stale generation 写回，而不只调用 `task.cancel()`。
18. 模型可见图片/DOM/state 只由模型主动调用当前 ToolView 的 `observe` 产生；provider retry重用
    冻结请求，不触发新观察。
19. Benchmark 拥有环境、录像和评分生命周期，Harness 拥有消息、session、tools、observation 和
    execution pipeline；双方不得越过该边界。

## 22. 建议首先落地的变更单序列

首批采用独立可 review/rollback 的 stacked series：Phase -1 对应 CL-01，M0 是其后的独立 evidence
gate，Phase 0A 对应 CL-02～CL-07。它们不 squash 成一张 CL；该 series 不切入口、不异步化 Provider、
不删除现有 benchmark 装配，也不同时引入 MCP/gateway：

1. 固定两仓 commit，补齐最新 Home/CLI、ALFWorld V1.8、Coworker characterization tests 和
   可比较的 ledger/artifact fixtures。
2. 建立 `upstream-port-manifest.json`，按 §18 的 V/A 规则复制 OpenHarness BaseTool、query tool-call
   flow 及其原测试；再定义 canonical immutable `ToolDefinition`、`RegisteredTool`、typed
   `ToolExecutionResult`、ToolCatalog/ToolView、ToolExecutionContext 和冲突规则。
3. 在同步兼容 adapter 后实现 validation、AllowAll permission、timeout/cancel、resource lock、
   execute、tool-specific verification 和 events/evidence pipeline。
4. 新增三个 internal observation variants 与 ObservationService，模型别名一律 `observe`；冻结
   observation/provider request 的 id、sequence、media、content hash、条件化 pixel hash 和 evidence
   binding，并删除所有 implicit model media conversion。
5. 让当前各入口通过 adapter 生成 ToolView；验证 disabled tools 对模型不可见且执行层拒绝，
   但暂不移动 ALFWorld/Coworker environment lifecycle。
6. 保持 `robot_go_to` 为正式导航名，ALFWorld guard 继续禁止 `robot_navigate`；记录 Home wrapper
   删除条件，不在兼容路径恢复旧名字。

第二张变更单再做 Phase 0B 的 Application/Session/Run scope，之后按 Phase 0C 一次迁移一个
入口，最后 Phase 0D 异步化。这样每次都能与 `5b150a9` 的证据、计数和评分行为对照。

## 23. 当前基线的兼容性锚点

实现和评审不能只按本规格中的类型名推断旧逻辑，至少要对照以下 `5b150a9` 文件：

- `src/homemaster/tools/dispatcher.py`：当前 `ToolDispatchObserver` seam。
- `src/homemaster/providers/attempts.py` 与 `src/homemaster/agent/generic_runtime.py`：provider
  attempt、commit state 和 retry request 边界。
- `src/homemaster/benchmarking/alfworld/runtime_contract.py`、`reset_transaction.py`、`gateway.py`、
  `model_view.py`、`tracing.py` 和 `runner.py`：ALFWorld V1.8 权威 identity/action/frame/outcome 链。
- `src/homemaster/benchmarking/alfworld/registry.py` 与
  `tests/homemaster/benchmarking/test_alfworld_v18_guards.py`：正式 `robot_go_to` 契约及旧名禁用。
- `src/homemaster/benchmarking/coworker_demo/registry.py`、`turn.py`、`presentation.py` 和
  `types.py`：固定十一项 ToolView、child run/lifecycle、公开投影和 formal artifact 契约。
- `../OpenHarness/src/openharness/channels/UPSTREAM`：channel 代码的 nanobot 二级来源；与
  OpenHarness provenance 一起写入 port manifest/notices；组内授权允许直接移植，不以此为阻塞。

若这些文件的行为或 schema 在实施前变化，必须先更新 commit、Phase -1 fixtures 和本节，再
决定是复用通用实现还是为 HomeMaster 新逻辑增加 variant/adapter。

## 24. 测试计划

本节是 V1.9 的规范性测试计划。第 20 节中的每个 Phase 只有通过本节对应 gate 才能进入下一
阶段。现有 ALFWorld/Coworker 测试和领域 ledger 是权威 oracle；不得为了通用层迁移另建一套
平行 scorer，也不得只断言 RuntimeEvent 文本来替代外部环境终态。

### 24.1 测试原则

1. 先修复测试环境，再冻结行为。collection failure、缺依赖和错误 interpreter 都不是可接受
   baseline。
2. 新 contracts 先写 RED tests，再接现有实现 adapter；旧入口在对照 gate 通过前保持可运行。
3. 共享的是 catalog、pipeline 和入口测试，不是把 Home、ALFWorld、Coworker 的 manifest 或
   schema 强制做成同一份 golden。
4. 对只读计算可以做 old/new differential；对机器人、THOR、browser、terminal 等 mutating
   action 禁止在同一环境“双写对比”。这类行为使用录制输入重放、独立 fresh run 和权威 ledger
   对比，避免测试本身制造第二次动作。
5. 所有失败、取消、timeout、初始化中断和 cleanup 都必须有断言；只覆盖 happy path 不算完成。
6. 时间、UUID、文件路径和网络通过 injectable clock/id factory/temp root/fake transport 控制；
   单元测试不得依赖 `sleep()`、真实 API、真实机器人或公网。
7. Golden 只保存稳定 contract 字段和 hash。时间戳、临时路径、随机 id 先 canonicalize；任何
   golden 更新都必须在 review 中说明行为变化，不能与实现一起静默刷新。

### 24.2 Phase -1 环境与基线 gate

先执行并保存：

```bash
cd /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster
git rev-parse HEAD
git status --short
uv sync --frozen --all-extras
uv sync --directory apps/case02_openenv --frozen
uv run python -m homemaster.cli --help
uv run pytest -q -m "not live_api and not live_alfworld and not live_mcp and not live_coworker"
uv run ruff check src tests
```

实施 V1.9 时在 `pyproject.toml` 注册尚不存在的 `live_mcp`、`live_coworker` 和 `stress` markers。
marker 只隔离外部依赖和压力测试，不用于隐藏普通 import/collection failure。当前 2026-07-20
试跑因根 `.venv` 未同步，在 collection 阶段报 38 个错误；Phase -1 的第一个交付就是让上述
non-live 命令在固定 lock 上完整 collection 并通过。

基线产物写入 `plan/V1.9/baseline/`：

```text
source-commits.json                 两仓完整 commit 与 Python/uv/platform identity
dependency-lock-hashes.json         根和 Coworker app lock hash
test-inventory.txt                  pytest --collect-only 的稳定 node id 列表
pytest-nonlive.txt                  完整摘要和退出码
tool-surfaces.json                  Home/ALFWorld/Coworker 当前有序工具名和 schema hash
provider-attempt-contract.json      request/attempt/commit-state 稳定字段
alfworld-contract-hashes.json       runtime/reset/model-view/outcome schema 与 fixture hash
coworker-contract-hashes.json       11-tool/presentation/artifact/formal-success fixture hash
```

这些文件记录 contract，不复制包含 secret、绝对主机路径或大体积运行 artifact 的内容。真实运行
产物保存在 CI/artifact store 或现有 `var/` run root，通过 manifest/hash 关联。

#### 24.2.1 HPC2、GitHub 与 hkust4 执行边界

| 环境 | 允许职责 | 禁止事项 |
|---|---|---|
| HPC2 | 唯一代码编辑、生成 lock/manifest、non-live gate、ALFWorld 真实运行 | 用未提交字节作为正式候选；把 key 写入 Git |
| GitHub `origin` | 传输已通过 HPC2 gate 的 immutable commit | 传输 ignored provider config、secret 或 `var/` artifact |
| hkust4 | fetch/checkout 同一 commit；同步依赖；运行 Coworker preflight、真实 LLM 和独立 verifier；必要时在 `hm_alfworld` 中做 ALFWorld 预检/复现 | 在正式 worktree 手改代码；用 rsync 覆盖 tracked 文件；复用旧 run 冒充新验收；把 hkust4 ALFWorld 复现冒充 HPC2 正式 4/4 gate |

release candidate 在 HPC2 完成 non-live gate 后创建 commit 并推送。`hkust4` 使用独立 clean
worktree fetch 并 detached checkout 该完整 SHA；两端都记录 `git rev-parse HEAD`、tracked
`git status --porcelain`、根/Coworker lock hash、Python/uv/platform identity。两端 SHA 不一致、
tracked worktree 非 clean 或 import origin 不在目标 worktree 时停止，不能开始 live run。

`hkust4` 上所有 ALFWorld 相关进程，包括 environment probe、pytest、manifest validator、runner 和
verifier，都必须以 `conda run -n hm_alfworld ...` 启动，不能复用 Coworker `.venv` 或裸 shell
Python。identity capture 同样在该 Conda 环境内运行并保存 `environment_name="hm_alfworld"`、
Python executable/version、`conda list --explicit` 原始输出的 SHA-256，以及 HomeMaster、ALFWorld、
ai2thor 的 resolved import origins；缺字段、环境名不符或 import origin 漂移均 fail-fast。此规则仅
约束 hkust4 的 ALFWorld 预检/复现；若不完整结果仍到达跨机报告合并器则分类为 `UNVERIFIED`。
正式 ALFWorld release gate 的权威运行位置仍是 HPC2。

真实 provider config/credential 只保留在各运行机的 gitignored mode-0600 文件中，不通过 GitHub
同步，也不能打印到日志。本文所谓“最新锁定 benchmark 内容”不是运行时从仓库外动态挑选最新任务，
而是最终候选 SHA 中提交的固定 inventory 加运行前验证的外部数据 bytes：ALFWorld 以该 SHA 的 V1.8
十条 source manifest 和固定算法生成的四条 release manifest 为准，并校验 root/config/dataset bytes；
Coworker 以该 SHA 的唯一 test-set item 为准。ALFWorld root/config/四条 trial manifest 的 commit/hash，以及 Coworker
`data/coworker_demo/case_02/dataset_manifest.json` 和其声明文件 hash 都进入
`var/v19-release/<candidate>/live-release-identity.json`。冻结后不得根据模型结果替换数据；
benchmark 内容变化则产生新候选 identity，并从 preflight 开始重跑。

### 24.3 测试层级与运行频率

| 层级 | 内容 | 外部依赖 | Gate |
|---|---|---|---|
| L0 静态/契约 | import boundary、schema、provenance、legacy name、compile、Ruff | 无 | 每次提交 |
| L1 单元 | Catalog/View、validation、policy、pipeline、observation、resource scope | 无 | 每次提交 |
| L2 组件 | ApplicationRuntime、SessionManager、fake provider/backend/MCP/channel | 仅本地 fake | 每次 PR |
| L3 入口/领域回归 | CLI/Interactive/Benchmark parity、全部现有 ALF/Coworker non-live tests | 本地 fixture/app | 每次 PR |
| L4 并发/故障/安全 | cancellation、generation fencing、bounded queue、path/redaction、resource leak | 本地 fake | 每次 PR；stress nightly |
| L5 live | 真实 provider、ALFWorld runtime、MCP subprocess/HTTP、Coworker 浏览器/VNC/录像 | 外部环境 | nightly/release |

PR 必须跑 L0-L4 的非 live 部分。L5 不可用时结果是明确的 `UNVERIFIED`，不能伪装成 pass。
一般外部扩展 gate 可以由发布负责人书面接受未验证风险，但 V1.9 最终验收不得豁免
ALFWorld 四条和 Coworker 一条真实 LLM gate；任一未运行或未通过都只能标为未完成。

### 24.4 共用 fixtures 与 test doubles

新增可复用 fixtures，避免每个入口自行 mock 私有对象：

| Fixture | 必须记录/模拟的行为 |
|---|---|
| `RecordingProviderTransport` | 完整 frozen request、图片 bytes/hash、attempt 顺序、stream cancel、可注入 retryable failure |
| `FakeEnvironmentBackend` | backend id、sequence、capture/action 次数、terminal state、owned/borrowed close counter |
| `InMemorySessionBackend` | snapshot revision、message/tool-call pairing、resume cleanup、并发写检测 |
| `RecordingPermissionPolicy` | typed subject/action/resource decision 与调用顺序；默认结果 allow |
| `RecordingExecutionObserver` | before/after/exception、terminal owner、domain ledger correlation |
| `FakeResourceManager` | resource key、最大并发数、lease owner、cancel/timeout release、emergency stop |
| `FakeClock/Deadline` | 无真实 sleep 的 timeout、retry budget、TTL 和 stale observation |
| `FakeMcpServer` | 复用 OpenHarness protocol fixture，支持 stdio/HTTP、invalid schema、disconnect 和 slow close |
| `FakeChannel/BoundedBus` | session identity、progress flood、final/error/cancel 保留和 shutdown drain |

建议放置于 `tests/homemaster/fakes/`；领域已有 fake 必须优先扩展，不能创建名字相同、语义不同的
第二套。所有 fake 都要有自测，证明它确实能触发目标失败路径。

### 24.5 核心 contract 测试矩阵

| 能力 | 关键断言 | 目标测试文件 |
|---|---|---|
| ToolDefinition/RegisteredTool | immutable serializable definition 与 model manifest 同源；stable id/provenance/version 不可变；executor/verifier 只在 RegisteredTool，不被序列化 | `tests/homemaster/tools/test_definition.py` |
| ToolExecutionResult | text/data/image/attachment/observation/evidence/failure/terminal 均能无损转消息；非法组合 fail-fast | `tests/homemaster/tools/test_execution_result.py` |
| ToolCatalog | internal id 唯一；不同环境可注册同 alias variant；来源可查询；不静默覆盖 | `tests/homemaster/tools/test_catalog.py` |
| ToolView | enabled ids 冻结；manifest 有序；同 view alias 冲突失败；disabled/unknown id 在执行层拒绝 | `tests/homemaster/tools/test_view.py` |
| Validation | missing/type/enum/nested/additionalProperties 在 executor 前拒绝；非空 output schema 在 executor 后拒绝不合规结果 | `tests/homemaster/tools/test_validation.py` |
| Pipeline order | 精确断言 view/terminal -> validation -> permission -> cancel/deadline -> pre-observation -> lock -> execute -> result validation -> policy-applicable verifier -> post-action debt -> authoritative ledger -> public event | `tests/homemaster/tools/test_execution_pipeline.py` |
| Verification policy | 四个 typed policy 维度被执行；visual debt 要求 fresh explicit `observe`；structured receipt/state 可不看图；none 不发布 runtime-verified success；verifier/audit capture 不能成为 model observation | 同上 |
| Tool retry | 每 attempt 独立 evidence；总 deadline 生效；`outcome_unknown` 的 mutating tool 不自动重试；read/idempotent policy 可重试 | `tests/homemaster/tools/test_retry_policy.py` |
| Permission seam | `AllowAll` 仍被调用；deny/confirm 不获得 lock、不执行 backend；subject 不从 metadata 伪造 | `tests/homemaster/permissions/test_policy.py` |
| ObservationService | id/backend/generation/state sequence/capture-event sequence/media/content hash/evidence 一致；raster 必须有 pixel hash，canonical DOM/state 必须无 pixel hash；stale/foreign/wrong-sequence 拒绝 | `tests/homemaster/tools/test_observation_service.py` |
| Observation profiles | 三个 ToolView 均恰好一个 model alias `observe`，internal id/schema/executor 可不同；旧别名不进 manifest；Coworker 正好 11 tools 且第五项 `observe` | `tests/homemaster/integration/test_observation_profiles.py` |
| No implicit model media | initial prompt、动作/verify result、普通含 `frame_path` 的 result、verifier/audit capture 均无 model image/DOM；native action frame 仍进入 internal evidence；只有显式 `observe` 可产生 observation content | `tests/homemaster/integration/test_no_implicit_model_media.py` |
| Observation transcript | no-image initial request -> observe -> result -> next frozen request binds -> action -> receipt only -> debt -> fresh observe；retry 不 recapture，同 response observe+action 拒绝，旧历史图不授权 | `tests/homemaster/integration/test_observation_transcript.py` |
| Task state trust | model constraints/evidence/completion 不能授权动作或覆盖 verifier/scorer；completion gate 执行 observation/verification policy；TaskStatus transition 不静默忽略，turn/iteration/revision 分离 | `tests/homemaster/task_state/test_trust_boundary.py` |
| Frozen provider retry | retry request canonical bytes/hash 和 observation bytes/content hash 完全相同；raster pixel bytes/hash 不变；capture count 不增加；failed attempt 不 commit model view | `tests/homemaster/providers/test_frozen_request_retry.py` |
| Public events | domain ledger 先持久化；RuntimeEvent 是投影；redaction/allowlist/correlation 失败不发布 public event | `tests/homemaster/events/test_public_projection.py` |

Pipeline 测试使用 recording spies 断言调用顺序和“未调用”条件，不能只比较最终错误字符串。
使用 stage-applicable matrix：validation 覆盖 accept/reject/exception；permission 覆盖 allow/deny/
confirm/error；pre-observation 覆盖 current/stale/foreign/missing；lock/execute/policy verifier 覆盖
success/typed failure/exception/timeout/cancel；post-action debt、authoritative ledger 和 public projection
覆盖 backend advanced/unchanged/unknown 及 projection reject。每一行都断言 short-circuit、后续阶段
未调用、lease cleanup、ledger-before-event 和合法 tool-use/result pairing；不要求把不适用 outcome
机械套到每个 stage，也不对 policy 为 `none` 的工具强行调用 verifier。
当前大量 legacy `ToolSpec.output_schema` 是空对象：adapter 对空 schema 只能记录
`output_schema_unset`，不能套用一个虚构的通用 result schema 改写领域返回值。新工具以及完成
variant 迁移的工具必须声明真实非空 schema，并验证其 text/data/image/terminal 等实际结果形态；
同一公开别名的 Home、ALFWorld、Coworker variant 可以保留不同 output schema。

### 24.6 生命周期、Session 与并发矩阵

新增 `tests/homemaster/application/`：

- `test_resource_scope.py`：每 acquire 成功后立即注册 release；第 N 步启动失败逆序回滚前 N-1
  个资源；一个 close 抛错不阻止其余 close；owned 只关闭一次，borrowed 永不关闭。
- `test_session_manager.py`：默认 run 新 session；resume 使用显式 session id；连续 ALFWorld taskset
  只有显式 flag 才共享；active backend/ToolView/provider client 不进入 snapshot；进程重启 resume/backend
  rebind 后环境 profile 从 `NEEDS_OBSERVE` 开始，旧 observation/binding 不授权动作或 completion。
- `test_session_file_backend.py`：expected revision/CAS 拒绝 stale writer；单写者锁不泄漏；先持久化
  immutable revision snapshot，再以原子 commit/latest pointer 发布；在两个写入边界注入 crash 后，
  load 只返回完整旧 revision 或完整新 revision，不返回 torn/mismatched snapshot；覆盖多进程/多线程
  concurrent writer。
- `test_task_state_wiring.py`：同一 session 的 task tools、ContextAssembler 和 persistence 获得 exact
  same TaskStateStore；每个 model iteration 重新投影，provider retry 不重组；不同 session 不串；
  `snapshot_id` 不作为 revision，TaskStatus/AgentRunStatus/classification 不混用。
- `test_context_assembler_scope.py`：force/manual compaction request 属于 session/generation，A session
  请求 compact 不触发 B session；TaskSnapshot prelude 不能消失。
- `test_application_runtime.py`：所有请求冻结 run scope；同 session turn lock 串行，不同 session
  可并发；run 完成或失败都清除 active task/lease。
- `test_cancellation.py`：provider wait、tool wait、verification wait 三个位置均可取消；取消后
  message history 保持合法 tool-use/result pairing，锁和 lease 释放。
- `test_generation_fencing.py`：旧 generation 即使吞掉 cancellation 后晚返回，也不能写 message、
  TaskSnapshot、domain ledger 投影或 final response。
- `test_concurrency.py`：至少 32 个 fake sessions 全部能在 barrier 前进入等待；同 resource key
  最大 backend 并发为 1，不同 key 可并发；断言使用 barrier/counter，不使用墙钟 sleep。

Session resume 必须额外跑现有 context compaction、TaskSnapshot 和 interruption tests，证明迁移
没有把持久化降级为只恢复聊天文本；新增 `compact -> process restart -> resume`，证明 manual compact
已原子持久化而不是只修改 shell 内存。

### 24.7 入口一致性与兼容 adapter

新增 `tests/homemaster/integration/test_entry_parity.py`，Phase 0 用同一个 recording application factory
分别驱动 CLI print、Interactive、ALFWorld fake 和 Coworker/Benchmark fake；Gateway 尚未实现，不作为
Phase 0 parity 前置。Phase 2 再把 Gateway fake 加入同一矩阵。断言：

- 四个入口都只提交 `RunRequest`，不直接构造 Provider、AgentRuntime、Dispatcher 或 registry。
- 相同 profile/config 解析到相同 provider identity、run policy、ToolView ids 和 pipeline。
- `metadata` 改值不能改变 tools、environment、terminal policy 或 permission subject。
- V1.9 新增的 top-level default interactive、`-p`、`--dry-run`、resume 和
  `text/json/stream-json` 共享同一 `RunResult`，仅 renderer 不同；现有 `run --utterance` wrapper
  保持兼容，但不能把新输出格式描述为历史 golden。
- dry-run 不连接 provider/MCP/backend；未探测 MCP tools 显示 `unknown_until_connect`；显式
  `--probe` 才允许 live discovery。
- 兼容 `run/shell` wrapper 只转调 ApplicationRuntime；删除 wrapper 前有 CLI golden 和明确版本。

迁移 ALFWorld/Coworker 时保留 old/new manifest differential，但 mutating episode 只运行一条
执行链。静态 import/AST guard 要证明 benchmark 目录不再创建 model-facing ToolSpec、Provider、
Dispatcher 或 AgentRuntime，同时允许它继续拥有 environment/scorer/recording/artifact verifier。

`tests/homemaster/fixtures/v19/allowed_deltas.json` 只允许记录已经由本规格确认的迁移差异：

- ALFWorld 增加 Harness `observe`，同时删除 runner 初始自动图片以及导航、操作、验证结果中的
  隐式模型图片；不能只增加 tool 而继续由 `_visual_tool_result()` 在动作后附图。runner 的录屏、
  verifier 审计 capture 和 artifact 图片仍可保留，但不得作为新的 model content block 注入。
- Home 正式导航 manifest 从 `robot_navigate` 切到 `robot_go_to`；旧名只在带删除版本的 wrapper
  测试中存在。
- Home 的旧 observation alias 从 model surface 删除，由 `observe` 取代；legacy backend adapter
  不得进入 manifest/prompt/skill/tool-result name。
- Coworker 仍固定十一项，但第五项从旧 observation alias 改为 `observe`；internal backend operation
  可以暂时保留兼容映射，model surface 不得泄漏内部名。
- Coworker `TICKET_READ` 节点从 `browser_navigate` 迁给显式 `observe` 是单独批准的 trust-boundary
  delta：node id/顺序/语义保持不变，但更新 `agent_trajectory_ground_truth.yaml`、prompt、episode store/
  scoring tests 和 evidence producer。`browser_navigate` 不能在模型尚未看到 DOM 时提前记 read。

每条 delta 必须包含 old/new canonical value、reason、owner、引入 Phase 和删除/稳定条件；除此
之外的 tool 顺序、schema、action count、classification、evidence 或 scorer input 差异都失败。

### 24.8 ALFWorld V1.8 不回归 gate

以下现有测试是迁移时的 required suite，优先扩展而不是复制：

```text
tests/homemaster/benchmarking/test_alfworld_runtime_contract.py
tests/homemaster/benchmarking/test_alfworld_reset_transaction.py
tests/homemaster/benchmarking/test_alfworld_gateway.py
tests/homemaster/benchmarking/test_alfworld_model_view.py
tests/homemaster/benchmarking/test_alfworld_navigation.py
tests/homemaster/benchmarking/test_alfworld_outcome.py
tests/homemaster/benchmarking/test_alfworld_tracing.py
tests/homemaster/benchmarking/test_alfworld_runner.py
tests/homemaster/benchmarking/test_alfworld_v18_guards.py
```

在这些 oracle 上增加统一入口断言：

- runtime identity 在 backend 借入前已 pin；reset/scan/restore/normal-time transaction 仍由 runner
  拥有，setup/control/model action counts 分域且可独立重算。
- 只有 successful frozen provider request 的 committed model view 可授权动作；`observe` 产生的新
  observation 必须绑定下一次 request，provider retry 不能换帧。
- 初始 request 无图片且只允许 bootstrap `observe`；完整 transcript 必须是 initial(no image) ->
  observe call -> observation result -> next frozen request binds exact bytes -> action -> structured receipt
  (no image) -> old view invalid -> fresh observe。覆盖 retry capture count 不增、同一 assistant response
  的 observe+mutation 被拒、旧历史图片不能重新授权。
- ALF ToolView 的导航公开名有且只有 `robot_go_to`，不得包含 `robot_navigate`/`robot_find_object`；
  操作、验证、任务和 `observe` 等非导航工具按 ALF profile 保留。disabled Home/Coworker variant
  即使伪造调用也不会到达 OracleActionGateway。
- legacy name guard 检查 model manifest、ToolView、model-facing registry 和模型提示；
  `env_adapter.py` 的 execution-only translator、历史 trace 兼容与低层回归 fixture 可以暂时识别
  `robot_navigate`，但必须有 provenance、调用边界和删除条件，不能重新暴露给模型。
- terminal/uncertain/closed 后新调用不增加 model backend action count；classification、terminal
  tool call id、score eligibility、cleanup/quarantine 与 `5b150a9` fixture 一致。
- 冻结当前 Dispatcher 对一个 batch 先为全部 tool calls 调用 observer `on_call()`、再进入 gate 的顺序，
  以及 `AlfworldToolDispatchObserver` 对 `agent_tool_call_count` 的影响；迁移不能无意改变分域计数。
- fresh episode 默认新 session；显式连续 taskset 才共享 session，并且每个 subtask 的 provider
  attempt、model view、observation sequence 和 ledger correlation 不串线。

PR 使用 fake/recorded provider 与 adapter tests；`live_alfworld` nightly 使用固定 runtime contract
和 trial manifest，保存完整 manifest、exit code、action counts 与 artifact hashes。live 分数可以
受模型影响，但 contract violation、证据缺失或 formal score unavailable 不能被平均分掩盖。

0C-ALF 候选迁移 gate 与最终产品质量 gate 使用同一份四条 manifest，但 verifier mode 不同。候选
`migration` mode 必须断言 selected=4、attempted=4、eligible=4、每条 formal score available，且没有
Harness invalid/contract violation；success 数完整报告但不影响迁移 gate。全部 CL 完成后，在最终 SHA
重新 fresh run，`release` mode 才额外断言 success=4。两个 mode 的 unit tests 必须证明 task failure 在
`migration` 中仍保留并报告，而在 `release` 中返回非零；缺证据/identity/contract 错误在两者都非零。

最终 release gate 新增并提交 `config/alfworld_v19_release_trials.json`。source inventory 是提交的
`config/alfworld_v18_regression_trials.json`；builder 使用 M0 固定的 hash-rank algorithm version/seed，validator 保存并校验
source hash、rank 和 trial identity，恰好取四条且在看到 V1.9 模型结果之前锁定。正式命令必须使用
真实 provider、`AlfredThorEnv`、`valid_unseen`、`visual_eval`、`--episodes 4` 和该 manifest。

四条必须在同一个 fresh release run 中全部满足：真实 provider/model identity 验证通过且每条均有
实际 Provider attempt；episode score eligible、无 Harness invalid/contract violation、
`classification="agent_success"`；release verifier 计算并断言 selected=4、attempted=4、eligible=4、
success=4、`formal_score_available=true` 且自身 exit 0。不得依赖当前通用 summary 中不存在的
`coverage` 字段，也不得只汇报
成功子集、从十条旧 evidence 中挑四条、用单条重跑替换失败条目，或把 provider/runtime
availability 当任务成功。修复后允许新建候选并重跑，但旧失败 run 和 manifest 必须保留，最终
候选仍须从头完整跑四条。

当前 README 的较新证据是 10 条中 1 条成功、5 条 score eligible、coverage 0.5 且 formal score
unavailable，因此 4/4 是新的产品质量 gate，不是架构迁移 parity。M0 只确认 runtime/provider 可测；
non-live contract-valid、候选 live contract-valid 和最终 4/4 quality gate 必须分别报告，任何一层不能
用另一层替代。

### 24.9 Coworker 不回归 gate

以下 suite 必须继续通过：

```text
tests/homemaster/benchmarking/coworker_demo/
tests/case02_openenv/
tests/coworker_demo/
```

新增统一入口断言：

- 每个 child run 默认新 session，provider/deadline/budget identity 与 run id 对齐。
- Coworker ToolView 严格为
  `task_planner, task_progress_check, skill_view, browser_navigate, observe, browser_click, browser_fill,
  browser_select, browser_wait, terminal_execute, sop_decide`。`coworker.observe.v1` 可映射 legacy
  backend operation，但 manifest/result name 只能是 `observe`，不新增第十二项。
- `browser_navigate` 只返回 URL/state-version/evidence receipt，不返回完整 DOM；显式 `observe` 后才
  产生 canonical DOM content hash 并记录 ticket-read。在此之前 planner、browser mutation、SOP
  decision 和 completion 按 policy 拒绝。click/fill/select/wait 的 typed receipt/readback 不是新
  observation，也不能由名为 `visible_observation` 的普通字段清除 debt。
- FastAPI、browser、VNC 和 recording 仍由 runner-owned lifecycle 管理；ApplicationRuntime
  不关闭 borrowed client/display/recording，失败路径仍 stop recording/finalize/cleanup。
- private runtime/provider/domain events 先经过现有 presentation trust boundary，再进入
  PublicEventProjection；secret、absolute path、raw provider payload 和不可信字段不外泄。
- artifact manifest/hash、video verification、trajectory/result score 和 `formal_success` 仍由独立
  verifier 决定，RuntimeEvent final 不得自行把 run 标成成功。
- 24 节点 DAG 只批准 `TICKET_READ.tool_name` 从旧 navigate owner 改为 `observe`；节点数量、依赖、
  其余 tool owner、14 个 checkpoint 和 scoring 权重不变，迁移后的 ground truth/hash 作为 V1.9 新基线。

Coworker 当前只有
`data/coworker_demo/case_02/test_set/item_change_ticket.json` 一个 test-set item。最终
`live_coworker` release gate 在 `hkust4` 对候选 SHA 中这个唯一且 hash-locked 的 item 执行一次 fresh normal
scenario 真实 LLM run；它是唯一计入“通过一条 Coworker 数据”的正式运行，不把同一 item 的
anomaly 注入包装成第二条数据。provider/model identity 必须通过现有真实模型 verifier（当前正式
模型为 `mimo-v2.5`），禁止 localhost/generated/scripted override。

这里的“一次”指该候选只有一个被 release verifier 接受并计分的 formal normal run，不表示失败后可
删除记录再伪装成首次调用。该候选产生的所有 rejected/failed attempts 必须保留、关联并列入 gate
report；原子 fresh-result pointer 只能指向当前正式结果，不能隐藏更早的失败或拒绝记录。

该唯一 run 必须 exit 0、`formal_success=true`，trajectory 24/24、required result checkpoints
14/14，并通过独立 `verify_run_bundle.py` 对页面终态、terminal evidence、连续录像、manifest/hash
和 formal summary 的复核。normal/anomaly/rollback 的 non-live、scripted 和故障注入回归仍全部
保留；anomaly 可作为额外诊断 live run，但不属于本次一条数据的必需结果，也不能替代 normal
正式通过。历史 Coworker run 不能复用为 V1.9 最终证据。

### 24.10 Skills、MCP、Gateway 与扩展测试

Phase 1 复用 OpenHarness tests/fixtures 后必须适配以下 HomeMaster 边界：

- Skills：YAML folded/literal/boolean、来源优先级、同名 provenance；resolved symlink/junction、
  nested resource、absolute/`..` escape；未授权 project/`.agents/.claude` skill 不能获得 robot
  capability 或覆盖 builtin safety skill；从构建出的 wheel 安装到 clean venv 后 builtin
  `SKILL.md`/resources 仍可发现，验证 package-data 而不是 source checkout 偶然性。
- Provider/auth config：typed schema、`defaults < file < env < limited CLI` precedence、field provenance、
  doctor/config/log/event recursive redaction，异常和 status 不泄漏 secret。
- MCP：stdio/HTTP handshake、list/call/resource、server 部分失败、disconnect、timeout、cancel、
  init rollback、close failure；nested/enum/additionalProperties 输入输出保真；同 alias conflict
  和 per-run ToolView enablement；headers/env 全链路 redaction。WebSocket 没有真实实现前不写
  success test，也不把 config type 当支持。

Phase 2 新增：

- router 对 private/group/thread/sender/tenant 产生稳定且隔离的 session key。
- bounded bus 在 progress flood 下允许合并旧 progress，但 final/error/cancellation 不丢；producer
  backpressure 和 shutdown drain 可确定重现。
- remote principal 不能通过 prompt、slash command、attachment 或 metadata 提权；attachment
  resolved path 不可逃出允许 root。
- 同机器人 resource key 串行，不同机器人可并发；timeout/cancel 释放 lease；emergency stop
  优先于普通队列；stale generation 不持有或释放新 generation 的 lease。
- Gateway restart 恢复并清洗 session snapshot，增加 generation；不恢复 live backend/ToolView/client，
  旧进程 late result 不能写回。第一个真实 channel adapter 及 security tests 必须从 OpenHarness 源码
  复制后适配，未选择/未过 auth-device gate 时 ingress disabled。

Phase 3 对 plugin/hook 做 manifest version、provenance、load isolation、timeout、blocking result、
hot reload rollback 和 capability authorization。任意 shell hook 不能成为 robot safety policy 的
唯一实现；每个新增 channel 逐个复制对应 adapter/security tests 后适配，不能批量宣称支持。

### 24.11 Phase gate 与退出条件

| Phase | 必须通过的测试 gate | 退出证据 |
|---|---|---|
| -1 | 环境完整 collection；现有 non-live suite；基线 contract/hash 生成 | `plan/V1.9/baseline/*` + pytest/Ruff exit 0 |
| M0 | live identity/reset/provider canary；四条 deterministic manifest builder/validator | provider attempts > 0 + runtime qualification + source/algorithm/rank hashes |
| 0A | §24.5 全部；三环境 manifest/observation profile；frozen retry | contract tests + stable schema hashes |
| 0B | §24.6；entry fake smoke；owned/borrowed failure matrix | resource acquisition/release ledger |
| 0C-Home | CLI/Home parity、compaction/session/domain fixtures | old/new normalized RunResult diff |
| 0C-ALF | §24.8 全部 non-live；候选 SHA 真实 LLM 四条均 attempted/eligible、formal score available，且无 Harness invalid/contract violation；任务 success 只报告不作为迁移 gate | 4-row manifest + ALF ledger/hash report |
| 0C-Coworker | §24.9 全部 non-live；候选 SHA 唯一 test-set item 真实 LLM 通过 | one fresh run + independent artifact report |
| 0D1 | provider/event async 与 request/event parity | attempt hash + event delivery report |
| 0D2 | agent/pipeline cancellation、deadline、resource concurrency | no pending task/lease + pairing parity |
| 0D3 | Coworker Playwright thread/async ownership | thread-affinity + browser lifecycle report |
| 0D4 | stress/generation/backpressure/leak；全部 0C 回归 | stress counters + model-view/scorer parity |
| 1 | Skills/MCP/config/artifact tests | protocol fixtures + redaction/cleanup report |
| 2 | Gateway/security/backpressure/device tests | overload/cancel/authorization matrix |
| 3 | plugin/hook isolation/version/hot reload tests | compatibility manifest + rollback report |
| V1.9 release | Phase 0D4 后同一最终 SHA：ALF success=4/4 + Coworker formal 1/1 | two-machine reports + independent verifiers + merged identity/hash report |

每个 gate 的硬退出条件：required tests exit 0；无新增非 live skip/xfail；无未解释 golden drift；
无 secret/绝对路径进入 committed fixtures；required cleanup counters 归零；领域 scorer 和 ledger
仍由原 owner 计算。外部 gate 若因基础设施不可用而未运行，阶段状态只能是 `UNVERIFIED`，不能
标为完成。对最终 V1.9，ALFWorld 4/4 与 Coworker 1/1 任一为 `UNVERIFIED` 或 `FAIL` 时整体也不得
标为完成。

### 24.12 CI、压力与可观测性

目标 CI 分组：

CL-05 复制首批 async query tests 前，dev dependencies 必须增加与 Python 3.11/pytest 版本兼容的
async pytest 支持；async tests 使用显式 asyncio mode，不依赖未声明的 runner plugin。Phase 0D 复用
同一 runner 契约。

```bash
# Required fast/static
uv run python -m compileall -q src tests
uv run ruff check src tests
uv run pytest -q tests/homemaster/tools tests/homemaster/application

# Required non-live regression
uv run pytest -q -m "not live_api and not live_alfworld and not live_mcp and not live_coworker and not stress"

# Nightly deterministic stress
uv run pytest -q -m stress

# Nightly/release external gates, each reported independently
uv run pytest -q -m live_api
uv run pytest -q -m live_alfworld
uv run pytest -q -m live_mcp
uv run pytest -q -m live_coworker
```

marker suite 只验证 live integration，不能替代最终 benchmark application run。现有 ALF CLI 会在
episode 失败时打印 summary 但仍可能 exit 0，Interactive 也会捕获 Coworker 异常后继续，因此它们
不能直接充当 release gate。CL-13/14 必须提供 machine-readable runners/verifiers：两种 ALF gate
mode 对缺 artifact、identity/hash mismatch、Harness invalid/contract violation 都返回非零；`migration`
允许任务失败但完整报告 success，`release` 对任一任务失败也返回非零。最终候选在 HPC2 使用本机
gitignored provider config 执行：

```bash
set -euo pipefail
: "${V19_RELEASE_SHA:?set V19_RELEASE_SHA}"
RELEASE_ROOT="var/v19-release/$V19_RELEASE_SHA"
uv run python scripts/v19_release/run_alfworld.py \
  --alfworld-root "$HOMEMASTER_ALFWORLD_ROOT" \
  --alfworld-config "$HOMEMASTER_ALFWORLD_CONFIG" \
  --trace-root "$RELEASE_ROOT/alfworld" \
  --env-type AlfredThorEnv \
  --split valid_unseen \
  --episodes 4 \
  --trial-manifest config/alfworld_v19_release_trials.json \
  --observation-mode visual_eval \
  --api-config "$HOMEMASTER_PROVIDER_CONFIG" \
  --provider-name "$HOMEMASTER_RELEASE_PROVIDER" \
  --report "$RELEASE_ROOT/alfworld-run.json"
uv run python scripts/v19_release/verify_alfworld_release.py \
  --gate release \
  --report "$RELEASE_ROOT/alfworld-run.json" \
  --manifest config/alfworld_v19_release_trials.json \
  --expected-sha "$V19_RELEASE_SHA" \
  --expect-selected 4 --expect-attempted 4 --expect-eligible 4 --expect-success 4
```

CL-13 的候选迁移 gate 对对应 fresh report 使用同一个 verifier，不传 success 期望：

```bash
uv run python scripts/v19_release/verify_alfworld_release.py \
  --gate migration \
  --report "$CANDIDATE_ROOT/alfworld-run.json" \
  --manifest config/alfworld_v19_release_trials.json \
  --expected-sha "$CANDIDATE_SHA" \
  --expect-selected 4 --expect-attempted 4 --expect-eligible 4
```

同一 release SHA 在 `hkust4` 完成 Coworker preflight 后，用唯一 ticket 启动一次真实 run，并对实际
输出的 run root 做独立验证：

```bash
set -euo pipefail
: "${V19_RELEASE_SHA:?set V19_RELEASE_SHA}"
RELEASE_ROOT="var/v19-release/$V19_RELEASE_SHA"
.venv/bin/python scripts/coworker_demo/preflight.py \
  --coworker-config config/coworker_demo.yaml \
  --provider-config config/homemaster.yaml
TICKET="$(realpath data/coworker_demo/case_02/test_set/item_change_ticket.json)"
.venv/bin/python scripts/v19_release/run_coworker.py \
  --ticket "$TICKET" \
  --output-root "$RELEASE_ROOT/coworker" \
  --result-pointer "$RELEASE_ROOT/coworker-run.json"
.venv/bin/python scripts/v19_release/verify_coworker_release.py \
  --result-pointer "$RELEASE_ROOT/coworker-run.json" \
  --data-root data/coworker_demo/case_02 \
  --expected-model mimo-v2.5 \
  --expected-sha "$V19_RELEASE_SHA" \
  --expect-success 1
```

Coworker 每个 candidate 维护加锁、fsync 的 append-only `coworker-attempts.jsonl`，每次 attempt 在开始
和完成时写 attempt id、candidate/SHA、dataset hash、start/end、run root、status 和 artifact manifest
hash，不允许覆盖/截断。runner 原子写 `coworker-run.json`，除本次 accepted run id/root 外还必须包含
attempt-index path/hash 和 attempt id。verifier 从 pointer 解析 index，校验同 candidate 的全部 rejected/
failed/accepted attempts 都存在于 gate report，拒绝历史目录、跨 candidate、多 accepted pointer 或早于
preflight 的 artifact；pointer 不能隐藏先前失败。preflight 同时校验 provider 和 Coworker 两份 ignored
config 都是 mode 0600。两项 live gate 都保存
release SHA、provider/model identity、dataset/manifest hash、命令退出码、summary 和 artifact hash；
任何 key、完整 provider payload 或未脱敏配置不得进入证据包。

stress suite 至少覆盖 32 并发 fake sessions、1000 个 progress events、连续 1000 次 open/close 的
资源计数和 100 次 cancel/restart generation race。验收使用 barrier、queue size、counter 和
pending-task inspection，不用脆弱的固定毫秒阈值。性能趋势可记录 no-tool turn、tool turn 和
observation serialization 的 p50/p95，但首版只在出现明显倍数回归时报警，不用单台共享机器的
绝对时间作为功能 gate。

CI 为每组保存 JUnit、pytest summary、source/lock identity 和领域 manifest。RuntimeEvent queue
是否丢弃 progress 不影响测试 oracle：evidence/domain ledger 必须先持久化，测试从权威 store
重算结果，再单独验证公共事件投影。
