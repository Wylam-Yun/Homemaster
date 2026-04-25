# HomeMaster V1.2 Pipeline-First 任务总控大脑执行计划

目标文档路径：`/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/2026-04-24-llm-first-task-brain-v2-execution-plan-zh.md`

## 1. Summary

V1.2 的目标是在 HomeMaster 仓库内建立一条新的 LLM-first 任务总控大脑主链。它不是继续深改旧 `task_brain` 的规则主链，而是新建独立的 `homemaster` 包，用更贴近最终系统的 pipeline 方式完成家庭养老场景里的“找物 + 简单操作”任务编排。

工程命名统一为：

- 系统名：`HomeMaster`
- Python 包：`src/homemaster/`
- CLI：`homemaster`
- 测试目录：`tests/homemaster/`
- 场景脚本：`scripts/run_homemaster_scenarios.sh`
- 测试结果目录：`plan/V1.2/test_results/`
- 阶段结论报告目录：`record/`

文档里可以把任务总控大脑称为 HomeBrain，但代码包、CLI、脚本和测试目录统一使用 `homemaster`，避免出现临时版本号式命名。

核心流程：

```text
用户输入
  -> 任务理解
  -> object_memory 检索
  -> 程序生成真实候选池
  -> 模型候选选择
  -> 高层模块编排
  -> 决策 / 执行 / 验证 / 恢复闭环
  -> 具身操作接口
  -> 任务总结
  -> 证据把关后的记忆写回
```

首版以 LLM 测试为主，先证明任务脑的理解、选择、编排、一步决策和恢复能力。VLM 图片接口要提前留好，但默认禁用；当前验证先用结构化 observation / mock observation。具身动作规划器接口也要留好，但本阶段先由 HomeMaster 大脑临时兼任，方便测试闭环，不提前拆出真正独立的操作规划智能体。

阶段测试默认使用 `config/nvidia_api_config.json` 中的 Mimo 配置：

```text
provider name: Mimo
model: mimo-v2-pro
protocol: anthropic
```

如果 Mimo 不可用，阶段验收不能悄悄换模型并当作同一结果；必须在测试日志和阶段报告中写清楚使用了哪个 provider、哪个 model、失败原因和是否重跑。

## 2. 当前项目基线

当前仓库已有 V1 主链：

- 包路径：`src/task_brain/`
- CLI：`task-brain`
- 场景数据：`data/scenarios/`
- mock 能力：`MockVLNAdapter`、`MockPerceptionAdapter`、`FakeRoboBrainClient`、`MockAtomicExecutor`
- 关键模块：`parser.py`、`memory.py`、`planner.py`、`graph.py`、`verification.py`、`recovery.py`

V1 的价值是提供 fixture、mock world、adapter 经验、memory schema 经验、verification/memory reconciliation 经验和对照测试。HomeMaster V1.2 可以读取同一批场景数据，也可以复用底层 mock adapter 的行为语义，但新的主链不直接依赖旧 parser、旧 deterministic planner、旧 recovery tree 或旧 graph。

需要保留的旧链隔离原则：

- `homemaster` 不导入 `task_brain.parser`
- `homemaster` 不导入 `task_brain.planner`
- `homemaster` 不导入 `task_brain.recovery`
- `homemaster` 不导入 `task_brain.graph`
- 如果确实需要某个底层工具能力，优先通过新 adapter wrapper 或复制小型纯工具函数，而不是把旧主链拉进来

## 3. 设计原则

### 3.1 Pipeline-first

HomeMaster V1.2 的主设计单位是 pipeline stage 和执行期闭环，不是固定业务模板。每个阶段都要说明输入、输出、模型职责、程序职责、测试方法和失败处理方式。

### 3.2 LLM-first，但程序守住落地边界

LLM 负责：

- 任务理解
- object memory 检索意图
- 候选选择
- 高层模块编排
- 执行期一步决策
- 失败恢复判断
- 任务总结

程序负责：

- 加载场景、object memory 和 runtime state
- 生成真实候选池
- 校验结构化输出
- 校验候选、观察点、锚点和模块调用是否存在
- 调用 mock VLN / observation / VLA 接口
- 记录 trace
- 根据验证证据推进状态
- 对长期记忆写回做证据把关

### 3.3 首版只检索一种记忆

为保证正确率，V1.2 首版只检索 `object_memory`。先不把 `category_prior_memory`、`episodic_memory`、用户习惯记忆和复杂相对关系记忆放进主链。

原因：

- 减少 LLM 上下文噪声。
- 让候选来源可解释、可测试。
- 避免 category prior 和 episodic hint 过早影响正确性。
- 先把“目标物历史位置 -> 真实候选 -> 模型选择 -> 验证恢复”跑稳。

预留扩展点：

- `category_prior_memory` 可在后续阶段作为 object memory 缺失时的兜底候选来源。
- `episodic_memory` 可在后续阶段只作为排序解释或轻量加权，不直接生成候选。

### 3.4 验证是推进硬门槛

模块返回成功不等于任务完成。每一步推进都必须经过验证结果支持：

