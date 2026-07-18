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
## 2026-07-16 - Synthetic fixture 手工补出了真实 producer 永远不会发布的容器 oracle

严重程度：高。三份 helper 的内部测试全部通过，却无法让同一 schema 的真实生产路径生成 post-Open case，直到第三次真实 discovery run 才暴露。

### 症状

- synthetic post-Open fixture 同时提供 hidden child oracle 和 closed-container oracle，因此 probe、controller、verifier 的 child-parent/snapshot binding 测试全部 GREEN。
- 真实 `discovery-run-006` 中两个 hidden CreditCard 有唯一 Drawer parent，Drawer snapshot 也为 `ok`，但 persisted visibility oracle 完全没有 Drawer 行，最终仍报告 `closed-child transition lacks its target-independent parent sequence`。
- 修复 child binding 后重放同一 run 仍失败，说明第一层修复必要但没有触及真正的 producer coverage 缺口。

### 根因

`_build_visibility_case_oracles()` 只按 trial requested physical types 产出行；target-independent post-Open sequence 还会使用该 trial 未请求的公共关闭容器。Synthetic fixture 手工补了这些容器行，相当于测试了一个真实 producer 无法生成的世界。三个 consumer 对同一手工数据达成一致只是内部回声。

### 修法与教训

- 统一真实覆盖规则为 trial-required physical exact IDs 与 frozen public closed-container exact IDs 的并集；probe 从 reset event 生成，controller/verifier 各自从 persisted raw reset event 重算。
- 从不可变 run-006 的 29 组 raw event/RGB/PNG artifact 重建 oracle，而不是复用旧 artifact。重建从 6 行增加到 15 行，产生 9 个 post-Open cases，controller 与完整 independent verifier 同时通过。
- 共享 schema 的 synthetic fixture 必须由真实 producer 生成，或至少用 producer 的实际 coverage rule 做逐 ID 审计。不得手工补出真实生产路径不会发布的行或字段。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-006`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-16 - 打开真实父容器被错误当成子物体必然进入当前画面

严重程度：高。这个假设同时进入 probe、controller 和 independent verifier，内部 schema/self-test 可以互相一致却共同接受错误的外部终态。

### 症状

- post-Open case 预先用 `opened_container_id == child_parent_id` 决定子物体会可见；打开真实父容器固定走 child lookup/navigation，打开其他容器固定走 `target_not_visible`。
- 真实引擎只保证 Open 的返回状态，不保证容器内物体进入当前相机画面。真实父容器打开后子物体仍可能无正 bbox；其他容器视角也可能碰巧已经看到子物体。
- controller 还把 container 授权 binding 与 child oracle 混在普通 requested-target 分支比较；verifier 从共享 manifest 假设校验结果，没有独立从 raw Open event 决定 outcome。

### 根因

把两个独立事实合并成一个推论：reset containment 回答“打开的容器与 child 是什么关系”，Open return event 才回答“child 此刻是否在模型看到的画面中”。关系正确不等于当前可见。三个 helper 复用了同一推论，同源验证只能形成内部回声。

### 修法与教训

- container relation 只决定 case kind，不能决定 child navigation 结果。公开三调用序列在读取 containment 前锁定，relation 后补且不得改变调用字节。
- manifest 同时冻结 `ok` 与 `target_not_visible` 两个 outcome envelope；worker 只用 Open 返回 event 的 exact `visible=true` 加正 bbox 选择一个。
- child 不可见时，child snapshot lookup、parent resolution、context creation 和 navigation 必须全为 0；可见时才允许用独立冻结的 unique-parent pose 执行一次 child navigation。
- independent verifier 分别从 reset raw containment 和 Open raw event 重算 relation/visibility，并要求真实 run 同时出现两种 outcome。不要用关系、动作意图或 worker 自报字段替代相机终态。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-15 - Direct snapshot 已选为正确 anchor，但旧 parent 错误先被追加

严重程度：高。`discovery-run-005` 的第二个真实 worker 完成 24 次 setup 且生成了正确 direct-pose分类，仍因一条过期 issue 退出 2。

### 症状

- 目标 Statue strict-visible，snapshot row 为 `ok/geometry`，visibility oracle 明确把 `execution_anchor_exact_id` 设为 Statue 自身。
- 它位于没有 Oracle row 的 Dresser 表面。最终 surface 与派生 inventory classification 的 `anchor_exact_id` 都正确等于 Statue。
- worker 仍保留 `strict-visible surface movable has no unique Oracle anchor`，result status FAIL；controller 在第 2/20 trial 停止。

### 根因

分类器先从 parent Dresser 计算 `anchor_id=None` 并立即追加错误，随后才执行“若 exact target 自身有 direct snapshot，则 anchor=exact target”。最终数据正确，但早先追加的 issue 没有撤销。错误判断观察的是中间值，不是最终锁定值。

### 修法与教训

- 按决策优先级先锁定 direct snapshot anchor，再对最终 anchor 做缺失判断。无 direct snapshot 的 surface/open-container 仍要求唯一 parent anchor，边界没有放宽。
- 增加含无 Oracle Dresser parent 的 strict-visible pickupable synthetic case，要求 direct anchor 且 issues 为空。
- 用 run-005 的真实 restored metadata、snapshot 与 visibility fixtures 离线重跑完整 `discover_cases()`；修复后得到 31 cases、空 issues、Statue 自身 anchor。
- 任何错误/terminal 判定都必须在所有更高优先级正常决策完成后基于最终锁定状态执行；不得把中间候选缺失永久写入结果。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-005`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`

## 2026-07-15 - Missing ordinal 合成 ID 被 controller 当成真实对象查 oracle

严重程度：高。全部 helper 自测和真实 THOR setup 都通过后，controller 在冻结 manifest 时拒绝第一个 trial，导致真实 Gate A 无法继续。

### 症状

- `discovery-run-004` 第一 worker 进程 exit 0、cleanup complete、status PASS，完成 27 次 setup、生成 23 cases，tested/model action 都是 0。
- controller 却报告 case `adb3e7cb... has no frozen visibility oracle`，没有生成 `exact-cases-v3.json`。
- 唯一缺 oracle 的 case 是 `grounding_ordinal_missing`：真实场景有两个 FloorLamp，测试 `FloorLamp 3` 使用 Gate-only sentinel 表达“序号不存在”。

### 根因

probe 和 independent verifier 都把 missing ordinal 定义为合成的不存在 ID，并用 `(trial_id, missing_exact_id, object_type, ordinal_index)` 闭式推导 pair/snapshot-not-applicable/freshness-not-applicable hash。controller 的真实 `validate_v3_discovery_result()` 却在任何 case-kind 分流前无条件执行 `visibility_by_id[requested_exact_id]`，要求合成 sentinel 拥有真实 oracle。controller self-test 只验 case schema，没有把 missing case 送进实际 discovery binding 循环。

### 修法与教训

- 在真实 controller 入口中先验证共同 snapshot binding，再让 missing ordinal 在任何 oracle lookup 前走独立闭式推导并 `continue`；普通 case 仍强制真实 oracle。
- 对 sentinel 同时要求：ordinal 恰为冻结集合长度、精确规范名称、在 snapshot/oracle 中都不存在、三个派生 hash 与 authorization binding 全部一致。
- 增加 actual-entrypoint 顺序门、正确缺失 PASS、binding 漂移 FAIL、sentinel 出现真实 oracle FAIL。
- 修复后先离线重放不可变 run-004 的完整 raw artifact 树；只有真实 23-case result 通过同一 validator，才启动新 THOR run。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-004`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-15 - 临时目录 Ruff 假绿且多命令 SSH 被末尾成功掩盖

