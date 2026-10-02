# 裁决：legacy 删除清单 — owner→consumer 证据表

状态：草案（evidence-map subagent 审计 + 主 agent 逐条 grep 核实，
0907ede 后基线）。每条删除必须满足"最后消费者已迁移"门，未满足的
行不得删。

## 删除候选清单（verified 消费面）

### A. `agent/messages.py` — canonical 消息契约

- 消费方：`agent/context.py`、`generic_runtime`、`substrate/messages.py`、
  `application/session.py`、快照、全部测试 — 全域 canonical 类型。
- **裁决：永久保留。** AS 只作内部引擎，canonical envelope 是持久契约。

### B. `agent/generic_runtime.py` — legacy 循环引擎 + 共享契约

- 导出被 substrate 复用的契约：`GenericRunResult` / `StopCondition` /
  `_cancelled`（`substrate/runtime.py:25-28`）、`RunBookkeepingState`。
- 生产消费方：`application/runtime.py:18`（legacy 分支，引擎开关）、
  `agent/__init__.py:29`（re-export）。
- 测试消费方：`test_generic_agent_runtime.py`、`test_substrate_runtime_e2e.py`、
  `application/test_model_observation_barrier.py`、`events/test_streaming_sanitizer.py`、
  `memory/test_feedback_context.py`、`application/test_agent_runtime_tool_view.py`。
- **裁决：删**。前置：①`GenericRunResult`/`StopCondition`/`_cancelled` 抽离到
  独立契约模块（substrate 与新分支共用）；②legacy 引擎分支下线；③上面
  6 个测试文件随引擎语义迁到 AS 路径或标记删除。

### C. `agent/context_projection.py` — 投影 + legacy 协议围栏

- `project_model_tool_context`：`agent/context.py:21`（ContextAssembler
  内嵌投影）。
- `project_model_tool_schemas` / `unavailable_tool_protocol_results` /
  `terminal_command_protocol_results`：`generic_runtime.py:18-21` 唯一消费。
- **裁决：拆分**。两个 `project_*` 函数迁到 `agent/context.py` 邻域
  （或保留文件但只留投影部分）；两个 `*_protocol_results` 随
  generic_runtime 一起删——AS 等价物已落地为 `ProtocolFenceMiddleware`
  （0907ede），分歧记录在 `decision-phase2-exit.md`。

### D. `providers/llm_client.py` — legacy 引擎 client + 共享 helpers

- 生产消费方：`application/factory.py:33`（provider 构造，默认引擎）、
  `cli/doctor.py:27`、`cli/errors.py:11`、`experience/success_path.py:14`
  （仅 `LLMClient` 注解）、`providers/__init__.py:4`（re-export）。
- substrate 复用：`substrate/as_llm_client.py:38-45` 导入
  `LLMJsonResponse` / `_attempt_record` / `_default_attempt_id` / `_emit` /
  `_map_sdk_error` / `_request_sha256`。
- 测试：`test_llm_client.py`、`test_token_estimator.py`、
  `providers/test_async_provider.py`、`providers/test_frozen_request_retry.py`、
  `test_e2e_real_api.py`、`test_substrate_dual_run.py`。
- **裁决：删**。前置：①as_llm_client 依赖的 6 个符号抽离到
  `providers/_shared.py`（`LLMJsonResponse` 是信封类型，helper 是私有名
  ——抽离后去下划线前缀转正）；②factory 默认引擎切 AS；③doctor/errors/
  success_path 迁注解；④legacy provider 测试逐个裁决 port/retire。

### E. `providers/transports/` — HTTP 传输层

- 生产消费方：`llm_client.py:40`（唯一生产消费者）、
  `substrate/as_llm_client.py:47-50`（`TransportDelta`/`aggregate_deltas`）、
  `generic_runtime.py:56`（`aggregate_deltas`）。
- `transports/base.py`、`anthropic.py`、`openai_chat.py` 自引用。
- 测试 ×12（`test_provider_transports.py`、`test_anthropic_transport.py` 等）。
- **裁决：删实现、保类型**。前置：`TransportDelta`/`aggregate_deltas`
  （纯数据类型+聚合器，无网络 IO）迁到 `providers/types.py` 或
  `substrate/` 邻域；`anthropic.py`/`openai_chat.py` 传输实现随
  llm_client 一起删；`test_substrate_dual_run.py` 等的 import 跟随迁移。

### F. `agent/turn.py` — CLI 回合适配器

- 消费方：`cli/interactive_shell.py:12`、`cli/run_command.py:13`、
  `test_agent_turn_cli_adapter.py`。
- **裁决：删**。**已执行**：CLI 两个入口本来就直走 `application.run`
  （核实：run_command.py:106、interactive_shell.py:180），`run_agent_turn`/
  `run_single_turn`/`compact_agent_context` 三个包装器零生产消费（仅自测
  文件引用——孤儿 compat shim）。`new_session_id`（唯一活件，CLI ×2）迁入
  `agent/session.py`；turn.py 与 `test_agent_turn_cli_adapter.py` 整体删除；
  `test_adapter_ownership.py` 的 `ENTRY_PATHS` 移除 turn.py 条目。
  回归 69/69 绿。

### G. `experience/success_path.py` — 仅注解引用

- `LLMClient` 注解（line 14）。**裁决：改注解，不删文件。**

### H. `mcp/client.py` — Connector/McpClientManager

- 生产消费方：`application/composition/base.py:48`（`Connector`、
  `McpClientManager` — 真生产）、`mcp/adapter.py:12`、`cli/dry_run.py:12`、
  `test_substrate_mcp.py:22`、mcp 测试套件。
