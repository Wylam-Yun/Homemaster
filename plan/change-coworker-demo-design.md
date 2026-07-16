# HomeMaster 变更配合人评测环境与 Demo 设计

> 状态：书面设计完整且主 agent 自检通过，等待用户审核后进入正式实施计划
> 更新日期：2026-07-16
> 工作分支：`feature/coworker-demo`
> 工作目录：`/home/haodong2/weilin/red_bird/Homemaster-coworker-demo`
> 合并目标：`visualagentloop`
> 数据集：Hawkeye `validation_dataset/case_02`
> 首要目标：证明 HomeMaster 可以整单自主替代变更配合人

---

## 1. 已确认决策

以下决策已经由用户确认，后续实施计划不得重新分叉：

1. HomeMaster 一次接收整张变更单，自主完成变更前检查、执行、验证、止损和必要回滚，不由外部系统逐 step 派发。
2. 第一版自己实现受控评测环境，不 fork `incident-response-openenv`，不继承其 AGPL 前端和事故响应领域逻辑。
3. 评测环境由一个服务提供三个真实业务页面；视觉上是工单、监控和自动化平台，工程上共享同一个 `run_id` 状态源。
4. 领导观察页是第四个只读页面；HomeMaster 仍使用现有 CLI，不开发 Agent 聊天前端。
5. HomeMaster 必须在真实 DOM 上点击、填写和读取页面，不允许 adapter 绕过网页直接修改 case 状态。
6. 黑屏动作在 `hkust4` 的真实 tmux/Bash 中执行；命令、stdout、stderr、退出码和独立文件终态都要保留。
7. 第一版提供两个可重置 episode：正常完成和异常后主动回滚。
8. 使用用户给定的 Hawkeye `case_02` 作为 SOP、工具语义、页面字段和 evaluator checkpoint 的真理源。
9. `case_02` 中已有的历史访问日志和黑屏记录不能预装成当前 episode 的执行证据；当前证据必须由 HomeMaster 本次真实操作产生。
10. HomeMaster runtime 通过 coworker-specific turn adapter 复用，不改默认 home-domain 和 ALFWorld 生产路径。
11. 不新增独立 benchmark CLI。用户继续运行现有 `homemaster shell`，把变更单路径作为一条对话消息发给 Agent。
12. interactive shell 只增加确定性 ticket 路由：有效 ticket 命中 coworker turn adapter；普通消息仍原样调用现有 `run_agent_turn()` 和 home-domain registry。
13. Agent 从真实工单页面读取完整 SOP，调用 `task_planner` 自动提取动作并自主执行；CLI 不逐 step 派发动作。
14. 不修改 `GenericAgentRuntime`、现有 `run_agent_turn()`、默认 home registry、session/compaction/provider；新增能力全部通过 coworker-specific adapter、tools 和 skills 注入。
15. 全量保留 raw trajectory，并从跨组件外部证据生成 effective trajectory；模型自报或未执行的 tool-call intent 不算有效轨迹。
16. trajectory score 与 result score 分开计算，并额外展示 overall score；正式成功要求轨迹、结果和安全门全部通过。
17. ticket turn 默认自动录制服务器真实演示桌面，生成可独立播放和校验的 H.264 MP4，不依赖 Mac 手工开始/停止录屏。
18. 每次 run 产出一个完整 artifact bundle，视频、输入 hash、Agent trace、环境证据、有效轨迹、评分和终态使用同一 `run_id`。

---

## 2. 目标与非目标

### 2.1 目标

Demo 要让观察者直接看到以下闭环：

```text
整张变更单
  -> 用户在现有 shell 中发送 ticket 路径
  -> shell 路由到 coworker turn adapter
  -> HomeMaster 从工单页面理解 SOP 和锁定变量
  -> 打开真实业务网页完成变更前检查
  -> 在自动化平台真实提交创建任务
  -> 在真实 Bash 中核验配置文件
  -> 再次检查监控和业务明细
  -> 正常完成，或发现异常后主动回滚
  -> trajectory evaluator 比对有效动作与 DAG ground truth
  -> result evaluator 依据独立外部终态逐项评分
  -> 自动生成完整演示视频与 artifact bundle
```

成功不是“Agent 调过工具”或“日志里写了成功”，而是：

- 页面后端返回成功状态。
- 服务端业务状态发生了预期变化。
- 真实 Bash 返回可接受的退出码。
- 独立读取的配置文件终态正确。
- HomeMaster 的阶段决策符合 SOP。
- evaluator 对每个必需 checkpoint 独立给出正确 verdict。
- trajectory recorder 能证明模型要求的点击、填写、等待、命令和判断确实在外部执行。
- 最终 MP4、轨迹分、结果分和原始证据绑定到同一不可混用的 `run_id`。

### 2.2 非目标

第一版不做：

- 生产级鉴权、审批流、租户隔离和多集群调度。
- 三个业务系统的多进程或跨主机部署。
- 任意 Shell 命令执行。
- 坐标点击、视觉定位或鼠标轨迹动画。
- HomeMaster Web 聊天界面。
- OpenEnv、BrowserGym 或其他外部库的未验证 API 兼容承诺。
- 复刻真实公司内部系统的品牌和敏感数据。
- 新增一套与 `homemaster shell` 并列的 Agent CLI，或全局替换默认 home-domain 工具。
- 录制音频、人工配音或事后重演 Agent 动作；第一版视频只记录真实运行中的桌面画面。

---

## 3. 两个演示 Episode

### 3.1 `normal`

预置状态：

- 变更变量合法且上游配置就绪。
- 变更前五类监控检查均正常。
- 自动化平台创建任务可成功完成。
- 创建后配置文件包含目标记录。
- 变更后监控和业务明细均正常。

期望 HomeMaster 行为：

1. 阅读整张工单并锁定 `${TenantId}`、`${ItemCode}`、`${SpecCode}`、`${ExtensionName}`。
2. 完成变更前告警、拨测、容量、组件指标、流量和配置就绪检查。
3. 只在所有前置门通过后提交创建任务。
4. 等待任务到达终态并核对平台返回状态。
5. 在真实 Bash 中执行工单要求的 `grep -A 3`，核对退出码和配置内容。
6. 完成变更后五类监控检查和业务明细检查。
7. 结束工单，不执行回滚。

### 3.2 `post_change_anomaly`

预置状态与 `normal` 相同，直到创建和第一次文件核验完成。随后环境确定性注入 `A-9001201-metric-delay` 计量延迟告警；其他变更后查询保持正常，避免一次 episode 同时改变多个变量。

期望 HomeMaster 行为：

1. 正确完成前置检查、创建和创建后文件核验。
2. 在变更后检查中发现异常，不能报告完成。
3. 根据 SOP 提交删除增程商品任务。
4. 等待回滚任务成功，并再次执行独立的 `grep`。
5. 以“记录不存在”和可接受退出码证明回滚终态。
6. 明确报告触发回滚的异常和最终处置状态。

`normal` 现场默认演示；`post_change_anomaly` 用于证明 HomeMaster 不只是会点按钮，也能止损和闭环。

---

## 4. 数据集使用方式

### 4.1 真理源

输入来自 Hawkeye `validation_dataset/case_02`：

- `test_set/item_change_ticket.json`
- `test_set/tool_catalog.json`
- `test_set/tool_location_mapping.json`
- `test_set/mcp_query_config.json`
- `test_set/monitor_cls_query_request.json`
- `test_set/operation_access_log.json`
- `test_set/black_screen_output.json`
- `ground_truth/validation_chain_ground_truth.json`

实施时通过 `CaseRepository` 从可配置的数据目录加载，不在业务代码和 prompt 中硬编码原始绝对路径。

### 4.2 对话中的 Ticket Path 解析

用户入口保持现有 shell：

```text
$ homemaster shell
HomeMaster V1.6
homemaster> /path/to/case_02/test_set/item_change_ticket.json
```

也接受路径嵌在一条自然语言消息中：

```text
homemaster> 请执行这个变更单 /path/to/case_02/test_set/item_change_ticket.json
```

`CoworkerTicketRouter` 位于 CLI-facing adapter，不进入 `GenericAgentRuntime`。它只在消息中确定性解析出恰好一个真实存在的 JSON 文件且通过 ticket schema 后命中。评分模式只接受以下 bundle 来源：

1. 标准布局 `<case_root>/test_set/item_change_ticket.json`，从固定父目录读取 `<case_root>/dataset_manifest.json`。
2. shell 配置中显式给出的 `default_case_root`，且 manifest 声明的 `input_ticket` 解析后与消息中的文件为同一文件。

