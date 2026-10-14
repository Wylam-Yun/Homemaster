# RoboCasa Benchmark 接入 HomeMaster — 实施设计文档（评审修订版）

日期：2026-10-14 · 状态：已吸收独立评审全部必改项 · 前置：`docs/reports/robocasa-feasibility-2026-10-08.md`
读者假设：对代码库零上下文。所有引用给出精确文件路径与行号。
评审记录：初版经独立 subagent 评审，发现 7 个阻塞项（S1-S7）已全部修订，建议项 13 条已吸收。

## 0. 范围

**做**：HomeMaster 以 `profile="robocasa"` 跑 RoboCasa 单 cell（一个 task+seed）与小批量评测；MindMemOS 全档接入（`memory_mode=full/disabled/readonly`）；成功判定环境权威（`env.check_success` RPC → `env._check_success()`）。
**不做**：全量 Target50 340 cells 批量（需用户另行批准）；RPent-memory 文件 bundle；explore/reset 模式；dashboard；物理 lease。

## 1. 总体拓扑（真机 smoke 已验证 2026-10-08）

```
homemaster benchmark-robocasa            (HomeMaster venv，纯 CPU 依赖)
  └─ RoboCasaBenchmarkRunner
      └─ RoboCasaWorkerClient ── NDJSON stdin/stdout ──▶ workers/robocasa_worker (.venv-robocasa)
                                                            ├─ RPent _init_runtime 拉起：
                                                            │    env_server (MuJoCo/EGL/GPU, 每 cell 重启)
                                                            │    vla_server (RLDX-1, GPU, 跨 cell 常驻)
                                                            └─ RoboCasaPrimitives + EnvState（RPent 原版）
```

- vla_server 跨 cell 常驻（任务无关，`vla_client.reset_session()` 清状态），每 cell 省 ~3min 模型加载。
- env_server 的 task/split/seed 在 spawn args 绑死（`rpent-ref/robots/robocasa/env_server.py:80-127`），换 cell 必须重启 env_server。
- **server 清理靠 `--parent-watch`**：worker 死 → stdin EOF → env/vla server 自杀（`rpent-ref/rpent/utils/daemon.py:37-53`）。这是取消路径的关键依赖。

## 2. 新增文件清单

### 2.1 Worker 侧（.venv-robocasa，零第三方依赖的 protocol + RPent 后端）

| 文件 | 参照 | 职责 |
|---|---|---|
| `workers/robocasa_worker/__init__.py` / `main.py` | alfworld 同名 | 包标记 / runpy 入口 |
| `workers/robocasa_worker/protocol.py` | `workers/alfworld_worker/protocol.py` | `PROTOCOL="homemaster-robocasa-v1"`、`OPERATIONS`、`emit`/`response`、MAX_LINE_BYTES=1MB；**纯 stdlib** |
| `workers/robocasa_worker/__main__.py` | `workers/alfworld_worker/__main__.py` | NDJSON 主循环：ready 握手 → 帧校验 → dispatch → response；`_redirect_external_output()` 覆盖**所有会碰 primitives 的 op**（`rldx_skill.py:67-71,147-152` 有裸 print）；顶层不 import 重型模块 |
| `workers/robocasa_worker/rpent_backend.py` | `thor_backend.py` | 封装 `_init_runtime`+`RoboCasaPrimitives`+`EnvState`/`dump_state` |

### 2.2 HomeMaster 侧 `src/homemaster/robocasa/`

