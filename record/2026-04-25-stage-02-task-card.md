# Stage 02 HomeMaster TaskCard Report

- 实验名称：HomeMaster V1.2 Stage 02 LLM task understanding
- 阶段编号：Stage 02
- 测试日期：2026-04-25
- Git 状态：`agent` 分支，当前工作区为未提交实现变更
- LLM provider：`Mimo / mimo-v2-pro / anthropic`
- 原始日志：`plan/V1.2/test_results/stage_02/`
- Debug cases：`tests/homemaster/llm_cases/stage_02/`

## 通过项

- 新增 `frontdoor` 正式任务理解入口，可将自然语言指令转换成 `TaskCard`。
- `homemaster understand --utterance ...` 可运行，并输出 provider、model 和 TaskCard 关键字段。
- 工程单测使用测试替身覆盖 prompt 上下文、mock Mimo 成功、失败重试、二次失败和 debug 资产脱敏。
- 真实 Mimo 验收 4 个 case 全部通过：
  - `check_medicine_task_card`: `task_type=check_presence`, `target=药盒`, `location_hint=桌子那边`
  - `fetch_cup_task_card`: `task_type=fetch_object`, `target=水杯`, `delivery_target=user`
  - `ambiguous_task_card`: `task_type=unknown`, `target=unknown_object`, `needs_clarification=true`
  - `kitchen_hint_task_card`: `task_type=check_presence`, `target=水杯`, `location_hint=厨房`
- 每个 live case 都有可读 `result.md`，包含完整 prompt、Mimo 原始回复、解析 JSON、校验后的 `TaskCard` 和 checks。
- `src/homemaster` 未导入旧 `task_brain`。

## 测试命令摘要

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/homemaster/test_contracts.py tests/homemaster/test_cli.py tests/homemaster/test_import_boundaries.py tests/homemaster/test_llm_client.py tests/homemaster/test_stage_01_pipeline.py tests/homemaster/test_frontdoor.py
HOMEMASTER_RUN_LIVE_LLM=1 PYTHONPATH=src .venv/bin/pytest -q tests/homemaster/test_stage_02_task_understanding_live.py -m live_api
PYTHONPATH=src .venv/bin/python -m homemaster.cli understand --utterance "去厨房找水杯，然后拿给我"
.venv/bin/ruff check src/homemaster tests/homemaster
```

## 结果

- 工程单测：`20 passed in 0.08s`
- 真实 Mimo 验收：`4 passed in 65.81s`
- CLI smoke：`understand: PASS`
- Ruff：`All checks passed!`

## 失败项

- 无。

## 关键结论

Stage 02 严格通过。HomeMaster 现在有了正式 LLM-first 任务理解入口，能够覆盖查看、取物、模糊澄清和位置提示 4 类基础输入。测试替身只用于工程单测；阶段验收结论来自真实 Mimo 输出。

## 后续观察

- Mimo 在模糊任务上可能先输出较长 thinking block；当前 Stage 02 将任务理解 `max_tokens` 提高到 4096，以保证最终 JSON 可被捕获。
- Stage 03 可以在 `TaskCard` 稳定输出的基础上继续实现 object_memory-only 检索。
