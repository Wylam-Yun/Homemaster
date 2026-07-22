# HomeMaster V1.9

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

Provider 配置只从 `config/homemaster.yaml` 读取。首次配置时复制脱敏模板：

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/homemaster.yaml
```

真实配置已加入 `.gitignore`，真实 key 只能保留在运行机器上，不能提交进 Git。仓库中的
`config/homemaster.example.yaml` 是字段模板，只包含占位认证值。

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

## 配置与 Skills

Home profile 支持 builtin、用户目录、Git root 内的项目目录和显式目录四类 skill 来源。每个
`SKILL.md` 使用标准 YAML frontmatter；加载时会核对真实路径、来源优先级、builtin 覆盖授权，
并确保 `tool_names` 只能引用当前 frozen ToolView 中的 model alias。可先运行：

```bash
uv run homemaster --dry-run -p '检查药盒状态' --output-format json
```

输出包含 provider/model 的 `default/file/env/cli` 来源、已加载 skill 及 secret-safe 拒绝计数，
不会创建 provider client 或执行工具。完整配置和 skill 示例见
[Skills 与配置用户指南](docs/skills-and-config-user-guide.md)，owner 与数据流见
[Application Runtime 架构](docs/architecture/application-runtime.md)。

## MCP 工具与资源

安装可选依赖后，Home application 可连接 stdio 或 streamable HTTP MCP server：

```bash
uv sync --extra dev --extra mcp
uv run homemaster --dry-run -p '检查外部工具' --output-format json
```

普通 `--dry-run` 只审计脱敏静态配置，不连接 server；显式增加 `--probe` 才会产生外部 I/O、
执行 discovery 并立即关闭临时连接，同时写入 `trace_dir/mcp_probe_audit.jsonl`。真实
one-shot/Interactive application 在首次 run 前只启动一次
MCP manager，连接成功的工具会加入 Home ToolView，失败 server 不影响 builtin。MCP nested JSON
Schema 保真进入 Catalog；resource URI 仅在 adapter 内部保存，模型只见 opaque `resource_id`。
所有 tool/resource 原始结果先按 tenant/session/run 写入 ACL artifact，模型和事件只接收脱敏、限长
preview 与 opaque handle；resource audit 只记录不可逆 hash 引用，audit 写入故障不会阻断连接清理。
在 MCP SDK 的 mutation/read-only annotation 经真环境核对前，普通 discovered tool 一律按可能修改
远端状态处理：PLAN 拒绝、DEFAULT 要求确认；已连接且调用失败返回不可自动重试的
`outcome_unknown`。`mcp_list_resources` 与 `mcp_read_resource` 保持只读。
配置示例和 skill 引用方式见
[Skills 与配置用户指南](docs/skills-and-config-user-guide.md)。

## 权限、设备租约与急停

每次工具执行都经过同一条 `ToolExecutionPipeline` 权限门。远程 Bearer credential 只能映射到预先
配置的 typed principal、tenant 和 capability；prompt、metadata、skill、slash command 与附件都不能
扩权。机器人读操作要求 `device.read`，写操作要求 `device.control`，MCP 调用要求 `mcp.call`。

application 在每个 run 边界把 borrowed backend 绑定为 tenant-pinned connection handle，并与
generation-aware FIFO lease 共用一个单调 generation owner。同一设备的写动作串行，不同设备可并发；
断线、close、stale generation 和 emergency-stop 会在 backend 前拒绝等待动作。已经开始但被 fence
的动作返回 `outcome_unknown`，不会自动重试。急停走独立 control path，只有 typed control receipt
成功且独立 typed state query 确认为 stopped 才成功；两次 return code 都进入结果与 audit。lease、
disconnect、fence 和 stop 事件追加到
`observability.trace_dir/device_audit.jsonl`，文件权限为 `0600`。
audit sink 是旁路镜像；写入失败会形成 typed `DeviceAuditFailure`，authoritative 内存事件、lease
释放和 emergency-stop 后端调用仍继续。

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

正常 shell 路径读取 `config/homemaster.yaml` 中配置的真实 provider；当前正式验收模型是
Mimo `mimo-v2.5`。录屏右侧五区分别展示锁定 SOP、模型 Planner、模型选择的工具/公开
回复、环境返回/确定性决策摘要、异常与关键历史。Planner 是模型状态，工具结果属于环境，
决策摘要由确定性 reducer 生成；三者不能互相冒充。`assistant.thinking`、prompt 和
chain-of-thought 永不进入 presentation v2 或页面。

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

最终真实模型 bundle 必须额外通过模型身份门：

```bash
.venv/bin/python scripts/coworker_demo/verify_run_bundle.py \
  var/coworker-demo/{run_id} \
  --data-root data/coworker_demo/case_02 \
  --expected-model mimo-v2.5
```

2026-07-18 的两条历史录屏和 `scripted_shell_gate.py` 只属于 scripted presentation
展示门，不能证明实时 LLM 做过决策，也不能作为最终 demo acceptance。失败的真实尝试
同样保留 `attempt_manifest.json`、run root 和错误类型，不得从报告中删除。

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

## Trusted Extensions（CL-21）

Home extensions 默认关闭。部署者必须在 `extensions.approvals` 中固定 manifest 路径、extension id、
SemVer、host 重新计算的 canonical SHA-256、deployment grants 和 enabled tool ids。manifest 的
requested capabilities 不能自授权；plugin tool 的权限是 requested、deployment grant 与当前
run principal capabilities 的交集。plugin tool 还必须使用 `ExecutionBackend.PLUGIN`、精确
`extension:<id>@<version>#sha256:<digest>` provenance 和非空 `required_capabilities`；exact tool token
只能替代通用读写能力，不能替代这些 canonical capabilities。entrypoint 从 pinned manifest directory
fd 逐级拒绝 symlink，校验与执行始终使用同一份字节。

