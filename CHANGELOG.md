# Changelog

## Unreleased

### V1.8 Implementation

- 问题：V1.7 的 THOR trial 身份、reset 扫描、模型当前视图、外部动作终态、Provider 重试和 Runner 计数由多个边界分别推断，可能把 Harness/运行时失败混入 Agent 评分，也无法证明模型动作只使用该次成功请求实际看到的图片。
  变更：V1.8 新增 strict trial manifest、controlled-time `N+4` reset transaction、atomic Oracle pose snapshot、current-visible one-pose navigation、exact manipulation gateway、唯一 typed feedback、request/image/model-view attempt binding、Runtime 单次 closed retry，以及普通 Episode/taskset 的 typed terminal 和 root-owned setup/control/model ledger。
  原因：把 trial、scene、goal、frame、pose、外部 return/terminal state 和责任分类绑定到可独立核对的 closed records，禁止 screen-off fallback、image stripping retry、未知错误默认归 Agent 或 not-run 行虚构 classification/count。
  影响：`AlfredThorEnv` 现在强制 `--trial-manifest`，reset/control terminal 在 Provider 构造前停止；CLI/summary 分开报告 raw/Agent-on-valid、evaluation/Harness coverage、Provider/Runtime availability 和 formal-score gate。V1.7 compatibility bodies仍物理保留，但正式 V1.8 call graph guards不可达。
  验证：最终 ALFWorld/Provider/Runtime 聚焦矩阵 `202 passed, 1 skipped`，全仓 `394 passed, 1 skipped`；48 个 changed Python files Ruff、compileall、cleanup/interface/V1.8 guards、JSON/Markdown/secret/whitespace 检查通过。全改动集 format check 精确保留既有 26-file baseline，不做批量格式化。Gate A 保留 19/20；Gate B `run-001` 暴露并修复 keyword-only 调用，`run-002` 真实进入 THOR 后以 `scan_pose_mismatch -> scan_time_scale_restore_rejected`、5 setup/0 model requests 终止。直接审查新增 runtime-scene 校验及两个回归后，最终 `run-003` 通过正确 FloorPlan219 身份门并复现同一诚实终态；独立 verifier 为 `slice_verified=true`、`overall_status=incomplete`、exit 2。完整 20-case 和十 Episode run 因缺少 exact manifest/凭据保持 `UNAVAILABLE`，不记为 PASS。

### Documentation

- 修订 V1.8 ALFWorld reset transaction 设计：保留 immutable `discovery-run-007` 的温度漂移证据，不通过删除 raw THOR `ObjectTemperature` 弱化 world digest。用户批准的 setup 固定为 `initial event -> ChangeTimeScale(0.01) -> query -> N scan Teleports -> exact pose restore -> ChangeTimeScale(1.0) -> atomic publish`，成功 setup 计数由 `N+2` 变为 `N+4`。中途失败必须 best-effort 恢复 pose 和 normal time，任一恢复无法确认即关闭/quarantine 且不发布 partial snapshot；唯一模型初始 event/frame 来自最后成功的 normal-time return event。`PausePhysicsAutoSim` 已在真实 ai2thor 2.1.0 中证明无效，`0.01 -> 1.0` 的稳定与恢复行为已正交验证；helper/产品接线仍保持 `UNVERIFIED`，后续只使用 fresh `discovery-run-008/case-run-008`。本条不改变 current-visible authorization、`target_not_visible`、snapshot pose 或 post-Open 语义。
- 修订 V1.8 ALFWorld 位姿与强类型执行反馈设计：导航模块 owner 确认所有物理目标必须已在 current event `metadata.visible=true` 且有正面积 bbox，才可执行 `robot_go_to`。保留 bounded reset scan 和 one immutable snapshot，但 snapshot 只提供“可见后怎么过去”的 sole direct/unique-parent pose，`ok` row、reset observation、public semantic type、addressability 和 containment 都不能授权屏外目标。generic label只从 current strict-visible peers中稳定选择；显式 ordinal绑定 frozen full set，missing/invisible统一返回 non-terminal `target_not_visible`、零 snapshot/parent/backend action且不 fallback。Gate A/B 改为同一 exact target/snapshot row 的 invisible-zero-action 与 Gate-fixture-visible single-move 成对黑盒，fixture完全位于产品/Provider/模型边界外。未来记忆模块负责让目标重新进入画面，不能绕过可见性门。本条仍仅交付设计；产品与 Gate helper保持冻结，所有新组合在新版 RED、`discovery-run-004`、case run和 Gate B逐实例通过前均为 `UNVERIFIED`。
- 记录 V1.8 Gate A smoke 的证据坑：真环境 bbox 为 NumPy ndarray，动作门成功后可能在 JSON 序列化阶段失败；ai2thor teardown 的独立 Player.log 异常不能替代动作返回码、准确外部终态、artifact 和进程退出码四道门。

### Fixed

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

- V1.8 本次设计提交的聚焦 ALFWorld/Runner/Dispatcher 回归为 `145 passed`；排除已证明在 `22cb122` 就会失败的 cleanup guard 后，其余全仓为 `351 passed, 1 skipped`，compileall 和文档 hash/fence/placeholder/secret/diff 门通过。完整 pytest 仍显示该唯一预存 guard FAIL（它全局禁用通用词 `deterministic`，而未修改的 V1.7 spec/既有测试已包含该词）；Ruff lint 的 39 项和 format 的 41 个文件也全部来自未修改的 `src/`/`tests/`，本设计任务未擅自修复。
- ALFWorld benchmark 单测与接口回归通过；真实 Shelf 1-6 exploration 全部达到 put 外部终态和 goal `1/1`。
- Shelf 3/4/6 在独立 Xvfb 产品 Harness 进程中分别通过 THOR return code、inventory、`isPickedUp`、准确 parent/child、goal 和最终图片像素门。
