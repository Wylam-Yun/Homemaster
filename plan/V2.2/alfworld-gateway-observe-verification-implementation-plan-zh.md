# V2.2 ALFWorld Gateway 动作后观察验证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. 本项目规则要求主 agent 独立实现；subagent 只用于计划完成后的单次计划评审和全部交付完成后的单次最终代码评审。

**Goal:** 让 `homemaster --gateway --alfworld` 从 HomeMaster 项目环境启动 Gateway，并通过受管 loopback HTTP worker 复用既有 ALFWorld 环境中的固定 THOR demo episode；每个真实导航/操作尝试后的下一次模型行为为独立 `observe`，同一张有效 PNG 进入紧接着的 Provider 请求并通过既有 Gateway MEDIA 链路发送给飞书用户。

**Architecture:** 启动时用一个上游环境选择决定 Registry 组成和 application-owned ALFWorld 生命周期，不在运行中做多环境切换。canonical `ToolDefinition` 只新增含义单一的 `requires_model_observation`；Agent Loop 持有可持久化的 observation barrier，并单独跟踪“有效图片尚未被 Provider 首次消费”。现有 `VerificationPolicy`、ALFWorld 外部状态验证和 artifact/MEDIA 链路保持原语义。

**Tech Stack:** Python 3.11、Pydantic 2、Typer、pytest/pytest-asyncio、Pillow、HTTPX + Python 标准库 HTTP server、Anthropic/OpenAI transport、Feishu `lark-oapi`、ALFWorld 0.5.0、AI2-THOR 2.1.0、Xvfb。

**实施状态（2026-07-29）：** owner 已否决统一大环境，选择“小 HTTP 边界 + 两套既有环境”。代码、聚焦测试和文档已完成；待全量门、真实飞书黑盒、最终代码评审和提交。

---

## 0. 锁定决策、基线和文件边界

### 0.1 已核对的 linchpin

- 当前 HomeMaster `.venv`：Python 3.11.15，`lark_oapi` 可 import，`alfworld`/`ai2thor` 不可 import。
- 既有 `/data0/yuqiao/envs/hm_alfworld`：Python 3.11.15，ALFWorld 0.5.0、AI2-THOR 2.1.0、TextWorld 1.7.0 可 import，`lark_oapi` 不可 import。
- ALFWorld asset/code checkout：`/home/haodong2/weilin/red_bird/alfworld`；由既有 `/data0/yuqiao/envs/hm_alfworld` 运行。HomeMaster 环境不重装 ALFWorld/Torch。
- 目标 demo 候选采用已有真机成功证据最多的 Pencil → Shelf / FloorPlan308 路径；最终只在 Task 9 的新鲜 reset/action gate 通过后写入 ignored 真实配置。
- Provider 强制 `tool_choice` 在目标 Mimo 真环境核对前保持 `UNVERIFIED`。MVP 的正确性只依赖工具披露收窄、assistant 结果校验和有界重试。
- 变更前聚焦基线：`75 passed in 8.39s`。

### 0.2 依赖方案比较与选择

1. 标准库 JSONL 子进程 IPC：依赖最小，但双向请求、超时和媒体 framing 需要自建协议。
2. loopback HTTP + JSON：HomeMaster 只增加轻量 `httpx` client，worker 用标准库 server；状态码、超时、媒体和调试边界清晰。**采用。**
3. 在 HomeMaster `.venv` 安装 ALFWorld/Torch：单进程，但依赖大、重复既有环境且存在旧 extra 构建问题，否决。
4. 在 ALFWorld 环境安装完整 HomeMaster/Gateway：污染既有环境并复制 Gateway 依赖，否决。

worker 只监听 `127.0.0.1` 的临时端口，使用随机 bearer token，并由 HomeMaster 进程启动、探活和关闭。HTTP 是工具 executor 到环境的内部 transport；模型仍只与 HomeMaster Provider/runtime 通信。

以下候选外部契约在各自真机 gate 前保持 `UNVERIFIED`：最终固定 demo episode、Mimo forced `tool_choice`、飞书接收侧图片重读。既有 ALFWorld 环境的 THOR reset、动作、PNG 和清理必须在最终黑盒再次核对。

### 0.3 目标文件图

**新增：**

- `src/homemaster/agent/model_observation.py`：barrier 判定、有效图片复核、协议错误结果和外部化 prompt 加载。
- `src/homemaster/prompts/model_observation_required.txt`：固定动态协议提示。
- `src/homemaster/gateway/alfworld.py`：固定 episode 的创建、reset、request binding、独占 session、close。
- `src/homemaster/benchmarking/alfworld/http_client.py`、`http_worker.py`：受管 loopback HTTP client/worker。
- `tests/homemaster/application/test_model_observation_barrier.py`
- `tests/homemaster/application/test_model_observation_resume.py`
- `tests/homemaster/gateway/test_alfworld_gateway_binding.py`
- `tests/homemaster/integration/test_alfworld_gateway_media.py`