| 文件 | 参照 | 职责 |
|---|---|---|
| `worker_protocol.py` | `alfworld/worker_protocol.py` | 协议常量 + `WorkerResponse` 验证 + `request_frame` |
| `worker_client.py` | `alfworld/worker_client.py` | spawn/ready/串行收发/terminate→wait→kill 阶梯/stderr 落盘/artifact 校验 |
| `backend.py` | `alfworld/backend.py` | `RoboCasaBackend` Protocol + receipt/observation dataclass |
| `types.py` | `alfworld/types.py` 精简 | `RoboCasaEnvState` / `RoboCasaEpisodeResult` / `RoboCasaSummary` / classification |
| `adapter.py` | `worker_adapter.py` + `adapter_lifecycle.py` | `RoboCasaEnvAdapter`：`backend_id`+`current_state`+`screenshot()`+`bind_application_run`+`close` |
| `tools.py` | `alfworld/tools.py` | `make_robocasa_*` 工厂；**mutating 工具必须产 evidence_refs**（经 trace.write_event，见 §4） |
| `prompt.py` | `alfworld/prompt.py` | episode prompt |
| `tracing.py` | `alfworld/tracing.py` | `RoboCasaToolDispatchObserver`（**前缀 `robocasa_`**，勿照抄 `robot_`）+ trace writer（产出 source_trace 供轨迹入库） |
| `trajectory_memory.py` | `alfworld/trajectory_memory.py` | `RoboCasaTrajectoryRecord` + 入库路径（见 §6，**有类型墙**） |
| `benchmark/cells.py` | 新 | `target50_v2.json` → cell 列表 + `--tasks`/`--seeds` 过滤 |
| `benchmark/runner.py` | `alfworld/benchmark/runner.py` | cell 循环 + worker 生命周期 + episode 编排 + summary |

### 2.3 注册点小改

| 位置 | 改动 |
|---|---|
| `composition/base.py:99` `profile` Literal | + `"robocasa"` |
| `composition/base.py:197` `tool_environment` Literal | + `"robocasa"` |
| **`composition/profiles.py:16-17` `resolve_tool_environment`** | **修复 S1**：`requested != "alfworld"` → `requested not in {"alfworld", "robocasa"}`，否则 `browser_gateway.start_url` 存在时 robocasa 被静默改成 browser |
| `adapters/profiles.py:92` Literal + `elif` 分支 | + `"robocasa"` → `_robocasa_tools(...)` |
| `adapters/profiles.py` `_robocasa_tools()` | 仿 `_alfworld_tools`；**不重复注册 `task_planner`/`task_progress_check`**（home 已含，重名会触发 `_select_universal_tools` 的 unapproved-duplicate `ValueError`，S2）。**这意味着 `EXTERNAL_TERMINAL_OWNER` 策略与 alfworld 现状一致不生效——完成闸门靠 `stop_condition`+`external_terminal_owner` dep** |
| `adapters/profiles.py:_policy_for` | robocasa mutating → `STRUCTURED_RECEIPT`；`robocasa_verify` → `EXTERNAL_STATE` |
| `application/tool_executor.py:327-332` `_bind_backend` | `elif profile == "robocasa": deps.setdefault("robocasa_env", backend)`（`deps["backend"]` 本就对所有 profile 注入） |
| `composition/tools.py:12-28` `compose_tool_registry` | **穿线 `memory_mode`**：目前不传 → `build_tool_registry` 的 `memory_mode` 参数在生产路径是死代码（alfworld 的 246-252 门控同样从未生效）。需要 `compose_application` 把 benchmark 的 memory_mode 传入 |
| `cli/app.py` | `benchmark-robocasa` 命令 → `cli/benchmark_robocasa.py` |

## 3. NDJSON 协议 `homemaster-robocasa-v1`

帧格式与 `WorkerResponse` 语义完全复用 alfworld（`worker_protocol.py:17-50`）。