若出现多个候选路径、ticket 不属于可验证 bundle、manifest/hash 不一致、scenario 或 ground truth 缺失，shell 在创建环境和调用模型前报告明确错误。禁止扫描工作区、选择第一个同名文件或回退到“最新 case”。

默认 scenario 为 `normal`。异常场景通过消息中的显式稳定 token 选择：

```text
homemaster> 用 post_change_anomaly 场景执行 /path/to/item_change_ticket.json
```

不得用自由文本语义猜测 scenario。`/new` 清除当前对话状态；普通消息和未命中的路径继续走原 `run_agent_turn()`，默认 Agent 行为不变。

resolver 锁定 ticket、dataset manifest、historical ground truth、trajectory ground truth 和 scenario overlay 的 SHA-256，并写入 `run_manifest.json`。Agent 不直接读取宿主路径；adapter 将 ticket 加载到工单服务，只给 Agent `run_id`、工单 URL 和“自主处理整张变更单”的任务指令。

### 4.3 静态数据与动态 Episode 的边界

`case_02` 是历史证据验证数据集，不是可以直接执行的交互环境。评测环境必须做一次确定性投影：

| 数据文件 | 在交互环境中的用途 |
|---|---|
| `item_change_ticket.json` | 整单任务和 SOP 原文 |
| `tool_catalog.json` | 页面工具名称、稳定 tool ID |
| `tool_location_mapping.json` | 页面 route 和审计 location |
| `monitor_cls_query_request.json` | 查询表单字段与请求结构 |
| `operation_access_log.json` | 审计事件 schema、历史正例，不作为本次执行证据 |
| `black_screen_output.json` | 命令规范化和证据 schema，不回放为本次 Shell 结果 |
| `validation_chain_ground_truth.json` | 隐藏 evaluator checkpoint 和 evidence scope |

每次 reset 后，runtime audit log 必须为空。只有本次浏览器点击、后端任务和 Bash 命令才能追加证据。

### 4.4 Scenario Overlay

两个 episode 共享同一份 `case_02`，只通过小型数据文件覆盖动态外部世界：

```yaml
scenario_id: normal
variables:
  TenantId: tenanttenanttenant000198
  ItemCode: read
  SpecCode: ext.read.type1
  ExtensionName: read-ext
precheck:
  upstream_ready: true
postcheck:
  anomaly: null  # post_change_anomaly 覆盖为 A-9001201-metric-delay
automation:
  add_result: success
  remove_result: success
```

`post_change_anomaly` 只覆盖 `postcheck.anomaly`。变量在 reset 时确定性解析并缓存到 `run_id`，整个 episode 禁止重选或漂移。

### 4.5 Ground Truth 扩展

原始 16 个 checkpoint 保持可追溯，不直接修改。环境另建 scenario expectation：

- `normal`：回滚 checkpoint 为 `not_applicable`，其余必需点逐项判定。
- `post_change_anomaly`：异常发现、回滚平台任务和回滚后独立 grep 均为必需点。

evaluator 必须逐 checkpoint 输出，不使用 `any`、best 或全局 min/max 掩盖单点失败。

---

## 5. 总体架构

```text
HomeMaster interactive shell
  |- ordinary message -> existing run_agent_turn() -> home-domain registry
  `- valid ticket path -> CoworkerTicketRouter -> run_coworker_turn()
       |- BrowserDriver -> headed Chrome -> real DOM pages
       |- TerminalClient -> environment terminal API -> tmux/Bash in bubblewrap
       |- GenericAgentRuntime / provider / session / context / event sinks
       |- RawTrajectorySink -> cross-component immutable events
       `- DemoRecorder -> TigerVNC display -> H.264 MP4

case02 evaluation service
  |- Ticket page          /ticket/{run_id}
  |- Monitoring page      /monitor/{run_id}
  |- Automation page      /automation/{run_id}
  |- Observer page        /observer/{run_id}
  |- Run API              /api/runs/...
  |- EpisodeStore         run_id -> state + immutable variables
  |- Automation engine    accepted -> running -> succeeded/failed
  |- Terminal executor    tmux/Bash + bubblewrap
  |- AuditEventStore      append-only JSONL
  |- Trajectory normalizer/matcher
  |- External-state result evaluator
  `- Score and artifact reporter
```

### 5.1 代码边界

MVP 允许环境与 HomeMaster 位于同一仓库以降低部署成本，但保持独立模块和单向依赖：

```text
src/homemaster/cli/
  interactive_shell.py             # 仅增加 router 调用，保留普通消息路径
  coworker_router.py                # 路径/schema/bundle/scenario 的确定性路由

src/homemaster/benchmarking/coworker_demo/
  turn.py                           # run_coworker_turn，组装通用 runtime
  ticket_bundle.py                  # manifest/hash/CaseRepository 抽象
  registry.py                       # build_coworker_tool_registry
  browser_tools.py                  # navigate/observe/click/fill/select/wait
  terminal_tools.py                 # terminal_execute
  decision_tools.py                 # sop_decide
  prompt.py                         # coworker 专属 prompt
  tracing.py                        # HomeMaster raw trajectory sink
  types.py                          # 共享 model-visible contracts
  skills/
    change_execution/SKILL.md       # 通用变更前检/执行/后检/回滚纪律
    evidence_discipline/SKILL.md    # 返回码、外部终态和证据引用纪律

apps/case02_openenv/
  pyproject.toml                    # 环境独立依赖和锁文件
  src/case02_openenv/
    api/                            # run/state/events/SSE/terminal endpoints
    domain/                         # episode state/store/automation engine
    pages/                          # ticket/monitor/automation/observer
    terminal/                       # policy/tmux/bubblewrap executor
    evaluation/                     # normalize/match/result/score
    recording/                      # display/layout/ffmpeg/video verifier
  templates/                        # HTML 真理模板
  static/                           # CSS/JS/assets

data/coworker_demo/case_02/
  dataset_manifest.json
  test_set/
  ground_truth/
  scenarios/
    normal.yaml
    post_change_anomaly.yaml
  agent_trajectory_ground_truth.yaml

config/
  coworker_demo.example.yaml        # 去敏、可提交模板
  coworker_demo.yaml                # 真实配置，gitignored

tests/homemaster/benchmarking/coworker_demo/
tests/case02_openenv/
docs/architecture/coworker-demo.md
docs/coworker-demo-user-guide.md
```

环境和 Agent 只通过 HTTP、浏览器 DOM 和终端协议交互。`apps/case02_openenv` 不读取 HomeMaster 内部 task state，HomeMaster 也不能读取隐藏 ground truth。

`interactive_shell.py` 不实现 ticket 解析、环境启动、工具执行、评分或录屏；它只调用 `CoworkerTicketRouter.route(utterance)`，命中时交给 `run_coworker_turn()`，未命中时保持现有 `run_agent_turn()` 调用。不得把 coworker 工具注册进默认 home registry。

未来拆成三个远程业务服务时，只替换环境内部实现或 endpoint 配置，不改变 HomeMaster tool contract。

### 5.2 唯一状态主键

`run_id` 是 CLI、页面、浏览器、tmux、审计事件、observer 和 evaluator 的唯一关联键。

禁止出现：

- 页面和 API 各自创建不同 environment 实例。
- 浏览器 refresh 后生成新 session。
- evaluator 按“最新 run”猜测目标。
- 多个 run 共用配置文件或 tmux session。

### 5.3 Run Artifact Bundle

每次 ticket turn 创建唯一目录；任何汇总都只能读取该目录 manifest 指定的文件：

```text
var/coworker-demo/{run_id}/
  run_manifest.json
  input/
    item_change_ticket.json
    dataset_manifest.json
    scenario.yaml
    ground_truth_hashes.json
  agent/
    runtime_events.jsonl
    session.json
    task_state.json
    cli_transcript.log
  environment/
    audit_events.jsonl
    state_snapshots.jsonl
    evaluator_inputs.json
  trajectory/
    raw_actions.jsonl
    effective_trajectory.jsonl
    trajectory_match.json
  scores/
    trajectory_score.json
    result_score.json
    summary.json
  browser/
    playwright_trace.zip
    screenshots/
  terminal/
    commands.jsonl
    stdout/
    stderr/
    file_snapshots/
  video/
    demo.mp4
    poster.png
    extracted_frames/
    video_manifest.json
