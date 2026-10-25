# Dreaming Phase-0 实体归一化 A/B 实验报告（2026-10-25）

## 背景

RoboCasa 记忆探针发现 dreaming 的**跨簇精确去重盲区**：两条 `content_hash`
完全相同的记忆（`d171dedf` / `6209bd8f`）因 enrichment 抽出不同实体名
（`probe-cabinet-42`+`DreamProbeZeta99` vs `cabinet-42`）落入不同实体簇，
簇内判重看不到对方，重复记忆双双存活。

修复方案：`_cluster_hot_memories` step2（实体→邻居查询）与 step3（噪声过滤）
之间插入 Phase-0——热区内实体两两生成候选对（name_sim/vector_sim 腿），
LLM 带记忆证据仲裁同指，命中则 MENTIONS 边重指到 canonical、败方实体
归档 `merged_into:<canonical_id>` 并留可审计 receipt。

## 实验设置

- **数据**：`smoke-003-memory` 共享图的应用层快照（Cypher 导出 1665 节点 /
  1091 边 → 三组独立 Neo4j 实例导入），Qdrant 按 store 目录物理复制。
- **种子**：探针对的两条 add_record（`ca815248` / `6f9cba79`），三组的
  `consolidation_status` 统一重置为 pending。
- **分组**：
  - A 对照：`entity_canonicalization_enabled=false`
  - B′ 影子：enabled + `entity_merge_dry_run=true`（只出建议不落变更）
  - B 执行：enabled + `dry_run=false`
- **端口**：A=27687，B′=27688，B=27689（各自独立 runtime 数据目录）

## 结果

### A（对照）：复现盲区

```
outcome=no_action, clusters=0
MEM: d171dedf active, 6209bd8f active   ← 字节级重复双双存活
```

`cabinet-42` 簇活跃邻居仅 `6209bd8f`（旧副本已归档），size 1 <
`min_cluster_size=2` 被过滤；`probe-cabinet-42`/`DreamProbeZeta99` 同理。
**跨簇判重依然不可达——对照成立。**

### B′（影子）：1 条可审计 merge 建议，零 mutation

```json
{"action":"merge","status":"ok","dry_run":true,
 "reason":"cabinet-42->probe-cabinet-42 [high, name_sim_xtype]
           Both memories are identical and reference the same storage
           location; 'cabinet-42' is a bare-counter variant of the more
           precise 'probe-cabinet-42'.",
 "target_entity_id":"fd6b4b15(cabinet-42)",
 "result_entity_id":"fdd3f68c(probe-cabinet-42)"}
```

- 跨 type 腿命中：`cabinet-42` 被 extractor 错标 `entity_type:"date"`，
  `probe-cabinet-42` 为 `"other"`；同 type 硬过滤会漏掉此对（第一轮实验
  实测漏网），改为 name_sim 腿 + type 作仲裁证据后正确捕获。
- 图终态与 A 完全一致 → dry_run 零污染 ✓
- 候选池=1 对：`DreamProbeZeta99` 无相似名邻居，正确地未进候选。

### B（执行）：merge + 跨簇精确去重，两个 receipt

```
merge:  cabinet-42 --archived--> merged_into:probe-cabinet-42
        6209bd8f MENTIONS 边重指 cabinet-42 → probe-cabinet-42
archive: d171dedf --archived--> duplicate_of:6209bd8f
scopes=1, clusters=1
```

终态：探针对 active 数 **2 → 1**（正本 `6209bd8f` 存活），
实体 `cabinet-42` 归档带 `merged_into:` 理由（可回滚），
`DreamProbeZeta99` 未受影响（正确地保持独立）。

## 假设判定

| 假设 | 结果 |
|---|---|
| H1 B 消除 A 的跨簇重复 | ✅ d171dedf 归档 `duplicate_of:6209bd8f` |
| H2 merge 建议可人工审计 | ✅ receipt 带 canonical/loser/confidence/leg/reason |
| H3 零错并 | ✅ 唯一候选对判为同指且证据充分；B′ 零 mutation |
| H4 成本有界 | ✅ 仲裁 LLM 1 次调用（1 候选对），总时长与 A 同量级（~6min，大头在 app 启动+enrichment 恢复任务，非 phase-0） |

## 实验中发现并修复的两个叠加 bug

1. **entity_type 硬过滤漏候选**：extractor 把 `cabinet-42` 标成 `date`
   （"-42" 似日期）。同 type 门是用抽取噪声过滤抽取噪声——自毁。
   修复：候选只看 name_sim/vector_sim，type 降为仲裁上下文；
   跨 type 命中标记 `name_sim_xtype` 腿。
2. **种子静默失效**：`add_record.consolidation_status=done` 随 store
   拷贝继承 → `_pending_consolidation_add_record_ids` 滤光种子 →
   Phase-0 上游直接 return。实验前重置种子字段才使三组可比。

## 工程附记：远端 store "按目录复制"并不隔离图

`managed_neo4j` 旧版 runtime conf 缺 `server.directories.*` → 数据落在
共享 `NEO4J_HOME/data`（`~/.local/share/homemaster/neo4j/data`），
**所有 smoke/composite store 写同一个 `neo4j` 库**；store 目录下
`runtime/data` 是空壳。A/B 隔离的正解：在拷贝 conf 显式钉
`server.directories.data/logs/run` 到各自 runtime（pid 文件随 run 目录
隔离），基线用 Cypher 导出→导入搬运（共享库有其他活跃使用者，不可停
不可 wipe）。`ab_boot.py`/`graph_io.py`/`dream_ab.py` 见
`robocasa-runs/ab-work/`。

另：根分区 `/` 曾写满（`/tmp` 在其上）→ `runtime_root`/`debug_root`/
`results_root` 已重定向到 `/data1`。

## 实现产物

- `mindmemos/pipelines/dreaming/default.py`：`_canonicalize_scope_entities`
  + 候选生成（name_sim/name_sim_xtype/vec_sim 腿）+ union-find canonical
  链 + `_execute_entity_merge`（边重指+败方归档+残留校验）+ dry_run
  全候选审计（ok/distinct/no_verdict 三类 receipt）
- `components/dreaming/entity_merge.py`：`EntityMergeVerdict`/`Batch` +
  parser；`prompts/EN/dreaming/entity_merge.py`：仲裁 prompt
- `config/algo/dreaming.py`：`entity_canonicalization_enabled`（默认关）、
  `entity_merge_dry_run`、`entity_merge_name_similarity`(0.5)、
  `entity_merge_vector_similarity`(None=关)、`entity_merge_max_pairs`(32)、
  `entity_merge_sample_memories`(3)
- `typing/service.py`：`DreamingActionReceipt` 增加
  `target_entity_id`/`result_entity_id`/`dry_run`（`action:"merge"` 复用既有枚举）
- 测试 9 条新增；本套件 11 绿（唯一失败为 HEAD 既有 IndexError，无关）

## 遗留

- 全库级（184 实体/29 候选对）的批量验收未跑——本实验只覆盖探针热区。
  建议先在 B′ dry-run 模式跑一轮真实 add 批次审计全部建议再放大。
- `entity_merge_vector_similarity` 腿未启用（robocasa 实体无向量）；
  有向量后可开第二候选腿。
- enrichment 恢复任务在实验组启动时会写非确定性实体（实验噪音，
  不进种子热区、不影响 phase-0，但让跨组图 diff 不干净）。
