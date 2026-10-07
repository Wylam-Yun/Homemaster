# V3.5 修复：压缩产物持久化（append-only 账本 + 持久标记）

**状态：** 设计稿 v2（已经过一轮独立评审，三处阻断问题已修正）
**日期：** 2026-10-06
**参考实现：** opencode `session/compaction.ts`、pi `core/compaction/compaction.ts`、
cline `extensions/context/compaction.ts`（持久化视图 + hash 校验）、
hermes-agent `conversation_compression.py`（就地改写 + 摘要失败兜底链）

---

## 1. 问题（根因）

main 里 `session.messages` 是唯一历史账本，`ContextAssembler._acompact` 用
`session.replace_messages(compacted)` 写回 → 压缩一次永久生效。

v35 把真账本换成 AgentScope 的 `engine_state.context`（AS 引擎只追加），
`session` 降级为**每轮被 `sync_session()` 全量覆盖的镜像**
（`substrate/runtime.py::_sync_session`）。`_acompact` 仍写 `session` →
压缩产物每轮被冲掉 → **越过阈值后每个 model call 都重跑完整压缩**，
每轮固定烧：一次 LLM 摘要调用（`summary_client` = provider，
`factory.py:202`）+ 一次 `context.compaction` 事件 +
`require_recall_after_compaction` → MindMemOS 召回。

判定语义不变量：`prepare` 对**同步后的全量历史**估算
（`context.py:429-444`），一旦越线永远触发——不会自行翻篇。

**AS 写入路径审计（评审已核实，设计成立的前提）**：`engine_state.context`
的全部活跃写入都是尾部 append 或尾 Msg 就地改（reply 合并按 `reply_id`
原地 extend、`append_usage` 累计、`_update_tool_call_state` 改尾部 block）；
中段改写与 `state.context` 重绑定只存在于 `_compress_context_impl`，
已被 `ContextAssemblyMiddleware.on_compress_context` fence 短路
（`middleware_runtime.py:112-125` 不调用 `next_handler`）。
**fence 是单点依赖，必须加存活性测试（§5.8）。**

## 2. 目标设计

```
engine_state.context   —— AS 真账本，只增不改
session.messages       —— 每轮从账本全量重建的镜像（继续如此）
agent_state.compaction —— 持久标记（新增 pydantic 字段，随快照自动往返）：

    CompactionArtifact:
      first_kept_index: int        # canonical session.messages 中连续保留尾的起点
      prefix_hash: str             # 折叠前缀规范化投影的 sha256（见 §3.2d）
      head_messages: list[dict]    # 压缩输出的"头段"逐字副本（见下）
      kind: "summary"

每轮 prepare/aprepare 组装视图（在 provider collect 之后、估算之前）：

    1. conversation_base = provider 产出的会话消息（正常即 session.messages）
    2. 若 artifact 存在且校验通过：
         view = artifact.head_messages + conversation_base[first_kept_index:]
       校验失败 → 丢弃 artifact，view = conversation_base
    3. view = repair_tool_pairs(view)                      # 整 view（pair 修复跨界）
       若 artifact 存在：view = head + micro_transform(tail)
          # strip_old_images + microcompact_tool_results_by_type
          # 只作用于 canonical 尾段——head 是逐字持久产物，
          # micro 摘要非幂等（[navigate] X → [navigate] [navigate] X），
          # 对 head 再跑会双包 stub 并污染下一次折叠的 durable head
    4. 对 view 估算 → 需压缩则 _acompact(view) → 写新 artifact
          # 有 artifact 时传 view_presanitized 跳过 stage1 重跑
```

### 头段（head）与折叠边界的精确定义 —— 评审阻断①修正

`split_preserving_recent_context`（`compact.py:157-185`）的保留区**非连续**：
`recent = messages[:protect_first_n] + messages[split:]`（前 N 条保护前缀 +
连续尾）。默认 `protect_first_n=3`（`config.py:206`，aggressive=1）。

因此压缩输出的结构是 `[新摘要, *保护前缀, *连续尾]`，其中**保护前缀是
canonical 消息的逐字副本，不属于摘要**。据此：

