# V3.3 ALFWorld 执行轨迹与经验记忆 Spec

## 0. 文档状态

- 版本：V3.3
- 状态：设计稿，待用户确认后进入实施计划
- 日期：2026-09-08
- 适用仓库：`/home/haodong2/weilin/red_bird/Homemaster`

本文档是 V3.3 的设计真理源。实现、测试、架构文档、用户指南和 CHANGELOG 必须与本文档保持同源；如果实现过程中需要改变下面的锁定语义，先修改本 Spec，再修改代码。

## 1. 背景与问题

当前 ALFWorld 已经通过 `AlfworldApplicationEntry` 使用统一 HomeMaster composition，并在 episode 结束时关闭 `application.session(...)`。Session 关闭会把应用 trace 交给已有 `SessionFinalizer`，历史运行已经产生过 MindMemOS 的 `experience`、`episodic` 和 `tool_trace` 记录。

当前仍有三个缺口：

1. Session Finalizer 读取的是通用 application trace，不能稳定拿到 ALFWorld runner 在 session 关闭之后才计算的 `success`、`classification` 和环境最终状态。
2. `trajectory.md` 和 `summary.json` 是文件证据，但没有统一的机器可验证轨迹记忆记录把 `success/failure/unknown` 固定下来。
3. 现有 `src/homemaster/experience/success_path.py` 是面向浏览器运维工单的实验性编译器，不能直接处理 ALFWorld 动作，也没有把编译结果与 ALFWorld 原始轨迹建立 lineage。

因此，模型可能在召回时看到一条失败轨迹，却无法从结构上判断它失败了；或者把诊断轨迹误当作可执行成功经验。

## 2. 目标

### 2.1 必须达成

任意一个 ALFWorld episode 或 taskset subtask 在结束时必须：

1. 保存一份不可变的原始执行轨迹，成功、失败和结果未知都保存。
2. 保存机器可读的 `outcome`，取值固定为 `success`、`failure` 或 `unknown`。
3. 保存 `classification`、`failure_reason` 和 `final_environment_state`，且来自 runner/adapter 的真实环境终态，不来自模型口头声明。
4. 将轨迹与 task、goal type、episode/subtask、代码和数据身份绑定。
5. 允许从轨迹生成第二类记忆：可执行成功经验，或不可执行的失败/诊断经验。
6. 所有编译结果必须指向唯一源轨迹，并能在源轨迹被篡改时拒绝发布。
7. 模型召回结果必须明确显示成功、失败或未知；失败和未知记录不能被投影为推荐执行步骤。
8. Web Memory Management 提供受限的编译入口，只允许对已验证的 ALFWorld trajectory 执行编译。

### 2.2 预期收益

- 成功轨迹可以被提炼成可复用的 ALFWorld procedure，减少重复探索。
- 失败轨迹可以告诉下一次探索哪些动作顺序、目标解析或状态假设曾经失败。
- 诊断轨迹可以保留 Harness、Provider 和 runtime 问题，但不会污染模型的动作建议。
- 原始轨迹永不被编译结果覆盖，后续可以更换编译器重新生成经验。

## 3. 非目标

V3.3 不做以下事项：

- 不修改 ALFWorld 外部动作语义、THOR grounding、reset transaction 或评分规则。
- 不把模型自然语言回答当作成功依据；最终成功仍由 ALFWorld `won=true` 和现有终态门决定。
- 不删除或覆盖现有 `trace.jsonl`、`model_trace.jsonl`、`summary.json`、`trajectory.md` 和 event artifacts。
- 不把失败轨迹编译成 `is_executable=true` 的 procedure。
- 不让模型提交 tenant、session、run、evidence ref、objectId、坐标、pose 或内部 trace ref 作为记忆参数。
- 不在 V3.3 中做全量历史轨迹回填；历史回填另立迁移方案，必须使用原始文件证据并逐条验证。

## 4. 核心术语

### 4.1 原始执行轨迹

