# Web-first UX 与可分发安装 — 实施计划

日期：2026-10-06 ｜ 状态：**v3 修订稿（W4/W5 UI 交互工作流提升为一等公民），待用户确认后动手** ｜
关联调研：`docs/reports/agent-ux-benchmark-2026-10-06.md` ｜
**功能搬运矩阵：`docs/reports/agent-feature-borrowing-matrix-2026-10-06.md`（每个 UI 零件的真实源码证据）** ｜
评审处置：`plan/2026-10-06-web-first-ux-review-disposition.md`

## 0. 已锁定决策（用户拍板，2026-10-06）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 第一公民前端 | **Web**：`serve` 为主入口，CLI 变瘦客户端 |
| D2 | 记忆分档 | **files-only 轻量档**：默认安装仅文件记忆，MindMemOS 全家桶变 extra |
| D3 | 网络边界 | **loopback + SSH tunnel**，不写远程认证层 |
| D4 | dsh 承载路线 | **不评估**，坚持自有 runtime |
| D5 | App 化 | **推迟**，本期不做 |

## 1. 问题与目标

- 安装：`scripts/setup.sh` 硬编码 Linux x86_64；依赖链含 Neo4j+Java（自建 release）、
  Qdrant、spaCy whl、aiokafka；`mindmemos` 是 `[tool.uv.sources]` path 依赖且
  **仍在 `[project.dependencies]`**（wheel 的 `Requires-Dist` 会让 pip 去 PyPI 解析它——
  这是 `uv tool install` 的直接死因）；`memory.enabled=false` 在 V3.2 被显式禁止
  （`config/config.py:379`）。
- 模型 UX：`--model`/`--provider-name` 只在 `-p`/`run`/`dry-run` 合法；shell 与 Web
  无法切换模型；无 login、无 picker。
- CLI：`-p` 与 `run` 双一次性入口；`app.py` callback flag 互斥矩阵；shell 是裸
  `input()` + `==` 硬编码命令；banner 写死 `V1.9`（`app.py:30`、`interactive_shell.py:29`）。
- 架构：6 个入口各自 `compose_application()`，交互逻辑 n 份实现。

**目标**：`uv tool install` 装出能跑的 HomeMaster（files 档）；`serve` 为唯一常驻
runtime，shell 与 Web 连同一 server；任意端可切 provider/model。

**非目标**：App 打包、远程认证、session 树（pi `/tree`，记录为未来项）、dsh 插件化、
模型全量 catalog（本期只做 provider 下拉 + 自由文本）、插件市场。

## 2. 每个零件抄谁（对照表）

| 零件 | 抄的对象 | 本地参考路径 | 抄什么 |
|---|---|---|---|
| Thin-client 架构 | **hermes `tui_gateway/`** | `_reference/hermes-agent/tui_gateway/` | Python backend + 薄前端进程模型（TUI/Desktop 共用 backend）。我们不引 JSON-RPC——现有 FastAPI REST+WS 就是协议面 |
| WS 重连/代际 | **dsh `connection.ts`** | `_reference/deepseek-harness/packages/client/connection/src/client/connection.ts` | `web/src/api/connection.ts` 的上游原型；CLI 瘦客户端 Python 版照抄 generation/backoff/jitter 语义 |
| provider picker | **cline `ModelPickerWithManualEntry`** | `_reference/cline/webview-ui/src/components/settings/ClineModelPicker.tsx`、`ModelPickerWithManualEntry.tsx` | provider 下拉 + 自由文本 model id（本期不上全量 catalog，见 §3-W3） |
| `auth` 向导 | **openclaw onboard + hermes `hermes model`** | `_reference/openclaw/scripts/install.sh`、`_reference/hermes-agent/hermes_cli/` | 分层降级：有终端走问答向导写 YAML；headless 打印手动步骤 |
| 裸命令 = serve+浏览器 | **dsh `dsh web` / openclaw onboard** | `_reference/deepseek-harness/apps/cli/` | `homemaster` 裸跑 = serve + `webbrowser.open`；无图形环境打印 URL+SSH 提示 |
| install 脚本骨架 | **opencode `install`** | `_reference/opencode/install` | 探测平台→装 uv（若无）→`uv tool install`→落 PATH，一条命令语义 |
| auto-approve 粒度 | **cline permissions** | `_reference/cline/docs/` | 后续期，本期不动权限模型 |
| session 树 | **pi `session-manager.ts`** | `_reference/pi/packages/coding-agent/src/core/session-manager.ts` | 仅记录为未来项 |

