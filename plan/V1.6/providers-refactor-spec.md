# Providers 层重构 Spec（hermes 化）

Date: 2026-06-27

关联：[audit-and-refactor-spec.md](audit-and-refactor-spec.md) 第七节
依赖：[config-refactor-spec.md](config-refactor-spec.md)（`ProviderProfileConfig` 来源）
被依赖：[observability-spec.md](observability-spec.md)（真 streaming 是实时输出的前置）、[context-compaction-spec.md](context-compaction-spec.md)（`provider_usage` 累加）

---

## 一、问题现状（已验证）

### 1.1 两套并行的 LLM 调用代码

| 实现 | 位置 | 用途 | 输入 | 输出 |
|------|------|------|------|------|
| `MimoTransport` | `providers/mimo_transport.py` (678 行) | agent loop（多轮对话+工具+thinking+图像） | `list[Message]` + tools | `Iterator[TransportDelta]` → `AssistantMessage` |
| `RawJsonLLMClient` | `llm_client.py` (407 行) | memory/retrieval + cli/doctor（单次 JSON 调用） | `prompt: str` | `LLMJsonResponse`（content + json dict） |

两者**零代码共用**，但功能高度重叠：

| 共同职责 | MimoTransport | RawJsonLLMClient |
|---------|---------------|------------------|
| 多 key 轮换 | ✅ `_post_with_retries` | ✅ `for api_key in self._provider.api_keys` |
| anthropic + openai 协议 | ✅ if/else 分支 | ✅ if/else 分支 |
| 响应解析 | ✅ `parse_response_payload` | ✅ `_extract_content` |
| 错误处理 | `RuntimeError` | `LLMProviderNetworkError` / `LLMProviderResponseError` |
| truncation 检测 | ✅ `_normalize_finish_reason` | ✅ `_response_was_truncated` |
| JSON 提取 | ❌ 无 | ✅ `extract_json_payload` |
| 图像处理 | ✅ strip fallback | ❌ 无 |
| retry | ✅ max_retries | ❌ 只 key 轮换 |

### 1.2 抽象基类 `LLMTransport` 是 YAGNI

`providers/transport.py:40` 定义 `LLMTransport` ABC，**唯一实现是 `MimoTransport`**。`MimoTransport` 内部用 `if self._protocol == "anthropic"` 分支处理两种协议——不需要第二个 transport 类。

抽象层把**协议转换**（messages → anthropic/openai 格式）和 **HTTP 调用**（POST + retry + key 轮换）耦合在一个 `stream()` 方法里，职责混乱。

### 1.3 假 streaming

`MimoTransport.stream()` 名为 stream，实际是同步 POST 完再切片 yield deltas。注释（`mimo_transport.py:135-136`）：

```python
# For now, use non-streaming and convert to deltas.
# Real SSE streaming can be added later.
```

无法支持可观测性 spec 的"实时输出 thinking"。

### 1.4 手写 httpx 重复造轮子

当前 `pyproject.toml` 只有 `httpx`，没有 `anthropic` / `openai` SDK。手写代码要做：

- SSE 流式解析（`text/event-stream` 格式）
- retry / 超时 / 连接池
- 错误分类（401/429/500）
- 协议格式细节（thinking block、tool_use、image source）

这些 `anthropic` 和 `openai` 官方 SDK 全部内置。

### 1.5 `llm_client.py` 在根目录

应在 `providers/`（审计 4.1）。

---

## 二、设计决策

### 2.1 整体架构：Transport（协议转换）+ LLMClient（HTTP 调用）分离

借鉴 hermes（`agent/transports/base.py`）：

```
providers/
├── __init__.py
├── transports/                    ← 协议转换层（薄）
│   ├── __init__.py
│   ├── base.py                    ← ProviderTransport ABC
│   ├── anthropic.py               ← AnthropicTransport
│   ├── openai_chat.py             ← OpenAIChatTransport
│   └── types.py                   ← NormalizedResponse
├── llm_client.py                  ← LLMClient（HTTP 调用层，用 SDK）
└── errors.py                      ← 统一错误体系
```