- **裁决：永久保留**（`decision-mcp.md` 8 项不满足已定论；AS MCP
  client 语义不符）。

### I. `events/bus.py` / 事件投影壳 — 保留

### J. `substrate/`（toolkit/messages/snapshot/as_llm_client/middleware_runtime/
runtime） — 新基座，保留。

### K. `application.contracts.TerminalPolicy` / `RunRequest.terminal_policy` — **已删**

- 消费方审计：src+tests 全仓零引用——Protocol 无实现、字段无写入方、
  validator 是唯一"消费"。`tests/case02_openenv/test_terminal_policy` 仅
  残留 `.pyc`（源码已删）。真实 terminal 拦截面在
  `PermissionPolicy.allowed_terminal_commands`（policy.py，两引擎共用的
  fail-closed 层）+ AS 侧 `ProtocolFenceMiddleware`。
- 裁决：**删除**（死 seam，公共契约面缩小；若未来需要 pre-exec hook，
  走 middleware/permission 通道而不是 request 字段）。

### L. `RunContext.deps` / `ToolExecutionContext.services` — 键集冻结（不删）

- 全键审计（src/homemaster）：29 个键全部有活的 writer↔reader 配对
  （alfworld_* benchmark DI、task_state_store、provider_attempt_context_
  binder、memory_feedback_context*、mindmemos、run_context 自引用等）。
  无 write-only/legacy-only 残留。
- 裁决：dict 通道保留（计划内认可的 DI 通道），但**键集冻结**——
  `test_substrate_imports.py::
  test_deps_services_dict_uses_only_audited_keys` 以 AST 扫描钉死
  白名单，新增键必须带 owner→consumer 证据才能进表。
- `terminal_command_protocol_results` 字符串路由：唯一消费方是
  `generic_runtime.py:735` 的 legacy 批量围栏——随 generic_runtime
  一起消亡（AS 等价物 = ProtocolFenceMiddleware），不独立迁移。

## 删除顺序（依赖拓扑序，每步独立可回滚）

1. ~~协议围栏移植进 AS middleware~~ — **已完成**（0907ede）。
2. ~~抽离共享契约~~ — **已完成**：`GenericRunResult`/`StopCondition`/
   `_cancelled` → `agent/runtime_contracts.py`（复用既有契约模块，
   不另开 `run_contracts.py`；全部注解走 `TYPE_CHECKING`，模块保持
   运行时零依赖）。`generic_runtime` 顶部 import 回引，外部消费方
   （`substrate/runtime.py`、`application/runtime.py`、tests）零改动。
3. ~~抽离 provider 共享 helpers~~ — **已完成**：`providers/_shared.py`
   新立（转正命名：`attempt_record`/`default_attempt_id`/`emit_event`/
   `map_sdk_error`/`request_sha256`/`LLMJsonResponse` 含
   `json_payload`/`public_summary`）。`llm_client` 以旧下划线名回引
   兼容；`as_llm_client` 已改指 `_shared` 公共名（局部变量
   `request_sha256` 改名 `request_hash` 避让函数名）。
4. ~~迁移 `transports/types.py`~~ — **已完成**：`TransportDelta`/
   `aggregate_deltas` → `providers/types.py`；transports 内部、
   llm_client、generic_runtime、as_llm_client、9 个测试文件全量改指。
5. ~~`success_path.py` 去 `LLMClient` 注解~~ — **已完成**（TYPE_CHECKING）。
6. ~~`doctor.py` 迁 `AsLLMClient`~~ — **已完成**；`cli/errors.py` 的
   `LLMClientError` 同步改指 `providers.errors`（真定义家）。
7. `factory.py` 默认引擎切 AS（env escape hatch 保留一个版本周期）。
   **门**：远端全量回归绿 + Feishu/Web smoke + ALFWorld 单集 e2e。
8. legacy provider 测试逐个 port/retire；`llm_client.py` + `transports/` 实现删除。
9. `ApplicationRuntime` legacy 分支删除；`generic_runtime.py` 删除；
   `context_projection.py` 拆投影/删围栏函数。
10. `turn.py` 删除 + CLI 直走 ApplicationRuntime。**done**
    （`new_session_id` → `agent/session.py`；CLI 本就直走 `application.run`）。
11. 反向 import 审计测试钉边界。**done** — `test_import_boundaries.py`
    AST 守卫覆盖：vendored `src/agentscope/` 零 `homemaster` import；
    `agent/`/`providers/`/`memory/`/`skills/`/`tools/` 不得 import
    `agentscope`（仅 `substrate/`、`application/`、`config` 允许）；
    `substrate/` 不得 import `application.*`/`memory.*`（分层下钻禁回勾）。

## 门

- 每步后跑定点回归 + ruff + `guard_no_legacy_terms`。
- 引擎默认切换（步骤 7）前：远端全量回归绿（扣除已知环境归因）+
  Feishu/Web smoke + ALFWorld 单集 e2e。
- `generic_runtime.py` 删除前：grep 全仓零引用（除迁移中的 re-export）。

## 远端回归门（786881b，hkust4 Homemaster-v35）

全量回归 `32 failed / 1635 passed`（20min）：全部 32 败与上一份基线
（`33 failed / 1647 passed`）逐项相同——cli_streaming/tmux、v19 ports、
web confirmations/permissions、openharness bash 进程组、mindmemos/neo4j
依赖段均为环境归因；基线上的 `test_system_prompt_delivered_to_model`
本次转绿（F1 session 镜像修复的直接外部证据）。零新增失败 →
删除清单步骤 7-9 的远端回归门满足（smoke 门另计）。
