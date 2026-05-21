# HomeMaster V1.4 Agent Loop Full Migration Spec

日期：2026-05-19

## 背景

HomeMaster 已经把默认入口切到了 AgentRuntime，但当前实现仍然保留了旧编号流水线的生产代码、测试命名、提示词、调试产物和历史报告。用户在 CLI 中输入普通问候时，系统会猜测固定 scenario 并运行任务；运行中也缺少实时事件反馈。更关键的是，当前 runtime 仍是自定义 JSON decision 协议，不是通用的 message/tool-call/tool-result agent loop。

V1.4 的目标是一次彻底迁移：当前仓库不再保留旧编号流水线作为架构、命名、fixtures、文档或产物。新的主链参考 hermes-agent 的成熟模式，但保持 HomeMaster 的体量更小、更清晰。

## 总目标

V1.4 完成后，HomeMaster 的核心形态应是：

```text
CLI shell/run
  -> AgentSession
  -> AgentRuntime
       -> ContextComposer (system + window + tool schemas)
       -> LLMTransport.stream()
            -> AssistantDelta stream  ----> RuntimeEvent sink (CLI live progress)
            -> Aggregator
       -> AssistantMessage (text + 0..N parallel tool_calls)
       -> ToolDispatcher (parallel)  ----> RuntimeEvent sink (CLI live progress)
       -> list[ToolResultMessage]    one-to-one with tool_call_id
       -> AgentSession.append(...)
       -> loop until termination condition
  -> final AssistantMessage to user
```

主链上的 streaming events 和 RuntimeEvent sink 是同一个东西的两面：transport 在产生 normalized message 的过程中同步往 event sink 发 `model.delta` / `tool.call_*`，CLI 实时显示的就是这条 sink。普通 `complete()` 只是 `stream()` 的便捷封装，不走 fast path 绕过 events。

家庭机器人能力不再是固定流程，而是挂在通用 loop 上的一组 domain tools：

```text
task_interpreter
memory_retriever
target_grounder
skill_view
robot_navigate
robot_observe
robot_manipulate
robot_verify
memory_writer
task_summarizer
```

这些工具名只是可注册能力清单，不是执行顺序。任何 runtime、prompt、tool description 或 ContextComposer 都不能暗示模型必须按上面顺序调用工具；模型必须可以选择 0 个、1 个或多个工具，也必须可以在信息不足时直接追问用户。

## 非目标

- 不在 V1.4 接入真实机器人硬件。
- 不在 V1.4 做复杂多用户账号系统。
- 不照搬 hermes-agent 的大而全实现，只吸收主链原则。
- 不保留仓库内旧编号流水线归档目录；需要追溯历史时使用 git 历史。
- 不在 V1.4 实现 sub-agent / agent-as-tool / 多 agent 编排。runtime 主循环只承担单 agent 的消息与工具调用循环；任何"agent 套 agent"都视为非目标，避免 runtime 复杂化。
- 不在 V1.4 实现真正多模态。message 的 `content` 形态从一开始就是 `list[ContentBlock]`，但本期 ContentBlock 只实现 `text`，其他模态留接口不实现。

## 架构原则

1. AgentRuntime 只认识消息、工具调用、工具结果、事件、会话和预算，不认识家庭地图、取物任务或记忆检索细节。
2. 机器人领域能力全部通过 ToolSpec 暴露，工具可以读写领域状态，但不能把 runtime 变回固定流程。
3. Provider 适配层负责把不同模型 API 统一成 NormalizedAssistantMessage。runtime 不解析某个供应商私有响应结构。
4. 模型优先使用原生 tool calling。如果供应商 endpoint 只能返回文本 JSON，适配逻辑也必须封装在 LLMTransport 内，runtime 仍只看 normalized tool calls。
5. CLI 是会话界面，不是 scenario runner。普通问候应得到 assistant reply；可执行任务才进入工具循环。
6. 当前仓库不保留旧编号流水线命名、产物和文档残留。清理不是注释说明，而是删除、改名、迁移测试和加守门检查。
7. `src/homemaster/skills/` 保留为未来可插拔能力注册层，但不能继续表达 `mock_skills` 这类生产 runtime 模式。测试替身只能放在 `tests/homemaster/fixtures/` 或 test doubles。
8. Prompt 必须重新撰写为 agent/tool 语义，不能从旧编号 prompt 复制或改头换面。新 prompt 不得包含固定流程、隐含 stage 顺序或“完成 X 后再做 Y”的工具编排要求。