**职责分离**：

| 层 | 职责 | 不负责 |
|----|------|--------|
| `Transport` | 协议格式转换（messages/tools ↔ provider-native）、响应归一化 | HTTP 调用、retry、key 轮换、streaming、SDK client 生命周期 |
| `LLMClient` | 构造 SDK client、HTTP 调用、retry、key 轮换、streaming、key 轮换、错误分类 | 协议格式细节 |

### 2.2 改用官方 SDK

**新增依赖**：

```toml
# pyproject.toml
dependencies = [
  "anthropic>=0.40,<1.0",   # Anthropic SDK（mimo/huya 等 anthropic 协议）
  "openai>=1.50,<3.0",      # OpenAI SDK（mytokenland/glm 等 openai 协议）
  "httpx>=0.27",       # 保留（SDK 底层也用 httpx）
  ...
]
```

**SDK 用法**：

```python
# anthropic 协议（mimo、huya）
from anthropic import Anthropic
client = Anthropic(api_key="tp-...", base_url="https://token-plan-cn.xiaomimimo.com/anthropic")
response = client.messages.create(
    model="mimo-v2.5",
    messages=[{"role": "user", "content": "..."}],
    stream=False,  # 或 True
)

# openai 协议（mytokenland、glm）
from openai import OpenAI
client = OpenAI(api_key="sk-...", base_url="https://api.mytokenland.com/v1")
response = client.chat.completions.create(
    model="glm-5.1",
    messages=[{"role": "user", "content": "..."}],
    stream=False,  # 或 True
)
```

两个 SDK 都接受自定义 `base_url`——同一 SDK 调任何兼容协议的 provider。

### 2.3 Transport 抽象层（只管协议转换）

```python
# providers/transports/base.py
from abc import ABC, abstractmethod
from typing import Any
from homemaster.agent.messages import Message, AssistantMessage, ToolCall


class ProviderTransport(ABC):
    """协议转换层：messages/tools ↔ provider-native 格式。不负责 HTTP 调用。"""

    @property
    @abstractmethod
    def protocol(self) -> str:
        """'anthropic' 或 'openai'"""

    @abstractmethod
    def build_create_kwargs(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[dict] | None,
        system_prompt: str,
        max_output_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        """构造 SDK client.create() 的 kwargs（不含 stream/timeout 等 SDK 通用参数）。"""
        ...

    @abstractmethod
    def normalize_response(self, raw_response: Any) -> AssistantMessage:
        """把 SDK 返回的 typed 对象转成 normalized AssistantMessage。"""
        ...

    @abstractmethod
    def iter_stream_deltas(self, raw_stream: Any) -> Iterator[TransportDelta]:
        """把 SDK 的 stream iterator 转成 TransportDelta 序列。Transport 负责遍历整个流。"""
        ...
```

**两个实现**：

| Transport | 文件 | SDK | 用于 |
|-----------|------|-----|------|
| `AnthropicTransport` | `transports/anthropic.py` | `anthropic` 库 | mimo、huya（`protocol="anthropic"`） |
| `OpenAIChatTransport` | `transports/openai_chat.py` | `openai` 库 | mytokenland、glm（`protocol="openai"`） |

**协议选择**：根据 `ProviderProfileConfig.protocol` 字段动态选择 Transport：

```python
def make_transport(protocol: str) -> ProviderTransport:
    if protocol == "anthropic":
        return AnthropicTransport()
    if protocol == "openai":
        return OpenAIChatTransport()
    raise RuntimeConfigError(f"unsupported protocol: {protocol!r}")
```

### 2.4 LLMClient（HTTP 调用层，用 SDK）

