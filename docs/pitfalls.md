# Engineering Pitfalls

最新记录放在最上方。

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
