# HomeMaster V1.6 上下文压缩方案

Date: 2026-06-24
Status: 已定稿 — 所有决策已确认，等待实施

---

## 一、问题陈述

### 1.1 致命问题（来自 audit-and-refactor-spec.md 11.1）

当前压缩流程：

```
context_assembler._compact()
  → split_preserving_recent_context(messages)
  → build_basic_summary(older)   ← 每条消息保留前 500 字符，纯字符串截断
  → build_compaction_summary_message(summary)
```

`build_basic_summary` 不调 LLM，只做空白压缩 + 500 字符截断。压缩后：

- 工具调用参数全部丢失
- 工具结果全部丢失
- 推理链断裂
- 多模态结果（图像）丢失

后果：agent 压缩后会重复执行已经做过的操作。Task state snapshot 是结论（"当前任务：fetch_object 水杯"），不是过程证据（"已经搜过 A、B、C 三个位置都没有"）。

### 1.2 现状已核实事实

| 事实 | 证据 |
|------|------|
| `build_basic_summary` 不调 LLM | `compact.py:172-183` 纯字符串操作 |
| `microcompact_old_tool_results` 定义但零调用 | `compact.py:25`，src/ tests/ 零引用 |
| `compact_summary_prompt.txt` 写好了规则但从未被加载 | `prompt_loader.py:23` 仅注册 ID，无 load 调用 |
| `ContextPriority` 四档（REQUIRED/IMPORTANT/AUXILIARY/TRACE_ONLY）已定义 | `context_items.py:12-16`，后两档零引用 |
| PRELUDE 不被压缩 | `_compact` 只处理 `conversation_messages` |
| 视觉工具返回图像 | 老人居家场景下 robot_observe 类工具会大量返回图像 |
| ProviderUsage 每次迭代覆盖而非累加 | `generic_runtime.py:251-255` |
| `max_wall_clock_minutes` 定义但从未检查 | `model_config.py:78`，generic_runtime.py 零引用 |
| reactive compaction 只重试一次 | `generic_runtime.py:202` |

### 1.3 产品场景特征（关键约束）

HomeMaster V1.6 是面向**老人居家**的具身 agent：

1. **视觉工具高频调用**：老人会频繁让 agent "看"（眼镜在哪、水开了吗、门锁了吗），每张图 ≈ 1000-2000 tokens
2. **图像信息时效性强**：1 分钟前的厨房状态对当前决策意义不大
3. **长程任务真实存在**：拿水杯、找眼镜这类任务可能 20+ 次工具调用
4. **用户不会输 `/compact`**：但 `/compact` 作为开发/看护人工具仍然需要
5. **单 session 多任务**：一个 session 跨多个任务，除非 `/new` 重置
6. **多渠道接入**：CLI / 飞书 / 微信都接入同一个 session，渠道无关

---

## 二、设计哲学

### 2.1 借鉴 Hermes 的核心思路

参考 `/Users/wylam/Documents/workspace/hermes-agent`（成熟方案，含 10+ 个真实 issue 修复）：

- **四阶段管线**：免费修剪 → 边界确定 → LLM 摘要 → 组装清理
- **位置 + 类型自动分级**，不打显式 priority 标签
- **工具结果摘要化**而非简单截断
- **迭代式摘要**：新摘要把上次摘要喂回去
- **强制最新用户消息进尾部**（issue #10896）
- **摘要 LLM 失败回退链**
- **工具对完整性兜底**（`_sanitize_tool_pairs`）

### 2.2 不照搬 Hermes 的部分

- ❌ 不做反抖动保护（阈值触发模式下是伪问题，见 §2.4）
- ❌ 不做工具调用参数 JSON 内截断（HomeMaster 还没遇到 provider 兼容性问题）
- ❌ 不做可插拔 ContextEngine 抽象（只有一个引擎，过度抽象）
- ❌ 不做离线轨迹压缩器（那是训练数据工具）
- ❌ 视觉工具不返回 description 字段（延迟敏感）

### 2.3 加做（HomeMaster 特有）

- ✅ **历史图像剥离作为必做**（HomeMaster 的图像密度比 Hermes 高）
- ✅ **工具结果按类型摘要化的规则表**（不同工具不同策略）
- ✅ **reactive compaction 激进压缩回退**（替代 Hermes 的反抖动）

### 2.4 关于反抖动的决策

Hermes 在"连续两次压缩各节省 < 10%"时跳过压缩。HomeMaster **不做**这个逻辑，理由：

HomeMaster 是阈值触发（context 占用 ≥ context_window × 50% 时压）。每次触发压缩时，context 都已经涨回到阈值附近，意味着两次压缩之间 context 有显著增长，第二次压缩时可压缩区必然足够大。反抖动保护的场景在 HomeMaster 不会出现。

但 **reactive compaction**（LLM 返回 context length error 后的紧急压缩）需要二次回退——见 §3.5。

