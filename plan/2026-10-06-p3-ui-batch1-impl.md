# P3 UI 第一批 — 实施规格（执行者零上下文假定）

日期：2026-10-06 ｜ 上游：`plan/2026-10-06-web-first-ux-and-install.md`（W4/W5）+
证据矩阵 `docs/reports/agent-feature-borrowing-matrix-2026-10-06.md`
分支：`v35-architecture` ｜ 基线 commit：`dc92986`

**本批全部为纯前端/纯输入层改造，不依赖 server 协议扩展。**

---

## A. Web 渲染层（agent W-A1 全权负责的文件面）

Owner 文件：`web/src/App.tsx`、`web/src/state/`、`web/src/components/{ReasoningRow,ToolCallCard}*.tsx`、
`web/src/hooks/`（新建）、侧栏渲染区、空态区、`web/src/styles.css` 追加。

### A1. turn→step→record 投影层（地基，先做）

- 新建 `web/src/state/projection.ts`：把 `conversation.ts` reducer 的事件流归一成
  `Turn { userText, steps: Step[], status }`、`Step { kind: 'reasoning'|'tool_group'|'answer'|'error' }`、
  tool_group 内含 `ToolRecord[]`（name/args/status/duration/output 摘要/错误）。
- `conversation.ts` 现有 reducer 保持职责=收集原始事件；projection.ts 输出只读视图模型，
  UI 组件从此只读投影。**不改 WS 事件协议**。
- 单测：`tests` 用录制的事件序列断言投影（turn 边界、tool 分组、cancelled/failed 态）。

### A2. 滚动契约

- 新 hook `web/src/hooks/useScrollFollow.ts`：`isAtBottom` 判定（threshold ~40px）、
  用户上滚→释放跟随、回到底部浮钮（新消息到达时显示计数）、新 turn 开始强制滚底。
- 参考 `_reference/cline/webview-ui/src/components/chat/chat-view/hooks/useScrollBehavior.ts`。
- 长流式期间若用户未上滚，继续平滑跟随；展开/收起大卡片时若处于底部做 scrollTop 补偿。

### A3. 消息渲染改造

- `ReasoningRow`：默认折叠为单行 "Thought for Ns · 展开"，流式期间可点开实时看。
- 工具调用：同一 step 内连续 tool records 聚合为 `ToolGroup`（每项一行摘要：
  状态图标 + name + 主参数摘要（命令带 `$`、文件带路径），点击行展开完整 `ToolCallCard`）。
  现有 `ToolCallCard` 保留为展开态内部组件。
- 空态：替换为欢迎区——标题 + 3 个建议任务卡（点击直接发送该文本）+ 最近 3 个会话
  "继续"入口（用现有 sessions 列表数据）。参考 cline `WelcomeSection`/`HistoryPreview`。
- Failed/cancelled run 行保留现有语义，样式并入投影的 `Step.status`。

### A4. 会话侧栏升级

- 行内 hover 快捷动作（重命名 inline / 删除）；删除走 toast+undo（toast 组件由 B 提供，
  本 agent 只接入 API：`toast.show({title, action})`——若 B 未就绪先写最小 `useToast` stub）。
- 每条会话右侧状态点：running（蓝/脉冲）/ waiting-approval（黄）/ unread-done（绿点）/
  idle（无）——从 WS 事件本地派生（`event.session_id` → 最近事件类型映射），不新增协议。
- 保留现有搜索框；搜索只过滤标题（本地），保持简单。

### A5. 转录内搜索（Mod+F）

- 新组件 `TranscriptSearch.tsx`：浮层输入框，对已投影的消息建文本索引，↑↓/Enter 跳行，
  命中行高亮（CSS class），Esc 关闭并清高亮。键位挂在全局 shortcuts（B 提供注册表；
  若 B 未就绪，本 agent 用本地 keydown 实现并在注释里标注 TODO 迁册）。

---

## B. Web Composer + 全局交互（agent W-B，禁止改 App.tsx）

交付物全部是**自包含新文件**，由 W-A1（或我）在 App.tsx 接线；props 契约在本节写死。

### B1. `web/src/components/composer/ComposerPanel.tsx`

替换现有 textarea 区的自包含组件，props：

