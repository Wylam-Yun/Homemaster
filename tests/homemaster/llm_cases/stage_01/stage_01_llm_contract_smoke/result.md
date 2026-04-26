# Stage 01 LLM Contract Smoke

Status: PASS

## Summary

- Result: PASS
- Provider: Mimo
- Model: mimo-v2-pro
- Protocol: anthropic
- Logs: /Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_01
- Message: TaskCard contract smoke completed.
- Utterance: 去桌子那边看看药盒是不是还在。

## Full Prompt Sent To Mimo

```text
你是 HomeMaster V1.2 的任务理解 smoke 测试组件。

只做一件事：把用户一句话转换成 TaskCard JSON。
必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可以使用中文名或中英别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

规则:
- 用户说“看看、还在不在、是否在”时，task_type 使用 "check_presence"。
- 用户说“找、拿给我、取来、送来”时，task_type 使用 "fetch_object"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出 clarification_question。
- 如果不需要澄清，needs_clarification 使用 false，clarification_question 使用 null。
- success_criteria 必须能被后续观察或验证模块判断。
- confidence 使用 0 到 1 之间的小数。

输入:
{"utterance": "去桌子那边看看药盒是不是还在。"}

只输出 JSON object:
```
## Mimo Raw Response

````json
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "桌子那边",
  "success_criteria": ["药盒在桌子那边被观察到"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
````

## Parsed JSON Payload

```json
{
  "clarification_question": null,
  "confidence": 0.95,
  "delivery_target": null,
  "location_hint": "桌子那边",
  "needs_clarification": false,
  "success_criteria": [
    "药盒在桌子那边被观察到"
  ],
  "target": "药盒",
  "task_type": "check_presence"
}
```

## Validated TaskCard

```json
{
  "clarification_question": null,
  "confidence": 0.95,
  "delivery_target": null,
  "location_hint": "桌子那边",
  "needs_clarification": false,
  "success_criteria": [
    "药盒在桌子那边被观察到"
  ],
  "target": "药盒",
  "task_type": "check_presence"
}
```

## Contract Checks

```json
{
  "does_not_need_clarification": true,
  "has_location_hint": true,
  "has_success_criteria": true,
  "target_mentions_medicine_box": true,
  "task_type_is_check_presence": true
}
```

## Expected Conditions

```json
{
  "case_name": "stage_01_llm_contract_smoke",
  "required_checks": [
    "provider returns parseable JSON object",
    "JSON validates as TaskCard",
    "task_type == check_presence",
    "target mentions 药盒/药/medicine",
    "needs_clarification == false",
    "success_criteria has at least one item",
    "location_hint is a non-empty user-stated hint"
  ],
  "schema": "TaskCard"
}
```

## Attempts

### Attempt 1

- Passed: True
- Error Type: None
- Message: None

#### Prompt

```text
你是 HomeMaster V1.2 的任务理解 smoke 测试组件。

只做一件事：把用户一句话转换成 TaskCard JSON。
必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置。

TaskCard schema:
{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可以使用中文名或中英别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}

规则:
- 用户说“看看、还在不在、是否在”时，task_type 使用 "check_presence"。
- 用户说“找、拿给我、取来、送来”时，task_type 使用 "fetch_object"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出 clarification_question。
- 如果不需要澄清，needs_clarification 使用 false，clarification_question 使用 null。
- success_criteria 必须能被后续观察或验证模块判断。
- confidence 使用 0 到 1 之间的小数。

输入:
{"utterance": "去桌子那边看看药盒是不是还在。"}

只输出 JSON object:
```

#### Raw Response

```text
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "桌子那边",
  "success_criteria": ["药盒在桌子那边被观察到"],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}
```

#### Parsed JSON

```json
{
  "clarification_question": null,
  "confidence": 0.95,
  "delivery_target": null,
  "location_hint": "桌子那边",
  "needs_clarification": false,
  "success_criteria": [
    "药盒在桌子那边被观察到"
  ],
  "target": "药盒",
  "task_type": "check_presence"
}
```
