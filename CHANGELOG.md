# Changelog

## Unreleased

### Verification

- V1.9 final-review remediation concentrated verification passed: full non-live `1295 passed, 11 deselected`,
  focused review regression `193 passed`, runtime stress `5 passed`, Ruff check/format, compileall and diff gates.
  Real Mimo API (`3 passed`), MCP stdio/HTTP (`21 passed`), device audit/file (`27 passed`, fake backend) and
  extension filesystem/reload (`32 passed`) gates also passed.
- Candidate `800d391dd780fd13e5d3116bb269a99b9b975474` executed the locked four real THOR episodes and one real
  Coworker normal run. ALFWorld finished 0/4 with three Harness-invalid episodes and one score-eligible model
  failure; its wrapper returned 1. Coworker reached 32 real Mimo attempts and produced a verified 255-second H.264
  video, but the changed observation freshness contract rejected business actions and the independent verifier
  returned 1 for missing `presentation/events.jsonl`. These are preserved failed release attempts, not PASS.
- Repaired the hkust4 Coworker editable install that pointed at a stale source tree, and completed Doctor/preflight
  without dependency upgrades. The temporary V1.9 candidate additionally received its omitted lock-matched
  Playwright/greenlet/pyee Coworker runtime dependencies; the initial `ModuleNotFoundError` attempt remains in the
  append-only release ledger.

### Added

- CL-21 增加默认关闭、部署者显式批准的 trusted local extension layer：canonical manifest/entrypoint
  SHA-256、same-bytes compile、non-symlink containment、content-bound plugin provenance、canonical
  `required_capabilities` 与 requested/deployment/run-principal 三方 capability 交集。原因：manifest
  requested capability 不是部署授权，Catalog 中存在的 plugin id 也不能由 request/CLI/Gateway 扩张
  profile。影响：扩展工具只进入 Home final ToolView，ALFWorld/Coworker 不变；坏 hash/import/collision
  在 Catalog mutation 前整体失败。
- CL-21 lifecycle 只接受可信 async callback，区分 application 与 run start/end/stop，提供稳定 priority/
  matcher、cooperative timeout/cancel、blocking、generation fencing、脱敏 JSONL trace 和 cleanup。
  hooks-only reload 在活动 callback 时返回 `busy`，任何 tool/provenance/capability/profile 变化返回
  `restart_required`。该 MVP 不宣称 hostile-code sandbox，也不允许 hook 成为 permission、device safety、
  terminal、verifier 或 scorer 的唯一 owner。当前证据仅为 HPC2 non-live；hkust4 外部门按用户要求延后。
- CL-21 stage review 后收紧边界：reload identity 现在固定 extension id/version/requested/granted
  capabilities 与完整 tool plane；exact tool/hook token 不能替代 canonical required capability；显式空
  `enabled_tool_ids` 关闭全部工具，非法扩权在任何 run hook 前拒绝。callback timeout 改为独立 task
  hard result fence，抗取消 task 继续计入 active 并阻止 reload/cleanup；application close 先 quiesce 再
  stop/cleanup。entrypoint 每级目录均通过 pinned dir-fd 与 `O_NOFOLLOW` 打开，失败 candidate、partial
  load 和 Catalog collision 会释放已取得的 extension cleanup ownership。原因：原有同源测试无法覆盖
  `asyncio.wait_for` 抗取消、父目录 symlink TOCTOU 和空 tuple fail-open。影响：post-review HPC2
  non-live 为 `1285 passed, 7 deselected`，未访问 hkust4 或 live 外部系统。

- CL-20 增加 Gateway、channel-neutral typed identity、确定性 tenant/session 路由、附件 containment、
  bounded priority bus 和严格 `PublicEventProjection`。首个 remote channel 为默认关闭的 Telegram
  long polling；它只从环境变量读取 token，并将 exact sender mapping 转成 immutable principal，
  不信任 prompt、metadata 或 session override。