| op | payload | result | 说明 |
|---|---|---|---|
| ready（首发） | — | `worker_version/python_executable/rpent_commit/robocasa_version/capabilities` | 版本信息用于复现记录 |
| `start` | `{rpent_root, task_name, split, seed, cuda_device, hi_res, workdir}` | `{task_language, meta}` | spawn vla（若无）+env server+primitives+EnvState |
| `reconfigure` | `{task_name, split, seed}` | `{task_language}` | **先 stop 旧 env daemon** → `_init_runtime(components={"env"})` → 新 env_client → **重建 primitives + 调 `vla_client.reset_session()`**（S6：复用 client 必须显式清 session，否则 RTC/memory 跨 cell 污染）；参数相同则 no-op |
| `reset` | `{}` | `{ok}` | 基建重试用：`env.reset()` + `vla.reset_session()` + 清标定/desync。**不暴露为模型工具**；primitives 构造用 `allow_reset=True`（内部不受 `RLDX_ALLOW_RESET` 门控，S6-3） |
| `observe` | `{cameras?: ["agentview","wrist","navview"]}` | `{state, task_progress, task_language, success, images:[{camera,artifact_path,sha256}]}` | state 含 eef/base/gripper；图片走 workdir artifact（同机路径+SHA256，仿 alfworld） |
| `skill` | `{}` | `rldx_skill` 返回 dict + 执行后 state | → `primitives.rldx_skill`。**不暴露 max_chunks/n_action_steps/settle_patience**——spawn env 已按 Target50 设死 `RLDX_MAX_CHUNKS=40/ACTION_STEPS=8/SETTLE_PATIENCE=999`（env var 会静默覆盖调用参数，`primitives.py:448-456`）。`prompt` 参数也不暴露（RPent 强制用 live task_language，`primitives.py:464-466`） |
| `arm` | `{}` | 同 skill | → `rldx_arm`（底座钳位微对准） |
| `navigate` | `{x, y, yaw?}` | 原语返回 + state | → `navigate_to` |
| `move_to`/`move_delta`/`rotate_pitch`/`set_gripper`/`release`/`scripted_grasp`/`move_base` | 各原语参数 | 原语返回 + state | VLA 失败后的手动恢复手段 |
| `back_project` | `{pixels:[[r,c],...], camera}` | `{points, median_xyz}` | **走 `EnvState` 落盘 `*_world.npz` + `partial(back_project_batch, state=state)`（S7 复用路线）**，不是在线 env 调用 |
| `world_map` | `{camera?, z_min?, z_max?, min_cluster_size?}` | `{clusters}` | 同上，`query_world_map` 参数一一对应（`tools.py:497-535`） |
| `check_success` | `{}` | `{success: bool, task_progress}` | 环境权威 |
| `close` | `{}` | `{closed, cleanup_status}` | 停 daemons |

**mutating op 的"自动回传观测"**（S7 复用路线决定）：worker 内建 `EnvState(workdir)`（`rpent/session/base.py`），每个 mutating op 执行后调 `robocasa_tools.dump_state(primitives, state, ...)`——产出 per-step RGB/depth/world.npz 工件 + 最新 `extras["success"]`，并把 state 快照附进 op result。代价 = RPent 同款 ~1-2s/步渲染。

**生命周期（S4 修订）**：
- run 取消/超时 → client **不再等 in-flight 响应**，直接 `terminate→wait→kill` worker → cell 记 `infrastructure_error`/`cancelled`（部分应用的具身动作 = `outcome_unknown`，**不自动重试**）→ 下一 cell 用新 worker。server 由 `--parent-watch` 自清。
- request 超时：`skill` 给 900s（最坏 40 chunk × predict 上限），其他按 RPent 表（observe 120 / reset 300 / close 120）；超时即视为 worker 不可信，走上面阶梯。
- SIGINT：worker 在不同进程组 spawn（`start_new_session=True`），SIGINT 不打给 worker，由 client 显式控制。

## 4. 工具面

| model_alias | mutating | 参数 |
|---|---|---|
| `robocasa_observe` | 否 | `{cameras?}` |
| `robocasa_skill` / `robocasa_arm` | 是 | `{}`（VLA 闭环，参数被 env var 锁死） |
| `robocasa_navigate` | 是 | `{x,y,yaw?}` |
| `robocasa_move_to`/`move_delta`/`rotate_pitch`/`set_gripper`/`release`/`scripted_grasp`/`move_base` | 是 | 各原语参数 |
| `robocasa_back_project` | 否 | `{pixels,camera}` |
| `robocasa_world_map` | 否 | `{z_min?,z_max?,min_cluster_size?,camera?}` |
| `robocasa_verify` | 否 | `{}` |
| `task_planner`/`task_progress_check` | — | 由 home 源提供，不重注册 |

