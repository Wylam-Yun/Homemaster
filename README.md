# HomeMaster V1.9

LLM-first generic agent runtime with home-robot domain tools.

默认入口是 **ApplicationRuntime**：上下文组装、任务状态快照、统一工具执行、V2.1 分层记忆、目标 grounding 和模拟机器人执行。

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

wheel 安装或其他非源码部署可设置 `HOMEMASTER_CONFIG_PATH=/absolute/path/homemaster.yaml`；该文件仍须
mode 0600 且不进入 Git。

真实配置已加入 `.gitignore`，真实 key 只能保留在运行机器上，不能提交进 Git。仓库中的
`config/homemaster.example.yaml` 是字段模板，只包含占位认证值。

配置文件至少包含聊天和记忆 embedding provider：

- Mimo：用于 agent loop、检索 query、编排、总结。
- `MemoryEmbedding`：正式部署使用 SiliconFlow `Qwen/Qwen3-Embedding-8B` 的 `/v1/embeddings` 生成 4096 维向量。

## V2.1 记忆系统

默认 Home profile 提供 `memory`、`add_memory`、`search_memories`、`get_memory`、`update_memory`、
`delete_memory`。SOUL/USER/MEMORY 固定注入 session 快照；fact/procedure 使用 application-owned
`EmbeddedMindMemOS` 调用 MindMemOS 原生 schema pipeline，数据写入本地 Qdrant 和 Neo4j。写入必须绑定当前
Runtime 的 opaque evidence，所有 mutation 都要经过原生返回状态和 raw memory 终态读回。旧
`memory_retriever/memory_writer` 只保留在 benchmark memory mode。配置、工具示例、隐私和诊断见
[记忆用户指南](docs/memory-user-guide.md)，owner、不变量与数据流见
[记忆系统架构](docs/architecture/memory-system.md)。

持久记忆与代码解耦，统一位于 `memory.data_root`（默认 `~/.homemaster/memory`）的 `files/`、
`mindmemos/qdrant/` 和 `evidence.sqlite3`。MindMemOS 同时连接配置中的 Neo4j。旧版
`~/.homemaster/memories` 文件记忆可执行：

```bash
uv run homemaster memory migrate --config config/homemaster.yaml
```

迁移只处理旧 SOUL/USER/MEMORY 文件并保留旧源；旧 mem0 Qdrant/history 数据不会读取或迁移。
`doctor` 只报告文件迁移状态，不会创建目录或打开 MindMemOS。

设置 `memory.neo4j.mode: managed_local` 后，同一节点上的多个 HomeMaster 进程共享一套私有 Neo4j：首个
进程自动启动，最后一个进程退出时自动停止；`external` 模式则只连接既有服务。托管模式需要在 gitignored、
mode-0600 的 `config/homemaster.yaml` 中配置 Neo4j 安装目录、Java 21、URI 和密码。完整示例与同节点约束见
[记忆用户指南](docs/memory-user-guide.md)。

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
- Qwen3 MemoryEmbedding `/v1/embeddings` 调用
- embedded MindMemOS 导入、文件迁移状态与 backend 配置；`doctor` 不打开 Qdrant 或 Neo4j

## 配置与 Skills

HomeMaster SkillRegistry 支持 bundled、builtin、用户目录、Git root 内的项目目录、显式目录和显式配置的
data-only plugin 六类 Skill 来源。每个 `SKILL.md` 使用 OpenHarness YAML frontmatter；加载时会核对
真实路径、来源优先级和 builtin 覆盖授权。Skill 不要求 `tool_names`，也不能修改 universal Registry 或权限。
Plugin adapter 只读取 JSON manifest 与 `SKILL.md`，绝不导入 plugin Python、tools、hooks 或 MCP。
可先运行：

```bash
uv run homemaster --dry-run -p '检查药盒状态' --output-format json
```

输出包含 provider/model 的 `default/file/env/cli` 来源、已加载 Skill 和配置值；按锁定的 candidate 2，
typed 输出中已选择的运行时文本保持原值。它不会创建 provider client 或执行工具。完整配置和 Skill 示例见
[Skills 与配置用户指南](docs/skills-and-config-user-guide.md)，owner 与数据流见
[Application Runtime 架构](docs/architecture/application-runtime.md)。