### 2.5 分级原则

| 级别 | 处理方式 | 对应位置/类型 |
|------|---------|-------------|
| **重要** | 不压缩 | 头部（system + 前 3 条）+ 尾部（最近 ~10%）+ 最新用户消息 |
| **中等** | micro-compaction + 进 LLM 摘要（占位符形式） | 头尾之间的工具结果 → 按类型摘要化 + 去重 + 图像剥离 |
| **不重要** | LLM 摘要 | 头尾之间剩余内容（在中等处理后仍超阈值时） |

LLM 摘要时**参考**重要+中等的上下文（task state、最近工具结果、上次摘要），生成更紧凑相关的摘要。

---

## 三、压缩管线详细设计

### 阶段 0：入口防御（写入消息时，压缩之外）

**位置**：`generic_runtime.py` 工具结果写入 session 之前

**逻辑**：
- 工具结果文本 > 阈值（默认 4000 字符）→ 溢出到磁盘 `$TMPDIR/homemaster-results/{tool_use_id}.txt`
- 消息内容替换为预览（前 500 + 后 500 字符）+ 文件路径引用

**归属**：PR2（依赖文件系统路径管理 + 工具结果 ID 体系）

### 阶段 1：免费修剪（无 LLM，每次触发都执行）

#### 1.1 历史图像剥离 ★★★ 最关键

- **规则**：保留最近 2 张图像，更早的图像替换为文字占位符
- **占位符格式**：`[image stripped — robot_observe @ iter {n}, room={room}]`
- **元信息来源**：通过 `tool_call_id` 在消息列表中回溯找到对应的 `AssistantMessage.tool_calls[i].arguments`，提取工具参数（如 `room`）作为占位符元信息。`ToolResultMessage` 不持有 args，需要向前查 tool_call。
- **保留张数**：2
- **session 层 vs transport 层分工**：session 层保留最近 2 张图像（压缩时用，避免全剥离）；transport 层发送时只发最新 1 张（mimo 约束）。两者独立——session 保留 2 张用于上下文完整性，transport 截到 1 张用于实际发送。
- **信息损失策略**：**直接折叠**。视觉工具不返回 description，agent 在信息不足时自己重新调用 `robot_observe`。这和老人场景下 agent 本来就会反复观察的产品逻辑自洽。

#### 1.2 工具结果按类型摘要化

按工具定制规则表：

| 工具 | 默认摘要格式 | 保留完整的条件 |
|------|------------|--------------|
| `robot_observe` | `[observe] room={room}, iter={n} → image stripped` | 最近 2 次保留原图 |
| `memory_retriever` | `[memory] query="{q}" → {N} hits, top-1: {hit[:200]}` | 最近 1 次保留完整 |
| `robot_navigate` | `[navigate] → {room} ({success/fail})` | 永远摘要化 |
| `robot_verify` | `[verify] {target}: {pass/fail} - {reason[:100]}` | 最近 2 次保留完整 |
| 默认（未注册工具） | `[{tool}] N lines output, last 10: {tail}` | 最近 3 次保留完整 |

**实现要点**：
- 注册表机制 `ToolResultSummarizer`，按 `tool_name` 路由
- 默认 fallback 是"保留最后 10 行"（用户偏好）
- 摘要规则集中维护，不分散到各工具代码

#### 1.3 去重

- 同一工具名 + 同一 args 的工具结果 → 旧的标 `[Duplicate tool output — see iter N]`
- 哈希用 `MD5(tool_name + sorted_args_json)`

### 阶段 2：边界确定

#### 2.1 头部保护

- `system_prompt`（始终保护）
- 前 `protect_first_n` 条消息（默认 3）—— 一般是初始用户指令 + 第一轮响应

#### 2.2 尾部保护

- 从末尾倒推 `tail_token_budget`（默认 = context_window × 10%）
- 至少保留 3 条消息（即使超 budget）
- **强制最新用户消息在尾部**：如果 budget 边界切掉最新 user message，向前推到包含它
- **边界对齐**：如果边界落在 `assistant_with_tool_calls + tool_results` 组中间，向前推到组之前

#### 2.3 可压缩区

= 头部和尾部之间的所有消息

### 阶段 3：LLM 摘要（条件触发）

#### 3.1 触发条件

- 阶段 1（免费修剪）执行后，重新估算 token
- 如果**仍然超阈值** → 触发 LLM 摘要
- 如果阶段 1 已经节省到阈值以下 → 跳过 LLM（省钱省延迟）

渐进式触发，不是每次都调 LLM。

**重要**：阶段 1 后的重新估算必须用 `TokenEstimator.estimate_messages()`（含图像 token），不能用旧的 `estimate_messages_tokens`（忽略图像）。当前 `context_assembler.py:124` 用 `estimate_messages_tokens` 按 `estimate_text_tokens` 累加，完全忽略图像 token。阶段 1 的核心操作就是剥离图像，如果估算忽略图像，阶段 1 的节省量就是 0，永远不会触发 LLM 摘要。

