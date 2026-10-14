# HomeMaster 接入 RoboCasa Benchmark 可行性评估

日期：2026-10-08 · 范围：技术调研与架构分析（只读，未改任何代码）· 调研方式：两个并行子 Agent（HomeMaster 架构核查 + RPent/RoboCasa 机制核查），主 Agent 交叉验证汇总。

---

## 一、核心结论

**HomeMaster 能跑 RoboCasa，属于"有限适配可行"——不需要动 Agent 核心架构，只需要新增一个 `robocasa/` 适配包 + 一个隔离 worker 进程 + 少量 profile 注册点扩展。**

三条决定性证据：

1. **ALFWorld 就是同构先例**。HomeMaster 已经完整解决过"一个具身 benchmark 如何接入"这件事：`RunRequest.environment`（borrowed 注入，`application/contracts.py:147`）→ 离散技能工具（`robot_go_to/robot_manipulate/robot_verify`）→ 环境 `won` 权威终止（`stop_condition` + `external_terminal_owner`）→ episode runner 编排（`alfworld/benchmark/runner.py`）→ 失败分类区分模型失败与基建失败（`AGENT_SCORE_CLASSIFICATIONS`）。RoboCasa 可以走**同一条链**。

2. **RPent 的职责分工与 HomeMaster 的工具契约天然匹配**。RPent 里 LLM/planner 只调 15 个离散工具，所有连续控制由 RLDX-1 VLA 在 `rldx_skill` 闭环内部完成（`rpent-ref/robots/robocasa/rldx_skill.py:217`）——模型从不输出连续动作向量。HomeMaster 工具调用同样是离散 JSON Schema 参数。两边粒度完全一致。

3. **依赖隔离已有现成模板**。`workers/alfworld_worker/` 是"环境跑在独立 Python 进程 + 版本化 NDJSON stdio"的完整实现（`alfworld/worker_client.py:76`）。RoboCasa/MuJoCo/robosuite/RLDX 这套重依赖可以整体关在独立 venv 进程里，**HomeMaster 主环境一个包都不用装**。

**代价边界**：真正缺失的是全部机器人专用层（env client、VLA client、技能原语、成功判定接线、评测 runner）——预计新增一个与 `alfworld/` 体量相当的包。核心改动只有几处注册点（`profile` Literal、`build_tool_registry` 环境枚举、`_bind_backend` 加一行绑定）。

**一个已确认的空洞**：文档描述的"动作后必须先 observe"硬屏障在 V3.7 迁移中被删除（`observe_barrier.py` middleware 计划过未实现），目前只剩 prompt 级软约束。但 RPent 的模式可以绕开它：每个非只读工具执行后自动把新观测附进 tool result（`rpent-ref/rpent/tools/toolkit.py:257-326`），不依赖模型自觉 observe。

---

## 二、RPent 是怎么跑 RoboCasa 的

**RPent = Recursive Physical Agent**（`github.com/RLinf/RPent`，RLinf 组织仓库），是论文 "Harness VLA: Steering Frozen VLAs into Reliable Manipulation Primitives via Memory-Guided Agents"（arXiv:2607.08448）的实现。Target50 协议 ID `robocasa-harness-vla-v2`。

### 进程拓扑（每 cell 一次）

```
rpent CLI (rpent --robot robocasa --task-name <T> --split target --seed <N>)
  └─ env_server 进程   : MuJoCo+EGL / robosuite.make(RoboCasa365, PandaOmron,
                         3相机256×256, seed)  → HTTP RPC
                         (reset/step/render_camera/check_success/grasp_contact/
                          get_task_progress/get_success_criteria_text)
  └─ vla_server 进程   : RLDX-1-FT-RC365 checkpoint 推理 → HTTP RPC
                         (predict/reset_session，session_id 由 RPC 层管理)
  └─ planner           : api(pydantic-ai) / claude_code / codex / flash
                         —— 只看工具 schema，不感知机器人在哪
```

关键实现细节（全部 `rpent-ref/robots/robocasa/` 内已核实）：