## 3. 工作分解（v2：已按评审修订）

### W1 记忆分档 + wheel 可分发

**评审核实过的真实结构**：`composition/base.py:437-550` 的 `if resolved.memory.enabled:`
块把 file_memory_store / frozen_memory_context / evidence ledger / migration / neo4j /
mindmemos / queues / dreaming **捆在一起**；`application_services` 用 `mindmemos is not None`
作总闸（base.py:659-663）；`adapters/profiles.py:177` 用 `memory_enabled` 一刀切注册
**全部 7 个记忆工具**。所以 files 档不是"传 None"，是拆块。

**改动**：

1. `config/config.py`：`MemoryConfig` 增 `mode: Literal["files","full"]="full"`；
   `mode=files` 时不再要求 embedding provider / Neo4j 绑定；`enabled` 保留一版
   deprecation warning（`enabled:true`→`full`、`enabled:false`→`files`，均告警指向
   `mode`——**有意推翻 V3.2 禁令**，注释写明，裁决见 §5-2）。
   **同时修 config 定位**（评审 I4）：新增 `HOMEMASTER_HOME`（默认 `~/.homemaster`），
   配置文件解析序：`-c/--config` > `HOMEMASTER_CONFIG_PATH` > `$HOMEMASTER_HOME/config.yaml` >
   repo `config/homemaster.yaml`（dev 兼容）。消除 `REPO_ROOT` fallback 到 `cwd` 的隐患——
   已安装形态下必须有稳定家目录。
2. `composition/base.py`：拆 `if enabled:` 块为两级——
   `files` 档：file_memory_store + frozen_memory_context +（无外部依赖的）evidence ledger；
   `full` 档：再加 migration / managed_neo4j / EmbeddedMindMemOS / add+enrichment queues /
   dreaming / finalizer 提交链。`mindmemos` 总闸改为按 mode 判定，文件记忆不再被它绑架。
3. `adapters/profiles.py` + `memory_tools.py`：`build_memory_tools()` 拆分——files 档注册
   `context_memory`（读文件记忆），摘掉 6 个 `mindmemos_*` 工具（否则 LLM 面对 6 个必然
   失败的工具）。`mindmemos_runtime.py`/`migration.py`/`managed_neo4j.py` 的重依赖 import
   本来就在函数级（评审已核实），只需保证这些函数在 files 档不可达。
4. **doctor 模式感知**（独立交付物，评审 B4）：`cli/doctor.py` `_import_checks`
   按 mode 分档——files 档跳过 `mindmemos`/`fastembed`/`qdrant_client` import 检查与
   `_mindmemos_blackbox_check`/`_embedding_endpoint_check`/`_memory_backend_check`，
   输出里明示 `memory.mode=files`。否则 doctor FAIL 会连累 shell 启动闸
   （`interactive_shell.py:30-34`）。
5. `pyproject.toml`：**从 `[project.dependencies]` 删除 `mindmemos`**（vendored 源码
   经 `packages.find` 已进 wheel，`Requires-Dist` 声明它只会让 pip 去 PyPI 解析失败——
   评审 B1）；`qdrant-client`/`neo4j`/`spacy`/`en-core-web-sm`/`fastembed`/`aiokafka`
   移入 `[project.optional-dependencies] memory`。依赖审计（评审 I3）：mindmemos vendored
   闭包顶层还吃 `omegaconf`/`jieba`/`numpy`/`traced`/`structlog`/`litellm`——逐一判定
   归属 core 还是 memory extra（litellm 若被 providers 用则留 core）；`posthog`/
   `sqlalchemy` 两侧源码疑似零 import，审计删除而非搬运；`aiokafka` 被 vendored
   `mindmemos/infra/kafka/*` 顶层 import，先验证 full 档是否真到达再决定删或随 extra。
6. files 档各入口行为（评审 I7）：`--alfworld`/`--browser`/`gateway` 可跑但
   trajectory/finalization 为 None（runner.py:408 已有守卫）；web `/api/memories*`
   返回 503 + 前端记忆页降级态；`benchmark-alfworld --memory-mode full` 在 files 档
   启动即报清晰配置错误。

