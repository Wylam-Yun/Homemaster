# Browser Gateway 用户指南

## 用途

`homemaster --gateway --browser` 和 `homemaster serve --browser` 为每个 run 创建独立、origin
受限的 Playwright 会话，并注册 V3.1 通用浏览器工具。它适合遵循 DOM/ARIA 语义的后台系统、
表单、表格、菜单、弹窗和常见复合控件；不加载 ALFWorld 或 Coworker 浏览器 owner，也不接管
用户已有 Chrome profile。

## 配置与启动

真实凭据和环境地址只写入 gitignored、mode-0600 的配置。先从
`config/homemaster.browser.yaml.example` 复制真实配置。Browser Web Console 保留该文件中的
`permissions.mode`；Run 32 使用 `full_auto`，普通 Web 和 ALFWorld 的既有确认模式不受影响：

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

`start_url` 必须属于 `allowed_origins`。HomeMaster 不启动或关闭目标站点。

`headless: false` 的录制/Web 回归需要完整 Chromium，不是只有 `chromium_headless_shell`。
运行前用当前项目环境执行 `.venv/bin/playwright install chromium`，并以相同 headful 配置实际启动、
打开 `start_url`、读取一个准确 DOM 控件；仅能 import Playwright 或 headless 测试通过不算预检成功。

飞书入口：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --browser \
  --config config/homemaster.yaml
```

本地 Web Console 入口：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli serve --browser \
  --config config/homemaster.browser.yaml --port 8765
```

`--browser` 与 `--alfworld` 互斥。每个 Gateway 进程固定一个环境，不按消息切换 owner。

## V3.1 操作协议

已知唯一语义目标时可以直接操作：

```json
{"target":{"role":"button","name":"确认执行","match":"exact"}}
```

目标未知、多匹配或重渲染后身份不确定时，先调用 `browser_inspect` 或 `browser_find`，再使用
返回的 `target_ref`。ref 只能在创建它的 session/tab/frame 中使用；不得猜测、拼接或跨页面复用。
恢复等级为 `exact`、`stable` 或 `reidentified`，冲突会返回 `stale_ref`，多匹配会返回
`target_ambiguous`，工具不会默认选第一个。

部分 UI 框架会把两个汉字的可访问文本暴露成带显示空格的形式，例如按钮视觉上是“确认”，DOM/AX
名称却是 `确 认`。`exact` 和 `contains` 会在 inspect、find、直接动作中一致折叠这种汉字间空白；
普通英文/数字空格仍有意义。`regex` 不做折叠，表达式必须匹配原始文本。

主要工具分组：

| 目的 | 工具 |
| --- | --- |
| 页面进入、历史和 tab | `browser_navigate`、`browser_history`、`browser_tabs` |
| DOM/AX/frame 发现 | `browser_inspect`、`browser_find` |
| 属性、正文和诊断 | `browser_read`、`browser_extract`、`browser_analyze` |
| 文本和复合表单 | `browser_fill`、`browser_type`、`browser_select`、`browser_check`、`browser_uncheck` |
| 指针、键盘和滚动 | `browser_click`、`browser_hover`、`browser_focus`、`browser_press`、`browser_scroll` |
| 文件、拖拽和图片回填 | `browser_upload`、`browser_drag`、`browser_download`、`browser_backfill` |
| 弹窗和异步结果 | `browser_dialog`、`browser_wait`、`browser_console`、`browser_network` |
| 视觉检查 | `browser_screenshot` |

`browser_eval` 默认不注册。只有 run policy 明确授予 `browser.eval` 时才出现；它只能用于 typed
工具无法表达的一般页面行为，并且仍要核对独立外部终态。普通任务、变更单和 Run 32 不依赖 eval。

readonly 不等于不可交互。Ant Design 等组件常用 readonly input 承载 combobox：这类目标应使用
`browser_select`，仍可 click/focus/press；只有 `browser_fill`、`browser_type` 和 `browser_backfill`
要求目标可编辑并会拒绝 readonly。

## 截图、上传与回填

浏览器写操作不会强制触发 observe 或截图 round trip；验证依赖结构化回执和独立 DOM/业务终态。
布局、Canvas、图表或视觉遮挡需要判断时，再显式调用 `browser_screenshot`；`annotate_refs=true`
可返回可见 ref 标注。不要连续截图代替 DOM 终态读取。

