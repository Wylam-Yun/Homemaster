# HomeMaster V1.9 实施计划

状态：历史 V1.9 执行基线。本文中的 `ObservationService`、provider/model-view binding、freshness/debt 和
环境专用 observation variants 已被 `generic-screenshot-observe-implementation-plan.md` 取代，不再描述
当前 observation 架构。本文其余内容是
[`openharness-homemaster-comparison-spec.md`](./openharness-homemaster-comparison-spec.md) 的实施
配套文档；冲突时以 comparison spec 的规范性契约和测试 gate 为准。

源码基线：

- HomeMaster：`5b150a9671bb087b32ed57971a39fa472e8ff1e1`
- OpenHarness：`9b2efd795c6aa09f88b0c257d269a9e518da6ae7`
- 计划日期：2026-07-20

## 1. 实施目标与边界

V1.9 的首要目标不是把 HomeMaster 改造成 OpenHarness 的分支，而是把 OpenHarness 已验证的
通用 Harness 能力复用到 HomeMaster，同时保留 HomeMaster 的机器人、ALFWorld 和 Coworker
领域契约。

本轮必须完成的核心闭环是：

1. 统一 tool contract、Catalog/View、执行 pipeline 和 observation。
2. 建立 application/session/run 三层 ownership，消除共享 dispatcher 的可变 run context。
3. 让 CLI、Interactive、ALFWorld、Coworker 通过同一个 `ApplicationRuntime` 运行。
4. 保持 ALFWorld V1.8 的 identity、frame authority、action accounting、terminal 和 scorer 语义。
5. 保持 Coworker 固定十一项工具、child run、budget、lifecycle、presentation 和 artifact 语义。
6. 在入口完成同步迁移后再异步化 provider/runtime/pipeline。
7. 最终候选使用真实 LLM，对该 SHA 已提交的固定 inventory 和运行前 hash/identity 校验通过的外部
   dataset bytes 完成 ALFWorld 4/4 和 Coworker 1/1。

本轮不以“代码量更少”为成功标准。成功标准是公共控制流只有一份、领域权威 owner 不变化，
且 comparison spec §24 的 gate 可以证明迁移前后契约一致。

### 1.1 执行环境

- 所有代码修改、测试修改、lock 更新和 release manifest 生成只在 HPC2 的
  `/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster` 进行。
- HPC2 non-live gate 通过后提交完整候选并 push 到 GitHub `origin`。`hkust4` 只 fetch 并检出
  同一完整 SHA，不在正式 worktree 手改 tracked 文件，也不用 rsync 覆盖 Git bytes。
- 当前正式 live 分工为：ALFWorld 在具备 preflight hash/identity 锁定的 ALFWorld/THOR 数据的 HPC2
  运行；Coworker 在
  `hkust4` 运行。两边都必须记录相同 HomeMaster release SHA、lock、dataset/manifest 和 provider
  identity；真实 config/key 留在运行机的 gitignored mode-0600 文件中。
- 若在 `hkust4` 运行任何 ALFWorld preflight、复现、pytest、import probe、runner 或 verifier，必须
  使用 `conda run -n hm_alfworld ...`；不得使用裸 `python`、`uv run` 或 Coworker `.venv`。记录
  environment name、Python executable/version、`conda list --explicit` hash，以及 HomeMaster、
  ALFWorld、ai2thor import origins。该约束不改变正式 ALFWorld 4/4 gate 位于 HPC2 的分工。
- 任何 live 失败都回 HPC2 修复并产生新 commit；同步新 SHA 后从 preflight 重跑。不能直接在
  `hkust4` 修补，也不能用旧 artifact 或成功子集拼出最终结果。

### 1.2 每个 Phase 的执行状态机与 subagent review

Phase -1、M0、0A、0B、每个 0C entry、0D1～0D4，以及未来单独执行的 CL-17～CL-21，都必须维护
`plan/V1.9/execution/<phase>/state.json`：

```text
PLANNED -> CONTEXT_LOADED -> SUBGOALS_FROZEN -> IMPLEMENTING -> VERIFYING
        -> SUBAGENT_REVIEW -> REMEDIATING* -> GATE_PASSED
```

- `CONTEXT_LOADED`：主 agent 在该 phase 开始前完整重读 spec/plan 对应板块、当前 owner 源码/tests、
  适用 AGENTS 指令、当前 git diff 和 OpenHarness port manifest/source；不能只沿用上轮摘要。
- `SUBGOALS_FROZEN`：根据当前依赖和 exit criteria 动态拆分子目标，记录 allowed deltas、目标文件、
  V/A/H source ports、测试命令、rollback 与 evidence。源码/依赖漂移时退回 CONTEXT_LOADED。
- `VERIFYING`：执行该 phase 全部静态、unit/integration/domain gate，raw logs 放 `var/`，state 只保存
  exit/hash/opaque evidence handle，不保存 secret/host path。
- `SUBAGENT_REVIEW`：每个 phase 结束必须新开至少一个独立 subagent，结合实际实现 diff、当前项目
  细节、复制的 OpenHarness 源码/tests、测试结果和领域 owner 做 code review，而不是只看计划。
- review finding 逐条记录 file/line、severity、disposition 和复验；correctness/safety/contract 问题未清
  零则进入 REMEDIATING，修后重跑 VERIFYING 和 SUBAGENT_REVIEW。缺 review evidence 不得 GATE_PASSED。

subagent review 是额外 gate，不替代测试、独立 scorer 或 final live verifier。

Phase -1 是唯一 bootstrap 例外：在其 `PLANNED` 状态先写入 schema version、锁定 OpenHarness
repo/commit 和空 `ports` 的 `upstream-port-manifest.json`，随后才能进入 `CONTEXT_LOADED` 并读取它。
CL-01 建立正式 schema/validator 后立即校验这份空清单；M0 及以后不允许 manifest absent 例外。

## 2. 当前代码的关键约束

| 现状 | 风险 | 实施决策 |
|---|---|---|
| `tools/spec.py` 和 `agent/generic_runtime.py` 各有一个不兼容的 `ToolSpec` | manifest 在 `agent/turn.py::_to_tool_specs()` 中丢字段 | 先增加 canonical contract 和 legacy adapter，入口切换完成后删除 runtime-local 类型 |
| `ToolRegistry.register()` 与 `ToolDispatcher.register()` 按名称静默覆盖 | 环境 variant 或插件可以意外替换安全工具 | Catalog 按 stable internal id 注册；重复 id 和同一 ToolView alias 冲突默认失败 |
| `ToolDispatcher._run_context` 是共享可变字段，runtime 多处直接读取 | 并发 session 可串 backend、ToolView、evidence 和 terminal 状态 | pipeline 每次调用显式接收 immutable `ToolExecutionContext`，共享对象不保存 run 状态 |
| dispatcher 只检查 required 字段 | type、enum、nested、additionalProperties 和输出结构未执行 | 使用标准 JSON Schema validator 覆盖输入；只对真实非空 output schema 做输出验证 |
| Home、ALFWorld、Coworker 各自装配 provider/registry/dispatcher/runtime | 修复和治理能力无法复用，入口行为易漂移 | 逐入口迁移到唯一 factory/runtime；每个入口通过 parity gate 后才删本地装配 |
| ALFWorld runner 和动作结果会隐式给模型注图 | 与显式 `observe` 重复，并使 retry/frame authority 模糊 | `observe` gate 通过后同时删除 initial 和 post-action model image 注入，保留审计/录屏图片 |
| Dispatcher 会把普通 result 的 `data.frame_path` 自动转为图片 | 即使 ALF helper 删除仍可绕过显式观察 | 删除通用隐式转换；只有 ObservationService 可创建 model-facing observation content |
| Home/Coworker 当前使用环境专属 observation alias | 三套 prompt/skill/manifest 继续漂移 | model alias 统一为 `observe`；internal id/schema/executor/verifier 仍可分域 |
| TaskSnapshot constraints/evidence/completion 由模型写入 | 自由文本 note/completion claim 可被误当 verifier/scorer 事实 | 明确三层 evidence；completion 必须过 observation/verification gate，不能授权动作或决定 formal success |
| `ContextAssembler.force_compact_next` 是可变字段且 TaskStateStore 从 Dispatcher private context 取 | 长生命周期 application 可能跨 session 压缩或丢 TaskSnapshot | compaction request 归 session/generation；同一 store 显式传给 task tools/context/persistence |
| 当前 CLI 只有 `run --utterance` | 把 `-p`、dry-run、多 renderer 写成兼容会漏掉 app/parser 实现 | 把 top-level callback、`-p`、dry-run、renderer 和 exit codes 作为 V1.9 新契约实施 |
| ALF 内部仍有 `robot_navigate` 兼容字符串 | 全源码禁词会破坏 translator 和历史回归 | 只禁止 model manifest、ToolView、model-facing registry/prompt 暴露旧名 |
| 大量 legacy `output_schema={}` | 强制统一 result schema 会改变现有环境返回 | 空 schema 记录 migration debt，不做伪验证；迁移后的 variant 声明各自真实 schema |

## 3. OpenHarness 复用策略

### 3.1 源码移植模式与精确范围

OpenHarness 是组内可直接使用的代码。所有兼容通用逻辑先复制实际源码和原测试，再适配；禁止只
借控制流描述重新实现。每个 CL 更新 `plan/V1.9/upstream-port-manifest.json`，记录 `V/A/H` mode、
source commit/path/symbol/SHA-256、destination、copied test ids、机械 import delta、HomeMaster delta 和
同步/删除策略。