一个 ALFWorld episode 或 taskset subtask 从开始到停止的完整、有序记录，包括模型消息、工具调用、工具结果、环境动作、环境反馈、图片引用和最终环境状态。它是所有派生记忆的唯一事实来源。

### 4.2 环境最终状态

`final_environment_state` 表示 runner 停止执行时从 Adapter 读取到的环境状态。它不等同于 terminal state：环境可能因为步数上限、Provider 错误或 runtime 失败而被停止，但没有进入 ALFWorld 自己的 `done=true`。

### 4.3 轨迹结果

`outcome` 是对整条轨迹的三态判断：

| 条件 | outcome | 说明 |
|---|---|---|
| 权威最终状态确认 `won=true` | `success` | 任务成功完成 |
| 最终状态可读，且确认未完成 | `failure` | 包括模型失败、达到限制、正常未完成 |
| 最终状态不可读、状态矛盾或基础设施终止 | `unknown` | 不能把不确定性伪装成失败 |

`done=true` 只表示环境或 runner 结束，不能单独推出 `success`。

### 4.4 可执行经验

经过编译、schema 校验、源轨迹 hash 校验和存储终态回读后，允许模型作为动作建议参考的派生记忆。只有成功轨迹才能产生 `is_executable=true` 的 ALFWorld procedure。

### 4.5 失败经验

从失败或未知轨迹中提取的不可执行经验，用于避免重复错误、选择新的探索方向和诊断基础设施问题。它可以被召回，但必须显示为 warning/diagnostic，不能进入推荐步骤列表。

## 5. 记忆分层

V3.3 固定保存两类逻辑记忆；两类都必须携带结果标签。

### 5.1 Trajectory Memory

每个 episode/subtask 一条，所有 outcome 都写入：

```json
{
  "schema_version": 1,
  "domain": "alfworld",
  "record_kind": "trajectory",
  "trajectory_id": "<stable id>",
  "source_session_id": "<session id>",
  "episode_id": "<environment episode id>",
  "taskset_id": null,
  "subtask_index": null,
  "goal_type": "put_object_in_receptacle",
  "outcome": "success",
  "classification": "agent_success",
  "failure_reason": null,
  "is_executable": false,
  "source_trace_path": "<internal artifact path>",
  "source_trace_sha256": "<64 lowercase hex>",
  "final_environment_state": {
    "won": true,
    "done": true,
    "step_index": 7,
    "invalid_action_count": 0,
    "goal_condition_success_rate": 1.0,
    "inventory": ["mug 1"]
  },
  "steps": []
}
```

约束：

- `outcome`、`classification`、`is_executable` 必须是结构化字段，不只写在 content 文本中。
- `source_trace_sha256` 绑定原始轨迹文件或 canonical trajectory payload；源文件变化后编译必须 fail closed。
- `final_environment_state.won`、`done`、`step_index`、`invalid_action_count` 和 goal success rate 保留真实值，不做推断性覆盖。
- 内部 objectId、pose、raw event ref 可以留在受 ACL 保护的 artifact 中，但不能进入 provider-facing memory content。
- `unknown` 结果必须保留具体原因，例如 `execution_state_uncertain` 或 `runtime_failure`。

Trajectory Memory 的模型可见投影必须包含明确标签：

```text
[ALFWorld 执行轨迹]
[结果：成功|失败|未知]
[可执行建议：否]
```

### 5.2 Derived Experience Memory

Trajectory Memory 是源；Experience Memory 是派生结果。两者通过 `DERIVED_FROM` 或等价 lineage 绑定。

成功轨迹的派生结果：

```json
{
  "domain": "alfworld",
  "record_kind": "experience",
  "outcome": "success",
  "is_executable": true,
  "source_trajectory_id": "<trajectory memory id>",
  "procedure": {
    "name": "将物体放入容器",
    "inputs": ["object", "container"],
    "steps": [],
    "success": ["environment.won == true"]
  }
}
```

失败轨迹的派生结果：

