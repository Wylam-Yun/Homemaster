# HomeMaster V1.2 Pipeline-First 任务总控大脑执行计划

目标文档路径：`/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/2026-04-24-llm-first-task-brain-v2-execution-plan-zh.md`

## 1. Summary

V1.2 的目标是在 HomeMaster 仓库内建立一条新的 LLM-first 任务总控大脑主链。它不是继续深改旧 `task_brain` 的规则主链，而是新建独立的 `homemaster` 包，用更贴近最终系统的 pipeline 方式完成家庭养老场景里的“找物 + 简单操作”任务编排。

工程命名统一为：

- 系统名：`HomeMaster`
- Python 包：`src/homemaster/`
- CLI：`homemaster`
- 测试目录：`tests/homemaster/`
- 主交互入口：`homemaster`
- 环境体检入口：`homemaster doctor`
- 场景验收脚本：`scripts/run_homemaster_scenarios.sh`
- 测试结果目录：`plan/V1.2/test_results/`
- 阶段结论报告目录：`record/`

文档里可以把任务总控大脑称为 HomeBrain，但代码包、CLI、脚本和测试目录统一使用 `homemaster`，避免出现临时版本号式命名。

核心流程：

```text
用户输入
  -> 任务理解
  -> object_memory RAG 检索
  -> 可靠执行记忆判定 / planning context 组装
  -> 高层子任务编排
  -> skill 选择型执行 / 验证 / 恢复闭环
  -> 任务总结
  -> 证据把关后的记忆写回
```

首版以 LLM 测试为主，先证明任务脑的理解、检索、编排、执行期 skill 选择、验证和恢复能力。VLM 图片接口要提前留好，但默认禁用；当前验证先用结构化 observation / mock observation。导航、操作和验证先抽象成 3 个可复用 skill，不单独引入具身规划 planner；operation skill 内部根据子任务选择原子动作，并生成给 VLA 的指令。

阶段测试默认使用 `config/api_config.json` 中的 Mimo 配置；为了兼容历史本地配置，如果该文件不存在，代码会回退读取旧 provider config：

```text
provider name: Mimo
model: mimo-v2-pro
protocol: anthropic
```

如果 Mimo 不可用，阶段验收不能悄悄换模型并当作同一结果；必须在测试日志和阶段报告中写清楚使用了哪个 provider、哪个 model、失败原因和是否重跑。

## 2. 当前项目基线

当前仓库已经把 V1 旧主链从活跃工程入口中清理出去：

- 当前包路径：`src/homemaster/`
- 当前 CLI：`homemaster`
- 当前测试目录：`tests/homemaster/`
- 当前场景数据：`data/scenarios/`

旧 `src/task_brain/`、`task-brain` CLI、旧根目录测试和旧场景脚本不再作为当前工程的一部分维护。V1.2 后续开发只沿 `homemaster` pipeline 继续推进；如果确实需要旧版本里的某个经验，只参考历史计划或 git 历史，不把旧 parser、旧 deterministic planner、旧 recovery tree 或旧 graph 拉回当前主链。

## 3. 设计原则

### 3.1 Pipeline-first

HomeMaster V1.2 的主设计单位是 pipeline stage 和执行期闭环，不是固定业务模板。每个阶段都要说明输入、输出、模型职责、程序职责、测试方法和失败处理方式。

### 3.2 LLM-first，但程序守住落地边界

LLM 负责：

- 任务理解
- 可选的 memory retrieval query rewrite
- 高层子任务编排
- 执行期从可用 skill 中选择下一步调用
- 失败恢复判断
- 任务总结

程序负责：

- 加载场景、object memory 和 ExecutionState
- 构建 object memory RAG index
- 调用 embedding 模型生成 memory/query 向量
- 执行 hybrid retrieval 和 metadata guardrail
- 对 RAG hits 做可靠执行记忆判定
- 只有 reliable + groundable hit 才生成 `GroundedMemoryTarget`
- 没有可靠执行记忆时输出 `selected_target=None` 的 `PlanningContext`，让 Stage 05 规划探索/寻找
- 组装高层编排所需 planning context
- 校验结构化输出
- 校验观察点、锚点、grounded target、skill 名称和 skill 参数是否合法
- 维护 3 个首版 skill manifest：`navigation`、`operation`、`verification`
- 执行期把当前 subtask、ExecutionState 和 skill manifest 交给 Mimo 选择可选 action skill
- 调用 mock navigation / operation skill，并在动作后由程序调用 verification skill
- 记录 trace
- 根据验证证据推进状态
- 对长期记忆写回做证据把关

### 3.3 首版只把一种记忆纳入 RAG

为保证正确率，V1.2 首版只把 `object_memory` 纳入 RAG index。先不把 `category_prior_memory`、`episodic_memory`、用户习惯记忆和复杂相对关系记忆放进主链。

原因：

- 减少 RAG 召回噪声和 LLM 上下文噪声。
- 让 grounded target 来源可解释、可测试。
- 避免 category prior 和 episodic hint 过早影响正确性。
- 先把“目标物历史位置 -> RAG evidence -> grounded target -> 编排执行 -> 验证恢复”跑稳。

预留扩展点：

- `category_prior_memory` 可在后续阶段作为 object memory 缺失时的兜底 memory source。
- `episodic_memory` 可在后续阶段只作为排序解释或轻量加权，不直接生成执行目标。

### 3.3.1 BM25、Tokenizer、BGE-M3 与索引约定

Stage 03 的 memory RAG 使用两路检索：BM25 lexical retrieval + BGE-M3 dense embedding retrieval。BM25 和 tokenizer 不需要模型，BGE-M3 需要 embedding API。没有 BGE-M3 embedding 时，只能算 lexical/metadata 预检，不能算 RAG 阶段通过。

约定：

- 新增依赖：`bm25s>=0.2` 和 `jieba>=0.42`。BM25 用于词项召回，jieba 用于中文分词。
- 新增 embedding provider 配置，优先放在 `config/api_config.json` 或独立的 `config/embedding_config.json`；旧 provider config 只作为兼容回退；后续接入用户提供的 BGE-M3 API，但日志和 debug 资产不能保存 API key。
- 已验证 `BAAI/bge-m3` 在 SiliconFlow 上应通过 `/v1/embeddings` 调用；`/v1/messages` 会把它当 chat/messages 模型而失败。因此 Stage 03 的 `embedding_client.py` 不能复用现有 `RawJsonLLMClient` 的 Anthropic messages 调法。
- 工程单测可以使用 deterministic test embedder，验证索引、过滤、打分和资产落盘；它不作为阶段验收证据。
- 阶段验收需要真实 BGE-M3 provider 完成 query/document embedding，或者明确记录 embedding provider 不可用导致 Stage 03 未通过。
- BM25 tokenizer 不能靠散落的硬编码字符串实现；必须有 `MemoryTokenizer` 接口和 `JiebaMemoryTokenizer` 默认实现。
- 领域词典从 memory/world 自动生成，给 tokenizer 做分词参考，来源包括 `object_category`、`aliases`、`anchor.display_text`、`anchor.anchor_type`、`room_id`、`viewpoint_id` 和必要的中英文 room/anchor 映射。
- object memory 文档要保存原始 metadata，RAG hit 不能只返回文本片段；后续 grounding / planning context 必须能拿到 `memory_id`、`anchor_id`、`viewpoint_id` 等结构化字段。
- 小规模 scenario 可在运行时临时建索引；后续真实系统再引入持久化向量库。

### 3.4 验证是推进硬门槛

skill 返回成功不等于任务完成。每一步推进都必须经过验证结果支持：

- step 是否完成，需要 verification result。
- subtask 是否完成，需要 verification result。
- task 是否完成，需要 final verification result。
- memory 是否写回，需要 verified evidence。

首版 verification 使用结构化 observation / mock observation。VLM 图片接口在数据结构中预留，但默认 `enabled=false`，避免当前阶段依赖真实图片理解。

### 3.5 高层子任务不绑定 skill

高层编排只描述要完成的子任务，不提前写死调用哪个 skill。skill 选择发生在执行期，由当前 subtask、ExecutionState 和可用 skill manifest 共同决定。

首版维护 3 个 skill manifest：

- `navigation`：只负责两类输入：给目标物名称后自动寻找目标物；给具体位置后自动到达指定位置。
- `operation`：根据当前子任务和观察信息，内部选择原子动作，并生成给 VLA 的操作指令；测试阶段可不输出图片。
- `verification`：由程序自动调用 VLM 或 mock verifier，判断当前子任务或整个任务是否完成；不作为 Mimo 可手动选择的执行 skill。

当前不对 skill manifest 做 RAG 或 embedding 检索，因为首版 action skill 很少；程序直接提供简短 manifest，由 Mimo 在 `navigation` / `operation` 等可执行候选中选择。未来 skill 数量明显增多时，再考虑对 skill manifest 做 BM25 或 embedding 检索。

业务细节放在 `intent`、目标、位置提示、接收对象和成功标准里。比如“去厨房找水杯，然后拿给我”的高层子任务应是：找到水杯、拿起水杯、找到用户、放下水杯、确认交付完成。

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
- `location_hint` 只表达用户提示，例如“厨房”“桌子那边”，不等于真实执行目标。

### 4.2 MemoryRetrievalQuery

职责：描述本轮 RAG memory retrieval 的 query、source filter 和排除条件。

建议字段：

- `query_text`
- `target_category`
- `target_aliases`
- `location_terms`
- `source_filter`
- `top_k`
- `excluded_memory_ids`
- `excluded_location_keys`
- `reason`

说明：

- `query_text` 在 Stage 03 live 验收中由 Mimo 从 `TaskCard` 生成；工程单测可以用固定 query substitute。Mimo 不直接读取 memory，也不直接决定 hit。
- `source_filter` 首版固定为 `["object_memory"]`。
- `target_category`、`target_aliases` 和 `location_terms` 用于 hybrid retrieval 的 metadata/keyword 加权。
- 当前任务内已经验证未找到的位置通过 `excluded_memory_ids` 或 `excluded_location_keys` 排除。
- 首版不读取 category prior，不读取 episodic memory。

### 4.3 MemoryRetrievalResult

职责：承载从 memory RAG index 召回并经过 metadata guardrail 后的证据。

建议字段：

- `hits`
- `excluded`
- `retrieval_query`
- `ranking_reasons`
- `retrieval_summary`
- `embedding_provider`
- `index_snapshot`

说明：

- `hits` 首版只包含 object memory 记录的精简信息：memory id、目标类别、锚点、观察点、置信度、确认时间、文本片段、向量分、metadata 分、最终分和排序原因。
- `excluded` 记录因为 task negative evidence 被排除的位置或 memory id。
- RAG hit 必须保留 canonical object memory metadata，或至少能通过 `memory_id` 回查原始 object memory；不能只把自然语言 chunk 交给后续 planning context。

### 4.4 GroundedMemoryTarget

职责：程序把通过可靠性硬判定的 RAG hit ground 成可导航、可验证的执行目标。

建议字段：

- `memory_id`
- `room_id`
- `anchor_id`
- `viewpoint_id`
- `display_text`
- `evidence`
- `executable`
- `invalid_reason`

说明：

- `GroundedMemoryTarget` 只能来自 `reliable` 的执行记忆，不等同于 RAG top1。
- 可靠执行记忆必须同时满足：未被 negative evidence 排除、`invalid_reason is None`、`executable=true`、`memory_id/room_id/anchor_id/viewpoint_id` 完整、viewpoint 存在于 `world.viewpoints`、anchor 存在于 `world.furniture` 且 anchor viewpoint 与 hit 一致、目标 metadata 能匹配 `TaskCard.target`、没有明显 location hint 冲突、`confidence_level` 不是 `low`、`belief_state` 不是 `stale`。
- `low confidence`、`stale`、位置冲突或只有弱相关证据的 hit 只能作为 `weak_lead`，不能生成 selected target，但可以保留给 Stage 05 作为探索线索。
- 如果没有 reliable hit，Stage 04 不生成 `GroundedMemoryTarget`，而是输出 `selected_target=None` 的 `PlanningContext`。

### 4.5 PlanningContext

职责：给 Stage 05 高层编排提供最小上下文。

建议字段：

- `task_card`
- `retrieval_query`
- `memory_evidence`
- `selected_target`
- `rejected_hits`
- `runtime_state_summary`
- `world_summary`
- `planning_notes`

说明：

- Stage 04 不要求 Mimo 再做单独目标选择，也不引入 reranker 或 LLM soft judge。
- `selected_target` 是可空字段：有可靠执行记忆时填 `GroundedMemoryTarget`；没有可靠执行记忆时为 `null`。
- `runtime_state_summary` 必须显式包含 `grounding_status=grounded|ungrounded`、`grounding_reason` 和 `needs_exploratory_search`，让 Stage 05 明确知道下一步是基于记忆执行，还是先探索/寻找。
- `memory_evidence`、`rejected_hits` 和 `planning_notes` 需要保留足够 score breakdown 与 reliability reasons，便于 Stage 05 prompt 和 debug 报告解释为什么可以去这个位置，或为什么不能直接去。
- `world_summary` 只包含编排需要的最小可落地信息，例如 room、anchor、viewpoint 和可用 skill 的必要提示。

### 4.6 OrchestrationPlan

职责：高层子任务编排。

建议字段：

- `goal`
- `subtasks`
- `confidence`

单个 subtask 建议字段：

- `id`
- `intent`
- `target_object`
- `recipient`
- `room_hint`
- `anchor_hint`
- `success_criteria`
- `depends_on`

说明：