- step 是否完成，需要 verification result。
- subtask 是否完成，需要 verification result。
- task 是否完成，需要 final verification result。
- memory 是否写回，需要 verified evidence。

首版 verification 使用结构化 observation / mock observation。VLM 图片接口在数据结构中预留，但默认 `enabled=false`，避免当前阶段依赖真实图片理解。

### 3.5 最小模块调用型子任务

高层编排只保留少量模块调用型子任务：

```text
navigate
observe_verify
embodied_operate
ask_user
finish
```

业务细节放在目标、参数和成功标准里，不再把“前往候选点、观察区域、确认目标、抓取、放下、回到用户”写成固定子任务 enum。

## 4. 公共接口

接口字段保持简洁。字段本身表达能力边界，不在每个字段下面重复写显而易见的禁止项。工程校验统一放在第 5 节和阶段测试里。

### 4.1 TaskCard

职责：描述用户要完成什么。

建议字段：

- `task_type`
- `target`
- `delivery_target`
- `location_hint`
- `success_criteria`
- `needs_clarification`
- `clarification_question`
- `confidence`

说明：

- `task_type` 首版覆盖 `check_presence`、`fetch_object`、`unknown`。
- `location_hint` 只表达用户提示，例如“厨房”“桌子那边”，不等于真实候选点。

### 4.2 ObjectMemorySearchPlan

职责：描述本轮如何从 `object_memory` 里检索。

建议字段：

- `target_category`
- `target_aliases`
- `location_hint`
- `excluded_location_keys`
- `ranking_policy`
- `reason`

检索方式：

- 先按 `target_category` 精确匹配。
- 再用 `target_aliases` 做别名命中加权。
- 如果用户给了 `location_hint`，对 room、anchor display text、anchor type 命中的记录加权。
- 按 `confidence_level`、`belief_state`、`last_confirmed_at` 排序。
- 当前任务内已经验证未找到的位置通过 `excluded_location_keys` 排除。
- 首版不读取 category prior，不读取 episodic memory。

### 4.3 ObjectMemoryEvidence

职责：承载从 `object_memory` 检索出的证据。

建议字段：

- `hits`
- `excluded`
- `ranking_reasons`
- `retrieval_summary`

说明：

- `hits` 只包含 object memory 记录的精简信息：memory id、目标类别、锚点、观察点、置信度、确认时间、排序原因。
- `excluded` 记录因为 task negative evidence 被排除的位置或 memory id。

### 4.4 CandidatePool

职责：程序把 object memory 证据转成真实可落地候选。

建议字段：

- `candidates`
- `generation_summary`

单个 candidate 建议字段：

- `candidate_id`
- `memory_id`
- `room_id`
- `anchor_id`
- `viewpoint_id`
- `display_text`
- `evidence`

说明：

- 候选池由程序生成。
- 候选必须能在当前 scenario / world 数据里找到 anchor 和 viewpoint。
- 如果 object memory 的 viewpoint 缺失或无效，该记录进入 `generation_summary.invalid_items`。

### 4.5 CandidateSelection

职责：模型从候选池中选择当前优先目标。

建议字段：

- `selected_candidate_id`
- `ranked_candidate_ids`
- `reason`
- `need_retrieve_again`

说明：

- 选择结果必须能通过程序校验。
- `need_retrieve_again=true` 时进入恢复/重检索路径，而不是让模型临时编造新地点。

### 4.6 OrchestrationPlan

职责：高层模块编排。

建议字段：

- `goal`
- `selected_candidate_id`
- `subtasks`
- `confidence`

单个 subtask 建议字段：

- `id`
- `type`
- `target`
- `success_criteria`
- `depends_on`

说明：

- `type` 只使用 `navigate`、`observe_verify`、`embodied_operate`、`ask_user`、`finish`。
- 查看类任务通常是 `navigate -> observe_verify -> finish`。
- 取物类任务通常是 `navigate -> observe_verify -> embodied_operate -> observe_verify -> finish`。

### 4.7 StepDecision

职责：执行期每轮只决定下一步。

建议字段：

- `subtask_id`
- `module`
- `module_input`
- `expected_result`
- `verify_after`
- `reason`

说明：

- `module` 首版覆盖 `navigate`、`observe`、`verify`、`operate`、`ask_user`、`finish`。
- `module_input` 由程序校验候选、viewpoint、memory id 和 object ref。

### 4.8 ModuleExecutionResult

职责：记录模块调用结果。

建议字段：

- `module`
- `status`
- `observation`
- `runtime_state_delta`
- `evidence`
- `error`

说明：

- `observation` 使用结构化 observation。
- 预留 `image_input` 字段，但首版配置为 disabled。

### 4.9 VerificationResult

职责：判断当前证据是否足够支持推进。

建议字段：

- `scope`
- `passed`
- `verified_facts`
- `missing_evidence`
- `failed_reason`
- `confidence`

说明：

- `scope` 覆盖 `step`、`subtask`、`task`。
- 首版由 mock/symbolic verifier 生成；后续可替换为真实 VLM verifier。

### 4.10 RecoveryDecision

职责：失败、证据不足或状态异常后，决定如何回到主循环。

建议字段：

