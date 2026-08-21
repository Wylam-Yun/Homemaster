# ALFWorld Harness 架构

## 目标边界

V1.8 的 `AlfredThorEnv` benchmark 评测模型对公开语义目标的选择和动作顺序。Harness 负责验证 trial、建立 reset-time Oracle pose snapshot、执行准确 grounding、调用唯一外部动作网关，并把外部终态压缩成一个强类型反馈。模型看不到 objectId、坐标、候选位姿、完整 scene metadata 或专家轨迹。

Prompt composition 从已验证 goal identity 公开达到终态所需的任务级语义，不公开具体实例或专家路径。例如
`look_at_obj_in_light` 明确要求目标物在 inventory 且指定灯已打开；`observe` 只提供视觉信息，不构成该 goal
predicate。普通 episode 可从标准 episode ID/任务文本识别，taskset 直接传入 typed `goal_type`。

THOR 运行必须提供有序 `TrialSelectionManifest`。每条记录绑定相对 trial ID、trial 文件 SHA-256、逻辑场景、goal identity、goal fingerprint 和 identity status；绝对路径、路径逃逸、未知字段、bytes/goal 漂移都会在 Adapter 构造前失败。

## Episode 生命周期

```text
load and verify complete trial manifest
  -> build one pinned Adapter for this Episode
  -> reset and verify trial/runtime identity
  -> ChangeTimeScale(0.01)
  -> GetReachablePositions
  -> bounded scan TeleportFull x N
  -> restore exact initial pose
  -> ChangeTimeScale(1.0)
  -> atomically publish immutable pose snapshot
  -> compose canonical HomeMaster runtime with embedded MindMemOS
  -> start managed Neo4j, MindMemOS and FIFO before Provider use
  -> dispatch public tool batch
  -> classify one terminal owner and close HomeMaster resources plus Adapter
```

成功 setup 的 backend action 数是 `N+4`：slow-time、query、`N` 次扫描、pose restore 和 normal-time restore。任一 post-enter 失败都会 best-effort 恢复初始 pose，再恢复 normal time；恢复状态不可确认时环境关闭或 quarantine，snapshot 不发布，Provider 不构造，Episode 返回 score-ineligible setup terminal。

`AlfworldResetResult` 和 `AlfworldGoalAdvanceResult` 是 closed typed result。ready 与 terminal 字段组合互斥；终止记录保留 trigger、最终 failure、classification、恢复/清理状态、环境 disposition、计数和 evidence ref。

ALFWorld 与记忆 backend 保持解耦：Adapter 不 import 或持久化 MindMemOS，`AlfworldApplicationEntry` 只选择
`tool_environment="alfworld"` 并委托标准 `create_home_application`。因此 benchmark 与其他入口共享
embedded MindMemOS、Evidence ledger、FIFO、自动召回、managed Neo4j 和 application close 顺序。
legacy `memory_mode=disabled` 仅禁止旧 ALFWorld writer；canonical `config.memory.enabled` 必须为 true，
MindMemOS 或 FIFO 未组成时 benchmark 在 Provider 调用前失败。

### Gateway 固定 Episode 绑定

`AlfworldGatewayApplication` 在 application composition 外绑定一个固定 episode 和唯一 session。
HomeMaster 进程不 import ALFWorld；`AlfworldHttpEnvironment` 使用随机 token 和 loopback ephemeral port
启动配置中的 ALFWorld Python worker。HTTP 只承载 reset/current state、语义动作、截图与 close，
worker 内部仍复用同一个 Adapter 和 Oracle 外部动作网关。启动 readiness、每次 HTTP status、返回
schema、图片 hash/可解码性和进程退出分别核对；关闭回收 worker、Unity 与由应用管理的 Xvfb。

这条 transport 只解决两套既有 Python 环境的进程隔离，不改变模型通信路径：模型仍经
ApplicationRuntime/Provider 调用普通工具，工具 executor 才访问绑定的 environment。

## Snapshot 与当前物理视图

