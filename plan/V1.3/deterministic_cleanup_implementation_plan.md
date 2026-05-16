# HomeMaster V1.3 AgentRuntime And Deterministic Cleanup Implementation Plan

> 目标：把 `deterministic / test_double` 从生产 runtime 里清掉，同时把 HomeMaster 从固定 Stage Pipeline 逐步演进成 Claude Code-like 的 `AgentRuntime` tool loop。测试可以保留 test doubles，但必须放在测试边界内；当前没有真实 VLA / VLN / VLM，所以 embodied tool 层允许继续使用明确标注的 simulated skills / simulated verification。

---

## 0. Guiding Principles

### 0.1 核心目标

本计划不是把 deterministic 模式“标注得更清楚”，也不是继续强化 Stage02 → Stage03 → Stage04 → Stage05 → Stage06 的固定流水线。目标是：

```text
1. deterministic / test_double 不再是 production runtime mode
2. Mimo 每轮基于 AgentState 和 tool manifest 选择 tool_call 或 finish
3. Runtime 校验 schema / tool 是否存在 / 当前是否允许执行
4. Dispatcher 执行 tool
5. StateUpdater 把 tool result 写回 AgentState
6. EventSink 记录每轮 decision / tool_call / tool_result / state transition
7. loop 持续到 completed / failed / max_turns
```

生产路径的最终边界必须满足：

```text
AgentRuntime decision loop: live_llm
understand_task: live_llm
retrieve_memory: live_llm + live_embedding 或现有 memory retrieval
ground_target: programmatic
navigate: simulated_skill
observe: simulated_skill
manipulate: simulated_skill
verify: simulated_verification
update_memory: programmatic
update_user_profile: programmatic
get_skill: programmatic progressive-disclosure skill loader
finish_task: internal programmatic finalization helper, not selectable_by_model
real_robot / real_vla / real_vln / real_vlm: not_integrated
```

Stage02-06 可以作为迁移期工具实现来源或兼容层存在，但不能再作为未来主 runtime 的架构方向。历史 Stage 边界的 runtime mode 仍需清理：

```text
Stage02 task understanding: live_llm
Stage03 memory query: live_llm
Stage03 embedding: live_embedding
Stage04 grounding: programmatic
Stage05 orchestration plan: live_llm
Stage05 step decision: live_llm
Stage05 skills: simulated_skill
Stage05 verification: simulated_verification
Stage06 summary: live_llm
Stage06 memory commit: programmatic
real_robot / real_vla / real_vlm: not_integrated
```

### 0.2 允许保留的例外

允许保留：

```text
tests/homemaster/test_doubles/
```

里面可以放 deterministic / static providers，用于单元测试。

允许保留：

```text
simulated_skill
simulated_verification
```

原因是当前真实 robot / VLA / VLN / VLM executor 尚未接入。

不允许保留：

```text
生产 CLI 可选择 deterministic
生产 RuntimeMode 支持 test_double
生产 stage wrapper fallback 到 deterministic
生产 Stage05 actual decision 使用 StaticScenarioDecisionProvider
把 AgentRuntime 写成 PLAN -> DECIDE -> ACT -> VERIFY -> RECOVER 固定阶段流水线
tool 失败后直接进入固定 RECOVER 阶段而不是写入 AgentState 等待下一轮 Mimo 决策
当前版本暴露 ask_user tool 给 Mimo
```

### 0.3 AgentRuntime 设计原则

实现时必须遵守：

```text
模型决定下一步，Runtime 管边界。
phase_label 只能作为 trace/status 标签，不能作为强制流程。
AgentRuntime 不得写成 Stage Pipeline 或固定 PLAN/ACT/VERIFY/RECOVER。
recovery 不是固定阶段；tool failure 写入 AgentState.failures 后，由下一轮 Mimo 决定 retry / observe / retrieve_memory / replan-like behavior / finish failed。
当前阶段不实现 ask_user 行为，不向 Mimo 暴露 ask_user。
信息不足时，Mimo 基于当前 state best-effort；无法继续则 finish failed with reason。
```

AgentRuntime 主循环目标形态：

```python
def run(user_request, runtime_settings):
    state = AgentState.from_user_request(user_request, runtime_settings)

    while not state.is_terminal():
        if state.turn_index >= runtime_settings.max_turns:
            state.mark_failed("max_turns_exceeded")
            break

        context = context_builder.build(state)
        decision = mimo_decision_client.decide(
            context=context,
            tools=tool_registry.tool_manifests(),
        )
        event_sink.emit_decision(state, decision)

        if decision.type == "tool_call":
            tool_spec = tool_registry.get(decision.tool)
            validated_args = tool_spec.input_schema.validate(decision.arguments)
            result = dispatcher.execute(tool_spec, validated_args, state)
            state = state_updater.apply(
                state=state,
                decision=decision,
                result=result,
            )
            event_sink.emit_tool_result(state, decision, result)
        elif decision.type == "finish":
            state.mark_finished(decision)
        else:
            state.mark_failed("invalid_decision")

        state.turn_index += 1

    return state.to_result()
```

Mimo 每轮输出只允许两类：

```json
{
  "type": "tool_call",
  "tool": "navigate",
  "arguments": {
    "target_anchor_id": "kitchen_table"
  },
  "reason": "The cup is likely on the kitchen table based on memory retrieval."
}
```

```json
{
  "type": "finish",
  "status": "failed",
  "summary": "The task cannot continue because no valid target candidate remains.",
  "failure_reason": "All candidate locations were checked and no cup was observed."
}
```

### 0.4 Mini-Agent 可借鉴的工程约束

`/Users/wylam/Documents/workspace/Mini-Agent` 值得借鉴的不是业务复杂度，而是小型 agent runtime 的工程边界。HomeMaster 迁移时应吸收这些约束，但不要照搬其 demo 级安全边界。

必须借鉴：

```text
1. Minimal loop shape:
   Mini-Agent 的 Agent.run() 是 LLM -> tool_call -> execute -> tool_result -> next turn。
   HomeMaster 的 AgentRuntime 也必须保持这个主形状，不能再变成更复杂的 Stage Pipeline。

2. Tool-first contract:
   Mini-Agent 的 Tool 暴露 name / description / parameters / execute / schema conversion。
   HomeMaster 的 ToolSpec 必须至少包含这些概念，并额外加入 output_schema、executor_mode、selectable_by_model、state_effects、failure_semantics。

3. Progressive disclosure:
   Mini-Agent 只先暴露 skill metadata，需要时再加载完整 skill。
   HomeMaster 要明确实现同类机制：SkillSpec / SkillLoader / SkillRegistry 只先给 Mimo skill 摘要；完整 SKILL.md 只能通过 get_skill tool 加载。
   HomeMaster 每轮只给 Mimo compact tool manifest、compact skill manifest 和 compact AgentState context；大型 skill 说明、schema 示例、历史 trace 不应默认塞进 prompt。

4. Tool descriptions encode constraints:
   Mini-Agent 的 read/write/edit/bash 工具把读前写、exact edit、background shell 等约束写进 tool description。
   HomeMaster 的 navigate / observe / manipulate / verify 必须在 ToolSpec 中写清模拟模式、允许执行条件、状态影响、失败输出，而不是只靠 prompt 口头约束。

5. Run-scoped configuration:
   Mini-Agent 有清晰的 Config 对象；HomeMaster 要进一步升级成 run-scoped RuntimeSettings，避免 import-time globals 和跨 run 污染。

6. Structured trace:
   Mini-Agent 记录 request / response / tool_result；HomeMaster 要借鉴其闭环，但必须升级为 JSONL RuntimeEvent、字段稳定、secret redaction、run_id path-safe。

7. Docs / examples / tests as boundary:
   Mini-Agent 用 docs、examples、tests 说明如何扩展 tool 和配置。
   HomeMaster 的每个 AgentRuntime 阶段都必须同步更新测试、README/scripts/debug 文案，避免 runtime 行为和文档分叉。

8. Package layout as architecture:
   Mini-Agent 的 `agent.py / tools/ / llm/ / schema/ / config/ / skills / docs / examples / tests` 很容易让人看出系统边界。
   HomeMaster 新增实现不能继续堆在 `src/homemaster/` root；必须按 AgentRuntime、tools、skills、providers、events、config、memory、scenarios 等职责分包。
```

明确不要照搬：

```text
Mini-Agent 主要靠 message history；HomeMaster 必须以 AgentState 为唯一核心状态。
Mini-Agent ToolResult 只有 success/content/error；HomeMaster ToolResult 必须结构化。
Mini-Agent skills 可以直接把完整内容拼进上下文；HomeMaster 必须用 get_skill 受控加载，且 SkillSpec 不得拥有 executor。
Mini-Agent logger 会记录完整请求内容；HomeMaster RuntimeEvent 必须脱敏。
Mini-Agent 文件工具允许较宽的绝对路径访问；HomeMaster artifact/run_id/world/memory 写入必须先校验边界。
Mini-Agent 可以用少量 root 文件承载核心；HomeMaster 已经更复杂，新实现不能继续增加 root-level active modules。
```

### 0.5 Target Package Layout

HomeMaster 要吸收 Mini-Agent 的文件组织优点，但不能照搬单文件 demo 结构。目标是让目录名本身表达 runtime 边界：

```text
src/homemaster/
  agent/
    __init__.py
    runtime.py              # AgentRuntime loop only
    state.py                # AgentState and state snapshots
    decision.py             # ToolCallDecision / FinishDecision parser
    context_builder.py      # AgentState -> compact Mimo context
    result.py               # AgentRunResult / terminal status

  tools/
    __init__.py
    spec.py                 # ToolSpec / schema conversion
    registry.py             # tool registry and compact manifests
    dispatcher.py           # runtime validation + executor dispatch
    state_updater.py        # ToolResult -> AgentState transitions
    results.py              # structured ToolResult contracts
    builtin.py              # understand/retrieve/ground/update/finish wrappers
    simulated.py            # simulated navigate/observe/manipulate/verify executors
    skill_tools.py          # get_skill progressive-disclosure tool

  skills/
    __init__.py
    spec.py                 # SkillSpec, no executor and no state mutation
    loader.py               # SKILL.md frontmatter/body loader
    registry.py             # metadata registry, activation, allowed_tools
    builtin/
      fetch_object/SKILL.md
      check_object_state/SKILL.md

  providers/
    __init__.py
    llm_client.py           # provider client wrapper / compatibility import target
    embedding_client.py
    mimo_decision_client.py

  events/
    __init__.py
    runtime_events.py       # RuntimeEvent schema
    sinks.py                # JSONL / console / fanout / null sinks
    sanitizer.py            # log redaction helpers

  config/
    __init__.py
    runtime_settings.py     # run-scoped RuntimeSettings
    runtime_paths.py
    token_budget.py
    recovery.py

  memory/
    rag.py
    index.py
    tokenizer.py
    profile.py
    commit.py
    runtime_store.py
    fact_memory.py
    context_snapshot.py     # MEMORY.md / USER.md model-facing snapshots

  scenarios/
    catalog.py
    runner.py
    validator.py
    world_overlay.py
    failure_rule_provider.py

  core/
    contracts.py
    execution_state.py
    planning_context.py
    task_record.py
    failure_log.py
    orchestration_validator.py

  cli/
  pipeline/                 # compatibility pipeline, not future main runtime
  stages/                   # transitional Stage02-06 handlers
```

Root-level files should follow this rule:

```text
allowed at src/homemaster/ root:
  __init__.py
  task_runner.py            # public run facade; delegates to AgentRuntime or compatibility resolver
  compatibility shims with documented lifecycle

not allowed for new active implementation:
  runtime_events.py
  simulated_skills.py
  new provider clients
  new tool registries
  new runtime settings modules
```

Tests should mirror the same boundaries:

```text
tests/homemaster/
  test_agent_runtime.py
  test_agent_state.py
  test_agent_decision_contract.py
  test_context_builder.py
  test_tool_registry.py
  test_tool_dispatcher.py
  test_runtime_events.py
  test_runtime_settings.py
  test_doubles/
```

Migration rule:

```text
When touching an old root-level implementation module for AgentRuntime work,
prefer moving active implementation into the target subpackage and leaving a thin shim only if compatibility needs it.
Every shim must document new import path, compat window, and target removal version.
```

### 0.6 执行方式