- 高层 subtask 不写 `skill`、`module`、`navigation`、`operation` 或 `verification`。
- `intent` 用自然语言或受控短语表达“找到目标物”“拿起目标物”“找到用户”“放下目标物”“确认完成”等任务意图。
- 查看类任务通常会拆成“找到/观察目标物”和“确认是否存在”，但高层 validator 不用规则强行匹配固定模板。
- 取物交付类任务通常会拆成“找到目标物 -> 拿起目标物 -> 找到接收对象 -> 放下/交付目标物 -> 确认交付完成”，这是 prompt 中给 Mimo 的示例和期望，不作为脆弱的程序硬规则。
- `selected_target` 不由 Mimo 在 `OrchestrationPlan` 中输出；程序从 `PlanningContext.selected_target` 附加到执行上下文，避免模型伪造或改写 memory target。
- Stage 05 第一批改动先迁移公共契约：`Subtask` 改为 intent 型字段，`StepDecision` 使用 `selected_skill/skill_input`，`ModuleExecutionResult` 使用 `skill` 并增加 `skill_output`，删除或弃用旧 `EmbodiedActionPlan`，并同步更新 `test_contracts.py`。
- 高层计划校验保持轻量：只校验 JSON/schema、必要字段、依赖引用、禁止旧 candidate 字段、禁止伪造 `selected_target` 或 memory id；不再用程序规则强制判断“取物必须拆成几步”。
- 任务正确性主要交给执行闭环：每个 action skill 后都快速调用 verification，verification 通过才推进子任务。

### 4.7 StepDecision

职责：执行期每轮只决定下一步。

建议字段：

- `subtask_id`
- `selected_skill`
- `skill_input`
- `expected_result`
- `reason`

说明：

- `selected_skill` 首版只允许 Mimo 选择 action skill，例如 `navigation`、`operation`。`verification` 由程序自动触发，不允许 Mimo 手动选择。
- 执行期程序把当前 subtask、ExecutionState、可用 skill manifest 给 Mimo，Mimo 只能从候选 skill 中选择并填参数。
- `navigation` 的输入只表达“找目标物”或“去具体位置”：`goal_type=find_object|go_to_location`、`target_object`、`target_location`、可选 `room_hint/anchor_hint`。不强制把 `viewpoint_id` 当成导航目标，VLN skill 后续自己负责找物体或到达位置。首版不增加 `find_user`，需要回到用户时使用 runtime 记录的用户位置并转成 `go_to_location`。
- `operation` 的输入只表达当前操作子任务、目标物、接收对象和当前观察；原子动作由 operation skill 内部选择，Stage 05 不让 Mimo 在 StepDecision 里直接编排原子动作。
- `verification` 在每次 `navigation` 或 `operation` 后由程序强制触发，并根据当前 subtask 的 success criteria 生成校验输入。
- StepDecision 不包含 `verify_after`，也不允许 Mimo 关闭验证；是否验证、验证 scope 和验证输入都由程序根据 action skill 结果自动生成。

### 4.8 ModuleExecutionResult

职责：记录 skill 调用结果。

建议字段：

- `skill`
- `status`
- `skill_output`
- `observation`
- `runtime_state_delta`
- `evidence`
- `error`

说明：

- `observation` 使用结构化 observation。
- `skill_output` 用来稳定保存 skill 的直接产出，例如 operation 生成的 `vla_instruction`、`planned_atomic_actions`、可选 `image_input`，避免把关键输出散塞进 `evidence`。
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
- 首版由 mock/symbolic verifier 根据结构化 observation 生成；后续替换为真实 VLM verifier，直接询问“当前子任务/整个任务是否完成”。

### 4.10 ExecutionState / SubtaskRuntimeState

职责：记录 Stage 05 执行闭环的当前状态，让“选下一步、校验前置条件、失败恢复、重规划”都有稳定依据。

建议字段：

- `task_status`
- `current_subtask_id`
- `subtasks`
- `held_object`
- `target_object_visible`
- `target_object_location`
- `user_location`
- `current_location`
- `last_observation`
- `last_skill_call`
- `last_skill_result`
- `last_verification_result`
- `failure_record_ids`
- `negative_evidence`
- `retry_counts`
- `completed_subtask_ids`

单个 `SubtaskRuntimeState` 建议字段：

- `subtask_id`
- `status`
- `depends_on`
- `attempt_count`
- `last_started_at`
- `last_completed_at`
- `last_skill`
- `last_observation`
- `last_verification_result`
- `failure_record_ids`

说明：

- `task_status` 覆盖 `running | completed | failed | needs_user_input`。
- `SubtaskRuntimeState.status` 覆盖 `pending | running | verified | failed | blocked | skipped`。
- 程序只能选择依赖已满足且未 verified 的 subtask 作为下一步；如果依赖未满足，不能让 Mimo 跳步执行。
- `target_object_visible`、`held_object`、`user_location` 和 `current_location` 是 operation / navigation 前置条件校验的核心状态来源。
- `negative_evidence` 记录“哪里找过但没看到目标”“哪个 memory/location 已失败”等事实，可供 `retrieve_again` 回 Stage 03 使用。
- ExecutionState 是运行期对象，写入 trace/debug；Stage 05 首版不把它当长期记忆写回。

### 4.11 FailureRecord

职责：记录 Stage 05 执行中的失败事实，供当前恢复/重规划使用，并为未来 event memory 检索预留结构。

建议字段：

- `failure_id`
- `subtask_id`
- `subtask_intent`
- `skill`
- `failure_type`
- `failed_reason`
- `skill_input`
- `skill_output`
- `verification_result`
- `observation`
- `negative_evidence`
- `retry_count`
- `created_at`
- `event_memory_candidate`

说明：

- `FailureRecord` 是运行期失败记录，不等于长期记忆；Stage 05 首版只写 debug/runtime，并把 `failure_id` 挂到 ExecutionState，不直接写入 event memory。
- `failure_type` 首版覆盖 `model_output_invalid`、`skill_schema_invalid`、`skill_failed`、`precondition_failed`、`verification_failed`、`target_not_visible`、`object_not_found`、`max_retry_exceeded`。
- `negative_evidence` 用于表达“哪里已经找过但没看到”“哪个操作没有被验证成功”等事实，可传回 Stage 03 RAG 排除已找过的位置或 memory。
- `event_memory_candidate` 只保存未来可转成事件记忆的结构化摘要，例如 failed_search / failed_operation / verification_failed；当前阶段不做 event memory RAG。

### 4.12 RecoveryDecision

职责：失败、证据不足或状态异常后，决定如何回到主循环。

建议字段：

- `action`
- `reason`
- `failure_record_ids`
- `should_retrieve_again`
- `should_replan`
- `ask_user_question`
- `final_failed_reason`

说明：

- `action` 覆盖 `retry_step`、`reobserve`、`retrieve_again`、`replan`、`ask_user`、`finish_failed`。
- 首版删除 `switch_target`：Stage 04 只输出一个 `selected_target`，不维护 candidate pool 或 alternative target list；如果当前 grounded target 失败，只能带 negative evidence 回 Stage 03 `retrieve_again`，重新生成新的 `PlanningContext`。
- RecoveryDecision prompt 必须包含当前 FailureRecord 列表、ExecutionState、negative evidence 和最近一次 verification result，避免 Mimo 重复选择已失败的位置或动作。

### 4.13 SkillManifest / SkillCall

职责：描述可调用 skill，并记录执行期真正发出的 skill 调用。

首版 `SkillManifest` 只维护 3 个：

- `navigation`：用于按目标物名称自动寻找目标物，或按具体位置描述自动到达指定位置。
- `operation`：用于拿起、放下、递送等操作；它内部根据子任务和观察信息选择原子动作，并生成 VLA 指令。
- `verification`：用于调用 VLM 或 mock verifier，判断当前子任务或整个任务是否完成；由程序调用，不进入 Mimo 的可选 action skill 列表。

建议字段：

- `name`
- `description`
- `selectable_by_llm`
- `input_schema`
- `preconditions`
- `output_schema`

`SkillCall` 建议字段：

- `skill`
- `input`
- `expected_result`
- `reason`

说明：

- 首版不使用 RAG 检索 skill，因为 action skill 很少；程序直接提供 manifest，Mimo 在可选候选中选择。
- Mimo 不能凭空创造 skill 名称；程序必须拒绝不在 manifest 中的 skill。
- Mimo 不能手动选择 `verification`；验证调用由程序自动生成。
- skill 参数必须通过对应 schema 校验。
- `navigation` schema 首版只保留 `goal_type`、`target_object`、`target_location`、`room_hint`、`anchor_hint`，避免 Stage 05 绑定具体导航实现；首版不支持 `find_user`，找到用户通过 runtime 用户位置 + `go_to_location` 实现。
- `operation` schema 首版只保留 `subtask_intent`、`target_object`、`recipient`、`current_observation`、`success_criteria`；operation skill 自己决定原子动作，输出 `vla_instruction`、可选 `planned_atomic_actions` 和可选 `image_input`。测试阶段 `image_input` 可以为空或 disabled。
- `verification` schema 首版包含 `scope`、`subtask_intent` 或 `task_goal`、`success_criteria`、`observation`、可选 `image_input`；测试阶段用结构化 observation，后续接真实 VLM 图片。
- 后续如果 skill 数量扩大，再引入 skill manifest 的 BM25 或 embedding 检索；它和 Stage 03 的 object memory RAG 是两套不同检索。

### 4.14 TaskSummary

职责：把 Stage 05 的执行过程总结成用户可读、可审计的任务结论。`TaskSummary` 由 Mimo 生成，但只能基于 Stage 05 trace、`ExecutionState`、`VerificationResult` 和 `FailureRecord`，不能编造没有验证过的结果。

建议字段：

- `result`
- `confirmed_facts`
- `unconfirmed_facts`
- `recovery_attempts`
- `user_reply`
- `failure_summary`
- `evidence_refs`

说明：

- `TaskSummary` 是面向用户和开发者的人类可读总结，可以由 Mimo 生成。
- `evidence_refs` 必须引用程序整理出的稳定证据 id，例如 `verification:verify-find-cup-1`、`failure:failure-1`、`skill_result:skill-pick-cup-1`。
- Mimo 只能总结证据，不能自己发明证据 id，也不能把未验证事实写成 confirmed fact。

### 4.15 MemoryCommitPlan

职责：把 verified evidence 和 failure evidence 转成可写回记忆的计划。`MemoryCommitPlan` 首版由程序基于 `EvidenceBundle` 生成并校验；Mimo 可以生成 `TaskSummary` 和候选事实描述，但不能直接决定长期记忆写什么。Stage 06 不是只写 object memory，而是同时管理三类长期记录：

- `object_memory`：物体位置记忆，用于 Stage 03 当前的 RAG 检索。
- `fact_memory` / `event_memory`：任务中发生过的事实和事件，例如“在厨房餐桌没看到水杯”“拿起水杯失败且未被验证成功”。首版先保存，不加入 Stage 03 检索。
- `task_record`：一次任务的完整摘要，方便人读、审计和后续调试。

支撑契约：

- `EvidenceRef`
  - `evidence_id`：稳定 id，例如 `verification:verify-find-cup-1`。
  - `evidence_type`：`verification_result | failure_record | skill_result | observation | selected_target | trace_event`。
  - `source_id`：原始对象 id，例如 `failure-1`、`skill-find-cup-1`。
  - `subtask_id`、`memory_id`、`location_key`：可选，用于追溯到子任务、物体记忆和位置。
  - `created_at`
  - `summary`：短文本摘要，不放 API key、headers 或原始长 prompt。
- `EvidenceBundle`
  - `task_id`
  - `evidence_refs`
  - `verified_facts`
  - `failure_facts`
  - `system_failures`
  - `negative_evidence`
- `ObjectMemoryUpdate`
  - `memory_id`
  - `update_type`: `confirm | mark_stale | mark_contradicted`
  - `updated_fields`
  - `evidence_refs`
  - `reason`
- `FactMemoryWrite`
  - `fact_id`
  - `fact_type`: `object_seen | object_not_seen | operation_failed | delivery_verified | verification_failed | user_preference | system_event`
  - `polarity`: `positive | negative | neutral`
  - `target`
  - `location`
  - `time_scope`: 例如 `task_run`、`timestamp` 或 `interval`
  - `confidence`
  - `text`
  - `evidence_refs`
  - `expires_at` 或 `stale_after`
  - `searchable`: Stage 06 首版固定为 `false`，先不进入 Stage 03 RAG。
- `TaskRecord`
  - `task_id`
  - `task_card`
  - `summary`
  - `result`
  - `started_at`、`completed_at`
  - `evidence_refs`
  - `failure_record_ids`

建议字段：

- `object_memory_updates`
- `fact_memory_writes`
- `task_record`
- `negative_evidence`
- `skipped_candidates`
- `index_stale_memory_ids`
- `commit_id`
- `skipped`

说明：

- 首版只允许更新已有 object memory 的确认时间、置信度、`belief_state`，以及把被可靠反证的记忆标记为 `stale` 或 `contradicted`。
- 新建 object memory 可以后置，避免本阶段误写新长期物体位置。
- fact/event memory 必须引用 `EvidenceRef`，不能只保存一段自由文本。
- 没有证据引用的事实候选必须进入 `skipped_candidates`，不能写长期记忆。
- 执行失败、验证失败、前置条件失败可以写 fact/event memory；模型 JSON 失败、schema 失败这类系统失败只能写 task record/debug，不能当成环境事实。
- 失败事实必须具体到时间、目标、位置或子任务，不能写成永久绝对结论。例如可以写“本次任务中，在厨房餐桌对应视角没有观察到水杯”，不能直接写“水杯不在厨房”。
- negative evidence 可以用于当前任务恢复和未来检索排除，但不等于长期 object memory 的删除。
- `MemoryCommitPlan` 由程序生成和校验；如果 Mimo 参与，只能作为候选事实摘要输入，程序必须重新检查 evidence refs、fact type、过期策略和写回边界。

### 4.16 VLMImageInput

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
- 程序校验 schema、enum、viewpoint、memory id、RAG hit metadata、grounded target、skill name 和 skill input。
- RAG 检索结果必须能回到 canonical object memory，不允许只凭自然语言 chunk 生成执行目标。
- 程序不把 RAG top1 直接当作可靠目标；必须先经过 reliability hard checks。
- 只有 reliable + world-groundable hit 才能成为 `GroundedMemoryTarget`；weak lead 只能作为探索线索。
- 没有可靠执行记忆时不是失败，也不能伪造地点；Stage 04 必须输出 ungrounded `PlanningContext`，交给 Stage 05 规划探索/寻找。
- 高层编排不鼓励写原子动作，但首版不因出现原子动作字样直接拒绝；真正执行时，Stage 05 只把子任务意图和观察交给 `operation`，由 operation skill 内部选择原子动作并生成 VLA 指令。
- StepDecision 每次只决策一步。
- skill 成功不等于任务成功，推进必须看 VerificationResult。
- 记忆写回必须基于 verified evidence。
- 模型阶段失败时必须记录失败原因。Stage 05 的 `OrchestrationPlan`、`StepDecision`、`RecoveryDecision` 如果返回非 JSON，使用修复 prompt 最多再试两次，总计 3 次 attempt；仍失败则写 FailureRecord，并追问用户或安全失败。
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