**修改：**

- `src/homemaster/tools/contracts.py`、`tools/base.py`、`tools/adapters.py`：canonical 声明及全部实现映射。
- `src/homemaster/adapters/profiles.py`、`adapters/__init__.py`、`adapters/alfworld_entry.py`、`adapters/coworker_entry.py`：通用工具 + 单一显式环境工具组成。
- `src/homemaster/agent/state.py`、`agent/session.py`、`agent/generic_runtime.py`：持久化 barrier、未消费图片和 loop enforcement。
- `src/homemaster/application/session.py`：snapshot 只保留尚未首次消费的观察图片。
- `src/homemaster/channels/bridge.py`、`gateway/runtime.py`：Gateway run request binding。
- `src/homemaster/config/config.py`、`config/homemaster.example.yaml`：完整 ALFWorld 部署配置模板。
- `src/homemaster/cli/app.py`、`cli/composition.py`、`cli/gateway_command.py`：根入口 `--alfworld` 和生命周期 ownership。
- `src/homemaster/benchmarking/alfworld/env_adapter.py`：显式 data root/import identity 接线，保持 benchmark 行为兼容。
- `uv.lock`：仅保留 HomeMaster 已有依赖解析；不加入 ALFWorld/Torch。
- 相关既有测试、`README.md`、`docs/alfworld-user-guide.md`、`docs/architecture/alfworld-harness.md`、`CHANGELOG.md`、`progress.md`。

---

## Task 1：canonical 工具声明和全实现一致性审计

**Files:**

- Modify: `src/homemaster/tools/contracts.py`
- Modify: `src/homemaster/tools/base.py`
- Modify: `src/homemaster/tools/adapters.py`
- Modify: `src/homemaster/adapters/profiles.py`
- Test: `tests/homemaster/tools/test_definition.py`
- Test: `tests/homemaster/tools/test_universal_registry.py`

- [ ] **Step 1：先写 RED 测试**

覆盖：

```python
def test_model_observation_flag_is_canonical_but_not_provider_visible():
    definition = make_definition(requires_model_observation=True)
    assert definition.to_dict()["requires_model_observation"] is True
    assert "requires_model_observation" not in definition.to_model_manifest()


def test_only_alfworld_state_changing_tools_require_model_observation():
    registry = build_tool_registry(environment="alfworld")
    assert registry.get("robot_go_to").requires_model_observation is True
    assert registry.get("robot_manipulate").requires_model_observation is True
    for name in set(registry.all_names()) - {"robot_go_to", "robot_manipulate"}:
        assert registry.get(name).requires_model_observation is False
```

再增加接口 audit：枚举 `ToolDefinition -> RegisteredTool -> FunctionTool/BaseTool`，逐项断言该公开字段没有在适配层丢失。

- [ ] **Step 2：运行并确认按预期失败**

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/tools/test_definition.py \
  tests/homemaster/tools/test_universal_registry.py
```

Expected: FAIL，缺少 `requires_model_observation` 或 `build_tool_registry`。

- [ ] **Step 3：最小实现**

`ToolDefinition`：

```python
requires_model_observation: bool = False

if not isinstance(self.requires_model_observation, bool):
    raise TypeError("requires_model_observation must be a boolean")
```

`to_dict()` 保存字段，`to_model_manifest()` 不保存字段。`BaseTool`/`FunctionTool` 增加同名 bool；`from_registered_tool()` 精确复制。

只在 `_adapted_tool` 收到 `environment="alfworld"` 且 alias 为 `robot_go_to`/`robot_manipulate` 时置 true。旧 `ToolSpec.requires_verification` 和 canonical `VerificationPolicy` 不改义。

- [ ] **Step 4：验证**

运行 Task 1 两个测试文件，并用：

```bash
rg -n "requires_verification|requires_model_observation|VerificationPolicy" src tests
```

人工逐项核对所有 adapter 映射；测试必须证明 Provider schema 无新增未知字段。

---

## Task 2：可持久化 barrier 与“首次图片消费”状态

**Files:**

- Modify: `src/homemaster/agent/state.py`
- Modify: `src/homemaster/agent/session.py`
- Modify: `src/homemaster/application/session.py`
- Test: `tests/homemaster/application/test_model_observation_resume.py`

- [ ] **Step 1：写 snapshot/resume RED 测试**

构造两类 snapshot：

1. 动作已触达 backend、尚未 `observe`：恢复后仍有 barrier。
2. `observe` 已返回有效 image、但尚未进入下一次 Provider 请求：只保留该 `tool_call_id` 的 image，旧图片仍被剥离。
3. 有效 observe 落 snapshot 后模拟 crash：重建 `ApplicationRuntime`，恢复后的第一个真实 Provider request 必须携带唯一图片；第一次 Provider request 失败后图片和未消费 id 仍保留；第二次 response 成功提交后未消费 id 才清除，下一 snapshot 才剥离该图。

关键断言：

```python
assert restored.agent_state.pending_model_observation.source_tool_call_id == "action-1"
assert restored.agent_state.unconsumed_observation_tool_call_id == "observe-1"
assert image_count(restored.session.messages, tool_call_id="observe-1") == 1
assert image_count(restored.session.messages, tool_call_id="old-observe") == 0
```

crash-boundary 还必须解析 transport request 并核对 content/pixel hash，不能只检查恢复对象内存。

- [ ] **Step 2：确认 RED**

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/application/test_model_observation_resume.py
```