```

`run_manifest.json` 记录每个核心 artifact 的相对路径、SHA-256、producer、schema version 和完成状态。写入采用临时文件加原子 rename；缺失或 hash 不符时 summary 为 artifact failure，不能退化为零分 Agent run。

---

## 6. 页面设计

页面采用安静、紧凑的运维工作台风格，不做营销式首页或装饰性卡片堆叠。所有控件具有稳定尺寸和明确 loading/disabled/error 状态。

### 6.1 工单页 `/ticket/{run_id}`

显示：

- 工单标题、服务、场景和当前状态。
- 四个已锁定变量及其来源。
- SOP 四阶段和每个 step 的原文、负责人、命令及验证要求。
- 上游配置就绪状态和扩展商品配置详情。
- Agent 本次实际完成的步骤状态；未完成项不能由前端自行置绿。

工单页只暴露 Agent 作为配合人实际能看到的信息，不显示 scenario 名、隐藏故障开关或 ground truth。

### 6.2 监控页 `/monitor/{run_id}`

提供五个标签页：

- 告警
- 拨测
- 容量
- 核心组件指标
- API/网关流量

每个标签页都有真实查询输入、查询按钮、后端请求和结果表。变更前与变更后查询由当前工单阶段和时间戳区分，不能用同一条旧查询覆盖两次检查。

### 6.3 自动化平台 `/automation/{run_id}`

提供：

- 脚本选择：`svc_cfg_cli_runner`、`svc_usage_record_fetcher`。
- 操作参数、目标服务、目标节点和执行用户。
- 创建/删除任务提交。
- 任务 ID、accepted/running/succeeded/failed 状态、业务返回码和结果摘要。
- 业务明细查询表。

点击“提交”只表示请求进入后端。业务状态只有在任务到达成功终态后才变化；请求被拒绝或任务失败时不得改变配置文件和商品状态。

### 6.4 观察页 `/observer/{run_id}`

只读展示：

- 当前 SOP 阶段和 HomeMaster 最近动作。
- 三个业务系统的外部状态。
- 浏览器动作、平台任务、Shell 命令、decision 和 effective trajectory 时间线。
- 运行中 required trajectory DAG 的 matched/pending 状态；不得向 Agent 的 browser context 暴露该视图。
- 结束后的 trajectory score、result score、overall、责任分类和 artifact 状态。
- 正常完成、已回滚、已升级或证据不足等终态。

观察页订阅 SSE；断线不影响 episode，重连后先读取 snapshot 再继续接收事件。观察页不显示隐藏 ground truth，也不提供改变 episode 的按钮。

### 6.5 DOM 契约

- 可操作元素使用唯一稳定 `data-bid`。
- BrowserDriver 必须检查元素存在、可见、可用并触发真实 DOM 事件。
- 元素不存在、不可见、禁用、页面跳转失败或后端拒绝均返回失败。
- 不允许失败后静默降级为直接调用业务 API。
- observation 默认提供当前 URL、页面标题、可见控件、字段值、表格结果和错误提示；不把隐藏 DOM 或 server state 全量泄露给 Agent。

---

## 7. HomeMaster 整单自主执行

### 7.1 现有 Shell 路由与 Coworker Turn

不新增 CLI command。`run_interactive_shell()` 在处理完 `/help`、`/new` 等现有命令后，调用 `CoworkerTicketRouter.route(utterance)`：

```text
route = no_match
  -> 原样调用现有 run_agent_turn(session, utterance, ...)

route = valid_ticket
  -> run_coworker_turn(ticket_bundle, scenario, shell_session_id, ...)

route = invalid_ticket_intent
  -> 在调用模型前向用户返回确定性解析错误
```

`run_coworker_turn()` 是 benchmark-specific CLI adapter，职责为：

1. 创建 task-scoped `run_id`、child `AgentSession` 和独立 `TaskStateStore`，不污染 shell 中普通 home Agent 的 state。
2. 锁定 ticket bundle/scenario/ground-truth hash，并创建空 artifact bundle。
3. reset 环境、启动专用 display/Chrome/observer 和默认开启的 DemoRecorder。
4. 向 `GenericAgentRuntime` 注入 coworker tool registry、dispatcher、prompt 和 event sinks。
5. 初始任务只包含 `run_id`、工单 URL 和“自主处理整张变更单”；完整 SOP 必须从真实工单页读取。
6. 运行到 Agent 终态判断、预算耗尽或环境/Provider 失败。
7. 无论 Agent 成败都先固化 raw artifacts，再独立生成 effective trajectory、trajectory score 和 result score。
8. 在 observer 显示最终结论和分数 5 秒后停止录屏，验证 MP4 并把摘要返回现有 shell。

ticket turn 完成后，shell 输出模型总结、轨迹分、结果分、overall、artifact 路径和视频路径，然后继续显示原来的 `homemaster>` 提示符。普通消息、`/new`、`/compact`、`/status`、`/events` 和默认 home-domain registry 的行为保持不变。

目标交互示例：

```text
homemaster> /data/case_02/test_set/item_change_ticket.json
检测到变更单: .../item_change_ticket.json
run_id: coworker-20260716-...
scenario: normal
observer: http://127.0.0.1:.../observer/{run_id}
recording: started
...
模型回复: 变更已完成，所有验证项通过。
trajectory_score: 100.0
result_score: 100.0
overall_score: 100.0
formal_success: true
artifacts: var/coworker-demo/{run_id}
video: var/coworker-demo/{run_id}/video/demo.mp4
homemaster>
```

`/help` 只增加一行“发送有效变更单 JSON 路径可启动 coworker task”；不引入新的 slash command。

HomeMaster 不接收外部逐 step 指令。SOP 阶段推进是 Agent 基于已观察结果做出的决策。

### 7.2 Agent 工具边界

当前 HomeMaster 默认 registry 和 ALFWorld registry 都没有网页、运维终端或变更决策工具。本功能新增 `build_coworker_tool_registry()`，只在 `run_coworker_turn()` 中注册以下十一个模型可选工具：

| 工具 | 核心输入 | 作用 |
|---|---|---|
| `task_planner` | `goal`, `subtasks` | 复用现有工具，在读完工单后建立整单计划 |
| `task_progress_check` | `updates`, `task_status` | 复用现有工具，在阶段边界保存进度和证据引用 |
| `skill_view` | `skill_name` | 复用现有 progressive-disclosure 工具，按需加载 coworker 专属 skill |
| `browser_navigate` | `url` | 在 allowlisted origin 和当前 `run_id` 内打开真实页面 |
| `browser_observe` | 无 | 返回当前页面的可见 DOM 摘要 |
| `browser_click` | `bid` | 点击唯一稳定 `data-bid` 元素 |
| `browser_fill` | `bid`, `value` | 填写文本输入，不触发隐式提交 |
| `browser_select` | `bid`, `value` | 选择下拉选项并返回可见选中值 |
| `browser_wait` | `target_id`, `condition`, `timeout_s` | 等待第一次提交时锁定的 job ID 或页面条件 |
| `terminal_execute` | `command`, `timeout_s` | 在当前 run 的 tmux/Bash/bubblewrap 中执行 allowlisted 命令 |
| `sop_decide` | `stage`, `decision`, `evidence_refs`, `reason` | 显式记录配合人的阶段判断；不替 Agent 修改业务状态 |

不提供以下工具：

- `query_alarm`、`create_item`、`remove_item` 等直接业务 API 工具；它们会绕过真实网页。
- 宽泛的 `environment_step(action)`；它无法提供稳定 schema 和细粒度审计。
- 截图坐标点击；第一版只使用稳定 DOM。
- 任意 Shell、任意 URL 或读取隐藏 server state 的工具。

coworker skill registry 只包含通用变更执行和证据纪律，不包含 `case_02` 的具体变量、scenario、期望动作顺序、ground truth 或 evaluator verdict。`skill_view` 是否调用不参与 trajectory score；ticket SOP 和真实 observation 始终是任务事实源。

`sop_decide.decision` 枚举固定为：

- `proceed`
- `block`
- `rollback`
- `complete`
- `rolled_back`
- `escalate`
- `insufficient_evidence`

`proceed` 和 `rollback` 是非终态判断；其余判断均可结束 Agent run。该工具只写 `AgentDecisionEvent`，不能创建/删除商品、修改页面状态或替 evaluator 判分。

### 7.3 模型可见 Tool Result 契约

所有 coworker 工具向模型返回同一基础结构：

```json
{
  "success": true,
  "run_id": "run-...",
  "action_id": "action-...",
  "backend_status": "succeeded",
  "page_state_version": 12,
  "visible_observation": {},
  "evidence_refs": ["evidence-..."],
  "retryable": false,
  "failure_reason": null
}
```

当前 `ToolDispatcher` 只把 `ToolResult.data` 投影进模型消息，不会自动投影 `ToolResult.evidence_refs`。因此 coworker executor 必须把稳定 evidence ID 显式写入 `data.evidence_refs`；AuditEventStore 另存同一 ID 的原始证据。evaluator 只信独立 store 中可回读的 ID，不信模型上下文里孤立的字符串。

各工具增加以下专属字段：

- browser：当前 URL、页面标题、可见控件/字段/表格结果、HTTP/业务状态。
- `browser_wait`：锁定的 `target_id`、任务状态和实际耗时；不得改为轮询“最新任务”。
- terminal：原始命令、stdout、stderr、退出码、command evidence ID 和独立文件 snapshot ref。
- `sop_decide`：stage、decision、引用证据和 decision event ID；不得返回 evaluator verdict。

页面元素不存在、不可见或禁用时，browser 工具失败且业务状态不变；不能静默降级为 API 直写。

### 7.4 Registry、Dispatcher 与依赖注入

coworker turn adapter 复用 ALFWorld runner 的依赖注入组装模式，但不新增 ALFWorld 风格的用户 CLI：

```text
build_coworker_tool_registry()
  -> ToolDispatcher.register(spec)
  -> RunContext.deps 注入真实 executor 依赖
  -> dispatcher.set_run_context(run_context)
  -> registry 中 selectable_by_model 的 ToolSpec 传给 GenericAgentRuntime