| Mode / OpenHarness 来源 | 实际复制内容与目标 | HomeMaster delta |
|---|---|---|
| A `tools/base.py` | 复制 `BaseTool`、Pydantic `to_api_schema()`、`ToolRegistry` 到 canonical tool 层；固定 commit 无直接 schema/registry tests，登记 test gap 并新增 characterization tests | 拆 immutable definition/registered executor；stable id；alias conflict fail-fast；typed result |
| A `engine/query.py` | 复制 `_execute_tool_call()`、gather/exception isolation 和 input/permission/single+parallel exception pairing tests 到 pipeline seed | 前置 View/terminal/pre-observation，后置 output/policy verifier/debt/authoritative ledger/public event；删除 coding artifact/hook metadata |
| V/A `services/session_backend.py`、`session_storage.py` | 复制 protocol、per-file atomic save、load/list/export 和 `tests/test_services/test_session_storage.py` 三个现存 node | payload 使用 Home message/TaskState/AgentState/revision/generation；expected-revision CAS、writer lock、revision-first/commit-pointer-last、crash/concurrent-writer gate；排除 live resources |
| A `engine/stream_events.py` | 复制 DTO/union 和 consumer branch tests 到 Home public stream adapter | 映射 RuntimeEvent/PublicEventProjection；domain ledger 先持久化 |
| A `cli.py`、`tests/test_commands/test_cli.py` | 复制 `_build_dry_run_preview()`、相关 formatter 和 dry-run/no-REPL/json/error tests | 接 RunRequest；删除 coding commands；无 `--probe` 时零外部 I/O |
| V/A `skills/` | 整文件复制 `_frontmatter.py` 及 parser tests；复制 discovery/precedence/security 相关 loader/registry 源码/tests | SkillSpec/provenance/path containment/capability gate |
| V/A `mcp/types.py`、`mcp/client.py` | 复制 types/client/fake server 与 stdio/HTTP/error tests | application ownership、redaction、rollback、完整 raw JSON Schema；WebSocket unsupported |
| A `permissions/{modes,checker}.py` | 复制规则实现与 allow/deny tests | typed principal、robot capability/device policy；不信任 metadata |
| A `channels/`、`ohmo/gateway/` | 复制 DTO/base/router/bus/bridge 与 routing/progress/media/cancel tests | bounded priority、tenant identity、generation fencing、PublicEventProjection；不复制 per-session QueryEngine owner |
| V/A `hooks/`、`plugins/` | 复制 events/types/schemas/loader/executor 和相关 manifest/lifecycle tests | capability/import isolation、atomic generation reload；hook 不做 safety owner |

存在直接上游测试时，原断言必须先通过，再追加 HomeMaster delta tests。锁定 commit 没有直接测试的
symbol 仍复制实际源码，但 `copied_test_ids=[]`，manifest 必须写 `upstream_test_gap`、search evidence
和新增 characterization test ids，不能编造 node id。Channel 已记录的 nanobot 二级 provenance chain
原样保留到 notices/port manifest；这不是改成“只参考行为”的理由。

### 3.2 不复制的部分

- 不复制 OpenHarness `QueryEngine/run_query` 整体；其 coding-agent message carryover、artifact 和
  hook 顺序不是 HomeMaster 领域契约。
- `ToolRegistry` 可作为 A-mode seed 复制，但同一 CL 必须删除其静默覆盖语义，未改成 stable-id/
  alias-conflict fail-fast 前不得合并。
- 不复制 `ui/runtime.py` 的 `build_runtime/start_runtime/close_runtime` 作为 HomeMaster lifecycle；
  HomeMaster 需要严格的部分初始化逆序回滚和 borrowed resource 规则。
- 不把 OpenHarness 权限 metadata、cwd/path 语义直接套给机器人动作。
- 不把 coding tool 的文本 `ToolResult` 作为 HomeMaster canonical result；图片、observation、
  terminal、classification 和 evidence 必须无损保留。
- 不在 Phase 0 同时引入 Skills、MCP、Gateway、Hooks 和 Plugins，避免入口迁移失去可定位的对照点。

保持 HomeMaster 权威的 H-mode owner 包括 Catalog/View 的严格语义、typed result/context 领域字段、
pipeline terminal/resource/verifier/evidence stages、ObservationService、ApplicationRuntime/AgentRuntime、
ResourceScope/SessionManager、ALF/Coworker scorer/ledger/artifact、MCP 保真 schema、device lease/e-stop、
bounded priority bus 和 atomic extension reload；这些 owner 应调用已移植 V/A 叶子代码。

## 4. 目标模块与依赖方向

建议新增模块：

```text
src/homemaster/tools/contracts.py
src/homemaster/tools/catalog.py
src/homemaster/tools/pipeline.py
src/homemaster/tools/legacy_adapter.py
src/homemaster/observations/service.py

src/homemaster/application/contracts.py
src/homemaster/application/resources.py
src/homemaster/application/session_manager.py
src/homemaster/application/runtime.py
src/homemaster/application/factory.py
src/homemaster/events/bus.py

src/homemaster/adapters/alfworld/tools.py
src/homemaster/adapters/coworker/tools.py

scripts/v19_release/build_alfworld_trials.py
scripts/v19_release/verify_alfworld_trials.py
scripts/v19_release/capture_environment_identity.py
scripts/v19_release/run_alfworld.py
scripts/v19_release/verify_alfworld_release.py
scripts/v19_release/run_coworker.py
scripts/v19_release/verify_coworker_release.py
scripts/v19_release/merge_release_reports.py

tests/homemaster/benchmarking/test_alfworld_v19_release_manifest.py
```

依赖方向固定为：

```text
CLI / Interactive / Benchmark controllers / future Gateway
                         |
                         v
                 ApplicationRuntime
             /            |             \
     SessionManager   AgentRuntime   RunResourceScope
                              |
                              v
                   ToolExecutionPipeline
                 /           |            \
          ToolView   ObservationService   Policies
                 \           |            /
                    environment adapters
                              |
                    borrowed/live backends
```

`agent/`、`tools/`、`application/` 不得反向 import `benchmarking/alfworld` 或
`benchmarking/coworker_demo`。环境-specific schema、executor、verifier 可以不同，由 composition
root 注册；benchmark controller 继续拥有环境启动、reset、推进、finalize、评分和 artifact。

### 4.1 当前文件到变更单的精确映射

| 当前文件 | 实施批次与目标 | 兼容代码删除 gate |
|---|---|---|
| `src/homemaster/tools/spec.py` | CL-02/04：适配到 `tools/contracts.py` | CL-15，所有入口不再产生 legacy `ToolSpec` 且 contract/result parity 通过 |
| `src/homemaster/tools/results.py` | CL-02/04：无损映射到 canonical result | CL-15；若仍被领域代码直接消费则保留为薄 facade，不强删 |
| `src/homemaster/tools/registry.py` | CL-03/04：旧 registry 输出经 Catalog/View adapter | CL-15，四入口 AST guard 均证明不再构造旧 registry |
| `src/homemaster/tools/dispatcher.py` | CL-04/05/07：observer 语义迁入 stateless pipeline；删除 `frame_path -> image` 隐式转换 | CL-15，删除 `set_run_context()` 和 `_run_context` 前须过 observer/terminal/action-accounting/concurrency gate |
| `src/homemaster/agent/generic_runtime.py` | CL-10：收敛为 `AgentRuntime`，显式接收 ToolView/context | CL-15 删除 runtime-local `ToolSpec`；`GenericAgentRuntime` alias 在全部外部 import 迁移并过 deprecation gate 后删 |
| `src/homemaster/agent/turn.py` | CL-11/12：`run_single_turn/run_agent_turn/compact_agent_context` 变为 ApplicationRuntime thin wrappers | 对应 CLI/Interactive parity 通过后删内部 assembly；wrapper 仅在 CLI compatibility version 到点后删除 |
| `src/homemaster/cli/app.py`、`run_command.py`、新 renderer | CL-11：实现 top-level default/`-p`/dry-run/output contract，只创建 `RunRequest` | 新契约 tests、现有 `run --utterance` compatibility、exit code/provider identity/Home evidence 通过后删旧分支 |
| `src/homemaster/cli/interactive_shell.py` | CL-12：复用长生命周期 application，调用 `run/compact/cancel/status` | resume/compact/interrupt parity 通过后删除直接 `agent.turn` assembly 依赖 |
| `src/homemaster/providers/attempts.py` | CL-10/CL-16a：保留为 frozen request/attempt 权威 ledger | 不删除；只有 schema owner 明确迁入 provider package 且 hash/commit-order gate 通过才可重命名 |
| `src/homemaster/benchmarking/alfworld/runner.py` | CL-13：替换 single episode 与 taskset 两处 runtime assembly，保留 benchmark lifecycle/scorer | §24.8 non-live+live gate 通过后删除本地 Provider/Dispatcher/Runtime 构造；runner 本身不删除 |
| `src/homemaster/benchmarking/alfworld/registry.py` | CL-07/13/15：model-facing definitions 迁到 `adapters/alfworld/tools.py` | ALF manifest/ToolView/disabled-call gate 通过且 runner 已切换后删除 |
| `src/homemaster/benchmarking/alfworld/tools.py` | CL-06/07/15：保留领域 executor/verifier，改由 adapter 注册；去掉动作结果 model image | 显式 `observe`、frame authority、action/evidence parity 通过后删除 `_visual_tool_result()` 隐式附图路径 |
| `src/homemaster/benchmarking/alfworld/prompt.py`、`src/homemaster/prompts/agent_system_prompt.md` | CL-07：删除“动作结果自动带图”承诺，要求显式 `observe` | no-implicit-media 和 transcript tests 通过后切换 |
| `src/homemaster/benchmarking/alfworld/model_view.py` | CL-06/10/13：继续作为 ALF frame authority | 不删除；ApplicationRuntime 只能调用其公开 observer/commit seam |
| `src/homemaster/benchmarking/alfworld/env_adapter.py` | CL-07/13：继续拥有 translator/grounding/backend adapter | execution-only `robot_navigate` 在历史 trace/低层 fixture 迁移完成后删；不能用 model-facing guard 提前强删 |
| `src/homemaster/benchmarking/coworker_demo/turn.py` | CL-14：只替换 `_run_runtime()` assembly，保留 `run_coworker_turn()` lifecycle | §24.9 normal/anomaly non-live 与唯一 item live gate 通过后删除 `_make_coworker_runtime` 和旧 `_run_runtime` 装配 |
| `src/homemaster/benchmarking/coworker_demo/registry.py` | CL-07/14/15：exact 11-tool profile 迁到 `adapters/coworker/tools.py` | ordered manifest 和伪造 disabled-call gate 通过后删除 builder；`EXPECTED_COWORKER_TOOLS` contract 可移到 adapter 测试 |
| `src/homemaster/benchmarking/coworker_demo/{browser_tools,browser_driver}.py`、prompts/skills | CL-07/14：model alias 统一 `observe`；navigate receipt-only；legacy backend/ledger operation 只在 adapter 内映射 | manifest/result/skill 无旧 alias；explicit observe flow、TICKET_READ 和 formal scorer tests 通过 |
| `src/homemaster/benchmarking/coworker_demo/presentation.py`、`types.py` | CL-14：保留 public trust boundary、artifact/formal types | 不删除；仅在保持公开投影和 formal artifact schema 的前提下调整 import |
| `data/coworker_demo/case_02/agent_trajectory_ground_truth.yaml`、episode/scoring/API/presentation fixtures | CL-14：`TICKET_READ` evidence owner 从 navigate 改为显式 `observe` | node id/order/dependencies、其余 23 nodes、14 checkpoints/weights 不变；新 ground-truth hash 经 formal verifier 后冻结 |
| `src/homemaster/task_state/{models,store,tools}.py`、`agent/context.py` | CL-05/09/10：明确 model-owned trust，completion gate，同一 store 显式 wiring | TaskSnapshot/context/persistence/trust tests 通过后删除 private context 读取 |
| `scripts/coworker_demo/preflight.py` | CL-14：同时校验 provider 与 Coworker config mode 0600；release runner 输出 fresh run pointer | machine release verifier 非零失败 gate 通过后替代 shell 人工解析 |

