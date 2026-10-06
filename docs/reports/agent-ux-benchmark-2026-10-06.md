# Agent 产品 UX 对标调研 — HomeMaster vs 主流开源 Agent

> 调研日期：2026-10-06。方法：本机视察 Homemaster-v35 源码 + 4 个并行子 agent 抓取各项目
> GitHub/README/官方文档。Star 数为搜索快照近似值，引用前建议复核。

## 0. HomeMaster 现状诊断（视察结论）

视察对象：`v35-architecture` 分支（最新开发线，含 V3.7 agentscope 迁移与
`model_observation` 拆除的进行中改动）；`main` 停在 9/24 的 v34，`sync-v37` 已被
v35 分支覆盖。

### 安装 —— 最重的一块短板

- `scripts/setup.sh` **硬编码 `Linux:x86_64`**，macOS 直接拒绝执行；要求 uv `>=0.11,<0.13`
  这种窄版本窗口。
- 运行时依赖极重：Neo4j（+ Java，走自建 GitHub release 分发 runtime-assets）、Qdrant、
  spaCy `en_core_web_sm` whl、fastembed、aiokafka、vendored `mindmemos`（`[tool.uv.sources]`
  **path 依赖**）、vendored `agentscope`。
- path 依赖意味着 `dist/` 里那个 1.1MB wheel **无法正常 pip 安装**，也没有 PyPI 发布、
  没有 `uvx`/`pipx` 一条命令、没有 curl|bash、没有 brew。装环境 = 克隆源码 + uv sync +
  下 Neo4j/Java + 手改 YAML + chmod 600，五步缺一不可。

### 模型配置 —— 功能在、UX 为零

- provider 抽象其实不差（`api_format`/`transport`/`auth_type`/`base_url`，支持
  anthropic/openai 兼容端点），但**唯一入口是手编 YAML**。
- `--provider-name`/`--model` 只在 `-p`/`run`/`--dry-run` 下合法——**交互 shell 和 Web
  Console 里完全无法切换模型**，没有 `/model`、没有下拉选择器、没有 `login` 命令。
- 对比：opencode `/connect`+`/models`、hermes `hermes model` 交互选择器、dsh Settings→Models
  卡片式配置 + "Fetch available models"、cline ModelPickerModal 模糊搜索。

### CLI 交互 —— 命令不少，体验停在 argparse 时代

- `app.py` callback 里十几个全局 flag 的互斥校验矩阵（`--gateway` 不能配 `--print`，
  `--browser` 需要 `--gateway` 或 `--print`……）——模式混杂在一个入口里。
- 两个一次性入口并存：`-p/--print` 和 `run --utterance`，参数体系还不一致。
- `shell` 是裸 `input()` 循环 + `utterance == "/help"` 字符串比较的 8 个硬编码命令；
  无补全、无历史搜索（只 import readline）、无 `/model`、无 session picker、无 `@file`、
  最终回复 `typer.echo(f"Assistant: ...")` 平铺输出（Rich renderer 只服务 `run`）。
- 版本横幅写死 `HomeMaster V1.9`（app.py、interactive_shell.py），CHANGELOG 已到 V3.x。

### Web Console —— 能用但素

- FastAPI + React/Vite 小组件（~10 个组件，无路由库），WebSocket per session，
  `web/static_dist` 打进 wheel。loopback-only 是刻意设计。
- 无模型/provider 切换 UI、无配置页、无登录概念；会话、记忆、权限三个视图。
- 值得注意的是 `web/src/api/connection.ts` 注释写明 "Adapted from DeepSeek Harness"，
  `rich_renderer.py` 注明 "adapted from locked OpenHarness"——项目已经在局部借用
  成熟实现，缺的是把它们上升到架构层。

### 架构 —— composition 已模块化，但仍是"每进程一份 Runtime"

