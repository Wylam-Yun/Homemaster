# 配置系统重构 Spec

Date: 2026-06-27

关联：[audit-and-refactor-spec.md](audit-and-refactor-spec.md) 第二节
依赖：无（独立重构，不依赖其他 V1.6 spec）

---

## 一、问题现状（已验证）

### 1.1 两套配置并存，新路径实际空跑

| 路径 | 文件 | schema | 状态 |
|------|------|--------|------|
| 旧 | `config/api_config.json` | 扁平 `providers: list` | **真实生效** |
| 新 | `config/homemaster.json` | 嵌套 `providers: {default, items}` | **不存在，运行时返回默认值** |
| 桥接 | `src/homemaster/config/resolution.py` | 优先新、fallback 旧 | 永远 fallback |

`load_model_config` 在 `homemaster.json` 不存在时返回 `HomeMasterConfig()` 默认值 → `resolution.py` 检测 `config.providers.items` 为空 → fallback 到 `load_provider_config` 读 `api_config.json`。**新配置系统（Pydantic 模型 + 5 个文件 343 行）写了但从未生效。**

V1.6 目标不是继续沿用 JSON，而是把真实生效文件迁到 `config/homemaster.yaml`。上表中的 `homemaster.json` 是当前未生效旧设计，迁移后删除。

### 1.2 runtime.py 三宗罪

| 问题 | 位置 | 影响 |
|------|------|------|
| 命名冲突 | `runtime.py`（配置加载）vs `agent/generic_runtime.py`（agent 循环） | 12+ 文件导入，命名误导 |
| 导入时副作用 | `runtime.py:125-126` 在 import 时执行 `_defaults_cfg = load_runtime_defaults_config()` | 任何 `from homemaster.runtime import RuntimeConfigError` 都触发磁盘 I/O |
| 与 model_config 重叠 | `load_homemaster_config` / `get_config_section` / `load_provider_client_config` 等 | 同一份 JSON 被解析多次 |

### 1.3 config/ 文件碎片化

| 文件 | 行数 | 职责 | 处置 |
|------|------|------|------|
| `model_config.py` | 120 | Pydantic 模型 + loader | **保留，扩展为唯一 config 模块** |
| `runtime_settings.py` | 82 | RuntimeSettings（与 HomeMasterConfig 字段重叠） | **删除** |
| `model_profiles.py` | 43 | 只知道 mimo v2.5 = 1M | **删除，context window 改为显式配置** |
| `resolution.py` | 59 | 新旧桥接 | **删除** |
| `runtime_paths.py` | 39 | `validate_run_id`（生产零引用） | **删除；若运行目录仍需校验，迁到 runtime artifact 工具模块，不放配置模块** |

### 1.4 死文件

- `config/nvidia_api_config.json` —— 全项目零引用，重复 API key
- `config/api_config.json` —— 统一后被 `homemaster.yaml` 取代

### 1.5 RuntimeSettings 与 HomeMasterConfig 冗余

`RuntimeSettings` 字段与 `HomeMasterConfig` 几乎完全重叠：

| HomeMasterConfig | RuntimeSettings | 关系 |
|------------------|-----------------|------|
| `context: ContextPolicyConfig` | `context: ContextPolicyConfig` | 完全相同 |
| `runtime: RuntimeGuardConfig` | `runtime_guards: RuntimeGuardConfig` | 完全相同（仅改名） |
| `prompts: PromptConfig` | `prompts: PromptConfig` | 完全相同 |
| `providers: ProviderConfigSection` | `provider_name: str` | RuntimeSettings 只存了名字 |
| （无对应字段） | `embedding_provider_name: str` | RuntimeSettings 独有 |
| （无对应字段） | `config_path: Path \| None` | RuntimeSettings 独有 |
| （无对应字段） | `run_id: str` | RuntimeSettings 独有 |
| （无对应字段） | `max_turns: int` | RuntimeSettings 独有 |
| （无对应字段） | `runtime_root: Path` | RuntimeSettings 独有（与 `RuntimeGuardConfig.runtime_root` 重复） |
| （无对应字段） | `debug_root: Path` | RuntimeSettings 独有（与 `RuntimeGuardConfig.debug_root` 重复） |
| （无对应字段） | `results_root: Path` | RuntimeSettings 独有（与 `RuntimeGuardConfig.results_root` 重复） |
| （无对应字段） | `memory_path: Path \| None` | RuntimeSettings 独有 |
| （无对应字段） | `world_path: Path \| None` | RuntimeSettings 独有；当前传参链路未实际使用，V1.6 删除 |

`RuntimeSettings` 有 10 个非重叠字段：`run_id`、`max_turns`、`runtime_root`、`debug_root`、`results_root`、`provider_name`、`embedding_provider_name`、`config_path`、`memory_path`、`world_path`。其中 `runtime_root`/`debug_root`/`results_root` 与 `RuntimeGuardConfig` 的对应字段完全重复。这些字段可作为函数参数显式传入，不需要单独一个类；`world_path` 当前不被 tool executor 消费，迁移时直接删除。`load_runtime_settings` 内部又调了一遍 `load_homemaster_config` + `load_model_config`——同一个 JSON 被解析两次。

