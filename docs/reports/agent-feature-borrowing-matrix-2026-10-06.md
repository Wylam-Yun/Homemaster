# Agent 可搬运功能清单（Web + CLI）

> 调研日期：2026-10-06。本地参考仓库：`~/Documents/workspace/_reference/`（pi / hermes-agent / opencode / cline / deepseek-harness / openclaw，均浅克隆只读）。
>
> 用途：把六个对标项目里**有真实源码证据**的用户可感知功能，映射到 HomeMaster 的 web/CLI 改造。所有路径均为各参考仓库相对路径。
>
> 难度三档：**纯前端可做** / **prompt_toolkit 可做**（CLI 输入渲染层）/ **需协议配合**（依赖 server 状态或 RPC）/ **架构级**（需 textual、数据模型变更或投影层重构）。

---

## 第一部分：Web 端可搬运功能

按"值得抄的程度"排序（投入产出比 × 与 HomeMaster 现状的差距）。

### P0：直接抄，纯前端为主

| # | 功能 | 解决什么问题 | 主要参考 | HomeMaster 落点 |
|---|------|--------------|----------|-----------------|
| 1 | **Composer 触发菜单**（`@` 引用 + `/` 命令候选） | 用户不知道能引用什么/输什么命令 | `cline/.../ChatTextArea.tsx` + `ContextMenu.tsx` + `SlashCommandMenu.tsx`；`dsh/packages/client/ui-input-trigger/README.md`（最完整行为规范）；`opencode/.../prompt-input/{slash-popover.tsx,context-items.tsx,files.ts}` | `web/src/App.tsx` composer → 新 `ComposerMenu.tsx` |
| 2 | **滚动契约**（跟随尾部 + "回到底部"浮钮） | 流式输出时阅读被打断/新消息看不见 | `cline/.../hooks/useScrollBehavior.ts` + `MessagesArea.tsx`；`dsh/ui-chat` + `apps/web/tests/chat-scroll-contract.e2e.ts`；`openclaw/.../chat-transcript-follow*.ts` | 对话滚动容器新 hook `useScrollFollow` |
| 3 | **思考折叠 + 工具调用分组渲染** | reasoning 刷屏、低风险工具行淹没正文 | `cline/.../{ThinkingRow,TypewriterText,ChatRow}.tsx` + `ToolGroupRenderer.tsx`；`dsh/ui-chat`；`openclaw/.../chat-transcript-projection.ts` | 现有 `ReasoningRow`/`ToolCallCard` 升级为按回合聚合 |
| 4 | **附件轨道**（粘贴/拖拽/缩略图 + Lightbox） | 截图粘贴不可见、无预览 | `dsh/ui-attachment/README.md`（最完整规格：64px 轨道、拖拽遮罩、Esc/mask 关闭 lightbox）；`opencode/.../{paste.ts,image-attachments.tsx,drag-overlay.tsx}`；`cline/.../Thumbnails.tsx` | 新 `AttachmentRail.tsx` + 现有 `ImageLightbox` 复用；上传需后端 |
| 5 | **Prompt 历史 ↑↓ + 按会话草稿持久化** | 误发/重发要重打；切会话草稿丢 | `opencode/.../prompt-input/{history-store.ts,history.ts,history.test.ts}`；`dsh/ui-conversation`（草稿含结构化 chip、跨刷新保留） | composer + `localStorage` by sessionId |
| 6 | **Toast 通知 + 可撤销操作** | 操作成败无反馈、误删无法挽回 | `opencode/.../utils/toast.tsx` + `use-session-commands.tsx`；`dsh/ui-workspace`（归档 + Undo）；`openclaw/.../control-ui-favicon-status.runtime.ts`（favicon 反映运行状态） | 新 `toast.tsx` 全局服务 |
| 7 | **键盘契约 + 快捷键参考页** | 快捷键没人发现、Esc 语义混乱 | `openclaw/ui/src/lib/keyboard-shortcut-contract.ts`（完整表：Mod+Shift+Enter=allowAlways、Mod+D=deny、Mod+F=转录搜索、Mod+/=快捷键页）；`dsh/ui-shortcuts` | 新 `shortcuts.ts` + 参考页 |
| 8 | **空态/欢迎页 + 建议任务** | 新用户面对空白输入框无引导 | `cline/.../WelcomeSection.tsx` + `HistoryPreview.tsx`（最近会话+继续） | 替换现有 empty state |
| 9 | **转录内搜索**（Mod+F） | 长会话找不回某条输出 | `openclaw/.../chat-transcript-message-index.ts` | 页内浮层，已渲染 DOM 建索引 |