- **LLM 可见工具面 = 15 个**（`tools.py:39-561`）：11 个动作原语（`move_to/move_delta/rotate_pitch/set_gripper/release/scripted_grasp/rldx_skill/rldx_arm/navigate_to/move_base/reset`）+ 4 个只读（`view_env_state/back_project_batch/query_world_map/finish`）。
- **`rldx_skill` 是核心**：一次调用内部跑完整 VLA 闭环——组 obs（3 相机 256² + proprio + **环境 live task_language**，agent 传的 prompt 被忽略，`primitives.py:464-466`）→ `vla.predict` → 8 个 env.step → chunk 末查 `_check_success()`/静止检测，直到 success/settle/cap（max_chunks=40）。LLM 只发"用 RLDX 做这个任务"，底层全在工具里。
- **观测回流是自动的**：任何非只读工具执行后强制 `get_env_state` → `dump_state` 落盘 step 工件（RGB/depth/world.npz/metadata）→ 新 state 随 tool result 返回（`toolkit.py`）。
- **成功判定环境权威**：`solved()` 读最后一条 StepRecord 的 `extras["success"]`（= `env._check_success()`）；`finish` 只是 planner 的停机信号，模型自称完成不算数（`toolkit.py:222`、注释明说 "an agent may call finish with status=success on a cell it did not actually finish"）。
- **记忆是分层 markdown/JSON bundle**：global `MEMORY.md` + task-family + task-specific recipe（`robots/robocasa/memory.py`），从 HF dataset `RLinf/RPent-memory` 同步；prompt 把选中文件的绝对路径写进去，作为策略先验（禁复用坐标）。
- **评测协议 Target50 v2**（`eval/target50_v2.json`）：50 任务 × 340 cells（atomic 10 seeds / composite 5 seeds），split=target（10 个 held-out 厨房），固定 `RLDX_MAX_CHUNKS=40 / SETTLE_PATIENCE=999 / ACTION_STEPS=8`，planner 参考 `codex/gpt-5.5/xhigh/max_turns=100`；`validate_target50.py` 离线汇总 → **task_weighted_success_rate**（50 任务种子均值再平均），与官方 leaderboard 的 Atomic-Seen(18)/Composite-Seen(16)/Composite-Unseen(16) 三 split 口径一致。
- **无批量启动器**：340 个 cell 就是 340 次 `rpent --robot robocasa` 单任务命令，validator 事后扫 result.json。

### RoboCasa 官方规范（核证）

RoboCasa365（ICLR'26, arXiv:2603.04356）：365 任务、2500 厨房、PandaOmron 12-DOF 移动操作臂。官方 target 评测 = 50 任务 × 10 held-out 厨房；官方指标 = average task success rate；`_check_success()` 是官方成功判据。RLDX-1（RLWRLD，6.9B，Qwen3-VL-8B backbone）官方裸 eval ≈32-36%；RPent 自报 Harness-VLA + codex/gpt-5.5 = 57.1% Overall（92.0/61.0/13.8 三 split）——**harness 层贡献约 +20 个点，这正是要测 HomeMaster 的部分**（自报值，未独立复跑）。

---

## 三、架构对标与差距

### 能力对照表

| 维度 | RPent | HomeMaster 现状 | 判定 |
|---|---|---|---|
| Agent 决策/规划 | planner 协议 + 4 后端；LLM 自主工具循环 | vendored AgentScope ReAct loop + `task_planner`/`task_progress_check` 工具 | ✅ 对等 |
| 环境交互/工具执行 | `Toolkit.execute_tool` 单锁 + 自动回采观测 | `ToolExecutor` + `ApplicationToolExecutor` + 权限/lease/冲突串行（`resource_key`） | ✅ 更强（有权限/lease） |
| 观测进上下文 | tool result 携带 state + base64 图片块 | tool result `images: tuple[ResultImage]` → DataBlock（`tools/contracts.py`、`substrate/toolkit.py:130`） | ✅ 同构；注意 `keep_recent_images=2` |
| 视觉/动作执行 | RLDX-1 VLA 进程（predict/reset_session RPC） | 无 | ❌ 需复用 RPent 栈 |
| 技能原语 | primitives.py（OSC servo/Jacobian 标定/导航闭环/desync 守护） | 无 | ❌ 需复用或移植 |
| 成功判定 | `solved()`=env `_check_success()` | `external_terminal_owner` + `stop_condition` 读 `state.won` | ✅ 语义一致 |
| Episode/评测 | 单 cell CLI + EnvState/states.json + result.json + 离线 validator | `BenchmarkApplicationLifecycle` + episode runner + trace/summary/trajectory_memory | ✅ 同构，HomeMaster 更重 |
| 失败归因 | `termination_reason`（completed/planner_timeout/infrastructure_error）+ `valid` | `classification`（agent_success vs harness/provider/runtime failure）+ `score_eligible` | ✅ 更细 |
| 记忆 | 文件 bundle 作为 planner 先验 | MindMemOS(Neo4j/Qdrant) + trajectory_memory + session finalizer | ⚠️ 语义不同，见 §六 |
| 进程隔离 | env/vla server 独立进程，HTTP RPC | `workers/alfworld_worker` NDJSON 子进程模板 | ✅ 模板现成 |
| profile 注册 | `RobotSpec` 插件式 `importlib` 发现 | `profile` Literal 硬编码 3 值（`adapters/profiles.py:90`） | ⚠️ 需加 `"robocasa"`（几处一行级改动） |

