# HomeMaster Agent Rules

## Coworker 外部编排纪律

- 修改 run 生命周期 helper、attempt manifest 或 CLI 异常传播后，必须用与真实入口完全相同的参数跑一次顶层 shell smoke，并断言 run root 实际创建、失败/成功路径都打印该路径；helper 单测不能替代入口验收，形参与落盘字段不得同名冲突。
- 正式成功只能在必需 artifact 全部登记后计算，并逐项验证存在、`complete=true` 和当前字节哈希；manifest 未列出的必需项也必须失败，禁止把 `artifact_failure` 写成常量或只遍历已有条目。
- 每个 action 消费、runtime/task/skill 写入和外部调用都必须先检查共享终态；decision 引用只能指向当前 run 已持久化的先前 evidence，伪造、跨 run 或终态后引用必须在写审计前拒绝。
- 配置中的 venv Python 必须保留 venv symlink 路径；只转成绝对路径，不得用 `Path.resolve()` 解引用到基础解释器。启动子服务后必须从该进程验证依赖 import 和 health，不能用父进程 import 成功代替。
- DAG 必需的 planner、progress、SOP decision 和 exact-job wait 必须在外部后继动作或审计写入前由环境端验证。模型叙述、TaskState 内部完成、最终业务状态或事后再次调用不能替代正确顺序，也不得合成或倒填轨迹节点。
- `browser_wait` 必须绑定当前 run 最新提交返回的准确 job ID；终端执行前要求 add/remove wait，normal progress 前要求五项 postcheck 与 business wait，rollback progress 前要求 remove wait 和 absence grep。

## 外部录制终态纪律

- 命名事件帧必须证明画面语义对应 source event：取帧 offset 必须严格早于下一条 presentation event，并逐帧核对 exact tool/failure code/中文原因/恢复状态；source ID、offset 公式、非空像素和区域方差都不能单独作为语义正确门。
- 独立 bundle verifier 不得信任产品写入的 `verified` 或帧统计；必须重新核对 FFmpeg 退出与 first-packet 落盘证据，独立运行 ffprobe，并从交付视频重新解码首中末帧计算内容门。
- 把“编码器处理了帧”和“视频 packet 已写入外部文件”分开。first-packet 门必须同时看到 FFmpeg progress 的有效帧/字节状态，以及输出文件在非零 header 之后至少一次可观测的正向增长；仅有 `frame`、`total_size > 0`、进程存活或非空文件一律不得放行模型调用。
- 录制最终成功必须另行核对 FFmpeg 正常退出码、独立 ffprobe 的 codec/尺寸/pixel format/时长/帧数，以及首中末每一帧预期区域的非黑屏与内容变化。不得用正常收尾后的可播放结果倒推录制开始时 readiness 已成立。

## ALFWorld 外部执行纪律

- 把“已发出动作”和“外部世界已完成动作”分开。任何 THOR 功能都必须同时通过返回状态门和独立外部终态黑盒门；mock、内部 result、trace 或模型反馈不能代替外部终态。
- 导航成功必须由同一个返回 event 证明：外部返回成功、requested/actual pose 一致、准确 objectId 的 `metadata.visible=true`、准确 objectId 的正面积 bbox，以及交付图片与该 event 的 RGB 像素一致。
- Put 成功必须证明：外部返回成功、准确对象离开完整 inventory、`isPickedUp=false`、准确目标在对象 parent membership 中、准确对象在目标 child membership 中。返回与终态矛盾或读取缺失时立即停止为不确定，不得重试。
- 携带物移动成功时，只要求准确 held ID、完整 inventory 不变、准确对象仍存在、`isPickedUp=true` 和实际 pose 匹配。不得要求 `parentReceptacles` 或 `receptacleObjectIds` 不变；THOR 会在携带物经过 receptacle 时更新这些字段。
- 移动失败后，只有完整动作状态和实际 pose 都与动作前一致才可继续。Put 失败后，只有完整动作状态不变才可继续；完整状态至少包含 held ID、完整 inventory、准确对象 parent tuple、准确目标 child tuple、对象存在与 `isPickedUp`。
- 每个发给 THOR 的请求都计一个 backend action，包括 `GetReachablePositions` 等 query。请求前检查候选数、backend action 数和 wall-clock 三预算，预算到达后不得再发 N+1 请求。
- 从确定性 scene snapshot 只解析一次准确对象和目标；显式实例 miss 不得类型级 fallback。候选集合、顺序和 hash 在 context 创建时锁定，重试期间不得重新解析或重算目标。
- 真环境验收按 target/instance 独立 reset、独立断言，禁止用 best/any 或全局聚合掩盖失败。更换 ALFWorld、ai2thor 或 Unity 运行时版本后，重新执行 runtime contract 与逐实例 characterization。