**验收门（外部终态）**：① 全新 venv `uv tool install dist/*.whl`（不装 extra）→
`homemaster doctor` PASS 且输出 `memory.mode=files`；② pytest 组合层用例：files 档
compose → 假 provider run → SOUL/USER/MEMORY.md 真实读写、`context_memory` 可用、
`sys.modules` 无 `mindmemos`/`qdrant_client`/`neo4j`/`spacy`；③ `uv tool install
".[memory]"` 完整档在 hkust4 复跑现有 memory 测试全绿；④ `mode`×`enabled` 配置矩阵
测试（含非法组合报错文案）。

### W2 server 化 + CLI 瘦客户端

**底座已存在**（评审复核确认）：`web/app.py` 829 行 REST+WS 协议：
sessions POST/GET、`/{id}/history`、`POST /{id}/messages`（**要求先 WS 订阅否则 409**——
`app.py:280-286`）、`/{id}/cancel`、approvals v2（submit/cancel/get）、
permissions grants+revoke、memories 系列、artifacts、WS `/api/events`。

**改动**：

1. **协议补齐**：
   - `SendMessageRequest` 增 `provider_name`/`model`；服务端映射为 provider 选择 +
     `RunRequest.model_override`。注意 `model_override` 语义（评审核实
     `factory.py:210-235`）= 在已配置 provider 中按 name/model 匹配恰好一个，
     **不是任意 model id**；CLI 现有 `--model` 走 `cli_overrides` 改写 provider.model
     ——server 侧需要同等改写路径或把该 override 显式化为一等 RunRequest 字段。
   - 新端点：`GET /api/providers`（name/kind/model/api_keys_configured，不回显 key）、
     `GET /api/meta`（version 取 importlib.metadata、memory.mode、environment）、
     `GET /api/sessions/{id}/approvals`（pending 列表，供重连恢复用）、
     `POST /api/sessions/{id}/compact`、`GET /api/sessions/{id}/status`（对齐
     `application.compact`/`status`，shell `/compact` `/status` 需要）、
     `POST /api/sessions/{id}/questions/{qid}/answer` + pending-question registry
     （**评审 B3：ask_user 跨进程通道**——web 路径 `ask_user_question` 现在直接返回
     `waiting_user` 终态，需要新增问题注册表 + 回答端点 + executor 接线，工作量与
     approvals 同量级）、`POST /api/skills/resolve`（thin client 无本地
     `skill_registry`，`/xxx` skill 解析要下发——或直接下送解析结果）。
   - **Plan/Act 模式**（用户拍板本轮做）：session 增 `ui_mode: plan|act` 持久化 +
     `POST /api/sessions/{id}/mode`；plan 态映射现有 `plan` permission mode 的
     只读工具约束，act 态回到会话原 permission mode；WS 广播 mode 变更事件让
     web/CLI 徽标同步（cline `session.uiMode` 语义）。
2. **断连契约**（评审 B5）：当前 `_deny_approvals_without_subscriber`
   （app.py:710-719）在最后一个订阅者断开时取消该 session 全部 pending approval，
   且 `WebEventHub` 无 replay buffer、`run.completed` payload 为空。thin client 需要：
   (a) pending approval 不因订阅者断开而取消（改宽限期或干脆不取消——审批是 server
   侧 durable 状态，opencode/openclaw 同此语义；注意别破坏 web 端依赖断连取消的
   现有测试）；(b) 重连后 `GET /{id}/history` + pending approvals 端点回补；
   (c) `run.completed` 携带 final payload 或 client 端从 `/history` 取终态。
3. **CLI 瘦客户端**（新 `cli/client.py` + `cli/server_process.py`）：
   - HTTP+WS client；**先 WS 订阅成功再 POST message**（评审 I2 修正顺序）。
     `websockets` 升正式依赖写入 `[project.dependencies]`（已裁决，§5-4）。
   - 断线策略抄 dsh generation/backoff；重连后走 §2 的回补路径。
   - `shell`/`run` 连 `HOMEMASTER_SERVER`（默认 `http://127.0.0.1:8000`）；事件→
     `rich_renderer.py` 投影复用；审批 pending → 终端逐项问答 → POST approvals；
     pending question → `ask>` 提示 → POST answer。
   - `run` 薄化取舍（评审 I5）：compose 期 flag（`--world`/`--memory`/`--alfworld`/
     `--browser`/`--config`）跨进程无对应物 → 保留 `--local` 逃生舱（进程内 compose，
     即现状路径），这些 flag 只在 `--local` 下合法；`session` 子命令转 API
     （delete/export 需补端点），`cron`/`memory` 保留为本机管理命令并文档标注
     "server 同机管理面"。
   - **同机假设写死**（评审 I6）：工具在 server 侧执行；SSH tunnel 转发的是 UI 通道，
     文件/终端等动作发生在 server 所在机器——README/用户指南必须讲明。