### 6.2 object_memory RAG 检索

输入：

- `TaskCard`
- runtime negative evidence
- object memory 文件
- BM25/tokenizer 配置
- BGE-M3 embedding provider 配置
- memory documents、BM25 index 和 dense vector index，或可建索引的原始 object memory

处理：

- Mimo 从 `TaskCard` 生成 `MemoryRetrievalQuery`，包括 `query_text`、`target_category`、`target_aliases`、`location_terms`、`source_filter`、`top_k` 和排除条件；Mimo 不直接读取 memory，不直接返回 memory hit，也不能编造 `memory_id`。
- 程序校验 `MemoryRetrievalQuery` schema 和边界：`source_filter` 首版只能是 `["object_memory"]`，`top_k` 有上限，排除条件只能来自 runtime negative evidence。
- 程序把 `memory.json.object_memory` 序列化为 `MemoryDocument(text + metadata)`，文本用于 BM25/BGE-M3，metadata 用于后续 grounding / planning context 和 guardrail。
- 程序从 memory documents 自动构建领域词典，并交给 `JiebaMemoryTokenizer` 做中文分词参考。
- 程序用 tokenized documents 建 BM25 lexical index。
- 程序调用 BGE-M3 embedding provider 生成 document/query dense vectors；小规模 scenario 可运行时建内存向量索引。
- 程序分别执行 BM25 检索和 BGE-M3 dense 检索，再用加权分数或 RRF 做融合排序。
- 程序应用 metadata guardrail：只允许 `source_filter=["object_memory"]`，排除 runtime negative evidence 命中的 memory/location。
- 程序输出 `MemoryRetrievalResult`。

排序建议：

```text
score =
  bm25_score
  + bge_m3_dense_score
  + metadata_boost(category/alias/location/confidence/recency)
  - stale_or_contradicted_penalty
  - task_negative_evidence_exclusion
```

如果 BM25 和 BGE-M3 分数尺度不稳定，优先使用 RRF 融合两个检索结果的排名，再叠加 metadata boost/penalty。

测试：

- 水杯任务不返回药盒 object memory。
- 已排除 location 不进入 hits。
- stale 记录可保留但排序降低，并在 reason 里说明。
- 每个 hit 记录 bm25_score、dense_score、metadata_score、final_score 和排序原因。
- RAG hit 可回查 canonical object memory metadata。
- 不读取 category prior 和 episodic memory。

### 6.3 可靠执行记忆判定与 planning context 组装

输入：

- `TaskCard`
- `MemoryRetrievalResult`
- mock world / scenario world
- runtime state summary

处理：

- 程序按 Stage 03 排序顺序遍历 hits，但排序只决定检查顺序，不直接决定可靠性。
- 程序对每个 hit 生成 `ReliabilityAssessment`：`reliable`、`weak_lead` 或 `unreliable`。
- 判定顺序固定：先过滤 excluded / invalid，再检查 execution fields，再校验 world viewpoint/anchor，再判断 target match，再判断 location conflict，再判断 confidence/belief_state。
- 第一个 `reliable` hit 被转成 `GroundedMemoryTarget`，并写入 `PlanningContext.selected_target`。
- 如果没有 reliable hit，`PlanningContext.selected_target=None`，`runtime_state_summary.grounding_status="ungrounded"`，`planning_notes` 明确写入“需要探索/寻找”，并保留 weak leads 作为探索线索。
- Stage 04 不调用 Mimo、不重跑 embedding、不重排检索结果、不让模型临时编造地点。

测试：

- reliable hit 能在 scenario 中找到真实 viewpoint 和 anchor，并生成 `GroundedMemoryTarget`。
- 缺失 viewpoint/anchor、被 negative evidence 排除、target metadata 不匹配的 hit 不能成为 selected target。
- `low confidence`、`stale`、位置冲突的 hit 降为 `weak_lead`，不作为可靠执行记忆。
- 没有 reliable hit 时仍输出合法 `PlanningContext`，Stage 05 可以据此规划探索/寻找。
- `PlanningContext` 中包含足够的 memory evidence、rejected hits、reliability reasons 和 world summary，Stage 05 不需要回头读原始 memory JSON。

### 6.4 高层子任务编排

输入：

- `TaskCard`
- `PlanningContext`

处理：

- LLM 生成 `OrchestrationPlan`。
- 程序只做轻量高层校验：JSON/schema 合法、每个 subtask 有 `id`、`intent`、`success_criteria`，`depends_on` 引用存在且无循环，不出现旧 candidate 字段，不伪造 `selected_target` 或 memory id。
- 高层 subtask 不写 skill 名称，不出现 `navigation`、`operation`、`verification` 这类执行实现。
- 当 `PlanningContext.selected_target` 存在时，计划可以使用 selected target 的 room、anchor、memory evidence 作为提示，但不能编造新 memory target。
- 当 `PlanningContext.selected_target=None` 时，计划应先生成探索/寻找/观察或追问型子任务，不能假装有可靠记忆。
- 取物交付任务在 prompt 中引导 Mimo 覆盖“找到目标物、拿起目标物、找到接收对象、放下/交付目标物、确认完成”这些高层意图；程序不写复杂规则去重判模型拆解质量，拆解质量通过真实 Mimo case 和执行期 verification 闭环来验证。

测试：

- 查看类真实 Mimo case 应自然生成观察/确认类子任务，不应执行拿起或放下动作；如果模型计划不理想，记录为 case 质量问题并用 prompt 迭代，而不是靠程序硬模板兜底。
- 取物类真实 Mimo case 应自然覆盖找物、拿起、找接收对象、放下/交付、确认；真正推进仍以每步 verification 为准。
- ungrounded context 生成探索/寻找计划，不伪造 memory target。
- 高层编排不写 `skill`；如果 Mimo 在高层计划里提到原子动作字样，首版不直接判失败，但真正执行时 Stage 05 不直接执行这些字样，而是让 `operation` skill 根据当前子任务和观察自行选择原子动作。
- 轻量 validator 覆盖非法 JSON/schema、缺必要字段、依赖不存在/循环依赖、旧 candidate 字段、伪造 selected target。

### 6.5 skill 选择 / 执行 / 验证 / 恢复闭环

主循环：

```text
while not finished:
  program -> choose next pending subtask from ExecutionState
  program -> provide current subtask + ExecutionState + 3 skill manifests
  LLM -> StepDecision(selected_skill + skill_input)
  program -> validate selected_skill, skill_input, and preconditions from ExecutionState
  program -> call skill
  program -> ModuleExecutionResult
  program -> call verification after navigation/operation
  verifier -> VerificationResult
  if verification passed:
    program -> update ExecutionState progress
  else:
    program -> write FailureRecord + negative evidence into ExecutionState
    program -> provide FailureRecords + ExecutionState to Mimo
    LLM -> RecoveryDecision
    program -> apply recovery action
```

LLM 输出重试策略：

- `OrchestrationPlan`、`StepDecision`、`RecoveryDecision` 必须是 JSON object。
- 如果 Mimo 返回非 JSON，程序追加修复提示，让模型最多再试两次；三次都不是 JSON 时写 `FailureRecord(failure_type=model_output_invalid)`。
- 如果是 JSON 但 schema / contract 不合法，程序记录错误并最多做一次同阶段修复；仍失败则写 FailureRecord，不合成假结果。

skill 选择策略：

- 首版维护 `navigation`、`operation`、`verification` 三个 skill manifest，不使用 RAG 或 embedding 搜索 skill。
- 程序把 skill manifest、当前 subtask、ExecutionState 和前置条件一起放进 prompt；Mimo 只能选择可选 action skill，首版为 `navigation` 和 `operation`。
- 程序负责硬过滤和校验：不存在的 skill 拒绝，schema 不匹配拒绝，前置条件不满足拒绝。
- `navigation` 可用于按目标物名称找物体，也可用于按具体位置描述到达指定位置；输入以 `goal_type`、`target_object`、`target_location`、`room_hint`、`anchor_hint` 为主。首版不新增 `find_user`，取物交付中的“找到用户”优先使用 runtime 里记录的用户位置，并转成 `goal_type=go_to_location`。
- `operation` 可用于拿起、放下、递送；输入以 `subtask_intent`、`target_object`、`recipient` 和当前 observation 为主。它内部选择原子动作并生成 VLA 指令，但不能在目标未验证可见、未拿稳或未找到接收对象时调用。
- `verification` 在每次 `navigation` 或 `operation` 后自动执行；Mimo 不手动选择 verification，skill 返回 success 不等于 subtask 完成。
- ExecutionState 是选择下一步和前置条件校验的唯一运行期状态来源；不得只靠 prompt 文本记忆判断“目标已可见”“物品已拿起”或“用户位置已知”。

测试：

- trace 顺序稳定。
- 每轮都有 StepDecision。
- 每次 action skill 后都有 VerificationResult，只有验证通过才推进。
- skill 成功但验证失败时不能推进。
- 模型选择不在 manifest 中的 skill 会被拒绝。
- 模型在目标未验证前选择 `operation` 会被拒绝。

### 6.6 恢复

失败分类：

- `model_output_invalid`：Mimo 输出不是 JSON，或重试后仍不能解析。
- `skill_schema_invalid`：Mimo 选择了不存在的 skill、手动选择 verification、或 skill_input schema 不合法。
- `precondition_failed`：例如目标物还没被验证可见，却试图调用 operation。
- `skill_failed`：skill 返回 failed / blocked，例如 navigation 无法到达目标位置。
- `verification_failed`：skill 返回 success，但 verification 未通过。
- `target_not_visible` / `object_not_found`：到达或观察后没有看到目标物。
- `max_retry_exceeded`：同一个子任务或恢复动作超过允许次数。

失败记录：

- 每次失败都写 `FailureRecord`，包含 subtask、skill、输入、输出、verification、failed_reason、negative_evidence 和 retry_count。
- `FailureRecord` 写入 Stage05 debug assets 和 trace；首版不直接写入长期 event memory。
- `event_memory_candidate` 字段预留未来事件记忆，例如“水杯在厨房桌子旁找过但没看到”“拿起水杯失败且未被验证成功”。
- 当失败能形成可靠 negative evidence 时，程序更新 runtime negative evidence；例如“厨房桌子旁已观察但未看到水杯”。

恢复动作：

- retry current step
- reobserve
- rerun memory RAG retrieval
- replan orchestration
- ask user
- finish failed

恢复/重规划规则：

- `retry_step`：同一子任务可有限重试，retry_count 增加；超过上限后转 `replan`、`retrieve_again`、`ask_user` 或 `finish_failed`。
- `reobserve`：不改变计划，只重新获取 observation，再执行 verification。
- `retrieve_again`：带上最新 negative evidence 回 Stage 03 RAG，排除已找过的位置或 memory，重新生成 `PlanningContext`。
- `replan`：把 TaskCard、当前 PlanningContext、ExecutionState、FailureRecord 列表和 negative evidence 一起给 Mimo，重新生成后续 OrchestrationPlan。
- `ask_user`：当目标不明确、用户位置缺失、可执行线索耗尽或反复失败时追问用户。
- `finish_failed`：安全失败，进入 Stage 06 summary；必须写清楚失败原因，不能伪造成功。
- 首版不支持 `switch_target`；如果当前 grounded target 失败，必须通过 `retrieve_again` 重新检索并生成新的 PlanningContext。

测试：

- `check_medicine_stale_recover`：当前 grounded target 失败后，通过 `retrieve_again` 带 negative evidence 重新生成 PlanningContext。
- `object_not_found`：可执行目标耗尽后失败。
- `distractor_rejected`：看到干扰物但不误判目标。
- 恢复动作来自 LLM 结构化输出，不来自旧 recovery tree。
- 非 JSON RecoveryDecision 会触发最多两次修复重试；仍失败时写 FailureRecord 并安全失败。
- replan prompt 包含 failure records 和 negative evidence，不重复选择已验证失败的位置。

### 6.7 navigation / operation / verification skill

当前阶段策略：

- 不单独实现独立具身规划 planner。
- 导航和操作都封装为 skill，Stage 05 只负责选择、校验、调用和验证。
- `navigation` 后续可接 VLN：给目标物名称就自动寻找目标物，给具体位置就自动到达指定位置；首版测试用 mock navigation，并返回结构化 observation。
- `operation` 后续可接 VLA/原子动作执行器：Stage 05 只传子任务意图、目标和观察，operation skill 内部选择原子动作并生成 `vla_instruction`；首版测试用 mock operation，并返回结构化 observation。
- `verification` 后续可接 VLM：程序把当前子任务或整个任务、success criteria、观察和可选图片交给 verifier，询问是否完成；首版使用结构化 observation，图片接口默认 disabled。
- 原子动作不作为高层 subtask，也不由 Stage 05 的 Mimo StepDecision 直接编排。

测试：

- 目标未验证前不能调用 `operation`。
- `fetch_cup_retry` 覆盖操作失败、验证失败、恢复重试。
- `navigation` 找不到水杯时不推进到拿取，而是写 negative evidence 后恢复或重规划。
- `operation` 成功但 verification failed 时不能结束任务。

### 6.8 总结和记忆写回

处理：

- Mimo 生成 `TaskSummary`，只负责把证据整理成人类可读结论。
- 程序生成 `EvidenceBundle` 和 `MemoryCommitPlan`，长期写回不由 Mimo 直接决定。
- 程序按证据写入三类记录：已有 object memory 的 confirmed/stale/contradicted 更新、fact/event memory、task_record。
- fact/event memory 首版只落盘和管理，不加入 Stage 03 object memory RAG。
- 每条写入都必须有 `EvidenceRef` 和 commit log。

