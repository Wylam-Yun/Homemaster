# HomeMaster V1.2 Stage06 Summary Memory Report

## 结论

Stage06 已完成首版任务总结、证据整理和事实记忆写回链路。真实 Mimo 负责生成 `TaskSummary`；长期写回由程序基于 `EvidenceBundle` 生成 `MemoryCommitPlan`，避免模型自由写长期记忆。

本阶段通过：工程测试、真实 Mimo live summary、secret scan、ruff、diff check。

## 实现内容

- 公共契约新增 `EvidenceRef`、`EvidenceBundle`、`ObjectMemoryUpdate`、`FactMemoryWrite`、`TaskRecord`。
- `TaskSummary` 增加 `failure_summary` 和 `evidence_refs`。
- `MemoryCommitPlan` 增加 fact/event memory、task record、skipped candidates、index stale ids 和 commit id。
- 新增 Stage06 模块：
  - `summary.py`：构造 prompt 并调用 Mimo 生成任务总结。
  - `memory_commit.py`：生成证据包和记忆写回计划。
  - `runtime_memory_store.py`：写 runtime object memory overlay。
  - `fact_memory.py` / `task_record.py`：追加写 fact memory、task records 和 commit log。
  - `stage_06.py`：live case runner 和 debug asset writer。
- `.gitignore` 已忽略 `var/homemaster/memory/`。

## 具体例子

### 取水杯成功

输入是 Stage05 的取水杯执行结果：水杯在厨房餐桌被观察到，并交付给用户。

Mimo 生成的 `TaskSummary` 结论为 success，confirmed facts 包含：

- 水杯在厨房餐桌被观察到
- 水杯已交付给用户

程序生成的 `MemoryCommitPlan`：

- 更新已有 `mem-cup-1`：`belief_state=confirmed`、`confidence_level=high`、`last_confirmed_at=2026-04-26T00:01:00Z`
- 写入 `object_seen` fact
- 写入 `delivery_verified` fact
- `index_stale_memory_ids=["mem-cup-1"]`

### 目标未找到

输入是水杯在厨房餐桌未观察到的失败结果。

Mimo summary 写清失败，不伪造成成功。程序写入 scoped negative fact：本次任务中，在厨房餐桌没有观察到水杯。旧 object memory 只会被标记 stale，不会删除，也不会写“水杯不在厨房”这种永久结论。

## 验证结果

- Stage06 scoped engineering tests:
  - `35 passed, 3 skipped`
- Non-live homemaster tests:
  - `116 passed, 18 deselected`
- Live Mimo Stage06:
  - `3 passed, 2 deselected in 99.09s`
- Secret scan:
  - `1 passed`
- Ruff:
  - `All checks passed`
- `git diff --check`:
  - passed

## Debug 资产

- `tests/homemaster/llm_cases/stage_06/check_medicine_summary_memory_live/`
- `tests/homemaster/llm_cases/stage_06/fetch_cup_success_fact_memory_live/`
- `tests/homemaster/llm_cases/stage_06/object_not_found_summary_memory_live/`

每个 case 的 `result.md` 包含 summary prompt、Mimo 原始回复、`TaskSummary`、`EvidenceBundle`、`MemoryCommitPlan`、写入路径和检查结果。

## 边界

- Stage06 不调用 BGE-M3。
- Stage06 不把 fact/event memory 加入 Stage03 RAG。
- Stage06 不新建 object memory，只更新已有记忆或标记 stale/contradicted。
- 系统失败只写 task record/debug，不写环境事实。