**横切细节（容易漏）**：
- **IME 安全**：中文输入法 composing 中 Enter 不得发送；组合期隐藏命令 hint（dsh `ui-conversation`）。
- **Esc 级联**：菜单 → 清除引用 → 中断 run → 关 modal，逐级消化（openclaw `keyboard-shortcut-contract.ts`）。
- **Ask 锁存**：批准按钮点击立即禁用，`seq:ts:text` 作 ask 身份防重复提交（cline `ActionButtons.tsx`）。
- **Sticky 用户消息**：滚过的 user 消息吸顶，点击跳回（cline `StickyUserMessage.tsx`）。
- **原子 chip**：菜单选中插入 `@path` chip 或纯文本（dsh vs cline 两种形态）。
- **响应式断点**：openclaw 固定 400/560/640/768/900/1100/1320px 阶梯（`ui/AGENTS.md`），比自定义断点值得统一。

### P1：需后端协议配合，收益很高

| # | 功能 | 主要参考 | 备注 |
|---|------|----------|------|
| 10 | **审批卡 2.0**：倒计时 + 命令高亮 + once/always/deny | `openclaw/.../exec-approval-card.ts`（inline/modal、1s 轮询倒计时、`<details>` 元数据、session deep-link、busy/error 态、队列计数）；`cline/.../ActionButtons.tsx`（锁存）；`dsh/ui-approval`（Enter 批准/Esc 拒绝、pending-request 锁、撤回/替换的迟到响应处理） | 过期时间/always-grant/撤回事件需协议扩展 |
| 11 | **Composer 临时接管**（审批/问题/计划评审占用输入区） | `dsh/ui-approval` + `ui-user-questions` + `ui-plan` + `approval-composer.e2e.ts`；`openclaw/.../chat-question-card.ts`（单/多选+自定义、倒计时"Take time"暂停） | 结束后原草稿原样恢复；依赖 pending-interaction 事件 + ask_user 回传通道（计划 W2 已列） |
| 12 | **排队/插队消息可视化 QueueDock** | `cline/.../QueuedPrompts.tsx`（queued vs steering、逐条取消）；`opencode/.../session-followup-dock.tsx`（"Send now"/"Edit"）；`dsh/ui-conversation` | 需 server 队列语义（同 CLI #11） |
| 13 | **模型选择器**（composer 座位 + `/model` 共目录） | `dsh/ui-model-selection`（provider 分组、搜索、reasoning-effort、进行中回合保留起始模型、generation 防串写）；`cline/.../{ClineModelPicker,ReasoningEffortSelector,ModelPickerWithManualEntry}.tsx`；`opencode/.../dialog-select-model.tsx` | 单一目录源；本期范围=已配置 provider + 手动 model id（评审已砍全量 catalog） |
| 14 | **权限预设 + 风险确认 + 自动批准细粒度** | `dsh/ui-permission-presets`（只读/工作区写/全权 + "当前会话"vs"未来默认"分开 + 全权需风险确认）；`cline/.../auto-approve-menu/{AutoApproveBar,AutoApproveModal}.tsx` | 预设是入口，细粒度是展开项 |
| 15 | **上下文用量环 + 明细 + 手动 compact** | `cline/.../task-header/{TaskHeader,ContextWindow,ContextWindowSummary}.tsx`（hover 明细 input/output/cache）；`opencode/.../session-context-usage.tsx` | 需事件带 token 统计；可先用本地估算出雏形 |
| 16 | **会话列表**：搜索/标题/置顶/归档 | `dsh/ui-workspace`（pin、双击重命名、归档 undo、"先停再归档"确认、pending-interaction 状态点）；`opencode/.../home-sessions-view.tsx` + `session-title.ts`；`cline/.../HistoryView*.tsx` | 现有 session 列表升级 |
| 17 | **消息编辑重发 + Checkpoint 恢复** | `cline/.../UserMessage.tsx`（`Reset Chat`/`Reset Code` 两档，无 checkpoint 时说明+设置链接）；`opencode/.../session.revert.stage/clear`（undo/redo + 原 prompt 回填 composer） | 编辑重发纯前端可先做；代码回滚需 workspace snapshot |
| 18 | **Fork 会话**（从任意 user 消息分叉） | `opencode/.../dialog-fork.tsx`（列出 user 消息可搜索，fork 后原 prompt 回填）；`dsh/apps/web/tests/fork-mid-turn.e2e.ts` | 需按消息 id 复制会话的后端 |
| 19 | **命令面板**（命令+会话+文件一框搜） | `opencode/.../dialog-command-palette-v2.tsx`；`dsh/ui-commands` | 骨架纯前端；结果源需后端 |
| 20 | **Diff/变更文件审阅** | `cline/.../DiffEditRow.tsx`（流式 patch、Add/Delete 边条+行号）；`opencode/.../review-tab.tsx` + `changed-files` e2e | 渲染纯前端；diff 数据归属需后端 |
| 21 | **审批中心页**（历史分页 + standing grants 四态：revoked/expired/N 天后过期/永久） | `openclaw/.../approvals-page.ts` | 需审批历史持久化 |
| 22 | **后台任务 dock** | `dsh/ui-jobs`（有任务才出现、运行/完结分组、3 秒内双击确认停止） | 需 job 事件流 |
| 23 | **消息反馈** 👍/👎 | `dsh/ui-message-feedback` | 按钮纯前端，持久化需后端 |