---

## 二、设计决策

### 2.1 schema 选型：YAML + Pydantic

**选择**：统一用 `config/homemaster.yaml` 嵌套 schema，Pydantic 类型化。

**理由**：
- Pydantic 模型已写好（`HomeMasterConfig` + 5 个子模型），直接用
- 嵌套 dict 能容纳 provider / context / runtime / prompts / retrieval_scoring / grounding / provider_client / runtime_paths / runtime_defaults 全部配置
- 扁平 `api_config.json` schema 不支持非 provider 配置
- YAML 比 JSON 更适合人工维护复杂配置，支持注释，后续 provider / observability / prompts 配置更容易审阅
- Pydantic 是运行时 schema：负责类型校验、默认值、Path/tuple 等 Python 类型转换；不单独维护手写 JSON Schema，避免两套 schema 漂移

**迁移**：把 `api_config.json` 的 4 个 provider 迁到 `homemaster.yaml` 的 `providers.items`。

### 2.2 API key 管理：B3 配置文件优先 + 环境变量覆盖

**选择**：配置文件优先，环境变量覆盖。

**理由**：
- 开发用本地配置文件（私有库 + 本地开发，key 可入库）
- 部署/CI 用环境变量覆盖（无需改文件）
- 保留 `api_keys: tuple[str, ...]` 字段以兼容未来 key rotation

**覆盖规则**：

```python
def resolve_api_keys(provider_name: str, config_keys: tuple[str, ...]) -> tuple[str, ...]:
    env_key = f"HOMEMASTER_{provider_name.upper()}_API_KEY"
    env_value = os.environ.get(env_key)
    if env_value:
        return (env_value,)
    return config_keys
```

- env 变量名：`HOMEMASTER_{PROVIDER_NAME_UPPER}_API_KEY`，如 `HOMEMASTER_MIMO_API_KEY`
- env 存在时**完全覆盖**配置文件的 keys（不做合并，简化语义）
- env 不存在时用配置文件的 `api_keys`

**.example 模板**：保留 `homemaster.example.yaml` 作为字段说明文档 + 新机器上手模板，key 字段用占位符 `"<your-api-key>"`。

### 2.3 文件结构：方案 ① 单文件 + 单模块

```
src/homemaster/
├── config/
│   ├── __init__.py        (re-export 公开 API)
│   ├── config.py          (~450 行，Pydantic 模型 + YAML loader + env 覆盖)
│   ├── observability.py   (~60 行，ObservabilityConfig，由 observability-spec 定义)
│   └── (删除 model_profiles / runtime_settings / resolution / runtime_paths)
└── (删除 runtime.py)
```

**注**：`config/observability.py` 由 [observability-spec §8](observability-spec.md) 定义，本 spec 不负责其内容，但在目标结构中为其保留位置。`ObservabilityConfig` 从 `homemaster.yaml` 的 `observability` section 加载（若有），否则用代码默认值。

`config/config.py` 内容：