- `action`
- `reason`
- `next_candidate_id`
- `should_retrieve_again`
- `should_replan`
- `ask_user_question`

说明：

- `action` 覆盖 `retry_step`、`reobserve`、`switch_candidate`、`retrieve_again`、`replan`、`ask_user`、`finish_failed`。

### 4.11 EmbodiedActionPlan

职责：VLA 调用前的操作上下文接口。

建议字段：

- `operation_goal`
- `target`
- `observation`
- `constraints`
- `success_criteria`
- `vla_instruction`

说明：

- 本阶段不实现独立具身规划器智能体。
- 当进入 `embodied_operate` 时，由 HomeMaster 大脑生成该结构，作为 VLA adapter 的输入。
- 代码上仍保留 `embodied_planner.py` 或同名接口，方便后续把这部分替换为独立具身动作规划器。

### 4.12 TaskSummary

职责：总结任务结果和用户回复。

建议字段：

- `result`
- `confirmed_facts`
- `unconfirmed_facts`
- `recovery_attempts`
- `user_reply`

### 4.13 MemoryCommitPlan

职责：把 verified evidence 转成可写回记忆的计划。

建议字段：

- `object_memory_updates`
- `negative_evidence`
- `task_record_note`
- `skipped`

说明：

- 首版只允许更新已有 object memory 的确认时间、置信度、stale/contradicted 状态。
- 新建 object memory 可以后置，避免本阶段误写新长期记忆。

### 4.14 VLMImageInput

职责：预留真实 VLM 图片接口。

建议字段：

- `enabled`
- `image_ref`
- `camera`
- `timestamp`
- `metadata`

说明：

- 首版 `enabled=false`。
- mock/symbolic observation 是默认验证输入。
- 后续接真实 VLM 时，不需要重写任务脑 pipeline，只替换 observation/verifier adapter。

## 5. 工程校验原则

不要把显而易见的限制重复写进每个接口下面。统一工程校验如下：

- 模型输出必须是结构化结果。
- 程序校验 schema、enum、候选 ID、viewpoint、memory id 和 module name。
- 程序生成真实候选池，模型只在候选池内选择。
- 高层编排不包含 VLA atomic action。
- StepDecision 每次只决策一步。
- 模块成功不等于任务成功，推进必须看 VerificationResult。
- 记忆写回必须基于 verified evidence。
- 模型阶段失败时只重试本阶段一次；仍失败则追问用户或安全失败。
- 不回退旧 parser、旧 deterministic planner、旧 recovery tree。

## 6. Pipeline 详细流程

### 6.1 任务理解

输入：

- 用户自然语言指令
- 用户 ID / source
- 可选最近任务摘要

处理：

- LLM 生成 `TaskCard`。
- 程序校验任务类型、目标、成功标准和澄清状态。
- 如果需要澄清，直接进入 `ask_user`。

测试：

- “去桌子那边看看药盒是不是还在”生成 `check_presence`。
- “去厨房找水杯，然后拿给我”生成 `fetch_object`。
- 模糊指令生成 `needs_clarification=true`。
- 带位置提示时，位置进入 `location_hint`。

### 6.2 object_memory 检索

输入：

- `TaskCard`
- runtime negative evidence
- object memory 文件

处理：

- LLM 生成 `ObjectMemorySearchPlan`。
- 程序只检索 `object_memory`。
- 程序输出 `ObjectMemoryEvidence`。

排序建议：

```text
score =
  category_match
  + alias_match
  + location_hint_match
  + confidence_score
  + recency_score
  - stale_or_contradicted_penalty
  - task_negative_evidence_exclusion
```

测试：

- 水杯任务不返回药盒 object memory。
- 已排除 location 不进入 hits。
- stale 记录可保留但排序降低，并在 reason 里说明。
- 不读取 category prior 和 episodic memory。

### 6.3 候选池生成和选择

输入：

- `ObjectMemoryEvidence`
- mock world / scenario world

处理：

- 程序把 object memory anchor 转成 candidate。
- 程序校验 anchor、room、viewpoint 是否存在。
- LLM 生成 `CandidateSelection`。
- 程序校验 selection 是否来自 `CandidatePool`。

测试：

- 候选都能在 scenario 中找到真实 viewpoint。
- 缺失 viewpoint 的 memory 不进入可执行候选。
- LLM 编造 candidate id 时，本阶段重试一次。

### 6.4 高层模块编排

输入：

- `TaskCard`
- `CandidateSelection`
- `CandidatePool`

处理：

- LLM 生成 `OrchestrationPlan`。
- 程序校验 subtask type、依赖关系和 success criteria。

测试：

- 查看类不进入 `embodied_operate`。
- 取物类必须先观察验证，再进入 `embodied_operate`。
- 高层编排不出现 `close_gripper`、`open_gripper`、`move_arm_to_pregrasp` 等 atomic action。

### 6.5 决策 / 执行 / 验证 / 恢复闭环

主循环：

```text
while not finished:
  LLM -> StepDecision
  program -> validate StepDecision
  program -> call module
  program -> ModuleExecutionResult
  verifier -> VerificationResult
  if verification passed:
    program -> update runtime progress
  else:
    LLM -> RecoveryDecision
    program -> apply recovery action
```