按阶段实施。每个阶段独立可验收，不跨阶段混做。

推荐提交边界：

```text
commit 1: runtime mode + CLI remove deterministic entrypoints
commit 2: package skeleton + RuntimeSettings + tool/skill decision contracts
commit 3: run_id + artifact hygiene
commit 4: AgentRuntime MVP fetch-cup tool loop + get_skill progressive disclosure
commit 5: Pipeline compatibility layer
commit 6: Mini-Agent-style Skill implementation + simulated tool boundary
commit 7: docs / shim lifecycle / Ruff cleanup
commit 8: runtime event trace + agent turn progress
commit 9: CLI command boundary + error handling cleanup
```

### 0.7 Mandatory Phase Gate: Independent Test And Review Agent

每个阶段完成后，进入下一阶段前，必须执行一个独立验收门。这个验收门不是可选建议，而是本计划的执行约束：

```text
phase implementation
  -> implementer runs the phase test plan
  -> implementer records changed files, commands, and known risks
  -> spawn a fresh review/test subagent
  -> subagent reviews project details against this plan and original P8 requirements
  -> blocker findings must be fixed before the next phase starts
```

Review/test subagent 的职责：

```text
1. 不实现新功能，不重构，不回滚实现者改动。
2. 阅读本阶段计划、相关项目代码、git diff、测试输出。
3. 校验本阶段是否真的满足 acceptance criteria。
4. 校验是否引入新的 deterministic/test_double 生产入口。
5. 校验是否破坏 AgentRuntime-first、ToolSpec/SkillSpec、simulated skill、event trace、artifact hygiene 等已完成边界。
6. 必要时补跑本阶段测试计划里的关键命令；对高风险阶段必须补跑指定 Stage07 matrix。
7. 输出 PASS / BLOCKED / PASS_WITH_FOLLOWUPS，并列出文件级问题。
```

每个阶段的 handoff 必须包含：

```text
Phase: <phase number and title>
Changed files: <git diff --name-only>
Commands run: <exact commands and result>
Known risks: <short list or "none">
Review focus:
  - original P8 requirement coverage
  - phase acceptance criteria
  - deterministic/test_double production leakage
  - AgentRuntime-first boundary
  - tests and artifact hygiene
```

进入下一阶段的硬性条件：

```text
1. implementer 本人跑完本阶段测试计划。
2. fresh review/test subagent 明确给出 PASS，或 PASS_WITH_FOLLOWUPS 且 followups 不阻塞本阶段目标。
3. 如果 subagent 给出 BLOCKED，必须修复 blocker、重跑相关测试、再开一个 fresh review/test subagent 复核。
4. Phase 1 / Phase 4 / Phase 6 / Phase 8 这类影响 runtime 主线的阶段，review/test subagent 必须额外关注 tests/homemaster/test_stage_07_scenarios_live.py -m "not live_api" 的广泛场景矩阵。
```

推荐 subagent prompt 模板：

```text
You are reviewing Phase <N> of the HomeMaster V1.3 AgentRuntime and deterministic cleanup plan.

Do not edit files. Review only.

Read:
- plan/V1.3/p8_package_cleanup_issue.md
- plan/V1.3/deterministic_cleanup_implementation_plan.md
- git diff for this phase
- relevant source and tests touched by this phase

Check:
- Does the implementation satisfy this phase's acceptance criteria?
- Does it preserve AgentRuntime-first architecture rather than strengthening Stage Pipeline?
- Does src/homemaster still expose deterministic/test_double production runtime paths?
- Are simulated robot tools clearly labeled as simulated_skill / simulated_verification?
- Are ToolSpec, SkillSpec, ContextBuilder, EventSink, RuntimeSettings, and package boundaries consistent with the plan?
- Did tests cover the risky paths, especially Stage07 offline matrix when required?
- Did normal tests avoid mutating tracked fixtures?

Return:
- PASS, BLOCKED, or PASS_WITH_FOLLOWUPS
- findings ordered by severity with file/line references
- exact tests you ran or inspected
- any remaining risks
```

---

## Phase 0. Baseline Protection And Worktree Hygiene

### Priority

P0-blocker. 必须先做。

### Intent

开始改代码前，先确认当前 worktree 状态，避免误回滚用户已有改动。

### Implementation Tasks

1. 记录当前 dirty 状态：

```bash
git status --short
```

2. 记录当前 deterministic 命中面：

```bash
rg -n "test_double|deterministic_|StaticScenarioDecisionProvider|StaticStepDecisionProvider|--no-live-models|live_models=False|mock_symbolic|live_step_decision_smoke" src tests README.md scripts
```

3. 不执行会刷新 tracked fixture 的全量 live/scenario 测试。

### Acceptance Criteria

* 当前 dirty files 被记录。
* 没有回滚与本任务无关的已有修改。
* 后续每个阶段只改该阶段需要的文件。

### Test Plan

```bash
git status --short
git diff --name-only
```

预期：只出现本阶段计划内文件，或明确记录为既有改动。

---

## Phase 1. Remove Deterministic/Test-Double Production Runtime Mode

### Priority

P0.

### Intent

切断 deterministic 作为生产 runtime mode 的所有入口。

### Target Files

```text
src/homemaster/pipeline/stage_runtime.py
src/homemaster/pipeline/adapters.py
src/homemaster/pipeline/core.py
src/homemaster/runtime.py
src/homemaster/task_runner.py
src/homemaster/cli/app.py
src/homemaster/cli/interactive_shell.py
src/homemaster/scenario_runner.py
src/homemaster/stages/executor.py
src/homemaster/stages/recovery_loop.py
src/homemaster/stage_runtime.py
src/homemaster/executor.py
config/homemaster.example.json
tests/homemaster/test_doubles/decision_provider.py
tests/homemaster/test_runtime_mode.py
tests/homemaster/test_cli_run.py
tests/homemaster/test_task_runner.py
tests/homemaster/test_homemaster_config.py
tests/homemaster/test_recovery_loop.py
tests/homemaster/test_executor.py
tests/homemaster/test_import_boundaries.py
tests/homemaster/test_stage_07_scenarios_live.py
tests/homemaster/test_stage_07_debug_assets_do_not_contain_secrets.py
tests/homemaster/test_stage_07_scenario_structure.py
tests/homemaster/test_scenario_runner.py
```

### Implementation Tasks

1. 修改 `RuntimeMode`。

生产 `ComponentMode` 只保留：

```python
ComponentMode = Literal[
    "live_llm",
    "live_embedding",
    "programmatic",
    "simulated_skill",
    "simulated_verification",
    "not_integrated",
]
```

`RuntimeMode` 使用 live-only constructor：

```python
RuntimeMode.live(skill_mode="simulated")
```

不再提供可正常返回 `test_double` 的 `from_flags()`。

2. 删除生产 stage wrapper 的 deterministic branches。

这些函数不再接受 `live_models`：

```text
run_stage02()
run_stage03()
run_stage05_plan()
run_stage06_summary()
```

它们始终使用 live provider。

3. 删除生产包 deterministic providers/builders。

从 `src/homemaster/pipeline/stage_runtime.py` 移出或删除：

```text
StaticMemoryQueryProvider
KeywordEmbeddingProvider
deterministic_task_card()
deterministic_query()
deterministic_plan()
dummy_provider()
```

如果测试仍需要，移动到：

```text
tests/homemaster/test_doubles/runtime_providers.py
```

4. 删除生产 CLI 的 `--live-models/--no-live-models`。

`homemaster run` 默认且只能 live brain。

5. 删除或拒绝生产 Python API 的 non-live 参数。

必须处理这些入口：

```text
run_homemaster_task(live_models=...)
run_stage_07_scenario_matrix(live_models=...)
PipelineContext.live_models
run_stage05_with_recovery(..., live_models=...)
```

目标状态：

```text
生产 API 不再接受 live_models
生产 context 不再携带 live_models
生产 recovery loop 不再按 live_models 分叉
```

不保留 `live_models=True` alias。生产 API 和 compatibility shim 都应移除 `live_models` 参数；如果外部调用仍传入该参数，必须抛出迁移错误，不能静默忽略，也不能进入 deterministic fallback。

6. 废弃 `runtime_defaults.live_models`。

如果 `config/homemaster.json` 里显式设置：

```json
{"runtime_defaults": {"live_models": false}}
```

应该抛出 `RuntimeConfigError`，提示 deterministic runtime 已移除。

更严格的目标是：

```text
runtime_defaults.live_models 这个 key 任意出现都视为 invalid
config/homemaster.example.json 不再包含 live_models
DEFAULT_LIVE_MODELS 不再从 production runtime 导出
```

7. 将 `mock_skills` 公共语义迁移为 `skill_mode`。

生产入口统一使用：

```python
skill_mode: Literal["simulated", "real"] = "simulated"
```

当前真实 VLA / VLN / VLM 未接入，所以：

```text
skill_mode="simulated" 允许运行，并报告 simulated_skill / simulated_verification
skill_mode="real" fail fast，错误信息明确写 real VLA/VLN/VLM skill executors are not integrated
```

`mock_skills` 不应继续作为 CLI / task_runner / scenario_runner 的用户可见参数。如果为了兼容临时保留，只允许它映射到 `skill_mode="simulated"`，并在 debug/status/README 中不再出现 `mock_skill`。

配置层也必须同步迁移：

```text
runtime_defaults.mock_skills 任意出现都视为 invalid
runtime_defaults.skill_mode 只允许 simulated / real
skill_mode=real 在真实 VLA/VLN/VLM 未接入前 fail fast
```

8. P0 同步清掉 Stage05 actual static decision。

Phase 1 完成后，生产路径不得再存在 “smoke live + actual static”。必须在本阶段处理：

```text
StaticScenarioDecisionProvider
StaticStepDecisionProvider
live_step_decision_smoke()
_fresh_decision_provider(ctx) returning static provider
```

具体要求：

```text
src/homemaster/pipeline/adapters.py:
  不再创建 StaticScenarioDecisionProvider 作为 Stage05 actual execution provider
  不再让 live_step_decision_smoke() 参与 run_homemaster_task()

src/homemaster/stages/recovery_loop.py:
  不再调用 _fresh_decision_provider(ctx) 覆盖传入 provider
  不再 import / construct StaticScenarioDecisionProvider

src/homemaster/stages/executor.py:
  不再定义 StaticStepDecisionProvider

src/homemaster/executor.py:
  不再 re-export StaticStepDecisionProvider 或其他 test-double provider

src/homemaster/stage_runtime.py:
  不再用 import * 重导出 pipeline.stage_runtime
  不再 re-export StaticScenarioDecisionProvider / StaticMemoryQueryProvider / KeywordEmbeddingProvider
  如保留 shim，必须显式 __all__ 且只暴露 live/runtime-safe 符号
```

如果测试需要 static decisions，只能放到：

```text
tests/homemaster/test_doubles/decision_provider.py
```

Stage05 compatibility path 如短期还存在，至少必须使用 live decision provider，并明确标为 transitional compatibility path；目标 runtime 仍是 AgentRuntime tool loop。

9. 增加 early import-boundary/static gate。

禁止从生产包导入测试替身：

```text
src/homemaster 不得 import tests/homemaster/test_doubles
root compatibility shims 不得用 import * 重新导出 test doubles
compat shims 必须显式 __all__
```

10. 更新 tests。

依赖 `live_models=False` 的测试改成：

* monkeypatch live stage functions 返回稳定对象
* 或使用 `tests/homemaster/test_doubles/`
* 不再通过生产 runtime flag 触发 deterministic

### Acceptance Criteria