## 5. 核心接口先行决策

### 5.1 Tool contract

`ToolDefinition` 只包含 stable internal id、model alias、description、raw input/output schema、
provenance、version、effects/resource 与 typed verification policy；它创建后不可变、可序列化且不含
executor。`RegisteredTool` 组合 definition、executor 与 optional verifier；后两者不进入 provider
manifest 或持久化 snapshot。

`ToolExecutionResult.status` 使用 typed enum，并对 status/error/retryability/outcome certainty/terminal/
verification 的组合 fail-fast。结果必须能无损表达：

- text 和 structured data；
- image、attachment 和 observation reference；
- typed failure、`is_error` 和 retryability；
- terminal/classification/score eligibility；
- evidence references 和 backend outcome certainty。

`ToolExecutionContext` 是每次 call 的显式值对象，至少绑定 session/run/tool call、frozen
ToolView、backend、deadline/cancellation、permission subject、observation 和 domain observer。
具有授权意义的字段不能从自由格式 metadata 读取。

### 5.2 Catalog 与 ToolView

`ToolCatalog` 是 application 级，只按 stable id 保存 definitions。Home 和 ALFWorld 可以分别注册
`home.robot_go_to.v1` 与 `alfworld.robot_go_to.v1`，二者都映射 model alias `robot_go_to`。

每个 run 通过 ordered enabled ids 冻结 immutable `ToolView`。同一 view 内 model alias 冲突
fail-fast；未知 id 是 `unknown_tool`，catalog 中存在但当前 view 未启用的是 `tool_disabled`。
disabled tool 不出现在 provider request 中，伪造 tool call 也不能绕过执行层检查。

### 5.3 Validation 与 deadline

HomeMaster 现有 schema 是 raw JSON Schema dict，不能只依赖 Pydantic model。CL-05 推荐加入
`jsonschema` 的稳定 runtime dependency，并在 definition 注册时 `check_schema()`，在调用时执行
Draft 2020-12 validation。若 provider 只支持 schema 子集，provider projection 和执行边界仍以
canonical schema 为源，不能维护第二份手写 schema。

空 `output_schema` 仅代表 legacy 未迁移，不代表“任何输出都已验证”。pipeline 对非空 schema
执行验证；新增和完成迁移的 observation/mutating variants 必须提供真实 schema。

同步兼容阶段的 deadline 是 cooperative contract：可以在执行前、后和 backend 支持的 timeout
点取消，但不能宣称能强制终止已经进入物理设备的同步动作。超时后无法确认结果的 mutating
action 返回 `outcome_unknown`，不自动重试；原生 async 或 backend cancellation 在 CL-16b 落地。

### 5.4 Observation 与 verification policy

模型 observation 只能由当前 ToolView 的 `observe` 触发。Catalog 注册 `home.observe.v1`、
`alfworld.observe.v1`、`coworker.observe.v1`，每个 view 只启用一个，三者 schema/media/executor/
verifier 可以不同。最终 manifest/prompt/skill/model result 不出现 legacy alias；Coworker 有序十一项的
第五项改为 `observe`。

`ObservationService` 记录 observation id、internal tool/backend id、run generation、backend state
sequence、capture-event sequence、media type、必填 content SHA-256、仅 raster 必填的 pixel SHA-256、evidence ref 和 provider binding。Home state/
Coworker canonical DOM 只有 content hash。provider retry 复用 frozen request/bytes，不 recapture。
导航/操作 backend 可天然取得 post-action frame 并写 internal receipt/evidence；verifier、录屏或
artifact checker 也可 `capture_for_audit()`，但这些路径不能产生 model content/provider binding 或
清除 debt。只有模型显式调用 `observe` 才建立 model-visible ObservationRecord。

每个工具定义 `execution_proof`、`requires_pre_observation`、`post_action_observation` 和 `terminal_rule`。
视觉动作在 backend state advance 后产生 debt，必须由同 backend/run/generation、capture event 晚于
action completion 且 source state >= action post-state 的显式 `observe` 清除；
结构化工具可用 typed receipt/external state；不需 verifier 的工具只能算 model judgment。ALF 初始无图，
bootstrap observe 无需 committed view，观察进入下一 successful frozen request 后才授权 mutation；同一
assistant response 的 observe+mutation 拒绝，动作后旧 view 立即失效。

## 6. 变更单总览

每个 CL 应独立可 review、可回滚；除明确的入口切换 CL 外，不同时删除旧路径。禁止对 mutating
环境同时执行 old/new 双写。对照使用 normalized manifest/result、recorded input、fresh episode
和领域 ledger。

| CL | 交付 | 依赖 | 合并 gate |
|---|---|---|---|
| CL-01 | 基线、fixtures、characterization | 无 | §24.2，生产代码零变化 |
| M0 | ALF live runtime qualification + deterministic four-trial lock | CL-01 | provider attempt > 0、reset/capture/scorer canary、manifest validator |
| CL-02 | Canonical tool contracts | M0 | contract 单测，legacy suite 不变 |
| CL-03 | ToolCatalog / ToolView | CL-02 | collision/disabled/order tests |
| CL-04 | Legacy adapters | CL-02/03 | 现有三类 result/observer 保真 |
| CL-05 | Execution pipeline | CL-02/03/04 | 完整顺序、失败、锁、deadline gate |
| CL-06 | ObservationService | CL-02/05 | hash/binding/retry tests |
| CL-07 | Home/ALF/Coworker profiles | CL-03/04/06 | 三环境 manifest/observation parity |
| CL-08 | Application contracts/resource scope | CL-05/06/07 | rollback 和 owned/borrowed matrix |
| CL-09 | SessionManager | CL-08 | isolation/lock/generation/snapshot tests |
| CL-10 | Unified ApplicationRuntime | CL-08/09 | fake entry smoke，旧入口仍可用 |
| CL-11 | Home one-shot CLI 迁移 | CL-10 | CLI/Home parity |
| CL-12 | Interactive/compact 迁移 | CL-11 | resume/compact/cancel parity |
| CL-13 | ALFWorld 迁移 | CL-12 + M0 explicit gate | §24.8 non-live + 候选 live；最终 4/4 重跑 |
| CL-14 | Coworker 迁移 | CL-13 | §24.9 non-live + 候选 live；最终唯一 item 重跑 |
| CL-15 | Adapter ownership 清理 | CL-13/14 | import/AST boundary gate |
| CL-16a | Provider/event async | CL-15 | frozen request/attempt 与 event parity |
| CL-16b | Agent/pipeline async cancellation | CL-16a | pairing/deadline/resource gate |
| CL-16c | Coworker Playwright adapter | CL-16b | thread-affinity/browser lifecycle |
| CL-16d | Stress/generation/leak | CL-16c | concurrency/backpressure/no-leak 与全部 parity |
| CL-17 | Skills/config sources | CL-16d | source/provenance/path/capability/package-data gate |
| CL-18 | MCP stdio/HTTP | CL-17 | protocol/schema/redaction/cleanup gate |
| CL-19 | Permission/auth/device foundations | CL-18 | authorization/lease/emergency-stop gate |
| CL-20 | Gateway/channel/public projection | CL-19 | routing/backpressure/generation/recovery/security gate |
| CL-21 | Hooks/plugins | CL-20 | isolation/version/capability/hot-reload gate |

## 7. 详细实施步骤

### CL-01：冻结行为基线

改动：