- V3.7 已完成内部重组：`compose_application` 拆为 `application/composition/` 的
  per-domain composer（base/memory/observability/profiles/providers/skills/tools），
  `ApplicationRuntime` 1212→~510 行瘦身（`run_driver`/`extension_lifecycle`/
  `memory_recall` 分离，资源所有权表显式化）——见 `plan/V3.7/design-session-orchestrator.md`。
- 但**调用拓扑没变**：`compose_application()` 仍被 6 个入口各自在进程内调用
  （`cli/run_command`、`cli/interactive_shell`、`cli/gateway_command`、
  `web/serve` 主+browser+alfworld 三处、benchmark、locomo runner）。没有"一个常驻
  server + 多个瘦客户端"的形态——这正是 opencode/dsh/openclaw/cline 全部收敛到的
  架构，也是"CLI 和 web 交互逻辑不一致"的总根源。V3.7 的拆分是前置利好：composition
  已是干净工厂 + 关闭序显式，server 化的改造面因此小得多。

## 1. 横向对比

| 项目 | 安装（一键程度） | CLI/TUI | Web/GUI | 模型配置/切换 | 打包/App |
|---|---|---|---|---|---|
| **opencode** (anomalyco) ~200k★ | `curl opencode.ai/install \| bash` 下平台二进制；另有 npm optionalDeps、brew、pacman、nix | 自研 OpenTUI（Zig+SolidJS）；leader-key 快捷键、`/init` `/undo`(git snapshot) `/share`、Tab 切 build/plan agent | `opencode serve` 无头 server + `opencode web`（UI **打进二进制**）、`opencode attach` 远程挂 TUI、Desktop(Electron 迁移中) | 75+ provider（models.dev catalog）、`/connect` 交互登录、`/models` 选择器、`opencode.json` 8 层配置 | **全渠道**：单二进制+npm+brew+AUR+scoop+nix+docker+desktop dmg/exe/AppImage |
| **pi** (earendil-works, 原 badlogic) ~73k★ | `npm i -g @earendil-works/pi-coding-agent` | 自研 pi-tui 差分渲染；`@`文件、会话**树** `/tree` `/fork` `/clone`、`/export` HTML、`/share` gist；刻意**无逐工具审批** | 无官方 web 完整版；RPC stdio 模式供 IDE 嵌入；pi-mom 接 Slack | `/model` 选择器（Ctrl+S 存默认）、`models.json` 自定义 provider、OAuth `/login` | npm 包；extensions/skills/themes 统一 `pi install npm:/git:` 分发 |
| **hermes-agent** (NousResearch) ~250k★ | `curl install.sh \| bash`（自动装 uv/Node/submodule）；`pip install hermes-agent` | Node 构建的现代 TUI（modal overlay、鼠标）；`/model` `/bg` `/btw`(旁路提问不打断) `/skin` `/voice` | `hermes dashboard` → 本地 web UI（Chat tab 用 **PTY 嵌入 TUI**）；社区 Electron/pywebview 桌面端 | `hermes model` 选择器、`/model provider:id` 会话切换、Nous Portal OAuth 300+ 模型、openai-compatible | PyPI + git install；terminal backend 抽象 local/Docker/SSH/Modal |
| **deepseek-harness `dsh`** (deepseek-ai) ~210k★ | `npx @deepseek-ai/dsh web` **一条命令起 Web**；`pip install deepseek-harness-sdk`（wheel 内含 native runtime） | **刻意无官方 TUI**：`dsh` 是 profile 启动器（web/headless/sdk/acp 四种产品形态同一二进制） | Web 是主交互面（官方）；桌面靠社区 Tauri/Electron | Settings→Models 卡片 UI、Fetch available models、`$DSH_HOME/.credentials.yaml`、LLM 适配本身也是插件 | pnpm monorepo + Cordis 插件框架；事件溯源 session log |
| **cline** (cline) ~70k★ | VS Marketplace 装扩展；`npm i -g cline` CLI | `cline -i` TUI（Tab 切 Plan/Act、Shift+Tab 切 auto-approve、`/undo`）；`cline "prompt"` headless；`--acp` ACP | VSCode webview（React+Tailwind）：TaskTimeline 色块时间轴可点跳、Checkpoint "Restore Files/Task/Both" 三分离、diff 用编辑器原生视图 | 40+ provider 枚举、ModelPickerModal（模糊搜索、Plan/Act 可配不同模型）、key 存 VSCode Secrets；`cline auth` OAuth | 扩展+npm CLI+Tauri desktop 样例+`@cline/sdk`（HostProvider gRPC 抽象，同一核心跑 VSCode/CLI/JetBrains/Desktop） |
| **Devin CLI** (Cognition) | 官方 install 脚本/npm（cli-install snippet） | TUI + `/model` 选择器、`Alt+T` 切 thinking level、`--model` flag、skill/MCP/subagent 体系 | 桌面 app 即其宿主（Devin.app） | `~/.config/devin/config.json` 全局配置 + `/model` 会话内切换 + short name（`opus`/`swe`）恒解析最新版 | 预编译分发，随 Devin.app 发布 |
| **OpenClaw** (openclaw) ~390k★ | `curl openclaw.ai/install.sh \| bash`：探测 OS/arch→装 Node→npm/git 装包→自动 `onboard`；桌面 app "全包" | 命令式 CLI（域分组：gateway/sessions/message/agent/skills…）+ `openclaw tui` + onboard 分层降级（浏览器握手→URL+SSH 提示→终端兜底） | **Gateway 单端口复用**：WS 控制面+OpenAI 兼容 API+Control UI（Lit，打进 Gateway）一端口全承载 | `openclaw.json`(JSON5+`$include`)、`models set/auth`、Control UI Settings→Models、`utilityModel` 小模型跑标题摘要、SecretRef 密钥不落盘 | **原生全家桶**：macOS SwiftUI 菜单栏、iOS/watchOS、Android Kotlin、Windows WinUI3、Linux Tauri——全以 `role:node` 接同一 WS |

