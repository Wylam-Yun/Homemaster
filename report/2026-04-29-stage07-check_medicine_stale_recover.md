# Stage07 报告：check_medicine_stale_recover

## 结果

- `run_id`: `stage07-check_medicine_stale_recover`
- `final_status`: `completed`
- 日志：`/Users/wylam/Documents/workspace/HomeMaster/log/2026-04-29-stage07-live-scenarios.log`

## 输入 Prompt

- Stage02 prompt 文件：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-check_medicine_stale_recover_task_understanding/input.json`
- Stage03 prompt 文件：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-check_medicine_stale_recover/stage_03_cases/stage07_stage07-check_medicine_stale_recover_memory_rag/input.json`

## 怎么处理

1. Stage02：识别目标是 `药盒`，位置提示是 `桌子那边`。
2. Stage03：检索候选里有厨房药柜和客厅边桌记忆。
3. Stage04：可靠性判定后选中 `mem-medicine-2`（客厅边桌）。
4. Stage05：两步执行（先到边桌，再确认药盒）。
5. Stage06：写成功总结和 `object_seen` 事实。

## Mimo 输出

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

Stage06 总结输出（task record）：

```json
{
  "result": "success",
  "confirmed_facts": ["结构化 observation 支持该子任务完成", "观察到药盒"],
  "failure_summary": null,
  "user_reply": "药盒在边桌"
}
```

## 字段含义

- `location_hint`: 用户指定位置，模型在检索时会转成 `location_terms`。
- `target_aliases`: 检索补充别名，提升召回率。
- `reason`: 检索意图备注，可为空。
- `confirmed_facts`: 本轮被验证通过的事实集合。