#### 3.2 输入（LLM 摘要的上下文）

LLM 摘要的 prompt 同时看到：
- **要压缩的内容**：阶段 2 确定的可压缩区**全文**（含阶段 1 摘要化后的工具结果占位符，如 `[tool result compacted] ...`）。这让 LLM 能看到摘要化的工具结果，更好地串联上下文。
- **task state snapshot**（重要，作为参考）
- **failure summary**（重要，作为参考）
- **上次摘要**（如果有，作为迭代基础）
- **尾部消息全文**（尾部只占 ~10%，token 成本可接受。让摘要 LLM 直接看尾部全文，避免两次 LLM 调用）

每项的注入方式：
- `task_state_snapshot` / `failure_summary`：LLM 摘要调用时作为 system prompt 的一部分注入（不是 CONTEXT_PRELUDE，因为摘要 LLM 调用不经 ContextAssembler）
- **上次摘要**：作为可压缩区的一部分，喂给摘要 LLM
- **尾部全文**：同样作为可压缩区的一部分，摘要 LLM 能直接读取

#### 3.3 输出格式

沿用并扩展现有 `compact_summary_prompt.txt` 的结构化模板：

```markdown
## Active Task        # 用户当前正在做的任务
## Goal               # 目标
## Completed Actions  # 编号列表，带工具名
## Active State       # 当前位置、持有物品、机器人状态
## In Progress        # 压缩触发时正在做什么
## Blocked            # 未解决的错误
## Key Observations   # 关键观察（视觉/记忆检索结果）
## Resolved Questions # 已回答的问题（防止重复工作）
## Pending User Asks  # 未回答的问题
## Critical Context   # 必须保留的具体值
```

**prompt 加载方式**：LLM 摘要调用时通过 `load_prompt(PromptId.COMPACT_SUMMARY)` 加载 prompt 模板（`prompts/loader.py`，原 `prompt_loader.py` 迁入此模块）。

**模板末尾必须保留的约束 section**：

```
## Critical Constraints
- Preserve all evidence references (object locations, observations, tool results)
- Do not invent objects or actions not present in the source
- Keep tool call arguments verbatim where referenced
- If uncertain, omit rather than fabricate
```

#### 3.4 输出放置

- 摘要包装为一条独立消息（user role）
- 前缀：`[CONTEXT COMPACTION - REFERENCE ONLY]`
- 后缀：`--- END OF CONTEXT SUMMARY ---`（Hermes 经验：防止弱模型把摘要当新指令）
- 放置位置：头部之后、尾部之前（替换掉可压缩区原内容）

#### 3.5 失败回退

```
配置的摘要模型
  → 失败 → 回退主模型
  → 还失败 → abort_on_summary_failure?
              True  → 中止压缩，保留原消息，30-60 秒冷却
              False → 静态占位符 "[Summary unavailable. N messages dropped]"
```

HomeMaster 默认 `True`（保守，避免静默丢消息）。

### 阶段 4：组装清理

#### 4.1 工具对完整性

- 压缩后扫描，修复孤儿：
  - assistant 的 tool_call 被压掉了 → 删掉对应 tool_result
  - tool_result 被压掉了 → 插入 stub `[Result from earlier — see context summary]`
- 复活并接入现有死函数 `sanitize_tool_pairs`

#### 4.2 最终图像剥离

阶段 1 的图像剥离主要针对可压缩区。阶段 4 再做一次全局扫描，确保最终消息列表里图像数量 ≤ 2。

### 3.5 reactive compaction 激进压缩回退

替代 Hermes 的反抖动逻辑。当 LLM 返回 context length error 时：

```
context length error
  → 第一次压缩（正常参数：尾部 10%、头部 3 条）
  → 重新估算，仍超长？
  → 第二次压缩（激进参数：尾部 5%、头部 1 条、强制 LLM 摘要）
  → 仍超长？
  → 报错给上层（emit 高优先级事件，让看护人/上层 UI 决定是否 /new）
```

当前代码（`generic_runtime.py:202`）只允许一次重试就失败。改为两次重试 + 激进参数。

**`force_compact_next` 类型**：当前是布尔值，无法区分正常/激进模式。改为：

```python
# ContextAssembler
force_compact_next: Literal[None, "normal", "aggressive"] = None
```

- 正常 reactive → 设 `"normal"`，`_compact()` 用默认参数（尾部 10%、头部 3 条）
- 二次回退 → 设 `"aggressive"`，`_compact()` 用激进参数（尾部 5%、头部 1 条、强制 LLM 摘要）

摘要后重新估算，若仍超阈值 → 触发 reactive compaction aggressive 模式（尾部 5%、头部 1 条）。