## 2. 为什么人家能"一键装 + 多前端 + 随便切模型"——三个共同模式

### A. 分发层：单一产物，渠道全铺

opencode 的答案最彻底：`bun build --compile` 出全平台单文件二进制（连 Web UI dist 都
embed 进去），`curl|bash`、npm optionalDeps、brew、Tauri sidecar **共用同一批 artifacts**；
install 脚本只做"探测平台→下载→落 PATH"三件事。dsh 用 `npx`（Node 生态自带零安装语义），
hermes 用"install.sh 自动把 uv/Node 都装好"。

HomeMaster 卡点：Python 生态没有等价的单二进制惯例（PyInstaller/uv tool 是近似解），
且 vendored path 依赖 + Neo4j/Java 外部资产把"装包"变成了"装一套运行时"。

### B. 架构层：Server 是唯一入口，前端全是客户端

- opencode：`opencode` = TUI + 内嵌 HTTP server（OpenAPI），TUI/Web/Desktop/IDE 插件
  同时连同一 server，`attach` 可远程接管。
- dsh：同一二进制按 profile 变出 web/headless/SDK/ACP 四种形态。
- OpenClaw：单 Gateway 进程持有全部渠道连接，一个端口复用 WS+HTTP API+Control UI。
- cline：HostProvider gRPC 抽象，核心对宿主无感知。

HomeMaster 现在四条入口各 compose 一份 runtime——改任何交互都要四处同步，CLI/web
"逻辑混乱"是必然结果而非实现失误。

### C. 模型层：catalog + 交互登录 + 会话内切换，三件套缺一不可

标配闭环：①内置 provider catalog（models.dev / 手写枚举）让"有什么可选"不需要查文档；
②`/connect` / `auth login` / Settings 卡片把 key 收进专用 store（auth.json /
credentials.yaml / VSCode Secrets），配置文件只留引用；③会话内 `/model` picker 即时
切换（opencode `f2` 最近模型循环、cline Plan/Act 双模型、devin `/model`+`Alt+T`、
openclaw `utilityModel` 分工）。HomeMaster 三件套全是空白，YAML 直通用户。