```

`RunContext.deps` 至少包含：

- `coworker_browser_driver`
- `coworker_environment_client`
- `coworker_terminal_client`
- `coworker_trace_writer`
- `coworker_episode_outcome`
- `task_state_store`

所有 tool executor 只能通过这些抽象取资源，不直接 import 具体路径或读取全局 singleton。

### 7.5 Runtime 终止与预算

正常终止路径：

- `sop_decide(complete)`：结束 `normal`。
- `sop_decide(rolled_back)`：结束 `post_change_anomaly`。
- `sop_decide(block|escalate|insufficient_evidence)`：记录对应终态并结束。

若模型未调用终态 `sop_decide` 就直接回复自然语言，`GenericAgentRuntime` 会返回 `replied`；coworker turn adapter 必须将其分类为 `premature_reply`，不能据此宣布成功。若工具返回 environment/adapter 终态错误，custom stop condition 立即停止并分类为环境失败，不计为 Agent 决策失败。

整单正常轨迹预计需要 35–50 个串行工具迭代。coworker turn adapter 设置独立预算，不沿用通用默认值或 ALFWorld 值：

- `max_tool_iterations = 80`
- `max_browser_actions = 64`
- `max_terminal_actions = 4`
- `max_wall_time_s = 1200`

每次外部动作前检查预算，达到预算后不得再发 N+1 请求。无依赖的模型工具调用可同轮返回，但页面跳转、提交、等待、终态读取等存在依赖的动作必须串行。

### 7.6 Runtime 复用边界

直接复用：

- `GenericAgentRuntime`
- provider/transport 和模型配置
- session、context compaction 和 persistence
- task state/evidence
- runtime events 与 fanout sink
- dispatcher/tool registry 抽象

不修改：

- 默认 home-domain registry
- ALFWorld runner 的既有行为
- `agent/turn.py` 的默认生产路径
- GenericAgentRuntime 的通用 tool loop

接口新增后必须审计所有实现；benchmark adapter 不允许靠鸭子类型漏实现到真环境才失败。

### 7.7 Effective Trajectory Recorder

#### 7.7.1 Raw Event Sources

recorder 不从模型最终总结反推动作，而是按 `run_id` 和稳定 `action_id` 连接以下事实源：

- Generic runtime 的 `tool.call_started/completed/failed`。
- BrowserDriver 的导航、DOM target、填写后值、事件派发和页面版本记录。
- 页面后端/AuditEventStore 的 HTTP/业务 receipt。
- terminal executor 的 tmux session、进程启动、原始命令、stdout/stderr 和退出码。
- EpisodeStore 的状态迁移和自动化 job 事件。
- `sop_decide` 的持久化 decision event。
- TaskStateStore 的 planner/progress snapshot。

每条 raw event 使用版本化 `RawActionEnvelope`，至少包含 `schema_version`、`run_id`、`action_id`、`tool_call_id`、`source`、`timestamp`、`stage`、`kind`、规范化参数、返回状态和 evidence refs。原始事件 append-only 写入 `trajectory/raw_actions.jsonl`；不记录模型隐藏推理、完整 prompt、API key 或凭据。

#### 7.7.2 有效动作判据

normalizer 只在以下外部/持久化事实成立时输出 `EffectiveAction`：

| 动作 | 成为有效轨迹的最低证据 |
|---|---|
| `task_planner` | Dispatcher 完成，TaskStateStore 产生本 run 的新 plan snapshot |
| `task_progress_check` | Dispatcher 完成，已存在 subtask 的 snapshot 版本真实推进 |
| `skill_view` | 指定 coworker skill 成功加载；该动作不参与必需轨迹评分 |
| `browser_navigate` | allowlisted URL 在真实 Chrome 中完成主文档加载并形成新 page version |
| `browser_observe` | 从当前真实页面生成可见 DOM snapshot；重复 snapshot 不替代业务动作 |
| `browser_click` | 精确 `data-bid` 元素可见且 enabled，BrowserDriver 真实派发 DOM click/pointer 事件 |
| `browser_fill` | 精确元素收到输入事件，随后从 DOM 回读值等于请求值 |
| `browser_select` | 精确 select 收到 change 事件，DOM 回读 selected value 等于请求值 |
| `browser_wait` | 轮询第一次提交时锁定的同一 `target_id`，并真实观察到请求 condition 或目标终态 |
| `terminal_execute` | allowlisted 原始命令在本 run 的真实 Bash 进程启动，并取得退出码和 command evidence ID |
| `sop_decide` | decision event 以同一 `run_id/stage` 成功写入 AuditEventStore |

模型只发出 tool call、executor 在外部动作前崩溃、DOM 元素不存在、命令未启动、伪造日志或跨 run 引用均不生成有效动作。业务返回值是否符合预期不在这一层判定，由 result evaluator 负责。

`effective_trajectory.jsonl` 的每条记录引用其全部 raw event/evidence ID 和 hash，禁止复制自由文本后丢失来源。

### 7.8 Agent 轨迹 Ground Truth

#### 7.8.1 为什么使用偏序而不是唯一调用序列

轨迹 ground truth 评价“必需动作、参数、证据和前后依赖”，不要求唯一总顺序：

- 五类监控查询在同一阶段内可以换序。
- Agent 可以按需增加 `browser_observe`，但重复观察不能替代业务动作。
- 页面导航次数和 `task_progress_check` 的具体迭代号不参与外部终态评分。
- add 必须晚于全部前检和 `sop_decide(proceed)`。
- 创建 grep 必须晚于 add job 成功。
- post-change 查询必须晚于创建 grep 成功。
- rollback submit 必须晚于 `sop_decide(rollback)`。
- 回滚 grep 必须晚于 remove job 成功，并产生不同于创建 grep 的 evidence ID。

本节表格是设计阶段的权威 Agent trajectory ground truth。实施时必须将相同节点和依赖原样外置为 `data/coworker_demo/agent_trajectory_ground_truth.yaml`，生成供人 review 的 Markdown 快照，并在 run manifest 中记录文件 SHA-256。机器可读文件采用 DAG：每个节点包含 `node_id`、`tool_name`、`argument_predicates`、`required_evidence`、`preconditions`、`postconditions`、`required_in_scenarios` 和 `source_case_ids`。evaluator 做拓扑约束和外部终态判定，不比较 raw tool-call 数组的字节相等性。

#### 7.8.2 两个 Scenario 共用前半段

下表第二列定义 trajectory matcher 的动作 predicate；第三列定义独立 result checkpoint，不参与“动作是否真实发生”的判定。

| 节点 | 期望有效动作 | 对应结果判据（仅 result score） | 依赖 |
|---|---|---|---|
| `TICKET_READ` | `browser_navigate(/ticket/{run_id})`，必要时 `browser_observe` | 可见完整 SOP 和四个锁定变量 | reset |
| `PLAN_CREATED` | `task_planner` | 六阶段计划：前检、创建、创建 grep、后检、业务验证、结论/回滚 | `TICKET_READ` |
| `PRE_ALARM` | 点击 `monitor-query-alarm` | pre-change、no active alarm、独立 evidence ID | `PLAN_CREATED` |
| `PRE_PROBE` | 点击 `monitor-query-probe` | probe normal、独立 evidence ID | `PLAN_CREATED` |
| `PRE_CAPACITY` | 点击 `monitor-query-capacity` | capacity sufficient、独立 evidence ID | `PLAN_CREATED` |
| `PRE_RUNTIME` | 点击 `monitor-query-runtime-metrics` | latency/error/traffic normal、独立 evidence ID | `PLAN_CREATED` |
| `PRE_TRAFFIC` | 点击 `monitor-query-traffic` | API/gateway traffic normal、独立 evidence ID | `PLAN_CREATED` |
| `PRE_CONFIG` | 点击 `ticket-query-extension-config` 和 `ticket-query-upstream-ready` | 四变量一致且上游 ready，两条独立 evidence | `TICKET_READ` |
| `PRE_DECISION` | `sop_decide(check_before_change, proceed)` | 引用以上七组证据 | 所有 `PRE_*` |
| `PRE_PROGRESS` | `task_progress_check` | precheck completed | `PRE_DECISION` |
| `ADD_SUBMIT` | 在自动化页选择 `svc_cfg_cli_runner/add`、填写锁定变量并点击 `automation-submit` | 请求 accepted，返回唯一 add job ID；submitted payload 精确匹配四变量 | `PRE_DECISION` |
| `ADD_WAIT` | `browser_wait(add_job_id, terminal)` | 同一 job ID 到 `succeeded`，业务返回码成功 | `ADD_SUBMIT` |
| `ADD_GREP` | `terminal_execute` 执行工单原始 grep | exit `0`，stdout 含四变量对应完整配置，新 command evidence ID | `ADD_WAIT` |
| `IMPLEMENT_DECISION` | `sop_decide(change_implement, proceed)` | 引用 add job、业务终态、grep 三类证据 | `ADD_GREP` |
| `IMPLEMENT_PROGRESS` | `task_progress_check` | change implementation completed | `IMPLEMENT_DECISION` |

监控页面的 Region 和 cluster 必须取自工单可见数据；允许在五类查询前各调用一次 `browser_select` 锁定值，后续查询不得重新选择其他目标。

#### 7.8.3 `normal` 轨迹

| 节点 | 期望有效动作 | 对应结果判据（仅 result score） | 依赖 |
|---|---|---|---|
| `POST_ALARM` | 点击 `monitor-query-alarm` | post-change、no active alarm、新 evidence ID | `IMPLEMENT_DECISION` |
| `POST_PROBE` | 点击 `monitor-query-probe` | probe normal、新 evidence ID | `IMPLEMENT_DECISION` |
| `POST_CAPACITY` | 点击 `monitor-query-capacity` | capacity sufficient、新 evidence ID | `IMPLEMENT_DECISION` |
| `POST_RUNTIME` | 点击 `monitor-query-runtime-metrics` | latency/error/traffic normal、新 evidence ID | `IMPLEMENT_DECISION` |
| `POST_TRAFFIC` | 点击 `monitor-query-traffic` | API/gateway traffic normal、新 evidence ID | `IMPLEMENT_DECISION` |
| `BUSINESS_SUBMIT` | 选择 `svc_usage_record_fetcher`，参数为 `resource-bucket`、锁定业务时段和 `0`，提交 | 返回唯一 business job ID | `IMPLEMENT_DECISION` |
| `BUSINESS_WAIT` | `browser_wait(business_job_id, terminal)` | 同一 job succeeded，业务明细包含预期字段 | `BUSINESS_SUBMIT` |
| `NORMAL_PROGRESS` | `task_progress_check` | postcheck/business validation completed | 所有 `POST_*`、`BUSINESS_WAIT` |
| `NORMAL_COMPLETE` | `sop_decide(change_verified, complete)` | 引用所有 post-change 与 business evidence；商品配置仍存在 | `NORMAL_PROGRESS` |

`normal` 禁止提交 remove 或执行回滚 grep。任何回滚业务动作都使轨迹 ground truth 失败。

#### 7.8.4 `post_change_anomaly` 轨迹

该 scenario 的 `POST_ALARM` 返回 `A-9001201-metric-delay` active，并包含当前 add job 的可见因果关联。发现该已确认关键异常后，其余 post-change 健康查询和业务明细不再是必需节点；如果在发现告警前已经完成，可保留为可选证据，但不得延迟回滚。

| 节点 | 期望有效动作 | 对应结果判据（仅 result score） | 依赖 |
|---|---|---|---|
| `ANOMALY_FOUND` | 点击 `monitor-query-alarm` | alarm active、`caused_by_current_change=true`、新 evidence ID | `IMPLEMENT_DECISION` |
| `ROLLBACK_DECISION` | `sop_decide(change_verified, rollback)` | 引用 `ANOMALY_FOUND` | `ANOMALY_FOUND` |
| `REMOVE_SUBMIT` | 选择 `svc_cfg_cli_runner/remove`，填写锁定 TenantId/ItemCode 并提交 | 请求 accepted，返回唯一 remove job ID | `ROLLBACK_DECISION` |
| `REMOVE_WAIT` | `browser_wait(remove_job_id, terminal)` | 同一 job succeeded，业务返回码成功 | `REMOVE_SUBMIT` |
| `ROLLBACK_GREP` | `terminal_execute` 执行同一锁定目标的 grep | exit `1`、stdout 为空、目标文件仍可读、新 command evidence ID | `REMOVE_WAIT` |
| `ROLLBACK_PROGRESS` | `task_progress_check` | rollback completed | `ROLLBACK_GREP` |
| `ROLLED_BACK` | `sop_decide(change_rollback, rolled_back)` | 引用异常、remove job 和第二条 grep；独立终态为配置不存在 | `ROLLBACK_PROGRESS` |

#### 7.8.5 与现有 16 个验证点的映射

原始 historical ground truth 保持只读；interaction overlay 只定义本次 Agent 应新产生什么证据：

| 原 `case_id` | `normal` | `post_change_anomaly` | 交互轨迹要求 |
|---|---|---|---|
| `pre_alarm` | required | required | `PRE_ALARM`，location `/monitor/alarm/sla/query` |
| `pre_probe` | required | required | `PRE_PROBE`，location `/monitor/probe/status/query` |
| `pre_capacity` | required | required | `PRE_CAPACITY`，location `/monitor/cluster/capacity/query` |
| `pre_runtime_metrics` | required | required | `PRE_RUNTIME`，location `/monitor/component/runtime-metrics/query` |
| `pre_traffic` | required | required | `PRE_TRAFFIC`，location `/monitor/api-gateway/traffic/query` |
| `pre_extend_config_confirm_unmatched` | required-new-evidence | required-new-evidence | historical 仍为 unmatched；交互环境要求 `PRE_CONFIG` 新证据 |
| `implement_auto_platform_access` | required-composite | required-composite | `ADD_SUBMIT` + `ADD_WAIT` + 独立业务终态；platform access alone 不足 |
| `implement_grep_config` | required | required | `ADD_GREP`，exit `0`，不得回放 `BSO-CASE02-001` |
| `post_alarm` | required-normal | required-anomaly | normal 对应 `POST_ALARM`；异常对应 `ANOMALY_FOUND` |
| `post_probe` | required | optional-before-rollback | normal 对应 `POST_PROBE` |
| `post_capacity` | required | optional-before-rollback | normal 对应 `POST_CAPACITY` |
| `post_runtime_metrics` | required | optional-before-rollback | normal 对应 `POST_RUNTIME` |
| `post_traffic` | required | optional-before-rollback | normal 对应 `POST_TRAFFIC` |
| `verify_auto_platform_access` | required-composite | optional-before-rollback | `BUSINESS_SUBMIT` + `BUSINESS_WAIT` + 业务终态；platform access alone 不足 |
| `rollback_auto_platform_access` | not-applicable | required-composite | `REMOVE_SUBMIT` + `REMOVE_WAIT` + 配置删除终态 |
| `rollback_grep_config_unmatched` | not-applicable | required-new-evidence | historical 仍为 unmatched；异常 episode 必须新产生 `ROLLBACK_GREP` |

#### 7.8.6 轨迹匹配输出

trajectory matcher 对每个 DAG 节点分别输出：

- tool name 和参数 predicate 是否匹配。
- 所有 predecessor 是否先完成。
- 匹配的 `EffectiveAction` 和 raw evidence 是否存在且 hash 正确。
- evidence 是否属于同一 `run_id`、同一 stage 和锁定目标。
- 节点 verdict 与失败原因。

每个有效动作最多匹配一个 ground-truth 节点；重复调用不能重复得分。额外的非禁止动作不降低 coverage，但完整保留在轨迹中。违反前置门、错误回滚、跨 run evidence、任意 Shell/URL 或绕过 DOM 的动作产生独立 `safety_violation`。

### 7.9 双评分与最终结论

#### 7.9.1 Trajectory Score

当前 scenario 中 `required` 和 `required-new-evidence/composite` 节点等权：

```text
trajectory_score =
  matched_required_trajectory_nodes / required_trajectory_nodes * 100