```python
# providers/llm_client.py
class LLMClient:
    """统一 LLM 调用层。负责 SDK client 构造、retry、key 轮换、streaming。"""

    def __init__(self, provider: ProviderProfileConfig, *,
                 max_retries: int = 2,
                 max_image_strip_attempts: int = 1,
                 key_rotation_attempts: int | None = None):
        self._provider = provider
        self._transport = make_transport(provider.protocol)
        self._max_retries = max_retries
        self._max_image_strip_attempts = max_image_strip_attempts
        self._key_rotation_attempts = (
            key_rotation_attempts
            if key_rotation_attempts is not None
            else len(provider.api_keys)
        )
        self._sdk_clients: dict[str, Any] = {}  # 按 api_key 缓存 SDK client

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        event_sink: EventSink | None = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
    ) -> AssistantMessage:
        """非流式调用。多 key 轮换 + retry。内部 = _aggregate_deltas(list(stream(...)))。"""
        ...

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        event_sink: EventSink | None = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
    ) -> Iterator[TransportDelta]:
        """真 SSE 流式。逐 token yield。"""
        ...

    def complete_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        event_sink: EventSink | None = None,
        run_id: str = "",
    ) -> LLMJsonResult:
        """单次调用 + JSON 提取。供 RAG / doctor 用。"""
        message = self.complete(
            messages=[UserMessage(content=[ContentBlock(text=prompt)])],
            temperature=temperature,
            event_sink=event_sink,
            run_id=run_id,
        )
        text = message.content[0].text if message.content else ""
        payload = extract_json_payload(text)
        return LLMJsonResult(
            payload=payload,
            content=text,
            elapsed_ms=...,   # 从 timing 上下文获取
            attempts=(...),   # key 轮换尝试记录
            finish_reason=getattr(message, "finish_reason", None),
        )

    def close(self) -> None: ...


@dataclass(frozen=True)
class LLMJsonResult:
    """complete_json() 的结构化返回值。"""
    payload: dict[str, Any]          # 解析后的 JSON dict
    content: str                     # 模型原始输出
    elapsed_ms: float
    attempts: tuple[dict, ...]       # key 轮换尝试记录
    finish_reason: str | None

    def public_summary(self) -> dict[str, Any]:
        return {
            "payload_keys": list(self.payload.keys()),
            "elapsed_ms": self.elapsed_ms,
            "attempts": list(self.attempts),
            "finish_reason": self.finish_reason,
        }
```

**关键设计**：

1. **SDK client 按 api_key 缓存**：`self._sdk_clients[key] = Anthropic(api_key=key, base_url=...)`，避免每次调用都新建 client
2. **多 key 轮换**：`for key in self._provider.api_keys:` 循环调用，第一个成功就返回
3. **retry**：用 SDK 自带 retry（`max_retries` 参数）+ 自己的 key 轮换层
4. **streaming**：SDK 的 `stream=True` 返回 iterator，Transport 把它转成 `TransportDelta`
5. **_aggregate_deltas**：静态方法，从 `list[TransportDelta]` 拼装 `AssistantMessage`（从 `transport.py:84-120` 的 `LLMTransport._aggregate` 迁移）

### 2.5 统一错误体系

```python
# providers/errors.py
class LLMClientError(RuntimeError):
    def __init__(self, *, error_type: str, message: str, raw_content: str | None = None):
        self.error_type = error_type
        self.raw_content = raw_content
        super().__init__(message)

class LLMNetworkError(LLMClientError): ...      # 超时、连接失败
class LLMResponseError(LLMClientError): ...     # 响应解析失败、truncated
class LLMAuthError(LLMClientError): ...          # 401、key 无效
class LLMRateLimitError(LLMClientError): ...     # 429
class LLMProviderError(LLMClientError): ...      # 5xx、provider 内部错误
```

**SDK 异常映射**：