- Gateway 复用 application factory 的同一个 `ApplicationRuntime`，通过 `RunRequest` 执行并用
  generation fencing、cancel-and-join、SessionBackend snapshot recovery 和 unpaired tool-tail 清洗
  拒绝 late result。progress 可合并/淘汰，final/error/cancel 保留并在满载 critical queue 时反压。
  Gateway 只消费 events 层的 allowlist/redaction/correlation public projection。

影响：新增 `gateway` optional extra 和 `homemaster gateway --config ...`；具体
python-telegram-bot 运行时符号等待用户指导的 hkust4 真环境核对，当前只宣称 HPC2 non-live gate。

- 收尾修复：borrowed device handle 的 pool fencing generation 不再冒充 backend application-run
  generation；观察 capture/provider binding 读取独立的 `backend_generation`，并保留原有 disconnect/
  close fencing 语义。同步刷新 CL-20 upstream manifest destinations/provenance、`uv.lock` baseline
  hash 与 legacy 文档术语。

- 阶段 review 修复：egress 消费时重新核对 generation/identity，Telegram 在认证前不查询或下载附件，
  supervisor 观察全部 service task 并在 outbound drain 后停止 channel；assistant reply 不再与
  `RunResult` duplicate final。progress、final、error、cancel 共用递归 public projection，补充自由文本
  credential、host path、URL query 和配置 secret 脱敏。新增 review regression 后 targeted gate 为
  `151 passed`，完整 HPC2 non-live gate 为 `1251 passed, 7 deselected`。

### Fixed

- V1.9 整体 final review 的 6 项发现已完成代码修复与集中验证：设备 authoritative event append
  与 audit sink 故障隔离，审计失败不再阻断 lease release 或 emergency-stop；未知 MCP discovered tool
  默认按 mutating fail closed，已尝试调用的 timeout/call failure 返回 `outcome_unknown`；Gateway 用一个
  absolute deadline 硬限制 active run、bus、channel 与 service-task shutdown，并在 RuntimeEvent 生产时
  固化 Gateway generation；extension reload 失败会 await partial candidate cleanup，composition 在
  ApplicationRuntime 接管前持有 rollback ownership；manifest 可显式声明 flat Python dependency files，
  digest 与 same-bytes loader 覆盖全部依赖且不暴露真实 `__file__`。原因：阶段内绿灯没有覆盖旁路 sink
  反向中断控制面、backlog 代际漂移、抗取消 shutdown、跨构建阶段 cleanup ownership 和 entrypoint
  之外的行为字节。影响：MCP SDK mutation annotation 仍标 `UNVERIFIED`，trusted extension 仍不宣称
  hostile-code sandbox。评审修复后的独立证据为 `1295 passed, 11 deselected`，没有沿用修复前的
  `1285 passed`；hkust4 外部门的真实失败结果见本节 Verification。

- 修复两条真实 Provider 验收仍按同步方式调用异步 `LLMClient.complete()`、导致 coroutine 逃逸且请求
  根本未发出的问题；live gate 现在直接 await 真实入口并断言外部响应，防止 non-live 全绿掩盖正式
  Provider 门失效。

### V1.9 Phase 2 - 权限、认证与设备资源基础

- 修复阶段评审发现的生产接线与 fencing 缺口：runtime 现在在 run 边界把 borrowed backend 自动
  注册为 tenant-pinned pool handle；同一 physical backend 不能跨 tenant 生成第二个 lease slot；
  disconnect/repeat-disconnect/stop/close 共用原子单调 `fence_next`；已 grant waiter 在 backend 前
  再次锁内核对。原因：只有 isolated pool 测试而没有生产环境接线，会让所有 ownership 与串行门在
  真路径失效；immutable registration generation 也会在 stop 后回退。影响：活动动作在 terminal
  transition 后为 `outcome_unknown`，等待动作从不进入 backend，borrowed backend 仍不会被关闭。