4. **入口重排**（`cli/app.py`）：裸 `homemaster` = serve + `webbrowser.open`，
   无图形环境（`SSH_CONNECTION` 且无 `DISPLAY`）→ 打印 URL+SSH 提示（openclaw onboard
   分层降级）；shell/run 连不上 server 时自动 spawn 临时 server 子进程（§5-1，hermes
   模式）；callback 互斥矩阵随子命令化消解；`-p` 保留为 `run` 的糖（§5-3，测试路径）。

**验收门**：① 同一 session 在 web 与 CLI 同开互见（外部终态=两端渲染同一事件流）；
② CLI confirm 模式逐项问答真实放行一次写文件；③ pending question 端到端（模型
触发 ask_user → CLI 收到 → 答 → 工具拿到答案）；④ WS 杀连→重连：pending approval
存活且历史回补完整；⑤ 杀 server→shell 优雅退出；⑥ 裸 `homemaster` 在 GUI/headless
两环境实测；⑦ `run --local` 逃生舱与现有语义逐项对齐。

### W3 模型 UX + `auth` 向导

1. `homemaster auth`（prompt_toolkit 问答，抄 hermes `hermes model`/openclaw onboard）：
   选模板（anthropic/openai-compatible）→ base_url/model/key → 写
   `$HOMEMASTER_HOME/config.yaml` chmod 600；headless 退化为打印 YAML 片段+路径说明。
2. `/model` picker：shell 命令 + web composer 上方下拉；数据源 = `GET /api/providers`
   已配置项 + 自由文本（cline ModelPickerWithManualEntry 形态）；选择经
   `SendMessageRequest.provider_name/model` 逐消息透传，会话级持久化由 client 侧记忆
   （web localStorage / shell 内 state），**本期不做服务端 per-session model 持久化**。
   全量 catalog（litellm 表/models.dev）按评审 YAGNI 意见砍掉，列为后续项。
3. shell `/help` 从命令注册表生成；banner 改 `importlib.metadata`；
   `prompt_toolkit` 替换 `input()`（`/` 补全+历史）。

**验收门**：`/model` 切换后发消息，`runtime_events.jsonl` 与实际 provider 调用记录的
model 一致；auth 产物 `doctor --live` 直过；`/help` 输出与新命令同步（测试断言）。

### W4 Web UI 重塑（本轮一等公民）

**目标**：打开 web 端一眼看出是"产品"而非"小组件"。分两批：纯前端批（不依赖协议扩展，
可与 W1/P0 并行先做）与协议批（依赖 W2 事件/端点）。完整证据见功能搬运矩阵 Web 部分。

**第一批（纯前端可做，搬运矩阵 Web-P0 #1-9）**：

1. **滚动契约**：跟随流式尾部 + 用户上滚释放所有权 + "回到底部"浮钮 + 新 turn 自动滚底
   （抄 `cline/.../useScrollBehavior.ts` + `MessagesArea.tsx`；dsh 有 `chat-scroll-contract.e2e.ts`
   可直接翻译成我们的 e2e）。
2. **思考折叠 + 工具分组**：`ReasoningRow` 默认折叠流式可展开；连续工具调用按回合聚合成
   `ToolGroupRenderer` 形态——每项一行摘要（图标+动作+路径），点开详情
   （抄 `cline/.../ToolGroupRenderer.tsx`）。
3. **Composer 升级**：`@`/`/` 触发分组候选菜单（↑↓高亮、Tab 确认、Esc 不毁草稿退出、
   `aria-activedescendant`；抄 `dsh ui-input-trigger` 行为规范 + cline `ContextMenu`）；
   prompt 历史 ↑↓ + 按 sessionId localStorage 草稿（opencode `history-store.ts`）；
   **IME composing 中 Enter 不发送**（dsh 规定，中文输入必需）。
4. **Toast 服务 + 可撤销操作**：全局 toast（title+desc+action），归档/删除类操作配 Undo；
   tab title/favicon 反映运行中状态（openclaw favicon runtime）。
5. **键盘契约**：先抄 openclaw `keyboard-shortcut-contract.ts` 清单落地为 `shortcuts.ts`
   （Enter 发送/Shift+Enter 换行/Esc 逐级消化/Mod+F 转录搜索/Mod+/ 快捷键参考页）。