最新 user 消息的保护采用 **hermes 式有界回拉**（`_ensure_last_user_message_in_tail`）：
仅当 `protect_first_n <= latest_user_index < split`（该消息真的会被折掉）
才把切点回拉到它；若它已在保护前缀内（`latest_user_index < protect_first_n`，
逐字留在 recent），pin 不生效——否则单指令会话（一条 user + 几十轮工具）
会永远 `older` 为空、summary 永不触发，只能依赖 micro 砍 tool 结果。
残余边界：`latest_user_index == protect_first_n` 时回拉仍产生空
`older`（不折，指令安全）；hermes 对这类"回拉无收益"场景是把整个
turn-pair 前推折叠 + 确定性逐字快照塞进摘要（`_latest_user_task_snapshot`
+ `_ground_historical_task_snapshot`），HM 未移植该机制——需要 canonical
前 3 条非 user 才会触发，属低概率形状，后续如需可在摘要头注入
deterministic task snapshot。

- `head_messages` = 输出中连续尾起点之前的**全部**消息
  （= `[新摘要] + view[:protect_first_n]`；二次压缩时旧摘要也在头段里
  逐字保留——main 是 **stacking**，不是 summary-of-summaries）；
- `fold_upto` = `_acompact` **输入视图**中连续保留尾的起点下标
  （对 `split` 在输入视图的位置语义，不是 stage1 变换后的位置——
  `repair_tool_pairs` 可能丢消息，`compact.py:134-138`）；
- `first_kept_index = fold_upto + view_offset`，其中
  `view_offset = artifact.first_kept_index - len(artifact.head_messages)`
  （无 artifact 时为 0）。

不变式自证：`len(head) = 1 + protect_first_n` ⟹ `split' ≥ len(head)`
⟹ `fold_upto` 必落在 canonical 尾区，映射闭合。

**`_compact`/`_acompact` 返回值协议**（两个实现同步改）：

```python
(triggered, kind, out_messages, fold_upto, head_len)
# fold_upto: 输入视图中连续保留尾的起点；kind=="summary" 时必填，否则 None
# head_len : out_messages 中属于持久头段的前缀长度（= 1 + protect_first_n
#            + 旧摘要数；由 _acompact 显式返回，禁止调用方做位置反推）
```

### artifact 写入（prepare/aprepare 内，compaction_triggered 分支）

```python
if compaction_triggered and kind == "summary" and fold_upto is not None:
    canonical_idx = fold_upto + view_offset
    agent_state.compaction = CompactionArtifact(
        first_kept_index=canonical_idx,
        prefix_hash=_hash_messages(session.messages[:canonical_idx]),
        head_messages=[m.model_dump(mode="json")
                       for m in out_messages[:head_len]],
        kind="summary",
    )
```

micro（`kind == "micro"`）**不写 artifact、不发事件**——见 §3.2e。

## 3. 改动点（精确到文件/函数）

### 3.1 `src/homemaster/agent/state.py`

- 新增 `CompactionArtifact(BaseModel)`；`AgentState` 加字段
  `compaction: CompactionArtifact | None = None`。
- `head_messages` 存 `list[dict]`（model_dump），使用时逐条
  `Message.model_validate`——union 无 discriminator，手工逐条还原更稳。
- 快照往返自动成立：`agent/session.py:65`（`model_dump(mode="json")`）/
  `:80`（`model_validate`），v1/v2 两条路径共用。

### 3.2 `src/homemaster/agent/context.py`

a) **投影点**：provider collect 循环**之后**、`project_model_tool_context`
   之前插入视图投影（`prepare` ~L422、`aprepare` ~L535 处
   `conversation_messages = rendered` 可能覆盖——投影必须作用于其产物；
   若 provider 产出与 `session.messages` 不等长则丢弃 artifact 防御降级）。
b) **micro 移入视图阶段**：每轮 `repair_tool_pairs` 跑整 view；有 artifact
   时 `strip_old_images` + `microcompact_tool_results_by_type` 只作用于
   canonical 尾段（head 逐字保留——post-review 修正：tool-result 摘要器
   **非幂等**，整 view 重跑会把 head 里的 stub 双包）。`_compact`/
   `_acompact` 增加 `view_presanitized` 参数，artifact 投影轮内跳过
   stage1 重跑。估算输入变为 post-micro 视图（等价于 main"session 已
   micro 化后估算"）。
   **注意**：评审确认 micro 今天只在触发门内运行（`context.py:557`），
   这步是行为变化，不是"与今天一致"——语义上从"每批一次"变成"每轮视图
   卫生"，对应通知语义变化见 e)。
