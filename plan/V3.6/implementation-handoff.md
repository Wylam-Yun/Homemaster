# V3.6 实施交接

更新时间：2026-09-18  
仓库：`hkust4:/home/haodong2/weilin/red_bird/Homemaster`  
分支：`main`  
归档提交：`1e160272`

## 一句话结论

V3.6 主体代码已经实现，受影响测试为绿色；但尚未达到最终交付条件，也没有提交 V3.6 代码。当前缺口是 replay 崩溃恢复的外部证明、prepared receipt 的 `episode_id` 一致性、失败状态传播、接口审计，以及真实 Qdrant/Neo4j/ALFWorld 黑盒验收。

## 当前工作区

- `1e160272` 是实施前归档点，且明确不包含 V3.6 runtime 修改。
- V3.6 改动仍在工作区，尚未 commit、未 push。
- 最近一次受影响测试：`53 passed, 1 warning in 13.58s`。
- 测试命令：

```bash
.runtime/venv/bin/python -m pytest \
  tests/homemaster/experience \
  tests/homemaster/memory/test_mindmemos_runtime.py \
  third_party/MindMemOS/tests/workers/test_schema_add_episode.py -q
```

warning 是 local Qdrant 对 payload index 的已知提示，不是测试失败。

## 已完成的实现

1. 确定性 episode normalization、事件排序、tool call/result pairing 和 provenance 保留。
2. 排除 transport/debug/thinking 事件，保留任务文本、工具调用、工具结果和完成/失败信息。
3. 三个固定 extractor：`object_location`、`search_observation`、`task_procedure`。
4. `task_procedure.is_executable` 已做 evidence-constrained 校验；失败、partial、unknown 不会自动变成 `ProcedureRecord`。
5. 单 episode ingress、单 planner 调用、单 writer mutation 主流程已接入。
6. `EmbeddedMindMemOS.add_schema_episode()` 已加入，`SessionFinalizer` 已从 vanilla add 迁移。
7. recall projection 保留 domain type、outcome、failure lesson 和 executable flag。
8. 初版 prepared/applied replay receipt 已实现；固定 mutation identity、Neo4j `MERGE` 等幂等基础已存在。
9. 相关测试、架构文档、用户指南、README、CHANGELOG 和 `docs/session-handoff.md` 已更新。

## 当前未闭环问题

### P0：replay 幂等只有实现基础，没有外部故障注入证明

当前流程：

```text
prepared receipt
  -> Qdrant/Neo4j mutation writer
  -> applied receipt
```

需要逐实例验证 Qdrant 写入后崩溃、Neo4j 写入后崩溃、add receipt 写入后崩溃、本地 job completion 前崩溃。每个场景都必须证明 mutation plan、memory/entity/update/archive ID 完全一致，最终没有重复 memory，Qdrant raw readback 和 Neo4j relationship/readback 成功；per-target 结果必须分别断言，不能用 `any` 或全局聚合掩盖失败。

### P0：prepared receipt 的 `episode_id` 有一致性风险

当前 `_build_schema_episode_receipt()` 初始返回的 `episode_id` 仍可能是 `"pending"`，执行结束后才替换内存对象中的 ID，可能造成持久化 prepared receipt 与后续记录不一致。

必须满足：

```text
task episode_id
= schema_episode_prepared receipt episode_id
= add record receipt episode_id
= finalizer job receipt episode_id
```

修复原则：在 prepared receipt 持久化前确定最终稳定的 episode ID，不要只事后修改内存对象。

### P1：domain 类型识别依赖格式化文本

`_build_schema_episode_receipt()` 当前通过事件文本中的类似 `(Type: object_location)` 识别 domain。格式变化会导致 memory ID 归类错误。优先改用结构化 domain metadata；否则至少补严格的 per-domain event/record 映射测试。

### P1：失败传播测试不完整

需要补齐 extractor failure、writer failure、retry exhaustion、cancelled sibling、add-record persistence failure，并确认 finalizer 不会报告 `completed`/`ok`。核心断言是：失败传播到 native add、add record 和 finalizer；`extractor failure != all types not_detected`。