- mutating 工具 `state_effects=("backend.advance",)` → 自动 `RESOURCE_KEY`+`resource_key="robocasa:backend"` 串行（`adapters/profiles.py:350-353`）。
- **`STRUCTURED_RECEIPT` 要求 evidence_refs 非空**（`_ReceiptVerifier`，`profiles.py:63-68`）→ mutating executor 必须像 alfworld 一样 `trace.write_event(...)` 拿 `trace.jsonl#sha256=` 引用（`alfworld/tools.py:206-213`），否则 verification 永 PENDING。
- `robocasa_verify` → `EXTERNAL_STATE`：`_ReceiptVerifier` 读 `context.backend.current_state.won`（`profiles.py:76-77`）→ **`RoboCasaEnvState` 必须有 `won` property（= success）**。
- **S3 修订**：RoboCasa 无"done/terminated≠success"维度——`env.terminated` 恒等于 `check_success()`（`env_client.py:75-77`，`ignore_done=True`）。`RoboCasaEnvState` 只留 `success`(=`won`)、`task_progress`、`step_index`、`invalid_action_count`、`task_language`；**`stop_condition` 只做 `success→completed`**，失败由 deadline/max_iterations/observer classification 产生。不做 `robocasa_finish` 工具。
- 通用 `observe`（ScreenshotTool→`backend.screenshot()`）在 home 源恒在——robocasa profile 下模型会同时看到 `observe` 和 `robocasa_observe`：adapter 实现 `screenshot()` 返回 agentview PNG 即可共存（单帧便捷 vs 多相机+state），文档注释说明。

## 5. Episode 编排

```
RoboCasaBenchmarkRunner.run()
  cells = load_cells(manifest, tasks_filter, seeds_filter)   # cell={task_name,split,seed,timeout_seconds}
  worker = RoboCasaWorkerClient.start(...)                   # vla+env 拉起
  for cell in cells:
      worker.reconfigure(task, split, seed)                  # env_server 换 + vla.reset_session
      adapter = RoboCasaEnvAdapter(worker)
      lifecycle = BenchmarkApplicationLifecycle(             # 泛化 profile 参数（见下）
          config, profile="robocasa", memory_mode=..., ...)
      lifecycle.run(RunRequest(
          text=build_episode_prompt(task_language,...),
          session_id=cell_run_id, profile="robocasa",
          environment=adapter,
          run_policy=RunPolicy(
              max_tool_iterations=100,                       # 对齐 Target50 planner_reference
              deadline_s=cell.timeout_seconds,               # 从 manifest splits.timeout_seconds 读
              stop_condition=lambda ctx: completed if adapter.current_state.success else None,
          ),
          dependencies={robocasa_env, robocasa_trace, robocasa_episode_outcome,
                        tool_dispatch_observer, external_terminal_owner,
                        provider_attempt_sink_factory}))
      success = adapter.current_state.success                # env 权威
      → RoboCasaEpisodeResult + result.json（Target50 字段名：task_name/environment_split/
        seed/evaluation_split/success_source/runtime.cell_timeout_seconds/
        planner={backend:"homemaster",model:<实际模型>,reasoning_effort:null,max_turns:100}）
      → trajectory 入库（§6）
  → summary.json {task_weighted_success_rate, per_split, cells}
```

- `BenchmarkApplicationLifecycle`（`alfworld/benchmark/episode.py:20-94`）泛化：新增 `profile` 参数默认 `"alfworld"` 透传给 `ApplicationCompositionRequest`——评审确认安全（quiet/console_show_replies/memory_off 语义不变）。
- **每 cell 一次 compose_application 的成本已知**（`memory_mode=full` 时含 MindMemOS/Neo4j 栈启动）：MVP 接受，340 全量前评估"共享 application + per-cell session"优化。
- cell 失败分类沿用 alfworld `classification`/`score_eligible` 语义；worker/timeout 类 → `infrastructure_error` + `valid=False`（Target50 口径）。
- close 检查标准：以 close 响应/进程退出码为准，**不用 stderr 含 "Traceback" 判失败**（robosuite/mujoco 噪音会假阳性）——stderr 仅诊断落盘。