```python
def _map_sdk_error(exc: Exception, protocol: str) -> LLMClientError:
    if protocol == "anthropic":
        from anthropic import APIConnectionError, RateLimitError, AuthenticationError, ...
        # 映射到 LLMNetworkError / LLMRateLimitError / LLMAuthError / LLMProviderError
    else:
        from openai import APIConnectionError, RateLimitError, AuthenticationError, ...
        # 同上
```

### 2.6 删除 `LLMTransport` 抽象基类

旧 `providers/transport.py` 删除，`TransportDelta` 类型迁移到 `providers/transports/types.py`。`_aggregate` 静态方法迁移到 `LLMClient._aggregate_deltas`。

### 2.7 删除 `RawJsonLLMClient`

`llm_client.py` 根目录文件删除。功能并入 `LLMClient.complete_json()`。

`extract_json_payload` 函数迁移到 `providers/llm_client.py`（或 `providers/json_utils.py`）。

### 2.8 图像 strip fallback 保留

`MimoTransport._post_with_retries` 里的"multimodal corruption 时 strip images 重试"逻辑（`mimo_transport.py:296-321`）迁移到 `LLMClient` 的 retry 层。这是真实的健壮性特性，不是死代码。

---

## 三、调用方迁移

### 3.1 agent loop（`agent/generic_runtime.py`）

```python
# 之前
from homemaster.providers.mimo_transport import MimoTransport
transport = MimoTransport(base_url=..., model=..., api_key=..., protocol=...)
for delta in transport.stream(messages, tools, ...):
    ...

# 之后
from homemaster.providers.llm_client import LLMClient
client = LLMClient(provider=provider_profile)
for delta in client.stream(messages, tools=..., system_prompt=..., ...):
    ...
```

**聚合迁移示例**（`LLMTransport._aggregate` → `LLMClient._aggregate_deltas`）：

```python
# 之前（generic_runtime.py / turn.py）
from homemaster.providers.transport import LLMTransport
deltas = list(transport.stream(messages, tools, ...))
assistant_msg = LLMTransport._aggregate(deltas)

# 之后
deltas = list(client.stream(messages, tools=..., system_prompt=..., ...))
assistant_msg = LLMClient._aggregate_deltas(deltas)  # 静态方法
```

**turn.py 迁移示例**：

```python
# 之前
from homemaster.providers.mimo_transport import MimoTransport
transport = MimoTransport(base_url=..., model=..., api_key=..., protocol=...)

# 之后
from homemaster.providers.llm_client import LLMClient
client = LLMClient(provider=provider_profile)
```

**benchmarking/alfworld/runner.py 迁移示例**：

```python
# 之前
from homemaster.providers.mimo_transport import MimoTransport
transport = MimoTransport(base_url=..., model=..., api_key=..., protocol=...)

# 之后
from homemaster.providers.llm_client import LLMClient
client = LLMClient(provider=provider_profile)
```

### 3.2 memory/retrieval.py

```python
# 之前
from homemaster.llm_client import RawJsonLLMClient
client = RawJsonLLMClient(provider=provider_config)
response = client.complete_json(prompt)
payload = response.json_payload

# 之后
from homemaster.providers.llm_client import LLMClient
client = LLMClient(provider=provider_profile)
result = client.complete_json(prompt)
payload = result.payload  # 解析后的 JSON dict
summary = result.public_summary()  # 可观测性摘要
```

### 3.3 cli/doctor.py

```python
# 之前
from homemaster.llm_client import RawJsonLLMClient
client = RawJsonLLMClient(provider=provider_config)
response = client.complete_json(prompt)
payload = response.json_payload

# 之后
from homemaster.providers.llm_client import LLMClient
client = LLMClient(provider=provider_profile)
result = client.complete_json(prompt)
payload = result.payload
```

---

## 四、待删除/合并清单

### 4.1 删除

