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
  -> ContextComposer
  -> LLMTransport
  -> NormalizedAssistantMessage
  -> ToolDispatcher
  -> ToolResultMessage
  -> AgentSession
```

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

## 非目标

- 不在 V1.4 接入真实机器人硬件。
- 不在 V1.4 做复杂多用户账号系统。
- 不照搬 hermes-agent 的大而全实现，只吸收主链原则。
- 不保留仓库内旧编号流水线归档目录；需要追溯历史时使用 git 历史。

## 架构原则

1. AgentRuntime 只认识消息、工具调用、工具结果、事件、会话和预算，不认识家庭地图、取物任务或记忆检索细节。
2. 机器人领域能力全部通过 ToolSpec 暴露，工具可以读写领域状态，但不能把 runtime 变回固定流程。
3. Provider 适配层负责把不同模型 API 统一成 NormalizedAssistantMessage。runtime 不解析某个供应商私有响应结构。
4. 模型优先使用原生 tool calling。如果供应商 endpoint 只能返回文本 JSON，适配逻辑也必须封装在 LLMTransport 内，runtime 仍只看 normalized tool calls。
5. CLI 是会话界面，不是 scenario runner。普通问候应得到 assistant reply；可执行任务才进入工具循环。
6. 当前仓库不保留旧编号流水线命名、产物和文档残留。清理不是注释说明，而是删除、改名、迁移测试和加守门检查。

## 新核心组件

### AgentSession

职责：
- 保存 `messages`、`session_id`、`run_id`、turn index、会话级配置和运行摘要。
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

不做：
- 不猜 scenario。
- 不直接调用家庭机器人模块。
- 不解析旧决策 JSON。

### LLMTransport

职责：
- 把内部消息和工具 schema 转换为 provider 请求。
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
- ToolDispatcher 校验工具名和参数，执行工具，返回 ToolResultMessage。
- 工具结果必须能成为下一轮模型上下文，而不是只更新 Python 内存对象。

### Domain Tools

现有家庭机器人能力迁移为工具：

- `task_interpreter`：从用户自然语言提取可执行任务结构；对闲聊可以不调用。
- `memory_retriever`：检索 object/fact memory。
- `target_grounder`：基于检索结果和 world snapshot 选择候选目标或说明无法确定。
- `skill_view`：按需展开技能说明，保持 progressive disclosure。
- `robot_navigate`：模拟移动或查找。
- `robot_observe`：返回结构化观察。
- `robot_manipulate`：模拟拿取、放置、交付等动作。
- `robot_verify`：验证子目标或最终目标。
- `memory_writer`：写回任务记录和事实记忆。
- `task_summarizer`：生成面向用户和长期记忆的任务总结。

## 迁移批次

详细执行计划见 `plan/V1.4/agent-loop-full-migration-execution-plan.md`。本 spec 只保留压缩后的 5 个批次，避免再次被拆成 8 个以上的小阶段。

### 批次 0：基线、守门脚本、静态清理

目的：
- 记录当前 dirty worktree、旧命名命中面和 tracked runtime artifact。
- 先删除不参与新运行链路的历史记录、报告、日志、旧计划、tracked `var/` 产物、旧截图/场景脚本。
- 新增 guard，先 report-only，最后强制失败。

核心文件处理：
- 新增 `plan/V1.4/baseline/` 下的 baseline 报告。
- 新增 `scripts/guard_no_legacy_terms.py`。
- 新增 `tests/homemaster/test_cleanup_guard.py`。
- 更新 `.gitignore`。
- 删除 `docs/shim_lifecycle.md`、`record/`、`report/`、`log/`、`plan/V1.2/`、`plan/V1.3/`、`plan/v1.0/`、`plan/v1.1/`、tracked `var/homemaster/...` 和旧 scenario/screenshot 脚本。

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
- 新增 `src/homemaster/providers/transport.py`、`mimo_transport.py`。
- 重写 `src/homemaster/agent/runtime.py`、`src/homemaster/llm_client.py`。
- 修改 `src/homemaster/events/runtime_events.py` 和 `src/homemaster/events/sinks.py`，删除旧 stage lifecycle 事件字段。
- 删除 `src/homemaster/agent/decision.py`、`src/homemaster/providers/mimo_decision_client.py` 及对应测试。

验收标准：
- Runtime 单测只依赖 fake transport 和 fake tools，不 import home domain。
- MiMo transport 测试覆盖 content-only、tool-use、reasoning/empty/truncated response。
- 工具失败会作为 tool result message 进入下一轮模型上下文。

### 批次 2：家庭机器人能力迁移为 Domain Tools，并删除旧运行包

目的：
- 保留任务理解、记忆检索、目标定位、模拟机器人动作、验证、总结、写回能力。
- 删除旧固定流程 runtime、pipeline、stages、scenario runner。

核心文件处理：
- 新增 `src/homemaster/domain/home/` 包，包含 contracts、state、tools、tool_registry、grounding、planning_context、world_overlay。
- 把 memory 相关顶层文件移入 `src/homemaster/memory/`。
- 删除 `src/homemaster/pipeline/`、`src/homemaster/stages/`、`src/homemaster/task_runner.py`、`src/homemaster/scenario_catalog.py`、`src/homemaster/scenario_runner.py`、`src/homemaster/scenario_validator.py`。
- 删除旧 pipeline/task/scenario 测试，新增 domain tool 和 import boundary 测试。

验收标准：
- home tool registry 暴露 `task_interpreter`、`memory_retriever`、`target_grounder`、`skill_view`、`robot_navigate`、`robot_observe`、`robot_manipulate`、`robot_verify`、`memory_writer`、`task_summarizer`。
- domain tools 不 import AgentRuntime。
- AgentRuntime 不 import domain tools。
- old runtime packages 不存在。

### 批次 3：CLI、配置、Prompts、Fixtures 重建

目的：
- CLI 成为真正的 agent 会话入口。
- 测试资产和 prompt 资产全部改为 agent/tool 语义。

核心文件处理：
- 重写 `src/homemaster/cli/app.py`、`interactive_shell.py`、`run_command.py`、`doctor.py`、`errors.py`。
- 重写 `src/homemaster/runtime.py`、`src/homemaster/token_budget.py`、`config/homemaster.example.json`、`README.md`、`pyproject.toml`。
- 删除 `src/homemaster/prompts/stage_*.txt`，新增 `agent_system_prompt.txt`、`task_interpreter_prompt.txt`、`memory_query_prompt.txt`、`task_summary_prompt.txt`。
- 删除 `tests/homemaster/llm_cases/`、`tests/homemaster/prompt_snapshots/`、`tests/homemaster/prompt_snapshot_export.py`。
- 把少量高价值 home task fixtures 移到 `tests/homemaster/fixtures/home_tasks/`，删除生产 `data/scenarios/`。

验收标准：
- CLI help 只显示 `run`、`shell`、`doctor`。
- 没有 `--scenario`、`stage`、`smoke`、`contract-smoke`、`understand` 入口。
- `你好` 返回 assistant reply，不输出 final_status/task completed。
- `帮我拿个水` 显示实时 model/tool progress。

### 批次 4：最终强制守门和全量验收

目的：
- 防止旧架构回潮。
- 用自动化和手工 CLI 验收证明迁移完成。

核心文件处理：
- `scripts/guard_no_legacy_terms.py` 从 report-only 切成默认强制。
- `tests/homemaster/test_cleanup_guard.py` 默认跑强制 guard。
- `tests/homemaster/test_import_boundaries.py` 只验证新包边界。
- README 保留最终启动、配置、事件、工具扩展说明。

验收标准：
- `python scripts/guard_no_legacy_terms.py` 返回 0。
- `PYTHONPATH=src .venv/bin/python -m pytest -q` 通过。
- `PYTHONPATH=src .venv/bin/python -m ruff check .` 通过。
- `git status --short --ignored` 中无需要提交的 runtime/cache/debug 产物。
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
