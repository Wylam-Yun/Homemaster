# Changelog

## Unreleased

### V1.8 Implementation

- 问题：V1.8 最初的 current-visible 导航前置条件与公开工具面不兼容，模型无法主动改变视角让离屏目标进入画面，导致工具循环零 backend action 后耗尽预算并被误归为 Agent 失败；reset evidence、ALFWorld control state 和持物导航的物理状态投影也不完整。
  变更：V1.8 使用 committed-frame integrity gate 和 frozen scene index，优先当前可见 exact target，否则消费同一 reset snapshot 的一个 direct pose 做单次离屏导航；新增独立 physical-world/control hashes、成功与失败 reset ledger/snapshot/raw event 持久化，并规范化 held object 随 agent 改变的 geometry。
  原因：既保留“模型动作必须绑定到成功 Provider 请求所见 frame”的完整性约束，又解除不可满足的目标可见性死锁；同时让 setup、恢复、导航和责任分类可从 artifact 独立重算，避免把 Harness 状态漂移伪装成模型失败。
  影响：`AlfredThorEnv` 继续强制 `--trial-manifest`；离屏目标只允许一个冻结 direct pose，移动后必须准确可见，不会恢复 V1.7 candidate search 或 hidden-parent search。CLI/summary 分开报告 raw/Agent-on-valid、evaluation/Harness coverage、Provider/Runtime availability 和 formal-score gate。V1.7 compatibility bodies仍物理保留，但正式 V1.8 call graph guards不可达。
  验证：修复聚焦回归 `72 passed`，完整套件（含 live API）`410 passed, 1 skipped`；changed Python files Ruff、compileall 和 whitespace checks 通过。单条 `alfworld-v18-offscreen-fix-smoke-20260718-003` 完成 36 setup 与 4 model backend actions，score-eligible 且 Provider/Runtime/evaluation/Harness coverage 全为 1。固定十 Episode `alfworld-valid_unseen-v18-offscreen-fix-20260718-002` 完整退出，52 Provider attempts、29 model backend actions、1 Agent success、5/10 score-eligible；4 条 FloorPlan10 physical-world drift 和 1 条持有 Basketball 时的 THOR navigation rejection 保持 Harness invalid，coverage 0.5、`formal_score_available=false`。10 个 snapshot、311 组 setup request/event/world/control/raw/frame hashes 和 321 个 event files 独立重算通过；Gate A 19/20 与缺失 exact-case manifest 仍不记为 PASS。

### Documentation

- 新增 Change Coworker 用户指南与架构文档，覆盖现有 shell 的 normal/anomaly 输入、隔离配置、preflight、真实 DOM/tmux 执行、双域评分、run bundle、独立验证和可选 VNC 观察。
- 新增经独立评审并逐条处置的 V1.8 ALFWorld Oracle 位姿与强类型执行反馈设计：针对真实 10 条运行暴露的候选预算截断、可见但不可操作、Put 状态投影错层和 Provider 误计分问题，明确删除隐藏对象/legacy 导航旁路，以单一 Oracle pose、exact target 可见终态、可 rebase 的执行 context、Adapter 到 Dispatcher 唯一 typed feedback 和分域评分替代 V1.7 搜索路径；本提交仅交付设计，产品接线在 Gate A/B 真环境通过前保持 `UNVERIFIED`。
- 修订 V1.8 ALFWorld reset transaction 设计：保留 immutable `discovery-run-007` 的温度漂移证据，不通过删除 raw THOR `ObjectTemperature` 弱化 world digest。用户批准的 setup 固定为 `initial event -> ChangeTimeScale(0.01) -> query -> N scan Teleports -> exact pose restore -> ChangeTimeScale(1.0) -> atomic publish`，成功 setup 计数由 `N+2` 变为 `N+4`。中途失败必须 best-effort 恢复 pose 和 normal time，任一恢复无法确认即关闭/quarantine 且不发布 partial snapshot；唯一模型初始 event/frame 来自最后成功的 normal-time return event。`PausePhysicsAutoSim` 已在真实 ai2thor 2.1.0 中证明无效，`0.01 -> 1.0` 的稳定与恢复行为已正交验证。该阶段保留的 current-visible 规则随后被本次 frozen-snapshot correction 明确取代。
- 修订 V1.8 ALFWorld 位姿与强类型执行反馈设计：current frame 仍必须与成功 Provider request 的图片绑定，但目标本身不再要求预先 strict-visible。generic label 优先当前可见 exact peer，否则稳定选择 frozen full set 中第一个非 inventory peer；显式 ordinal 始终绑定 frozen full set。离屏目标只能消费自己的 direct snapshot pose，不能通过 hidden parent 定位；返回 event 必须证明准确 objectId 可见且 bbox 为正。physical world 与 ALFWorld control state 分开哈希，held object 的 agent-coupled geometry 被规范化，inventory、picked-up、containment 和任务状态仍保留。该修订以真实 Runner smoke 和逐项 artifact 重算验证，但未把不完整 Gate A/B 宣称为 PASS。
- 记录 V1.8 Gate A smoke 的证据坑：真环境 bbox 为 NumPy ndarray，动作门成功后可能在 JSON 序列化阶段失败；ai2thor teardown 的独立 Player.log 异常不能替代动作返回码、准确外部终态、artifact 和进程退出码四道门。