| 文件 | 行数 | 处置 |
|------|------|------|
| `src/homemaster/providers/transport.py` | 121 | 删除，`LLMTransport` ABC 废弃，`TransportDelta` 迁到 `transports/types.py` |
| `src/homemaster/providers/mimo_transport.py` | 678 | 删除，协议转换迁到 `transports/anthropic.py` + `transports/openai_chat.py`，HTTP 调用迁到 `llm_client.py` |
| `src/homemaster/llm_client.py` | 407 | 删除，`RawJsonLLMClient` 并入 `LLMClient.complete_json`，`extract_json_payload` 迁到 `providers/json_utils.py` |

**测试文件处置**：

| 测试文件 | 处置 |
|---------|------|
| `tests/homemaster/test_transport_mimo.py` | 删除，重写为 `test_anthropic_transport.py` |
| `tests/homemaster/test_llm_client.py`（旧） | 删除，重写为 `test_llm_client.py`（新） |
| `tests/homemaster/test_transport_system_prompt.py` | 删除，逻辑迁到 `test_anthropic_transport.py` |
| `tests/homemaster/test_generic_agent_runtime.py:21` 的 `class FakeTransport(LLMTransport)` | 改为 duck-typed 类（不继承 `LLMTransport`），实现 `LLMClient` 接口 |

**总计：3 个源文件 + 3 个测试文件，~1200 行代码删除/重构。**

### 4.2 新增

| 文件 | 估计行数 | 职责 |
|------|----------|------|
| `providers/transports/base.py` | ~60 | `ProviderTransport` ABC |
| `providers/transports/types.py` | ~40 | `TransportDelta` + `NormalizedResponse` |
| `providers/transports/anthropic.py` | ~150 | `AnthropicTransport`（messages/tools 转换 + 响应归一化 + stream delta 迭代） |
| `providers/transports/openai_chat.py` | ~130 | `OpenAIChatTransport` |
| `providers/llm_client.py` | ~250 | `LLMClient`（SDK client 管理 + retry + key 轮换 + complete/stream/complete_json） |
| `providers/errors.py` | ~60 | 统一错误体系 + SDK 异常映射 |
| `providers/json_utils.py` | ~40 | `extract_json_payload` |

**总计：~730 行新代码**（vs 删除 1200 行——净减 ~470 行，且复用 SDK 减少了手写复杂度）。

### 4.3 依赖更新

`pyproject.toml`：

```diff
 dependencies = [
   "bm25s>=0.2",
+  "anthropic>=0.40,<1.0",
   "httpx>=0.27",
+  "openai>=1.50,<3.0",
   "jieba>=0.42",
   "pydantic>=2.7",
   "rich>=13.7",
   "typer>=0.12",
 ]
```

---

## 五、实现纪律

### 5.1 Transport 零 HTTP（硬规则）

`ProviderTransport` 子类**不允许**直接调用 SDK client 或 httpx。只做：

- 输入：normalized `list[Message]` + tools schema
- 输出：SDK `create()` 的 kwargs dict / normalized `AssistantMessage` / `TransportDelta` iterator

HTTP 调用、retry、key 轮换全部在 `LLMClient`。Transport 是纯函数式的格式转换器。

### 5.2 LLMClient 零协议细节（硬规则）

`LLMClient` 不允许出现 `if self._protocol == "anthropic"` 之类的分支。协议差异通过 `self._transport`（`ProviderTransport` 实例）隔离。

```python
# 错误 ❌
def complete(self, messages, ...):
    if self._provider.protocol == "anthropic":
        kwargs = self._build_anthropic_kwargs(...)
        response = self._anthropic_client.messages.create(**kwargs)
    else:
        kwargs = self._build_openai_kwargs(...)
        response = self._openai_client.chat.completions.create(**kwargs)

# 正确 ✅
def complete(self, messages, ...):
    kwargs = self._transport.build_create_kwargs(
        model=self._provider.model,
        messages=messages,
        tools=tools,
        system_prompt=system_prompt,
        ...
    )
    sdk_client = self._get_sdk_client()  # 按 protocol 选 Anthropic/OpenAI
    response = sdk_client.messages.create(**kwargs)  # 或 chat.completions.create
    return self._transport.normalize_response(response)
```

