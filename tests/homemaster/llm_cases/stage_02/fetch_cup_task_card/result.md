# Stage 02 Task Understanding

## Summary

- Result: PASS
- Case: fetch_cup_task_card
- Provider: Mimo
- Model: mimo-v2-pro
- Protocol: anthropic
- Logs: /Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_02
- Message: Task understanding completed.
- Utterance: 去厨房找水杯，然后拿给我
- Retry Count: 0

## Request Context

```json
{
  "recent_task_summary": null,
  "source": null,
  "user_id": null,
  "utterance": "去厨房找水杯，然后拿给我"
}
```

## Expected Conditions

```json
{
  "case_name": "fetch_cup_task_card",
  "delivery_target": "user",
  "expected_task_type": "fetch_object",
  "location_keywords": [
    "厨房"
  ],
  "needs_clarification": false,
  "required_checks": [
    "task_type == fetch_object",
    "target mentions 水杯/cup",
    "delivery_target == user",
    "location_hint contains 厨房"
  ],
  "target_keywords": [
    "水杯",
    "杯",
    "cup"
  ],
  "utterance": "去厨房找水杯，然后拿给我"
}
```

## Prompt Attempt 1

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

## Mimo Raw Response Attempt 1

````json
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": ["水杯被成功交付给用户"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
````

## Parsed JSON Payload Attempt 1

```json
{
  "clarification_question": null,
  "confidence": 0.95,
  "delivery_target": "user",
  "location_hint": "厨房",
  "needs_clarification": false,
  "success_criteria": [
    "水杯被成功交付给用户"
  ],
  "target": "水杯",
  "task_type": "fetch_object"
}
```

## Validated TaskCard Attempt 1

```json
{
  "clarification_question": null,
  "confidence": 0.95,
  "delivery_target": "user",
  "location_hint": "厨房",
  "needs_clarification": false,
  "success_criteria": [
    "水杯被成功交付给用户"
  ],
  "target": "水杯",
  "task_type": "fetch_object"
}
```

## Final Contract Checks

```json
{
  "delivery_target_matches": true,
  "has_success_criteria": true,
  "location_hint_matches": true,
  "needs_clarification_matches": true,
  "schema_valid": true,
  "target_matches": true,
  "task_type_matches": true
}
```

## Final TaskCard

```json
{
  "clarification_question": null,
  "confidence": 0.95,
  "delivery_target": "user",
  "location_hint": "厨房",
  "needs_clarification": false,
  "success_criteria": [
    "水杯被成功交付给用户"
  ],
  "target": "水杯",
  "task_type": "fetch_object"
}
```