```python
# 1. 常量
REPO_ROOT = Path(__file__).resolve().parents[2]
HOMEMASTER_CONFIG_PATH = REPO_ROOT / "config" / "homemaster.yaml"

# 2. 异常
class ConfigError(RuntimeError): ...

# 3. Pydantic 模型（从 model_config.py 迁入 + 5 个新增子模型）
class ProviderProfileConfig(BaseModel):
    """单个 provider 配置。"""
    name: str
    api_format: ApiFormatName  # "anthropic" | "openai"
    transport: TransportName = "raw_http"  # "anthropic_sdk" | "openai_sdk" | "raw_http"
    base_url: str
    model: str
    api_keys: tuple[str, ...] = Field(default_factory=tuple)
    context_window_tokens: int
    max_output_tokens: int | None = None
    embedding_url: str | None = None
    kind: Literal["chat", "embedding"] = "chat"
    # 用于区分 LLM provider 和 embedding provider。
    # LLMClient 构造时校验 kind=="chat"，EmbeddingClient 构造时校验 kind=="embedding"。

class ProviderConfigSection(BaseModel):
    default: str = "Mimo"
    items: list[ProviderProfileConfig] = Field(default_factory=list)

class ContextPolicyConfig(BaseModel): ...
class RuntimeGuardConfig(BaseModel): ...
class PromptConfig(BaseModel): ...

# ---- 以下 5 个为新增子模型，覆盖 homemaster.example.yaml 全部 9 个 section ----

class RetrievalScoringConfig(BaseModel):
    """检索评分配置（§4.4 MEMORY_CASE_ROOT/MEMORY_RESULTS_ROOT 迁移目标之一）。"""
    metadata_weights: dict[str, float] = Field(default_factory=dict)
    rrf_k: int = 60
    top_k_limit: int = 50

class GroundingConfig(BaseModel):
    """空间锚定提示词配置。"""
    room_hints: dict[str, list[str]] = Field(default_factory=dict)
    anchor_hints: dict[str, list[str]] = Field(default_factory=dict)
    specific_anchor_words: dict[str, list[str]] = Field(default_factory=dict)

class ProviderClientConfig(BaseModel):
    """HTTP client 超时配置。"""
    timeout_s: float = 60.0
    connect_timeout_s: float = 10.0
    write_timeout_s: float = 15.0
    pool_timeout_s: float = 10.0

class RuntimePathsConfig(BaseModel):
    """运行时路径配置（取代 RuntimeGuardConfig 里的 runtime_root/debug_root/results_root
    以及 §4.4 的 MEMORY_CASE_ROOT/MEMORY_RESULTS_ROOT 等模块级常量）。"""
    runtime_root: Path | None = None
    debug_root: Path | None = None
    test_results_root: Path | None = None
    llm_case_root: Path | None = None
    memory_case_root: Path | None = None
    memory_results_root: Path | None = None

class RuntimeDefaultsConfig(BaseModel):
    """运行时默认值（取代 DEFAULT_PROVIDER_NAME/DEFAULT_EMBEDDING_PROVIDER_NAME）。"""
    default_provider_name: str = "Mimo"
    default_embedding_provider_name: str = "MemoryEmbedding"

class HomeMasterConfig(BaseModel):
    """顶层配置模型，覆盖 homemaster.example.yaml 全部 9 个 section。"""
    providers: ProviderConfigSection = Field(default_factory=ProviderConfigSection)
    context: ContextPolicyConfig = Field(default_factory=ContextPolicyConfig)
    runtime: RuntimeGuardConfig = Field(default_factory=RuntimeGuardConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)
    retrieval_scoring: RetrievalScoringConfig = Field(default_factory=RetrievalScoringConfig)
    grounding: GroundingConfig = Field(default_factory=GroundingConfig)
    provider_client: ProviderClientConfig = Field(default_factory=ProviderClientConfig)
    runtime_paths: RuntimePathsConfig = Field(default_factory=RuntimePathsConfig)
    runtime_defaults: RuntimeDefaultsConfig = Field(default_factory=RuntimeDefaultsConfig)

    def get_provider(self, name: str | None = None) -> ProviderProfileConfig: ...

# 4. Loader（无导入副作用，只在调用时读磁盘）
def load_config(config_path: Path | None = None) -> HomeMasterConfig: ...

# 5. env 覆盖
def _apply_env_overrides(config: HomeMasterConfig) -> HomeMasterConfig: ...
```

**行数估算**：9 个 Pydantic 子模型 + YAML loader + env 覆盖，预计 400-500 行。当前 `model_config.py` 已 120 行，加上 5 个新子模型（每个 ~15-30 行）和 loader 逻辑，仍在单文件可控范围内。若后续子模型持续膨胀，可拆为 `config/models.py` + `config/loader.py`，但当前阶段单文件足够，拆分会增加模块间导入循环风险（`models.py` 需要 `ConfigError`，`loader.py` 需要所有模型）。

### 2.4 Provider SDK 边界

**选择**：不采用 OpenAI Agents SDK；只采用 OpenAI Python SDK 和 Anthropic Python SDK 作为 provider transport 实现。

边界：
- HomeMaster 继续保留自己的 `GenericAgentRuntime`、`ContextAssembler`、`ToolDispatcher`、context compaction、task-state snapshot、event sink。
- SDK 只负责 provider API 请求、SSE streaming、SDK 错误类型、超时/重试，以及把 provider stream event 转换成 HomeMaster 的 `TransportDelta`。
- OpenAI API 格式 provider 使用 `openai` Python SDK；Anthropic Messages API 格式 provider 使用 `anthropic` Python SDK。
- 兼容网关（MiMo / huya / mytokenland）如果官方 SDK 兼容性不足，允许通过 `transport: raw_http` 回退到当前手写 HTTP transport。

配置字段：

```yaml
providers:
  items:
    - name: Mimo
      kind: chat
      api_format: anthropic
      transport: anthropic_sdk
      base_url: https://token-plan-cn.xiaomimimo.com/anthropic
      model: mimo-v2.5
      api_keys: ["..."]
      context_window_tokens: 1000000
```

### 2.5 删除 RuntimeSettings

`RuntimeSettings` 的字段改为函数参数（显式传）或 `RunContext` 直接字段：