### P2：架构级，长期收益

| # | 功能 | 主要参考 | 备注 |
|---|------|----------|------|
| 24 | **Timeline/Trajectory 投影**（turn→step→record + inspector：token/duration/IO） | `dsh/ui-trajectory`；`opencode/.../timeline/{model,projection,rows,row-reconciliation}.ts(x)`；`openclaw/.../run-inspector-*.ts` | **三家共同骨架**：WS 事件先归一成投影层，UI 只读投影——值得作为 web 重构的地基 |
| 25 | 多窗格/会话 Tab/侧栏 dock | `dsh/ui-dockkit`；`openclaw/.../chat-pane-panel-shortcuts.ts` | |
| 26 | **子代理树浏览 + 续聊**（面包屑+后代目录树、行状态点/token/时长、子会话续聊） | `dsh/ui-subagent`；`openclaw/.../app-sidebar-session-tree.ts` | 需子会话关系暴露 |
| 27 | 侧栏实时状态点/逐会话叙述行 | `openclaw/.../app-sidebar-session-narration.ts`；`dsh/ui-workspace` pendingInteraction 点 | 可从 WS 事件本地派生 |
| 28 | Composer/UI 槽位化 | `dsh/ui-conversation` + `ui-sidebar`（`sidebar.workspaces.session.menu.item` 声明式 seat） | 只学"固定座位名+可注入动作"概念，不搬实现 |

---

## 第二部分：CLI/TUI 端可搬运功能

**目标架构前提**：Python thin client（`prompt_toolkit` 输入层 + Rich 流式渲染）↔ 本地 server。协议模板 = `hermes-agent/tui_gateway/AGENTS.md`（Python backend owns session/tools/model/slash 逻辑，前端只渲染；契约先行——Pydantic 声明再生成类型；server 可反向发 request：approval/clarify/sudo/secret）。

### S 级：先抄，直接提升输入层体验