- 急停外部边界改为内部 `DeviceControlReceipt` + 独立 `DeviceStateObservation`，拒绝 raw
  `"success"/"stopped"` 字符串；两次 return code 保留到 result、authoritative event 和 mode-0600
  audit。具体机器人 SDK enum 仍为 `UNVERIFIED`，等待用户指导的 hkust4 真机核对。阶段扩大回归
  `143 passed`，未访问 hkust4。

- 变更：统一 canonical execution chain 默认接入 capability-aware permission policy；Bearer credential 只映射预配置
  typed principal/tenant/capabilities，robot read/control 与 MCP 分别要求 `device.read`、
  `device.control` 和 `mcp.call`，显式 allow、prompt、metadata 与 skill 均不能扩权。
  原因：远程入口不能复用本地隐式信任，也不能建立第二条绕过 canonical executor 的机器人路径。
  影响：本地 `RunRequest` 保留兼容能力；远程 channel 必须显式配置 principal capability，敏感路径、
  command deny 和 tool deny 继续 fail closed。
- 变更：application-owned connection pool 与 generation-aware physical-device FIFO lease 绑定；同设备
  写动作串行、不同设备并发，disconnect/stale/emergency-stop 拒绝等待动作并把活动动作标为
  `outcome_unknown`。急停要求 control capability，且必须同时看到成功返回和外部 stopped 状态。
  原因：连接状态、互斥锁和急停若各自维护 generation，会允许 late result、重复动作或断线后误执行。
  影响：owned/borrowed connection 按 ownership 隔离关闭；设备 lease/fence/stop 追加到 mode-0600
  `device_audit.jsonl`；未知写结果不可自动重试。

### V1.9 Phase 1 - MCP、Resources 与 Tool Output Artifacts

- 修复阶段评审发现的五个边界缺口：artifact 分区改用 immutable tenant identity 而非 principal id；
  resource URI 从 model preview 移除且 audit 只保留 SHA-256 引用；audit sink 故障以 typed failure
  留存但不改变连接状态或中止 cleanup；显式 `--probe` 写入独立 mode-0600 JSONL audit。原因：
  principal/tenant 混淆会破坏 quota/ACL domain，resource payload 与 audit 可能泄漏 query token 或宿主
  路径，观测系统故障也不能反向泄漏连接。影响：raw resource artifact 仍在精确 tenant ACL 内保真；
  MCP、artifact、application/CLI 聚焦回归 `40 passed`，未访问 hkust4。

- 变更：增加 optional MCP SDK、application-owned async manager、stdio/streamable HTTP discovery、
  typed per-server status、timeout/cancel/disconnect fencing、best-effort close isolation 和脱敏 JSONL
  audit；首次真实 run 前原子注册保真 JSON Schema tools，并在 final Home ToolView 后重验 skills。
  原因：动态 MCP 连接与 event loop 绑定，不能由每个 turn 临时创建，也不能用浅层 schema 投影或
  单个 server 失败破坏 builtin Catalog。
  影响：普通 dry-run 保持零外部 I/O，`--probe` 才临时连接；WebSocket 明确 unsupported；
  ALFWorld/Coworker ToolView 不变，每个 Home run 仍只能执行其 frozen ToolView 中的 MCP stable id。
- 变更：新增 tenant/session/run 分区 `ToolOutputStore`，原始 MCP 输出在脱敏前以 opaque handle、
  quota、TTL、0600 原子文件和精确 ACL 落盘；模型只接收 bounded preview/hash/handle，resource URI
  由 adapter 内部映射为 opaque `resource_id`。
  原因：外部工具结果和 URI 可能包含 credential、宿主路径或大 payload，不能直接进入模型、事件
  或 session snapshot。
  影响：调用者必须持有准确 partition identity 才能读取 artifact；config summary、状态、异常、
  dry-run 和 audit 不输出 MCP headers/env/URL userinfo。

### V1.9 Phase 1 - Skills 与配置来源