```python
# 之前
def run_agent(settings: RuntimeSettings): ...

# 之后
def run_agent(
    config: HomeMasterConfig,
    *,
    run_id: str,
    max_turns: int = 12,
    provider_name: str = "Mimo",
    embedding_provider_name: str = "MemoryEmbedding",
    config_path: Path | None = None,
    runtime_root: Path = Path("/tmp/homemaster/runs"),
    debug_root: Path = Path("/tmp/homemaster/debug"),
    results_root: Path = Path("/tmp/homemaster/results"),
    memory_path: Path | None = None,
): ...
```

迁移说明：
- `run_id`、`max_turns`、`memory_path`：与旧方案一致，作为函数参数显式传入。
- `world_path`：删除。当前只在 CLI/registry 构造链路里传递，`target_grounder` executor 没有消费它；V1.6 不保留死参数。
- `runtime_root`、`debug_root`、`results_root`：当前从 `RuntimeGuardConfig` 已有对应字段（`runtime_root`/`debug_root`/`results_root`），运行时优先取函数参数，未传时 fallback 到 `config.runtime.runtime_root` 等。**调用方 `agent/turn.py:105-107` 已经硬编码 `Path("/tmp/homemaster/runs")` 等值，可直接改为读 `config.runtime` 对应字段。**
- `provider_name`、`embedding_provider_name`：函数参数显式传入（当前 `agent/turn.py:209` 用 `settings.provider_name`，`retrieval.py` 用 `DEFAULT_PROVIDER_NAME`）。
- `config_path`：函数参数显式传入（当前 `agent/turn.py:208` 用 `settings.config_path`）。

调用方（CLI / turn / test）显式传入这些参数，不再用 `RuntimeSettings` 包一层。

### 2.6 context_window 显式配置

删除 `model_profiles.py` 和模型名字符串推断逻辑。`context_window_tokens` 是 provider 配置的显式必填字段；如果某个测试或本地临时配置没有提供，loader 可以使用 `DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000` 作为保守默认值并记录 warning，但正式 `homemaster.yaml` 和 example 必须写明。

理由：
- OpenAI / Anthropic 官方 SDK 能提供部分模型元数据，但不同 provider 和 OpenAI-compatible 网关不保证暴露上下文窗口。
- MiMo / huya / mytokenland 这类兼容网关可能没有可靠 `/models` 元数据。
- 通过模型名猜测 `mimo-v2.5 = 1_000_000` 脆弱且不可审计。

### 2.7 validate_run_id 处置

`validate_run_id` 不是配置逻辑，不放进 `config.py`。删除 `runtime_paths.py` 后有两种允许处置：

1. 若 V1.6 运行目录仍接受外部 `run_id` 字符串：迁到运行 artifact 辅助模块，例如 `homemaster.agent.run_ids` 或 `homemaster.events.paths`，并在创建 run dir 前显式调用。
2. 若运行入口只生成内部 UUID-like run_id：可以先删除该 helper 和对应测试，后续有外部 run_id 输入时再加回来。

保留原则：`run_id` 可以继续是字符串，不强制数字；如果作为路径片段使用，必须防止 `/`、`\`、`.`、`..`、控制字符和超长值。

---

## 三、迁移后的 homemaster.yaml

把 `api_config.json` 的 4 个 provider 合并到 `homemaster.yaml` 结构：

```yaml
providers:
  default: Mimo
  items:
    - name: Mimo
      kind: chat
      api_format: anthropic
      transport: anthropic_sdk
      base_url: https://token-plan-cn.xiaomimimo.com/anthropic
      model: mimo-v2.5
      api_keys:
        - "<your-api-key>"
      context_window_tokens: 1000000
      max_output_tokens: null

    - name: MemoryEmbedding
      kind: embedding
      api_format: openai
      transport: openai_sdk
      base_url: https://api.siliconflow.cn/v1
      model: BAAI/bge-m3
      api_keys:
        - "<your-api-key>"
      embedding_url: https://api.siliconflow.cn/v1/embeddings
      context_window_tokens: 8192

    - name: huya_anthropic
      kind: chat
      api_format: anthropic
      transport: anthropic_sdk
      base_url: https://copilot.huya.info/api/anthropic
      model: z-ai/glm-5.1
      api_keys:
        - "<your-api-key>"
      context_window_tokens: 128000

    - name: mytokenland
      kind: chat
      api_format: openai
      transport: openai_sdk
      base_url: https://api.mytokenland.com/v1
      model: glm-5.1
      api_keys:
        - "<your-api-key>"
      context_window_tokens: 128000

context: { ... }             # 现有
runtime: { ... }             # 现有
prompts: { ... }             # 现有
retrieval_scoring: { ... }   # 现有
grounding: { ... }           # 现有
provider_client: { ... }     # 现有
runtime_paths: { ... }       # 现有
runtime_defaults: { ... }    # 现有
```

**注意**：
- Mimo 的 `base_url` 改为 `https://token-plan-cn.xiaomimimo.com/anthropic`（cn endpoint，旧 sgp endpoint 已 401 失效），`model` 改为 `mimo-v2.5`（与 `api_config.json` 一致）。
- 每个 provider 加 `kind: "chat" | "embedding"` 字段。`ProviderProfileConfig` Pydantic 模型定义：
  ```python
  kind: Literal["chat", "embedding"] = "chat"
  ```
  用途：`LLMClient` 构造时校验 `kind=="chat"`，`EmbeddingClient` 构造时校验 `kind=="embedding"`。