## 核心跨层数据契约

Batch 1 开始前必须先钉死以下 normalized schema 和不变量。后续 runtime、transport、tool dispatcher、CLI events 和 tests 都以这组契约为准；不要让实现者在各文件里各自补字段。

### Session / Run / Turn

- `session`：跨用户消息的长期会话，持久保存 transcript、session metadata 和 summary。
- `run`：一次用户输入触发的处理边界。用户输入入队为 run start，最终 assistant reply、失败或中断出队为 run end。一个 run 可以包含多次模型调用和多次工具调用。
- `turn` 或 `step`：一次 model call 及其后续 0..N 个 tool execution。turn 是 run 内部迭代单位，不等于用户对话轮。

### ContentBlock

本期只实现 text block，但 message 字段从一开始就使用 block list，避免后续多模态大改：

```text
ContentBlock:
  type: "text"
  text: str
```

外部便捷 API 可以接收 `str`，但进入 session 和 transport 前必须归一化为 `list[ContentBlock]`。

### AssistantMessage

```text
AssistantMessage:
  role: "assistant"
  content: list[ContentBlock]
  reasoning_content: str | None
  tool_calls: list[ToolCall]
  finish_reason: "stop" | "tool_calls" | "length" | "content_filter" | "error" | None
  usage: dict[str, int] | None
  provider_metadata: dict[str, Any]
```

不变量：
- `reasoning_content` 不得混入用户可见 `content`，MiMo/Claude thinking replay 只进 reasoning 字段或 trace metadata。
- `tool_calls` 可以为空、单个或多个。runtime 不得假设一轮只有一个工具调用。
- `finish_reason` 只用于终止和错误判断，不用于表达 home-domain 状态。

### ToolCall

```text
ToolCall:
  id: str
  name: str
  arguments: dict[str, Any]
```

不变量：
- 每个 provider tool call 必须归一化为稳定的 `id`。如果 provider 没有 id，transport 负责生成 `call_<index>`。
- 同一 AssistantMessage 内 `tool_call.id` 必须唯一。
- ToolDispatcher 接收整个 `list[ToolCall]`，可以顺序执行或并发执行，但返回结果必须与原始 `tool_call_id` 一一对应。

### ToolResultMessage

```text
ToolResultMessage:
  role: "tool"
  tool_call_id: str
  name: str
  content: list[ContentBlock]
  is_error: bool
  data: dict[str, Any] | None
  provider_metadata: dict[str, Any]
```

不变量：
- 工具 Python 返回值可以是 dict/object，但进入下一轮模型上下文前必须序列化为 JSON text block。
- 工具失败也走 ToolResultMessage，不抛出到 runtime 主循环外，除非是不可恢复的框架错误。
- `tool_call_id` 必须回填原始 ToolCall.id；缺失或不匹配是 runtime contract violation。

### RunContext

```text
RunContext:
  session_id: str
  run_id: str
  turn_index: int
  settings: RuntimeSettings
  event_sink: RuntimeEventSink
  deps: dict[str, Any]
  cancellation_token: Any | None
```

不变量：
- AgentRuntime 只传递 RunContext，不解读 `deps` 内容。
- Home domain state、memory store、world overlay、skill registry、fake test doubles 都通过 `deps` 或显式 tool registry 注入。
- 禁止把 `current_location`、`holding_object`、`memory_hits` 这类 home 字段重新加回 runtime-visible AgentState。

### RuntimeEvent

```text
RuntimeEvent:
  type: str
  session_id: str
  run_id: str
  turn_index: int | None
  tool_call_id: str | None
  name: str | None
  payload: dict[str, Any]
  timestamp: str
```

Event type 使用分层 namespace：
- `runtime.*`：run/turn lifecycle、budget、retry、interrupt、final reply。
- `transport.*`：request started、delta、response completed、provider error。
- `tool.*`：tool call started/completed/failed。

CLI 实时显示只订阅 RuntimeEvent sink。transport streaming 的 delta 也必须经由同一 sink 发出，不能另开一套输出通道。

### 终止条件