测试：

- trace 顺序稳定。
- 每轮都有 StepDecision。
- 每步推进前都有 VerificationResult。
- 工具成功但验证失败时不能推进。

### 6.6 恢复

恢复动作：

- retry current step
- reobserve
- switch candidate
- retrieve object_memory again
- replan orchestration
- ask user
- finish failed

测试：

- `check_medicine_stale_recover`：候选切换。
- `object_not_found`：候选耗尽后失败。
- `distractor_rejected`：看到干扰物但不误判目标。
- 恢复动作来自 LLM 结构化输出，不来自旧 recovery tree。

### 6.7 具身操作接口

当前阶段策略：

- 不单独实现独立具身规划智能体。
- `embodied_operate` 由 HomeMaster 大脑生成 `EmbodiedActionPlan`。
- `EmbodiedActionPlan` 作为 VLA adapter 输入。
- 后续如果接独立具身动作规划器，只替换该接口实现。

测试：

- 目标未验证前不进入 `embodied_operate`。
- `fetch_cup_retry` 覆盖操作失败、验证失败、恢复重试。
- 具身操作结果仍需 VerificationResult。

### 6.8 总结和记忆写回

处理：

- LLM 生成 `TaskSummary`。
- 程序生成 `MemoryCommitPlan`。
- 程序只写 verified object memory update。

测试：

- 成功查看后更新 confirmed/stale 状态。
- 找不到时记录 task negative evidence，不猜测新位置。
- 干扰物不写成目标物。
- summary 与 trace 一致。

## 7. 实施阶段与验收

### 7.0 阶段验收硬口径

每个阶段都有两类测试：

- 辅助测试：schema、validator、fake provider、规则校验、mock adapter、单元测试。
- 阶段通过测试：真实调用 `config/nvidia_api_config.json` 中的 `Mimo / mimo-v2-pro`，并得到结构化输出，通过本阶段程序校验。

阶段验收只能以后者为准。规则通过、fake provider 通过、schema 单测通过，都只能说明工程辅助能力可用，不能单独判定该阶段完成。

每轮 LLM 测试都必须保存 debug 资产到 `tests/` 下，方便复现和排查。固定根目录为：

```text
/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/
```

每轮 case 目录格式：

```text
tests/homemaster/llm_cases/stage_xx/<case_name>/
  input.json
  expected.json
  actual.json
  result.md
```

命名要求：

- `stage_xx` 对应阶段编号。
- `<case_name>` 使用英文短名，例如 `check_medicine_task_card`。
- `input.json` 保存实际发给阶段入口的测试输入，不保存 API key。
- `expected.json` 保存关键预期字段和通过条件。
- `actual.json` 保存 LLM 原始结构化输出或裁剪后的完整输出。
- `result.md` 保存通过/失败结论、失败原因、日志位置和调试备注。

如果同一 case 重跑，使用子目录后缀区分，例如：

```text
tests/homemaster/llm_cases/stage_02/check_medicine_task_card/
tests/homemaster/llm_cases/stage_02/check_medicine_task_card_rerun_01/
```

原始长日志、trace JSONL、pytest 输出仍放在 `plan/V1.2/test_results/stage_xx/`；`tests/homemaster/llm_cases/` 只放便于 debug 的精炼输入、预期和实际结果。

### 阶段 1：HomeMaster 包、CLI 与契约层

实现：

- 新建 `src/homemaster/`
- 新建 `contracts.py`
- 新建 `runtime.py`
- 新建 `trace.py`
- 新建 `pipeline.py`
- 新建 `cli.py`
- 在 `pyproject.toml` 增加脚本：`homemaster = "homemaster.cli:app"`
- 新建 `tests/homemaster/`

测试：

- `import homemaster` 成功。
- `homemaster --help` 可运行。
- contract 可序列化。
- V1/V1.2 import boundary 测试：不导入旧 parser/planner/recovery/graph。
- LLM smoke case：调用 Mimo 生成最小 `TaskCard`，证明 provider 配置、结构化输出和本阶段 contract 校验链路可用。

测试样例：

- case：`stage_01_llm_contract_smoke`
- 输入：`{"utterance": "去桌子那边看看药盒是不是还在。"}`
- 预期：Mimo 返回合法 JSON，至少包含 `task_type`、`target`、`success_criteria`，并能被 `TaskCard` contract 校验通过。
- debug 资产：`tests/homemaster/llm_cases/stage_01/stage_01_llm_contract_smoke/`

验收标准：