* `src/homemaster` 中不存在生产可运行 deterministic branch。
* `homemaster run --no-live-models` 是无效参数。
* `RuntimeMode` 不能生成 `test_double`。
* `model_boundary()` 不再输出 `deterministic`。
* 普通运行缺省不会进入 non-live fallback。
* `run_homemaster_task()`、`run_stage_07_scenario_matrix()`、`PipelineContext` 不再提供可进入 non-live brain 的生产参数。
* `runtime_defaults.live_models` 任意出现都抛出 `RuntimeConfigError`，example config 不再包含该 key。
* `runtime_defaults.mock_skills` 任意出现都抛出 `RuntimeConfigError`；合法配置改为 `runtime_defaults.skill_mode`。
* `mock_skills` 不再作为生产用户界面的一等参数；生产报告统一使用 `simulated_skill` / `simulated_verification`。
* `src/homemaster/pipeline/adapters.py` 不再走 `live_step_decision_smoke()` + actual static provider。
* `src/homemaster/stages/recovery_loop.py` 不再构造或覆盖为 `StaticScenarioDecisionProvider`。
* `src/homemaster/stages/executor.py` 不再定义 `StaticStepDecisionProvider`。
* root compatibility shims 不通过 `import *` 重新导出 test-double/static providers。
* `src/homemaster/stage_runtime.py` 和 `src/homemaster/executor.py` 不再重新暴露 static/test-double providers。
* `StaticScenarioDecisionProvider` / `StaticStepDecisionProvider` 只能在 `tests/homemaster/test_doubles/` 定义和导入。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_runtime_mode.py \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_task_runner.py \
  tests/homemaster/test_homemaster_config.py \
  tests/homemaster/test_recovery_loop.py \
  tests/homemaster/test_executor.py \
  tests/homemaster/test_import_boundaries.py \
  tests/homemaster/test_stage_07_debug_assets_do_not_contain_secrets.py \
  tests/homemaster/test_stage_07_scenario_structure.py \
  tests/homemaster/test_scenario_runner.py
```

Stage07 scenario matrix 是关键验收，不只跑单个 smoke：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_stage_07_scenarios_live.py -m "not live_api"
```

预期：大部分离线/fixture-backed Stage07 场景都运行，包括 fetch cup、check medicine、object not found、distractor rejected、retry/recovery 类场景；只跳过真正需要外部 key 的 `live_api` 标记用例。

运行后确认没有污染 tracked fixtures：

```bash
git diff -- tests/homemaster/llm_cases plan/V1.2/test_results
```

Static check:

```bash
rg -n "test_double|deterministic_task_card|deterministic_query|deterministic_plan|dummy_provider|StaticScenarioDecisionProvider|StaticStepDecisionProvider|live_step_decision_smoke|--live-models|--no-live-models|live_models|DEFAULT_LIVE_MODELS|mock_symbolic|mock_skill" src/homemaster README.md scripts config
rg -n "from tests|test_doubles|import \\*" src/homemaster
```

预期：

* `src/homemaster` 不再命中上述生产 runtime 符号。
* `src/homemaster` 不从 tests/test_doubles import，不用 `import *` 暴露 static/test-double providers。
* `README.md`、`scripts/`、`config/` 不再出现用户可执行的 non-live brain 入口。
* 如果 `tests/` 命中 deterministic/test-double 字符串，只能在 `tests/homemaster/test_doubles/` 或测试断言中出现。

---

## Phase 2. Package Skeleton, RuntimeSettings, And Decision Contract Foundations

### Priority

P1.

### Intent

先建立 AgentRuntime 需要的目录骨架、run-scoped `RuntimeSettings`、Mimo decision contract、ToolSpec/ToolResult 边界，以及 Mini-Agent-style SkillSpec / SkillLoader / SkillRegistry 的轻量实现边界。这个阶段**不实现** `AgentRuntime.run()` 主循环，也不把 Stage05 包装成新 runtime。Phase 1 已经负责清掉生产 static/deterministic provider；本阶段负责让后续 MVP 有正确的工程落点。

### Target Files

```text
src/homemaster/agent/__init__.py
src/homemaster/agent/decision.py
src/homemaster/agent/state.py
src/homemaster/tools/__init__.py
src/homemaster/tools/spec.py
src/homemaster/tools/results.py
src/homemaster/tools/registry.py
src/homemaster/tools/skill_tools.py
src/homemaster/skills/__init__.py
src/homemaster/skills/spec.py
src/homemaster/skills/loader.py
src/homemaster/skills/registry.py
src/homemaster/skills/builtin/fetch_object/SKILL.md
src/homemaster/skills/builtin/check_object_state/SKILL.md
src/homemaster/config/__init__.py
src/homemaster/config/runtime_settings.py
src/homemaster/config/runtime_paths.py
src/homemaster/runtime.py
src/homemaster/events/__init__.py
src/homemaster/providers/__init__.py
src/homemaster/providers/mimo_decision_client.py
tests/homemaster/test_agent_decision_contract.py
tests/homemaster/test_tool_registry.py
tests/homemaster/test_skill_registry.py
tests/homemaster/test_skill_loader.py
tests/homemaster/test_runtime_settings.py
tests/homemaster/test_mimo_decision_client.py
tests/homemaster/test_import_boundaries.py
```

### Implementation Tasks

1. 建立 target package skeleton。

创建空包和包级 docstring，先让目录表达职责：

```text
agent/    AgentRuntime contracts and future loop
tools/    ToolSpec / ToolResult / registry contracts
skills/   SkillSpec / SkillLoader / SkillRegistry contracts
config/   run-scoped RuntimeSettings and runtime path helpers
events/   RuntimeEvent contracts added in later phase
```

本阶段不得新增 root-level active implementation module。`src/homemaster/runtime.py`、`src/homemaster/skill_registry.py` 等旧文件如被触碰，只能逐步变成 facade/shim。

2. 提前新增最小 `RuntimeSettings`，并移除 import-time config 读取。

放置：

```text
src/homemaster/config/runtime_settings.py
```

最小字段：

```python
class RuntimeSettings(BaseModel):
    run_id: str
    skill_mode: Literal["simulated", "real"] = "simulated"
    max_turns: int = 12
    runtime_root: Path
    debug_root: Path
    results_root: Path
    provider_name: str
    embedding_provider_name: str
```

规则：

```text
import homemaster.* 不读取用户 config
RuntimeSettings 只能通过 explicit loader / resolver 构造
skill_mode="real" 在真实 executor 未接入前 fail fast
runtime_defaults.live_models / mock_skills 在 loader 层报 RuntimeConfigError
```

`src/homemaster/runtime.py` 不能继续在 import 时读取用户配置。现有 import-time defaults 要迁到显式 loader：

```text
禁止:
  module import -> load_runtime_defaults_config()
  module import -> DEFAULT_LIVE_MODELS / DEFAULT_MOCK_SKILLS from user config

允许:
  load_runtime_settings(config_path=...) 显式读取配置
  RuntimeSettings 默认值来自代码常量，不来自用户文件
```

3. 新增结构化 decision contract。

Mimo 输出只允许：

```python
AgentDecision = ToolCallDecision | FinishDecision

ToolCallDecision:
  type: Literal["tool_call"]
  tool: str
  arguments: dict[str, Any]
  reason: str | None

FinishDecision:
  type: Literal["finish"]
  status: Literal["completed", "failed"]
  summary: str
  failure_reason: str | None
```

当前阶段不允许：

```text
{"type": "ask_user", ...}
```

如果模型输出未知 type、未知 tool、schema 不通过，Runtime 写入 failure 并让下一轮 Mimo 决策；如果不能继续，则 finish failed。

4. 建立 tool contract 边界。

本阶段只定义 contract，不实现完整 dispatcher/runtime loop。

采用 Mini-Agent 式最小 tool surface，但补齐 HomeMaster 的 runtime metadata。

Mini-Agent 的 `Tool` 接口证明小系统也应把 tool contract 做成第一等对象。HomeMaster 的最小 `ToolSpec` 不应只是 Stage05 skill manifest，而应能同时生成 Mimo 可见 manifest 和 runtime 内部校验数据：

```python
class ToolExecutor(Protocol):
    def __call__(
        self,
        *,
        arguments: dict[str, Any],
        state: AgentState,
        settings: RuntimeSettings,
    ) -> ToolResult: ...

class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    executor_mode: Literal[
        "live_llm",
        "live_embedding",
        "programmatic",
        "simulated_skill",
        "simulated_verification",
        "not_integrated",
        "internal",
    ]
    selectable_by_model: bool = True
    requires_verification: bool = False
    state_effects: list[str] = []
    failure_semantics: str
    executor: ToolExecutor | None = None

    def to_mimo_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "executor_mode": self.executor_mode,
        }
```

`ToolSpec` 可以持有 executor，但不直接执行。只有 `Dispatcher` 可以调用 executor。`tool_manifests()` 只能返回 `selectable_by_model=True` 的 compact manifest。完整 output schema、state effects、failure semantics、executor 留在 Runtime 侧用于校验、trace、StateUpdater，不默认塞进每轮 prompt。

5. 定义 `MimoDecisionClient` 边界。

放置：

```text
src/homemaster/providers/mimo_decision_client.py
```

最小 contract：

```python
class MimoDecisionClient(Protocol):
    def decide(
        self,
        *,
        context: dict[str, Any],
        tools: list[dict[str, Any]],
        settings: RuntimeSettings,
    ) -> AgentDecision: ...
```

实现边界：

```text
FakeMimoDecisionClient:
  tests/homemaster/test_doubles/ 或测试内，用固定 decision list

LiveMimoDecisionClient:
  production provider wrapper，调用 live Mimo
  不做 deterministic fallback
  不直接执行 tool
  不直接修改 AgentState
```

6. 定义职责边界表。

```text
ToolSpec:
  declares name / description / input_schema / output_schema / executor_mode / selectable_by_model / state_effects / failure_semantics
  may hold executor reference for Dispatcher
  generates compact Mimo manifest
  does not execute tools

ToolRegistry:
  stores ToolSpec by name
  returns compact manifests for selectable_by_model=True only
  may contain internal tools such as finish_task, but they are not Mimo-selectable

Dispatcher:
  validates tool existence, current availability, permission, and input schema
  calls executor
  converts executor exception into ToolResult failure
  does not mutate AgentState

ToolResult:
  contains typed data / evidence_refs / failure_reason / retryable / summary
  does not contain state_patch
  does not mutate or prescribe direct AgentState writes

StateUpdater:
  is the only component that transforms AgentState
  interprets ToolSpec.state_effects + ToolResult typed data
  appends FailureRecord for failed tool results

EventSink:
  append-only events
  redacts payload
  does not decide flow and does not mutate AgentState
```

7. 实现轻量 `SkillSpec` / `SkillLoader` / `SkillRegistry` 边界。

这一层要参照 Mini-Agent 的 `skill_loader.py` / `skill_tool.py`，但不能把 skill 做成第二套执行系统。

最小 `SkillSpec`：

```python
class SkillSpec(BaseModel):
    name: str
    description: str
    allowed_tools: list[str]
    activation_rules: list[str] = []
    context_snippet: str
    content_path: Path
    examples: list[str] = []
    constraints: list[str] = []
    success_criteria: list[str] = []
    version: str = "v1"
```

硬约束：

```text
SkillSpec 不包含 executor
SkillSpec 不返回 ToolResult
SkillSpec 不直接修改 AgentState
SkillSpec 不允许 deterministic/test_double fallback
SkillRegistry 不决定下一步 action，只提供 candidate skill metadata
```

本阶段实现：

```text
skills/loader.py:
  读取 SKILL.md frontmatter/body
  校验 name / description / allowed_tools
  解析相对引用，但不默认把全文塞进 prompt

skills/registry.py:
  注册 SkillSpec
  根据 user_request / task_card 返回 candidate skill summaries
  校验 allowed_tools 都存在于 ToolRegistry

tools/skill_tools.py:
  定义 get_skill tool 的 schema，不在本阶段接入 AgentRuntime loop
  get_skill 输入 skill_name，输出 full skill content + allowed_tools + constraints
```

第一版 builtin skills：

```text
fetch_object:
  allowed_tools:
    understand_task
    retrieve_memory
    ground_target
    get_skill
    navigate
    observe
    manipulate
    verify
    update_memory

check_object_state:
  allowed_tools:
    understand_task
    retrieve_memory
    ground_target
    get_skill
    navigate
    observe
    verify
    update_memory
    update_user_profile
```

8. 增加 import-boundary 硬门禁。

本阶段测试要保护后续迁移边界：

```text
src/homemaster 不得 import tests 或 test_doubles
root shims 不得 import *
new active implementation 不得放在 src/homemaster root
```

### Acceptance Criteria