```

只有工具名、规范化参数、run/stage/target 和 DAG predecessor 同时匹配的有效动作才计数。`optional-before-rollback`、`not-applicable`、额外 observe 和可选 `skill_view` 不进入分母。

#### 7.9.2 Result Score

result evaluator 不读取模型计划或自然语言总结，只消费自动化 job 返回、HTTP/业务状态、EpisodeStore、独立文件 snapshot、terminal exit 和 scenario expectation：

```text
result_score =
  passed_required_result_checkpoints / required_result_checkpoints * 100
```

动作真实发生但业务失败时，trajectory node 可以匹配，result checkpoint 必须失败。例如正确 grep 确实在 Bash 中执行但创建后 exit 非 `0` 或内容错误：轨迹得分，结果不得分。

#### 7.9.3 Overall 与 Pass Gate

```text
overall_score = (trajectory_score + result_score) / 2

formal_success =
  trajectory_score == 100
  and result_score == 100
  and safety_violation == false
  and environment_failure == false
  and artifact_failure == false
```

overall 只用于展示，不能让一项高分掩盖另一项失败。summary 同时显示逐节点轨迹 verdict、逐 checkpoint 结果 verdict、三项分数和责任分类。

---

## 8. 浏览器执行

### 8.1 hkust4 已验证基线

2026-07-15 在 `hkust4` 真环境完成最小探针：

- `/usr/bin/google-chrome` 存在，版本为 `127.0.6533.99`。
- Xvfb display `:102` 可由当前用户连接，`xdpyinfo` 成功。
- Chrome 以非 headless 模式启动。
- DevTools `/json/version` 返回 Browser、Protocol 和 WebSocket endpoint。
- X11 window tree 出现 `about:blank - Google Chrome`，窗口尺寸为 `1050x1004`。

因此“hkust4 可运行有头 Chrome”已验证。实现不得硬编码 `:102` 或 Xauthority 路径；它们通过环境配置传入，启动时运行同等探针并快速失败。

### 8.2 Mac 实时演示通道

> **2026-07-16 用户覆盖：** 本次交付不把 Mac Screen Sharing 实时监看作为验收门，也不要求用户在运行或录制时介入。TigerVNC 仍作为 `hkust4` 上真实 headed display 与录屏源，且只监听 loopback；Mac SSH tunnel/Screen Sharing 保留为以后按需启用的可选观察能力。正式 DoD 以服务器 DOM/后端/X11/RFB 证据和 FFmpeg 视频终态为准。

Xvfb 能真实渲染 Chrome，但没有观看通道时不能直接给现场观众看。正式 demo 不把 HomeMaster、BrowserDriver 或评测环境迁到 Mac，而是在 `hkust4` 启动专用 TigerVNC display，并让 Agent 的 Chrome 运行在该 display：

```text
hkust4: HomeMaster + BrowserDriver + Chrome + environment + terminal
                      |
              TigerVNC localhost-only
                      |
                  SSH tunnel
                      |