- 修复 lock/interpreter/dev extras，使 non-live tests 可以完整 collection。
- 注册 `live_mcp`、`live_coworker`、`stress` pytest markers。
- 增加可重复的 baseline capture script，输出 comparison spec §24.2 规定的 manifest/hash。
- 补 Home CLI/Interactive、ALFWorld V1.8 和 Coworker characterization tests。
- 新增 `scripts/v19_release/build_alfworld_trials.py` 与 `verify_alfworld_trials.py`。source inventory 固定
  为提交的 `config/alfworld_v18_regression_trials.json` 十条，并直接复用现有
  `benchmarking.alfworld.trial_selection` 的 portable path/hash/scene/goal validator；固定
  `sha256-rank-v1`、seed `homemaster-v1.9-release`，hash
  `seed + NUL + canonical_json(trial_id, trial_sha256, goal_fingerprint, expected_logical_scene)`，按
  `(rank_digest, trial_id)` 升序取前四。
  输出保存 source hash/algorithm/seed/source rank/rank digest，验证唯一性、dataset path、
  scene/goal fingerprint；任何 V1.9 live 结果产生前锁定，不允许按成功结果换 trial。
- builder input/output schema 禁止 success/classification/score；新增 manifest tests 覆盖 determinism、
  source/hash/extra-field drift、少于/多于四条、unsafe path 和 dataset identity。
- 增加 `scripts/v19_release/capture_environment_identity.py`，记录 HPC2/HKUST4 HomeMaster SHA、
  ALFWorld root/config/trial hashes、Coworker dataset manifest/declared hashes、provider/model identity；
  在 `hkust4` 产生的 ALFWorld 预检/复现 identity 还记录 Conda environment name、Python
  executable/version、`conda list --explicit` 输出 SHA-256 和 HomeMaster/ALFWorld/ai2thor 实际
  import origin。HPC2 仍记录其实际 Python/import/runtime identity，但不由本条强制改用 Conda。
- baseline 只 canonicalize 不稳定字段，不写 secret、绝对主机路径或大 artifact。
- 把 Phase -1 `PLANNED` 时创建的 bootstrap `upstream-port-manifest.json` 提升为正式 schema/validator；
  后续每个 V/A CL 必须更新并验证 source hash、真实 copied test ids 和本地 delta。无直接上游测试时
  强制 `copied_test_ids=[]`，并校验 `upstream_test_gap`、search evidence 和新增 characterization ids。

OpenHarness 复用：复制需要沿用的 baseline fixture/canonicalization helpers 和原测试时也登记 manifest；
本 CL 不复制 production runtime。

退出条件：固定 lock 下 compile/Ruff/non-live test exit 0；`plan/V1.9/baseline/` 产物绑定两仓
完整 commit；4-row ALF manifest 和 Coworker 唯一 test-set item 的 hash guard 通过。回滚点是
`5b150a9` 的生产行为，未通过前不得开始删除任何旧 registry/入口。

### M0：ALFWorld live runtime qualification

改动：

- pin 并记录 ALFWorld/ai2thor/Unity/dataset root、`visual_eval` config、provider/model、Python/import
  origin、display/GPU 与 reset fingerprint。
- 用真实 LLM 跑一个 fresh canary，证明 Provider attempts > 0、`AlfredThorEnv` 可
  start/reset/scan/restore、显式 capture adapter 可读取 frame 且 formal scorer 可运行。
- 构建并验证四条 release manifest；M0 不要求任务成功，不作为 4/4 证据。
- M0 权威运行在 HPC2；若在 `hkust4` 交叉复现，每一个 ALFWorld 命令都以
  `conda run -n hm_alfworld ...` 启动，并验证 environment name、Python executable/version、
  explicit package-list hash 和 HomeMaster/ALFWorld/ai2thor import origins 后才能运行 canary。

退出条件：runtime qualification JSON 与 four-trial source/algorithm/rank hashes 进入候选 evidence；
provider=0、reset 失败或 scorer unavailable 时先修基础设施，不进入架构入口迁移。当前 README 的
较新结果是 1/10 success、5 score eligible、coverage 0.5、formal score unavailable，因此最终 4/4
单列为产品质量 gate，不能描述成 migration parity。

### CL-02：Canonical contracts

改动：

- 新增 `tools/contracts.py`，定义 immutable serializable `ToolDefinition`、`RegisteredTool`、typed-status
  `ToolExecutionResult`、`ToolExecutionContext`、executor/verifier protocols 和 legal result combinations。
- 保留现有 `ContentBlock` 能力，明确 canonical result 到 provider message 的无损转换。
- stable id、provenance、version 和 schema 在构造时校验。
- runtime-local `agent.generic_runtime.ToolSpec` 暂时保留，只允许经 CL-04 adapter 使用。

OpenHarness 复用（A）：实际复制 `tools/base.py::BaseTool/to_api_schema()`，再拆 definition/registered
executor 并扩展 HomeMaster result/context。固定 commit 没有直接 schema tests，因此 port manifest
记录 `copied_test_ids=[]`、`upstream_test_gap` 和可复核的 `git grep` evidence；本 CL 新增同步的
HomeMaster schema characterization tests。禁止凭描述重写或伪造 copied node id。

退出条件：contracts 不 import benchmark adapters；非法 result 组合 fail-fast；现有测试零行为漂移。

### CL-03：ToolCatalog 与 immutable ToolView

改动：

- 新增 application-level Catalog，以 stable id 注册，不以 model alias 作为唯一键。
- 实现 ordered ToolView freeze、provider manifest projection 和 execution lookup。
- 重复 stable id、无授权 override、同 view alias 冲突在 startup/freeze 时失败并报告双方 provenance。
- execution lookup 区分 `unknown_tool` 与 `tool_disabled`。

OpenHarness 复用（A）：复制 `tools/base.py::ToolRegistry` 作为 seed，同一 CL 立即替换静默 name 覆盖
为 stable id + freeze-time alias conflict。固定 commit 没有直接 registry/list tests，manifest 按 test-gap
规则登记；本 CL 新增同步的 order/get/collision/disabled-call characterization 和 Home delta tests。

退出条件：Home/ALF 同 alias variant 可同时注册但不能进入同一 view；manifest 顺序稳定；并发 run
冻结的 views 互不影响。

### CL-04：Legacy adapter

改动：

- 新增 `tools/legacy_adapter.py`，适配当前 `tools.spec.ToolSpec`、runtime-local `ToolSpec`、
  `ToolResult`、`ToolResultMessage` 和 dict result。
- 保留 `ToolDispatchObserver` 的 before/after/exception 顺序、tool call id 和 terminal fencing。
- adapter 对空 output schema 记录 debt，不制造通用 schema；对 lossy conversion 加显式测试。
- 保持旧 `ToolRegistry`、`ToolDispatcher` 和所有旧入口可运行。

退出条件：同一 recorded tool call 的 legacy 和 canonical normalized result 等价；exception 和
terminal 路径仍产生合法 tool-use/result pairing。

### CL-05：ToolExecutionPipeline

改动：

- 新增无 session 状态的 pipeline，顺序固定为：view/terminal gate -> input validation -> permission
  -> cancellation/deadline -> pre-observation gate -> resource lock -> execute -> result validation ->
  policy-applicable verifier -> post-action observation debt -> authoritative evidence/domain ledger -> public event。
- 加入标准 JSON Schema validator；definition 注册时检查 schema，自定义 format 必须显式启用。
- 加入 `AllowAllPermissionPolicy`，即使默认 allow 也必须被调用并留下 decision evidence。
- 资源键由 definition/context 的 typed 字段计算，不接受模型 metadata 覆盖。
- sibling tool exception 必须各自转 result；并发前按资源冲突策略分组。
- mutating `outcome_unknown` 不自动 retry，read/idempotent retry 受总 deadline 限制。
- 执行 typed verification policy：pre-observation gate、post-backend observation debt、structured/
  external proof 与 terminal owner；有 debt 时 model/task completion 返回 `observation_required`，
  verifier 不持有 model capture capability。
- 在根 `pyproject.toml` 增加 `jsonschema` runtime dependency，并在同一 CL 更新根 `uv.lock`；
  validator 不得依赖开发环境偶然安装的传递依赖。
- 本 CL 的第一步是在 dev extra 增加 Python 3.11/pytest 兼容的 `pytest-asyncio`、设置显式
  `asyncio_mode` 并更新 lock；随后才复制/运行 async query tests。CL-02/03 的新 schema/registry
  characterization tests 是同步测试，不能把它们伪报成上游 async tests，也不能把 runner 依赖推迟到
  CL-16a。
- `ToolExecutionPipeline.execute()` 从本 CL 起使用复制来的 async core。legacy 同步入口只经唯一
  `SyncPipelineBridge` 调用，sync executor 包装成 awaitable；bridge 只能在无 running event loop 的
  兼容入口运行，不得形成第二套 pipeline/control flow。

OpenHarness 复用（A）：复制 `engine/query.py::_execute_tool_call()`、gather/exception-isolation 实际源码，
  以及锁定 commit 中真实存在的 input/permission 与 single/parallel exception pairing node。至少登记
`test_execute_tool_call_blocks_sensitive_directory_roots`、
`test_execute_tool_call_applies_path_rules_to_directory_roots`、
`test_execute_tool_call_returns_actionable_reason_when_user_denies_confirmation`、
`test_query_engine_synthesizes_tool_result_when_single_tool_raises` 和
`test_query_engine_synthesizes_tool_result_when_parallel_tool_raises`。先通过机械移植断言，再前置
View/terminal/pre-observation、后置 output/policy verifier/debt/authoritative ledger/public event；不得从
文字重写这段控制流。

退出条件：§24.5 顺序和“未调用”断言全部通过；删除 pipeline 中对
`ToolDispatcher._run_context` 的任何依赖。

### CL-06：ObservationService

改动：

- 新增媒体无关 observation record、capture protocol、backend/run generation/state sequence/
  capture-event sequence/freshness 校验和 evidence binding；content SHA-256 始终必填，pixel SHA-256
  仅 raster 必填。
- 扩展 `OutboundImageBinding`，新增 optional observation metadata，同时保留现有 ALF 字段。
- provider request freeze 时记录 observation/image hashes；retry 直接复用 frozen payload bytes。
- capture、serialization、binding 分开测试，禁止 retry 再次调用 capture。
- action-native frame 与 model observation 分开：前者可写 internal evidence，不能生成 ContentBlock；
  后续显式 `observe` 即使读取相同 current-frame bytes，也必须生成新的 explicit capture record。