```ts
interface ComposerPanelProps {
  sessionId: string | null;
  busy: boolean;                  // run 进行中 → 显示 Stop 按钮位（回调交给父级）
  disabled?: boolean;
  onSubmit(text: string, attachments: AttachmentDraft[]): void;
  onCancel(): void;               // Stop 按钮
  slashCommands: SlashCommand[];  // 由父级注入 [{name, description}]
  resolveMentions(query: string): Promise<MentionItem[]>;  // @ 候选（本期父级传空数组即可）
}
```

- 多行自适应 textarea（1–6 行）；Enter 发送、Shift+Enter 换行；
  **IME composing 期间 Enter 不发送**（`compositionstart/end` + `keydown.isComposing`）。
- `/` 行首或空白后触发 slash 菜单：分组候选、↑↓ 高亮、Tab/Enter 确认、
  Esc 关闭且不毁草稿、`aria-activedescendant`；菜单数据源 = props.slashCommands。
- `@` 同样触发 mention 菜单（props.resolveMentions；空实现下菜单不弹）。
- 选中项插入为 chip 样式 span（`@path` 形态）或纯文本——本期纯文本即可。
- Prompt 历史：空草稿光标在首行按 ↑ 进入历史回放（`usePromptHistory` hook，
  环形去重 200 条，localStorage `hm.promptHistory` 全局表 + `hm.draft.<sessionId>`
  按会话草稿）；↓ 回到草稿位。参考 `opencode/.../prompt-input/history-store.ts`。
- 草稿持久化：输入即写 localStorage（debounce 300ms），切会话/刷新恢复。

### B2. `web/src/components/toast.tsx`

- `ToastProvider` + `useToast()`：`show({title, description?, action?: {label, onClick}, duration})`；
  底部浮层堆叠，操作按钮（Undo 用）、进出场动画、默认 5s。
- favicon 状态：导出 `useFaviconStatus(running: boolean)`——running 时 document.title 加
  `●` 前缀（最小实现；favicon 换色可后续）。

### B3. `web/src/state/shortcuts.ts`

- `registerShortcut({id, keys, handler, when?})` 注册表 + `ShortcutHelpDialog.tsx`
  （Mod+/ 打开的只读参考页，列出已注册键位）。
- 初始内置契约（抄 openclaw `keyboard-shortcut-contract.ts` 裁剪）：
  Enter 发送（textarea 内）、Esc 逐级（菜单→中断 run→关 dialog）、Mod+F 转录搜索、
  Mod+/ 帮助页、Mod+K 预留命令面板位（本期显示 "coming soon" toast）。

### B4. `web/src/components/ApprovalDialog.tsx` 增强（本文件允许 B 改）

- 点击即锁存：approved/denied 按钮一经点击全部禁用 + 显示提交中态（防双击重复提交；
  ask 身份 = `approval_id`，父级在 props 已给）。
- 参数摘要格式化：tool name 含 `shell|exec|bash` → 命令前缀 `$` 等宽展示；
  含 `file|write|edit` → 首行显示路径 + 折叠其余 args；其他→现有 JSON 块。
- once/session/always/deny 四选项需要协议支持，**本期只做 once/deny 两键的锁存**
  （always 留 placeholder disabled 按钮 + tooltip "requires server support"）。

### B5. 附件轨道骨架 `web/src/components/composer/AttachmentRail.tsx`

- `AttachmentDraft { id, name, size, mime, blobUrl }`；粘贴图片/拖拽文件入轨→64px 方图
  （图片）或 240px 卡片（文件：名+大小），hover 显示删除 ×，点图片调现有 `ImageLightbox`。
- **上传不接后端**：onSubmit 时 attachments 先经 props 传出；App 侧本期仅允许
  图片以现有 artifact 机制附带（若现有 sendMessage 不支持附件字段，则附件在提交时
  弹 toast "附件上传需要服务端支持（规划中）"并保留草稿——不允许静默丢弃）。
- 全页拖拽 overlay（`dragenter` 含 Files 才显示）。

---

## C. CLI 输入层（agent W-C，owner = `src/homemaster/cli/`）