### 3.6 `session.replace_messages` 调用

新 `_compact` 仍调用 `session.replace_messages(compacted_messages)` 把压缩结果写回 session。否则下次 `prepare()` 拿到未压缩的 `session.messages`，压缩白做。

---

## 四、Session 模型

### 4.1 单 session 多任务

- Session 是长生命周期容器，跨多个任务
- 每个用户/渠道对应一个独立 session（按 user_id 区分）
- 任务在 session 内顺序执行，前一个任务的对话历史保留在 session 中
- `/new` 清空 session，开始新会话

### 4.2 渠道无关

- CLI、飞书、微信都是同一个 session 的接入渠道
- 消息从任何渠道进来都进同一个 session
- `/compact`、`/new` 作为**消息内容**识别（不是命令行参数），任何渠道都能触发

### 4.3 持久化

- 当前阶段：单进程内存持久（进程不退出，session 在内存里）
- 进程退出 = session 丢失（可接受，未来再做磁盘持久化）
- `/compact`、`/new` 在进程内即可生效

---

## 五、配置项（新增/调整/删除）

在 `ContextPolicyConfig` 中：

**新增**：

```python
class ContextPolicyConfig(BaseModel):
    # 现有字段（保留的） ...

    # 分级压缩参数
    protect_first_n: int = 3
    tail_token_ratio: float = 0.10
    aggressive_tail_token_ratio: float = 0.05  # reactive 第二次压缩用
    aggressive_protect_first_n: int = 1
    keep_recent_images: int = 2
    keep_recent_tool_results_per_type: dict[str, int] = {
        "robot_observe": 2,
        "memory_retriever": 1,
        "robot_verify": 2,
    }
    default_keep_recent_tool_results: int = 3

    # LLM 摘要
    enable_llm_summary: bool = True
    summary_model: str | None = None  # None 表示用主模型
    abort_on_summary_failure: bool = True
    summary_failure_cooldown_seconds: int = 60

    # reactive compaction
    reactive_compact_max_retries: int = 2  # 当前是 1，改为 2

    # 阶段 0（磁盘溢出，PR2）
    enable_disk_overflow: bool = False
    tool_result_overflow_threshold_chars: int = 4000
```

**删除的旧字段**（被新设计取代）：

- `preserve_recent_agent_steps` —— 被 `protect_first_n` + `tail_token_ratio` 取代
- `preserve_recent_user_turns` —— 同上
- `recent_tail_ratio` —— 被 `tail_token_ratio` 取代（值从 0.20 改为 0.10）
- `image_token_estimate` —— 被 `TokenEstimator.estimate_image` 取代

---

## 六、Token 估算

### 6.1 设计目标

- **不绑死 MiMo**：换 provider 时只改 adapter，不改压缩逻辑
- **harness 接入方便**：新 provider 实现一个 estimator 类即可，零样板代码
- **优先用真实 usage**：provider 返回的 usage 是真理源，零误差
- **本地估算是兜底**：首轮或 provider 不返回 usage 时用本地估算

### 6.2 MiMo Vision Token 算法（已实测）

通过 4 组实测数据反推：

| 图像尺寸 | 像素数 | input_tokens 增量 |
|---------|-------|------------------|
| 16×16 | 256 | ~0 |
| 128×128 | 16K | 8 |
| 512×512 | 262K | 248 |
| 1024×1024 | 1M | 1016 |
| 2048×512 | 1M | 1081 |

**结论**：token 与像素总数成正比，与边长比例无关。

```
vision_tokens ≈ width × height / 1000
```

对照 Anthropic 官方公式 `w×h/750`，MiMo 系数约 1/1000，比 Anthropic 便宜约 25%。

### 6.3 MiMo Usage 字段实测

测试请求返回：

```json
"usage": {
  "input_tokens": 66,
  "output_tokens": 15,
  "cache_read_input_tokens": 192
}
```

**关键发现**：`cache_read_input_tokens` **不计入** `input_tokens`，但**确实是 context 占用**。MiMo 支持 prompt caching，缓存命中的部分单独统计。

```
真实 prompt 占用 = usage.input_tokens + usage.cache_read_input_tokens
```

如果只看 `input_tokens` 会严重低估（66 vs 258，差 4 倍）。

### 6.4 TokenEstimator 接口设计