- 每个 provider 加 `api_format: "anthropic" | "openai"` 和 `transport: "anthropic_sdk" | "openai_sdk" | "raw_http"`。`api_format` 描述请求/响应协议，`transport` 描述具体实现。
- 每个 provider 显式写 `context_window_tokens`，不再从 model name 推断。
- `MemoryEmbedding` 的 `kind` 为 `"embedding"`（旧 `api_config.json` 已有对应字段）。
- provider `default` 用 `"Mimo"`（旧 `api_config.json` 的 name）而非 `"mimo_v25"`（旧 example 的 name），保持与现有代码一致。
- `config/homemaster.yaml` 是本地真实配置，可放真实 key；`config/homemaster.example.yaml` 必须使用占位符。

---

## 四、待删除/合并清单

### 4.1 删除（8 个文件，~560 行）

| 文件 | 行数 | 处置 |
|------|------|------|
| `src/homemaster/compat.py` | ~15 | 删除，3 处 import 改 `from enum import StrEnum` |
| `src/homemaster/runtime.py` | 261 | 删除，内容迁入 `config/config.py` |
| `src/homemaster/config/runtime_settings.py` | 82 | 删除，`RuntimeSettings` 废弃 |
| `src/homemaster/config/model_profiles.py` | 43 | 删除，context window 改为 provider 显式配置 |
| `src/homemaster/config/resolution.py` | 59 | 删除，桥接层消失 |
| `src/homemaster/config/runtime_paths.py` | 39 | 删除；`validate_run_id` 若保留则迁到运行 artifact 工具模块 |
| `config/api_config.json` | 40 | 删除，provider 迁入 `homemaster.yaml` |
| `config/nvidia_api_config.json` | ~20 | 删除，零引用 |

### 4.2 合并

| 来源 | 目标 | 说明 |
|------|------|------|
| `src/homemaster/config/model_config.py` | `src/homemaster/config/config.py` | 重命名 + 扩展（吸收 runtime.py 的 loader/常量；删除 model_profiles 推断；不吸收 validate_run_id） |

### 4.3 import 迁移（13 个文件）

`from homemaster.runtime import X` → `from homemaster.config import X`（经 grep 验证完整列表，共 13 个文件）：

```
src/homemaster/embedding_client.py       # ProviderConfig, load_provider_client_config
src/homemaster/llm_client.py             # ProviderConfig, load_provider_client_config
src/homemaster/memory/retrieval.py       # DEFAULT_CONFIG_PATH, DEFAULT_EMBEDDING_PROVIDER_NAME,
                                         #   DEFAULT_PROVIDER_NAME, MEMORY_CASE_ROOT,
                                         #   MEMORY_RESULTS_ROOT, REPO_ROOT, ProviderConfig,
                                         #   RuntimeConfigError, get_config_section,
                                         #   load_homemaster_config, load_provider_config
src/homemaster/agent/turn.py             # HOMEMASTER_CONFIG_PATH
src/homemaster/cli/errors.py             # RuntimeConfigError
src/homemaster/cli/doctor.py             # DEFAULT_CONFIG_PATH, DEFAULT_EMBEDDING_PROVIDER_NAME,
                                         #   DEFAULT_PROVIDER_NAME, GENERIC_CONFIG_PATH,
                                         #   REPO_ROOT, RuntimeConfigError, load_provider_config
src/homemaster/domain/home/grounding.py  # RuntimeConfigError, get_config_section, load_homemaster_config
src/homemaster/config/model_config.py    # RuntimeConfigError, HOMEMASTER_CONFIG_PATH, REPO_ROOT
                                         #   （该文件本身将被合并到 config/config.py）
tests/homemaster/test_embedding_client.py # ProviderConfig
tests/homemaster/test_e2e_real_api.py     # DEFAULT_CONFIG_PATH, load_provider_config
tests/homemaster/test_home_world.py       # REPO_ROOT
tests/homemaster/test_llm_client.py       # load_provider_config
```

**排除**：`src/homemaster/__init__.py`（`__all__` 里的 `"runtime"` 是字符串，不是 import）。

`from homemaster.config.runtime_settings import RuntimeSettings` → 改为函数参数（见 2.5）：