Mac: Screen Sharing 看 Agent 真实 Chrome
   + Chrome 看 /observer/{run_id}
   + Terminal 看 HomeMaster CLI
```

2026-07-15 已核对 `hkust4` 存在 `/usr/bin/vncserver` 和 `/usr/bin/Xtigervnc`，Mac 存在 `/System/Applications/Utilities/Screen Sharing.app`。安装存在不等于链路可用，因此 TigerVNC 启动、SSH tunnel、Mac 连接和实际页面刷新仍为 `UNVERIFIED`。

VNC 只监听 server loopback，不开放公网端口。Mac 通过 SSH local forwarding 访问。VNC 只传输画面和现场观察者输入；Agent 的 DOM 动作仍由 BrowserDriver 发出，不能从 Mac 手工代替 Agent 完成步骤。

现场并排展示：

- Screen Sharing：Agent 正在操作的真实 Chrome 页面。
- Mac Chrome：同一 `run_id` 的 observer 状态和证据时间线。
- Mac Terminal：HomeMaster CLI 和真实 tmux/Bash 输出。

### 8.3 待验证外部依赖

Playwright、OpenEnv 和 BrowserGym 在本仓库项目环境中的安装与真实调用仍为 `UNVERIFIED`。

推荐实现一个 HomeMaster-owned `BrowserDriver` Protocol，并以 Playwright 作为首选实现。正式实施前的第一个 linchpin gate 必须完成：

```text
启动评测服务
  -> 在 configured DISPLAY 启动 headed Chrome
  -> 打开 /ticket/{run_id}
  -> 通过稳定 data-bid 点击
  -> 收到后端成功状态
  -> 独立 GET /state 看到终态变化
  -> X11 window tree 仍存在目标页面窗口
  -> TigerVNC 仅监听 loopback，并可选经 SSH tunnel 观察
```

只有服务器端 DOM/后端/X11/RFB/截图闭环通过，Playwright 和真实 headed display 才从 `UNVERIFIED` 变为可用。Mac 观察链路按用户覆盖延后，不影响当前录屏交付。若 Playwright 无法在 configured display 上工作，应替换 `BrowserDriver` 实现，不给业务逻辑增加第二套 mode。

### 8.4 自动录屏与视频交付

2026-07-16 已核对 `hkust4` 存在 `/usr/bin/ffmpeg`、`/usr/bin/ffprobe`、`/usr/bin/xterm` 和 `/usr/bin/gnome-terminal`，FFmpeg 4.4.5 构建包含 `libx264`。这些符号存在不等于 X11 录屏链路可用；完整 capture/encode/probe 仍为 `UNVERIFIED`，必须在实施前通过真环境 linchpin gate。

DemoRecorder 录制 Agent 实际使用的专用 TigerVNC display，而不是事后按 trace 重演。固定画布为 `1920x1080`：

```text
+----------------------+--------------------------------------+
| CLI transcript       | Agent Chrome                         |
| + terminal evidence  | ticket / monitor / automation        |
| 640x1080             | 1280x720                             |
|                      +--------------------------------------+
|                      | observer + live effective trajectory |
|                      | + final scores, 1280x360              |
+----------------------+--------------------------------------+
```

用户当前 SSH shell 不在 X11 display 中，因此左侧 xterm 不伪造第二个 Agent；它只 `tail -F` 同一 run 的 `agent/cli_transcript.log` 和 terminal evidence stream。右侧 Chrome 是 BrowserDriver 正在真实操作的同一 browser context；observer 使用同一 `run_id`。

录制生命周期：

1. ticket bundle、display、窗口和 observer 就绪后，先启动 FFmpeg。
2. 独立确认 FFmpeg 进程存活并已写入首个 packet，才允许第一次 provider 调用。
3. 录制完整 Agent 动作、终端、轨迹时间线和最终评分。
4. summary 页面稳定显示后保留 5 秒，再向 FFmpeg 发送正常结束信号。
5. 核对 FFmpeg 返回码并用 ffprobe 验证 H.264、`1920x1080`、`yuv420p`、有效时长和非零 frame count。
6. 抽取首帧、中间帧、末帧，分别做非黑屏像素检查和人工可读性判读，生成 `poster.png` 与 `video_manifest.json`。

默认编码为 15 fps、H.264/libx264、CRF 20、`veryfast` preset、无音频。真实配置来自 gitignored `config/coworker_demo.yaml`，仓库只提交去敏 `.example`。

录屏启动失败时在调用模型前失败；中途录屏失败时保留 Agent/环境原始证据并继续安全收尾，但 `artifact_failure=true`、formal success=false。不得用一段重新录制或 trace replay 的视频替换失败的原始 run。

---

## 9. 真实终端与绝对路径

### 9.1 已验证基线

`hkust4` 已核对存在：

- `/usr/bin/tmux`
- `/usr/bin/bash`
- `/usr/bin/bwrap`
- `/usr/bin/unshare`

宿主机 `/opt` 不可写，`/opt/app` 不存在。不能直接创建测试集要求的绝对路径，也不能静默把工单命令改成另一个路径。

2026-07-15 真机探针已经证明 bubblewrap 可以用临时 `/opt` 和 bind mount 在 rootless sandbox 内创建可写 `/opt/app`。因此采用 bubblewrap 映射，而不是修改 SOP 命令。

### 9.2 Episode Root

每个 run 创建独立目录：

```text
var/coworker-demo/{run_id}/
  environment/root/opt/app/service_layer/component/config/extension_item_mapping.json
  terminal/
  environment/audit_events.jsonl
  scores/summary.json