- `import homemaster`、CLI、contract 单测通过。
- Mimo 真实调用成功。
- Mimo 输出通过 `TaskCard` contract 校验。
- 只有前 3 项同时满足，阶段 1 才算通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_01/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-01-homemaster-contract.md`

### 阶段 2：LLM 任务理解

实现：

- 新建 `frontdoor.py`
- 定义 `TaskUnderstandingProvider`
- 支持 fake provider 和真实 LLM provider 两条路径。
- CI 单测使用 fake provider；阶段验收主要跑真实 LLM，并把日志写到 test result。

测试：

- 查看药盒。
- 取水杯。
- 模糊指令。
- 带位置提示。
- 非法 LLM 输出重试一次后安全失败或追问。

测试样例：

- case：`check_medicine_task_card`
  - 输入：`去桌子那边看看药盒是不是还在。`
  - 预期：`task_type=check_presence`，target 指向 `medicine_box/药盒`，`needs_clarification=false`。
- case：`fetch_cup_task_card`
  - 输入：`去厨房找水杯，然后拿给我`
  - 预期：`task_type=fetch_object`，target 指向 `cup/水杯`，`delivery_target=user`。
- case：`ambiguous_task_card`
  - 输入：`帮我看看那个还在不在`
  - 预期：`needs_clarification=true`，给出澄清问题。
- case：`kitchen_hint_task_card`
  - 输入：`去厨房看看水杯还在不在`
  - 预期：`location_hint` 包含厨房。
- debug 资产：`tests/homemaster/llm_cases/stage_02/<case_name>/`

验收标准：

- 上述 4 个 case 必须真实调用 Mimo。
- Mimo 输出都能通过 `TaskCard` 校验。
- 关键预期字段与 `expected.json` 一致。
- fake provider 或规则 parser 通过不计入阶段通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_02/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-02-task-card.md`

### 阶段 3：object_memory-only 检索

实现：

- 新建 `memory_search.py`
- 只读取 scenario 的 `memory.json.object_memory`
- 输出 `ObjectMemoryEvidence`
- category prior / episodic 暂不进入主链

测试：

- category / alias 匹配。
- location hint 加权。
- confidence / recency 排序。
- stale 降权。
- negative evidence 排除。
- 确认未读取 category prior 和 episodic memory。

测试样例：

- case：`medicine_object_memory_search`
  - 输入：`check_medicine_success` 的 `memory.json.object_memory` + `TaskCard(target=medicine_box, location_hint=桌子那边)`
  - 预期：返回 medicine_box object memory，不返回 cup；排序理由包含 category match。
- case：`cup_object_memory_search`
  - 输入：`fetch_cup_retry` 的 `memory.json.object_memory` + `TaskCard(target=cup, location_hint=厨房)`
  - 预期：`mem-cup-1` 排在前面，理由包含厨房或高置信度。
- case：`negative_evidence_excludes_location`
  - 输入：`object_not_found` 的 object memory + 当前任务 negative evidence 标记 `mem-cup-1` 位置已找过
  - 预期：`mem-cup-1` 进入 excluded，不进入 hits。
- debug 资产：`tests/homemaster/llm_cases/stage_03/<case_name>/`

验收标准：

- LLM 必须真实生成 `ObjectMemorySearchPlan`。
- 程序按该 plan 只检索 `object_memory`。
- 每个 case 的 actual 输出与 expected 关键字段一致。
- 如果只靠规则检索得到正确结果，但没有 Mimo 的 `ObjectMemorySearchPlan`，阶段不算通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_03/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-03-object-memory-search.md`

### 阶段 4：候选池与候选选择

实现：

- 新建 `candidate_pool.py`
- 新建 `candidate_selector.py`
- 候选由程序从 object memory + world 生成。
- LLM 只选择候选。

测试：

- 候选真实存在。
- viewpoint 可导航。
- LLM 编造候选 id 被拒绝。
- 候选选择理由可追踪。

测试样例：

- case：`select_medicine_candidate`
  - 输入：`check_medicine_success` 的 object memory hits + world
  - 预期：程序生成真实 candidate；Mimo 从候选池选择一个已有 candidate；不能新增 candidate。
- case：`select_cup_candidate`
  - 输入：`fetch_cup_retry` 的 object memory hits + world
  - 预期：优先选择厨房水杯候选，`selected_candidate_id` 存在于 CandidatePool。
- case：`reject_fake_candidate_id`
  - 输入：候选池只有 `candidate_cup_1`，模拟 LLM 输出不存在的 `candidate_fake`
  - 预期：程序拒绝并触发本阶段重试或安全失败。
- debug 资产：`tests/homemaster/llm_cases/stage_04/<case_name>/`

验收标准：

- CandidatePool 由程序真实生成。
- Mimo 真实输出 `CandidateSelection`。
- `selected_candidate_id` 校验通过。
- 规则直接选 top candidate 不算阶段通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_04/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-04-candidate-selection.md`

### 阶段 5：高层模块编排

实现：

- 新建 `orchestrator.py`
- 新建 `orchestration_validator.py`
- 支持最小 subtask enum。
- 计划由 LLM 生成，程序校验。

测试：

- 查看类计划。
- 取物类计划。
- forbidden atomic action。
- success criteria 覆盖。
- 依赖关系合法。

测试样例：

- case：`check_medicine_orchestration`
  - 输入：查看药盒 TaskCard + selected candidate
  - 预期：计划包含 `navigate`、`observe_verify`、`finish`，不包含 `embodied_operate`。
- case：`fetch_cup_orchestration`
  - 输入：取水杯 TaskCard + selected candidate
  - 预期：计划包含 `navigate`、`observe_verify`、`embodied_operate`、后置验证和 `finish`。