## 3. 建议（分层，带取舍）

### P0 不动架构的速效包（1-2 周内可见效）

1. **`homemaster auth/config` 交互式配置命令**：prompt_toolkit 向导选 provider 模板
   （anthropic/openai-compatible 两种 api_format 已在）→ 写 YAML + chmod 600。消灭
   "手 cp 模板改占位符"。
2. **shell 补 `/model` `/provider` `/resume` picker**：模型切换走
   `RunRequest.model_override`（已存在，skill 在用）， picker 数据源直接读
   `providers.items`——零新增配置面。
3. **shell 输入换 prompt_toolkit**：补全 `/` 命令、多行、历史搜索；Rich renderer 复用
   到 shell 的回答渲染。
4. **Web 顶栏加 provider/model 下拉** + composer 上方 session 元信息；serve 加一个
   `GET /api/providers` 只读端点 + run 级 `model_override` 入参。
5. **合并 `-p` 与 `run`**：保留 `run` 为正式一次性入口，`-p` 转 alias；callback flag
   矩阵下沉为各子命令自己的参数（typer 支持 callback-less 分组）。
6. 版本横幅改从 `importlib.metadata` 读。

### P1 架构级（决定上限，建议单独立项评审）

7. **Server 化**：把 `web/serve.py` 的 FastAPI 层提为唯一对外协议（HTTP+WS），
   `homemaster` 裸命令默认 `serve` 并自动开浏览器（dsh `dsh web` / openclaw onboard
   模式）；CLI shell 变成连同一 server 的瘦客户端。收益：CLI/web/未来 app 共享
   session、审批、事件流；交互逻辑只有一份。
   - 代价：权限模式需要按 session/请求参数化（现在 web 固定 full_auto、CLI 三档），
     审批协议已有 v2 可复用；需要定义远程模式下 doctor/config 的边界。
8. **Provider catalog**：内置常见 provider 预设（名称+base_url+api_format 默认值），
   用户只填 key 和 model id；`homemaster models list/add` 管自定义端点。配合
   `--model name` 短名解析（devin 式 family alias）。
9. **瘦身安装**：默认安装去掉 aiokafka（确认是否真用）；memory 分档
   `memory.mode: files|embedded_full`，files-only 档不装 Qdrant/Neo4j/spacy——
   让"装上先跑起来"和"全量记忆"解耦（这是 install 卡顿的最大来源）。

### P2 分发与 App（等 P1 落地后做才有意义）

10. **pipx/uv tool 可装**：把 mindmemos 从 path dep 改为 vendored build（setuptools
    packages.find 已含 `mindmemos*`——wheel 其实已把它打进去，验证 `pip install dist/*.whl`
    是否自洽；若自洽，发内部 PyPI index 或 GitHub release wheel +
    `uv tool install --from <url> homemaster`）。
11. **App 化**：server 化之后，桌面 app = 壳 + 内嵌 server。低成本路径是 pywebview
    （hermes-desktop 路线）或 Tauri 调 sidecar（cline/openclaw 路线）；不要走 Electron
    自带 Chromium 那条重路。

### 取舍建议

- 如果目标是"**给自己和实验室同事好用**"：P0 全部 + P1-7（server 化）性价比最高，
  web 直接变成唯一前端，CLI 变瘦客户端，交互混乱问题连根拔掉。
- 如果目标是"**对外分发**"：P1-9（memory 分档）是前置条件，否则任何一键安装
  都会被 Neo4j/Java/Qdrant/spacy 拖垮。
- App 化优先级最低——没有 server 化，app 只是第四种各起一份 runtime 的入口。

## 4. 已锁定决策（2026-10-06 用户拍板）