### 差距清单（按档位）

**可直接复用（零改动）**：Agent loop/预算/取消、`RunRequest.environment` borrowed 注入、stop_condition/external_terminal_owner、工具执行链（权限/lease/冲突串行/typed receipt/outcome_unknown）、ContextAssembler、session 持久化/generation fencing、隔离 worker NDJSON 模板、benchmark lifecycle/runner 模式、trajectory→MindMemOS 入库、device read/control 权限域。

**需适配（接缝存在）**：`RoboCasaBackend` 契约（仿 `ThorBackend`）、`profile="robocasa"` 注册（`adapters/profiles.py` + `composition/base.py` Literal + `_bind_backend` 加 `deps["robocasa_env"]`）、`robocasa_*` 工具集、多相机观测（`observe` 当前单图 `screenshot()`，RoboCasa 需多图 STANDARD 投影）、env state 模型可见化投影、`RoboCasaTerminalOwner.succeeded = env._check_success()`、`RoboCasaTraceWriter`/EpisodeOutcome、图片保留策略（`keep_recent_images=2` 对多视角核验可能偏小）。

**完全缺失（须自建或复用 RPent）**：robocasa/mujoco/robosuite/RLDX 全部栈、`observe_barrier` 硬屏障（可用"工具结果自动带观测"替代）、连续控制（**不应**让模型直出 action vector——VLA 必须在工具内部）。

---

## 四、高层架构对比图

```
RPent（现状）                          HomeMaster（推荐接法）
─────────────────────────            ─────────────────────────────
planner (codex/claude/api)             AsAgentRuntime (vendored AS ReAct)
  │ tool calls (15 tools)                │ tool calls (robocasa_* tools)
  ▼                                      ▼
Toolkit.execute_tool                   ApplicationToolExecutor
  │  +auto get_env_state                 │  +observer/completion_guard/
  ▼                                      │   permission/lease
RoboCasaPrimitives                     RunContext deps: robocasa_env
  ├─ rldx_skill 闭环                     ▼
  ├─ OSC servo primitives              RoboCasaWorkerClient (NDJSON stdio)
  └─ navigate_to 闭环                    │  ← 仿 workers/alfworld_worker
  │   │                                  ▼
  ├─→ env_client ──HTTP──→ env_server  robocasa_worker 进程 (RPent venv)
  │       MuJoCo/EGL/RoboCasa365            ├─ RPent primitives 原样复用
  ├─→ vla_client ──HTTP──→ vla_server       ├─→ env_server (MuJoCo/EGL)
  │       RLDX-1-FT-RC365                   └─→ vla_server (RLDX-1)
  ▼                                      ▼
solved()=extras["success"]            stop_condition + terminal_owner
  = env._check_success()                = env._check_success()
  ▼                                      ▼
result.json + validate_target50       RoboCasaBenchmarkRunner + trace/
                                      summary + trajectory_memory
```

**两条路线本质一样**：高层 agent ↔ 离散工具 ↔ （RPent 机器人栈）↔ env/VLA 两个隔离进程 ↔ 环境权威成功判定。HomeMaster 侧唯一新增的是"工具 → RPent primitives"这一段 plumbing。

---

