# HomeMaster V1.2 Stage05 Skill 执行闭环记录

日期：2026-04-26

## 结论

Stage05 首版已实现高层编排与 mock skill 执行闭环：

- Mimo 生成 `OrchestrationPlan`。
- 执行期 Mimo 只选择 `navigation` 或 `operation`。
- `verification` 由程序在 action skill 后自动调用。
- 失败会生成 `FailureRecord`，并保留未来 event memory 扩展字段。
- 首版不接真实导航、真实 VLA、真实 VLM。

## 真实 API 验收

命令：

```bash
HOMEMASTER_RUN_LIVE_LLM=1 PYTHONPATH=src .venv/bin/pytest -q tests/homemaster/test_stage_05_orchestration_live.py -m live_api
```

结果：

```text
5 passed in 330.06s (0:05:30)
```

覆盖 case：

- `check_medicine_orchestration_live`
- `fetch_cup_orchestration_live`
- `ungrounded_exploration_live`
- `find_cup_step_decision_live`
- `pick_cup_step_decision_live`

## 工程验证

命令：

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/homemaster/test_contracts.py tests/homemaster/test_execution_state.py tests/homemaster/test_orchestration_validator.py tests/homemaster/test_skill_registry.py tests/homemaster/test_skill_selector.py tests/homemaster/test_executor.py tests/homemaster/test_recovery.py tests/homemaster/test_stage_05_debug_assets_do_not_contain_secrets.py tests/homemaster/test_import_boundaries.py
```

结果：

```text
44 passed in 0.09s
```

命令：

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/homemaster -m 'not live_api'
```

结果：

```text
102 passed, 15 deselected in 1.81s
```

命令：

```bash
.venv/bin/ruff check src/homemaster tests/homemaster
git diff --check
```

结果：均通过。

## Debug 资产

可读报告写入：

```text
tests/homemaster/llm_cases/stage_05/
```

原始 JSONL 和 trace 写入 ignored 目录：

```text
plan/V1.2/test_results/stage_05/
```