```python
# providers/token_estimator.py

class TokenEstimator(Protocol):
    """每个 provider 自己实现 token 估算。
    Protocol 定义 5 个抽象方法 + 1 个有默认实现的方法。
    calibrate/calibrated_estimate 在 BaseTokenEstimator 具体类里。
    estimate_messages 在 BaseTokenEstimator 提供默认实现（基于 estimate_text + estimate_image 累加）。
    """

    def estimate_text(self, text: str) -> int:
        """文本 token 估算。"""
        ...

    def estimate_image(self, *, base64_data: str, media_type: str) -> int:
        """图像 token 估算。从 base64 头解码尺寸，按 provider 公式算。
        不同 provider 算法不同：
        - MiMo: w × h / 1000
        - Anthropic: w × h / 750
        - OpenAI: 170 + 85 × tiles
        """
        ...

    def estimate_json(self, value: object) -> int:
        """JSON（工具 schema 等）token 估算。"""
        ...

    def estimate_messages(self, messages: list[Message]) -> int:
        """消息列表 token 估算（含图像）。
        BaseTokenEstimator 提供默认实现：遍历 messages，对每个 ContentBlock
        调 estimate_text（text block）或 estimate_image（image block）累加。
        provider 如有更精确算法可覆盖。
        """
        ...

    def real_usage(self, usage: dict[str, int]) -> int:
        """从 provider 返回的 usage dict 提取真实 prompt 占用。
        不同 provider 字段不同：
        - Anthropic/MiMo: input_tokens + cache_read_input_tokens
        - OpenAI: prompt_tokens
        """
        ...

    def supports_real_usage(self) -> bool:
        """这个 provider 是否返回可靠的真实 usage。
        默认返回 True，provider 可覆盖。
        """
        return True

    # calibrate/calibrated_estimate 不在此 Protocol——它们在 BaseTokenEstimator 具体类
```

### 6.5 图像尺寸解码（共享工具函数）

图像尺寸从 base64 头解码，**零侵入**——不需要工具结果带 width/height 元信息。

```python
def decode_image_dimensions(base64_data: str) -> tuple[int, int]:
    """从 base64 字符串解码图像尺寸。支持 PNG/JPEG。
    只解码前 200 字节，O(1)。
    """
    header = base64.b64decode(base64_data[:200])

    if header.startswith(b"\x89PNG"):
        # PNG: IHDR 在第 16-24 字节
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        return width, height

    if header.startswith(b"\xff\xd8"):
        # JPEG: 扫描 SOF0 marker
        ...
        return width, height

    raise ValueError("Unsupported image format")
```

放进 `TokenEstimator` 基类作为工具方法，所有 provider 共享。

### 6.6 MiMo 实现

```python
class MimoTokenEstimator(BaseTokenEstimator):
    def estimate_text(self, text: str) -> int:
        cjk = sum(1 for c in text if "一" <= c <= "鿿")
        non_cjk = max(0, len(text) - cjk)
        return max(1, math.ceil(cjk / 2) + math.ceil(non_cjk / 4))

    def estimate_image(self, *, base64_data: str, media_type: str) -> int:
        width, height = decode_image_dimensions(base64_data)
        return max(1, (width * height) // 1000)  # MiMo 实测系数

    def estimate_json(self, value: object) -> int:
        return self.estimate_text(json.dumps(value, ensure_ascii=False, sort_keys=True))

    def real_usage(self, usage: dict[str, int]) -> int:
        return usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)

    def supports_real_usage(self) -> bool:
        return True

    # estimate_messages 继承自 BaseTokenEstimator（默认实现足够，无需覆盖）
```

### 6.7 校准机制 + estimate_messages 默认实现

基类提供默认校准逻辑（滑动平均，避免单次抖动）+ `estimate_messages` 默认实现：

```python
class BaseTokenEstimator:
    _calibration_ratio: float | None = None

    def calibrate(self, estimated: int, real: int) -> None:
        if estimated > 0:
            ratio = real / estimated
            self._calibration_ratio = (
                self._calibration_ratio * 0.7 + ratio * 0.3
                if self._calibration_ratio else ratio
            )

    def calibrated_estimate(self, raw_estimate: int) -> int:
        if self._calibration_ratio:
            return int(raw_estimate * self._calibration_ratio)
        return raw_estimate

    def estimate_messages(self, messages: list[Message]) -> int:
        """默认实现：遍历 messages，对每个 ContentBlock 调 estimate_text 或 estimate_image 累加。
        provider 如有更精确算法可覆盖。
        """
        total = 0
        for msg in messages:
            for block in msg.content:
                if block.type == "text" and block.text:
                    total += self.estimate_text(block.text)
                elif block.type == "image" and isinstance(block.source, dict):
                    src = block.source
                    if src.get("type") == "base64":
                        total += self.estimate_image(
                            base64_data=src.get("data", ""),
                            media_type=src.get("media_type", "image/png"),
                        )
        return total
```

`MimoTokenEstimator(BaseTokenEstimator)` 继承基类，自动获得 `estimate_messages` 默认实现。

### 6.8 接入点

- `token_estimator` 属性挂在 `LLMClient` 上（V1.6 按 providers-refactor-spec 删除 `LLMTransport` 抽象基类，不保留该抽象层）
- `ContextAssembler` 通过 `LLMClient` 拿 estimator，替换全局 `estimate_text_tokens` 函数
- `ProviderUsage` 累加时用 `estimator.real_usage()` 解析真实占用