manifest 可用 `dependencies` 显式列出同目录 flat `.py` 依赖；canonical digest 覆盖 manifest、
entrypoint 与排序后的依赖字节，依赖只从同一批已验证 bytes 加载。未声明的同目录 import fail closed，
执行模块的 `__file__` 不暴露批准目录。MVP 只支持显式批准的可信本地 Python 文件和 async lifecycle
hooks，不是 hostile-code sandbox；硬编码任意外部绝对路径仍属于部署者批准 trusted code 的权限，
timeout/cancel 会按 deadline 立即 fence 结果；抗取消 task 仍计入 active 并阻止 reload/cleanup，但不撤销
任意副作用。hook
不能成为 permission、device safety、terminal、verifier 或 scorer 的唯一 owner。reload 只允许
hooks-only candidate；extension version、tool/provenance/capability/profile 变化返回 `restart_required`，
活动 callback 存在时返回 `busy`。省略 request `enabled_tool_ids` 使用 profile；显式空 tuple 禁用全部工具，
任何越界 id 在 lifecycle hook 前拒绝。所有 hook result 和 lifecycle trace 都经过统一脱敏。CL-21 当前只在 HPC2 做
non-live 验证，具体外部 API/设备符号保持 `UNVERIFIED`，hkust4 测试等待用户指导。

## 当前边界

- 真实：Mimo、BGE-M3。
- 程序：可靠记忆判定、轻量记忆写回。
- 模拟：navigation、operation、verification skill。
- Benchmark：`AlfredThorEnv` 已接入 V1.8 trial/reset/snapshot/current-view/typed-feedback 产品边界；内部回归通过，但完整 Gate B 与十 Episode 真实 API 证据仍不可用。

## 架构

默认入口是 **ApplicationRuntime**（`src/homemaster/application/`），其内部使用统一
AgentRuntime 和无 session 状态的 ToolExecutionPipeline。CLI、Interactive、ALFWorld 与
Coworker 共享这条控制流，每个 run 单独冻结 ToolView、provider request、generation 和环境绑定。

**Tool 系统**：Home 正式 alias 包括 `robot_go_to` 与显式 `observe`；canonical Catalog 以 stable
internal id 注册环境 variant，ToolView 决定每个 run 的可见与可执行集合。

**Skills**：通过 `skill_view` 实现 progressive disclosure。builtin/user/project/explicit 来源在
composition 时完成路径和 capability 校验，运行中不能修改 frozen ToolView 或扩大 permission。

**MCP**：application-owned manager 在首次真实 run 前连接 stdio/HTTP server，原子注册 discovery
结果并重新冻结 Home ToolView；连接、调用、断线和关闭写入脱敏 JSONL audit。WebSocket 配置会
明确报告 unsupported，不会静默降级。

**Permissions/Devices**：Gateway credential 产生 immutable principal/capabilities；统一 execution chain 在
每次调用前授权。application-owned connection pool 与 physical-device FIFO lease 共用 generation，
disconnect/emergency-stop fencing 阻止等待动作，并把已开始动作标为不可自动重试的未知结果。

**Gateway/Channels**：首个 remote channel 为 Telegram long polling。Gateway 从 exact sender mapping
产生 typed tenant/channel/chat/thread/sender identity，确定性路由到 application-owned session，并只向
现有 `ApplicationRuntime.run(RunRequest)` 提交请求。bounded priority bus 对 progress 合并/淘汰，
final/error/cancel 保留并反压；远程 progress 只能来自严格 allowlist/redaction 的公共事件投影，终态
`RunResult` 只发送一次 final。每条 outbound 在 egress 重新核对 generation，shutdown 在 deadline 内先
排空 outbound 再停止 channel；未认证 sender 在任何附件下载前即被拒绝。
默认配置关闭 Gateway，安装 `gateway` extra、配置环境变量 token 与 sender principal 后运行：

```bash
uv sync --extra dev --extra gateway
export HOMEMASTER_TELEGRAM_BOT_TOKEN='...'
uv run homemaster gateway --config config/homemaster.yaml
```

**目录结构**：

```text
application/ ApplicationRuntime、SessionManager 与资源 ownership
agent/      AgentRuntime、context 与 provider turn
tools/      canonical contracts、Catalog/ToolView 与统一执行链
domain/     Home domain tools and contracts
skills/     SkillSpec / SkillLoader / SkillRegistry / builtin SKILL.md
mcp/        MCP config/status、stdio/HTTP client、Catalog adapter 与 audit
artifacts/  tenant/session/run 分区的 opaque tool-output store
permissions/ typed 配置、capability policy 与路径/命令规则
devices/    connection pool、generation lease、emergency stop 与 JSONL audit
channels/   typed channel DTO、bounded priority bus、router 与 Telegram adapter
gateway/    credential、ApplicationRuntime bridge、cancel/recovery 与公共事件边界
memory/     RAG retrieval / index / tokenizer / runtime memory store
events/     RuntimeEvent schema, sinks, sanitizer 与 remote public projection
config/     RuntimeSettings 和 path/config helpers
providers/  LLM/embedding provider clients
cli/        CLI 入口（run, doctor, interactive shell）
```