`browser_upload` 只接受 HomeMaster artifact ref。`browser_backfill` 把当前页面的一份锁定 PNG 粘贴
到明确的图片回填控件；成功要求页面接收 paste 且预览字节 SHA-256 与源 PNG 一致。普通文本框拒绝
图片时返回 `backfill_rejected`，不会伪造成功。

Dialog、popup、download 和 response/XHR 等事件必须通过对应工具在 trigger 前建立 listener。不要先
点击再调用 wait 猜测已经错过的事件。

## 从票据执行任务

消息包含自然语言要求和可读取票据即可。模型先加载 `change-ticket-executor`，再用允许的 Home 文件/
网络工具读取票据。Skill 只定义通用元流程：提取并锁定计划、执行、读取外部终态、截图、回填、确认
和按票据回滚；具体 SOP、字段、命令、顺序和回滚值必须来自票据。

`task_planner` 创建或整体替换 TODO list；`task_progress_check` 只根据已有证据增量更新状态。任务清单
不读取页面、不执行动作、不验证证据。浏览器写操作必须独占一次模型回复，但不再要求每次写前都先
inspect；唯一且可信的语义目标可直接执行。

## Run 32 回归输入

发布 V3.1 前，在全新 Web session 一次性发送以下输入；完整逐项验收断言见
`plan/V3.1/browser-tools-spec.md` 9.4：

```text
请先调用 load_skill 加载 change-ticket-executor。

然后读取本机变更单：
/home/haodong2/weilin/red_bird/hawkeye/show_data/ops_monitor_agent_demo.source.ticket.json

读取、分页、解析或搜索这份变更单时，必须且只能调用 read_file；禁止调用 terminal、cat、grep、jq、Python 或任何 shell 命令处理变更单。

严格按照 data.sop_change_step 中 is_involved_step=true 的步骤，以及每一步的 operate_description 和 operate_verified，执行完整变更。先建立任务计划，并逐步更新任务状态。

每次任务开始先通过页面的“重置环境(全部)”重置环境。所有浏览器操作只使用 browser_* 工具和页面语义目标；目标未知、多匹配或身份不确定时，用 browser_inspect/browser_find 返回的 target_ref，禁止坐标、JavaScript、CDP、后台接口或另一个浏览器会话。写操作独占一次模型回复，task_progress_check 单独调用。

文本、日期和时间使用 browser_fill；真实输入事件使用 browser_type；select/combobox 使用 browser_select；checkbox/radio/switch 使用 browser_check/browser_uncheck；按钮、链接、tab、option 使用 browser_click。

在时间范围设置完成、正式执行前、执行回显出现后、变更后资产查询与取证完成后，按需单独调用
browser_screenshot 并消费视觉结果。browser_backfill 只用于图片回填，文件使用 browser_upload。

terminal 只能执行 operate_verified 原文指定的非浏览器终态验证命令。任何前置检查、返回码或终态失败时立即停止。最终分别确认三个 involved SOP、外部执行返回码、agent_version=1.0.0、fixture-node-01 为 running/1.0.0，以及前后证据关联到准确 SOP 步骤和字段。

现在开始执行。
```

## 验收判据

每个目标分别核对，不使用 `any` 或最佳实例聚合：

1. DOM/AX/find/read/extract/action 返回成功状态，且实际 DOM、URL 或文件终态精确变化。
2. 外部命令/API 返回码成功；点击 receipt 不能替代业务成功。
3. 下载文件存在且 hash 一致，popup/tab/dialog 状态属于当前 run。
4. 每个显式请求的关键截图真正进入模型上下文，并按 tool-call ID 关联。
5. `browser_actions.jsonl`、trace 和 WebM 非空，run 结束后 session、server 和 owned process 全部关闭。
6. 未授权 provider schema 不含 `browser_eval`、旧 `observe` 或写协议的 `snapshot_id/element_id`。

Canvas、图表、反爬页面或未授权跨源内容可能只能观察而不能安全操作。Mock UI 成功不代表真实业务
系统已变更，最终仍以独立后端/文件/数据库终态为准。