reset scan 只生成 scene-generation 级 immutable snapshot。每个 addressable exact object row 最多给出一个 direct 或 unique-parent pose；lookup 不搜索、不枚举候选，也不会因一次失败选择另一个 pose。

`robot_go_to` 与 manipulation 从动作即将使用的当前 THOR event 读取 object metadata、visibility 和 2D bbox。这个物理校验与模型何时调用 `observe`、provider 是否收到图片、图片是否被历史 context 裁剪完全独立。

目标解析使用 frozen full scene index：generic label 优先锁定当前 strict-visible peer，没有可见 peer 时稳定锁定冻结顺序中的第一个离屏实例；显式 ordinal 绑定 frozen full set，不允许 fallback。Gateway
配置关闭 offscreen object navigation 时，锁定目标必须满足 strict-visible 或 frozen
`receptacle=true`；frozen 与当前 typed metadata 不一致、当前值缺失或类型错误都进入
`execution_state_uncertain`。strict-invisible non-receptacle 返回可纠正的
`target_not_visible`，backend action count 为零。只有通过该门后才允许 pose lookup 和
`TeleportFull`。`observe({})` 只读取当前 frame PNG 供模型确认，绝不创建动作授权或 freshness 状态。

Gateway/通用 Agent Loop 另有一个模型观察屏障：具身工具结果只有在
`backend_attempted=true` 时建立屏障；参数校验/权限拒绝不会建立。屏障期间 Provider 只看到
`observe`，有效 PNG 返回后解除。未首次消费的观察图片在 snapshot 中按精确 tool-call ID 保留；
动作与其他工具同 batch 会在任何 backend 调用前整体拒绝。该屏障控制“模型下一步可做什么”，
不替代 Adapter 对 return code 和外部终态的独立验证。

## 外部动作网关

所有 V1.8 setup、navigation 和 manipulation 外部请求经过 `OracleActionGateway`。每次请求拥有单调 sequence、phase、canonical payload hash、时长和标准化 `ExternalEventRead`。成功判断同时要求：

- event 可读；
- 返回 action 与请求 action 相同；
- `lastActionSuccess=true`；
- 动作专用 pose/world/终态门通过。

导航把通过 policy gate 的 visible 或 offscreen exact target 映射到 snapshot 的唯一 pose，并发送一次
`TeleportFull`。offscreen target 必须拥有 direct pose；实验模式只允许 offscreen receptacle
进入这一步。`unobserved/relocated/absent` 的 parent fallback 只对已 strict-visible 的目标开放，
不能用 hidden containment 定位离屏 child。返回后必须核对 actual pose、physical world、ALFWorld
control hash、准确目标可见性和 bbox。每个 runtime THOR action 的 raw event 另存为受限 JSONL/artifact，
用于独立核对返回码、pose 和 strict visibility，不投影给模型或飞书。

Manipulation 锁定准确对象、准确 target 和有效 `OracleExecutionContext`。`take/open/close/put/use/slice/heat/cool/clean` 通过动作专用 precondition、gateway 请求、return-code 和 terminal-state evaluator；context 按动作语义 preserve、rebase、consume 或 invalidate。正式 V1.8 public call graph 不到达 V1.7 navigation/local-Put compatibility implementation，但兼容代码仍物理保留，尚未完成源文件级删除。

## 强类型反馈

Adapter 是唯一 `AlfworldExecutionFeedback` 构造边界。Tools、Dispatcher 和 Runner 只转发或消费它，不从字符串重建状态。

模型 payload 固定包含：

```text
success, action, object, target
inventory + inventory_status
object_state + object_state_status
target_state + target_state_status
state_changed + state_read_status
error, terminal, classification, score_eligible, detail
```

缺失、malformed、stale 或读取异常的外部状态不能被当作普通动作失败；它必须成为 `execution_state_uncertain` 或 `unclassified_execution_failure`。公开可纠正错误保持 non-terminal，Harness/外部状态错误通过 Dispatcher terminal gate 阻止同 batch 后续 robot 工具。

## Provider 尝试与重试