```python
class LLMClient:
    def __init__(self, provider, *, token_estimator: TokenEstimator | None = None):
        self._token_estimator = token_estimator or make_default_estimator(provider)

    @property
    def token_estimator(self) -> TokenEstimator:
        return self._token_estimator

class ContextAssembler:
    def __init__(self, *, provider, policy, system_prompt, llm_client: LLMClient):
        self._estimator = llm_client.token_estimator
        ...
```

**`estimate_messages_tokens` 迁移**：当前 `context_assembler.py:215` 的 `estimate_messages_tokens` 按 `estimate_text_tokens` 逐条累加，忽略图像 token。迁移为：

```python
def estimate_messages_tokens(messages: list[Message], estimator: TokenEstimator) -> int:
    """迁移适配层：旧调用点 → 新 estimator。
    直接委托给 estimator.estimate_messages()，含图像 token 估算。
    """
    return estimator.estimate_messages(messages)
```

所有现有调用 `estimate_messages_tokens` 的地方改为传入 `estimator` 参数即可。阶段 1 后重新估算直接调 `estimator.estimate_messages()`，不走这个适配层。

### 6.9 Padding 策略

- 首轮（无校准数据）：padding × 4/3（沿用现有 `token_estimation_padding`）
- 校准后：用校准系数，padding 降为 × 1.1（校准已吸收大部分误差）
- 始终保留小 padding 应对边缘情况

### 6.10 不做的事

- ❌ **图像降采样**：分辨率变化时降采样到固定值是硬编码，且可能损坏图像细节（老人问"药盒上的字"时降采样会让字糊）。靠压缩管线的"历史图像剥离"解决图像 token 占用问题。
- ❌ **引入 tiktoken 等外部 tokenizer**：方案 A 的校准机制已经够准，外部依赖不必要。
- ❌ **工具结果带图像元信息**：base64 头解码零侵入，不需要改工具接口。

## 七、可观测性

### 7.1 压缩事件

每次压缩 emit `context.compaction` 事件，payload：

```json
{
  "trigger": "auto" | "reactive",
  "before_tokens": 45000,
  "after_tokens": 18000,
  "savings_pct": 0.60,
  "stages_executed": ["trim_images", "summarize_tools", "dedup", "llm_summary"],
  "llm_summary_used": true,
  "llm_summary_model": "mimo-v2.5",
  "llm_summary_duration_ms": 3200,
  "compression_count": 2,
  "aggressive_mode": false,
  "warnings": []
}
```

**枚举映射表**（`CompactionRecord.kind` 与 event `trigger` 对齐）：

| `CompactionRecord.kind` | event `trigger` | 含义 |
|------------------------|-----------------|------|
| `micro` | `auto` | 阈值触发的 micro-compaction |
| `summary` | `auto` | 阈值触发的 LLM 摘要 |
| `reactive` | `reactive` | context_length 错误触发 |
| `emergency` | `reactive` | reactive 二次回退（aggressive） |
| `none` | （不 emit） | 没压缩 |

`trigger="manual"` 废弃（用户决策：`/compact` 不做，压缩只走阈值/reactive）。

**emit 位置**：由 `GenericAgentRuntime` 在 `prepare()` 返回后根据 `ComposedContext.metrics`（`context_assembler.py:145-149` 已返回 `compaction_triggered`/`compaction_kind`）emit `context.compaction` 事件。`ContextAssembler` 不直接 emit（它不持有 event_sink）。

### 7.2 用户可见提示

终端打印（依赖 audit 第六节可观测性增强）：

```
[context compaction] 45K → 18K tokens (saved 60%), used LLM summary
```

reactive compaction 触发时：

```
[warning] Reactive compaction triggered (context length error), aggressive mode
```

---

## 八、落地分两个 PR

所有功能必做，不分优先级。分两个 PR 是因为 PR2 依赖 PR1 之外的基础设施。

### PR1：压缩管线本体

所有压缩逻辑互相耦合，一个 PR 完成：