AgentRuntime 只能因为以下条件结束一个 run：
- assistant message 没有 `tool_calls`，且 `finish_reason` 是 `stop`、`end_turn` 或等价 provider stop。
- `max_tool_iterations` / token budget / runtime budget exhausted。
- 用户中断或 cancellation token 触发。
- 不可恢复的 transport/runtime contract error。

`finish_reason == "length"`、tool result id 不匹配、provider 返回无法归一化等情况必须产生 `runtime.failed` 或可见错误回复，不能被当作成功 completed。

## 新核心组件

### AgentSession

职责：
- 保存 `messages`、`session_id`、当前 `run_id`、turn index、会话级配置和运行摘要。
- 支持 shell 中连续多轮对话。
- 区分持久 transcript 和 API-call-time ephemeral context。

不做：
- 不执行工具。
- 不决定下一步动作。
- 不保存领域细节为专用字段，领域数据通过 tool result messages 和 domain state snapshot 暴露。

### AgentRuntime

职责：
- 执行通用 loop：build context -> model call -> append assistant message -> execute tools -> append tool messages -> continue or return final reply。
- 管理最大迭代数、中断、空回复恢复、工具调用校验和最终状态。
- 产生 RuntimeEvent，并保证 CLI 可实时订阅。
- 把同一 AssistantMessage 内的 0..N 个 ToolCall 作为一个批次交给 ToolDispatcher，并校验返回的 ToolResultMessage 与 tool_call_id 一一对应。

不做：
- 不猜 scenario。
- 不直接调用家庭机器人模块。
- 不解析旧决策 JSON。

### LLMTransport

职责：
- 把内部消息和工具 schema 转换为 provider 请求。
- 提供 `stream()` 作为主接口，产生 AssistantDelta/RuntimeEvent，并由 aggregator 得到 AssistantMessage。
- 把 provider 响应归一化为：
  - visible assistant content
  - reasoning metadata
  - tool calls
  - finish reason
  - usage
- 处理 MiMo thinking/reasoning replay 需要的 provider-specific 字段。

验收重点：
- AgentRuntime 不依赖 MiMo 私有响应结构。
- MiMo、测试 fake provider 至少走同一个 normalized response contract。

### ToolRegistry 和 ToolDispatcher

职责：
- ToolRegistry 只注册工具 schema、描述、执行器和 safety metadata。
- ToolDispatcher 校验工具名和参数，接收 `list[ToolCall]` 和 `RunContext`，执行工具，返回 `list[ToolResultMessage]`。
- 工具结果必须能成为下一轮模型上下文，而不是只更新 Python 内存对象。
- ToolDispatcher 对 runtime 不透明地传递 RunContext；领域工具自己读取 `run_context.deps` 中的 domain state、memory store 或 skill registry。

### Skills Registry

职责：
- 保留 `src/homemaster/skills/` 作为可插拔 skill package 注册体系，用于未来把一组工具、prompt、schema 和说明文档组织成可发现能力包。
- `skills` 可以向 `ToolRegistry` 贡献 ToolSpec，也可以被 `skill_view` 查询和展示。
- `skills` 的 loader、registry、spec 必须使用通用 agent/tool 语义。
- `SkillSpec`/`SkillRegistry` 必须收敛到一个稳定 API，例如 `tool_names`、`register()`、`get()`、`all()`、`all_names()`；不能在计划和测试里混用旧 `allowed_tools`/`list_specs()` 命名。
- `SkillSpec` 的稳定产物是 `tools: list[ToolSpec]`、`system_prompt_fragment: str | None`、`metadata: SkillMeta`。`skill_view` 读取 metadata 和说明文本，不能绕过 ToolRegistry 直接执行旧能力。
- progressive disclosure 策略必须明确：默认模型只看到基础 agent prompt、核心工具 schema 和 `skill_view`；大型 skill 的长说明、样例和扩展工具说明通过 `skill_view` 按需展开，避免 ContextComposer 一次性拼入所有 skill 文档。

不做：
- 不作为 runtime mode，不提供 `mock_skills`、deterministic 或 scenario 选择。
- 不提供 `skill_mode` 这类生产 runtime mode；测试用 fake/simulated 能力必须通过 test doubles、fixture 或显式工具注册注入。
- 不 import `AgentRuntime`、CLI 或旧 pipeline/stages/task_runner。
- 不让 builtin skills 直接调用旧 Stage 函数。

### ContextComposer