## 五、三种接入方案对比

| | 方案 A：最小适配（worker 复用 RPent 栈） | 方案 B：模块扩展（ primitives 移植） | 方案 C：HomeMaster 作 RPent planner backend |
|---|---|---|---|
| 做法 | HomeMaster 新增 `robocasa/` 包 + `robocasa_worker` 子进程；worker 跑在 RPent venv 里直接 `import robots.robocasa.*` 复用 primitives/env_client/vla_client | 把 primitives/RLDXSkill/OSC 标定/world_map 反投影移植成 HomeMaster 原生模块，env/VLA 仍走 RPent server 或自包 | 给 RPent 的 `Planner` 协议（`planner/base.py:58`）加一个 `homemaster` 后端，solve() 内部转发给 HomeMaster runtime |
| 新增代码 | HomeMaster 侧 ~适配层（参照 alfworld/ 体量）；worker 是薄壳 | A 的全部 + 移植 ~2000 行机器人专用代码 | 仅一个 planner backend + 工具桥接 |
| HomeMaster 核心改动 | profile 注册点（几处） | 同 A | **零** |
| 架构侵入 | 低 | 低-中 | 无（但也没接入 HomeMaster 能力） |
| 能力边界 | 评测语义与 RPent Target50 完全对齐；VLA/技能行为 = RPent 原样 | 可做更细粒度控制（chunk 级干预、自定义观测流） | HomeMaster 的 memory/permission/web UI/Runtime 全都没被测——只测了"模型经 HomeMaster 调用" |
| 主要风险 | worker↔RPent 内部 API 的边界需逐一核实（无正式 RPC contract） | 移植引入 bug（Jacobian 标定/desync/动作 unmap 都是踩过坑的代码，`primitives.py` 注释为证） | 结论解释力弱：测出来的是"HomeMaster 壳 + RPent 躯壳" |
| 适合 | **主方案** | A 验证通过后、需要更深控制时再考虑 | **对照锚点**：快速拿一个"HomeMaster loop 在 RPent harness 里的参考分" |

**推荐：A 为主线，C 可作为廉价对照实验顺带做（如果动机是拿锚点数字）。B 暂不做。**

方案 A 里还有一个子选择值得点出：worker 复用 RPent primitives 的前提是 `import rpent`/`import robots.robocasa` 在 `.venv-robocasa` 里可用（`rpent.egg-info` 存在、`robots/` 经 sys.path 注入，`rpent/robots/base.py:36-41`——大概率成立，MVP 第一步就验证）。若不成立，退化为"worker 内直接调 env_server/vla_server 的 HTTP RPC + 自写薄 skill 循环"——代码量仍可控，但失去 primitives 复用。

---

## 六、评测有效性分析（关键问题）

1. **实际测的是什么**：固定 VLA checkpoint/env split/seeds/memory 后，HomeMaster 被测的是**高层 agent 能力**——任务分解、技能选择（rldx_skill vs 手动原语 vs navigate）、失败恢复（VLA 没成功时换策略）、观测使用（world_map/反投影定位）、记忆利用、终止判断。底层感知与连续控制是共享基建，不在被测面。
2. **能力归因**：VLA（RLDX-1-FT-RC365@587e9ecd）+ 环境（RLinf fork @rpent 分支，需 `uv pip freeze` 固化）+ seeds + 任务记忆 bundle 全部固定 → 成功率差异可归因到 agent/harness 层。可加消融：无记忆、无 world_map 工具、不同 planner 模型。
3. **公平 baseline**：直接用 RPent 自报的 codex/gpt-5.5 Target50 v2 结果做参考（57.1%），或本机用 RPent 原样跑几个 cell 校准基建。注意 `validate_target50.py:98-108` **校验 planner 元数据必须等于 manifest 的 planner_reference**——HomeMaster 的结果按官方口径属于"非参考提交"，要么 fork validator 放宽该检查，要么统计时注明。
4. **任务集合/指标**：可以直接吃 `target50_v2.json` 的 340 cells 清单与 `task_weighted_success_rate` 指标；和 RPent 对比时用同口径。
5. **记忆语义差异**（须用户决策）：RPent 的记忆是给 planner 的**策略先验**（task recipe + global lessons，人工/探索产出，静态注入 prompt）；HomeMaster MindMemOS 是**运行时经验库**（episode 后自动抽取）。要可比，初版应直接喂 RPent-memory bundle 给 HomeMaster（按路径注入 prompt 或转成 recall 数据）；用 MindMemOS 自动积累做"增强变量"单测。
6. **最小验证任务**：选 1 个 **atomic-seen** 任务（单技能 pick-place 类，episode 短），1-2 seeds。composite 任务动辄上千步+3600s 超时，不适合 smoke。

