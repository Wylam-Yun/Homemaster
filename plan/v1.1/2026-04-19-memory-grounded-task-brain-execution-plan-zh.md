# Memory-Grounded Task Brain v1.1 Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 HomeMaster 的 memory-grounded embodied task brain CLI MVP，先打穿 Phase A 主链，再按 Phase B 增强 LLM、仿真迁移和 gateway。

**Architecture:** HomeMaster 是独立 Python 项目，不在 RoboOS 内部开发。Task Brain 使用粗粒度 LangGraph 编排阶段，所有外部能力通过 skill-compatible capability contract 接入；当前任务执行状态统一由 runtime/task-scoped state 维护，长期 memory 只保存跨任务记忆，trace 只记录事实。Phase A 使用 deterministic planner、mock observation、fake RoboBrain 和 mock executor 打穿闭环；Phase B 再加入 NVIDIA Kimi LLM provider、richer memory、simulator-style wrapper 和 optional gateway。

**Tech Stack:** Python 3.11+、Pydantic v2、LangGraph、Typer、Rich、httpx、pytest、ruff。

---

## 执行总原则

### Phase A 必须先完成

Phase A 的目标是稳定可演示的 CLI 闭环。只做主链必需功能：

- CLI 主链。
- 3 个核心场景：`check_medicine_success`、`check_medicine_stale_recover`、`fetch_cup_retry`。
- rule-first parser。
- deterministic planner。
- object memory。
- task negative evidence。
- standard Observation / VerificationEvidence。
- skill-compatible capability registry。
- verification。
- failure analysis。
- recovery。
- memory reconciliation。

Phase A 完成前，不实现真实 LLM planner、不接 simulator wrapper、不接 Hermes gateway。

### Phase B 主链稳定后再做

Phase B 增强包括：

- richer category prior。
- episodic retrieval weighting。
- NVIDIA Kimi LLM provider。
- invalid LLM plan fallback。
- simulator-style wrapper。
- optional Hermes / Feishu gateway bridge。

Phase B 不能破坏 Phase A 接口和测试。

### 密钥与配置规则

NVIDIA Kimi 只在 Phase B 使用：

```text
base_url = https://integrate.api.nvidia.com/v1
model = moonshotai/kimi-k2.5
api_key_env = NVIDIA_API_KEY
```

不要把 API key 写入任何仓库文件。实现时只允许本地设置：

```bash
export NVIDIA_API_KEY="<local-secret>"
```

或者本地 `.env.local`。`.gitignore` 必须排除：

```gitignore
.env*
```

测试中不调用真实 NVIDIA API，使用 fake provider 或 httpx mock。

## 目标目录结构

```text
/Users/wylam/Documents/workspace/HomeMaster/
  pyproject.toml
  README.md
  .gitignore
  data/
    scenarios/
      check_medicine_success/
      check_medicine_stale_recover/
      fetch_cup_retry/
      object_not_found/
      distractor_rejected/
  src/
    task_brain/
      __init__.py
      adapters/
      capabilities.py
      cli.py
      context.py
      domain.py
      evidence.py
      graph.py
      llm.py
      memory.py
      parser.py
      planner.py
      recovery.py
      trace.py
      verification.py
      world.py
  tests/
```

## Stage 0（Phase A）：仓库初始化与环境准备

### 目标

把 `/Users/wylam/Documents/workspace/HomeMaster` 变成独立、可测试、可安装的 Python 项目。

### 范围

- 初始化 git。
- 添加 Python packaging。
- 配置基础依赖。
- 添加 README 和 `.gitignore`。
- 不引入 RoboOS runtime。
- 不引入 Hermes runtime。

### 实现细节

- 在 HomeMaster 根目录创建 `pyproject.toml`。
- 项目名使用 `elder-task-brain`。
- 包路径使用 `src/task_brain`。
- CLI entry point 使用 `task-brain = "task_brain.cli:app"`。
- 基础依赖：
  - `pydantic>=2.7`
  - `langgraph>=0.2`
  - `typer>=0.12`
  - `rich>=13.7`
  - `httpx>=0.27`
- dev 依赖：
  - `pytest>=8.2`
  - `ruff>=0.6`
- `.gitignore` 必须包含 `.venv/`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`.env*`、`dist/`、`build/`。

### 执行步骤