c) **`_compact`/`_acompact` 纯函数化**：删除全部 6 处
   `session.replace_messages`（L684/712/730/790/822/834），改为按
   §2 协议返回 `fold_upto`/`head_len`。`fold_upto` 必须解析为**输入视图**
   坐标：实现时对保留尾首条消息跟踪其在输入视图中的位置（对象身份或
   下标回查），不得用 stage1 变换后的下标。
d) **`_hash_messages` 规范化投影（评审阻断②）**：
   哈希输入**不得**用原始 `model_dump`——merged assistant Msg 的
   `usage` 经 `_save_to_context→append_usage` 每次 model call 累计增长，
   `from_agent_scope` 把它折进拆分出的首条 HM 消息 → 折叠前缀含该消息
   时 hash 每轮失配，bug 在主场景复发。
   规范化：剔除 `usage`、`provider_metadata`、`finish_reason`，并沿用
   `_stable_msg_view`（context.py:60-65）先例剔除 `id/created_at/
   finished_at`；保留 role/text/tool_call(id,name,args)/tool_call_id/
   is_error/data/block metadata 等 canonical 载荷。实现为
   `_canonical_hash_view(message)` → `json.dumps(sort_keys=True)` → sha256。
e) **通知语义**：`compaction_triggered` → `_notify_compaction` +
   `on_compaction`（召回 re-arm）**只在 `kind == "summary"` 且 artifact
   已写入时触发**。micro 变连续视图卫生后不再产生事件（与 main 的
   "每批一次"语义有偏差，属于有意简化：micro 不丢语义内容，无需召回）。
   文档级语义变化，CHANGELOG 写明。

### 3.3 `src/homemaster/substrate/middleware_runtime.py`

- `_assemble`/`sync_session` 不动。
- reactive 路径澄清（评审事实核查①）：reactive 压缩走
  `_recompact → _compact() → aprepare(force_compact="aggressive")`
  （L192-200, 274-293），**不经过** `_pending_force_compact`
  （后者只服务 run 级初始 force_compact，L108/L209）。
  文档按此修正——reactive 重试是全新 prepare，刚写入的 artifact 会被
  **投影**（`[S]+canonical[fk:]`）而非叠加，不产生摘要重复；视触发结果
  可能再被 aggressive 二次压缩覆写为 `[S2]`，无害。
- 顺带记录（pre-existing quirk，不本 PR 修）：`_assemble` 读
  `metrics.estimated_input_tokens` 但字段名实为 `estimated_tokens`，
  getattr 恒 0；`reactive_compact_max_retries` 配置存在但未接线。

### 3.4 `src/homemaster/substrate/snapshot.py`

无代码改动。预期行为修正（评审事实核查④）：存档时
`_strip_context_images`（snapshot.py:99-100）把 image block 换占位文本——
**下标一致、内容不一致**。resume 含折叠前缀内图片的会话时 `prefix_hash`
必然失配 → 降级全量 → 重压缩一次。**这是预期行为**（方向安全），
§5.5 测试按此断言而非"artifact 存活"。

### 3.5 摘要失败策略（对齐 hermes）

`_build_summary`/`_abuild_summary` 失败时：不再 `(False,"none")` 发全量
原文（会进 reactive 重试放大失败），改返回**确定性兜底摘要**
`[Earlier conversation compacted: {len(older)} messages omitted]`。
`abort_on_summary_failure` 语义改为"True = 终止 run"（行为变更），
默认翻转为 False；CHANGELOG 写明。

摘要调用的输出预算与异常分类（对齐 opencode/pi，本轮落地）：

- `summary_max_output_tokens`（`ContextPolicyConfig`，默认 **40960**）是
  摘要调用的**专用输出预算**（opencode 专用常量同款思路），不再继承
  `output_reserve_tokens`——reasoning 模型能把普通 reserve 全花在
  thinking 上导致 text 恒空、摘要路径事实死亡（live 实测命中）。发送前
  按 pi 规则钳到 provider profile 声明的 `max_output_tokens`（若声明）。
- `finish_reason` 非 `"stop"`（length/refusal/content_filter/杂散
  tool_calls）视为**失败不落盘**——无工具摘要调用的唯一健康终态就是
  stop，比 pi 单挡 `length` 更紧；Anthropic 的
  `model_context_window_exceeded` 在 `_normalize_stop_reason` 归一为
  `length`。走 `abort_on_summary_failure` 同一语义（True=raise 终止，
  False=确定性兜底折叠）。sync/async 共用 `_accept_summary` +
  `_summary_client_complete` 收口，两条路径不再有实现漂移。

### 3.6 触发预估升级（已实施：usage 锚定 + 增量估算）

