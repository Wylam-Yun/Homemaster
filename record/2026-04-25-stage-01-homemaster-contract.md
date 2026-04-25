# Stage 01 HomeMaster Contract Report

- 实验名称：HomeMaster V1.2 Stage 01 package, CLI, contract, Mimo smoke
- 阶段编号：Stage 01
- 测试日期：2026-04-25
- Git 状态：`agent` 分支，当前工作区为未提交实现变更
- LLM provider：`Mimo / mimo-v2-pro / anthropic`
- 原始日志：`plan/V1.2/test_results/stage_01/`
- Debug case：`tests/homemaster/llm_cases/stage_01/stage_01_llm_contract_smoke/`
- 可读测试结果：`tests/homemaster/llm_cases/stage_01/stage_01_llm_contract_smoke/result.md`

## 通过项

- 新 `homemaster` 包可导入，`__version__` 输出 `0.1.0`。
- `python -m homemaster.cli --help` 可运行，并显示 `contract-smoke` 命令。
- `TaskCard` contract 可序列化和反序列化，并拒绝非法 enum、空字段、额外字段和越界置信度。
- `src/homemaster` 静态扫描未导入任何 `task_brain` 模块。
- Mock LLM client 和 pipeline 测试通过，debug 资产写入时不保存 API key。
- 真实 Mimo smoke 通过，输出可校验 `TaskCard`：
  - `task_type=check_presence`
  - `target=药盒`
  - `location_hint=桌子那边`
  - `needs_clarification=false`
- `result.md` 已记录完整 Mimo prompt、请求上下文、Mimo 原始回复、解析 JSON、校验后的
  `TaskCard` 和 contract checks。

## 测试命令摘要

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/homemaster/test_contracts.py tests/homemaster/test_cli.py tests/homemaster/test_import_boundaries.py tests/homemaster/test_llm_client.py tests/homemaster/test_stage_01_pipeline.py
HOMEMASTER_RUN_LIVE_LLM=1 PYTHONPATH=src .venv/bin/pytest -q tests/homemaster/test_stage_01_llm_contract_smoke.py -m live_api
.venv/bin/ruff check src/homemaster tests/homemaster
```

## 结果

- 工程单测：`14 passed in 0.08s`
- 真实 LLM smoke：`1 passed in 17.92s`
- Ruff：`All checks passed!`

## 失败项

- 无。

## 关键结论

Stage 01 严格通过。新的 `homemaster` 包、CLI、契约层、最小 Mimo raw JSON client、
prompt 构建、debug 资产和阶段日志都已建立；旧 `task_brain` 主链未被改造，也没有被新链导入。

## 后续观察

- Mimo Anthropic 响应可能只返回 `thinking` block，不返回 `text` block；Stage 01 client 已兼容该形状。
- Stage 02 可以在当前 `TaskCard` contract 和 raw JSON client 上继续扩展正式任务理解 provider。
