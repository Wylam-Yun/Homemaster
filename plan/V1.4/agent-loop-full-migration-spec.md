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

## 迁移阶段

### 阶段 0：基线保护和禁止词清单

目的：
- 在大改前记录当前 dirty worktree、tracked/untracked 产物、旧编号流水线命中面。
- 建立清理 guard 的最终标准，避免后续又把旧命名引回来。

主要工作：
- 生成 baseline 报告到 `plan/V1.4/baseline/`。
- 列出需要删除、改名、迁移、保留但重写的文件集合。
- 新增 `scripts/guard_no_legacy_terms.py`，禁止 tracked files 中出现旧编号流水线英文命名和旧目录词。
- guard 暂时可配置为 report-only，直到最后阶段切为强制失败。

验收标准：
- baseline 文件包含 git status、旧命名命中列表、tracked runtime artifact 列表。
- guard 脚本可以运行并输出当前违规列表。
- 不修改既有业务代码。

### 阶段 1：删除历史产物和旧文档残留

目的：
- 先清掉不参与运行的历史包袱，让后续迁移面更清楚。

主要工作：
- 删除仓库内旧计划、记录、报告、日志、历史运行产物。
- 删除 tracked `var/` 运行结果。
- 删除本地交互误生成的 untracked fixture。
- 清理 build/cache/egg-info 等本地产物，只保留 `.gitignore` 规则。
- README 和 docs 重写为 V1.4 agent loop 语义。

验收标准：
- `git ls-files var` 无输出。
- `git ls-files plan record report log` 中只保留 V1.4 有效规划文件，不再包含旧编号流水线文档。
- 仓库根目录 `rg` 旧英文命名只剩 guard 脚本自身的规则定义，且该文件在 guard 白名单内。
- README 不再描述固定流程。

### 阶段 2：建立通用消息和 transport contract

目的：
- 把主链从自定义 decision JSON 改为通用 message/tool-call contract。

主要工作：
- 新增或重写：
  - `agent/messages.py`
  - `agent/session.py`
  - `agent/transport.py`
  - `agent/normalized.py`
- 定义 `AgentMessage`、`AssistantMessage`、`ToolCall`、`ToolResultMessage`、`NormalizedAssistantMessage`。
- 重写 MiMo provider：优先使用 Anthropic 协议的 tool use；如果 endpoint 不支持原生工具，则在 transport 内部做适配转换。
- 删除自定义 decision contract 的主链依赖。

验收标准：
- 单测覆盖：content-only response、tool-call response、reasoning-only response、empty response、truncated response。
- AgentRuntime 测试不 import 任何 domain tool 模块。
- Provider 测试证明 runtime 只消费 NormalizedAssistantMessage。

### 阶段 3：重写 AgentRuntime 主循环

目的：
- 实现真正的通用 agent loop。

主要工作：
- AgentRuntime 改为：
  1. 接收 AgentSession。
  2. 追加用户消息。
  3. 调 ContextComposer 构造 API messages。
  4. 调 LLMTransport。
  5. 追加 assistant message。
  6. 有 tool calls 时执行并追加 tool result messages。
  7. 无 tool calls 时返回 assistant reply。
- 加入迭代预算、中断、空回复恢复、无效工具名恢复、无效 JSON 参数恢复。
- RuntimeEvent 覆盖：
  - run started/completed/failed
  - turn started/completed
  - model call started/completed/failed
  - assistant message received
  - tool call started/completed/failed
  - final reply emitted

验收标准：
- `你好` 在 shell 中返回自然语言回复，不创建机器人任务产物。
- 一个 fake provider 场景可跑通：user -> assistant tool call -> tool result -> assistant final reply。
- 工具失败会进入下一轮模型上下文，而不是只写内部状态。
- CLI 可实时看到模型调用和工具调用事件。

### 阶段 4：迁移家庭机器人能力为 domain tools

目的：
- 保留 HomeMaster 的取物、观察、验证、记忆能力，但全部通过工具边界进入主链。

主要工作：
- 新建 `domain/` 或 `home/` 包承载家庭机器人数据结构和工具实现。
- 把当前任务理解、记忆检索、目标定位、模拟动作、验证、总结、写回能力迁移成 domain tools。
- 工具输入输出改为 domain-native schema，不携带旧流程编号。
- ToolResultMessage 中包含人可读 summary、结构化 data、failure reason、retryable。

验收标准：
- `帮我拿个水` 可通过通用 loop 完成，并在 trace 中体现每个工具调用。
- `帮我看看药还在不在` 可通过相同 loop 完成。
- 记忆检索失败不会导致 Python attribute error；会作为工具失败消息返回模型。
- domain tools 不 import AgentRuntime；AgentRuntime 不 import domain tools 的具体实现。

### 阶段 5：CLI shell 和 run 命令重建

目的：
- CLI 成为真正的 agent 会话入口。

主要工作：
- shell 支持连续会话、`/new`、`/status`、`/debug`、`/events`、`/exit`。
- 默认显示实时 progress events。
- 删除 scenario guessing。
- `run` 命令支持单轮任务，输出 final assistant reply、status、trace path。
- debug 路径改为 `var/homemaster/runs/<run_id>/...` 语义，但不跟踪到 git。

验收标准：
- shell 输入 `你好` 输出 assistant reply，不输出任务 completed 状态。
- shell 输入 `帮我拿个水` 能实时显示 model/tool progress。
- 同一 shell 内连续两轮会复用 session history。
- 每次交互使用唯一 run id，不覆盖上一轮 debug。

### 阶段 6：测试和 fixture 体系重建

目的：
- 测试资产跟随新架构重命名，删除旧编号 fixture。

主要工作：
- 删除旧 live case 目录和 prompt snapshots。
- 新建：
  - `tests/homemaster/fixtures/agent_loop/`
  - `tests/homemaster/fixtures/domain_tools/`
  - `tests/homemaster/fixtures/sessions/`
- 测试名称改为：
  - `test_agent_loop.py`
  - `test_agent_session.py`
  - `test_transport_mimo.py`
  - `test_domain_tools.py`
  - `test_cli_shell.py`
  - `test_cleanup_guard.py`
- 把高价值断言迁移为新测试，不保留旧命名。

验收标准：
- `pytest` 通过。
- `ruff check .` 通过。
- `rg` 旧英文命名在 `tests/` 无命中。
- 测试不依赖 tracked runtime artifact。

### 阶段 7：配置、文档、包边界收口

目的：
- 清理配置和工程边界，让新贡献者只看到 agent loop 架构。

主要工作：
- `config/homemaster.example.json` token budget key 改为 agent/tool 语义。
- 删除旧入口转发 ignore 和过期 import-boundary 例外。
- README 写明新 CLI、配置、事件、工具扩展方式。
- 增加 package boundary tests：
  - runtime core 不依赖 domain implementation。
  - domain tools 不依赖 CLI。
  - provider transport 不依赖 domain tools。

验收标准：
- `pyproject.toml` 无已删除文件 ignore。
- `config/` 无旧流程编号 key。
- import boundary tests 通过。
- README 中的启动命令可以实际运行。

### 阶段 8：最终强制守门

目的：
- 防止旧编号流水线残留和回潮。

主要工作：
- guard 从 report-only 切为强制测试。
- CI/本地测试默认运行 guard。
- 最终跑一次全仓库扫描、pytest、ruff。

验收标准：
- `python scripts/guard_no_legacy_terms.py` 返回 0。
- `pytest -q` 通过。
- `ruff check .` 通过。
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