### P1：接口一致性审计未完成

需要审计所有传给 `SessionFinalizer` 的 MindMemOS fake/实现，至少包括 finalizer fake、session-finalization fake、add queue fake、ALFWorld runner fake，确认都实现：

```python
add_schema_episode(...)
```

并增加接口审计测试，防止 alternate implementation 漏方法。

### P2：旧 MindMemOS 测试存在路径风险

仍有测试直接使用 `config/mindmemos/dev.example.yaml`，还需确认从仓库根目录以外执行时的行为，并清理 `/data1/haodong2/...` 一类不应作为运行时前提的旧硬编码路径。当前未阻塞上述 53 个受影响测试，但可能阻塞完整测试或安装后验证。

## 尚未完成的外部验收

以下命令尚未形成 PASS 结论：

```bash
HOMEMASTER_RUN_REAL_AUTOMATIC_RECALL=1 \
.runtime/venv/bin/python -m pytest \
  tests/homemaster/memory/test_automatic_recall_integration.py -q -s
```

真实 ALFWorld schema episode gate 也尚未完成；需先确认测试文件/fixture 是否存在，再运行：

```bash
HOMEMASTER_RUN_REAL_SCHEMA_EPISODE_ALFWORLD=1 \
.runtime/alfworld-venv/bin/python -m pytest \
  tests/homemaster/memory/test_schema_episode_alfworld_integration.py -q -s
```

如果真实 Qdrant、Neo4j、LLM、ALFWorld 资源不可用，必须记录为 `BLOCKED`，不能写成 PASS。

外部验收至少检查：native add 返回状态；Qdrant raw memory readback；Neo4j relationship/readback；`object_location`/`search_observation` 为 native `fact`；`task_procedure` 为 native `experience`；失败/partial/unknown 不可执行；recovery 只保留证据充分的 reusable steps；per-target 外部任务终态。

## 建议接管顺序

1. 修复 prepared receipt 的 `episode_id`，增加四处 ID 相等断言。
2. 增加 replay crash-injection 测试，先用 fake Qdrant/Neo4j 验证 plan 和 ID，再接真实服务 readback。
3. 将 domain 类型从文本解析迁移到结构化 metadata，补 per-domain 映射测试。
4. 补齐 extractor/writer/retry/cancel/persistence failure 传播测试。
5. 完成 `add_schema_episode` 的所有实现和 fake 接口审计。
6. 修复 MindMemOS 测试的路径假设。
7. 运行 affected suite、compileall 和 `git diff --check`。
8. 运行真实 Qdrant/Neo4j automatic recall gate。
9. 确认并运行真实 ALFWorld gate；资源缺失则记录 `BLOCKED`。
10. 做最终 diff review，更新 `docs/session-handoff.md`，再提交，不 push。

## 最终交付约束

- 不 push。
- commit message 必须完全等于：

```text
Replace session vanilla experience writes with one episode-level schema add using fixed object-location, search-observation, and task-procedure extractors; preserve failure lessons separately from externally verified reusable steps.
```

- `CHANGELOG.md` 必须与该 commit message 完全同源。
- 最终运行：

```bash
.runtime/venv/bin/python -m compileall -q src third_party/MindMemOS/src
.runtime/venv/bin/python -m pytest \
  tests/homemaster/experience \
  tests/homemaster/memory/test_mindmemos_runtime.py -q
git diff --check
```

## 当前干预点

- 若优先保证可交付性：先处理 P0 的 ID 一致性和 replay 外部证明。
- 若优先保证真实环境可运行：先检查 Qdrant/Neo4j/LLM/ALFWorld 资源与测试文件，再把缺失资源明确标为 `BLOCKED`。
- 如果发现 `episode_id` 已在 prepared 持久化前稳定生成，应保留证据并把 P0 降级为已验证项，不要只凭代码表面判断。
- 任何真实外部 gate 失败时，先保留失败产物和返回码，再定位根因；不要用单测结果覆盖外部失败。