- [ ] **Step 3：增加强类型状态**

```python
class ModelObservationBarrier(BaseModel):
    source_tool_name: str
    source_tool_call_id: str
    source_status: str
    observe_tool_name: str = "observe"
    protocol_failures: int = 0
    observe_failures: int = 0


pending_model_observation: ModelObservationBarrier | None = None
unconsumed_observation_tool_call_id: str | None = None
```

不要把 barrier 塞进无 schema 的 `metadata`。

扩展 `AgentSession.to_snapshot_dict()`：

```python
def to_snapshot_dict(
    self,
    *,
    agent_state: AgentState,
    task_state_store: TaskStateStore,
    model: str,
    system_prompt: str,
    strip_images: bool = True,
    preserve_image_tool_call_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
```

`_message_to_dict()` 只对指定未消费 `ToolResultMessage.tool_call_id` 保留 image，其他行为不变。`_snapshot_payload()` 从 `AgentState.unconsumed_observation_tool_call_id` 传入精确集合。

- [ ] **Step 4：验证恢复 envelope**

除 RED 测试外，运行：

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/application/test_model_observation_resume.py \
  tests/homemaster/gateway/test_runtime.py \
  tests/homemaster/application/test_session_file_backend.py \
  tests/homemaster/application/test_session_manager.py \
  tests/homemaster/test_agent_session.py
```

---

## Task 3：Agent Loop observation barrier

**Files:**

- Create: `src/homemaster/agent/model_observation.py`
- Create: `src/homemaster/prompts/model_observation_required.txt`
- Modify: `src/homemaster/agent/generic_runtime.py`
- Test: `tests/homemaster/application/test_model_observation_barrier.py`
- Test: `tests/homemaster/application/test_application_runtime.py`

- [ ] **Step 1：写动作 batch RED 测试**

参数化：

- `robot_go_to + observe`
- `robot_go_to + robot_manipulate`
- `robot_manipulate + task_progress_check`

每个实例必须断言所有 backend 调用次数都是 0、每个 tool call 都收到 `model_observation_batch_rejected`、无 barrier。

- [ ] **Step 2：写 barrier 建立 RED 测试**

参数化 `robot_go_to`/`robot_manipulate` × success/failure/outcome_unknown：

```python
assert next_request.tool_names == ["observe"]
assert MODEL_OBSERVATION_PROMPT in next_request.system_prompt
assert episode_specific_words_are_absent(next_request.system_prompt)
assert state.pending_model_observation.source_tool_call_id == action_call_id
```

再参数化 invalid/denied/cancelled 且 `backend_attempted=False`，断言不建立 barrier。

- [ ] **Step 3：写协议失败与有界重试 RED 测试**

模型在 barrier 下：

- 直接 final text；
- 无 tool call；
- 非单一 `observe`。

前两次继续只披露 `observe`，第三次返回 `model_observation_protocol_failed`；任何后续 action backend 次数为 0。

再覆盖普通 iteration budget 边界：

- observation-required action 恰好由最后一个普通 iteration 提出并真实执行；
- runtime 进入独立 observation grace budget，仍至少获得 Provider→`observe` 和图片后 Provider 消费各一次机会；
- grace 中协议失败最多 3 次、observe 失败最多 3 次，随后明确失败；
- grace 中图片消费后的模型若再要求新 action，因普通预算已耗尽而在 backend 前返回 `model_observation_budget_exhausted`，不得通过连续新 action 无限续命；
- pending barrier 或 unconsumed image 从 snapshot 恢复时重新获得同样有界 grace，不因上一 run 的 iteration 计数留下未观察动作。

- [ ] **Step 4：写有效/无效 observe RED 测试**

成功 PNG 必须经过 Agent Loop 独立解码：

```python
def validate_observation_result(result: ToolResultMessage) -> ObservationImageEvidence:
    # exactly one image block; base64 strict decode; Pillow PNG decode;
    # width/height > 0; compute content_sha256 and pixel_sha256
