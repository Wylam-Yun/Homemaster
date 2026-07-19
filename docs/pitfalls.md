# Engineering Pitfalls

最新记录放在最上方。

## 2026-07-20 - 聚合存在性检查让缺失 Planner 和部分真实模型调用仍可通过验收

严重程度：高。最终评审发现两个假阳性窗口：成功 Planner 结果可以静默丢弃不安全/超限 plan；provider 门只要求至少一个 request 和 response，不能证明完整实时推演都由真实模型响应驱动。

### 症状与根因

- `_safe_plan_snapshot()` 返回 `None` 后，投影仍发布 `tool.call_completed/succeeded`，导致页面保留旧计划或等待状态；verifier 又只检查“存在的 plan 是否属于 Planner”，没有检查“成功 Planner 是否必须有 plan”。
- provider verifier 分别检查 request 列表和 response 列表非空，但不核对 iteration 缺失、重复、集合不一致、顺序颠倒或工具在成功响应前启动。
- 两个门都把“至少有一个/已有的都合法”误当成“每个必需实例都合法”，属于聚合判据掩盖 per-instance 缺失。

### 修法与教训

- 成功 `task_planner` / `task_progress_check` 的安全快照无法生成时必须抛出投影错误，不能降级为无 plan 的成功事件。
- 独立 presentation verifier 逐事件要求每个成功 Planner/进度结果带合法 plan。
- provider verifier 要求每个 transport iteration 都是连续非负整数，request/response 各一次、集合相等、request 早于 response，且工具不能在成功 response 前启动。
- 对必需实例验收，写“每一个”的反例 mutation 测试；不要用非空、any 或只验证现存项替代完整配对。

### 参考

- `src/homemaster/benchmarking/coworker_demo/presentation.py`
- `scripts/coworker_demo/verify_run_bundle.py`
- `tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py`
- `tests/coworker_demo/test_verify_run_bundle_presentation.py`

## 2026-07-20 - 长视频停止已成功却因客户端超时触发重复非幂等停止

严重程度：高。真实 Mimo 已完成 24/24 轨迹、14/14 结果检查和 100 分，FFmpeg 也已正常退出并产出通过验证的 362 秒视频，但顶层 attempt 仍被标记失败。

### 症状与根因

- 通用环境请求超时固定为 20 秒，而长视频停止还要执行 ffprobe、首中末帧和多张命名事件帧解码，第一次 `recording/stop` 在服务端完成并返回 200 时，客户端已经超时。
- 调用方只有在完整 HTTP 响应返回后才设置 `recording_stopped=True`，因此 `finally` 把超时误判成“尚未停止”，再次调用同一副作用接口。
- 录制会话没有缓存完成结果，第二次停止继续向已正常退出的 FFmpeg stdin 写入 `q`，在 `flush()` 抛出 `BrokenPipeError` 并返回 500。
- 业务分数、视频 manifest 和第一次 200 都是成功证据；attempt 失败属于生命周期协议错误，不能反向把真实模型或视频判成失败。

### 修法与教训

- `recording/stop` 使用 180 秒专用验证超时，不继承短请求的通用 20 秒预算。
- 服务会话用互斥锁保护停止过程，并缓存 recorder/display 的完成结果；重复调用返回深拷贝缓存，不再触碰 FFmpeg 或 display。
- 对“请求超时但服务端可能已执行”的副作用接口，必须同时设计足够的操作级超时与服务端幂等语义；仅在客户端加重试会把已成功的外部终态破坏掉。
- 验收继续要求顶层 attempt 成功、外部返回码和独立 bundle verifier 同时通过，不能只因已存在可播放视频就接受失败 attempt。

### 参考

- `src/homemaster/benchmarking/coworker_demo/environment_client.py`
- `apps/case02_openenv/src/case02_openenv/api.py`
- `var/coworker-demo/coworker-20260720-022516-8c773877/`

## 2026-07-20 - 命名事件帧跨过下一事件却仍通过像素门

严重程度：高。manifest 的 source event、时间戳和像素统计都可以正确，但截图时刻已经进入下一条 presentation event，导致一张名为 `rollback_decision_required` 的帧实际显示 `progress_required`。

### 症状与根因

