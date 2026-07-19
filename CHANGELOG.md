# Changelog

## Unreleased

### Documentation

- 处置实时 LLM 可观测演示计划评审的十项问题：补齐自由文本机密拒绝、独立外部终态 mutation 门、真实 Planner 状态、reply 中间态、provider 端点身份、完整失败码、工具中文标签/类别、录像单调时间基准、失败 run manifest 与长计划当前项固定显示；所有问题均在实施前写回设计和计划。
- 新增实时 LLM 可观测 Coworker 实施计划：按 presentation v2 类型/纯 reducer、安全 Planner/公开回复/失败码投影、原子 Snapshot/SSE、五区观察面板、独立 verifier、失败恢复黑盒门、文档和真实 Mimo normal/anomaly 连续视频十一项任务推进；主 agent 独立实施，仅在计划和最终代码两个固定关卡使用 reviewer。
- 新增实时 LLM 可观测 Coworker 演示设计：明确最终 normal/anomaly 视频必须由 Mimo mimo-v2.5 现场选工具执行，不能以 scripted-coworker 替代；定义模型计划、模型动作、环境返回、确定性决策摘要、异常恢复折叠、公开回复与隐藏推理隔离，以及 presentation v2 协议、真实外部终态和连续视频验收门。
- 新增 Change Coworker 用户指南与架构文档，覆盖现有 shell 的 normal/anomaly 输入、隔离配置、preflight、真实 DOM/tmux 执行、双域评分、run bundle、独立验证和可选 VNC 观察。
- 新增经独立评审并逐条处置的 V1.8 ALFWorld Oracle 位姿与强类型执行反馈设计：针对真实 10 条运行暴露的候选预算截断、可见但不可操作、Put 状态投影错层和 Provider 误计分问题，明确删除隐藏对象/legacy 导航旁路，以单一 Oracle pose、exact target 可见终态、可 rebase 的执行 context、Adapter 到 Dispatcher 唯一 typed feedback 和分域评分替代 V1.7 搜索路径；本提交仅交付设计，产品接线在 Gate A/B 真环境通过前保持 `UNVERIFIED`。

### Added

- EpisodeStore 现从候选 append-only 展示事件原子重建 presentation v2 Snapshot，SSE 重连可恢复模型计划、当前动作/结果、决策摘要和异常历史，不在浏览器或 Episode 中维护漂移副本。
- 实时展示投影新增持久化 Planner 快照、公开 assistant reply 和封闭失败码；继续拒绝 assistant.thinking、Prompt、证据原文、任意异常文本及敏感字段，且不向模型建立观察面板回流。
- 新增 presentation v2 强类型协议与纯事件 reducer，从同一 run 的 append-only 展示事件确定性重建模型计划、当前动作/结果、决策摘要、异常恢复和关键历史，避免浏览器或 Episode 维护不可审计的第二套状态。
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
- ALFWorld benchmark 单测与接口回归通过；真实 Shelf 1-6 exploration 全部达到 put 外部终态和 goal `1/1`。
- Shelf 3/4/6 在独立 Xvfb 产品 Harness 进程中分别通过 THOR return code、inventory、`isPickedUp`、准确 parent/child、goal 和最终图片像素门。
