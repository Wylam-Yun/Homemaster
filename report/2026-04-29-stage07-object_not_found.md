# Stage07 报告：object_not_found

## 结果

- `run_id`: `stage07-object_not_found`
- `final_status`: `failed`（安全失败）
- 日志：`/Users/wylam/Documents/workspace/HomeMaster/log/2026-04-29-stage07-live-scenarios.log`

## 输入 Prompt

- Stage02 prompt 文件：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-object_not_found_task_understanding/input.json`
- Stage03 prompt 文件：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-object_not_found/stage_03_cases/stage07_stage07-object_not_found_memory_rag/input.json`

## 怎么处理

1. Stage02/03/04 都成功，给出可执行目标记忆（`mem-cup-1`）。
2. Stage05 在第一个子任务“找水杯”后，验证结果是“没看到水杯”。
3. 系统生成 `FailureRecord`，停止伪执行，不报假成功。
4. Stage06 写失败总结和 `object_not_seen` 事实。

## Mimo 输出

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

Stage06 总结输出（task record）：

```json
{
  "result": "failed",
  "confirmed_facts": [],
  "unconfirmed_facts": ["没有观察到水杯"],
  "failure_summary": "任务失败：在厨房查找水杯时未观察到目标。",
  "user_reply": "很抱歉，我在厨房没有找到水杯。"
}
```

## 字段含义

- `final_status=failed`：任务失败，但走的是受控失败路径（有记录、有解释、不伪造）。
- `unconfirmed_facts`：未被验证通过的关键事实。
- `failure_summary`：对失败原因的人类可读总结。