Pillow 是默认 `observe` 工具的核心运行依赖；MCP SDK 仍属于 `mcp` optional extra。未安装该 extra 时，
universal Registry 仍可构造，只是不连接或注册 MCP resource/dynamic tool 入口。

## MCP 工具与资源

安装可选依赖后，Home application 可连接 stdio 或 streamable HTTP MCP server：

```bash
uv sync --extra dev --extra mcp
uv run homemaster --dry-run -p '检查外部工具' --output-format json
```

普通 `--dry-run` 只审计静态配置，不连接 server；显式增加 `--probe` 才会产生外部 I/O、
执行 discovery 并立即关闭临时连接，同时写入 `trace_dir/mcp_probe_audit.jsonl`。真实
one-shot/Interactive application 在首次 run 前只启动一次
MCP manager，连接成功的工具会按普通名称原子加入 application Registry，失败 server 不影响 builtin。
MCP nested JSON Schema 保真进入 Registry；resource discovery 用 opaque `resource_id`，读取后授权文本内容
在 typed result、preview 和 audit 中保持原值。tool/resource 原始结果同时按 tenant/session/run 写入 ACL
artifact；二进制和仅用于 transport 的宿主路径只投影 opaque handle。audit 写入故障不会阻断连接清理。
在 MCP SDK 的 mutation/read-only annotation 经真环境核对前，普通 discovered tool 一律按可能修改
远端状态处理：PLAN 拒绝、DEFAULT 要求确认；已连接且调用失败返回不可自动重试的
`outcome_unknown`。`list_mcp_resources` 与 `read_mcp_resource` 保持只读。
配置示例和 skill 引用方式见
[Skills 与配置用户指南](docs/skills-and-config-user-guide.md)。

## 权限、设备租约与急停

每次工具执行都经过同一条 `ToolExecutor -> PermissionChecker` 权限门。远程 Bearer credential 只能映射到预先
配置的 typed principal、tenant 和 capability；prompt、metadata、skill、slash command 与附件都不能
扩权。机器人读操作要求 `device.read`，写操作要求 `device.control`，MCP 调用要求 `mcp.call`。
后台任务/子 agent、Cron、配置修改和 MCP 凭证管理还分别要求 `process.spawn`、`scheduler.manage`、
`config.mutate` 和 `mcp.manage`，不能只凭通用 `tool.mutate` 调用。

application 在每个 run 边界把 borrowed backend 绑定为 tenant-pinned connection handle，并与
generation-aware FIFO lease 共用一个单调 generation owner。同一设备的写动作串行，不同设备可并发；
断线、close、stale generation 和 emergency-stop 会在 backend 前拒绝等待动作。已经开始但被 fence
的动作返回 `outcome_unknown`，不会自动重试。急停走独立 control path，只有 typed control receipt
成功且独立 typed state query 确认为 stopped 才成功；两次 return code 都进入结果与 audit。lease、
disconnect、fence 和 stop 事件追加到
`observability.trace_dir/device_audit.jsonl`，文件权限为 `0600`。
audit sink 是旁路镜像；写入失败会形成 typed `DeviceAuditFailure`，authoritative 内存事件、lease
释放和 emergency-stop 后端调用仍继续。

## 飞书 Gateway

Gateway 现在只装配一个飞书/Lark channel，不提供 Telegram selector 或多通道 registry。它支持私聊、
无需 @ 的群消息、thread/root 回复，基础 text/share 与简化 post/interactive 解析，图片、音频、视频和文件收发，
以及飞书建群/改名。该部署把飞书 transport 整体视为 trusted owner boundary：所有非 bot sender 都映射
为同一个内置 `feishu-owner`，无需配置 bot/user `open_id` 或 principal。

```bash
uv sync --extra dev --extra gateway
cp config/homemaster.example.yaml config/homemaster.yaml
chmod 600 config/homemaster.yaml

# 在 ignored config/homemaster.yaml 的 gateway.feishu 中填写：
# app_id: cli_xxx
# app_secret: ...
export HOMEMASTER_FEISHU_ENCRYPT_KEY='...'
export HOMEMASTER_FEISHU_VERIFICATION_TOKEN='...'

# Gateway 不会随配置自动启动；每次启动需在仓库根目录手动运行并保持该进程存活。
PYTHONPATH=src uv run homemaster gateway --config config/homemaster.yaml
```