| # | 功能 | 主要参考 | HomeMaster 落点 |
|---|------|----------|-----------------|
| 1 | **Slash Command 声明式注册表**（CommandSpec 表驱动 dispatch；`getArgumentCompletions` 挂命令上；help 由表自动生成） | `openclaw/src/tui/commands.ts:91-180`（五元组表+alias hidden）；`pi/.../services/slash-commands.ts`（registry `register/replace/list/subscribe`）；`hermes/.../slash/registry.ts` | `cli/shell_commands.py` 新模块替换 `interactive_shell.py` 的 `==` 分支 |
| 2 | **`@file` 路径补全**（`~/`、目录层级展开、引号、括号内触发、fuzzy；目录选中继续展开） | `pi/packages/tui/src/autocomplete.ts`（最完整）；`cline/.../use-autocomplete.ts:23-95,210-246`（异步+debounce+防乱序、`@"path"` 引号）；`openclaw/.../tui-autocomplete.ts` | prompt_toolkit `Completer` |
| 3 | **`!command` shell 注入**（三档：`!` 进上下文、`!!` 不进、`{!cmd}` 内插进 prompt） | `pi/.../interactive-mode.ts:3326-3341,6950`（流式 append+exit code、`!` 输入框边框变色=bash mode 反馈）；`hermes/.../useSubmission.ts`（`{!}` 内插走 `shell.exec` RPC）；`opencode/.../footer.prompt.tsx:1055-1094`（`!` 持续 shell mode） | `!` 本地执行即可；`{!}` 内插最适合 thin client |
| 4 | **多行输入 + 历史 ring + draft 恢复**（↑↓ 进历史/回草稿、去重 ring=200、1–6 行弹性高、Esc 清空） | `opencode/.../prompt.shared.ts`（纯函数 FSM）；`opencode/.../footer.prompt.tsx:35-37`；`pi/.../editor.ts`（undo/kill-ring） | prompt_toolkit `FileHistory`+`multiline` |
| 5 | **快捷键 action→key 注册表**（禁硬编码 `ctrl+x` 判断；`/hotkeys` 由表生成帮助） | `pi/.../core/keybindings.ts` + `interactive-mode.ts:6780-6869`；`opencode/.../keymap.tsx` + `config/keybind.ts`（leader=`ctrl+x`、palette=`ctrl+p`） | prompt_toolkit `KeyBindings`+registry |
| 6 | **状态栏**（model+ctx%+cost+cwd/branch 常驻；按宽度优先级裁剪；数据经事件推送 invalidate） | `hermes/.../appChrome.tsx`（最完整字段表）；`pi/.../footer.ts`+`footer-data-provider.ts`；`cline/.../status-bar.tsx`（Plan/Act `(Tab)`、auto-approve `(Shift+Tab)` 徽标） | prompt_toolkit bottom toolbar；usage 数据需协议 |

### A 级：必须先扩协议，但价值高

| # | 功能 | 主要参考 | 协议需求 |
|---|------|----------|----------|
| 7 | **Selector/overlay 统一挂载点 + stale guard**（所有 picker 走一个 `openSelector`；异步 RPC 带 generation 校验） | `pi/.../interactive-mode.ts:4844-4867` `showSelector()`+`activeSelectorToken`；`hermes/.../overlayStore.ts`（approval/clarify/confirm/sessions/modelPicker 枚举）；`openclaw/.../sessionTransition` epoch + `rejectUnsafeSessionRollover` | RPC 带 session generation |
| 8 | **`/model` `/provider` 切换**（bare→selector；`/model <term>`→精确匹配→超时刷新 catalog→预填 selector；busy deferral） | `pi/.../interactive-mode.ts:5163-5218` + `model-selector.ts`（fuzzy、Enter 选中首个、persist 默认）；`hermes/.../session.ts`（idle 立即/busy deferred）；`openclaw`（`applySessionSetting{model}` RPC、`shouldForwardModelCommandToServer`） | catalog RPC + apply RPC + busy 事件 |
| 9 | **`/session` resume picker**（列表 RPC 直接带 title/相对时间/末条预览；`resolveResumeSession` = 精确 key→唯一 substring→fuzzy 三级匹配，歧义返回候选） | `pi/.../session-selector.ts`；`openclaw/.../tui-session-picker.ts`；`hermes/.../session.ts` `/sessions`（aliases switch/session/resume） | sessions 列表 RPC（已有基础） |
| 10 | **`/undo` `/retry` `/compact`**（undo 必须 server 截断 canonical history 并回传 transcript 基线；compact 回包带 usage 更新状态栏；`/compact <instructions>`；cline 双击 Esc 恢复 checkpoint 替代形态） | `hermes/.../core.ts`+`session.ts`（`session.undo`/`session.compress` 带 focus_topic）；`pi/core/agent-session.ts:2750` `compact()`（compaction_start/end 事件、扩展钩子） | compact/undo 端点 |
| 11 | **busy 输入：queue / steer / 排队面板**（steer=当前 tool call 后插入、followUp=run 后追加；发送时带 `deliverAs` 字段；排队气泡可编辑撤销） | `pi/core/agent-session.ts:2197-2248` `steer()/followUp()/clearQueue()`；`cline/.../use-root-keyboard.ts:152-226`（↑↓选中/Tab 编辑/Enter promote/Ctrl+S steer）；`hermes` `/busy queue\|steer\|interrupt` `/steer` `/queue` | submit RPC `deliverAs` + 队列事件 |
| 12 | **权限/审批交互**（once/session/always/deny 四选项+数字键；always 二次确认；reject 可输入理由；参数按 tool 类型格式化：文件带行号区间、命令带 `$`） | `hermes/.../prompts.tsx` + `tui_gateway/AGENTS.md`（server→client request 方向反转）；`opencode/.../footer.permission.tsx`（三态状态机+内嵌 diff）；`cline/.../tool-approval.tsx`；`openclaw` `/elevated` | approval request/response RPC（已有 v2 雏形，补 scope/reason/diff） |
| 13 | **plan/act 模式切换键**（Tab 切换，placeholder/状态栏徽标/配色三处同步；server 侧约束工具集） | `cline/.../chat-view.tsx` `session.uiMode` + `use-root-keyboard.ts:278-285` + `status-bar.tsx` | mode 存 server session |

