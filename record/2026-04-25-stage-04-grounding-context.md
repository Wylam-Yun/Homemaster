# HomeMaster V1.2 Stage 04 Grounding Context

## Summary

Stage 04 已落地可靠执行记忆判定与 `PlanningContext` 组装。程序从 Stage 03
`MemoryRetrievalResult.hits` 中按顺序评估 hit，不调用 Mimo、不使用 reranker、不使用旧
`CandidatePool`。

有可靠执行记忆时生成 `GroundedMemoryTarget`；没有可靠执行记忆时返回
`PlanningContext(selected_target=None)`，并通过 runtime summary 和 planning notes 告诉
Stage 05 需要探索寻找。

## Results

- `ground_cup_target`: grounded, selected `mem-cup-1`
- `ground_medicine_target`: grounded, selected `mem-medicine-1`
- `ground_negative_evidence_target`: ungrounded, selected `None`
- `planning_context_for_orchestration`: grounded, selected `mem-cup-1`
- `ungrounded_all_hits_invalid`: ungrounded, selected `None`
- `ungrounded_no_memory_context`: ungrounded, selected `None`

## Reliability Rules

- excluded/invalid hits cannot be selected.
- missing `memory_id` / `room_id` / `anchor_id` / `viewpoint_id` is unreliable.
- viewpoint and anchor must exist in `world.json`.
- target metadata must match the task target.
- location conflict, low confidence, and stale belief become weak leads, not selected targets.
- sorting only decides evaluation order; it does not override reliability checks.

## Artifacts

- Debug cases: `tests/homemaster/llm_cases/stage_04/`
- Raw notes/trace: `plan/V1.2/test_results/stage_04/`