- [ ] 在 `/Users/wylam/Documents/workspace/HomeMaster` 执行 `git init`，如果已经是 git repo 则跳过。
- [ ] 创建 `.gitignore`，确保 `.env*` 被排除。
- [ ] 创建 `pyproject.toml` 和 `README.md`。
- [ ] 创建 `src/task_brain/__init__.py`。
- [ ] 创建 `tests/` 空目录。
- [ ] 创建虚拟环境并安装开发依赖。

### 测试计划

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
uv pip install ".[dev]" --python .venv/bin/python
python -c "import task_brain; print(task_brain.__version__)"
pytest -q
ruff check .
```

本机 Stage 0 使用非 editable 安装。当前 uv Python 会跳过 setuptools editable install 生成的 hidden `__editable__` `.pth` 文件，导致 `python -I -c "import task_brain"` 失败。后续开发阶段如果需要让 console script 读取最新源码，改完源码后重新执行 `uv pip install ".[dev]" --python .venv/bin/python` 即可；pytest 仍会通过 `pyproject.toml` 的 `pythonpath = ["src"]` 读取源码。

### 完成标准

- `python -c "import task_brain"` 成功。
- `pytest -q` 可以运行，即使暂时没有业务测试。
- `ruff check .` 通过。
- `.env*` 不会进入 git。

### 提交点

```bash
git add .gitignore pyproject.toml README.md src/task_brain/__init__.py tests
git commit -m "chore: initialize task brain project"
```

## Stage 1（Phase A）：项目骨架与核心类型

### 目标

建立 Task Brain 的稳定类型边界，后续所有模块都只依赖这些 typed schemas。

### 范围

创建 `src/task_brain/domain.py`，定义 Phase A 必需模型：

- `TaskRequest`
- `TaskIntent`
- `TargetObject`
- `ParsedTask`
- `Predicate`
- `Observation`
- `ObservedObject`
- `ObservedAnchor`
- `SceneRelation`
- `VerificationEvidence`
- `RobotRuntimeState`
- `RuntimeState`
- `ObjectMemory`
- `Anchor`
- `RelativeRelation`
- `TaskNegativeEvidence`
- `Subgoal`
- `SubgoalType`
- `HighLevelPlan`
- `FailureType`
- `FailureAnalysis`
- `RecoveryAction`
- `RecoveryDecision`
- `CapabilitySpec`
- `TraceEvent`

### 实现细节

- 所有 schema 使用 Pydantic v2。
- `RuntimeState` 是当前任务执行中的唯一状态主语，包含：
  - `current_observation`
  - `selected_candidate_id`
  - `selected_object_id`
  - `retry_budget`
  - `recent_failure_analysis`
  - `task_negative_evidence`
  - `candidate_exclusion_state`
- `ObjectMemory.memory_id` 是长期 ID，不能等于 detector 临时 ID。
- `ObservedObject` 同时保留 `observation_object_id`、`detector_id`、`memory_id`，并在测试中证明三者可区分。
- `CapabilitySpec` 必须包含：
  - `name`
  - `input_schema`
  - `output_schema`
  - `failure_modes`
  - `timeout_s`
  - `returns_evidence`

### 测试计划

创建 `tests/test_domain.py`，覆盖：

- `Predicate.from_list()` 和 `to_list()` round trip。
- `TaskRequest` 保存 source、user_id、utterance。
- `Subgoal` 没有 success conditions 时校验失败。
- `ObservedObject.detector_id` 不等于 `memory_id`。
- `RuntimeState` 持有当前任务状态字段。
- `CapabilitySpec` 缺少 evidence/result contract 时校验失败。

运行：

```bash
pytest tests/test_domain.py -q
```

### 完成标准

- `tests/test_domain.py` 全部通过。
- 其他模块不需要重新定义这些类型。

### 提交点

```bash
git add src/task_brain/domain.py tests/test_domain.py
git commit -m "feat: add core task brain domain models"
```

## Stage 2（Phase A）：Mock World 与标准 Observation

### 目标

实现 mock world truth 和标准 Observation 抽象，确保 Task Brain 不直接消费 mock world 私有字段。

### 范围

创建：

- `src/task_brain/world.py`
- `src/task_brain/evidence.py`
- `src/task_brain/adapters/__init__.py`
- `src/task_brain/adapters/mock_perception.py`
- `tests/test_observation_abstraction.py`

### 实现细节

- `MockWorld` 负责读取 `world.json`、维护 symbolic predicates、查询 objects/furniture/visible_from。
- `MockPerceptionAdapter.observe(world, viewpoint_id)` 返回标准 `Observation`。
- `build_verification_evidence(...)` 返回 `VerificationEvidence`，其中包含 observation、execution result、robot runtime state、task negative evidence。
- Verification public interface 后续只能接收 `VerificationEvidence`，不能要求调用方传 raw world。

### 测试计划

`tests/test_observation_abstraction.py` 覆盖：

- mock perception 返回 `Observation`。
- observation 中 visible object 包含 category、detector_id、memory_id 字段边界。
- verification evidence 可以从 observation + runtime state 构造，不暴露 raw world。

运行：

```bash
pytest tests/test_observation_abstraction.py -q
```

### 完成标准

- Mock world 可读最小 fixture。
- Mock perception 输出标准 observation。
- Task Brain 后续模块有标准 evidence 输入。

### 提交点

```bash
git add src/task_brain/world.py src/task_brain/evidence.py src/task_brain/adapters tests/test_observation_abstraction.py
git commit -m "feat: add mock world and observation abstraction"
```

## Stage 3（Phase A）：Object Memory 与 Task Negative Evidence

### 目标

实现 Phase A 主链必需 memory：object memory、structured retrieval first、task negative evidence、当前任务候选排除。

### 范围

创建：

- `src/task_brain/memory.py`
- `tests/test_memory_retrieval.py`

更新：

- `src/task_brain/domain.py` 中与 memory 相关的 schema。

### 实现细节

- `MemoryStore` 只管理长期 object memory。
- `TaskMemory` 或 `RuntimeState` 管理当前任务 negative evidence。
- `retrieve_candidates(parsed_task, memory_store, runtime_state)` 先按 `object_category` 过滤，再按 alias、location hint、confidence level 排序，最后应用 task negative evidence 排除。
- 当前任务中 `searched_not_found` 的 location 默认排除，除非调用方显式传 `allow_revisit=True`。
- Phase A 不实现 richer category prior 和 episodic weighting；这些放到 Stage 15。

### Memory 更新边界

- Planner 猜测不能更新长期 memory。
- 未验证 execution result 不能更新长期 memory。
- Task negative evidence 默认不写入长期 object memory 主表。

### 测试计划

`tests/test_memory_retrieval.py` 覆盖：

- object memory 使用结构化 anchor。
- object category filter 能返回候选。
- task negative evidence 排除当前任务已搜索未找到的位置。
- negative evidence 不污染长期 object memory。
- `allow_revisit=True` 才允许重新考虑已排除位置。

运行：

```bash
pytest tests/test_memory_retrieval.py -q
```

### 完成标准

- Memory retrieval 可解释。
- Stale-memory recovery 有任务内排除机制。
- 长期 memory 和 task-scoped state 没有混用。

### 提交点

```bash
git add src/task_brain/memory.py tests/test_memory_retrieval.py
git commit -m "feat: add object memory and task negative evidence"
```

## Stage 4（Phase A）：Rule-first Parser 与 Task Context

### 目标

实现第一版中文规则 parser 和 `TaskContext`，为 deterministic planner 提供唯一上下文入口。

### 范围

创建：

- `src/task_brain/parser.py`
- `src/task_brain/context.py`
- `tests/test_parser_context.py`

### 实现细节

- Parser 支持两类 MVP intent：
  - `check_object_presence`
  - `fetch_object`
- 中文规则：
  - “药盒”“看看”“还在” -> check medicine。
  - “水杯”“杯子”“拿给我”“厨房” -> fetch cup。
- `TaskContext` 包含 request、parsed_task、ranked_candidates、current_observation、runtime_state、capability registry、constraints。
- Planner 只能读 `TaskContext`，不能直接读 world、memory store 或 trace。

### 测试计划

`tests/test_parser_context.py` 覆盖：

- parser 提取检查药盒任务。
- parser 提取拿水杯任务。
- task context 包含 runtime state 和候选列表。
- task context 中 negative evidence 来自 runtime state。

运行：

```bash
pytest tests/test_parser_context.py -q
```

### 完成标准

- Parser 不调用 LLM。
- TaskContext 是 planner 的唯一输入。

### 提交点

```bash
git add src/task_brain/parser.py src/task_brain/context.py tests/test_parser_context.py
git commit -m "feat: add rule parser and task context"
```

## Stage 5（Phase A）：Skill-compatible Capability Registry

### 目标

把能力层正式建成可替换 skill contract，确保未来 `mock_vln`、`fake RoboBrain`、`mock_perception` 可替换为团队 VLN/VLA/perception skill。

### 范围

创建：

- `src/task_brain/capabilities.py`
- `tests/test_capabilities.py`

### 实现细节

定义 `default_capability_registry()`，至少包含：

- `mock_vln.navigate`
- `mock_perception.observe`
- `robobrain.plan`
- `mock_atomic_executor.execute`
- `verification.evaluate`
- `recovery.analyze_failure`
- `recovery.decide`
- `memory.reconcile`

每个 capability 都必须声明：

- stable capability name。
- typed input schema。
- typed output schema。
- failure modes。
- timeout。
- evidence-carrying result。

### 测试计划

`tests/test_capabilities.py` 覆盖：

- 每个 adapter-facing capability 有 stable name。
- 每个 capability 有 input/output schema。
- 每个 capability 有 failure modes 和 timeout。
- 每个 adapter-facing capability 的 result 标记 `returns_evidence=True`。

运行：

```bash
pytest tests/test_capabilities.py -q
```

### 完成标准

- Task Brain 只依赖 capability contract。
- 后续替换能力实现不需要改 planner / verification / recovery 主体。

### 提交点

```bash
git add src/task_brain/capabilities.py tests/test_capabilities.py
git commit -m "feat: add skill-compatible capability registry"
```

## Stage 6（Phase A）：Deterministic Planner 与 Plan Validator

### 目标

实现 Phase A 默认 planner 和计划校验器，确保主链不依赖 LLM。

### 范围

创建：

- `src/task_brain/planner.py`
- `tests/test_planner_validation.py`

### 实现细节

- `DeterministicHighLevelPlanner.generate(context)` 支持：
  - check object presence。
  - fetch object。
- 计划只允许 high-level subgoals：
  - `navigate`
  - `observe`
  - `verify_object_presence`
  - `embodied_manipulation`
  - `return_to_user`
  - `report_failure`
  - `ask_clarification`
- 禁止 atomic robot actions，如 `move_arm_to_pregrasp`、`close_gripper`、`lift`。
- `PlanValidator` 检查：
  - plan 有 memory/candidate grounding。
  - 每个 subgoal 有 success conditions。
  - manipulation 之前必须有 object presence verification。
  - final task verification 不能被 plan 省略。
  - 所有 subgoal type 都在 capability registry 支持范围内。

### 测试计划

`tests/test_planner_validation.py` 覆盖：

- check medicine 计划使用 top candidate。
- fetch cup 计划包含 navigate、observe、verify、manipulation、return。
- planner 不发 atomic action。
- validator 拒绝 manipulation-before-verification。
- validator 拒绝没有 memory grounding 的 plan。

运行：

```bash
pytest tests/test_planner_validation.py -q
```

### 完成标准

- Phase A 默认路径无 LLM。
- Plan 执行前必经 validation。

### 提交点

```bash
git add src/task_brain/planner.py tests/test_planner_validation.py
git commit -m "feat: add deterministic planner and validation"
```

## Stage 7（Phase A）：Mock Adapters 与 Fake RoboBrain

### 目标

实现 Phase A 的能力层 mock：导航、感知、RoboBrain 规划、原子执行。

### 范围

创建：

- `src/task_brain/adapters/mock_vln.py`
- `src/task_brain/adapters/robobrain.py`
- `src/task_brain/adapters/mock_atomic_executor.py`
- `tests/test_adapters.py`

### 实现细节

- `MockVLNAdapter.navigate(world, viewpoint_id)` 返回 navigation result，并写入 evidence。
- `FakeRoboBrainClient.plan(request)` 接收 `EmbodiedSubgoalRequest`，返回 `AtomicPlanResponse`。
- `MockAtomicExecutor.execute(plan, runtime_state, world, attempt)` 返回 `ExecutionResult`。
- 所有 adapter result 都必须携带 evidence，供 verification 和 failure analysis 使用。
- failure injection 通过 scenario 的 `failures.json` 控制。

### 测试计划

`tests/test_adapters.py` 覆盖：

- mock VLN 可导航到合法 viewpoint。
- mock perception 已在 Stage 2 验证，但这里要验证 adapter integration。
- fake RoboBrain 接收 embodied subgoal request。
- fake RoboBrain 返回 atomic plan，但 planner 不直接生成这些 atomic actions。
- mock executor 可以成功 apply state delta。
- mock executor 可以按 failure rule 注入失败。

运行：

```bash
pytest tests/test_adapters.py -q
```

### 完成标准

- 所有 adapter-facing result 都能进入 `VerificationEvidence`。
- RoboBrain 是 subgoal planner，不是成功判断者。

### 提交点

```bash
git add src/task_brain/adapters tests/test_adapters.py
git commit -m "feat: add mock adapters and fake robobrain"
```

## Stage 8（Phase A）：Verification Engine

### 目标

实现 verification before success，所有成功判断都基于标准 evidence。

### 范围

创建：

- `src/task_brain/verification.py`
- `tests/test_verification.py`

### 实现细节

Verification engine 接收 `VerificationEvidence`，支持：

- arrival verification。
- object presence verification。
- manipulation verification。
- final task verification。

成功条件示例：

- `at(robot, viewpoint)`
- `visible_category(category)`
- `reachable_category(category)`
- `holding_category(robot, category)`
- `near(robot, user)`

### 测试计划

`tests/test_verification.py` 覆盖：

- 从 observation 验证 visible category。
- missing visible category 返回 failed conditions。
- 从 runtime state 验证 holding category。
- execution result alone 不等于 success。
- final success 必须经过 final task verification。

运行：

```bash
pytest tests/test_verification.py -q
```

### 完成标准

- 任何 subgoal success 都必须来自 verification。
- final response success 必须晚于 final task verification。

### 提交点

```bash
git add src/task_brain/verification.py tests/test_verification.py
git commit -m "feat: add evidence-based verification"
```

## Stage 9（Phase A）：Failure Analysis 与 Recovery

### 目标

把 recovery 从 attempt-based 升级为 failure-type-aware。

### 范围

创建：

- `src/task_brain/recovery.py`
- `tests/test_failure_analysis.py`
- `tests/test_recovery.py`

### 实现细节

Failure types：

- `navigation_failure`
- `object_presence_failure`
- `manipulation_failure`
- `final_task_failure`

Recovery actions：

- `continue`
- `retry_same_subgoal`
- `switch_candidate`
- `re_observe`
- `replan`
- `ask_clarification`
- `report_failure`

规则：

- object presence failure：写 task negative evidence，排除当前 candidate，切换下一个 candidate。
- manipulation failure 且目标仍可见：最多 retry 1 次。
- manipulation failure 且目标状态变化：re-observe，再 replan。
- candidate exhausted：ask clarification 或 report failure。
- final task failure：replan 或 failure path，不允许 fabricate success。

### 测试计划

`tests/test_failure_analysis.py` 和 `tests/test_recovery.py` 覆盖：

- object presence failure 触发 switch candidate 和 task negative evidence。
- manipulation failed + object still visible 触发 retry。
- manipulation failed + object missing 触发 re-observe/replan。
- candidate exhausted 触发 report failure。
- final task failure 不允许 success。
- 相同 failed condition 在不同 evidence 下得到不同 recovery action。

运行：

```bash
pytest tests/test_failure_analysis.py tests/test_recovery.py -q
```

### 完成标准

- Recovery 决策基于 failure analysis，而不是只看 attempt 数。
- Runtime state 保存 recent failure analysis 和 retry budget。

### 提交点

```bash
git add src/task_brain/recovery.py tests/test_failure_analysis.py tests/test_recovery.py
git commit -m "feat: add failure-aware recovery policy"
```

## Stage 10（Phase A）：Scenario Fixtures

### 目标

提供可重复、可解释的 mock household 场景，支撑 CLI demo 和 scenario tests。

### 范围

创建：

- `data/scenarios/check_medicine_success/world.json`
- `data/scenarios/check_medicine_success/memory.json`
- `data/scenarios/check_medicine_success/failures.json`
- `data/scenarios/check_medicine_stale_recover/*`
- `data/scenarios/fetch_cup_retry/*`
- `data/scenarios/object_not_found/*`
- `data/scenarios/distractor_rejected/*`
- `tests/test_scenario_fixtures.py`

### 实现细节

先完成 3 个核心场景：

- `check_medicine_success`：药盒记忆正确，final verification 成功。
- `check_medicine_stale_recover`：top memory 过期，第一个位置 searched_not_found，写 task negative evidence，切换候选成功。
- `fetch_cup_retry`：水杯可见，第一次 executor 注入 `object_slipped`，目标仍可见，retry 成功。

再完成 hardening 场景：

- `object_not_found`：所有候选都没有目标，report failure。
- `distractor_rejected`：可见 distractor，但不能当作目标，不调用 RoboBrain pickup。

### 测试计划

`tests/test_scenario_fixtures.py` 覆盖：

- 每个 scenario 都有 world/memory/failures 文件。
- 每个 memory fixture 使用结构化 anchor。
- stale 场景的 top memory 与 world truth 冲突。
- fetch retry 场景有 failure injection rule。
- distractor 场景没有 target category 对象。

运行：

```bash
pytest tests/test_scenario_fixtures.py -q
```

### 完成标准

- Fixtures 能支撑 Stage 11 graph tests。
- World truth、long-term memory、failure injection 分离。

### 提交点

```bash
git add data/scenarios tests/test_scenario_fixtures.py
git commit -m "test: add task brain scenario fixtures"
```

## Stage 11（Phase A）：Coarse-grained LangGraph 主链

### 目标

实现真实 compiled LangGraph，打通 parse -> retrieve -> context -> plan -> validate -> execute -> verify -> recover -> update -> respond。

### 范围

创建：

- `src/task_brain/graph.py`
- `tests/test_graph_scenarios.py`

### 实现细节

Graph nodes：

```text
input_instruction
parse_instruction
retrieve_memory
build_task_context
generate_plan
validate_plan
execute_subgoal_loop
final_task_verification
update_memory
respond_with_trace
```

`execute_subgoal_loop` 内部处理：

- select candidate。
- call adapter。
- collect evidence。
- verify subgoal。
- analyze failure。
- decide recovery。
- retry / switch / replan / report failure。

State rules：

- `RuntimeState` 是唯一执行状态源。
- Trace 只记录，不驱动状态。
- Long-term memory 只在 reconciliation 阶段更新。

### 测试计划

`tests/test_graph_scenarios.py` 覆盖：

- `build_task_graph()` 返回可 invoke 的 compiled graph。
- trace 中 `retrieve_memory` 早于 `generate_plan`。
- `check_medicine_success` 返回 success。
- `check_medicine_stale_recover` 包含 `write_task_negative_evidence` 和 `recovery_switch_candidate`。
- `fetch_cup_retry` 包含 `call_robobrain_planner`、两次 `execute_atomic_plan`、`recovery_retry_same_subgoal`。
- `object_not_found` 返回 failed。
- `distractor_rejected` 不调用 RoboBrain pickup。
- success 一定晚于 final task verification。

运行：

```bash
pytest tests/test_graph_scenarios.py -q
```

### 完成标准

- 三个核心场景能通过 graph runner。
- Graph 是真实 LangGraph，不是手写假 graph。
- Trace 证明关键顺序。

### 提交点

```bash
git add src/task_brain/graph.py tests/test_graph_scenarios.py
git commit -m "feat: orchestrate task brain with langgraph"
```

## Stage 12（Phase A）：CLI Trace Demo

### 目标

实现可演示 CLI，输出可审计 trace，并支持 JSONL trace 写出。

### 范围

创建：

- `src/task_brain/cli.py`
- `src/task_brain/trace.py`
- `tests/test_cli.py`

### 实现细节

CLI 命令：

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
```

支持参数：

- `--scenario`
- `--instruction`
- `--trace-jsonl`：可选，写 JSONL trace。

Trace 必须包含：

- input instruction。
- parse instruction。
- retrieve memory。
- build task context。
- generate plan。
- validate plan。
- adapter calls。
- observation。
- verification。
- failure analysis。
- recovery decision。
- final task verification。
- memory reconciliation。
- response。

### 测试计划

`tests/test_cli.py` 覆盖：

- CLI 能跑 `check_medicine_success`。
- CLI 输出包含 retrieve/generate/final verification。
- failed task exit code 非 0。
- stale memory 场景 trace 包含 failure analysis。
- `--trace-jsonl` 写出 JSONL 文件。

运行：

```bash
pytest tests/test_cli.py -q
```

### 完成标准

- CLI 是主要 demo 入口。
- CLI demo 不需要真实机器人、真实 VLM 或真实 LLM。

### 提交点

```bash
git add src/task_brain/cli.py src/task_brain/trace.py tests/test_cli.py
git commit -m "feat: add CLI trace demo"
```

## Stage 13（Phase A）：Memory Reconciliation

### 目标

实现长期 memory 的更新、降级、冲突标记规则，保证 memory 不被 planner 猜测或 task negative evidence 污染。

### 范围

修改：

- `src/task_brain/memory.py`
- `src/task_brain/graph.py`

创建：

- `tests/test_memory_reconciliation.py`

### 实现细节

更新旧 object memory 的条件：

- evidence 来自 verified observation 或 verified execution evidence。
- 新 evidence 与旧 memory_id 明确匹配，或 category/anchor/relation 足以判定同一长期实例。
- category 一致。

新建 object memory 的条件：

- evidence 已验证。
- 无可匹配旧 memory。
- 新 memory_id 不能直接使用 detector_id。

冲突规则：

- 当前任务中 verified not found：写 task negative evidence，当前任务排除 candidate。
- 长期 memory 不直接删除。
- verified conflict：将旧 memory 降级，`belief_state` 标为 `stale` 或 `contradicted`。
- 弱证据或不完整 observation：保留旧 memory，只降低 confidence 或等待更多 evidence。

优先级：

```text
verified current observation
  > task negative evidence / candidate exclusion
  > active object memory
  > category prior
  > episodic weak hint
```

### 测试计划

`tests/test_memory_reconciliation.py` 覆盖：

- verified observation 更新 object memory。
- unverified planner guess 不更新 memory。
- task negative evidence 不写入 object memory 主表。
- verified not found 将旧 memory 标为 stale/contradicted。
- detector_id 不会成为 memory_id。

运行：

```bash
pytest tests/test_memory_reconciliation.py -q
```

### 完成标准

- Memory update 只基于 verified evidence。
- Stale-memory scenario 既能当前任务排除，也能长期 memory 降级。

### 提交点

```bash
git add src/task_brain/memory.py src/task_brain/graph.py tests/test_memory_reconciliation.py
git commit -m "feat: add memory reconciliation rules"
```

## Stage 14（Phase A）：Quality Gate

### 目标

确认 Phase A 主链稳定，可以进入 Phase B。

### 范围

- 全量测试。
- Ruff。
- 三个核心 CLI demo。
- 两个 hardening scenario。
- README 更新。

### 执行命令

```bash
pytest -q
ruff check .
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

### 必查 trace

- `retrieve_memory` 早于 `generate_plan`。
- `validate_plan` 早于 `execute_subgoal_loop`。
- `final_task_verification` 早于 success response。
- stale memory 场景包含 `write_task_negative_evidence` 和 `recovery_switch_candidate`。
- cup retry 场景包含 `post_action_verification_failed` 和 `recovery_retry_same_subgoal`。

### 完成标准

- 全量测试通过。
- lint 通过。
- 三个核心 CLI demo 成功。
- hardening scenario 通过。
- README 记录安装、测试、demo 命令。

### 提交点

```bash
git add README.md
git commit -m "docs: document phase a demo and quality gate"
```

## Stage 15（Phase B）：LLM / Richer Memory

### 目标

在不破坏 Phase A 主链的前提下，加入 richer memory retrieval 和 NVIDIA Kimi LLM provider。

### 范围

创建或修改：

- `src/task_brain/llm.py`
- `src/task_brain/planner.py`
- `src/task_brain/memory.py`
- `src/task_brain/context.py`
- `tests/test_phase_b_memory_planner.py`
- `tests/test_kimi_provider.py`

### Richer Memory 实现细节

新增：

- category prior memory。
- episodic retrieval weighting。

规则：

- category prior 只在 active object memory 不足或 stale/contradicted 时补充候选。
- episodic retrieval 只作为 ranking hint，不覆盖 task negative evidence。
- task negative evidence 仍是当前任务最高优先级排除规则。

### NVIDIA Kimi Provider 实现细节

Provider 配置：

```text
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=moonshotai/kimi-k2.5
NVIDIA_API_KEY=<read from environment only>
```

实现建议：

- `KimiPlanProvider` 使用 `httpx.Client` 调用 OpenAI-compatible `/chat/completions`。
- 从环境变量读取 `NVIDIA_API_KEY`。
- 如果没有 `NVIDIA_API_KEY`，provider 初始化失败，但 deterministic planner 仍可运行。
- 测试不访问真实 API，使用 fake transport 或 fake provider。
- LLM 输出必须经过 `HighLevelPlan` schema validation。
- invalid LLM plan fallback deterministic planner。

### 测试计划

`tests/test_phase_b_memory_planner.py` 覆盖：

- category prior 在 instance memory 缺失时提供候选。
- episodic hint 提升排序但不覆盖 negative evidence。
- invalid LLM plan fallback deterministic planner。
- LLM plan 不能忽略 task negative evidence。

`tests/test_kimi_provider.py` 覆盖：

- provider 从 env 读取 API key。
- provider 使用 base URL `https://integrate.api.nvidia.com/v1`。
- provider 使用 model `moonshotai/kimi-k2.5`。
- fake chat completion 可转换为 `HighLevelPlan`。

运行：

```bash
pytest tests/test_phase_b_memory_planner.py tests/test_kimi_provider.py -q
```

### 完成标准

- Phase A 全量测试仍通过。
- 无 API key 时 CLI 主链仍使用 deterministic planner。
- API key 不进入 git。

### 提交点

```bash
git add src/task_brain/llm.py src/task_brain/planner.py src/task_brain/memory.py src/task_brain/context.py tests/test_phase_b_memory_planner.py tests/test_kimi_provider.py
git commit -m "feat: add phase b llm provider and richer retrieval"
```

## Stage 16（Phase B）：Simulator Wrapper 与 Optional Gateway

### 目标

验证 Task Brain 接口能迁移到 simulator-style observation，并提供可选 Hermes/Feishu gateway bridge。

### 范围

创建：

- `src/task_brain/adapters/simulator_style.py`
- `src/task_brain/gateway.py`
- `tests/test_simulator_readiness.py`
- `tests/test_gateway.py`

### Simulator Wrapper 实现细节

`SimulatorEvent` 包含：

- agent pose。
- room。
- visible objects。
- object states。
- metadata。

`SimulatorStyleAdapter.to_observation(event)` 输出标准 `Observation`。

规则：

- planner 接口不变。
- verification 接口不变。
- raw simulator payload 只进入 `raw_ref`。

### Gateway 实现细节

Gateway 只做：

- message intake 转 `TaskRequest`。
- user/session identity。
- allowlist。
- trace summary reply。

Gateway 不做：

- planning。
- memory update。
- recovery。
- robot execution。

Hermes 参考文件：

```text
/Users/wylam/.hermes/hermes-agent/gateway/run.py
/Users/wylam/.hermes/hermes-agent/gateway/platforms/base.py
/Users/wylam/.hermes/hermes-agent/gateway/platforms/feishu.py
/Users/wylam/.hermes/hermes-agent/gateway/session.py
```

### 测试计划

`tests/test_simulator_readiness.py` 覆盖：

- simulator-style event 转标准 Observation。
- planner 接收 simulator observation 构造的 context。
- verification 接收 simulator observation evidence。

`tests/test_gateway.py` 覆盖：

- gateway event 转 `TaskRequest`。
- 非 allowlisted user 被拒绝。
- failed trace 可生成可读 summary。

运行：

```bash
pytest tests/test_simulator_readiness.py tests/test_gateway.py -q
```

### 完成标准

- Phase A tests 仍全部通过。
- Simulator wrapper 不改变 Task Brain 主接口。
- Gateway 是可选桥接，不影响 CLI。

### 提交点

```bash
git add src/task_brain/adapters/simulator_style.py src/task_brain/gateway.py tests/test_simulator_readiness.py tests/test_gateway.py
git commit -m "feat: add simulator wrapper and optional gateway bridge"
```

## 最终验收清单

- [ ] Phase A 主链已完成，Phase B 未阻塞 Phase A。
- [ ] `pytest -q` 通过。
- [ ] `ruff check .` 通过。
- [ ] 三个核心 CLI demo 成功。
- [ ] Trace 证明 retrieve memory before planning。
- [ ] Trace 证明 verification before success。
- [ ] Runtime/task-scoped state 是唯一执行状态源。
- [ ] Task negative evidence 不污染长期 object memory。
- [ ] Long-term memory 只基于 verified evidence 更新。
- [ ] Adapter-facing capabilities 都满足 skill-compatible contract。
- [ ] Kimi API key 只从 `NVIDIA_API_KEY` 读取，未写入仓库。
- [ ] Phase B LLM provider 缺 key 时不会破坏 deterministic CLI 主链。