`gateway.enabled: true` 和 `gateway.feishu.enabled: true` 只允许 Gateway 启动，不会创建后台守护进程，
也不会配置开机自启。进程退出或电脑重启后，需要再次执行上面的手动启动命令；当前仓库未提供
`launchd`、systemd 或其他自动重启服务。

`app_id/app_secret` 优先从 mode 0600、Git ignored 的真实 YAML 读取；旧环境变量配置仍兼容但不会与
YAML 单项混拼。`gateway.feishu.domain` 只允许 `feishu` 或 `lark`；完整配置见
[Skills 与配置用户指南](docs/skills-and-config-user-guide.md)。WebSocket 使用可终止的
子进程，关闭、outbound drain、active run 和 service join 共用一个 deadline。代码与 non-live 门已
覆盖 typed contract；真实 chat-list、message-create 和独立 message-get readback 均已通过，业务返回码为
0 且唯一 canary 精确匹配一次。媒体、reaction、群状态、reconnect 和 `lark` domain 仍为 `UNVERIFIED`。
localized post、真实 card `header/elements` 入站解析及 Markdown 链接/多表格完整 renderer 尚未迁移完成。

### 飞书驱动 ALFWorld

ALFWorld 具身模式复用同一个飞书 Gateway，但不会把 ALFWorld/Torch 重装进 HomeMaster 环境。
HomeMaster 通过 loopback HTTP 启动配置中指定的 ALFWorld Python worker；Gateway 独占绑定一个固定
episode 和一个飞书 session，进程退出时一并关闭 worker、Unity 和可选的 Xvfb。

```bash
# 真实路径只写在 ignored、mode-0600 的 config/homemaster.yaml：
# alfworld_gateway.python_executable / asset_root / data_root /
# config_path / trial_manifest / display
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --alfworld \
  --config config/homemaster.yaml
```

模型若调用 `task_planner`，飞书会展示结构化子任务；未调用时不会拦截或强制重试。每次真正触达
backend 的 `robot_go_to` / `robot_manipulate` 后，Runtime 要求模型单独调用 `observe`。飞书按事件顺序
展示语义动作说明及其对应图片，并转发模型可见回复；`usage.update`、thinking、原始
tool started/completed 等内部事件只保留在 JSONL trace，不发送给用户。

### 飞书驱动通用浏览器

Browser 模式复用现有飞书 Gateway 和 Home 通用工具，并为每个 run 创建隔离的
Playwright session。它不加载 ALFWorld 或 Coworker 旧工具：

```bash
# 先在 ignored、mode-0600 的 config/homemaster.yaml 配置 browser_gateway。
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --browser \
  --config config/homemaster.yaml
```

飞书正文可以直接包含变更单 URL。模型会加载唯一通用
`change-ticket-executor` Skill，从票据自然语言动态提取步骤、参数、验收和回滚；Skill 和
browser prompt 都不包含具体业务 SOP。每次浏览器写操作后，Runtime 强制单独调用
`observe`，现有 Gateway 媒体链把截图发回飞书；`browser_backfill` 还能把当前页面 PNG
粘贴到 Mock UI 的回填控件，并要求页面预览与原 PNG 的 SHA-256 完全一致。Browser run
不设置工具迭代次数上限；`--browser` 与 `--alfworld`
互斥。当前验收只证明 Ant Design Pro Mock UI 的页面操作和回填，不代表真实业务系统变更。

配置、演示输入和终态判据见
[Browser Gateway 用户指南](docs/browser-gateway-user-guide.md)，实现不变量见
[通用浏览器架构](docs/architecture/generic-browser-tools-phase1.md)。

## 跑一个任务

实时输出模式：

```bash
# 纯文本会在模型仍在生成时写入 stdout，最终答案不会重复打印
homemaster -p "检查客厅" --output-format text

# 单个缓冲 JSON 文档，只在运行结束后输出
homemaster -p "检查客厅" --output-format json

# 每个事件一行紧凑 JSON，最后一行固定为 type=result
homemaster -p "检查客厅" --output-format stream-json
```