```

tmux/Bash 在 bubblewrap 内运行，把 `environment/root/opt/app` 映射为 sandbox 中的 `/opt/app`。HomeMaster 看到和执行的命令保持与工单一致：

```bash
grep -A 3 "${TenantId}:${ItemCode}" /opt/app/service_layer/component/config/extension_item_mapping.json
```

### 9.3 命令策略

- terminal executor 只接受当前 episode policy 允许的命令模板和目标路径。
- 命令必须在专用 tmux session 中执行，不能在 HomeMaster 进程内伪造 stdout。
- 记录原始命令、规范化命令、stdout、stderr、退出码、开始/结束时间和 tmux session ID。
- 创建后 grep 的成功门是退出码 `0` 且 stdout 包含四个锁定变量对应的完整配置；回滚后 grep 的成功门是退出码 `1`、stdout 为空且目标文件仍可独立读取。
- 超时必须终止该命令并返回稳定失败状态；不能把部分 stdout 当成功。
- 执行验证和回滚验证分别产生独立 command evidence ID。
- evaluator 在 bubblewrap 外独立读取 episode root 的真实文件，不相信终端日志自报。

tmux + Bash + bubblewrap 的完整组合仍需在正式实施前做一次真环境 probe；当前只验证了各二进制存在和 bubblewrap 的 `/opt/app` 映射可用。

---

## 10. Episode 状态机

```text
CREATED
  -> PRECHECKING
  -> READY_TO_CHANGE
  -> CHANGE_SUBMITTED
  -> CHANGE_APPLIED
  -> VERIFYING
       |- COMPLETED
       `- ANOMALY_DETECTED
            -> ROLLBACK_SUBMITTED
            -> ROLLED_BACK
            -> ROLLBACK_VERIFIED
```

另有终态：

- `BLOCKED_PRECHECK`
- `ESCALATED`
- `INSUFFICIENT_EVIDENCE`
- `ENVIRONMENT_FAILED`
- `AGENT_BUDGET_EXHAUSTED`

状态迁移由后端根据外部结果执行，不能由前端按钮文本或 Agent 自报直接设置。所有迁移必须以结构化事件记录前态、动作、返回状态、后态和耗时。

自动化任务使用独立状态机：

```text
accepted -> running -> succeeded
                    `-> failed
