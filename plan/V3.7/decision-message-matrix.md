# 裁决①：字段级消息兼容矩阵

状态：已裁决（Phase 0 先决文档）
范围：`homemaster.agent.messages` ↔ `agentscope.message`，substrate 转换层的唯一事实源。
依据：两侧源码实测（`src/homemaster/agent/messages.py`、`src/agentscope/message/_base.py`、`_block.py`）。

## 0. 结构差异（先于字段）

HomeMaster 把一个 turn 建模为**消息序列**：

```
UserMessage → AssistantMessage(content + tool_calls) → ToolResultMessage ×N → AssistantMessage → …
```

AgentScope 把一个 reply 建模为**一条 assistant `Msg`**，其 `content` 累积本次回复的全部 block：

```
Msg(role=user) → Msg(role=assistant, content=[TextBlock, ThinkingBlock,
                  ToolCallBlock, ToolResultBlock, TextBlock, ToolCallBlock, …])
```

推论（约束所有下游设计）：

- AS **没有 `role="tool"`**。tool_result 是 assistant Msg 内部的 block。
- HM→AS 是**分组转换**：一条 AssistantMessage + 其后连续的 ToolResultMessage 折叠进一条 assistant Msg。
- AS→HM 是**拆分转换**：一条 assistant Msg 展开成 AssistantMessage + ToolResultMessage×N。
- AS `Msg.name` 必填（HM 无发送者概念）→ 转换时填固定名 `"user"`/`"homemaster"`；多 agent 场景 name 保留对方 agent 名（未来 TeamPipeline 红利）。
- AS block 带 `id`/`created_at`/`finished_at`，HM 无 → AS→HM 方向**丢弃**，HM→AS 方向**重新生成**（标 R=regenerated，不算丢失）。

## 1. HM ContentBlock ↔ AS block

| HM 字段 | AS 目标 | 类别 | 规则 |
|---|---|---|---|
| `type="text"`, `text` | `TextBlock.text` | 无损 | 直接搬运 |
| `type="image"`, `source={type:base64, media_type, data}` | `DataBlock(source=Base64Source{data, media_type})` | 无损 | base64 直搬 |
| `source={type:url, url, media_type}`（HM 未产出但 schema 允许） | `DataBlock(source=URLSource{url, media_type})` | 无损 | AnyUrl 校验失败 → fail closed |
| `metadata.path`（image 的磁盘路径，见 `from_image_path`） | `DataBlock.name` | 转换 | name ← basename(path)；完整 path 进 `metadata["hm"]["source_path"]` |
| `metadata` 其余键 | `ToolResultBlock.metadata["hm"]["block_meta"][i]` / 所在 Msg `metadata["hm"]["block_meta"][i]` | 转换 | 按 block 序号存侧袋，HM→AS→HM 还原 |
| —（HM 不存在） | `ThinkingBlock.thinking` | 转换 | AS→HM：并入 `AssistantMessage.reasoning_content`（多块 join `\n`）；redacted_thinking extras 进 provider_metadata |
| —（HM 不存在） | `HintBlock` | 不映射 | hint 是 AS 运行时注入物，不进 canonical 历史；转换遇 hint → **跳过 + 计数告警** |
| AS block `id`/`created_at`/`finished_at` | —（HM 不存在） | 再生 | HM→AS 由 AS 默认值重生成；round-trip 断言豁免这三字段 |

## 2. HM ToolCall ↔ AS ToolCallBlock

| HM 字段 | AS 目标 | 类别 | 规则 |
|---|---|---|---|
| `id` | `ToolCallBlock.id` | 无损 | 工具调用 ID 是跨层关联锚点（permission/result/resume 全靠它），必须原值 |
| `name` | `ToolCallBlock.name` | 无损 | 普通名直通（AS 无 stable_id 概念；stable_id 留在 tool wrapper 层） |
| `arguments: dict` | `ToolCallBlock.input: str` | 转换 | `json.dumps(arguments, sort_keys=True, ensure_ascii=False)`；AS→HM `json.loads` 失败 → fail closed（不用 json_repair——canonical 数据不许"修复"） |
| —（HM 不存在） | `ToolCallBlock.state` | 转换 | 从结果配对推导：有配对 tool_result → `FINISHED`；无 → `PENDING`。ASKING/SUBMITTED 只在 AS 运行时存活，HM 快照语义里是 "unresolved" |
| —（HM 不存在） | `suggested_rules` | 不映射 | AS permission 子系统内部态，HM permission 走自己的 store |

## 3. HM AssistantMessage ↔ AS Msg(role=assistant)

