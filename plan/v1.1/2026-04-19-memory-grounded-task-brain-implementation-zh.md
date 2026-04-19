# Memory-Grounded Task Brain v1.1 中文实施计划

日期：2026-04-19

状态：中文版评审稿

对应设计文档：

```text
plan/v1.1/2026-04-19-memory-grounded-task-brain-design-zh.md
```

## 目标

构建一个 CLI-first 的 memory-grounded embodied task brain MVP。它使用 mock world、mock observation、fake RoboBrain 和 mock executor 验证高层任务闭环：

```text
parse instruction
  -> retrieve memory
  -> build task context
  -> generate / validate high-level plan
  -> execute subgoal loop
  -> verify
  -> analyze failure
  -> recover / replan
  -> update memory
  -> respond with trace
```

v1.1 的重点不是接真实机器人，也不是证明真实视觉能力，而是把接口、记忆、恢复策略和测试边界打牢。

实施优先级必须拆成 Phase A / Phase B：先把可演示主链打穿，再补增强项。

## 技术栈

- Python 3.11+
- Pydantic v2
- LangGraph
- Typer
- Rich
- httpx
- pytest
- ruff

## 项目基线

主项目：

```text
/Users/wylam/Documents/workspace/HomeMaster
```

实现应在 HomeMaster 中创建独立 Python 包，不要在 RoboOS 内部改。

参考项目：

```text
/Users/wylam/Documents/workspace/RoboOS
/Users/wylam/.hermes/hermes-agent
```

RoboOS 只参考架构思想。Hermes 只参考 gateway，不接入 agent runtime。

## 目录结构目标

```text
HomeMaster/
  pyproject.toml
  README.md
  data/
    scenarios/
      check_medicine_success/
        world.json
        memory.json
        failures.json
      check_medicine_stale_recover/
        world.json
        memory.json
        failures.json
      fetch_cup_retry/
        world.json
        memory.json
        failures.json
      object_not_found/
        world.json
        memory.json
        failures.json
      distractor_rejected/
        world.json
        memory.json
        failures.json
  src/
    task_brain/
      __init__.py
      adapters/
        __init__.py
        mock_atomic_executor.py
        mock_perception.py
        mock_vln.py
        robobrain.py
        simulator_style.py
      capabilities.py
      cli.py
      context.py
      domain.py
      evidence.py
      graph.py
      memory.py
      parser.py
      planner.py
      recovery.py
      trace.py
      verification.py
      world.py
  tests/
    test_adapters.py
    test_cli.py
    test_domain.py
    test_failure_analysis.py
    test_graph_scenarios.py
    test_memory_retrieval.py
    test_observation_abstraction.py
    test_parser_context.py
    test_planner_validation.py
    test_simulator_readiness.py
    test_verification.py
```

## 实施阶段与优先级

### Phase A：必须先打穿

Phase A 只做稳定可演示闭环，不追求所有增强项同时完成。

范围：

```text
CLI 主链
3 个核心场景
rule-first parser
deterministic planner
object memory
task negative evidence
verification
failure analysis
recovery
memory reconciliation 的硬规则
skill-compatible capability contract
```

3 个核心场景：

```text
check_medicine_success
check_medicine_stale_recover
fetch_cup_retry
```

Phase A 退出条件：

- 三个核心 CLI demo 都能跑通。
- Trace 能证明 retrieve memory before planning。
- Trace 能证明 verification before success。
- stale memory 场景能写入 task negative evidence 并切换候选。
- cup retry 场景能根据 failure analysis 做 bounded retry。
- 长期 memory 只基于 verified evidence 更新。

Phase A 完成前，不启动 Phase B。

### Phase B：主链稳定后再补

Phase B 增强项：

```text
richer category prior
episodic retrieval 加权
LLM planner
simulator-style wrapper
optional gateway
```