**SDK client 选择**也是按 protocol 分发的，但封装在 `_get_sdk_client()` 内部，不污染主路径。

### 5.3 真 SSE streaming

用 SDK 的 `stream=True`：

```python
# Anthropic（Transport 内部遍历 stream）
with self._anthropic_client.messages.stream(**kwargs) as stream:
    yield from self._transport.iter_stream_deltas(stream)

# OpenAI（stream_options 确保流式返回 usage）
# 注：openai SDK 的 Stream 对象支持 __enter__/__exit__（已核实 openai 2.x 源码 _streaming.py），
# 所以 with 块合法——break/return 时 Stream.close() 自动调用，释放连接
with self._openai_client.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kwargs) as stream:
    yield from self._transport.iter_stream_deltas(stream)
```

`Transport.iter_stream_deltas` 把 SDK 的 typed event 转成 `TransportDelta`（text_delta / reasoning_delta / tool_call_delta / finish_reason / usage）。

### 5.4 多 key 轮换 + retry 分层

```python
def complete(self, messages, ...):
    last_error: LLMClientError | None = None
    for key_index, api_key in enumerate(self._provider.api_keys, start=1):
        sdk_client = self._get_sdk_client(api_key)
        try:
            kwargs = self._transport.build_create_kwargs(...)
            response = sdk_client.messages.create(**kwargs)  # SDK 自带 max_retries
            return self._transport.normalize_response(response)
        except LLMAuthError:
            continue  # key 无效，换下一个
        except LLMRateLimitError as exc:
            last_error = exc
            continue  # 限流，换下一个 key
        except LLMNetworkError as exc:
            last_error = exc
            continue  # 网络问题，换 key 重试
    raise last_error or LLMClientError(error_type="no_keys", message="no api keys configured")
```

**分层**：SDK 内部 retry（`max_retries=2`，处理瞬时网络错误）→ `LLMClient` 的 key 轮换（处理 key 级问题）。

### 5.5 图像 strip fallback

迁移自 `MimoTransport._post_with_retries`。strip 与 key 轮换的嵌套关系：外层 strip 循环，内层 key 轮换——strip 后重新遍历所有 key。

```python
def _extract_error_message(exc: Exception) -> str:
    """从 SDK 异常提取错误消息字符串。"""
    # anthropic / openai APIStatusError 有 .response 和 .message
    if hasattr(exc, "response"):
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                err = body.get("error", {})
                if isinstance(err, dict):
                    return err.get("message", str(exc))
                return str(err)
        except Exception:
            pass
    return str(exc)


def complete(self, messages, ...):
    last_error: LLMClientError | None = None
    for strip_attempt in range(self._max_image_strip_attempts + 1):
        current_messages = (
            messages if strip_attempt == 0
            else _strip_images_from_messages(messages)
        )
        for key_index, api_key in enumerate(self._provider.api_keys, start=1):
            sdk_client = self._get_sdk_client(api_key)
            try:
                kwargs = self._transport.build_create_kwargs(
                    model=self._provider.model,
                    messages=current_messages,
                    tools=tools,
                    system_prompt=system_prompt,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
                response = sdk_client.messages.create(**kwargs)
                return self._transport.normalize_response(response)
            except (LLMAuthError, LLMRateLimitError, LLMNetworkError):
                continue  # 换 key
            except LLMProviderError as exc:
                if (
                    strip_attempt < self._max_image_strip_attempts
                    and _is_multimodal_corruption(_extract_error_message(exc))
                ):
                    break  # 跳出 key 循环，进入下一轮 strip
                raise
    raise last_error or LLMClientError(error_type="no_keys", message="no api keys configured")
```