交互 shell 与 `run --progress` 使用 Rich 实时区域；Rich/状态信息只写 stderr，
不会污染 `json` 或 `stream-json` 的 stdout。公开实时协议只包含七种
`StreamEvent`，`type=result` 是 HomeMaster 的终端扩展。Rich 完整显示 Bash command，成功只显示状态，
失败详情最多显示 500 字符及明确截断标记；机器事件和结果不截断。

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

`robot_go_to` 先验证当前成功 Provider 请求与 THOR event 的 frame 绑定，再从 frozen scene index
解析语义目标：优先选择当前 strict-visible 实例，默认允许使用同一 reset snapshot 中的唯一 exact
pose 尝试一次离屏导航。`alfworld_gateway.allow_offscreen_object_navigation: false` 可用于位置记忆实验：
strict-invisible non-receptacle 在任何 THOR 动作前返回 `target_not_visible`，offscreen receptacle
仍可作为搜索锚点导航；默认 `true` 保留 V1.8 点导航能力。冻结与当前 receptacle metadata 必须一致，
返回 event 必须让准确目标 strict-visible。内部 objectId、containment、坐标、候选和 snapshot
authority 不进入 Provider body；只有可供 `add_memory` 回传的 run-bound opaque evidence ref 会进入
模型 tool-result content。

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

> **Runtime output note:** authenticated and authorized runtime text is exact under the locked candidate-2
> policy, including selected config, trace, audit, SDK-log, and service-repr fields. Event field allowlists,
> tenant/session/run ownership, invalid-auth non-echo, binary artifact isolation, and Git placeholders remain
> mandatory; exact text does not grant authority or add fields.

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
hooks-only candidate；extension version、tool/provenance/capability 变化返回 `restart_required`，
活动 callback 存在时返回 `busy`。部署 approval 决定哪些 extension contributions 被注册；`RunRequest`
不再携带工具筛选字段。所有 hook result 和 lifecycle trace 都经过统一投影。CL-21 当前只在 HPC2 做
non-live 验证，具体外部 API/设备符号保持 `UNVERIFIED`，hkust4 测试等待用户指导。

## 当前边界

- 真实：Mimo，以及 V2.1 使用的 SiliconFlow Qwen3 MemoryEmbedding。
- 真实：V2.1 文件记忆、SiliconFlow Qwen3 4096 维 semantic、embedded Qdrant/BM25 与证据门。
- 模拟：navigation、operation、verification skill。
- Benchmark：`AlfredThorEnv` 已接入 V1.8 trial/reset/snapshot/current-view/typed-feedback 产品边界；内部回归通过，但完整 Gate B 与十 Episode 真实 API 证据仍不可用。

## 架构

默认入口是 **ApplicationRuntime**（`src/homemaster/application/`），其内部使用统一
AgentRuntime 和 application-owned ToolExecutor。CLI、Interactive、ALFWorld 与 Coworker 共享同一套
ordinary-name 工具协议，但在 composition 时只组合通用工具与当前显式环境的工具。

**Tool 系统**：Home 正式 alias 包括 `robot_go_to` 与显式 `observe`。`observe({})` 是 Home、ALFWorld
和 Coworker 共用的当前画面截图工具：成功时模型只收到一张 PNG，不含文字、DOM、状态或审计元数据；它只用于
确认画面，不授权、阻塞或使其他动作失效。universal Registry 以普通模型名称注册工具；
`homemaster.<name>.v1` 只作为隐藏诊断元数据，不参与模型选择或执行路由。

**Skills**：Skill 是 OpenHarness 兼容的按需 instruction document，不是工具授权声明，不要求
`tool_names`，也不能修改 Registry 或扩大 permission。来源优先级为 OpenHarness bundled < Home builtin
< `~/.homemaster/skills` < Git 项目内 `.homemaster/skills` < 显式目录；不会自动扫描 `.codex`、
`.claude` 或 `.agents`。模型先看 Available Skills 的名称与简介，再用唯一的
`load_skill(name=...)` 读取完整 `SKILL.md`；Skill 正文不会预加载，`/<skill-name>` 继续支持参数和已配置
模型覆盖。