- 变更：移植 OpenHarness YAML frontmatter、skill discovery 和 registry 控制流，增加 builtin/user/
  project/explicit 优先级、完整 provenance、Git-root/symlink containment、ToolView capability gate、
  named builtin override、model invocation gate 和真实 wheel package-data 验证；Home one-shot、
  Interactive 与 dry-run 现在使用同一份 registry。
  原因：旧 loader 只支持两个硬编码 builtin 和逐行伪 YAML，且用户/项目 skill 没有路径、能力或
  覆盖边界；局部实现若不接 composition 只能得到内部自洽测试，不能成为用户能力。
  影响：自动来源中的非法单项会以 secret-safe diagnostic 拒绝且不影响 builtin，显式来源失败会
  阻止启动；ALFWorld manifest 和 Coworker 固定十一项 ToolView 不变。
- 变更：provider/auth 配置新增 `api_key/auth_token` typed schema、provider-specific env、有限 CLI
  model override、逐字段 `default/file/env/cli` provenance 和递归 redaction；恢复字段完整且只有
  占位值的 `config/homemaster.example.yaml`。
  原因：环境/CLI 合并必须可诊断且不能被 ambient `ANTHROPIC_*` 变量改变身份，也不能在异常、
  doctor、dry-run、日志或事件中泄漏 credential。
  影响：真实配置仍只保存在 ignored mode-0600 `config/homemaster.yaml`；Anthropic SDK
  `auth_token` 已在安装的 0.116.0 真环境构造器中核对可用。
- 验证：全量 non-live `1155 passed, 7 deselected`；全仓 Ruff lint、改动文件 format-check、compileall、
  cleanup guard、OpenHarness port manifest 和 diff/secret/config 权限门通过。真实 wheel 已安装进隔离
  venv 并从源码树外发现 builtin `SKILL.md`；dry-run 黑盒返回 12 项 Home ToolView、CLI model 来源、
  skill 诊断和 `external_io=false`，不输出 skill 宿主机路径。

### V1.8 Implementation

- 问题：V1.8 最初的 current-visible 导航前置条件与公开工具面不兼容，模型无法主动改变视角让离屏目标进入画面，导致工具循环零 backend action 后耗尽预算并被误归为 Agent 失败；reset evidence、ALFWorld control state 和持物导航的物理状态投影也不完整。
  变更：V1.8 使用 committed-frame integrity gate 和 frozen scene index，优先当前可见 exact target，否则消费同一 reset snapshot 的一个 direct pose 做单次离屏导航；新增独立 physical-world/control hashes、成功与失败 reset ledger/snapshot/raw event 持久化，并规范化 held object 随 agent 改变的 geometry。
  原因：既保留“模型动作必须绑定到成功 Provider 请求所见 frame”的完整性约束，又解除不可满足的目标可见性死锁；同时让 setup、恢复、导航和责任分类可从 artifact 独立重算，避免把 Harness 状态漂移伪装成模型失败。
  影响：`AlfredThorEnv` 继续强制 `--trial-manifest`；离屏目标只允许一个冻结 direct pose，移动后必须准确可见，不会恢复 V1.7 candidate search 或 hidden-parent search。CLI/summary 分开报告 raw/Agent-on-valid、evaluation/Harness coverage、Provider/Runtime availability 和 formal-score gate。V1.7 compatibility bodies仍物理保留，但正式 V1.8 call graph guards不可达。
  验证：修复聚焦回归 `72 passed`，完整套件（含 live API）`410 passed, 1 skipped`；changed Python files Ruff、compileall 和 whitespace checks 通过。单条 `alfworld-v18-offscreen-fix-smoke-20260718-003` 完成 36 setup 与 4 model backend actions，score-eligible 且 Provider/Runtime/evaluation/Harness coverage 全为 1。固定十 Episode `alfworld-valid_unseen-v18-offscreen-fix-20260718-002` 完整退出，52 Provider attempts、29 model backend actions、1 Agent success、5/10 score-eligible；4 条 FloorPlan10 physical-world drift 和 1 条持有 Basketball 时的 THOR navigation rejection 保持 Harness invalid，coverage 0.5、`formal_score_available=false`。10 个 snapshot、311 组 setup request/event/world/control/raw/frame hashes 和 321 个 event files 独立重算通过；Gate A 19/20 与缺失 exact-case manifest 仍不记为 PASS。