测试：

- 成功查看后更新 confirmed/stale 状态。
- 找不到时记录带 `failure_record_id` 和过期字段的 negative evidence，不猜测新位置。
- 干扰物不写成目标物。
- summary 与 trace 一致。

## 7. 实施阶段与验收

### 7.0 阶段验收硬口径

每个阶段都有两类测试：

- 辅助测试：schema、validator、测试替身、规则校验、mock adapter、单元测试。
- 阶段通过测试：真实调用 `config/api_config.json` 或 legacy fallback 中的 `Mimo / mimo-v2-pro`，并得到结构化输出，通过本阶段程序校验。

阶段验收只能以后者为准。规则通过、测试替身通过、schema 单测通过，都只能说明工程辅助能力可用，不能单独判定该阶段完成。

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
- import boundary 测试：`src/homemaster` 不导入旧链路包名或旧 parser/planner/recovery/graph 口径。
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
- 产品路径只走真实 LLM provider。
- 工程单测可以使用 `httpx.MockTransport` 或测试内 provider stub 验证 prompt、重试、校验和资产写入；这些测试替身不进入 runtime 配置，也不作为阶段验收证据。

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
- 测试替身或规则 parser 通过不计入阶段通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_02/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-02-task-card.md`

### 阶段 3：object_memory-only RAG 检索（BM25 + BGE-M3）

实现：

- 修改 `pyproject.toml`：新增 `bm25s>=0.2`、`jieba>=0.42`。
- 新建 `embedding_client.py`
- 新建 `memory_tokenizer.py`
- 新建 `memory_index.py`
- 新建 `memory_rag.py`
- 修改 `contracts.py`：保留新链路契约 `MemoryRetrievalQuery`、`MemoryRetrievalResult`、`GroundedMemoryTarget`、`PlanningContext`；删除旧 `ObjectMemorySearchPlan`、`ObjectMemoryEvidence`、`Candidate`、`CandidatePool`、`CandidateSelection`、`selected_candidate_id`、`switch_candidate`。当前工程不再保留旧 `src/task_brain` candidate 基线，避免旧候选池口径回流到 V1.2 新链路。
- Mimo 从 `TaskCard` 生成 `MemoryRetrievalQuery`；程序负责校验 query 和执行检索，Mimo 不直接返回 memory hit。
- 只把 scenario 的 `memory.json.object_memory` 转成 `MemoryDocument(text + canonical metadata)`。
- 从 memory documents 自动生成领域词典，供 `JiebaMemoryTokenizer` 做中文分词参考；不把领域词散落硬编码在检索逻辑里。
- 用 tokenized memory documents 建 BM25 index。
- 为 object memory documents 和 retrieval query 调用 BGE-M3 `/v1/embeddings` API 生成 dense embedding；如果 provider config 里保存的是 `https://api.siliconflow.cn/v1/messages`，embedding client 必须派生或读取实际 embeddings endpoint `https://api.siliconflow.cn/v1/embeddings`。
- 执行 hybrid retrieval：BM25 lexical score + BGE-M3 dense score + metadata boost/penalty。
- 预留 embedding cache：按 `document_id + provider + model + document_text_hash + embedding_dim` 复用 document embedding，避免同一 memory 反复调用 BGE-M3 API；Stage 03 可先实现本地 JSON/SQLite cache 或明确以接口形式预留。
- 输出 `MemoryRetrievalResult`
- category prior / episodic 暂不进入主链
- Stage 03 暂不实现 reranker；只预留 reranker stage 字段和接口边界，避免影响当前 BM25+BGE-M3 debug。

建议文件职责：

- `embedding_client.py`：封装 BGE-M3 embedding provider 配置读取、`/v1/embeddings` 请求、错误分类和 secret 过滤；工程单测可注入 deterministic embedder；不得调用 `RawJsonLLMClient.complete_json()`。
- `memory_tokenizer.py`：定义 `MemoryTokenizer` 协议、`JiebaMemoryTokenizer` 和领域词典构建逻辑。
- `memory_index.py`：定义 `MemoryDocument`、object memory 到 document 的序列化、BM25 index、embedding cache / dense vector cache 和 metadata lookup。
- `memory_rag.py`：调用 Mimo 生成 `MemoryRetrievalQuery`，执行 BM25/BGE-M3 检索、融合排序、metadata guardrail、debug 资产写入；预留 optional reranker hook 但默认 disabled。

MemoryDocument 文本模板：

```text
物体记忆。目标类别: <object_category>。别名: <aliases>。
历史位置: <anchor.display_text>。房间: <room_id>。锚点类型: <anchor_type>。
可观察视角: <viewpoint_id>。
置信度: <confidence_level>。记忆状态: <belief_state>。
最近确认时间: <last_confirmed_at>。
```

MemoryDocument metadata 必须至少包含：

```text
document_id
source_type
memory_id
object_category
aliases
room_id
anchor_id
anchor_type
display_text
viewpoint_id
confidence_level
belief_state
last_confirmed_at
document_text_hash
```

Embedding cache 约定：

- cache key 包含 `document_id`、embedding provider、embedding model、`document_text_hash` 和 embedding 维度。
- object memory 内容或 MemoryDocument 模板变化时，`document_text_hash` 变化，必须重新生成该 document embedding。
- Stage 06 写回 object memory 后，必须刷新对应 document embedding 或标记 cache/index stale。
- cache 文件写入 ignored 路径，例如 `.cache/homemaster/embeddings/` 或 SQLite；不得提交 embedding cache、API key 或请求头。
- query embedding 默认不长期缓存，除非后续证明重复 query 很多；Stage 03 只要求缓存 document embedding。

Reranker 约定：

- Stage 03 不引入 reranker，当前只做 BM25 + BGE-M3 + metadata guardrail。
- `MemoryRetrievalResult` 预留 `rerank_score`、`reranker_model`、`ranking_stage` 等可选字段，默认为空。
- 未来只有在“正确 memory 经常进入 top_k 但排不到 top1/top3”、memory 数量明显增大、多源记忆引入导致噪声升高、或检索排序引发频繁验证失败时，再引入 reranker。

领域词典自动来源：

- `object_category`
- `aliases`
- `anchor.display_text`
- `anchor.anchor_type`
- `room_id`
- `viewpoint_id`
- 必要的房间/锚点中英文映射，例如 `kitchen -> 厨房`、`living_room -> 客厅`、`table -> 桌子/餐桌`

Mimo Prompt 要求：

- 输入 `TaskCard` 和 runtime negative evidence 摘要。
- 只输出 `MemoryRetrievalQuery` JSON object。
- `source_filter` 必须是 `["object_memory"]`。
- `query_text` 应包含目标物、别名、位置提示和稳定英文别名，例如“厨房 水杯 杯子 cup kitchen”。
- `target_aliases` 和 `location_terms` 只能来自用户输入、TaskCard、领域词典或常识别名，不允许编造 memory id。
- `excluded_memory_ids` / `excluded_location_keys` 只能来自 runtime negative evidence。

测试：

- dependency smoke：`import bm25s`、`import jieba` 成功。
- memory document 序列化保留 `memory_id`、anchor、viewpoint、confidence、belief_state、last_confirmed_at。
- 领域词典由 memory 自动生成，包含 `水杯`、`药盒`、`厨房餐桌` 等 alias/display text；tokenizer 对这些词分词稳定。
- contract 迁移测试确认 `MemoryRetrievalQuery` / `MemoryRetrievalResult` / `GroundedMemoryTarget` / `PlanningContext` 可序列化、可校验，并确认新 `homemaster` 不再暴露旧 candidate 契约、`selected_candidate_id` 或 `switch_candidate`。
- Mimo 真实生成 `MemoryRetrievalQuery`；工程单测可用 mock Mimo，但不计入阶段验收。
- BGE-M3 query/document embedding 被真实调用；工程单测可用 deterministic test embedder，但不计入阶段验收。
- document embedding cache 命中时不重复调用 BGE-M3；document text hash 变化时只刷新受影响 document。
- BM25 检索命中 alias/location token；BGE-M3 检索命中语义近邻；融合排序保留 score breakdown。
- category / alias 匹配。
- location hint 加权。
- confidence / recency 排序。
- stale 降权。
- negative evidence 排除。
- 确认未读取 category prior 和 episodic memory。
- debug 报告展示完整 Mimo query prompt、Mimo query JSON、memory documents、tokenized query、BM25 hits、BGE-M3 hits、bm25_score、dense_score、metadata_score、final_score 和排除原因。

测试样例：

- case：`memory_document_serialization`
  - 输入：`fetch_cup_retry` 的 `memory.json.object_memory`
  - 预期：生成 `object_memory:mem-cup-1` document；text 包含 `水杯`、`杯子`、`厨房餐桌`、`kitchen_table_viewpoint`；metadata 保留 `memory_id=mem-cup-1`、`anchor_id`、`viewpoint_id`。
- case：`jieba_domain_tokenizer`
  - 输入：从 `check_medicine_success` 和 `fetch_cup_retry` memory 自动生成领域词典
  - 预期：词典包含 `药盒`、`水杯`、`厨房餐桌`、`客厅边桌`；tokenize query `去厨房找水杯` 至少包含 `厨房` 和 `水杯`。
- case：`mimo_memory_retrieval_query`
  - 输入：`TaskCard(target=水杯, location_hint=厨房)` + empty negative evidence
  - 预期：Mimo 返回 `MemoryRetrievalQuery`；`source_filter=["object_memory"]`；`query_text` 包含水杯/杯子/cup 或等价词；`location_terms` 包含厨房/kitchen。
- case：`medicine_object_memory_rag`
  - 输入：`check_medicine_success` 的 `memory.json.object_memory` + `TaskCard(target=medicine_box, location_hint=桌子那边)`
  - 预期：RAG 返回 medicine_box object memory；排序理由包含 BM25 alias/category 命中或 BGE-M3 semantic match，并能回查 canonical memory metadata。
- case：`cup_object_memory_rag`
  - 输入：`fetch_cup_retry` 的 `memory.json.object_memory` + `TaskCard(target=cup, location_hint=厨房)`
  - 预期：`mem-cup-1` 排在前面；理由包含 BM25 对 `水杯/厨房` 的词项命中、BGE-M3 dense score、厨房 location hint 或高置信度。
- case：`negative_evidence_excludes_location`
  - 输入：`object_not_found` 的 object memory + 当前任务 negative evidence 标记 `mem-cup-1` 位置已找过
  - 预期：`mem-cup-1` 进入 excluded，不进入 hits。
- case：`rag_metadata_guardrail`
  - 输入：构造相似文本但 `source_type` 不是 `object_memory` 的 memory document
  - 预期：该 document 不进入 hits，报告写明 source filter 排除原因。
- case：`memory_rag_contract_migration`
  - 输入：`MemoryRetrievalQuery(query_text=..., source_filter=["object_memory"])` 和带 canonical metadata 的 RAG hit
  - 预期：`MemoryRetrievalQuery`、`MemoryRetrievalResult` 均可 `model_dump_json()` / `model_validate_json()`；静态检查确认新 `homemaster` 不再暴露旧 candidate/search-plan 契约。
- case：`embedding_cache_reuses_document_vectors`
  - 输入：同一批 `MemoryDocument` 连续建索引两次
  - 预期：第一次调用 BGE-M3/deterministic embedder 写入 cache；第二次命中 cache，不重复请求同一 document embedding。
- case：`embedding_cache_invalidates_changed_document`
  - 输入：修改 `mem-cup-1` 的 anchor display text 或 confidence 后重建索引
  - 预期：只有 `mem-cup-1` 的 `document_text_hash` 变化并重新 embedding，其它 document 继续复用 cache。
- case：`reranker_not_required_stage_03`
  - 输入：正常 RAG retrieval
  - 预期：`MemoryRetrievalResult` 可以记录 `ranking_stage=bm25_dense_fusion`，`rerank_score` 和 `reranker_model` 为空；阶段通过不依赖 reranker。
- debug 资产：`tests/homemaster/llm_cases/stage_03/<case_name>/`

工程测试文件：

- `tests/homemaster/test_memory_tokenizer.py`
  - 验证领域词典从 memory 自动生成，不从散落硬编码列表生成。
  - 验证 `JiebaMemoryTokenizer` 对 `水杯`、`药盒`、`厨房餐桌`、`客厅边桌` 分词稳定。
  - 验证 query `去厨房找水杯` 的 tokens 至少包含 `厨房` 和 `水杯`。
- `tests/homemaster/test_memory_index.py`
  - 验证 object memory 到 `MemoryDocument` 的 text/metadata 转换。
  - 验证 BM25 index 能在不调用 BGE-M3 的情况下把 alias/location 命中文档排前。
  - 验证 document embedding cache 的复用和 text hash 失效逻辑。
  - 验证缺少 `memory_id`、anchor 或 viewpoint 的 document 不能进入可执行 grounded hits。
- `tests/homemaster/test_memory_rag.py`
  - 用 mock Mimo 返回 `MemoryRetrievalQuery`，用 deterministic embedder 返回固定向量，验证 BM25 + dense 融合排序。
  - 验证 negative evidence 强制排除，即使 BM25/dense 分数很高也进入 `excluded`。
  - 验证 `source_filter` 只允许 `object_memory`，非 object memory document 被 metadata guardrail 排除。
  - 验证 debug `result.md` 包含完整 Mimo query prompt、query JSON、tokenized query、BM25 hits、BGE-M3 hits、score breakdown。
- `tests/homemaster/test_stage_03_memory_rag_live.py`
  - 使用真实 Mimo 生成 `MemoryRetrievalQuery`。
  - 使用真实 BGE-M3 API 生成 query/document embedding。
  - 跑 `medicine_object_memory_rag`、`cup_object_memory_rag`、`negative_evidence_excludes_location` 三个核心 live case。

阶段验证命令：

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/homemaster/test_contracts.py \
  tests/homemaster/test_memory_tokenizer.py \
  tests/homemaster/test_memory_index.py \
  tests/homemaster/test_memory_rag.py