6. **空态欢迎页**：建议任务卡片（点击直接发送）+ 最近会话继续入口（cline `WelcomeSection`）。
7. **会话侧栏升级**：行内 hover 快捷动作 + "运行中/等待批准/未读"状态点（可从现有 WS
   事件本地派生，openclaw `app-sidebar-session-narration` 模式）+ 本地标题搜索。
8. **审批卡 1.5**（纯前端部分）：点击锁存防重复提交（`seq:ts:text` ask 身份，cline
   `ActionButtons`）、参数按工具类型格式化摘要（命令带 `$`、文件带路径）。

**第二批（需 W2 协议就位）**：

9. 审批卡 2.0 完整版：倒计时+once/always/deny+`<details>` 元数据（openclaw
   `exec-approval-card.ts`）；**Composer 临时接管**——pending question/approval 时输入区
   换专用卡，结束后原草稿还原（dsh `ui-approval`+`ui-user-questions`+`ui-plan` 三件套，
   直接治"agent 反问无处安放"）。
10. **QueueDock** busy 排队消息可视化（cline `QueuedPrompts` + opencode `session-followup-dock`）。
11. **模型选择器** 落位 composer（见 W3-2，dsh `ui-model-selection` 的"进行中回合保留起始
    模型"语义直接抄）。
12. **上下文用量环** hover 明细 + 手动 compact（cline `ContextWindow`；依赖 W2 status 端点
    带 token 字段）。
13. 附件轨道（粘贴/拖拽/缩略图+lightbox，dsh `ui-attachment` 规格最完整；上传持久化需协议）。
14. 消息编辑重发（编辑→重发先做；代码回滚 checkpoint 依赖 workspace snapshot，**推迟**）。
15. **Plan/Act 切换**：composer 旁模式徽标+点击切换（web）；placeholder 与配色随 mode 变
    （cline `chat-view.tsx` 三处同步：placeholder/状态栏/配色）；依赖 W2 mode 端点。

**架构地基（和第一批同步做）**：把 `web/src/state/conversation.ts` 的事件 reducer 升级为
**turn→step→record 投影层**（dsh `ui-trajectory`/opencode `timeline/projection`/openclaw
`chat-transcript-projection` 三家共同骨架）——UI 组件只读投影不读原始事件。这是后续所有
时间线/inspector 功能的底座，现在做比重写便宜。

### W5 CLI 交互重塑（本轮一等公民）

**前提**：W2 的 thin client 落地后，shell 的协议面就是 web 的协议面——很多"CLI 功能"
本质是协议功能在两个前端的投影。第一批是输入/渲染层改造，**不等 W2，可在现有 shell 上做**。

**第一批（纯输入渲染层，prompt_toolkit 可做，搬运矩阵 CLI-S 级）**：

1. **CommandSpec 声明式注册表**：`{name, aliases, help, usage, getArgumentCompletions, run}`
   五元组表驱动 dispatch，help/补全自动生成（openclaw `commands.ts:91-180` 五元组表 +
   pi `SlashCommandContribution` registry）——替换 `interactive_shell.py` 的 `==` 分支。
2. **prompt_toolkit 输入层**：多行（Shift+Enter/Ctrl+J）、`FileHistory` 历史 ring、
   ↑↓ 进历史/回草稿 FSM（opencode `prompt.shared.ts` 纯函数可直接翻译）、Esc 清空。
3. **`@file` 路径补全**：光标前 `@` 触发，`~/`/相对/绝对路径、目录选中继续展开、
   含空格自动引号（pi `autocomplete.ts` 最完整，cline 版有 debounce+防乱序）。
4. **状态栏**（bottom toolbar）：model + ctx% + cwd/branch 一行常驻（hermes `appChrome.tsx`
   字段表裁剪到 MVP）；数据走 WS 事件 invalidate。
5. **快捷键 action→key 注册表**：禁散落 `if key==` 硬编码（pi `keybindings.ts` + opencode
   `keymap.tsx` 的 action 表模式）。
6. **`!cmd` shell 注入**：`!` 本地执行流式输出（pi `interactive-mode.ts:3326` 语义）；
   `{!cmd}` 内插进 prompt（hermes 模式，thin client 最省事——服务端拿到已展开文本）。
   输入框边框变色提示 bash mode（低成本高感知细节，pi 同款）。

**第二批（协议依赖，W2 落地后）**：

7. **`/model` picker**：bare→selector、`/model <term>` 精确匹配→miss 开预填 selector
   （pi 三层交互）；busy 时 deferred 到下轮（hermes 语义）。