严重程度：高。若未在正式仓库路径复跑，Gate A 会带着项目 lint/format 失败进入真实 THOR；组合命令还会错误报告进程 exit 0。

### 症状

- 三个 helper 在 `hkust4:/tmp` 下执行 Ruff 时报告 lint/format PASS，同一哈希复制到正式仓库后，项目 Ruff 报两个 `B023`、一个 `E501`，并要求格式化全部三文件。
- 正式 SSH 命令最后继续运行了三个成功的 self-test，整体返回码因此是 0，尽管前面的 Ruff 已明确失败。

### 根因

Ruff 从当前目录向上发现配置。隔离 `/tmp` 不在 HomeMaster 仓库树内，且命令没有显式 `--config`，所以使用了默认规则和默认 formatter，而非 `pyproject.toml` 的 100 列与 `E/F/I/UP/B` 规则。正式组合脚本又没有 `set -e`，只把最后一条命令的状态作为总返回码。

### 修法与教训

- 在仓库外 lint/format 时始终显式传 `--config /data1/haodong2/weilin/red_bird/Homemaster/pyproject.toml`；同步后仍在正式仓库路径复跑同一门。
- 多命令验收用 `set -e`，或逐条记录并断言每个返回码。不得用组合 shell 的最终 exit code代替每个子门的状态。
- 修复闭包变量绑定和超长行后，使用项目配置机械格式化三文件，并重新执行 compile、lint、format-check 和全部 self-test。