```

参数化空 bytes、损坏 base64、JPEG 冒充 PNG、0 image、2 images、tool error。无效时保留 barrier，达到三次返回 `model_observation_failed`。有效时清 barrier、设置 `unconsumed_observation_tool_call_id` 并记录两个 hash。

- [ ] **Step 5：实现外部化协议 prompt**

`model_observation_required.txt` 精确内容：

```text
A state-changing environment action was attempted.
Before taking another action or giving a final answer, call `observe`
and use the returned visual evidence to evaluate the action outcome.
```

用 `importlib.resources.files("homemaster").joinpath("prompts/model_observation_required.txt")` 加载；缺失时 fail closed，不使用代码内第二份 fallback。

- [ ] **Step 6：实现 loop 顺序**

固定顺序：

1. context assembler 完成；
2. 若 barrier 存在，工具 schema 收窄到唯一 `observe`，system prompt 追加固定提示；
3. freeze Provider request；
4. assistant 返回后，先验证 barrier 协议，再决定是否完成或 dispatch；
5. 无 barrier 时，在任何 dispatch 前拒绝含 observation-required action 的多调用 batch；
6. dispatch 后按 call/result 一一配对，以 `requires_model_observation && backend_attempted` 建 barrier；
7. 单一 `observe` 有效图片才清 barrier；
8. 下一次真实 Provider response 成功提交后才清 `unconsumed_observation_tool_call_id`。

不得由 Harness 合成 `observe` 调用或结果。

普通 `max_tool_iterations` 与 observation grace 的不变量：

```text
normal budget controls whether a new environment action may start;
once such an action reaches the backend, a bounded observation grace owns
protocol retries, observe retries, and exactly one post-image model consumption.
grace never authorizes another new environment action after normal budget exhaustion.
```

while-loop 的退出条件必须同时考虑 normal budget、pending barrier 和 unconsumed image；预算耗尽不得直接跳过既有 barrier。

- [ ] **Step 7：结构化事件**

增加：

- `model_observation.barrier_set`
- `model_observation.barrier_held`
- `model_observation.protocol_rejected`
- `model_observation.observe_failed`
- `model_observation.barrier_cleared`
- `model_observation.image_consumed`

payload 只含 tool/call id、status、backend_attempted、failure count、content/pixel hash、耗时；不复制 base64。

- [ ] **Step 8：验证**

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/application/test_model_observation_barrier.py \
  tests/homemaster/application/test_model_observation_resume.py \
  tests/homemaster/application/test_application_runtime.py \
  tests/homemaster/application/test_agent_runtime_tool_view.py
```

---

## Task 4：Registry 上游环境选择

**Files:**

- Modify: `src/homemaster/adapters/profiles.py`
- Modify: `src/homemaster/adapters/__init__.py`
- Modify: `src/homemaster/adapters/alfworld_entry.py`
- Modify: `src/homemaster/adapters/coworker_entry.py`
- Modify: `src/homemaster/cli/composition.py`
- Modify: `src/homemaster/cli/dry_run.py`
- Test: `tests/homemaster/tools/test_universal_registry.py`
- Test: `tests/homemaster/integration/test_observation_profiles.py`

- [ ] **Step 1：写三种组成 RED 测试**

```python
common = set(build_tool_registry(environment=None).all_names())
alfworld = set(build_tool_registry(environment="alfworld").all_names())
coworker = set(build_tool_registry(environment="coworker").all_names())

assert {"ask_user_question", "observe"} <= common
assert {"robot_go_to", "robot_manipulate", "robot_verify"}.isdisjoint(common)
assert {"robot_go_to", "robot_manipulate", "robot_verify"} <= alfworld
assert not COWORKER_NAMES & alfworld
assert not EMBODIED_NAMES & coworker
```

Provider request 边界测试读取 transport 收到的 `tools`，不能只看 Registry。

- [ ] **Step 2：重组而不保留多 mode 补丁**

把 `_home_tools()` 拆成：

```python
def _common_tools(
    *,
    world_path: Path | None,
    memory_path: Path | None,
    runtime_memory_root: Path | None,
    memory_enabled: bool,
) -> tuple[RegisteredTool, ...]:
    # core/file/memory/web/service + ask_user_question/observe/
    # task and skill tools that do not require an environment

def _embodied_tools(
    *,
    memory_mode: str,
    memory_path: Path | None,
    runtime_memory_root: Path | None,
) -> tuple[RegisteredTool, ...]:
    # only ALFWorld robot_go_to/manipulate/verify

def build_tool_registry(
    *,
    environment: Literal["local_robot", "alfworld", "coworker"] | None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
    memory_mode: str = "disabled",
    memory_enabled: bool = True,
):
    selected = list(
        _common_tools(
            world_path=world_path,
            memory_path=memory_path,
            runtime_memory_root=runtime_memory_root,
            memory_enabled=memory_enabled,
        )
    )
    if environment == "local_robot":
        selected.extend(
            _local_robot_tools(world_path=world_path, memory_path=memory_path)
        )
    elif environment == "alfworld":
        selected.extend(
            _embodied_tools(
                memory_mode=memory_mode,
                memory_path=memory_path,
                runtime_memory_root=runtime_memory_root,
            )
        )
    elif environment == "coworker":
        selected.extend(_coworker_tools())
```

逐 live caller 锁定语义：