```
src/homemaster/agent/turn.py                    # load_runtime_settings
src/homemaster/agent/normalized.py              # RuntimeSettings
src/homemaster/benchmarking/alfworld/runner.py  # load_runtime_settings
tests/homemaster/test_tool_dispatcher.py        # RuntimeSettings
tests/homemaster/test_task_state_tools.py       # RuntimeSettings
tests/homemaster/test_domain_home_tools.py      # RuntimeSettings
tests/homemaster/test_domain_memory_tools.py    # RuntimeSettings
tests/homemaster/test_runtime_settings.py       # RuntimeSettings, load_runtime_settings
tests/homemaster/benchmarking/test_alfworld_tools.py # RuntimeSettings
tests/homemaster/test_e2e_real_api.py           # RuntimeSettings
```

`from homemaster.config.model_profiles import resolve_context_window_tokens` → 删除，调用方直接使用 `provider.context_window_tokens`：

```
src/homemaster/agent/context_assembler.py
tests/homemaster/test_model_config.py
```

`from homemaster.config.resolution import resolve_provider_profile` → `load_config().get_provider()`：

```
src/homemaster/agent/turn.py                    # 行 39, 181
src/homemaster/benchmarking/alfworld/runner.py  # 行 38
tests/homemaster/test_config_resolution.py      # 行 7
```

### 4.4 公开 API（`config/__init__.py` re-export）

**核心 re-export**（`config/__init__.py`）：

```python
from homemaster.config.config import (
    # 异常
    ConfigError,
    # Pydantic 模型
    HomeMasterConfig,
    ProviderProfileConfig,
    ProviderConfigSection,
    ContextPolicyConfig,
    RuntimeGuardConfig,
    PromptConfig,
    RetrievalScoringConfig,
    GroundingConfig,
    ProviderClientConfig,
    RuntimePathsConfig,
    RuntimeDefaultsConfig,
    # 常量
    REPO_ROOT,
    HOMEMASTER_CONFIG_PATH,
    # 函数
    load_config,
)
```

调用方统一 `from homemaster.config import ...`。

**被生产代码引用但不在 re-export 中的符号——逐一迁移方案**：

| 符号（当前 `from homemaster.runtime import ...`） | 引用文件 | 处置 |
|---|---|---|
| `DEFAULT_PROVIDER_NAME` | `retrieval.py:27`, `doctor.py:19` | 保留为模块级常量 `DEFAULT_PROVIDER_NAME = "Mimo"`（代码默认值），re-export 到 `homemaster.config` 顶层。调用方改 `from homemaster.config import DEFAULT_PROVIDER_NAME`。 |
| `DEFAULT_EMBEDDING_PROVIDER_NAME` | `retrieval.py:26`, `doctor.py:18` | 同上，保留为模块级常量 `DEFAULT_EMBEDDING_PROVIDER_NAME = "MemoryEmbedding"`，re-export。 |
| `DEFAULT_CONFIG_PATH` | `retrieval.py:25`, `doctor.py:17`, `test_e2e_real_api.py:16` | 迁移为 `load_config().runtime_paths` 或 `HOMEMASTER_CONFIG_PATH`。若调用方需要"当前生效的配置文件路径"，改为 `load_config().__config_path__`（loader 记录实际读取的路径）。re-export 可选。 |
| `GENERIC_CONFIG_PATH` | `doctor.py:20` | 与 `DEFAULT_CONFIG_PATH` 同义，统一为 `HOMEMASTER_CONFIG_PATH` 或废弃。 |
| `MEMORY_CASE_ROOT` | `retrieval.py:28` | 迁移到 `RuntimePathsConfig.memory_case_root`。调用方改为 `load_config().runtime_paths.memory_case_root`（值为 `str \| None`，调用方自行判空 + fallback）。 |
| `MEMORY_RESULTS_ROOT` | `retrieval.py:29` | 同上，迁移到 `RuntimePathsConfig.memory_results_root`。 |
| `ProviderConfig`（旧 dataclass） | `retrieval.py:31`, `embedding_client.py:13`, `test_embedding_client.py:13` | **废弃**。调用方改用 `ProviderProfileConfig`（Pydantic 模型）。旧 `ProviderConfig` 是 `runtime.py` 定义的 dataclass，字段与 `ProviderProfileConfig` 对应。迁移时改类型注解 + 构造方式（Pydantic 构造 `ProviderProfileConfig(**kw)` 替代 `ProviderConfig(**kw)`）。 |
| `load_provider_config` | `retrieval.py:35`, `doctor.py:23`, `test_llm_client.py:15`, `test_e2e_real_api.py:16` | **废弃**。调用方改用 `load_config().get_provider(name)`。旧 `load_provider_config(config_path)` 返回 `dict[str, ProviderConfig]`，新 API 返回 `ProviderProfileConfig` 对象。 |
| `load_provider_client_config` | `embedding_client.py:13`, `llm_client.py:13` | **废弃**。调用方改用 `load_config().provider_client`（返回 `ProviderClientConfig`，含 `timeout_s` 等字段）。 |
| `get_config_section` | `retrieval.py:33`, `grounding.py:8` | **废弃**。调用方改用 `load_config()` 返回的完整 `HomeMasterConfig` 对象，直接 `.retrieval_scoring` / `.grounding` 等属性访问。 |
| `load_homemaster_config` | `retrieval.py:34`, `grounding.py:8` | **废弃**。统一改用 `load_config()`。旧函数返回 raw dict，新函数返回 Pydantic 模型。 |
| `RuntimeConfigError` | `retrieval.py:32`, `errors.py:11`, `doctor.py:22`, `grounding.py:8` | 重命名为 `ConfigError`（re-export），`RuntimeConfigError` 作为别名保留一个版本过渡期后删除。 |