### Documentation

- 调整实时 Mimo 验收报告中的 verifier JSON 摘录，移除已由小节标题表达且触发全仓历史术语守卫的冗余场景字段，不改变 run、分数、返回码、视频哈希或验收结论。
- README、用户指南和架构文档同步说明实时 Mimo 入口、五区可观测面板、presentation v2、异常恢复、公开输出/隐藏推理边界、`--expected-model` 验证、失败 attempt 保留，以及 scripted gate 不能替代真实 LLM 视频。
- 处置实时 LLM 可观测演示计划评审的十项问题：补齐自由文本机密拒绝、独立外部终态 mutation 门、真实 Planner 状态、reply 中间态、provider 端点身份、完整失败码、工具中文标签/类别、录像单调时间基准、失败 run manifest 与长计划当前项固定显示；所有问题均在实施前写回设计和计划。
- 新增实时 LLM 可观测 Coworker 实施计划：按 presentation v2 类型/纯 reducer、安全 Planner/公开回复/失败码投影、原子 Snapshot/SSE、五区观察面板、独立 verifier、失败恢复黑盒门、文档和真实 Mimo normal/anomaly 连续视频十一项任务推进；主 agent 独立实施，仅在计划和最终代码两个固定关卡使用 reviewer。
- 新增实时 LLM 可观测 Coworker 演示设计：明确最终 normal/anomaly 视频必须由 Mimo mimo-v2.5 现场选工具执行，不能以 scripted-coworker 替代；定义模型计划、模型动作、环境返回、确定性决策摘要、异常恢复折叠、公开回复与隐藏推理隔离，以及 presentation v2 协议、真实外部终态和连续视频验收门。
- 新增 Change Coworker 用户指南与架构文档，覆盖现有 shell 的 normal/anomaly 输入、隔离配置、preflight、真实 DOM/tmux 执行、双域评分、run bundle、独立验证和可选 VNC 观察。
- 新增经独立评审并逐条处置的 V1.8 ALFWorld Oracle 位姿与强类型执行反馈设计：针对真实 10 条运行暴露的候选预算截断、可见但不可操作、Put 状态投影错层和 Provider 误计分问题，明确删除隐藏对象/legacy 导航旁路，以单一 Oracle pose、exact target 可见终态、可 rebase 的执行 context、Adapter 到 Dispatcher 唯一 typed feedback 和分域评分替代 V1.7 搜索路径；本提交仅交付设计，产品接线在 Gate A/B 真环境通过前保持 `UNVERIFIED`。
- 修订 V1.8 ALFWorld reset transaction 设计：保留 immutable `discovery-run-007` 的温度漂移证据，不通过删除 raw THOR `ObjectTemperature` 弱化 world digest。用户批准的 setup 固定为 `initial event -> ChangeTimeScale(0.01) -> query -> N scan Teleports -> exact pose restore -> ChangeTimeScale(1.0) -> atomic publish`，成功 setup 计数由 `N+2` 变为 `N+4`。中途失败必须 best-effort 恢复 pose 和 normal time，任一恢复无法确认即关闭/quarantine 且不发布 partial snapshot；唯一模型初始 event/frame 来自最后成功的 normal-time return event。`PausePhysicsAutoSim` 已在真实 ai2thor 2.1.0 中证明无效，`0.01 -> 1.0` 的稳定与恢复行为已正交验证。该阶段保留的 current-visible 规则随后被本次 frozen-snapshot correction 明确取代。
- 修订 V1.8 ALFWorld 位姿与强类型执行反馈设计：current frame 仍必须与成功 Provider request 的图片绑定，但目标本身不再要求预先 strict-visible。generic label 优先当前可见 exact peer，否则稳定选择 frozen full set 中第一个非 inventory peer；显式 ordinal 始终绑定 frozen full set。离屏目标只能消费自己的 direct snapshot pose，不能通过 hidden parent 定位；返回 event 必须证明准确 objectId 可见且 bbox 为正。physical world 与 ALFWorld control state 分开哈希，held object 的 agent-coupled geometry 被规范化，inventory、picked-up、containment 和任务状态仍保留。该修订以真实 Runner smoke 和逐项 artifact 重算验证，但未把不完整 Gate A/B 宣称为 PASS。
- 记录 V1.8 Gate A smoke 的证据坑：真环境 bbox 为 NumPy ndarray，动作门成功后可能在 JSON 序列化阶段失败；ai2thor teardown 的独立 Player.log 异常不能替代动作返回码、准确外部终态、artifact 和进程退出码四道门。