| HM 字段 | AS 目标 | 类别 | 规则 |
|---|---|---|---|
| `content` (text/image blocks) | TextBlock / DataBlock 序列 | 无损 | 见 §1 |
| `reasoning_content: str|None` | 一个 `ThinkingBlock`（置于 content 之前，与 provider 输出顺序一致） | 转换 | AS→HM 时所有 ThinkingBlock join `\n` 回填 |
| `tool_calls` | `ToolCallBlock` 追加到 Msg.content 尾部 | 转换 | 顺序必须保持：content blocks → tool_call blocks →（若有 ToolResultMessage 则其 blocks 继续）|
| `finish_reason: str|None`（provider stop reason） | `Msg.metadata["hm"]["finish_reason"]` | 转换 | AS `finished_reason` 是**回复级**终态（error/interrupted/exceed_max_iters），≠ provider stop reason，不可互相映射 |
| `usage: dict` | `Msg.usage = Usage(input_tokens=…, output_tokens=…)` | 转换 | HM dict 的 `cache_*`/`total_tokens` 等额外键进 `metadata["hm"]["usage_extra"]`；Usage 无 `total_tokens` 字段 |
| `provider_metadata: dict` | `Msg.metadata["hm"]["provider_metadata"]` | 无损 | 原 dict 透传 |
| `role="assistant"` | `Msg(role="assistant", name="homemaster")` | 转换 | name 常量化；AS→HM 时丢弃 name（单 agent 无歧义） |

## 4. HM ToolResultMessage ↔ AS ToolResultBlock

| HM 字段 | AS 目标 | 类别 | 规则 |
|---|---|---|---|
| `tool_call_id` | `ToolResultBlock.id` | 无损 | AS 用 block.id == tool_call.id 配对 |
| `name` | `ToolResultBlock.name` | 无损 | — |
| `content` (list[ContentBlock]) | `output: list[TextBlock|DataBlock]` | 转换 | HM 只允许 text/image → 恰好落在 AS 联合类型内；`str` output 不参与 canonical（AS 内部 delta 累积才用） |
| `is_error: bool` | `ToolResultBlock.state` | 转换 | `is_error` → `ERROR`；否则 `SUCCESS`。INTERRUPTED/DENIED 由 AS→HM 方向回译（见 §6 status 表） |
| `data: dict|None`（**含 backend_attempted 等 machine-only 字段**） | `ToolResultBlock.metadata["hm"]["data"]` | 无损 | **关键不变量**：CLAUDE.md 纪律要求 `backend_attempted` 等字段必须在 canonical 结果里存活——挂在 block.metadata 侧袋，不进模型可见 output |
| `provider_metadata: dict` | `ToolResultBlock.metadata["hm"]["provider_metadata"]` | 无损 | 透传 |

## 5. 角色与边界

| 项 | 裁决 |
|---|---|
| AS `role="system"` | HM 消息序列无 system（system_prompt 在快照顶层）。转换遇 system Msg → **拒绝**（ValueError），不许静默降级为 user |
| HM `role="tool"` | 见 §0：不产 AS 消息，产 block |
| 空 content 的 assistant Msg | AS 允许 `content=[]`；HM→AS 保留 |
| 尾部悬挂 tool_call（无配对 result） | 保留为 `PENDING` ToolCallBlock；快照层的"尾部脱锚 assistant 截断"纪律照旧由 `save_snapshot` 在序列化前执行 |

## 6. Round-trip 断言规格（substrate 测试的验收表）

**H→A→H**（canonical 方向）：对任一 HM `Message` 序列 `msgs`：

```
from_agent_scope(to_agent_scope_grouped(msgs)) == msgs
```

逐字段相等，**豁免项**：
- AS block id/created_at/finished_at（再生）
- AS Msg.id/created_at（再生）
- `ToolCallBlock.input` 字符串排序差异（断言 `json.loads` 后相等，且 `arguments` dict 逐键相等）

**A→H→A**（仅约束 HM 可表达的子集）：AS Msg 经一轮转换后，text/thinking/tool_call/tool_result/data 五类 block 的语义内容相等；`name`、`Msg.id`、block 时间戳、hint block、suggested_rules、structured_output、error 不进入断言域（HM 无对应面）。断言用 `assert_semantic_equal(a, b)` 辅助函数逐类比较，不用 `==`。

**Fail-closed 清单**（转换器必须抛错而非降级）：
1. AS→HM 遇 `role="system"`（hint block 不在此列：hint 是 AS 运行时注入物，合法存在于 context，转换时跳过 + 计数告警）
2. AS→HM 遇 ToolCallBlock.input 非法 JSON
3. HM→AS 遇未知 `ContentBlock.type`（schema 外的扩展块）
4. HM→AS 遇 source.type ∉ {base64, url}
5. 任一方向的 tool_result 找不到配对 tool_call（id 引用断裂）

## 7. 实现落点

`homemaster/substrate/messages.py`：

```python
def to_agent_scope(messages: Sequence[HMMessage]) -> list[Msg]: ...
    # 分组：assistant + 尾随 tool_result → 单 Msg

def from_agent_scope(messages: Sequence[Msg]) -> list[HMMessage]: ...
    # 拆分：assistant Msg → AssistantMessage + ToolResultMessage×N

def msg_to_agent_scope_one(m: HMMessage) -> Msg  # 单条（user/不入组的 assistant）
def assert_semantic_equal(a: Msg, b: Msg) -> None  # 测试辅助
```

转换器无状态、纯函数；所有 HM 私有语义走 `metadata["hm"]` 侧袋（单一命名空间，禁止散落裸键）。
