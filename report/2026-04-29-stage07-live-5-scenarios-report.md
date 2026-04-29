# HomeMaster Stage07 五场景 Live 运行报告（2026-04-29）

## 1. 本次运行

- 执行方式：逐个调用 `homemaster run --live-models --mock-skills`
- 原始日志：`/Users/wylam/Documents/workspace/HomeMaster/log/2026-04-29-stage07-live-scenarios.log`
- 场景结果：
  - `check_medicine_success`：`completed`
  - `check_medicine_stale_recover`：`completed`
  - `fetch_cup_retry`：`completed`
  - `object_not_found`：`failed`（安全失败）
  - `distractor_rejected`：`failed`（安全失败）

## 2. 字段解释（通用）

### 2.1 Stage02 `TaskCard` 字段

- `task_type`：任务类型，`check_presence`（确认是否在）或 `fetch_object`（找并交付）。
- `target`：目标物体名称。
- `delivery_target`：交付对象；取物任务通常是 `user`。
- `location_hint`：用户口头给的位置提示，不代表已验证。
- `success_criteria`：后续验证模块要检查的完成条件。
- `needs_clarification`：是否需要追问用户澄清。
- `clarification_question`：若需澄清，这里给追问句子。
- `confidence`：模型自评置信度（0 到 1）。

### 2.2 Stage03 `MemoryRetrievalQuery` 字段

- `query_text`：用于检索记忆的查询语句。
- `target_category`：目标类别（可为空）。
- `target_aliases`：目标别名集合。
- `location_terms`：位置词集合。
- `source_filter`：当前固定为 `["object_memory"]`。
- `top_k`：召回条数。
- `excluded_memory_ids` / `excluded_location_keys`：负证据排除列表。
- `reason`：为什么这样构造查询。

### 2.3 Stage05 输出字段

- `orchestration_plan.goal`：高层目标。
- `orchestration_plan.subtasks`：子任务列表（含依赖）。
- `step_decisions`：每一步选择的 skill（`navigation` / `operation`）与输入参数。

### 2.4 Stage06 `summary` 字段

- `result`：任务最终结果（`success` / `failed`）。
- `confirmed_facts`：本轮已确认事实。
- `unconfirmed_facts`：未确认或失败相关事实。
- `failure_summary`：失败原因摘要（成功时为空）。
- `user_reply`：面向用户的自然语言结果说明。

---

## 3. 场景 1：`check_medicine_success`

### 3.1 输入 Prompt 是什么

- Stage02 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-check_medicine_success_task_understanding/input.json` 的 `prompt`
- Stage03 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-check_medicine_success/stage_03_cases/stage07_stage07-check_medicine_success_memory_rag/input.json` 的 `prompt`
- 本场景用户输入：`去厨房看看药盒是不是还在。`

### 3.2 怎么处理

1. Stage02：Mimo 把用户句子转成 `TaskCard`。
2. Stage03：Mimo 生成检索 query，程序做 BM25 + BGE-M3 融合检索。
3. Stage04：选中可靠记忆 `mem-medicine-1`（厨房药柜）。
4. Stage05：生成单子任务“确认药盒存在”，执行 `navigation`，自动验证通过。
5. Stage06：生成成功总结，写回 object/fact/task record。

### 3.3 Mimo 输出是什么

Stage02 原始输出：

```json
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "厨房",
  "success_criteria": ["药盒在厨房中被确认存在"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
```

Stage03 原始输出：

```json
{
  "query_text": "药盒在厨房中，别名药物盒，英文 medicine box",
  "target_category": null,
  "target_aliases": ["药盒", "药物盒", "medicine box"],
  "location_terms": ["厨房"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "检查药盒在厨房的存在性"
}
```

Stage05 计划输出（结构化）：

```json
{
  "goal": "检查药盒在厨房的存在性",
  "subtasks": [
    {
      "id": "confirm_presence",
      "intent": "确认药盒在厨房的存在",
      "target_object": "药盒",
      "room_hint": "厨房",
      "anchor_hint": "厨房药柜"
    }
  ]
}
```

Stage06 总结输出（结构化）：

```json
{
  "result": "success",
  "confirmed_facts": ["观察到药盒"],
  "failure_summary": null,
  "user_reply": "药盒在厨房中被确认存在。"
}
```

---

## 4. 场景 2：`check_medicine_stale_recover`

### 4.1 输入 Prompt 是什么