* `agent/`、`tools/`、`skills/`、`config/`、`events/` skeleton 存在，并有包级职责说明。
* `RuntimeSettings` active implementation 位于 `src/homemaster/config/runtime_settings.py`。
* import `homemaster.*` 不读取用户 config，除非调用 explicit loader。
* `src/homemaster/runtime.py` 不再有 import-time `_defaults_cfg = load_runtime_defaults_config()` 类行为。
* Mimo decision schema 只有 `tool_call` 和 `finish`；当前没有 `ask_user`。
* `ToolSpec` 具备 Mini-Agent 式 `name/description/input schema/executor` 基础能力，并补齐 HomeMaster 所需的 `output_schema/executor_mode/selectable_by_model/state_effects/failure_semantics`。
* `ToolSpec` 包含 `executor: ToolExecutor | None`，但执行只能由 Dispatcher 触发。
* `MimoDecisionClient` protocol 存在；fake/live client 边界清楚且不引入 deterministic fallback。
* `tool_manifests()` 只返回 compact Mimo manifest，不把完整 runtime metadata 和历史 trace 默认塞入 prompt。
* `ToolResult` 不包含 `state_patch`，不直接表达 AgentState mutation。
* 职责边界表被写入文档，并有对应单元测试保护关键边界。
* `finish_task` 可以注册为 internal tool，但 `selectable_by_model=False`，不会出现在 `tool_manifests()`。
* `SkillSpec` / `SkillLoader` / `SkillRegistry` 按 Mini-Agent progressive disclosure 模型实现轻量边界。
* `SkillSpec` 不包含 executor / ToolResult / AgentState mutation。
* `SkillLoader` 能从 builtin `SKILL.md` 读取 metadata 和 body。
* `SkillRegistry` 能返回 compact skill summaries，并校验 `allowed_tools` 都存在于 ToolRegistry。
* `get_skill` tool 有明确 schema，但不绕过 Dispatcher / StateUpdater。
* root-level 不新增 active implementation module。
* `src/homemaster` 不 import `tests` / `test_doubles`，compat shim 不用 `import *` 重导出。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_agent_decision_contract.py \
  tests/homemaster/test_tool_registry.py \
  tests/homemaster/test_skill_registry.py \
  tests/homemaster/test_skill_loader.py \
  tests/homemaster/test_runtime_settings.py \
  tests/homemaster/test_mimo_decision_client.py \
  tests/homemaster/test_import_boundaries.py
```

新增/更新测试：

* Mimo decision parser 接受合法 `tool_call` / `finish`。
* Mimo decision parser 拒绝 `ask_user` / unknown type / unknown tool。
* `ToolSpec.to_mimo_manifest()` 输出只包含模型需要选择 tool 的 compact 字段。
* `ToolSpec.to_mimo_manifest()` 不输出 executor。
* Dispatcher 调用 `ToolSpec.executor`，`ToolSpec` 本身不执行。
* `tool_manifests()` 过滤掉 `selectable_by_model=False` 的 tools。
* `finish_task` 不出现在 selectable manifests。
* `MimoDecisionClient` fake client 能返回固定 `ToolCallDecision` / `FinishDecision`。
* `LiveMimoDecisionClient` 无 key 时 fail fast 或 skip live tests，不 fallback 到 deterministic。
* `SkillLoader` 读取 `fetch_object` / `check_object_state` 的 `SKILL.md`。
* `SkillRegistry` 返回 compact summaries，不返回 full SKILL.md body。
* `SkillRegistry` 拒绝引用未知 tool 的 `allowed_tools`。
* `get_skill` schema 接受 `skill_name`，输出中包含 skill content / allowed_tools / constraints。
* `SkillSpec` 没有 executor 字段。
* `RuntimeSettings` 可以在同进程构造两个不同实例，不污染全局状态。
* import `homemaster.task_runner` 不触发用户 config 读取。
* root-level active module allowlist 精确匹配。
* src package import-boundary 测试拒绝 `tests` / `test_doubles` / `import *`。

Static check:

```bash
rg -n "from tests|test_doubles|import \\*" src/homemaster
find src/homemaster -maxdepth 1 -type f | sort
```

预期：无生产导入测试替身；root-level files 只包含 public facade 或已登记 lifecycle 的 compatibility shims。

---

## Phase 3. Runtime Safety And Artifact Hygiene

### Priority

P2.

### Intent

修掉 run id path traversal 风险，并阻止普通运行/测试污染 tracked fixtures。

### Target Files

```text
src/homemaster/task_runner.py
src/homemaster/scenario_runner.py
src/homemaster/config/runtime_paths.py
src/homemaster/runtime.py
src/homemaster/cli/app.py
README.md
scripts/run_homemaster_scenarios.sh
scripts/capture_scenario_snapshot.py
scripts/compare_all_baselines.py
tests/homemaster/test_task_runner.py
tests/homemaster/test_scenario_runner.py
tests/homemaster/test_stage_07_scenarios_live.py
tests/homemaster/test_stage_07_debug_assets_do_not_contain_secrets.py
```

### Implementation Tasks

1. 增加 `validate_run_id()`。

放置位置：

```text
src/homemaster/config/runtime_paths.py
```

`src/homemaster/runtime.py` 如短期保留导入，只能 re-export 该 helper，不承载 active implementation。

规则：

```text
^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$
```

拒绝：

```text
empty
absolute path
path separators
..
control characters
too long
```

必须在任何 materialize/write 前执行。

2. `run_homemaster_task()` 和 `run_stage_07_scenario_matrix()` 增加结果目录参数。

目标：

```text
run_homemaster_task(results_root=...)
run_stage_07_scenario_matrix(results_root=...)
```

默认写 ignored runtime/debug/results 目录；测试必须传 `tmp_path / "results"`。普通运行不得再写入 `STAGE_07_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_07"` 这类 tracked fixture 目录。

3. README 和 scenario script 默认 debug root 改为：

```text
var/homemaster/debug
```

不是：

```text
tests/homemaster/llm_cases
```

4. 文档统一描述为：

```text
AgentRuntime + live Mimo decisions + simulated robot tools
```

不要写“完整真实机器人执行”。

5. 处理 legacy snapshot/baseline 脚本。

这些脚本当前可能仍然描述或调用 deterministic/non-live matrix：

```text
scripts/capture_scenario_snapshot.py
scripts/compare_all_baselines.py
```

迁移方式：

```text
如果脚本仍需要保留，只能变成 explicit legacy baseline tool
脚本不能调用 production runtime 的 live_models=False
脚本输出必须写 ignored runtime/snapshot 目录
脚本文案必须标注 legacy/test baseline，不得称为 current production mode
```

如果 legacy baseline 对当前 V1.3 已无价值，直接删除脚本比保留误导性入口更好。

### Acceptance Criteria

* invalid `run_id` 在任何文件写入前失败。
* 测试不修改 `tests/homemaster/llm_cases` 或 `plan/V1.2/test_results`。
* README 的 run 示例不再写 tracked fixture。
* 脚本默认 debug root 是 ignored runtime/debug 目录。
* `run_homemaster_task()` 默认不写 tracked `TEST_RESULTS_ROOT`。
* legacy snapshot/baseline 脚本不再调用生产 non-live brain；若保留，必须标为 legacy/test-only。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_task_runner.py \
  tests/homemaster/test_scenario_runner.py \
  tests/homemaster/test_stage_07_debug_assets_do_not_contain_secrets.py
```

Stage07 matrix artifact hygiene check：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_stage_07_scenarios_live.py -m "not live_api"
```

Dirty check：

```bash
git diff -- tests/homemaster/llm_cases plan/V1.2/test_results
```

预期：没有新 diff。

新增测试：

* `run_id="../x"` fail
* `run_id="/tmp/x"` fail
* `run_id="a/b"` fail
* `run_id="live-fetch-cup-001"` pass
* `run_homemaster_task()` 使用 tmp results root
* scenario matrix 使用 tmp results root

---

## Phase 4. AgentRuntime MVP Tool Loop

### Priority

P3.

### Intent

新增真正的 `AgentRuntime` MVP，让它能以 tool loop 跑通一个简单场景（例如 fetch cup）。本阶段保留现有 Stage02-06 代码作为可复用工具实现来源，但主入口不应是固定 Stage Pipeline。

### Target Files

```text
src/homemaster/agent/__init__.py
src/homemaster/agent/runtime.py
src/homemaster/agent/state.py
src/homemaster/agent/context_builder.py
src/homemaster/agent/decision.py
src/homemaster/tools/__init__.py
src/homemaster/tools/spec.py
src/homemaster/tools/dispatcher.py
src/homemaster/tools/state_updater.py
src/homemaster/tools/builtin.py
src/homemaster/tools/skill_tools.py
src/homemaster/tools/results.py
src/homemaster/tools/registry.py
src/homemaster/skills/spec.py
src/homemaster/skills/loader.py
src/homemaster/skills/registry.py
src/homemaster/memory/context_snapshot.py
src/homemaster/providers/mimo_decision_client.py
src/homemaster/events/runtime_events.py
src/homemaster/events/sinks.py
src/homemaster/events/sanitizer.py
src/homemaster/config/runtime_settings.py
src/homemaster/task_runner.py
tests/homemaster/test_agent_runtime.py
tests/homemaster/test_agent_state.py
tests/homemaster/test_context_builder.py
tests/homemaster/test_tool_dispatcher.py
tests/homemaster/test_tool_registry.py
tests/homemaster/test_agent_tools.py
tests/homemaster/test_skill_loader.py
tests/homemaster/test_skill_registry.py
tests/homemaster/test_context_snapshot.py
tests/homemaster/test_mimo_decision_client.py
tests/homemaster/test_task_runner.py
```

### Implementation Tasks

1. 新增 `AgentState`。

最小字段：

```text
run_id
user_request
task_card
memory_hits
target_candidates
current_location
current_object
holding_object
actions
observations
verifications
failures
active_skills
loaded_skill_contexts
memory_context_snapshot
user_context_snapshot
status: running / completed / failed
turn_index
runtime_settings snapshot
```

`AgentState` 是运行时唯一核心状态。`PipelineContext` 可以作为兼容 snapshot 存在，但不能驱动 AgentRuntime 主循环。

2. 新增 `ContextBuilder.build(state)`。

输出本轮 compact context，至少包含：

```text
current goal
task_card summary
memory_hits summary
target_candidates
current location/object/holding state
recent actions
recent observations
recent verifications
failure records and negative evidence
available tools with executor_mode
turn index and max_turns
compact active skill summaries
loaded skill snippets requested through get_skill
MEMORY.md snapshot
USER.md snapshot
```

`ContextBuilder` 不控制流程，不返回 next phase/status label，不决定 retry/replan/verify。

ContextBuilder 输出必须分层：

```text
stable_context:
  runtime constraints
  compact tool manifest
  compact active skill summaries
  MEMORY.md / USER.md snapshots

task_state_context:
  user request
  task card
  target candidates
  current location/object/holding state
  memory hits and rejected evidence

recent_dynamics_context:
  recent actions
  recent observations
  recent verifications
  failures and negative evidence
  turn_index / max_turns
```

3. 引入 Mini-Agent 式 progressive disclosure，但以 `AgentState` 为核心。

Mini-Agent 主要靠 message history 累积上下文；HomeMaster 不能照搬这一点。每轮 prompt 应由 `ContextBuilder` 从 `AgentState` 生成 compact context：

```text
always include:
  current task/status/turn
  compact task card
  compact current embodied state
  recent actions/observations/verifications
  open failures and negative evidence
  compact selectable tool manifest
  compact active skill summaries
  MEMORY.md / USER.md prompt snapshots

do not include by default:
  full runtime event trace
  full prompt history
  full debug payload
  full skill implementation details unless get_skill loaded them
  secrets or provider raw payload
```

如果某个 skill 需要较长说明，Mimo 可以调用 `get_skill`。`get_skill` 是普通 runtime tool：它经过 Dispatcher 校验，结果通过 StateUpdater 写入 `AgentState.loaded_skill_contexts`，EventSink 记录受控 trace。Skill 全文不能由 ContextBuilder 默认塞进 prompt。

`MEMORY.md` / `USER.md` 是长期上下文快照，给模型读，不是权威存储。结构化 memory/profile 仍是 source of truth。Mimo 不能直接写这些文件，只能通过 `update_memory` / `update_user_profile` 提交 proposal，由 Runtime 校验、去重、审计后提交。

`memory/context_snapshot.py` 必须定义快照刷新 contract：