职责：
- 组合 system prompt、消息历史、工具 schema 和本轮 ephemeral context。
- 保持 provider 无关，输出给 LLMTransport 消费的 normalized request context。
- 实施固定 baseline 截断策略：保留 system prompt、当前 user input、最近 N 条消息、仍未闭合的 assistant tool call 与对应 tool result；超过预算时优先使用 session summary，再失败为可见错误，而不是静默丢失 tool/result 对。
- token 预算 key 使用 agent/tool 语义，例如 `agent_response`、`tool_task_interpreter`、`tool_memory_query`，不得继续使用旧编号流程 key。

禁止：
- 不拼接旧 stage 名、scenario 名或历史 pipeline 状态。
- 不在 prompt 或上下文中写固定工具调用顺序。
- 不把领域状态机塞回 runtime-visible message 结构。
- 不拆散 assistant tool call 与对应 ToolResultMessage；截断必须保持 provider 要求的 tool_call_id 配对关系。

### Domain Tools

现有家庭机器人能力迁移为工具：

- `task_interpreter`：从用户自然语言提取可执行任务结构；对闲聊可以不调用。
- `memory_retriever`：检索 object/fact memory。
- `target_grounder`：基于检索结果和 world snapshot 选择候选目标或说明无法确定。
- `skill_view`：通过 skills registry 按需展开技能说明，保持 progressive disclosure。
- `robot_navigate`：模拟移动或查找。
- `robot_observe`：返回结构化观察。
- `robot_manipulate`：模拟拿取、放置、交付等动作。
- `robot_verify`：验证子目标或最终目标。
- `memory_writer`：写回任务记录和事实记忆。
- `task_summarizer`：生成面向用户和长期记忆的任务总结。

## 迁移批次

详细执行计划见 `plan/V1.4/agent-loop-full-migration-execution-plan.md`。本 spec 只保留压缩后的 5 个批次，避免再次被拆成 8 个以上的小阶段。

执行顺序有一个硬约束：**先让 CLI 和 generic runtime 主入口脱离旧 `task_runner/pipeline/stages`，再删除旧包**。否则低级 agent 按计划执行时会在中间批次把项目打断。

### 批次 0：基线、守门脚本、静态清理

目的：
- 记录当前 dirty worktree、旧命名命中面和 tracked runtime artifact。
- 先删除不参与新运行链路的历史记录、报告、日志、旧计划、tracked `var/` 产物、旧 review 报告、旧截图/场景脚本。
- 新增 guard，先 report-only，最后强制失败。
- guard 必须跳过且只跳过自己的源文件，不能通过忽略整个 `scripts/` 来绕开检查。

核心文件处理：
- 新增 `plan/V1.4/baseline/` 下的 baseline 报告。
- 新增 `scripts/guard_no_legacy_terms.py`。
- 新增 `tests/homemaster/test_cleanup_guard.py`。
- 更新 `.gitignore`。
- 删除 `docs/shim_lifecycle.md`、`record/`、`report/`、`log/`、`review/V1.2/`、`plan/V1.2/`、`plan/V1.3/`、`plan/v1.0/`、`plan/v1.1/`、tracked `var/homemaster/` 产物和旧 scenario/screenshot 脚本。

验收标准：
- baseline 文件齐全。
- guard 可以 report-only 运行。
- tracked runtime/debug artifact 不再保留。

### 批次 1：通用消息、会话、Transport、Runtime 主链

目的：
- 把 runtime 从自定义 decision JSON 改为 message/tool-call/tool-result loop。
- provider-specific 响应解析只存在于 transport。

核心文件处理：
- 新增 `src/homemaster/agent/messages.py`、`session.py`、`normalized.py`、`context.py`。
- 新增 `src/homemaster/agent/generic_runtime.py` 承载新的 message/tool-call/tool-result loop。
- 新增 `src/homemaster/providers/transport.py`、`mimo_transport.py`。
- `src/homemaster/agent/runtime.py` 在 Batch 1 保持旧 `AgentRuntime` 构造兼容，避免旧 `task_runner.py` 和 CLI 在 Batch 2 cutover 前断裂。
- 兼容扩展 `src/homemaster/llm_client.py`。
- 修改 `src/homemaster/events/runtime_events.py`、`sinks.py`、`sanitizer.py`，新 runtime 只产生 generic events，但旧事件解析保留到 Batch 3 删除旧 runtime 后清理。
- Batch 1 期间 `llm_client.py`、`runtime_events.py`、`sinks.py` 必须 backward-compatible：保留旧入口仍需的 `RawJsonLLMClient` 和旧事件 sink 接收能力，新 runtime 只产生 generic events。
- 不在本批次删除 `src/homemaster/providers/mimo_decision_client.py`，因为旧 `task_runner.py` 在 CLI 切走前仍会 import 它。

