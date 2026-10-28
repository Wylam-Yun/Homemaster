# 当前 ALFWorld trajectory compile 产物示例

本文展示 HomeMaster 当前 compiler 的输出结构，帮助决定它是否要进入 V3.8。样例由仓库中 `tests/homemaster/experience/test_alfworld_compiler.py` 的合成成功/失败夹具实际调用 compiler 生成，不是 RoboCasa 数据，也不是线上 ALFWorld 轨迹。

## 成功轨迹

输入夹具的动作是：导航到 `cabinet 1` 并观察到 `cabinet reached`，然后执行 `put mug` 并观察到 `mug placed`。compiler 返回结构化 `ProcedureRecord`，不是代码文件或可自动执行脚本：

```json
{
  "memory_type": "procedure",
  "name": "Household task: pick_and_place_simple",
  "sop_id": "alfworld-traj-1",
  "entry": { "page_name": "Household environment", "menu_path": [] },
  "steps": [
    {
      "order": 1,
      "action": "navigate",
      "target": { "page_name": "Household environment", "name": "cabinet 1" },
      "expect": { "visible_text": "cabinet reached" }
    },
    {
      "order": 2,
      "action": "put",
      "target": { "page_name": "Household environment", "name": "mug" },
      "expect": { "visible_text": "mug placed" }
    }
  ],
  "success": { "all_of": ["environment.won == true"] }
}
```

包装记录另带 `outcome=success`、`source_trajectory_id`、源 trace SHA-256、`compiler_version=alfworld-compiler-v2`、`is_executable=true` 和 `validation_status=verified`。当前结构有一个可见质量问题：第二步保留了 `mug`，却没有把输入中的 `cabinet` receptacle 关系放进目标字段；所以 `verified` 不能解释成内容完整、Procedure 已在新任务中成功复用，或已经过人工认可。

## 失败轨迹

失败或 unknown 输入不会生成 Procedure，只返回 `is_executable=false` 的诊断，包含来源 trajectory ID/hash、失败分类与原因。例如测试夹具会产生 `outcome=failure`、`classification=runtime_failure`、`failure_reason=runtime_failure`。诊断没有动作步骤，不可执行。

## 当前写入行为

ALFWorld source writer 先把原始轨迹作为独立 Memory 写入。成功/失败轨迹写入并读回后，当前绑定还会自动排入 compile job；记忆管理页也提供人工重新编译入口。compile job 再把上面的 Procedure 或诊断作为另一条带来源 lineage 的 experience Memory 写入。RoboCasa 没有对应 compiler。V3.8 尚未决定这套双层 ALFWorld 记忆是否继续；决定前不应将它和普通重复 Turn Add 混为一谈，也不应从实施文件清单中默认删除。