- Stage02 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-check_medicine_stale_recover_task_understanding/input.json` 的 `prompt`
- Stage03 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-check_medicine_stale_recover/stage_03_cases/stage07_stage07-check_medicine_stale_recover_memory_rag/input.json` 的 `prompt`
- 本场景用户输入：`去桌子那边看看药盒是不是还在。`

### 4.2 怎么处理

1. Stage02：提取出目标 `药盒`，位置提示 `桌子那边`。
2. Stage03：检索到候选记忆，程序评估后 Stage04 选中 `mem-medicine-2`（客厅边桌）。
3. Stage05：先到边桌附近，再观察确认药盒。
4. Stage06：成功写回“药盒在客厅边桌被观察到”事实。

### 4.3 Mimo 输出是什么

Stage02 原始输出：

```json
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "桌子那边",
  "success_criteria": ["确认药盒在桌子那边"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
```

Stage03 原始输出：

```json
{
  "query_text": "药盒 药箱 medicine box 桌子那边",
  "target_category": null,
  "target_aliases": ["药盒", "药箱", "药品盒"],
  "location_terms": ["桌子那边", "桌子"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": null
}
```

Stage05 计划输出（结构化）：

```json
{
  "goal": "确认药盒在桌子那边",
  "subtasks": [
    {"id": "subtask_1", "intent": "导航到客厅边桌"},
    {"id": "subtask_2", "intent": "确认药盒在桌子上"}
  ]
}
```

Stage06 总结输出（结构化）：

```json
{
  "result": "success",
  "confirmed_facts": ["结构化 observation 支持该子任务完成", "观察到药盒"],
  "failure_summary": null,
  "user_reply": "药盒在边桌"
}
```

---

## 5. 场景 3：`fetch_cup_retry`

### 5.1 输入 Prompt 是什么

- Stage02 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-fetch_cup_retry_task_understanding/input.json` 的 `prompt`
- Stage03 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-fetch_cup_retry/stage_03_cases/stage07_stage07-fetch_cup_retry_memory_rag/input.json` 的 `prompt`
- 本场景用户输入：`去厨房找水杯，然后拿给我`

### 5.2 怎么处理

1. Stage02：识别为 `fetch_object`，目标 `水杯`，交付对象 `user`。
2. Stage03：构造 query 并召回 `mem-cup-1`（厨房餐桌）为首选记忆。
3. Stage04：grounded 成功，进入执行。
4. Stage05：5 步闭环（找杯 -> 拿杯 -> 回用户 -> 交付 -> 确认）。
5. Stage06：写入“观察到水杯”和“已交付水杯”两类事实。

### 5.3 Mimo 输出是什么

Stage02 原始输出：

```json
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": ["水杯被成功交付给用户"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.9
}
```

Stage03 原始输出：

```json
{
  "query_text": "水杯 杯子 茶杯 water cup cup 厨房 kitchen",
  "target_category": null,
  "target_aliases": ["杯子", "茶杯", "water cup", "cup"],
  "location_terms": ["厨房", "kitchen"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "Fetch water cup from kitchen for user delivery."
}
```

Stage05 计划输出（结构化）：

```json
{
  "goal": "将水杯从厨房交付给用户",
  "subtasks": [
    {"id": "subtask_1", "intent": "找到水杯"},
    {"id": "subtask_2", "intent": "拿起水杯"},
    {"id": "subtask_3", "intent": "回到用户位置"},
    {"id": "subtask_4", "intent": "交付水杯给用户"},
    {"id": "subtask_5", "intent": "确认任务完成"}
  ]
}
```

Stage06 总结输出（结构化）：

```json
{
  "result": "success",
  "confirmed_facts": ["观察到水杯", "已经拿起水杯", "已到达目标位置", "已经交付水杯", "结构化 observation 支持该子任务完成"],
  "failure_summary": null,
  "user_reply": "水杯已成功交付给用户。"
}
```

---

## 6. 场景 4：`object_not_found`

### 6.1 输入 Prompt 是什么

- Stage02 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-object_not_found_task_understanding/input.json` 的 `prompt`
- Stage03 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-object_not_found/stage_03_cases/stage07_stage07-object_not_found_memory_rag/input.json` 的 `prompt`
- 本场景用户输入：`去厨房找水杯，然后拿给我`

### 6.2 怎么处理

1. Stage02/03/04 都成功，选中 `mem-cup-1`。
2. Stage05 在“找到水杯”子任务执行后，验证结果为“没有观察到水杯”。
3. 系统触发安全失败，不伪造成功结果。
4. Stage06 只写失败事实和失败摘要。