1. 历史图像剥离（阶段 1.1）
2. 工具结果按类型摘要化 + 注册表（阶段 1.2）
3. 工具结果去重（阶段 1.3）
4. 边界确定重写（阶段 2，含强制最新用户消息进尾部、配对对齐）
5. LLM 摘要接入 + 迭代式 + 失败回退（阶段 3）
6. 工具对完整性兜底（阶段 4.1，复活 `sanitize_tool_pairs`）
7. 最终图像剥离（阶段 4.2）
8. reactive compaction 激进压缩回退（§3.5）
9. ProviderUsage 改累加（audit 11.5），累加时包含 `cache_read_input_tokens`
10. `TokenEstimator` 接口 + `MimoTokenEstimator` 实现（§6.4-6.6）
11. 图像尺寸解码工具函数 `decode_image_dimensions`（§6.5）
12. `ContextAssembler` 改用 estimator 替换全局 `estimate_text_tokens`
13. `LLMClient` 新增 `token_estimator` 属性（V1.6 删除 `LLMTransport` 抽象基类）
14. 校准机制接入：每次 LLM 调用后用真实 usage 校准本地估算（§6.7）
15. 配置项新增（§5）
16. `context.compaction` 事件 emit（§7.1）
17. 实际加载 `compact_summary_prompt.txt`（当前只注册 ID 不加载）
18. 扩展 `compact_summary_prompt.txt` 模板（§3.3）
19. 删除 `build_basic_summary`（被 LLM 摘要取代）
20. 删除 `compact_tool_result_text`（被新摘要规则取代）
21. 删除 `ContextBudget.image_token_estimate` 字段（被 estimator 取代，audit 3.3 已核实该字段从未被读取）
22. 删除 `ContextBudget.recent_tail_budget_tokens` 字段（audit 3.3 已核实从未被读取）

### PR2：`/compact` 命令 + 磁盘溢出

依赖 PR1 之外的入口/基础设施：

17. `/compact` 作为消息内容识别（CLI / 飞书 / 微信任何渠道触发）
18. `/compact <topic>` 焦点压缩
19. `/new` 重置 session
20. 阶段 0 大结果溢出到磁盘
21. session 在多任务间的状态管理（任务切换语义）

---

## 九、与其他重构的依赖关系

| 依赖项 | 关系 |
|--------|------|
| audit 第五节 Session 持久化 | 当前阶段单进程内存即可，`/compact` 在 PR2 内做 |
| audit 11.5 ProviderUsage 累加 | PR1 内做 |
| audit 11.8 wall-clock / loop guard | 取消 4 个 loop guard 决策来自 audit 11.8，本 spec 不负责；压缩机制不依赖 loop guard |
| audit 第六节 可观测性 | 压缩事件 emit 依赖事件系统增强，但 `context.compaction` 事件本身在 PR1 定义 |
| audit 1.5 死函数处理 | `microcompact_old_tool_results` 被新摘要规则取代后删除；`sanitize_tool_pairs` 在 PR1 复活并接入 |

---

## 十、影响范围估算

**新增**：
- `agent/compact.py` 重写 + ~400 行
  - 图像剥离函数
  - 工具结果摘要化注册表 `ToolResultSummarizer`
  - 去重
  - 边界确定（替换 `split_preserving_recent_context`）
  - LLM 摘要调用
  - 激进压缩回退

**修改**：
- `context_assembler._compact()`：重写，从 20 行扩展到 ~80 行
- `compact_summary_prompt.txt`：扩展结构化字段
- `ContextPolicyConfig`：新增 ~12 个字段
- `prompt_loader.py`：实际加载 compact_summary_prompt
- `generic_runtime.py`：
  - emit `context.compaction` 事件
  - ProviderUsage 改累加
  - reactive compaction 二次回退

**删除**：
- `build_basic_summary`（被 LLM 摘要取代）
- `compact_tool_result_text`（被新摘要规则取代）
- `microcompact_old_tool_results`（被新摘要规则取代）

---

## 十一、决策汇总（已全部确认）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 视觉工具是否返回 description | ❌ 不返回 | 延迟敏感 |
| 图像剥离后信息保留 | 直接折叠，agent 信息不够会重新观察 | 老人场景下 agent 本来就会反复观察 |
| 历史图像保留张数 | 2 张 | 平衡节省与对照能力 |
| 默认工具结果摘要 | 保留最后 10 行 | 用户偏好 |
| 分级主语 | 位置+类型自动判定 | 不打显式 priority 标签，简化实现 |
| 压缩管线 | 串行四阶段 | 免费→边界→LLM→清理 |
| LLM 摘要触发 | 渐进式（阶段 1 不够才调 LLM） | 省钱省延迟 |
| LLM 摘要参考上下文 | task state + failure + 上次摘要 + 尾部全文 | 让摘要更紧凑相关 |
| LLM 摘要失败回退 | 配置模型 → 主模型 → 中止（默认） | 保守，避免静默丢消息 |
| 反抖动保护 | ❌ 不做 | 阈值触发模式下是伪问题 |
| reactive compaction | 二次回退 + 激进参数 | 替代反抖动 |
| `/compact` 触发形式 | 作为消息内容识别（任何渠道） | 渠道无关 |
| Session 模型 | 单进程内存，单 session 多任务 | 当前阶段 |
| 落地分批 | PR1（管线本体）+ PR2（命令+溢出） | PR2 依赖外部入口 |
| Token 估算 | 方案 A：优先用 provider 真实 usage，本地估算兜底 | 真实 usage 是真理源，零误差 |
| Token 估算可插拔 | `TokenEstimator` 接口 + provider adapter | 换 provider 只改 adapter，压缩逻辑零改动 |
| MiMo vision token | `w × h / 1000`（实测） | 4 组数据反推，与像素数成正比 |
| MiMo 真实占用 | `input_tokens + cache_read_input_tokens` | 实测确认，cache_read 不计入 input |
| 图像尺寸获取 | base64 头解码（PNG/JPEG） | 零侵入，不改工具接口 |
| 图像降采样 | ❌ 不做 | 分辨率变化时降采样是硬编码，且损坏细节 |
| 校准机制 | 滑动平均（0.7 旧 + 0.3 新） | 避免单次抖动 |
| Loop guard 取消 | 参见 audit 11.8 决策（用户已确认取消 4 个 loop guard） | 本 spec 不负责 loop guard，但压缩机制不依赖 loop guard |
| 循环终止机制 | context window 物理上限 + reactive compaction | 取消 guard 后的唯一可靠终止 |
| `consecutive_tool_errors` 字段 | 保留作为观测指标，不作终止条件 | 诊断信号有价值 |