### 5.6 usage 累加（关联 audit 11.5）

`LLMClient.complete()` 返回的 `AssistantMessage.usage` 由 `Transport.normalize_response` 从 SDK response 提取。`agent/generic_runtime.py` 累加到 `agent_state.provider_usage`（修复 audit 11.5 覆盖 bug）。

---

## 六、验证计划

### 6.1 单元测试

| 测试 | 断言 |
|------|------|
| `test_anthropic_transport_build_kwargs` | `build_create_kwargs` 输出 anthropic SDK 接受的 kwargs |
| `test_openai_transport_build_kwargs` | 同上，openai 格式 |
| `test_anthropic_normalize_response` | SDK response 对象 → `AssistantMessage`（含 thinking / tool_use / usage） |
| `test_openai_normalize_response` | 同上 |
| `test_llm_client_complete` | mock SDK，验证多 key 轮换 + retry |
| `test_llm_client_complete_json` | mock SDK 返回 JSON 字符串，验证 `complete_json` 返回 dict |
| `test_llm_client_stream` | mock SDK stream，验证逐 delta yield |
| `test_llm_client_image_strip_fallback` | mock multimodal corruption，验证 strip 重试 |
| `test_error_mapping_anthropic` | SDK `AuthenticationError` → `LLMAuthError` |
| `test_error_mapping_openai` | SDK `RateLimitError` → `LLMRateLimitError` |
| `test_transport_no_http` | 静态检查：Transport 子类不 import httpx / anthropic / openai client |

### 6.2 集成验证（live_api 测试）

| 测试 | 断言 |
|------|------|
| `test_live_mimo_complete` | 真调 mimo，返回 `AssistantMessage` 且 `content` 非空 |
| `test_live_mimo_stream` | 真调 mimo stream=True，至少 yield 3 个 text_delta |
| `test_live_mimo_complete_json` | 真调 mimo，返回的 JSON dict 字段齐全 |
| `test_live_mimo_with_image` | 带图像调 mimo，正常返回 |
| `test_live_mimo_tool_use` | 带 tools 调 mimo，返回 `tool_calls` |
| `test_live_mimo_stream_sse` | 真环境验证 SSE 流式：首 event < 2s 到达，stream.close() 可中断，usage 在流末尾返回 |
| `test_live_openai_complete` | 真调 mytokenland（openai 协议），返回 `AssistantMessage` |

### 6.3 黑盒门（§3 纪律）

| 门 | 断言 |
|----|------|
| 外部终态 | 真 LLM 返回的 `content` 写入 `AssistantMessage` 且非空（不是 mock） |
| 返回码 | SDK 调用返回 200（无 `LLMClientError` 抛出） |
| per-instance | mimo（anthropic）和 mytokenland（openai）**分别**验证，不能只验 mimo |
| streaming | stream 模式下，第一个 text_delta 在 `complete()` 返回**之前**到达（用 timing 验证真流式，不是先收完再切片） |

### 6.4 性能验证

| 指标 | 期望 |
|------|------|
| 首 token 延迟（stream 模式） | < 2s（mimo 真实响应） |
| 完整响应延迟 | 与手写 httpx 持平或更快（SDK 连接池优化） |

---

## 七、PR 拆分

### PR1：基础设施 + anthropic 协议

1. 加依赖 `anthropic>=0.40,<1.0`、`openai>=1.50,<3.0`
2. 新建 `providers/transports/{base,types,anthropic}.py`
3. 新建 `providers/{llm_client,errors,json_utils}.py`
4. `LLMClient` 实现 `complete` / `complete_json` / `stream`（anthropic 协议）
5. 迁移 `memory/retrieval.py`、`cli/doctor.py` 到 `LLMClient.complete_json`
6. 迁移 `agent/generic_runtime.py` 到 `LLMClient.stream`（anthropic 协议）
7. 删除 `llm_client.py`（根目录）、`providers/transport.py`、`providers/mimo_transport.py`
8. 单测 + live_api 测试（mimo）
9. 文档同源更新