- one-shot `run`、interactive shell、其 dry-run：`environment="local_robot"`，保留历史本地 robot tools；
- 普通 Gateway：`environment=None`，只披露 common；
- ALFWorld benchmark/Gateway：`environment="alfworld"`；
- Coworker：`environment="coworker"`；
- installed-package 默认 builder：明确要求调用者传 environment；兼容导出 `build_universal_tool_registry()` 暂时保持 `local_robot`，但任何 Gateway composition 禁止调用它。

这样不把 Gateway 的安全收窄扩散成 one-shot/shell breaking change，同时消除“home 同时表示 common 和 robot”的分类。为上述每个 live caller 增加实际 Provider request 边界测试，不只测 Registry。

- [ ] **Step 3：同步所有调用者**

ALFWorld benchmark 显式传 `environment="alfworld"`；Coworker 显式传 `"coworker"`；CLI/Gateway 默认 `None`。

- [ ] **Step 4：验证**

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/tools/test_universal_registry.py \
  tests/homemaster/integration/test_observation_profiles.py \
  tests/homemaster/integration/test_generic_browser_ant_runtime.py \
  tests/homemaster/benchmarking/test_alfworld_registry.py \
  tests/homemaster/memory/test_memory_tools.py \
  tests/homemaster/skills/test_installed_package.py
```

---

## Task 5：配置、双环境边界和根 CLI

**Files:**

- Modify: `src/homemaster/config/config.py`
- Modify: `config/homemaster.example.yaml`
- Modify: `src/homemaster/cli/app.py`
- Test: `tests/homemaster/test_config_resolution.py`
- Test: `tests/homemaster/test_model_config.py`
- Test: `tests/homemaster/test_homemaster_cli.py`
- Test: `tests/homemaster/test_cli_help.py`

- [ ] **Step 1：写配置 RED 测试**

新增严格 `extra="forbid"`：

```python
class AlfworldGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_root: Path | None = None
    data_root: Path | None = None
    config_path: Path | None = None
    python_executable: Path | None = None
    env_type: Literal["AlfredThorEnv"] = "AlfredThorEnv"
    split: Literal["train", "valid_seen", "valid_unseen"] = "valid_unseen"
    trial_manifest: Path | None = None
    trial_index: int = Field(default=0, ge=0)
    seed: int = 42
    display: str = ":102"
    manage_xvfb: bool = False
    xvfb_executable: Path = Path("/usr/bin/Xvfb")
```

模型放在顶层 `HomeMasterConfig.alfworld_gateway`。仅 CLI 传 `--alfworld` 时要求所有必填路径/sha 完整；普通启动允许模板为空。

- [ ] **Step 2：写 CLI RED 测试**

覆盖：

- `homemaster --gateway --alfworld`
- `homemaster --gateway --alfworld --config other.yaml`
- `--alfworld` 不配 `--gateway` 明确拒绝
- `homemaster gateway` 保持普通 Gateway；MVP 不承诺子命令 `gateway --alfworld`
- callback 传给 `run_gateway(config, environment="alfworld")`

- [ ] **Step 3：锁定双环境边界**

HomeMaster `.venv` 保留 Gateway/Provider/Feishu/httpx；配置中的
`python_executable` 指向已有 ALFWorld Python。不得把 ALFWorld/Torch 加入 HomeMaster
依赖，也不得在 ALFWorld 环境重复安装 HomeMaster。worker 通过显式 argv 收到
asset/data/config/manifest/display，不依赖 ambient cwd 或 `ALFWORLD_DATA`。

- [ ] **Step 4：配置模板**

`config/homemaster.example.yaml` 只放占位值：

```yaml
alfworld_gateway:
  python_executable: /replace/with/hm_alfworld/bin/python
  asset_root: /replace/with/alfworld-assets
  data_root: /replace/with/alfworld-data
  config_path: /replace/with/alfworld/configs/base_config.yaml
  env_type: AlfredThorEnv
  split: valid_unseen
  trial_manifest: /replace/with/one-entry-or-reviewed-manifest.json
  trial_index: 0
  seed: 42
  display: ":102"
  manage_xvfb: false
  xvfb_executable: /usr/bin/Xvfb
```

真实 `config/homemaster.yaml` 已 gitignore，后续只更新它，不纳入 diff。

---

## Task 6：application-owned ALFWorld HTTP environment

**Files:**

- Create: `src/homemaster/gateway/alfworld.py`
- Create: `src/homemaster/benchmarking/alfworld/http_client.py`
- Create: `src/homemaster/benchmarking/alfworld/http_worker.py`
- Modify: `src/homemaster/benchmarking/alfworld/env_adapter.py`
- Modify: `src/homemaster/cli/composition.py`
- Modify: `src/homemaster/cli/gateway_command.py`
- Test: `tests/homemaster/gateway/test_alfworld_gateway_binding.py`

- [ ] **Step 1：写 preflight/reset/close RED 测试**

fake checkout/env 覆盖：

- root/config/data/manifest 缺失 fail closed；
- unset 或错误 ambient `ALFWORLD_DATA` 时仍使用配置 data root；
- trial index 越界或 trial bytes/hash/goal 不匹配 fail closed；
- reset 非 ready 不启动 Gateway；
- adapter close 返回非成功 cleanup 时 Gateway close 报错；
- application close 顺序 adapter → managed Xvfb；
- 重复 close 幂等。

- [ ] **Step 2：显式构建对象**

```python
@dataclass(frozen=True)
class AlfworldGatewayBinding:
    adapter: AlfworldEnvAdapter
    translator: AlfworldCommandTranslator
    terminal_owner: object
    dependencies: Mapping[str, object]