`LLMClient` 每次调用只选择一个 key、发送一个请求并产生一个 `ProviderAttemptRecord`；它不内部轮换 key，也不删除图片后重试。GenericRuntime 在 provider 请求真正开始、且尚未产生可见正文、工具调用或提交副作用时，最多执行 8 次总请求。第一次失败后立即重试，之后等待 3、6、12、24、48、96 秒再重试。

重试前要求 assistant/tool/external 三个 commit flag 都为 false，且每次请求的 serialized hash 与第一次完全相同。每次 attempt 有独立 ID 和 call-scoped sink。transport 确定性省略历史图片不阻止冻结请求重试；当前图片是否进入最终 body 在首次发送前独立验证。没有真正发出 provider 请求的本地错误、可见正文或工具 partial delta、已完成响应、请求 hash 漂移或 run deadline 到期均不重试。

## Runner、Taskset 与计数

普通 Episode 每次独立构造、reset 和关闭 Adapter。reset terminal 在 transport/runtime/prompt 之前返回。Taskset 在任何 Adapter 或 Provider 构造前验证所有 trial bytes；首个 reset terminal 把全部 subtask 标成 `not_run/taskset_setup_failure`，goal advance terminal 把当前行标成 `goal_advance_failure`，后续行标成 `prior_infrastructure_failure`。

not-run 行必须满足：`classification=None`、score-ineligible、所有执行计数为零，并通过 `blocked_by_classification` 指向 root owner。setup、benchmark control 和 model action 由 root 分开拥有：

```text
total_backend = setup_backend + model_backend
total_external = total_backend + benchmark_control
```

Episode classification 是 closed set：Agent、Harness grounding/navigation/operation、execution uncertainty、unclassified、provider、runtime、artifact 和 cancelled 互斥。未知 runtime termination 不默认归给 Agent。

## 指标与证据

CLI 和 `summary.json` 分开报告：

```text
raw_success_rate
agent_success_rate_on_valid
evaluation_valid_coverage / harness_valid_coverage
harness_coverage
provider_availability
runtime_availability
cancelled_episodes
formal_score_available
```

`formal_score_available` 只在 evaluation、Harness、Provider、Runtime 全覆盖，且无 unclassified/cancelled 时为 true。模型 trace、Provider attempts、tool trace、reset/control terminal 和 root ledger 分开持久化，不能用一个聚合 PASS 覆盖单行失败。

## 当前外部证据边界

固定运行时为 Python 3.11.15、ALFWorld 0.5.0 和 ai2thor 2.1.0。Gate A `discovery-run-015` 的 20 个 worker 中 19 个通过，补充 Slice worker 因无关 Apple settling 失败；`exact-cases-v3.json` 未生成，Slice 精确行为保持 `UNVERIFIED`。

历史 Gate B best-effort `run-002` 通过真实 Runner/Adapter 进入 THOR，但 reset scan 在 `scan_pose_mismatch` 后的恢复校验最终为 `scan_time_scale_restore_rejected`：5 个 setup backend actions、0 个 Provider request、classification `execution_state_uncertain`。固定十 Episode manifest 的早期运行也 10/10 复现该 reset recovery terminal；这些历史结果不是 PASS。

2026-07-18 correction 先用单条 smoke 证明 setup、Provider、模型和 backend action 都可达；`alfworld-v18-offscreen-fix-smoke-20260718-003` 为 score-eligible `agent_model_failure/not_won`，而非 Harness invalid。最终固定十 Episode `alfworld-valid_unseen-v18-offscreen-fix-20260718-002` 完整退出：1 个 `agent_success`、5/10 score-eligible、4 个 FloorPlan10 physical-world uncertainty、1 个持有 Basketball 时的 THOR navigation rejection；raw success 0.1、Agent-on-valid 0.2、coverage 0.5、Provider/Runtime availability 1.0、`formal_score_available=false`。独立 artifact verifier 重算 10 个 snapshot、311 组 setup request/event/world/control/raw/frame hashes 和 321 个 event files 全部通过。它证明修复后的链路可运行并如实暴露剩余边界，不等于完整 Gate A/B PASS。