- case：`forbid_atomic_action_in_plan`
  - 输入：构造包含 `close_gripper` 的 LLM 输出
  - 预期：validator 拒绝该计划。
- debug 资产：`tests/homemaster/llm_cases/stage_05/<case_name>/`

验收标准：

- 查看类和取物类计划都必须来自 Mimo 实际输出。
- Mimo 输出通过 `OrchestrationPlan` 校验。
- success criteria 和依赖关系满足 expected。
- forbidden atomic action 的拒绝是辅助测试；不能替代真实 Mimo 计划通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_05/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-05-orchestration.md`

### 阶段 6：执行期闭环

实现：

- 新建 `policy.py`
- 新建 `executor.py`
- 新建 `verifier.py`
- 先用 mock/symbolic observation 验证。
- 预留 `VLMImageInput`，默认 disabled。

测试：

- `check_medicine_success`
- trace 包含 step decision、module call、execution result、verification result。
- 工具成功但验证失败不能推进。
- 图片接口 disabled 时不影响运行。

测试样例：

- case：`check_medicine_execution_loop`
  - 输入：`check_medicine_success` 场景 + 查看药盒指令
  - 预期：至少完成一次 Mimo `StepDecision`，调用 navigate/observe/verify，最终 task verification 通过。
- case：`tool_success_verification_fail`
  - 输入：构造 module status success 但 observation 不含目标物
  - 预期：不能推进到 finish，必须进入 recovery。
- case：`vlm_image_disabled_execution`
  - 输入：传入 `VLMImageInput(enabled=false)` 和结构化 observation
  - 预期：不调用真实 VLM 图片接口，仍使用结构化验证。
- debug 资产：`tests/homemaster/llm_cases/stage_06/<case_name>/`

验收标准：

- 主成功路径必须至少包含一次真实 Mimo `StepDecision`。
- StepDecision 输出通过程序校验并被实际执行。
- trace 中能看到 Mimo 决策、模块执行、验证结果。
- 只用规则 executor 跑通场景不算阶段通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_06/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-06-execution-loop.md`

### 阶段 7：恢复与重规划

实现：

- 新建 `recovery.py`
- LLM 生成 `RecoveryDecision`
- 程序应用候选切换、重观察、重检索、重编排、追问或失败结束。

测试：

- `check_medicine_stale_recover`
- `object_not_found`
- `distractor_rejected`
- 恢复后 trace 可还原原因和动作。

测试样例：

- case：`stale_medicine_recovery`
  - 输入：`check_medicine_stale_recover` 场景
  - 预期：第一次候选验证失败后，Mimo 输出 `RecoveryDecision(action=switch_candidate)` 或等价候选切换动作。
- case：`object_not_found_recovery`
  - 输入：`object_not_found` 场景
  - 预期：候选耗尽后 Mimo 输出失败结束或追问，不伪造成功。
- case：`distractor_rejected_recovery`
  - 输入：`distractor_rejected` 场景
  - 预期：看到非目标物后不进入成功或错误具身操作。
- debug 资产：`tests/homemaster/llm_cases/stage_07/<case_name>/`

验收标准：

- 每个恢复 case 都必须真实调用 Mimo 生成 `RecoveryDecision`。
- RecoveryDecision 通过程序校验并驱动后续状态变化。
- 旧 recovery tree 或规则 switch candidate 不能单独判定通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_07/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-07-recovery.md`

### 阶段 8：具身操作接口

实现：

- 新建 `embodied_planner.py`
- 当前由 HomeMaster 大脑生成 `EmbodiedActionPlan`
- 调用 mock VLA adapter / existing mock executor wrapper
- 保留后续替换为独立具身规划器的接口

测试：

- `fetch_cup_retry`
- 未验证目标前不操作。
- 操作失败后验证再恢复。
- VLA 成功但验证失败不能结束任务。

测试样例：

- case：`fetch_cup_embodied_interface`
  - 输入：`fetch_cup_retry` 场景 + 已通过目标观察验证的 runtime state
  - 预期：Mimo 生成合法 `EmbodiedActionPlan`，作为 mock VLA adapter 输入。
- case：`no_operate_before_verify`
  - 输入：未完成目标验证的 fetch task
  - 预期：不能进入 `embodied_operate`。
- case：`operate_success_verify_fail`
  - 输入：mock VLA 成功但 verification failed
  - 预期：任务不能 finish，进入 recovery。
- debug 资产：`tests/homemaster/llm_cases/stage_08/<case_name>/`

验收标准：

- `EmbodiedActionPlan` 必须由 Mimo 或 HomeMaster 大脑 LLM 调用生成。
- 计划通过接口校验并实际传入 mock VLA adapter。
- 只用固定操作模板通过不算阶段通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_08/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-08-embodied-interface.md`

### 阶段 9：总结与记忆写回

实现：

- 新建 `summary.py`
- 新建 `memory_commit.py`
- 只更新 verified object memory。
- 失败只记录可靠 negative evidence 和 summary。

测试：

- 成功更新 object memory confirmed 信息。
- 失败不写猜测位置。
- 干扰物不写成目标物。
- summary 与 trace 一致。

