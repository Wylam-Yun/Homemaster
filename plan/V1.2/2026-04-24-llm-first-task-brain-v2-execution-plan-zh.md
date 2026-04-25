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
  -> object_memory RAG 检索
  -> 可靠执行记忆判定 / planning context 组装
  -> 高层模块编排
  -> 决策 / 执行 / 验证 / 恢复闭环
  -> 具身操作接口
  -> 任务总结
  -> 证据把关后的记忆写回
```

首版以 LLM 测试为主，先证明任务脑的理解、检索、编排、一步决策和恢复能力。VLM 图片接口要提前留好，但默认禁用；当前验证先用结构化 observation / mock observation。具身动作规划器接口也要留好，但本阶段先由 HomeMaster 大脑临时兼任，方便测试闭环，不提前拆出真正独立的操作规划智能体。

阶段测试默认使用 `config/api_config.json` 中的 Mimo 配置；为了兼容旧脚本，如果该文件不存在，代码会回退读取旧 provider config：

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
- 可选的 memory retrieval query rewrite
- 高层模块编排
- 执行期一步决策
- 失败恢复判断
- 任务总结

程序负责：

- 加载场景、object memory 和 runtime state
- 构建 object memory RAG index
- 调用 embedding 模型生成 memory/query 向量
- 执行 hybrid retrieval 和 metadata guardrail
- 对 RAG hits 做可靠执行记忆判定
- 只有 reliable + groundable hit 才生成 `GroundedMemoryTarget`
- 没有可靠执行记忆时输出 `selected_target=None` 的 `PlanningContext`，让 planner 规划探索/寻找
- 组装高层编排所需 planning context
- 校验结构化输出
- 校验观察点、锚点、grounded target 和模块调用是否存在
- 调用 mock VLN / observation / VLA 接口
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

业务细节放在目标、参数和成功标准里，不再把“前往目标点、观察区域、确认目标、抓取、放下、回到用户”写成固定子任务 enum。

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
- `world_summary` 只包含编排需要的最小可落地信息，例如 room、anchor、viewpoint 和可用模块。

### 4.6 OrchestrationPlan

职责：高层模块编排。

建议字段：

- `goal`
- `selected_target`
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
- `module_input` 由程序校验 grounded target、viewpoint、memory id 和 object ref。

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
- `next_target_id`
- `should_retrieve_again`
- `should_replan`
- `ask_user_question`

说明：

- `action` 覆盖 `retry_step`、`reobserve`、`switch_target`、`retrieve_again`、`replan`、`ask_user`、`finish_failed`。

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
- 程序校验 schema、enum、viewpoint、memory id、RAG hit metadata、grounded target 和 module name。
- RAG 检索结果必须能回到 canonical object memory，不允许只凭自然语言 chunk 生成执行目标。
- 程序不把 RAG top1 直接当作可靠目标；必须先经过 reliability hard checks。
- 只有 reliable + world-groundable hit 才能成为 `GroundedMemoryTarget`；weak lead 只能作为探索线索。
- 没有可靠执行记忆时不是失败，也不能伪造地点；Stage 04 必须输出 ungrounded `PlanningContext`，交给 Stage 05 规划探索/寻找。
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

### 6.4 高层模块编排

输入：

- `TaskCard`
- `PlanningContext`

处理：

- LLM 生成 `OrchestrationPlan`。
- 程序校验 subtask type、依赖关系和 success criteria。
- 当 `PlanningContext.selected_target` 存在时，程序校验计划中引用的 target/viewpoint/module 都来自 selected target 和 world summary。
- 当 `PlanningContext.selected_target=None` 时，LLM 不能编造 memory target；计划应先生成探索/寻找/观察或追问步骤，导航目标只能来自 `TaskCard.location_hint` 可落到的 world summary 或明确的 room/viewpoint。

测试：

- 查看类不进入 `embodied_operate`。
- 取物类必须先观察验证，再进入 `embodied_operate`。
- ungrounded context 生成探索/寻找计划，不伪造 memory target。
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
- switch grounded target
- rerun memory RAG retrieval
- replan orchestration
- ask user
- finish failed

测试：

- `check_medicine_stale_recover`：grounded target 切换。
- `object_not_found`：可执行目标耗尽后失败。
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
- 阶段通过测试：真实调用 `config/api_config.json` 或 legacy fallback 中的 `Mimo / mimo-v2-pro`，并得到结构化输出，通过本阶段程序校验。

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

### 阶段 3：object_memory-only RAG 检索（BM25 + BGE-M3）

实现：

- 修改 `pyproject.toml`：新增 `bm25s>=0.2`、`jieba>=0.42`。
- 新建 `embedding_client.py`
- 新建 `memory_tokenizer.py`
- 新建 `memory_index.py`
- 新建 `memory_rag.py`
- 修改 `contracts.py`：新增 `MemoryRetrievalQuery`、`MemoryRetrievalResult`、`GroundedMemoryTarget`、`PlanningContext`；Stage 01 预留的旧契约类只作为 deprecated compatibility shells 保留，新代码不再使用。
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
- Stage 09 写回 object memory 后，必须刷新对应 document embedding 或标记 cache/index stale。
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
- contract 迁移测试确认 `MemoryRetrievalQuery` / `MemoryRetrievalResult` / `GroundedMemoryTarget` / `PlanningContext` 可序列化、可校验，旧契约类保留兼容但新 Stage 03/04 代码不再依赖它们。
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
  - 预期：`MemoryRetrievalQuery`、`MemoryRetrievalResult` 均可 `model_dump_json()` / `model_validate_json()`；静态检查确认 Stage 03/04 新代码不再使用 `ObjectMemorySearchPlan`。
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
- `contracts.py` 已完成兼容式契约迁移：Stage 03/04 新代码和测试使用 `MemoryRetrievalQuery` / `MemoryRetrievalResult` / `GroundedMemoryTarget` / `PlanningContext`；旧契约类仅为兼容保留。
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
- Stage 07 的重检索不是重跑旧规则 search，而是带最新 negative evidence 重跑 Stage 03 RAG retrieval。
- Stage 09 写回 object memory 后必须刷新或标记 RAG index stale，否则后续 retrieval 可能命中过期 embedding。
- Stage 10 scenario matrix 需要在 trace 中记录 retrieval query、BGE-M3 provider/model、BM25 tokenizer、score breakdown 和 source filter，便于复盘。

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
- 没有 reliable hit 时输出合法 `PlanningContext(selected_target=None)`，并明确要求 Stage 05 探索/寻找。
- PlanningContext 足够支持 Stage 05 直接生成 OrchestrationPlan。

测试样例：

- case：`ground_medicine_target`
  - 输入：`check_medicine_success` 的 RAG memory hits + world
  - 预期：程序选择第一个 reliable hit，生成 `GroundedMemoryTarget(memory_id=mem-medicine-1, viewpoint_id=...)`，不调用 Mimo 目标选择。
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
- top1 排序不等于可靠；low confidence、stale、location conflict、metadata target mismatch 都必须有测试覆盖。
- Debug 报告可解释每个 hit 为什么 reliable、weak_lead 或 unreliable。
- `PlanningContext` 是 Stage 05 的唯一上下文入口，包含编排所需最小信息。

测试结果：

- 保存到 `plan/V1.2/test_results/stage_04/`
- 精炼结论报告保存到 `record/YYYY-MM-DD-stage-04-grounding-context.md`

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
  - 输入：查看药盒 TaskCard + PlanningContext(selected_target=药盒 memory target)
  - 预期：计划包含 `navigate`、`observe_verify`、`finish`，不包含 `embodied_operate`。
- case：`fetch_cup_orchestration`
  - 输入：取水杯 TaskCard + PlanningContext(selected_target=水杯 memory target)
  - 预期：计划包含 `navigate`、`observe_verify`、`embodied_operate`、后置验证和 `finish`。
- case：`forbid_atomic_action_in_plan`
  - 输入：构造包含 `close_gripper` 的 LLM 输出
  - 预期：validator 拒绝该计划。
- case：`ungrounded_exploratory_orchestration`
  - 输入：`PlanningContext(selected_target=None, grounding_status=ungrounded, needs_exploratory_search=true)`
  - 预期：Mimo 生成探索/观察/追问型计划，不伪造 `GroundedMemoryTarget` 或不存在的 memory id。
- debug 资产：`tests/homemaster/llm_cases/stage_05/<case_name>/`

验收标准：

- 查看类和取物类计划都必须来自 Mimo 实际输出。
- Mimo 输出通过 `OrchestrationPlan` 校验。
- grounded case 中，OrchestrationPlan 引用的 navigation target / viewpoint 必须来自 `PlanningContext.selected_target`。
- ungrounded case 中，OrchestrationPlan 不能引用伪造 memory target；探索导航目标必须来自 `PlanningContext.world_summary` 或 TaskCard 明确 location hint 可落到的 world 信息。
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
- 程序应用 grounded target 切换、重观察、重跑 RAG 检索、重编排、追问或失败结束。

测试：

- `check_medicine_stale_recover`
- `object_not_found`
- `distractor_rejected`
- 恢复后 trace 可还原原因和动作。

测试样例：

- case：`stale_medicine_recovery`
  - 输入：`check_medicine_stale_recover` 场景
  - 预期：第一次 grounded target 验证失败后，Mimo 输出 `RecoveryDecision(action=switch_target)` 或等价目标切换动作。
- case：`object_not_found_recovery`
  - 输入：`object_not_found` 场景
  - 预期：可执行目标耗尽后 Mimo 输出失败结束或追问，不伪造成功。
- case：`distractor_rejected_recovery`
  - 输入：`distractor_rejected` 场景
  - 预期：看到非目标物后不进入成功或错误具身操作。
- debug 资产：`tests/homemaster/llm_cases/stage_07/<case_name>/`

验收标准：

- 每个恢复 case 都必须真实调用 Mimo 生成 `RecoveryDecision`。
- RecoveryDecision 通过程序校验并驱动后续状态变化。
- 当 action 表示 `retrieve_again` 时，程序必须带上最新 negative evidence 重新执行 Stage 03 RAG retrieval。
- 旧 recovery tree 或规则目标切换不能单独判定通过。

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
- object memory 写回后必须标记 RAG index stale，或同步更新对应 memory document embedding。
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
  - 预期：LLM 驱动 grounded target 切换并成功或给出合理失败结论。
- case：`scenario_matrix_fetch_cup_retry`
  - 输入：`fetch_cup_retry` 场景
  - 预期：LLM 驱动取物链路，操作失败后进入验证和恢复。
- case：`scenario_matrix_object_not_found`
  - 输入：`object_not_found` 场景
  - 预期：可执行目标耗尽后失败，不伪造成功。
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
- reliability grounding
- planning context assembly
- orchestration validation
- step decision validation
- recovery decision validation
- memory commit guard

### 8.2 LLM 测试

本阶段主要用 LLM 测试任务脑能力：

- 每个模型阶段保留 fake provider 单测，保证 CI 稳定。
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

- `input.json` 保存阶段入口输入和必要上下文，例如 instruction、TaskCard、MemoryRetrievalQuery、PlanningContext、runtime state。
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
record/2026-04-24-stage-03-memory-rag.md
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
  embedding_client.py
  memory_tokenizer.py
  memory_index.py
  memory_rag.py
  grounding.py
  planning_context.py
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
  test_embedding_client.py
  test_memory_tokenizer.py
  test_memory_index.py
  test_memory_rag.py
  test_stage_03_memory_rag_live.py
  test_grounding.py
  test_planning_context.py
  test_stage_04_grounding_context.py
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
- RAG 单路化：Stage 03 必须保留 BM25 + BGE-M3 双路证据；不能只靠规则、只靠 BM25 或只靠 dense embedding 判定通过。
- RAG 只召回不校验：BM25/BGE-M3 召回后必须经过 metadata guardrail 和 grounding 校验，不能把错误 source 或缺失 viewpoint 的 chunk 交给 planning context。
- top1 迷信：Stage 04 不能把 RAG top1 或 top executable 直接当可靠执行记忆；必须通过 reliability hard checks。
- ungrounded 误判失败：没有可靠执行记忆不是系统失败，Stage 04 要给 Stage 05 一个可探索的 `PlanningContext(selected_target=None)`。
- embedding 配置漂移：Stage 03 必须记录 BGE-M3 provider/model；测试资产不能保存 embedding API key。
- RAG index 陈旧：object memory 写回后必须刷新或标记 index stale，避免后续任务搜到旧证据。
- 过早独立具身规划器：本阶段由 HomeMaster 大脑兼任，接口留好即可。
- VLM 假接入：图片接口要有，但默认 disabled；不要写成“已经接真实 VLM”。
- 旧链泄漏：不要把旧 parser/planner/recovery/graph 作为 fallback。
- LLM 测试不可复现：真实 LLM 默认使用 `config/api_config.json` 或 legacy fallback 里的 `Mimo / mimo-v2-pro`，日志落盘，CI 仍用 fake provider 保稳定。
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
- 高层编排、一步决策和恢复由 LLM 主导；目标选择不再作为独立 LLM 阶段。
- VLM 图片接口存在但默认 disabled。
- 具身规划接口存在，本阶段由 HomeMaster 大脑兼任。
- 5 个核心场景有端到端结果。
- 每阶段都有原始测试结果落盘到 `plan/V1.2/test_results/stage_xx/`。
- 每阶段都有精炼结论报告落盘到 `record/YYYY-MM-DD-实验名称.md`。
- 阶段 LLM 测试默认使用 `config/api_config.json` 或 legacy fallback 中的 `Mimo / mimo-v2-pro`。
- trace 能清楚还原任务理解、检索、grounding/context、编排、决策、执行、验证、恢复、总结和写回。
- V1 旧主链保持可运行，不被新主链污染。