8. **`/session` resume picker**：列表带 title/相对时间/末条预览；`resolveResumeSession`
   三级匹配（精确→唯一 substring→fuzzy，openclaw `tui-session-picker.ts` 照搬）。
9. **`/compact` `/status`**（走 W2 新端点）+ `/undo`（server 截断 canonical history）。
10. **审批交互 2.0**：once/session/always/deny 四选项数字键、always 二次确认、reject 可输
    理由（hermes `prompts.tsx` + opencode `footer.permission.tsx` 三态状态机）。
11. **排队/steer**：busy 时输入带 `deliverAs` 字段（pi `steer()/followUp()` 语义）。
12. **外部编辑器**：挂起 prompt_toolkit→`$EDITOR`→读回（pi `external-editor.ts`，几十行）。
13. **Plan/Act**：Tab 切换 + 状态栏徽标 + prompt placeholder 随 mode 变
    （cline `use-root-keyboard.ts:278` + `status-bar.tsx` 形态）；依赖 W2 mode 端点。

**推迟**：`/tree` `/fork`（需 session DAG 数据模型）、theme 面板。

## 4. 阶段与验收依赖（v3：UI 提升为一等工作流）

| 阶段 | 内容 | 依赖 |
|---|---|---|
| P0 | wheel 自洽 spike + 依赖审计：删 `mindmemos` dep 后 `pip install dist/*.whl` → `import homemaster` + `doctor`（full 档）在干净 venv 通；跑通前不排后续 | 无 |
| P1 | W1 记忆分档（含 doctor 模式感知、files 档入口降级、配置矩阵测试）；**验收走 compose 层 pytest + serve+curl smoke，不依赖瘦客户端** | P0 |
| P2 | W2 server 化（协议补齐→断连契约→thin client→入口重排） | 可与 P1/P3 并行 |
| P3 | **UI 第一批（本轮效果的主要来源）**：W4 第一批（web 纯前端）+ 投影层地基 + W5 第一批（CLI 输入层）+ W3-1 `auth` 向导（本地写 YAML 无 server 依赖）+ W3-3 卫生（banner/help/prompt_toolkit 已由 W5 覆盖） | 可与 P1/P2 并行 |
| P4 | **UI 第二批 + 模型切换**：W4 第二批 + W5 第二批 + W3-2 模型选择器 | P2、P3 |

## 5. 开放问题裁决（2026-10-06）

| # | 问题 | 结论 |
|---|---|---|
| 1 | shell 连不上 server 的默认行为 | **(b) 自动 spawn 临时 server 子进程**（hermes 模式，已确认）：`HOMEMASTER_SERVER` 显式设置时只连接不 spawn；`--local` 永远走进程内 compose。spawn 的生命周期/端口竞争/关闭序是实现期重点（spawn 侧写 owner，client 退出即关） |
| 2 | `enabled`→`mode` 迁移期 | **deprecation warning 一版**：`enabled:true`→映射 `full`+warning；`enabled:false`→映射 `files`+warning（原来非法值，语义放宽无破坏）；下版移除 `enabled` |
| 3 | `-p` 兼容 | **保留**（用户：主要是测试用的）。实现为 `run` 的 thin-client 糖（同一语义），不再是独立代码路径 |
| 4 | `websockets` 依赖形态 | **升正式依赖**（用户拍板），写入 `[project.dependencies]` |
| 5 | Plan/Act 模式切换 | **本轮做**（用户拍板）：复用现有 permission mode 语义（plan≈只读规划），session 级 `ui_mode` 存 server，web/CLI 提供快捷切换+徽标（cline `session.uiMode` 形态）；工具集分流落 W2 协议 |
| 6 | UI 第一批范围 | **W4 第一批 8 项全做**（用户拍板） |
| 7 | checkpoint/diff 恢复 | **推迟**（需 workspace snapshot 基建）；web 消息"编辑重发"（纯前端级）留第一批 |
| 8 | 开工顺序 | **先 P0 安装 spike**（用户拍板），P3 UI 第一批紧随其后 |

## 6. 不动的东西

typed capability 权限模型、approval v2 协议语义（只改"断连即取消"的服务端策略）、
MindMemOS 本体、gateway/benchmark 进程形态、session 持久化格式、**服务端** WS 事件投影层
（`web/event_projection.py`；web 前端 reducer→投影层的升级属 W4 范围）、
`run --local` 的 compose 路径（作为逃生舱保留）。