class AlfworldSessionOwner:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._sealed = False

    async def claim(self, session_id: str) -> bool:
        async with self._lock:
            if self._sealed:
                return False
            if self._session_id is None:
                self._session_id = session_id
            return self._session_id == session_id

    async def seal(self) -> None:
        async with self._lock:
            self._sealed = True
```

factory 顺序：

1. resolve/validate asset/data/config/manifest paths；
2. 如 `manage_xvfb`，启动准确 display 并用 `xdpyinfo -display` 黑盒确认；
3. 用配置中的 Python 启动 worker，传入显式 asset/data/config/manifest/display；
4. worker 绑定 loopback ephemeral port、随机 bearer token，并在 ready envelope 中返回 reset state；
5. client 核对 ready schema、进程存活和固定 episode；
6. 构造 HTTP environment facade、translator/terminal owner/session owner；
7. 把 worker 和 Xvfb 以 application lifetime 逆序 cleanup ownership 绑定到 scope。

- [ ] **Step 3：data root 不依赖 ambient**

在 `load_alfworld_yaml()` 增加显式 `data_root` 参数，递归只替换准确 `$ALFWORLD_DATA`/`${ALFWORLD_DATA}` 前缀；不做通用字符串 rewrite。`build_alfworld_batch_env*()` 从 config 字段传入，benchmark 旧调用同步。

HomeMaster 进程不得 import `alfworld`、`ai2thor` 或 `torch`。worker 启动日志、HTTP ready
envelope 和真环境 reset/action/close 是该边界的外部证据。

- [ ] **Step 4：独占 session**

第一个到达 application wrapper、尚未触达任何 ALFWorld backend 的 `session_id` 通过 `AlfworldSessionOwner.claim()` 原子锁定。owner 一经 claim 即保持到 application close；首个 run 的普通失败不会把已变更 episode 交给另一 session。相同 session 的追问/resume 允许；close 先 seal，之后所有 claim 拒绝。其他 session 由 application wrapper 返回：

```python
RunResult(
    status=RunStatus.FAILED,
    error_code="alfworld_session_busy",
    final_reply="ALFWorld demo is already owned by another session.",
)
```

不能让第二个 session 排队后共享已变更 episode。

并发测试用 `asyncio.gather()` 同时提交两个不同 session，逐实例断言恰好一个 claim 成功；另一个在任何 reset/action/screenshot/backend 调用前收到 `alfworld_session_busy`。再覆盖同 session resume、首 run 失败后的 owner 保留、close seal。

---

## Task 7：Gateway RunRequest 注入与恢复

**Files:**

- Modify: `src/homemaster/channels/bridge.py`
- Modify: `src/homemaster/gateway/runtime.py`
- Modify: `src/homemaster/cli/gateway_command.py`
- Test: `tests/homemaster/gateway/test_alfworld_gateway_binding.py`
- Test: `tests/homemaster/gateway/test_runtime.py`

- [ ] **Step 1：写真实 bridge RED 测试**

不要直接 new `RunRequest` 代替边界。把认证 `InboundMessage` 送进 `GatewayRuntime.submit()`，在 fake application 捕获：

```python
assert request.profile == "alfworld"
assert request.borrowed_environment is binding.adapter
assert request.dependencies["alfworld_translator"] is binding.translator
assert request.dependencies["external_terminal_owner"] is binding.terminal_owner
assert request.dependencies["alfworld_config"] is binding.config
assert request.metadata["gateway_generation"] == 1
```

追问 `waiting_user` 后同 session resume 必须保留同一 adapter identity。

- [ ] **Step 2：增加窄 request binding 接口**

`ChannelBridge` 接收：

```python
RunRequestEnricher = Callable[[RunRequest], RunRequest]
```

先构造包含认证 principal、attachments、generation 的 canonical request，再调用 enricher；enricher 只能用 `dataclasses.replace()` 增加 profile/environment/dependencies，不能替换 principal、session、generation、delivery route。

普通 Gateway 不传 enricher，行为完全不变。

- [ ] **Step 3：stop condition**

Gateway ALFWorld 不用 benchmark“环境 won 后立即在工具结果阶段终止”绕过 observe。terminal owner 只阻止模型把未 won 宣称为完成；即使 action 使 `won=true`，仍先建立 barrier、调用 observe、模型消费图片后才能 final。

- [ ] **Step 4：验证**

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/gateway/test_alfworld_gateway_binding.py \
  tests/homemaster/gateway/test_runtime.py
```