### Added

- 新增仅用于展示黑盒验收的 `observable_failures` 脚本 profile：normal/anomaly 分别逐实例触发并恢复叙事门禁错误，全码矩阵另行验证 18 个稳定安全码的投影、恢复规则和 Chrome 展开/折叠；该 profile 明确不计入最终真实 LLM 验收。
- 产品与独立 bundle verifier 现在强制核对 presentation v2 全字段、异常/恢复/历史关联、禁止字段、每次工具生命周期、关键事件画面、当前 run 外部终态和真实 provider 模型身份；`--expected-model mimo-v2.5` 会拒绝 scripted 视频、回环/覆盖 provider、缺少成功响应或身份文件晚于首个请求的验收。
- 高管录屏面板改为五区固定布局，常驻展示真实模型计划、每次模型工具选择、独立环境返回和确定性决策摘要；异常展开置顶并在匹配恢复后折叠保留，长文本不再挤压或覆盖相邻区域。
- EpisodeStore 现从候选 append-only 展示事件原子重建 presentation v2 Snapshot，SSE 重连可恢复模型计划、当前动作/结果、决策摘要和异常历史，不在浏览器或 Episode 中维护漂移副本。
- 实时展示投影新增持久化 Planner 快照、公开 assistant reply 和封闭失败码；继续拒绝 assistant.thinking、Prompt、证据原文、任意异常文本及敏感字段，且不向模型建立观察面板回流。
- 新增 presentation v2 强类型协议与纯事件 reducer，从同一 run 的 append-only 展示事件确定性重建模型计划、当前动作/结果、决策摘要、异常恢复和关键历史，避免浏览器或 Episode 维护不可审计的第二套状态。
- 在现有 `homemaster shell` 中加入严格 ticket router 和独立 coworker child runtime；有效 `case_02` run 获得六项浏览器工具、真实受限终端、SOP 决策、planner/progress 和两个通用 skill，共固定十一项工具。
- 新增 run-scoped FastAPI 环境、ticket/monitor/automation/observer 页面、异步自动化 job、action ledger、真实 tmux/Bash/bubblewrap 执行、31 节点场景 DAG、16 项结果检查和 raw/effective trajectory artifact。
- 新增 localhost-only TigerVNC headed display、FFmpeg x11grab/libx264 录制、first-packet 落盘门、ffprobe/首中末帧验证、OpenAPI snapshot、SSE replay 与产品独立 bundle verifier。

### Fixed