### 参考

- `pyproject.toml`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-15 - V2 自测全绿但真实 consumer 仍走 V1 路径或依赖已删除字段

严重程度：高。若直接同步运行，case worker 会绕过 fresh reset snapshot；独立 verifier 只核对 worker 自报计数，存在完整假阳性风险。

### 症状

- verifier 的 10 个 v2 mutation 全部正确拒绝，controller 的 12 项自测也通过。
- 但 AST 读取真实入口后发现：`case_main()` 仍调用 `oracle_lookup_twice()`，run CLI 没有 matrix 输入；`verify_case_bundle()` 没有回读每个 case 的 scan/restore/snapshot artifacts。
- 修完入口接线后，`discovery-run-002` 又证明共享 schema 仍是假绿：`matrix-v2.json` 已删除旧 `discovery_contract`，真实 `discover_cases()` 却继续读取它；transaction 已完成 27 次 setup action，generic failure result 仍把 controller 汇总计数降成 0。

### 根因

自测只覆盖新写的纯校验函数和 synthetic discovery fixture，没有证明 CLI/handler 实际调用这些函数。controller 又从 case result 读取三个计数字段，导致“新函数存在且自测绿”被误当成“真实 run 已接线”。

三个 helper 的 synthetic schema 还各自补出了 production payload 已删除的字段，没有把同一份 committed matrix 直接喂给真实 consumer。异常边界只保留最小错误，混淆了“外部 transaction 已完成”和“后续 case 派生失败”。

### 修法与教训

- 对 helper 增加真实入口 AST/call-graph audit：case handler 必须调用 fresh transaction 和 snapshot lookup，禁止旧 map lookup；run verifier 必须调用 per-case setup artifact verifier。
- 每个 case 独立重跑 reset/query/完整 scan/restore，从 verified restore event 开始测试动作；verifier 从 raw refs 重算 policy、plan、snapshot、restore、witness 和计数。
- 增加正反 artifact 门：完整 synthetic case 为零失败，篡改 snapshot 必须失败。isolated function self-test 不能替代 actual handler verification。
- v2 consumer 统一从 `public_semantic_vocabulary` 确定性派生固定 contract，并拒绝重新出现的 legacy 字段；mutation test 必须核对目标拒绝原因，不能因无关缺字段异常假绿。
- transaction 完成后的下游失败仍写出 policy/plan/snapshot/restore refs、逐动作 rows 和真实计数。`discovery-run-003` 证明同一 27-action transaction 不再被汇总成 0。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-002`
- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-003`

## 2026-07-15 - 真环境检测框通过动作门却在证据序列化阶段失败

严重程度：中。该问题让最小 Oracle smoke 的 `reset` 和 `TeleportFull` 都成功后仍无结果 JSON、进程退出 1；Player.log 恰好又在 teardown 记录异常，容易把根因错归给 Unity 移动。

### 症状

- 阶段日志显示 reset 成功，`TeleportFull.lastActionSuccess=true`，requested/actual pose 一致；但没有 `after_result` 标记和预期 JSON。
- Python stderr 最终为 `TypeError: Object of type ndarray is not JSON serializable`。
- Player.log 末尾同时出现 `ArgumentNullException(name)`，但其修改时间严格位于 `before_close` 与 `after_close` 之间。

### 根因

`event.instance_detections2D[exact_id]` 在当前 ALFWorld/ai2thor 真环境中是 NumPy ndarray。旧 smoke 的 bbox 面积函数只接受 list/tuple，所以先把有效检测框误判为不可用；随后又把原 ndarray 直接交给 `json.dumps()`，导致结果序列化失败。独立的 ai2thor 2.1.0 close 路径向 Unity 发送空 control payload，产生 teardown 日志，但不是 reset/move 失败。

### 修法与教训

- 在证据边界先把 ndarray/NumPy scalar 确定性投影为 JSON-safe list/number，再对同一投影检查长度、有限值和正面积；不能只给 JSON encoder 加兜底，否则几何门仍会假失败。
- 给真环境 probe 加阶段标记并同时保存 Python exit/stdout/stderr；按时间窗区分动作、结果构造和 teardown，不能从 Player.log 最后一条异常倒推动作失败。
- teardown anomaly 单独记录。只有外部动作返回码、准确终态、结果 artifact 和进程退出码分别通过时才接受 case；不得泛化忽略其他 Player.log 异常。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/smoke-root-cause/gate_a_diag_stderr.log`
- `var/alfworld-evidence/20260713-v18-gate-a/smoke-root-cause/gate_a_diag_normalized_stdout.log`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`

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