pi/cline/opencode/hermes 共识做法，本轮落地：

- `AgentState.pending_view: UsageAnchor`：每次 prepare/aprepare 末尾记录
  本次构建的视图覆盖——`canonical_len`（覆盖到 canonical 第几条）、
  `artifact_key`（fold 形状：`first_kept_index:prefix_hash[:16]`）、
  `tail_key`（最后一条 canonical 的规范化 hash）、`prefix_key`
  （覆盖区间除尾条外每条消息的规范化 hash 组合——hermes
  `base_prefix_fp` 等价物，中段改写 fail closed）、`fixed_est`（本轮
  非会话份额的启发式估值 = system + prelude + tools）、`tools_key`
  （tool 集 JSON 的 sha256[:16]）。
- `AgentState.usage_anchor`：`AsAgentRuntime._record_usage` 在
  ModelCallEndEvent 把单次真实 `input_tokens`（按 wire family 归一化
  cache 字段后）落到 pending 覆盖上，提升为锚。
- `_estimate_input`：锚有效（`input>0` 且 `canonical_len ≤ len` 且
  `artifact_key` 同且 `tools_key` 同且 `prefix_key` 逐条吻合）时
  `est = anchor.input + est(canonical[delta_start:]) + fixed_est_now
  − anchor.fixed_est`；`tail_key` 不符则 `delta_start -= 1` 把原地
  长大的尾消息重新计价（AS merged-Msg）。否则全量启发式回退。
- 失效即回退的边界：新 fold（artifact_key 变）、tool 集变化
  （tools_key 变——既改 schema token 又重投影已覆盖消息）、canonical
  变短、**任何中段 canonical 改写**（prefix_key 变——strip/splice/
  rewind/外部编辑整类覆盖，不再依赖在改写点手动清锚）。
  prelude/记忆召回与 system prompt 变化走 `fixed_est` 差值调整，不丢锚。
- **估算口径（hermes/pi 移植）**：`estimate_text_tokens_rough` 按
  CJK dense ~1 token/codepoint + 其余 UTF-8 字节/4（修正俄/阿/印地语
  等 2 字节/字语言 ~2x 低估）；`estimate_messages` 计价上 wire 的
  reasoning_content（ThinkingBlock）、tool_calls 的 id/name/JSON 参数、
  ToolResultMessage 的 name/call-id——tool-heavy delta 不再系统性低估。
- 注意：压缩门判的是"不折叠时这次输入的代价"——折叠发生的那一轮
  prepare 仍在旧锚上估（旧视图+增量），artifact_key 换新写在 pending
  里，下次真实 usage 落锚自然对齐折叠后视图。

#### 独立评审处理记录（估算实现）

- **`_record_usage` 按 `handle.model_api_format` 归一化 input**：
  OpenAI 系（`OPENAI_WIRE_FORMATS`）的 `input_tokens` 已含 cached
  share，再加 `cache_read` 每次命中都双倍计数；anthropic 系才是
  uncached + cache_read + cache_creation 三段相加。同时修正
  `provider_usage` 遥测（同一处）。
- **reactive `_compact` 透传 `input_kwargs["tools"]`**：原传
  `all_tool_schemas`（HM schema 形状），与重试请求实际发送的 AS
  function-envelope 指纹不同——tools_key 每轮失配、锚恰好死在折叠后
  最贵的那次 prepare。
- **`ConversationProvider.token_estimate` 死字段清零**：字段无消费方，
  collect() 每轮对全历史做 O(chars) 扫描——锚路径省下的成本被它吃掉
  一半。
- **`_artifact_key` 纳入 head 内容 hash**：同边界重折叠出不同摘要时
  锚不再幸存。
- **中段 canonical 改写由 `prefix_key` 结构性覆盖**（精度轮）：
  hermes `base_prefix_fp` 等价物——锚覆盖区间 `[:-1]` 每条消息一个
  规范化 sha256 指纹再组合；末条归 `tail_key`（merged-Msg 原地长大
  走 delta 重计而非失锚）。替代了精度轮前在 image-strip 点手动清锚的
  做法——整类改写（不止已知点）都 fail closed。
- **delta 计价补 reasoning/tool_calls**（精度轮）：此前 `estimate_messages`
  对 `reasoning_content`/`tool_calls` 盲是系统性低估；现在 id/name/
  sorted-JSON 参数与 ToolResult name/call-id 全部计价（metadata.hm
  包络不上 wire，不计）。