退出条件：同一 retry chain 的 request SHA-256、observation bytes/content hash 完全一致；raster pixel
hash 不变、structured pixel hash 为 null；foreign/stale/wrong-generation/wrong capture ordering 在
backend action 前拒绝。

### CL-07：环境 Tool profiles

改动：

- Home：模型正式导航名迁为 `robot_go_to`；`robot_navigate` 仅保留 execution-only compatibility
  wrapper，声明 owner 和删除版本；内置 skill/prompt 同步使用正式名。
- Home/ALFWorld/Coworker 分别注册 `home.observe.v1`、`alfworld.observe.v1`、
  `coworker.observe.v1`，model alias 均为 `observe`；每个 profile 恰好启用一个。
- ALFWorld：导航只暴露 `robot_go_to`，保留 manipulation、verify、task 等现有工具。
- Coworker：ToolView 顺序严格保持
  `task_planner, task_progress_check, skill_view, browser_navigate, observe, browser_click,
  browser_fill, browser_select, browser_wait, terminal_execute, sop_decide`。
- 三个 observation variants 都调用 CL-06 service，但保留环境-specific schema/executor/verifier。
- legacy execution/backend adapter 可识别旧 observation operation，model-facing manifest/registry/
  prompt/skill/result name 不得出现旧 alias。
- 显式 observe 和 model-view gate 通过后，删除 ALF initial 以及 navigate/manipulate/verify result 的
  model image blocks；导航/操作仍可接收 native post-action frame 并写 internal receipt/evidence，
  录屏、审计和 artifact capture 不删除。视觉验证一律等待后续显式 `observe`。
- 删除 Dispatcher 对普通 `data.frame_path` 的自动 image conversion；更新 ALF/system prompt，不再承诺
  action result 带图。ALF bootstrap observe 可在无 committed view 时运行；同 response observe+action
  拒绝，backend advance 后旧 view 失效。

退出条件：allowed deltas 仅为 spec §24.7 所列内容；Coworker 不增加第十二项；provider retry 不
换帧；旧 ALF action count/scorer input 不变化。

### CL-08：Application contracts 与 ResourceScope

改动：

- 新增 typed `RunRequest`、`RunResult`、`RunPolicy`、`TerminalPolicy`、resource ownership/binding。
- 新增 `RunResourceScope`：每次 acquire 成功立即注册 cleanup，初始化失败逆序回滚。
- resource 明确 `owned` 或 `borrowed`；borrowed backend/browser/environment 永不由 runtime close。
- close 一个资源失败时继续关闭其余资源，并聚合报告，不覆盖主异常。

OpenHarness 复用模式 H：Application/ResourceScope 保持 HomeMaster owner；`ui/runtime.py` 没有满足
partial-init/borrowed 语义的兼容块，因此不复制该整体，直接调用已移植的 tool/session/event 叶子模块。

退出条件：§24.6 resource failure matrix 全通过，所有 counter/lease 归零，borrowed close count 为 0。

### CL-09：SessionManager

改动：

- SessionManager 持有 `AgentSession`、`AgentState`、`TaskStateStore`、per-session turn lock、
  generation、active task 和 cancellation source。
- 默认普通 episode/Coworker child run 创建新 session；resume 必须传显式 session id；只有明确
  连续 ALF taskset policy 才共享。
- snapshot 保存消息、完整 model-owned TaskState、AgentState 和另行验证的 canonical evidence refs；
  `TaskSubtask.evidence` 仍是非权威字符串 note，不能称为稳定 ref。不得保存 backend、provider client、
  ToolView、browser、MCP 或 robot connection。
- process restart 后 resume 或 active backend rebind 时，环境/视觉 profile 一律从 `NEEDS_OBSERVE`
  开始；旧 observation id/hash/sequence/model-view binding 只作审计，不能授权动作、清 debt 或完成任务。
  新观察必须绑定当前 run/backend 并进入新的 successful frozen request。
- generation fencing 覆盖 message、snapshot、domain projection 和 final result 写回。
- 同一 session 的 task tools、ContextAssembler 和 persistence 显式接收 exact same TaskStateStore；每个
  model iteration 重新投影，provider retry 不重组。新增独立 revision/CAS，不复用只在 plan replace
  时变化的 `snapshot_id`；TaskStatus、AgentRunStatus、benchmark classification 分开。
- 定义 TaskStatus transition table；schema 声明的 active/paused/completed/failed/cancelled 要么执行要么
  typed reject，不能继续只处理 completed 而静默忽略其他值。保持 interrupt active->paused、resume
  paused->active 的明确契约，并区分 session turn index、model iteration、plan revision。
- compaction request/`force_compact_next` 归 session/generation，不能放 application-shared assembler。

OpenHarness 复用（V/A）：复制 `services/session_backend.py` protocol、`session_storage.py` 的 per-file
`atomic_write_text` save 与 load/list/export 控制流，以及现存
`test_save_and_load_session_snapshot`、`test_export_session_markdown`、
`test_load_session_snapshot_sanitizes_legacy_empty_assistant_messages`。上游会先后写 `latest.json` 和
named snapshot，并不提供跨文件 transaction/CAS；HomeMaster snapshot 增加 revision/generation、
expected-revision compare 和单写者锁，先写 immutable revision snapshot，fsync 后再原子发布
commit/latest pointer。加入 concurrent-writer 与两个写入边界的 crash injection tests，确保 load 只看见
完整旧/新 revision。继续保留 TaskState/evidence，明确排除 active resource，也不复制 OpenHarness 的
自由格式 tool metadata 白名单作为 HomeMaster 权威 session schema。

退出条件：同 session 串行、不同 session 可并发；旧 generation 即使吞掉 cancellation 后返回也
不能写状态；现有 compaction/interruption/TaskSnapshot tests 继续通过。

### CL-10：统一 ApplicationRuntime

改动：

- 新增唯一 composition root `application/factory.py`，创建 application-level Catalog、pipeline、
  ObservationService、EventBus、SessionManager 和 provider factory。
- `ApplicationRuntime` 提供 `run(RunRequest)`、`compact(session_id)`、`cancel(session_id)`、
  `status(session_id)`；控制操作不伪装成用户消息。
- `AgentRuntime` 专注 model loop，ToolView/context 每次显式传入；临时保留
  `GenericAgentRuntime = AgentRuntime` compatibility alias。
- provider retry 继续严格比较 canonical request SHA-256；仅对 policy 声明
  `requires_pre_observation=current_bound` 的工具，successful attempt 的 model view 必须在 dispatch 前
  commit。bootstrap `observe` 是唯一无 committed view 例外；其 read-only capture 不计为 ALF model
  backend action，但仍经过 view/terminal/validation/permission/cancel/deadline/ledger gate。
- TaskSnapshot completion claim 在 observation debt、policy 要求的 verifier 未满足，或且仅当
  `terminal_rule=external_terminal_owner` 时外部终态未成功的情况下拒绝；普通 Home profile 按自身
  policy 完成。model constraints/evidence note 不参与 permission、action authorization 或 formal scorer。

退出条件：fake entry smoke 通过，所有入口尚未切换时旧路径仍工作；共享 application 上并发 run
不出现 backend/ToolView/observation/cancellation 泄漏。

### CL-11：Home one-shot CLI 迁移

改动：

- 修改 `cli/app.py` top-level callback、`cli/run_command.py` 和独立 renderer：实现 V1.9 新增的默认
  interactive、`-p`、`--dry-run`、resume 与 text/json/stream-json；现有 `run --utterance` 保留 wrapper。
- `agent/turn.py::run_single_turn/run_agent_turn` 先变为 thin wrapper；移除其中每 turn 重建
  provider/registry/dispatcher/runtime 的路径。
- 定义各 renderer envelope 和错误 exit code，不把尚不存在的格式写成旧兼容；保持 provider identity
  和 Home task/evidence 行为。
- dry-run 通过同一 profile/config resolver 生成预览，但不创建 ApplicationRuntime 外部连接；只有
  显式 `--probe` 才能做 MCP discovery，并清楚标记它会产生外部 I/O。

OpenHarness 复用（A）：复制 `cli.py::_build_dry_run_preview()`、相关 formatter 源码及
`tests/test_commands/test_cli.py` 的 dry-run/no-REPL/json/error tests，再接 RunRequest；删除 coding
slash/autopilot/provider-admin 分支，不复制整个 CLI composition root。

退出条件：old/new normalized CLI golden 只允许 comparison spec §24.7
`tests/homemaster/fixtures/v19/allowed_deltas.json` 中逐项批准的差异；wrapper 自己不再装配 runtime。
回滚只切回该入口 commit，不复用已经执行过动作的同一个 backend run 做 fallback。

### CL-12：Interactive 与 compact 迁移

改动：

- `cli/interactive_shell.py` 复用同一个长生命周期 ApplicationRuntime。
- turn 走 `run()`，手工压缩走 `compact()`，取消和状态查询走 typed methods。
- `compact()` 原子持久化新 messages/snapshot revision；覆盖 compact -> process restart -> resume，不能
  沿用当前仅修改内存的 shell 行为。
- interactive shutdown 只关闭 owned application resources，不关闭当前借入的 benchmark/backend。

退出条件：session resume、manual compact、interrupt、history pairing 和 renderer parity 通过；多 turn
不重复创建 application 级 MCP/Catalog/provider manager。

### CL-13：ALFWorld 入口迁移

改动：

- 仅替换 `benchmarking/alfworld/runner.py` 中两处 provider/dispatcher/registry/runtime assembly，
  active backend 作为 borrowed binding 传给 `ApplicationRuntime.run()`。
- runner 继续拥有 runtime identity pin、start/reset/scan/restore transaction、goal advance、
  OracleActionGateway、action accounting、classification、cleanup/quarantine、scoring 和 artifacts。