```text
Input:
  object_memory records
  fact_memory.jsonl records
  user profile / preference records
  run-scoped RuntimeSettings paths

Output:
  MEMORY.md read-only prompt snapshot
  USER.md read-only prompt snapshot
  snapshot metadata: source versions / generated_at / content_hash

Refresh rules:
  AgentRuntime start: load latest valid snapshots or regenerate from structured stores
  update_memory commit success: atomically regenerate MEMORY.md after structured write
  update_user_profile commit success: atomically regenerate USER.md after structured write
  proposal rejected: do not refresh snapshot
  stale snapshot detected: regenerate before next ContextBuilder build
```

测试必须覆盖 snapshot stale/refresh，不允许结构化 memory 已提交但 prompt snapshot 仍旧。

4. 新增 `AgentRuntime.run()` 主循环。

主循环必须遵循：

```text
read AgentState
build context
call Mimo decision client
emit decision event
validate tool_call
dispatch tool
apply state update
emit tool_result/state_transition event
repeat until finish / failed / max_turns
```

`MimoDecisionClient` 必须通过 `src/homemaster/providers/mimo_decision_client.py` 注入：

```text
FakeMimoDecisionClient:
  offline tests only, fixed decision list

LiveMimoDecisionClient:
  production live Mimo decision provider
  no deterministic fallback
  no direct tool execution
  no direct AgentState mutation
```

不允许写成：

```text
PLAN -> DECIDE -> ACT -> VERIFY -> RECOVER
```

`phase_label` 只作为 event/status 标签。

5. 新增 first-version tools。

必须注册 runtime tools：

```text
understand_task
retrieve_memory
ground_target
get_skill
navigate
observe
manipulate
verify
update_memory
update_user_profile
finish_task
```

`finish_task` 是 runtime internal tool/finalizer，不进入 `tool_manifests()`，也不能由 Mimo 通过 `{"type": "tool_call", "tool": "finish_task"}` 调用。Mimo 结束任务只能返回：

```json
{
  "type": "finish",
  "status": "completed",
  "summary": "The requested object has been delivered."
}
```

每个 tool 都必须有：

```text
name
description
input_schema
output_schema
executor_mode
requires_verification
selectable_by_model
executor
state_effects
failure_semantics
```

当前 executor mode：

```text
understand_task: live_llm
retrieve_memory: live_llm + live_embedding 或现有 memory retrieval
ground_target: programmatic
get_skill: programmatic
navigate: simulated_skill
observe: simulated_skill
manipulate: simulated_skill
verify: simulated_verification
update_memory: programmatic
update_user_profile: programmatic
finish_task: internal programmatic finalization helper, selectable_by_model=false
```

V1.3 明确定义：

```text
verify.selectable_by_model = True
```

验证是 Mimo 可显式选择的 tool，不由 Runtime 在每次 manipulate 后自动插入。这样避免把 AgentRuntime 重新写成隐含 ACT -> VERIFY 固定流程。Runtime 只负责校验 tool 是否允许、执行、更新 state 和记录 trace。

每个 tool executor 返回结构化 `ToolResult`，不能退化成只有自然语言 `content`：

```text
success
tool_name
executor_mode
summary
data
evidence_refs
failure_reason
retryable
```

`ToolResult` 不允许携带 `state_patch` 或任何可直接写入 AgentState 的 patch。`StateUpdater` 只能从结构化 `ToolResult.data`、`failure_reason`、`evidence_refs` 和对应 `ToolSpec.state_effects` 推导 AgentState transition。自然语言 summary 只用于 trace/debug 和给 Mimo 的下一轮 compact context。

第一版 skills 同步接入，但保持轻量：

```text
fetch_object:
  allowed_tools:
    understand_task
    retrieve_memory
    ground_target
    get_skill
    navigate
    observe
    manipulate
    verify
    update_memory

check_object_state:
  allowed_tools:
    understand_task
    retrieve_memory
    ground_target
    get_skill
    navigate
    observe
    verify
    update_memory
    update_user_profile
```

Skill 的作用范围：

```text
可以:
  进入 ContextBuilder 的 compact skill summaries
  通过 get_skill 加载完整 SKILL.md
  限定当前 active skill 允许出现的 tools
  提供 examples / constraints / success criteria

不可以:
  自己执行动作
  返回 ToolResult
  修改 AgentState
  决定下一轮 tool
  绕过 Dispatcher / StateUpdater / EventSink
```

文件组织要求：

```text
ToolResult 定义放在 src/homemaster/tools/results.py
ToolSpec 定义放在 src/homemaster/tools/spec.py
SkillSpec / SkillLoader / SkillRegistry 定义放在 src/homemaster/skills/
tool registry 放在 src/homemaster/tools/registry.py
event schema/sinks 放在 src/homemaster/events/
runtime settings 放在 src/homemaster/config/
```

本阶段不得新增 `src/homemaster/runtime_events.py`、`src/homemaster/simulated_skills.py` 这类 root-level active implementation。

6. 不实现 `ask_user`。

可以预留内部接口位置，但：

```text
tool_registry.tool_manifests() 不返回 ask_user
tool_registry.tool_manifests() 不返回 finish_task
Mimo 输出 ask_user 会被视为 invalid_decision
Mimo 输出 tool_call finish_task 会被视为 invalid_decision
AgentRuntime 不会暂停等待用户输入
```

信息不足时，Mimo 要么选择 best-effort tool，要么 finish failed with reason。

7. 用现有 Stage 代码包一层 tool executor。

迁移期可以这样复用：

```text
understand_task executor may call run_stage02()
retrieve_memory executor may call run_stage03()
ground_target executor may call build_planning_context()
get_skill executor may call SkillRegistry / SkillLoader
navigate / observe / manipulate executors may call simulated skill executors
verify executor may call current verifier.py symbolic/simulated verification
update_memory executor may call Stage06 memory commit programmatic pieces
update_user_profile executor may call profile update validator / memory profile writer
finish_task is internal finalizer only; it is never selected by Mimo
```

但是 AgentRuntime 不应按 Stage02-06 固定顺序调用这些工具；它只执行 Mimo 每轮选择的 tool。

8. fetch cup MVP。

新增离线可跑的最小 scenario 测试，使用 fake Mimo decision client。测试必须验证 runtime 执行的是“模型每轮选择”，而不是 hardcoded pipeline。

允许的 linear happy-path fake decisions 示例：

```text
ToolCallDecision(tool="understand_task", ...)
ToolCallDecision(tool="retrieve_memory", ...)
ToolCallDecision(tool="ground_target", ...)
ToolCallDecision(tool="navigate", ...)
ToolCallDecision(tool="observe", ...)
ToolCallDecision(tool="manipulate", ...)
ToolCallDecision(tool="verify", ...)
FinishDecision(status="completed", summary="...")
```

必须同时新增非线性 fake decisions：

```text
observe failed -> next fake decision chooses retrieve_memory
verify failed -> next fake decision chooses observe
all target candidates exhausted -> next fake decision returns FinishDecision(status="failed")
```

测试替身只能在测试内或 `tests/homemaster/test_doubles/`。不得通过 `finish_task` tool call 结束任务。

### Acceptance Criteria

* 存在 `AgentRuntime.run()` 主循环。
* `AgentRuntime` 不是固定 PLAN/ACT/VERIFY/RECOVER pipeline。
* Mimo 每轮通过结构化 decision 选择 tool 或 finish。
* `AgentState` 是运行时唯一核心状态。
* `ContextBuilder` 输出 compact context，不依赖完整 message history 作为主状态。
* `ContextBuilder` 输出 stable_context / task_state_context / recent_dynamics_context 三层上下文。
* `MEMORY.md` / `USER.md` 只作为模型可读快照进入 stable_context；结构化 memory/profile 仍是 source of truth。
* 每轮只给 Mimo compact selectable tool manifest；完整 runtime metadata 保留在 Runtime 侧。
* 每轮只给 Mimo compact skill summaries；完整 `SKILL.md` 只能通过 `get_skill` 加载。
* `get_skill` 是普通 tool call，经过 Dispatcher、StateUpdater、EventSink，不绕过 Runtime。
* Tool 执行结果都会写回 `AgentState`。
* Tool executor 返回结构化 `ToolResult`，`StateUpdater` 不从自然语言文本猜状态。
* `ToolResult` 不包含 `state_patch`；`StateUpdater` 是唯一 AgentState writer。
* `SkillSpec` 不包含 executor，不能返回 ToolResult，不能直接修改 AgentState。
* `update_memory` / `update_user_profile` 是 Mimo 提交长期记忆/用户偏好更新 proposal 的唯一入口。
* AgentRuntime 新增模块遵守 target package layout；root-level 不新增 active implementation module。
* tool failure 写入 `AgentState.failures`，下一轮由 Mimo 基于 failure record 决策。
* AgentRuntime 当前阶段不暴露、不执行 `ask_user`，也不会进入 `needs_user_input`。
* legacy pipeline compatibility 如暂时保留 `ask_user/needs_user_input`，必须标注为 `legacy_compat_only=true`，且不能通过默认 `run_homemaster_task()` 进入。
* `navigate` / `observe` / `manipulate` / `verify` 明确显示 `simulated_skill` / `simulated_verification`。
* production runtime 不允许 deterministic/test_double 作为运行模式。
* runtime event trace 能记录每轮 decision、tool_call、tool_result、state status。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_agent_runtime.py \
  tests/homemaster/test_agent_state.py \
  tests/homemaster/test_context_builder.py \
  tests/homemaster/test_tool_dispatcher.py \
  tests/homemaster/test_tool_registry.py \
  tests/homemaster/test_agent_tools.py \
  tests/homemaster/test_skill_loader.py \
  tests/homemaster/test_skill_registry.py \
  tests/homemaster/test_context_snapshot.py \
  tests/homemaster/test_mimo_decision_client.py \
  tests/homemaster/test_task_runner.py
