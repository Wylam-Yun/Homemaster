# RoboCasa composite_seen × seed1 首跑发现与 Harness 修改清单

- Run: `composite-seen-s1`（16 任务 × seed 1 = 16 cells，memory mode = full，Mimo planner + RLDX-1 VLA + RoboCasa env）
- 产物根目录（hkust4）：
  `/data1/haodong2/weilin/red_bird/robocasa-runs/robocasa/robocasa/composite-seen-s1/`
- 记忆库：`/data1/haodong2/weilin/red_bird/robocasa-runs/smoke-003-memory/`（Neo4j `bolt://127.0.0.1:17687`）

## 结果总览

```
scored cells: 13（3 格 infra 无效）
成功:         4/13（task-weighted 30.8%，overall 4/16 = 25%）
infra errors: 3    provider failures: 0    cancelled: 0
coverage:     0.8125    总时长 ~11h
```

| # | 任务 | 结果 | 工具调用 | 备注 |
|---|------|------|---------|------|
| 1 | ScrubCuttingBoard | ✅ | 6 | 2 次 VLA 收工 |
| 2 | StackBowlsCabinet | ✅ | 6 | 同上 |
| 3 | WashLettuce | ❌ deadline | 97 | washed_time 始终 0 |
| 4 | RinseSinkBasin | ❌ deadline | 70 | |
| 5 | PreSoakPan | ⚠️ 无效 | — | worker_setup_failed |
| 6 | StirVegetables | ✅ | 34 | |
| 7 | LoadDishwasher | ❌ deadline | 94 | |
| 8 | SteamInMicrowave | ❌ deadline | 79 | 4 子目标 0 完成 |
| 9 | SetUpCuttingStation | ✅ | 48 | |
| 10 | GetToastedBread | ❌ deadline | 80 | |
| 11 | DeliverStraw | ❌ deadline | 74 | |
| 12 | KettleBoiling | ❌ deadline | 47 | |
| 13 | PrepareCoffee | ⚠️ 无效 | — | worker_setup_failed |
| 14 | StoreLeftoversInBowl | ❌ deadline | 89 | |
| 15 | SearingMeat | ⚠️ 无效 | — | worker_setup_failed |
| 16 | PackIdenticalLunches | ❌ deadline | 47 | base_drift 3.76m |

## 失败模式画像

- **能赢**：单物体刚性操作、动作重复型（刷/叠/炒/摆台）——VLA 在分布内时
  `grasped/contact=True` 是真的能打。
- **必输**（9 格全 deadline）：
  - 沾水任务（WashLettuce / RinseSinkBasin / KettleBoiling）：水流交互不在
    RLDX-1 训练分布。
  - 多子目标链（SteamInMicrowave 四子目标、PackIdenticalLunches、
    StoreLeftoversInBowl 等）：链条一长就断。
- **共性**：`robocasa_skill` 反复 `status=cap`（40 chunks 打满原地磨），VLA
  没有"做不到"的弃权信号，agent 只能耗到 deadline；cell-16 base_drift 3.76m
  说明 skill 会把底盘带跑。

## Harness 问题清单（按优先级）

### H1（高）跨 cell VLA session 被空闲清扫回收 → `worker_setup_failed` ×3

- 机制：`vla_server` 的 `rpc_facade` 对 `rpc_xxx` session 有 3600s 空闲
  超时（`--session-timeout-s`），从**最后一次 session-scoped 调用**起算。
  前一格尾部若长时间只做非 VLA 操作（本批 3 格的前格最后 VLA 调用分别在
  step 11/69、4/44、23/88），叠加格间记忆固化 ~10-50min + env 重建，空闲
  越过 3600s → session 被 sweep → 下格 `reset_session` 报 session not found。
- 修法：`reset_session` 撞 `session not found` 时重新 `session.register`
  拿新 session 再 reset（幂等，语义等价）；或 spawn 时传大
  `--session-timeout-s`；或格间发轻量 keepalive（`get_modality_config`）。
- Ref: `workers/robocasa_worker/rpent_backend.py`、
  `rpent/utils/rpc/rpc_facade.py`（`_touch_session`/`_sweep_sessions`）

### H2（高）SessionFinalizer embedding 400 → 记忆蒸馏静默失败 ×9

- 机制：`litellm aembedding`（SiliconFlow Qwen3-Embedding-8B）返回
  `400 code 20015 'parameter invalid'`；高度怀疑蒸馏文本超长。轨迹记录
  本身 16 格全部入库，但蒸馏经验条目大面积缺失，且**只在 stderr 留
  warning，cell 结果/summary 不可见**。
- 修法：①finalizer 失败写入 `memory_finalization_error` 进 result/summary；
  ②embedding 输入截断/分片；③验收记忆链路时分别断言"轨迹入库"与"蒸馏
  条目入库"。

### H3（中）embedding 客户端 aiohttp 连接未关闭

- 现象：stderr 大量 `Unclosed connector / Unclosed client session` 警告。
  单格无害，长跑累积 fd 泄漏，且淹没真正报错。
- 修法：cell/run 边界显式 close embedding HTTP client。

### H4（低）trace_root 多传一级 → `robocasa/robocasa/` 嵌套

- 调用约定：runner 会在 trace_root 下自建 `robocasa/` 子目录，
  `--trace-root` 应传 `robocasa-runs/` 而非 `robocasa-runs/robocasa/`。
  本次数据在嵌套路径下，内容完整。

## 结构性观察（框架改版输入）

1. **VLA 无弃权/无进度信号**：`status=cap` 是唯一反馈，无法区分"快成了"和
   "永远不行"。harness 可考虑在 skill 结果里暴露 subgoal 进度或让 VLA 支持
   early-abort。
2. **串行 16 格 ~11h** 主要来自：60min deadline × 失败格 + 格间记忆固化
   （串行 FIFO ~10-50min/格）。要跑量需要 cell 级并行或 finalizer 流水线化。
3. **composite 的 task_progress 子目标字段很有价值**（bowl_in_microwave 等），
   已被 trace 记录——可用于失败归因分类器，建议在 harness 里沉淀为标准字段。
4. 记忆链路本身（recall→消费→轨迹入库）工作正常，但本批数据**蒸馏层残血**，
   不能用来回答"记忆是否提升 composite 成功率"——修 H2 后需重测。
