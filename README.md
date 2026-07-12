# HomeMaster V1.7

LLM-first generic agent runtime with home-robot domain tools.

默认入口是 **GenericAgentRuntime**（Mimo 驱动的 tool loop）：上下文组装、任务状态快照、工具调用、记忆检索、目标 grounding、模拟机器人执行和轻量记忆写回。

> `skill_mode=simulated` 是当前支持的运行模式。navigation / operation / verification skill 使用模拟执行器，未接真实机器人、VLA、VLM。真实 VLA/VLN/VLM 执行器尚未集成。

## 环境配置

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -c "import homemaster; print(homemaster.__version__)"
```

如果迁移到新机器或新目录，按下面顺序配置：

```bash
cd <HomeMaster 项目目录>

# 推荐使用 uv 创建项目内虚拟环境
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python ".[dev]"

# RAG 依赖
uv pip install --python .venv/bin/python "bm25s>=0.2" "jieba>=0.42"

# 验证包能导入
PYTHONPATH=src .venv/bin/python -c "import homemaster, bm25s, jieba; print(homemaster.__version__)"
```

如果机器上没有 `uv`，先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

API 配置默认从 `config/api_config.json` 读取；更完整的运行配置放在 `config/homemaster.json`。不要把真实 key 提交进 git。

ALFWorld benchmark 使用完整的 YAML provider 配置。首次配置时复制脱敏模板并只在本机填写真实 key：

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
```

`config/homemaster.yaml` 已加入 `.gitignore`，真实文件只能保留在运行机器上；仓库只提交字段齐全的 `config/homemaster.example.yaml`。

配置文件需要包含两个 provider：

- Mimo：用于 agent loop、检索 query、编排、总结。
- BGE-M3：用于 `/v1/embeddings` 生成向量。

配置好之后，用 `doctor --live` 检查，不要先直接跑。

## 体检

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -m homemaster.cli doctor --live
```

`doctor --live` 会检查：

- 本地依赖和导入
- API 配置是否可读
- Mimo 最小 JSON 调用
- BGE-M3 `/v1/embeddings` 调用
- runtime memory 目录是否可写

## 跑一个任务

```bash
cd /Users/wylam/Documents/workspace/HomeMaster

PYTHONPATH=src .venv/bin/python -m homemaster.cli run \
  --utterance "去厨房找水杯，然后拿给我" \
  --progress
```

交互式 shell：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli shell
```

## ALFWorld Benchmark

`AlfredThorEnv` 模式使用真实 THOR scene state 评测高层规划，同时由 Harness 负责准确对象 grounding、主要导航和同目标局部 put 位姿执行。公开工具保持为 `robot_go_to`、`robot_manipulate`、`robot_verify` 和 `task_progress_check`；不产生新观察的 `robot_inspect_view` 已删除。

```bash
export ALFWORLD_DATA=/path/to/alfworld/data

PYTHONPATH=src .venv/bin/python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /path/to/alfworld \
  --alfworld-config /path/to/alfworld/configs/base_config.yaml \
  --trace-root var/alfworld-trace \
  --env-type AlfredThorEnv \
  --split valid_unseen \
  --episodes 10 \
  --observation-mode visual_eval
```

MVP 对 `put` 使用固定生产预算：导航最多 65 个候选、66 个 THOR action、34.804 秒；局部 put 最多 9 个候选、17 个 THOR action、5.669 秒。成功同时要求 THOR 返回成功和准确外部终态；Harness grounding/navigation/operation 或状态矛盾会立即终止 Episode、排除 Agent 分数，并在 CLI/summary 同时报告 `harness_valid_coverage` 与 `formal_score_available`。

使用说明见 [ALFWorld 用户指南](docs/alfworld-user-guide.md)，实现不变量与数据流见 [ALFWorld Harness 架构](docs/architecture/alfworld-harness.md)。

## Runtime Event Trace

Every `homemaster run` writes `runtime_events.jsonl` to the run's trace directory.

Event types include: `run_started`, `run_completed`, `run_failed`, `turn_started`,
`turn_completed`, `llm_call_started`, `llm_call_completed`, `llm_call_failed`,
`tool_call_started`, `tool_call_completed`, `tool_call_failed`, and more.
See `src/homemaster/events/runtime_events.py` for the full `RuntimeEvent` definition.

Use `--progress` to stream a compact progress summary to stderr during the run.

> **Security note:** Runtime event traces contain tool call names and result status codes
> but never raw LLM prompts, responses, or API keys. The `sanitize_for_log()` function
> strips sensitive content before writing to the trace sink.

## 当前边界

- 真实：Mimo、BGE-M3。
- 程序：可靠记忆判定、轻量记忆写回。
- 模拟：navigation、operation、verification skill。
- Benchmark：`AlfredThorEnv` 通过真实 THOR 返回码和 scene metadata 验证导航及 put；MVP 尚未为 take/open/use 提供同等级局部恢复契约。

## 架构

默认入口是 **GenericAgentRuntime**（`src/homemaster/agent/`），一个 Mimo 驱动的
tool loop。Mimo 在每一轮选择 tool 调用，Dispatcher 执行，结果返回给 Mimo，
直到 Mimo 选择结束或达到 max_turns。

**Tool 系统**：10 个 home domain tools（task_interpreter, memory_retriever,
target_grounder, skill_view, robot_navigate, robot_observe, robot_manipulate,
robot_verify, memory_writer, task_summarizer）。

**Skills**：通过 skill_view 实现 progressive disclosure。Mimo 按需加载 skill context
（fetch_object, check_object_state），而不是一次性获取所有 skill 信息。

**目录结构**：

```text
agent/      GenericAgentRuntime 实现（tool loop, context, turn）
tools/      ToolSpec / ToolRegistry / Dispatcher
domain/     Home domain tools and contracts
skills/     SkillSpec / SkillLoader / SkillRegistry / builtin SKILL.md
memory/     RAG retrieval / index / tokenizer / runtime memory store
events/     RuntimeEvent schema, sinks, sanitizer
config/     RuntimeSettings 和 path/config helpers
providers/  LLM/embedding provider clients
cli/        CLI 入口（run, doctor, interactive shell）
```