```

新增测试：

* AgentRuntime 按 fake Mimo decision list 执行多个 turn。
* ContextBuilder 不包含 full trace、raw prompt、secret、完整 debug payload。
* ContextBuilder 将 `MEMORY.md` / `USER.md` 放入 stable_context，而不是 recent_dynamics_context。
* `memory/context_snapshot.py` 从结构化 memory/profile 生成 `MEMORY.md` / `USER.md`，并记录 source versions / content_hash。
* `update_memory` commit 成功后原子刷新 `MEMORY.md`。
* `update_user_profile` commit 成功后原子刷新 `USER.md`。
* stale snapshot 会在下一轮 ContextBuilder 前被检测并刷新。
* selectable tool manifest 只包含 compact tool fields。
* `verify` 出现在 selectable tool manifest，且由 Mimo fake decisions 显式调用。
* 默认 context 只包含 skill summary，不包含完整 `SKILL.md`。
* Mimo 调用 `get_skill(fetch_object)` 后，`AgentState.loaded_skill_contexts` 增加该 skill 内容。
* active skill 的 `allowed_tools` 能过滤 tool manifest，但不决定下一步 tool。
* `update_user_profile` 只提交受控 proposal，不允许模型直接写 `USER.md`。
* ToolResult 的 typed `data` / `failure_reason` / `evidence_refs` 驱动 StateUpdater，纯文本 summary 不驱动状态。
* `src/homemaster/events/runtime_events.py` 存在；`src/homemaster/runtime_events.py` 不作为 active implementation 新增。
* `src/homemaster/tools/registry.py` 存在；`src/homemaster/skill_registry.py` 如保留只能是 compatibility shim。
* max_turns exceeded 后 final status 是 failed。
* invalid tool name 写入 failure 并继续下一轮或 fail with reason。
* schema invalid tool call 写入 failure，event trace 记录 invalid_tool_call。
* tool executor 返回 failed 时，StateUpdater 追加 failure，下一轮 context 包含 failure。
* `ask_user` decision 被拒绝。
* `tool_call` 到 `finish_task` 被拒绝，终止只能走 `FinishDecision`。
* fetch cup MVP happy path 以 `FinishDecision(status="completed")` 结束，不调用 `finish_task` tool。
* observe failed 后下一轮 fake decision 选择 `retrieve_memory`，证明 runtime 不是固定 pipeline。
* verify failed 后下一轮 fake decision 选择 `observe`，证明 failure record 会进入下一轮 context。
* 无可用目标后 fake decision 返回 `FinishDecision(status="failed")`，runtime 正确终止。
* simulated tools 在 debug/status 中输出 `simulated_skill` / `simulated_verification`。

---

## Phase 5. Pipeline Compatibility Layer

### Priority

P4.

### Intent

把现有 Stage Pipeline 收敛为兼容层，使用 Phase 2 已建立的 run-scoped `RuntimeSettings`，避免旧 `task_runner.py` 和 import-time globals 继续绑死 runtime。这个阶段不是强化 Stage Pipeline；它只是为迁移期间保留旧测试和旧入口提供可控边界。未来主入口仍是 `AgentRuntime.run()`。

### Target Files

```text
src/homemaster/pipeline/core.py
src/homemaster/pipeline/runner.py
src/homemaster/config/runtime_settings.py
src/homemaster/config/runtime_paths.py
src/homemaster/runtime.py
src/homemaster/task_runner.py
src/homemaster/agent/runtime.py
tests/homemaster/test_pipeline_core.py
tests/homemaster/test_stage_registry.py
tests/homemaster/test_task_runner.py
tests/homemaster/test_runtime_settings.py
tests/homemaster/test_homemaster_config.py
```

### Implementation Tasks

1. 扩展 Phase 2 的 `RuntimeSettings`，覆盖 compatibility pipeline 需要的字段。

新增字段：

```text
config_path
event_sink
recovery_max_attempts
executor_step_multiplier
executor_minimum_max_steps
```

2. 继续改造 import-time config。

短期允许硬编码 defaults，但用户配置必须通过 explicit loader 进入 `RuntimeSettings`。

`RuntimeSettings` active implementation 放在：

```text
src/homemaster/config/runtime_settings.py
```

`src/homemaster/runtime.py` 如仍存在，只能作为 compatibility/public facade，不能继续承载新 runtime settings 实现或读取用户 config。

3. 新增 `PipelineRunner` 兼容层。

职责：

```text
ordered stage execution for legacy Stage02-06 tests
stage enter/exit logging
elapsed time
partial context preservation on failure
stage exception wrapping
event emission with phase_label/status labels
```

`PipelineRunner` 必须标注为 compatibility layer，不作为新 AgentRuntime 的架构主线。

4. `task_runner.py` 只保留：

```text
parameter validation
run_id validation
data-source resolution
RuntimeSettings construction
AgentRuntime invocation by default
PipelineRunner invocation only for explicit compatibility/test entrypoint
final debug asset writing
```

5. 明确入口策略。

目标：

```text
run_homemaster_task() -> AgentRuntime.run()
run_stage_pipeline_compat() -> PipelineRunner.run()  # explicit compatibility/test helper
```

不允许 `run_homemaster_task()` 默认继续走 `pipeline_compat`。如果某些测试或旧脚本还需要 Stage02-06，必须显式调用 compatibility helper 或显式传入 compatibility-only 开关；该开关不能成为生产 CLI 默认路径。

compatibility debug payload 必须写：

```text
runtime_entrypoint = "pipeline_compat"
target_entrypoint = "AgentRuntime.run"
migration_required = true
default_entrypoint = false
```

### Acceptance Criteria

* `task_runner.py` 不再直接 loop `for stage in registry.stages()`。
* 同一进程可以构造两个不同 settings 并分别运行。
* debug payload 记录 effective runtime settings。
* import `homemaster.*` 不读取用户 runtime config，除非调用 explicit loader。
* `PipelineRunner` 明确标为 compatibility layer。
* `run_homemaster_task()` 默认入口是 `AgentRuntime.run()`。
* `PipelineRunner` 只能通过显式 compatibility helper/flag 进入，不能作为默认生产路径。
* compatibility debug/status 明确标注 `runtime_entrypoint="pipeline_compat"`、`migration_required=true`、`default_entrypoint=false`。
* `RuntimeSettings` active implementation 位于 `src/homemaster/config/runtime_settings.py`。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_pipeline_core.py \
  tests/homemaster/test_stage_registry.py \
  tests/homemaster/test_task_runner.py \
  tests/homemaster/test_runtime_settings.py \
  tests/homemaster/test_homemaster_config.py
```

新增测试：

* PipelineRunner 按注册顺序执行。
* stage 抛错时保留 partial context。
* 两个不同 RuntimeSettings 同进程不污染。
* root `src/homemaster/runtime.py` 不读取用户 config；如保留，只代理到 `config/runtime_settings.py`。
* `run_homemaster_task()` 默认调用 `AgentRuntime.run()`。
* `run_stage_pipeline_compat()` 或显式 compatibility flag 调用 `PipelineRunner.run()`，且 debug payload 能区分 `AgentRuntime` vs `pipeline_compat`。
* import `homemaster.task_runner` 不触发用户 runtime config 读取。

---

## Phase 6. Mini-Agent-style Skill Implementation And Simulated Tool Boundary

### Priority

P5.

### Intent

mock skill 可以暂留，但必须改成明确 simulation layer，并纳入 AgentRuntime tool system。本阶段同时把 Phase 2/4 的轻量 skills 边界补到可扩展实现：Mini-Agent-style `SkillLoader` / `SkillRegistry` / `get_skill` progressive disclosure 可以工作，但 skills 仍然不能成为第二套 executor 或 workflow engine。

### Target Files

```text
src/homemaster/tools/spec.py
src/homemaster/tools/registry.py
src/homemaster/tools/results.py
src/homemaster/tools/builtin.py
src/homemaster/tools/simulated.py
src/homemaster/tools/skill_tools.py
src/homemaster/skills/spec.py
src/homemaster/skills/loader.py
src/homemaster/skills/registry.py
src/homemaster/skills/builtin/fetch_object/SKILL.md
src/homemaster/skills/builtin/check_object_state/SKILL.md
src/homemaster/skill_registry.py
src/homemaster/stages/executor.py
src/homemaster/agent/runtime.py
src/homemaster/task_runner.py
src/homemaster/scenario_runner.py
src/homemaster/cli/app.py
tests/homemaster/test_skill_registry.py
tests/homemaster/test_skill_loader.py
tests/homemaster/test_agent_tools.py
tests/homemaster/test_executor.py
tests/homemaster/test_skill_selector.py
```

### Implementation Tasks

1. 将旧 `SkillManifest` 拆成 `ToolSpec` 和 Mini-Agent-style `SkillSpec`。

`ToolSpec` 字段：

```text
name
description
input_schema
output_schema
executor_mode
requires_verification
selectable_by_model
executor
state_effects
failure_semantics
```

`SkillSpec` 字段：

```text
name
description
allowed_tools
activation_rules
context_snippet
content_path
examples
constraints
success_criteria
version
```

`SkillSpec` 硬约束：

```text
no executor
no ToolResult
no AgentState mutation
no deterministic fallback
no hidden PLAN/ACT/VERIFY workflow
```

这部分应吸收 Mini-Agent `Tool.to_schema()` 的优点：tool 自己能生成模型可见 schema，而不是由 prompt 字符串手工拼装。HomeMaster 需要更严格的两个视图：

```text
to_mimo_manifest():
  给模型看的 compact manifest，只包含 name / description / input_schema / executor_mode / simulated marker

to_runtime_spec():
  给 Runtime / Dispatcher / StateUpdater 用的完整 spec，包含 output_schema / state_effects / failure_semantics / selectable_by_model / executor
```

`ToolSpec.description` 必须写清楚执行约束，不只写业务用途。最低要求：

```text
navigate:
  说明当前是 simulated_skill
  说明只接受 target_anchor_id 或 target_location
  说明成功会更新 current_location
  说明失败会产生 FailureRecord 而不是自动 recover

observe:
  说明当前是 simulated_skill
  说明会追加 observations / scene_evidence
  说明未观察到目标属于 negative evidence

manipulate:
  说明当前是 simulated_skill
  说明可能更新 holding_object 或 object state
  说明失败必须返回 failure_reason

verify:
  说明当前是 simulated_verification
  说明会追加 verifications
  说明 verification failed 写入 failures，由下一轮 Mimo 决定后续动作
```

`SkillSpec.context_snippet` 必须写清任务策略约束，不写执行代码。最低要求：

```text
fetch_object:
  说明先确认目标、检索记忆、定位候选、观察、操作、验证
  说明失败证据写入 state 后由下一轮 Mimo 决策
  allowed_tools 必须包含 get_skill / navigate / observe / manipulate / verify

check_object_state:
  说明以观察和验证为主，不默认 manipulate
  说明用户偏好更新只能走 update_user_profile proposal
  allowed_tools 必须包含 get_skill / observe / verify / update_user_profile
```

2. 实现 Mini-Agent-style skill progressive disclosure。

参照 Mini-Agent：

```text
mini_agent/tools/skill_loader.py -> homemaster/skills/loader.py
mini_agent/tools/skill_tool.py   -> homemaster/tools/skill_tools.py
```

实现要求：

```text
SkillLoader:
  读取 SKILL.md frontmatter/body
  校验 name / description / allowed_tools / activation_rules
  解析 relative paths 时只允许 skill root 内路径
  返回 SkillSpec + body，不直接修改 AgentState

SkillRegistry:
  注册 builtin skills
  返回 compact summaries 给 ContextBuilder
  根据 user_request / task_card 给出 candidate skills
  校验 allowed_tools 都存在于 ToolRegistry

get_skill tool:
  executor_mode=programmatic
  selectable_by_model=True
  input_schema={skill_name}
  output_schema={skill_name, content, allowed_tools, constraints, examples}
  通过 StateUpdater 写入 AgentState.loaded_skill_contexts
```

`get_skill` 必须是普通 tool call，不是特殊通道。它必须经过 schema validation、Dispatcher、ToolResult、StateUpdater、RuntimeEvent。

3. 注册 first-version runtime tools。

必须覆盖：

```text
understand_task
retrieve_memory
ground_target
get_skill
navigate
observe
manipulate
verify
update_memory
update_user_profile
finish_task
```

`tool_manifests()` 只返回 `selectable_by_model=True` 的工具。V1.3 明确定义 `verify.selectable_by_model=True`：验证由 Mimo 显式选择，不由 Runtime 自动插入。prompt / skill snippets 必须说明何时调用 verify，StateUpdater 负责记录 verification 结果。

`finish_task` 必须是 `selectable_by_model=False`。终止任务只能走 `FinishDecision`，不能作为 tool manifest 暴露给 Mimo。

4. 移动 simulated executors。

从 `skill_registry.py` 移到：

```text
src/homemaster/tools/simulated.py
```

`src/homemaster/skill_registry.py` 如因兼容需要保留，只能成为 thin shim，re-export `src/homemaster/tools/registry.py` 的公开符号，并在 shim lifecycle 文档里写明目标删除版本。

`navigate` / `observe` / `manipulate` 当前都使用 `executor_mode="simulated_skill"`。

`verify` 当前使用 `executor_mode="simulated_verification"`。

5. `build_default_skill_registry(skill_mode="simulated")` 显式注册 simulated executors。

6. `skill_mode="real"` 时 fail fast：

```text
real VLA/VLN/VLM skill executors are not integrated
```

7. 清掉用户可见的 `mock_skills` 语义。

这些入口使用 `skill_mode`：

```text
homemaster run --skill-mode simulated
run_homemaster_task(skill_mode="simulated")
run_stage_07_scenario_matrix(skill_mode="simulated")
PipelineContext.skill_mode
```

不再使用：

```text
--mock-skills
--no-mock-skills
mock_skills=True
mock_skills=False
```

如果为了兼容短期保留旧参数，旧参数只能作为 deprecated alias 映射到 `skill_mode="simulated"`，并且不能继续出现在 status/debug payload 的 component mode 中。

8. verification 标记为：

```text
simulated_verification
```

### Acceptance Criteria