**默认工具**：本地机器人 Registry 提供文件、`bash`、联网、
LSP、图片、计划、配置、Cron、后台任务、子 agent 和团队。后台 Cron 用
`homemaster cron start|status|stop` 管理；child worker 显式继承父应用配置。远程
`ask_user_question` 会把 session 置为等待态，并在下一条 channel 消息到达后恢复，而不是占住 webhook。
普通 Gateway 只披露通用工具；本地机器人、ALFWorld 与 Coworker 分别只追加自己的环境工具。
`--gateway --alfworld` 因此包含 `robot_go_to`、`robot_manipulate`、`robot_verify` 与 `observe`，
但不包含 Coworker 浏览器工具。

**MCP**：application-owned manager 在首次真实 run 前连接 stdio/HTTP server，原子注册 discovery
结果并加入 application Registry；Skills 发现不等待 MCP。资源入口为 `list_mcp_resources`、
`read_mcp_resource`，动态工具为 `mcp__<server>__<tool>`。连接、调用、断线和关闭写入字段受限、文本精确的
JSONL audit；WebSocket 配置会明确报告 unsupported，不会静默降级。

**Permissions/Devices**：飞书 Gateway 产生固定 trusted owner principal/capabilities；统一 execution chain 在
每次调用前授权。application-owned connection pool 与 physical-device FIFO lease 共用 generation，
disconnect/emergency-stop fencing 阻止等待动作，并把已开始动作标为不可自动重试的未知结果。

**Gateway/Channels**：唯一 remote channel 为飞书/Lark WebSocket。Gateway 把所有非 bot sender 映射为
固定 `feishu-owner`，同时保留 typed tenant/channel/chat/thread/sender identity 与 delivery context，确定性路由到
application-owned session，并只向现有 `ApplicationRuntime.run(RunRequest)` 提交请求。bounded priority
bus 对 progress 合并/淘汰，MEDIA/final/error/cancel 保留并反压；远程 progress 只能来自严格
allowlist 的公共事件投影；ALFWorld 只投影可选计划、语义动作进度、动作后媒体和模型回复，不投影
usage、thinking 或原始工具生命周期名。终态 `RunResult` 只发送一次 final。每条 outbound 在 egress 重新
核对 generation；shutdown 在一个 deadline 内处理 drain、子进程 channel 和 service join。默认配置关闭
Gateway。安装 `gateway` extra，在 ignored、mode-0600 的真实 YAML 中填写 `app_id/app_secret` 后运行：

```bash
uv sync --extra dev --extra gateway
uv run homemaster --gateway --config config/homemaster.yaml
```

`homemaster --gateway` 只启动远程 Gateway，不启动本地交互 shell；原有
`homemaster gateway --config ...` 命令继续可用。该命令必须手动执行并保持进程存活；配置开关不会自动启动
Gateway，进程退出或电脑重启后也不会自动恢复。

**目录结构**：

```text
application/ ApplicationRuntime、SessionManager 与资源 ownership
agent/      AgentRuntime、context 与 provider turn
tools/      BaseTool、universal Registry、权限检查与统一执行链
domain/     Home domain tools and contracts
skills/     SkillSpec / SkillLoader / SkillRegistry / builtin SKILL.md
mcp/        MCP config/status、stdio/HTTP client、Registry adapter 与 audit
artifacts/  tenant/session/run 分区的 opaque tool-output store
permissions/ typed 配置、capability policy 与路径/命令规则
devices/    connection pool、generation lease、emergency stop 与 JSONL audit
channels/   typed channel DTO、bounded priority bus、router、飞书 adapter 与群操作
gateway/    credential、ApplicationRuntime bridge、cancel/recovery 与公共事件边界
memory/     RAG retrieval / index / tokenizer / runtime memory store
events/     RuntimeEvent schema、sinks 与 remote allowlist projection
config/     RuntimeSettings 和 path/config helpers
providers/  LLM/embedding provider clients
cli/        CLI 入口（run, doctor, interactive shell）
```