| # | 决策 | 结论 | 排除的备选 |
|---|---|---|---|
| D1 | 第一公民前端 | **Web 第一**：serve 为主入口，CLI 变瘦客户端 | CLI-first、飞书-first、三者平等 |
| D2 | 记忆分档 | **files-only 轻量档**：默认安装仅 SOUL/USER/MEMORY.md，MindMemOS 全家桶变 extra | embedded-lite（留 Qdrant 砍 Neo4j）、不分档 |
| D3 | 网络边界 | **loopback + SSH tunnel**：不加 token 认证远程直连 | token+0.0.0.0、双模式 |
| D4 | dsh 承载路线 | **不评估**，坚持自有 runtime | spike、押注 dsh |
| — | App 化 | **推迟**（用户明确先不做） | pywebview/Tauri 壳 |

由此冻结的范围：server 化按 web-first 做完整协议（CLI 客户端接同一 WS/HTTP）；
安装瘦身以"剥 MindMemOS 全家桶"为主轴；不写远程认证层；不投入 dsh 适配。

## 5. 本地参考（已浅克隆，`~/Documents/workspace/_reference/`，只读）

| 仓库 | 关键路径 | 抄什么 |
|---|---|---|
| `opencode/` | `install`（根脚本）、`packages/opencode`（bun 二进制包）、`packages/app`（web UI）、`packages/llm`（provider 适配）、`packages/core/src/v1/config` | install 脚本平台探测/下载/PATH；server+OpenAPI 拓扑；web 资源打进产物的做法 |
| `pi/` | `packages/coding-agent/src/core/session-manager.ts`、`session-export.ts`、`modes/interactive/components/tree-selector.ts`、`core/models-store.ts`、`cli/list-models.ts`、`packages/ai/scripts/models-dev-*` | session 树（parentId JSONL）、`/tree`/`/fork`/`/export`、models.dev catalog 生成管线、`/model` 选择器 |
| `hermes-agent/` | **`tui_gateway/`（Python JSON-RPC backend + Ink TUI，Desktop 也复用它）**、`hermes_cli/web_routers/`、`cli.py`+`cli_*_mixin.py`（slash 注册表）、`pm/`（uv 环境管理）、`tests/install/` | **同语言同处境的最佳参考**：Python 重 runtime 如何让 TUI/Web/Desktop 都变瘦客户端；install.sh 自动装 uv/Node |
| `deepseek-harness/` | `packages/client/connection/src/client/connection.ts`（**我们的 `web/src/api/connection.ts` 的上游原型**）、`apps/cli` profile 启动器、`cordis.patch.yml` 分层配置 | profile 化入口（web/headless/sdk 同 binary）、connection 重连/generation 协议 |
| `cline/` | `sdk/packages/core`（ClineCore session/SQLite/工具）、webview `ModelPicker*`、`src/integrations/checkpoints/` | Plan/Act 双模式、auto-approve 粒度、checkpoint 三分离 restore、HostProvider 抽象 |
| `openclaw/` | `scripts/install.sh`、`src/gateway/`（单端口 WS+HTTP+UI 复用）、`ui/`（Lit control-ui）、`openclaw.json` 配置 | onboard 分层降级、单 gateway 持有所有渠道、SecretRef 凭据、utilityModel 分工 |

## 6. 参考链接

- opencode: github.com/anomalyco/opencode（原 sst/）、opencode.ai/docs/{tui,server,permissions}
- pi: github.com/earendil-works/pi（原 badlogic/pi-mono）、packages/coding-agent/docs/usage.md
- hermes: github.com/NousResearch/hermes-agent、website/docs/user-guide/cli.md
- dsh: github.com/deepseek-ai/deepseek-harness、deepseek.com/en/harness、apps/cli/README
- cline: github.com/cline/cline、docs.cline.bot/{cli,sdk,core-workflows}
- openclaw: github.com/openclaw/openclaw、docs.openclaw.ai/{install,concepts/architecture,cli,web/control-ui}