* AgentRuntime status 显示 `navigate/observe/manipulate=simulated_skill`。
* AgentRuntime status 显示 `verify=simulated_verification`。
* Stage05 compatibility status 如仍存在，也必须显示 `skills=simulated_skill`、`verification=simulated_verification`。
* real skill mode 未接入时不能运行。
* Mimo prompt payload 只暴露当前允许选择的 tools。
* Skill summaries 由 `SkillRegistry` 进入 ContextBuilder；完整 `SKILL.md` 只通过 `get_skill` 进入 `AgentState.loaded_skill_contexts`。
* `fetch_object` 和 `check_object_state` builtin skills 存在，并有 `allowed_tools` / constraints / success criteria。
* `SkillSpec` 不含 executor / ToolResult / AgentState mutation。
* `get_skill` 不绕过 Runtime；它必须产生 ToolResult、RuntimeEvent，并由 StateUpdater 写入 loaded skill context。
* Tool manifest 由 `ToolSpec` 生成，不再手写散落的 tool prompt 字符串。
* Tool descriptions 明确包含 simulated marker、允许输入、状态影响、失败语义。
* simulated executor 位于 `src/homemaster/tools/simulated.py` 并有测试覆盖。
* `src/homemaster/skill_registry.py` 不再承载 active implementation；如存在，只能是 compatibility shim。
* CLI/API/status/debug/README 不再把 robot layer 称为 `mock_skill`。
* `--mock-skills` / `--no-mock-skills` 不再是生产 CLI 的主路径；若保留 alias，必须有 deprecation 测试。
* `ask_user` 不出现在当前 `tool_manifests()`。
* `finish_task` 不出现在当前 `tool_manifests()`。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_skill_registry.py \
  tests/homemaster/test_skill_loader.py \
  tests/homemaster/test_agent_tools.py \
  tests/homemaster/test_executor.py \
  tests/homemaster/test_skill_selector.py \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_task_runner.py \
  tests/homemaster/test_stage_05_debug_assets_do_not_contain_secrets.py
```

新增测试：

* simulated registry 有 navigation / operation executor。
* AgentRuntime tool registry 有 understand_task / retrieve_memory / ground_target / get_skill / navigate / observe / manipulate / verify / update_memory / update_user_profile / finish_task。
* `SkillLoader` 能加载 builtin `fetch_object` / `check_object_state` 的 `SKILL.md`。
* `SkillRegistry` compact summary 不包含完整 body。
* `SkillRegistry` 校验 allowed_tools 中的 tool 必须存在。
* `get_skill` 成功时返回 full skill content；失败时返回 retryable=false 的清晰错误。
* `verify` 是 selectable tool，出现在 `tool_manifests()`，并由 Mimo decision 显式调用。
* `finish_task` 注册为 internal finalizer，`selectable_by_model=False`。
* `to_mimo_manifest()` 不包含 executor callable、runtime-only state effects、secret/debug payload。
* navigate / observe / manipulate / verify 的 description 包含 executor mode 和 state effect 说明。
* `src/homemaster/tools/simulated.py` 定义 simulated executors；`src/homemaster/simulated_skills.py` 不新增为 active implementation。
* real registry fail fast。
* verification 的 selectable 策略固定为 Mimo-selectable，并有测试覆盖。
* 未注册 executor 的 skill 报错清晰。
* `skill_mode="simulated"` 在 status/debug 中输出 `simulated_skill`。
* `skill_mode="real"` 在未集成 executor 时 fail fast。
* `ask_user` 不可被 Mimo 选择。
* `finish_task` 不可被 Mimo 选择，Mimo finish JSON 由 AgentRuntime 处理。

---

## Phase 7. Package Cleanup, Shim Lifecycle, And Ruff Gate

### Priority

P6.

### Intent

迁移完成后收口工程边界，避免旧结构残留继续误导。

本阶段要把 Mini-Agent 的文件组织优点真正落地：目录边界应该让新贡献者不用打开每个文件，就能看出 agent runtime、tool system、providers、events、config、memory、scenarios、compat pipeline 的职责分离。

### Target Files

```text
README.md
tests/homemaster/llm_cases/README.md
src/homemaster/__init__.py
src/homemaster/agent/__init__.py
src/homemaster/tools/__init__.py
src/homemaster/tools/registry.py
src/homemaster/tools/simulated.py
src/homemaster/events/__init__.py
src/homemaster/events/runtime_events.py
src/homemaster/events/sinks.py
src/homemaster/events/sanitizer.py
src/homemaster/config/__init__.py
src/homemaster/config/runtime_settings.py
src/homemaster/providers/__init__.py
src/homemaster/stage_runtime.py
src/homemaster/executor.py
src/homemaster/frontdoor.py
src/homemaster/skill_registry.py
pyproject.toml
scripts/render_screenshots.py
```

### Implementation Tasks

1. 建 shim lifecycle 文档。

内容：

```text
shim path
new import path
compat window
target removal version
```

2. 固化 target package layout。

新增或更新包级文档/`__init__.py` docstring，说明：

```text
agent/      active AgentRuntime implementation
tools/      ToolSpec / registry / dispatcher / simulated executors / get_skill tool
skills/     SkillSpec / SkillLoader / SkillRegistry / builtin SKILL.md packages
memory/     RAG / profile / fact memory / MEMORY.md and USER.md snapshots
events/     RuntimeEvent schema, sinks, sanitizer
config/     run-scoped RuntimeSettings and path/config helpers
providers/  LLM/embedding/Mimo decision provider clients
pipeline/   compatibility layer only
stages/     transitional Stage02-06 handlers only
root/       public facade and documented compatibility shims only
```

3. shim 不再 re-export test doubles。

尤其：

```text
src/homemaster/stage_runtime.py
src/homemaster/executor.py
```

4. README 改成当前真实边界：

```text
AgentRuntime tool loop
live Mimo decisions
simulated robot tools
real robot / VLA / VLN / VLM not integrated
phase_label values are trace/status only
```

5. Ruff cleanup。

要么修绿：

```bash
.venv/bin/python -m ruff check .
```

要么在 `pyproject.toml` 写明确 per-file ignores，并注明 legacy/test/script 原因。

### Acceptance Criteria

* 新贡献者能从文档看出 active implementation vs shim。
* 新贡献者能从目录结构看出 AgentRuntime、tools、events、config、providers、pipeline compatibility 的边界。
* 新 active implementation 不再新增到 `src/homemaster/` root。
* root-level implementation modules 有迁移目标或明确 public facade 理由。
* `ruff check .` 通过，或每个例外都有显式说明。
* README/scripts/debug 文案不再暗示 production deterministic-capable。
* 全量测试通过；live API tests 无 key 时 skip。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check .
find src/homemaster -maxdepth 1 -type f | sort
rg -n "test_double|deterministic_task_card|deterministic_query|deterministic_plan|dummy_provider|StaticScenarioDecisionProvider|StaticStepDecisionProvider|live_step_decision_smoke|--live-models|--no-live-models|live_models|DEFAULT_LIVE_MODELS|mock_symbolic|mock_skill" src/homemaster README.md scripts config
```

`find src/homemaster -maxdepth 1 -type f` 预期：root-level files 只剩 public facade、`__init__.py`、或已记录 lifecycle 的 compatibility shims；不能出现新 active implementation。

Production static gate 预期：

```text
src/homemaster: 无命中
README.md/scripts/config: 无用户可执行 non-live brain 入口
tests: deterministic/static provider 只允许在 tests/homemaster/test_doubles/ 或明确测试断言中出现
```

Tests allowlist gate：

```bash
rg -n "StaticScenarioDecisionProvider|StaticStepDecisionProvider|test_double|deterministic" tests/homemaster \
  --glob '!test_doubles/**' \
  --glob '!**/llm_cases/**'
```

预期：只命中明确断言或 legacy baseline 说明；不能命中从 production package import test-double provider。

如必须保留 legacy 文档或脚本，必须在本计划对应阶段列出具体 allowlist，不允许用模糊 “legacy” 理由跳过整棵目录。

最终真实 API 验收：

```bash
HOMEMASTER_RUN_LIVE_LLM=1 HOMEMASTER_RUN_LIVE_EMBEDDING=1 \
PYTHONPATH=src .venv/bin/python -m pytest -q \
tests/homemaster/test_stage_07_scenarios_live.py -m live_api
```

---

## Phase 8. AgentRuntime Event Trace And CLI Progress

### Priority

P7.

### Intent

补齐 runtime observability。迁移完成后，HomeMaster 运行时必须能输出结构化事件，让开发者看到每一轮 Mimo decision、tool_call、tool_result、state transition，以及兼容 Stage 路径的 run/stage 进度。`phase_label` 只能是事件标签，不能成为固定流程控制。

### Target Files

```text
src/homemaster/events/runtime_events.py
src/homemaster/events/sinks.py
src/homemaster/events/sanitizer.py
src/homemaster/trace.py
src/homemaster/agent/runtime.py
src/homemaster/agent/state.py
src/homemaster/tools/dispatcher.py
src/homemaster/tools/state_updater.py
src/homemaster/pipeline/runner.py
src/homemaster/task_runner.py
src/homemaster/pipeline/adapters.py
src/homemaster/stages/executor.py
src/homemaster/stages/recovery_loop.py
src/homemaster/providers/llm_client.py
src/homemaster/providers/embedding_client.py
src/homemaster/cli/app.py
tests/homemaster/test_runtime_events.py
tests/homemaster/test_task_runner.py
tests/homemaster/test_recovery_loop.py
tests/homemaster/test_cli_run.py
```

### Implementation Tasks

1. 新增 runtime event schema。

建议新增：

```text
src/homemaster/events/runtime_events.py
```

最小事件字段：

```python
RuntimeEvent(
    event_id: str,
    run_id: str,
    event_type: str,
    phase_label: str,
    status: str,
    timestamp: str,
    duration_ms: int | None,
    stage: str | None,
    subtask_id: str | None,
    skill_name: str | None,
    provider_name: str | None,
    attempt: int | None,
    parent_event_id: str | None,
    payload: dict[str, Any],
)
```

所有 payload 必须经过 `sanitize_for_log()`，不能写出 API key、authorization、token、secret。

这里借鉴 Mini-Agent 的 `AgentLogger` 闭环：记录 LLM request / response / tool result。但 HomeMaster 不能照搬其完整明文日志方式，必须改成：

```text
JSONL one event per line
stable event_type
stable run_id / event_id / turn_index
compact payload summary
secret redaction
no raw prompt by default
no raw provider response by default
debug-only expanded payload 也必须先 sanitize
```

2. 新增 event sink。

最小接口：

```python
class RuntimeEventSink(Protocol):
    def emit(self, event: RuntimeEvent) -> None: ...
```

最小实现：

```text
JsonlRuntimeEventSink -> 写 debug_root / "stage_07" / run_id / "trace" / "runtime_events.jsonl"
NullRuntimeEventSink -> 测试或禁用进度时使用
ConsoleProgressEventSink -> CLI --progress 时输出用户可读进度
FanoutRuntimeEventSink -> 同时写 JSONL 和 console
```

这些 sink 放在：

```text
src/homemaster/events/sinks.py
```

脱敏逻辑放在：

```text
src/homemaster/events/sanitizer.py
```

不得新增 root-level `src/homemaster/runtime_events.py` 作为 active implementation。

3. `AgentRuntime` 发出 agent turn 事件。

必须覆盖：

```text
run_started
run_completed
run_failed
turn_started
context_built
decision_started
decision_completed
decision_failed
tool_call_validated
tool_call_rejected
tool_call_started
tool_call_completed
tool_call_failed
state_transitioned
finish_decision_received
max_turns_exceeded
```

每个 turn event 至少包含：

```text
run_id
turn_index
event_type
phase_label
status
tool_name
executor_mode
state_status
failure_record_id
duration_ms
```

4. `PipelineRunner` 兼容层发出 run/stage 事件。

必须覆盖：

```text
run_started
run_completed
run_failed
stage_started
stage_completed
stage_failed
```

每个 stage event 包含：

```text
run_id
stage
component_modes
duration_ms
status
```

5. LLM / embedding 边界发出事件。

事件类型：

```text
llm_call_started
llm_call_completed
llm_call_failed
embedding_call_started
embedding_call_completed
embedding_call_failed
```

事件 payload 只能记录 provider name、model、case name、prompt token/response metadata 等非敏感摘要。不得记录完整 API key 或 authorization header。

6. Stage05 compatibility path 发出内部 act / verify / recover 事件。

必须覆盖：

```text
planning_completed
step_decision_generated
subtask_started
subtask_completed
subtask_failed
skill_call_started
skill_call_completed
skill_call_failed
verification_started
verification_completed
verification_failed
recovery_started
recovery_decision_generated
recovery_completed
recovery_failed
```

每个 Stage05 compatibility event 至少包含：

```text
subtask_id
step_index
skill_name
failure_record_id
recovery_attempt
phase_label
```

字段不存在时用 `null`，不要省略关键字段。

7. CLI 增加可选进度展示。

生产命令支持：

```text
homemaster run --progress
homemaster run --no-progress
```