### B 级：价值明确但更重

| # | 功能 | 主要参考 |
|---|------|----------|
| 14 | **thinking 展示与档位**（level 是 session 配置；流式 reasoning 块默认折叠；输入框边框色随档位变——低成本高感知） | `pi` `thinking_level_changed`→footer invalidate+边框变色；`openclaw` `/think` label↔id 双匹配 |
| 15 | **外部编辑器**（临时文件→`$EDITOR`→读回；挂起 prompt_toolkit→subprocess→恢复） | `pi/.../external-editor.ts`；hermes `Ctrl+G`；opencode `<leader>e` |
| 16 | **`/btw` 旁路提问 + `/bg` 后台任务**（不打断当前 turn；openclaw 把 `/btw` 做成 shared 文本命令→非 TUI 前端自动支持） | `hermes/.../session.ts`（`prompt.btw`/`prompt.background` RPC）；`openclaw/.../btw-inline-message.ts` |
| 17 | **会话树 `/tree` `/fork` `/clone`**（分支缩进连接线；切分支可选总结方式；前提=session 持久化为 entry DAG 非线性数组） | `pi/.../tree-selector.ts` + `interactive-mode.ts`（`/fork` 从历史 user 消息分叉） |
| 18 | **导出/分享**（本地导 JSONL 零协议；在线分享失败回退 gist） | `pi/.../session-share.ts`；hermes `/save` |
| 19 | **theme/skin**（light/dark 成对+automatic 跟随终端+实时预览+取消恢复） | `pi/.../settings-selector.ts:243-457`；hermes `/theme` |
| 20 | **`/settings` 统一设置面板**（SettingItem=`{id,label,description,currentValue,values[]}` 通用 widget） | `pi/.../settings-selector.ts:463`；hermes `/statusbar` `/verbose` |

### CLI 推荐落地顺序（调研 agent 建议）

1. CommandSpec registry + `getArgumentCompletions` + help 自动生成（#1）+ 快捷键 action 表（#5）
2. `@file`（#2）+ `!`/`{!}`（#3）+ 多行/history（#4）+ 单行状态栏（#6）
3. 协议：session generation + catalog/apply RPC + usage 事件 → `/model`（#8）`/resume`（#9）`/undo`/`/compact`（#10）
4. queue/steer（#11）→ approval RPC 增强（#12）→ plan/act（#13）
5. thinking 流（#14）→ 外部编辑器（#15）→ `/btw`/`/bg`（#16）
6. 数据模型允许后 `/tree`/`/fork`（#17）；导出（#18）、theme（#19）、settings 面板（#20）穿插

---

## 第三部分：不抄清单（明确排除）

- **pi 的零审批/yolo 哲学**：HomeMaster 有机器人物理动作，审批是产品特性。
- **dsh Cordis 全插件化**：投入产出不匹配，`application/composition/` 已是插件边界。
- **自研 TUI 框架**（opencode OpenTUI/Zig）：无对应人力；Python 走 prompt_toolkit，重度面板需求才考虑 textual。
- **openclaw gateway 特有命令**（`/goal` `/activation` `/question` 等）：依赖其特有后端；但其 **shared 文本命令模式**（命令即消息，TUI 与 IM 前端共享）值得在协议设计时参考。
- **全量模型 catalog**：评审已认定当前是装饰品，本期只做"已配置 provider + 手动 model id"。
