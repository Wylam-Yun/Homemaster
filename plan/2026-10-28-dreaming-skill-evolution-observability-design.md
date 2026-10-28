# Dreaming / Skill Evolution 观测页设计（2026-10-28）

## 背景

V3.8 的记忆后台管线由同一张 `outbox_jobs` 驱动五种 job kind；其中
`implicit_feedback` 已有独立观测页（`plan/2026-10-28-implicit-feedback-observability-design.md`），
而 `archive_dreaming` 与 `skill_evolution` 仍是黑盒：

- **Dreaming**：只能从 session 的 archive-status 徽标看到 pending/completed，
  用户不知道一次 dreaming 吃了哪些 turn、产出/更新了哪些记忆、改前改后是什么；
- **Skill Evolution**：traces 攒批、evolution job、产出的 draft SKILL.md 全部不可见，
  无法回答"这个技能被进化了几次、每次改了什么内容、下批还差几条 trace"。

本设计为两者各加一个**只读观测页**（侧栏英文入口 `Dreaming`、`Skill Evolution`），
不改 vendored MindMemOS、不改任何写路径。

## 数据可得性审计（已对 vendored 源码核实）

可拿：

| 数据 | 来源 |
|---|---|
| 全量 session（含活跃/未归档） | `application.session_manager.session_summaries()`（session_id/title/message_count/updated_at） |
| 每 session 的 archive 集合及状态 | `memory_work_store.archives_for_session(session_id)`；`archives` 表 `UNIQUE(session_id, max_event_seq)` 天然支持一个 session 多次归档 |
| Dreaming job 结果 | `outbox_jobs(kind='archive_dreaming')` 经 `archive.dreaming_job_id` 关联；`result` 含 `dreaming_record_id`/`produced_memory_ids`/`actions`/`raw_verified`/`missing_ids` |
| 每条记忆改动 before/after | 复用 `FeedbackRunInspector.action_diffs`——`memory_item_v1` + `parent_ids` 血缘，dreaming 与 feedback 的 actions wire 形状相同（`{action_id, action, memory_id}`） |
| 归档 turn 范围/种子 | `archives.turn_range_start/end` + 本地 `turns_for_session`/`events_for_turn`（会话节选不依赖服务端） |
| skill 级 trace/批次 | `skill_invocations`（含 `skill_id` 本地名、`cloud_skill_id`、status/score/outcome/task_input、`evolution_batch_id` 血缘）+ `skill_invocation_steps` + `skill_evolution_batches`（`trace_ids_json`/`job_id`/status） |
| 批次进度 | `unconsumed_trace_count(cloud_skill_id)`（攒批阈值 8 已固化于 executor 校验） |
| draft 内容及父版本 diff | **服务端已有读口**：`GET /v1/skills/{id}/versions`（`SkillVersion` 含 `status`/`parent_version_id`/`content_hash`/`version_label`）、`GET /v1/skills/{id}/versions/{vid}/content`（整份 SKILL.md 文本）。`MindMemOSClient` 尚无对应封装——新增两个薄 `_get` 方法。**UNVERIFIED**：端点在 vendored `api/skill_routes.py` 确认存在，但真实部署行为（版本列表是否含 draft、content 是否含 frontmatter）须真机核对 |

拿不到（与 feedback 页同级的诚实边界）：per-action 结构化理由文本、
draft 的语义化"变更说明"（只能给原文 diff，理由在服务端进程内）。

## 范围

- IN：`Dreaming` 页（全量 session 列表 + per-session 逐 archive dreaming 详情 + 改动前后一一对应）、`Skill Evolution` 页（skill 列表 + traces/batches/版本谱系 + draft 与父版本行级 diff）、`MindMemOSClient` 两个只读封装、`MemoryWorkStore` 若干只读 list 方法
- 两页互相独立（不同 inspector/端点/页面，仅共享 ActionDiff 渲染件），实施时可拆两个独立 slice 并行
- OUT：draft 激活/审批门禁（服务端语义即"draft 永不自动激活"，本页只读）、上游 MindMemOS 改动、`turn_add`/`generic_work` 观测、归档的手动触发（已有 `/api/sessions/{id}/archive`）