### 6.3 Mimo 输出是什么

Stage02 原始输出：

```json
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": ["机器人成功拿到水杯并交付给用户"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
```

Stage03 原始输出：

```json
{
  "query_text": "水杯 杯子 水壶 茶杯 water cup mug 厨房",
  "target_category": null,
  "target_aliases": ["水杯", "杯子", "水壶", "茶杯"],
  "location_terms": ["厨房"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "根据 TaskCard 检索水杯在厨房的位置以执行 fetch_object 任务"
}
```

Stage05 计划输出（结构化）：

```json
{
  "goal": "机器人成功拿到水杯并交付给用户",
  "subtasks": [
    {"id": "find_cup", "intent": "找到水杯"},
    {"id": "pick_cup", "intent": "拿起水杯"},
    {"id": "return_to_user", "intent": "回到用户位置"},
    {"id": "deliver_cup", "intent": "交付水杯给用户"},
    {"id": "confirm_completion", "intent": "确认任务完成"}
  ]
}
```

Stage06 总结输出（结构化）：

```json
{
  "result": "failed",
  "confirmed_facts": [],
  "unconfirmed_facts": ["没有观察到水杯"],
  "failure_summary": "任务失败：在厨房查找水杯时未观察到目标。",
  "user_reply": "很抱歉，我在厨房没有找到水杯。"
}
```

---

## 7. 场景 5：`distractor_rejected`

### 7.1 输入 Prompt 是什么

- Stage02 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-distractor_rejected_task_understanding/input.json` 的 `prompt`
- Stage03 Prompt（完整文本）：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-distractor_rejected/stage_03_cases/stage07_stage07-distractor_rejected_memory_rag/input.json` 的 `prompt`
- 本场景用户输入：`去厨房找水杯，然后拿给我`

### 7.2 怎么处理

1. Stage02：识别 fetch 任务。
2. Stage03：生成检索 query 并定位到厨房相关记忆。
3. Stage05：首个导航观察后仍未见目标，验证失败。
4. 系统记录失败并安全结束，不继续伪执行。

### 7.3 Mimo 输出是什么

Stage02 原始输出：

```json
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": ["机器人成功将水杯交付给用户"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
```

Stage03 原始输出：

```json
{
  "query_text": "水杯，杯子，厨房，water cup",
  "target_category": null,
  "target_aliases": ["水杯", "杯子"],
  "location_terms": ["厨房"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "根据fetch_object任务检索水杯以交付给用户"
}
```

Stage05 计划输出（结构化）：

```json
{
  "goal": "成功将水杯交付给用户",
  "subtasks": [
    {"id": "subtask_1", "intent": "找到水杯"},
    {"id": "subtask_2", "intent": "拿起水杯"},
    {"id": "subtask_3", "intent": "回到用户位置"},
    {"id": "subtask_4", "intent": "交付水杯给用户"}
  ]
}
```

Stage06 总结输出（结构化）：

```json
{
  "result": "failed",
  "confirmed_facts": [],
  "unconfirmed_facts": ["没有观察到水杯"],
  "failure_summary": "任务失败：没有观察到水杯。",
  "user_reply": "抱歉，机器人没有找到水杯。"
}
```

---

## 8. 证据路径（可直接对照）

- 全量运行日志：
  `/Users/wylam/Documents/workspace/HomeMaster/log/2026-04-29-stage07-live-scenarios.log`
- Stage07 汇总 case：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-check_medicine_success/result.md`
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-check_medicine_stale_recover/result.md`
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-fetch_cup_retry/result.md`
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-object_not_found/result.md`
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-distractor_rejected/result.md`
- Stage02 Prompt/回复：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/`
- 运行期记忆写回：
  `/Users/wylam/Documents/workspace/HomeMaster/var/homemaster/runs/`

## 9. 备注

- 本次是 `real_mimo + real_bge-m3`，但 `navigation / operation / verification` 仍是 mock skill。
- Stage05 当前资产落盘里保留了结构化计划与步骤结果；完整 Stage05 prompt 文本没有单独落盘文件。

---

## 10. 附录：完整 Prompt（已内嵌）

### 10.1 `stage07-check_medicine_success` Stage02 Prompt

