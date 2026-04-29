# Stage07 报告：distractor_rejected

## 结果

- `run_id`: `stage07-distractor_rejected`
- `final_status`: `failed`（安全失败）
- 日志：`/Users/wylam/Documents/workspace/HomeMaster/log/2026-04-29-stage07-live-scenarios.log`

## 输入 Prompt

- Stage02 prompt 文件：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_02/stage07_stage07-distractor_rejected_task_understanding/input.json`
- Stage03 prompt 文件：
  `/Users/wylam/Documents/workspace/HomeMaster/tests/homemaster/llm_cases/stage_07/stage07-distractor_rejected/stage_03_cases/stage07_stage07-distractor_rejected_memory_rag/input.json`

## 怎么处理

1. Stage02：生成取物任务卡（找水杯并交付给用户）。
2. Stage03：在厨房相关记忆上构建检索 query，选中 `mem-cup-1`。
3. Stage05：首个导航观察后未见水杯，验证失败。
4. 系统生成失败记录并结束，避免错误交付流程继续执行。
5. Stage06：写失败总结与负向事实记忆。

## Mimo 输出

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

Stage06 总结输出（task record）：

```json
{
  "result": "failed",
  "confirmed_facts": [],
  "unconfirmed_facts": ["没有观察到水杯"],
  "failure_summary": "任务失败：没有观察到水杯。",
  "user_reply": "抱歉，机器人没有找到水杯。"
}
```

## 字段含义

- `target_aliases`: 检索扩展词，帮助召回候选。
- `source_filter`: 只检索对象记忆。
- `result=failed`: 本轮任务未完成。
- `user_reply`: 对用户的失败说明，避免给出误导性成功信息。