### PR2：openai 协议 + 图像 fallback

1. 新建 `providers/transports/openai_chat.py`
2. `LLMClient` 扩展支持 openai 协议（`make_transport` 加分支）
3. 图像 strip fallback 迁移
4. live_api 测试（mytokenland）
5. 文档同源更新

**PR2 可延后**：当前主力是 mimo（anthropic 协议），mytokenland 仅作备用。PR1 完成后系统已可运行，PR2 是补全 openai 支持。

---

## 八、风险与回退

### 8.1 风险

| 风险 | 缓解 |
|------|------|
| SDK 版本与 provider API 不兼容 | pin 版本（`anthropic>=0.40,<1.0`、`openai>=1.50,<3.0`） |
| mimo 的 SSE 格式 SDK 不支持 | 已验证 ✅。anthropic SDK `messages.stream()` 调 mimo `https://token-plan-cn.xiaomimimo.com/anthropic` 成功，首 event 0.71s 到达（真流式），`stream.close()` 中断生效，usage 在流末尾返回（input=64, output=39），stop_reason 正常 |
| SDK 引入额外依赖体积 | 接受（两个 SDK 都是轻量纯 Python） |
| 手写图像 strip fallback 逻辑迁移出错 | 单测覆盖 + live_api 带图测试 |

### 8.2 回退

若 PR1 集成后发现 SDK 不兼容 mimo，回退方案：

1. 保留 `LLMClient` 接口不变
2. 内部退回手写 httpx（恢复 `MimoTransport` 的 HTTP 层）
3. Transport 协议转换层保留（这是正确的设计，与 SDK 无关）

回退成本：~1 天（恢复 HTTP 层），不影响调用方。


---

## 十、评审修复记录

本次评审发现 10 处必改项，全部修复：

| # | 问题 | 修复位置 | 说明 |
|---|------|---------|------|
| 1 | `iter_stream_deltas` 签名矛盾 | §2.3 ABC / §5.3 伪代码 | 统一为“接收整个流”，Transport 内部遍历 |
| 2 | openai 流式不返回 usage | §5.3 openai 分支 | 加 `stream_options={"include_usage": True}` |
| 3 | `complete_json` 返回类型退化为 dict | §2.4 LLMClient / §3.2 §3.3 | 新增 `LLMJsonResult` dataclass，含 `payload`/`content`/`elapsed_ms`/`attempts`/`finish_reason`/`public_summary()` |
| 4 | `ConfigError` 不存在 | §2.3 `make_transport` | 替换为 `RuntimeConfigError`（`runtime.py:15`，config-refactor-spec 会 re-export） |
| 5 | 图像 strip fallback 三处未定义 | §2.4 `__init__` / §5.5 | 加 `max_image_strip_attempts`/`key_rotation_attempts` 参数，补 `_extract_error_message` 函数，明确 strip 外层 + key 内层的嵌套策略 |
| 6 | `_aggregate` 调用方迁移未展示 | §3.1 / §2.4 | 补聚合迁移示例、`turn.py`/`benchmarking` 迁移示例，`complete()` 明确 = `_aggregate_deltas(list(stream(...)))` |
| 7 | 测试文件处置未列 | §4.1 | 补 3 个测试文件 + 1 个 FakeTransport 的处置方案 |
| 8 | openai stream 资源清理 | §5.3 openai 分支 | 改用 `with` 块，确保 break 后 close |
| 9 | SDK 版本约束矛盾 | §2.2 / §4.3 / §8.1 | 统一为 `anthropic>=0.40,<1.0`、`openai>=1.50,<3.0` |
| 10 | mimo SSE 真环境验证 | §8.1 风险表 / §6.2 集成测试 | 风险表更新为“已验证 ✅”，补真环境测试 `test_live_mimo_stream_sse` |