## 架构

### Dreaming 页

**`GET /api/memories/dreaming-sessions`**（`web/routes/memory.py`，只读）：

```json
{"sessions": [{"session_id", "title", "updated_at",
               "archive_state": "unarchived"|"processing"|"completed"|"failed",
               "archive_count": 2,
               "last_archive_at": null}]}
```

聚合规则（每 session 取其 `archives_for_session`）：无 archive → `unarchived`；
否则按"最活跃"取徽章——任一 archive `pending`（或其 dreaming job 非终态）→ `processing`；
无在途时任一 `failed` → `failed`；全部 `dreamed` → `completed`。
`archive_count>1` 时前端行尾标 `×N`。按 `updated_at` 倒排。本端点不查 qdrant。

**`GET /api/memories/dreaming-sessions/{session_id}`**：

```json
{"session_id", "title",
 "archives": [{"archive_id", "state", "turn_range": [s, e], "max_event_seq",
               "created_at", "job": {"job_id", "status", "attempts", "error",
                                     "dreaming_record_id", "raw_verified", "missing_ids"},
               "seeds": [{"turn_id", "preview"}],
               "actions": [{"action_id", "action", "memory_id", "memory_session_id",
                            "before": {"content","status"}|null,
                            "after": {"content","status"}|null, "missing": bool}]}]}
```

- `seeds`：archive `add_operation_ids` 逐条 `job_by_operation_key` → `turn_id`
  精确定位（1:1，非范围近似），再经本地 trace store 取该 turn 最后一条
  user message 截断 ~200 字作 preview（与 feedback 页 preview 同构）；
  operation_key 找不到对应 turn_add job 时该项 `missing: true`；
- `actions`：job `result.actions` 直接喂 `FeedbackRunInspector.action_diffs()`，
  零新增 diff 逻辑；
- session 不存在于 `session_summaries` → 404 `session_not_found`；无 archive →
  `archives: []`，前端显示"尚无归档"占位 + 会话节选；
- inspector 缺失（memory off/files-only）→ detail 中 `actions` 置 `null` 并带
  `diffs_unavailable` 标记（区别于"有改动但内容读不出"的 per-action `missing`）。

### Skill Evolution 页

**`MindMemOSClient` 新增（薄 `_get` 封装，复用现有错误分类 `_classify_error`）**：

- `list_skill_versions(cloud_skill_id) -> list[SkillVersion]` → `GET /v1/skills/{id}/versions`
- `get_skill_version_content(cloud_skill_id, version_id) -> {version, content}` → `GET /v1/skills/{id}/versions/{vid}/content`

**`MemoryWorkStore` 新增只读方法**：

- `list_archives(*, limit)` — dreaming 列表 join 用（替代每 session 一次 `archives_for_session` 的 N+1 查询）
- `skills_with_invocations() -> [{cloud_skill_id, skill_id, invocation_count, last_sealed_at}]` — `GROUP BY cloud_skill_id`（进度另取权威的 `unconsumed_trace_count`，不自造状态分类）
- `skill_invocations_for_skill(cloud_skill_id, *, limit)` — 按 sealed_at 倒排
- `skill_evolution_batches_for_skill(cloud_skill_id)` — 按 created_at 倒排
- `list_skill_evolution_jobs(cloud_skill_id)` — `list_jobs` 过滤 payload 中 `cloud_skill_id`（或在 batch→job_id 直接关联，优先后者：batch 行有 `job_id` 列）

**端点（`web/routes/skills.py` 或新 `skill_evolution.py`，只读）**：

`GET /api/skill-evolution`：