---

## 五、实现纪律

### 5.1 导入无副作用（硬规则）

`config/config.py` 模块顶层**禁止**任何磁盘 I/O。所有 `load_*` 函数必须显式调用。模块导入只定义常量、类、函数，不执行读文件。

**反例**（当前 `runtime.py:125-126`）：
```python
_defaults_cfg = load_runtime_defaults_config()  # 导入时执行 ❌
_paths_cfg = load_runtime_paths_config()        # 导入时执行 ❌
DEFAULT_PROVIDER_NAME = _defaults_cfg.get(...)
```

**正例**：
```python
DEFAULT_PROVIDER_NAME = "Mimo"  # 代码默认值

def load_config(config_path: Path | None = None) -> HomeMasterConfig:
    path = config_path or HOMEMASTER_CONFIG_PATH
    if not path.exists():
        return HomeMasterConfig()  # 代码默认值
    return HomeMasterConfig.model_validate(yaml.safe_load(path.read_text()) or {})
```

`DEFAULT_PROVIDER_NAME` 等"代码默认值"在 `HomeMasterConfig` 的 Pydantic 字段默认值里定义，不在模块顶层计算。

### 5.2 Loader 单次解析

`load_config` 内部只解析 YAML 一次，返回完整 `HomeMasterConfig`。所有子配置（context / runtime / prompts / providers）从同一个对象取，不再有"先读 raw dict 判断 typed config、再调 model_config"的双重解析。

### 5.3 env 覆盖在 loader 末尾应用

```python
def load_config(config_path: Path | None = None) -> HomeMasterConfig:
    # 1. 读文件（或返回默认）
    config = _read_config_file(config_path)
    # 2. env 覆盖 api_keys
    config = _apply_env_overrides(config)
    return config
```

`_apply_env_overrides` 遍历 `config.providers.items`，对每个 provider 检查 `HOMEMASTER_{NAME}_API_KEY` env 变量。

### 5.4 兼容性

- **不保留** `runtime.py` / `runtime_settings.py` / `resolution.py` 的旧 API（不做 deprecation shim，直接删）
- 调用方一次性迁移，迁移完跑全量测试
- `config/homemaster.yaml` 不存在时返回代码默认值（与当前行为一致）
- `api_config.json` 删除后，旧路径 fallback 代码（`resolution.py`）一并删除

---

## 六、验证计划

### 6.1 单元测试

| 测试 | 断言 |
|------|------|
| `test_load_config_default` | 文件不存在时返回 `HomeMasterConfig()` 默认值，且无异常 |
| `test_load_config_from_file` | 读 `homemaster.yaml`，Pydantic 模型字段正确填充 |
| `test_env_override_api_key` | 设 `HOMEMASTER_MIMO_API_KEY=env-key`，`load_config()` 后 Mimo 的 `api_keys == ("env-key",)` |
| `test_env_override_missing` | 不设 env，`api_keys` 保持配置文件值 |
| `test_provider_context_window_required_in_example` | example 中每个 provider 都显式配置 `context_window_tokens` |
| `test_provider_transport_values` | `transport` 只能为 `anthropic_sdk` / `openai_sdk` / `raw_http` |
| `test_provider_api_format_values` | `api_format` 只能为 `anthropic` / `openai` |
| `test_no_import_side_effects` | `import homemaster.config` 不触发磁盘 I/O（mock `Path.read_text` 验证未调用） |
| `test_openai_sdk_transport_stream_maps_deltas` | OpenAI SDK stream event 被转换为 HomeMaster `TransportDelta` |
| `test_anthropic_sdk_transport_stream_maps_deltas` | Anthropic SDK stream event 被转换为 HomeMaster `TransportDelta` |

### 6.2 集成验证

- `hm` CLI 启动正常，能加载配置、调 LLM
- `python -c "from homemaster.config import load_config; print(load_config())"` 输出配置（key 脱敏）
- 现有所有测试通过（迁移 import 后）

### 6.3 黑盒门（§3 纪律）

| 门 | 断言 |
|----|------|
| 外部终态 | `load_config()` 真的能从磁盘读到 `homemaster.yaml` 并填充 Pydantic 模型（不是返回默认值） |
| 返回码 | env 覆盖后，provider transport 用 env key 调 LLM 返回 200（不是 401） |
| per-instance | 4 个 provider 分别验证 `api_keys` 正确（不是只验 Mimo） |