- 两个失败事件只相隔约 0.32 秒，recorder 固定使用 `source + 0.35s` 的 UI settle margin。
- 独立 verifier 只核对 offset 公式、source event 存在和 observer 区域非空；下一事件同样是非空有效 UI，因此全部门都假绿。
- controlled provider 连续返回工具调用，没有给 observer 留出稳定展示失败事件的窗口。

### 修法与教训

- 每个命名帧的 settle margin 取配置上限与“到下一 presentation event 间隔的一半”的较小值，保证取帧时刻严格早于下一事件。
- 独立 verifier 反向检查每个命名帧不得跨过 source 后的下一事件；controlled failure profile 另留固定观察窗口。
- 最终仍逐张人工判读 exact failure code、中文原因、失败工具和恢复折叠；source ID、非空像素和区域方差不能证明画面语义正确。

### 参考

- `apps/case02_openenv/src/case02_openenv/recording/recorder.py`
- `scripts/coworker_demo/verify_run_bundle.py`
- `var/coworker-demo/coworker-20260720-012043-f3f9680b/`（跨事件的反例）
- `var/coworker-demo/coworker-20260720-014757-c9a55e12/`（修复后 anomaly PASS）

## 2026-07-20 - attempt manifest 字段名与函数形参冲突让真实入口立即失败

严重程度：高。Task 5 的 286 项单测、静态检查和 verifier mutation 全绿，但第一次真实 shell 黑盒 run 在分配目录后立刻抛出 `_update_attempt_manifest() got multiple values for argument 'run_root'`。

### 症状与根因

- helper 的第一个位置形参叫 `run_root`，manifest 同时需要写入名为 `run_root` 的字段。
- 真实入口调用 `_update_attempt_manifest(run_root, run_root=str(run_root), ...)`，Python 在进入函数前就因重复绑定失败。
- 单测只覆盖了 status/error 更新，没有使用与真实入口完全相同的 `run_root` 字段，因此内部 helper 绿不能证明顶层入口可运行。

### 修法与教训

- 将内部路径形参改名为 `artifact_root`，保留外部 manifest 的 `run_root` 字段。
- 回归测试按真实入口参数形态创建 manifest，并由 normal/anomaly shell 黑盒 run 证明目录、视频和最终 bundle 都真实生成。
- 修改生命周期 helper、参数名或 attempt tracking 后，必须跑至少一次顶层 CLI smoke；不能把 helper 单测当作入口验收。

### 参考

- `src/homemaster/benchmarking/coworker_demo/turn.py`
- `tests/homemaster/benchmarking/coworker_demo/test_turn.py`
- `var/coworker-demo/observable-failure-gate/shell-normal-observable_failures.stdout.log`

## 2026-07-20 - 零失败轨迹无法证明异常原因在视频中可观察

严重程度：高。clean scripted 视频和单测都可以通过，但没有失败实例时，无法证明真实 LLM 被门禁拒绝后，具体原因会持续显示并在匹配恢复后折叠。

### 症状与根因

- 旧演示只有成功路径，observer 的 latest result 很快被后续动作覆盖。
- 展示投影若丢失稳定错误码，只剩通用失败文本，clean 轨迹仍不会暴露问题。
- 聚合检查只要存在一张好图就通过，会掩盖其他 incident 帧错误。

### 修法与教训

- 发布前运行仅用于展示验收的 `observable_failures` profile，normal 和 anomaly 分别逐实例触发并恢复门禁错误。
- 对全部稳定安全码分别验证投影、恢复规则和真实 Chrome open/resolved DOM；叙事黑盒 run 另外验证真实环境拒绝、连续视频和外部终态。
- scripted gate 只证明展示能力，绝不替代最终 Mimo `mimo-v2.5` 实时执行验收。

### 参考

- `scripts/coworker_demo/scripted_shell_gate.py`
- `tests/case02_openenv/test_observable_presentation.py`
- `tests/case02_openenv/test_pages.py`
- `var/coworker-demo/coworker-20260720-015325-2101b694/`（normal PASS）
- `var/coworker-demo/coworker-20260720-014757-c9a55e12/`（anomaly PASS）

## 2026-07-16 - 正式成功门自引用产品 artifact 与视频结论

严重程度：高。业务、轨迹和视频都可以真实成功，但若最终门把 `artifact_failure` 固定为 false、只校验 manifest 已列出的条目，或让独立 verifier 信任产品写入的帧结论，缺失、篡改和伪造证据仍可能被报告为正式成功。