---

## 七、推荐技术方案与改造清单

### 目标形态（方案 A）

```
homemaster benchmark --profile robocasa --task <T> --split target --seed <N>
  → RoboCasaBenchmarkRunner (仿 alfworld/benchmark/runner.py)
    → BenchmarkApplicationLifecycle (复用, profile="robocasa")
    → robocasa_worker 子进程 spawn (cwd=RPent, venv=.venv-robocasa,
       NDJSON ops: reset/observe/skill/navigate/world_map/check_success/close)
        └─ 内部: RPent env_client/vla_client/Primitives + spawn env/vla server
    → RunRequest(environment=adapter(borrowed),
                 run_policy=RunPolicy(max_tool_iterations≈100,
                                      stop_condition=env success/done),
                 dependencies={robocasa_env, robocasa_trace,
                               tool_dispatch_observer,
                               external_terminal_owner, ...})
```

### HomeMaster 侧改造清单（全部新增；核心改动仅限注册点）

新增 `src/homemaster/robocasa/`（仿 `alfworld/` 结构）：
- `backend.py`：`RoboCasaBackend` Protocol（reset/observe/skill/check_success/close → typed receipt）
- `worker_client.py` + `workers/robocasa_worker/main.py`：NDJSON 协议（照抄 alfworld worker 的 1MB 行上限/stderr 隔离/artifact+SHA256 图传/SIGINT 语义）
- `benchmark/adapter.py`：`RoboCasaEnvAdapter`（`backend_id` + `current_state` → `{task, obs_text, camera_refs, done, success, step, invalid_count}`）
- `tools.py`：`robocasa_observe`（多相机 STANDARD 投影）、`robocasa_skill`（→ rldx_skill）、`robocasa_navigate`、`robocasa_manipulate`（→ OSC 原语）、`robocasa_world_map`、`robocasa_finish`；`resource_key="robocasa:backend"` 保证串行
- `tracing.py`：`RoboCasaToolDispatchObserver`（terminal 短路/计数/异常→runtime_failure，照抄 Alfworld 版）
- `benchmark/runner.py` + `taskset`：读 `target50_v2.json` cell 清单
- `prompt.py`：episode prompt（任务语言 + 工具面 + 成功判据声明）

注册点小改（每处一行级）：
- `adapters/profiles.py:90`：`build_tool_registry` 环境枚举 + `"robocasa"`
- `application/composition`：`profile` Literal + `"robocasa"`
- `application/tool_executor.py:327` `_bind_backend`：`deps["robocasa_env"] = backend`
- 可选：`keep_recent_images` 从 `config.py:264` 硬编码 2 提为可配置

### 服务端（hkust4，零改动复用）

已就绪：`.venv-robocasa`（py3.10/torch2.7+cu126/robocasa 1.0.1/mujoco 3.3.1）、`~/.robocasa/assets` 33G、`checkpoints/rldx-1-ft-rc365` 13G、3×A6000、`activate-robocasa.sh`（注意其中 "assets TODO" 注释已过期，资产实际已补齐）。

### 不做的事

- 不让模型直出连续 action vector（架构上也不支持，且违背两边共同的职责分离）。
- 不在 HomeMaster 主 venv 装 mujoco/robosuite/torch。
- 不恢复 observe_barrier（用"工具结果自动携带观测"替代；若后续要硬序再补 middleware）。

---

## 八、最小验证方案（若批准实施，第一步）