- **文本估算换字节级**（精度轮）：hermes `estimate_text_tokens_rough`
  移植——ASCII chars/4 不变；CJK dense（CJK/Hangul/Kana/Fullwidth
  码位段）~1 token/codepoint 替代 chars/2；其余非 ASCII 按 UTF-8
  bytes/4 修正 ~2x 低估；`errors="replace"` 容忍脏 surrogate。
- **`note_view_usage` 消费 pending_view**：覆盖只描述一次请求，
  防迟到重复事件二次落锚。
- 已记录不修：merged-tail 重计方向为高估（保守、下次 usage 自愈）；
  tools JSON 在 `_tools_key`/`estimate_tools_tokens` 各 dumps 一次
  （成本可忽略）；completion 计价（pi/hermes 的 input+output 总价锚）
  与 AS merged-Msg 原地合并语义对不齐，维持 input-only + delta
  启发式（偏保守方向）。
- 精度轮评审记录（approve，无阻断）——接受项：
  - **repair-membership 盲区（唯一残余 false-pass）**：`repair_tool_pairs`
    会把孤儿 assistant（unmatched tool_calls）从发送视图剔除；锚键按
    canonical 内容指纹，看不见"成员资格"翻转——某条被剔除的孤儿后来
    配对到 result 重返视图时，其份额静默缺失。有界（单条消息、
    单次 prepare，下次 usage 落地自愈，reactive 重试兜底）。修它需把
    post-repair 视图成员资格纳入指纹——坐标成本大于收益，接受。
  - **reasoning_content 双 formatter 都不上 wire**（OpenAI 恒丢
    ThinkingBlock；Anthropic 无 signature 亦丢）：计价是刻意的保守
    偏高而非 wire 声明——未来 formatter 回传 thinking 时不低估。
    ToolResultMessage.name 在 Anthropic wire 上同样缺席，同向处理。
  - **镜像往返指纹漂移**：`data={}`→`None`/`reasoning_content=""`→`None`
    会改变 `_canonical_hash_view` 输出——resume 后一次回退即自愈
    （pending 按镜像后形态重写），近乎不可达。
  - `prefix_key` 每轮 O(covered) 序列化+hash，比被替换的字符启发式
    单消息成本高——精度换 CPU，且只在廉价键全部通过时才执行。
  - Indic/Thai 等非 dense 非 ASCII 文字 bytes/4 仍 ~0.75 低估（较旧
    chars/4 已改善）；`estimate_text("")`→1 与模块函数 0 的 1-token
    差异无消费方。

### 3.7 `middleware_runtime.py` `estimated_input_tokens` quirk（已修）

`_assembler_prepare` 回写 `estimated_context_tokens` 时读了不存在的
字段名 `estimated_input_tokens`（`ContextMetrics` 实为
`estimated_tokens`）——该路径下计数恒为 0，已改为正确字段名。

## 4. 边界与不变量

- **tool 配对**：`_split_index_preserving_groups`（`compact.py:188-203`）
  组对齐不劈对；保护前缀可劈对但 `repair_tool_pairs` 已剥孤儿 →
  投影视图配对完整。另：canonical 自带孤儿残留（legacy/崩溃恢复）会
  在投影重现——**每轮视图阶段无条件跑 `repair_tool_pairs`**（已在 §3.2b
  的 micro 链内，零 LLM 成本）一刀切掉整类风险。
- **hash 覆盖范围**：只对折叠前缀校验；canonical 尾部的 AS 原地修改
  每轮重新投影自动可见。
- **session 镜像不被写**：压缩后 `session.messages` 与压缩前逐条相等
  （纳入测试断言）。`session` 的其他写入者（run 播种 append、
  `application/session.py:610` 队列追加、`:709` resume replace、
  `application/runtime.py:311-316` continuous_taskset strip）全部被下次
  sync 覆盖，与 artifact 无冲突——其中 continuous_taskset 的 strip 在
  AS 路径（engine_state 存在）下**实际是死代码**，顺带在注释/文档记录。
- **artifact 生命周期**：无需显式清理——`run_driver.py:180` 每 run
  `model_copy(deep=True)`、commit 回写、session 轮换新建 runtime；
  hash 自失效覆盖一切 canonical 漂移。
- **`_emit_assistant_events` 水位线**读 engine 不读 session，不受影响。
- **engine 重建弹性**：`engine_state=None` 重播种路径
  （`runtime.py:277-281`）从 session 全量镜像重建 engine——artifact
  摘要从不进 canonical，与 hash 校验共存无恙。