```json
{
  "domain": "alfworld",
  "record_kind": "experience",
  "outcome": "failure",
  "is_executable": false,
  "source_trajectory_id": "<trajectory memory id>",
  "failure_reason": "target_not_visible",
  "avoid": ["不要在目标不可见时直接执行 manipulation"]
}
```

成功和失败都可以是 experience，但只有 `outcome=success` 且通过独立终态校验的经验允许 `is_executable=true`。

## 6. ALFWorld 数据流

### 6.1 单 episode

```text
reset and publish initial state
  -> model/provider/tool loop
  -> AlfworldTraceWriter writes raw events and model events
  -> runner reads adapter.current_state and computes outcome/classification
  -> runner writes canonical alfworld.episode_finished event
  -> runner writes summary, trajectory artifact and source hash
  -> application session close
  -> ALFWorld trajectory writer admits the record to application-owned memory queue
  -> Qdrant + Neo4j write and raw readback
  -> optional compile job creates derived experience
```

`alfworld.episode_finished` 必须在 `entry.end_session()` 之前写入；否则 Session Finalizer 无法看到结果标签。

### 6.2 Taskset

一个 taskset session 可以包含多个 subtask。每个 subtask 必须拥有自己的 trajectory ID、subtask index、goal type、outcome、classification、final environment state 和 source trace hash。Taskset 汇总不能用其中最佳 subtask 的结果覆盖其他 subtask；任何召回、统计或编译操作都必须 per-subtask 处理，再生成 taskset aggregate。

### 6.3 Session Finalizer 边界

现有通用 Session Finalizer 继续保留，用于保存对话级 session experience；ALFWorld 结果记录必须通过明确的 domain payload 或独立 ALFWorld trajectory writer 注入，不能让 finalizer 从自然语言 `Session ended: ...` 反推成功与否。

推荐边界：

1. Runner 负责产生权威 `AlfworldEpisodeResult` 和 canonical trajectory payload。
2. `AlfworldApplicationEntry` 或 application session close handler 负责把 payload 提交给 application-owned memory writer。
3. 通用 Finalizer 可以继续保存 dialogue experience，但不能替代 Trajectory Memory。
4. 任一记忆写入失败必须留下明确失败 receipt；不能把已写成功的文件或数据库终态伪装成全部成功。

## 7. 编译设计

### 7.1 输入

编译器只接受：

- `record_kind=trajectory`
- `domain=alfworld`
- 已通过 source trace hash 校验的 trajectory memory
- 具有完整 `outcome` 和 `final_environment_state`
- 当前 tenant/session/run 作用域内可见的准确 memory ID

不接受模型手写 trajectory ID、旧 run 的 opaque ref、只存在于 search 文本中的 ID 或没有源 artifact 的记录。

### 7.2 成功轨迹编译

成功轨迹可以生成 ALFWorld ProcedureRecord，动作集合至少覆盖：

```text
observe, navigate, take, open, close, put, use,
slice, heat, cool, clean, verify
```

编译器必须：

- 折叠重复 observe、失败探索和无效动作，但不能删除影响成功语义的关键状态变化。
- 保留任务 goal semantics 和最终验证条件。
- 禁止把 objectId、坐标、pose、snapshot_id、raw event ref 或绝对路径写进可执行 procedure。
- 对每个步骤生成非空 expect/verification 条件。
- 生成结果中的 source trajectory ID、source hash、compiler version 和 validation status。
- 通过 ProcedureRecord schema 后，独立读取源轨迹和新记忆，确认 lineage、content、record 和状态一致。

### 7.3 失败和未知轨迹编译

失败和未知轨迹不得生成可执行 ProcedureRecord。允许生成非执行型经验摘要，必须包含结果标签、真实 classification、failure/diagnostic 原因、最后状态和下一步探索建议。若无法可靠提炼原因，保留原始 Trajectory Memory 即可，不能凭空生成建议。

### 7.4 编译任务状态