测试样例：

- case：`check_medicine_summary_memory`
  - 输入：`check_medicine_success` 的成功 trace 和 verified facts
  - 预期：Mimo 生成 `TaskSummary(result=success)`，MemoryCommitPlan 只更新 verified object memory。
- case：`object_not_found_summary_memory`
  - 输入：`object_not_found` 的失败 trace
  - 预期：summary 写清未找到；不写新位置；negative evidence 可记录。
- case：`distractor_summary_memory`
  - 输入：`distractor_rejected` 的失败 trace
  - 预期：干扰物不写入目标 object memory。
- debug 资产：`tests/homemaster/llm_cases/stage_09/<case_name>/`

验收标准：

- TaskSummary 必须来自 Mimo 真实输出。
- MemoryCommitPlan 通过 verified evidence 校验。
- 仅规则生成 summary 或 memory commit 不算阶段通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_09/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-09-summary-memory.md`

### 阶段 10：CLI、脚本、矩阵收口

实现：

- 补齐 `homemaster` CLI。
- 新建 `scripts/run_homemaster_scenarios.sh`。
- 输出 trace JSONL 和 scenario summary。
- 增加 HomeMaster acceptance matrix。

测试：

- `check_medicine_success`
- `check_medicine_stale_recover`
- `fetch_cup_retry`
- `object_not_found`
- `distractor_rejected`
- 全量 `pytest`
- `ruff check .`

测试样例：

- case：`scenario_matrix_check_medicine_success`
  - 输入：`check_medicine_success` 场景 + 查看药盒指令
  - 预期：LLM 驱动全链路成功。
- case：`scenario_matrix_stale_recover`
  - 输入：`check_medicine_stale_recover` 场景
  - 预期：LLM 驱动候选切换并成功或给出合理失败结论。
- case：`scenario_matrix_fetch_cup_retry`
  - 输入：`fetch_cup_retry` 场景
  - 预期：LLM 驱动取物链路，操作失败后进入验证和恢复。
- case：`scenario_matrix_object_not_found`
  - 输入：`object_not_found` 场景
  - 预期：候选耗尽后失败，不伪造成功。
- case：`scenario_matrix_distractor_rejected`
  - 输入：`distractor_rejected` 场景
  - 预期：拒绝干扰物，不误写 memory。
- debug 资产：`tests/homemaster/llm_cases/stage_10/<case_name>/`

验收标准：

- 5 个场景都必须走真实 Mimo 驱动的关键模型阶段。
- scenario summary 标注每个模型阶段是否真实调用 Mimo。
- 规则-only 端到端跑通不算 HomeMaster V1.2 通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_10/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-10-scenario-matrix.md`

## 8. 测试策略

### 8.1 单元测试

覆盖：

- contract schema
- LLM output validation
- object memory retrieval
- candidate generation
- orchestration validation
- step decision validation
- recovery decision validation
- memory commit guard

### 8.2 LLM 测试

本阶段主要用 LLM 测试任务脑能力：

- 每个模型阶段保留 fake provider 单测，保证 CI 稳定。
- 每个阶段验收至少跑一组真实 LLM 样例。
- 真实 LLM 默认从 `config/nvidia_api_config.json` 读取 provider。
- 默认 provider 必须是 `Mimo`，默认 model 必须是 `mimo-v2-pro`。
- 测试命令或 provider loader 不应默认读取环境变量中的其他模型覆盖 Mimo。
- 如果人工指定其他 provider，必须在测试结果和阶段报告中显式标注，不能覆盖 Mimo 默认结论。
- 真实 LLM 输出、重试、失败原因保存到 `plan/V1.2/test_results/stage_xx/`。
- 不把真实 LLM 不稳定性伪装成单元测试稳定通过。

### 8.3 LLM debug case 资产

每轮模型测试的输入、预期和实际结果必须保存到：

```text
/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_xx/<case_name>/
```

仓库内相对路径为：

```text
tests/homemaster/llm_cases/stage_xx/<case_name>/
```

固定文件：

```text
input.json
expected.json
actual.json
result.md
```

要求：

- `input.json` 保存阶段入口输入和必要上下文，例如 instruction、TaskCard、CandidatePool、runtime state。
- `expected.json` 保存关键断言，不要求保存完整大对象。
- `actual.json` 保存 Mimo 返回的结构化输出和程序裁剪后的关键执行结果。
- `result.md` 保存该轮是否通过、失败原因、对应 test result 日志路径和 debug 备注。
- 文件名和目录名使用稳定英文短名，方便 grep 和人工定位。
- 不保存 API key、完整 config secret 或不可公开的请求头。

### 8.4 VLM 图片接口测试

首版不启用真实图片 VLM。

测试重点：

- `VLMImageInput.enabled=false` 是默认值。
- disabled 时 pipeline 使用结构化 observation。
- 如果传入 image ref 但配置 disabled，系统不调用真实 VLM。
- 后续 enabled 时接口字段已经足够承载图片引用。

### 8.5 场景测试

核心场景：

- `check_medicine_success`
- `check_medicine_stale_recover`
- `fetch_cup_retry`
- `object_not_found`
- `distractor_rejected`