- 修复成功 Planner/进度快照投影失败后仍发布无 plan 的 succeeded 事件，以及真实 provider 验收只要求至少一个 request/response 的假阳性窗口；投影现拒绝无法安全生成的 plan，独立 verifier 逐个要求成功 Planner 带 plan，并按连续 iteration 核对 request/response 唯一配对、顺序和工具调用前置响应。
- 修复长视频停止验证继承 20 秒通用请求超时、超时清理再次向已退出 FFmpeg 写入 `q` 的问题；客户端现使用 180 秒专用停止超时，服务录制会话用锁缓存完成结果并幂等返回，避免已成功视频被重复停止标成失败 attempt。
- 修复 attempt manifest 的 `run_root` 字段与 helper 形参冲突导致真实 shell 在分配 run 后立即失败，以及固定 0.35 秒命名帧越过下一事件却仍通过像素门的问题；顶层 shell 黑盒门和独立 verifier 现在分别锁定真实入口与帧事件边界。
- 修复 coworker 正式成功把 `artifact_failure` 固定为 false、manifest 缺项不失败的问题；最终评分和独立 verifier 现在都要求核心 artifact 已登记、完整且哈希一致。
- 修复终态后预留 action、runtime event 和内置 planner/progress/skill 仍可继续执行，以及 decision 可引用伪造或跨 run evidence 的问题；服务端、工具端、归一化和离线 verifier 现在共享终态与证据所有权门禁。
- 修复独立 bundle verifier 信任产品首中末帧布尔结论的问题；它现在独立核对 FFmpeg/first-packet/视频哈希，并从视频重新解码 raw RGB 帧计算非黑比例、方差和首末变化。
- 修复模型可跳过 planner、阶段 progress、exact job wait 或 implementation proceed，导致外部结果成功但 DAG 轨迹级联失配的问题；环境现在在副作用或审计写入前验证真实前置节点，拒绝伪造、错序和跨 job 证据。
- 修复服务启动路径对 venv Python 使用 `Path.resolve()` 后解引用解释器 symlink、丢失 venv 包环境的问题；子进程保留配置的绝对 venv 路径。
- 修复 fragmented MP4 已编码帧但媒体 packet 尚未落盘时过早放行 provider 的问题；录制门同时要求 progress 和 header 后文件正向增长，并固定短 GOP 与 flush。
- 修复 current-visible 前置条件让三工具公开面无法推进离屏任务、最终以零模型 backend action 耗尽工具预算的问题；`robot_go_to` 现在对冻结 exact target 只尝试一个 snapshot pose，并在移动后严格验证准确目标。
- 修复持有物随 `TeleportFull` 改变世界坐标、旋转和 bounds 导致 physical-world hash 误报 `execution_state_uncertain` 的问题；拿起、放下、inventory、containment 和非 held 物体变化仍可检测。
- 修复 reset terminal 引用不存在 evidence、恢复位姿不匹配被误写为 time-scale reject、goal/control 读取异常被哈希为合法 null，以及 raw event/frame hash 无法从 artifact 独立重算的问题。
- 修复 ALFWorld 导航把检测框存在误报为准确目标已可见的问题；导航现在锁定准确 objectId，并同时核对 THOR 返回码、实际 pose、`metadata.visible`、正面积 bbox 和最终 event 图片。
- 修复 `pencil 2` 等显式实例 miss 被二次解析并回退到其他实例的问题；grounding 现在由确定性 `SceneObjectIndex` 一次完成并锁定。
- 修复 put 只调用一次 THOR、只信任 `lastActionSuccess` 且把底层失败压缩成 `action_failed` 的问题；新执行器在同一目标的固定局部候选内重试，并用 inventory、`isPickedUp` 和准确 parent/child membership 验证终态。
- 修复 Harness terminal 后模型仍可继续调用 robot 工具和错误计入 Agent invalid/score 的问题；普通 Episode 与长程 taskset 现在共享 `EpisodeOutcome`，未运行子任务有明确基础设施标记。

### Changed