HOMEMASTER_RUN_LIVE_LLM=1 HOMEMASTER_RUN_LIVE_EMBEDDING=1 \
  PYTHONPATH=src .venv/bin/pytest -q \
  tests/homemaster/test_stage_03_memory_rag_live.py -m live_api

.venv/bin/ruff check src/homemaster tests/homemaster
```

验收标准：

- 阶段必须真实调用 Mimo 生成 `MemoryRetrievalQuery`。
- 阶段必须通过 `/v1/embeddings` 真实调用 BGE-M3 provider 完成 query/document embedding；没有 embedding 只能算工程预检，不算 Stage 03 通过。
- BM25 index 必须由 `jieba` tokenized memory documents 构建；不能只靠手写字段过滤假装 BM25。
- 程序只把 `object_memory` 纳入 RAG index。
- `contracts.py` 已完成新链路契约迁移：Stage 03/04 新代码和测试使用 `MemoryRetrievalQuery` / `MemoryRetrievalResult` / `GroundedMemoryTarget` / `PlanningContext`；旧 candidate 契约和字段不再出现在 `src/homemaster`。
- 程序输出 `MemoryRetrievalResult`，每个 hit 都包含 bm25_score、dense_score、metadata_score、final_score、ranking reason 和 canonical metadata。
- document embedding cache 已有复用和失效测试，避免重复调用 BGE-M3 API。
- Stage 03 不要求 reranker；如果实现了 reranker，也不能替代 BM25 + BGE-M3 双路检索证据。
- 每个 case 的 actual 输出与 expected 关键字段一致。
- 如果只靠规则过滤、只靠 BM25、只靠关键词匹配或只靠 BGE-M3 任一路得到正确结果，但没有 BM25 + BGE-M3 双路检索证据，阶段不算通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_03/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-03-memory-rag.md`

后续兼容性：

- Stage 04 不直接消费自然语言 chunk，而是消费 `MemoryRetrievalResult.hits` 中的 canonical metadata；如果 hit 缺少 `memory_id`、anchor 或 viewpoint，必须进入 invalid/excluded。
- Stage 05 的 `retrieve_again` 不是重跑旧规则 search，而是带最新 negative evidence 重跑 Stage 03 RAG retrieval。
- Stage 06 写回 object memory 后必须刷新或标记 RAG index stale，否则后续 retrieval 可能命中过期 embedding。
- Stage 07 scenario matrix 需要在 trace 中记录 retrieval query、BGE-M3 provider/model、BM25 tokenizer、score breakdown 和 source filter，便于复盘。

### 阶段 4：可靠执行记忆判定与 PlanningContext 组装

实现：

- 新建 `grounding.py`
- 新建 `planning_context.py`
- 新建 `stage_04.py` 作为 debug case runner。
- 实现 `assess_hit_reliability(task_card, hit, world, excluded_ids)`，返回 `reliable | weak_lead | unreliable`、reasons、是否需要探索和可选搜索线索。
- 实现 `select_grounded_target(task_card, memory_result, world)`，只选择第一个通过可靠性硬判定的 hit。
- 将 reliable hit 转成 `GroundedMemoryTarget`；没有 reliable hit 时不生成 selected target。
- 组装 Stage 05 使用的 `PlanningContext`：包含 `TaskCard`、retrieval query、memory evidence、selected target 或 null、rejected hits、runtime grounding status、world summary、planning notes。
- Stage 04 不调用 Mimo，不做 LLM soft judge，不要求 reranker，不修改 Stage 03 排序；排序来源仍是 Stage 03 的 BM25+BGE-M3 融合结果，只作为可靠性检查顺序。

可靠执行记忆硬标准：

- hit 不在 `excluded`，`invalid_reason is None`，`executable is True`。
- 必须有 `memory_id`、`room_id`、`anchor_id`、`viewpoint_id`。
- `viewpoint_id` 存在于 `world["viewpoints"]`。
- `anchor_id` 存在于 `world["furniture"]`，且 furniture 的 `viewpoint_id` 与 hit 一致。
- 目标物必须匹配：`object_category`、`aliases`、或 ranking reasons 中有 target alias/category match。
- 如果 `TaskCard.location_hint` 明确指向房间，hit 不能明显冲突。
- 如果 `TaskCard.location_hint` 明确指向锚点类型，例如“桌子/餐桌/边桌”或“柜子/药柜/橱柜”，hit 的 anchor/display text 不能明显冲突；冲突 hit 降为 `weak_lead`，不能成为 reliable。
- `confidence_level=low` 或 `belief_state=stale` 默认只能成为 `weak_lead`，不能成为 reliable。

`weak_lead` 用法：

- 不生成 `selected_target`。
- 保留在 `rejected_hits`、assessment records 或 `planning_notes` 里。
- Stage 05 可以把它当作探索线索，例如“水杯相关但 stale/low confidence/位置冲突，先搜索附近或用户明说的位置”。

测试：

- reliable hit 真实存在，viewpoint 可导航，anchor 与 viewpoint 一致。
- selected target 能回溯到 canonical object memory，而不是只来自 RAG chunk 文本。
- 缺 anchor/viewpoint、viewpoint 不存在、anchor 与 viewpoint 不匹配的 hit 不能成为 selected target。
- 被 negative evidence 排除的 hit 不能成为 selected target。
- target metadata 不匹配的 dense-only 高分 hit 不能成为 selected target。
- `low confidence`、`stale`、location conflict 降为 `weak_lead`，不作为 selected target。
- `桌子/柜子` 等锚点提示冲突降为 `weak_lead`，不作为 selected target。
- 没有 reliable hit 时输出合法 `PlanningContext(selected_target=None)`，并明确要求 Stage 05 探索/寻找。
- PlanningContext 足够支持 Stage 05 直接生成 OrchestrationPlan。

测试样例：

- case：`ground_medicine_target`
  - 输入：`check_medicine_success` 的 RAG memory hits + world
  - 预期：用户提示是“桌子那边”时，厨房药柜 hit 因 `anchor_hint_conflict` 降为 `weak_lead`；如果客厅边桌 hit 通过 world 校验，则生成 `GroundedMemoryTarget(memory_id=mem-medicine-2, viewpoint_id=living_side_table_viewpoint)`；不调用 Mimo 目标选择。
- case：`ground_cup_target`
  - 输入：`fetch_cup_retry` 的 RAG memory hits + world
  - 预期：优先选择厨房水杯 hit，selected target 的 `memory_id`、`anchor_id`、`viewpoint_id` 全部来自 canonical metadata。
- case：`skip_invalid_viewpoint_hit`
  - 输入：top hit 缺失或引用不存在的 viewpoint，第二个 hit 可执行
  - 预期：top hit 进入 rejected hits，原因包含 `viewpoint_not_found`；如果第二个 hit reliable，程序选择第二个 hit。
- case：`reject_missing_execution_fields`
  - 输入：hit 缺 `memory_id` 或 `anchor_id`
  - 预期：status=`unreliable`，不能成为 selected target。
- case：`location_conflict_becomes_weak_lead`
  - 输入：TaskCard location hint 为 `厨房`，hit 在 `living_room`
  - 预期：status=`weak_lead`，不生成 selected target，notes 保留搜索线索。
- case：`anchor_hint_conflict_becomes_weak_lead`
  - 输入：TaskCard location hint 为 `桌子那边`，hit 在 `厨房药柜`
  - 预期：status=`weak_lead`，原因包含 `anchor_hint_conflict`，不生成 selected target。
- case：`low_confidence_or_stale_becomes_weak_lead`
  - 输入：hit target 匹配但 `confidence_level=low` 或 `belief_state=stale`
  - 预期：status=`weak_lead`，不作为可靠执行记忆。
- case：`dense_only_without_metadata_target_match_is_unreliable`
  - 输入：dense score 高，但没有 target alias/category metadata match
  - 预期：status=`unreliable`。
- case：`ungrounded_no_memory_context`
  - 输入：空 `MemoryRetrievalResult.hits`
  - 预期：`PlanningContext.selected_target is None`，`runtime_state_summary.grounding_status="ungrounded"`，notes 写明需要探索寻找。
- case：`negative_evidence_excluded_hit_never_selected`
  - 输入：Stage03 `negative_evidence_excludes_location` actual
  - 预期：被排除的 memory 不会 selected；如果剩余 hit 不可靠，生成 ungrounded context。
- case：`planning_context_for_orchestration`
  - 输入：`TaskCard` + `MemoryRetrievalResult` + world summary
  - 预期：`PlanningContext` 包含 task、query、selected target 或 null、memory evidence、rejected hits、runtime summary、world summary 和 planning notes；Stage 05 prompt 不需要读取原始 memory JSON。
- debug 资产：`tests/homemaster/llm_cases/stage_04/<case_name>/`

工程测试文件：

- `tests/homemaster/test_grounding.py`
  - 覆盖 reliable、weak_lead、unreliable 的关键分支。
  - 覆盖缺失 execution fields、viewpoint 不存在、anchor 不匹配、location conflict、low confidence、stale、target mismatch。
- `tests/homemaster/test_planning_context.py`
  - 覆盖 grounded 和 ungrounded 两种 `PlanningContext` 序列化。
  - 覆盖 ungrounded notes / runtime summary 明确指向探索寻找。
- `tests/homemaster/test_stage_04_grounding_context.py`
  - 读取 Stage03 debug actual + scenario world，生成 Stage04 debug assets。
  - 验证 result.md 包含 TaskCard、Stage03 hits、hit assessments、selected target 或 ungrounded reason、PlanningContext JSON。

阶段验证命令：

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/homemaster/test_contracts.py \
  tests/homemaster/test_grounding.py \
  tests/homemaster/test_planning_context.py \
  tests/homemaster/test_stage_04_grounding_context.py \
  tests/homemaster/test_import_boundaries.py

.venv/bin/ruff check src/homemaster tests/homemaster
```

验收标准：

- 有可靠执行记忆时，`GroundedMemoryTarget` 由程序从 Stage 03 RAG hits 真实生成。
- selected target 只接受带 `memory_id`、anchor、room、viewpoint、target metadata match 且通过 world 校验的 reliable hit。
- 没有可靠执行记忆时，不失败、不伪造地点，输出 `selected_target=None` 的 `PlanningContext`。
- Stage 05 可以通过 `PlanningContext` 明确区分 `grounded` 和 `ungrounded`：前者有可靠导航/观察目标，后者需要探索寻找。
- Stage 04 不要求 Mimo 输出单独选择结果；也不使用 LLM soft judge 或 reranker。
- top1 排序不等于可靠；low confidence、stale、location conflict、anchor hint conflict、metadata target mismatch 都必须有测试覆盖。
- Debug 报告可解释每个 hit 为什么 reliable、weak_lead 或 unreliable。
- `PlanningContext` 是 Stage 05 的唯一上下文入口，包含编排所需最小信息。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_04/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-04-grounding-context.md`

### 阶段 5：高层子任务编排与 skill 执行闭环

实现：

- 新建 `orchestrator.py`
- 新建 `orchestration_validator.py`
- 新建 `skill_registry.py`
- 新建 `skill_selector.py`
- 新建 `execution_state.py`
- 新建 `executor.py`
- 新建 `verifier.py`
- 新建 `failure_log.py`
- 新建 `recovery.py`
- 兼容性修复优先：Stage 05 第一批改动先迁移公共契约和 `test_contracts.py`，让代码契约和新链路一致。
- `OrchestrationPlan` 不再要求 Mimo 输出 `selected_target`；`Subtask` 改为 intent 型字段；`StepDecision` 从 `module/module_input` 迁移到 `selected_skill/skill_input`，且不包含 `verify_after`；`ModuleExecutionResult` 从 `module` 迁移到 `skill`，并增加 `skill_output` 承载 skill 直接产出；新增 `ExecutionState/SubtaskRuntimeState`；删除或弃用 `EmbodiedActionPlan`。
- 支持高层 intent 型 subtask。
- 高层计划由 LLM 生成，程序只做轻量校验：JSON/schema、必要字段、依赖引用、禁止旧 candidate 字段、禁止伪造 `selected_target` 或 memory id。
- 程序不再用固定模板强校验高层拆解质量；模型拆解能力通过真实 Mimo case、可读 debug report 和执行期 verification 闭环验证。
- 高层计划不绑定 skill，执行到某个 subtask 时再让 Mimo 从可用 skill manifest 中选择。
- 首版 skill manifest 固定为 3 个：`navigation`、`operation`、`verification`；其中 `navigation` 和 `operation` 是 Mimo 可选 action skill，`verification` 是程序自动后置 skill。
- 首版不对 skill 做 RAG 检索或 embedding 检索；程序直接提供 manifest，Mimo 只能从可选候选中选择。未来 skill 数量明显增多后，再考虑 skill manifest 检索。
- 每次 `navigation` 或 `operation` 后都由程序自动触发 `verification`；Mimo 不手动选择 `verification`。
- Stage 05 使用 `ExecutionState` 记录当前子任务、依赖状态、retry_count、最近 observation、最近 verification、negative evidence、目标物是否可见、是否已拿起物品、用户位置和当前机器人位置。
- `navigation` 可用于找目标物或移动到指定位置；输入只需要目标物名称或具体位置描述，后续接 VLN，当前用 mock navigation 和结构化 observation。首版不新增 `find_user`，如果需要回到用户身边，使用 runtime 记录的用户位置并调用 `go_to_location`。
- `operation` 可用于拿起、放下、递送；Stage 05 只传子任务意图、目标、接收对象和当前观察，operation skill 内部选择原子动作并生成 VLA 指令，当前用 mock operation 和结构化 observation。
- `verification` 后续调用 VLM 判断当前子任务或整个任务是否完成；VLM 图片接口保留但默认 disabled，本阶段不依赖真实视觉。
- 执行失败、验证失败、模型输出失败和前置条件失败都必须写 `FailureRecord`，记录失败原因、输入、输出、verification 结果、negative evidence 和 retry_count。
- `FailureRecord` 预留 `event_memory_candidate`，未来可写入 event memory 并支持 RAG 检索；Stage 05 首版只保存在 runtime/debug/trace，不做 event memory 检索。
- 重规划时必须把 FailureRecord 列表、negative evidence、ExecutionState 和当前未完成 subtask 放进 prompt，避免 Mimo 重复规划已经验证失败的位置或动作。
- Stage 05 中 `OrchestrationPlan`、`StepDecision`、`RecoveryDecision` 的非 JSON 输出最多再让 Mimo 修复两次；三次仍失败则写 FailureRecord 并进入 ask_user 或 finish_failed。
- Stage 05 只消费 `PlanningContext`，不得读取或生成旧 `CandidatePool`、`CandidateSelection`、`selected_candidate_id`、`switch_candidate`。
- Stage 05 首版不支持 `switch_target`；如果 grounded target 被验证失败或不可达，只能通过 `retrieve_again` 带最新 negative evidence 回 Stage 03 重新检索，不能让 Mimo 直接编 `next_target_id`。
- `grounded` 路径：当 `PlanningContext.selected_target` 存在时，Mimo 可以把 selected target 的 room、anchor、memory evidence 作为提示，但高层计划和 skill call 不能编造新 memory target；`navigation` 输入以目标物名称、房间提示、锚点提示为主，不强制直接导航到 viewpoint。
- `ungrounded` 路径：当 `PlanningContext.selected_target=None` 且 `runtime_state_summary.grounding_status="ungrounded"` 时，Mimo 不能编造 memory target；计划必须先探索/寻找/观察或追问。
- 每个子任务是否完成不靠高层计划自称完成，而是靠 action 后自动 verification；整个任务结束前也需要 final verification。

