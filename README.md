# HomeMaster V1.8

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

## Change Coworker Demo

现有 `homemaster shell` 现在可以识别锁定的 `case_02` 变更单路径，并在独立 child
run 中自主完成真实网页操作、自动化 job、tmux/Bash 验证、SOP 决策、DAG 评分与
H.264 录屏。普通对话仍使用原有 HomeMaster runtime；coworker 的十一项工具、两份
skill、任务状态和证据不会进入默认 home 或 ALFWorld registry。

```bash
uv pip install --python .venv/bin/python ".[dev,coworker]"
uv venv --python 3.11 apps/case02_openenv/.venv
uv pip install --python apps/case02_openenv/.venv/bin/python -e apps/case02_openenv

TICKET_PATH="$(realpath data/coworker_demo/case_02/test_set/item_change_ticket.json)"
PYTHONPATH=src .venv/bin/python -m homemaster.cli shell

# normal
<TICKET_PATH 的绝对路径输出>

# post-change anomaly and verified rollback
post_change_anomaly <TICKET_PATH 的绝对路径输出>
```

运行前先执行 `scripts/coworker_demo/preflight.py`。完整配置、操作、评分和产物说明见
[Change Coworker 用户指南](docs/coworker-demo-user-guide.md)，边界与证据流见
[Change Coworker 架构](docs/architecture/coworker-demo.md)。Mac Screen Sharing 是可选
观察通道，不是运行或交付门。

## ALFWorld Benchmark

`AlfredThorEnv` 模式使用真实 THOR scene state 评测高层规划。V1.8 在模型循环前验证 exact trial manifest，执行 controlled-time reset scan，并原子发布 immutable Oracle pose snapshot；公开工具保持为 `robot_go_to`、`robot_manipulate`、`robot_verify` 和 `task_progress_check`。

`robot_go_to` 先验证当前成功 Provider 请求与 THOR event 的 frame 绑定，再从 frozen scene index 解析语义目标：优先选择当前 strict-visible 实例，没有可见匹配时允许使用同一 reset snapshot 中的唯一 exact pose 尝试一次离屏导航。返回 event 必须让准确目标 strict-visible，否则按 Harness 导航失败终止。导航校验把 physical world 和 ALFWorld control state 分开；持有物随 agent 移动的 geometry 会规范化，但 inventory、`isPickedUp`、containment 和任务状态仍参与完整性检查。所有 manipulation 通过统一外部动作网关和强类型反馈返回，内部 objectId、坐标、候选和 snapshot authority 不进入 Provider body。

```bash
export ALFWORLD_DATA=/path/to/alfworld/data

PYTHONPATH=src .venv/bin/python -m homemaster.cli benchmark-alfworld \
  --alfworld-root /path/to/alfworld \
  --alfworld-config /path/to/alfworld/configs/base_config.yaml \
  --trace-root var/alfworld-trace \
  --env-type AlfredThorEnv \
  --split valid_unseen \
  --episodes 1 \
  --trial-manifest /path/to/trial-manifest.json \
  --observation-mode visual_eval
```

manifest entry 数必须等于 `--episodes`，并绑定相对 trial ID、trial SHA-256、逻辑场景和 goal fingerprint。reset 成功 setup 的 backend action 数是 `N+4`；reset/control terminal 在 Provider 构造前停止。CLI/summary 分开报告 Agent 成功率、evaluation/Harness coverage、Provider/Runtime availability 和 `formal_score_available`。

当前 V1.8 外部验收仍有公开缺口：Gate A 为 19/20 worker，`exact-cases-v3.json` 未生成，不能宣称完整 PASS。中断后的修复已让真实 run 从 0 次模型 backend action 的离屏死锁恢复为正常 Provider/tool/backend 调用；固定十 Episode run `alfworld-valid_unseen-v18-offscreen-fix-20260718-002` 完整退出并得到 1 个 `agent_success`、5 个可计分 Episode、5 个 Harness invalid、29 个模型 backend actions 和 52 个 Provider attempts。4 个 FloorPlan10 Episode 暴露 normal-time physical-world drift，另 1 个 Episode 中 THOR 在手持 Basketball 时拒绝 DeskLamp 冻结位姿；Provider/Runtime availability 均为 1.0，但 coverage 为 0.5、`formal_score_available=false`。这些失败保持可见，不通过放宽终态门伪装成 PASS。

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
- Benchmark：`AlfredThorEnv` 已接入 V1.8 trial/reset/snapshot/current-view/typed-feedback 产品边界；内部回归通过，但完整 Gate B 与十 Episode 真实 API 证据仍不可用。

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