---

## 十二、未决事项

无。所有决策已确认。实施时如遇新问题再讨论。

---

## 十三、评审修复记录

以下为独立评审发现的 17 处 B 类问题修复记录：

| # | 问题 | 修复位置 | 修法摘要 |
|---|------|---------|---------|
| 1 | 阶段 1 后重新估算的 estimator 路径未明确 | §3.1 | 明确必须用 `TokenEstimator.estimate_messages()`（含图像 token），不能用旧的 `estimate_messages_tokens` |
| 2 | §2.5 与 §3.2 自相矛盾（中等优先级不进 LLM vs 进） | §2.5, §3.2 | 中等优先级改为 "micro-compaction + 进 LLM 摘要（占位符形式）"；§3.2 明确 LLM 摘要输入是压缩区全文含阶段 1 占位符 |
| 3 | `force_compact_next` 无法区分正常/激进模式 | §3.5 | 类型从 `bool` 改为 `Literal[None, "normal", "aggressive"]` |
| 4 | 与 audit §7.1 "去掉 LLMTransport 抽象"冲突 | §6.8, PR1 #13 | `token_estimator` 挂 `LLMClient` 而非 `LLMTransport`；删除 `LLMTransport` 抽象基类 |
| 5 | `CompactionRecord.kind` 与 event `trigger` 枚举不对齐 | §7.1 | 加枚举映射表；废弃 `trigger="manual"` |
| 6 | `session.replace_messages` 是否仍调用未明确 | 新增 §3.6 | 明确新 `_compact` 仍调用 `session.replace_messages` |
| 7 | `ContextPolicyConfig` 旧字段未删 | §5 | 明确删除 `preserve_recent_agent_steps`/`preserve_recent_user_turns`/`recent_tail_ratio`/`image_token_estimate` |
| 8 | TokenEstimator Protocol `calibrate` 位置不对 + `estimate_messages_tokens` 迁移未明确 | §6.4, §6.8 | Protocol 只保留 4 抽象方法；`calibrate` 移到 `BaseTokenEstimator`；`estimate_messages_tokens` 改为委托 `estimator.estimate_messages()` |
| 9 | 章节编号错误（两个 "## 七"） | 全局 | 第二个 "## 七" 改为 "## 八"，后续章节顺延（八→九，九→十，十→十一，十一→十二） |
| 10 | LLM 摘要参考上下文的注入方式未明确 | §3.2 | 砍掉"尾部摘要"，改为尾部全文；明确每项注入方式（system prompt 注入 vs 可压缩区一部分） |
| 11 | 摘要后仍超阈值的衔接 | §3.5 | 加"摘要后重新估算，若仍超阈值 → 触发 reactive compaction aggressive 模式" |
| 12 | `context.compaction` event emit 位置未明确 | §7.1 | 明确由 `GenericAgentRuntime` 根据 `ComposedContext.metrics` emit，`ContextAssembler` 不直接 emit |
| 13 | 图像占位符的 args 回溯逻辑 | §1.1 | 通过 `tool_call_id` 回溯 `AssistantMessage.tool_calls[i].arguments` 提取参数 |
| 14 | 图像 session 层保留 2 张 vs transport 层发送 1 张的分工 | §1.1 | 明确 session 保留 2 张（压缩用）与 transport 发送 1 张（mimo 约束）独立 |
| 15 | LLM 摘要 prompt 加载路径 | §3.3 | 明确通过 `load_prompt(PromptId.COMPACT_SUMMARY)` 加载 `prompts/loader.py` |
| 16 | `compact_summary_prompt.txt` 重写时保留关键约束 | §3.3 | 模板末尾加 `## Critical Constraints` section（4 条） |
| 17 | Loop guard 取消决策移出本 spec | §11（原 §10） | 改为"参见 audit 11.8 决策"，本 spec 不负责 loop guard |