---

## Task 8：同一图片的 Provider/Gateway MEDIA 跨边界证明

**Files:**

- Create: `tests/homemaster/integration/test_alfworld_gateway_media.py`

- [ ] **Step 1：写单一 canonical image 集成测试**

使用带已知像素的 PNG 和真实 `ApplicationRuntime` + `ArtifactPublisher` + Gateway public projection：

1. action result `backend_attempted=true`；
2. 下一 Provider call 只披露 `observe`；
3. 模型真实发 `observe`；
4. `ScreenshotTool` 返回唯一 `ResultImage`；
5. 紧接 Provider request 解析 image block；
6. Gateway MEDIA 出站解析 artifact；
7. artifact store 重新读取 bytes。

逐实例断言：

```python
assert sha256(provider_png) == sha256(artifact_png) == expected_content_hash
assert pixel_sha256(provider_png) == pixel_sha256(artifact_png) == expected_pixel_hash
assert provider_png == artifact_png
```

还要断言一次 observe 只产生一个 MEDIA，不新增第二次 screenshot。

- [ ] **Step 2：失败实例**

- observe 失败：0 Provider image、0 MEDIA、barrier 保留；
- artifact/飞书发送失败：模型仍能消费图片，但 Gateway 必须产生 typed media delivery failure，不能报告用户已看到；
- action failure + backend attempted：仍有 observe 和 MEDIA。

- [ ] **Step 3：验证**

```bash
.venv/bin/python -m pytest -q \
  tests/homemaster/integration/test_alfworld_gateway_media.py \
  tests/homemaster/artifacts/test_publisher.py \
  tests/homemaster/gateway/test_runtime.py \
  tests/homemaster/channels/test_feishu.py
```

---

## Task 9：真环境固定 demo episode 和外部动作门

**Files:**

- Modify only ignored: `config/homemaster.yaml`
- Evidence under ignored: `var/v2.2-alfworld-gateway/YYYYMMDD-HHMMSS/`

- [ ] **Step 1：双环境 preflight**

```bash
env -u ALFWORLD_DATA .venv/bin/python -c \
  'import homemaster, httpx, lark_oapi; print("gateway-imports-ok")'
env -u ALFWORLD_DATA /data0/yuqiao/envs/hm_alfworld/bin/python -c \
  'import alfworld, ai2thor; print("alfworld-imports-ok")'
```

断言两个环境各自只承担已有职责；worker 显式收到 asset/data/config/manifest，且没有从 ambient
data root 取值。核对 loopback bind、bearer 拒绝、ready/reset、HTTP status 和 close return code。

- [ ] **Step 2：枚举候选并锁定**

优先验证 Pencil → Shelf / FloorPlan308 的已有候选：

- fresh reset ready；
- 初始 PNG 非空且 pixel hash 可算；
- `robot_go_to(Pencil)` 独立返回码；
- `robot_manipulate(take Pencil)` 后 inventory 外部查询；
- `robot_go_to(Shelf 3)` 返回码/pose；
- `robot_manipulate(put Pencil, Shelf 3)` 后 parent/child + goal 外部查询；
- 每一步后 screenshot；
- close 后 Unity/Xvfb 进程逐个消失。

任何一步失败就保留 FAIL evidence，再按同一标准评估下一候选；不能挑“最好的一次”聚合通过。

- [ ] **Step 3：更新 ignored 配置**

只在完整链逐实例通过后把 root/data/config/expected sha/manifest/index/display 写入 `config/homemaster.yaml`，保持 mode 0600。提交区只能包含 `.example`。

- [ ] **Step 4：Provider tool-choice 真机核对**

用目标 Mimo 发两个最小请求：

1. tools 只含 `observe`，不传强制参数；
2. 若 transport/SDK 已有受支持 `tool_choice` 接口，再发强制 `observe`。

记录 HTTP/SDK return、真实 tool call。若强制参数不支持，保持 `UNVERIFIED`，产品继续使用 schema narrowing + validation；不得猜 API enum。

---

## Task 10：真实 Gateway + Provider + 飞书演示验收

**Files:**

- Evidence under ignored: `var/v2.2-alfworld-gateway/YYYYMMDD-HHMMSS/`

- [ ] **Step 1：启动真实命令**

```bash
env -u ALFWORLD_DATA .venv/bin/python -m homemaster.cli --gateway --alfworld
```

核对进程返回/启动日志中的最终工具名集合：common + `robot_go_to`/`robot_manipulate`/`robot_verify`，无 Coworker。

- [ ] **Step 2：普通 Gateway 工具披露黑盒**

独立启动 `homemaster --gateway`，捕获真实 Provider request，逐项断言 common tools 存在、具身/Coworker 工具不存在。关闭并核对 return code。

- [ ] **Step 3：模糊意图与 resume**