- 删除不产生新观察的 `robot_inspect_view`。
- 将含真实认证信息的 `config/homemaster.yaml` 从版本控制移除并加入 `.gitignore`；运行机器继续保留本地文件，仓库只提交脱敏的 `config/homemaster.example.yaml`。
- 模型 put 反馈新增稳定 inventory、object state、state change、error 和安全 detail；内部 objectId、坐标、候选及专家信息不会进入模型上下文。
- 导航与 put 内部 trace 新增 context、逐候选 move/put/read、raw event hash、预算用量、context invalidation 和 terminal JSONL 事件；`isPickedUp` 缺失不再被接受为成功状态。
- 汇总与 CLI 同时报告有效子集 Agent 成功率、Harness coverage、基础设施失败数和正式分数可用门。
- 根据六 Shelf 真环境 characterization 固定生产预算：导航 `65 candidates / 66 backend actions / 34804 ms`，局部 put `9 / 17 / 5669 ms`。

### Verification

- 最终 reviewer 的两项 P2 均已采纳：新增 10 个 RED 回归后，Planner 投影与 provider iteration 加强门 128 项聚焦测试通过；评审修复后全量 798 项通过、1 项跳过，两条 accepted real-Mimo bundle 在更强 verifier 下仍独立 PASS。
- 最终审计通过：793 项测试通过、1 项跳过；ruff lint、compileall、历史术语守卫、本次 4 个代码/测试文件 format-check、preflight 和两条真实 bundle 独立验证均通过。全库 format-check 仍只报告 40 个未触及的历史文件，不纳入本次格式化范围。
- 完成两条由真实 Mimo `mimo-v2.5` 现场决策的可观测录屏：normal `coworker-20260720-024949-b7004546` 与 post_change_anomaly `coworker-20260720-025635-a46d87ca` 均通过模型身份、工具/展示关联、真实配置终态、自动化返回码、grep、连续 H.264 视频、逐张人工画面检查和独立 bundle verifier；失败 normal `coworker-20260720-022516-8c773877` 同时保留并写入验收报告。
- 真实服务幂等停止黑盒门 `recording-stop-gate-20260720-024549` 连续两次 `recording/stop` 均返回 HTTP 200，FFmpeg 返回码 0，两个响应与磁盘 MP4 的 SHA-256 一致，证明重复停止不再触碰已退出的录制进程。
- 最终审计处置后全量测试为 `478 passed, 1 skipped`；两个正式 bundle 均通过加强后的独立 manifest/evidence/ffprobe/raw-RGB 帧验证。
- 真实 Mimo `normal` run `coworker-20260716-154711-853f071d` 达到 24/24 节点、14/14 检查点和 trajectory/result/overall 100，正式成功；H.264 视频 SHA-256 为 `a6cd33f1b3c62ca3820ea870c5ffcbe8f236cfb5c66090332f46ae707593755e`。
- 真实 Mimo `post_change_anomaly` run `coworker-20260716-160128-c4f0faa9` 达到 22/22 节点、11/11 检查点、add/remove 与 grep `[0,1]`，正式回滚成功；H.264 视频 SHA-256 为 `d00f19c7b699cc5d832f349eb86a9ab2e0b0aa2a050f7e99b6e335fcfd64cfcd`。
- V1.8 本次设计提交的聚焦 ALFWorld/Runner/Dispatcher 回归为 `145 passed`；排除已证明在 `22cb122` 就会失败的 cleanup guard 后，其余全仓为 `351 passed, 1 skipped`，compileall 和文档 hash/fence/placeholder/secret/diff 门通过。完整 pytest 仍显示该唯一预存 guard FAIL（它全局禁用通用词 `deterministic`，而未修改的 V1.7 spec/既有测试已包含该词）；Ruff lint 的 39 项和 format 的 41 个文件也全部来自未修改的 `src/`/`tests/`，本设计任务未擅自修复。
- ALFWorld benchmark 单测与接口回归通过；真实 Shelf 1-6 exploration 全部达到 put 外部终态和 goal `1/1`。
- Shelf 3/4/6 在独立 Xvfb 产品 Harness 进程中分别通过 THOR return code、inventory、`isPickedUp`、准确 parent/child、goal 和最终图片像素门。