```text
你是 HomeMaster V1.2 的 LLM-first 任务理解入口。

目标：把用户自然语言指令转换成一个 TaskCard JSON。
你只负责理解任务，不负责检索记忆、不负责选择候选地点、不负责规划机器人动作。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置、物品状态或执行结果。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可包含中文名或稳定英文别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要，默认用户为 user",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

判断规则:
- 用户要求“看看、确认、还在不在、是否在”某物时，task_type 使用 "check_presence"。
- 用户要求“找、拿、取、拿给我、送来”某物时，task_type 使用 "fetch_object"。
- 取物并交给说话人时，delivery_target 使用 "user"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出澄清问题。
- 如果目标物不明确，target 使用 "unknown_object" 或 "不明确目标"；不要猜药盒、水杯等具体物品。
- 如果用户只说“那个”“这个”“它”且没有具体目标物，必须使用 task_type="unknown"，
  target="unknown_object"，needs_clarification=true，并提出“请问您想确认哪个物品？”这类澄清问题。
- 如果目标物明确，needs_clarification 使用 false，clarification_question 使用 null。
- location_hint 只放用户明说的位置，例如“桌子那边”“厨房”；不要把它当成已验证位置。
- success_criteria 必须描述后续 observation/verification 可以判断的完成条件。
- confidence 使用 0 到 1 之间的小数。

输入上下文:
{
  "utterance": "去厨房看看药盒是不是还在。",
  "user_id": null,
  "source": null,
  "recent_task_summary": null
}

只输出 JSON object:
```

### 10.2 `stage07-check_medicine_success` Stage03 Prompt

```text
你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "厨房",
  "success_criteria": [
    "药盒在厨房中被确认存在"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}

Runtime negative evidence:
{}

只输出 JSON object:
```

### 10.3 `stage07-check_medicine_stale_recover` Stage02 Prompt

```text
你是 HomeMaster V1.2 的 LLM-first 任务理解入口。

目标：把用户自然语言指令转换成一个 TaskCard JSON。
你只负责理解任务，不负责检索记忆、不负责选择候选地点、不负责规划机器人动作。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置、物品状态或执行结果。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可包含中文名或稳定英文别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要，默认用户为 user",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

判断规则:
- 用户要求“看看、确认、还在不在、是否在”某物时，task_type 使用 "check_presence"。
- 用户要求“找、拿、取、拿给我、送来”某物时，task_type 使用 "fetch_object"。
- 取物并交给说话人时，delivery_target 使用 "user"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出澄清问题。
- 如果目标物不明确，target 使用 "unknown_object" 或 "不明确目标"；不要猜药盒、水杯等具体物品。
- 如果用户只说“那个”“这个”“它”且没有具体目标物，必须使用 task_type="unknown"，
  target="unknown_object"，needs_clarification=true，并提出“请问您想确认哪个物品？”这类澄清问题。
- 如果目标物明确，needs_clarification 使用 false，clarification_question 使用 null。
- location_hint 只放用户明说的位置，例如“桌子那边”“厨房”；不要把它当成已验证位置。
- success_criteria 必须描述后续 observation/verification 可以判断的完成条件。
- confidence 使用 0 到 1 之间的小数。

输入上下文:
{
  "utterance": "去桌子那边看看药盒是不是还在。",
  "user_id": null,
  "source": null,
  "recent_task_summary": null
}

只输出 JSON object:
```

### 10.4 `stage07-check_medicine_stale_recover` Stage03 Prompt

```text
你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "桌子那边",
  "success_criteria": [
    "确认药盒在桌子那边"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}

Runtime negative evidence:
{}

只输出 JSON object:
```

### 10.5 `stage07-fetch_cup_retry` Stage02 Prompt

```text
你是 HomeMaster V1.2 的 LLM-first 任务理解入口。

目标：把用户自然语言指令转换成一个 TaskCard JSON。
你只负责理解任务，不负责检索记忆、不负责选择候选地点、不负责规划机器人动作。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置、物品状态或执行结果。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可包含中文名或稳定英文别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要，默认用户为 user",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

判断规则:
- 用户要求“看看、确认、还在不在、是否在”某物时，task_type 使用 "check_presence"。
- 用户要求“找、拿、取、拿给我、送来”某物时，task_type 使用 "fetch_object"。
- 取物并交给说话人时，delivery_target 使用 "user"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出澄清问题。
- 如果目标物不明确，target 使用 "unknown_object" 或 "不明确目标"；不要猜药盒、水杯等具体物品。
- 如果用户只说“那个”“这个”“它”且没有具体目标物，必须使用 task_type="unknown"，
  target="unknown_object"，needs_clarification=true，并提出“请问您想确认哪个物品？”这类澄清问题。
- 如果目标物明确，needs_clarification 使用 false，clarification_question 使用 null。
- location_hint 只放用户明说的位置，例如“桌子那边”“厨房”；不要把它当成已验证位置。
- success_criteria 必须描述后续 observation/verification 可以判断的完成条件。
- confidence 使用 0 到 1 之间的小数。

输入上下文:
{
  "utterance": "去厨房找水杯，然后拿给我",
  "user_id": null,
  "source": null,
  "recent_task_summary": null
}

只输出 JSON object:
```