固定演示文案使用已锁定 episode 的真实物体，例如“把铅笔放到架子上”；模型必须先 `ask_user_question` 询问具体 Shelf，飞书回复“3号架”后恢复同一 session，环境动作在回复前调用次数为 0。

- [ ] **Step 4：逐 action/observe gate**

对每个 `robot_go_to`/`robot_manipulate` 实例保存：

- action call id/args；
- backend return code 和独立外部状态；
- 下一 Provider request tool list；
- `observe` call id；
- image content/pixel hash；
- artifact handle；
- Feishu upload/send receipt。

按 action call id 一一配对，禁止 any/best 聚合。

- [ ] **Step 5：飞书用户端黑盒**

飞书接收侧图片重读能力在真实 API 核对前为 `UNVERIFIED`。如果当前授权 API 能从接收侧重新读取消息图片，自动下载并比 hash；否则此一步明确标为人工门，由用户确认真实可见后记录确认时间/消息 id。上传 API success 不能替代用户端可见。

- [ ] **Step 6：关闭门**

发送 SIGTERM，逐个断言：

- HomeMaster parent exit；
- Feishu WebSocket worker exit；
- ALFWorld adapter cleanup confirmed；
- Unity process exit；
- 本次 owned Xvfb exit（外部预存 Xvfb 不得误杀）；
- 相关 socket 消失；
- command return code。

---

## Task 11：文档、全量验证和交付

**Files:**

- Modify: `README.md`
- Modify: `docs/alfworld-user-guide.md`
- Modify: `docs/architecture/alfworld-harness.md`
- Modify: `CHANGELOG.md`
- Modify: `progress.md`
- Modify: `docs/pitfalls.md`、`CLAUDE.md`（仅当出现非显而易见/严重坑）

- [ ] **Step 1：文档同源**

README 列出两个启动模式；用户指南给真实命令、ignored 配置和演示对话；架构文档写 barrier/snapshot/image/Gateway/ALFWorld 生命周期和不变量；CHANGELOG 写“改了什么、为什么、影响”。

- [ ] **Step 2：聚焦测试**

运行 Task 1-8 所有新增/相关测试，要求全绿。

- [ ] **Step 3：全量与静态门**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check <所有改动 Python 文件>
.venv/bin/python -m ruff format --check <所有改动 Python 文件>
.venv/bin/python -m compileall -q src/homemaster
uv lock --check
uv build
git diff --check
git status --short
```

检查测试后没有新增意外 untracked 文件。

- [ ] **Step 4：wheel 与外部 worker 门**

从源码 checkout 外建立空临时 venv，安装 HomeMaster wheel 的 gateway 能力并真实 import
`homemaster`、`httpx`、`lark_oapi`；wheel 不声明 ALFWorld/Torch。随后仍由 ignored 配置指向既有
ALFWorld Python，完成一次受管 worker reset/frame/close，证明安装产物可跨环境驱动真实 episode。

- [ ] **Step 5：最终代码 reviewer gate**

全部实现、测试、真实外部门和文档完成后，启动一个只读 reviewer subagent，一次性评审完整 diff；外部符号未真机核对的一律标 `UNVERIFIED`。主 agent 逐条处置，采纳项修复后只做针对性验证，不追加 reviewer。

- [ ] **Step 6：提交**

先确认 CHANGELOG 条目与 commit message 同源，再提交。不得提交 ignored 真实配置、credential、外部 evidence 或用户无关改动；保留设计文档的 owner 修改。

---

## 验收映射

| 设计验收 | 实施任务 |
| --- | --- |
| 普通/ALFWorld Provider 工具名单 | Task 4、7、10 |
| 模糊输入先追问、同 session resume | Task 7、10 |
| 每个真实 action 后独立 observe | Task 1、3、10 |
| barrier 下只披露 observe + 固定提示 | Task 3 |
| action success/failure 都观察 | Task 3、8、10 |
| backend 未触达不建 barrier | Task 3 |
| 多调用 batch 全拒绝且零 backend | Task 3 |
| observe 失败保留 barrier | Task 3 |
| 首次 Provider 消费前图片不被 snapshot 剥离 | Task 2、3 |
| Provider/Gateway 同帧 hash | Task 8、10 |
| ALFWorld return + 外部终态 | Task 9、10 |
| adapter/Unity/Xvfb/Feishu 清理 | Task 6、9、10 |
| HomeMaster/ALFWorld 双环境隔离 | Task 5、6、9、11 |

## 自审记录

- 设计 §1-15 均映射到 Task 1-11。
- 未新增阶段事件协议、隐藏 screenshot、通用 verification policy、多 session episode 或动态环境切换。
- `VerificationPolicy` 与 model observation 两个事实保持独立。
- Provider forced tool choice 仍按真机证据决定，当前不背书。
- snapshot 恢复场景补上了设计中最容易被“内存运行正常”掩盖的图片首次消费缺口。
- 每个外部门都同时要求 return code 和外部终态；多 action/image/session 按实例断言。