```json
{"skills": [{"cloud_skill_id", "skill_id",
             "invocation_count", "unconsumed_count", "batch_size": 8,
             "batches_count", "last_batch_at", "last_draft_version_id"}]}
```

`GET /api/skill-evolution/{cloud_skill_id}`：

```json
{"cloud_skill_id", "skill_id", "unconsumed_count",
 "invocations": [{"invocation_id", "status", "score", "outcome",
                  "task_input", "sealed_at", "evolution_batch_id",
                  "steps_count"}],
 "batches": [{"batch_id", "status", "trace_ids", "job": {...},
              "draft_version_id"}],
 "versions": [{"version_id", "parent_version_id", "status",
               "version_label", "content_hash"}]}
```

`GET /api/skill-evolution/{cloud_skill_id}/versions/{version_id}/content`：

```json
{"version_id", "status", "parent_version_id", "content",
 "parent": {"version_id", "content"}|null}
```

- 服务端版本列表/content 拉取失败（server down、UNVERIFIED 行为不符）→
  对应字段 `null` + `versions_unavailable` 标记，本地数据（traces/batches）照常返回；
- 调用方请求的 version 不属于该 `cloud_skill_id` → 404；
- `parent` 为 null 的情形：根版本、父版本被服务端清理——前端显示"无父版本可比"，不是错误。

### 前端

- `router.tsx`/`App.tsx`：route kind `dreaming`、`skill-evolution`；侧栏 `Dreaming`
  紧随「记忆整理」，`Skill Evolution` 紧随「技能」；徽章/状态沿用既有 chip 样式。
- `DreamingPage.tsx`：复刻 `FeedbackRunsPage` 骨架——左列 session 卡片
  （title、状态徽章、`×N`、updated_at；筛选 by 徽章），右列详情：session 元信息 +
  每个 archive 一张卡（状态/turn_range/error + 左「种子」右「改动」双栏）。
  ActionDiff 双栏组件直接复用 feedback 页的；新增只多一个「新增」单栏渲染
  （feedback 页的 add 分支已存在，沿用）。
- `SkillEvolutionPage.tsx`：左列 skill 列表（`skill_id`/cloud id 短码、`n/8` 进度条、
  sealed 数、batch 数），右列选中 skill 分两段：`Traces`（列表可展开 steps）与
  `Evolution`（批次卡 → draft vs 父版本行级 diff；行 diff 用简单 LCS/现成小组件，
  不引大依赖）。
- 进行中对象（processing archive、在途 evolution job）沿用 feedback 页轮询模式。

## 错误与边界

- 两个 inspector 与全部新端点只读；`NullFeedbackRunInspector` 同款空实现供
  files-only/absent-backend 配置用（新增 `NullDreamingInspector`/
  `NullSkillEvolutionInspector` 或复用空 inspector 模式）。
- store 未开/服务未装配 → 503 `*_unavailable`，不 500。
- 聚合徽章是 session 级近似（多 archive 混合态时按最活跃取），前端徽章 hover
  给出逐 archive 分解的 tooltip——不隐瞒中间态。
- `raw_verified=false`/`missing_ids` 非空时在详情卡显著标红——验证失败不许静默。

## 验收

- store 新 list 方法单测：per-instance 断言每个 archive/invocation/batch 的字段，
  禁止"任一存在即过"的聚合断言；
- 路由测试沿用 `test_feedback_runs.py` 桩模式：stub inspector + stub store，
  逐字段断言响应形状、404/503/unavailable 分支；
- 前端 vitest：两页各自的徽章判定表（unarchived/processing/completed/failed/×N）、
  diff 渲染（update 双栏、add 单栏、missing 占位）；
- **真机门（UNVERIFIED 项验收）**：对真 MindMemOS server 跑
  注册 skill → 提交 8 条 trace → evolution batch → `list_versions`/`content` 读回，
  断言 draft 真实存在、content 为完整 SKILL.md、父版本可 diff；
- live run 纪律照旧：stderr 无迟到 traceback、外部终态黑盒核对。