- 保留 `providers/attempts.py` 与 `alfworld/model_view.py` 的权威关系：只有 successful frozen
  provider attempt 的 committed frame 可以授权 `current_bound` 动作，commit 必须发生在 dispatch 前。
- 初始 request 不含 image，bootstrap `observe` 是唯一无需 committed frame 的环境调用；observe
  result 必须进入下一 successful frozen request 后才变成 current binding。同 response observe+mutation
  拒绝；动作后旧 view invalid，下一步须 fresh explicit observe；retry 不 recapture。
- terminal/uncertain/closed fencing 发生在任何 backend model action 之前。
- 单 episode 默认新 session；连续 taskset 显式共享时每个 subtask 的 attempt/view/observation/
  ledger correlation 仍隔离。
- 冻结 Dispatcher 当前对 batch 先调用全部 `on_call()` 再执行 gate 的 observer 顺序及
  `agent_tool_call_count` 语义，迁移后 action accounting 无漂移。
- 新增 `scripts/v19_release/run_alfworld.py` 与 `verify_alfworld_release.py`，输出 machine JSON；verifier
  使用显式 `migration`/`release` gate mode。两种模式都对缺证据、identity mismatch、Harness invalid/
  contract violation 非零；`migration` 只要求 selected/attempted/eligible/formal 可用，`release` 额外要求
  success=4。不能依赖当前 CLI 打印任务失败后仍 exit 0 的行为。

退出条件：spec §24.8 required suite 通过；候选真实 LLM 四条必须来自预先锁定的 manifest，且 verifier
断言 `selected=4`、`attempted=4`、`eligible=4`、`formal_score_available=true`，无 Harness invalid/
contract violation。任务 success 数只报告，不作为 CL-13 架构迁移 gate。model action count、
classification、terminal call id、cleanup 和 fixture hash 无未批准 drift，runner 不再创建 model-facing
registry/dispatcher/runtime。CL-16d 完成后在 V1.9 core 最终 SHA 从头重跑，并以 `success=4` 作为独立产品
质量 gate。

### CL-14：Coworker 入口迁移

改动：

- 只替换 `benchmarking/coworker_demo/turn.py::_run_runtime()` 内的 assembly。
- `run_coworker_turn()` 继续拥有 FastAPI/browser/VNC/recording/finalize/cleanup、deadline/budget、
  scoring、presentation 和 artifact verification。
- environment/browser/display/recording 以 borrowed resources 传入，ApplicationRuntime 不 close。
- private provider/runtime/domain events 继续先经过 presentation trust boundary，再做 public projection。
- `browser_navigate` 改为 receipt-only，不自动返回完整 DOM；显式 `observe` 才记录 ticket-read 和
  canonical DOM content hash。在此之前 planner/browser mutation/SOP/completion 按 policy 拒绝；
  click/fill/select/wait 的 readback/receipt 不伪装成 observation。
- 同步更新 24-node ground truth、prompt、episode store/scoring tests：只把
  `TICKET_READ.tool_name`/evidence producer 迁到 `observe`，node id/order/dependencies、其余 tool owner、
  14 checkpoints 和 weights 不变；把该差异登记 allowed-delta/hash。
- `coworker.observe.v1` model alias 为 `observe`，可在 adapter 内映射 legacy backend operation；
  该 adapter 是旧 ledger operation 的唯一兼容边界，driver/tools/prompt/skill/provider result 不再自行
  翻译；Coworker 仍严格 11 tools 且第五项是 `observe`。
- preflight 同时校验 provider/coworker config mode 0600；新增 release runner 原子写 fresh run pointer，
  release verifier 从 pointer 读取并在 formal/artifact/identity 失败时非零，禁止 shell 捕获异常后假通过；
  每个 candidate 同时维护加锁/fsync 的 append-only `coworker-attempts.jsonl`。pointer 保存 attempt id、
  index path/hash 和 accepted run root；verifier 重算 index 并要求全部 rejected/failed/accepted attempts
  进入 gate report，拒绝截断、跨 candidate 或多 accepted pointer，不能隐藏更早失败。

退出条件：exact ordered eleven tools、child run identity、normal/anomaly/rollback 非 live 回归、
formal artifacts 全部通过；候选 SHA 在 `hkust4` 对唯一 test-set item 完成一次 fresh normal 真实
LLM score-counted accepted run，`formal_success=true` 且独立 verifier PASS。required artifact 缺失不能被
RuntimeEvent final 掩盖。CL-16d 完成后还必须在 V1.9 core 最终 SHA 重跑该唯一 1/1 gate；anomaly
不计为第二条数据。

### CL-15：Adapter ownership 和旧装配清理

改动：

- 把 model-facing ALF/Coworker definitions 移到 `homemaster/adapters/`，benchmark 目录只保留环境和
  evaluation owner。
- 删除旧 registry/dispatcher assembly、runtime-local `ToolSpec` 和 `_to_tool_specs()` lossy path。
- 删除 `ToolDispatcher.set_run_context()` 及 runtime 对 `_run_context` 的 private 访问。
- 删除前用 identity tests 证明 TaskStateStore 已从 SessionRuntime 显式传到 task tools、ContextAssembler
  和 persistence，TaskSnapshot prelude/完成门没有随 private seam 一起消失。
- Home compatibility wrapper 只有达到声明版本/usage gate 后才删除；ALF execution translator 的
  历史兼容单独管理。

退出条件：AST/import guards 通过，所有四个入口只调用 ApplicationRuntime；仓库中只剩一个
canonical tool contract 和一条 model-facing dispatch pipeline。

### CL-16a：Provider 与 event async

- Provider transport/attempt wait 与 RuntimeEvent stream 改为原生 async；application-owned EventBus
  使用 bounded lifecycle，domain ledger 仍先持久化。
- 复用 CL-05 已锁定的 async pytest plugin/mode；本 CL 不再改变测试 runner 契约。
- 复制 OpenHarness `engine/stream_events.py` DTO/consumer 分支源码和 tests，再映射 public projection。

退出条件：frozen provider request bytes/hash、attempt/commit 顺序与 public/private event projection
parity 通过；provider wait 不阻塞另一 session，无 pending producer/consumer。

### CL-16b：Agent 与 pipeline async/cancellation

- AgentRuntime 改为直接 await CL-05 已 async 的 ToolExecutionPipeline，删除 `SyncPipelineBridge`；
  取消传播到 provider wait、lock wait、execute 和 verification wait。同步 backend 用受控 adapter，
  明确 deadline 和 `outcome_unknown`。
- 沿用 CL-05 已移植的 BaseTool/query gather/exception isolation 源码和原测试，只复制尚未覆盖的
  upstream cancellation cases，再加入 Home resource conflict scheduler；不得再次复制出第二条 pipeline。

退出条件：stage-applicable cancellation/deadline/lease cleanup gate 通过；一 session 等待 backend 时
不阻塞另一 session，frozen request/model-view/evidence 顺序不变。

### CL-16c：Coworker Playwright adapter

- 在实现前作出并记录唯一选择：thread-owned sync backend adapter，或整体迁到 Playwright async API。
  禁止对现有 sync page/browser 随意 `to_thread()` 或跨 owner thread 调用。
- 单独适配 FastAPI/browser/VNC/recording/finalize/cleanup，保持 borrowed ownership 和 formal artifacts。

退出条件：thread-affinity、cancel/timeout、normal/anomaly/rollback 和 lifecycle matrix 通过；页面、录像
和正式 verifier 无漂移。

### CL-16d：Stress、generation 与 leak

- 32 session barrier、1000 progress flood、1000 open/close、100 cancel/restart generation race。
- 检查 pending asyncio tasks、EventBus subscriber、resource lease、browser thread 和 late write。

退出条件：comparison spec §24.12 stress 通过，无 pending task/lease/thread/subscriber；CLI/ALF/
Coworker 全部 0C gate 继续通过。

## 8. Phase 1-3 后续路线图（不属于 V1.9 core release）

V1.9 必发范围止于 CL-16d，并在该 SHA 完成真实 4+1 gate。CL-17～CL-21 是已设计但单独排期的
后续路线图，不阻塞 V1.9 core，也不得在 core live gate 前混入 release branch。未来若把其中任一
CL 纳入新的正式版本，必须先锁定首个 channel/extension 范围，并在新最终 SHA 重跑完整 4+1。

这些变更必须在 CL-16d 后独立实施，不与核心入口迁移混合。每个 CL 都使用 comparison spec
§24.10-24.12 的独立 gate；上游源码和测试必须实际移植，但不能替代 HomeMaster 的 session、
resource、redaction 和机器人安全 delta tests。

### CL-17：Skills 与 config sources

阶段：Phase 1。

目标文件：

- 新增 `src/homemaster/skills/_frontmatter.py`；修改 `skills/loader.py`、`skills/registry.py`、
  `skills/spec.py` 和 `config/config.py`。
- 扩展 `tests/homemaster/test_skills_registry.py`，新增
  `tests/homemaster/skills/test_frontmatter.py`、`test_loader_sources.py` 和
  `test_skill_security.py`。
- 修改 root build/package-data 配置，新增 installed-wheel smoke，证明 builtin `SKILL.md`/resources
  安装后可发现，不依赖 source checkout。

OpenHarness 来源（V/A）：整文件复制 `skills/_frontmatter.py` 及 frontmatter parser 原 tests；复制
skills types/registry/loader 中 builtin/user/project discovery、source precedence/security 实现和原
tests，再做 HomeMaster delta。HomeMaster 已有 `pyyaml` runtime dependency，本 CL 不新增解析库。

HomeMaster 适配：

- 固定 builtin、user、project、explicit config 的优先级，并在同名替换时保留双方 provenance；
  非显式 policy 不允许静默覆盖 builtin safety skill。
- `SKILL.md` 的 `tool_names` 只能引用当前 profile 可解析的 model alias；skill 本身不能启用
  ToolView 中 disabled tool，也不能授予 robot capability 或 permission subject。