Phase B 不能反向修改 Phase A 的核心接口，除非测试证明 Task Brain 主链仍然稳定。

## Task 0（Phase A）：仓库与环境初始化

### 目标

明确一开始基于什么项目改、需要 git 什么项目、需要提前配什么环境。

### 决策

- 主工程基于 `/Users/wylam/Documents/workspace/HomeMaster`。
- HomeMaster 应初始化为独立 git 仓库。
- 不 fork RoboOS，不在 RoboOS 里开发本 MVP。
- Hermes 只作为 gateway 参考，不作为依赖安装进主工程。

### 本机检查

当前本地已有：

```text
/Users/wylam/Documents/workspace/HomeMaster
/Users/wylam/Documents/workspace/RoboOS
/Users/wylam/.hermes/hermes-agent
```

如果是干净机器，可执行：

```bash
git clone -b stand-alone https://github.com/FlagOpen/RoboOS.git /Users/wylam/Documents/workspace/RoboOS
git clone https://github.com/NousResearch/hermes-agent.git /Users/wylam/.hermes/hermes-agent
```

### 初始化 HomeMaster

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
git init
git add plan/v1.0/*.md plan/v1.1/*.md
git commit -m "docs: add task brain plans"
```

### Python 环境

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
```

创建 `pyproject.toml` 后执行：

```bash
uv pip install ".[dev]" --python .venv/bin/python
pytest -q
ruff check .
```

本机 Stage 0 使用非 editable 安装。当前 uv Python 会跳过 setuptools editable install 生成的 hidden `__editable__` `.pth` 文件，导致隔离 Python 进程无法 `import task_brain`。后续开发阶段如果需要让 console script 读取最新源码，改完源码后重新执行 `uv pip install ".[dev]" --python .venv/bin/python` 即可；pytest 仍会通过 `pyproject.toml` 的 `pythonpath = ["src"]` 读取源码。

### 验收

- `git status` 能正常工作。
- `.venv` 可激活。
- `python --version` 是 3.11+。
- `pytest`、`ruff` 可运行。
- RoboOS 和 Hermes 都只作为 reference，不是主工程依赖。

## Task 1（Phase A）：项目脚手架与核心领域模型

### 文件

- 创建 `pyproject.toml`
- 创建 `README.md`
- 创建 `src/task_brain/__init__.py`
- 创建 `src/task_brain/domain.py`
- 创建 `tests/test_domain.py`

### 领域模型

第一批 domain model：

- `TaskRequest`
- `TaskIntent`
- `TargetObject`
- `ParsedTask`
- `Predicate`
- `Subgoal`
- `SubgoalType`
- `HighLevelPlan`
- `RuntimeState`
- `RobotRuntimeState`
- `TraceEvent`

v1.1 新增 domain model：

- `Observation`
- `ObservedObject`
- `ObservedAnchor`
- `SceneRelation`
- `VerificationEvidence`
- `ObjectMemory`
- `Anchor`
- `RelativeRelation`
- `TaskNegativeEvidence`
- `FailureType`
- `FailureAnalysis`
- `RecoveryDecision`
- `CapabilitySpec`

Phase B 再补：

- `CategoryPriorMemory`
- `CandidateLocationPrior`
- `EpisodicMemory`

### 测试

`tests/test_domain.py` 应覆盖：

- `Predicate` list round trip。
- `TaskRequest` 保存 source / user / utterance。
- `Subgoal` 必须有 success conditions。
- `Observation` 能区分 `detector_id` 和 `memory_id`。
- `ObjectMemory.memory_id` 是长期 ID。
- `FailureType` 枚举包含四类基础 failure。
- `RuntimeState` 是当前任务执行中的唯一状态主语，包含 current observation、selected candidate、selected object、retry budget、recent failure analysis、task negative evidence、candidate exclusion state。
- `CapabilitySpec` 必须包含 stable capability name、typed input schema、typed output schema、failure modes、timeout、evidence-carrying result。

### 验收

```bash
pytest tests/test_domain.py -q
```

期望通过。

## Task 2（Phase A）：Mock World 与最小 Observation Abstraction

### 文件

- 创建 `src/task_brain/world.py`
- 创建 `src/task_brain/adapters/mock_perception.py`
- 创建 `src/task_brain/evidence.py`
- 创建 `tests/test_observation_abstraction.py`

### 目标

Mock world 可以保存 symbolic truth，但 Task Brain 不直接把 raw world 当 perception 输入。

`MockPerceptionAdapter` 负责：

```text
MockWorld + viewpoint_id -> Observation
```

### Observation Schema

实现：

- `Observation`
- `ObservedObject`
- `ObservedAnchor`
- `SceneRelation`
- `VerificationEvidence`

### 测试

新增测试：

- `test_mock_perception_returns_standard_observation`
- `test_observation_distinguishes_detector_id_from_memory_id`
- `test_verification_evidence_wraps_observation_without_raw_world_dependency`

### 验收

```bash
pytest tests/test_observation_abstraction.py -q
```

期望通过。

## Task 3（Phase A）：Object Memory 与 Task Negative Evidence

### 文件

- 创建 `src/task_brain/memory.py`
- 创建 `tests/test_memory_retrieval.py`
- 创建各 scenario 的 `memory.json`

### 目标

Phase A 只实现主链必需的两层记忆：

- Object memory。
- Task negative evidence。

Category prior memory 和 episodic retrieval 加权放到 Phase B。Phase A 可以先保留字段边界，但不把它们做成主链依赖。

### memory.json 推荐结构

```json
{
  "object_memory": []
}
```

Task negative evidence 不直接写进长期 memory 主表。运行时保存在 task context / runtime state 中；如果需要持久化，也应作为 task-scoped run artifact。

### 功能

`MemoryStore` 提供：

- `retrieve_object_memory(object_category, aliases)`
- `update_object_memory_from_verified_observation(...)`
- `downgrade_stale_memory(...)`
- `mark_object_memory_stale_or_contradicted(...)`

`TaskMemory` 或 runtime state 提供：

- `add_negative_evidence(...)`
- `is_excluded_for_task(...)`
- `candidate_exclusion_state`

### 检索规则

第一版 structured retrieval first：

```text
object_category filter
  -> alias match
  -> explicit location hint boost
  -> object memory candidates
  -> task negative evidence exclusion
```

### 测试

- `test_object_memory_uses_structured_anchor`
- `test_task_negative_evidence_excludes_searched_location`
- `test_negative_evidence_does_not_pollute_long_term_object_memory`
- `test_stale_memory_confidence_can_be_downgraded`
- `test_direct_observation_maps_to_high_confidence`
- `test_only_verified_observation_can_update_object_memory`

### 验收

```bash
pytest tests/test_memory_retrieval.py -q
```

期望通过。

## Task 4（Phase A）：Parser 与 Task Context Builder

### 文件

- 创建 `src/task_brain/parser.py`
- 创建 `src/task_brain/context.py`
- 创建 `tests/test_parser_context.py`

### Parser

第一版 rule-first。

支持任务：

- 检查物体是否还在。
- 找到并拿取物体。

输出：

- `intent`
- `target_object.category`
- `target_object.aliases`
- `target_object.attributes`
- `quantity`
- `explicit_location_hint`
- `delivery_target`
- `requires_navigation`
- `requires_manipulation`

### Task Context

`build_task_context` 显式合并：

- request。
- parsed task。
- ranked candidates。
- object memory hits。
- task negative evidence。
- current observation。
- robot runtime state。
- capability registry。
- adapter status。
- constraints。

Planner 只能读 `TaskContext`，不直接读 memory store 或 mock world。

当前任务执行状态只能从 runtime/task-scoped state 进入 `TaskContext`。Trace 不能作为状态源，长期 memory 不能存放 selected candidate、retry budget、recent failure analysis 等运行中状态。

### 测试

- `test_parser_extracts_check_medicine_task`
- `test_parser_extracts_fetch_cup_task`
- `test_task_context_includes_memory_observation_runtime_and_capabilities`
- `test_task_context_keeps_negative_evidence_for_replanning`

### 验收

```bash
pytest tests/test_parser_context.py -q
```

期望通过。

## Task 5（Phase A）：Skill-Compatible Capability Registry、Deterministic Planner 与 Plan Validator

### 文件

- 创建 `src/task_brain/capabilities.py`
- 创建 `src/task_brain/planner.py`
- 创建 `tests/test_planner_validation.py`

### Capability Registry

基础 capability：

- `mock_vln.navigate`
- `mock_perception.observe`
- `robobrain.plan`
- `mock_atomic_executor.execute`
- `verification.evaluate`
- `memory.update`
- `recovery.analyze_failure`
- `recovery.decide`

每个 adapter-facing capability 都必须是 skill-compatible contract：

```text
CapabilitySpec
- name
- input_schema
- output_schema
- failure_modes
- timeout_s
- returns_evidence
```

后续替换时只能替换 capability 实现，不能让 Task Brain 依赖具体 adapter 内部状态。

### Planner

实现：

- `DeterministicHighLevelPlanner`
- `PlannerService`
- `PlanValidator`

Phase A 默认只用 deterministic planner。LLM provider 不进入 Phase A 主链。

规则：

- Planner 只能生成 high-level subgoals。
- 不允许 atomic robot action。
- 必须包含 memory grounding / candidate grounding。
- 不允许跳过 verification。
- 不允许 RoboBrain manipulation 早于 object presence verification。
- planner service 的默认路径必须是 deterministic plan。

### 测试

- `test_planner_uses_memory_grounding`
- `test_planner_does_not_emit_atomic_actions`
- `test_validator_rejects_manipulation_before_presence_verification`
- `test_replan_context_includes_negative_evidence_and_recent_failure`
- `test_adapter_capabilities_are_skill_compatible`

### 验收

```bash
pytest tests/test_planner_validation.py -q
```

期望通过。

## Task 6（Phase A）：Mock Adapters 与 RoboBrain 边界

### 文件

- 创建 `src/task_brain/adapters/mock_vln.py`
- 创建 `src/task_brain/adapters/robobrain.py`
- 创建 `src/task_brain/adapters/mock_atomic_executor.py`
- 创建 `tests/test_adapters.py`

### Adapter

`MockVLNAdapter`：

```text
navigate(world, viewpoint_id) -> NavigationResult
```

`FakeRoboBrainClient`：

```text
plan(EmbodiedSubgoalRequest) -> AtomicPlanResponse
```

`MockAtomicExecutor`：

```text
execute(AtomicPlanResponse, world/runtime, attempt) -> ExecutionResult
```

所有 adapter result 必须携带 evidence，供 verification 和 failure analysis 使用。

### RoboBrain 请求

v1.1 应使用更接近真实边界的 request：

```text
EmbodiedSubgoalRequest
- subgoal
- target_object
- current_observation
- constraints
- success_conditions
```

### 测试

- `test_mock_vln_returns_navigation_result`
- `test_fake_robobrain_accepts_embodied_subgoal_request`
- `test_fake_robobrain_returns_atomic_plan`
- `test_mock_atomic_executor_applies_success_delta`
- `test_mock_atomic_executor_can_inject_failure`

### 验收

```bash
pytest tests/test_adapters.py -q
```

期望通过。

## Task 7（Phase A）：Verification Engine

### 文件

- 创建 `src/task_brain/verification.py`
- 创建 `tests/test_verification.py`

### 目标

Verification engine 消费标准化 evidence，而不是直接消费 raw world。

输入：

```text
VerificationEvidence
- observation
- execution_result
- robot_runtime_state
- task_negative_evidence
```

验证层：

- arrival verification。
- object presence verification。
- manipulation verification。
- final task verification。

### 测试

- `test_verifies_visible_category_from_observation`
- `test_rejects_missing_visible_category_from_observation`
- `test_verifies_holding_category_from_runtime_state`
- `test_final_success_requires_final_task_verification`
- `test_execution_result_alone_does_not_mark_success`

### 验收

```bash
pytest tests/test_verification.py -q
```

期望通过。

## Task 8（Phase A）：Failure Analysis 与 Recovery Policy

### 文件

- 创建 `src/task_brain/recovery.py`
- 创建 `tests/test_failure_analysis.py`
- 创建 `tests/test_recovery.py`

### 目标

把 recovery 从 attempt-based 升级为 failure-type-aware。

流程：

```text
verification failed
  -> analyze_failure
  -> decide_recovery
```

### Failure Type

```text
navigation_failure
object_presence_failure
manipulation_failure
final_task_failure
```

### Recovery Action

```text
continue
retry_same_subgoal
switch_candidate
re_observe
replan
ask_clarification
report_failure
```

### 规则

- object presence failure -> 写 task negative evidence + switch candidate。
- manipulation failure 且目标仍可见 -> retry same subgoal，最多 1 次。
- manipulation failure 且目标状态变化 -> re-observe then replan。
- candidate exhausted -> ask clarification 或 report failure。
- final task failure -> high-level replan 或 failure path。

### 测试

- `test_object_presence_failure_switches_candidate_and_writes_negative_evidence`
- `test_manipulation_failure_with_visible_target_retries_once`
- `test_manipulation_failure_with_missing_target_reobserves_then_replans`
- `test_candidate_exhausted_reports_failure`
- `test_final_task_failure_requires_replan_or_failure_path`
- `test_same_failed_condition_can_map_to_different_actions_based_on_evidence`

### 验收

```bash
pytest tests/test_failure_analysis.py tests/test_recovery.py -q
```

期望通过。

## Task 9（Phase A）：Scenario Fixtures

### 文件

创建或更新：

- `data/scenarios/check_medicine_success/*`
- `data/scenarios/check_medicine_stale_recover/*`
- `data/scenarios/fetch_cup_retry/*`
- `data/scenarios/object_not_found/*`
- `data/scenarios/distractor_rejected/*`

### 每个 scenario 至少包含

- `world.json`
- `memory.json`
- `failures.json`

### 场景

1. `check_medicine_success`
   - 记忆正确。
   - 药盒在记忆位置。
   - final verification 成功。

2. `check_medicine_stale_recover`
   - top object memory 已过期。
   - 第一个位置搜索不到。
   - 写 task negative evidence。
   - 切换候选并成功。

3. `fetch_cup_retry`
   - 找到水杯。
   - 第一次 manipulation 失败。
   - 目标仍可见。
   - retry 一次成功。

Phase A 必须先打穿前三个核心场景。以下两个负向场景是 Phase A hardening：前三个核心场景稳定后再补，但仍应在 Phase A 完成前进入质量门槛。

4. `object_not_found`
   - 所有候选都没有目标。
   - candidate exhausted。
   - report failure，不伪造成功。

5. `distractor_rejected`
   - 候选位置有 distractor。
   - 不能把 bowl / medicine bottle 当 cup。
   - 不调用 RoboBrain pickup distractor。

### 验收

```bash
pytest tests/test_scenario_fixtures.py -q
```

期望通过。

## Task 10（Phase A）：Coarse-Grained LangGraph Orchestration

### 文件

- 创建 `src/task_brain/graph.py`
- 创建 `tests/test_graph_scenarios.py`

### 主图

必须是真实 compiled LangGraph：

```text
input_instruction
  -> parse_instruction
  -> retrieve_memory
  -> build_task_context
  -> generate_plan
  -> validate_plan
  -> execute_subgoal_loop
  -> final_task_verification
  -> update_memory
  -> respond_with_trace
```

实现上可以将某些轻量阶段合并在同一个 Python 文件中，但 trace 和 graph node 命名应保持清楚。

### State

`TaskGraphState` 至少包含：

- `scenario`
- `instruction`
- `request`
- `parsed_task`
- `world`
- `memory_store`
- `runtime_state`
- `memory_context`
- `task_context`
- `plan`
- `current_observation`
- `selected_object_id`
- `recent_failure_analysis`
- `trace`
- `final_status`

运行中的唯一状态主语是 `runtime_state`。`current_observation`、`selected_object_id`、`recent_failure_analysis`、task negative evidence、candidate exclusion state、retry budget 都必须写入 `runtime_state`，不要分散到 memory store 或 trace 中。

### 测试

- `test_build_task_graph_returns_invokable_langgraph`
- `test_trace_order_retrieves_memory_before_plan_generation`
- `test_check_medicine_success_trace_order`
- `test_check_medicine_stale_recover_switches_candidate`
- `test_fetch_cup_retry_uses_robobrain_and_retries`
- `test_object_not_found_candidate_exhausted_reports_failure`
- `test_distractor_object_rejected_does_not_pick_wrong_object`
- `test_final_status_success_requires_final_task_verification`

### 验收

```bash
pytest tests/test_graph_scenarios.py -q
```

期望通过。

## Task 11（Phase A）：CLI Trace Demo

### 文件

- 创建 `src/task_brain/cli.py`
- 创建 `src/task_brain/trace.py`
- 创建 `tests/test_cli.py`

### CLI

命令：

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

Trace 必须显示：

- parse instruction。
- retrieve memory。
- build task context。
- generate plan。
- validate plan。
- observe scene。
- verify subgoal。
- analyze failure。
- decide recovery。
- final task verification。
- update memory。
- final response。

### 测试

- `test_cli_runs_check_medicine_success`
- `test_cli_outputs_retrieve_memory_before_generate_plan`
- `test_cli_failed_task_exits_nonzero`
- `test_cli_trace_contains_failure_analysis_for_recovery_case`

### 验收

```bash
pytest tests/test_cli.py -q
```

期望通过。

## Task 12（Phase A）：Memory Reconciliation 与 Object Memory Update

### 文件

- 修改 `src/task_brain/memory.py`
- 修改 `src/task_brain/graph.py`
- 创建 `tests/test_memory_update.py`

### Memory Reconciliation Rules

成功任务：

- 根据 verified observation 更新 object memory。
- 不根据 planner 猜测更新长期 memory。
- 不根据未验证的 execution result 更新长期 memory。

失败任务：

- 对被验证为 stale 的 object memory 降级。
- task negative evidence 保持 task-scoped，不污染长期 object memory 主表。
- 只有 verified not found 且 location 与 object memory anchor 明确对应时，才在 reconciliation 阶段将旧 object memory 标记为 `stale` 或 `contradicted`。

更新旧 memory：

- 新 evidence 来自 verified observation / verified execution evidence。
- 新 evidence 与旧 `memory_id` 匹配，或 anchor / relative relation 足以判定是同一长期实例。
- category 一致。

新建 memory：

- 新 evidence 已验证。
- 没有可匹配的旧 `memory_id`。
- 不能直接用 detector id 作为新 memory id。

冲突优先级：

```text
verified current observation
  > task negative evidence / candidate exclusion
  > active object memory
  > category prior
  > episodic weak hint
```

### 测试

- `test_successful_observation_updates_object_memory`
- `test_stale_memory_is_downgraded_after_search_not_found`
- `test_task_negative_evidence_not_persisted_as_object_memory`
- `test_unverified_planner_guess_does_not_update_memory`
- `test_conflicting_verified_evidence_marks_memory_stale_or_contradicted`

### 验收

```bash
pytest tests/test_memory_update.py -q
```

期望通过。

## Task 13（Phase A）：End-to-End Quality Gate

### 命令

```bash
pytest -q
ruff check .
```

运行核心 demo：

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

### 验收

- 全部测试通过。
- lint 通过。
- 三个核心 demo 成功运行。
- stale memory 场景 trace 包含：
  - `verify_object_presence`
  - `analyze_failure`
  - `write_task_negative_evidence`
  - `recovery_switch_candidate`
- cup retry 场景 trace 包含：
  - `call_robobrain_planner`
  - `execute_atomic_plan`
  - `post_action_verification_failed`
  - `analyze_failure`
  - `recovery_retry_same_subgoal`
- 所有 success response 都出现在 final task verification 之后。

## Task 14（Phase B）：Simulator Readiness Test

### 启动条件

只有 Task 13 的 Phase A quality gate 通过后再做。这个 task 是为了验证接口迁移方向，不是 Phase A 执行顺序的一部分。

### 文件

- 创建 `src/task_brain/adapters/simulator_style.py`
- 创建 `tests/test_simulator_readiness.py`

### 目标

不接真实 simulator，但写一个 simulator-style wrapper，模拟未来 AI2-THOR / BEHAVIOR / Habitat adapter 的输出。

输入示例：

```text
SimulatorEvent
- agent_pose
- visible_objects
- object_states
- room
- metadata
```

转换：

```text
SimulatorEvent -> Observation
```

### 测试

- `test_simulator_style_wrapper_outputs_standard_observation`
- `test_planner_accepts_context_built_from_simulator_style_observation`
- `test_verification_accepts_simulator_style_observation_without_interface_change`

### 验收

```bash
pytest tests/test_simulator_readiness.py -q
```

期望通过。

## Task 15（Phase B）：Richer Memory Retrieval 与 LLM Planner

### 启动条件

只有 Task 13 的 Phase A quality gate 通过后再做。

### 文件

- 修改 `src/task_brain/memory.py`
- 修改 `src/task_brain/planner.py`
- 修改 `src/task_brain/context.py`
- 创建 `tests/test_phase_b_memory_planner.py`

### 范围

Phase B 增强：

- richer category prior。
- episodic retrieval 加权。
- LLM planner provider。
- invalid LLM plan fallback 到 deterministic planner。
- replanning prompt/context。

### Category Prior

新增或激活：

```text
CategoryPriorMemory
- object_category
- candidate_locations
- prior_source
- updated_at
```

`candidate_locations`：

```text
CandidateLocationPrior
- anchor.room_id
- anchor.anchor_id
- anchor.anchor_type
- anchor.viewpoint_id
- prior_rank
- confidence_level
- reason
```

### Episodic Retrieval

Episodic memory 在 Phase B 只作为 ranking hint，不作为硬约束。

允许影响：

- candidate 排序。
- replan context summary。
- trace summary。

不允许影响：

- 直接标记任务成功。
- 绕过 verification。
- 覆盖当前任务 negative evidence。

### LLM Planner

LLM planner 接入规则：

- provider 输出必须通过 `HighLevelPlan` schema validate。
- 失败或非法 plan 必须 fallback deterministic planner。
- LLM plan 不能发出 atomic robot action。
- LLM plan 不能跳过 verification。
- LLM plan 不能忽略 task negative evidence。

### 测试

- `test_category_prior_provides_candidate_when_instance_memory_missing`
- `test_episodic_retrieval_boosts_but_does_not_override_negative_evidence`
- `test_invalid_llm_plan_falls_back_to_deterministic_plan`
- `test_llm_plan_cannot_ignore_task_negative_evidence`
- `test_replanning_prompt_includes_recent_failure_and_candidate_exclusion`

### 验收

```bash
pytest tests/test_phase_b_memory_planner.py -q
```

期望通过。

## Task 16（Phase B）：Optional Hermes / Feishu Gateway Spike

### 启动条件

只有 Task 13 的 Phase A quality gate 通过后再做。它不是 CLI MVP blocker。

### 文件

- 创建 `src/task_brain/gateway.py`
- 创建 `tests/test_gateway.py`

### 范围

Hermes 只做：

- message intake。
- user/session identity。
- allowlist。
- TaskRequest 转换。
- trace summary reply。

Hermes 不做：

- planning。
- memory update。
- recovery decision。
- robot execution。

### 参考 Hermes 文件

```text
/Users/wylam/.hermes/hermes-agent/gateway/run.py
/Users/wylam/.hermes/hermes-agent/gateway/platforms/base.py
/Users/wylam/.hermes/hermes-agent/gateway/platforms/feishu.py
/Users/wylam/.hermes/hermes-agent/gateway/session.py
/Users/wylam/.hermes/hermes-agent/hermes_cli/gateway.py
```

### 测试

- `test_gateway_message_converts_to_task_request`
- `test_gateway_rejects_non_allowlisted_user`
- `test_gateway_summarizes_failed_trace`

## 自检清单

### v1.1 相对 v1.0 的升级是否覆盖

- [ ] 明确 MVP 当前不是真实 VLM 输入。
- [ ] 明确 Phase A / Phase B，且 Phase B 不阻塞 Phase A CLI 主链。
- [ ] 新增统一 Observation abstraction。
- [ ] Verification engine 消费标准化 evidence。
- [ ] Memory 分成 object memory、category prior、task negative evidence、episodic memory。
- [ ] Phase A 只强依赖 object memory 和 task negative evidence。
- [ ] Object memory 使用结构化 anchor。
- [ ] Relative relation 同时有规范关系和自然语言。
- [ ] 当前任务执行状态唯一主语是 runtime/task-scoped state。
- [ ] current observation、selected candidate/object、retry budget、recent failure analysis、task negative evidence、candidate exclusion state 都在 runtime/task state。
- [ ] 长期 memory 与 runtime state 分离，trace 不作为状态源。
- [ ] `memory_id` 与 `detector_id` 明确区分。
- [ ] Confidence source 与 confidence level 分离。
- [ ] Category prior memory 已加入。
- [ ] Task negative evidence 不污染长期 object memory。
- [ ] Memory reconciliation rules 明确 update / create / conflict / stale / contradicted。
- [ ] 长期 memory 只能基于 verified observation / verified evidence 更新。
- [ ] Retrieval 是 structured retrieval first。
- [ ] Task negative evidence 优先级最高。
- [ ] Parser 是 rule-first。
- [ ] Phase A 默认 planner 是 deterministic planner。
- [ ] LLM planner 放在 Phase B。
- [ ] Adapter-facing capability 满足 skill-compatible contract。
- [ ] LangGraph 显式包含 build_task_context、generate_plan、validate_plan、update_memory、respond_with_trace。
- [ ] Recovery 是 failure-type-aware。
- [ ] 有 analyze_failure 节点或函数。
- [ ] 明确必须 replan 的条件。
- [ ] 新增 perception abstraction、failure analysis、simulator readiness 测试。

### 工程边界是否清楚

- [ ] HomeMaster 是主工程。
- [ ] RoboOS 只参考，不作为 runtime dependency。
- [ ] Hermes 只参考 gateway，不接 agent runtime。
- [ ] CLI MVP 不依赖真实机器人、真实 VLM 或完整 simulator。

### 质量门槛

- [ ] 每个 task 有对应测试。
- [ ] 每个关键规则能从 trace 看到。
- [ ] 所有成功都经过 verification。
- [ ] 所有失败都有 failure analysis 或明确 failure path。
- [ ] `pytest -q` 通过。
- [ ] `ruff check .` 通过。