### 10.6 `stage07-fetch_cup_retry` Stage03 Prompt

```text
你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": [
    "水杯被成功交付给用户"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.9
}

Runtime negative evidence:
{}

只输出 JSON object:
```

### 10.7 `stage07-object_not_found` Stage02 Prompt

```text
你是 HomeMaster V1.2 的 LLM-first 任务理解入口。

目标：把用户自然语言指令转换成一个 TaskCard JSON。
你只负责理解任务，不负责检索记忆、不负责选择候选地点、不负责规划机器人动作。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置、物品状态或执行结果。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可包含中文名或稳定英文别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要，默认用户为 user",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

判断规则:
- 用户要求“看看、确认、还在不在、是否在”某物时，task_type 使用 "check_presence"。
- 用户要求“找、拿、取、拿给我、送来”某物时，task_type 使用 "fetch_object"。
- 取物并交给说话人时，delivery_target 使用 "user"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出澄清问题。
- 如果目标物不明确，target 使用 "unknown_object" 或 "不明确目标"；不要猜药盒、水杯等具体物品。
- 如果用户只说“那个”“这个”“它”且没有具体目标物，必须使用 task_type="unknown"，
  target="unknown_object"，needs_clarification=true，并提出“请问您想确认哪个物品？”这类澄清问题。
- 如果目标物明确，needs_clarification 使用 false，clarification_question 使用 null。
- location_hint 只放用户明说的位置，例如“桌子那边”“厨房”；不要把它当成已验证位置。
- success_criteria 必须描述后续 observation/verification 可以判断的完成条件。
- confidence 使用 0 到 1 之间的小数。

输入上下文:
{
  "utterance": "去厨房找水杯，然后拿给我",
  "user_id": null,
  "source": null,
  "recent_task_summary": null
}

只输出 JSON object:
```

### 10.8 `stage07-object_not_found` Stage03 Prompt

```text
你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": [
    "机器人成功拿到水杯并交付给用户"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}

Runtime negative evidence:
{}

只输出 JSON object:
```

### 10.9 `stage07-distractor_rejected` Stage02 Prompt

```text
你是 HomeMaster V1.2 的 LLM-first 任务理解入口。

目标：把用户自然语言指令转换成一个 TaskCard JSON。
你只负责理解任务，不负责检索记忆、不负责选择候选地点、不负责规划机器人动作。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置、物品状态或执行结果。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可包含中文名或稳定英文别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要，默认用户为 user",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

判断规则:
- 用户要求“看看、确认、还在不在、是否在”某物时，task_type 使用 "check_presence"。
- 用户要求“找、拿、取、拿给我、送来”某物时，task_type 使用 "fetch_object"。
- 取物并交给说话人时，delivery_target 使用 "user"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出澄清问题。
- 如果目标物不明确，target 使用 "unknown_object" 或 "不明确目标"；不要猜药盒、水杯等具体物品。
- 如果用户只说“那个”“这个”“它”且没有具体目标物，必须使用 task_type="unknown"，
  target="unknown_object"，needs_clarification=true，并提出“请问您想确认哪个物品？”这类澄清问题。
- 如果目标物明确，needs_clarification 使用 false，clarification_question 使用 null。
- location_hint 只放用户明说的位置，例如“桌子那边”“厨房”；不要把它当成已验证位置。
- success_criteria 必须描述后续 observation/verification 可以判断的完成条件。
- confidence 使用 0 到 1 之间的小数。

输入上下文:
{
  "utterance": "去厨房找水杯，然后拿给我",
  "user_id": null,
  "source": null,
  "recent_task_summary": null
}

只输出 JSON object:
```

### 10.10 `stage07-distractor_rejected` Stage03 Prompt

```text
你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": [
    "机器人成功将水杯交付给用户"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}

Runtime negative evidence:
{}

只输出 JSON object:
```