- 所有 skill/resource path 在读取前 resolve 并校验仍位于授权 root；覆盖 symlink/junction、nested
  resource、absolute path 和 `..` escape。配置和诊断输出统一经过 secret redaction。
- provider/auth config 补 typed schema、`defaults < file < env < limited CLI` precedence、field provenance 和
  recursive redaction；doctor/config output 只报告来源/存在性/有效性，不输出 secret。

测试与退出条件：frontmatter folded/literal/boolean/invalid YAML、来源优先级、同名 provenance、
路径 escape、capability escalation、provider/auth redaction 与 installed-wheel builtin discovery
全部通过；Home/ALF/Coworker 三个 profile 的 ToolView 与 CL-16d fixture 无变化。回滚点是关闭新增 user/project sources并恢复仅 builtin loader；已加载的
skill 只影响下一 fresh run，不能热改正在运行的 ToolView。

### CL-18：MCP client、resources 与 tool adapter

阶段：Phase 1。

目标文件：

- 新增 `src/homemaster/mcp/types.py`、`mcp/client.py`、`mcp/adapter.py`；修改
  `application/factory.py`、`application/resources.py` 和 `config/config.py`。
- 新增 `src/homemaster/artifacts/tool_output_store.py`：tenant/session/run 分区、write-before-redaction、
  ACL、quota/TTL 和 opaque handle；MCP/tool 大输出只把 bounded preview/handle 放入 model/event。
- 新增 `tests/homemaster/mcp/`、`tests/homemaster/fixtures/fake_mcp_server.py`，并扩展
  `tests/homemaster/application/test_resource_scope.py` 和 entry dry-run/probe tests。
- 仅在本 CL 给根 `pyproject.toml` 增加 `mcp` optional extra 并更新根 `uv.lock`；Phase 0 不提前
  引入 MCP SDK。未安装 extra 时返回 typed feature-unavailable，不在 import collection 阶段失败。

OpenHarness 来源（V/A）：复制 `mcp/types.py`、`mcp/client.py`、fake server 及 stdio/HTTP/error 原
tests；复制 `McpToolAdapter`/resource tool 作为 seed 后替换 shallow schema projection。manager
ownership、typed result 与 artifact policy作为 HomeMaster delta；不得只重写 protocol flow。

HomeMaster 适配：

- `McpClientManager` 是 application-owned resource；每个 server 初始化成功后立即登记 cleanup，
  单个连接失败不泄漏已创建 stack，close failure 不阻止其他 server 关闭。
- discovery 生成带 server provenance 的 canonical `ToolDefinition`，完整保留 nested、enum、
  `additionalProperties` 等 JSON Schema；alias conflict 在 Catalog/View freeze 时失败，不能使用
  shallow MCP schema adapter。
- run 只能启用其 frozen ToolView 中的 MCP stable ids；headers/env、异常和 status detail 全链路
  redaction。首版只支持 stdio/streamable HTTP；WebSocket config 明确返回 unsupported。

测试与退出条件：stdio/HTTP handshake、list/call/resource、partial init、disconnect、timeout、
cancel、close isolation、schema round-trip、alias conflict、per-run enablement 和 redaction gate
通过；large output 的 partition/redaction/ACL/quota/TTL/opaque-handle gate 通过；dry-run 不拉起
server，`--probe` 行为有审计记录。回滚通过禁用 MCP config 并关闭
application-owned manager 完成，不影响 builtin Catalog、session snapshot 或正在执行的机器人动作。

### CL-19：权限、认证与设备资源基础

阶段：Phase 2。

目标文件：

- 新增 `src/homemaster/devices/contracts.py`、`devices/connection_pool.py`、
  `devices/lease_manager.py`、`permissions/policy.py` 和 `gateway/auth.py`；修改
  `application/resources.py` 与 robot adapter composition。
- 新增 `tests/homemaster/devices/`、`tests/homemaster/permissions/` 和
  `tests/homemaster/gateway/test_robot_authorization.py`。

OpenHarness 来源（A）：复制 `permissions/modes.py`、`checker.py` 的 actual rules 与 allow/deny 原 tests，
再接 Home typed policy；不直接套用 coding-agent
cwd/command policy，也不从 metadata 推断 HomeMaster principal。connection pool、device lease、
emergency stop 与 safety fencing 按 HomeMaster 机器人语义实现。

HomeMaster 适配：

- remote auth 在 Gateway 边界产生 typed principal/capabilities，pipeline permission seam 每次执行；
  skill、prompt、slash command、attachment 均不能扩大 capability。
- lease key 绑定 physical device/backend identity；同设备 mutating actions 串行，不同设备可并发。
  timeout/cancel 必须释放本 generation 的 lease，stale generation 不得释放新 lease。
- emergency stop 使用独立高优先级控制路径并记录权威 device event；terminal、uncertain、closed
  在 backend 前 fencing，动作后无法确认结果则返回 `outcome_unknown`，断线或未知结果的 mutating
  action 不自动重试。

测试与退出条件：typed allow/deny、跨 tenant/device 越权、lease 冲突、公平性、timeout/cancel、
disconnect、stale generation、emergency stop 抢占和 owned/borrowed close matrix 全通过；fake 两设备
可并发且同设备最大并发为 1。回滚先关闭 remote ingress 并保留 emergency-stop/control path，等待
已有 action 达到可判定状态后释放 lease；禁止在同一 physical run 上自动切回旧执行链重做动作。

### CL-20：Gateway、Channel 与公共事件投影

阶段：Phase 2。

目标文件：

- 新增 `src/homemaster/channels/contracts.py`、`channels/router.py`、`channels/bus.py`、
  `channels/bridge.py`、`gateway/runtime.py` 和 `events/public_projection.py`。
- 新增 `tests/homemaster/channels/`、`tests/homemaster/gateway/`，并扩展
  `tests/homemaster/events/test_public_projection.py` 与 entry parity tests。

OpenHarness 来源（A）：复制 `channels/bus/events.py`、`queue.py`、`channels/adapter.py`、
`channels/impl/base.py`、ohmo gateway models/router/bridge 的相关源码，以及 routing/progress/media/
cancel 原 tests。随后把无界 queue 改成 bounded priority，不复制 per-session QueryEngine/RuntimeBundle。
复制时原样记录 `channels/UPSTREAM` 的 nanobot provenance chain。

HomeMaster 适配：

- 合并前必须锁定首个真实 channel，并复制该 `channels/impl/<channel>.py` 与 security tests；未选择时
  external ingress 保持 disabled，不从零写占位 adapter。Phase 3 每个新增 channel 同样逐个复制适配。
- contracts 包含 tenant/channel/chat/thread/sender typed identity；router 产生稳定隔离 session key，
  principal 不能从 prompt/metadata 覆盖。
- bridge 只向 application-owned ApplicationRuntime 提交 RunRequest，通过 generation fencing 处理
  reconnect/cancel/late result；附件 resolve 后仍在允许 root。
- bounded priority bus 可合并 progress，final/error/cancel 不丢，producer backpressure、deadline drain；
  domain ledger 先持久化，再经 PublicEventProjection allowlist/redaction/correlation 发布。
- Gateway restart 从 SessionBackend 恢复合法 snapshot、清洗未配对 tool tail、增加新 generation；不得
  恢复 live backend/ToolView/provider client，也不得让旧进程 late result 写回。

测试与退出条件：private/group/thread/tenant 路由、身份伪造、附件 escape、1000 progress flood、
final/error/cancel 保留、shutdown drain、restart/session recovery、reconnect generation 和 redaction
全部通过；Gateway 与 CLI/benchmark 同 factory/profile。回滚先停 ingress/drain/close owned resources，
不关闭本地 CLI/Interactive 或 borrowed benchmark backend。

### CL-21：Hooks 与 Plugins

阶段：Phase 3。

目标文件：

- 新增 `src/homemaster/extensions/contracts.py`、`extensions/loader.py`、
  `extensions/hook_runner.py`、`extensions/reloader.py`；修改 application factory 以加载显式启用的
  extension manifest。
- 对每个已批准的额外 channel，复制对应 OpenHarness adapter/security tests 后适配 typed router；
  不批量启用全部 channel。
- 新增 `tests/homemaster/extensions/` 和 compatibility manifest fixtures；如选择文件 watcher，只在
  本 CL 增加相应 optional dependency 并更新根 `uv.lock`。

OpenHarness 来源（V/A）：复制 hooks events/types/schemas/loader/executor 及 manifest/priority/block/
timeout 原 tests；复制 plugin schemas/types 和 discovery/hook/MCP/tool-loader 相关源码及 lifecycle
tests。hot_reload 文件只作 input seed，正式 atomic generation swap 为 HomeMaster delta。不得保留隐式
capability 扩张或把 hook 当 safety owner 的行为。

HomeMaster 适配：

- manifest 固定 version/provenance/capabilities/entrypoints；加载失败按 plugin 隔离，不能让一个
  extension 破坏 application rollback。hook 有 deadline、typed result 和 redacted input/output。
- extension tool 只能作为带 provenance 的 Catalog definition 注册，并仍受 run ToolView、permission
  和 resource policy；hook 不能绕过 pipeline 执行机器人动作。
- hot reload 先构建并验证新 generation，成功后原子切换；失败继续使用旧 generation，旧回调的
  late result 受 fencing。terminal、verification、scorer 和 emergency stop 不以 shell hook 为唯一实现。

测试与退出条件：manifest 版本/来源、重复 id、import failure、timeout、blocking、capability deny、
hot reload success/rollback、resource cleanup 和 generation fencing 全通过；所有 Phase 0-2 gate 继续
通过。回滚为原子恢复上一份已验证 extension generation，或禁用 extension layer；canonical builtin
Catalog、permission/device safety owner 和 domain scorer 始终可独立工作。

## 9. 合并、发布与回滚策略