每个场景检查：

- final status
- trace 顺序
- 候选选择理由
- 验证结果
- 恢复动作
- memory commit 是否保守

### 8.6 V1 隔离测试

检查：

- 旧 `task-brain` 仍可运行。
- 新 `homemaster` CLI 不覆盖旧 CLI。
- `homemaster` 不导入旧 parser/planner/recovery/graph。
- 新测试不要求修改旧主链才能通过。

## 9. Test result 落盘约定

V1.2 每个阶段的测试结果统一保存到：

```text
plan/V1.2/test_results/
```

目录固定为：

```text
stage_01/
stage_02/
stage_03/
stage_04/
stage_05/
stage_06/
stage_07/
stage_08/
stage_09/
stage_10/
```

该目录已在 `.gitignore` 中忽略。不要提交测试日志、真实 LLM 输出、trace JSONL 或临时截图。

每个阶段建议保存：

- `pytest.log`
- `ruff.log`
- `llm_samples.jsonl`
- `scenario_summary.tsv`
- `trace/*.jsonl`
- `notes.md`

未实际运行的测试不要写成通过；失败重跑要保留失败日志，并在 `notes.md` 说明修复和重跑结果。

## 10. 阶段结论报告约定

每个阶段完成测试后，都必须在 `record/` 下保存一份精炼结论报告。报告是给人读的阶段结论，不替代 `test_results/` 中的原始日志。

命名格式：

```text
record/YYYY-MM-DD-实验名称.md
```

示例：

```text
record/2026-04-24-stage-03-object-memory-search.md
record/2026-04-24-stage-06-execution-loop.md
```

每份报告建议包含：

- 实验名称
- 阶段编号
- 测试日期
- Git commit 或当前工作区说明
- 使用的 LLM provider 和 model，默认应为 `Mimo / mimo-v2-pro`
- 测试命令摘要
- 原始日志位置，例如 `plan/V1.2/test_results/stage_03/pytest.log`
- 通过项
- 失败项
- 关键结论
- 后续需要修复或观察的问题

报告要精炼，重点写结论和证据路径，不复制整段日志。

## 11. 文件与模块建议

建议目录：

```text
src/homemaster/
  __init__.py
  contracts.py
  runtime.py
  trace.py
  frontdoor.py
  memory_search.py
  candidate_pool.py
  candidate_selector.py
  orchestrator.py
  orchestration_validator.py
  policy.py
  executor.py
  verifier.py
  recovery.py
  embodied_planner.py
  summary.py
  memory_commit.py
  pipeline.py
  cli.py
```

建议测试目录：

```text
tests/homemaster/
  test_contracts.py
  test_import_boundaries.py
  test_task_card.py
  test_object_memory_search.py
  test_candidate_pool.py
  test_candidate_selection.py
  test_orchestration_plan.py
  test_step_loop.py
  test_recovery_loop.py
  test_vlm_image_input_disabled.py
  test_embodied_planner_interface.py
  test_summary_memory_commit.py
  test_homemaster_scenarios.py
```

## 12. 工程 review 风险清单

实施时重点避免：

- 命名漂移：新主链代码、CLI、脚本和测试目录统一使用 `homemaster`，不要再使用临时版本号式命名。
- 过早多记忆检索：首版不要把 category prior、episodic、用户习惯都塞进上下文。
- 过早独立具身规划器：本阶段由 HomeMaster 大脑兼任，接口留好即可。
- VLM 假接入：图片接口要有，但默认 disabled；不要写成“已经接真实 VLM”。
- 旧链泄漏：不要把旧 parser/planner/recovery/graph 作为 fallback。
- LLM 测试不可复现：真实 LLM 默认使用 `config/nvidia_api_config.json` 里的 `Mimo / mimo-v2-pro`，日志落盘，CI 仍用 fake provider 保稳定。
- 记忆误写：没有 verified evidence 不更新长期 object memory。
- 测试日志进 git：`plan/V1.2/test_results/` 必须保持 ignored。
- 阶段结论缺失：每阶段完成后必须在 `record/YYYY-MM-DD-实验名称.md` 写精炼报告。

## 13. 完成标准

V1.2 完成时至少满足：

- `homemaster` 包可独立导入。
- `homemaster` CLI 可独立运行。
- 首版只使用 object memory 检索。
- 候选池由程序真实生成。
- 候选选择、高层编排、一步决策和恢复由 LLM 主导。
- VLM 图片接口存在但默认 disabled。
- 具身规划接口存在，本阶段由 HomeMaster 大脑兼任。
- 5 个核心场景有端到端结果。
- 每阶段都有原始测试结果落盘到 `plan/V1.2/test_results/stage_xx/`。
- 每阶段都有精炼结论报告落盘到 `record/YYYY-MM-DD-实验名称.md`。
- 阶段 LLM 测试默认使用 `config/nvidia_api_config.json` 中的 `Mimo / mimo-v2-pro`。
- trace 能清楚还原任务理解、检索、候选、编排、决策、执行、验证、恢复、总结和写回。
- V1 旧主链保持可运行，不被新主链污染。