编译操作必须有持久 job receipt：

```text
queued -> running -> succeeded
queued -> running -> failed
```

`succeeded` 的必要条件：源轨迹 hash 仍匹配；编译输出通过 schema 和安全字段校验；派生 memory 成功写入外部存储；Qdrant raw readback 成功；Neo4j lineage readback 成功；`outcome` 和 `is_executable` 与源轨迹一致。任何一步失败都不能发布 active executable experience。

## 8. Web Memory Management

当前页面保持已有的 active/archived、搜索、类型筛选、详情和历史能力；V3.3 增加 ALFWorld trajectory 的识别和编译入口。当前 `main` 的页面仍是只读查看，编译 API/UI 属于本版本新增能力。

每条 ALFWorld 轨迹必须显示：结果、classification、goal type、episode/subtask、是否可执行、是否已有派生经验和编译状态。失败和未知记忆必须与成功经验区分。

建议新增：

```text
POST /api/memories/{memory_id}/compile
GET  /api/memory-compilations/{job_id}
```

接口不接受请求体中的 source path、session ID、run ID、tenant ID 或 evidence ref；这些由服务端从当前 scope 和 memory record 派生。重复编译同一 source hash 和 compiler version 必须幂等。HTTP 成功只表示 job admission；最终成功以 job receipt 和 memory/lineage readback 为准。

## 9. 方案选择

### 方案一：只扩展现有 Session Finalizer

让 finalizer 接收 ALFWorld outcome，并把整段 session 作为 experience 写入。改动少，但原始轨迹和派生经验混在一个 pipeline，失败/未知的结构化语义容易被 LLM 文本化，编译重跑和 lineage 边界不清晰。

### 方案二：ALFWorld 专用 Trajectory Writer，加通用 Finalizer 旁路

Runner 生成 canonical trajectory；专用 writer 写 Trajectory Memory；通用 Finalizer 继续写对话 experience；编译器从 Trajectory Memory 生成 Derived Experience。增加一个清晰的 ALFWorld memory adapter，但复用 application-owned lifecycle，不复制 MindMemOS/Neo4j 生命周期，失败处理、重编译和测试边界最明确。

**推荐方案：方案二。**

### 方案三：统一重构所有 native memory type

把 trajectory、procedure、fact、episodic、tool_trace 全部重构为通用 DomainRecord。架构更统一，但会扩大 V3.3 的数据模型和迁移风险，影响已有 memory tools、Web projection、history 和 package data。

### 方案四：只保存文件轨迹，之后离线批量导入

实现简单，但下一次 run 无法自动召回刚刚产生的失败/成功经验，也无法验证在线 memory composition，不符合本版本目标。

## 10. 预计修改范围

### ALFWorld

- `src/homemaster/benchmarking/alfworld/runner.py`
- `src/homemaster/benchmarking/alfworld/tracing.py`
- `src/homemaster/benchmarking/alfworld/types.py`
- 新增 `src/homemaster/benchmarking/alfworld/trajectory_memory.py` 或等价 domain adapter
- taskset runner 的 per-subtask 收尾和结果事件

### Memory / Experience

- `src/homemaster/memory/models.py`
- `src/homemaster/memory/serialization.py`
- `src/homemaster/memory/mindmemos_runtime.py`
- `src/homemaster/memory/management.py`
- `src/homemaster/experience/session_finalization.py`
- `src/homemaster/experience/success_path.py` 或新增 ALFWorld 专用编译器

### Web / Tests / Docs

- `src/homemaster/web/app.py`、`src/homemaster/web/schemas.py`
- `web/src/api/http.ts`、`MemoryPage.tsx`、`MemoryDetailDialog.tsx` 和对应测试
- ALFWorld runner/tracing/entry、schema、finalizer、Qdrant/Neo4j readback、Web API/UI、interface audit 测试
- 实施后同步架构文档、ALFWorld 用户指南、Memory 用户指南、README、CHANGELOG、`docs/session-handoff.md`

