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
- **裁决：删**。前置：CLI 入口直接走 `ApplicationRuntime`（AS 引擎
  是默认后 turn.py 只是一层壳）。

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

## 删除顺序（依赖拓扑序，每步独立可回滚）

1. ~~协议围栏移植进 AS middleware~~ — **已完成**（0907ede）。
2. 抽离共享契约：`GenericRunResult`/`StopCondition`/`_cancelled` 出
   `generic_runtime.py` → `agent/run_contracts.py`（新文件，generic_runtime
   re-export 保持向后兼容）。
3. 抽离 provider 共享 helpers → `providers/_shared.py`（转正命名：
   `attempt_record`/`default_attempt_id`/`emit_event`/`map_sdk_error`/
   `request_sha256`/`LLMJsonResponse`），`as_llm_client` 改指。
4. 迁移 `transports/types.py`（`TransportDelta`/`aggregate_deltas`）→
   `providers/types.py`；全部 import 跟随。
5. `success_path.py` 去 `LLMClient` 注解 → duck-type/Protocol。
6. `doctor.py` 迁 `AsLLMClient`（doctor 验收=构造+probe，无引擎语义）。
7. `factory.py` 默认引擎切 AS（env escape hatch 保留一个版本周期）。
8. legacy provider 测试逐个 port/retire；`llm_client.py` + `transports/` 实现删除。
9. `ApplicationRuntime` legacy 分支删除；`generic_runtime.py` 删除；
   `context_projection.py` 拆投影/删围栏函数。
10. `turn.py` 删除 + CLI 直走 ApplicationRuntime。
11. 反向 import 审计测试钉边界（已有 `test_as_boundaries.py` AST 守卫，
    补 `agent/`、`providers/` 不得 import `agentscope` 的反向断言）。

## 门

- 每步后跑定点回归 + ruff + `guard_no_legacy_terms`。
- 引擎默认切换（步骤 7）前：远端全量回归绿（扣除已知环境归因）+
  Feishu/Web smoke + ALFWorld 单集 e2e。
- `generic_runtime.py` 删除前：grep 全仓零引用（除迁移中的 re-export）。