### 9.1 合并规则

- 一个 CL 一个主要架构目的；生产改动与相应测试同 CL 合并。
- 任何复制代码在文件头或 notices 中保留 upstream commit/path；后续本地改动可追踪。
- old/new differential 先比较 canonical manifest/request/result，再比较领域 ledger/scorer input。
- 不能通过更新 golden 隐藏差异；每条 allowed delta 必须有 reason、owner 和稳定/删除条件。
- ALFWorld 与 Coworker 分开合并和发布 gate，任一失败不阻塞另一入口继续定位，但不能宣称
  V1.9 核心入口迁移完成。

### 9.2 切换规则

入口只在自己的 parity gate 全通过后切换。切换时旧函数先作为 thin wrapper 保留一个兼容窗口，
但 wrapper 不能继续维护第二套 assembly。mutating run 一旦开始，失败时不能在同一环境自动回退
旧 runtime 重做动作；回滚发生在下一 fresh run 或部署版本层面。

### 9.3 硬停止条件

出现以下任一情况立即停止后续 CL，并回到最近通过 gate 的变更单：

- provider retry request hash 或 observation bytes/content hash 变化；raster pixel bytes/hash 变化；
- 对 `requires_pre_observation=current_bound` 的工具，在 successful attempt commit 前发生 ALF model
  backend action（bootstrap `observe` read-only capture 不属于该 action）；
- terminal/uncertain/closed 后 model action count 增长；
- Coworker ToolView 不等于固定有序十一项；
- borrowed backend/browser/environment 被 ApplicationRuntime close；
- session 间出现 ToolView、backend、observation、TaskState、event 或 cancellation 泄漏；
- formal scorer/evidence owner 被 RuntimeEvent 或通用 pipeline 替代；
- non-live suite 新增未解释 skip/xfail、collection failure 或 golden drift。
- HPC2 与 `hkust4` 的 HomeMaster commit 不一致或任一 tracked worktree 非 clean；
- 最终真实 LLM 结果不是同一 release SHA 上的 ALFWorld 4/4 和 Coworker 1/1。

### 9.4 HPC2 到 hkust4 的 Git 同步

用户已允许使用 GitHub 同步到 `hkust4`。每个准备进入 live gate 的候选遵循以下流程：

1. 在 HPC2 完成实现、format/compile/Ruff/non-live/benchmark contract gates，确认所有计划内 tracked
   文件已提交；provider config、key 和 `var/` artifact 不加入 commit。
2. 在 HPC2 记录完整 `V19_RELEASE_SHA=$(git rev-parse HEAD)`，push `HEAD` 到明确的 V1.9 release
   branch。不得用未提交补丁或本机 editable source 覆盖候选。
3. 在 `hkust4` 的专用 clean worktree 执行 `git fetch --prune origin`，detached checkout
   `V19_RELEASE_SHA`；验证 `git rev-parse HEAD` 相等、包含 untracked 的 status 为空、Python import
   origin 与源文件 hash 位于该 worktree，再按 lock 同步环境。
4. `hkust4` 只保留其现有 gitignored、mode-0600 的真实 provider/Coworker config。不得从 HPC2 或
   GitHub 传输 credential，也不得为了修 live failure 在 `hkust4` 编辑产品代码。
5. live 发现问题后回 HPC2 修改、测试、commit、push，`hkust4` fetch 新 SHA。任何产品代码或
   tracked benchmark 内容的新 commit 都使旧 4+1 证据失效，两个 benchmark 必须在新 SHA 重跑。

建议命令骨架：

```bash
# HPC2
git status --short
V19_RELEASE_SHA="$(git rev-parse HEAD)"
: "${V19_RELEASE_BRANCH:?set V19_RELEASE_BRANCH}"
git push origin "HEAD:refs/heads/$V19_RELEASE_BRANCH"

# hkust4 dedicated worktree
git fetch --prune origin
git checkout --detach "$V19_RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$V19_RELEASE_SHA"
test -z "$(git status --porcelain)"
```

`hkust4` 的 Coworker 命令继续使用其锁定的 `.venv`。只有在该机执行 ALFWorld 预检或复现时，所有
Python、pytest、runner、verifier 和 import probe 都必须经 `hm_alfworld`，例如：

```bash
conda run -n hm_alfworld python scripts/v19_release/capture_environment_identity.py \
  --profile alfworld --expected-conda-env hm_alfworld \
  --output "var/v19-release/$V19_RELEASE_SHA/hkust4-alfworld-environment.json"
conda run -n hm_alfworld python scripts/v19_release/verify_alfworld_trials.py \
  --manifest config/alfworld_v19_release_trials.json
```

identity capture 必须在 `hm_alfworld` 进程内读取并保存 `CONDA_DEFAULT_ENV`、`sys.executable`、完整
Python version、`conda list --explicit` 原始输出 SHA-256，以及 `homemaster`、`alfworld`、`ai2thor`
resolved import origins。任一字段缺失、环境名不等于 `hm_alfworld`、HomeMaster origin 不位于 detached
candidate worktree，或依赖 origin 与预检不一致时非零退出。hkust4 结果只作预检/复现证据，不能替代
HPC2 上最终 ALFWorld 4/4 gate。

正式 evidence 写入 `var/v19-release/<candidate>/live-release-identity.json`，保存两端 SHA、remote
ref、lock hashes、dataset/manifest hashes、环境 identity 和 import origins。新增 sanitized gate report
merger/verifier：只合并两机 machine JSON、拒绝 SHA/dataset/provider identity 不同或缺少 verifier exit，
不通过 Git 传 credential/raw provider payload。命令中的
branch/worktree 路径在执行时写入 manifest，不在规格中假设；该 `var/` 证据不提交 Git。

### 9.5 最终真实 benchmark gate

V1.9 core 的 CL-01、M0、CL-02～CL-16d 完成后冻结最终 SHA，并在看模型结果前验证“最新锁定内容”：
它明确指最终 SHA 已提交的
V1.8/Coworker 固定 inventory 加 preflight hash/identity 验证通过的外部 dataset bytes，不动态挑选任务。

- ALFWorld：HPC2 上对 ALFWorld root/config/dataset bytes 完成 preflight hash/identity 锁定，使用恰好四条的
  `config/alfworld_v19_release_trials.json`，真实 LLM、`AlfredThorEnv`、`valid_unseen`、
  `visual_eval` fresh run。machine verifier 必须断言 selected=4、attempted=4、eligible=4、success=4，
  每条 `classification="agent_success"` 且 `formal_score_available=true`，失败时非零；不读取不存在的
  generic `coverage` 字段。
- Coworker：同一 SHA 同步到 `hkust4`，验证 `data/coworker_demo/case_02/dataset_manifest.json`
  及声明 hash，对唯一 `test_set/item_change_ticket.json` 做一次 fresh normal 真实 LLM run。
  `formal_success=true`、24/24 trajectory、14/14 checkpoints、连续录像和独立 bundle verifier 全部
  PASS，才计为唯一 score-counted accepted run 和 1/1。该候选全部 rejected/failed attempts 必须保留并
  列入 report，原子 pointer 不得隐藏更早尝试；anomaly/rollback 保留非 live 回归，不虚算第二条数据。

正式运行只允许 comparison spec §24.12 的 `scripts/v19_release/run_*` + `verify_*` 入口。ALF runner 和
Coworker runner 都原子写本次 fresh result pointer/report；case/formal/artifact/identity 任一失败必须
非零。禁止用现有会打印失败后 exit 0 的 ALF CLI，或会捕获 Coworker 异常后继续的 Interactive shell
作为发布 gate。

两个正式 run 都必须证明非 mock/scripted/loopback provider、实际 Provider attempts 大于零，并绑定
最终 model/provider identity。旧 V1.8/Coworker 历史 run、nightly marker、单条补跑或成功子集不能
作为完成证据。失败 artifact 不删除；修复产生新 SHA 后重新执行完整 ALF 4 条和 Coworker 1 条。

## 10. V1.9 完成定义

V1.9 核心改造只有同时满足以下条件才算完成：

1. CLI、Interactive、ALFWorld 和 Coworker 都只通过同一个 `ApplicationRuntime` 运行。
2. 仓库只有一个 canonical tool contract、一个 application Catalog 和一条无 session 状态的 pipeline。
3. 每个 run 使用 immutable ToolView，disabled tool 对模型和执行层同时不可用。
4. 模型可见图片/DOM/state 只由显式 `observe` 产生；三个 profile 只暴露该统一 alias，retry 复用
   冻结请求且不 capture，审计/verifier/result 无隐式 model media 通道。
5. ALFWorld V1.8 和 Coworker 的全部领域 gate、独立 scorer 和 artifact verifier 保真。
6. 同 session 串行、不同 session 可并发，generation fencing 和 resource cleanup 可重复证明。
7. sync compatibility 的不确定动作语义被明确记录，CL-16a～CL-16d async/cancellation/stress gate 通过。
8. V1.9 core 所需的 OpenHarness 兼容逻辑已按 V/A 实际复制源码和原测试，port manifest 可追溯；
   没有静默复制或按文字重复实现。后续 CL 适用同一规则。
9. comparison spec §24 对应 required test exit 0，外部 gate 具有明确 PASS/FAIL/UNVERIFIED 状态。
10. 所有 tracked 代码只在 HPC2 修改，经 GitHub 同步后 HPC2 与 `hkust4` 的正式 worktree 绑定同一
    最终 SHA；真实 credential 未进入 Git 或 evidence。
11. 同一最终 SHA 上，最新锁定 ALFWorld 四条真实 LLM 结果为 4/4，Coworker 唯一 test-set item
    真实 LLM 结果为 1/1；两边独立 scorer/verifier 和 artifact hash 均 PASS。
12. 每个 core phase 都有动态 state/subgoal record、实际测试 evidence 和 phase-end independent subagent
    code review；所有 correctness/safety/contract findings 已处置并复验。