## 5. 测试清单（per-instance 断言）

新增 `tests/homemaster/substrate/test_compaction_durability.py`：

1. **核心回归门**：阈值压缩后第 2、3 个 model call `summary_client`
   调用计数 **== 1**（recording stub：`complete(msgs, system_prompt=,
   max_output_tokens=, temperature=)`；provider 侧用 `ScriptedModel`）。
2. **merged-Msg 边界 hash 稳定性（评审阻断②专测）**：构造单 user +
   多 tool round（engine 侧一个 merged Msg，first_kept 落其拆分区间
   内部）→ 压缩后连续 2+ model call → artifact 不失效、摘要计数 ==1。
3. **protect_first_n 语义**：压缩后视图含 `canonical[:3]` 逐字前缀；
   二次压缩断言 `[S2, S1, ...]` stacking 与 main 逐条相等。
4. **micro 事件语义**：夹具含 >keep_recent 同型 tool result → micro
   触发 → 断言 `context.compaction`/召回次数符合 §3.2e 最终决策
   （若采用"不写 artifact 不发事件"则断言为 0）。
5. **reactive 双投影**：首 attempt 写 artifact、retry 后断言请求内
   摘要恰好 1 条、folded 前缀 0 条。
6. **resume 含图降级**：折叠前缀含 image → snapshot → resume →
   断言恰好 1 次额外摘要调用（降级路径），而非"artifact 存活"。
7. **session 镜像不被写**：压缩后 `session.messages` 逐条相等。
8. **fence 存活**：agent 构造后 `_compress_context_middlewares` 非空
   且首个为 ContextAssembly；`compress_context` 路径下 `state.context`
   不被重绑定。
9. **hash 失效双向**：修改折叠前缀内某消息 text / 前缀内插入一条 →
   各断言 artifact 丢弃 + 重压缩一次。
10. **commit 链存活**：`model_copy`+commit 回写后（不走磁盘）artifact
    存活可用。
11. **三触发等价**：自动阈值 / `force_compact="manual"` / reactive
    路径产物结构一致且均持久。
12. **摘要失败兜底**：summary_client 抛异常 → 视图含确定性占位摘要，
    provider 不收到超限请求（不再裸发全文）。
13. 现有 `_compact`/`_acompact` 单测更新：新返回协议 + 无副作用断言。

## 6. 验收门（DoD，黑盒终态）

- 真实 run（ScriptedModel 超长历史脚本）：压缩后连续 3 轮 model call，
  provider 调用计数 = 3 对话 + **1** 摘要。
- `context.compaction` 事件序列 = 1 次（summary 夹具）；stderr 无 traceback。
- 快照 resume 后续轮摘要调用计数不增长（无图会话）/ +1（含图会话）。

## 7. 明确不做

- 不改 `engine_state.context` 内容；不开 AS 原生压缩（fence 保留+测试）。
- 不改摘要 prompt、切分策略、阈值 0.50。
- `reactive_compact_max_retries` 接线 ——记录为 pre-existing，另行处理。

## 8. 评审处理记录

评审（独立上下文）判定"方向正确，需修改后实施"，三处阻断全部采纳：

| 评审项 | 处理 |
|---|---|
| ① `protect_first_n` 非连续保留区 → `head_messages` 改为"头段逐字副本"，`fold_upto` 按输入视图坐标由 `_acompact` 显式返回 | 已修 §2 |
| ② `prefix_hash` 含 `usage` 挥发字段每轮失配 → 规范化投影剔除挥发字段 | 已修 §3.2d |
| ③ micro 每轮触发 notify/召回风暴 → micro 移入每轮视图阶段，事件只对 summary 持久产物发 | 已修 §3.2b/e |
| 事实核查修正（`_pending_force_compact` 错位、stacking 非 summary-of-summaries、resume 图片剥离必失配、投影点须在 provider 循环后） | 已修 §3.3/§2/§3.4/§3.2a |
| 新增测试用例（merged-Msg hash 稳定、stacking、reactive 双投影、fence 存活、hash 双向、commit 链） | 已并入 §5 |
| `prefix_hash` 保留（无它则前缀改写静默错位）、artifact 放 AgentState（vs `engine_state.middle_context` 等效候选，维持清晰性选择） | 采纳维持 |
| `abort_on_summary_failure` 语义变更需写 CHANGELOG | 已记 §3.5 |