### 症状与根因

- 评分器生成了 artifact registry，却在 `formal_success` 计算中把 `artifact_failure` 直接设为 false；registry 的 `verify()` 也不知道哪些路径是必需项。
- 离线 verifier 会验证 manifest 已列条目的哈希，但必需文件若完全未登记就不会进入循环。
- 视频只重新运行 ffprobe；首中末帧、FFmpeg exit 和 first-packet growth 仍取信产品 `video_manifest.verified`，形成证据自引用。
- action ledger 只在 reserve 时检查终态，预先 reserve 的 action 仍能在 terminal decision 后 consume；decision evidence 也未验证是否属于当前 run。

### 修法与教训

- 用显式核心 artifact 集合同时驱动产品 finalization 和独立 verifier；缺少 manifest entry、`complete=false`、文件缺失或哈希漂移都设置 `artifact_failure`，然后才重新计算正式成功。
- 所有 action 消费、runtime/task/skill 工具先查共享终态。decision evidence 只能引用当前 run 先前持久化的 event/evidence；environment、normalizer 和离线 verifier 分别拒绝未知引用。
- 离线视频门独立检查 FFmpeg 退出、first-packet 样本增长、视频 SHA-256 和 ffprobe，并直接从 MP4 解码 raw RGB 首中末帧计算非黑比例、方差和首末变化。
- “独立 verifier”必须从原始字节和外部进程重建结论，不能换一个函数再次读取产品布尔值。必需集合必须检查缺项，而不只是检查现存项。

### 参考

- `apps/case02_openenv/src/case02_openenv/artifacts.py`
- `apps/case02_openenv/src/case02_openenv/evaluation/scoring.py`
- `apps/case02_openenv/src/case02_openenv/episode_store.py`
- `scripts/coworker_demo/verify_run_bundle.py`
- `tests/case02_openenv/test_{artifacts,episode_store,scoring,independent_bundle_verifier}.py`

## 2026-07-16 - 最终业务成功掩盖必需轨迹节点错序或缺失

严重程度：高。真实模型可以把配置正确写入、业务验证成功并得到 result 100，同时跳过 planner、implementation gate、exact job wait，或在依赖完成前写 progress；若只验最终文件和模型自报，会把不可审计流程误当成正式成功。

### 症状

- 一个真实 normal run 达到 result 100，但因 `PLAN_CREATED` 晚于 prechecks，trajectory 只有 12.5。
- 另一个 run 用 `browser_observe` 看到 job 已成功而没有调用 `browser_wait`；缺失 `ADD_WAIT` 让 `ADD_GREP` 及后续节点按 DAG 级联失配，trajectory 只有 45.8。
- 模型在只完成 post alarm 后过早写入 `NORMAL_PROGRESS`。即使后来补齐四项检查并再次调用 progress，首个错序有效动作仍不能被事后覆盖。

### 根因链

1. Prompt 能说明流程，但不能保证模型实际选择对应工具或顺序。
2. 业务状态机只限制最终 mutation，没有把 planner/progress/wait 当作后继动作的外部前置条件。
3. 允许错序 runtime event 先进入 append-only audit 后，后续正确事件无法合法改写历史。

### 修法与教训

- ticket 首次读取后，除只读 observe 外的操作必须先看到真实 `task_planner -> PLAN_CREATED`。
- precheck/implementation proceed 后，下一项浏览器动作必须先看到阶段对应 progress；add grep 后若缺 implementation decision，直接返回准确恢复指令。
- terminal 启动前要求最新 add/remove job 的 exact `browser_wait`；`NORMAL_PROGRESS` 写入前要求五项 postcheck 与最新 business wait；`ROLLBACK_PROGRESS` 写入前要求 rollback grep。
- 无效或错序节点在 append 前拒绝，不能由 evaluator 合成、重排、选择后一个候选或倒填。正式成功同时要求 trajectory/result 100、视频与 artifact 门，而不是只看业务终态。

### 参考

- `apps/case02_openenv/src/case02_openenv/episode_store.py`
- `apps/case02_openenv/src/case02_openenv/terminal/executor.py`
- `tests/case02_openenv/test_episode_store.py`
- `var/coworker-demo/coworker-20260716-154711-853f071d/`（修复后 normal PASS）
- `var/coworker-demo/coworker-20260716-160128-c4f0faa9/`（修复后 anomaly PASS）