测试：

- 查看类计划。
- 取物类计划。
- skill 选择。
- skill 参数校验。
- 公共契约迁移测试。
- 高层计划轻量 validator 测试。
- operation skill 输入 schema 校验，确认 Stage 05 不直接要求 Mimo 输出原子动作。
- ModuleExecutionResult / skill result 契约包含 `skill_output`，可稳定保存 `vla_instruction`、`planned_atomic_actions` 和可选 `image_input`。
- ExecutionState / SubtaskRuntimeState 契约可序列化，能记录 subtask 状态、依赖满足情况、retry_count、最近 observation、最近 verification、negative evidence、目标可见性、持有物和用户位置。
- FailureRecord 契约可序列化，包含 failure_type、failed_reason、skill_input、skill_output、verification_result、negative_evidence、retry_count 和 event_memory_candidate。
- success criteria 必要字段存在。
- 依赖关系引用合法且无循环。
- grounded / ungrounded 两条路径都必须覆盖。
- navigation / operation 成功但 verification 失败不能推进。
- 找不到目标物时写 negative evidence，并触发恢复或重规划。
- verification failed、skill failed、precondition failed、model output invalid 都要写 FailureRecord。
- Stage05 非 JSON LLM 输出会触发最多两次修复重试；仍失败时不能继续假执行。
- replan prompt 必须包含 FailureRecord 和 negative evidence。
- `retrieve_again` 是替换 grounded target 的唯一首版入口，不能输出 `switch_target` 或 `next_target_id`。
- 真实 Mimo 取物交付 case 应覆盖找物、拿起、找用户、放下、确认交付；这作为 live case 质量验收和 prompt 迭代依据，不由程序固定模板强判。
- Mimo 手动选择 `verification` 会被拒绝，因为 verification 是程序自动后置校验。
- validator 拒绝任何包含 `selected_candidate_id`、`candidate_id` 或 `switch_candidate` 的 Stage 05 输出。

测试样例：

- case：`check_medicine_orchestration`
  - 输入：查看药盒 TaskCard + PlanningContext(selected_target=药盒 memory target)
  - 预期：Mimo 高层计划自然表达“找到/观察药盒”和“确认药盒是否存在”；执行期不会调用拿起或放下动作。
- case：`fetch_cup_orchestration`
  - 输入：取水杯 TaskCard + PlanningContext(selected_target=水杯 memory target)
  - 预期：Mimo 高层计划自然表达“找到水杯、拿起水杯、找到用户、放下水杯、确认交付完成”；如果拆解不理想，记录 debug report 并迭代 prompt。
- case：`orchestration_light_validator_rejects_only_structural_errors`
  - 输入：构造缺 `intent`、依赖不存在、循环依赖、包含旧 candidate 字段、伪造 `selected_target` 的高层计划。
  - 预期：这些结构性错误被拒绝；但高层 intent 使用自然语言或出现原子动作字样不直接失败。
- case：`find_cup_selects_navigation_skill`
  - 输入：当前 subtask 为“找到水杯”，ExecutionState 尚未看到水杯。
  - 预期：Mimo 从可选 action skill 中选择 `navigation`，参数包含 `goal_type=find_object`、`target_object=水杯` 和可选 `room_hint/anchor_hint`。
- case：`go_to_location_selects_navigation_skill`
  - 输入：当前 subtask 为“去厨房桌子旁”，ExecutionState 尚未在目标位置。
  - 预期：Mimo 选择 `navigation`，参数包含 `goal_type=go_to_location` 和 `target_location=厨房桌子旁`。
- case：`go_to_recorded_user_location_for_delivery`
  - 输入：当前 subtask 为“找到用户”，ExecutionState 已记录 `user_location=客厅沙发旁`。
  - 预期：Mimo 选择 `navigation`，参数包含 `goal_type=go_to_location` 和 `target_location=客厅沙发旁`；首版不要求 navigation schema 支持 `find_user`。
- case：`pick_cup_requires_verified_visible_target`
  - 输入：当前 subtask 为“拿起水杯”，但 ExecutionState 没有 `target_object_visible=true`。
  - 预期：程序拒绝 `operation` skill call，不能跳过验证直接拿取。
- case：`pick_cup_operation_generates_vla_instruction`
  - 输入：当前 subtask 为“拿起水杯”，且 verification 已确认水杯可见。
  - 预期：Mimo 选择 `operation`，参数包含 `subtask_intent=拿起水杯`、`target_object=水杯` 和当前 observation；operation skill 内部生成 VLA 指令和可读 planned atomic actions，operation 后必须再 verification。
- case：`deliver_cup_requires_find_user_and_place`
  - 输入：取水杯交付给用户。
  - 预期：拿起水杯后还必须执行“找到用户”和“放下/交付水杯”，不能把拿起水杯当作任务结束。
- case：`high_level_atomic_words_do_not_fail_plan`
  - 输入：构造包含 `close_gripper` 的 LLM 输出
  - 预期：高层计划不因原子动作字样直接失败；执行到 `operation` 时，Stage 05 不直接执行这些字样，而是把子任务和 observation 交给 operation skill。
- case：`verification_uses_subtask_or_task_scope`
  - 输入：navigation 或 operation 返回结构化 observation。
  - 预期：程序自动构造 verification input，包含 `scope=subtask|task`、当前 intent、success criteria、observation 和可选 disabled image input。
- case：`manual_verification_skill_rejected`
  - 输入：Mimo 在 StepDecision 中选择 `verification`。
  - 预期：程序拒绝该 StepDecision，并说明 verification 由程序在 action skill 后自动调用。
- case：`orchestration_plan_does_not_generate_selected_target`
  - 输入：Mimo 输出的 OrchestrationPlan 包含 `selected_target`。
  - 预期：Stage05 validator 拒绝或剥离该字段；执行上下文只使用 `PlanningContext.selected_target`。
- case：`ungrounded_exploratory_orchestration`
  - 输入：`PlanningContext(selected_target=None, grounding_status=ungrounded, needs_exploratory_search=true)`
  - 预期：Mimo 生成探索/观察/追问型计划，不伪造 `GroundedMemoryTarget` 或不存在的 memory id。
- case：`navigation_success_verification_fail_recovery`
  - 输入：`navigation` 返回 success，但结构化 observation 里没有目标水杯。
  - 预期：verification failed，当前 subtask 不推进，写入 negative evidence，进入 `RecoveryDecision`。
- case：`execution_state_blocks_unmet_dependencies`
  - 输入：当前计划中“拿起水杯”依赖“找到水杯”，但 ExecutionState 里“找到水杯”尚未 verified。
  - 预期：程序不会把“拿起水杯”交给 Mimo 作为当前可执行 subtask，也不会允许 operation 跳步执行。
- case：`execution_state_records_visibility_and_holding`
  - 输入：navigation 后 verification 确认水杯可见，operation 后 verification 确认水杯已拿起。
  - 预期：ExecutionState 更新 `target_object_visible=true` 和 `held_object=水杯`，后续交付子任务可读取这些状态。
- case：`stage05_non_json_output_retries_twice`
  - 输入：Mimo 对 StepDecision 或 RecoveryDecision 连续返回非 JSON 文本。
  - 预期：程序追加修复提示最多再试两次；第三次仍失败时写 `FailureRecord(failure_type=model_output_invalid)`，不合成假 StepDecision。
- case：`failure_record_captures_operation_verification_failure`
  - 输入：operation 返回 success，但 verification 判断“未拿起水杯”。
  - 预期：写 FailureRecord，包含 subtask、operation skill_input、skill_output、verification failed_reason 和 retry_count。
- case：`replan_prompt_contains_failure_records`
  - 输入：水杯在厨房桌子旁未找到，RecoveryDecision 选择 `replan`。
  - 预期：重规划 prompt 包含该 FailureRecord 和 negative evidence，Mimo 不应重复规划同一已失败位置。
- case：`retrieve_again_replaces_failed_grounded_target`
  - 输入：当前 selected target 已验证失败，RecoveryDecision 选择重新检索。
  - 预期：程序带 negative evidence 回 Stage 03 生成新的 PlanningContext；RecoveryDecision 不允许包含 `switch_target` 或 `next_target_id`。
- case：`failure_record_event_memory_candidate_is_preserved`
  - 输入：构造 failed_search FailureRecord。
  - 预期：`event_memory_candidate` 可序列化保存，但 Stage 05 不执行 event memory 写入或检索。
- debug 资产：`tests/homemaster/llm_cases/stage_05/<case_name>/`

验收标准：