### Added

- 在现有 `homemaster shell` 中加入严格 ticket router 和独立 coworker child runtime；有效 `case_02` run 获得六项浏览器工具、真实受限终端、SOP 决策、planner/progress 和两个通用 skill，共固定十一项工具。
- 新增 run-scoped FastAPI 环境、ticket/monitor/automation/observer 页面、异步自动化 job、action ledger、真实 tmux/Bash/bubblewrap 执行、31 节点场景 DAG、16 项结果检查和 raw/effective trajectory artifact。
- 新增 localhost-only TigerVNC headed display、FFmpeg x11grab/libx264 录制、first-packet 落盘门、ffprobe/首中末帧验证、OpenAPI snapshot、SSE replay 与产品独立 bundle verifier。

### Fixed

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

- 最终审计处置后全量测试为 `478 passed, 1 skipped`；两个正式 bundle 均通过加强后的独立 manifest/evidence/ffprobe/raw-RGB 帧验证。
- 真实 Mimo `normal` run `coworker-20260716-154711-853f071d` 达到 24/24 节点、14/14 检查点和 trajectory/result/overall 100，正式成功；H.264 视频 SHA-256 为 `a6cd33f1b3c62ca3820ea870c5ffcbe8f236cfb5c66090332f46ae707593755e`。
- 真实 Mimo `post_change_anomaly` run `coworker-20260716-160128-c4f0faa9` 达到 22/22 节点、11/11 检查点、add/remove 与 grep `[0,1]`，正式回滚成功；H.264 视频 SHA-256 为 `d00f19c7b699cc5d832f349eb86a9ab2e0b0aa2a050f7e99b6e335fcfd64cfcd`。
- V1.8 本次设计提交的聚焦 ALFWorld/Runner/Dispatcher 回归为 `145 passed`；排除已证明在 `22cb122` 就会失败的 cleanup guard 后，其余全仓为 `351 passed, 1 skipped`，compileall 和文档 hash/fence/placeholder/secret/diff 门通过。完整 pytest 仍显示该唯一预存 guard FAIL（它全局禁用通用词 `deterministic`，而未修改的 V1.7 spec/既有测试已包含该词）；Ruff lint 的 39 项和 format 的 41 个文件也全部来自未修改的 `src/`/`tests/`，本设计任务未擅自修复。
- ALFWorld benchmark 单测与接口回归通过；真实 Shelf 1-6 exploration 全部达到 put 外部终态和 goal `1/1`。
- Shelf 3/4/6 在独立 Xvfb 产品 Harness 进程中分别通过 THOR return code、inventory、`isPickedUp`、准确 parent/child、goal 和最终图片像素门。