---

## 七、PR 拆分

### PR1（核心重构，单 PR）

1. 新建 `src/homemaster/config/config.py`，吸收 `model_config.py` + `runtime.py` loader + env 覆盖；删除 `model_profiles` 推断逻辑，不吸收 `validate_run_id`
2. 新建 `config/homemaster.yaml`（从 `api_config.json` 迁移 4 个 provider）
3. 新建/更新 `config/homemaster.example.yaml`（与 `homemaster.yaml` 同步，key 用占位符）
4. 更新 `config/__init__.py` re-export
5. 增加 `openai` / `anthropic` / `pyyaml` 主依赖
6. 新增 OpenAI SDK / Anthropic SDK transport，实现 provider stream event → `TransportDelta`
7. 迁移 12 个文件的 import
8. 删除 8 个垃圾文件
9. 删除 `RuntimeSettings`，改为函数参数；删除 `world_path` 死参数
10. 单元测试 + 集成验证
11. 文档同源更新（README / CHANGELOG）

### PR2（无）

本 spec 无 PR2 延迟项，全部在 PR1 完成。

---

## 八、评审修复记录

2026-06-27 独立评审发现 4 处必改（+ 2 处附带修正），已逐条修复。

2026-07-02 讨论后追加 5 项设计修正：配置文件改 YAML；删除未使用的 `world_path`；不采用 OpenAI Agents SDK，只在 transport 层使用 OpenAI / Anthropic Python SDK；context window 改为显式配置；`validate_run_id` 不放配置模块。

| # | 问题 | 位置 | 修法 |
|---|------|------|------|
| 1 | RuntimeSettings 字段漏列（实际 10 个非重叠字段，spec 说 4 个） | §1.5, §2.5 | §1.5 表格补全 10 个字段对照关系；§2.5 代码示例扩为函数参数 + 迁移说明 |
| 2 | re-export 清单不全（漏 10 个被生产代码引用的符号） | §4.4 | 补全 `DEFAULT_PROVIDER_NAME`、`DEFAULT_EMBEDDING_PROVIDER_NAME`、`DEFAULT_CONFIG_PATH`、`GENERIC_CONFIG_PATH`、`MEMORY_CASE_ROOT`、`MEMORY_RESULTS_ROOT`、`ProviderConfig`、`load_provider_config`、`load_provider_client_config`、`get_config_section`、`load_homemaster_config` 的处置方案，每个符号明确：保留/废弃/迁移路径 + 调用方迁移方式 |
| 3 | HomeMasterConfig 只覆盖 4/9 section（缺 RetrievalScoringConfig、GroundingConfig、ProviderClientConfig、RuntimePathsConfig、RuntimeDefaultsConfig） | §2.3 | 补全 5 个子模型的 Pydantic 定义；`HomeMasterConfig` 加 5 个字段；重新估算行数（400-500 行），说明暂不拆文件的 trade-off |
| 4 | import 迁移清单有错（2 个错列入 + 5 个漏列） | §4.3 | 删 `__init__.py`（只是 `__all__` 字符串）；补 5 个漏列文件（`model_config.py`、`test_embedding_client.py`、`test_e2e_real_api.py`、`test_home_world.py`、`test_llm_client.py`）；grep 验证完整列表 |
| 5 | cn endpoint | §3 | Mimo `base_url` 改为 cn endpoint（`token-plan-cn.xiaomimimo.com`），`model` 改为 `mimo-v2.5`；真实 key 只写入本地 `homemaster.yaml`，example 使用占位符 |
| 6 | MemoryEmbedding 缺 `kind` 字段定义 | §2.3, §3 | `ProviderProfileConfig` 加 `kind: Literal["chat", "embedding"] = "chat"` 字段定义；§3 每个 provider 加 `kind` 字段；说明 LLMClient/EmbeddingClient 校验逻辑 |
| 7 | 配置格式应改 YAML | §2.1, §3, §5-§7 | 目标文件改为 `config/homemaster.yaml` / `homemaster.example.yaml`；loader 改为 `yaml.safe_load`；`pyyaml` 进入主依赖 |
| 8 | `world_path` 是死参数 | §1.5, §2.5, §7 | 明确删除 `world_path`，不作为新运行参数保留 |
| 9 | SDK 边界不清 | §2.4, §3, §6-§7 | 明确不使用 OpenAI Agents SDK 替代 runtime；只用 `openai` / `anthropic` Python SDK 实现 provider streaming transport |
| 10 | context window 不应靠模型名推断 | §1.3, §2.6, §3, §6 | 删除 `model_profiles.py` 推断逻辑；provider 必须显式配置 `context_window_tokens` |
| 11 | `validate_run_id` 不属于配置 | §1.3, §2.7, §4 | 不内联到 `config.py`；若保留则迁到运行 artifact 工具模块，或在无外部 run_id 输入时删除 |