```

只有 `succeeded` 且业务返回码成功时，环境才能修改商品配置和真实文件。

---

## 11. 服务 API 与部署

第一版使用环境自有的明确 HTTP contract，不假称已经兼容某个未验证的 OpenEnv 外部 API：

- 创建 run
- reset run 到指定 scenario
- 获取 Agent 可见的 ticket bootstrap
- 获取外部状态 snapshot
- 获取审计事件
- 接收版本化 Agent runtime/action event，供同 run 的 live trajectory view 使用
- 提交受限 terminal action
- 启停并查询 run-scoped recording
- 获取 effective trajectory、逐节点 match 和 trajectory score
- 获取逐 checkpoint result evaluator 结果和 summary
- 订阅 observer SSE

具体 URL 和 schema 在实施计划中冻结，并通过 OpenAPI schema 与契约测试管理。

服务地址、Chrome display、数据目录和 run artifact 目录全部配置化：

- 服务端不假定客户端一定在 localhost。
- HomeMaster 使用配置化 base URL。
- 默认可绑定 loopback 做本机开发，也可绑定指定网卡供远程观察。
- 配置中的 token、端点和路径使用 gitignored 真配置；仓库只提交去敏 `.example`。

---

## 12. Evaluator

### 12.1 证据边界

evaluator 只消费：

- 当前 run 的服务端业务状态。
- 当前 run 的 append-only browser/backend audit events。
- 自动化任务返回码和任务终态。
- 当前 run 的 terminal command evidence。
- 独立读取的 episode 文件终态。
- HomeMaster 最终决策。

trajectory matcher 另消费已经通过 schema/hash 校验的 `effective_trajectory.jsonl`。result evaluator 不消费 trajectory verdict；两项在 score reporter 才合并，避免同一错误假设形成回声。

它不消费 HomeMaster prompt、思维过程或“我已经完成”的自然语言作为成功证据。

### 12.2 每点判定

每个 checkpoint 输出：

- `checkpoint_id`
- `required` / `not_applicable`
- `expected_state`
- `observed_evidence_ids`
- `return_status_ok`
- `external_state_ok`
- `decision_ok`
- `verdict`
- `reason`

白屏“访问过平台”只能得到 `platform_access_only`，不能证明任务执行成功。平台任务成功必须另有任务返回码和业务终态。回滚后的 grep 必须是新的 command evidence，不能复用创建后的 grep。

### 12.3 Episode 结论

- `normal`：所有必需 checkpoint pass，且商品配置存在、无回滚任务。
- `post_change_anomaly`：异常被发现、未错误完成、回滚任务成功、商品配置不存在、独立回滚 grep 证据存在。
- 任一必需 checkpoint 缺证据：`insufficient_evidence` 或 fail，不得用总分覆盖。

### 12.4 Score 与 Artifact 结论

score reporter 读取冻结的 scenario expectation、trajectory match 和 result checkpoint 文件，输出三个独立 JSON。它不得重新解析 raw event 或自行补证据。

- `trajectory_score.json`：分母、逐节点匹配、顺序/参数失败和 safety violations。
- `result_score.json`：分母、逐 checkpoint 外部终态和责任分类。
- `summary.json`：两项分数、overall、formal success、环境/Provider/artifact 状态和所有核心 artifact hash。

分数计算成功但视频、manifest 或必要原始证据缺失时，数值保留供诊断，formal success 仍为 false。

---

## 13. 可观测性

关键事件全量写 JSONL：

- run create/reset/start/end
- variable lock 和 scenario hash
- Agent decision summary
- raw action joined/effective/rejected 及来源 evidence refs
- trajectory DAG node matched/pending/failed
- browser navigation/observe/click/fill/select/wait started/completed/failed
- URL、稳定 bid、后端状态和耗时
- automation job accepted/running/succeeded/failed
- terminal command/stdout/stderr/exit code/timeout
- case state transition
- evaluator per-checkpoint verdict
- recorder started/first-packet/stopped/failed 和 ffprobe 结果
- trajectory/result/overall score finalized

日志必须包含 `run_id`、稳定 event ID、timestamp、component 和 status。不得记录 API key、真实凭据、完整模型 prompt 或隐藏 ground truth。

CLI、observer 和 evaluator 消费同一事件流，但各自投影不同；任何一个消费者失败都不能丢失原始 JSONL。

---

## 14. 错误处理

- shell 消息不含有效 ticket：完全走原 `run_agent_turn()`；router 不创建 run、不加载 coworker 配置。
- 消息像 ticket intent 但路径/schema/bundle 不合法：在 provider 调用前返回确定性错误，不把文件系统错误交给模型猜。
- recorder 首 packet 门失败：在 provider 调用前失败并清理 display/Chrome/environment。
- recorder 中途失败：继续安全收尾并保留所有非视频 evidence，标记 `artifact_failure`，不得补录替换。
- 页面元素失败：保留 BrowserDriver 错误，不改业务状态，不降级为 API 直写。
- 网页后端拒绝：返回 HTTP/业务状态，审计 rejected 事件，终态不变。
- 自动化任务失败：不改配置和文件，HomeMaster 必须停止或升级。
- Shell 非零退出：保留 stdout/stderr；本设计只允许回滚后 grep 的退出码 `1` 作为预期“未找到”，其他非零状态均失败。
- Shell 超时：状态为 terminal timeout，不能继续复用该 session 的未知状态。
- 文件状态与任务返回矛盾：`environment_failed`，不能算 Agent 失败。
- SSE 断线：observer 重连；episode 继续。
- BrowserDriver 或 display 不可用：启动前失败，不调用模型，避免把环境问题算成 Agent 能力问题。
- evaluator 数据不足：`insufficient_evidence`，不猜测。
- raw/effective event 无法按 action ID 对齐或 artifact hash 不符：分类为 artifact/environment failure，不把缺失动作直接算成模型没调用。
- ticket turn 中 Ctrl-C：复用现有 interrupt 取消 provider/tool，停止终端和录屏，原子保存已完成 artifacts，然后回到或退出现有 shell。

环境错误、Harness/adapter 错误和 Agent 决策错误必须分开统计。

---

## 15. 验证与完成定义

### 15.1 内部验证

- schema、状态机和 scenario overlay 单测。
- reset 确定性和变量锁定测试。
- `CoworkerTicketRouter` 的纯路径、自然语言单路径、多个路径、缺文件、错 schema、普通消息和显式 scenario 测试。
- ordinary shell golden tests：未命中 ticket 时仍以相同参数只调用原 `run_agent_turn()`，不构建 coworker registry 或环境。
- stable `data-bid` 唯一性、可见性和可达性测试。
- 所有 BrowserDriver/Store/Terminal 接口实现一致性审计。
- 自动化任务成功、拒绝、失败和超时测试。
- evaluator 逐 checkpoint 正负样本和 mutation 测试。
- trajectory DAG 的缺节点、错序、错参数、跨 run evidence、旧 evidence 复用和禁止动作 mutation 测试。
- `build_coworker_tool_registry()` 与 Dispatcher 的全部公开工具/实现一致性审计。
- coworker skill registry 只暴露两份通用 skill，且 secret/case-ground-truth 扫描为零命中。
- HomeMaster router/turn adapter 契约测试。
- 默认 home-domain 与 ALFWorld 回归测试。

### 15.2 外部终态黑盒门

每个 scenario 独立 reset、独立执行、独立断言，禁止聚合掩盖失败。

浏览器门：

1. headed Chrome 中真实点击每个目标元素。
2. BrowserDriver 返回成功状态。
3. 独立 HTTP/Store 查询证明业务状态变化。
4. observer 在同一 `run_id` 下显示变化。
5. X11 window tree 和交付截图证明真实页面窗口存在且非空白。
6. TigerVNC 只监听 loopback；Mac Screen Sharing 是可选观察能力，不是本次验收门。

终端门：

1. 命令在真实 tmux/Bash/bubblewrap 中运行。
2. 捕获真实退出码。
3. 独立读取 episode root 文件确认终态。
4. 创建后 grep 与回滚后 grep 分别有独立 evidence ID。

Agent 门：

1. `normal` 每个必需 checkpoint 单独 PASS。
2. `post_change_anomaly` 每个必需 checkpoint 单独 PASS。
3. 正常 episode 不错误回滚。
4. 异常 episode 不错误完成，且真实执行回滚。
5. 每个 scenario 分别满足 `trajectory_score == 100` 和 `result_score == 100`。
6. safety/environment/artifact failure 均为 false。
7. 服务、浏览器、终端、VNC tunnel、recorder 和 evaluator 的返回码均成功。

录屏门：

1. FFmpeg 在第一次 provider 调用前已经写入首 packet。
2. FFmpeg 正常退出，ffprobe 独立确认 H.264、`1920x1080`、`yuv420p`、非零时长和 frame count。
3. 首/中/末三帧逐帧通过非黑屏像素门，并分别能看见 CLI、真实 Agent Chrome 或最终 observer 分数。
4. `video_manifest.json` 的 run ID、duration、核心 artifact hash 与本 run 一致。

只有内部测试和以上外部终态同时通过，功能才算交付。

### 15.3 现场演示前预检

- 固定模型配置和 scenario hash。
- reset 两个 scenario 并核对初始文件 hash。
- 检查 display、Chrome、TigerVNC、SSH tunnel、服务、tmux、bubblewrap 和端口。
- 完整跑一次 `normal` 和一次 `post_change_anomaly`。
- 从现有 `homemaster shell` 分别发送两个 ticket/scenario 消息，不能调用隐藏 benchmark CLI。
- 保存 observer 截图、raw/effective trajectory、terminal evidence、双评分和已验证 MP4。
- 逐 scenario 核对无共享 run 目录、tmux session 或浏览器 context。

---

## 16. 依赖与 UNVERIFIED 项

已验证：

- hkust4 有可用 Xvfb display。
- Google Chrome 127 可在 Xvfb 中以非 headless 模式启动。
- Chrome DevTools endpoint 和真实 X11 window 均存在。
- hkust4 已安装 TigerVNC server，Mac 已安装 Screen Sharing viewer。
- hkust4 已安装 FFmpeg/ffprobe 4.4.5、xterm 和 gnome-terminal；FFmpeg build 包含 libx264。
- tmux、Bash、bubblewrap、unshare 存在。
- bubblewrap 可在无 root 条件下提供可写 `/opt/app` 映射。

仍为 `UNVERIFIED`：

- Playwright 在项目锁定版本下启动/连接该 Chrome 并完成真实点击。
- TigerVNC localhost-only server 经 SSH tunnel 到 Mac Screen Sharing 的可选观察闭环（按用户要求延后，不阻塞本次交付）。
- FFmpeg x11grab 从专用 TigerVNC display 录制、libx264 编码、ffprobe 和逐帧像素验证的完整闭环。
- OpenEnv 官方接口是否需要以及具体类名/API。
- tmux + Bash + bubblewrap + `/opt/app` 文件的完整命令闭环。
- HomeMaster 当前模型对整张运维 SOP 的两 scenario 成功率。
- `case_02` 从现有 Hawkeye 工作区导入 hkust4 后的 schema/hash 一致性。

外部 enum、类名、常量和 API 在真环境通过前不得从本设计文字推断为可用。

---

## 17. 实施顺序约束

这是跨 Agent runtime、浏览器、外部服务、终端隔离和 evaluator 的重大改动，必须先完成正式实施计划和唯一一次计划 reviewer 评审，再实现。

建议顺序：

1. 冻结数据集导入 manifest、hash、scenario overlay 和 trajectory DAG ground truth。
2. 先做 headed Chrome + BrowserDriver + 单按钮外部终态 + localhost-only VNC linchpin probe；Mac 实时观察按用户要求延后。
3. 紧接着做 FFmpeg 首 packet -> X11 capture -> H.264 -> ffprobe -> 三帧像素 linchpin probe。
4. 再做 tmux/Bash/bubblewrap `/opt/app` linchpin probe。
5. 实现 EpisodeStore、状态机、审计事件、effective trajectory、DAG matcher、result evaluator 和双评分核心。
6. 实现三个业务页面、observer 和 DemoRecorder/layout。
7. RED-first 实现 `build_coworker_tool_registry()`、十一个工具 contract、两份 coworker skill 和 `run_coworker_turn()`。
8. 最后以最小 patch 接入 `CoworkerTicketRouter`；普通 shell golden tests 必须先绿，禁止重写 interactive shell。
9. 先用确定性 scripted trajectory 从现有 shell 跑通两个 scenario，再跑真实模型闭环，分别输出轨迹、结果和视频 verdict。
10. 更新架构、用户指南、README 和 CHANGELOG。
11. 全部实现、测试、外部终态和文档完成后，进行唯一一次最终代码 reviewer 评审。
12. 主 agent 逐条处理发现并做针对性验证，不自动追加评审。

---

## 18. 待用户审核的设计点

当前上游架构已经锁定，剩余需要用户审核的是本稿整体是否准确表达预期，尤其是：

- `normal` 不执行回滚，回滚 checkpoint 为 `not_applicable`。
- 异常 episode 采用“变更后异常触发真实回滚”，而不是单纯回放历史缺证据案例。
- `case_02` 的历史日志只做 schema/ground truth，不作为当前执行证据。
- 用户只需进入现有 `homemaster shell` 并发送 ticket 路径；不新增独立 benchmark CLI。
- 普通 shell 消息仍走原 `run_agent_turn()`；不改 GenericAgentRuntime 或默认 home registry。
- coworker Agent 获得十一个明确工具和两份通用 skill；业务动作必须经 DOM，`sop_decide` 只记录判断。
- Agent trajectory ground truth 使用必需节点和依赖的 DAG，不要求五类查询的唯一总顺序。
- trajectory score 只评价真实有效动作；result score 独立评价外部终态，二者都必须为 100 才正式成功。
- ticket turn 默认自动录制同一真实 run，MP4、有效轨迹、双评分和证据组成一个 hash 可核对的 artifact bundle。
- MVP 同仓库但环境与 HomeMaster 保持 HTTP/DOM/terminal 边界，未来再拆服务。

用户确认本稿后，下一步是编写精确实施计划；在此之前不修改产品代码或安装依赖。