## 2026-07-16 - 解引用 venv Python symlink 让子服务丢失依赖环境

严重程度：高。父进程测试和 import 全部通过，但启动的 FastAPI 子服务使用基础解释器，运行时才报依赖缺失，容易被误判为 lock 或安装损坏。

### 症状与根因

- 配置指向 `apps/case02_openenv/.venv/bin/python`，路径校验后却变成 uv 管理的基础 Python。
- `Path.resolve()` 跟随 venv 中的解释器 symlink；以解引用后的路径启动进程时，Python 不再发现该 venv 的 `site-packages`。

### 修法与教训

- 可执行路径只使用 `expanduser()` 和 `absolute()` 做定位，保留 venv symlink 身份；数据根等普通路径仍可用 `resolve()` 做 containment。
- preflight 同时验证配置权限、解释器文件与子服务 health。父进程能 import 不能证明子进程解释器正确。

### 参考

- `src/homemaster/benchmarking/coworker_demo/config.py`
- `src/homemaster/benchmarking/coworker_demo/environment_client.py`
- `scripts/coworker_demo/preflight.py`

## 2026-07-16 - FFmpeg 编码进度把未落盘视频误判为首 packet 就绪

严重程度：高。该假阳性会让 Agent 在录屏文件尚不可恢复时开始调用模型；若进程随后异常退出，内部已有几十帧 `frame` trace，但交付 MP4 仍可能只有 28-byte header。

### 症状

- 首次 x11grab linchpin 返回 `pass=true`，FFmpeg progress 已到 `frame=58`，最终 ffprobe 和三帧检查也通过。
- 逐采样复查却发现录制期间 `demo.mp4` 始终只有 28 bytes，直到发送 `q` 正常收尾后才一次性增长。
- 因此“编码器处理了帧”和“fragmented MP4 已在外部文件系统写入可增长 packet”并不是同一个终态。

### 根因链

1. first-packet gate 只要求 `frame >= 1`、`total_size > 0` 和文件非空；28-byte container header 也满足“非空”。
2. x264 默认 GOP 很长，fragmented MP4 只在后续 keyframe/收尾时刷出媒体 fragment。
3. 最终正常收尾让视频可播放，反过来掩盖了模型调用前的 readiness gate 实际未成立。

### 修法与教训

- RED-test 固定 `[0, 28, 28, 28]` 必须失败，只有观察到 header 后又出现更大的正向文件大小才可通过。
- 编码命令锁定 `-g 15 -keyint_min 15 -sc_threshold 0`，并同时要求 FFmpeg progress 的 `total_size > 28` 与宿主文件至少两个不同的正值大小。
- final gate 仍独立要求 FFmpeg exit 0、ffprobe H.264/1920x1080/yuv420p/时长/帧数，以及首中末每帧区域检查；first-packet 与 final-video 是两道不同的门。

### 参考

- `scripts/coworker_demo/linchpin_recording.py`
- `tests/case02_openenv/test_linchpin_helpers.py`
- `var/coworker-demo/linchpin/recording/run-001/video_manifest.json`（假阳性证据）
- `var/coworker-demo/linchpin/recording/run-002/video_manifest.json`（修复后真环境 PASS）

## 2026-07-12 - ALFWorld Harness 把内部执行回声当成外部成功

严重程度：高。该问题曾让含 Harness 执行失败的 Episode 进入 Agent 评分，并让 `9/10` 的汇总结果无法直接解释为模型能力。

### 症状

- 模型正确选择并执行 `put(pencil 1, shelf 1)`，但 Harness 先把仅有 2D detection、准确对象 `metadata.visible=false` 的姿态报告成 `Reached shelf 1`，随后只尝试一次 `PutObject`。
- THOR 明确返回失败，Pencil 仍在 inventory，goal 仍为 `0/1`；模型却只收到 `{"success": false, "error": "action_failed"}`，无法判断对象是否仍被持有或失败属于模型、Harness 还是引擎。
- `robot_inspect_view` 重复返回同一图片。Episode 最终耗尽 50 个环境步骤并累计 37 次 invalid action，掩盖了最初的 Harness 失败。
- 修复期间又发现一个相反方向的假设：携带物体成功移动时，THOR 可能随空间重叠更新该物体的 `parentReceptacles` 和 Shelf 的 `receptacleObjectIds`。若要求完整父子集合不变，真实成功的移动会被误判为 `execution_state_uncertain`。