验收标准：
- Runtime 单测只依赖 fake transport 和 fake tools，不 import home domain。
- MiMo transport 测试覆盖 content-only、tool-use、reasoning/empty/truncated response。
- 工具失败会作为 tool result message 进入下一轮模型上下文。
- CLI 旧入口仍能 import；新 runtime 文件不新增 `LiveMimoDecisionClient` 依赖。
- 旧 `AgentRuntime` 构造方式仍可用；新 generic runtime 通过新符号测试。
- `你好` 的 generic runtime 测试必须覆盖 0 tool call。

### 批次 2：CLI Cutover、Generic State、Tool Contract Boundary

目的：
- 先把用户可见入口切到通用 agent turn，确保 CLI 不再 import `task_runner/pipeline/stages`。
- 把 `AgentState` 从家庭任务状态改成通用 runtime 状态。
- 把 `ToolSpec`、`ToolDispatcher` 从 `AgentState` 领域字段中解耦。

核心文件处理：
- 重写 `src/homemaster/agent/state.py`，只保留 run/session/status/tool-result 等通用字段。
- 修改 `src/homemaster/tools/spec.py`、`dispatcher.py`、`results.py`、`registry.py`、`state_updater.py`，工具执行器接收 generic state 或 mapping。
- 修改 `src/homemaster/tools/builtin.py`、`simulated.py`、`skill_tools.py`，移除对旧 pipeline 和 home-domain AgentState 字段的依赖；`skill_tools.py` 改为读取保留下来的 skills registry。
- 修改 `src/homemaster/memory/context_snapshot.py`，不能继续读取 `AgentState.memory_hits`、`memory_context_snapshot`、`user_context_snapshot` 这类 home-shaped runtime 字段。
- 新增 `src/homemaster/agent/turn.py`，作为 CLI 调用 `AgentRuntime` 的薄适配层。
- 重写 `src/homemaster/cli/app.py`、`interactive_shell.py`、`run_command.py`、`doctor.py`、`errors.py`。
- 更新 CLI、tool dispatcher、AgentState 相关测试。

验收标准：
- CLI help 只显示 `run`、`shell`、`doctor`。
- 没有 `--scenario`、`stage`、`smoke`、`contract-smoke`、`understand` 入口。
- CLI 文件里没有 `task_runner`、`pipeline`、`stages`、`_guess_scenario`。
- `你好` 返回 assistant reply，不输出 `final_status` 或任务 completed。
- 本批次结束时旧 runtime 包可以还存在，但已经不能从 CLI 触达。

### 批次 3：Domain Tools、Fixture Sanitization、Old Runtime Deletion

目的：
- 保留任务理解、记忆检索、目标定位、模拟机器人动作、验证、总结、写回能力，但全部迁移为 domain tools。
- 把 memory 算法移动到 `src/homemaster/memory/`。
- 迁移少量高价值测试输入，并删除旧固定流程 runtime、pipeline、stages、scenario runner。