现有基线：`interactive_shell.py` 是 `input()` + `==` 硬编码命令 + `rich_renderer.py` 流式渲染。
**本轮保持 in-process compose 路径不动**（thin client 是 W2），只换输入/渲染壳。

### C1. CommandSpec 注册表 `cli/shell_commands.py`

- `@dataclass CommandSpec { name, aliases: tuple, usage, help, arg_completer: Callable|None, run: Callable }`；
  全局 `REGISTRY: list[CommandSpec]` + `@slash_command(...)` 装饰器注册。
- 迁移现有 8 个命令（`/new` `/compact` `/status` `/events` `/doctor` `/help` `/exit` 等，
  以 `interactive_shell.py` 实际为准）到表驱动 dispatch；未知 `/x` 提示 "did you mean"。
- `_render_help()` 改为由 REGISTRY 生成（name+usage+help 三列对齐）。
- 测试：注册表完整性（help 覆盖每条命令）、alias 命中、未知命令提示。

### C2. prompt_toolkit 输入层 `cli/prompt_loop.py`

- `PromptSession`：`FileHistory(~/.homemaster/shell_history)` + `multiline` 语义
  （Enter 提交、Alt+Enter/Ctrl+J 换行；或反之前一行——以最少惊讶为准并在 help 写明）、
  ↑↓ 历史遍历 + 草稿恢复（中途编辑未发的新行按↓回草稿）、Esc 清当前输入。
- `Completer` 合并：`/` 前缀→命令名补全（带子命令 hint）；`@` 触发文件路径补全
  （光标前 `@` 起步，`~/`、相对/绝对路径、目录项选中继续展开、含空格自动引号
  `@"path"`——参考 `pi/packages/tui/src/autocomplete.ts` 行为描述）。
- 替换 `input()` 主循环；非 TTY（管道）自动退化原 `input()` 行为（CI/脚本兼容）。
- 顶部 toolbar（bottom toolbar 亦可）：`model=<当前> | ctx=<n>% | <cwd>`；
  数据源从 runtime/session 状态取，无字段则先显示 `--`（协议字段是 W2 的事）。

### C3. 快捷键 action 表 `cli/keymap.py`

- `ACTION_KEYS = {"interrupt": "c-c", "clear_input": "escape", "newline": "c-j", ...}` 常量表
  + `prompt_toolkit.KeyBindings` 绑定；后续命令内禁止散落 `if key ==` 判断。

### C4. `!cmd` shell 注入

- `!` 开头：本地 `subprocess` 流式执行，输出进 Rich panel，退出码非零标红；
  结果默认不进上下文；`{!cmd}` 内插：执行后以 stdout 替换占位符随消息发送
  （stderr 进 stderr，不进 prompt）。
- 输入行检测到 `!` 前缀时 prompt 前缀变色提示 "bash mode"（低成本实现：prompt 文本
  或样式切换即可）。

### C5. banner/版本

- `"HomeMaster V1.9"` → `importlib.metadata.version("homemaster")`，fallback `"dev"`。

---

## 验收门（每 agent 自查 + 我终审）

- **W-A1/A2**：`cd web && npm test`（现有 vitest 全绿）+ 新投影层/组件单测 +
  `npm run build` 通过；手工 smoke：`cd web && npm run dev` + `homemaster serve`
  起来后滚动/分组/菜单/审批锁存肉眼验。
- **W-C**：`pytest tests/homemaster/cli`（若存在）+ 新注册表/补全单测；
  手工：`homemaster shell` 本地跑 `/help`、`@` 补全、`!echo hi`、多行输入、历史 ↑。
- 不许动：`web/src/api/`、`src/homemaster/web/`、`src/homemaster/application/`、
  pyproject/lock（我刚提交）、任何 `compose_application` 调用方。

## 风格纪律

- 沿用现有 CSS Modules + React 18 + 无 UI 框架现状（不引入组件库——基线就是手写
  CSS，贸然引库会和 ApprovalDialog 等现有风格撕裂；设计系统留待后续）。
- 注释最少化；已有注释不许删。
- 每个组件 .tsx 配 .module.css；新文件就近放现有目录结构。
- 测试命名/断言风格对齐现有 `MemoryPage.test.tsx`、`components.test.tsx`。