## 6. 记忆接线（S5 修订：类型墙）

`AlfworldTrajectoryWriter.enqueue` 与 `mindmemos.add_trajectory_memory` 都 `isinstance` 硬检 `AlfworldTrajectoryRecord`（`trajectory_memory.py:212-214`、`memory/mindmemos_writes.py:455-460`），且 `source_type="alfworld_trajectory"`/`domain: Literal["alfworld"]`。**采用并行路线**（不泛化既有类型，风险更小）：

- `RoboCasaTrajectoryRecord`（robocasa/trajectory_memory.py）：字段仿 alfworld 版，需要 `source_trace_path`+`source_trace_sha256`（trace writer 产出）、`final_environment_state.success/step_index`。
- `RoboCasaTrajectoryWriter`（独立文件，`enqueue` 走自己的 JSONL → `mindmemos.add_robocasa_trajectory_memory`，后者复用 `add_flat`+readback 模板，`source_type="robocasa_trajectory"`）。
- `compose_application` 按 profile 绑定 writer（仿 `base.py:494-501` alfworld_compile_jobs 的绑定方式，robocasa 不组 compile jobs——只入库存储）。
- 每 cell = 独立 session_id → `SessionFinalizer` 自动抽取 + 下 cell `AutomaticRecall` 注入。
- **memory_mode 三档在 `_robocasa_tools` 真实生效**（§2.3 已列入穿线改动）：`disabled`=无 retriever/writer；`readonly`=只有 retriever（**裁掉所有写工具**，避免消融臂被污染）；`full`=retriever+writer+finalizer。

## 7. Worker 启动配置

`RoboCasaWorkerClient.start(...)`：
- `python_executable=<rpent_root>/.venv-robocasa/bin/python`；`worker_source=<repo>/workers/robocasa_worker/main.py`
- spawn env（**全部放 worker spawn env，不靠 op 传导**）：`HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`/`NO_ALBUMENTATIONS_UPDATE=1`、`HF_HOME=<rpent_root>/.cache/huggingface`、`ROBOCASA_ASSETS_PATH=~/.robocasa/assets`、`RLDX_MODEL_PATH=<ckpt>`、`RLDX_MAX_CHUNKS=40`/`RLDX_SETTLE_PATIENCE=999`/`RLDX_ACTION_STEPS_PER_CHUNK=8`、`RLDX_RESET_SEED` 清空、**pop `PYTHONPATH`**。`RPENT_RLINF_ROOT` 不设——`ProcessDaemon` 会 prepend `<rpent>/../rlinf` 到子进程 PYTHONPATH（`daemon.py:88-93`），路径不存在是无害 no-op（rldx 从 site-packages 解析，smoke 已验证）。
- `workdir=<run_dir>/cells/<cell>/worker`（**per-cell**，`ProcessDaemon.log_path` 是 append，共享会把日志混一起）
- worker 侧 `_init_runtime` 的 `args` Namespace **不得含 `memory_profile` 属性**（S6-1：否则触发 `TaskMemory.load` → HF 请求被 reset）
- `vla_server` session idle 默认 3600s 与 composite cell timeout 恰好打平 → spawn 时调大（若 `--session-timeout` 参数存在；否则记录为边界注意）
- worker stderr 落盘 `<cell>/worker/worker.stderr.log`；env/vla server log 由 RPent daemon 写 workdir

CLI flags（仿 `benchmark-alfworld`，`app.py:754-839`）：`--rpent-root --rpent-python --vla-model-path --cuda-device --task-manifest --tasks --seeds --split --episodes --memory-mode --max-tool-iterations --provider-name --run-id --trace-root --log-level`。

## 8. 测试计划（本机可跑，无需 GPU）

`tests/homemaster/robocasa/` + worker 测试：