1. **hkust4 上证明 worker 可行**：在 `.venv-robocasa` 里手写最小脚本：`import robots.robocasa.*` → spawn env_server + vla_server → `env.reset` → `rldx_skill` 一次 → `env.check_success`。验证"RPent primitives 可被外部驱动"这一个 linchpin 假设（不做这个其余全是纸上谈兵）。
2. **HomeMaster 单工具打通**：只实现 `robocasa_observe` + `robocasa_skill` + `robocasa_verify` 三个工具 + adapter + terminal_owner，跑**一个 atomic cell**。验收=episode 端到端走完、env success 落盘、stderr 无 traceback——不看通过率。
3. **小样本校准**：同 1 任务跑 3-5 seeds，对比 RPent 同 cell 的基建开销与成功率分布，确认无系统性劣势。
4. 之后才谈 Target50 340 cells 全量。

---

## 九、待你决策的问题

1. **评测目标定位**：要的是"HomeMaster agent 能力在标准具身 benchmark 上的可比数字"（→ 严格吃 RPent 协议+记忆 bundle）还是"HomeMaster 全栈（含 MindMemOS 记忆）在具身任务上的表现"（→ 记忆语义要转换，结论口径不同）？两者不矛盾但第二轮才有意义。
2. **部署形态**：整条链跑在 hkust4（HomeMaster 装成独立 venv，env/VLA 用 GPU）几乎是唯一现实选项；本机 Mac 只能做离 env 的开发。确认是否接受"HomeMaster 本体也部署在 hkust4"。
3. **Planner 模型对齐**：RPent 参考值是 codex/gpt-5.5。HomeMaster 跑哪个 provider？要严格可比需同模型；若 HomeMaster 的价值在于"任意模型"，报 per-model 表格即可，但需声明非参考提交口径。
4. **与 RPent 上游的关系**：worker 依赖 `robots.robocasa` 内部 API（非稳定 contract），RPent 分支推进会打破——接受 pin 当前 commit（0ae9f9e）还是需要向 RPent 提稳定接口？
5. **observe 硬屏障**：接受 RPent 式"结果自动带观测"（推荐，省一个 middleware）还是要把 `observe_barrier.py` 补回来？
6. **多相机/图片预算**：`keep_recent_images=2` 是否需要为 RoboCasa 调大（视觉核验类任务可能需要同时看 agentview+wrist）。

---

## 十、验证与决策记录（2026-10-14 更新）

**真机 smoke 已通过（hkust4，OpenDrawer/target/seed 1，GPU 2）**：外部脚本驱动 `_init_runtime` 拉起 env_server+vla_server（176s）→ env.reset → `task_language="Open the left drawer."` → `rldx_skill` 真实执行 5 chunks × 8 env.step → `check_success` 正常返回 → 全部进程干净回收。**方案 A 的 linchpin 假设（RPent primitives 可被外部驱动）已验证。**

运维事实：hkust4 访问 huggingface.co 被 reset，worker 启动环境必须带 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`（缓存已热，离线可用）；单张 A6000 可同时装下 VLA(~15.5G)+EGL(~1.7G)。

**用户已拍板**：
- 模型 = **Mimo**（默认 provider，Anthropic 协议；**用户确认支持图像输入** → observe 工具回传真实相机帧）
- 记忆 = **MindMemOS 必须接入**——评测目标就是检验记忆系统在具身任务上是否成立。采用 HomeMaster 原生闭环：episode → SessionFinalizer 抽取轨迹经验入库 → 下集 AutomaticRecall 注入。评测设计含 `memory_mode=full` vs `disabled` **消融臂**以量化记忆增益，外加写入质量与召回相关性两层检查。
- 可比性口径：Mimo+MindMemOS 的结果与 RPent 自报 57.1% 不可比（模型/记忆均不同），目的是验证 HomeMaster 全栈；如需官方口径对照可另跑 codex 锚点。

---

## 十一、证据边界

- RPent 侧事实全部来自 `rpent-ref` 工作树静态核查（commit 0ae9f9e）+ 官方 RoboCasa/RLDX-1 公开资料；57.1%/92.0% 等成绩为项目自报，未独立复跑。
- HomeMaster 侧事实来自 `v35-architecture` 分支源码核查；provider 对图片 DataBlock 的 wire 兼容性、`import rpent` 在 `.venv-robocasa` 的可用性、`_check_success` 在 RLinf fork 的确切签名标注为 UNVERIFIED，均在 MVP 第一步真环境核对。
- 本评估未修改、安装、运行任何代码与环境。