## 11. 验收标准

### 11.1 内部一致性门

- success/failure/unknown 判定表逐分支测试通过。
- `done=false` 且 `won=false` 不会被误判为 success。
- runner 在 session close 前写入 `alfworld.episode_finished`。
- 单 episode 和 taskset 每个 subtask 的 outcome、classification、final state、trajectory ID 分别正确。
- 编译器拒绝失败/未知轨迹生成 executable procedure。
- 源轨迹 hash 变化时编译拒绝发布。
- provider-facing content 和 Web projection 都显示结果标签。
- 失败轨迹不会因为 native memory type 相同而被投影为成功经验。

### 11.2 外部终态黑盒门

至少执行一条真实成功 episode 和一条真实非成功 episode，逐条核对：

1. ALFWorld worker 返回码和环境返回状态。
2. 轨迹文件真实存在、可解析、source hash 与原始事件重算一致。
3. Trajectory Memory 的 Qdrant raw readback 包含完整 outcome、classification、final state 和 source hash。
4. Neo4j 存在 trajectory 对应 Source/lineage 关系。
5. 成功轨迹编译后，新 experience 为 active 且 `is_executable=true`，指向准确源轨迹。
6. 失败或 unknown 轨迹的派生经验为 `is_executable=false`，不能出现在可执行 procedure 列表。
7. 重启新的 memory reader 后仍能读回相同 record/content/lineage。
8. 每个 episode/subtask 单独通过，不使用 best/any 聚合判据掩盖失败实例。
9. 进程、Neo4j、Qdrant lock、ALFWorld worker、Unity/Xvfb 和端口全部清理，stderr 无 traceback 或迟到外部错误。

### 11.3 Web 黑盒门

- Web API 返回 compile job admission 后，能通过 job 查询看到最终 succeeded/failed。
- succeeded job 在页面重新读取后显示派生经验和源轨迹关系。
- failure/unknown 页面显示结果标签和不可执行状态。
- 错误类型、错误 outcome、旧 run ID 和 hash tamper 分别得到明确失败状态，且没有外部 memory mutation。

## 12. 可观测性与隔离

关键 JSONL 事件至少包括：

```text
alfworld.episode_finished
memory.trajectory.queued
memory.trajectory.stored
memory.trajectory.readback_verified
memory.experience.compile.started
memory.experience.compile.completed
memory.experience.compile.failed
```

每个事件保留 session/run/episode/subtask identity、trajectory/job ID、outcome/classification、source hash/compiler version、duration、external return status、readback status 和 error code。日志只做观测，不能替代轨迹文件、Qdrant、Neo4j 和 ALFWorld 环境的真实终态验证。

轨迹记忆沿用当前 tenant/session/run scope；旧 run 的 trajectory ID 不能影响当前编译。公开模型内容不包含 objectId、pose、坐标、内部路径、evidence ref、provider credential 或数据库连接信息。失败轨迹可以被召回作为 warning，但不能授予任何额外环境或工具权限。

## 13. Definition of Done

V3.3 只有在以下条件全部满足后才算完成：

1. 本 Spec 已经用户确认，实施计划已写入 `plan/V3.3`。
2. 所有 episode/subtask 都能生成带真实结果标签的 Trajectory Memory。
3. 成功、失败、未知三类结果在文件、memory record、provider content 和 Web projection 中语义一致。
4. 成功轨迹可编译成可追溯的 executable experience；失败/未知轨迹只能生成 non-executable memory。
5. Qdrant、Neo4j、source artifact、lineage 和 compile job 均有独立终态验证。
6. 相关单测、集成测试、Ruff、compileall、diff-check 和真实 ALFWorld 黑盒门通过。
7. 架构文档、ALFWorld 用户指南、Memory 用户指南、README、CHANGELOG、`docs/session-handoff.md` 同步更新。
8. 若发现新的非显而易见坑，追加 `docs/pitfalls.md`，并将正向规则补入 `CLAUDE.md`。