核心文件处理：
- 新增 `src/homemaster/domain/home/` 包，包含 contracts、state、tools、tool_registry、grounding、planning_context、world_overlay。
- 保留并泛化 `src/homemaster/skills/` 的 loader、registry、spec、builtin skill 包；删除或改写其中旧 Stage/旧 mode 依赖。
- 把 memory 相关顶层文件移入 `src/homemaster/memory/`，并重写现有 `src/homemaster/memory/__init__.py`、moved memory modules 和 `embedding_client.py` 中的 Stage 03 / Stage 06 文案。
- 把 `contracts.py`、`execution_state.py`、`failure_log.py`、`failure_rule_provider.py`、`grounding.py`、`orchestration_validator.py`、`planning_context.py`、`world_overlay.py` 中可复用的 home 领域代码迁入 `src/homemaster/domain/home/`，删除旧-only 顶层包装。
- 重写 `src/homemaster/__init__.py`、`src/homemaster/agent/__init__.py`，删除 pipeline/stages/decision 等旧包导出和旧架构说明。
- 重写或删除 `src/homemaster/trace.py`，不能保留 stage debug asset、`result.md`、`actual.json`、`llm_samples.jsonl` 语义。
- 删除 `src/homemaster/recovery_config.py` 和旧 recovery-loop 测试；若仍需重试预算，迁到通用 runtime 配置并使用 `retry_budget` / `max_tool_iterations` 这类命名。
- 旧 runtime 删除后清理 `events/runtime_events.py`、`events/sinks.py`、`events/sanitizer.py` 的旧 stage event 兼容。
- 创建 sanitized fixtures 到 `tests/homemaster/fixtures/home_tasks/`，不能原样 `git mv data/scenarios/*`。
- 删除 `src/homemaster/pipeline/`、`src/homemaster/stages/`、`src/homemaster/task_runner.py`、`src/homemaster/scenario_catalog.py`、`src/homemaster/scenario_runner.py`、`src/homemaster/scenario_validator.py`、`src/homemaster/providers/mimo_decision_client.py`、`src/homemaster/agent/decision.py`。
- 删除旧 pipeline/task/scenario/stage 测试或重写到新 domain/memory 模块；`test_skill_loader.py`、`test_skill_registry_phase4.py` 必须同步迁移到最终 skills API 或删除；`test_embedding_degradation.py` 必须迁到新 memory retrieval/tool 测试并去掉 `data/scenarios` 与 `case_dir/actual.json` 断言。
- 删除 `tests/homemaster/llm_cases/`、`tests/homemaster/prompt_snapshots/`、`tests/homemaster/prompt_snapshot_export.py`、`data/scenarios/`。

验收标准：
- home tool registry 暴露 `task_interpreter`、`memory_retriever`、`target_grounder`、`skill_view`、`robot_navigate`、`robot_observe`、`robot_manipulate`、`robot_verify`、`memory_writer`、`task_summarizer`。
- skills registry 可以注册和查询 skill package，但不会变成 runtime mode，也不包含 `mock_skills` 配置。
- domain tools 不 import AgentRuntime。
- AgentRuntime 和 generic tools 不 import domain tools。
- old runtime packages 不存在。
- 顶层 home-domain 旧模块不再被 import。
- events 只保留 generic runtime event vocabulary。
- 新 fixtures 不包含 `scenario`、`runtime_modes`、`deterministic` 或 numbered stage 字段。

### 批次 4：Config、Prompts、最终强制守门和全量验收

目的：
- 防止旧架构回潮。
- 配置、token budget、prompt、README 全部改成 agent/tool 语义。
- 用自动化和手工 CLI 验收证明迁移完成。

核心文件处理：
- 重写 `src/homemaster/runtime.py`、`src/homemaster/token_budget.py`、`config/homemaster.example.json`、`README.md`、`pyproject.toml`。
- 重写 `src/homemaster/prompt_loader.py`，只加载新 prompt 名称且不再引用旧编号 prompt。
- 重写 `tests/homemaster/test_runtime_settings.py`，删除或改写仍断言 `skill_mode`、`scenario_root`、`case_dir`、`executor_step_multiplier` 或旧 key rejection 的测试。
- 删除 `src/homemaster/prompts/stage_*.txt`，重新撰写 `agent_system_prompt.txt`、`task_interpreter_prompt.txt`、`memory_query_prompt.txt`、`task_summary_prompt.txt`，不得搬运旧 prompt 文案。
- `scripts/guard_no_legacy_terms.py` 从 report-only 切成默认强制。
- `tests/homemaster/test_cleanup_guard.py` 默认跑强制 guard。
- `tests/homemaster/test_import_boundaries.py` 验证 runtime/tools/skills/domain/context 边界。
- README 保留最终启动、配置、事件、工具扩展说明。