### 根因链

1. 导航把“画面中存在检测框”误当成“准确目标已达到严格观察/交互姿态”，允许 detection 覆盖准确对象的 `metadata.visible=false`。
2. 目标标签在工具层和 Adapter 层重复解析；显式实例 miss 还可能被去掉编号后退化成类型级匹配，导致锁定的语义目标漂移。
3. `put` 没有复用导航成功 event 创建的局部 `PoseContext`，只在当前姿态调用一次 THOR，也没有用准确 inventory 与父子归属证明终态。
4. 内部 trace 虽记录 inventory、THOR error 和 goal，模型投影却把信息压成 `action_failed`；无新观察的 inspect 又制造了“已经复查”的假象。
5. Runner 没有独立的 Harness terminal/score eligibility 控制面，于是低层执行失败被累计为模型 invalid action 并进入 Agent 分数。
6. 第一版移动门把“完整动作状态不变”同时用于成功和失败移动，没有先在真环境核对派生父子字段的移动语义；同源 mock 无法揭示这个假设错误。

### 为什么单测和 trace 会假绿

- mock event、分类器断言和设计文字可以共享同一个错误假设；三者一致只是内部自洽，不是独立证据。
- `lastActionSuccess`、`Reached ...`、`action_failed` 或一条 `put_result` 日志只证明代码走到某处，不能证明准确对象的外部终态发生了预期变化。
- 2D bbox 证明目标出现在渲染中，不证明准确对象 `metadata.visible=true`，更不证明后续 `PutObject` 可用。
- 内部 trace 中存在丰富状态，不代表模型实际收到了这些字段；历史 `model_trace.jsonl` 只保留了通用错误和旧图片。
- 按多个 Shelf 的 best/any 结果验收会让一个可成功实例遮住其他实例失败。候选预算和终态必须逐实例断言。

### 修法与教训

- 每个外部动作同时核对外部返回状态和独立读取的真实终态。导航成功要求同一 event 的 `TeleportFull` 成功、requested/actual pose 一致、准确对象 `visible=true`、准确对象正面积 bbox 和可保存且像素一致的 RGB frame。
- Put 成功要求 `PutObject.lastActionSuccess=true`、准确 Pencil 离开完整 inventory、`isPickedUp=false`、准确 Shelf 属于 Pencil parent membership、Pencil 属于准确 Shelf child membership；真环境验收再独立要求 goal `1/1`。任何返回/终态矛盾都立即停止为 `execution_state_uncertain`。
- 成功携物移动只锁定 held ID、完整 inventory、准确对象仍存在、`isPickedUp=true` 并核对实际 pose；不得要求 parent/child 集合不变。失败移动只有在完整动作状态和 pose 都不变时才能继续；失败 Put 只有在完整动作状态不变时才能换下一个候选。
- 准确 objectId、目标 objectId、候选集合、顺序和 hash 在一次调用开始时锁定。重试只幂等执行锁定候选，不重新解析实例、不重算漂移目标。
- 所有发给 THOR 的请求都计入 backend action，包括 `GetReachablePositions` 这类 query；每次请求前检查固定候选数、backend action 数和 wall-clock 三预算，禁止 N+1 请求。
- 用不 import 产品 resolver、候选生成器或分类器的真环境 probe 做正交黑盒门，并对每个 Shelf 独立 reset、执行和断言。`shelf-characterization-v3` 的 Shelf 1-6 均通过；产品 Harness 又对 Shelf 3/4/6 分别证明返回成功、准确外部放置终态和 goal `1/1`。

### 参考

- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`
- `plan/V1.7/alfworld-navigation-local-pose-execution-spec.md`
- `plan/V1.7/alfworld-put-local-pose-feedback-evaluation-spec.md`
- `src/homemaster/benchmarking/alfworld/env_adapter.py`
- `src/homemaster/benchmarking/alfworld/execution.py`
- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/summary.json`
- `var/alfworld-evidence/20260712-preimplementation/product-harness-v2/shelf-{3,4,6}/result.json`