1. 协议单测：`WorkerResponse.from_json` 合法/非法、dup request_id、行超长、ops 白名单
2. Fake worker 端到端：stdlib fake worker 子进程 ↔ `RoboCasaWorkerClient`，验证 start/reconfigure/reset/observe/skill/check_success/close 往返 + artifact SHA256
3. Adapter：`current_state` 转换、`screenshot()`、`won` 别名、invalid 计数、`bind_application_run`/`close`
4. Tools：schema 校验、RESOURCE_KEY 串行、STANDARD 投影（text+多图）、`robocasa_verify` EXTERNAL_STATE、mutating 工具 evidence_refs 非空（S-改进5）
5. Runner：仿 `test_alfworld_runner.py`（fake adapter_factory/transport）→ cell 循环、success→completed、classification/score_eligible、result.json Target50 字段、summary 聚合（task_weighted）
6. 注册：`build_tool_registry("robocasa")` 工具集、`_bind_backend` 注入、`resolve_tool_environment` 在 `browser_gateway.start_url` 存在时 robocasa 不被改写（S1 回归）、collision 表不触发
7. Memory 门控：三档下工具集差异、readonly 无写工具、trajectory enqueue 调用
8. 取消语义：fake worker 阻塞 op 时 cancel → terminate→wait→kill 阶梯被调用、cell 记 cancelled
9. Audit 测试：`resolve_tool_environment`/`build_tool_registry`/`ApplicationCompositionRequest.profile` 三处 Literal 与 robocasa 分支一致性（项目纪律）

## 9. 风险与未决

| 风险 | 处理 |
|---|---|
| RPent 内部 API 非稳定 contract | pin commit 0ae9f9e；ready 帧带 rpent_commit 校验 |
| env_server 每 cell 重启 ~1-2min | 接受；vla 常驻已省大头 |
| worker 崩溃/超时 | §3 生命周期：terminate→kill→infra_error→新 worker，不重试部分动作 |
| `keep_recent_images=2` 对多视角偏小 | MVP 不动；需要时调 `context.keep_recent_images`（config.py:39 已是配置项） |
| Target50 `planner_reference` 校验 | 结果按非参考提交口径；result.json planner 字段标 homemaster/mimo |
| `TaskMemory.load` 陷阱 | args 不含 `memory_profile`（§7） |
| vla session idle 3600s | spawn arg 调大或记录边界（§7） |

## 10. 实施顺序

1. `workers/robocasa_worker/`（protocol → __main__ → rpent_backend）
2. `robocasa/worker_protocol.py` + `worker_client.py` + `backend.py` + `types.py` + `adapter.py`
3. `tools.py` + `adapters/profiles.py` 注册 + `_bind_backend` + `resolve_tool_environment` 修复 + `compose_tool_registry` memory_mode 穿线 + `prompt.py`/`tracing.py`
4. `trajectory_memory.py` + `RoboCasaTrajectoryWriter` + `add_robocasa_trajectory_memory` + composition 绑定
5. `benchmark/cells.py` + `runner.py` + `BenchmarkApplicationLifecycle` profile 泛化 + CLI
6. 测试全套 → 本机验证 → hkust4 单 cell → 记忆消融小样本（全量需批准）

## 11. 实施状态（2026-10-14）

- [x] 全部新增/修改代码落盘并通过独立 review（2 BLOCKER + 8 MAJOR 已修：
  start/reconfigure 携带权威 state、blocking I/O 移入 `to_thread`、
  artifact_root 随 cell 更新、`_invalid_count`/step_index/step-0 dump_state、
  `allow_reset=False` 且 `reset` op 从协议移除、transport 失败→杀 worker +
  infra terminal、错误码真实映射、版本探针纳入 stdout 隔离等）
- [x] 本机测试：`tests/homemaster/robocasa/` 35 项全绿；alfworld/application/
  benchmarking/memory/cli/tools 回归全绿（800+）
- [x] `benchmark-robocasa --help` 冒烟通过
- [ ] hkust4 单 cell 端到端（OpenDrawer × seed 1）——下一步
- [ ] 记忆消融小样本（full vs disabled）
- [ ] 全量 340 cells —— **需用户显式批准**