- 查看类和取物类计划都必须来自 Mimo 实际输出。
- Mimo 输出通过轻量 `OrchestrationPlan` 校验。
- Stage05 公共契约和当前计划口径一致；如果迁移了 `contracts.py`，对应 contract 单测必须同步更新。
- 高层计划不绑定 skill，不出现 `navigation`、`operation`、`verification`。
- 高层计划里即使出现原子动作字样，首版也不作为独立失败条件；真正执行时，Stage 05 不直接执行这些字样，operation skill 内部选择原子动作并生成 VLA 指令。
- grounded case 中，skill call 参数只能使用 TaskCard、PlanningContext、world summary 或 ExecutionState 中已有的信息，不能编造新 memory target。
- ungrounded case 中，OrchestrationPlan 不能引用伪造 memory target；探索和寻找只能使用 TaskCard 明确 location hint、world summary 或 ExecutionState。
- 执行期 Mimo 只能从可选 action skill 中选择；首版为 `navigation` 和 `operation`。
- `verification` 只能由程序自动调用，不能由 Mimo 手动选择。
- `navigation` / `operation` 返回 success 后，必须经过 `verification` 才能推进 subtask。
- 每个子任务完成后都要有 verification 结果；verification failed 时当前 subtask 不推进。
- `operation` 只能在目标物或交付对象满足前置条件后调用；Stage 05 校验 operation 输入 schema，但不校验 operation 内部原子动作序列。
- ModuleExecutionResult 必须有 `skill_output`，operation 的 VLA 指令和内部 planned atomic actions 不应只散落在 `evidence` 里。
- ExecutionState 必须是执行期唯一状态账本：选择下一 subtask、判断依赖、校验 operation 前置条件、记录 retry_count、维护 negative evidence 都从这里读写。
- 任何失败都必须有 FailureRecord；debug `result.md` 必须展示失败发生在哪个 subtask、哪个 skill、失败原因、verification 结果、negative evidence 和下一步恢复动作。
- Stage05 非 JSON LLM 输出最多修复两次；超过次数后必须安全失败或追问用户，不能静默跳过。
- `retrieve_again` 必须带最新 negative evidence 回 Stage 03；`replan` 必须带 FailureRecord 列表重新生成后续计划；首版恢复动作不能包含 `switch_target` 或 `next_target_id`。
- FailureRecord 的 `event_memory_candidate` 字段保留未来事件记忆扩展，但当前 Stage 05 验收不要求 event memory RAG。
- Stage 05 输出中不得出现 `selected_candidate_id`、`candidate_id`、`CandidatePool`、`CandidateSelection` 或 `switch_candidate`；旧 candidate 逻辑已从当前工程入口清理，不允许进入 V1.2 新链路。
- success criteria 存在，依赖关系结构合法；复杂任务拆解质量以真实 Mimo case、debug report 和 execution verification 结果判断。
- 手动选择 verification、伪造 selected_target、operation 输入 schema 越界的拒绝是辅助测试；不能替代真实 Mimo 计划和 skill 选择通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_05/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-05-skill-execution-loop.md`

### 阶段 6：任务总结与事实记忆写回

说明：原计划中的独立阶段 6-8 已合并进阶段 5。阶段 6 直接接 Stage 05 的执行结果，做任务总结、证据整理和事实记忆写回。Stage 06 的重点不是“把所有结果都塞进 object memory”，而是把成功、失败、未确认事实分别写到合适的位置，形成以后可被机器人当经验使用的事实记忆。

输入：

- `TaskCard`
- `PlanningContext`
- `OrchestrationPlan`
- `ExecutionState`
- `ModuleExecutionResult[]`
- `VerificationResult[]`
- `FailureRecord[]`
- `negative_evidence[]`

记忆分层：

- `object_memory`：物体位置记忆。首版仍是 Stage 03 当前唯一检索源。
- `fact_memory` / `event_memory`：任务过程中发生过的事实和事件。首版先写入和管理，不加入 Stage 03 检索。
- `task_record`：一次任务的人类可读摘要和审计记录。

实现：

- 新建 `summary.py`
- 新建 `memory_commit.py`
- 新建 `fact_memory.py`
- 新建 `task_record.py`
- Stage 06 前置迁移 `contracts.py`：补齐 `EvidenceRef`、`EvidenceBundle`、`ObjectMemoryUpdate`、`FactMemoryWrite`、`TaskRecord`，并扩展 `TaskSummary` / `MemoryCommitPlan`。
- Stage 06 前置更新 Stage 05 结果整理：给 `VerificationResult`、`ModuleExecutionResult`、`FailureRecord`、关键 trace event 生成稳定 evidence id；如果 Stage 05 原始对象没有 id，Stage 06 ingestion 层必须生成可重复的 id。
- Mimo 生成 `TaskSummary`，但必须基于 Stage 05 trace、verified facts 和 FailureRecord，不能编造成功结果。
- Mimo 可以生成候选事实摘要，但长期写回由程序基于 `EvidenceBundle` 生成并校验 `MemoryCommitPlan`。
- 程序生成 `EvidenceBundle`，整理可写入记忆的证据来源。
- 程序生成并校验 `MemoryCommitPlan`，包括 evidence refs、事实类型、过期策略、写回边界和 index stale 标记。
- 成功任务可以更新已有 object memory 的 `last_confirmed_at`、`confidence_level`、`belief_state`。
- 失败任务不能写猜测位置；只能写可靠 negative evidence、fact/event memory 和 task record。
- fact/event memory 首版不进入 Stage 03 RAG 检索，避免失败经验污染当前 object memory 检索。
- object memory 写回后必须标记 RAG index stale，或同步更新对应 memory document embedding。
- 新增 `.gitignore` 规则忽略 `var/homemaster/memory/`，避免运行期事实记忆、用户位置、失败记录和 commit log 进入仓库。
- 新增 `RuntimeMemoryStore` 概念：Stage 03 测试仍可读取 `data/scenarios/*/memory.json`；产品/集成运行时优先读取 `var/homemaster/memory/object_memory.json` 或由 store 合并 scenario base memory + runtime object memory overlay。否则 Stage 06 写回后，下一轮 Stage 03 检索会看不到最新 object memory。
- 事实写入建议路径：
  - `var/homemaster/memory/object_memory.json`
  - `var/homemaster/memory/fact_memory.jsonl`
  - `var/homemaster/memory/task_records.jsonl`
  - `var/homemaster/memory/commit_log.jsonl`

写回规则：

- 每条 `object_memory_update`、`fact_memory_write`、`task_record` 都必须引用 `EvidenceRef`。
- 可接受证据来源包括：`verification_result`、`failure_record`、`skill_result`、`observation`、`selected_target`、Stage 05 trace event。
- 没有证据引用的候选必须进入 `skipped_candidates`。
- “看到了目标物”可以更新 object memory confirmed 信息。
- “在某位置没看到目标物”只写成 fact/event memory 或 negative evidence；如果它反证了旧 object memory，可以把旧记忆标记为 `stale` 或 `contradicted`，不能直接删除。
- “拿取/放下/交付失败”写成 event memory，不更新物体位置。
- “Mimo 非 JSON / schema 失败 / provider 失败”属于系统失败，只写 task record 和 debug，不能写环境事实。
- 失败事实必须有时间、目标、位置或子任务边界，避免永久化。例如写“本次任务中，在厨房餐桌对应视角没有观察到水杯”，不要写“水杯不在厨房”。
- 过期策略首版写入 `expires_at` 或 `stale_after`，让 negative fact 可以随时间变弱。
- `negative_evidence` 写入前必须补齐 `failure_record_id`、`location_key` 或 `memory_id`、`created_at`、`expires_at` 或 `stale_after`；缺字段时写入 `skipped_candidates` 或降级为 task record，不直接进入长期 fact memory。
- fact/event memory 首版只存储和管理，不纳入 Stage 03 RAG；后续若要引入事件记忆检索，需要单独建立 event memory index 和过滤策略。
- `commit_log.jsonl` 记录每次 commit 的 `commit_id`、输入 task id、写入条数、skipped candidates、index stale ids 和时间，方便回滚和审计。

测试：

- `TaskSummary` / `MemoryCommitPlan` 契约与 Stage 06 计划字段一致。
- `EvidenceBundle` 能从 Stage 05 的 verification、skill result、failure record、trace event 中生成稳定 `EvidenceRef`。
- 成功更新 object memory confirmed 信息。
- 失败不写猜测位置。
- 干扰物不写成目标物。
- summary 与 trace 一致。
- FailureRecord 可以转成 fact/event memory candidate。
- 模型输出失败只写 task record，不写 object/fact 环境记忆。
- negative evidence 写入包含 `memory_id`、`location_key`、`failure_record_id` 和过期字段。
- object memory 写回后 `index_stale_memory_ids` 包含被更新的 memory id。
- runtime memory store 写入后，下一轮 Stage 03 可以通过 runtime object memory path 或 overlay 读到更新后的 object memory。
- `var/homemaster/memory/` 被 `.gitignore` 忽略。

测试样例：

- case：`check_medicine_summary_memory`
  - 输入：`check_medicine_success` 的成功 trace 和 verified facts
  - 预期：Mimo 生成 `TaskSummary(result=success)`；`MemoryCommitPlan` 更新 verified object memory，并写入 task_record。
- case：`fetch_cup_success_fact_memory`
  - 输入：Stage 05 取水杯成功 trace
  - 预期：写入“水杯在厨房餐桌被观察到”和“水杯已交付用户”的 fact/task record；只更新已有 `mem-cup-1` confirmed 信息。
- case：`object_not_found_summary_memory`
  - 输入：`object_not_found` 的失败 trace
  - 预期：summary 写清未找到；不写新位置；fact_memory 写“本次在某位置未观察到目标”；negative evidence 可记录；相关 object memory 可标记 stale/contradicted，但不能删除。
- case：`distractor_summary_memory`
  - 输入：`distractor_rejected` 的失败 trace
  - 预期：干扰物不写入目标 object memory。
- case：`operation_failed_event_memory`
  - 输入：水杯可见但拿起失败的 FailureRecord
  - 预期：写 event memory 表达操作失败，不更新 object memory 位置。
- case：`model_output_invalid_task_record_only`
  - 输入：Mimo 三次非 JSON 的 FailureRecord
  - 预期：只写 task_record/debug，不写 object_memory 或 fact_memory 环境事实。
- debug 资产：`tests/homemaster/llm_cases/stage_06/<case_name>/`

验收标准：

- TaskSummary 必须来自 Mimo 真实输出。
- MemoryCommitPlan 通过 verified evidence 和 failure evidence 校验。
- 成功事实、失败事实、系统失败被写到不同位置，不互相污染。
- fact/event memory 已落盘但暂时不加入 Stage 03 object memory RAG。
- 每条长期记忆写入都有证据引用和 commit log。
- 失败记忆不能伪造成永久位置结论。
- 仅规则生成 summary 不算阶段通过；`TaskSummary` 必须有真实 Mimo 输出。
- `MemoryCommitPlan` 由程序生成和校验，不能由 Mimo 自由决定长期写回内容。
- Stage 06 写回后的 object memory 必须有明确路径被下一轮 Stage 03 runtime 检索使用，不能只写到孤立文件。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_06/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-06-summary-memory.md`

### 阶段 7：Claude Code / Hermes 风格 CLI、Doctor 与矩阵收口

目标：

- 把 `homemaster` 做成用户启动 HomeMaster 的主入口，而不是只做开发测试命令集合。
- 参考 Claude Code / Hermes 这类 CLI 体验：用户输入 `homemaster` 后进入对话；输入 `homemaster doctor` 后做环境、配置、模型和运行期记忆体检。
- 保留并强化 5 个端到端 scenario matrix，用来真实压测 Stage02 -> Stage06 整条 pipeline；它们主要用于批量验收和 CI，不作为普通用户入口。

实现：

- 补齐 `homemaster` CLI 的三层入口：
  - `homemaster`：默认启动交互式对话。用户直接输入自然语言任务，系统按 Stage02 -> Stage03 -> Stage04 -> Stage05 -> Stage06 跑完整链路，并在终端展示关键进度、失败原因和 debug/report 路径。
  - `homemaster run --utterance "<用户任务>"`：单轮非交互执行，适合脚本、CI 和复现 bug。Stage07 首版需要补齐场景上下文参数：
    - `--scenario <scenario_name>`：指定 `data/scenarios/<scenario_name>/`。
    - `--world <path>`：可选覆盖 world.json；默认使用 scenario 目录下的 world。
    - `--memory <path>`：可选覆盖 base object memory；默认使用 scenario 目录下的 memory。
    - `--runtime-memory-root <path>`：运行期 object/fact/task record 写入根目录。
    - `--debug-root <path>`：debug case 和 report 写入根目录。
    - `--run-id <id>`：一次任务/场景运行的稳定 id，用于 trace、task record、memory root 隔离。
    - `--live-models` / `--no-live-models`：显式声明是否调用真实 Mimo / BGE-M3；Stage07 验收用 `--live-models`。
    - `--mock-skills`：首版固定启用，明确 navigation / operation / verification 仍是 mock skill，不接真实机器人、VLA 或 VLM。
  - `homemaster doctor`：环境体检，不执行完整任务。默认只做本地检查；加 `--live` 时才调用真实 Mimo / BGE-M3 做最小 API smoke。
- 保留已有开发命令：
  - `contract-smoke`、`understand` 等 Stage01/02 命令继续可用，避免破坏已完成测试。
  - 后续可放入 help 文案的“developer commands”区域，但首版不强行重命名，减少兼容风险。
- 新增 `doctor` 检测项：
  - Python 和 `.venv` 是否来自 HomeMaster 项目环境。
  - `homemaster` 包、CLI、Pydantic 契约是否可导入。
  - `bm25s`、`jieba`、`httpx`、`typer` 等依赖是否可导入。
  - `DEFAULT_CONFIG_PATH` 解析结果是否可用：优先检查 `config/api_config.json`，不存在时才报告使用 legacy fallback；provider 是否包含 chat LLM 和 embedding provider。输出时只能显示 provider/model/protocol/config source，不能显示 API key。
  - chat LLM 是否走 messages/chat 协议，BGE-M3 embedding 是否走 `/v1/embeddings`，避免再把 embedding 模型误接到 chat client。
  - `var/homemaster/memory/`、`.cache/homemaster/embeddings/`、`plan/V1.2/test_results/` 是否被 `.gitignore` 忽略。
  - runtime object memory store 是否可读写，Stage03 是否能读到 runtime overlay。
  - Stage05/06 需要的 debug/report 目录是否可创建。
  - `src/homemaster` 是否仍不导入旧链路包名或旧 parser/planner/recovery/graph 口径。
- `doctor` 输出格式：
  - 终端展示 `PASS / WARN / FAIL` 表格。
  - 每个失败项给出“发生了什么、影响什么、建议怎么修”。
  - 可选 `--json` 输出机器可读报告，便于脚本和 CI。
  - 可选 `--live` 做真实 provider smoke：Mimo 最小 JSON 输出、BGE-M3 最小 embedding 输出；不保存 API key、请求头或完整 secret config。
- 交互式 `homemaster` 行为：
  - 启动时先跑轻量 doctor。只有本地环境严重失败时阻止进入；provider live 检查默认不自动跑，只给提示。
  - 支持用户输入自然语言任务，例如“去厨房找水杯，然后拿给我”。
  - 显示当前阶段：任务理解、记忆检索、可靠记忆判定、计划、执行/验证、总结写回。
  - 每轮任务结束后展示：任务结果、失败原因或成功证据、记忆写回摘要、debug case 路径、task record 路径。
  - 预留 slash command：`/doctor`、`/status`、`/debug`、`/exit`。首版只要求 `/doctor` 和 `/exit` 稳定可用。
- scenario matrix 收口：
  - 新建或更新 `scripts/run_homemaster_scenarios.sh`，内部优先调用 `homemaster run` 或专用 `homemaster eval scenarios`，而不是绕过 CLI 直接拼 Python 私有函数。
  - 5 个端到端场景必须覆盖任务理解、object memory RAG、可靠执行记忆判定、高层计划、skill 执行/验证/恢复、任务总结和记忆写回。
  - 每个 scenario case 必须使用独立运行期目录，避免 Stage06 写回互相污染：
    - `var/homemaster/runs/<run_id>/memory/object_memory.json`
    - `var/homemaster/runs/<run_id>/memory/fact_memory.jsonl`
    - `var/homemaster/runs/<run_id>/memory/task_records.jsonl`
    - `var/homemaster/runs/<run_id>/memory/commit_log.jsonl`
  - 每个 case 从干净的 scenario base memory + 独立 runtime overlay 开始；除非测试明确验证跨任务记忆累积，否则不允许复用上一个 case 的 runtime memory。
  - 每个场景都要生成独立 case report，写清楚每个阶段的输入、输出、是否真实调用 Mimo / BGE-M3、失败或恢复原因、最终 task record 和 memory commit 摘要。
  - 输出 trace JSONL、scenario summary 和 acceptance matrix。acceptance matrix 必须显式区分：
    - 真实 Mimo：Stage02、Stage03 query、Stage05 plan/step/recovery、Stage06 summary。
    - 真实 BGE-M3：Stage03 embedding retrieval。
    - mock skill：Stage05 navigation / operation / verification。
    - 未接入项：真实机器人、真实 VLA、真实 VLM。
  - scenario matrix 用于证明端到端链路稳定，不替代普通用户入口。

测试：

- `test_cli_doctor.py`
  - `homemaster doctor` 在无 live API 情况下可运行。
  - provider/config 输出不包含 API key、`Authorization`、`Bearer`、`sk-`。
  - doctor 使用 `DEFAULT_CONFIG_PATH` 口径，优先报告 `config/api_config.json`；只有缺失时才报告 legacy fallback。
  - `.venv`、依赖、ignored runtime paths、import boundary 检测能给出 PASS/WARN/FAIL。
  - `--json` 输出可解析 JSON。
- `test_cli_interactive.py`
  - `homemaster --help` 显示 `doctor`、`run`、已有开发命令。
  - 无子命令启动交互入口时不会误跑完整任务；测试里用输入 `/exit` 验证可退出。
  - `/doctor` 在交互会话里能触发本地体检摘要。
- `test_cli_run.py`
  - `homemaster run --utterance ...` 能接通 Stage02-06 mock pipeline。
  - `homemaster run --scenario ... --run-id ... --runtime-memory-root ... --debug-root ...` 能把 world、base memory、runtime overlay 和 debug 资产全部写到指定路径。
  - 缺少 scenario 或 memory 时给出明确错误；不能悄悄使用默认全局 runtime memory 导致污染。
  - 运行结果输出 task id、final status、debug path、record path。
  - 失败时返回非 0，并写清失败阶段和失败原因。
- `test_homemaster_scenarios.py`
  - `check_medicine_success`：查看药盒成功，验证 TaskCard、RAG、grounded target、查看/验证、summary 和 object memory confirm update。
  - `check_medicine_stale_recover`：旧记忆失效，验证 Stage05 失败记录、negative evidence、`retrieve_again` 和最终合理结论。
  - `fetch_cup_retry`：取水杯过程中出现操作失败，验证 operation failure、自动 verification、recovery 和 task record。
  - `object_not_found`：没有可靠目标或现场未找到，验证不伪造成功、不新建 object memory，只写 scoped fact/event memory。
  - `distractor_rejected`：出现干扰物，验证 grounding/verification 拒绝错误目标，不误写 memory。
  - 每个 scenario 使用独立 `run_id` 和 runtime memory root；断言 `commit_log.jsonl`、`task_records.jsonl` 和 `object_memory.json` 不跨 case 复用。
  - acceptance matrix 标明每个阶段是真实模型、mock skill 还是未接入真实硬件。
- 全量 `pytest`
- `ruff check .`

测试样例：

- case：`doctor_local_environment`
  - 输入：`homemaster doctor`
  - 预期：本地依赖、导入、ignored 路径、runtime memory store 检测完成；配置来源显示为 generic config 或 legacy fallback；不调用外部 API；不泄露 secret。
- case：`doctor_live_provider_smoke`
  - 输入：`homemaster doctor --live`
  - 预期：真实 Mimo 最小 JSON smoke 和 BGE-M3 embedding smoke 成功；失败时能标出 provider/auth/network/schema 类别。
- case：`interactive_fetch_cup_exit`
  - 输入：启动 `homemaster`，输入“去厨房找水杯，然后拿给我”，结束后输入 `/exit`
  - 预期：终端展示 Stage02-06 的关键进度和最终 task record，不要求用户知道内部模块名。
- case：`scenario_matrix_check_medicine_success`
  - 输入：`check_medicine_success` 场景 + 查看药盒指令
  - 预期：LLM 驱动全链路成功；最终写入药盒已确认的 object memory update 和 task record；写入路径位于该 case 独立 runtime memory root。
- case：`scenario_matrix_stale_recover`
  - 输入：`check_medicine_stale_recover` 场景
  - 预期：当前 grounded target 失败后写 `FailureRecord` 和 negative evidence；`retrieve_again` 带 negative evidence 重新生成 PlanningContext，并成功或给出合理失败结论。
- case：`scenario_matrix_fetch_cup_retry`
  - 输入：`fetch_cup_retry` 场景
  - 预期：LLM 驱动取物链路，操作失败后进入验证和恢复；成功交付时写 delivery verified fact，不把失败位置写成永久事实。
- case：`scenario_matrix_object_not_found`
  - 输入：`object_not_found` 场景
  - 预期：可执行目标耗尽后失败，不伪造成功；只写 object_not_seen / task_record，不新建 object memory。
- case：`scenario_matrix_distractor_rejected`
  - 输入：`distractor_rejected` 场景
  - 预期：拒绝干扰物，不误写 memory；report 里说明拒绝原因来自目标类别、别名、验证结果或 world 校验。
- case：`scenario_matrix_memory_isolation`
  - 输入：连续运行 5 个 scenario matrix case。
  - 预期：每个 case 的 runtime memory、fact memory、task record 和 commit log 路径不同；前一个 case 的写回不会影响后一个 case 的 Stage03 检索，除非 case 明确声明要测试跨任务记忆累积。
- case：`scenario_matrix_model_boundary_report`
  - 输入：任意一个 Stage07 scenario matrix case。
  - 预期：acceptance matrix 明确列出真实 Mimo、真实 BGE-M3、mock navigation、mock operation、mock verification、真实机器人/VLA/VLM 未接入。
- debug 资产：`tests/homemaster/llm_cases/stage_07/<case_name>/`

验收标准：

- 用户可以通过 `homemaster` 进入对话，不需要记住内部 stage 命令。
- `homemaster doctor` 可以在不调用 API 的情况下发现本地环境和配置问题；`--live` 可以验证真实 Mimo / BGE-M3。
- `homemaster run --utterance ... --scenario ... --run-id ... --runtime-memory-root ... --debug-root ...` 可以作为脚本和 scenario matrix 的稳定入口。
- 5 个场景都必须走真实 Mimo 驱动的关键模型阶段。
- 5 个场景都必须覆盖完整 pipeline，不能只跑到 Stage04 或只验证单个模块。
- 5 个场景必须使用独立 runtime memory root 或独立 run id；默认不允许跨 case 复用 Stage06 写回结果。
- scenario summary 标注每个模型阶段是否真实调用 Mimo。
- acceptance matrix 必须逐项列出 Stage02、Stage03、Stage04、Stage05、Stage06 是否执行、是否通过、debug/report 路径。
- acceptance matrix 必须标明真实模型和 mock skill 边界，不能把 mock navigation / operation / verification 写成真实机器人或真实 VLA/VLM 成功。
- doctor、interactive、run、scenario matrix 的报告都不保存 API key、请求头或完整 secret config。
- 规则-only 端到端跑通不算 HomeMaster V1.2 通过。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_07/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-07-cli-doctor-scenario-matrix.md`

## 8. 测试策略

### 8.1 单元测试

覆盖：

- contract schema
- LLM output validation
- object memory retrieval
- reliability grounding
- planning context assembly
- orchestration validation
- step decision validation
- recovery decision validation
- memory commit guard

### 8.2 LLM 测试

本阶段主要用 LLM 测试任务脑能力：

- 每个模型阶段保留测试替身单测，保证 CI 稳定；测试替身可以是 `MockTransport` 或测试内 stub，不进入正式 runtime provider。
- 每个阶段验收至少跑一组真实 LLM 样例。
- 真实 LLM 默认从 `config/api_config.json` 读取 provider，缺失时兼容回退旧 provider config。
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

- `input.json` 保存阶段入口输入和必要上下文，例如 instruction、TaskCard、MemoryRetrievalQuery、PlanningContext、ExecutionState。
- `expected.json` 保存关键断言，不要求保存完整大对象。
- `actual.json` 保存 Mimo 返回的结构化输出、RAG 检索结果和程序裁剪后的关键执行结果。
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
- selected grounded target 和 retrieval/grounding 理由
- 验证结果
- 恢复动作
- memory commit 是否保守

### 8.6 旧链路清理检查

检查：

- 当前工程只暴露 `homemaster` CLI。
- `src/homemaster` 不导入旧链路包名或旧 parser/planner/recovery/graph 口径。
- 根目录旧链路测试、旧场景脚本和旧 `task-brain` console entry 不再保留。
- 新测试只覆盖 `homemaster` pipeline，不再要求旧主链可运行。

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
record/2026-04-24-stage-03-memory-rag.md
record/2026-04-24-stage-05-skill-execution-loop.md
record/2026-04-24-stage-06-summary-memory.md
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
  embedding_client.py
  memory_tokenizer.py
  memory_index.py
  memory_rag.py
  grounding.py
  planning_context.py
  orchestrator.py
  orchestration_validator.py
  skill_registry.py
  skill_selector.py
  execution_state.py
  executor.py
  verifier.py
  failure_log.py
  recovery.py
  summary.py
  memory_commit.py
  doctor.py
  interactive_shell.py
  scenario_runner.py
  pipeline.py
  cli.py
```

建议测试目录：

```text
tests/homemaster/
  test_contracts.py
  test_import_boundaries.py
  test_task_card.py
  test_embedding_client.py
  test_memory_tokenizer.py
  test_memory_index.py
  test_memory_rag.py
  test_stage_03_memory_rag_live.py
  test_grounding.py
  test_planning_context.py
  test_stage_04_grounding_context.py
  test_orchestration_plan.py
  test_skill_registry.py
  test_skill_selector.py
  test_execution_state.py
  test_skill_execution_loop.py
  test_failure_log.py
  test_recovery_loop.py
  test_vlm_image_input_disabled.py
  test_summary_memory_commit.py
  test_cli_doctor.py
  test_cli_interactive.py
  test_cli_run.py
  test_homemaster_scenarios.py
```

## 12. 工程 review 风险清单

实施时重点避免：

- 命名漂移：新主链代码、CLI、脚本和测试目录统一使用 `homemaster`，不要再使用临时版本号式命名。
- 过早多记忆检索：首版不要把 category prior、episodic、用户习惯都塞进上下文。
- RAG 单路化：Stage 03 必须保留 BM25 + BGE-M3 双路证据；不能只靠规则、只靠 BM25 或只靠 dense embedding 判定通过。
- RAG 只召回不校验：BM25/BGE-M3 召回后必须经过 metadata guardrail 和 grounding 校验，不能把错误 source 或缺失 viewpoint 的 chunk 交给 planning context。
- top1 迷信：Stage 04 不能把 RAG top1 或 top executable 直接当可靠执行记忆；必须通过 reliability hard checks。
- ungrounded 误判失败：没有可靠执行记忆不是系统失败，Stage 04 要给 Stage 05 一个可探索的 `PlanningContext(selected_target=None)`。
- embedding 配置漂移：Stage 03 必须记录 BGE-M3 provider/model；测试资产不能保存 embedding API key。
- RAG index 陈旧：object memory 写回后必须刷新或标记 index stale，避免后续任务搜到旧证据。
- CLI 入口割裂：普通用户入口必须是 `homemaster` 对话和 `homemaster run` 单轮执行；scenario matrix 和开发 smoke 命令不能成为用户必须理解的主入口。
- doctor 泄露配置：`homemaster doctor` 可以检测 provider 和 model，但不能打印 API key、请求头、完整 secret config 或 embedding cache 内容。
- doctor 误触发付费 API：默认 `homemaster doctor` 只做本地体检；只有显式 `--live` 才调用真实 Mimo / BGE-M3。
- doctor 配置路径漂移：doctor 不能写死旧 provider config；必须使用 `DEFAULT_CONFIG_PATH` 口径，报告 generic config 或 legacy fallback 的实际来源。
- scenario 记忆污染：5 个端到端场景默认必须使用独立 `run_id` 和 runtime memory root，避免 Stage06 写回结果影响后续场景。
- scenario 入口绕过 CLI：`scripts/run_homemaster_scenarios.sh` 应优先通过 `homemaster run` 或 `homemaster eval scenarios` 调用，CLI 必须提供 scenario/world/memory/runtime/debug/run id 参数。
- 全链路边界误报：Stage07 可以说真实 Mimo / BGE-M3 跑通，但首版 skill 仍是 mock navigation / operation / verification，不能写成真实机器人或真实 VLA/VLM 已完成。
- 过早独立具身规划器：本版本不引入具身 planner；导航、操作和验证统一走 skill。
- skill 过度拆分：首版只保留 `navigation`、`operation`、`verification` 三个复用 skill，不要把找水杯、找用户、拿起水杯、放下水杯拆成多个独立 skill。
- skill RAG 过早引入：首版 skill 数量太少，不需要 RAG 或 embedding 检索；后续 skill 数量明显增多时再考虑 skill manifest 检索。
- 失败记录缺失：Stage 05 的失败不能只写一条 error 字符串；必须写 FailureRecord，否则无法恢复、重规划或未来转成 event memory。
- VLM 假接入：图片接口要有，但默认 disabled；不要写成“已经接真实 VLM”。
- 旧链泄漏：不要把旧 parser/planner/recovery/graph 作为 fallback。
- LLM 测试不可复现：真实 LLM 默认使用 `config/api_config.json` 或 legacy fallback 里的 `Mimo / mimo-v2-pro`，日志落盘，CI 仍用测试替身保稳定。
- 记忆误写：没有 verified evidence 不更新长期 object memory。
- 测试日志进 git：`plan/V1.2/test_results/` 必须保持 ignored。
- 阶段结论缺失：每阶段完成后必须在 `record/YYYY-MM-DD-实验名称.md` 写精炼报告。

## 13. 完成标准

V1.2 完成时至少满足：

- `homemaster` 包可独立导入。
- `homemaster` CLI 可独立运行。
- 首版只把 object memory 纳入 RAG 检索。
- Stage 03 使用 BM25 + jieba tokenizer + 真实 BGE-M3 embedding provider 完成 memory retrieval，并输出可解释 MemoryRetrievalResult。
- Stage 04 对 RAG hits 做可靠执行记忆判定：有 reliable hit 时生成 `GroundedMemoryTarget`，没有 reliable hit 时输出 `selected_target=None` 的 `PlanningContext` 让 Stage 05 探索/寻找。
- Stage 05 高层计划只写子任务 intent，不绑定 skill；执行期由 LLM 在可选 action skill 中选择，首版为 `navigation` 和 `operation`，`verification` 由程序自动后置调用。
- Stage 05 中任何执行失败、验证失败、模型输出失败或前置条件失败都写 FailureRecord，并可用于恢复、重规划和未来 event memory 扩展。
- 高层编排、skill 选择、一步决策和恢复由 LLM 主导；目标选择不再作为独立 LLM 阶段。
- VLM 图片接口存在但默认 disabled。
- 不引入独立具身规划 planner；operation skill 内部负责选择原子动作，并生成给 VLA 的指令。
- 5 个核心场景有端到端结果。
- 每阶段都有原始测试结果落盘到 `plan/V1.2/test_results/stage_xx/`。
- 每阶段都有精炼结论报告落盘到 `record/YYYY-MM-DD-实验名称.md`。
- 阶段 LLM 测试默认使用 `config/api_config.json` 或 legacy fallback 中的 `Mimo / mimo-v2-pro`。
- trace 能清楚还原任务理解、检索、grounding/context、编排、决策、执行、验证、恢复、总结和写回。
- V1 旧主链保持可运行，不被新主链污染。