默认可以保持安静，但 debug artifact 中必须始终写 `runtime_events.jsonl`。

`--progress` 只展示高层事件：

```text
run_started
turn_started
decision_completed
tool_call_started
tool_call_completed
tool_call_failed
state_transitioned
finish_decision_received
run_completed
run_failed
```

如果走 pipeline compatibility path，可以额外展示 `stage_started` / `stage_completed` / `stage_failed`。

不要在 CLI 输出 prompt、raw response、secret、完整 provider payload。

### Acceptance Criteria

* 每次 `run_homemaster_task()` 都写出 `runtime_events.jsonl`。
* JSONL 每行都是一个合法 JSON object，并包含 `run_id`、`event_id`、`event_type`、`phase_label`、`status`、`timestamp`。
* AgentRuntime 记录 turn/context/decision/tool/state transition start/end/failure。
* PipelineRunner compatibility path 记录 run/stage start/end/failure。
* Stage05 compatibility path 记录 planning、step decision、skill、verification、recovery 事件。
* LLM / embedding 调用有 start/completed/failed 事件。
* RuntimeEvent schema/sinks/sanitizer 位于 `src/homemaster/events/`，不是 root-level active module。
* CLI `--progress` 可以显示高层事件，`--no-progress` 不显示进度但仍写 JSONL。
* event payload 不包含 secret。
* debug report 能链接到 runtime event trace 路径。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_runtime_events.py \
  tests/homemaster/test_task_runner.py \
  tests/homemaster/test_recovery_loop.py \
  tests/homemaster/test_cli_run.py
```

新增测试：

* `RuntimeEvent` 序列化后包含必需字段。
* `JsonlRuntimeEventSink` 写出的 JSONL 每行可解析。
* secret-like payload keys 被 redacted。
* raw prompt、raw response、authorization、api_key 不会进入默认 JSONL trace。
* `src/homemaster/events/runtime_events.py`、`src/homemaster/events/sinks.py`、`src/homemaster/events/sanitizer.py` 有直接单元测试覆盖。
* fake AgentRuntime run 产生 turn/context/decision/tool/state transition 事件。
* pipeline compatibility fake run 产生 run/stage start/end 事件。
* Stage05 compatibility recovery 场景产生 `recovery_started` 和 `recovery_decision_generated`。
* CLI `--progress` 输出高层进度，且不包含 prompt/raw_response/API key。

---

## Phase 9. CLI Command Boundary And Error Handling Cleanup

### Priority

P8.

### Intent

把 CLI 从 runtime policy 中解耦。`app.py` 应该负责命令注册和参数转发，不负责重复实现 Stage07 policy；expected runtime failure 和 unexpected internal crash 必须分开处理。这个阶段用于关闭 CLI command boundary / error handling 类问题。

这里可以借鉴 Mini-Agent 的 CLI 工程做法：CLI 展示 session/run 信息、可查看日志目录、命令 help 明确列出可用能力。但 HomeMaster 不能把 CLI 做成 runtime policy 的第二份实现；CLI 只展示 `RuntimeSettings`、`AgentRuntime` 入口、progress/event trace 路径和错误分类。

### Target Files

```text
src/homemaster/cli/app.py
src/homemaster/cli/run_command.py
src/homemaster/cli/errors.py
src/homemaster/cli/doctor.py
src/homemaster/cli/interactive_shell.py
src/homemaster/task_runner.py
tests/homemaster/test_cli_run.py
tests/homemaster/test_cli_help.py
tests/homemaster/test_task_runner.py
README.md
```

### Implementation Tasks

1. 拆出 run command handler。

新增：

```text
src/homemaster/cli/run_command.py
```

职责：

```text
parse already-typed command arguments
call run_homemaster_task()
render successful result
render expected HomeMasterRunError
delegate unexpected exception handling
```

`src/homemaster/cli/app.py` 只保留：

```text
Typer app construction
command registration
sub-app registration
thin callback
```

2. Stage07/runtime policy 下沉到 task runner 或 runtime resolver。

CLI 不再直接判断：

```text
scenario is required
skill_mode real is unavailable
```

这些规则由 `run_homemaster_task()` 或 RuntimeSettings resolver 统一抛出 `HomeMasterRunError` / `RuntimeConfigError`，CLI 只负责渲染。

3. 分离 expected 和 unexpected errors。

新增：

```text
src/homemaster/cli/errors.py
```

行为：

```python
HomeMasterRunError -> run_failed: <message>, exit 1
RuntimeConfigError -> config_failed: <message>, exit 2
typer.BadParameter / usage errors -> exit 2
unexpected Exception -> internal_error: <type>: <message>, exit 1, with traceback logged
```

不要再写：

```python
except (HomeMasterRunError, Exception)
```

4. 更新命令分组和 help text。

目标命令结构：

```text
homemaster run
homemaster doctor
homemaster stage understand
homemaster smoke contract
```

如果为了兼容暂留旧命令：

```text
homemaster understand
homemaster contract-smoke
```

必须在 help text 中标注 deprecated，并在 shim lifecycle 文档中写目标删除版本。

5. 更新 `run` 描述。

`homemaster run` 不再描述为模糊的 “Stage02-Stage06”，而是：

```text
Run one HomeMaster task with AgentRuntime, live Mimo decisions, and simulated robot tools.
```

同时写清：

```text
real VLA / VLN / VLM executors are not integrated
skill_mode=simulated is the supported robot execution mode
phase_label values are progress/trace labels, not a fixed PLAN/ACT/VERIFY/RECOVER flow
```

6. 增加 examples/docs 作为 CLI contract。

Mini-Agent 的 `examples/` 和 development guide 让新开发者知道如何扩展 tool、配置模型、查看日志。HomeMaster 应该补齐同等层级的最小示例：

```text
README.md:
  homemaster run 的 live Mimo + simulated robot tools 示例
  runtime_events.jsonl 路径说明
  skill_mode=simulated / real not_integrated 说明

docs 或 plan/V1.3 follow-up:
  如何新增一个 AgentRuntime tool
  ToolSpec 字段约束
  EventSink 和 RuntimeSettings 扩展点

scripts:
  普通脚本默认写 var/homemaster/debug
  不写 tracked fixture，除非显式 refresh baseline
```

### Acceptance Criteria

* `app.py` 不再直接包含 Stage07 policy 判断。
* `app.py` 不再用 catch-all 方式吞掉 expected 和 unexpected errors。
* `run` help text 准确描述 AgentRuntime + live Mimo decisions + simulated robot tools。
* `stage` / `smoke` 子命令存在，或旧命令有明确 deprecation 文档。
* `HomeMasterRunError`、`RuntimeConfigError`、unexpected exception 的 CLI 输出和 exit code 可区分。
* `task_runner.py` 是 run policy 的唯一来源之一；CLI 不重复实现同一规则。
* README 或 docs 有最小 AgentRuntime run 示例、event trace 查看方式、simulated robot boundary。
* CLI 输出包含 debug/event trace 路径，但不输出 raw prompt/raw response/secret。

### Test Plan

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/homemaster/test_cli_run.py \
  tests/homemaster/test_cli_help.py \
  tests/homemaster/test_task_runner.py
```

新增测试：

* `homemaster run --help` 包含 AgentRuntime、live Mimo decisions、simulated robot tools。
* `homemaster stage --help` 包含 `understand`。
* `homemaster smoke --help` 包含 `contract`。
* missing scenario 由 task runner 或 runtime resolver 转成 expected runtime/config error，不由 CLI 手写重复 policy。
* monkeypatch `run_homemaster_task()` 抛 `HomeMasterRunError`，CLI 输出 `run_failed`。
* monkeypatch `run_homemaster_task()` 抛 `RuntimeConfigError`，CLI 输出 `config_failed`。
* monkeypatch `run_homemaster_task()` 抛 `RuntimeError`，CLI 输出 `internal_error`，并且不伪装成 expected runtime failure。
* README 示例命令不包含 `--no-live-models`、`--mock-skills` 或 tracked fixture 输出路径。
* CLI success output 包含 run_id 和 runtime event trace 路径。

---

## Final Done Criteria

整个计划完成时必须同时满足：

```text
1. src/homemaster 不存在生产 deterministic runtime branch
2. CLI 不支持 --no-live-models
3. RuntimeMode 不支持 test_double
4. 存在 AgentRuntime.run() 主循环，且不是固定 PLAN/ACT/VERIFY/RECOVER pipeline
5. Mimo 每轮通过结构化 decision 选择 tool_call 或 finish
6. AgentState 是运行时唯一核心状态，tool result / failure 都写回 AgentState
7. AgentRuntime 当前版本不暴露 ask_user tool，也没有 ask_user / needs_user_input 行为；legacy pipeline compatibility 如暂留 ask_user，必须标注 legacy_compat_only 且不能作为默认入口
8. 当前版本不把 finish_task 暴露给 Mimo；终止只能走 FinishDecision
9. verify 是 Mimo-selectable tool，不由 Runtime 自动插入固定 ACT -> VERIFY 流程
10. AgentState 中 phase_label 仅用于 trace/status，不能驱动 runtime 分支
11. run_homemaster_task 默认入口是 AgentRuntime.run；PipelineRunner 只能显式 compatibility helper/flag 进入
12. Stage05 static actual decision 被移除；如果 LiveStepDecisionProvider 仍在 compatibility path，必须标注 transitional
13. model_boundary 不输出 deterministic
14. mock skills 被更名/标注为 simulated skills / simulated verification
15. tests 中 static providers 位于 tests/homemaster/test_doubles
16. normal tests 不修改 tracked fixture
17. run_id path traversal 被拒绝
18. full pytest 通过
19. tests/homemaster/test_stage_07_scenarios_live.py -m "not live_api" 广泛场景矩阵通过
20. ruff check 通过或例外显式配置
21. run_homemaster_task / scenario_runner / PipelineContext 不提供可进入 non-live brain 的生产参数
22. config/example/docs/scripts 中没有 live_models / DEFAULT_LIVE_MODELS / --no-live-models 的生产入口
23. src/homemaster/runtime.py 不在 import 时读取用户 runtime config
24. src/homemaster/stage_runtime.py / executor.py root shim 不用 import * 重新导出 static/test-double providers
25. StaticScenarioDecisionProvider / StaticStepDecisionProvider 不从 src/homemaster 定义或导出
26. MimoDecisionClient 有 fake/live 边界；live client 不 deterministic fallback
27. AgentRuntime decision/tool wiring 有离线 fake Mimo 测试覆盖，不依赖真实 API 才能验收
28. ToolSpec 由 first-class tool contract 生成 compact Mimo manifest，且 runtime-only metadata 不默认进入 prompt
29. ToolSpec 包含 executor reference，但 executor 只能由 Dispatcher 调用，不能由 ToolSpec 自执行
30. ContextBuilder 使用 AgentState 生成 compact context，不以完整 message history 作为主状态
31. ContextBuilder 使用 stable_context / task_state_context / recent_dynamics_context 三层上下文
32. MEMORY.md / USER.md 是模型可读快照；结构化 memory/profile 仍是 source of truth
33. memory/context_snapshot.py 定义 snapshot 输入、source version、content hash、stale detection、commit 后原子刷新
34. update_memory / update_user_profile 是长期记忆和用户偏好更新 proposal 的唯一模型入口
35. ToolResult 是结构化对象，不包含 state_patch；StateUpdater 是唯一 AgentState writer
36. SkillSpec / SkillLoader / SkillRegistry 按 Mini-Agent progressive disclosure 实现，但 SkillSpec 不含 executor / ToolResult / AgentState mutation
37. get_skill 是普通 runtime tool，经过 Dispatcher / StateUpdater / EventSink
38. runtime_events.jsonl 覆盖 run/turn/decision/tool_call/tool_result/state transition/LLM/compat Stage05 关键事件，且 payload 不泄露 secret
39. CLI command boundary 清晰：app.py 薄入口、runtime policy 不重复、expected/unexpected error 输出可区分
40. README/docs/examples 说明如何运行 AgentRuntime、查看 event trace、理解 simulated robot boundary、扩展 tool 和 skill
41. 新 active implementation 遵守 target package layout；root-level 只保留 public facade 或有 lifecycle 的 compatibility shim
42. src/homemaster 不 import tests/test_doubles，compat shims 不用 import * 重新导出 test doubles
```