验收标准：
- `python scripts/guard_no_legacy_terms.py` 返回 0。
- `PYTHONPATH=src .venv/bin/python -m pytest -q` 通过。
- `PYTHONPATH=src .venv/bin/python -m ruff check .` 通过。
- `git status --short --ignored` 中无需要提交的 runtime/cache/debug 产物。
- guard 不扫描失败自己的源文件，但必须扫描其他 `scripts/` 文件。guard 禁止具体旧架构 token，不全局禁止 `compat` 或 `shim` 这类普通词根。
- `pyproject.toml` 不再包含 pipeline、编号 stage、scenario runner 或 legacy shim 相关描述/忽略项。
- `pyproject.toml` 不再包含 `stage_04.py`、`stage_05.py`、`stage_06.py`、`pipeline_core.py`、`pipeline_stages.py`、`recovery.py` 等已删除 facade ignore。
- `RuntimeSettings` 不再包含 `scenario`、`live_models`、`mock_skills`、`runtime_modes`、`deterministic`、`skill_mode`、`scenario_root`、`case_dir`、`executor_step_multiplier` 这类旧运行模式字段。
- ContextComposer 不包含 `task_card`、`target_candidates`、`current_location`、`holding_object`、`memory_hits` 等 home task 字段。
- 新 prompt 通过内容审查：不包含旧编号流程词，不暗示工具调用顺序，不要求闲聊进入任务流程。
- prompt loader 只能通过枚举式新 prompt id 加载新 prompt，不能保留任意 filename 入口访问旧 `stage_*.txt`。
- final guard 覆盖旧 live-case/debug asset 模式：`llm_cases`、`prompt_snapshots`、`result.md`、`llm_samples.jsonl`、`stage runs`、`case_dir + actual.json/expected.json/input.json` 组合。
- CLI 手工验收通过：
  - `PYTHONPATH=src .venv/bin/python -m homemaster.cli shell`
  - 输入 `你好` 得到自然回复。
  - 输入 `帮我拿个水` 看到实时事件并得到最终回复。

## 关键验收场景

### 闲聊

输入：

```text
你好
```

期望：
- assistant 直接回复问候。
- 不调用 domain tools。
- 不创建任务 debug 目录。
- final status 是 reply，不是任务 completed。

### 简单取物

输入：

```text
帮我拿个水
```

期望：
- 模型根据需要调用 task_interpreter、memory_retriever、target_grounder、robot tools。
- CLI 实时显示工具开始和结束。
- 最终 assistant 用自然语言说明结果。
- trace 可回放每次 model call 和 tool result。

### 信息不足

输入：

```text
帮我拿那个东西
```

期望：
- agent 可以直接 ask/clarify，而不是强行进入取物流程。
- 用户补充后继续同一 session。

### 记忆检索失败

期望：
- memory_retriever 返回 retryable tool failure。
- runtime 不崩溃。
- 模型可选择观察、搜索或说明无法完成。

## 删除和迁移策略

### 直接删除

- 历史运行产物。
- 旧编号流水线 live case 结果。
- 旧报告、旧记录、旧日志。
- 只为旧入口转发存在的配置。

### 重写保留

- contracts 中仍有价值的 domain schema，但命名改为任务、记忆、机器人动作语义。
- memory index 和 retrieval 算法，但作为 `memory_retriever` 工具内部实现。
- grounding 算法，但作为 `target_grounder` 工具内部实现。
- simulated robot 能力，但作为 domain tools。

### 不保留仓库内归档

不新增 `archive/` 存放旧资料。需要追溯时使用 git 历史，避免仓库继续出现旧架构残留。

## 风险和缓解

风险：一次性删除大量 fixture 后测试覆盖下降。
缓解：先迁移高价值断言，再删除旧 fixture；每个迁移阶段都有 pytest 和 guard。

风险：MiMo endpoint 对原生 tool calling 支持不完整。
缓解：把兼容逻辑限制在 LLMTransport 内，runtime contract 不变。

风险：旧文档删除导致上下文丢失。
缓解：V1.4 spec 记录最终目标；历史细节通过 git 历史查找。

风险：当前工作树已有大量改动。
缓解：实施前先做 Phase 0 baseline，并只提交明确属于当前迁移的文件。

## 最终完成定义

V1.4 完成时，用户应感受到：

- HomeMaster CLI 是一个会说话、会用工具、会追问的 agent shell。
- 任务执行过程可实时看见。
- 项目目录不再像旧流水线项目。
- 家庭机器人能力是工具集，不是固定流程。
- 新增工具或更换 provider 不需要改 runtime 主循环。

工程完成标准：

- 全仓库 legacy guard 通过。
- pytest 和 ruff 通过。
- README、配置、测试、fixtures、runtime trace 都使用新 agent loop 语义。
- `AgentRuntime` 可在不加载家庭机器人 domain tools 的情况下通过通用 loop 单测。
